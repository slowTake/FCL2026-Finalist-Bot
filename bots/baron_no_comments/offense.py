
from __future__ import annotations

from fcode import Controller, Direction, EntityType, Environment, Position

from guns import (
    GUNNER_RANGE_SQ,
    _canAffordGunPlace,
    allyGunCovering,
    gunnerHasLos,
    placeGun,
    placeSentinel,
)
from geometry import (
    BELT_TYPES,
    beltNeighbors,
    chebyshevToCore,
    coreFootprint,
    harvesterClaimedByAlly,
    isAdjacent,
    manhattanToCore,
)
from logistics import moveToward, pathKnownBlocked
from pathing import CARDINALS, inBounds, manhattan, walkPassable
from walls import wallEnemyCore

_AVOID_RADIUS = 4
_ORBIT_OFFSET = 3
_FIRE_TI_COST = 2

_AVOID_FIRE_MEMORY_TURNS = 3

_GOAL_DEFAULT = object()


def harasserRank(bot) -> int:
    idx = bot.hivemind.my_index
    if idx <= 0:
        return 0
    return idx - 1


def _nth(items: list, rank: int):
    if not items:
        return None
    return items[rank % len(items)]


def _orbitMidSide(bot, ct: Controller, core: Position) -> list[Position]:
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
    local_map = bot.hivemind.local_map
    o = _ORBIT_OFFSET
    xs = [t.x for t in coreFootprint(core)]
    ys = [t.y for t in coreFootprint(core)]
    scored: list[Position] = []
    for y in range(min(ys) - o, max(ys) + o + 1):
        for x in range(min(xs) - o, max(xs) + o + 1):
            tile = Position(x, y)
            if chebyshevToCore(tile, core) != o:
                continue
            if not inBounds(ct, tile):
                continue
            if not walkPassable(ct, tile, local_map=local_map):
                continue
            scored.append(tile)
    scored.sort(key=lambda p: (p.y, p.x))
    return scored


def _orbitJitter(bot, ct: Controller) -> bool:
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

    return _harassMove(bot, ct, target)


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
    *,
    cache: dict | None = None,
) -> bool:
    for gpos, facing in gunners:
        if tile.distance_squared(gpos) > GUNNER_RANGE_SQ:
            continue
        if gunnerHasLos(ct, gpos, facing, tile, shooterIsAlly=False, cache=cache):
            return True
        if facing == gpos.direction_to(tile) and tile.distance_squared(gpos) <= GUNNER_RANGE_SQ:
            if ct.can_fire_from(gpos, facing, EntityType.GUNNER, tile):
                return True
    return False


def _avoidGoal(bot) -> Position | None:
    sticky = getattr(bot, "harassTarget", None)
    if sticky is not None:
        return Position(sticky[0], sticky[1])
    return getattr(bot, "enemyCorePos", None)


def _dodgeMemory(bot) -> dict[tuple[int, int], int]:
    mem = getattr(bot, "dodgeBlacklist", None)
    if mem is None:
        bot.dodgeBlacklist = {}
        return bot.dodgeBlacklist
    return mem


def _pruneDodgeMemory(bot, ct: Controller) -> dict[tuple[int, int], int]:
    mem = _dodgeMemory(bot)
    if not mem:
        return mem
    round_n = ct.get_current_round()
    stale = [
        k for k, marked in mem.items() if round_n - marked >= _AVOID_FIRE_MEMORY_TURNS
    ]
    for k in stale:
        del mem[k]
    return mem


def _harassMove(bot, ct: Controller, target: Position) -> bool:
    avoid = _pruneDodgeMemory(bot, ct)
    return moveToward(bot, ct, target, avoid=set(avoid) if avoid else None)


