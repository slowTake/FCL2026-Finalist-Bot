"""Core defence role for earl builders.

Late spawns (indices >= DEFENDER_MIN_INDEX) stay near the ally core: hunt
enemy gunners in vision, heal, fire, then lay a per-side inward apron
(3-deep belts, no barriers).

Even builders may run PHALANX (see ACTIVE_RAID_ORDER in main): hunt enemy
gunners/sentinels, fill flank wing gunners from round 20, then apron, then
one gunner beside each nearby ally harvester, then soft-idle.
"""

from __future__ import annotations

from attack import (
    SLOT_SENTINEL_COUNT,
    build_gunner,
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

APRON_DEPTHS = (1, 2, 3)
# Wing gunner pads: one column/row per flank, one tile per depth (4 each side).
WING_DEPTHS = (1, 2, 3, 4)

# Named phases for the home-defence raid order (priority-gated in run_phalanx).
PHALANX = (
    "hunt_turrets",
    "place_phalanx_gunners",
    "assist_apron",
    "guard_harvesters",
    "soft_idle",
)
PHALANX_GUNNER_TURN = 20
# Chebyshev distance from core footprint — ~10×10 neighbourhood.
HARVESTER_GUARD_CHEB = 10
_ENEMY_TURRET_TYPES = (EntityType.GUNNER, EntityType.SENTINEL)


def is_defender(my_index: int) -> bool:
    return my_index >= DEFENDER_MIN_INDEX


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


def _has_allied_gunner(ct: Controller, pos: Position) -> bool:
    if not ct.is_in_vision(pos):
        return False
    bid = ct.get_tile_building_id(pos)
    if bid is None:
        return False
    return (
        ct.get_entity_type(bid) == EntityType.GUNNER
        and ct.get_team(bid) == ct.get_team()
    )


def side_conveyor_specs(
    core_pos: Position, side: int
) -> list[tuple[Position, Direction]]:
    """2-wide × 3-deep inward strip for one cardinal side."""
    cx, cy = core_pos.x, core_pos.y
    tiles: list[tuple[Position, Direction]] = []
    for depth in APRON_DEPTHS:
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


def enemy_approach_side(ally_core: Position, enemy_core: Position) -> int:
    """Dominant cardinal from ally 2×2 center toward the enemy core."""
    cx = ally_core.x + 0.5
    cy = ally_core.y + 0.5
    dx = (enemy_core.x + 0.5) - cx
    dy = (enemy_core.y + 0.5) - cy
    if abs(dx) >= abs(dy):
        return SIDE_E if dx >= 0 else SIDE_W
    return SIDE_S if dy >= 0 else SIDE_N


def side_wing_tiles(core_pos: Position, side: int) -> list[Position]:
    """Flank columns/rows beside the belt strip — 4 pads per wing."""
    cx, cy = core_pos.x, core_pos.y
    tiles: list[Position] = []
    for depth in WING_DEPTHS:
        if side == SIDE_N:
            tiles.append(Position(cx - 1, cy - depth))
            tiles.append(Position(cx + 2, cy - depth))
        elif side == SIDE_E:
            tiles.append(Position(cx + 1 + depth, cy - 1))
            tiles.append(Position(cx + 1 + depth, cy + 2))
        elif side == SIDE_S:
            tiles.append(Position(cx - 1, cy + 1 + depth))
            tiles.append(Position(cx + 2, cy + 1 + depth))
        elif side == SIDE_W:
            tiles.append(Position(cx - depth, cy - 1))
            tiles.append(Position(cx - depth, cy + 2))
    return tiles


def phalanx_gunner_sites(
    ct: Controller,
    core_pos: Position,
    side: int,
) -> list[Position]:
    """In-vision approach-side wing tiles that are empty or hold an allied gunner."""
    sites: list[Position] = []
    for tile in side_wing_tiles(core_pos, side):
        if not _in_bounds(ct, tile):
            continue
        if not ct.is_in_vision(tile):
            continue
        if not ct.is_tile_empty(tile) and not _has_allied_gunner(ct, tile):
            continue
        sites.append(tile)
    return sites


def phalanx_missing_gunner_sites(
    ct: Controller, sites: list[Position]
) -> list[Position]:
    """Wings in vision that still need a gunner (empty)."""
    return [s for s in sites if ct.is_tile_empty(s)]


def _apron_incomplete(ct: Controller, core_pos: Position, side: int) -> bool:
    """True if an in-vision apron belt is missing/wrong. Fogged tiles count as OK."""
    for tile, facing in side_conveyor_specs(core_pos, side):
        if not _in_bounds(ct, tile):
            continue
        if not ct.is_in_vision(tile):
            continue
        if not _belt_faces(ct, tile, facing):
            return True
    return False


def _phalanx_apron_incomplete(
    ct: Controller, core_pos: Position, side: int
) -> bool:
    """Approach-side belt apron incomplete."""
    return _apron_incomplete(ct, core_pos, side)


def _phalanx_try_apron(
    ct: Controller, *, core_pos: Position, side: int
) -> bool:
    """Lay approach-side apron belts."""
    return try_make_apron(ct, core_pos=core_pos, side=side)


def try_make_apron(
    ct: Controller, *, core_pos: Position, side: int
) -> bool:
    """Lay this side's apron conveyors. True if built or moved."""
    pending: list[Position] = []
    conveyor_specs = apron_conveyor_specs(core_pos, side)

    for tile, facing in conveyor_specs:
        if not _in_bounds(ct, tile):
            continue
        if not ct.is_in_vision(tile):
            continue
        if _belt_faces(ct, tile, facing):
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
    return _nearest_enemy_turret(ct, (EntityType.GUNNER,))


def _nearest_enemy_turret(
    ct: Controller,
    types: tuple[EntityType, ...] = _ENEMY_TURRET_TYPES,
) -> Position | None:
    """Nearest enemy gunner/sentinel in vision."""
    my_team = ct.get_team()
    pos = ct.get_position()
    best: Position | None = None
    best_dist = float("inf")
    for eid in ct.get_nearby_entities():
        if ct.get_entity_type(eid) not in types:
            continue
        if ct.get_team(eid) == my_team:
            continue
        tpos = ct.get_position(eid)
        d = pos.distance_squared(tpos)
        if d < best_dist:
            best_dist = d
            best = tpos
    return best


def core_under_turret_threat(ct: Controller) -> bool:
    """True if an enemy gunner or sentinel is within this unit's vision (core)."""
    return _nearest_enemy_turret(ct) is not None


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
    my_index: int, missing: list[Position]
) -> Position | None:
    """Stagger even raiders across open wing pads."""
    if not missing:
        return None
    return missing[((max(my_index, 1) // 2) - 1) % len(missing)]


def _core_footprint(core_pos: Position) -> list[Position]:
    return [
        core_pos,
        Position(core_pos.x + 1, core_pos.y),
        Position(core_pos.x, core_pos.y + 1),
        Position(core_pos.x + 1, core_pos.y + 1),
    ]


def _chebyshev_to_core(pos: Position, core_pos: Position) -> int:
    return min(
        max(abs(pos.x - t.x), abs(pos.y - t.y)) for t in _core_footprint(core_pos)
    )


def _apron_tile_keys(core_pos: Position) -> set[tuple[int, int]]:
    keys: set[tuple[int, int]] = set()
    for s in SIDE_ORDER:
        for tile, _ in side_conveyor_specs(core_pos, s):
            keys.add((tile.x, tile.y))
        for tile in side_wing_tiles(core_pos, s):
            keys.add((tile.x, tile.y))
    for t in _core_footprint(core_pos):
        keys.add((t.x, t.y))
    return keys


def _harvester_has_gunner(ct: Controller, harvester: Position) -> bool:
    """True if a cardinal neighbour already holds an allied gunner."""
    for d in CARDINALS:
        n = harvester.add(d)
        if not _in_bounds(ct, n):
            continue
        if ct.is_in_vision(n) and _has_allied_gunner(ct, n):
            return True
    return False


def _allied_harvesters_near_core(
    ct: Controller,
    core_pos: Position,
    local_map: dict,
) -> list[Position]:
    """Ally harvesters within HARVESTER_GUARD_CHEB of the core footprint."""
    my_team = ct.get_team()
    found: dict[tuple[int, int], Position] = {}

    for eid in ct.get_nearby_entities():
        if ct.get_entity_type(eid) != EntityType.HARVESTER:
            continue
        if ct.get_team(eid) != my_team:
            continue
        pos = ct.get_position(eid)
        if _chebyshev_to_core(pos, core_pos) > HARVESTER_GUARD_CHEB:
            continue
        found[(pos.x, pos.y)] = pos

    for (x, y), cell in local_map.items():
        ent = cell.get("entity") if isinstance(cell, dict) else None
        if not ent or ent.get("type") != EntityType.HARVESTER:
            continue
        if ent.get("team") != my_team:
            continue
        pos = Position(x, y)
        if _chebyshev_to_core(pos, core_pos) > HARVESTER_GUARD_CHEB:
            continue
        found[(x, y)] = pos

    return sorted(found.values(), key=lambda p: (p.x, p.y))


def _pick_harvester_gunner_site(
    ct: Controller,
    *,
    harvester: Position,
    core_pos: Position,
    enemy_core: Position,
) -> Position | None:
    """Cardinal pad beside harvester; prefer empty, off apron, toward enemy."""
    forbidden = _apron_tile_keys(core_pos)
    best: Position | None = None
    best_score: tuple[int, int] | None = None

    for d in CARDINALS:
        cand = harvester.add(d)
        if not _in_bounds(ct, cand):
            continue
        key = (cand.x, cand.y)
        if key in forbidden:
            continue
        if ct.is_in_vision(cand):
            if _has_allied_gunner(ct, cand):
                return cand
            if not ct.is_tile_empty(cand):
                continue
        toward = cand.distance_squared(enemy_core)
        score = (toward, abs(cand.x - harvester.x) + abs(cand.y - harvester.y))
        if best_score is None or score < best_score:
            best_score = score
            best = cand
    return best


def phalanx_harvester_gunner_sites(
    ct: Controller,
    *,
    core_pos: Position,
    enemy_core: Position,
    local_map: dict,
) -> list[Position]:
    """One gunner pad per unguarded ally harvester near the core."""
    sites: list[Position] = []
    used: set[tuple[int, int]] = set()
    for harv in _allied_harvesters_near_core(ct, core_pos, local_map):
        if _harvester_has_gunner(ct, harv):
            continue
        site = _pick_harvester_gunner_site(
            ct,
            harvester=harv,
            core_pos=core_pos,
            enemy_core=enemy_core,
        )
        if site is None:
            continue
        key = (site.x, site.y)
        if key in used:
            continue
        if ct.is_in_vision(site) and _has_allied_gunner(ct, site):
            continue
        used.add(key)
        sites.append(site)
    return sites


def run_phalanx(
    ct: Controller,
    *,
    my_index: int,
    local_map: dict,
    enemy_core: Position,
    ally_core: Position | None,
) -> Position | None:
    """PHALANX: hunt turrets → wing gunners → apron → harvester guards → idle."""
    if ally_core is None:
        return None

    side = enemy_approach_side(ally_core, enemy_core)

    # 1. Attack nearby enemy gunners / sentinels first.
    threat = _nearest_enemy_turret(ct)
    if threat is not None:
        if _try_fire_tile(ct, threat):
            return _defence_hold_target(ct, ally_core, side)
        return threat

    round_n = ct.get_current_round()

    # 2. Wing gunners before apron.
    sites = phalanx_gunner_sites(ct, ally_core, side)
    wing_living = sum(1 for s in sites if _has_allied_gunner(ct, s))
    missing = phalanx_missing_gunner_sites(ct, sites)
    if round_n >= PHALANX_GUNNER_TURN and missing:
        site = _phalanx_assigned_site(my_index, missing)
        if site is not None:
            pos = ct.get_position()
            if abs(pos.x - site.x) + abs(pos.y - site.y) == 1:
                if build_gunner(ct, site, enemy_core):
                    wing_living += 1
            ct.write_store(SLOT_SENTINEL_COUNT, wing_living)
            return site

    # 3. Approach-side apron belts.
    if _phalanx_apron_incomplete(ct, ally_core, side):
        if _phalanx_try_apron(ct, core_pos=ally_core, side=side):
            return None
        return _defence_hold_target(ct, ally_core, side)

    harvesters = _allied_harvesters_near_core(ct, ally_core, local_map)
    guard_living = sum(1 for h in harvesters if _harvester_has_gunner(ct, h))
    ct.write_store(SLOT_SENTINEL_COUNT, wing_living + guard_living)

    # 4. One gunner beside each nearby ally harvester.
    if round_n >= PHALANX_GUNNER_TURN:
        guard_sites = phalanx_harvester_gunner_sites(
            ct,
            core_pos=ally_core,
            enemy_core=enemy_core,
            local_map=local_map,
        )
        if guard_sites:
            site = _phalanx_assigned_site(my_index, guard_sites)
            if site is not None:
                pos = ct.get_position()
                if abs(pos.x - site.x) + abs(pos.y - site.y) == 1:
                    build_gunner(ct, site, enemy_core)
                return site

    return _defence_hold_target(ct, ally_core, side)


def run_defender(
    ct: Controller, *, core_pos: Position | None, my_index: int
) -> Position | None:
    """Gunner/sentinel hunt → heal → fire → side apron → linger."""
    side: int | None = None
    if core_pos is not None:
        side = assigned_side(ct, core_pos, my_index)

    gunner = _nearest_enemy_turret(ct)
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
