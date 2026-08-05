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
_ORBIT_OFFSET = 3


def _coreFootprint(core_pos: Position) -> list[Position]:
    return [
        core_pos,
        Position(core_pos.x + 1, core_pos.y),
        Position(core_pos.x, core_pos.y + 1),
        Position(core_pos.x + 1, core_pos.y + 1),
    ]


def _chebyshevToFootprint(pos: Position, core_pos: Position) -> int:
    return min(
        max(abs(pos.x - t.x), abs(pos.y - t.y)) for t in _coreFootprint(core_pos)
    )


def _orbitMidSide(bot, ct: Controller, core: Position) -> list[Position]:
    """Four mid-side tiles around enemy 2x2 at ORBIT_OFFSET."""
    o = _ORBIT_OFFSET
    raw = [
        Position(core.x, core.y - o),  # N
        Position(core.x + 1 + o, core.y),  # E
        Position(core.x, core.y + 1 + o),  # S
        Position(core.x - o, core.y),  # W
    ]
    local_map = bot.hivemind.local_map
    out: list[Position] = []
    for tile in raw:
        if not inBounds(ct, tile):
            continue
        if not walkPassable(ct, tile, local_map=local_map):
            continue
        out.append(tile)
    return out


def _orbitRing(bot, ct: Controller, core: Position) -> list[Position]:
    """Walkable tiles at Chebyshev ORBIT_OFFSET from nearest footprint tile."""
    local_map = bot.hivemind.local_map
    o = _ORBIT_OFFSET
    # Bounding box around footprint expanded by o
    xs = [t.x for t in _coreFootprint(core)]
    ys = [t.y for t in _coreFootprint(core)]
    scored: list[Position] = []
    for y in range(min(ys) - o, max(ys) + o + 1):
        for x in range(min(xs) - o, max(xs) + o + 1):
            tile = Position(x, y)
            if _chebyshevToFootprint(tile, core) != o:
                continue
            if not inBounds(ct, tile):
                continue
            if not walkPassable(ct, tile, local_map=local_map):
                continue
            scored.append(tile)
    scored.sort(key=lambda p: (p.y, p.x))
    return scored


def _orbitJitter(bot, ct: Controller) -> bool:
    """Step to any walkable cardinal neighbor."""
    if ct.get_move_cooldown() != 0:
        return False
    pos = ct.get_position()
    local_map = bot.hivemind.local_map
    for d in (Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST):
        cand = pos.add(d)
        if not inBounds(ct, cand):
            continue
        if ct.is_in_vision(cand) and ct.get_tile_builder_bot_id(cand) is not None:
            continue
        if not walkPassable(ct, cand, local_map=local_map):
            continue
        if ct.can_move(d):
            ct.move(d)
            return True
    return False


def _orbitEnemyCore(bot, ct: Controller, core: Position) -> bool:
    """Cycle mid-side / ring waypoints; jitter if none."""
    waypoints = _orbitMidSide(bot, ct, core)
    if len(waypoints) < 2:
        ring = _orbitRing(bot, ct, core)
        if ring:
            waypoints = ring
    if not waypoints:
        return _orbitJitter(bot, ct)

    idx = getattr(bot, "harassOrbitIdx", 0) % len(waypoints)
    target = waypoints[idx]
    pos = ct.get_position()
    if pos.x == target.x and pos.y == target.y:
        bot.harassOrbitIdx = (idx + 1) % len(waypoints)
        target = waypoints[bot.harassOrbitIdx]
    elif getattr(bot, "stuck", 0) >= 3:
        bot.harassOrbitIdx = (idx + 1) % len(waypoints)
        target = waypoints[bot.harassOrbitIdx]

    return moveToward(bot, ct, target)


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


def _avoidGoal(bot) -> Position | None:
    """Prefer sticky harass target, else enemy core."""
    sticky = getattr(bot, "harassTarget", None)
    if sticky is not None:
        return Position(sticky[0], sticky[1])
    return getattr(bot, "enemyCorePos", None)


def avoidFire(bot, ct: Controller) -> bool:
    """If under enemy gunner LoS, step to a safe tile that still advances toward goal."""
    pos = ct.get_position()
    gunners = _enemyGunners(ct)
    if not gunners:
        return False
    if not _tileUnderGunnerFire(ct, pos, gunners):
        return False

    goal = _avoidGoal(bot)
    goal_d = manhattan(pos, goal) if goal is not None else None
    local_map = bot.hivemind.local_map

    safe: list[tuple[Position, int, int]] = []  # cand, dist_to_goal, step
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
            step = abs(dx) + abs(dy)
            if goal is not None:
                safe.append((cand, manhattan(cand, goal), step))
            else:
                safe.append((cand, step, step))

    if not safe:
        return False

    if goal is not None and goal_d is not None:
        progress = [s for s in safe if s[1] < goal_d]
        pool = progress if progress else safe
        pool.sort(key=lambda s: (s[1], s[2]))
    else:
        # No goal — nearest safe step (legacy)
        pool = sorted(safe, key=lambda s: s[2])

    return moveToward(bot, ct, pool[0][0])


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


def snipeHarvester(bot, ct: Controller) -> bool:
    """Opportunistic: place gun on nearest visible enemy harvester not ally-covered."""
    target = _nearestEnemyOfType(ct, EntityType.HARVESTER)
    if target is None:
        return False
    if allyGunCovering(ct, target):
        return False
    return placeGun(bot, ct, target)


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
    """Approach enemy core; if blocked, orbit mid-side / ring waypoints."""
    core = bot.enemyCorePos
    if core is None:
        return False
    if moveToward(bot, ct, core):
        return True
    return _orbitEnemyCore(bot, ct, core)


def run_raider(bot, ct: Controller) -> None:
    from builder import runHarasser

    runHarasser(bot, ct)


def run_raiders(bot, ct: Controller) -> None:
    from builder import runHarasser

    runHarasser(bot, ct)


def run_attack_builder(bot, ct: Controller) -> None:
    from builder import runHarasser

    runHarasser(bot, ct)
