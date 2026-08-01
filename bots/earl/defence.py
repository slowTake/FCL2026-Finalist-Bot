"""Core defence role for earl builders.

Late spawns (indices >= DEFENDER_MIN_INDEX) stay near the ally core: lay a
2-deep inward conveyor apron + diagonal barriers, heal friendlies, then fire
adjacent enemies. Gunner building is deferred.
"""

from __future__ import annotations

from fcode import Controller, Direction, EntityType, Position

DEFENDER_MIN_INDEX = 6  # indices 6..9

CARDINALS = [Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST]
DIRECTIONS = [d for d in Direction if d != Direction.CENTRE]


def is_defender(my_index: int) -> bool:
    return my_index >= DEFENDER_MIN_INDEX


def _core_footprint(core_pos: Position) -> list[Position]:
    return [
        core_pos,
        Position(core_pos.x + 1, core_pos.y),
        Position(core_pos.x, core_pos.y + 1),
        Position(core_pos.x + 1, core_pos.y + 1),
    ]


def _in_bounds(ct: Controller, pos: Position) -> bool:
    return 0 <= pos.x < ct.get_map_width() and 0 <= pos.y < ct.get_map_height()


def _allied_belt_id(ct: Controller, pos: Position) -> int | None:
    if not ct.is_in_vision(pos):
        return None
    bid = ct.get_tile_building_id(pos)
    if bid is None:
        return None
    etype = ct.get_entity_type(bid)
    if etype not in (EntityType.CONVEYOR, EntityType.SPLITTER):
        return None
    if ct.get_team(bid) != ct.get_team():
        return None
    return bid


def _belt_faces(ct: Controller, tile: Position, expected: Direction) -> bool:
    bid = _allied_belt_id(ct, tile)
    if bid is None:
        return False
    return ct.get_direction(bid) == expected


def _has_allied_barrier(ct: Controller, pos: Position) -> bool:
    if not ct.is_in_vision(pos):
        # Fog: treat as still pending so we walk nearer and re-check.
        return False
    bid = ct.get_tile_building_id(pos)
    if bid is None:
        return False
    return (
        ct.get_entity_type(bid) == EntityType.BARRIER
        and ct.get_team(bid) == ct.get_team()
    )


def apron_conveyor_specs(
    core_pos: Position, my_index: int
) -> list[tuple[Position, Direction]]:
    """2-wide × 2-deep inward apron strips. Even → N+E; odd → S+W."""
    cx, cy = core_pos.x, core_pos.y
    tiles: list[tuple[Position, Direction]] = []
    for depth in (1, 2):
        if my_index % 2 == 0:
            for dx in (0, 1):
                tiles.append((Position(cx + dx, cy - depth), Direction.SOUTH))
            for dy in (0, 1):
                tiles.append((Position(cx + 1 + depth, cy + dy), Direction.WEST))
        else:
            for dx in (0, 1):
                tiles.append((Position(cx + dx, cy + 1 + depth), Direction.NORTH))
            for dy in (0, 1):
                tiles.append((Position(cx - depth, cy + dy), Direction.EAST))
    return tiles


def apron_barrier_tiles(core_pos: Position, my_index: int) -> list[Position]:
    """Depth-1 diagonal barriers. Even → NW+NE; odd → SW+SE."""
    cx, cy = core_pos.x, core_pos.y
    if my_index % 2 == 0:
        return [
            Position(cx - 1, cy - 1),  # NW
            Position(cx + 2, cy - 1),  # NE
        ]
    return [
        Position(cx - 1, cy + 2),  # SW
        Position(cx + 2, cy + 2),  # SE
    ]


