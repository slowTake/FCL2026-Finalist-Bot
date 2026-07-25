"""Magnum Grokus v1.2 — disciplined logistics.

One harvester chain at a time; one builder owns the active route.
Belts only while routing; wait on gaps until continuity is restored.
"""

from __future__ import annotations

import random
from collections import deque

from fcode import Controller, Direction, EntityType, Environment, Position

DIRECTIONS = [d for d in Direction if d != Direction.CENTRE]
CARDINALS = [Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST]

SLOT_CORE_X = 0
SLOT_CORE_Y = 1
SLOT_HARVESTER_COUNT = 2
SLOT_ORE_LOCATION = 3
SLOT_CHAIN_OK = 4
SLOT_ACTIVE_HARVESTER = 5
SLOT_ROUTE_OWNER = 6

MAX_BUILDERS = 5


def pack_pos(pos: Position) -> int:
    return ((pos.x + 1) << 16) | (pos.y + 1)


def unpack_pos(val: int) -> Position | None:
    if val == 0:
        return None
    return Position((val >> 16) - 1, (val & 0xFFFF) - 1)


def nearest_cardinal(d: Direction) -> Direction:
    return {
        Direction.NORTH: Direction.NORTH,
        Direction.NORTHEAST: Direction.NORTH,
        Direction.EAST: Direction.EAST,
        Direction.SOUTHEAST: Direction.EAST,
        Direction.SOUTH: Direction.SOUTH,
        Direction.SOUTHWEST: Direction.SOUTH,
        Direction.WEST: Direction.WEST,
        Direction.NORTHWEST: Direction.WEST,
        Direction.CENTRE: Direction.NORTH,
    }[d]


def in_bounds(ct: Controller, pos: Position) -> bool:
    return 0 <= pos.x < ct.get_map_width() and 0 <= pos.y < ct.get_map_height()


def l_path_step(pos: Position, core: Position, y_first: bool = False) -> Direction:
    """Next cardinal step toward core. Default: larger delta first."""
    dx = core.x - pos.x
    dy = core.y - pos.y

    def step_x() -> Direction | None:
        if dx == 0:
            return None
        return Direction.EAST if dx > 0 else Direction.WEST

    def step_y() -> Direction | None:
        if dy == 0:
            return None
        return Direction.SOUTH if dy > 0 else Direction.NORTH

    if y_first:
        return step_y() or step_x() or Direction.CENTRE
    if abs(dx) >= abs(dy):
        return step_x() or step_y() or Direction.CENTRE
    return step_y() or step_x() or Direction.CENTRE


def splitter_outputs(facing: Direction) -> list[Direction]:
    facing = nearest_cardinal(facing)
    return [facing, facing.rotate_left().rotate_left(), facing.rotate_right().rotate_right()]


