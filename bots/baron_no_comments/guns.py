
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

from geometry import chebyshevToCore, coreFootprint, isAdjacent
from pathing import inBounds, manhattan

GUNNER_RANGE_SQ = getattr(GameConstants, "GUNNER_ATTACK_RADIUS_SQUARED", 13)
SENTINEL_RANGE_SQ = getattr(GameConstants, "SENTINEL_ATTACK_RADIUS_SQUARED", 32)
GUNNER_ROTATE_MIN_TI = 50

SENTINEL_PLACE_COOLDOWN_TURNS = 10

SENTINEL_MIN_TURN = 50


def _sentinelPlaceReady(bot, ct: Controller) -> bool:
    last = getattr(bot, "lastSentinelRound", None)
    if last is None:
        return True
    return ct.get_current_round() - last >= SENTINEL_PLACE_COOLDOWN_TURNS


def _markSentinelPlaced(bot, ct: Controller) -> None:
    bot.lastSentinelRound = ct.get_current_round()


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


def allyTurretCounts(
    ct: Controller, local_map: dict[Any, Any] | None
) -> tuple[int, int]:
    gunners = 0
    sentinels = 0
    my_team = ct.get_team()
    if local_map:
        for cell in local_map.values():
            ent = cell.get("entity")
            if not ent or ent.get("team") != my_team:
                continue
            etype = ent.get("type")
            if etype == EntityType.GUNNER:
                gunners += 1
            elif etype == EntityType.SENTINEL:
                sentinels += 1
    return gunners, sentinels


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

    if AMMO_CONVERT_MIN_TI is not None and AMMO_CONVERT_MIN_TI > 0:
        if ct.get_global_resources() < AMMO_CONVERT_MIN_TI:
            return

    gunners, sentinels = allyTurretCounts(ct, local_map)

    target = AMMO_BASE + AMMO_PER_GUNNER * gunners + AMMO_PER_SENTINEL * sentinels
    if MAX_AMMO_POOL is not None:
        target = min(target, MAX_AMMO_POOL)
    target = max(target, AMMO_MIN_POOL)

    ammo = ct.get_global_ammo()
    if ammo >= target:
        return
    need = min(AMMO_CONVERT_CHUNK, target - ammo)
    if need <= 0:
        return
    if ct.can_convert_ammo(need):
        ct.convert_ammo(need)


def _entityOnTile(
    ct: Controller, pos: Position, *, cache: dict | None = None
) -> tuple[EntityType | None, bool]:
    key = (pos.x, pos.y)
    if cache is not None and key in cache:
        return cache[key]
    if not ct.is_in_vision(pos):
        result = (None, False)
    else:
        me = ct.get_team()
        bid = ct.get_tile_building_id(pos)
        if bid is not None:
            result = (ct.get_entity_type(bid), ct.get_team(bid) != me)
        else:
            bot_id = ct.get_tile_builder_bot_id(pos)
            if bot_id is not None:
                result = (EntityType.BUILDER_BOT, ct.get_team(bot_id) != me)
            else:
                result = (None, False)
    if cache is not None:
        cache[key] = result
    return result


def gunnerHasLos(
    ct: Controller,
    site: Position,
    facing: Direction,
    target: Position,
    *,
    shooterIsAlly: bool = True,
    cache: dict | None = None,
) -> bool:
    if site.distance_squared(target) < 1:
        return False
    if site.distance_squared(target) > GUNNER_RANGE_SQ:
        return False
    if ct.can_fire_from(site, facing, EntityType.GUNNER, target):
        return True
    w, h = ct.get_map_width(), ct.get_map_height()
    env_cache = None
    if cache is not None:
        env_cache = cache.setdefault("_env", {})
    p = site.add(facing)
    for _ in range(8):
        if not (0 <= p.x < w and 0 <= p.y < h):
            return False
        if p == target:
            return True
        if ct.is_in_vision(p):
            key = (p.x, p.y)
            if env_cache is not None and key in env_cache:
                env = env_cache[key]
            else:
                env = ct.get_tile_env(p)
                if env_cache is not None:
                    env_cache[key] = env
            if env == Environment.WALL:
                return False
            etype, is_enemy = _entityOnTile(ct, p, cache=cache)
            if etype is not None:
                chewable = is_enemy if shooterIsAlly else not is_enemy
                if not chewable or etype not in _TRANSPARENT_LOS:
                    return False
        if p.distance_squared(site) > GUNNER_RANGE_SQ:
            return False
        p = p.add(facing)
    return False


