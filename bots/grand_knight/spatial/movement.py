"""Grand_knight — Spatial Domain: Navigation, A* Pathfinding & Greedy Evasion.

Unit navigation built on the lean A* kernel in spatial/astar.py: a per-unit
PathCache holds a chunked route (max Config.PATH_CHUNK_SIZE steps) that is
replanned only when exhausted, stale, or its next step is dynamically
blocked -- never recomputed from scratch every tick. Falls back to
sideways-first evasion with a strict oscillation guard when no route exists
at all. Never calls raw ct.move.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence, Set

from fcode import Controller, Direction, Position

from config import Config
from engine.safe_controller import SafeController
from spatial.astar import astar_from_seeds, make_lean_cost_fn, manhattan_heuristic
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


def _tile_available(ct: Controller, pos: Position, seen_walls: Optional[Set[Position]] = None) -> bool:
    """Traversable and not currently occupied by another builder bot."""
    if not is_traversable(ct, pos, seen_walls):
        return False
    try:
        return ct.get_tile_builder_bot_id(pos) is None
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


def _approach_goal_tiles(
    ct: Controller,
    target_pos: Position,
    seen_walls: Optional[Set[Position]] = None,
    allowed_fn: Optional[Any] = None,
) -> Set[Position]:
    """Tiles orthogonally adjacent to target_pos that qualify as a stand point,
    falling back to target_pos itself when no adjacent tile qualifies."""
    goals: Set[Position] = set()
    for d in (Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST):
        adj = target_pos.add(d)
        if is_in_bounds(ct, adj) and is_traversable(ct, adj, seen_walls):
            if allowed_fn is None or allowed_fn(adj):
                goals.add(adj)
    if not goals:
        if is_in_bounds(ct, target_pos) and is_traversable(ct, target_pos, seen_walls):
            goals.add(target_pos)
    return goals


def find_builder_navigation_path(
    ct: Controller,
    start_pos: Position,
    target_pos: Position,
    max_hops: int = 40,
    seen_walls: Optional[Set[Position]] = None,
    allowed_fn: Optional[Any] = None,
) -> list[Direction]:
    """A*-backed pathfinder for unit navigation around walls and buildings to reach target_pos or adjacent."""
    if start_pos == target_pos or is_adjacent(start_pos, target_pos):
        return []

    goals = _approach_goal_tiles(ct, target_pos, seen_walls, allowed_fn)
    if not goals or start_pos in goals:
        return []

    path = astar_from_seeds(
        [start_pos],
        ct,
        cost_fn=make_lean_cost_fn(ct, seen_walls, allowed_fn),
        is_goal=lambda p: p in goals,
        heuristic=manhattan_heuristic(target_pos),
        max_cost=max_hops,
    )
    if not path or len(path) < 2:
        return []
    return [path[i].cardinal_direction_to(path[i + 1]) for i in range(len(path) - 1)]


def _chunk_waypoint(pos: Position, target: Position, chunk: int) -> Position:
    """A point at most `chunk` steps along the Manhattan route toward target."""
    dx = target.x - pos.x
    dy = target.y - pos.y
    total = abs(dx) + abs(dy)
    if total <= chunk:
        return target
    step_x = round(chunk * (abs(dx) / total))
    step_y = chunk - step_x
    sx = 1 if dx > 0 else -1 if dx < 0 else 0
    sy = 1 if dy > 0 else -1 if dy < 0 else 0
    return Position(pos.x + sx * min(step_x, abs(dx)), pos.y + sy * min(step_y, abs(dy)))


def _plan_chunked_path(
    ct: Controller,
    current_pos: Position,
    target_pos: Position,
    seen_walls: Optional[Set[Position]],
) -> list[Position]:
    """Chunked A* replan: search directly to target_pos's approach tiles when
    within one chunk, otherwise to an intermediate waypoint capped at
    Config.PATH_CHUNK_SIZE steps -- count's raidBot chunked-travel model."""
    if current_pos == target_pos or is_adjacent(current_pos, target_pos):
        return []

    manhattan_dist = abs(current_pos.x - target_pos.x) + abs(current_pos.y - target_pos.y)
    if manhattan_dist <= Config.PATH_CHUNK_SIZE:
        goals = _approach_goal_tiles(ct, target_pos, seen_walls)
        if not goals or current_pos in goals:
            return []
        is_goal = (lambda p: p in goals)
        heuristic = manhattan_heuristic(target_pos)
    else:
        waypoint = _chunk_waypoint(current_pos, target_pos, Config.PATH_CHUNK_SIZE)
        is_goal = (lambda p: p == waypoint)
        heuristic = manhattan_heuristic(waypoint)

    path = astar_from_seeds(
        [current_pos],
        ct,
        cost_fn=make_lean_cost_fn(ct, seen_walls),
        is_goal=is_goal,
        heuristic=heuristic,
    )
    if not path or len(path) < 2:
        return []
    return path[1:]


