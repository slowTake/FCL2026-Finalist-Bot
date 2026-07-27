from __future__ import annotations
from heapq import heappop, heappush
from typing import Iterable
from fcode import Position
from bots.map_detector_bot.hivemind import HiveMind

def _to_point(pos: object) -> Position:
    if isinstance(pos, Position):
        return pos
    if isinstance(pos, tuple):
        x, y = pos
        return Position(x, y)
    if hasattr(pos, "x") and hasattr(pos, "y"):
        return Position(pos.x, pos.y)
    raise TypeError(f"Unsupported position type: {type(pos)!r}")

def heuristic(a: object, b: object) -> int:
    ax, ay = _to_point(a).x, _to_point(a).y
    bx, by = _to_point(b).x, _to_point(b).y
    return abs(ax - bx) + abs(ay - by)


def neighbors(pos: object) -> Iterable[Position]:
    p = _to_point(pos)
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        yield Position(p.x + dx, p.y + dy)

def is_known_passable(pos: object, hivemind: HiveMind = None) -> bool:
    point = _to_point(pos)

    if hivemind is not None:
        entry = hivemind.local_map.get((point.x, point.y))
        if entry is None:
            return False
        return bool(entry.get("passable", False))

    if map_data is None:
        raise ValueError("Provide hivemind or map_data")

    entry = map_data.get((point.x, point.y))
    if entry is None:
        return False
    return bool(entry.get("passable", False))

def reconstruct_path(came_from: dict[Position, Position], current: Position) -> list[Position]:
    path = [current]
    while current in came_from:
        current = came_from[current]
        path.append(current)
    path.reverse()
    return path

def astar(start: object, goal: object, hivemind: HiveMind = None):
    """
    A* algo to find path between start and goal. Feed it the hivemind and it should find a good path between. Returns a list of positions or None
    if a path cannot be found, probably due to insufficient map knowledge (or a bug who knows)
    """
    start_point = _to_point(start)
    goal_point = _to_point(goal)

    if start_point == goal_point:
        return [start_point]

    open_set = [(heuristic(start_point, goal_point), start_point)]
    came_from: dict[Position, Position] = {}
    g_score = {start_point: 0}

    while open_set:
        _, current = heappop(open_set)

        if current == goal_point:
            return reconstruct_path(came_from, current)

        for nxt in neighbors(current):
            if not is_known_passable(nxt, hivemind=hivemind):
                continue

            tentative = g_score[current] + 1
            if tentative < g_score.get(nxt, float("inf")):
                came_from[nxt] = current
                g_score[nxt] = tentative
                heappush(open_set, (tentative + heuristic(nxt, goal_point), nxt))

    return None