def try_make_apron(
    ct: Controller, *, core_pos: Position, my_index: int
) -> bool:
    """Lay owned apron conveyors + barriers. True if built or moved this turn."""
    pending: list[Position] = []

    for tile, facing in apron_conveyor_specs(core_pos, my_index):
        if not _in_bounds(ct, tile):
            continue
        if _belt_faces(ct, tile, facing):
            continue
        pending.append(tile)

    for tile in apron_barrier_tiles(core_pos, my_index):
        if not _in_bounds(ct, tile):
            continue
        if _has_allied_barrier(ct, tile):
            continue
        pending.append(tile)

    if not pending:
        return False

    pos = ct.get_position()

    # Standing on a pending tile — step off so we can build adjacent next turn
    for tile in pending:
        if pos.x == tile.x and pos.y == tile.y:
            if ct.get_move_cooldown() == 0:
                for d in CARDINALS:
                    if ct.can_move(d):
                        ct.move(d)
                        return True
            return True  # occupied the turn intent (cannot leave yet)

    # Try adjacent builds; skip tiles that are not buildable
    if ct.get_action_cooldown() == 0:
        for tile, facing in apron_conveyor_specs(core_pos, my_index):
            if not _in_bounds(ct, tile):
                continue
            if _belt_faces(ct, tile, facing):
                continue
            if abs(pos.x - tile.x) + abs(pos.y - tile.y) != 1:
                continue
            if ct.can_build_conveyor(tile, facing):
                ct.build_conveyor(tile, facing)
                return True
        for tile in apron_barrier_tiles(core_pos, my_index):
            if not _in_bounds(ct, tile):
                continue
            if _has_allied_barrier(ct, tile):
                continue
            if abs(pos.x - tile.x) + abs(pos.y - tile.y) != 1:
                continue
            if ct.can_build_barrier(tile):
                ct.build_barrier(tile)
                return True

    # Nothing built — walk toward nearest pending tile
    if ct.get_move_cooldown() != 0:
        return False
    goal = min(pending, key=lambda t: pos.distance_squared(t))
    desired = pos.direction_to(goal)
    if desired == Direction.CENTRE:
        return False
    primary = [desired, desired.rotate_left(), desired.rotate_right()]
    for d in primary:
        if d != Direction.CENTRE and ct.can_move(d):
            ct.move(d)
            return True
    remaining = [d for d in DIRECTIONS if d not in primary]
    for d in remaining:
        if ct.can_move(d):
            ct.move(d)
            return True
    return False


def _defence_hold_target(ct: Controller, core_pos: Position) -> Position:
    """Prefer an empty apron tile; fall back to core NW so mover walks adjacent."""
    pos = ct.get_position()
    footprint = {(t.x, t.y) for t in _core_footprint(core_pos)}
    best: Position | None = None
    best_dist = float("inf")
    w, h = ct.get_map_width(), ct.get_map_height()

    for tile in _core_footprint(core_pos):
        for d in CARDINALS:
            cand = tile.add(d)
            key = (cand.x, cand.y)
            if key in footprint:
                continue
            if not (0 <= cand.x < w and 0 <= cand.y < h):
                continue
            if ct.is_in_vision(cand) and not ct.is_tile_empty(cand):
                # Still a valid linger spot if we're already on it
                if pos.x != cand.x or pos.y != cand.y:
                    continue
            dist = pos.distance_squared(cand)
            if dist < best_dist:
                best_dist = dist
                best = cand
    return best if best is not None else core_pos


def _try_heal(ct: Controller) -> bool:
    if ct.get_action_cooldown() != 0:
        return False
    pos = ct.get_position()
    for d in Direction:
        if d == Direction.CENTRE:
            continue
        check = pos.add(d)
        # Heal is orthogonal-only for builders
        if d not in CARDINALS:
            continue
        if ct.can_heal(check):
            ct.heal(check)
            return True
    return False


def _try_adjacent_fire(ct: Controller) -> bool:
    """Fire orthogonal adjacent enemies: prefer builders, then any enemy building."""
    if ct.get_action_cooldown() != 0:
        return False
    pos = ct.get_position()
    my_team = ct.get_team()
    builder_tile: Position | None = None
    other_tile: Position | None = None

    for d in CARDINALS:
        tile = pos.add(d)
        if not ct.can_fire(tile):
            continue
        bot = ct.get_tile_builder_bot_id(tile)
        if bot is not None and ct.get_team(bot) != my_team:
            builder_tile = tile
            break
        bid = ct.get_tile_building_id(tile)
        if bid is None:
            continue
        if ct.get_team(bid) == my_team:
            continue
        if other_tile is None:
            other_tile = tile

    target = builder_tile if builder_tile is not None else other_tile
    if target is None:
        return False
    ct.fire(target)
    return True


def run_defender(
    ct: Controller, *, core_pos: Position | None, my_index: int
) -> Position | None:
    """Apron first; else heal/fire and return linger target. None if turn used."""
    if core_pos is not None and try_make_apron(
        ct, core_pos=core_pos, my_index=my_index
    ):
        return None

    if not _try_heal(ct):
        _try_adjacent_fire(ct)

    if core_pos is None:
        return None
    return _defence_hold_target(ct, core_pos)
