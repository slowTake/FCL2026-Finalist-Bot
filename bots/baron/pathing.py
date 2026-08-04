"""A* and distance helpers for builder walk and relay routing."""

from __future__ import annotations

from fcode import Controller, Position


def manhattan(a: Position, b: Position) -> int:
    return abs(a.x - b.x) + abs(a.y - b.y)


def astar(
    ct: Controller,
    start: Position,
    goal: Position,
    *,
    is_passable,
    max_iters: int = 400,
) -> list[Position] | None:
    del ct, start, goal, is_passable, max_iters
    return None
