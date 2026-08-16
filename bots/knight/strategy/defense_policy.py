"""knight — Strategy Domain: Defense Policy.

Evaluates base defense requirements, guardian role transitions, repair necessity,
and defense spending priorities without freezing macro expansion.
"""

from __future__ import annotations

from typing import Tuple
from config import Config
from strategy.threat import ThreatAssessment, ThreatClass


class DefensePolicy:
    """Evaluates base defense rules and priority spending decisions."""

    @staticmethod
    def defenders_needed(threat: ThreatAssessment, builder_count: int) -> int:
        """Return the target number of base defenders (0, 1, or max 2)."""
        if threat.classification == ThreatClass.SAFE:
            return 1 if builder_count >= 2 else 0
        if threat.classification == ThreatClass.SCOUT_SIGHTED:
            return 1
        if threat.classification in (ThreatClass.PERIMETER_PRESSURE, ThreatClass.BREACH):
            return min(Config.MAX_BASE_DEFENDERS, max(1, builder_count // 3))
        return 1

    @staticmethod
    def should_prioritize_turret(
        turret_count: int,
        barrier_count: int,
        threat: ThreatAssessment,
        round_num: int,
    ) -> Tuple[bool, str]:
        """Determine if turret construction should precede remaining barrier ring completion."""
        if turret_count == 0 and threat.pressure >= Config.EMERGENCY_TURRET_PRESSURE:
            return True, "EMERGENCY_PRESSURE"

        if turret_count == 0 and round_num >= Config.TURRET_PRIORITY_ROUND:
            return True, "PRIORITY_ROUND"

        if turret_count == 0 and round_num >= Config.DEFENSE_DEADLINE_ROUND:
            return True, "DEADLINE_ROUND"

        if barrier_count >= Config.MIN_BARRIERS_BEFORE_TURRET:
            return True, "BARRIERS_MET"

        return False, ""

    @staticmethod
    def should_guardian_retreat(hp: int, threat: ThreatAssessment, near_core: bool) -> bool:
        """Determine if Guardian should pull inside the apron for self-preservation."""
        if hp < Config.RETREAT_HP_THRESHOLD:
            return True
        if hp < Config.GUARDIAN_EXTERIOR_PANIC_HP and threat.pressure > 0:
            return True
        return False

    @staticmethod
    def needs_emergency_repair(core_hp_pct: int, threat: ThreatAssessment) -> bool:
        """True if structural healing is urgent."""
        return core_hp_pct < Config.CORE_HP_SECURE_PCT or threat.breach
