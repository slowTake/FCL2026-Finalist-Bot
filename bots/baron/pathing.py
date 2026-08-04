"""A* and passable helpers for baron walk and relay routing."""

from __future__ import annotations

from heapq import heappop, heappush
from typing import Callable

from fcode import Controller, Direction, EntityType, Environment, Position

Point = tuple[int, int]
PassableFn = Callable[[Position], bool]
GoalFn = Callable[[Position], bool]
HeuristicFn = Callable[[Position], int]

_CARDINAL_DELTAS = ((1, 0), (-1, 0), (0, 1), (0, -1))
CARDINALS = (Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST)

_RELAY_BLOCK_TYPES = (
    EntityType.CORE,
    EntityType.HARVESTER,
    EntityType.GUNNER,
    EntityType.SENTINEL,
    EntityType.LAUNCHER,
    EntityType.BARRIER,
)


def _point(pos: Position | Point) -> Point:
    if isinstance(pos, tuple):
        return (int(pos[0]), int(pos[1]))
    return (pos.x, pos.y)


def _pos(p: Point) -> Position:
    return Position(p[0], p[1])


def manhattan(a: Position | Point, b: Position | Point) -> int:
    ax, ay = _point(a)
    bx, by = _point(b)
    return abs(ax - bx) + abs(ay - by)


def neighbors(pos: Position | Point):
    x, y = _point(pos)
    for dx, dy in _CARDINAL_DELTAS:
        yield Position(x + dx, y + dy)


def inBounds(ct: Controller, pos: Position) -> bool:
    return 0 <= pos.x < ct.get_map_width() and 0 <= pos.y < ct.get_map_height()


def nearestCardinal(d: Direction) -> Direction:
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


def _reconstruct(
    came_from: dict[Point, Point | None], current: Point
) -> list[Position]:
    path = [current]
    while came_from.get(current) is not None:
        current = came_from[current]  # type: ignore[assignment]
        path.append(current)
    path.reverse()
    return [_pos(p) for p in path]


def astar(
    start: Position | Point,
    goal: Position | Point | None = None,
    *,
    is_passable: PassableFn,
    is_goal: GoalFn | None = None,
    heuristic: HeuristicFn | None = None,
    max_iters: int = 800,
) -> list[Position] | None:
    """Cardinal A* to a goal position or any tile matching is_goal."""
    start_p = _point(start)
    goal_p = _point(goal) if goal is not None else None

    if is_goal is None:
        if goal_p is None:
            raise ValueError("astar requires goal or is_goal")

        def _is_goal(pos: Position) -> bool:
            return _point(pos) == goal_p

        is_goal = _is_goal

    if heuristic is None:
        if goal_p is not None:

            def _h(pos: Position) -> int:
                return manhattan(pos, goal_p)

            heuristic = _h
        else:

            def _h0(_pos: Position) -> int:
                return 0

            heuristic = _h0

    if is_goal(_pos(start_p)):
        return [_pos(start_p)]

    open_heap: list[tuple[int, int, Point]] = []
    counter = 0
    heappush(open_heap, (heuristic(_pos(start_p)), counter, start_p))
    counter += 1

    came_from: dict[Point, Point | None] = {start_p: None}
    g_score: dict[Point, int] = {start_p: 0}
    closed: set[Point] = set()
    iters = 0

    while open_heap and iters < max_iters:
        iters += 1
        _f, _c, current = heappop(open_heap)
        if current in closed:
            continue
        closed.add(current)

        cur_pos = _pos(current)
        if is_goal(cur_pos):
            return _reconstruct(came_from, current)

        for nxt in neighbors(current):
            nkey = _point(nxt)
            if nkey in closed:
                continue
            if not is_passable(nxt):
                continue
            tentative = g_score[current] + 1
            if tentative >= g_score.get(nkey, 1 << 30):
                continue
            came_from[nkey] = current
            g_score[nkey] = tentative
            heappush(
                open_heap,
                (tentative + heuristic(nxt), counter, nkey),
            )
            counter += 1

    return None


