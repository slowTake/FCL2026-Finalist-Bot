"""knight — Core Domain: Macro Evaluation, Threat Detection & Gate Cycling Spawner.

Evaluates game phases, threat detection, tactical mode transitions, predictive ammo
conversion, smoothed expansion policy (with hysteresis), role assignment broadcasts,
authoritative builder intents, and Core gate cycling to spawn Builder Bots through barrier walls.
"""

from __future__ import annotations

import sys
from typing import Optional
from fcode import Controller, Direction, EntityType, GameConstants, Position

from config import Config
from engine.safe_controller import SafeController
from utils.geometry import (
    get_core_footprint,
    is_valid_ore_target,
    pack_builder_intent,
    pack_ore_discovery,
    pack_pos,
    pack_position,
    pack_role_assignment,
    unpack_builder_intent,
    unpack_ore_discovery,
    unpack_pos,
    unpack_position,
    unpack_role_assignment,
)


class CoreMacro:
    """Macro state evaluator executing on the Core unit."""

    def __init__(self, safe: Optional[SafeController] = None) -> None:
        self.safe = safe or SafeController(None)  # type: ignore
        self.spawn_count: int = 0
        self.quiet_turns: int = 0
        self.last_core_hp: Optional[int] = None
        self.last_core_damage_round: int = -10000
        self.last_policy: Optional[int] = None

        # Hysteresis state for expansion policy
        self.current_policy: int = Config.EXPANSION_FREE
        self.candidate_policy: int = Config.EXPANSION_FREE
        self.candidate_stable_turns: int = 0

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

        for eid in self.safe.get_nearby_entities(Config.THREAT_RADIUS_SQ):
            if ct.get_team(eid) != my_team:
                etype = ct.get_entity_type(eid)
                epos = ct.get_position(eid)

                if etype in (EntityType.GUNNER, EntityType.SENTINEL, EntityType.LAUNCHER):
                    threat_pressure += 2
                    for cp in core_footprint:
                        if epos.distance_squared(cp) <= 4:
                            breach = True
                            break
                elif etype == EntityType.BUILDER_BOT:
                    threat_pressure += 1
                else:
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

        if self.candidate_policy == self.current_policy:
            return self.current_policy

        required_turns = Config.POLICY_UPGRADE_STABLE_TURNS
        if self.current_policy == Config.EXPANSION_PARTIAL and self.candidate_policy == Config.EXPANSION_FREE:
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
        threat_pressure, breach = self._get_threat_pressure(ct, core_footprint)
        turret_count, barrier_count = self._get_defense_counts(ct, core_footprint)
        my_team = ct.get_team()
        harvester_count = sum(
            1 for bid in self.safe.get_nearby_buildings()
            if ct.get_team(bid) == my_team and ct.get_entity_type(bid) == EntityType.HARVESTER
        )

        if breach:
            raw_policy = Config.EXPANSION_LEASHED
        elif core_hp_pct < Config.CORE_HP_CRITICAL_PCT:
            raw_policy = Config.EXPANSION_LEASHED
        elif recently_damaged:
            raw_policy = Config.EXPANSION_LEASHED
        elif harvester_count == 0 and core_hp_pct >= Config.CORE_HP_SECURE_PCT:
            # Prevent circular deadlock: allow expansion to find first ore when Core is secure
            raw_policy = Config.EXPANSION_PARTIAL if tactical_mode > 0 else Config.EXPANSION_FREE
        elif tactical_mode == 2:
            base_secure = (
                core_hp_pct >= Config.CORE_HP_SECURE_PCT
                and turret_count >= Config.UNSHACKLE_MIN_DEFENSE_TURRETS
                and barrier_count >= Config.UNSHACKLE_MIN_BARRIER_COUNT
            )
            if base_secure and threat_pressure <= Config.UNSHACKLE_MAX_THREAT_PRESSURE:
                raw_policy = Config.EXPANSION_PARTIAL
            else:
                raw_policy = Config.EXPANSION_LEASHED
        elif tactical_mode == 1:
            if core_hp_pct >= Config.CORE_HP_SECURE_PCT:
                raw_policy = Config.EXPANSION_PARTIAL
            else:
                raw_policy = Config.EXPANSION_LEASHED
        else:
            raw_policy = Config.EXPANSION_FREE

        policy = self._smooth_expansion_policy(raw_policy, ct)

        ct.write_store(Config.SLOT_EXPANSION_POLICY, policy)
        ct.write_store(Config.SLOT_BASE_INTEGRITY, core_hp_pct)

        if (Config.DEBUG_ECONOMY or Config.DEBUG_DEFENSE) and policy != self.last_policy:
            print(
                f"POLICY round={ct.get_current_round()} mode={tactical_mode} policy={policy} "
                f"hp_pct={core_hp_pct} turrets={turret_count} barriers={barrier_count} "
                f"pressure={threat_pressure} breach={breach}",
                file=sys.stderr,
            )
            self.last_policy = policy

        return policy

    def _broadcast_strategic_intent(self, ct: Controller, policy: int) -> None:
        """Author authoritative strategic intent into SLOT_BUILDER_INTENT."""
        if policy == Config.EXPANSION_LEASHED:
            core_pos = ct.get_position()
            val = pack_builder_intent(core_pos.x, core_pos.y, Config.INTENT_DEFEND_CORE, 15)
            ct.write_store(Config.SLOT_BUILDER_INTENT, val)
        else:
            val_ore = ct.read_store(Config.SLOT_ORE_DISCOVERY)
            ore_pos, round_mod = unpack_ore_discovery(val_ore)
            curr_rnd = ct.get_current_round()
            core_footprint = get_core_footprint(ct)
            if ore_pos is not None and is_valid_ore_target(ct, ore_pos, core_footprint):
                age = (curr_rnd - round_mod) % 256
                if age <= Config.ORE_DISCOVERY_TTL_ROUNDS:
                    val = pack_builder_intent(ore_pos.x, ore_pos.y, Config.INTENT_EXPAND_ORE, 10)
                    ct.write_store(Config.SLOT_BUILDER_INTENT, val)
                    return
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
        """Scan nearby entities for threats and set SLOT_TACTICAL_MODE and SLOT_GLOBAL_FLAGS."""
        core_pos = ct.get_position()
        my_team = ct.get_team()

        nearest_threat_pos: Optional[Position] = None
        nearest_threat_dist = 10**9
        enemy_spotted = False
        armed_combat_near_core = False

        # Mode 2 Condition 1: Core is taking damage
        core_damaged = ct.get_hp() < ct.get_max_hp()

        for eid in self.safe.get_nearby_entities():
            if ct.get_team(eid) != my_team:
                enemy_spotted = True
                epos = ct.get_position(eid)
                d = core_pos.distance_squared(epos)
                etype = ct.get_entity_type(eid)

                # Armed military units (Gunners, Sentinels, Launchers) within 4 tiles (d <= 16)
                if etype in (EntityType.GUNNER, EntityType.SENTINEL, EntityType.LAUNCHER) and d <= Config.THREAT_RADIUS_SQ:
                    armed_combat_near_core = True
                    if d < nearest_threat_dist:
                        nearest_threat_dist = d
                        nearest_threat_pos = epos
                elif d < nearest_threat_dist:
                    nearest_threat_dist = d
                    nearest_threat_pos = epos

        flags = 0
        if core_damaged or armed_combat_near_core:
            self.quiet_turns = 0
            mode = 2  # All-Out Defense
            ct.write_store(Config.SLOT_TACTICAL_MODE, 2)
            flags |= Config.FLAG_CORE_THREAT
            if nearest_threat_pos is not None:
                ct.write_store(Config.SLOT_ALERTS, pack_pos(nearest_threat_pos))
        elif enemy_spotted:
            self.quiet_turns = 0
            mode = 1  # Balanced
            ct.write_store(Config.SLOT_TACTICAL_MODE, 1)
            flags |= Config.FLAG_BALANCED_ALERT
            if nearest_threat_pos is not None:
                ct.write_store(Config.SLOT_ALERTS, pack_pos(nearest_threat_pos))
        else:
            self.quiet_turns += 1
            if self.quiet_turns >= Config.DECAY_QUIET_TURNS:
                mode = 0  # Decay to Eco
                ct.write_store(Config.SLOT_TACTICAL_MODE, 0)
            else:
                mode = ct.read_store(Config.SLOT_TACTICAL_MODE)

        # Count active team harvesters (memoized query)
        harvester_count = sum(
            1 for bid in self.safe.get_nearby_buildings()
            if ct.get_team(bid) == my_team and ct.get_entity_type(bid) == EntityType.HARVESTER
        )

        curr_round = ct.get_current_round()
        curr_bank = ct.get_global_resources()

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
        """Predictively convert Titanium to Ammo for baseline, emergency, and reactive defense."""
        current_ammo = ct.get_global_ammo()
        current_ti = ct.get_global_resources()
        my_team = ct.get_team()
        curr_round = ct.get_current_round()
        mode = ct.read_store(Config.SLOT_TACTICAL_MODE)
        threat_alert = ct.read_store(Config.SLOT_ALERTS)
        threat_pressure = 1 if (mode >= 1 or threat_alert != 0) else 0

        # Count active team turrets near Core
        turret_count = sum(
            1 for bid in self.safe.get_nearby_buildings()
            if ct.get_team(bid) == my_team and ct.get_entity_type(bid) in (EntityType.SENTINEL, EntityType.GUNNER)
        )

        # Count active team harvesters (memoized query)
        harvester_count = sum(
            1 for bid in self.safe.get_nearby_buildings()
            if ct.get_team(bid) == my_team and ct.get_entity_type(bid) == EntityType.HARVESTER
        )

        # Hotfix 4: Defense Ammo Safety (convert ammo before turret construction)
        if turret_count == 0:
            if curr_round >= Config.BASELINE_DEFENSE_ROUND or threat_pressure > 0:
                if current_ammo < 10 and current_ti >= 40:
                    if ct.can_convert_ammo(10):
                        ct.convert_ammo(10)
                        current_ammo += 10
                        current_ti -= 10
        elif threat_pressure > 0:
            if current_ammo < 10 and current_ti >= 40:
                if ct.can_convert_ammo(10):
                    ct.convert_ammo(10)
                    current_ammo += 10
                    current_ti -= 10

        # Tier 1 Baseline Ammo Seed: Convert up to BASELINE_AMMO_RESERVE (~20) whenever bank >= 40 Ti
        baseline_ammo_allowed = (
            curr_round >= Config.BASELINE_DEFENSE_ROUND and
            current_ti >= 40
        )

        # Tier 2 Reactive Ammo Scaling: Scale with active turret count when threatened
        reactive_ammo_allowed = (
            mode >= 1 or (
                curr_round > Config.DEFENSE_SPENDING_GATE_ROUND and
                current_ti > Config.DEFENSE_SPENDING_MIN_BANK and
                harvester_count >= Config.DEFENSE_SPENDING_MIN_HARVESTERS
            )
        )

        desired_ammo = 0
        if reactive_ammo_allowed:
            desired_ammo = max(Config.AMMO_CONVERT_MAX_RESERVE, turret_count * 10)
        elif baseline_ammo_allowed:
            desired_ammo = Config.BASELINE_AMMO_RESERVE

        # Scaled ammo conversion based on turret count and Ti bank
        if current_ammo < desired_ammo and current_ti >= 10:
            if ct.can_convert_ammo(10):
                ct.convert_ammo(10)
                current_ammo += 10

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

        target_builders = self._get_target_builders(ct)
        builder_cost = ct.get_builder_bot_cost()
        flags = ct.read_store(Config.SLOT_GLOBAL_FLAGS)
        bank_floor = (
            Config.EXPLORATION_BANK_FLOOR
            if bool(flags & Config.FLAG_EMERGENCY_EXPLORATION)
            else Config.DEFENSE_BANK_FLOOR
        )
        curr_ti = ct.get_global_resources()

        # Check affordability respecting DEFENSE_BANK_FLOOR
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
        flags = ct.read_store(Config.SLOT_GLOBAL_FLAGS)
        curr_round = ct.get_current_round()
        ti = ct.get_global_resources()
        ore_disc, _ = unpack_ore_discovery(ct.read_store(Config.SLOT_ORE_DISCOVERY))

        # Fast-start behavior: in first 10 rounds, spawn up to 4 builders unconditionally
        if curr_round <= 10:
            target = max(4, Config.MAX_BUILDERS_EARLY)
        elif ti >= 50 and ore_disc is not None:
            target = min(14, Config.MAX_BUILDERS_CEILING)
        else:
            target = Config.MAX_BUILDERS_EARLY
            for min_ti, t in Config.BUILDER_TARGET_BRACKETS:
                if ti >= min_ti:
                    target = min(t, Config.MAX_BUILDERS_CEILING)
                    break

        if bool(flags & Config.FLAG_NEED_SCOUTS):
            target = max(target, Config.SCOUT_MIN_BUILDERS_NO_HARVESTER)

        if policy != Config.EXPANSION_LEASHED and curr_round >= 60:
            target = max(target, Config.MAX_BUILDERS_BASE)

        if policy == Config.EXPANSION_FREE and ti >= 500:
            target = max(target, Config.MAX_BUILDERS_CEILING)

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
