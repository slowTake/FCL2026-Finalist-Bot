"""Turret placement and operation for sentinel_baron (focused on Sentinels)."""

from __future__ import annotations

from typing import Any

from fcode import (
    Controller,
    Direction,
    EntityType,
    Environment,
    GameConstants,
    Position,
)

from pathing import inBounds, manhattan

GUNNER_RANGE_SQ = getattr(GameConstants, "GUNNER_ATTACK_RADIUS_SQUARED", 13)
SENTINEL_RANGE_SQ = getattr(GameConstants, "SENTINEL_ATTACK_RADIUS_SQUARED", 32)
GUNNER_ROTATE_MIN_TI = 50


def _coreFootprint(core_pos: Position) -> list[Position]:
    return [
        core_pos,
        Position(core_pos.x + 1, core_pos.y),
        Position(core_pos.x, core_pos.y + 1),
        Position(core_pos.x + 1, core_pos.y + 1),
    ]


_TRANSPARENT_LOS = frozenset(
    {
        EntityType.CONVEYOR,
        EntityType.SPLITTER,
        EntityType.HARVESTER,
        EntityType.BUILDER_BOT,
        EntityType.LAUNCHER,
        EntityType.GUNNER,
        EntityType.SENTINEL,
    }
)

_GUNNER_PRIORITY = {
    EntityType.CORE: 1,
    EntityType.BUILDER_BOT: 2,
    EntityType.GUNNER: 3,
    EntityType.SENTINEL: 4,
    EntityType.LAUNCHER: 5,
    EntityType.HARVESTER: 6,
    EntityType.CONVEYOR: 8,
    EntityType.SPLITTER: 8,
    EntityType.BARRIER: 7,
}

_SENTINEL_PRIORITY = {
    EntityType.CORE: 1,
    EntityType.HARVESTER: 2,
    EntityType.GUNNER: 3,
    EntityType.SENTINEL: 4,
    EntityType.BUILDER_BOT: 5,
    EntityType.LAUNCHER: 6,
    EntityType.BARRIER: 7,
    EntityType.CONVEYOR: 8,
    EntityType.SPLITTER: 8,
}

_FACE_DIRS = (
    Direction.NORTH,
    Direction.NORTHEAST,
    Direction.EAST,
    Direction.SOUTHEAST,
    Direction.SOUTH,
    Direction.SOUTHWEST,
    Direction.WEST,
    Direction.NORTHWEST,
)


def facing8way(tile: Position, target: Position) -> Direction:
    return tile.direction_to(target)


def topUpAmmo(
    ct: Controller,
    *,
    local_map: dict[Any, Any] | None = None,
    min_turn: int = 0,
) -> None:
    if ct.get_current_round() < min_turn:
        return
    from main import (
        AMMO_BASE,
        AMMO_CONVERT_CHUNK,
        AMMO_CONVERT_MIN_TI,
        AMMO_MIN_POOL,
        AMMO_PER_GUNNER,
        AMMO_PER_SENTINEL,
        MAX_AMMO_POOL,
    )

    if AMMO_CONVERT_MIN_TI is None or AMMO_CONVERT_MIN_TI <= 0:
        return
    if ct.get_action_cooldown() != 0:
        return
    ti = ct.get_global_resources()
    if ti < AMMO_CONVERT_MIN_TI:
        return

    guns = 0
    sentinels = 0
    if local_map is not None:
        me = ct.get_team()
        for _key, entry in local_map.items():
            ent = entry.get("entity")
            if not ent or ent.get("team") != me:
                continue
            t = ent.get("type")
            if t == EntityType.GUNNER:
                guns += 1
            elif t == EntityType.SENTINEL:
                sentinels += 1
    else:
        me = ct.get_team()
        for eid in ct.get_nearby_entities():
            if ct.get_team(eid) != me:
                continue
            etype = ct.get_entity_type(eid)
            if etype == EntityType.GUNNER:
                guns += 1
            elif etype == EntityType.SENTINEL:
                sentinels += 1

    desired = AMMO_BASE + guns * AMMO_PER_GUNNER + sentinels * AMMO_PER_SENTINEL
    if MAX_AMMO_POOL is not None:
        desired = min(desired, MAX_AMMO_POOL)

    ammo = ct.get_global_ammo()
    if ammo >= desired:
        return
    if ammo >= AMMO_MIN_POOL and ti < 100:
        return

    need = desired - ammo
    chunks = (need + AMMO_CONVERT_CHUNK - 1) // AMMO_CONVERT_CHUNK
    amount = chunks * AMMO_CONVERT_CHUNK
    amount = min(amount, ti)
    if amount > 0 and ct.can_convert_ammo(amount):
        ct.convert_ammo(amount)


top_up_ammo = topUpAmmo


