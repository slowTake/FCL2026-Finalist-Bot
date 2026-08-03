"""Raider attack role, ammo top-up, and turret fire for earl.

RAIDERS task list (even my_index, enemy core known) — select via
ACTIVE_RAID_ORDER = "raiders" in roles.py (also after phalanx pivots):
  - Top priority: if an uncovered enemy harvester is in vision, place a gunner
    that can hit it via place_gunner_aggressive (on-pad places adjacent;
    cover = can_fire_from, not range).
  - From GUNNER_TURN: repeating RAID_LOOP — first GUNNER_PHASE_LEN turns place
    gunners (place_gunner_aggressive), remainder place siege sentinels
    (place_sentinel_aggressive).
  - Last: belt_raid — stand-fire / path to enemy belts, else push enemy core
    (also the pre-GUNNER_TURN fallthrough when core siege is inactive).
  - After PHALANX_WAVE1_TURN / PHALANX_WAVE2_TURN (300 / 600), the loop epoch
    restarts so each fire-and-forget even wave gets a fresh gunner→sentinel
    window. Siege gunner cap counts only pads near the enemy core (home wings
    do not fill the cap).

Default ACTIVE_RAID_ORDER is "phalanx" (home apron + wing gunners in defence.py).

Core tops ammo to 12 + 2×gunners + 10×sentinels (from local_map) from a
caller-chosen min_turn (default GUNNER_TURN; phalanx uses turn 10).
Turret fire: combat on the current line first (core → gunner → sentinel →
launcher → builder → harvester), then enemy belts on that facing, then rotate
toward hittable core/combat (incl. launcher), then soft rotate to belts /
launcher. Sentinels stay fixed-facing (core → … → harvester, no belts).
"""

from __future__ import annotations

from collections.abc import Callable

from fcode import Controller, Direction, EntityType, Environment, Position

GUNNER_TURN = 30
RAID_LOOP = 40
GUNNER_PHASE_LEN = 20  # first half of each loop
# Mirror main.PHALANX_WAVE1_TURN / PHALANX_WAVE2_TURN — restart siege at each wave.
PHALANX_WAVE1_TURN = 300
PHALANX_WAVE2_TURN = 600
# Back-compat alias for callers / docs that still name the first pivot.
PHALANX_RAID_PIVOT_TURN = PHALANX_WAVE1_TURN
MAX_CORE_GUNNERS = 1000
MAX_SIEGE_SENTINELS = 5
SENTINEL_RANGE_SQ = 32
SENTINEL_PLACE_MIN_SQ = 16  # prefer d≈4+
SENTINEL_PLACE_FALLBACK_MIN_SQ = 9  # allow d≈3 when nothing else fits
GUNNER_PLACE_MIN = 1
GUNNER_PLACE_MAX = 3
# False = place by range (test); True = prefer can_fire_from pads / cover.
GUNNER_REQUIRE_LOS = False
AMMO_FLOOR_BONUS = 12
AMMO_PER_GUNNER = 2
AMMO_PER_SENTINEL = 10
SLOT_SENTINEL_COUNT = 3

_CARDINALS = frozenset(
    {Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST}
)

# Fire priority: lower = better
_FIRE_PRIORITY = {
    EntityType.CORE: 1,
    EntityType.GUNNER: 2,
    EntityType.SENTINEL: 3,
    EntityType.BUILDER_BOT: 4,
    EntityType.HARVESTER: 5,
}

_COMBAT_TYPES = (
    EntityType.GUNNER,
    EntityType.SENTINEL,
    EntityType.BUILDER_BOT,
)

# Rotate onto hittable threats (core first, then turrets / builders / harvesters).
_COMBAT_ROTATE_TYPES = (
    EntityType.CORE,
    EntityType.GUNNER,
    EntityType.SENTINEL,
    EntityType.LAUNCHER,
    EntityType.BUILDER_BOT,
    EntityType.HARVESTER,
)

_COMBAT_FIRE_PRIORITY = {
    EntityType.CORE: 1,
    EntityType.GUNNER: 2,
    EntityType.SENTINEL: 3,
    EntityType.LAUNCHER: 4,
    EntityType.BUILDER_BOT: 5,
    EntityType.HARVESTER: 6,
}

# Post-combat on facing — clear belts before rotating off the line.
_GUNNER_BELT_FIRE_PRIORITY = {
    EntityType.CONVEYOR: 6,
    EntityType.SPLITTER: 6,
}

# After combat/core rotate — turn onto belts or a leftover launcher.
_SOFT_ROTATE_TYPES = (
    EntityType.LAUNCHER,
    EntityType.CONVEYOR,
    EntityType.SPLITTER,
)
_GUNNER_SOFT_ROTATE_PRIORITY = {
    EntityType.LAUNCHER: 4,
    EntityType.CONVEYOR: 7,
    EntityType.SPLITTER: 7,
}

_FACE_DIRS = tuple(d for d in Direction if d != Direction.CENTRE)
GUNNER_RANGE_SQ = 13


def _core_footprint(core_pos: Position) -> list[Position]:
    return [
        core_pos,
        Position(core_pos.x + 1, core_pos.y),
        Position(core_pos.x, core_pos.y + 1),
        Position(core_pos.x + 1, core_pos.y + 1),
    ]


def _chebyshev_to_footprint(pos: Position, footprint: list[Position]) -> int:
    return min(max(abs(pos.x - t.x), abs(pos.y - t.y)) for t in footprint)


def _aim_core_tile(site: Position, enemy_core: Position) -> Position:
    """Nearest 2x2 footprint tile to site — facing points into the known core."""
    return min(
        _core_footprint(enemy_core),
        key=lambda t: site.distance_squared(t),
    )


