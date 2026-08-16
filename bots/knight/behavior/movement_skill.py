"""knight — Behavior Domain: MovementSkill Navigation Coordinator.

Provides robust, high-level navigation primitives routing 100% of movement actions
through SafeController for mutual exclusivity, cooldown tracking, and CPU limit validation.
"""

from __future__ import annotations

from typing import Any, Callable, Optional, Sequence, Set
from fcode import Controller, Direction, Position

from engine.outcome import Outcome, Reason
from engine.safe_controller import SafeController
from spatial.movement import (
    choose_bug2_direction,
    find_builder_navigation_path,
    get_greedy_step,
    is_traversable,
)
from utils.geometry import is_in_bounds


class MovementSkill:
    """High-level movement coordinator executing side-effect actions via SafeController."""

    def __init__(self, safe: SafeController) -> None:
        self.safe = safe

    def move_direction(self, direction: Direction) -> Outcome:
        """Execute directional movement through SafeController."""
        return self.safe.safe_move(direction)

    def move_towards(
        self,
        ct: Controller,
        current_pos: Position,
        target_pos: Position,
        pos_history: Optional[Sequence[Position]] = None,
        seen_walls: Optional[Set[Position]] = None,
    ) -> Outcome:
        """Plan and execute a single step toward target_pos using BFS and bug2 evasion."""
        if current_pos == target_pos:
            return Outcome(success=True, reason=Reason.OK)

        dir_choice = choose_bug2_direction(
            ct, current_pos, target_pos, pos_history=pos_history, seen_walls=seen_walls
        )
        if dir_choice is not None and dir_choice != Direction.CENTRE:
            return self.safe.safe_move(dir_choice)

        return Outcome(success=False, reason=Reason.BLOCKED)

    def move_within_allowed(
        self,
        ct: Controller,
        current_pos: Position,
        target_pos: Position,
        allowed_fn: Callable[[Position], bool],
        pos_history: Optional[Sequence[Position]] = None,
        seen_walls: Optional[Set[Position]] = None,
    ) -> Outcome:
        """Move toward target while ensuring all stepped-on tiles satisfy allowed_fn."""
        if current_pos == target_pos:
            return Outcome(success=True, reason=Reason.OK)

        dir_choice = choose_bug2_direction(
            ct, current_pos, target_pos, pos_history=pos_history, seen_walls=seen_walls
        )
        if dir_choice is not None and dir_choice != Direction.CENTRE:
            dest = current_pos.add(dir_choice)
            if allowed_fn(dest):
                return self.safe.safe_move(dir_choice)

        # Fallback: check cardinal directions satisfying allowed_fn
        for d in (Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST):
            dest = current_pos.add(d)
            if is_in_bounds(ct, dest) and allowed_fn(dest) and is_traversable(ct, dest, seen_walls):
                return self.safe.safe_move(d)

        return Outcome(success=False, reason=Reason.BLOCKED)

    def retreat_inside(
        self,
        ct: Controller,
        current_pos: Position,
        core_footprint: list[Position],
        pos_history: Optional[Sequence[Position]] = None,
        seen_walls: Optional[Set[Position]] = None,
    ) -> Outcome:
        """Retreat safely inside the Core footprint or interior apron."""
        if not core_footprint:
            return Outcome(success=False, reason=Reason.BLOCKED)

        core_anchor = core_footprint[0]
        return self.move_towards(
            ct, current_pos, core_anchor, pos_history=pos_history, seen_walls=seen_walls
        )

    def step_greedy(self, current_pos: Position, target_pos: Position) -> Outcome:
        """Execute single direct greedy cardinal step toward target_pos."""
        direction = get_greedy_step(current_pos, target_pos)
        if direction == Direction.CENTRE:
            return Outcome(success=True, reason=Reason.OK)
        return self.safe.safe_move(direction)
