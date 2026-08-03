"""Core defence role for earl builders.

Late spawns (indices >= DEFENDER_MIN_INDEX) stay near the ally core: hunt
enemy gunners in vision, heal, fire, then lay a per-side inward apron
(3-deep belts, no barriers).

Even builders may run PHALANX (see ACTIVE_RAID_ORDER in roles.py): while the
core's sticky threat switch is armed (enemy builder or gunner/sentinel in
vision), place wing gunners first then hunt enemy gunners/sentinels;
approach apron only while armed. Soft fill uses depths 1–5; live builder
or turret rush uses the same depths. Then one gunner beside each nearby
ally harvester, clear encroaching enemy harvesters/belts in vision, then
soft-idle.
"""

from __future__ import annotations

from attack import (
    SLOT_SENTINEL_COUNT,
    _pick_enemy_belt_target,
    _try_stand_fire_belt,
    build_gunner,
)
from fcode import Controller, Direction, EntityType, Environment, Position

DEFENDER_MIN_INDEX = 6  # indices 6+

# Sticky: set when any inbound relay finishes (economy / docs).
SLOT_RELAY_COMPLETE = 0
# Core-written: 0 = off; 1+side = phalanx wings/apron armed on that cardinal.
SLOT_PHALANX_BUILDER_THREAT = 1
PHALANX_THREAT_STICKY_TURNS = 20


def team_has_complete_relay(ct: Controller) -> bool:
    """True after any economy builder finishes an inbound relay to the core."""
    return ct.read_store(SLOT_RELAY_COMPLETE) > 0


def mark_relay_complete(ct: Controller) -> None:
    """Sticky team flag: first finished inbound relay."""
    ct.write_store(SLOT_RELAY_COMPLETE, 1)

# Cardinal sides around the core, assignment order N → E → S → W
SIDE_N = 0
SIDE_E = 1
SIDE_S = 2
SIDE_W = 3
SIDE_ORDER = (SIDE_N, SIDE_E, SIDE_S, SIDE_W)

CARDINALS = [Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST]
DIRECTIONS = [d for d in Direction if d != Direction.CENTRE]

APRON_DEPTHS = (1, 2, 3)
# Wing gunner pads: one column/row per flank, one tile per depth.
# Soft fill under sticky threat; rush (live builder/turret) same depths for now.
WING_SOFT_DEPTHS = (1, 2, 3, 4, 5)
WING_RUSH_DEPTHS = (1, 2, 3, 4, 5)
WING_DEPTHS = WING_RUSH_DEPTHS  # full set for site enumeration / apron keys

# Temporary: skip laying / repairing core apron conveyor belts.
LAY_APRON_BELTS = False

# Named phases for the home-defence raid order (priority-gated in run_phalanx).
PHALANX = (
    "place_phalanx_gunners",
    "hunt_turrets",
    "assist_apron",
    "guard_harvesters",
    "clear_economy",
    "heal",
    "soft_idle",
)
PHALANX_GUNNER_TURN = 10
# Chebyshev distance from core footprint — ~20×20 neighbourhood.
HARVESTER_GUARD_CHEB = 20
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


def _gunner_pad_ok(ct: Controller, tile: Position, *, allow_ally_gunner: bool) -> bool:
    """True if tile is a valid wing/guard pad (not ore/wall; empty or ally gunner)."""
    if not ct.is_in_vision(tile):
        return False
    if ct.get_tile_env(tile) != Environment.EMPTY:
        return False
    if ct.is_tile_empty(tile):
        return True
    return allow_ally_gunner and _has_allied_gunner(ct, tile)


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


def side_toward(ally_core: Position, target: Position) -> int:
    """Dominant cardinal from ally 2×2 center toward a tile (e.g. enemy builder)."""
    cx = ally_core.x + 0.5
    cy = ally_core.y + 0.5
    dx = (target.x + 0.5) - cx
    dy = (target.y + 0.5) - cy
    if abs(dx) >= abs(dy):
        return SIDE_E if dx >= 0 else SIDE_W
    return SIDE_S if dy >= 0 else SIDE_N


def clear_phalanx_builder_threat(ct: Controller) -> None:
    ct.write_store(SLOT_PHALANX_BUILDER_THREAT, 0)


def phalanx_builder_threat(ct: Controller) -> int | None:
    """Armed threat side from slot 1, or None if off."""
    val = ct.read_store(SLOT_PHALANX_BUILDER_THREAT)
    if val <= 0:
        return None
    side = val - 1
    if side not in SIDE_ORDER:
        return None
    return side


def _nearest_enemy_builder(ct: Controller) -> Position | None:
    """Nearest enemy builder bot in this unit's vision."""
    my_team = ct.get_team()
    pos = ct.get_position()
    best: Position | None = None
    best_dist = 10**9
    for eid in ct.get_nearby_entities():
        if ct.get_entity_type(eid) != EntityType.BUILDER_BOT:
            continue
        if ct.get_team(eid) == my_team:
            continue
        tpos = ct.get_position(eid)
        dist = pos.distance_squared(tpos)
        if dist < best_dist:
            best_dist = dist
            best = tpos
    return best