class PathCache:
    """Per-unit persistent chunked A* route. Own one instance per navigating
    role and pass it explicitly into execute_bug2_evasion -- never share one
    across two unrelated concurrent destinations."""

    __slots__ = ("target", "path")

    def __init__(self) -> None:
        self.target: Optional[Position] = None
        self.path: list[Position] = []

    def clear(self) -> None:
        self.target = None
        self.path = []


def _evasion_fallback_step(
    ct: Controller,
    current_pos: Position,
    target_pos: Position,
    pos_history: Optional[Sequence[Position]] = None,
    seen_walls: Optional[Set[Position]] = None,
) -> Optional[Direction]:
    """Last-resort sideways-first evasion with oscillation guard, used only
    when A* finds no route at all (e.g. a fully enclosed unit)."""
    primary_dir = get_greedy_step(current_pos, target_pos)
    if primary_dir == Direction.CENTRE:
        return None

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

    recent_visited = set(pos_history[-4:]) if pos_history else set()
    for d in candidates:
        nxt = current_pos.add(d)
        if ct.can_move(d) and _tile_available(ct, nxt, seen_walls) and nxt not in recent_visited:
            return d
    for d in candidates:
        nxt = current_pos.add(d)
        if ct.can_move(d) and _tile_available(ct, nxt, seen_walls):
            return d
    return None


def choose_bug2_direction(
    ct: Controller,
    current_pos: Position,
    target_pos: Position,
    pos_history: Optional[Sequence[Position]] = None,
    seen_walls: Optional[Set[Position]] = None,
) -> Optional[Direction]:
    """Calculate best navigation Direction using A* and sideways-first evasion fallback."""
    if current_pos == target_pos or is_adjacent(current_pos, target_pos):
        return None

    nav_path = find_builder_navigation_path(ct, current_pos, target_pos, seen_walls=seen_walls)
    if nav_path:
        first_step = nav_path[0]
        nxt = current_pos.add(first_step)
        if ct.can_move(first_step) and is_traversable(ct, nxt, seen_walls):
            return first_step

    return _evasion_fallback_step(ct, current_pos, target_pos, pos_history, seen_walls)


# Backward-compatible alias
plan_bug2_step = choose_bug2_direction


def execute_bug2_evasion(
    ct: Controller,
    safe: SafeController,
    path_cache: PathCache,
    current_pos: Position,
    target_pos: Position,
    pos_history: Optional[Sequence[Position]] = None,
    seen_walls: Optional[Set[Position]] = None,
) -> bool:
    """Step toward target_pos via a cached, chunked A* route.

    Only replans when path_cache is empty, targets something else, or its
    next step is dynamically blocked (newly built wall/turret or an occupying
    unit) -- never recomputes the full route every tick. Falls back to
    sideways-first evasion if no route exists at all. Executes exclusively
    through SafeController, never raw ct.move.
    """
    if ct.get_move_cooldown() != 0:
        return False

    if current_pos == target_pos or is_adjacent(current_pos, target_pos):
        path_cache.clear()
        return False

    # Tier 1: consume the cached chunk if it's still valid.
    if path_cache.target == target_pos and path_cache.path:
        nxt = path_cache.path[0]
        if _tile_available(ct, nxt, seen_walls):
            d = current_pos.cardinal_direction_to(nxt)
            if d.is_cardinal() and ct.can_move(d):
                outcome = safe.safe_move(d)
                if outcome.success:
                    path_cache.path.pop(0)
                    if not path_cache.path:
                        path_cache.clear()
                    return True
        path_cache.clear()

    # Tier 2: replan a fresh chunk (at most Config.PATH_CHUNK_SIZE steps).
    new_path = _plan_chunked_path(ct, current_pos, target_pos, seen_walls)
    if new_path:
        nxt = new_path[0]
        if _tile_available(ct, nxt, seen_walls):
            d = current_pos.cardinal_direction_to(nxt)
            if d.is_cardinal() and ct.can_move(d):
                outcome = safe.safe_move(d)
                if outcome.success:
                    path_cache.target = target_pos
                    path_cache.path = new_path[1:]
                    return True

    # Tier 3: no route found at all -- sideways-first evasion.
    path_cache.clear()
    d = _evasion_fallback_step(ct, current_pos, target_pos, pos_history, seen_walls)
    if d is not None:
        outcome = safe.safe_move(d)
        return outcome.success
    return False