def facing_8way(tile: Position, target: Position) -> Direction:
    """Full 8-direction facing for sentinels / gunners."""
    d = tile.direction_to(target)
    return d if d != Direction.CENTRE else Direction.NORTH


def facing_toward_core(site: Position, enemy_core: Position) -> Direction:
    return facing_8way(site, _aim_core_tile(site, enemy_core))


def best_gunner_facing(
    ct: Controller, site: Position, enemy_core: Position
) -> Direction | None:
    """Facing that can hit the enemy core from site; cardinals before diagonals."""
    footprint = _core_footprint(enemy_core)
    cardinals = (
        Direction.NORTH,
        Direction.EAST,
        Direction.SOUTH,
        Direction.WEST,
    )
    diagonals = (
        Direction.NORTHEAST,
        Direction.SOUTHEAST,
        Direction.SOUTHWEST,
        Direction.NORTHWEST,
    )
    for facing in cardinals:
        for tile in footprint:
            if ct.can_fire_from(site, facing, EntityType.GUNNER, tile):
                return facing
    for facing in diagonals:
        for tile in footprint:
            if ct.can_fire_from(site, facing, EntityType.GUNNER, tile):
                return facing
    return None


def is_raider(my_index: int, enemy_core: Position | None) -> bool:
    """Even builders leave economy once the enemy core is known (any turn)."""
    return my_index > 0 and my_index % 2 == 0 and enemy_core is not None


def should_attack(
    round_n: int, my_index: int, enemy_core: Position | None
) -> bool:
    """Alias for is_raider (round unused; phase split is inside the runner)."""
    del round_n
    return is_raider(my_index, enemy_core)