def resolve_phalanx_threat_side(
    ct: Controller, ally_core: Position
) -> int | None:
    """Core sticky slot, or same-turn side from a local enemy builder/turret."""
    side = phalanx_builder_threat(ct)
    if side is not None:
        return side
    threat = _nearest_enemy_builder(ct) or _nearest_enemy_turret(ct)
    if threat is None:
        return None
    return side_toward(ally_core, threat)


def update_phalanx_builder_threat(
    ct: Controller,
    *,
    sticky_until: int,
    last_side: int,
) -> tuple[int, int]:
    """Core: arm/refresh sticky threat from enemy builders or turrets; write slot 1.

    Returns updated (sticky_until, side).
    """
    round_n = ct.get_current_round()
    core_pos = ct.get_position()
    my_team = ct.get_team()
    best: Position | None = None
    best_dist = 10**9
    threat_types = (EntityType.BUILDER_BOT,) + _ENEMY_TURRET_TYPES
    for eid in ct.get_nearby_entities():
        if ct.get_entity_type(eid) not in threat_types:
            continue
        if ct.get_team(eid) == my_team:
            continue
        pos = ct.get_position(eid)
        dist = core_pos.distance_squared(pos)
        if dist < best_dist:
            best_dist = dist
            best = pos

    if best is not None:
        last_side = side_toward(core_pos, best)
        sticky_until = round_n + PHALANX_THREAT_STICKY_TURNS

    if round_n <= sticky_until and last_side in SIDE_ORDER:
        ct.write_store(SLOT_PHALANX_BUILDER_THREAT, 1 + last_side)
    else:
        ct.write_store(SLOT_PHALANX_BUILDER_THREAT, 0)

    return sticky_until, last_side


def side_wing_tiles(core_pos: Position, side: int) -> list[Position]:
    """Flank columns/rows beside the belt strip — up to rush depth per wing."""
    tiles: list[Position] = []
    for depth in WING_DEPTHS:
        tiles.extend(side_wing_tiles_at_depth(core_pos, side, depth))
    return tiles


def side_wing_tiles_at_depth(
    core_pos: Position, side: int, depth: int
) -> list[Position]:
    """Two flank pads at one depth on a cardinal side."""
    cx, cy = core_pos.x, core_pos.y
    if side == SIDE_N:
        return [
            Position(cx - 1, cy - depth),
            Position(cx + 2, cy - depth),
        ]
    if side == SIDE_E:
        return [
            Position(cx + 1 + depth, cy - 1),
            Position(cx + 1 + depth, cy + 2),
        ]
    if side == SIDE_S:
        return [
            Position(cx - 1, cy + 1 + depth),
            Position(cx + 2, cy + 1 + depth),
        ]
    if side == SIDE_W:
        return [
            Position(cx - depth, cy - 1),
            Position(cx - depth, cy + 2),
        ]
    return []


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
        if not _gunner_pad_ok(ct, tile, allow_ally_gunner=True):
            continue
        sites.append(tile)
    return sites


def phalanx_threat_wing_targets(
    ct: Controller,
    core_pos: Position,
    side: int,
    local_map: dict,
    *,
    depths: tuple[int, ...] = WING_SOFT_DEPTHS,
) -> tuple[list[Position], int]:
    """Open wing pads on threat side (shallow depths first). Fog OK to path.

    Returns (targets needing a gunner, count of known ally gunners on pads).
    """
    my_team = ct.get_team()
    targets: list[Position] = []
    living = 0
    for depth in depths:
        for tile in side_wing_tiles_at_depth(core_pos, side, depth):
            if not _in_bounds(ct, tile):
                continue
            key = (tile.x, tile.y)
            if ct.is_in_vision(tile):
                if _has_allied_gunner(ct, tile):
                    living += 1
                    continue
                if ct.get_tile_env(tile) != Environment.EMPTY:
                    continue
                if not ct.is_tile_empty(tile):
                    continue
            else:
                ent = local_map.get(key, {}).get("entity")
                if (
                    ent
                    and ent.get("type") == EntityType.GUNNER
                    and ent.get("team") == my_team
                ):
                    living += 1
                    continue
            targets.append(tile)
    return targets, living


def _nearest_wing_pad(pos: Position, pads: list[Position]) -> Position | None:
    if not pads:
        return None
    return min(pads, key=lambda t: pos.distance_squared(t))