def allyGunCovering(ct: Controller, tile: Position) -> bool:
    me = ct.get_team()
    for eid in ct.get_nearby_entities():
        if ct.get_team(eid) != me:
            continue
        if ct.get_entity_type(eid) != EntityType.GUNNER:
            continue
        gpos = ct.get_position(eid)
        if tile.distance_squared(gpos) > GUNNER_RANGE_SQ:
            continue
        facing = ct.get_direction(eid)
        if gunnerHasLos(ct, gpos, facing, tile):
            return True
        if ct.can_fire_from(gpos, facing, EntityType.GUNNER, tile):
            return True
    return False


def _tilesInFront(
    gpos: Position, facing: Direction, *, max_steps: int = 8
) -> list[Position]:
    if facing == Direction.CENTRE:
        return []
    out: list[Position] = []
    p = gpos.add(facing)
    for _ in range(max_steps):
        out.append(p)
        p = p.add(facing)
    return out


def _allyGunnerBlockedPads(ct: Controller) -> set[tuple[int, int]]:
    blocked: set[tuple[int, int]] = set()
    me = ct.get_team()
    w, h = ct.get_map_width(), ct.get_map_height()
    for eid in ct.get_nearby_entities():
        if ct.get_team(eid) != me:
            continue
        if ct.get_entity_type(eid) != EntityType.GUNNER:
            continue
        gpos = ct.get_position(eid)
        try:
            facing = ct.get_direction(eid)
        except Exception:
            facing = Direction.CENTRE
        for p in _tilesInFront(gpos, facing):
            if 0 <= p.x < w and 0 <= p.y < h:
                blocked.add((p.x, p.y))
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                nx, ny = gpos.x + dx, gpos.y + dy
                if 0 <= nx < w and 0 <= ny < h:
                    blocked.add((nx, ny))
    return blocked


def _bestGunnerFacing(
    ct: Controller, site: Position, target: Position, *, cache: dict | None = None
) -> Direction | None:
    preferred = facing8way(site, target)
    if preferred == Direction.CENTRE:
        preferred = Direction.NORTH
    ordered = (preferred,) + tuple(d for d in _FACE_DIRS if d != preferred)
    for facing in ordered:
        if gunnerHasLos(ct, site, facing, target, cache=cache):
            return facing
    return None


_MAX_GUN_PADS = 10

_MAX_SENTINEL_PADS = 10


def _gunnerPadsNearTarget(
    ct: Controller, target: Position
) -> list[tuple[Position, Direction]]:
    pads: list[tuple[Position, Direction]] = []
    w, h = ct.get_map_width(), ct.get_map_height()
    blocked = _allyGunnerBlockedPads(ct)
    cache: dict = {}
    for dy in range(-4, 5):
        for dx in range(-4, 5):
            site = Position(target.x + dx, target.y + dy)
            if not (0 <= site.x < w and 0 <= site.y < h):
                continue
            dist = site.distance_squared(target)
            if dist < 1 or dist > GUNNER_RANGE_SQ:
                continue
            if not ct.is_in_vision(site):
                continue
            env = ct.get_tile_env(site)
            if env == Environment.WALL or env == Environment.ORE_TITANIUM:
                continue
            if not ct.is_tile_empty(site):
                continue
            if (site.x, site.y) in blocked:
                continue
            facing = _bestGunnerFacing(ct, site, target, cache=cache)
            if facing is None:
                continue
            pads.append((site, facing))
            if len(pads) >= _MAX_GUN_PADS:
                return pads
    return pads


def _canAffordGunPlace(ct: Controller) -> bool:
    from main import GUN_PLACE_MIN_TI

    if GUN_PLACE_MIN_TI is None or GUN_PLACE_MIN_TI <= 0:
        return True
    return ct.get_global_resources() >= GUN_PLACE_MIN_TI


def placeGun(bot, ct: Controller, target: Position) -> bool:
    if not _canAffordGunPlace(ct):
        return False
    pads = _gunnerPadsNearTarget(ct, target)
    if not pads:
        return False

    pos = ct.get_position()
    pads.sort(
        key=lambda sf: (
            sf[0].distance_squared(target),
            manhattan(pos, sf[0]),
        )
    )

    if ct.get_action_cooldown() == 0:
        for site, facing in pads:
            if not isAdjacent(pos, site):
                continue
            if ct.can_build_gunner(site, facing):
                ct.build_gunner(site, facing)
                return True

    best_site = pads[0][0]
    if manhattan(pos, best_site) <= 1:
        return False
    from logistics import moveToward

    return moveToward(bot, ct, best_site)