def avoidFire(bot, ct: Controller, *, goal=_GOAL_DEFAULT) -> bool:
    pos = ct.get_position()
    gunners = _enemyGunners(ct)
    if not gunners:
        return False
    cache: dict = {}
    if not _tileUnderGunnerFire(ct, pos, gunners, cache=cache):
        return False

    if goal is _GOAL_DEFAULT:
        goal = _avoidGoal(bot)
    goal_d = manhattan(pos, goal) if goal is not None else None
    local_map = bot.hivemind.local_map
    dodgeMem = _pruneDodgeMemory(bot, ct)

    safe: list[tuple[Position, int, int]] = []  # cand, dist_to_goal, step
    for dy in range(-_AVOID_RADIUS, _AVOID_RADIUS + 1):
        for dx in range(-_AVOID_RADIUS, _AVOID_RADIUS + 1):
            if dx == 0 and dy == 0:
                continue
            cand = Position(pos.x + dx, pos.y + dy)
            if not inBounds(ct, cand):
                continue
            if (cand.x, cand.y) in dodgeMem:
                continue
            if not walkPassable(ct, cand, local_map=local_map):
                continue
            if ct.is_in_vision(cand) and ct.get_tile_builder_bot_id(cand) is not None:
                continue
            if _tileUnderGunnerFire(ct, cand, gunners, cache=cache):
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
        pool = sorted(safe, key=lambda s: s[2])

    dodgeMem[(pos.x, pos.y)] = ct.get_current_round()
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


def _nearestUnclaimedEnemyHarvester(bot, ct: Controller) -> Position | None:
    me = ct.get_team()
    pos = ct.get_position()
    local_map = bot.hivemind.local_map
    best: Position | None = None
    best_d = 10**9
    for eid in ct.get_nearby_entities():
        if ct.get_team(eid) == me:
            continue
        if ct.get_entity_type(eid) != EntityType.HARVESTER:
            continue
        epos = ct.get_position(eid)
        if harvesterClaimedByAlly(ct, epos, local_map=local_map):
            continue
        d = manhattan(pos, epos)
        if d < best_d:
            best_d = d
            best = epos
    return best


def snipeHarvester(bot, ct: Controller) -> bool:
    target = _nearestUnclaimedEnemyHarvester(bot, ct)
    if target is None:
        return False
    if allyGunCovering(ct, target):
        return False
    return placeGun(bot, ct, target)


def _corePushReady(ct: Controller) -> bool:
    from main import CORE_PUSH_MIN_TI

    if CORE_PUSH_MIN_TI is None or CORE_PUSH_MIN_TI <= 250:
        return True
    return ct.get_global_resources() >= CORE_PUSH_MIN_TI


def offGun(bot, ct: Controller) -> bool:
    enemy_core = bot.enemyCorePos
    if enemy_core is not None and _corePushReady(ct):
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
        if etype == EntityType.HARVESTER and (
            allyGunCovering(ct, target)
            or harvesterClaimedByAlly(ct, target, local_map=bot.hivemind.local_map)
        ):
            continue
        if placeGun(bot, ct, target):
            return True
    return False


def wallCore(bot, ct: Controller) -> bool:
    core = bot.enemyCorePos
    if core is None:
        return False
    return wallEnemyCore(ct, core)


def _enemyBeltAt(ct: Controller, tile: Position) -> bool:
    if not ct.is_in_vision(tile):
        return False
    bid = ct.get_tile_building_id(tile)
    if bid is None:
        return False
    if ct.get_team(bid) == ct.get_team():
        return False
    return ct.get_entity_type(bid) in BELT_TYPES


def _oreCloserToAlly(bot, ct: Controller, pad: Position) -> bool:
    if ct.get_tile_env(pad) != Environment.ORE_TITANIUM:
        return False
    ally = bot.corePos
    enemy = bot.enemyCorePos
    if ally is None or enemy is None:
        return False
    return manhattanToCore(pad, ally) <= manhattanToCore(pad, enemy)


def _forwardBarrierPads(bot, ct: Controller) -> list[Position]:
    me = ct.get_team()
    pads: list[Position] = []
    seen: set[tuple[int, int]] = set()
    for eid in ct.get_nearby_entities():
        if ct.get_team(eid) == me:
            continue
        if ct.get_entity_type(eid) not in BELT_TYPES:
            continue
        try:
            facing = ct.get_direction(eid)
        except Exception:
            continue
        if facing == Direction.CENTRE:
            continue
        belt = ct.get_position(eid)
        pad = belt.add(facing)
        key = (pad.x, pad.y)
        if key in seen:
            continue
        seen.add(key)
        if not inBounds(ct, pad):
            continue
        if not ct.is_in_vision(pad):
            continue
        if ct.get_tile_env(pad) == Environment.WALL:
            continue
        if not ct.is_tile_empty(pad):
            continue
        if _oreCloserToAlly(bot, ct, pad):
            continue
        pads.append(pad)
    pads.sort(key=lambda p: manhattan(ct.get_position(), p))
    return _dropSkipped(bot, pads)


