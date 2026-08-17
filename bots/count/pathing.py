"""Weighted 4-connected search. No game knowledge — callers supply cost and goal."""

from __future__ import annotations

from collections import deque
from heapq import heappop, heappush
from typing import Callable

from fcode import Position

Point = tuple[int, int]
CostFn = Callable[[Position], int | None]
GoalFn = Callable[[Position], bool]
HeuristicFn = Callable[[Position], int]
PassableFn = Callable[[Position], bool]

CARDINAL_DELTAS = ((0, -1), (1, 0), (0, 1), (-1, 0))

DEFAULT_MAX_ITERS = 900


def manhattan(a: Position | Point, b: Position | Point) -> int:
    ax, ay = (a.x, a.y) if isinstance(a, Position) else a
    bx, by = (b.x, b.y) if isinstance(b, Position) else b
    return abs(ax - bx) + abs(ay - by)


def neighbors(p: Point):
    x, y = p
    for dx, dy in CARDINAL_DELTAS:
        yield (x + dx, y + dy)


def _reconstruct(cameFrom: dict[Point, Point | None], end: Point) -> list[Position]:
    path = [end]
    node = cameFrom.get(end)
    while node is not None:
        path.append(node)
        node = cameFrom.get(node)
    path.reverse()
    return [Position(x, y) for x, y in path]


def astarFromSeeds(
    seeds,
    *,
    costFn: CostFn,
    isGoal: GoalFn,
    heuristic: HeuristicFn | None = None,
    maxIters: int = DEFAULT_MAX_ITERS,
) -> list[Position] | None:
    """Multi-seed weighted A*. costFn returns None for impassable tiles.

    Returned path starts at whichever seed won and ends on the goal tile.
    """
    if heuristic is None:
        def heuristic(_p: Position) -> int:
            return 0

    openHeap: list[tuple[int, int, Point]] = []
    cameFrom: dict[Point, Point | None] = {}
    gScore: dict[Point, int] = {}
    closed: set[Point] = set()
    counter = 0

    for s in seeds:
        key = (s.x, s.y)
        if key in gScore:
            continue
        cameFrom[key] = None
        gScore[key] = 0
        heappush(openHeap, (heuristic(s), counter, key))
        counter += 1

    iters = 0
    while openHeap and iters < maxIters:
        iters += 1
        _f, _c, current = heappop(openHeap)
        if current in closed:
            continue
        closed.add(current)

        if isGoal(Position(*current)):
            return _reconstruct(cameFrom, current)

        base = gScore[current]
        for nkey in neighbors(current):
            if nkey in closed:
                continue
            npos = Position(*nkey)
            step = costFn(npos)
            if step is None:
                continue
            tentative = base + step
            if tentative >= gScore.get(nkey, 1 << 30):
                continue
            cameFrom[nkey] = current
            gScore[nkey] = tentative
            heappush(openHeap, (tentative + heuristic(npos), counter, nkey))
            counter += 1

    return None


def astar(
    start: Position,
    goal: Position | None = None,
    *,
    costFn: CostFn,
    isGoal: GoalFn | None = None,
    heuristic: HeuristicFn | None = None,
    maxIters: int = DEFAULT_MAX_ITERS,
) -> list[Position] | None:
    """Single-seed wrapper. astarFromSeeds owns the only search loop here."""
    if isGoal is None:
        if goal is None:
            raise ValueError("astar needs goal or isGoal")
        goalKey = (goal.x, goal.y)

        def isGoal(p: Position) -> bool:
            return (p.x, p.y) == goalKey

    return astarFromSeeds(
        [start],
        costFn=costFn,
        isGoal=isGoal,
        heuristic=heuristic,
        maxIters=maxIters,
    )


def bfs(
    start: Position,
    *,
    isPassable: PassableFn,
    isGoal: GoalFn,
    maxIters: int = DEFAULT_MAX_ITERS,
) -> list[Position] | None:
    """Unweighted nearest-match. Redundancy when A* returns None, and the
    primitive for 'nearest ore / nearest buildable tile' queries."""
    return bfsFromSeeds(
        [start], isPassable=isPassable, isGoal=isGoal, maxIters=maxIters
    )


def bfsFromSeeds(
    seeds,
    *,
    isPassable: PassableFn,
    isGoal: GoalFn,
    maxIters: int = DEFAULT_MAX_ITERS,
) -> list[Position] | None:
    cameFrom: dict[Point, Point | None] = {}
    queue: deque[Point] = deque()

    for s in seeds:
        key = (s.x, s.y)
        if key in cameFrom:
            continue
        cameFrom[key] = None
        queue.append(key)

    iters = 0
    while queue and iters < maxIters:
        iters += 1
        current = queue.popleft()
        if isGoal(Position(*current)):
            return _reconstruct(cameFrom, current)
        for nkey in neighbors(current):
            if nkey in cameFrom:
                continue
            if not isPassable(Position(*nkey)):
                continue
            cameFrom[nkey] = current
            queue.append(nkey)

    return None
