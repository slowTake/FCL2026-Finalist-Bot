"""Grand_knight — Behavior Domain: MovementSkill Navigation Coordinator.

Provides robust, high-level navigation primitives routing 100% of movement actions
through SafeController for mutual exclusivity, cooldown tracking, and CPU limit validation.
Returns deterministic StepResult enums for pipeline integration.
"""

from __future__ import annotations

import sys
from typing import Any, Callable, Optional, Sequence, Set
from fcode import Controller, Direction, Position

from config import Config
from engine.outcome import Outcome, Reason
from engine.safe_controller import SafeController
from engine.step_result import StepResult
from spatial.movement import (
    choose_bug2_direction,
    find_builder_navigation_path,
    get_greedy_step,
    is_traversable,
)
from utils.geometry import is_adjacent, is_in_bounds


class MovementSkill:
    """High-level movement coordinator executing side-effect actions via SafeController."""

    def __init__(self, safe: SafeController) -> None:
        self.safe = safe
        self.cached_target: Optional[Position] = None
        self.cached_path: list[Direction] = []
        self.path_step_index: int = 0
        self.stuck_counter: int = 0

    def clear_cache(self) -> None:
        """Reset cached navigation path."""
        self.cached_target = None
        self.cached_path = []
        self.path_step_index = 0
        self.stuck_counter = 0

    def move_direction(self, direction: Direction) -> StepResult:
        """Execute directional movement through SafeController."""
        outcome = self.safe.safe_move(direction)
        return StepResult.ACTED if outcome.success else StepResult.FAILED

    def move_towards(
        self,
        ct: Controller,
        current_pos: Position,
        target_pos: Position,
        pos_history: Optional[Sequence[Position]] = None,
        seen_walls: Optional[Set[Position]] = None,
    ) -> StepResult:
        """Plan and execute a single step toward target_pos with short-term path caching."""
        if current_pos == target_pos or is_adjacent(current_pos, target_pos):
            self.clear_cache()
            return StepResult.FAILED

        if ct.get_move_cooldown() != 0 or self.safe.movement_used:
            return StepResult.HOLDING

        # Check cached path validity
        if self.cached_target == target_pos and self.path_step_index < len(self.cached_path):
            next_step = self.cached_path[self.path_step_index]
            dest = current_pos.add(next_step)
            if ct.can_move(next_step) and is_traversable(ct, dest, seen_walls):
                # Verify not occupied by another builder
                try:
                    if ct.get_tile_builder_bot_id(dest) is None:
                        outcome = self.safe.safe_move(next_step)
                        if outcome.success:
                            self.path_step_index += 1
                            self.stuck_counter = 0
                            return StepResult.ACTED
                except Exception:
                    pass

        # Recompute path or use bug2 direction selection
        dir_choice = choose_bug2_direction(
            ct, current_pos, target_pos, pos_history=pos_history, seen_walls=seen_walls
        )
        if dir_choice is not None and dir_choice != Direction.CENTRE:
            outcome = self.safe.safe_move(dir_choice)
            if outcome.success:
                self.stuck_counter = 0
                return StepResult.ACTED
            else:
                self.stuck_counter += 1
                if self.stuck_counter >= 3:
                    self.clear_cache()

        return StepResult.FAILED

    def move_within_allowed(
        self,
        ct: Controller,
        current_pos: Position,
        target_pos: Position,
        allowed_fn: Callable[[Position], bool],
        pos_history: Optional[Sequence[Position]] = None,
        seen_walls: Optional[Set[Position]] = None,
    ) -> StepResult:
        """Move toward target while ensuring all stepped-on tiles satisfy allowed_fn."""
        if current_pos == target_pos:
            return StepResult.FAILED

        if ct.get_move_cooldown() != 0 or self.safe.movement_used:
            return StepResult.HOLDING

        dir_choice = choose_bug2_direction(
            ct, current_pos, target_pos, pos_history=pos_history, seen_walls=seen_walls
        )
        if dir_choice is not None and dir_choice != Direction.CENTRE:
            dest = current_pos.add(dir_choice)
            if allowed_fn(dest):
                outcome = self.safe.safe_move(dir_choice)
                if outcome.success:
                    return StepResult.ACTED

        # Fallback: check cardinal directions satisfying allowed_fn
        for d in (Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST):
            dest = current_pos.add(d)
            if is_in_bounds(ct, dest) and allowed_fn(dest) and is_traversable(ct, dest, seen_walls):
                outcome = self.safe.safe_move(d)
                if outcome.success:
                    return StepResult.ACTED

        return StepResult.FAILED

    def retreat_towards(
        self,
        ct: Controller,
        current_pos: Position,
        safe_pos: Position,
        pos_history: Optional[Sequence[Position]] = None,
        seen_walls: Optional[Set[Position]] = None,
    ) -> StepResult:
        """Execute retreat movement strictly toward a safe position or Core footprint."""
        return self.move_towards(
            ct, current_pos, safe_pos, pos_history=pos_history, seen_walls=seen_walls
        )

    def retreat_inside(
        self,
        ct: Controller,
        current_pos: Position,
        core_footprint: list[Position],
        pos_history: Optional[Sequence[Position]] = None,
        seen_walls: Optional[Set[Position]] = None,
    ) -> StepResult:
        """Retreat safely inside the Core footprint or interior apron."""
        if not core_footprint:
            return StepResult.FAILED

        core_anchor = core_footprint[0]
        return self.move_towards(
            ct, current_pos, core_anchor, pos_history=pos_history, seen_walls=seen_walls
        )

    def step_greedy(self, current_pos: Position, target_pos: Position) -> Outcome:
        """Execute single direct greedy cardinal step toward target_pos."""
        if current_pos == target_pos:
            return Outcome(success=True, reason=Reason.OK)
        d = get_greedy_step(current_pos, target_pos)
        if d != Direction.CENTRE and d.is_cardinal():
            return self.safe.safe_move(d)
        return Outcome(success=False, reason=Reason.INVALID_DIRECTION)
