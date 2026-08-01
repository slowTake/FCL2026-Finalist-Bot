"""Mid-game sentinel placement, ammo top-up, and fire for earl.

Even builders (my_index even, > 0) place sentinels around the enemy core at
max range once ATTACK_TURN is reached. Core tops ammo to 20 × max(1, n);
sentinels fire at enemy core → builders → harvesters.
"""

from __future__ import annotations

from fcode import Controller, Direction, EntityType, Environment, Position

ATTACK_TURN = 80
MAX_SIEGE_SENTINELS = 5
SENTINEL_RANGE_SQ = 32
SENTINEL_PLACE_MIN_SQ = 16  # d=4 → 16
AMMO_PER_SENTINEL = 20
SLOT_SENTINEL_COUNT = 3

# Fire priority: lower = better
_FIRE_PRIORITY = {
    EntityType.CORE: 1,
    EntityType.BUILDER_BOT: 2,
    EntityType.HARVESTER: 3,
}

def _core_footprint(core_pos: Position) -> list[Position]:
    return [
        core_pos,
        Position(core_pos.x + 1, core_pos.y),
        Position(core_pos.x, core_pos.y + 1),
        Position(core_pos.x + 1, core_pos.y + 1),
    ]


def facing_8way(tile: Position, target: Position) -> Direction:
    """Full 8-direction facing for sentinels."""
    d = tile.direction_to(target)
    return d if d != Direction.CENTRE else Direction.NORTH


def should_attack(
    round_n: int, my_index: int, enemy_core: Position | None
) -> bool:
    return (
        round_n >= ATTACK_TURN
        and my_index > 0
        and my_index % 2 == 0
        and enemy_core is not None
    )


def siege_preferred_side(my_index: int) -> int:
    """0=N 1=NE 2=E 3=SE 4=S 5=SW 6=W 7=NW — stagger by even builder index."""
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


def pick_siege_site(
    ct: Controller,
    *,
    enemy_core: Position,
    ally_core: Position | None,
    my_index: int,
    local_map: dict,
) -> Position | None:
    """Ring tile in sentinel range; each builder prefers a different side."""
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


def build_sentinel(
    ct: Controller, site: Position, enemy_core: Position
) -> bool:
    """Build a sentinel at site facing the enemy core. Caller must be adjacent."""
    pos = ct.get_position()
    if abs(pos.x - site.x) + abs(pos.y - site.y) != 1:
        return False
    if ct.get_action_cooldown() != 0:
        return False
    facing = facing_8way(site, enemy_core)
    if not ct.can_build_sentinel(site, facing):
        return False
    ct.build_sentinel(site, facing)
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
    """Team ammo floor: 20 × max(1, living sentinels), capped at siege max."""
    living = max(0, min(n, MAX_SIEGE_SENTINELS))
    return AMMO_PER_SENTINEL * max(1, living)


def top_up_ammo(ct: Controller) -> None:
    """Core: top ammo to 20 × max(1, n) from turn ATTACK_TURN onward."""
    if ct.get_current_round() < ATTACK_TURN:
        return
    n = ct.read_store(SLOT_SENTINEL_COUNT)
    target = ammo_target(n)
    ammo = ct.get_global_ammo()
    if ammo >= target:
        return
    amount = min(target - ammo, ct.get_global_resources())
    if amount > 0 and ct.can_convert_ammo(amount):
        ct.convert_ammo(amount)


def run_sentinel(ct: Controller) -> None:
    """Fire at best enemy on the attack line: core > builder > harvester."""
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
        pri = _FIRE_PRIORITY.get(etype)
        if pri is None:
            continue
        if pri < best_pri:
            best_pri = pri
            best_tile = tile
            if pri == 1:
                break

    if best_tile is not None:
        ct.fire(best_tile)


def run_attack_builder(
    ct: Controller,
    *,
    my_index: int,
    local_map: dict,
    enemy_core: Position,
    ally_core: Position | None,
) -> Position | None:
    """Pick/build a siege site. Returns target for movement, or None if idle."""
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
            # Reflect the new turret immediately for same-turn store readers.
            n = min(ct.read_store(SLOT_SENTINEL_COUNT) + 1, MAX_SIEGE_SENTINELS)
            ct.write_store(SLOT_SENTINEL_COUNT, n)

    return site
