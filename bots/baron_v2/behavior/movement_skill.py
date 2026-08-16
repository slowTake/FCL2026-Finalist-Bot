"""knight — Behavior Domain: MovementSkill Navigation Adapter.

Wraps all unit movement through SafeController to enforce mutual exclusivity,
cooldown validation, and structured Outcome receipts.
"""

from __future__ import annotations

from typing import Optional, Sequence, Set
from fcode import Controller, Direction, Position

from engine.outcome import Outcome, Reason
from engine.safe_controller import SafeController
from spatial.movement import choose_bug2_direction, get_greedy_step, is_traversable


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
        """Plan and execute a step toward target_pos using BFS and bug2 evasion."""
        if current_pos == target_pos:
            return Outcome(success=True, reason=Reason.ALREADY_VALID)

        dir_choice = choose_bug2_direction(
            ct, current_pos, target_pos, pos_history=pos_history, seen_walls=seen_walls
        )
        if dir_choice is not None and dir_choice != Direction.CENTRE:
            return self.safe.safe_move(dir_choice)

        return Outcome(success=False, reason=Reason.BLOCKED)

    def step_greedy(self, current_pos: Position, target_pos: Position) -> Outcome:
        """Execute single direct greedy cardinal step toward target_pos."""
        direction = get_greedy_step(current_pos, target_pos)
        if direction == Direction.CENTRE:
            return Outcome(success=True, reason=Reason.ALREADY_VALID)
        return self.safe.safe_move(direction)
