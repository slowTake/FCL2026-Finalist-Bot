"""Shared Florent Code League helpers (fcode 2.2).

Reusable bot utilities you can import from a repo checkout or copy into a
single-file submission. League upload zips only ``main.py``, so before
submitting copy any functions you need from this module into your bot file.
"""

from __future__ import annotations

from fcode import Direction, Position


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