def allyGunCovering(ct: Controller, tile: Position) -> bool:
    """True if any ally gunner in vision can already hit tile."""
    me = ct.get_team()
    for eid in ct.get_nearby_entities():
        if ct.get_team(eid) != me:
            continue
        if ct.get_entity_type(eid) not in (EntityType.GUNNER, EntityType.SENTINEL):
            continue
        gpos = ct.get_position(eid)
        rng = SENTINEL_RANGE_SQ if ct.get_entity_type(eid) == EntityType.SENTINEL else GUNNER_RANGE_SQ
        if tile.distance_squared(gpos) > rng:
            continue
        facing = ct.get_direction(eid)
        if gunnerHasLos(ct, gpos, facing, tile):
            return True
        if ct.can_fire_from(gpos, facing, ct.get_entity_type(eid), tile):
            return True
    return False


def _tilesInFront(site: Position, facing: Direction) -> list[Position]:
    tiles: list[Position] = []
    p = site.add(facing)
    for _ in range(3):
        tiles.append(p)
        p = p.add(facing)
    return tiles


def _canAffordGunPlace(ct: Controller) -> bool:
    from main import GUN_PLACE_MIN_TI

    if GUN_PLACE_MIN_TI is None or GUN_PLACE_MIN_TI <= 0:
        return True
    return ct.get_global_resources() >= GUN_PLACE_MIN_TI


def gunnerHasLos(
    ct: Controller, site: Position, facing: Direction, target: Position
) -> bool:
    w, h = ct.get_map_width(), ct.get_map_height()
    p = site.add(facing)
    for _ in range(4):
        if not (0 <= p.x < w and 0 <= p.y < h):
            return False
        if p == target:
            return True
        if p.distance_squared(site) > GUNNER_RANGE_SQ:
            return False
        if ct.is_in_vision(p):
            env = ct.get_tile_env(p)
            if env == Environment.WALL:
                return False
            bid = ct.get_tile_building_id(p)
            if bid is not None:
                etype = ct.get_entity_type(bid)
                if etype not in _TRANSPARENT_LOS:
                    return False
        p = p.add(facing)
    return False


def _hostileOnFacing(ct: Controller, priority: dict) -> Position | None:
    me = ct.get_team()
    pos = ct.get_position()
    try:
        facing = ct.get_direction()
    except Exception:
        return None
    if facing == Direction.CENTRE:
        return None

    best: Position | None = None
    best_key = None
    w, h = ct.get_map_width(), ct.get_map_height()
    p = pos.add(facing)
    for _ in range(4):
        if not (0 <= p.x < w and 0 <= p.y < h):
            break
        if p.distance_squared(pos) > GUNNER_RANGE_SQ:
            break
        if ct.is_in_vision(p):
            env = ct.get_tile_env(p)
            if env == Environment.WALL:
                break
            bid = ct.get_tile_building_id(p)
            if bid is not None:
                if ct.get_team(bid) != me:
                    etype = ct.get_entity_type(bid)
                    if etype in priority:
                        key = (priority[etype], manhattan(pos, p))
                        if best_key is None or key < best_key:
                            best_key = key
                            best = p
                else:
                    etype = ct.get_entity_type(bid)
                    if etype not in _TRANSPARENT_LOS:
                        break
        p = p.add(facing)
    return best


def _hostileTiles(
    ct: Controller, eid: int, etype: EntityType
) -> list[Position]:
    epos = ct.get_position(eid)
    if etype == EntityType.CORE:
        return _coreFootprint(epos)
    return [epos]


def placeGun(bot, ct: Controller, target: Position) -> bool:
    if not _canAffordGunPlace(ct):
        return False
    from main import GUNNER_ROTATE_MIN_TI
    pos = ct.get_position()
    w, h = ct.get_map_width(), ct.get_map_height()
    cost = ct.get_gunner_cost()

    pads = []
    for dy in range(-4, 5):
        for dx in range(-4, 5):
            site = Position(target.x + dx, target.y + dy)
            if not (0 <= site.x < w and 0 <= site.y < h):
                continue
            if site.distance_squared(target) > GUNNER_RANGE_SQ:
                continue
            if not ct.is_in_vision(site):
                continue
            env = ct.get_tile_env(site)
            if env == Environment.WALL or env == Environment.ORE_TITANIUM:
                continue
            if not ct.is_tile_empty(site):
                continue
            facing = _bestGunnerFacing(ct, site, target)
            if facing is None:
                continue
            pads.append((site, facing))

    if not pads:
        return False

    pads.sort(key=lambda sf: (manhattan(target, sf[0]), manhattan(pos, sf[0])))

    if ct.get_action_cooldown() == 0:
        for site, facing in pads:
            if abs(pos.x - site.x) + abs(pos.y - site.y) != 1:
                continue
            if ct.get_global_resources() >= cost and ct.can_build_gunner(site, facing):
                ct.build_gunner(site, facing)
                return True

    best_site = pads[0][0]
    if abs(pos.x - best_site.x) + abs(pos.y - best_site.y) <= 1:
        return False
    from logistics import moveToward

    return moveToward(bot, ct, best_site)


def gunnerLoop(ct: Controller) -> None:
    if ct.get_action_cooldown() != 0:
        return
    hit = _hostileOnFacing(ct, _GUNNER_PRIORITY)
    if hit is not None and ct.can_fire(hit):
        ct.fire(hit)


