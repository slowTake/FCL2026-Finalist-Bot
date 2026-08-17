"""Grand_knight — Core Domain: Expansion Policy Hysteresis.

Smooths the raw per-turn expansion policy signal (Free/Partial/Leashed) into a
stable, debounced policy: immediate downgrade to Leashed on danger, but upgrades
(and the Partial->Free fast-downgrade) require several consecutive stable turns
before committing, to prevent policy flapping.
"""

from __future__ import annotations

import sys
from typing import Optional
from fcode import Controller

from config import Config
from engine.safe_controller import SafeController
from strategy.threat import ThreatClassifier, get_core_hp_pct, get_defense_counts
from utils.geometry import get_core_footprint


class ExpansionPolicy:
    """Debounced Free/Partial/Leashed expansion policy state machine."""

    def __init__(self, safe: SafeController) -> None:
        self.safe = safe
        self.classifier = ThreatClassifier(safe)

        self.current_policy: int = Config.EXPANSION_FREE
        self.candidate_policy: int = Config.EXPANSION_FREE
        self.candidate_stable_turns: int = 0
        self.last_policy: Optional[int] = None

        self.last_core_hp: Optional[int] = None
        self.last_core_damage_round: int = -10000

    def evaluate(self, ct: Controller, tactical_mode: int) -> int:
        """Decouple economy expansion leashing from combat mode with hysteresis smoothing."""
        core_hp_pct = get_core_hp_pct(ct)
        recently_damaged = self._track_core_damage(ct)
        core_footprint = get_core_footprint(ct)
        threat = self.classifier.evaluate(ct, core_footprint)
        turret_count, barrier_count = get_defense_counts(self.safe, ct, core_footprint)
        curr_rnd = ct.get_current_round()

        # Priority-ordered danger checks -- most severe wins
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

        policy = self._smooth(raw_policy, ct)

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

    def _track_core_damage(self, ct: Controller) -> bool:
        """Track damage events and return True if Core took damage within RECENT_DAMAGE_WINDOW_TURNS."""
        curr_hp = ct.get_hp()
        curr_round = ct.get_current_round()
        if self.last_core_hp is not None and curr_hp < self.last_core_hp:
            self.last_core_damage_round = curr_round
        self.last_core_hp = curr_hp
        return (curr_round - self.last_core_damage_round) <= Config.RECENT_DAMAGE_WINDOW_TURNS

    def _smooth(self, raw_policy: int, ct: Controller) -> int:
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
        if raw_policy == Config.EXPANSION_PARTIAL and old_policy == Config.EXPANSION_FREE:
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