def astarFromSeeds(
    seeds: list[Position],
    *,
    is_passable: PassableFn,
    is_goal: GoalFn,
    heuristic: HeuristicFn | None = None,
    max_iters: int = 800,
) -> list[Position] | None:
    """A* with multiple start seeds; returns the best path found."""
    if not seeds:
        return None

    if heuristic is None:

        def heuristic(_pos: Position) -> int:
            return 0

    open_heap: list[tuple[int, int, Point]] = []
    counter = 0
    came_from: dict[Point, Point | None] = {}
    g_score: dict[Point, int] = {}
    closed: set[Point] = set()

    for s in seeds:
        sp = _point(s)
        if sp in g_score:
            continue
        came_from[sp] = None
        g_score[sp] = 0
        heappush(open_heap, (heuristic(s), counter, sp))
        counter += 1

    iters = 0
    while open_heap and iters < max_iters:
        iters += 1
        _f, _c, current = heappop(open_heap)
        if current in closed:
            continue
        closed.add(current)

        cur_pos = _pos(current)
        if is_goal(cur_pos):
            return _reconstruct(came_from, current)

        for nxt in neighbors(current):
            nkey = _point(nxt)
            if nkey in closed:
                continue
            if not is_passable(nxt):
                continue
            tentative = g_score[current] + 1
            if tentative >= g_score.get(nkey, 1 << 30):
                continue
            came_from[nkey] = current
            g_score[nkey] = tentative
            heappush(
                open_heap,
                (tentative + heuristic(nxt), counter, nkey),
            )
            counter += 1

    return None


def walkPassable(
    ct: Controller,
    pos: Position,
    *,
    local_map: dict | None = None,
) -> bool:
    """Builder walk: belts OK; walls and non-belt buildings block."""
    if not inBounds(ct, pos):
        return False
    if ct.is_in_vision(pos):
        if ct.get_tile_env(pos) == Environment.WALL:
            return False
        bid = ct.get_tile_building_id(pos)
        if bid is None:
            return True
        etype = ct.get_entity_type(bid)
        return etype in (EntityType.CONVEYOR, EntityType.SPLITTER)
    if local_map is not None:
        entry = local_map.get((pos.x, pos.y))
        if entry is not None:
            if entry.get("passable") is False:
                return False
            ent = entry.get("entity")
            if ent and ent.get("type") in _RELAY_BLOCK_TYPES:
                return False
    return True


def relayPassable(
    ct: Controller,
    pos: Position,
    *,
    ore_blocked: set[tuple[int, int]] | None = None,
    local_map: dict | None = None,
) -> bool:
    """Tiles where a relay belt can be laid or joined (existing conveyor/splitter)."""
    if not inBounds(ct, pos):
        return False
    if ore_blocked is not None and (pos.x, pos.y) in ore_blocked:
        return False
    if not ct.is_in_vision(pos):
        if local_map is not None:
            entry = local_map.get((pos.x, pos.y))
            if entry is not None:
                ent = entry.get("entity")
                if ent and ent.get("type") in _RELAY_BLOCK_TYPES:
                    return False
                if entry.get("passable") is False:
                    return False
        return True
    if ct.get_tile_env(pos) == Environment.WALL:
        return False
    bid = ct.get_tile_building_id(pos)
    if bid is None:
        return ct.get_tile_env(pos) == Environment.EMPTY
    etype = ct.get_entity_type(bid)
    if etype in (EntityType.CONVEYOR, EntityType.SPLITTER):
        return True
    return False


def findPathAroundObstruction(
    ct: Controller,
    start: Position,
    *,
    is_goal: GoalFn,
    heuristic_to: Position | None = None,
    ore_blocked: set[tuple[int, int]] | None = None,
    local_map: dict | None = None,
    max_iters: int = 800,
) -> list[Position] | None:
    """Fallback relay path: seed from cardinal neighbors of start when origin blocked."""

    def ok(p: Position) -> bool:
        return relayPassable(
            ct, p, ore_blocked=ore_blocked, local_map=local_map
        )

    seeds: list[Position] = []
    if ok(start):
        seeds.append(start)
    for d in CARDINALS:
        n = start.add(d)
        if ok(n) and n not in seeds:
            seeds.append(n)
    if not seeds:
        return None

    def h(p: Position) -> int:
        if heuristic_to is None:
            return 0
        return manhattan(p, heuristic_to)

    return astarFromSeeds(
        seeds,
        is_passable=ok,
        is_goal=is_goal,
        heuristic=h,
        max_iters=max_iters,
    )