def _skippedTargets(bot) -> set[tuple[int, int]]:
    s = getattr(bot, "skippedTargets", None)
    if s is None:
        bot.skippedTargets = set()
        return bot.skippedTargets
    return s


def _dropSkipped(bot, tiles: list[Position]) -> list[Position]:
    skipped = _skippedTargets(bot)
    if not skipped:
        return tiles
    kept = [t for t in tiles if (t.x, t.y) not in skipped]
    return kept if kept else tiles


def breakBeltDeadlock(bot, ct: Controller) -> bool:
    del ct
    from main import HARASS_DEADLOCK_TURNS

    if HARASS_DEADLOCK_TURNS is None or HARASS_DEADLOCK_TURNS <= 0:
        return False
    if getattr(bot, "stuck", 0) < HARASS_DEADLOCK_TURNS:
        return False

    skipped = _skippedTargets(bot)
    dropped = False
    for attr in ("denyBeltTile", "harassTarget", "supplyCutTile"):
        target = getattr(bot, attr, None)
        if target is not None:
            skipped.add(target)
            setattr(bot, attr, None)
            dropped = True
    bot.cachedPath = None
    bot.cachedPathTarget = None
    bot.stuck = 0  # re-arm, so we don't blacklist again next turn
    return dropped


def _wouldSelfEncircle(ct: Controller, pos: Position, pad: Position) -> bool:
    d = pos.direction_to(pad)
    for other in CARDINALS:
        if other == d:
            continue
        if ct.can_move(other):
            return False
    return True


def _tryForwardBarrier(bot, ct: Controller, *, adjacent_only: bool) -> bool:
    pads = _forwardBarrierPads(bot, ct)
    if not pads:
        return False
    pos = ct.get_position()
    if ct.get_action_cooldown() == 0 and _denyBeltCooldownElapsed(bot, ct):
        for pad in pads:
            if not isAdjacent(pos, pad):
                continue
            if _wouldSelfEncircle(ct, pos, pad):
                continue
            if ct.can_build_barrier(pad):
                ct.build_barrier(pad)
                bot.lastDenyBeltRound = ct.get_current_round()
                return True
    if adjacent_only:
        return False
    target = _nth(pads, harasserRank(bot))
    if target is None:
        return False
    return _harassMove(bot, ct, target)


def _enemyBeltsByDist(ct: Controller) -> list[Position]:
    me = ct.get_team()
    pos = ct.get_position()
    belts: list[Position] = []
    for eid in ct.get_nearby_entities():
        if ct.get_team(eid) == me:
            continue
        if ct.get_entity_type(eid) not in BELT_TYPES:
            continue
        belts.append(ct.get_position(eid))
    belts.sort(key=lambda p: (manhattan(pos, p), p.x, p.y))
    return belts


def _stepOff(ct: Controller) -> bool:
    if ct.get_move_cooldown() != 0:
        return False
    for d in CARDINALS:
        if ct.can_move(d):
            ct.move(d)
            return True
    return False


def _cutInProgress(bot, ct: Controller, *, attr: str = "denyBeltTile") -> bool:
    sticky = getattr(bot, attr, None)
    if sticky is None:
        return False
    tile = Position(sticky[0], sticky[1])
    if not ct.is_in_vision(tile):
        return True
    if ct.is_tile_empty(tile):
        return True
    if not _enemyBeltAt(ct, tile):
        return False
    bid = ct.get_tile_building_id(tile)
    if bid is None:
        return False
    try:
        return ct.get_hp(bid) < ct.get_max_hp(bid)
    except Exception:
        return False


def _cutCooldownElapsed(bot, ct: Controller) -> bool:
    from main import DENY_CUT_COOLDOWN

    if DENY_CUT_COOLDOWN is None or DENY_CUT_COOLDOWN <= 0:
        return True
    last = getattr(bot, "lastCutRound", -(10**9))
    return ct.get_current_round() - last >= DENY_CUT_COOLDOWN


def _denyBeltCooldownElapsed(bot, ct: Controller) -> bool:
    from main import DENY_BELT_COOLDOWN

    if DENY_BELT_COOLDOWN is None or DENY_BELT_COOLDOWN <= 0:
        return True
    last = getattr(bot, "lastDenyBeltRound", -(10**9))
    return ct.get_current_round() - last >= DENY_BELT_COOLDOWN


