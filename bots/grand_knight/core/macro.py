"""Grand_knight — Core Domain: Macro Evaluation, Threat Detection & Gate Cycling Spawner.

Evaluates game phases, threat detection, and tactical mode transitions each Core
turn, then delegates to the expansion-policy hysteresis, builder-intent broadcast,
and builder-production sub-evaluators (core/expansion_policy.py, core/intent_broadcast.py,
core/builder_production.py) to author their respective store slots.
"""

from __future__ import annotations

import sys
from typing import Optional
from fcode import Controller, EntityType, Position

from config import Config
from core.builder_production import BuilderProductionManager
from core.expansion_policy import ExpansionPolicy
from core.intent_broadcast import IntentBroadcaster
from engine.safe_controller import SafeController
from strategy.threat import ThreatClassifier, ThreatClass, get_core_hp_pct, get_defense_counts
from utils.geometry import get_core_footprint, pack_pos, pack_position


class CoreMacro:
    """Macro state evaluator executing on the Core unit."""

    def __init__(self, safe: Optional[SafeController] = None) -> None:
        self.safe = safe or SafeController(None)  # type: ignore
        self.classifier = ThreatClassifier(self.safe)
        self.expansion_policy = ExpansionPolicy(self.safe)
        self.intent_broadcaster = IntentBroadcaster()
        self.builder_production = BuilderProductionManager(self.safe)

        self.quiet_turns: int = 0

        self.max_ti_seen: int = 0
        self.last_ti_growth_round: int = 0

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
        policy = self.expansion_policy.evaluate(ct, tactical_mode)

        # Gate opportunistic raiding on a stable, unthreatened economy
        self._evaluate_offense_readiness(ct, policy)

        # Broadcast strategic intent for builders
        self.intent_broadcaster.broadcast(ct, policy)

        # Update Slot 7 (SLOT_TURRET_COUNT) and Slot 8 (SLOT_BUILDER_COUNT)
        live_turret_count = sum(
            1 for bid in self.safe.get_nearby_buildings()
            if ct.get_team(bid) == ct.get_team() and ct.get_entity_type(bid) in (EntityType.SENTINEL, EntityType.GUNNER)
        )
        ct.write_store(Config.SLOT_TURRET_COUNT, live_turret_count)
        ct.write_store(Config.SLOT_BUILDER_COUNT, max(0, ct.get_unit_count() - 1))

        # Manage predictive and reactive ammo reserves
        self._manage_ammo_reserves(ct)

        # Spawn economy and guardian builders
        self.builder_production.evaluate(ct)

        # Periodic telemetry broadcast
        if Config.DEBUG_METRICS and (curr_round % Config.METRIC_INTERVAL_ROUNDS == 0):
            self._emit_validation_metrics(ct, tactical_mode, policy)

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

    def _should_request_scouts(self, ct: Controller) -> bool:
        """True if the team appears to be starving for ore discovery.

        Judged by round progress and bank alone -- both reliable globally,
        unlike a harvester count read off Core's own local vision (which
        undercounts once expansion pushes harvesters out of Core's sensing
        range; a low bank this late is itself sufficient evidence something
        needs scouting, whether or not Core can currently see why).
        """
        curr_rnd = ct.get_current_round()
        if curr_rnd >= Config.EXPLORATION_EMERGENCY_ROUND and ct.get_global_resources() < 50:
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

        curr_round = ct.get_current_round()
        curr_bank = ct.get_global_resources()

        # Core income stall monitor
        if curr_bank > self.max_ti_seen:
            self.max_ti_seen = curr_bank
            self.last_ti_growth_round = curr_round

        # Guarded by round instead of a harvester count (see _should_request_scouts):
        # gives the stall window a chance to have elapsed at least once before
        # treating flat income as suspicious, without needing a count Core's
        # local vision can't reliably provide once harvesters spread out.
        if (
            curr_round >= Config.BELT_INCOME_STALL_TURNS
            and curr_round - self.last_ti_growth_round > Config.BELT_INCOME_STALL_TURNS
        ):
            flags |= Config.FLAG_BELT_REPAIR_NEEDED
            if Config.DEBUG_ECONOMY and (curr_round % 25 == 0):
                print(
                    f"BELT_INCOME_STALL round={curr_round} "
                    f"ti={curr_bank} max_ti={self.max_ti_seen} last_growth={self.last_ti_growth_round}",
                    file=sys.stderr,
                )

        # Emergency Exploration Request
        if self._should_request_scouts(ct):
            flags |= Config.FLAG_NEED_SCOUTS
            if curr_round >= Config.NO_HARVESTER_EMERGENCY_ROUND and curr_bank < 50:
                flags |= Config.FLAG_EMERGENCY_EXPLORATION
                if Config.DEBUG_ECONOMY and (curr_round % 25 == 0):
                    print(f"EXPLORATION_EMERGENCY round={curr_round} ti={curr_bank}", file=sys.stderr)

        ct.write_store(Config.SLOT_GLOBAL_FLAGS, flags)

        # Two-Tier Defense Spending Gate:
        # Tier 1 — Baseline defense (always allowed early, threat-independent)
        baseline_allowed = (
            curr_round >= Config.BASELINE_DEFENSE_ROUND and
            curr_bank >= Config.BASELINE_DEFENSE_MIN_BANK
        )

        # Tier 2 — Reactive defense (threat-triggered or late-game surplus).
        # Bank alone stands in for "economy is doing fine" -- it's a
        # reliable global number, unlike a harvester count read off Core's
        # own local vision.
        reactive_allowed = (
            mode >= 1 or (
                curr_round > Config.DEFENSE_SPENDING_GATE_ROUND and
                curr_bank > Config.DEFENSE_SPENDING_MIN_BANK
            )
        )

        proactive_turret = 1 if (baseline_allowed or reactive_allowed) else 0
        ct.write_store(Config.SLOT_PROACTIVE_TURRET, proactive_turret)

        return mode

    def _evaluate_offense_readiness(self, ct: Controller, policy: int) -> None:
        """Set FLAG_OFFENSE_READY once enough of the match has passed --
        scaled by map size -- that the economy can plausibly spare an idle
        builder or two for opportunistic raiding.

        A round-based schedule instead of a bank/harvester-count threshold:
        the latter needs a cross-map harvester count Core's local vision
        can't reliably provide once expansion pushes harvesters out of
        range (see builder_production.py's target-population note for the
        same problem). "Not under attack" is already covered without extra
        checks -- policy only reaches EXPANSION_FREE once ExpansionPolicy's
        own threat/core-hp gates have cleared. Read-modify-write -- preserves
        every other flag bit _evaluate_threats_and_tactical_mode already set
        this tick.
        """
        map_scale = max(ct.get_map_width(), ct.get_map_height())
        ready = (
            policy == Config.EXPANSION_FREE
            and ct.get_current_round() >= Config.OFFENSE_READY_ROUNDS_PER_TILE * map_scale
        )
        flags = ct.read_store(Config.SLOT_GLOBAL_FLAGS)
        new_flags = (flags | Config.FLAG_OFFENSE_READY) if ready else (flags & ~Config.FLAG_OFFENSE_READY)
        if new_flags != flags:
            ct.write_store(Config.SLOT_GLOBAL_FLAGS, new_flags)

    def _manage_ammo_reserves(self, ct: Controller) -> None:
        """Builders are the primary demolition force; preserve all Titanium for building economy/barriers."""
        current_ammo = ct.get_global_ammo()
        ct.write_store(Config.SLOT_AMMO_RESERVE, current_ammo)

    def _emit_validation_metrics(self, ct: Controller, mode: int, policy: int) -> None:
        """Emit structured metric line every METRIC_INTERVAL_ROUNDS for match validation."""
        core_hp_pct = get_core_hp_pct(ct)
        core_footprint = get_core_footprint(ct)
        pressure, _ = self._get_threat_pressure(ct, core_footprint)
        turrets, _ = get_defense_counts(self.safe, ct, core_footprint)
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

    run_core = run_core_turn
    run_core_macro = run_core_turn
