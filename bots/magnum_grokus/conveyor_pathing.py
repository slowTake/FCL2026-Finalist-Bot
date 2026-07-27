"""Conveyor path planning and lay/join/overwrite helpers for magnum_grokus.

No PlayerBot state — callers pass Controller + positions and wire movement.
"""

from __future__ import annotations

from collections import deque

from fcode import Controller, Direction, EntityType, Environment, Position

CARDINALS = [Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST]


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


def travel_facing(from_pos: Position, to_pos: Position) -> Direction:
    """Cardinal facing from from_pos toward to_pos."""
    return nearest_cardinal(from_pos.direction_to(to_pos))


def can_lay_infrastructure(ct: Controller, pos: Position) -> bool:
    """True only on plain empty terrain — not ore, walls, or occupied tiles."""
    if not in_bounds(ct, pos):
        return False
    if not ct.is_in_vision(pos):
        return False
    return (
        ct.is_tile_empty(pos)
        and ct.get_tile_env(pos) == Environment.EMPTY
    )


def _core_footprint(core_pos: Position) -> list[Position]:
    """Core is a 2x2; core_pos is treated as the footprint origin (min x/y)."""
    return [
        core_pos,
        Position(core_pos.x + 1, core_pos.y),
        Position(core_pos.x, core_pos.y + 1),
        Position(core_pos.x + 1, core_pos.y + 1),
    ]


def is_core_adjacent(
    ct: Controller, pos: Position, core_pos: Position | None
) -> bool:
    """True if pos orthogonally touches any Core footprint tile."""
    for d in CARDINALS:
        neighbor = pos.add(d)
        if not in_bounds(ct, neighbor):
            continue
        if ct.is_in_vision(neighbor):
            bid = ct.get_tile_building_id(neighbor)
            if bid is not None and ct.get_entity_type(bid) == EntityType.CORE:
                return True
    if core_pos is None:
        return False
    for tile in _core_footprint(core_pos):
        if abs(pos.x - tile.x) + abs(pos.y - tile.y) == 1:
            return True
    return False


def _allied_belt_id(ct: Controller, pos: Position) -> int | None:
    if not ct.is_in_vision(pos):
        return None
    bid = ct.get_tile_building_id(pos)
    if bid is None:
        return None
    etype = ct.get_entity_type(bid)
    if etype not in (EntityType.CONVEYOR, EntityType.SPLITTER):
        return None
    if ct.get_team(bid) != ct.get_team():
        return None
    return bid


def is_layable_or_join(
    ct: Controller,
    pos: Position,
    *,
    ore_blocked: set[tuple[int, int]] | None = None,
) -> bool:
    """Empty non-ore tile, or allied conveyor/splitter. Optimistic out of vision."""
    if not in_bounds(ct, pos):
        return False
    if ore_blocked is not None and (pos.x, pos.y) in ore_blocked:
        return False
    if not ct.is_in_vision(pos):
        return True
    if _allied_belt_id(ct, pos) is not None:
        return True
    return can_lay_infrastructure(ct, pos)


def facing_into_core(pos: Position, core_pos: Position) -> Direction:
    """Travel facing from a Core-adjacent tile into the Core footprint."""
    best: Direction | None = None
    best_dist = 10**9
    for tile in _core_footprint(core_pos):
        dist = abs(pos.x - tile.x) + abs(pos.y - tile.y)
        if dist != 1:
            continue
        if dist < best_dist:
            best_dist = dist
            best = travel_facing(pos, tile)
    if best is not None and best != Direction.CENTRE:
        return best
    return nearest_cardinal(pos.direction_to(core_pos))


