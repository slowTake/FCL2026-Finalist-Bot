"""Core defence role for earl builders.

Late spawns (indices >= DEFENDER_MIN_INDEX) stay near the ally core: hunt
enemy gunners in vision, heal, fire, then lay a per-side inward apron +
corner barrier. Gunner building is deferred.

Even builders may run PHALANX (see ACTIVE_RAID_ORDER in main): assist the
approach-side apron, place 2 sentinels beside approach-side corner barriers
from round 20, then soft-idle (repair / rebuild / linger).
"""

from __future__ import annotations

from attack import (
    SLOT_SENTINEL_COUNT,
    build_sentinel,
)
from fcode import Controller, Direction, EntityType, Position

DEFENDER_MIN_INDEX = 6  # indices 6+

# Cardinal sides around the core, assignment order N → E → S → W
SIDE_N = 0
SIDE_E = 1
SIDE_S = 2
SIDE_W = 3
SIDE_ORDER = (SIDE_N, SIDE_E, SIDE_S, SIDE_W)

CARDINALS = [Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST]
DIRECTIONS = [d for d in Direction if d != Direction.CENTRE]

# Named phases for the home-defence raid order (priority-gated in run_phalanx).
PHALANX = ("assist_apron", "place_phalanx_sentinels", "soft_idle")
PHALANX_SENTINEL_TURN = 20
PHALANX_SENTINEL_COUNT = 2

_NEIGHBOR_OFFSETS = (
    (-1, -1),
    (-1, 0),
    (-1, 1),
    (0, -1),
    (0, 1),
    (1, -1),
    (1, 0),
    (1, 1),
)


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
        return False
    bid = ct.get_tile_building_id(pos)
    if bid is None:
        return False
    return (
        ct.get_entity_type(bid) == EntityType.BARRIER
        and ct.get_team(bid) == ct.get_team()
    )


def _has_allied_sentinel(ct: Controller, pos: Position) -> bool:
    if not ct.is_in_vision(pos):
        return False
    bid = ct.get_tile_building_id(pos)
    if bid is None:
        return False
    return (
        ct.get_entity_type(bid) == EntityType.SENTINEL
        and ct.get_team(bid) == ct.get_team()
    )


def side_conveyor_specs(
    core_pos: Position, side: int
) -> list[tuple[Position, Direction]]:
    """2-wide × 2-deep inward strip for one cardinal side."""
    cx, cy = core_pos.x, core_pos.y
    tiles: list[tuple[Position, Direction]] = []
    for depth in (1, 2):
        if side == SIDE_N:
            for dx in (0, 1):
                tiles.append((Position(cx + dx, cy - depth), Direction.SOUTH))
        elif side == SIDE_E:
            for dy in (0, 1):
                tiles.append((Position(cx + 1 + depth, cy + dy), Direction.WEST))
        elif side == SIDE_S:
            for dx in (0, 1):
                tiles.append((Position(cx + dx, cy + 1 + depth), Direction.NORTH))
        elif side == SIDE_W:
            for dy in (0, 1):
                tiles.append((Position(cx - depth, cy + dy), Direction.EAST))
    return tiles


def side_barrier_tile(core_pos: Position, side: int) -> Position:
    """Depth-1 diagonal on the clockwise corner of the side."""
    cx, cy = core_pos.x, core_pos.y
    if side == SIDE_N:
        return Position(cx - 1, cy - 1)  # NW
    if side == SIDE_E:
        return Position(cx + 2, cy - 1)  # NE
    if side == SIDE_S:
        return Position(cx + 2, cy + 2)  # SE
    return Position(cx - 1, cy + 2)  # SW


def side_depth1_tiles(core_pos: Position, side: int) -> list[Position]:
    return [t for t, _ in side_conveyor_specs(core_pos, side)[:2]]


def available_sides(ct: Controller, core_pos: Position) -> list[int]:
    """Sides with at least one in-bounds depth-1 apron tile (corner → often 2)."""
    out: list[int] = []
    for side in SIDE_ORDER:
        if any(_in_bounds(ct, t) for t in side_depth1_tiles(core_pos, side)):
            out.append(side)
    return out


