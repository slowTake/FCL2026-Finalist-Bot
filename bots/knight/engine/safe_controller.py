"""knight — Safe Controller Engine Wrapper.

Wraps fcode.Controller to guarantee mutual exclusivity between movement and actions,
enforce the 10ms CPU safety limit, prevent engine exceptions, and provide deterministic Outcome receipts.
"""

from __future__ import annotations

from typing import Any, Optional
from fcode import Controller, Direction, EntityType, Position

from config import Config
from engine.cache import TurnCache
from engine.outcome import Outcome, Reason
from utils.geometry import is_adjacent, is_in_bounds


class SafeController:
    """Defensive wrapper around fcode.Controller enforcing strict engine contracts."""

    def __init__(self, ct: Optional[Controller] = None) -> None:
        self.ct = ct  # type: ignore
        self.cache = TurnCache(ct)
        self._major_action_used: bool = False

    def begin_turn(self, ct: Controller) -> None:
        """Reset tick-scoped action flags and cache at the start of a unit's turn."""
        self.ct = ct
        self.cache.reset(ct)
        self._major_action_used = False

    def get_nearby_entities(self, dist_sq: Optional[int] = None) -> list[int]:
        """Memoized ct.get_nearby_entities() query with defensive vision clamp fallback."""
        key = f"nearby_entities_{dist_sq}"
        def _fetch() -> list[int]:
            try:
                return self.ct.get_nearby_entities(dist_sq)
            except Exception:
                return self.ct.get_nearby_entities()
        return self.cache.get(key, _fetch)

    def get_nearby_buildings(self, dist_sq: Optional[int] = None) -> list[int]:
        """Memoized ct.get_nearby_buildings() query with defensive vision clamp fallback."""
        key = f"nearby_buildings_{dist_sq}"
        def _fetch() -> list[int]:
            try:
                return self.ct.get_nearby_buildings(dist_sq)
            except Exception:
                return self.ct.get_nearby_buildings()
        return self.cache.get(key, _fetch)

    def get_nearby_units(self, dist_sq: Optional[int] = None) -> list[int]:
        """Memoized ct.get_nearby_units() query with defensive vision clamp fallback."""
        key = f"nearby_units_{dist_sq}"
        def _fetch() -> list[int]:
            try:
                return self.ct.get_nearby_units(dist_sq)
            except Exception:
                return self.ct.get_nearby_units()
        return self.cache.get(key, _fetch)

    def get_nearby_tiles(self, dist_sq: Optional[int] = None) -> list[Position]:
        """Memoized ct.get_nearby_tiles() query with defensive vision clamp fallback."""
        key = f"nearby_tiles_{dist_sq}"
        def _fetch() -> list[Position]:
            try:
                return self.ct.get_nearby_tiles(dist_sq)
            except Exception:
                return self.ct.get_nearby_tiles()
        return self.cache.get(key, _fetch)

    @property
    def major_action_used(self) -> bool:
        """True if a move or major action has already been executed this tick."""
        return self._major_action_used

    def _check_preconditions(self, require_adjacent: Optional[Position] = None) -> Optional[Outcome]:
        """Check CPU budget, single action per tick, and positional bounds."""
        if self.ct.get_cpu_time_elapsed() > Config.CPU_SAFETY_LIMIT_MICROSEC:
            return Outcome(False, Reason.CPU_BUDGET, "CPU safety budget exceeded")

        if self._major_action_used:
            return Outcome(False, Reason.ALREADY_ACTED, "Major action already performed this tick")

        if require_adjacent is not None:
            if not is_in_bounds(self.ct, require_adjacent):
                return Outcome(False, Reason.OUT_OF_BOUNDS, f"Target {require_adjacent} out of map bounds")
            my_pos = self.ct.get_position()
            if not is_adjacent(my_pos, require_adjacent):
                return Outcome(False, Reason.NOT_ADJACENT, f"Target {require_adjacent} is not orthogonally adjacent to {my_pos}")

        return None

    def safe_move(self, direction: Direction) -> Outcome:
        """Safely execute a 1-step move in a cardinal direction.
        
        Outcomes:
        - OK: Movement command successfully dispatched.
        - CPU_BUDGET: CPU elapsed exceeded safety threshold.
        - ALREADY_ACTED: Major action already executed this tick.
        - INVALID_DIRECTION: Direction is CENTRE or non-cardinal.
        - COOLDOWN: Move cooldown is non-zero.
        - ENGINE_REJECTED: Engine can_move returned False (e.g. wall/obstacle).
        """
        err = self._check_preconditions()
        if err is not None:
            return err

        if not direction.is_cardinal() or direction == Direction.CENTRE:
            return Outcome(False, Reason.INVALID_DIRECTION, f"Direction {direction} is not a valid cardinal move")

        if self.ct.get_move_cooldown() != 0:
            return Outcome(False, Reason.COOLDOWN, f"Move cooldown active: {self.ct.get_move_cooldown()}")

        if not self.ct.can_move(direction):
            return Outcome(False, Reason.ENGINE_REJECTED, f"can_move({direction}) returned False")

        self.ct.move(direction)
        self._major_action_used = True
        return Outcome(True, Reason.OK, f"Moved {direction}")

    def safe_build_barrier(self, pos: Position) -> Outcome:
        """Safely construct a 3-Ti Barrier on an adjacent tile.
        
        Outcomes:
        - OK: Barrier successfully built.
        - CPU_BUDGET, ALREADY_ACTED, OUT_OF_BOUNDS, NOT_ADJACENT.
        - COOLDOWN: Action cooldown is non-zero.
        - NO_RESOURCES: Insufficient global Titanium for barrier cost.
        - ENGINE_REJECTED: Tile occupied or impassable.
        """
        err = self._check_preconditions(require_adjacent=pos)
        if err is not None:
            return err

        if self.ct.get_action_cooldown() != 0:
            return Outcome(False, Reason.COOLDOWN, f"Action cooldown active: {self.ct.get_action_cooldown()}")

        if self.ct.get_global_resources() < self.ct.get_barrier_cost():
            return Outcome(False, Reason.NO_RESOURCES, f"Insufficient Titanium ({self.ct.get_global_resources()}) for barrier")

        if not self.ct.can_build_barrier(pos):
            return Outcome(False, Reason.ENGINE_REJECTED, f"can_build_barrier({pos}) returned False")

        bid = self.ct.build_barrier(pos)
        self._major_action_used = True
        return Outcome(True, Reason.OK, f"Built barrier at {pos}", value=bid)

    def safe_build_conveyor(self, pos: Position, direction: Direction) -> Outcome:
        """Safely construct a Conveyor belt on an adjacent tile facing direction.
        
        Outcomes:
        - OK: Conveyor successfully built.
        - CPU_BUDGET, ALREADY_ACTED, OUT_OF_BOUNDS, NOT_ADJACENT, INVALID_DIRECTION.
        - COOLDOWN: Action cooldown is non-zero.
        - NO_RESOURCES: Insufficient global Titanium for conveyor cost.
        - ENGINE_REJECTED: Tile occupied or impassable.
        """
        err = self._check_preconditions(require_adjacent=pos)
        if err is not None:
            return err

        if not direction.is_cardinal() or direction == Direction.CENTRE:
            return Outcome(False, Reason.INVALID_DIRECTION, f"Direction {direction} is not a valid cardinal facing")

        if self.ct.get_action_cooldown() != 0:
            return Outcome(False, Reason.COOLDOWN, f"Action cooldown active: {self.ct.get_action_cooldown()}")

        if self.ct.get_global_resources() < self.ct.get_conveyor_cost():
            return Outcome(False, Reason.NO_RESOURCES, f"Insufficient Titanium ({self.ct.get_global_resources()}) for conveyor")

        if not self.ct.can_build_conveyor(pos, direction):
            return Outcome(False, Reason.ENGINE_REJECTED, f"can_build_conveyor({pos}, {direction}) returned False")

        bid = self.ct.build_conveyor(pos, direction)
        self._major_action_used = True
        return Outcome(True, Reason.OK, f"Built conveyor at {pos} facing {direction}", value=bid)

    def safe_build_splitter(self, pos: Position, direction: Direction) -> Outcome:
        """Safely construct a Splitter on an adjacent tile facing direction."""
        err = self._check_preconditions(require_adjacent=pos)
        if err is not None:
            return err

        if not direction.is_cardinal() or direction == Direction.CENTRE:
            return Outcome(False, Reason.INVALID_DIRECTION, f"Direction {direction} is not a valid cardinal facing")

        if self.ct.get_action_cooldown() != 0:
            return Outcome(False, Reason.COOLDOWN, f"Action cooldown active: {self.ct.get_action_cooldown()}")

        if self.ct.get_global_resources() < self.ct.get_splitter_cost():
            return Outcome(False, Reason.NO_RESOURCES, f"Insufficient Titanium ({self.ct.get_global_resources()}) for splitter")

        if not self.ct.can_build_splitter(pos, direction):
            return Outcome(False, Reason.ENGINE_REJECTED, f"can_build_splitter({pos}, {direction}) returned False")

        bid = self.ct.build_splitter(pos, direction)
        self._major_action_used = True
        return Outcome(True, Reason.OK, f"Built splitter at {pos} facing {direction}", value=bid)

    def safe_build_harvester(self, pos: Position) -> Outcome:
        """Safely construct a Harvester on an adjacent ore tile.
        
        Outcomes:
        - OK: Harvester successfully built.
        - CPU_BUDGET, ALREADY_ACTED, OUT_OF_BOUNDS, NOT_ADJACENT.
        - COOLDOWN: Action cooldown is non-zero.
        - NO_RESOURCES: Insufficient global Titanium for harvester cost.
        - ENGINE_REJECTED: Tile not ore or already occupied.
        """
        err = self._check_preconditions(require_adjacent=pos)
        if err is not None:
            return err

        if self.ct.get_action_cooldown() != 0:
            return Outcome(False, Reason.COOLDOWN, f"Action cooldown active: {self.ct.get_action_cooldown()}")

        if self.ct.get_global_resources() < self.ct.get_harvester_cost():
            return Outcome(False, Reason.NO_RESOURCES, f"Insufficient Titanium ({self.ct.get_global_resources()}) for harvester")

        if not self.ct.can_build_harvester(pos):
            return Outcome(False, Reason.ENGINE_REJECTED, f"can_build_harvester({pos}) returned False")

        bid = self.ct.build_harvester(pos)
        self._major_action_used = True
        return Outcome(True, Reason.OK, f"Built harvester at {pos}", value=bid)

    def safe_build_sentinel(self, pos: Position, direction: Direction) -> Outcome:
        """Safely construct a Sentinel on an adjacent tile facing direction.
        
        Outcomes:
        - OK: Sentinel successfully built.
        - CPU_BUDGET, ALREADY_ACTED, OUT_OF_BOUNDS, NOT_ADJACENT, INVALID_DIRECTION.
        - COOLDOWN: Action cooldown is non-zero.
        - NO_RESOURCES: Insufficient global Titanium or Ammo.
        - ENGINE_REJECTED: Tile occupied or invalid.
        """
        err = self._check_preconditions(require_adjacent=pos)
        if err is not None:
            return err

        if not direction.is_cardinal() or direction == Direction.CENTRE:
            return Outcome(False, Reason.INVALID_DIRECTION, f"Direction {direction} is not a valid cardinal facing")

        if self.ct.get_action_cooldown() != 0:
            return Outcome(False, Reason.COOLDOWN, f"Action cooldown active: {self.ct.get_action_cooldown()}")

        if self.ct.get_global_resources() < self.ct.get_sentinel_cost():
            return Outcome(False, Reason.NO_RESOURCES, f"Insufficient Titanium ({self.ct.get_global_resources()}) for sentinel")

        if not self.ct.can_build_sentinel(pos, direction):
            return Outcome(False, Reason.ENGINE_REJECTED, f"can_build_sentinel({pos}, {direction}) returned False")

        bid = self.ct.build_sentinel(pos, direction)
        self._major_action_used = True
        return Outcome(True, Reason.OK, f"Built sentinel at {pos} facing {direction}", value=bid)

    def safe_build_gunner(self, pos: Position, direction: Direction) -> Outcome:
        """Safely construct a Gunner on an adjacent tile facing direction.
        
        Outcomes:
        - OK: Gunner successfully built.
        - CPU_BUDGET, ALREADY_ACTED, OUT_OF_BOUNDS, NOT_ADJACENT, INVALID_DIRECTION.
        - COOLDOWN: Action cooldown is non-zero.
        - NO_RESOURCES: Insufficient global Titanium or Ammo.
        - ENGINE_REJECTED: Tile occupied or invalid.
        """
        err = self._check_preconditions(require_adjacent=pos)
        if err is not None:
            return err

        if not direction.is_cardinal() or direction == Direction.CENTRE:
            return Outcome(False, Reason.INVALID_DIRECTION, f"Direction {direction} is not a valid cardinal facing")

        if self.ct.get_action_cooldown() != 0:
            return Outcome(False, Reason.COOLDOWN, f"Action cooldown active: {self.ct.get_action_cooldown()}")

        if self.ct.get_global_resources() < self.ct.get_gunner_cost():
            return Outcome(False, Reason.NO_RESOURCES, f"Insufficient Titanium ({self.ct.get_global_resources()}) for gunner")

        if not self.ct.can_build_gunner(pos, direction):
            return Outcome(False, Reason.ENGINE_REJECTED, f"can_build_gunner({pos}, {direction}) returned False")

        bid = self.ct.build_gunner(pos, direction)
        self._major_action_used = True
        return Outcome(True, Reason.OK, f"Built gunner at {pos} facing {direction}", value=bid)

    def safe_heal(self, pos: Position) -> Outcome:
        """Safely heal adjacent friendly unit or building (+4 HP for 1 Ti).
        
        Outcomes:
        - OK: Target healed.
        - CPU_BUDGET, ALREADY_ACTED, OUT_OF_BOUNDS, NOT_ADJACENT.
        - COOLDOWN: Action cooldown is non-zero.
        - NO_RESOURCES: Global Titanium balance is 0.
        - ENGINE_REJECTED: Target not damaged or not friendly.
        """
        err = self._check_preconditions(require_adjacent=pos)
        if err is not None:
            return err

        if self.ct.get_action_cooldown() != 0:
            return Outcome(False, Reason.COOLDOWN, f"Action cooldown active: {self.ct.get_action_cooldown()}")

        if self.ct.get_global_resources() < 1:
            return Outcome(False, Reason.NO_RESOURCES, "Insufficient Titanium for heal action")

        if not self.ct.can_heal(pos):
            return Outcome(False, Reason.ENGINE_REJECTED, f"can_heal({pos}) returned False")

        self.ct.heal(pos)
        self._major_action_used = True
        return Outcome(True, Reason.OK, f"Healed entity at {pos}")

    def safe_fire(self, target: Position) -> Outcome:
        """Safely fire turret or unit weapon at target tile.
        
        Outcomes:
        - OK: Weapon discharged at target.
        - CPU_BUDGET, ALREADY_ACTED, OUT_OF_BOUNDS.
        - COOLDOWN: Action cooldown is non-zero.
        - ENGINE_REJECTED: Target not in firing arc/range or ammo empty.
        """
        err = self._check_preconditions()
        if err is not None:
            return err

        if not is_in_bounds(self.ct, target):
            return Outcome(False, Reason.OUT_OF_BOUNDS, f"Target {target} out of map bounds")

        if self.ct.get_action_cooldown() != 0:
            return Outcome(False, Reason.COOLDOWN, f"Action cooldown active: {self.ct.get_action_cooldown()}")

        if not self.ct.can_fire(target):
            return Outcome(False, Reason.ENGINE_REJECTED, f"can_fire({target}) returned False")

        self.ct.fire(target)
        self._major_action_used = True
        return Outcome(True, Reason.OK, f"Fired at target {target}")

    def safe_rotate(self, direction: Direction) -> Outcome:
        """Safely rotate turret facing direction (Gunner only).
        
        Outcomes:
        - OK: Turret rotated.
        - CPU_BUDGET, ALREADY_ACTED, INVALID_DIRECTION.
        - COOLDOWN: Action cooldown is non-zero.
        - WRONG_UNIT_TYPE: Unit is not a Gunner.
        - ENGINE_REJECTED: Rotation rejected by engine.
        """
        err = self._check_preconditions()
        if err is not None:
            return err

        if not direction.is_cardinal() or direction == Direction.CENTRE:
            return Outcome(False, Reason.INVALID_DIRECTION, f"Direction {direction} is not a valid cardinal facing")

        if self.ct.get_action_cooldown() != 0:
            return Outcome(False, Reason.COOLDOWN, f"Action cooldown active: {self.ct.get_action_cooldown()}")

        if self.ct.get_entity_type() != EntityType.GUNNER:
            return Outcome(False, Reason.WRONG_UNIT_TYPE, "Rotate is valid only on Gunner units")

        if not self.ct.can_rotate(direction):
            return Outcome(False, Reason.ENGINE_REJECTED, f"can_rotate({direction}) returned False")

        self.ct.rotate(direction)
        self._major_action_used = True
        return Outcome(True, Reason.OK, f"Rotated gunner to {direction}")

    def safe_destroy(self, pos: Position) -> Outcome:
        """Safely demolish friendly building on adjacent tile (e.g. gate cycling)."""
        err = self._check_preconditions(require_adjacent=pos)
        if err is not None:
            return err

        if self.ct.get_action_cooldown() != 0:
            return Outcome(False, Reason.COOLDOWN, f"Action cooldown active: {self.ct.get_action_cooldown()}")

        if not self.ct.can_destroy(pos):
            return Outcome(False, Reason.ENGINE_REJECTED, f"can_destroy({pos}) returned False")

        self.ct.destroy(pos)
        self._major_action_used = True
        return Outcome(True, Reason.OK, f"Demolished building at {pos}")

    def safe_spawn_builder(self, pos: Position) -> Outcome:
        """Safely spawn a Builder Bot at adjacent tile (Core only).
        
        Outcomes:
        - OK: Builder spawned.
        - CPU_BUDGET, ALREADY_ACTED, OUT_OF_BOUNDS, NOT_ADJACENT.
        - COOLDOWN: Action cooldown is non-zero.
        - WRONG_UNIT_TYPE: Unit is not a Core.
        - NO_RESOURCES: Insufficient global Titanium balance.
        - ENGINE_REJECTED: Tile blocked or unit cap reached.
        """
        err = self._check_preconditions(require_adjacent=pos)
        if err is not None:
            return err

        if self.ct.get_entity_type() != EntityType.CORE:
            return Outcome(False, Reason.WRONG_UNIT_TYPE, "Spawn is valid only on Core unit")

        if self.ct.get_action_cooldown() != 0:
            return Outcome(False, Reason.COOLDOWN, f"Action cooldown active: {self.ct.get_action_cooldown()}")

        if self.ct.get_global_resources() < self.ct.get_builder_bot_cost():
            return Outcome(False, Reason.NO_RESOURCES, f"Insufficient Titanium for builder spawn")

        if not self.ct.can_spawn(pos):
            return Outcome(False, Reason.ENGINE_REJECTED, f"can_spawn({pos}) returned False")

        uid = self.ct.spawn_builder(pos)
        self._major_action_used = True
        return Outcome(True, Reason.OK, f"Spawned builder at {pos}", value=uid)
