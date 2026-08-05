"""Offensive execution for harasser builders — avoid fire, place turrets, seek."""

from __future__ import annotations

from fcode import Controller, Direction, EntityType, Position

from guns import (
    GUNNER_RANGE_SQ,
    allyGunCovering,
    gunnerHasLos,
    placeGun,
    placeSentinel,
)
from logistics import moveToward
from pathing import inBounds, manhattan, walkPassable

_AVOID_RADIUS = 4


def _enemyGunners(ct: Controller) -> list[tuple[Position, Direction]]:
    me = ct.get_team()
    out: list[tuple[Position, Direction]] = []
    for eid in ct.get_nearby_entities():
        if ct.get_team(eid) == me:
            continue
        if ct.get_entity_type(eid) != EntityType.GUNNER:
            continue
        out.append((ct.get_position(eid), ct.get_direction(eid)))
    return out


def _tileUnderGunnerFire(
    ct: Controller,
    tile: Position,
    gunners: list[tuple[Position, Direction]],
) -> bool:
    for gpos, facing in gunners:
        if tile.distance_squared(gpos) > GUNNER_RANGE_SQ:
            continue
        if gunnerHasLos(ct, gpos, facing, tile):
            return True
        # Optimistic: gunner aimed at us even if engine LoS disagrees on fog
        if facing == gpos.direction_to(tile) and tile.distance_squared(gpos) <= GUNNER_RANGE_SQ:
            if ct.can_fire_from(gpos, facing, EntityType.GUNNER, tile):
                return True
    return False


def avoidFire(bot, ct: Controller) -> bool:
    """If under enemy gunner LoS, step to a nearby tile outside that LoS."""
    pos = ct.get_position()
    gunners = _enemyGunners(ct)
    if not gunners:
        return False
    if not _tileUnderGunnerFire(ct, pos, gunners):
        return False

    local_map = bot.hivemind.local_map
    best: Position | None = None
    best_d = 10**9
    for dy in range(-_AVOID_RADIUS, _AVOID_RADIUS + 1):
        for dx in range(-_AVOID_RADIUS, _AVOID_RADIUS + 1):
            if dx == 0 and dy == 0:
                continue
            cand = Position(pos.x + dx, pos.y + dy)
            if not inBounds(ct, cand):
                continue
            if not walkPassable(ct, cand, local_map=local_map):
                continue
            if ct.is_in_vision(cand) and ct.get_tile_builder_bot_id(cand) is not None:
                continue
            if _tileUnderGunnerFire(ct, cand, gunners):
                continue
            d = abs(dx) + abs(dy)
            if d < best_d:
                best_d = d
                best = cand
    if best is None:
        return False
    return moveToward(bot, ct, best)


def _nearestEnemyOfType(ct: Controller, etype: EntityType) -> Position | None:
    me = ct.get_team()
    pos = ct.get_position()
    best: Position | None = None
    best_d = 10**9
    for eid in ct.get_nearby_entities():
        if ct.get_team(eid) == me:
            continue
        if ct.get_entity_type(eid) != etype:
            continue
        epos = ct.get_position(eid)
        d = manhattan(pos, epos)
        if d < best_d:
            best_d = d
            best = epos
    return best


def offGun(bot, ct: Controller) -> bool:
    """Place gunner/sentinel toward core or visible threats (priority order)."""
    enemy_core = bot.enemyCorePos
    if enemy_core is not None:
        if placeGun(bot, ct, enemy_core):
            return True
        if placeSentinel(bot, ct, enemy_core):
            return True

    for etype in (
        EntityType.GUNNER,
        EntityType.SENTINEL,
        EntityType.HARVESTER,
        EntityType.LAUNCHER,
    ):
        target = _nearestEnemyOfType(ct, etype)
        if target is None:
            continue
        if etype == EntityType.HARVESTER and allyGunCovering(ct, target):
            continue
        if placeGun(bot, ct, target):
            return True
    return False