def _enemyBuilderNear(ct: Controller, tile: Position, radius: int) -> bool:
    me = ct.get_team()
    for eid in ct.get_nearby_entities():
        if ct.get_team(eid) == me:
            continue
        if ct.get_entity_type(eid) != EntityType.BUILDER_BOT:
            continue
        epos = ct.get_position(eid)
        if max(abs(epos.x - tile.x), abs(epos.y - tile.y)) <= radius:
            return True
    return False


def _worthwhileCutTarget(bot, ct: Controller) -> Position | None:
    candidate = _nth(_dropSkipped(bot, _enemyBeltsByDist(ct)), harasserRank(bot))
    if candidate is None:
        return None
    if not _canAffordGunPlace(ct):
        return candidate
    from main import UNDEFENDED_BELT_CHEBYSHEV

    radius = UNDEFENDED_BELT_CHEBYSHEV
    if radius and radius > 0 and not _enemyBuilderNear(ct, candidate, radius):
        return candidate
    return None


def _cutAndBarrier(
    bot,
    ct: Controller,
    *,
    attr: str = "denyBeltTile",
    cooldownAttr: str = "lastCutRound",
    pick=None,
) -> bool:
    sticky = getattr(bot, attr, None)
    tile: Position | None = None
    if sticky is not None:
        cand = Position(sticky[0], sticky[1])
        if ct.is_in_vision(cand):
            if _enemyBeltAt(ct, cand) or ct.is_tile_empty(cand):
                tile = cand
            else:
                setattr(bot, attr, None)
        else:
            tile = cand  # keep sticky while fogged / walking in
    if tile is None:
        finder = pick if pick is not None else (
            lambda: _nth(_dropSkipped(bot, _enemyBeltsByDist(ct)), harasserRank(bot))
        )
        tile = finder()
        if tile is None:
            return False
        setattr(bot, attr, (tile.x, tile.y))
        setattr(bot, cooldownAttr, ct.get_current_round())

    pos = ct.get_position()
    adjacent = isAdjacent(pos, tile)

    if ct.is_in_vision(tile) and ct.is_tile_empty(tile):
        if _oreCloserToAlly(bot, ct, tile):
            setattr(bot, attr, None)
            return False
        if pos == tile:
            return _stepOff(ct)  # can't build on our own tile
        if adjacent:
            if ct.get_action_cooldown() == 0 and ct.can_build_barrier(tile):
                ct.build_barrier(tile)
                setattr(bot, attr, None)
                return True
            return False
        return _harassMove(bot, ct, tile)

    if not _enemyBeltAt(ct, tile):
        setattr(bot, attr, None)
        return False

    if ct.get_global_resources() < _FIRE_TI_COST:
        return False

    if adjacent:
        if ct.get_action_cooldown() != 0:
            return False
        if ct.can_fire(tile):
            ct.fire(tile)
            return True
        return _harassMove(bot, ct, tile)

    if pos == tile:
        if ct.get_action_cooldown() != 0:
            return False
        if ct.can_fire(pos):
            ct.fire(pos)
            return True
        return False

    return _harassMove(bot, ct, tile)


def _claimedHarvesterSupplyTargets(bot, ct: Controller) -> list[Position]:
    me = ct.get_team()
    pos = ct.get_position()
    local_map = bot.hivemind.local_map
    out: list[Position] = []
    for (x, y), cell in local_map.items():
        ent = cell.get("entity")
        if not ent or ent.get("type") != EntityType.HARVESTER or ent.get("team") == me:
            continue
        tile = Position(x, y)
        if not harvesterClaimedByAlly(ct, tile, local_map=local_map):
            continue
        for n, belt_ent in beltNeighbors(ct, tile, local_map=local_map):
            if belt_ent.get("team") != me:
                out.append(n)
                break  # one enemy belt per claimed harvester is enough per turn
    out.sort(key=lambda p: manhattan(pos, p))
    return _dropSkipped(bot, out)


def denyHarvesterSupply(bot, ct: Controller) -> bool:
    from main import STEAL_ENEMY_HARVESTERS

    if not STEAL_ENEMY_HARVESTERS:
        return False
    return _cutAndBarrier(
        bot,
        ct,
        attr="supplyCutTile",
        cooldownAttr="lastSupplyCutRound",
        pick=lambda: _nth(_claimedHarvesterSupplyTargets(bot, ct), harasserRank(bot)),
    )


