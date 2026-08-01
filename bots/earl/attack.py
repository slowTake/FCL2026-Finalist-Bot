"""Raider attack role, ammo top-up, and turret fire for earl.

Raiders (even my_index, enemy core known):
  - Before GUNNER_TURN: push the enemy core; stand-fire enemy belts on the way.
  - From GUNNER_TURN: repeating RAID_LOOP — first GUNNER_PHASE_LEN turns place
    gunners, remainder place siege sentinels, then loop.

Core tops ammo to 5 + 20 × max(1, n) from GUNNER_TURN.
Gunners fire core → builders → harvesters → belts; sentinels omit belts.
"""

from __future__ import annotations

from fcode import Controller, Direction, EntityType, Environment, Position

GUNNER_TURN = 30
RAID_LOOP = 40
GUNNER_PHASE_LEN = 20  # first half of each loop
MAX_CORE_GUNNERS = 3
MAX_SIEGE_SENTINELS = 5
SENTINEL_RANGE_SQ = 32
SENTINEL_PLACE_MIN_SQ = 16  # d=4 → 16
GUNNER_PLACE_MIN = 1
GUNNER_PLACE_MAX = 3
AMMO_PER_SENTINEL = 20
AMMO_FLOOR_BONUS = 5
SLOT_SENTINEL_COUNT = 3

_CARDINALS = frozenset(
    {Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST}
)

# Fire priority: lower = better
_FIRE_PRIORITY = {
    EntityType.CORE: 1,
    EntityType.BUILDER_BOT: 2,
    EntityType.HARVESTER: 3,
}

_GUNNER_FIRE_PRIORITY = {
    **_FIRE_PRIORITY,
    EntityType.CONVEYOR: 4,
    EntityType.SPLITTER: 4,
}


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
) -> Position | None:
    """Ring tile in sentinel range; each raider prefers a different side."""
    cx, cy = enemy_core.x, enemy_core.y
    footprint = _core_footprint(enemy_core)
    best: Position | None = None
    best_score = float("inf")
    my_team = ct.get_team()
    prefer = siege_preferred_side(my_index)
    w, h = ct.get_map_width(), ct.get_map_height()

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
        if not (0 <= cand.x < w and 0 <= cand.y < h):
            continue
        dist = min(cand.distance_squared(t) for t in footprint)
        if dist < SENTINEL_PLACE_MIN_SQ or dist > SENTINEL_RANGE_SQ:
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
) -> Position | None:
    """Empty tile Chebyshev 1–3 from enemy footprint; prefer cardinal aim."""
    footprint = _core_footprint(enemy_core)
    fp_keys = {(t.x, t.y) for t in footprint}
    best: Position | None = None
    best_score = float("inf")
    my_team = ct.get_team()
    prefer = siege_preferred_side(my_index)
    pos = ct.get_position()
    w, h = ct.get_map_width(), ct.get_map_height()
    cx, cy = enemy_core.x, enemy_core.y

    for x in range(cx - GUNNER_PLACE_MAX - 1, cx + 2 + GUNNER_PLACE_MAX + 1):
        for y in range(cy - GUNNER_PLACE_MAX - 1, cy + 2 + GUNNER_PLACE_MAX + 1):
            if not (0 <= x < w and 0 <= y < h):
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
            if ct.is_in_vision(cand):
                if not ct.is_tile_empty(cand):
                    continue
                if ct.get_tile_env(cand) != Environment.EMPTY:
                    continue
            facing = facing_toward_core(cand, enemy_core)
            diag_pen = 0 if facing in _CARDINALS else 500
            side = siege_cand_side(cand, enemy_core)
            side_pen = (
                0
                if side == prefer
                else (1000 + 50 * min((side - prefer) % 8, (prefer - side) % 8))
            )
            score = side_pen + diag_pen + pos.distance_squared(cand) + cheb
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
    ct: Controller, site: Position, enemy_core: Position
) -> bool:
    """Build a gunner facing into the enemy core. No wrong-facing fallback."""
    pos = ct.get_position()
    if abs(pos.x - site.x) + abs(pos.y - site.y) != 1:
        return False
    if ct.get_action_cooldown() != 0:
        return False
    facing = facing_toward_core(site, enemy_core)
    if not ct.can_build_gunner(site, facing):
        return False
    ct.build_gunner(site, facing)
    return True