def plan_conveyor_path(
    ct: Controller,
    start: Position,
    core_pos: Position,
    *,
    ore_blocked: set[tuple[int, int]] | None = None,
) -> list[Position] | None:
    """BFS over layable/join tiles from start (or its neighbors) to a Core-adj tile.

    Returns the path of belt tiles including the goal, or None if unreachable.
    If start itself is not layable (e.g. harvester), seeds from layable cardinal
    neighbors of start.
    """
    w = ct.get_map_width()
    h = ct.get_map_height()

    def ok(p: Position) -> bool:
        return is_layable_or_join(ct, p, ore_blocked=ore_blocked)

    seeds: list[Position] = []
    if ok(start):
        seeds.append(start)
    else:
        for d in CARDINALS:
            n = start.add(d)
            if ok(n):
                seeds.append(n)
    if not seeds:
        return None

    parent: dict[tuple[int, int], tuple[int, int] | None] = {}
    q: deque[Position] = deque()
    for s in seeds:
        key = (s.x, s.y)
        if key in parent:
            continue
        parent[key] = None
        q.append(s)

    goal: Position | None = None
    while q:
        cur = q.popleft()
        if is_core_adjacent(ct, cur, core_pos):
            goal = cur
            break
        for d in CARDINALS:
            nxt = cur.add(d)
            if not (0 <= nxt.x < w and 0 <= nxt.y < h):
                continue
            nkey = (nxt.x, nxt.y)
            if nkey in parent:
                continue
            if not ok(nxt):
                continue
            parent[nkey] = (cur.x, cur.y)
            q.append(nxt)

    if goal is None:
        return None

    path_rev: list[Position] = []
    key: tuple[int, int] | None = (goal.x, goal.y)
    while key is not None:
        path_rev.append(Position(key[0], key[1]))
        key = parent[key]
    path_rev.reverse()
    return path_rev


def path_travel_dir(
    path: list[Position], index: int, core_pos: Position
) -> Direction:
    """Facing for path[index]: toward next hop, or into Core on the last tile."""
    tile = path[index]
    if index + 1 < len(path):
        return travel_facing(tile, path[index + 1])
    return facing_into_core(tile, core_pos)


def ensure_belt_on_path(
    ct: Controller, tile: Position, travel_dir: Direction
) -> str:
    """Join if allied belt already faces travel_dir; else destroy+rebuild; else build.

    Returns: 'joined' | 'built' | 'failed'
    Builder must be orthogonally adjacent to tile. Does not move.
    """
    if travel_dir == Direction.CENTRE:
        return "failed"

    bid = _allied_belt_id(ct, tile)
    if bid is not None:
        if ct.get_direction(bid) == travel_dir:
            return "joined"
        if ct.can_destroy(tile):
            ct.destroy(tile)
        else:
            return "failed"
        if ct.can_build_conveyor(tile, travel_dir):
            ct.build_conveyor(tile, travel_dir)
            return "built"
        return "failed"

    if not can_lay_infrastructure(ct, tile):
        return "failed"
    if ct.get_global_resources() < ct.get_conveyor_cost():
        return "failed"
    if ct.can_build_conveyor(tile, travel_dir):
        ct.build_conveyor(tile, travel_dir)
        return "built"
    return "failed"


def belt_faces(ct: Controller, tile: Position, expected: Direction) -> bool:
    """True if tile has an allied belt facing expected."""
    bid = _allied_belt_id(ct, tile)
    if bid is None:
        return False
    return ct.get_direction(bid) == expected


def path_is_complete(
    ct: Controller, path: list[Position], core_pos: Position
) -> bool:
    """True when every path tile has an allied belt with correct travel facing."""
    if not path:
        return False
    for i, tile in enumerate(path):
        want = path_travel_dir(path, i, core_pos)
        if not belt_faces(ct, tile, want):
            return False
    return True


def first_incomplete_index(
    ct: Controller, path: list[Position], core_pos: Position
) -> int | None:
    """Index of first path tile missing or wrong-facing; None if complete."""
    for i, tile in enumerate(path):
        want = path_travel_dir(path, i, core_pos)
        if not belt_faces(ct, tile, want):
            return i
    return None
