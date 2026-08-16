"""knight — Strategy Domain: Economy Policy.

Decouples expansion commitments from base defense noise, ensuring non-reserved
builders continue gathering Titanium without broad economic freezes.
"""

from __future__ import annotations

from config import Config
from strategy.threat import ThreatAssessment, ThreatClass


class EconomyPolicy:
    """Evaluates builder leashing, commitment preservation, and scouting triggers."""

    @staticmethod
    def should_leash_builder(
        my_id: int,
        reserved_ids: tuple[int, ...],
        threat: ThreatAssessment,
        near_core: bool,
        need_scouts: bool,
    ) -> bool:
        """Decide if a builder should flex into base defense."""
        if need_scouts:
            return False

        reserved = my_id in reserved_ids
        emergency_recall = threat.breach and near_core

        return (reserved or emergency_recall) and near_core

    @staticmethod
    def should_abort_commitment(
        my_id: int,
        reserved_ids: tuple[int, ...],
        policy: int,
        threat: ThreatAssessment,
        dist_sq_to_core: int,
    ) -> bool:
        """Decide if an active ore expansion commitment must be aborted."""
        if policy != Config.EXPANSION_LEASHED:
            return False

        reserved = my_id in reserved_ids
        close_emergency = threat.breach and (dist_sq_to_core <= (Config.CORE_LEASH_TIGHT ** 2))

        # Only reserved builders or units directly inside close emergency abort commitments
        return reserved or close_emergency

    @staticmethod
    def should_scout(
        no_ore_turns: int,
        need_scouts: bool,
        emergency_exp: bool,
        builder_id: int,
        map_dim: int,
    ) -> bool:
        """Decide if a builder should switch to frontier scouting."""
        if need_scouts or emergency_exp:
            return True
        if no_ore_turns > 20:
            return True
        if builder_id in (3, 4) and map_dim > 20:
            return True
        return False
