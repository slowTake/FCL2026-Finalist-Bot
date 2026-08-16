"""knight — Shared Support Skills (Heal Duty & Field Repairs).

Provides reusable support abilities across combat and logistics roles.
"""

from __future__ import annotations

from typing import Optional
from fcode import Controller, Direction, Position

from config import Config
from engine.safe_controller import SafeController
from utils.geometry import is_adjacent, is_in_bounds, is_in_vision


class SupportSkill:
    """Shared support capabilities including structure repair and unit healing."""

    def __init__(self, safe: SafeController) -> None:
        self.safe = safe

    def try_heal_damaged_structures(self, ct: Controller) -> bool:
        """Priority: Heal damaged friendly buildings/units on orthogonally adjacent tiles (+4 HP for 1 Ti)."""
        if not ct.can_act() or ct.get_global_resources() < 1:
            return False

        pos = ct.get_position()
        my_team = ct.get_team()

        for d in (Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST):
            adj = pos.add(d)
            if is_in_vision(ct, pos, adj, Config.BUILDER_BOT_VISION_RADIUS_SQ):
                bid = ct.get_tile_building_id(adj)
                if bid is not None and ct.get_team(bid) == my_team:
                    if ct.get_hp(bid) < ct.get_max_hp(bid):
                        outcome = self.safe.safe_heal(adj)
                        if outcome.success:
                            return True
                uid = ct.get_tile_builder_bot_id(adj)
                if uid is not None and ct.get_team(uid) == my_team:
                    if ct.get_hp(uid) < ct.get_max_hp(uid):
                        outcome = self.safe.safe_heal(adj)
                        if outcome.success:
                            return True
        return False