def assigned_side(ct: Controller, core_pos: Position, my_index: int) -> int | None:
    sides = available_sides(ct, core_pos)
    if not sides:
        return None
    return sides[(my_index - DEFENDER_MIN_INDEX) % len(sides)]


def apron_conveyor_specs(
    core_pos: Position, side: int
) -> list[tuple[Position, Direction]]:
    return side_conveyor_specs(core_pos, side)


def apron_barrier_tiles(core_pos: Position, side: int) -> list[Position]:
    return [side_barrier_tile(core_pos, side)]


def enemy_approach_side(ally_core: Position, enemy_core: Position) -> int:
    """Dominant cardinal from ally 2×2 center toward the enemy core."""
    cx = ally_core.x + 0.5
    cy = ally_core.y + 0.5
    dx = (enemy_core.x + 0.5) - cx
    dy = (enemy_core.y + 0.5) - cy
    if abs(dx) >= abs(dy):
        return SIDE_E if dx >= 0 else SIDE_W
    return SIDE_S if dy >= 0 else SIDE_N


def approach_corner_barriers(core_pos: Position, side: int) -> list[Position]:
    """Two corner barriers flanking the approach-side apron."""
    return [
        side_barrier_tile(core_pos, side),
        side_barrier_tile(core_pos, (side + 1) % 4),
    ]


def _all_apron_keys(core_pos: Position) -> set[tuple[int, int]]:
    keys: set[tuple[int, int]] = set()
    for s in SIDE_ORDER:
        for tile, _ in side_conveyor_specs(core_pos, s):
            keys.add((tile.x, tile.y))
    return keys


def _pick_beside_barrier(
    ct: Controller,
    *,
    core_pos: Position,
    barrier: Position,
    enemy_core: Position,
    forbidden: set[tuple[int, int]],
) -> Position | None:
    """Outward adjacent tile beside a barrier; never on apron/barrier/core."""
    cx = core_pos.x + 0.5
    cy = core_pos.y + 0.5
    best: Position | None = None
    best_score: tuple[float, float] | None = None

    for dx, dy in _NEIGHBOR_OFFSETS:
        cand = Position(barrier.x + dx, barrier.y + dy)
        key = (cand.x, cand.y)
        if key in forbidden:
            continue
        if not _in_bounds(ct, cand):
            continue
        if ct.is_in_vision(cand):
            if not ct.is_tile_empty(cand) and not _has_allied_sentinel(ct, cand):
                continue
        away = (cand.x - cx) ** 2 + (cand.y - cy) ** 2
        toward = -cand.distance_squared(enemy_core)
        score = (away, toward)
        if best_score is None or score > best_score:
            best_score = score
            best = cand
    return best


def phalanx_sentinel_sites(
    ct: Controller,
    core_pos: Position,
    side: int,
    enemy_core: Position,
) -> list[Position]:
    """One pad beside each approach-side corner barrier (off apron belts)."""
    forbidden = _all_apron_keys(core_pos)
    for tile in _core_footprint(core_pos):
        forbidden.add((tile.x, tile.y))
    barriers = approach_corner_barriers(core_pos, side)
    for b in barriers:
        forbidden.add((b.x, b.y))

    sites: list[Position] = []
    for barrier in barriers:
        site = _pick_beside_barrier(
            ct,
            core_pos=core_pos,
            barrier=barrier,
            enemy_core=enemy_core,
            forbidden=forbidden,
        )
        if site is not None:
            sites.append(site)
            forbidden.add((site.x, site.y))
    return sites


def _apron_incomplete(ct: Controller, core_pos: Position, side: int) -> bool:
    for tile, facing in side_conveyor_specs(core_pos, side):
        if not _in_bounds(ct, tile):
            continue
        if not _belt_faces(ct, tile, facing):
            return True
    for tile in apron_barrier_tiles(core_pos, side):
        if not _in_bounds(ct, tile):
            continue
        if not _has_allied_barrier(ct, tile):
            return True
    return False


def _phalanx_apron_incomplete(
    ct: Controller, core_pos: Position, side: int
) -> bool:
    """Approach-side apron plus the other flanking corner barrier."""
    if _apron_incomplete(ct, core_pos, side):
        return True
    other = side_barrier_tile(core_pos, (side + 1) % 4)
    if _in_bounds(ct, other) and not _has_allied_barrier(ct, other):
        return True
    return False