def _denyOreCandidates(bot, ct: Controller) -> list[Position]:
    from main import DENY_ORE_CHEBYSHEV, DENY_ORE_MIN_ALLY_CHEBYSHEV
    from logistics import knownOreTiles, tileThreatenedByGunner

    radius = DENY_ORE_CHEBYSHEV
    if radius is None or radius <= 0:
        return []
    ally, enemy = bot.corePos, getattr(bot, "enemyCorePos", None)
    if ally is None or enemy is None:
        return []
    floor = DENY_ORE_MIN_ALLY_CHEBYSHEV or 0

    pos = ct.get_position()
    scored: list[tuple[int, int, Position]] = []
    for ore in knownOreTiles(bot):
        d_enemy = chebyshevToCore(ore, enemy)
        if d_enemy > radius:
            continue
        if chebyshevToCore(ore, ally) < floor:
            continue
        if manhattanToCore(ore, ally) <= manhattanToCore(ore, enemy):
            continue
        if ct.is_in_vision(ore):
            if ct.get_tile_building_id(ore) is not None:
                continue
        else:
            entry = bot.hivemind.local_map.get((ore.x, ore.y), {})
            if entry.get("entity"):
                continue
        if tileThreatenedByGunner(ct, ore):
            continue
        scored.append((d_enemy, manhattan(pos, ore), ore))
    scored.sort(key=lambda s: (s[0], s[1]))
    return _dropSkipped(bot, [ore for _de, _dm, ore in scored])


def denyOre(bot, ct: Controller) -> bool:
    candidates = _denyOreCandidates(bot, ct)
    if not candidates:
        return False

    pos = ct.get_position()
    if ct.get_action_cooldown() == 0:
        for ore in candidates:
            if not isAdjacent(pos, ore):
                continue
            if _wouldSelfEncircle(ct, pos, ore):
                continue
            if ct.can_build_barrier(ore):
                ct.build_barrier(ore)
                return True

    target = candidates[0]
    bot.harassTarget = (target.x, target.y)
    moved = _harassMove(bot, ct, target)
    if not moved and pathKnownBlocked(bot, target):
        _skippedTargets(bot).add((target.x, target.y))
        bot.harassTarget = None
    return moved


def denyBelt(bot, ct: Controller) -> bool:
    if _tryForwardBarrier(bot, ct, adjacent_only=True):
        return True
    if _cutInProgress(bot, ct):
        if _cutAndBarrier(bot, ct):
            return True
    elif _cutCooldownElapsed(bot, ct):
        target = _worthwhileCutTarget(bot, ct)
        if target is not None and _cutAndBarrier(bot, ct, pick=lambda t=target: t):
            return True
    return _tryForwardBarrier(bot, ct, adjacent_only=False)


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
    return _dropSkipped(bot, found)


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
    sticky = getattr(bot, "harassTarget", None)
    if sticky is not None:
        sx, sy = sticky
        tile = Position(sx, sy)
        if ct.is_in_vision(tile):
            if _enemyHarvesterOnTile(ct, tile):
                pass  # keep sticky on live enemy harvester
            else:
                _markOreScouted(bot, tile)
                bot.harassTarget = None
                sticky = None
        if sticky is not None and _harassMove(bot, ct, tile):
            return True

    for harv in _localEnemyHarvesters(bot, ct):
        bot.harassTarget = (harv.x, harv.y)
        if _harassMove(bot, ct, harv):
            return True

    for ore in _scoutOreTargets(bot, ct):
        if ct.is_in_vision(ore) and not _enemyHarvesterOnTile(ct, ore):
            pos = ct.get_position()
            if abs(pos.x - ore.x) + abs(pos.y - ore.y) <= 1:
                _markOreScouted(bot, ore)
                continue
        bot.harassTarget = (ore.x, ore.y)
        if _harassMove(bot, ct, ore):
            return True
    return False


def seekCore(bot, ct: Controller) -> bool:
    core = bot.enemyCorePos
    if core is None:
        return False
    if _harassMove(bot, ct, core):
        return True
    return _orbitEnemyCore(bot, ct, core)