def _hostileTiles(
    ct: Controller, eid: int, etype: EntityType
) -> list[Position]:
    epos = ct.get_position(eid)
    if etype != EntityType.CORE:
        return [epos]
    return coreFootprint(epos)


def _hostileOnFacing(
    ct: Controller, priority: dict
) -> Position | None:
    me = ct.get_team()
    best: Position | None = None
    best_p = 10**9
    for eid in ct.get_nearby_entities():
        if ct.get_team(eid) == me:
            continue
        etype = ct.get_entity_type(eid)
        if etype not in priority:
            continue
        prio = priority[etype]
        for epos in _hostileTiles(ct, eid, etype):
            if not ct.can_fire(epos):
                continue
            if prio < best_p:
                best_p = prio
                best = epos
    try:
        gt = ct.get_gunner_target()
        if gt is not None and ct.can_fire(gt):
            bid = ct.get_tile_building_id(gt)
            bot_id = ct.get_tile_builder_bot_id(gt)
            eid = bid if bid is not None else bot_id
            if eid is not None and ct.get_team(eid) != me:
                etype = ct.get_entity_type(eid)
                p = priority.get(etype, 9)
                if best is None or p <= best_p:
                    return gt
    except Exception:
        pass
    return best


def _bestRotateTarget(ct: Controller, priority: dict) -> Position | None:
    me = ct.get_team()
    pos = ct.get_position()
    best: Position | None = None
    best_key = None
    for eid in ct.get_nearby_entities():
        if ct.get_team(eid) == me:
            continue
        etype = ct.get_entity_type(eid)
        if etype not in priority:
            continue
        for epos in _hostileTiles(ct, eid, etype):
            if pos.distance_squared(epos) > GUNNER_RANGE_SQ:
                continue
            if not inBounds(ct, epos):
                continue
            facing = facing8way(pos, epos)
            if facing == Direction.CENTRE:
                continue
            if not ct.can_fire_from(pos, facing, EntityType.GUNNER, epos):
                continue
            key = (priority[etype], manhattan(pos, epos))
            if best_key is None or key < best_key:
                best_key = key
                best = epos
    return best


def gunnerLoop(ct: Controller) -> None:
    if ct.get_action_cooldown() != 0:
        return
    hit = _hostileOnFacing(ct, _GUNNER_PRIORITY)
    if hit is not None and ct.can_fire(hit):
        ct.fire(hit)
        return

    target = _bestRotateTarget(ct, _GUNNER_PRIORITY)
    if target is None:
        return
    facing = facing8way(ct.get_position(), target)
    if facing == Direction.CENTRE:
        return
    try:
        current = ct.get_direction()
    except Exception:
        current = None
    if current == facing:
        return
    if ct.get_global_resources() < GUNNER_ROTATE_MIN_TI:
        return
    if ct.can_rotate(facing):
        ct.rotate(facing)


