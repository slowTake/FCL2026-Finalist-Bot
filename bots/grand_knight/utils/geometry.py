"""Grand_knight — Spatial & Coordinate Geometry Utilities.

Centralizes pure geometric functions, packing/unpacking, and vision validation.
"""

from __future__ import annotations

from typing import Any, Optional
from fcode import Controller, Direction, EntityType, Position

from config import Config
from utils.storecoding import (
    pack_builder_intent,
    pack_intent,
    pack_ore_discovery,
    pack_ore_status,
    pack_pos,
    pack_position,
    pack_role_assignment,
    unpack_builder_intent,
    unpack_intent,
    unpack_ore_discovery,
    unpack_ore_status,
    unpack_pos,
    unpack_position,
    unpack_role_assignment,
)


def get_reserved_builder_ids(ct: Controller) -> tuple[int, ...]:
    """Retrieve dynamic reserved builder IDs from SLOT_ROLE_ASSIGNMENT, falling back to Config."""
    packed = ct.read_store(Config.SLOT_ROLE_ASSIGNMENT)
    primary, secondary = unpack_role_assignment(packed)
    if primary != 0:
        return (primary, secondary)
    return Config.LEASH_RESERVED_BUILDER_IDS


def is_in_bounds(ct: Controller, pos: Position) -> bool:
    """True if position is within map grid boundaries."""
    return 0 <= pos.x < ct.get_map_width() and 0 <= pos.y < ct.get_map_height()


def is_adjacent(p1: Position, p2: Position) -> bool:
    """True if p1 and p2 are orthogonally adjacent (Manhattan distance == 1)."""
    return abs(p1.x - p2.x) + abs(p1.y - p2.y) == 1


def is_in_vision(ct: Controller, my_pos: Position, target_pos: Position, vision_radius_sq: int) -> bool:
    """True if target_pos is within map bounds and within Euclidean vision radius squared of my_pos."""
    if not is_in_bounds(ct, target_pos):
        return False
    return my_pos.distance_squared(target_pos) <= vision_radius_sq


def get_core_footprint(ct: Controller) -> list[Position]:
    """Return the 4 grid positions occupied by the ally Core (2x2 footprint) via 4-tier priority."""
    # Tier 1: Current unit is Core
    if ct.get_entity_type() == EntityType.CORE:
        cpos = ct.get_position()
        return [
            cpos,
            Position(cpos.x + 1, cpos.y),
            Position(cpos.x, cpos.y + 1),
            Position(cpos.x + 1, cpos.y + 1),
        ]

    # Tier 2: Read from SLOT_CORE_POS
    raw_core_pos = ct.read_store(Config.SLOT_CORE_POS)
    if raw_core_pos != 0:
        cpos = unpack_position(raw_core_pos)
        if cpos is not None and is_in_bounds(ct, cpos):
            return [
                cpos,
                Position(cpos.x + 1, cpos.y),
                Position(cpos.x, cpos.y + 1),
                Position(cpos.x + 1, cpos.y + 1),
            ]

    # Tier 3: Scan nearby visible buildings
    my_team = ct.get_team()
    for bid in ct.get_nearby_buildings():
        if ct.get_team(bid) == my_team and ct.get_entity_type(bid) == EntityType.CORE:
            cpos = ct.get_position(bid)
            return [
                cpos,
                Position(cpos.x + 1, cpos.y),
                Position(cpos.x, cpos.y + 1),
                Position(cpos.x + 1, cpos.y + 1),
            ]

    # Tier 4: Map-center fallback
    cx = ct.get_map_width() // 2
    cy = ct.get_map_height() // 2
    return [Position(cx, cy)]


def get_nearest_core_tile(pos: Position, core_footprint: list[Position]) -> Position:
    """Find the closest tile within the 2x2 Core footprint to pos."""
    if not core_footprint:
        return Position(0, 0)
    return min(core_footprint, key=lambda c: pos.distance_squared(c))


def get_core_bbox(core_footprint: list[Position]) -> tuple[int, int, int, int]:
    """Return (min_x, min_y, max_x, max_y) bounding box for the Core footprint."""
    xs = [p.x for p in core_footprint]
    ys = [p.y for p in core_footprint]
    return min(xs), min(ys), max(xs), max(ys)


def is_inside_core_area(pos: Position, core_footprint: list[Position], margin: int = 1) -> bool:
    """True if pos is inside the Core bounding box expanded by margin."""
    min_x, min_y, max_x, max_y = get_core_bbox(core_footprint)
    return (min_x - margin) <= pos.x <= (max_x + margin) and (min_y - margin) <= pos.y <= (max_y + margin)


def clamp_to_core_area(pos: Position, core_footprint: list[Position], margin: int = 1) -> Position:
    """Return the closest clamped Position inside the expanded Core bounding box."""
    min_x, min_y, max_x, max_y = get_core_bbox(core_footprint)
    clamped_x = max(min_x - margin, min(max_x + margin, pos.x))
    clamped_y = max(min_y - margin, min(max_y + margin, pos.y))
    return Position(clamped_x, clamped_y)


def get_core_center_tile(core_footprint: list[Position]) -> Position:
    """Return the nearest Core footprint tile to use as an anchor."""
    return core_footprint[0]


def choose_guardian_stand_tile(
    ct: Controller,
    target_tile: Position,
    core_footprint: list[Position],
    traversable_fn: Any,
) -> Optional[Position]:
    """Choose a safe interior stand tile for the Guardian while building target_tile.
    
    Prefers candidates inside the Core area and closer to the Core than target_tile.
    Returns None if all available candidates are exterior without being closer to Core.
    """
    core_center = get_core_center_tile(core_footprint)
    target_dist_to_core = target_tile.distance_squared(core_center)

    candidates: list[tuple[bool, bool, int, Position]] = []
    for d in (Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST):
        cand = target_tile.add(d)
        if not is_in_bounds(ct, cand):
            continue
        if not traversable_fn(cand):
            continue

        inside = is_inside_core_area(cand, core_footprint, margin=1)
        d_core = cand.distance_squared(core_center)
        closer = d_core < target_dist_to_core

        if not inside and not closer:
            continue

        # Sort priority: (inside descending, closer descending, d_core ascending)
        candidates.append((inside, closer, d_core, cand))

    if not candidates:
        return None

    candidates.sort(key=lambda item: (item[0], item[1], -item[2]), reverse=True)
    return candidates[0][3]


def get_greedy_step(from_pos: Position, to_pos: Position) -> Direction:
    """Compute single greedy cardinal step toward target position."""
    dx = to_pos.x - from_pos.x
    dy = to_pos.y - from_pos.y

    if abs(dx) > abs(dy):
        if dx > 0:
            return Direction.EAST
        if dx < 0:
            return Direction.WEST
    else:
        if dy > 0:
            return Direction.SOUTH
        if dy < 0:
            return Direction.NORTH

    return Direction.CENTRE


def is_in_friendly_half(ct: Controller, pos: Position, core_footprint: list[Position]) -> bool:
    """True if pos is closer to ally Core than to inferred enemy Core."""
    map_w = ct.get_map_width()
    map_h = ct.get_map_height()
    cpos = core_footprint[0]
    enemy_cpos = Position(map_w - 1 - cpos.x, map_h - 1 - cpos.y)
    return pos.distance_squared(cpos) <= pos.distance_squared(enemy_cpos)