def phalanx_all_gunner_sites(
    ct: Controller,
    core_pos: Position,
    approach_side: int,
) -> list[Position]:
    """Wing pads on every in-bounds side; depth rings fill before stacking.

    Within each depth, prefer the enemy approach side, then other available
    sides — so on atoll (east approach) north/south get depth-1 before east
    stacks depth-2+.
    """
    sides = available_sides(ct, core_pos)
    if not sides:
        return []
    ordered = [approach_side] + [s for s in sides if s != approach_side]
    # Keep only sides that are actually available.
    ordered = [s for s in ordered if s in sides]

    sites: list[Position] = []
    seen: set[tuple[int, int]] = set()
    for depth in WING_DEPTHS:
        for side in ordered:
            for tile in side_wing_tiles_at_depth(core_pos, side, depth):
                key = (tile.x, tile.y)
                if key in seen:
                    continue
                if not _in_bounds(ct, tile):
                    continue
                if not _gunner_pad_ok(ct, tile, allow_ally_gunner=True):
                    continue
                seen.add(key)
                sites.append(tile)
    return sites


def phalanx_missing_gunner_sites(
    ct: Controller, sites: list[Position]
) -> list[Position]:
    """Wings in vision that still need a gunner (empty, not ore)."""
    return [s for s in sites if _gunner_pad_ok(ct, s, allow_ally_gunner=False)]


def _apron_incomplete(ct: Controller, core_pos: Position, side: int) -> bool:
    """True if an in-vision apron belt is missing/wrong. Fogged tiles count as OK."""
    if not LAY_APRON_BELTS:
        return False
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
    if not LAY_APRON_BELTS:
        return False
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


def _nearest_enemy_harvester(ct: Controller) -> Position | None:
    """Nearest enemy harvester in vision."""
    return _nearest_enemy_turret(ct, (EntityType.HARVESTER,))


def _run_phalanx_clear(ct: Controller) -> Position | None:
    """Clear encroaching enemy harvesters/belts in vision. None if nothing to do."""
    if _try_stand_fire_belt(ct):
        return ct.get_position()

    harvester = _nearest_enemy_harvester(ct)
    if harvester is not None:
        if _try_fire_tile(ct, harvester):
            return harvester
        return harvester

    belt = _pick_enemy_belt_target(ct)
    if belt is not None:
        return belt
    return None


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
    """Fire orthogonal adjacent enemy buildings (not builder bots)."""
    if ct.get_action_cooldown() != 0:
        return False
    pos = ct.get_position()
    my_team = ct.get_team()

    for d in CARDINALS:
        tile = pos.add(d)
        if not ct.can_fire(tile):
            continue
        bid = ct.get_tile_building_id(tile)
        if bid is None:
            continue
        if ct.get_team(bid) == my_team:
            continue
        ct.fire(tile)
        return True
    return False


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
            if not _gunner_pad_ok(ct, cand, allow_ally_gunner=False):
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
    """PHALANX: threat wings → turrets → apron → guards → clear → heal → idle."""
    if ally_core is None:
        return None

    threat_side = resolve_phalanx_threat_side(ct, ally_core)
    hold_side = (
        threat_side
        if threat_side is not None
        else enemy_approach_side(ally_core, enemy_core)
    )

    round_n = ct.get_current_round()
    wing_living = 0

    # 1. Wing gunners while threat armed — place before hunting their turrets.
    # Soft fill depths 1–5; live builder/turret rush uses the same depths.
    if threat_side is not None:
        rushed = (
            _nearest_enemy_builder(ct) is not None
            or core_under_turret_threat(ct)
        )
        wing_depths = WING_RUSH_DEPTHS if rushed else WING_SOFT_DEPTHS
        targets, wing_living = phalanx_threat_wing_targets(
            ct, ally_core, threat_side, local_map, depths=wing_depths
        )
        if targets:
            site = _nearest_wing_pad(ct.get_position(), targets)
            if site is not None:
                pos = ct.get_position()
                if abs(pos.x - site.x) + abs(pos.y - site.y) == 1:
                    if build_gunner(ct, site, enemy_core):
                        wing_living += 1
                ct.write_store(SLOT_SENTINEL_COUNT, wing_living)
                return site

    # 2. Attack nearby enemy gunners / sentinels (after open wing pads filled).
    turret = _nearest_enemy_turret(ct)
    if turret is not None:
        if _try_fire_tile(ct, turret):
            return _defence_hold_target(ct, ally_core, hold_side)
        return turret

    # 3. Apron while threat armed.
    if threat_side is not None:
        if _phalanx_apron_incomplete(ct, ally_core, threat_side):
            if _phalanx_try_apron(ct, core_pos=ally_core, side=threat_side):
                return None
            return _defence_hold_target(ct, ally_core, threat_side)

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

    # 5. Clear encroaching enemy harvesters / belts in vision.
    clear = _run_phalanx_clear(ct)
    if clear is not None:
        return clear

    # 6. Heal adjacent damaged friendlies (if action free), then soft idle.
    _try_heal(ct)

    return _defence_hold_target(ct, ally_core, hold_side)


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