def siege_preferred_side(my_index: int) -> int:
    """0=N 1=NE 2=E 3=SE 4=S 5=SW 6=W 7=NW — stagger by raider index."""
    if my_index <= 0:
        return 0
    return ((my_index - 1) // 2) % 8


def siege_cand_side(cand: Position, enemy: Position) -> int:
    """Classify candidate into one of 8 sides around the 2x2 core center."""
    center_x = enemy.x + 0.5
    center_y = enemy.y + 0.5
    dx = cand.x - center_x
    dy = cand.y - center_y
    ax, ay = abs(dx), abs(dy)
    if ay * 2 < ax:
        return 2 if dx > 0 else 6  # E / W
    if ax * 2 < ay:
        return 4 if dy > 0 else 0  # S / N
    if dx > 0 and dy < 0:
        return 1  # NE
    if dx > 0 and dy > 0:
        return 3  # SE
    if dx < 0 and dy > 0:
        return 5  # SW
    return 7  # NW


def count_our_sentinels(local_map: dict, my_team) -> int:
    count = 0
    for info in local_map.values():
        ent = info.get("entity")
        if (
            ent
            and ent.get("type") == EntityType.SENTINEL
            and ent.get("team") == my_team
        ):
            count += 1
    return count


def count_our_gunners(local_map: dict, my_team) -> int:
    count = 0
    for info in local_map.values():
        ent = info.get("entity")
        if (
            ent
            and ent.get("type") == EntityType.GUNNER
            and ent.get("team") == my_team
        ):
            count += 1
    return count


def count_siege_gunners(local_map: dict, my_team, enemy_core: Position) -> int:
    """Ally gunners within Chebyshev GUNNER_PLACE_MAX of the enemy 2×2."""
    footprint = _core_footprint(enemy_core)
    count = 0
    for (x, y), info in local_map.items():
        ent = info.get("entity") if isinstance(info, dict) else None
        if (
            not ent
            or ent.get("type") != EntityType.GUNNER
            or ent.get("team") != my_team
        ):
            continue
        if _chebyshev_to_footprint(Position(x, y), footprint) <= GUNNER_PLACE_MAX:
            count += 1
    return count


def _enemy_belt_at(ct: Controller, pos: Position) -> bool:
    if not ct.is_in_vision(pos):
        return False
    bid = ct.get_tile_building_id(pos)
    if bid is None:
        return False
    etype = ct.get_entity_type(bid)
    if etype not in (EntityType.CONVEYOR, EntityType.SPLITTER):
        return False
    return ct.get_team(bid) != ct.get_team()


def _try_stand_fire_belt(ct: Controller) -> bool:
    """Builders may only fire their own tile — stand on belt, then shoot."""
    pos = ct.get_position()
    if not _enemy_belt_at(ct, pos):
        return False
    if ct.get_action_cooldown() != 0:
        return False
    if not ct.can_fire(pos):
        return False
    ct.fire(pos)
    return True


def _pick_enemy_belt_target(ct: Controller) -> Position | None:
    """Nearest enemy belt in vision (on the way) — no local_map memory pulls."""
    pos = ct.get_position()
    best: Position | None = None
    best_dist = float("inf")

    for tile in ct.get_nearby_tiles():
        if not _enemy_belt_at(ct, tile):
            continue
        dist = pos.distance_squared(tile)
        if dist < best_dist:
            best_dist = dist
            best = tile
    return best


def pick_siege_site(
    ct: Controller,
    *,
    enemy_core: Position,
    ally_core: Position | None,
    my_index: int,
    local_map: dict,
    exclude: Position | None = None,
) -> Position | None:
    """Ring tile in sentinel range; each raider prefers a different side.

    Prefers r² ≥ SENTINEL_PLACE_MIN_SQ; if none, falls back to
    SENTINEL_PLACE_FALLBACK_MIN_SQ (~3 tiles from the footprint).
    """
    site = _pick_siege_site(
        ct,
        enemy_core=enemy_core,
        ally_core=ally_core,
        my_index=my_index,
        local_map=local_map,
        min_dist_sq=SENTINEL_PLACE_MIN_SQ,
        exclude=exclude,
    )
    if site is not None:
        return site
    return _pick_siege_site(
        ct,
        enemy_core=enemy_core,
        ally_core=ally_core,
        my_index=my_index,
        local_map=local_map,
        min_dist_sq=SENTINEL_PLACE_FALLBACK_MIN_SQ,
        exclude=exclude,
    )


def _pick_siege_site(
    ct: Controller,
    *,
    enemy_core: Position,
    ally_core: Position | None,
    my_index: int,
    local_map: dict,
    min_dist_sq: int,
    exclude: Position | None = None,
) -> Position | None:
    cx, cy = enemy_core.x, enemy_core.y
    footprint = _core_footprint(enemy_core)
    best: Position | None = None
    best_score = float("inf")
    my_team = ct.get_team()
    prefer = siege_preferred_side(my_index)
    w, h = ct.get_map_width(), ct.get_map_height()
    ex = exclude.x if exclude is not None else None
    ey = exclude.y if exclude is not None else None

    # Outer rings first (5..3); fallback min allows d=3 tiles through the filter.
    ring: list[Position] = []
    for d in (5, 4, 3):
        for dx in (0, 1):
            ring.append(Position(cx + dx, cy - d))
            ring.append(Position(cx + dx, cy + 1 + d))
        for dy in (0, 1):
            ring.append(Position(cx - d, cy + dy))
            ring.append(Position(cx + 1 + d, cy + dy))
        ring.append(Position(cx - d, cy - d))
        ring.append(Position(cx + 1 + d, cy - d))
        ring.append(Position(cx - d, cy + 1 + d))
        ring.append(Position(cx + 1 + d, cy + 1 + d))

    seen: set[tuple[int, int]] = set()
    for cand in ring:
        key = (cand.x, cand.y)
        if key in seen:
            continue
        seen.add(key)
        if ex is not None and cand.x == ex and cand.y == ey:
            continue
        if not (0 <= cand.x < w and 0 <= cand.y < h):
            continue
        dist = min(cand.distance_squared(t) for t in footprint)
        if dist < min_dist_sq or dist > SENTINEL_RANGE_SQ:
            continue
        ent = local_map.get(key, {}).get("entity")
        if (
            ent
            and ent.get("type") == EntityType.SENTINEL
            and ent.get("team") == my_team
        ):
            continue
        if ct.is_in_vision(cand):
            if not ct.is_tile_empty(cand):
                continue
            if ct.get_tile_env(cand) != Environment.EMPTY:
                continue
        side = siege_cand_side(cand, enemy_core)
        side_pen = (
            0
            if side == prefer
            else (1000 + 50 * min((side - prefer) % 8, (prefer - side) % 8))
        )
        ally_d = cand.distance_squared(ally_core) if ally_core is not None else 0
        # Prefer max range when available; closer only via fallback min.
        score = side_pen + ally_d + (SENTINEL_RANGE_SQ - dist)
        if score < best_score:
            best_score = score
            best = cand
    return best


def pick_gunner_site(
    ct: Controller,
    *,
    enemy_core: Position,
    my_index: int,
    local_map: dict,
    exclude: Position | None = None,
) -> Position | None:
    """Empty tile Chebyshev 1–3 from enemy footprint.

    When ``GUNNER_REQUIRE_LOS``, only pads that can hit the core. Otherwise any
    empty in-range pad (aim with ``facing_8way`` at build time if blocked).
    Core pads do not reject for ally turrets on the aim ray (harvester pads do).
    """
    footprint = _core_footprint(enemy_core)
    fp_keys = {(t.x, t.y) for t in footprint}
    best: Position | None = None
    best_score = float("inf")
    my_team = ct.get_team()
    prefer = siege_preferred_side(my_index)
    pos = ct.get_position()
    w, h = ct.get_map_width(), ct.get_map_height()
    cx, cy = enemy_core.x, enemy_core.y
    ex = exclude.x if exclude is not None else None
    ey = exclude.y if exclude is not None else None

    for x in range(cx - GUNNER_PLACE_MAX - 1, cx + 2 + GUNNER_PLACE_MAX + 1):
        for y in range(cy - GUNNER_PLACE_MAX - 1, cy + 2 + GUNNER_PLACE_MAX + 1):
            if not (0 <= x < w and 0 <= y < h):
                continue
            if ex is not None and x == ex and y == ey:
                continue
            key = (x, y)
            if key in fp_keys:
                continue
            cand = Position(x, y)
            cheb = _chebyshev_to_footprint(cand, footprint)
            if cheb < GUNNER_PLACE_MIN or cheb > GUNNER_PLACE_MAX:
                continue
            ent = local_map.get(key, {}).get("entity")
            if (
                ent
                and ent.get("type") == EntityType.GUNNER
                and ent.get("team") == my_team
            ):
                continue
            in_vis = ct.is_in_vision(cand)
            if in_vis:
                if not ct.is_tile_empty(cand):
                    continue
                if ct.get_tile_env(cand) != Environment.EMPTY:
                    continue
            facing = best_gunner_facing(ct, cand, enemy_core)
            if facing is None:
                if GUNNER_REQUIRE_LOS:
                    continue
                facing = facing_toward_core(cand, enemy_core)
            diag_pen = 0 if facing in _CARDINALS else 500
            if GUNNER_REQUIRE_LOS:
                side = siege_cand_side(cand, enemy_core)
                side_pen = (
                    0
                    if side == prefer
                    else (
                        1000
                        + 50 * min((side - prefer) % 8, (prefer - side) % 8)
                    )
                )
            else:
                side_pen = 0
            fog_pen = 0 if in_vis else 5000
            # Prefer closer to the enemy core; builder distance is tiebreak only.
            score = (
                side_pen
                + fog_pen
                + diag_pen
                + cheb * 100
                + pos.distance_squared(cand)
            )
            if score < best_score:
                best_score = score
                best = cand
    return best


def build_sentinel(
    ct: Controller, site: Position, enemy_core: Position
) -> bool:
    """Build a sentinel at site facing into the enemy core. Caller must be adjacent."""
    pos = ct.get_position()
    if abs(pos.x - site.x) + abs(pos.y - site.y) != 1:
        return False
    if ct.get_action_cooldown() != 0:
        return False
    facing = facing_toward_core(site, enemy_core)
    if not ct.can_build_sentinel(site, facing):
        return False
    ct.build_sentinel(site, facing)
    return True


def build_gunner(
    ct: Controller,
    site: Position,
    enemy_core: Position,
    *,
    aim_target: Position | None = None,
) -> bool:
    """Build a gunner at site. Face core if in range and aiming at core; else aim_target.

    When aim_target is None, aim is the enemy core (siege / wing pads).
    Core-siege pads prefer can_fire_from-verified facing; if blocked and
    ``GUNNER_REQUIRE_LOS`` is off, fall back to facing_8way. Harvester /
    out-of-range aim always uses facing_8way.
    """
    pos = ct.get_position()
    if abs(pos.x - site.x) + abs(pos.y - site.y) != 1:
        return False
    if ct.get_action_cooldown() != 0:
        return False

    footprint = _core_footprint(enemy_core)
    core_in_range = (
        min(site.distance_squared(t) for t in footprint) <= GUNNER_RANGE_SQ
    )
    target = aim_target if aim_target is not None else enemy_core
    aim_is_core = aim_target is None or any(
        aim_target.x == t.x and aim_target.y == t.y for t in footprint
    )

    if core_in_range and aim_is_core:
        facing = best_gunner_facing(ct, site, enemy_core)
        if facing is None:
            if GUNNER_REQUIRE_LOS:
                return False
            facing = facing_8way(site, target)
    else:
        facing = facing_8way(site, target)

    # Ally-turret ray reject is harvester-only; core pads may stack on a line.
    if not aim_is_core and _ally_turret_blocks_aim(ct, site, target):
        return False

    if not ct.can_build_gunner(site, facing):
        return False
    ct.build_gunner(site, facing)
    return True


def sync_sentinel_count(ct: Controller, local_map: dict) -> int:
    """Write living ally sentinel count to store (raid bookkeeping)."""
    n = min(
        count_our_sentinels(local_map, ct.get_team()),
        MAX_SIEGE_SENTINELS,
    )
    ct.write_store(SLOT_SENTINEL_COUNT, n)
    return n


def ammo_target(gunners: int, sentinels: int) -> int:
    """Team ammo floor: 12 + 2×ally gunners + 10×ally sentinels."""
    g = max(0, gunners)
    s = max(0, sentinels)
    return AMMO_FLOOR_BONUS + AMMO_PER_GUNNER * g + AMMO_PER_SENTINEL * s


def top_up_ammo(
    ct: Controller, *, local_map: dict, min_turn: int | None = None
) -> None:
    """Core: top ammo from min_turn (default GUNNER_TURN) onward."""
    start = GUNNER_TURN if min_turn is None else min_turn
    if ct.get_current_round() < start:
        return
    my_team = ct.get_team()
    target = ammo_target(
        count_our_gunners(local_map, my_team),
        count_our_sentinels(local_map, my_team),
    )
    ammo = ct.get_global_ammo()
    if ammo >= target:
        return
    amount = min(target - ammo, ct.get_global_resources())
    if amount > 0 and ct.can_convert_ammo(amount):
        ct.convert_ammo(amount)


def _tile_victim(ct: Controller, tile: Position) -> int | None:
    bot = ct.get_tile_builder_bot_id(tile)
    return bot if bot is not None else ct.get_tile_building_id(tile)


def _try_fire_priority(
    ct: Controller, priority: dict[EntityType, int]
) -> bool:
    """Fire best enemy currently on the attack line. True if fired."""
    my_team = ct.get_team()
    best_tile: Position | None = None
    best_pri = 99

    for tile in ct.get_attackable_tiles():
        if not ct.can_fire(tile):
            continue
        victim = _tile_victim(ct, tile)
        if victim is None or ct.get_team(victim) == my_team:
            continue
        pri = priority.get(ct.get_entity_type(victim))
        if pri is None:
            continue
        if pri < best_pri:
            best_pri = pri
            best_tile = tile
            if pri == 1:
                break

    if best_tile is None:
        return False
    ct.fire(best_tile)
    return True


def _enemy_core_tiles(ct: Controller) -> list[Position]:
    """Enemy core footprint tiles currently in vision."""
    my_team = ct.get_team()
    tiles: list[Position] = []
    for eid in ct.get_nearby_entities():
        if ct.get_entity_type(eid) != EntityType.CORE:
            continue
        if ct.get_team(eid) == my_team:
            continue
        core = ct.get_position(eid)
        for t in _core_footprint(core):
            if ct.is_in_vision(t):
                tiles.append(t)
    return tiles


def _iter_nearby_enemy_ids(ct: Controller):
    """Entities + buildings in vision (sentinels show up as both)."""
    seen: set[int] = set()
    for eid in ct.get_nearby_entities():
        seen.add(eid)
        yield eid
    for eid in ct.get_nearby_buildings():
        if eid not in seen:
            yield eid


def _best_vision_target(
    ct: Controller,
    priority: dict[EntityType, int],
    types: tuple[EntityType, ...],
    *,
    must_hit: bool = False,
) -> Position | None:
    """Best enemy of given types in vision: priority then nearer.

    If must_hit, only consider tiles a gunner could hit after some rotate.
    """
    my_team = ct.get_team()
    pos = ct.get_position()
    best: Position | None = None
    best_pri = 99
    best_dist = 10**9

    for eid in _iter_nearby_enemy_ids(ct):
        if ct.get_team(eid) == my_team:
            continue
        etype = ct.get_entity_type(eid)
        if etype not in types:
            continue
        pri = priority.get(etype)
        if pri is None:
            continue
        tile = ct.get_position(eid)
        if must_hit and _gunner_facing_to_hit(ct, tile) is None:
            continue
        dist = pos.distance_squared(tile)
        if pri < best_pri or (pri == best_pri and dist < best_dist):
            best_pri = pri
            best_dist = dist
            best = tile
    return best


def _gunner_facing_to_hit(ct: Controller, tile: Position) -> Direction | None:
    """Facing that lets this gunner hit tile, prefer compass aim then any of 8."""
    pos = ct.get_position()
    preferred = facing_8way(pos, tile)
    ordered = (preferred,) + tuple(d for d in _FACE_DIRS if d != preferred)
    for facing in ordered:
        if ct.can_fire_from(pos, facing, EntityType.GUNNER, tile):
            return facing
    return None


def _try_rotate_toward(ct: Controller, tile: Position) -> bool:
    """Gunner-only: rotate only to a facing that can hit tile."""
    facing = _gunner_facing_to_hit(ct, tile)
    if facing is None:
        return False
    if facing == ct.get_direction():
        return False
    if not ct.can_rotate(facing):
        return False
    ct.rotate(facing)
    return True


def _try_rotate_toward_core(ct: Controller) -> bool:
    """Rotate toward a vision core tile that would be hittable after rotate."""
    best: Position | None = None
    best_dist = 10**9
    pos = ct.get_position()
    for tile in _enemy_core_tiles(ct):
        if _gunner_facing_to_hit(ct, tile) is None:
            continue
        dist = pos.distance_squared(tile)
        if dist < best_dist:
            best_dist = dist
            best = tile
    return best is not None and _try_rotate_toward(ct, best)


def _try_rotate_toward_combat(ct: Controller) -> bool:
    """Rotate onto nearest hittable core/gunner/sentinel/launcher/builder/harvester."""
    target = _best_vision_target(
        ct, _COMBAT_FIRE_PRIORITY, _COMBAT_ROTATE_TYPES, must_hit=True
    )
    return target is not None and _try_rotate_toward(ct, target)


def _try_rotate_toward_soft(ct: Controller) -> bool:
    """Rotate onto nearest hittable launcher/conveyor/splitter."""
    target = _best_vision_target(
        ct, _GUNNER_SOFT_ROTATE_PRIORITY, _SOFT_ROTATE_TYPES, must_hit=True
    )
    return target is not None and _try_rotate_toward(ct, target)


def run_sentinel(ct: Controller) -> None:
    """Fire on facing line: core > gunner > sentinel > builder > harvester."""
    _try_fire_priority(ct, _FIRE_PRIORITY)


def run_gunner(ct: Controller) -> None:
    """Combat on facing → belts → rotate combat/core → soft rotate belts/launcher."""
    # 1. Kill combat on this facing (incl. launcher).
    if _try_fire_priority(ct, _COMBAT_FIRE_PRIORITY):
        return
    # 2. Clear enemy belts on this facing before abandoning the line.
    if _try_fire_priority(ct, _GUNNER_BELT_FIRE_PRIORITY):
        return
    # 3. Empty line: rotate toward a better target (fires next turn).
    if _try_rotate_toward_combat(ct):
        return
    if _try_rotate_toward_core(ct):
        return
    if _try_rotate_toward_soft(ct):
        return


def _gunner_can_hit_tile(
    ct: Controller, site: Position, tile: Position
) -> bool:
    """True if some gunner facing from site can fire at tile."""
    dist = site.distance_squared(tile)
    if dist < 1 or dist > GUNNER_RANGE_SQ:
        return False
    preferred = facing_8way(site, tile)
    ordered = (preferred,) + tuple(d for d in _FACE_DIRS if d != preferred)
    for facing in ordered:
        if ct.can_fire_from(site, facing, EntityType.GUNNER, tile):
            return True
    return False


_ALLY_TURRET_TYPES = frozenset({EntityType.GUNNER, EntityType.SENTINEL})


def _ally_turret_at(
    ct: Controller,
    tile: Position,
    my_team,
    local_map: dict | None,
) -> bool:
    """True if an ally gunner/sentinel is on tile (vision, else local_map)."""
    if ct.is_in_vision(tile):
        bid = ct.get_tile_building_id(tile)
        if bid is None:
            return False
        if ct.get_team(bid) != my_team:
            return False
        return ct.get_entity_type(bid) in _ALLY_TURRET_TYPES
    if local_map is None:
        return False
    ent = local_map.get((tile.x, tile.y), {}).get("entity")
    if not ent:
        return False
    return (
        ent.get("team") == my_team
        and ent.get("type") in _ALLY_TURRET_TYPES
    )


def _ally_turret_blocks_aim(
    ct: Controller,
    site: Position,
    target: Position,
    local_map: dict | None = None,
) -> bool:
    """True if ally gunner/sentinel is on facing_8way ray before target."""
    if site.x == target.x and site.y == target.y:
        return False
    facing = facing_8way(site, target)
    my_team = ct.get_team()
    w, h = ct.get_map_width(), ct.get_map_height()
    p = site.add(facing)
    # Chebyshev span covering gunner range (r²≤13 → |dx|,|dy| ≤ 3)
    for _ in range(8):
        if not (0 <= p.x < w and 0 <= p.y < h):
            return False
        if p.x == target.x and p.y == target.y:
            return False
        if max(abs(p.x - site.x), abs(p.y - site.y)) > 4:
            return False
        if _ally_turret_at(ct, p, my_team, local_map):
            return True
        p = p.add(facing)
    return False


def _enemy_harvesters_nearest_first(ct: Controller) -> list[Position]:
    """Enemy harvesters in vision, nearest first."""
    my_team = ct.get_team()
    pos = ct.get_position()
    found: list[Position] = []
    for eid in _iter_nearby_enemy_ids(ct):
        if ct.get_team(eid) == my_team:
            continue
        if ct.get_entity_type(eid) != EntityType.HARVESTER:
            continue
        found.append(ct.get_position(eid))
    found.sort(key=lambda t: pos.distance_squared(t))
    return found


def _nearest_enemy_harvester(ct: Controller) -> Position | None:
    """Nearest enemy harvester in vision."""
    found = _enemy_harvesters_nearest_first(ct)
    return found[0] if found else None


def _local_map_ally_gunner_credible(
    ct: Controller, tile: Position, my_team
) -> bool:
    """False when vision shows the local_map gunner ghost is gone / wrong."""
    if not ct.is_in_vision(tile):
        return True
    bid = ct.get_tile_building_id(tile)
    if bid is None:
        return False
    return (
        ct.get_entity_type(bid) == EntityType.GUNNER
        and ct.get_team(bid) == my_team
    )


def _ally_gunner_covers_harvester(
    ct: Controller, local_map: dict, harvester: Position
) -> bool:
    """True if an ally gunner already covers the harvester.

    With ``GUNNER_REQUIRE_LOS``, must be able to hit (`can_fire_from`).
    Otherwise in-range is enough (avoids stacking pads on belt rings).
    """
    my_team = ct.get_team()

    def _covers(gpos: Position) -> bool:
        dist = gpos.distance_squared(harvester)
        if dist < 1 or dist > GUNNER_RANGE_SQ:
            return False
        if not GUNNER_REQUIRE_LOS:
            return True
        return _gunner_can_hit_tile(ct, gpos, harvester)

    for eid in ct.get_nearby_entities():
        if ct.get_entity_type(eid) != EntityType.GUNNER:
            continue
        if ct.get_team(eid) != my_team:
            continue
        if _covers(ct.get_position(eid)):
            return True
    for (x, y), info in local_map.items():
        ent = info.get("entity")
        if not ent:
            continue
        if ent.get("type") != EntityType.GUNNER or ent.get("team") != my_team:
            continue
        gpos = Position(x, y)
        if not _local_map_ally_gunner_credible(ct, gpos, my_team):
            continue
        if _covers(gpos):
            return True
    return False


def pick_harvester_siege_site(
    ct: Controller,
    *,
    harvester: Position,
    my_index: int,
    local_map: dict,
    exclude: Position | None = None,
) -> Position | None:
    """Empty tile in gunner range of harvester; prefer closer + index stagger.

    With ``GUNNER_REQUIRE_LOS``, prefer in-vision pads that can hit
    (`can_fire_from`) and fog walk targets; else fall back to any empty
    in-range in-vision pad. With the flag off, pick by range/score only
    (fog walk targets still allowed).
    """
    best_los: Position | None = None
    best_los_score = float("inf")
    best_fallback: Position | None = None
    best_fallback_score = float("inf")
    my_team = ct.get_team()
    prefer = siege_preferred_side(my_index)
    pos = ct.get_position()
    w, h = ct.get_map_width(), ct.get_map_height()
    # Chebyshev span covering GUNNER_RANGE_SQ (r²≤13 → |dx|,|dy| ≤ 3)
    span = 4
    hx, hy = harvester.x, harvester.y
    ex = exclude.x if exclude is not None else None
    ey = exclude.y if exclude is not None else None

    for x in range(hx - span, hx + span + 1):
        for y in range(hy - span, hy + span + 1):
            if not (0 <= x < w and 0 <= y < h):
                continue
            if x == hx and y == hy:
                continue
            if ex is not None and x == ex and y == ey:
                continue
            cand = Position(x, y)
            dist = cand.distance_squared(harvester)
            if dist < 1 or dist > GUNNER_RANGE_SQ:
                continue
            key = (x, y)
            ent = local_map.get(key, {}).get("entity")
            if (
                ent
                and ent.get("type") == EntityType.GUNNER
                and ent.get("team") == my_team
            ):
                continue
            in_vis = ct.is_in_vision(cand)
            if in_vis:
                if not ct.is_tile_empty(cand):
                    continue
                if ct.get_tile_env(cand) != Environment.EMPTY:
                    continue
            if _ally_turret_blocks_aim(ct, cand, harvester, local_map):
                continue
            facing = facing_8way(cand, harvester)
            diag_pen = 0 if facing in _CARDINALS else 500
            side = siege_cand_side(cand, harvester)
            side_pen = (
                0
                if side == prefer
                else (1000 + 50 * min((side - prefer) % 8, (prefer - side) % 8))
            )
            # Prefer closer to harvester; builder distance is tiebreak.
            score = side_pen + diag_pen + dist * 100 + pos.distance_squared(cand)
            if not GUNNER_REQUIRE_LOS:
                if score < best_fallback_score:
                    best_fallback_score = score
                    best_fallback = cand
                continue
            los_ok = (not in_vis) or _gunner_can_hit_tile(ct, cand, harvester)
            if los_ok and score < best_los_score:
                best_los_score = score
                best_los = cand
            if in_vis and score < best_fallback_score:
                best_fallback_score = score
                best_fallback = cand
    return best_los if best_los is not None else best_fallback


def place_gunner_aggressive(
    ct: Controller,
    *,
    site: Position,
    enemy_core: Position,
    aim_target: Position | None = None,
    pick_alt: Callable[[Position], Position | None] | None = None,
) -> Position:
    """Path/place a gunner without softlocking on the pad.

    If standing on ``site``, place on an adjacent empty pad (LOS preference
    gated by ``GUNNER_REQUIRE_LOS``). If adjacent to ``site``, build there.
    Always returns a move/hold target.
    """
    pos = ct.get_position()
    if pos.x == site.x and pos.y == site.y:
        if _try_build_gunner_on_adjacent_pad(
            ct, enemy_core=enemy_core, aim_target=aim_target
        ):
            return pos
        if pick_alt is not None:
            alt = pick_alt(pos)
            if alt is not None:
                return alt
        return pos
    if abs(pos.x - site.x) + abs(pos.y - site.y) == 1:
        build_gunner(ct, site, enemy_core, aim_target=aim_target)
        return pos
    return site


def place_sentinel_aggressive(
    ct: Controller,
    *,
    site: Position,
    enemy_core: Position,
    pick_alt: Callable[[Position], Position | None] | None = None,
) -> Position:
    """Path/place a sentinel without softlocking on the pad.

    Same stance as ``place_gunner_aggressive``: on-pad places on an adjacent
    valid ring tile; adjacent builds; else path to ``site``.
    """
    pos = ct.get_position()
    if pos.x == site.x and pos.y == site.y:
        if _try_build_sentinel_on_adjacent_pad(ct, enemy_core=enemy_core):
            return pos
        if pick_alt is not None:
            alt = pick_alt(pos)
            if alt is not None:
                return alt
        return pos
    if abs(pos.x - site.x) + abs(pos.y - site.y) == 1:
        if build_sentinel(ct, site, enemy_core):
            n = min(ct.read_store(SLOT_SENTINEL_COUNT) + 1, MAX_SIEGE_SENTINELS)
            ct.write_store(SLOT_SENTINEL_COUNT, n)
        return pos
    return site


def _try_build_gunner_on_adjacent_pad(
    ct: Controller,
    *,
    enemy_core: Position,
    aim_target: Position | None,
) -> bool:
    """From current tile, try build_gunner on an adjacent valid offensive pad."""
    pos = ct.get_position()
    w, h = ct.get_map_width(), ct.get_map_height()
    footprint = _core_footprint(enemy_core)
    prefer: list[Position] = []
    fallback: list[Position] = []
    for d in (
        Direction.NORTH,
        Direction.EAST,
        Direction.SOUTH,
        Direction.WEST,
    ):
        n = pos.add(d)
        if not (0 <= n.x < w and 0 <= n.y < h):
            continue
        if not ct.is_in_vision(n):
            continue
        if not ct.is_tile_empty(n):
            continue
        if ct.get_tile_env(n) != Environment.EMPTY:
            continue
        if aim_target is not None:
            dist = n.distance_squared(aim_target)
            if dist < 1 or dist > GUNNER_RANGE_SQ:
                continue
            if _ally_turret_blocks_aim(ct, n, aim_target):
                continue
            if GUNNER_REQUIRE_LOS and _gunner_can_hit_tile(ct, n, aim_target):
                prefer.append(n)
            else:
                fallback.append(n)
        else:
            cheb = _chebyshev_to_footprint(n, footprint)
            if cheb < GUNNER_PLACE_MIN or cheb > GUNNER_PLACE_MAX:
                continue
            if best_gunner_facing(ct, n, enemy_core) is not None:
                prefer.append(n)
            elif not GUNNER_REQUIRE_LOS:
                fallback.append(n)
    for pad in prefer + fallback:
        if build_gunner(ct, pad, enemy_core, aim_target=aim_target):
            return True
    return False


def _try_build_sentinel_on_adjacent_pad(
    ct: Controller, *, enemy_core: Position
) -> bool:
    """From current tile, try build_sentinel on an adjacent valid siege pad."""
    pos = ct.get_position()
    w, h = ct.get_map_width(), ct.get_map_height()
    footprint = _core_footprint(enemy_core)
    pads: list[Position] = []
    for d in (
        Direction.NORTH,
        Direction.EAST,
        Direction.SOUTH,
        Direction.WEST,
    ):
        n = pos.add(d)
        if not (0 <= n.x < w and 0 <= n.y < h):
            continue
        if not ct.is_in_vision(n):
            continue
        if not ct.is_tile_empty(n):
            continue
        if ct.get_tile_env(n) != Environment.EMPTY:
            continue
        dist = min(n.distance_squared(t) for t in footprint)
        if dist < SENTINEL_PLACE_FALLBACK_MIN_SQ or dist > SENTINEL_RANGE_SQ:
            continue
        pads.append(n)
    for pad in pads:
        if build_sentinel(ct, pad, enemy_core):
            n = min(ct.read_store(SLOT_SENTINEL_COUNT) + 1, MAX_SIEGE_SENTINELS)
            ct.write_store(SLOT_SENTINEL_COUNT, n)
            return True
    return False


def _try_melee_harvester(ct: Controller, harvester: Position) -> bool:
    """Fire or destroy an orthogonally adjacent enemy harvester. True if acted."""
    pos = ct.get_position()
    if abs(pos.x - harvester.x) + abs(pos.y - harvester.y) != 1:
        return False
    if ct.get_action_cooldown() != 0:
        return False
    if ct.can_fire(harvester):
        ct.fire(harvester)
        return True
    if ct.can_destroy(harvester):
        ct.destroy(harvester)
        return True
    return False


def _run_siege_enemy_harvester(
    ct: Controller,
    *,
    my_index: int,
    local_map: dict,
    enemy_core: Position,
) -> Position | None:
    """If an uncovered enemy harvester is in vision, place a gunner or melee it.

    Gunner pads win over adjacent melee — melee only when no placeable pad.
    """
    for harvester in _enemy_harvesters_nearest_first(ct):
        if _ally_gunner_covers_harvester(ct, local_map, harvester):
            continue
        pos = ct.get_position()
        site = pick_harvester_siege_site(
            ct,
            harvester=harvester,
            my_index=my_index,
            local_map=local_map,
        )
        if site is not None:
            return place_gunner_aggressive(
                ct,
                site=site,
                enemy_core=enemy_core,
                aim_target=harvester,
                pick_alt=lambda ex: pick_harvester_siege_site(
                    ct,
                    harvester=harvester,
                    my_index=my_index,
                    local_map=local_map,
                    exclude=ex,
                ),
            )
        if abs(pos.x - harvester.x) + abs(pos.y - harvester.y) == 1:
            _try_melee_harvester(ct, harvester)
            return pos
        return harvester
    return None


def _run_belt_raid(
    ct: Controller,
    *,
    enemy_core: Position,
) -> Position | None:
    if _try_stand_fire_belt(ct):
        return ct.get_position()
    belt = _pick_enemy_belt_target(ct)
    if belt is not None:
        return belt
    return enemy_core


def _run_gunner_phase(
    ct: Controller,
    *,
    my_index: int,
    local_map: dict,
    enemy_core: Position,
) -> Position | None:
    if _try_stand_fire_belt(ct):
        return ct.get_position()

    if count_siege_gunners(local_map, ct.get_team(), enemy_core) >= MAX_CORE_GUNNERS:
        return None

    site = pick_gunner_site(
        ct,
        enemy_core=enemy_core,
        my_index=my_index,
        local_map=local_map,
    )
    if site is None:
        return enemy_core

    return place_gunner_aggressive(
        ct,
        site=site,
        enemy_core=enemy_core,
        aim_target=None,
        pick_alt=lambda ex: pick_gunner_site(
            ct,
            enemy_core=enemy_core,
            my_index=my_index,
            local_map=local_map,
            exclude=ex,
        ),
    )


def _run_sentinel_phase(
    ct: Controller,
    *,
    my_index: int,
    local_map: dict,
    enemy_core: Position,
    ally_core: Position | None,
) -> Position | None:
    sync_sentinel_count(ct, local_map)

    if count_our_sentinels(local_map, ct.get_team()) >= MAX_SIEGE_SENTINELS:
        return None

    site = pick_siege_site(
        ct,
        enemy_core=enemy_core,
        ally_core=ally_core,
        my_index=my_index,
        local_map=local_map,
    )
    if site is None:
        return None

    return place_sentinel_aggressive(
        ct,
        site=site,
        enemy_core=enemy_core,
        pick_alt=lambda ex: pick_siege_site(
            ct,
            enemy_core=enemy_core,
            ally_core=ally_core,
            my_index=my_index,
            local_map=local_map,
            exclude=ex,
        ),
    )


# Named phases for the offensive raid order (priority-gated in run_raiders_tasklist).
RAIDERS = (
    "siege_enemy_harvester",
    "place_gunners",
    "place_siege_sentinels",
    "belt_raid",
)


def _run_raid_core_siege(
    ct: Controller,
    *,
    my_index: int,
    local_map: dict,
    enemy_core: Position,
    ally_core: Position | None,
) -> Position | None:
    """Gunner/sentinel core siege on the raider RAID_LOOP clock.

    After each phalanx even-wave pivot (300 / 600), the epoch restarts so
    walkers get a full gunner window then sentinels. Returns a move/build
    site, or None when idle at cap / no sentinel site (caller falls through
    to belt_raid / push core).
    """
    round_n = ct.get_current_round()
    if round_n >= PHALANX_WAVE2_TURN:
        epoch = PHALANX_WAVE2_TURN
    elif round_n >= PHALANX_WAVE1_TURN:
        epoch = PHALANX_WAVE1_TURN
    else:
        epoch = GUNNER_TURN
    cycle_t = (round_n - epoch) % RAID_LOOP
    if cycle_t < GUNNER_PHASE_LEN:
        return _run_gunner_phase(
            ct,
            my_index=my_index,
            local_map=local_map,
            enemy_core=enemy_core,
        )
    return _run_sentinel_phase(
        ct,
        my_index=my_index,
        local_map=local_map,
        enemy_core=enemy_core,
        ally_core=ally_core,
    )


def run_raiders_tasklist(
    ct: Controller,
    *,
    my_index: int,
    local_map: dict,
    enemy_core: Position,
    ally_core: Position | None,
) -> Position | None:
    """RAIDERS: harvester gunner → core siege loop → belt raid / push core."""
    site = _run_siege_enemy_harvester(
        ct,
        my_index=my_index,
        local_map=local_map,
        enemy_core=enemy_core,
    )
    if site is not None:
        return site

    round_n = ct.get_current_round()
    if round_n >= GUNNER_TURN:
        site = _run_raid_core_siege(
            ct,
            my_index=my_index,
            local_map=local_map,
            enemy_core=enemy_core,
            ally_core=ally_core,
        )
        if site is not None:
            return site
    return _run_belt_raid(ct, enemy_core=enemy_core)


def run_attack_builder(
    ct: Controller,
    *,
    my_index: int,
    local_map: dict,
    enemy_core: Position,
    ally_core: Position | None,
) -> Position | None:
    """Thin wrapper over the RAIDERS task list."""
    return run_raiders_tasklist(
        ct,
        my_index=my_index,
        local_map=local_map,
        enemy_core=enemy_core,
        ally_core=ally_core,
    )