def run_gunner(ct: Controller) -> None:
    gunnerLoop(ct)


def _aimCoreTile(site: Position, core_pos: Position) -> Position:
    return min(
        _coreFootprint(core_pos),
        key=lambda t: site.distance_squared(t),
    )


def _sentinelHitsTile(
    ct: Controller, site: Position, facing: Direction, tile: Position
) -> bool:
    if site.distance_squared(tile) < 1:
        return False
    if site.distance_squared(tile) > SENTINEL_RANGE_SQ:
        return False
    try:
        if ct.can_fire_from(site, facing, EntityType.SENTINEL, tile):
            return True
    except Exception:
        pass
    w, h = ct.get_map_width(), ct.get_map_height()
    p = site.add(facing)
    for _ in range(8):
        if not (0 <= p.x < w and 0 <= p.y < h):
            return False
        if ct.is_in_vision(p):
            if ct.get_tile_env(p) == Environment.WALL:
                return False
        if p == tile:
            return True
        if p.distance_squared(site) > SENTINEL_RANGE_SQ:
            return False
        p = p.add(facing)
    return False


def _bestSentinelFacing(
    ct: Controller, site: Position, core_pos: Position
) -> Direction | None:
    footprint = _coreFootprint(core_pos)
    preferred = facing8way(site, _aimCoreTile(site, core_pos))
    ordered: list[Direction] = []
    if preferred != Direction.CENTRE:
        ordered.append(preferred)
    ordered.extend(d for d in _FACE_DIRS if d != preferred)

    for facing in ordered:
        for tile in footprint:
            if _sentinelHitsTile(ct, site, facing, tile):
                return facing
    return None


def _sentinelPadsNearTarget(
    ct: Controller, target: Position
) -> list[tuple[Position, Direction]]:
    pads: list[tuple[Position, Direction]] = []
    w, h = ct.get_map_width(), ct.get_map_height()
    footprint = _coreFootprint(target)
    for dy in range(-6, 7):
        for dx in range(-6, 7):
            site = Position(target.x + dx, target.y + dy)
            if not (0 <= site.x < w and 0 <= site.y < h):
                continue
            if any(site.x == t.x and site.y == t.y for t in footprint):
                continue
            if min(site.distance_squared(t) for t in footprint) > SENTINEL_RANGE_SQ:
                continue
            if ct.is_in_vision(site):
                env = ct.get_tile_env(site)
                if env == Environment.WALL or env == Environment.ORE_TITANIUM:
                    continue
                if not ct.is_tile_empty(site):
                    continue
            else:
                local_map = getattr(ct, "local_map", {})
                entry = local_map.get((site.x, site.y), {})
                if entry.get("passable") is False or entry.get("entity") is not None:
                    continue
            facing = _bestSentinelFacing(ct, site, target)
            if facing is None:
                continue
            pads.append((site, facing))
    return pads


def placeSentinel(bot, ct: Controller, target: Position) -> bool:
    if not _canAffordGunPlace(ct):
        return False
    pads = _sentinelPadsNearTarget(ct, target)
    if not pads:
        return False
    pos = ct.get_position()
    footprint = _coreFootprint(target)
    # Sort to prefer maximum range r^2 up to 32
    pads.sort(
        key=lambda sf: (
            -min(sf[0].distance_squared(t) for t in footprint),
            manhattan(pos, sf[0]),
        )
    )
    if ct.get_action_cooldown() == 0:
        for site, facing in pads:
            if abs(pos.x - site.x) + abs(pos.y - site.y) != 1:
                continue
            if ct.can_build_sentinel(site, facing):
                ct.build_sentinel(site, facing)
                return True
    best_site = pads[0][0]
    if abs(pos.x - best_site.x) + abs(pos.y - best_site.y) <= 1:
        return False
    from logistics import moveToward

    return moveToward(bot, ct, best_site)


def place_sentinel_aggressive(bot, ct: Controller, **kwargs: Any) -> bool:
    target = kwargs.get("target")
    if target is None:
        return False
    return placeSentinel(bot, ct, target)


def sentinelLoop(ct: Controller) -> None:
    if ct.get_action_cooldown() != 0:
        return
    me = ct.get_team()
    pos = ct.get_position()
    best: Position | None = None
    best_key = None
    for eid in ct.get_nearby_entities():
        if ct.get_team(eid) == me:
            continue
        etype = ct.get_entity_type(eid)
        if etype not in _SENTINEL_PRIORITY:
            continue
        epos = ct.get_position(eid)
        if pos.distance_squared(epos) > SENTINEL_RANGE_SQ:
            continue
        if not ct.can_fire(epos):
            continue
        key = (_SENTINEL_PRIORITY[etype], manhattan(pos, epos))
        if best_key is None or key < best_key:
            best_key = key
            best = epos
    if best is not None and ct.can_fire(best):
        ct.fire(best)


def run_sentinel(ct: Controller) -> None:
    sentinelLoop(ct)
