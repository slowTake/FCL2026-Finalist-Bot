"""knight — Core Domain: Macro Evaluation, Threat Detection & Gate Cycling Spawner.

Evaluates game phases, threat detection, tactical mode transitions, predictive ammo
conversion, smoothed expansion policy (with hysteresis), role assignment broadcasts,
authoritative builder intents, and Core gate cycling to spawn Builder Bots through barrier walls.
"""

from __future__ import annotations

import sys
from typing import Optional
from fcode import Controller, Direction, EntityType, GameConstants, Position

from blackboard.belt_protocol import belt_alert_is_fresh, unpack_belt_alert
from blackboard.ore_protocol import OreLifecycle
from config import Config
from engine.safe_controller import SafeController
from strategy.threat import ThreatClassifier, ThreatClass
from utils.geometry import (
    get_core_footprint,
    is_plausible_ore_target,
    is_valid_ore_target,
    pack_builder_intent,
    pack_ore_discovery,
    pack_pos,
    pack_position,
    pack_role_assignment,
    unpack_builder_intent,
    unpack_ore_discovery,
    unpack_ore_status,
    unpack_pos,
    unpack_position,
    unpack_role_assignment,
)


class CoreMacro:
    """Macro state evaluator executing on the Core unit."""

    def __init__(self, safe: Optional[SafeController] = None) -> None:
        self.safe = safe or SafeController(None)  # type: ignore
        self.classifier = ThreatClassifier(self.safe)
        self.spawn_count: int = 0
        self.quiet_turns: int = 0
        self.last_core_hp: Optional[int] = None
        self.last_core_damage_round: int = -10000
        self.last_policy: Optional[int] = None

        # Income Stall Monitor (Phase G.7)
        self.max_ti_seen: int = 0
        self.last_ti_growth_round: int = 0

        # Hysteresis state for expansion policy
        self.current_policy: int = Config.EXPANSION_FREE
        self.candidate_policy: int = Config.EXPANSION_FREE
        self.candidate_stable_turns: int = 0

        # Phase 13: Core Intent Lease State
        self.current_intent_target: Optional[Position] = None
        self.intent_created_round: int = 0
        self.intent_last_supported_round: int = 0

        # Dynamic role assignment state
        self.guardian_builder_id: int = 0
        self.reserve_builder_id: int = 0

    def run_core_turn(self, ct: Controller) -> None:
        """Main turn loop for the Core entity."""
        self.safe.begin_turn(ct)
        core_pos = ct.get_position()
        curr_round = ct.get_current_round()

        # Slot 0: Core Position (unified offset-encoded packing)
        ct.write_store(Config.SLOT_CORE_POS, pack_position(core_pos))

        # Synchronize threat assessments and tactical mode
        tactical_mode = self._evaluate_threats_and_tactical_mode(ct)

        # Evaluate and smooth expansion policy hysteresis
        policy = self._evaluate_expansion_policy(ct, tactical_mode)

        # Broadcast strategic intent for builders
        self._broadcast_strategic_intent(ct, policy)

        # Update Slot 7 (SLOT_TURRET_COUNT) and Slot 8 (SLOT_BUILDER_COUNT)
        turret_count = sum(
            1 for bid in self.safe.get_nearby_buildings()
            if ct.get_team(bid) == ct.get_team() and ct.get_entity_type(bid) in (EntityType.SENTINEL, EntityType.GUNNER)
        )
        ct.write_store(Config.SLOT_TURRET_COUNT, turret_count)
        ct.write_store(Config.SLOT_BUILDER_COUNT, max(0, ct.get_unit_count() - 1))

        # Manage predictive and reactive ammo reserves
        self._manage_ammo_reserves(ct)

        # Spawn economy and guardian builders
        self._evaluate_builder_production(ct)

        # Periodic telemetry broadcast
        if Config.DEBUG_METRICS and (curr_round % Config.METRIC_INTERVAL_ROUNDS == 0):
            self._emit_validation_metrics(ct, tactical_mode, policy)

    def _get_core_hp_pct(self, ct: Controller) -> int:
        """Return integer percentage of Core HP remaining (0-100)."""
        return int(100 * ct.get_hp() / max(1, ct.get_max_hp()))

    def _track_core_damage(self, ct: Controller) -> bool:
        """Track damage events and return True if Core took damage within RECENT_DAMAGE_WINDOW_TURNS."""
        curr_hp = ct.get_hp()
        curr_round = ct.get_current_round()
        if self.last_core_hp is not None and curr_hp < self.last_core_hp:
            self.last_core_damage_round = curr_round
        self.last_core_hp = curr_hp
        return (curr_round - self.last_core_damage_round) <= Config.RECENT_DAMAGE_WINDOW_TURNS

    def _get_threat_pressure(self, ct: Controller, core_footprint: list[Position]) -> tuple[int, bool]:
        """Compute aggregate threat pressure and check for armed perimeter breach."""
        my_team = ct.get_team()
        threat_pressure = 0
        breach = False

        for eid in self.safe.get_nearby_entities():
            if ct.get_team(eid) != my_team:
                etype = ct.get_entity_type(eid)
                epos = ct.get_position(eid)
                d = min(epos.distance_squared(cp) for cp in core_footprint)

                if etype == EntityType.BUILDER_BOT:
                    if d <= Config.ENEMY_BUILDER_ALERT_DIST_SQ:
                        threat_pressure += 1
                elif etype in (EntityType.GUNNER, EntityType.SENTINEL, EntityType.LAUNCHER):
                    if d <= Config.ARMED_BREACH_DIST_SQ:
                        threat_pressure += 3
                        breach = True
                    elif d <= Config.ARMED_ALERT_DIST_SQ:
                        threat_pressure += 2
                else:
                    if d <= Config.ARMED_ALERT_DIST_SQ:
                        threat_pressure += 1

        if ct.get_hp() < ct.get_max_hp():
            breach = True

        return threat_pressure, breach

    def _get_defense_counts(self, ct: Controller, core_footprint: list[Position]) -> tuple[int, int]:
        """Count operational defense turrets and intact barrier apron tiles."""
        my_team = ct.get_team()
        core_pos = core_footprint[0]
        turret_count = 0
        barrier_count = 0

        for bid in self.safe.get_nearby_buildings():
            if ct.get_team(bid) == my_team:
                btype = ct.get_entity_type(bid)
                bpos = ct.get_position(bid)
                if btype in (EntityType.GUNNER, EntityType.SENTINEL):
                    turret_count += 1
                elif btype == EntityType.BARRIER and bpos.distance_squared(core_pos) <= 8:
                    barrier_count += 1

        return turret_count, barrier_count

    def _smooth_expansion_policy(self, raw_policy: int, ct: Controller) -> int:
        """Apply hysteresis smoothing to prevent expansion policy flapping."""
        old_policy = self.current_policy

        if raw_policy == Config.EXPANSION_LEASHED:
            # Immediate safety downgrade
            self.candidate_policy = Config.EXPANSION_LEASHED
            self.candidate_stable_turns = 0
            self.current_policy = Config.EXPANSION_LEASHED
            if old_policy != self.current_policy and (Config.DEBUG_ECONOMY or Config.DEBUG_DEFENSE):
                print(
                    f"POLICY_STABLE_CHANGE round={ct.get_current_round()} old={old_policy} "
                    f"new={self.current_policy} raw={raw_policy} stable_turns=0",
                    file=sys.stderr,
                )
            return self.current_policy

        if raw_policy == self.candidate_policy:
            self.candidate_stable_turns += 1
        else:
            self.candidate_policy = raw_policy
            self.candidate_stable_turns = 1

        required_turns = Config.POLICY_UPGRADE_STABLE_TURNS
        if raw_policy == Config.EXPANSION_PARTIAL and self.candidate_policy == Config.EXPANSION_FREE:
            required_turns = Config.POLICY_DOWNGRADE_STABLE_TURNS

        if self.candidate_stable_turns >= required_turns:
            self.current_policy = self.candidate_policy
            if old_policy != self.current_policy and (Config.DEBUG_ECONOMY or Config.DEBUG_DEFENSE):
                print(
                    f"POLICY_STABLE_CHANGE round={ct.get_current_round()} old={old_policy} "
                    f"new={self.current_policy} raw={raw_policy} stable_turns={self.candidate_stable_turns}",
                    file=sys.stderr,
                )
            self.candidate_stable_turns = 0

        return self.current_policy

    def _evaluate_expansion_policy(self, ct: Controller, tactical_mode: int) -> int:
        """Decouple economy expansion leashing from combat mode with hysteresis smoothing."""
        core_hp_pct = self._get_core_hp_pct(ct)
        recently_damaged = self._track_core_damage(ct)
        core_footprint = get_core_footprint(ct)
        threat = self.classifier.evaluate(ct, core_footprint)
        turret_count, barrier_count = self._get_defense_counts(ct, core_footprint)
        curr_rnd = ct.get_current_round()

        # Phase 14.2: Break Expansion Policy Circularity
        if threat.breach:
            raw_policy = Config.EXPANSION_LEASHED
        elif core_hp_pct < Config.CORE_HP_CRITICAL_PCT:
            raw_policy = Config.EXPANSION_LEASHED
        elif recently_damaged:
            raw_policy = Config.EXPANSION_LEASHED
        elif tactical_mode == 2:
            if core_hp_pct >= Config.CORE_HP_SECURE_PCT:
                raw_policy = Config.EXPANSION_PARTIAL
            else:
                raw_policy = Config.EXPANSION_LEASHED
        elif tactical_mode == 1:
            if core_hp_pct >= Config.CORE_HP_SECURE_PCT:
                raw_policy = Config.EXPANSION_FREE
            else:
                raw_policy = Config.EXPANSION_PARTIAL
        else:
            raw_policy = Config.EXPANSION_FREE

        policy = self._smooth_expansion_policy(raw_policy, ct)

        ct.write_store(Config.SLOT_EXPANSION_POLICY, policy)
        ct.write_store(Config.SLOT_BASE_INTEGRITY, core_hp_pct)

        if (Config.DEBUG_ECONOMY or Config.DEBUG_DEFENSE) and policy != self.last_policy:
            print(
                f"POLICY_RAW round={curr_rnd} raw={raw_policy} final={policy} "
                f"mode={tactical_mode} hp_pct={core_hp_pct} turrets={turret_count} "
                f"barriers={barrier_count} pressure={threat.pressure} breach={threat.breach}",
                file=sys.stderr,
            )
            self.last_policy = policy

        return policy

    def _broadcast_strategic_intent(self, ct: Controller, policy: int) -> None:
        """Author authoritative strategic intent into SLOT_BUILDER_INTENT with lease & invalidation checks."""
        curr_rnd = ct.get_current_round()
        if policy == Config.EXPANSION_LEASHED:
            self.current_intent_target = None
            core_pos = ct.get_position()
            val = pack_builder_intent(core_pos.x, core_pos.y, Config.INTENT_DEFEND_CORE, 15)
            ct.write_store(Config.SLOT_BUILDER_INTENT, val)
            return

        # Phase G.8: Core Repair Intent Broadcast
        val_belt = ct.read_store(Config.SLOT_BELT_ALERT)
        if belt_alert_is_fresh(ct, val_belt):
            belt_pos, _ = unpack_belt_alert(val_belt)
            if belt_pos is not None:
                val = pack_builder_intent(belt_pos.x, belt_pos.y, Config.INTENT_REPAIR, 13)
                ct.write_store(Config.SLOT_BUILDER_INTENT, val)
                return

        val_ore = ct.read_store(Config.SLOT_ORE_DISCOVERY)
        ore_pos, round_mod = unpack_ore_discovery(val_ore)

        val_status = ct.read_store(Config.SLOT_ORE_STATUS)
        inv_pos, inv_status = unpack_ore_status(val_status)

        core_footprint = get_core_footprint(ct)

        # Check shared invalidation
        if inv_pos is not None and ore_pos is not None and inv_pos == ore_pos and inv_status != Config.ORE_STATUS_NONE:
            if Config.DEBUG_ECONOMY:
                print(f"INTENT_CLEAR round={curr_rnd} target={ore_pos} reason=STATUS_{inv_status}", file=sys.stderr)
            ct.write_store(Config.SLOT_ORE_DISCOVERY, 0)
            ore_pos = None

        if ore_pos is not None:
            age = (curr_rnd - round_mod) % 256
            fresh = age <= Config.ORE_DISCOVERY_TTL_ROUNDS
            plausible = is_plausible_ore_target(ct, ore_pos, core_footprint)

            if fresh and plausible:
                if self.current_intent_target == ore_pos:
                    self.intent_last_supported_round = curr_rnd
                else:
                    self.current_intent_target = ore_pos
                    self.intent_created_round = curr_rnd
                    self.intent_last_supported_round = curr_rnd

                val = pack_builder_intent(ore_pos.x, ore_pos.y, Config.INTENT_EXPAND_ORE, 10)
                ct.write_store(Config.SLOT_BUILDER_INTENT, val)
                return
            else:
                reason = "STALE" if not fresh else "IMPLAUSIBLE"
                if Config.DEBUG_ECONOMY:
                    print(f"INTENT_CLEAR round={curr_rnd} target={ore_pos} reason={reason}", file=sys.stderr)
                self.current_intent_target = None
                ct.write_store(Config.SLOT_ORE_DISCOVERY, 0)

        if self.current_intent_target is not None:
            if (curr_rnd - self.intent_last_supported_round) > Config.INTENT_LEASE_TURNS:
                if Config.DEBUG_ECONOMY:
                    print(f"INTENT_CLEAR round={curr_rnd} target={self.current_intent_target} reason=LEASE_EXPIRED", file=sys.stderr)
                self.current_intent_target = None

        ct.write_store(Config.SLOT_BUILDER_INTENT, 0)

    def _should_request_scouts(self, ct: Controller) -> bool:
        """True if team is starving for harvesters/ore discovery on unexplored maps."""
        my_team = ct.get_team()
        curr_rnd = ct.get_current_round()
        harvester_count = sum(
            1 for bid in self.safe.get_nearby_buildings()
            if ct.get_team(bid) == my_team and ct.get_entity_type(bid) == EntityType.HARVESTER
        )
        if curr_rnd >= Config.EXPLORATION_EMERGENCY_ROUND and harvester_count == 0:
            return True
        if curr_rnd >= Config.NO_HARVESTER_EMERGENCY_ROUND and ct.get_global_resources() < 100:
            return True
        return False

    def _evaluate_threats_and_tactical_mode(self, ct: Controller) -> int:
        """Scan nearby entities for threats with calibrated distance thresholds and set SLOT_TACTICAL_MODE."""
        core_footprint = get_core_footprint(ct)
        threat = self.classifier.evaluate(ct, core_footprint)
        curr_round = ct.get_current_round()
        old_mode = ct.read_store(Config.SLOT_TACTICAL_MODE)

        flags = 0
        if threat.classification == ThreatClass.BREACH or ct.get_hp() < ct.get_max_hp():
            self.quiet_turns = 0
            mode = 2  # All-Out Defense
            ct.write_store(Config.SLOT_TACTICAL_MODE, 2)
            flags |= Config.FLAG_CORE_THREAT
            if threat.nearest_threat_pos is not None:
                ct.write_store(Config.SLOT_ALERTS, pack_pos(threat.nearest_threat_pos))
        elif threat.classification in (ThreatClass.SCOUT_SIGHTED, ThreatClass.PERIMETER_PRESSURE):
            self.quiet_turns = 0
            mode = 1  # Balanced
            ct.write_store(Config.SLOT_TACTICAL_MODE, 1)
            flags |= Config.FLAG_BALANCED_ALERT
            if threat.nearest_threat_pos is not None:
                ct.write_store(Config.SLOT_ALERTS, pack_pos(threat.nearest_threat_pos))
        else:
            self.quiet_turns += 1
            if self.quiet_turns >= Config.DECAY_QUIET_TURNS:
                mode = 0  # Decay to Eco
                ct.write_store(Config.SLOT_TACTICAL_MODE, 0)
            else:
                mode = old_mode

        if Config.DEBUG_DEFENSE and mode != old_mode:
            print(f"THREAT_ALERT round={curr_round} mode={mode} pressure={threat.pressure}", file=sys.stderr)

        # Count active team harvesters (memoized query)
        my_team = ct.get_team()
        harvester_count = sum(
            1 for bid in self.safe.get_nearby_buildings()
            if ct.get_team(bid) == my_team and ct.get_entity_type(bid) == EntityType.HARVESTER
        )

        curr_round = ct.get_current_round()
        curr_bank = ct.get_global_resources()

        # Phase G.7: Core Income Stall Monitor
        if curr_bank > self.max_ti_seen:
            self.max_ti_seen = curr_bank
            self.last_ti_growth_round = curr_round

        if (
            harvester_count > 0
            and curr_round - self.last_ti_growth_round > Config.BELT_INCOME_STALL_TURNS
        ):
            flags |= Config.FLAG_BELT_REPAIR_NEEDED
            if Config.DEBUG_ECONOMY and (curr_round % 25 == 0):
                print(
                    f"BELT_INCOME_STALL round={curr_round} harvesters={harvester_count} "
                    f"ti={curr_bank} max_ti={self.max_ti_seen} last_growth={self.last_ti_growth_round}",
                    file=sys.stderr,
                )

        # Emergency Exploration Request
        if self._should_request_scouts(ct):
            flags |= Config.FLAG_NEED_SCOUTS
            if curr_round >= Config.NO_HARVESTER_EMERGENCY_ROUND and harvester_count == 0:
                flags |= Config.FLAG_EMERGENCY_EXPLORATION
                if Config.DEBUG_ECONOMY and (curr_round % 25 == 0):
                    print(
                        f"EXPLORATION_EMERGENCY round={curr_round} harvesters={harvester_count} ti={curr_bank}",
                        file=sys.stderr,
                    )

        ct.write_store(Config.SLOT_GLOBAL_FLAGS, flags)

        # Two-Tier Defense Spending Gate:
        # Tier 1 — Baseline defense (always allowed early, threat-independent)
        baseline_allowed = (
            curr_round >= Config.BASELINE_DEFENSE_ROUND and
            curr_bank >= Config.BASELINE_DEFENSE_MIN_BANK
        )

        # Tier 2 — Reactive defense (threat-triggered or late-game surplus)
        reactive_allowed = (
            mode >= 1 or (
                curr_round > Config.DEFENSE_SPENDING_GATE_ROUND and
                curr_bank > Config.DEFENSE_SPENDING_MIN_BANK and
                harvester_count >= Config.DEFENSE_SPENDING_MIN_HARVESTERS
            )
        )

        proactive_turret = 1 if (baseline_allowed or reactive_allowed) else 0
        ct.write_store(Config.SLOT_PROACTIVE_TURRET, proactive_turret)

        return mode

    def _manage_ammo_reserves(self, ct: Controller) -> None:
        """Builders are the primary demolition force; preserve all Titanium for building economy/barriers."""
        current_ammo = ct.get_global_ammo()
        ct.write_store(Config.SLOT_AMMO_RESERVE, current_ammo)

    def _evaluate_builder_production(self, ct: Controller) -> None:
        """Evaluate Titanium bank balance and spawn builders if affordable.
        
        Executes Gate Cycling if all adjacent spawn tiles are blocked by friendly barriers.
        Broadcasts dynamic role assignments to SLOT_ROLE_ASSIGNMENT.
        """
        if ct.get_action_cooldown() != 0:
            return

        living_units = ct.get_unit_count()
        if living_units >= GameConstants.MAX_TEAM_UNITS:
            return

        mode = ct.read_store(Config.SLOT_TACTICAL_MODE)
        threat_alert = ct.read_store(Config.SLOT_ALERTS)
        threat_pressure = 1 if (mode >= 1 or threat_alert != 0) else 0
        my_team = ct.get_team()
        harvester_count = sum(
            1 for bid in self.safe.get_nearby_buildings()
            if ct.get_team(bid) == my_team and ct.get_entity_type(bid) == EntityType.HARVESTER
        )

        target_builders = self._get_target_builders(ct)
        builder_cost = ct.get_builder_bot_cost()

        # If 0 harvesters, preserve at least 20 Ti for initial harvester
        bank_floor = 20 if harvester_count == 0 else 0

        curr_ti = ct.get_global_resources()

        # Check affordability
        if living_units < target_builders and curr_ti >= builder_cost and (curr_ti - builder_cost) >= bank_floor:
            core_pos = ct.get_position()
            # 2x2 footprint adjacent spawn candidates
            spawn_candidates = [
                core_pos.add(Direction.NORTH),
                Position(core_pos.x + 1, core_pos.y).add(Direction.NORTH),
                core_pos.add(Direction.WEST),
                Position(core_pos.x, core_pos.y + 1).add(Direction.WEST),
                Position(core_pos.x + 1, core_pos.y).add(Direction.EAST),
                Position(core_pos.x + 1, core_pos.y + 1).add(Direction.EAST),
                Position(core_pos.x, core_pos.y + 1).add(Direction.SOUTH),
                Position(core_pos.x + 1, core_pos.y + 1).add(Direction.SOUTH),
            ]

            spawned_uid: Optional[int] = None

            # Try direct spawn on open tile via SafeController
            for spawn_pos in spawn_candidates:
                if ct.can_spawn(spawn_pos):
                    outcome = self.safe.safe_spawn_builder(spawn_pos)
                    if outcome.success:
                        spawned_uid = outcome.value
                        self.spawn_count += 1
                        break

            # Gate Cycling: If all spawn tiles blocked, demolish barrier at gate tile and return to spawn next tick
            if spawned_uid is None:
                gate_pos = spawn_candidates[0]  # Designated gate tile
                bid = ct.get_tile_building_id(gate_pos)
                if bid is not None and ct.get_entity_type(bid) == EntityType.BARRIER:
                    destroy_outcome = self.safe.safe_destroy(gate_pos)
                    if destroy_outcome.success:
                        return

            # Update dynamic role assignment on spawn
            if spawned_uid is not None and spawned_uid > 0:
                if self.guardian_builder_id == 0:
                    self.guardian_builder_id = spawned_uid
                elif self.reserve_builder_id == 0:
                    self.reserve_builder_id = spawned_uid

                ct.write_store(
                    Config.SLOT_ROLE_ASSIGNMENT,
                    pack_role_assignment(self.guardian_builder_id, self.reserve_builder_id),
                )

    def _get_target_builders(self, ct: Controller) -> int:
        policy = ct.read_store(Config.SLOT_EXPANSION_POLICY)
        curr_round = ct.get_current_round()
        ti = ct.get_global_resources()
        map_w = ct.get_map_width()
        map_h = ct.get_map_height()
        map_size = max(map_w, map_h)
        my_team = ct.get_team()
        harvester_count = sum(
            1 for bid in self.safe.get_nearby_buildings()
            if ct.get_team(bid) == my_team and ct.get_entity_type(bid) == EntityType.HARVESTER
        )

        # 0 Harvesters (Early Exploration & Initial Setup):
        if harvester_count == 0:
            has_known_ore = len(OreLifecycle.known_friendly_ores) > 0
            if has_known_ore:
                target = 2 if ti < 60 else 3
            else:
                # No ore sighted yet: scale scouts by map size so quadrants are searched immediately
                if map_size >= 24:
                    target = 4 if ti >= 80 else 3
                elif map_size >= 16:
                    target = 3
                else:
                    target = 2
        # 1 Harvester: ramp up builders rapidly to expand to all remaining ore nodes
        elif harvester_count == 1:
            target = 8 if ti >= 40 else 6
        # 2+ Harvesters: full production capacity
        else:
            target = Config.MAX_BUILDERS_CEILING

        return min(target, GameConstants.MAX_TEAM_UNITS)

    def _emit_validation_metrics(self, ct: Controller, mode: int, policy: int) -> None:
        """Emit structured metric line every METRIC_INTERVAL_ROUNDS for match validation."""
        core_hp_pct = self._get_core_hp_pct(ct)
        core_footprint = get_core_footprint(ct)
        pressure, _ = self._get_threat_pressure(ct, core_footprint)
        turrets, _ = self._get_defense_counts(ct, core_footprint)
        my_team = ct.get_team()
        harvester_count = sum(
            1 for bid in self.safe.get_nearby_buildings()
            if ct.get_team(bid) == my_team and ct.get_entity_type(bid) == EntityType.HARVESTER
        )
        builder_count = ct.get_unit_count()
        ti = ct.get_global_resources()
        ammo = ct.get_global_ammo()

        print(
            f"METRIC round={ct.get_current_round()} ti={ti} ammo={ammo} builders={builder_count} "
            f"harvesters={harvester_count} turrets={turrets} policy={policy} mode={mode} "
            f"hp_pct={core_hp_pct} pressure={pressure}",
            file=sys.stderr,
        )
