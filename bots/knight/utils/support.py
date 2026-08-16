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

    def try_heal_core_priority(
        self,
        ct: Controller,
        core_footprint: list[Position],
        pos_history: Optional[list[Position]] = None,
        seen_walls: Optional[set[Position]] = None,
    ) -> bool:
        """High priority: If Core is damaged, heal immediately or move adjacent to heal."""
        if not core_footprint:
            return False

        my_pos = ct.get_position()
        my_team = ct.get_team()
        core_id = ct.get_tile_building_id(core_footprint[0])
        if core_id is None or ct.get_team(core_id) != my_team:
            return False

        if ct.get_hp(core_id) >= ct.get_max_hp(core_id):
            return False

        # 1. If already adjacent to Core, heal it
        for d in (Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST):
            adj = my_pos.add(d)
            if adj in core_footprint and ct.can_act() and ct.get_global_resources() >= 1:
                outcome = self.safe.safe_heal(adj)
                if outcome.success:
                    return True

        # 2. If not adjacent, move towards nearest core tile
        if ct.get_move_cooldown() == 0:
            from spatial.movement import execute_bug2_evasion
            nearest_core = min(core_footprint, key=lambda c: my_pos.distance_squared(c))
            if execute_bug2_evasion(ct, my_pos, nearest_core, pos_history, seen_walls):
                return True

        return True

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