def _aimCoreTile(site: Position, core_pos: Position) -> Position:
    return min(
        coreFootprint(core_pos),
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
        return ct.can_fire_from(site, facing, EntityType.SENTINEL, tile)
    except Exception:
        pass
    w, h = ct.get_map_width(), ct.get_map_height()
    p = site.add(facing)
    for _ in range(8):
        if not (0 <= p.x < w and 0 <= p.y < h):
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
    footprint = coreFootprint(core_pos)
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


def _gunfireSafePads(
    ct: Controller, pads: list[tuple[Position, Direction]]
) -> list[tuple[Position, Direction]]:
    from offense import _enemyGunners, _tileUnderGunnerFire

    gunners = _enemyGunners(ct)
    if not gunners:
        return pads
    cache: dict = {}
    return [
        (site, facing)
        for site, facing in pads
        if not _tileUnderGunnerFire(ct, site, gunners, cache=cache)
    ]


def _sentinelPadsNearTarget(
    ct: Controller, target: Position
) -> list[tuple[Position, Direction]]:
    pads: list[tuple[Position, Direction]] = []
    w, h = ct.get_map_width(), ct.get_map_height()
    footprint = coreFootprint(target)
    for dy in range(-6, 7):
        for dx in range(-6, 7):
            site = Position(target.x + dx, target.y + dy)
            if not (0 <= site.x < w and 0 <= site.y < h):
                continue
            if any(site.x == t.x and site.y == t.y for t in footprint):
                continue
            if min(site.distance_squared(t) for t in footprint) > SENTINEL_RANGE_SQ:
                continue
            if not ct.is_in_vision(site):
                continue
            env = ct.get_tile_env(site)
            if env == Environment.WALL or env == Environment.ORE_TITANIUM:
                continue
            if not ct.is_tile_empty(site):
                continue
            facing = _bestSentinelFacing(ct, site, target)
            if facing is None:
                continue
            pads.append((site, facing))
            if len(pads) >= _MAX_SENTINEL_PADS:
                return pads
    return pads


def placeSentinel(bot, ct: Controller, target: Position) -> bool:
    if ct.get_current_round() < SENTINEL_MIN_TURN:
        return False
    if not _canAffordGunPlace(ct):
        return False
    if not _sentinelPlaceReady(bot, ct):
        return False
    pads = _sentinelPadsNearTarget(ct, target)
    if not pads:
        return False
    pads = _gunfireSafePads(ct, pads)
    if not pads:
        return False
    pos = ct.get_position()
    footprint = coreFootprint(target)
    pads.sort(
        key=lambda sf: (
            -min(sf[0].distance_squared(t) for t in footprint),  # max range first
            manhattan(pos, sf[0]),  # then nearest to self
        )
    )
    if ct.get_action_cooldown() == 0:
        for site, facing in pads:
            if not isAdjacent(pos, site):
                continue
            if ct.can_build_sentinel(site, facing):
                ct.build_sentinel(site, facing)
                _markSentinelPlaced(bot, ct)
                return True
    best_site = pads[0][0]
    if manhattan(pos, best_site) <= 1:
        return False
    from logistics import moveToward

    return moveToward(bot, ct, best_site)


def allySentinelCovering(ct: Controller, tile: Position) -> bool:
    me = ct.get_team()
    for eid in ct.get_nearby_entities():
        if ct.get_team(eid) != me:
            continue
        if ct.get_entity_type(eid) != EntityType.SENTINEL:
            continue
        spos = ct.get_position(eid)
        if tile.distance_squared(spos) > SENTINEL_RANGE_SQ:
            continue
        try:
            facing = ct.get_direction(eid)
        except Exception:
            continue
        if _sentinelHitsTile(ct, spos, facing, tile):
            return True
    return False


def _defSentinelPads(
    ct: Controller, target: Position, core_pos: Position, max_cheb: int
) -> list[tuple[Position, Direction]]:
    pads: list[tuple[Position, Direction]] = []
    w, h = ct.get_map_width(), ct.get_map_height()
    for dy in range(-6, 7):
        for dx in range(-6, 7):
            site = Position(target.x + dx, target.y + dy)
            if not (0 <= site.x < w and 0 <= site.y < h):
                continue
            if site.distance_squared(target) > SENTINEL_RANGE_SQ:
                continue
            if chebyshevToCore(site, core_pos) > max_cheb:
                continue
            if not ct.is_in_vision(site):
                continue
            env = ct.get_tile_env(site)
            if env == Environment.WALL or env == Environment.ORE_TITANIUM:
                continue
            if not ct.is_tile_empty(site):
                continue
            facing = None
            preferred = facing8way(site, target)
            ordered = (preferred,) + tuple(
                d for d in _FACE_DIRS if d != preferred
            )
            for d in ordered:
                if d != Direction.CENTRE and _sentinelHitsTile(ct, site, d, target):
                    facing = d
                    break
            if facing is None:
                continue
            pads.append((site, facing))
    return pads


def placeDefSentinel(
    bot, ct: Controller, target: Position, core_pos: Position, max_cheb: int
) -> bool:
    if ct.get_current_round() < SENTINEL_MIN_TURN:
        return False
    if not _canAffordGunPlace(ct):
        return False
    if not _sentinelPlaceReady(bot, ct):
        return False
    pads = _defSentinelPads(ct, target, core_pos, max_cheb)
    if not pads:
        return False
    pads = _gunfireSafePads(ct, pads)
    if not pads:
        return False

    pos = ct.get_position()
    pads.sort(
        key=lambda sf: (
            chebyshevToCore(sf[0], core_pos),
            manhattan(pos, sf[0]),
        )
    )

    if ct.get_action_cooldown() == 0:
        for site, facing in pads:
            if not isAdjacent(pos, site):
                continue
            if ct.can_build_sentinel(site, facing):
                ct.build_sentinel(site, facing)
                _markSentinelPlaced(bot, ct)
                return True

    best_site = pads[0][0]
    if manhattan(pos, best_site) <= 1:
        return False
    from logistics import moveToward

    return moveToward(bot, ct, best_site)


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
        for epos in _hostileTiles(ct, eid, etype):
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