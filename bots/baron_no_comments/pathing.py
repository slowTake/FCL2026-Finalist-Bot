
from __future__ import annotations

from heapq import heappop, heappush
from typing import Callable

from fcode import Controller, Direction, EntityType, Environment, Position

from geometry import BELT_TYPES

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
    goal_p = _point(goal) if goal is not None else None

    if is_goal is None:
        if goal_p is None:
            raise ValueError("astar requires goal or is_goal")

        def is_goal(pos: Position) -> bool:
            return _point(pos) == goal_p

    if heuristic is None and goal_p is not None:

        def heuristic(pos: Position) -> int:
            return manhattan(pos, goal_p)

    return astarFromSeeds(
        [_pos(_point(start))],
        is_passable=is_passable,
        is_goal=is_goal,
        heuristic=heuristic,
        max_iters=max_iters,
    )


def astarFromSeeds(
    seeds: list[Position],
    *,
    is_passable: PassableFn,
    is_goal: GoalFn,
    heuristic: HeuristicFn | None = None,
    max_iters: int = 800,
) -> list[Position] | None:
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
    if not inBounds(ct, pos):
        return False
    if ct.is_in_vision(pos):
        if ct.get_tile_env(pos) == Environment.WALL:
            return False
        bid = ct.get_tile_building_id(pos)
        if bid is None:
            return True
        etype = ct.get_entity_type(bid)
        return etype in BELT_TYPES
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
    return ct.get_entity_type(bid) in BELT_TYPES


_DOOR_SACRIFICE_ORDER = (
    EntityType.BARRIER,
    EntityType.CONVEYOR,
    EntityType.SPLITTER,
    EntityType.HARVESTER,
    EntityType.LAUNCHER,
    EntityType.SENTINEL,
    EntityType.GUNNER,
)


def _doorRank(etype: EntityType) -> int:
    try:
        return _DOOR_SACRIFICE_ORDER.index(etype)
    except ValueError:
        return len(_DOOR_SACRIFICE_ORDER)


def _doorCandidates(
    ct: Controller, local_map: dict | None
) -> tuple[list[tuple], list[tuple]]:
    pos = ct.get_position()
    me = ct.get_team()
    ally: list[tuple] = []
    enemy: list[tuple] = []
    for d in CARDINALS:
        tile = pos.add(d)
        if not inBounds(ct, tile):
            continue
        if not ct.is_in_vision(tile):
            continue
        if ct.get_tile_env(tile) == Environment.WALL:
            continue  # terrain — not targetable, not destructible
        bid = ct.get_tile_building_id(tile)
        if bid is None:
            continue  # blocked by a unit or nothing at all — no door to make
        etype = ct.get_entity_type(bid)
        if etype == EntityType.CORE:
            continue
        beyond = 0 if walkPassable(ct, tile.add(d), local_map=local_map) else 1
        entry = ((beyond, _doorRank(etype), tile.x, tile.y), d, tile)
        if ct.get_team(bid) == me:
            ally.append(entry)
        else:
            enemy.append(entry)
    ally.sort(key=lambda e: e[0])
    enemy.sort(key=lambda e: e[0])
    return ally, enemy


def breakEncirclement(bot, ct: Controller, *, local_map: dict | None = None) -> bool:
    from main import ENCIRCLED_TURNS

    if ENCIRCLED_TURNS is None or ENCIRCLED_TURNS <= 0:
        return False
    if getattr(bot, "motionless", 0) < ENCIRCLED_TURNS:
        return False
    if ct.get_move_cooldown() != 0:
        return False
    for d in CARDINALS:
        if ct.can_move(d):
            return False  # a way out exists; the stall is a routing problem

    ally, enemy = _doorCandidates(ct, local_map)

    for _key, d, tile in ally:
        if not ct.can_destroy(tile):
            continue
        ct.destroy(tile)
        bot.cachedPath = None
        bot.cachedPathTarget = None
        bot.stuck = 0
        if ct.get_move_cooldown() == 0 and ct.can_move(d):
            ct.move(d)
        return True

    if ct.get_action_cooldown() == 0:
        for _key, _d, tile in enemy:
            if not ct.can_fire(tile):
                continue
            ct.fire(tile)
            bot.cachedPath = None
            bot.cachedPathTarget = None
            bot.stuck = 0  # see comment above
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

    def ok(p: Position) -> bool:
        return relayPassable(
            ct,
            p,
            ore_blocked=ore_blocked,
            local_map=local_map,
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


_FRONTIER_ORE_WINDOW = 3
_FRONTIER_ORE_WEIGHT = 40.0
_FRONTIER_OUTWARD_WEIGHT = 6.0


def findFrontierTarget(
    ct: Controller,
    local_map: dict,
    *,
    origin: Position,
    home: Position | None = None,
    outward: bool = False,
) -> Position | None:
    w, h = ct.get_map_width(), ct.get_map_height()
    best: Position | None = None
    best_score = float("-inf")
    seen: set[tuple[int, int]] = set()

    for (x, y), cell in local_map.items():
        if cell.get("passable") is False:
            continue
        for dx, dy in _CARDINAL_DELTAS:
            nx, ny = x + dx, y + dy
            if not (0 <= nx < w and 0 <= ny < h):
                continue
            key = (nx, ny)
            if key in local_map or key in seen:
                continue
            seen.add(key)

            dist = abs(origin.x - nx) + abs(origin.y - ny)
            score = 100.0 / (1.0 + dist)

            ore = 0
            for oy in range(ny - _FRONTIER_ORE_WINDOW, ny + _FRONTIER_ORE_WINDOW + 1):
                for ox in range(
                    nx - _FRONTIER_ORE_WINDOW, nx + _FRONTIER_ORE_WINDOW + 1
                ):
                    near = local_map.get((ox, oy))
                    if near is not None and near.get("ore"):
                        ore += 1
            score += _FRONTIER_ORE_WEIGHT * ore

            if outward and home is not None:
                score += _FRONTIER_OUTWARD_WEIGHT * (
                    abs(nx - home.x) + abs(ny - home.y)
                )

            if score > best_score:
                best_score = score
                best = Position(nx, ny)
    return best