def sync_sentinel_count(ct: Controller, local_map: dict) -> int:
    """Write living ally sentinel count to store for core ammo top-up."""
    n = min(
        count_our_sentinels(local_map, ct.get_team()),
        MAX_SIEGE_SENTINELS,
    )
    ct.write_store(SLOT_SENTINEL_COUNT, n)
    return n


def ammo_target(n: int) -> int:
    """Team ammo floor: 5 + 20 × max(1, living sentinels)."""
    living = max(0, min(n, MAX_SIEGE_SENTINELS))
    return AMMO_FLOOR_BONUS + AMMO_PER_SENTINEL * max(1, living)


def top_up_ammo(ct: Controller) -> None:
    """Core: top ammo from GUNNER_TURN onward."""
    if ct.get_current_round() < GUNNER_TURN:
        return
    n = ct.read_store(SLOT_SENTINEL_COUNT)
    target = ammo_target(n)
    ammo = ct.get_global_ammo()
    if ammo >= target:
        return
    amount = min(target - ammo, ct.get_global_resources())
    if amount > 0 and ct.can_convert_ammo(amount):
        ct.convert_ammo(amount)


def _fire_best(
    ct: Controller, priority: dict[EntityType, int]
) -> None:
    my_team = ct.get_team()
    best_tile: Position | None = None
    best_pri = 99

    for tile in ct.get_attackable_tiles():
        if not ct.can_fire(tile):
            continue
        bot = ct.get_tile_builder_bot_id(tile)
        victim = bot if bot is not None else ct.get_tile_building_id(tile)
        if victim is None:
            continue
        if ct.get_team(victim) == my_team:
            continue
        etype = ct.get_entity_type(victim)
        pri = priority.get(etype)
        if pri is None:
            continue
        if pri < best_pri:
            best_pri = pri
            best_tile = tile
            if pri == 1:
                break

    if best_tile is not None:
        ct.fire(best_tile)


def run_sentinel(ct: Controller) -> None:
    """Fire at best enemy on the attack line: core > builder > harvester."""
    _fire_best(ct, _FIRE_PRIORITY)


def run_gunner(ct: Controller) -> None:
    """Fire: core > builder > harvester > belt."""
    _fire_best(ct, _GUNNER_FIRE_PRIORITY)


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

    if count_our_gunners(local_map, ct.get_team()) >= MAX_CORE_GUNNERS:
        return None

    site = pick_gunner_site(
        ct,
        enemy_core=enemy_core,
        my_index=my_index,
        local_map=local_map,
    )
    if site is None:
        return enemy_core

    pos = ct.get_position()
    if abs(pos.x - site.x) + abs(pos.y - site.y) == 1:
        build_gunner(ct, site, enemy_core)
    return site


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

    pos = ct.get_position()
    if abs(pos.x - site.x) + abs(pos.y - site.y) == 1:
        if build_sentinel(ct, site, enemy_core):
            n = min(ct.read_store(SLOT_SENTINEL_COUNT) + 1, MAX_SIEGE_SENTINELS)
            ct.write_store(SLOT_SENTINEL_COUNT, n)

    return site


def run_attack_builder(
    ct: Controller,
    *,
    my_index: int,
    local_map: dict,
    enemy_core: Position,
    ally_core: Position | None,
) -> Position | None:
    """Belt raid, then looping gunner → sentinel phases. None if idle."""
    round_n = ct.get_current_round()
    if round_n < GUNNER_TURN:
        return _run_belt_raid(ct, enemy_core=enemy_core)
    cycle_t = (round_n - GUNNER_TURN) % RAID_LOOP
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