def _try_build_barrier_at(ct: Controller, tile: Position) -> bool:
    """Build or step toward a single barrier tile. True if acted/moved."""
    if not _in_bounds(ct, tile):
        return False
    if _has_allied_barrier(ct, tile):
        return False
    pos = ct.get_position()
    if pos.x == tile.x and pos.y == tile.y:
        if ct.get_move_cooldown() == 0:
            for d in CARDINALS:
                if ct.can_move(d):
                    ct.move(d)
                    return True
        return True
    if ct.get_action_cooldown() == 0:
        if abs(pos.x - tile.x) + abs(pos.y - tile.y) == 1:
            if ct.can_build_barrier(tile):
                ct.build_barrier(tile)
                return True
    if ct.get_move_cooldown() != 0:
        return False
    desired = pos.direction_to(tile)
    if desired == Direction.CENTRE:
        return False
    primary = [desired, desired.rotate_left(), desired.rotate_right()]
    for d in primary:
        if d != Direction.CENTRE and ct.can_move(d):
            ct.move(d)
            return True
    for d in DIRECTIONS:
        if d not in primary and ct.can_move(d):
            ct.move(d)
            return True
    return False


def _phalanx_try_apron(
    ct: Controller, *, core_pos: Position, side: int
) -> bool:
    """Lay approach-side apron, then the other flanking barrier if needed."""
    if try_make_apron(ct, core_pos=core_pos, side=side):
        return True
    other = side_barrier_tile(core_pos, (side + 1) % 4)
    return _try_build_barrier_at(ct, other)


def try_make_apron(
    ct: Controller, *, core_pos: Position, side: int
) -> bool:
    """Lay this side's apron conveyors + barrier. True if built or moved."""
    pending: list[Position] = []
    conveyor_specs = apron_conveyor_specs(core_pos, side)
    barriers = apron_barrier_tiles(core_pos, side)

    for tile, facing in conveyor_specs:
        if not _in_bounds(ct, tile):
            continue
        if _belt_faces(ct, tile, facing):
            continue
        pending.append(tile)

    for tile in barriers:
        if not _in_bounds(ct, tile):
            continue
        if _has_allied_barrier(ct, tile):
            continue
        pending.append(tile)

    if not pending:
        return False

    pos = ct.get_position()

    for tile in pending:
        if pos.x == tile.x and pos.y == tile.y:
            if ct.get_move_cooldown() == 0:
                for d in CARDINALS:
                    if ct.can_move(d):
                        ct.move(d)
                        return True
            return True

    if ct.get_action_cooldown() == 0:
        for tile, facing in conveyor_specs:
            if not _in_bounds(ct, tile):
                continue
            if _belt_faces(ct, tile, facing):
                continue
            if abs(pos.x - tile.x) + abs(pos.y - tile.y) != 1:
                continue
            if ct.can_build_conveyor(tile, facing):
                ct.build_conveyor(tile, facing)
                return True
        for tile in barriers:
            if not _in_bounds(ct, tile):
                continue
            if _has_allied_barrier(ct, tile):
                continue
            if abs(pos.x - tile.x) + abs(pos.y - tile.y) != 1:
                continue
            if ct.can_build_barrier(tile):
                ct.build_barrier(tile)
                return True

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


def _defence_hold_target(
    ct: Controller, core_pos: Position, side: int | None
) -> Position:
    """Prefer empty depth-1 tile on assigned side; else any side apron; else core."""
    pos = ct.get_position()
    candidates: list[Position] = []
    if side is not None:
        candidates.extend(side_depth1_tiles(core_pos, side))
        for tile, _ in side_conveyor_specs(core_pos, side):
            if tile not in candidates:
                candidates.append(tile)
    else:
        for s in SIDE_ORDER:
            candidates.extend(side_depth1_tiles(core_pos, s))

    best: Position | None = None
    best_dist = float("inf")
    for cand in candidates:
        if not _in_bounds(ct, cand):
            continue
        if ct.is_in_vision(cand) and not ct.is_tile_empty(cand):
            if pos.x != cand.x or pos.y != cand.y:
                continue
        dist = pos.distance_squared(cand)
        if dist < best_dist:
            best_dist = dist
            best = cand
    return best if best is not None else core_pos


