"""Shared Florent Code League helpers (fcode 2.2).

Reusable bot utilities you can import from a repo checkout or copy into a
single-file submission. League upload zips only ``main.py``, so before
submitting copy any functions you need from this module into your bot file.
"""

from __future__ import annotations

from fcode import Direction, Position, Game

def is_tile_passable(pos: Position) -> bool:
    """Return True if a friendly builder bot could stand on this tile."""
    return (
        Game.get_tile_building_id(pos) is None
        and not Game.is_tile_empty(pos)
    )

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

def make_path(start: Position, end: Position, y_first: bool = False) -> Direction:
    """Return the next cardinal step on an L-shaped Manhattan path toward core.

    Default axis order: larger |dx| vs |dy| first (horizontal bias on ties
    when y_first=False). Set y_first=True to prefer vertical steps first.
    Returns Direction.CENTRE when already aligned on both axes.
    """
    dx = end.x - start.x
    dy = end.y - start.y

    if dx == 0 and dy == 0: # already on target, probably not needed but just in case it'll save a few ms
        return Direction.CENTRE

    def step_x() -> Direction | None:
        if dx == 0:
            return None
        if dx > 0:
            next_pos = Position(start.x + 1, start.y)
            if 0 <= next_pos.x < Game.get_map_width() and 0 <= next_pos.y < Game.get_map_height() and is_tile_passable(next_pos):
                return Direction.EAST # check if east is a viable tile and if so go that way
        elif dx < 0:
            next_pos = Position(start.x - 1, start.y)
            if 0 <= next_pos.x < Game.get_map_width() and 0 <= next_pos.y < Game.get_map_height() and is_tile_passable(next_pos):
                return Direction.WEST # check if west is a viable tile and if so go that way
        return None # fallback if east and west are blocked

    def step_y() -> Direction | None:
        if dy == 0:
            return None
        if dy > 0:
            next_pos = Position(start.x, start.y + 1)
            if 0 <= next_pos.x < Game.get_map_width() and 0 <= next_pos.y < Game.get_map_height() and is_tile_passable(next_pos):
                return Direction.SOUTH # check if south is a viable tile and if so go that way
        elif dy < 0:
            next_pos = Position(start.x, start.y - 1)
            if 0 <= next_pos.x < Game.get_map_width() and 0 <= next_pos.y < Game.get_map_height() and is_tile_passable(next_pos):
                return Direction.NORTH # check if north is a viable tile and if so go that way
        return None # fallback if north and south are blocked

    if y_first:
        return step_y() or step_x() or Direction.CENTRE
    if abs(dx) >= abs(dy):
        return step_x() or step_y() or Direction.CENTRE
    return step_y() or step_x() or Direction.CENTRE