def _localEnemyHarvesters(bot, ct: Controller) -> list[Position]:
    me = ct.get_team()
    found: list[Position] = []
    for (x, y), cell in bot.hivemind.local_map.items():
        ent = cell.get("entity")
        if not ent:
            continue
        if ent.get("type") != EntityType.HARVESTER:
            continue
        if ent.get("team") == me:
            continue
        found.append(Position(x, y))
    found.sort(key=lambda p: manhattan(ct.get_position(), p))
    return found


def _scoutedOres(bot) -> set[tuple[int, int]]:
    s = getattr(bot, "scoutedOres", None)
    if s is None:
        bot.scoutedOres = set()
        return bot.scoutedOres
    return s


def _markOreScouted(bot, tile: Position) -> None:
    _scoutedOres(bot).add((tile.x, tile.y))


def _enemyHarvesterOnTile(ct: Controller, tile: Position) -> bool:
    bid = ct.get_tile_building_id(tile)
    return (
        bid is not None
        and ct.get_entity_type(bid) == EntityType.HARVESTER
        and ct.get_team(bid) != ct.get_team()
    )


def _scoutOreTargets(bot, ct: Controller) -> list[Position]:
    """Ore tiles to inspect: hostile entity on ore, or ore without ally harvester."""
    me = ct.get_team()
    pos = ct.get_position()
    seen = _scoutedOres(bot)
    scored: list[tuple[int, int, Position]] = []
    for (x, y), cell in bot.hivemind.local_map.items():
        if not cell.get("ore"):
            continue
        if (x, y) in seen:
            continue
        ent = cell.get("entity")
        # Skip friendly harvesters we're not clearing
        if ent and ent.get("type") == EntityType.HARVESTER and ent.get("team") == me:
            continue
        priority = 1
        if ent and ent.get("team") != me:
            priority = 0
        tile = Position(x, y)
        scored.append((priority, manhattan(pos, tile), tile))
    scored.sort()
    return [t for _, _, t in scored]


def seekHarvester(bot, ct: Controller) -> bool:
    """Move to known enemy harvesters or scout ore nodes; vision refreshes local_map."""
    sticky = getattr(bot, "harassTarget", None)
    if sticky is not None:
        sx, sy = sticky
        tile = Position(sx, sy)
        if ct.is_in_vision(tile):
            if _enemyHarvesterOnTile(ct, tile):
                pass  # keep sticky on live enemy harvester
            else:
                # Inspected empty / friendly / cleared — don't re-scout this pad
                _markOreScouted(bot, tile)
                bot.harassTarget = None
                sticky = None
        if sticky is not None and moveToward(bot, ct, tile):
            return True

    for harv in _localEnemyHarvesters(bot, ct):
        bot.harassTarget = (harv.x, harv.y)
        if moveToward(bot, ct, harv):
            return True

    for ore in _scoutOreTargets(bot, ct):
        # Already standing on / next to an empty scout ore → mark and skip
        if ct.is_in_vision(ore) and not _enemyHarvesterOnTile(ct, ore):
            pos = ct.get_position()
            if abs(pos.x - ore.x) + abs(pos.y - ore.y) <= 1:
                _markOreScouted(bot, ore)
                continue
        bot.harassTarget = (ore.x, ore.y)
        if moveToward(bot, ct, ore):
            return True
    return False


def seekCore(bot, ct: Controller) -> bool:
    """Push toward known enemy core."""
    core = bot.enemyCorePos
    if core is None:
        return False
    return moveToward(bot, ct, core)


def run_raider(bot, ct: Controller) -> None:
    from builder import runHarasser

    runHarasser(bot, ct)


def run_raiders(bot, ct: Controller) -> None:
    from builder import runHarasser

    runHarasser(bot, ct)


def run_attack_builder(bot, ct: Controller) -> None:
    from builder import runHarasser

    runHarasser(bot, ct)