def _nearest_enemy_gunner(ct: Controller) -> Position | None:
    my_team = ct.get_team()
    pos = ct.get_position()
    best: Position | None = None
    best_dist = float("inf")
    for eid in ct.get_nearby_entities():
        if ct.get_entity_type(eid) != EntityType.GUNNER:
            continue
        if ct.get_team(eid) == my_team:
            continue
        gpos = ct.get_position(eid)
        d = pos.distance_squared(gpos)
        if d < best_dist:
            best_dist = d
            best = gpos
    return best


def _try_fire_tile(ct: Controller, tile: Position) -> bool:
    if ct.get_action_cooldown() != 0:
        return False
    if abs(ct.get_position().x - tile.x) + abs(ct.get_position().y - tile.y) != 1:
        return False
    if not ct.can_fire(tile):
        return False
    ct.fire(tile)
    return True


def _try_heal(ct: Controller) -> bool:
    if ct.get_action_cooldown() != 0:
        return False
    pos = ct.get_position()
    for d in CARDINALS:
        check = pos.add(d)
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


def _phalanx_assigned_site(
    my_index: int, sites: list[Position], missing: list[Position]
) -> Position | None:
    """Prefer this raider's staggered site; else any missing pad."""
    if not missing:
        return None
    if sites:
        preferred = sites[(0 if my_index <= 2 else 1) % len(sites)]
        for m in missing:
            if m.x == preferred.x and m.y == preferred.y:
                return preferred
    return missing[0]


def run_phalanx(
    ct: Controller,
    *,
    my_index: int,
    local_map: dict,
    enemy_core: Position,
    ally_core: Position | None,
) -> Position | None:
    """PHALANX: apron assist → 2 corner pads from turn 20 → soft idle."""
    del local_map  # reserved for parity with run_attack_builder signature
    if ally_core is None:
        return None

    side = enemy_approach_side(ally_core, enemy_core)

    if _phalanx_apron_incomplete(ct, ally_core, side):
        if _phalanx_try_apron(ct, core_pos=ally_core, side=side):
            return None
        return _defence_hold_target(ct, ally_core, side)

    sites = phalanx_sentinel_sites(ct, ally_core, side, enemy_core)
    living = sum(1 for s in sites if _has_allied_sentinel(ct, s))
    ct.write_store(SLOT_SENTINEL_COUNT, min(living, PHALANX_SENTINEL_COUNT))

    missing = [s for s in sites if not _has_allied_sentinel(ct, s)]
    round_n = ct.get_current_round()
    if round_n >= PHALANX_SENTINEL_TURN and living < PHALANX_SENTINEL_COUNT:
        site = _phalanx_assigned_site(my_index, sites, missing)
        if site is not None:
            pos = ct.get_position()
            if abs(pos.x - site.x) + abs(pos.y - site.y) == 1:
                if build_sentinel(ct, site, enemy_core):
                    living = min(living + 1, PHALANX_SENTINEL_COUNT)
                    ct.write_store(SLOT_SENTINEL_COUNT, living)
            return site

    return _defence_hold_target(ct, ally_core, side)


def run_defender(
    ct: Controller, *, core_pos: Position | None, my_index: int
) -> Position | None:
    """Gunner hunt → heal → fire → side apron → linger. None if move already used."""
    side: int | None = None
    if core_pos is not None:
        side = assigned_side(ct, core_pos, my_index)

    gunner = _nearest_enemy_gunner(ct)
    if gunner is not None:
        if _try_fire_tile(ct, gunner):
            if core_pos is None:
                return None
            return _defence_hold_target(ct, core_pos, side)
        return gunner

    if _try_heal(ct):
        if core_pos is None:
            return None
        return _defence_hold_target(ct, core_pos, side)

    if _try_adjacent_fire(ct):
        if core_pos is None:
            return None
        return _defence_hold_target(ct, core_pos, side)

    if core_pos is not None and side is not None and try_make_apron(
        ct, core_pos=core_pos, side=side
    ):
        return None

    if core_pos is None:
        return None
    return _defence_hold_target(ct, core_pos, side)
