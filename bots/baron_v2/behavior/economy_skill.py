"""baron_v2 — Multi-Belt Expansion & Dynamic Re-Role Skill.

Orchestrates harvester placement, wall-aware conveyor belt construction, and
immediate transition back to frontier exploration upon belt completion.
"""

from __future__ import annotations

from collections import deque
import sys
from typing import Any, Optional, Sequence, Set
from fcode import Controller, Direction, EntityType, Environment, Position

from config import Config
from core.ledger import OreRegistry, OreStatus
from engine.outcome import ActionOutcome, OutcomeReason
from engine.safe_controller import SafeController
from engine.world import WorldView
from spatial.movement import choose_bug2_direction, find_wall_aware_belt_path
from utils.geometry import get_core_footprint, is_adjacent, is_in_bounds


class EconomyState:
    SEEKING_ORE: int = 0
    BUILDING_HARVESTER: int = 1
    PAVING_BELT: int = 2
    COMPLETED_RE_ROLE: int = 3


class EconomySkill:
    """Autonomous economy expansion with instant post-paving re-role."""

    def __init__(self, safe: SafeController, world: WorldView, ore_registry: OreRegistry) -> None:
        self.safe = safe
        self.world = world
        self.ore_registry = ore_registry

        self.state = EconomyState.SEEKING_ORE
        self.target_ore: Optional[Position] = None
        self.belt_path: list[tuple[Position, Direction]] = []
        self.paving_index: int = 0
        self.belt_block_counter: int = 0
        self.pos_history: deque[Position] = deque(maxlen=8)
        self.seen_walls: set[Position] = set()

    def tick(self, ct: Controller) -> ActionOutcome:
        """Execute one economy sub-turn under 10ms CPU safety."""
        pos = ct.get_position()
        self.pos_history.append(pos)

        # Update local terrain memory
        for t in ct.get_nearby_tiles():
            if ct.get_tile_env(t) == Environment.WALL:
                self.seen_walls.add(t)

        self.ore_registry.sync_from_store(ct)

        if self.state == EconomyState.COMPLETED_RE_ROLE:
            self._reset_for_next_expansion()

        if self.state == EconomyState.SEEKING_ORE:
            return self._step_seeking_ore(ct, pos)
        elif self.state == EconomyState.BUILDING_HARVESTER:
            return self._step_building_harvester(ct, pos)
        elif self.state == EconomyState.PAVING_BELT:
            return self._step_paving_belt(ct, pos)

        return ActionOutcome(success=False, reason=OutcomeReason.NO_OP)

    def _reset_for_next_expansion(self) -> None:
        """Immediately recycle builder state to claim next ore node without idling."""
        self.state = EconomyState.SEEKING_ORE
        self.target_ore = None
        self.belt_path = []
        self.paving_index = 0
        self.belt_block_counter = 0

    def _step_seeking_ore(self, ct: Controller, pos: Position) -> ActionOutcome:
        if self.target_ore is None or not self.ore_registry.is_available(ct, self.target_ore, ct.get_id()):
            self.target_ore = self._find_best_unclaimed_ore(ct, pos)
            if self.target_ore is not None:
                self.ore_registry.register_claim(ct, self.target_ore, ct.get_id())

        if self.target_ore is None:
            return ActionOutcome(success=False, reason=OutcomeReason.NO_TARGET)

        if is_adjacent(pos, self.target_ore):
            self.state = EconomyState.BUILDING_HARVESTER
            return self._step_building_harvester(ct, pos)

        # Navigate toward target ore
        if ct.get_move_cooldown() == 0:
            step_dir = choose_bug2_direction(ct, pos, self.target_ore, self.pos_history, self.seen_walls)
            if step_dir is not None:
                return self.safe.safe_move(step_dir)

        return ActionOutcome(success=False, reason=OutcomeReason.ACTION_COOLDOWN)

    def _step_building_harvester(self, ct: Controller, pos: Position) -> ActionOutcome:
        if self.target_ore is None or not is_adjacent(pos, self.target_ore):
            self.state = EconomyState.SEEKING_ORE
            return ActionOutcome(success=False, reason=OutcomeReason.TARGET_INVALID)

        # Check if already occupied
        b_id = ct.get_tile_building_id(self.target_ore)
        if b_id is not None:
            self.ore_registry.mark_occupied(ct, self.target_ore, ct.get_id())
            self.belt_path = find_wall_aware_belt_path(ct, self.target_ore, Config.MAX_BELT_RELAY_HOPS, self.seen_walls)
            self.paving_index = 0
            self.state = EconomyState.PAVING_BELT
            return ActionOutcome(success=True, reason=OutcomeReason.SUCCESS)

        if ct.can_act() and ct.get_global_resources() >= ct.get_harvester_cost():
            outcome = self.safe.safe_build_harvester(self.target_ore)
            if outcome.success:
                self.ore_registry.mark_occupied(ct, self.target_ore, ct.get_id())
                self.belt_path = find_wall_aware_belt_path(ct, self.target_ore, Config.MAX_BELT_RELAY_HOPS, self.seen_walls)
                self.paving_index = 0
                self.state = EconomyState.PAVING_BELT
            return outcome

        return ActionOutcome(success=False, reason=OutcomeReason.INSUFFICIENT_RESOURCES)

    def _step_paving_belt(self, ct: Controller, pos: Position) -> ActionOutcome:
        if self.paving_index >= len(self.belt_path):
            # Belt successfully connected to Core! Trigger Instant Re-Role
            if Config.DEBUG_ECONOMY:
                print(f"BELT_COMPLETED builder_id={ct.get_id()} ore={self.target_ore} -> RE_ROLE", file=sys.stderr)
            self.state = EconomyState.COMPLETED_RE_ROLE
            return ActionOutcome(success=True, reason=OutcomeReason.SUCCESS)

        target_tile, facing = self.belt_path[self.paving_index]
        existing_b = ct.get_tile_building_id(target_tile)
        if existing_b is not None:
            # Tile is already paved or contains Core footprint; advance to next step
            self.paving_index += 1
            return ActionOutcome(success=True, reason=OutcomeReason.SUCCESS)

        if is_adjacent(pos, target_tile):
            if ct.can_act() and ct.get_global_resources() >= ct.get_conveyor_cost():
                outcome = self.safe.safe_build_conveyor(target_tile, facing)
                if outcome.success:
                    self.paving_index += 1
                    self.belt_block_counter = 0
                return outcome
            return ActionOutcome(success=False, reason=OutcomeReason.INSUFFICIENT_RESOURCES)

        # Move into adjacent placement range
        if ct.get_move_cooldown() == 0:
            step_dir = choose_bug2_direction(ct, pos, target_tile, self.pos_history, self.seen_walls)
            if step_dir is not None:
                return self.safe.safe_move(step_dir)

        return ActionOutcome(success=False, reason=OutcomeReason.NO_OP)

    def _find_best_unclaimed_ore(self, ct: Controller, pos: Position) -> Optional[Position]:
        """Find closest unmined, unclaimed ore node."""
        core_footprint = get_core_footprint(ct)
        best_pos = None
        best_dist = 999999

        for tile in ct.get_nearby_tiles():
            if ct.get_tile_env(tile) == Environment.ORE_TITANIUM:
                if self.ore_registry.is_available(ct, tile, ct.get_id()):
                    d = pos.distance_squared(tile)
                    if d < best_dist:
                        best_dist = d
                        best_pos = tile

        return best_pos
