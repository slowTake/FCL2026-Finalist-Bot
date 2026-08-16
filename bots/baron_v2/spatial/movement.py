"""knight — Spatial Domain: Navigation, BFS Pathfinding & Greedy Evasion.

Stateless micro movement interfaces implementing BFS unit navigation, sideways-first evasion,
terrain memory (seen_walls), and strict 2-turn oscillation guards without calling raw ct.move.
"""

from __future__ import annotations

from collections import deque
from typing import Any, Optional, Sequence, Set
from fcode import Controller, Direction, Position

from config import Config
from utils.geometry import is_adjacent, is_in_bounds, is_in_vision


def is_traversable(ct: Controller, pos: Position, seen_walls: Optional[Set[Position]] = None) -> bool:
    """Single ground-truth helper for movement validity based on engine truth & terrain memory.
    
    Checks in-bounds, checks seen_walls, verifies vision range, and defers to ct.is_tile_passable(pos).
    """
    if not is_in_bounds(ct, pos):
        return False
    if seen_walls is not None and pos in seen_walls:
        return False
    my_pos = ct.get_position()
    if not is_in_vision(ct, my_pos, pos, Config.BUILDER_BOT_VISION_RADIUS_SQ):
        return True
    try:
        return ct.is_tile_passable(pos)
    except Exception:
        return True


def get_greedy_step(current_pos: Position, target_pos: Position) -> Direction:
    """Calculate the primary cardinal step direction toward target_pos.
    
    Args:
        current_pos: Current unit position
        target_pos: Desired destination tile position

    Returns:
        Direction enum (NORTH, EAST, SOUTH, WEST) or CENTRE if already on target tile.
    """
    return current_pos.cardinal_direction_to(target_pos)


def find_builder_navigation_path(
    ct: Controller,
    start_pos: Position,
    target_pos: Position,
    max_hops: int = 40,
    seen_walls: Optional[Set[Position]] = None,
    allowed_fn: Optional[Any] = None,
) -> list[Direction]:
    """BFS pathfinder for unit navigation around walls and buildings to reach target_pos or adjacent."""
    if start_pos == target_pos or is_adjacent(start_pos, target_pos):
        return []

    goals: set[Position] = set()
    for d in (Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST):
        adj = target_pos.add(d)
        if is_in_bounds(ct, adj) and is_traversable(ct, adj, seen_walls):
            if allowed_fn is None or allowed_fn(adj):
                goals.add(adj)

    if not goals:
        if is_in_bounds(ct, target_pos) and is_traversable(ct, target_pos, seen_walls):
            goals.add(target_pos)

    if start_pos in goals:
        return []

    queue: deque[tuple[Position, list[Direction]]] = deque([(start_pos, [])])
    visited: set[Position] = {start_pos}

    while queue:
        if ct.get_cpu_time_elapsed() > Config.CPU_SEARCH_VALVE_MICROSEC:
            return []  # Abort pathfinding to protect unit from CPU timeout

        curr, path = queue.popleft()

        if curr in goals:
            return path

        if len(path) >= max_hops:
            continue

        for d in (Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST):
            nxt = curr.add(d)
            if not is_in_bounds(ct, nxt) or nxt in visited:
                continue
            if not is_traversable(ct, nxt, seen_walls):
                continue
            if allowed_fn is not None and not allowed_fn(nxt):
                continue

            visited.add(nxt)
            queue.append((nxt, path + [d]))

    return []


def choose_bug2_direction(
    ct: Controller,
    current_pos: Position,
    target_pos: Position,
    pos_history: Optional[Sequence[Position]] = None,
    seen_walls: Optional[Set[Position]] = None,
) -> Optional[Direction]:
    """Calculate best navigation Direction using BFS and sideways-first evasion without executing move.

    Args:
        ct: Unit Controller instance
        current_pos: Current unit position
        target_pos: Desired destination tile position
        pos_history: Optional sequence of recent unit positions for oscillation check
        seen_walls: Optional set of remembered wall positions

    Returns:
        Direction enum if a valid step exists, or None if completely blocked.
    """
    # Primary: Try BFS navigation path
    nav_path = find_builder_navigation_path(ct, current_pos, target_pos, seen_walls=seen_walls)
    if nav_path:
        first_step = nav_path[0]
        nxt = current_pos.add(first_step)
        if ct.can_move(first_step) and is_traversable(ct, nxt, seen_walls):
            return first_step

    primary_dir = get_greedy_step(current_pos, target_pos)
    if primary_dir == Direction.CENTRE:
        return None

    pos_t_minus_2 = pos_history[-2] if (pos_history and len(pos_history) >= 2) else None

    # Candidate directions: Primary -> Rotate Right -> Rotate Left -> Opposite
    candidates: list[Direction] = []
    if primary_dir.is_cardinal():
        candidates.append(primary_dir)
    side_right = primary_dir.rotate_right()
    if side_right.is_cardinal():
        candidates.append(side_right)
    side_left = primary_dir.rotate_left()
    if side_left.is_cardinal():
        candidates.append(side_left)
    back = primary_dir.opposite()
    if back.is_cardinal():
        candidates.append(back)

    # Pass 1: Strict oscillation prevention (reject steps returning to pos_t_minus_2)
    for d in candidates:
        nxt = current_pos.add(d)
        if ct.can_move(d) and is_traversable(ct, nxt, seen_walls):
            if pos_t_minus_2 is None or nxt != pos_t_minus_2:
                return d

    # Pass 2: Last resort (allow stepping to pos_t_minus_2 if no other step exists)
    for d in candidates:
        nxt = current_pos.add(d)
        if ct.can_move(d) and is_traversable(ct, nxt, seen_walls):
            return d

    return None


# Backward-compatible aliases
plan_bug2_step = choose_bug2_direction


def execute_bug2_evasion(
    ct: Controller,
    current_pos: Position,
    target_pos: Position,
    pos_history: Optional[Sequence[Position]] = None,
    seen_walls: Optional[Set[Position]] = None,
) -> bool:
    """Compatibility adapter: plans and steps toward target_pos."""
    if ct.get_move_cooldown() != 0:
        return False
    d = choose_bug2_direction(ct, current_pos, target_pos, pos_history, seen_walls)
    if d is not None and d != Direction.CENTRE and ct.can_move(d):
        ct.move(d)
        return True
    return False
