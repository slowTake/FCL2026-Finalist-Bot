"""Grand_knight — Spatial Domain: Lean Weighted A* Search Kernel.

Pure, engine-agnostic multi-seed heap search ported from count's astarFromSeeds
shape. No game knowledge -- callers supply cost, goal, and heuristic functions.
CPU-valved: yields the best partial path found so far rather than blocking
past its wall-clock budget, so a unit always makes forward progress instead
of stalling on a search too expensive to finish this tick.
"""

from __future__ import annotations

from heapq import heappop, heappush
from typing import Callable, Optional, Sequence, Set

from fcode import Controller, Position

from config import Config

Point = tuple[int, int]
CostFn = Callable[[Position], Optional[int]]
GoalFn = Callable[[Position], bool]
HeuristicFn = Callable[[Position], int]

_CARDINAL_DELTAS = ((0, -1), (1, 0), (0, 1), (-1, 0))


def manhattan_heuristic(goal: Position) -> HeuristicFn:
    """Admissible heuristic factory: Manhattan distance to a single reference point."""
    def h(pos: Position) -> int:
        return abs(pos.x - goal.x) + abs(pos.y - goal.y)
    return h


def make_lean_cost_fn(
    ct: Controller,
    seen_walls: Optional[Set[Position]] = None,
    allowed_fn: Optional[Callable[[Position], bool]] = None,
) -> CostFn:
    """Flat-cost traversability cost function -- no threat/chokepoint pricing.

    Impassable (per grand_knight's existing is_traversable rules) -> None.
    Everything else -> flat cost 1.
    """
    from spatial.movement import is_traversable

    def cost_fn(pos: Position) -> Optional[int]:
        if not is_traversable(ct, pos, seen_walls):
            return None
        if allowed_fn is not None and not allowed_fn(pos):
            return None
        return 1

    return cost_fn


def _neighbors(p: Point):
    x, y = p
    for dx, dy in _CARDINAL_DELTAS:
        yield (x + dx, y + dy)


def _reconstruct(came_from: dict[Point, Optional[Point]], end: Point) -> list[Position]:
    path = [end]
    node = came_from.get(end)
    while node is not None:
        path.append(node)
        node = came_from.get(node)
    path.reverse()
    return [Position(x, y) for x, y in path]


def astar_from_seeds(
    seeds: Sequence[Position],
    ct: Controller,
    *,
    cost_fn: CostFn,
    is_goal: GoalFn,
    heuristic: Optional[HeuristicFn] = None,
    max_iters: int = Config.ASTAR_MAX_ITERS,
    max_cost: Optional[int] = None,
    cpu_valve_micros: int = Config.ASTAR_CPU_VALVE_MICROSEC,
) -> Optional[list[Position]]:
    """Multi-seed weighted A*.

    Returns None only when no progress at all was possible from any seed.
    A CPU-valve or max_iters cutoff instead yields the best partial path
    reconstructed to the lowest-heuristic node actually reached, so a caller
    always gets forward progress rather than a hard stall on a big map.
    """
    if heuristic is None:
        def heuristic(_p: Position) -> int:
            return 0

    open_heap: list[tuple[int, int, int, Point]] = []
    came_from: dict[Point, Optional[Point]] = {}
    g_score: dict[Point, int] = {}
    closed: Set[Point] = set()
    counter = 0

    best_key: Optional[Point] = None
    best_h = 1 << 30

    for s in seeds:
        key = (s.x, s.y)
        if key in g_score:
            continue
        came_from[key] = None
        g_score[key] = 0
        h = heuristic(s)
        if h < best_h:
            best_h, best_key = h, key
        heappush(open_heap, (h, counter, h, key))
        counter += 1

    iters = 0
    while open_heap and iters < max_iters:
        if ct.get_cpu_time_elapsed() > cpu_valve_micros:
            break
        iters += 1
        _f, _c, h, current = heappop(open_heap)
        if current in closed:
            continue
        closed.add(current)

        if h < best_h:
            best_h, best_key = h, current

        if is_goal(Position(*current)):
            return _reconstruct(came_from, current)

        base = g_score[current]
        for nkey in _neighbors(current):
            if nkey in closed:
                continue
            npos = Position(*nkey)
            step = cost_fn(npos)
            if step is None:
                continue
            tentative = base + step
            if max_cost is not None and tentative > max_cost:
                continue
            if tentative >= g_score.get(nkey, 1 << 30):
                continue
            came_from[nkey] = current
            g_score[nkey] = tentative
            nh = heuristic(npos)
            heappush(open_heap, (tentative + nh, counter, nh, nkey))
            counter += 1

    # Only report a path if the search actually left a seed -- a best_key
    # still sitting on a seed means nothing was reachable at all, and the
    # caller's own fallback (sideways evasion / BFS) should take over instead
    # of a zero-length "partial path" that silently does nothing.
    if best_key is not None and g_score.get(best_key, 0) > 0:
        return _reconstruct(came_from, best_key)
    return None