class Player:
    def __init__(self) -> None:
        self.num_spawned = 0
        self.core_pos: Position | None = None
        self.target: Position | None = None
        self.last_pos: Position | None = None
        self.stuck = 0
        self.routing = False
        self.route_step: Direction | None = None
        self.harvester_pos: Position | None = None
        self.l_path_y_first = False

    def run(self, ct: Controller) -> None:
        etype = ct.get_entity_type()
        if etype == EntityType.CORE:
            self._run_core(ct)
        elif etype == EntityType.BUILDER_BOT:
            self._run_builder(ct)
        elif etype == EntityType.GUNNER:
            self._run_gunner(ct)

    def _run_core(self, ct: Controller) -> None:
        pos = ct.get_position()
        ct.write_store(SLOT_CORE_X, pos.x)
        ct.write_store(SLOT_CORE_Y, pos.y)

        if self.num_spawned >= MAX_BUILDERS:
            return
        if ct.get_global_resources() < ct.get_builder_bot_cost():
            return

        dirs = list(DIRECTIONS)
        random.shuffle(dirs)
        for d in dirs:
            spawn_pos = pos.add(d)
            if ct.can_spawn(spawn_pos):
                ct.spawn_builder(spawn_pos)
                self.num_spawned += 1
                return

    def _run_builder(self, ct: Controller) -> None:
        pos = ct.get_position()
        self._read_core_pos(ct)
        self._sync_team_state(ct)

        if self.last_pos == pos:
            self.stuck += 1
        else:
            self.stuck = 0
        self.last_pos = pos

        is_owner = self._is_route_owner(ct)

        if ct.get_action_cooldown() == 0:
            if self._can_build_harvester_team(ct):
                if not self._try_build_harvester(ct):
                    if self.routing and is_owner:
                        if self.harvester_pos is not None:
                            self._try_first_conveyor(ct, self.harvester_pos)
                        self._try_ensure_core_splitter(ct)
            elif self.routing and is_owner:
                if self.harvester_pos is not None:
                    self._try_first_conveyor(ct, self.harvester_pos)
                self._try_ensure_core_splitter(ct)
            if ct.get_action_cooldown() == 0:
                self._try_heal(ct)

        self._move_toward_target(ct)
        self._share_ore(ct)

    def _is_route_owner(self, ct: Controller) -> bool:
        return ct.read_store(SLOT_ROUTE_OWNER) == ct.get_id()

    def _can_build_harvester_team(self, ct: Controller) -> bool:
        if ct.read_store(SLOT_CHAIN_OK) != 0:
            return True
        return ct.read_store(SLOT_ROUTE_OWNER) == 0

    def _sync_team_state(self, ct: Controller) -> None:
        active = unpack_pos(ct.read_store(SLOT_ACTIVE_HARVESTER))
        if active is not None:
            self.harvester_pos = active

        owner = ct.read_store(SLOT_ROUTE_OWNER)
        chain_ok = ct.read_store(SLOT_CHAIN_OK)
        my_id = ct.get_id()

        if chain_ok == 0 and owner == my_id:
            self.routing = True
        elif owner != my_id:
            self.routing = False

    def _read_core_pos(self, ct: Controller) -> None:
        if self.core_pos is not None:
            return
        if ct.get_current_round() > 1:
            x = ct.read_store(SLOT_CORE_X)
            y = ct.read_store(SLOT_CORE_Y)
            self.core_pos = Position(x, y)
            return
        for tile in ct.get_nearby_tiles():
            bid = ct.get_tile_building_id(tile)
            if bid is not None and ct.get_entity_type(bid) == EntityType.CORE:
                self.core_pos = tile
                return

    def _finish_routing_local(self) -> None:
        self.routing = False
        self.route_step = None
        self.harvester_pos = None
        self.l_path_y_first = False

    def _mark_chain_complete(self, ct: Controller) -> None:
        ct.write_store(SLOT_CHAIN_OK, 1)
        ct.write_store(SLOT_ACTIVE_HARVESTER, 0)
        ct.write_store(SLOT_ROUTE_OWNER, 0)
        self._finish_routing_local()

    def _try_finish_routing(self, ct: Controller) -> bool:
        harvester = unpack_pos(ct.read_store(SLOT_ACTIVE_HARVESTER))
        if harvester is None:
            self._mark_chain_complete(ct)
            return True
        if self._chain_reaches_core(ct, harvester):
            self._mark_chain_complete(ct)
            return True
        return False

    def _core_tiles(self) -> set[Position]:
        if self.core_pos is None:
            return set()
        return {
            Position(self.core_pos.x + dx, self.core_pos.y + dy)
            for dx in range(2)
            for dy in range(2)
        }

    def _adjacent_to_core_pos(self, pos: Position) -> bool:
        core_tiles = self._core_tiles()
        for d in CARDINALS:
            if pos.add(d) in core_tiles:
                return True
        return False

    def _chain_reaches_core(self, ct: Controller, harvester_pos: Position) -> bool:
        if self.core_pos is None:
            return False

        core_tiles = self._core_tiles()
        queue: deque[Position] = deque()
        visited: set[Position] = set()

        for d in CARDINALS:
            n = harvester_pos.add(d)
            if not in_bounds(ct, n) or not ct.is_in_vision(n):
                continue
            bid = ct.get_tile_building_id(n)
            if bid is None:
                continue
            if ct.get_entity_type(bid) not in (
                EntityType.CONVEYOR,
                EntityType.SPLITTER,
            ):
                continue
            queue.append(n)
            visited.add(n)

        if not queue:
            return False

        while queue:
            pos = queue.popleft()
            if self._adjacent_to_core_pos(pos):
                bid = ct.get_tile_building_id(pos)
                if bid is not None:
                    et = ct.get_entity_type(bid)
                    if et == EntityType.SPLITTER:
                        toward = nearest_cardinal(pos.direction_to(self.core_pos))
                        if ct.get_direction(bid) == toward:
                            return True
                    elif et == EntityType.CONVEYOR:
                        return True

            bid = ct.get_tile_building_id(pos)
            if bid is None:
                continue
            et = ct.get_entity_type(bid)
            if et == EntityType.CONVEYOR:
                nxt = pos.add(ct.get_direction(bid))
                if (
                    in_bounds(ct, nxt)
                    and nxt not in visited
                    and ct.is_in_vision(nxt)
                ):
                    visited.add(nxt)
                    queue.append(nxt)
            elif et == EntityType.SPLITTER:
                for out_dir in splitter_outputs(ct.get_direction(bid)):
                    nxt = pos.add(out_dir)
                    if (
                        in_bounds(ct, nxt)
                        and nxt not in visited
                        and ct.is_in_vision(nxt)
                    ):
                        visited.add(nxt)
                        queue.append(nxt)

        return False

    def _try_build_harvester(self, ct: Controller) -> bool:
        if not self._can_build_harvester_team(ct):
            return False
        if ct.get_global_resources() < ct.get_harvester_cost():
            return False

        pos = ct.get_position()
        candidates: list[tuple[int, Direction]] = []
        for d in DIRECTIONS:
            build_pos = pos.add(d)
            if not in_bounds(ct, build_pos):
                continue
            if not ct.can_build_harvester(build_pos):
                continue
            score = 0
            if self.core_pos is not None:
                step = l_path_step(build_pos, self.core_pos, self.l_path_y_first)
                if step != Direction.CENTRE:
                    conv_pos = build_pos.add(step)
                    if conv_pos == pos:
                        score = 2
                    elif conv_pos.distance_squared(pos) == 1:
                        score = 1
            candidates.append((score, d))

        if not candidates:
            return False

        candidates.sort(key=lambda item: (-item[0], item[1].value))
        for _, d in candidates:
            build_pos = pos.add(d)
            ct.build_harvester(build_pos)
            count = ct.read_store(SLOT_HARVESTER_COUNT)
            ct.write_store(SLOT_HARVESTER_COUNT, count + 1)
            ct.write_store(SLOT_ACTIVE_HARVESTER, pack_pos(build_pos))
            ct.write_store(SLOT_ROUTE_OWNER, ct.get_id())
            ct.write_store(SLOT_CHAIN_OK, 0)
            self.harvester_pos = build_pos
            self.route_step = None
            self._try_first_conveyor(ct, build_pos)
            self.routing = True
            self.target = None
            return True
        return False

    def _try_first_conveyor(self, ct: Controller, harvester_pos: Position) -> None:
        if self.core_pos is None or not ct.is_in_vision(harvester_pos):
            return

        step = l_path_step(harvester_pos, self.core_pos, self.l_path_y_first)
        if step == Direction.CENTRE:
            return
        conv_pos = harvester_pos.add(step)
        if not in_bounds(ct, conv_pos):
            return
        if self._lay_route_tile(ct, conv_pos, step):
            self.harvester_pos = None

    def _try_ensure_core_splitter(self, ct: Controller) -> bool:
        if self.core_pos is None:
            return False
        pos = ct.get_position()
        if not self._adjacent_to_core(ct, pos):
            return False
        if ct.get_tile_building_id(pos) is not None:
            return False

        toward = nearest_cardinal(pos.direction_to(self.core_pos))
        if toward == Direction.CENTRE:
            return False
        if ct.can_build_splitter(pos, toward):
            ct.build_splitter(pos, toward)
            self._try_finish_routing(ct)
            return True
        return False

    def _route_tile_ready(
        self,
        ct: Controller,
        tile_pos: Position,
        move_dir: Direction,
    ) -> bool:
        """True when the next routing tile is built or does not need a new belt."""
        if not in_bounds(ct, tile_pos):
            return False
        if not ct.is_in_vision(tile_pos):
            return True

        bid = ct.get_tile_building_id(tile_pos)
        if bid is not None:
            return True
        if not ct.is_tile_empty(tile_pos):
            return True

        return self._lay_route_tile(ct, tile_pos, move_dir)

    def _lay_route_tile(
        self,
        ct: Controller,
        tile_pos: Position,
        move_dir: Direction,
    ) -> bool:
        """Lay belt or splitter on tile_pos along routing path. Returns True if built."""
        if self.core_pos is None or not in_bounds(ct, tile_pos):
            return False
        if not ct.is_in_vision(tile_pos):
            return False
        if not ct.is_tile_empty(tile_pos):
            return False

        if self._adjacent_to_core(ct, tile_pos):
            toward = nearest_cardinal(tile_pos.direction_to(self.core_pos))
            if toward == Direction.CENTRE:
                return False
            if ct.can_build_splitter(tile_pos, toward):
                ct.build_splitter(tile_pos, toward)
                self._try_finish_routing(ct)
                return True
            return False

        leg_after = l_path_step(tile_pos, self.core_pos, self.l_path_y_first)
        if leg_after != move_dir and leg_after != Direction.CENTRE:
            incoming = move_dir.opposite()
            if incoming == leg_after.opposite():
                if ct.get_global_resources() < ct.get_splitter_cost():
                    return False
                if ct.can_build_splitter(tile_pos, leg_after):
                    ct.build_splitter(tile_pos, leg_after)
                    return True
            elif ct.can_build_conveyor(tile_pos, leg_after):
                ct.build_conveyor(tile_pos, leg_after)
                return True
            return False

        if ct.get_global_resources() < ct.get_conveyor_cost():
            return False
        if ct.can_build_conveyor(tile_pos, move_dir):
            ct.build_conveyor(tile_pos, move_dir)
            return True
        return False

    def _try_heal(self, ct: Controller) -> None:
        pos = ct.get_position()
        for d in DIRECTIONS:
            check = pos.add(d)
            if not in_bounds(ct, check):
                continue
            if ct.can_heal(check):
                ct.heal(check)
                return

    def _adjacent_to_core(self, ct: Controller, pos: Position) -> bool:
        for d in CARDINALS:
            n = pos.add(d)
            if not in_bounds(ct, n) or not ct.is_in_vision(n):
                continue
            bid = ct.get_tile_building_id(n)
            if bid is not None and ct.get_entity_type(bid) == EntityType.CORE:
                return True
        return False

    def _move_toward_target(self, ct: Controller) -> None:
        if ct.get_move_cooldown() != 0:
            return

        pos = ct.get_position()

        if self.routing and self._is_route_owner(ct) and self.core_pos is not None:
            if self.stuck >= 3:
                self.l_path_y_first = not self.l_path_y_first
                self.stuck = 0
            step = l_path_step(pos, self.core_pos, self.l_path_y_first)
            candidates: list[Direction] = []
            if step != Direction.CENTRE:
                candidates.append(step)
            for d in CARDINALS:
                if d not in candidates:
                    candidates.append(d)
            candidates.sort(
                key=lambda d: pos.add(d).distance_squared(self.core_pos)
            )
            for d in candidates:
                if self._try_move(ct, d, routing=True):
                    return
            return

        if self.target is None or pos == self.target or self.stuck >= 3:
            self.target = self._pick_target(ct)
            self.stuck = 0

        if self.target is None:
            return

        desired = pos.direction_to(self.target)
        if desired == Direction.CENTRE:
            return

        primary = [desired, desired.rotate_left(), desired.rotate_right()]
        for d in primary:
            if self._try_move(ct, d, routing=False):
                return
        remaining = [d for d in DIRECTIONS if d not in primary]
        random.shuffle(remaining)
        for d in remaining:
            if self._try_move(ct, d, routing=False):
                return

    def _try_move(self, ct: Controller, d: Direction, routing: bool) -> bool:
        if d == Direction.CENTRE:
            return False
        pos = ct.get_position()
        next_pos = pos.add(d)
        if not in_bounds(ct, next_pos):
            return False

        if routing:
            if not self._route_tile_ready(ct, next_pos, d):
                return False

        if ct.can_move(d):
            ct.move(d)
            if routing:
                self.route_step = d
            return True
        return False

    def _pick_target(self, ct: Controller) -> Position | None:
        pos = ct.get_position()

        best_ore = None
        best_dist = 10**9
        for tile in ct.get_nearby_tiles():
            if ct.get_tile_env(tile) != Environment.ORE_TITANIUM:
                continue
            if ct.get_tile_building_id(tile) is not None:
                continue
            dist = pos.distance_squared(tile)
            if dist < best_dist:
                best_dist = dist
                best_ore = tile
        if best_ore is not None:
            return best_ore

        shared = unpack_pos(ct.read_store(SLOT_ORE_LOCATION))
        if shared is not None and pos.distance_squared(shared) > 4:
            return shared

        w, h = ct.get_map_width(), ct.get_map_height()
        return Position(random.randrange(w), random.randrange(h))

    def _share_ore(self, ct: Controller) -> None:
        for tile in ct.get_nearby_tiles():
            if ct.get_tile_env(tile) != Environment.ORE_TITANIUM:
                continue
            if ct.get_tile_building_id(tile) is not None:
                continue
            ct.write_store(SLOT_ORE_LOCATION, pack_pos(tile))
            return

    def _run_gunner(self, ct: Controller) -> None:
        target = ct.get_gunner_target()
        if target is not None and ct.can_fire(target):
            ct.fire(target)
