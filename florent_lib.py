"""Shared Florent Code League helpers (fcode 2.2).

Reusable bot utilities you can import from a repo checkout or copy into a
single-file submission. League upload zips only ``main.py``, so before
submitting copy any functions you need from this module into your bot file.
"""

from __future__ import annotations

from collections import deque

from fcode import Controller, Direction, Position


def is_tile_passable_ct(ct: Controller, pos: Position) -> bool:
    """Return True if a friendly builder bot could stand on this tile."""
    return ct.is_tile_passable(pos)


def l_path_step(pos: Position, core: Position, y_first: bool = False) -> Direction:
    """Return the next cardinal step on an L-shaped Manhattan path toward core.

    Default axis order: larger |dx| vs |dy| first (horizontal bias on ties
    when y_first=False). Set y_first=True to prefer vertical steps first.
    Returns Direction.CENTRE when already aligned on both axes.
    """
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


def make_path_ct(
    ct: Controller,
    start: Position,
    end: Position,
    y_first: bool = False,
) -> Direction:
    """Return the next cardinal step on an L-shaped path toward end.

    Skips a cardinal step when the candidate tile is not passable for a
    friendly builder. Falls back to the other axis or Direction.CENTRE.
    """
    dx = end.x - start.x
    dy = end.y - start.y
    width = ct.get_map_width()
    height = ct.get_map_height()

    if dx == 0 and dy == 0:
        return Direction.CENTRE

    def step_x() -> Direction | None:
        if dx == 0:
            return None
        if dx > 0:
            next_pos = Position(start.x + 1, start.y)
            if (
                0 <= next_pos.x < width
                and 0 <= next_pos.y < height
                and is_tile_passable_ct(ct, next_pos)
            ):
                return Direction.EAST
        else:
            next_pos = Position(start.x - 1, start.y)
            if (
                0 <= next_pos.x < width
                and 0 <= next_pos.y < height
                and is_tile_passable_ct(ct, next_pos)
            ):
                return Direction.WEST
        return None

    def step_y() -> Direction | None:
        if dy == 0:
            return None
        if dy > 0:
            next_pos = Position(start.x, start.y + 1)
            if (
                0 <= next_pos.x < width
                and 0 <= next_pos.y < height
                and is_tile_passable_ct(ct, next_pos)
            ):
                return Direction.SOUTH
        else:
            next_pos = Position(start.x, start.y - 1)
            if (
                0 <= next_pos.x < width
                and 0 <= next_pos.y < height
                and is_tile_passable_ct(ct, next_pos)
            ):
                return Direction.NORTH
        return None

    if y_first:
        return step_y() or step_x() or Direction.CENTRE
    if abs(dx) >= abs(dy):
        return step_x() or step_y() or Direction.CENTRE
    return step_y() or step_x() or Direction.CENTRE


# Backwards-compatible alias (requires a Controller at runtime).
make_path = make_path_ct


_CARDINAL_DELTAS = (
    (Direction.NORTH, 0, -1),
    (Direction.SOUTH, 0, 1),
    (Direction.EAST, 1, 0),
    (Direction.WEST, -1, 0),
)


def is_walkable_in_local_map(local_map: dict, x: int, y: int) -> bool:
    """Return True if a tile is traversable according to HiveMind local_map."""
    tile = local_map.get((x, y))
    if tile is None:
        return True
    return tile.get("passable", False)


def bfs_next_step(
    local_map: dict,
    start: Position,
    goal: Position,
    width: int,
    height: int,
) -> Direction | None:
    """Return the first cardinal step toward goal using BFS over local_map."""
    if start.x == goal.x and start.y == goal.y:
        return None

    start_key = (start.x, start.y)
    goal_key = (goal.x, goal.y)
    parent: dict[tuple[int, int], tuple[int, int] | None] = {start_key: None}
    queue: deque[tuple[int, int]] = deque([start_key])

    while queue:
        cx, cy = queue.popleft()
        if (cx, cy) == goal_key:
            cur = goal_key
            while parent[cur] not in (None, start_key):
                nxt = parent[cur]
                if nxt is None:
                    return None
                cur = nxt
            if parent[cur] is None:
                return None
            dx = cur[0] - start.x
            dy = cur[1] - start.y
            for direction, ddx, ddy in _CARDINAL_DELTAS:
                if ddx == dx and ddy == dy:
                    return direction
            return None

        for _direction, dx, dy in _CARDINAL_DELTAS:
            nx, ny = cx + dx, cy + dy
            if not (0 <= nx < width and 0 <= ny < height):
                continue
            if (nx, ny) in parent:
                continue
            if not is_walkable_in_local_map(local_map, nx, ny):
                continue
            parent[(nx, ny)] = (cx, cy)
            queue.append((nx, ny))

    return None
