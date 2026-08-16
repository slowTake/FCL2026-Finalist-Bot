
from __future__ import annotations

from fcode import Controller, Direction, EntityType, Environment, Position

from geometry import (
    BELT_TYPES,
    DEFEND_HOME_CHEBYSHEV,
    TURRET_TYPES,
    chebyshevToCore,
    coreFootprint,
    harvesterClaimedByAlly,
    isAdjacent,
    nearCore,
    tileEntity,
)
from guns import (
    GUNNER_RANGE_SQ,
    allyGunCovering,
    allySentinelCovering,
    placeDefSentinel,
    placeGun,
)
from pathing import CARDINALS, inBounds, manhattan, walkPassable


def _pickDefGunTarget(
    ct: Controller, core_pos: Position | None, max_cheb: int = DEFEND_HOME_CHEBYSHEV
) -> Position | None:
    me = ct.get_team()
    pos = ct.get_position()
    best: Position | None = None
    best_d = 10**9
    for eid in ct.get_nearby_units():
        if ct.get_team(eid) == me:
            continue
        if ct.get_entity_type(eid) not in TURRET_TYPES:
            continue
        epos = ct.get_position(eid)
        if (
            core_pos is not None
            and chebyshevToCore(epos, core_pos) > max_cheb
        ):
            continue
        if allyGunCovering(ct, epos):
            continue  # already answered — don't stack gunners on one threat
        dist = abs(pos.x - epos.x) + abs(pos.y - epos.y)
        if dist < best_d:
            best_d = dist
            best = epos
    return best


def defGun(bot, ct: Controller, *, homeOnly: bool = False) -> bool:
    from logistics import guardianLeash

    if bot.inRelay or getattr(bot, "recheckingOre", False):
        return False
    leash = guardianLeash(ct)
    if not nearCore(bot, ct, leash):
        return False
    target = _pickDefGunTarget(ct, bot.corePos if homeOnly else None, leash)
    if target is None:
        return False
    return placeGun(bot, ct, target)


def _gunnerFiringAtCore(
    ct: Controller, core: Position
) -> tuple[Position, Direction, Position] | None:
    me = ct.get_team()
    pos = ct.get_position()
    best: tuple[Position, Direction, Position] | None = None
    best_d = 10**9
    for eid in ct.get_nearby_units():
        if ct.get_team(eid) == me:
            continue
        if ct.get_entity_type(eid) != EntityType.GUNNER:
            continue
        gpos = ct.get_position(eid)
        facing = ct.get_direction(eid)
        for tile in coreFootprint(core):
            if gpos.distance_squared(tile) > GUNNER_RANGE_SQ:
                continue
            if not ct.can_fire_from(gpos, facing, EntityType.GUNNER, tile):
                continue
            d = abs(pos.x - gpos.x) + abs(pos.y - gpos.y)
            if d < best_d:
                best_d = d
                best = (gpos, facing, tile)
    return best


def _gunLineBlocked(bot, ct: Controller, tile: Position, core: Position) -> bool:
    for foot in coreFootprint(core):
        if tile.x == foot.x and tile.y == foot.y:
            return True
    ent = tileEntity(ct, tile, local_map=bot.hivemind.local_map)
    if not ent or ent.get("team") != ct.get_team():
        return False
    return ent.get("type") == EntityType.BARRIER


def _placeGunLineBarrier(ct: Controller, tile: Position) -> bool:
    if not ct.is_in_vision(tile):
        return False
    if ct.get_tile_building_id(tile) is not None:
        return False
    if ct.get_action_cooldown() != 0:
        return False
    if ct.can_build_barrier(tile):
        ct.build_barrier(tile)
        return True
    return False


def blockGunLine(bot, ct: Controller) -> bool:
    core = bot.corePos
    if core is None or bot.inRelay or getattr(bot, "recheckingOre", False):
        return False
    hit = _gunnerFiringAtCore(ct, core)
    if hit is None:
        return False
    gpos, facing, core_tile = hit
    del gpos
    block = core_tile.add(facing.opposite())
    if not inBounds(ct, block):
        return False
    if _gunLineBlocked(bot, ct, block, core):
        return False  # already blocked
    if not _layableTile(ct, block):
        return False
    if _placeGunLineBarrier(ct, block):
        bot.gunLineBlockTile = (block.x, block.y)
        return True
    return False


def maintainGunLineBlock(bot, ct: Controller) -> bool:
    core = bot.corePos
    tile_xy = getattr(bot, "gunLineBlockTile", None)
    if core is None or tile_xy is None:
        return False
    if bot.inRelay or getattr(bot, "recheckingOre", False):
        return False
    tile = Position(tile_xy[0], tile_xy[1])
    if _gunLineBlocked(bot, ct, tile, core):
        return False  # still standing, nothing to do
    if not ct.is_in_vision(tile):
        return False  # can't confirm or act — wait for vision, don't chase it
    if not _layableTile(ct, tile):
        bot.gunLineBlockTile = None  # tile is occupied by something else now
        return False
    return _placeGunLineBarrier(ct, tile)


_TURRET_PRIORITY = {
    EntityType.SENTINEL: 0,  # reach sqrt(32) ≈ 5.7 — shells the core from 5–6
    EntityType.LAUNCHER: 1,  # reach sqrt(26) ≈ 5.1
    EntityType.GUNNER: 2,  # reach sqrt(13) ≈ 3.6 — defGun already covers these
}


def threateningTurret(bot, ct: Controller) -> Position | None:
    from main import TURRET_MEMORY_TURNS, TURRET_THREAT_CHEBYSHEV

    core = bot.corePos
    if core is None or not TURRET_THREAT_CHEBYSHEV:
        return None
    hm = bot.hivemind
    me = ct.get_team()
    memory = TURRET_MEMORY_TURNS or 0
    best: Position | None = None
    best_key = None
    for (x, y), cell in hm.local_map.items():
        ent = cell.get("entity")
        if not ent or ent.get("team") == me:
            continue
        etype = ent.get("type")
        if etype not in TURRET_TYPES:
            continue
        if memory > 0:
            age = hm.global_turn - cell.get("last_seen_turn", -(10**9))
            if age > memory:
                continue
        epos = Position(x, y)
        d = chebyshevToCore(epos, core)
        if d > TURRET_THREAT_CHEBYSHEV:
            continue
        key = (_TURRET_PRIORITY.get(etype, 3), d, x, y)
        if best_key is None or key < best_key:
            best_key = key
            best = epos
    return best


def defSentinel(bot, ct: Controller) -> bool:
    from main import DEF_SENTINEL

    if not DEF_SENTINEL or bot.corePos is None:
        return False
    from logistics import guardianLeash

    if bot.inRelay or getattr(bot, "recheckingOre", False):
        return False
    leash = guardianLeash(ct)
    if not nearCore(bot, ct, leash):
        return False
    target = threateningTurret(bot, ct)
    if target is None:
        return False
    if allySentinelCovering(ct, target) or allyGunCovering(ct, target):
        return False  # already answered — don't stack turrets on one threat
    return placeDefSentinel(bot, ct, target, bot.corePos, leash)


def clearBarriers(bot, ct: Controller) -> bool:
    from logistics import guardianLeash

    me = ct.get_team()
    pos = ct.get_position()
    core = bot.corePos
    leash = guardianLeash(ct)
    best: Position | None = None
    best_d = 10**9
    for eid in ct.get_nearby_entities():
        if ct.get_team(eid) == me:
            continue
        if ct.get_entity_type(eid) != EntityType.BARRIER:
            continue
        epos = ct.get_position(eid)
        if core is not None and chebyshevToCore(epos, core) > leash:
            continue
        dist = abs(pos.x - epos.x) + abs(pos.y - epos.y)
        if dist < best_d:
            best_d = dist
            best = epos
    if best is None:
        return False

    if isAdjacent(pos, best):
        if ct.get_action_cooldown() == 0 and ct.can_fire(best):
            if ct.get_tile_env(best) == Environment.ORE_TITANIUM:
                bot.guardianOreBarrier = best
            ct.fire(best)
            return True
        return False

    from logistics import moveToward

    return moveToward(bot, ct, best)


def _buildHarvesterOnClearedBarrier(bot, ct: Controller) -> bool:
    tile = getattr(bot, "guardianOreBarrier", None)
    if tile is None:
        return False

    if not ct.is_in_vision(tile):
        return False

    if ct.get_tile_env(tile) != Environment.ORE_TITANIUM:
        bot.guardianOreBarrier = None
        bot.guardianOreWaitTurns = 0
        return False

    bid = ct.get_tile_building_id(tile)
    if bid is not None:
        return False

    if not isAdjacent(ct.get_position(), tile):
        return False

    from main import GUARDIAN_ORE_WAIT_TURNS

    if ct.get_global_resources() < ct.get_harvester_cost():
        bot.guardianOreWaitTurns = getattr(bot, "guardianOreWaitTurns", 0) + 1
        if bot.guardianOreWaitTurns > GUARDIAN_ORE_WAIT_TURNS:
            bot.guardianOreBarrier = None
            bot.guardianOreWaitTurns = 0
            return False
        return True  # hold position; Ti may still arrive next turn

    if ct.get_action_cooldown() == 0 and ct.can_build_harvester(tile):
        ct.build_harvester(tile)
        bot.guardianOreBarrier = None
        bot.guardianOreWaitTurns = 0
        return True
    return False


def _isBeltOrCore(bot, ct: Controller, tile: Position, core: Position) -> bool:
    for foot in coreFootprint(core):
        if tile.x == foot.x and tile.y == foot.y:
            return True
    ent = tileEntity(ct, tile, local_map=bot.hivemind.local_map)
    if not ent or ent.get("team") != ct.get_team():
        return False
    return ent.get("type") in BELT_TYPES


_RELAY_OWNER_CHEBYSHEV = 2


def _layableTile(ct: Controller, tile: Position) -> bool:
    if not ct.is_in_vision(tile):
        return False
    if ct.get_tile_building_id(tile) is not None:
        return False
    return ct.get_tile_env(tile) == Environment.EMPTY


def _allyBuilderNear(ct: Controller, tile: Position) -> bool:
    me = ct.get_team()
    self_id = ct.get_id()
    for eid in ct.get_nearby_units():
        if eid == self_id or ct.get_team(eid) != me:
            continue
        if ct.get_entity_type(eid) != EntityType.BUILDER_BOT:
            continue
        p = ct.get_position(eid)
        if max(abs(p.x - tile.x), abs(p.y - tile.y)) <= _RELAY_OWNER_CHEBYSHEV:
            return True
    return False


def _danglingBeltEnds(
    bot,
    ct: Controller,
    *,
    anchor: Position | None = None,
    max_dist: int | None = None,
    respect_owner: bool = True,
    include_harvesters: bool = True,
) -> list[tuple[Position, Position]]:
    core = bot.corePos
    if core is None:
        return []
    if anchor is None:
        anchor = core
    if max_dist is None:
        max_dist = DEFEND_HOME_CHEBYSHEV
    me = ct.get_team()
    pos = ct.get_position()
    out: list[tuple[int, int, Position, Position]] = []
    for (x, y), cell in bot.hivemind.local_map.items():
        ent = cell.get("entity")
        if not ent or ent.get("team") != me:
            continue
        if ent.get("type") != EntityType.CONVEYOR:
            continue
        facing = ent.get("direction")
        if facing is None or facing == Direction.CENTRE:
            continue
        belt = Position(x, y)
        if max(abs(belt.x - anchor.x), abs(belt.y - anchor.y)) > max_dist:
            continue
        target = belt.add(facing)
        if not inBounds(ct, target):
            continue
        if _isBeltOrCore(bot, ct, target, core):
            continue
        if not _layableTile(ct, target):
            continue
        if respect_owner and _allyBuilderNear(ct, target):
            continue
        out.append((chebyshevToCore(belt, core), manhattan(pos, target), belt, target))

    if include_harvesters:
        for (x, y), cell in bot.hivemind.local_map.items():
            ent = cell.get("entity")
            if not ent or ent.get("team") != me:
                continue
            if ent.get("type") != EntityType.HARVESTER:
                continue
            harv = Position(x, y)
            if max(abs(harv.x - anchor.x), abs(harv.y - anchor.y)) > max_dist:
                continue
            if harvesterClaimedByAlly(ct, harv, local_map=bot.hivemind.local_map):
                continue
            for d in CARDINALS:
                target = harv.add(d)
                if not inBounds(ct, target):
                    continue
                if _isBeltOrCore(bot, ct, target, core):
                    continue
                if not _layableTile(ct, target):
                    continue
                if respect_owner and _allyBuilderNear(ct, target):
                    continue
                out.append(
                    (chebyshevToCore(harv, core), manhattan(pos, target), harv, target)
                )

    out.sort(key=lambda t: (t[0], t[1]))
    return [(source, target) for _d, _m, source, target in out]


def _reconnectFacing(
    bot, ct: Controller, target: Position, core: Position
) -> Direction | None:
    from logistics import _relayReachesCore, travelFacing

    footprint = coreFootprint(core)
    here = chebyshevToCore(target, core)

    for d in CARDINALS:
        near = target.add(d)
        if not inBounds(ct, near):
            continue
        if any(near.x == f.x and near.y == f.y for f in footprint):
            return travelFacing(target, near)
        if chebyshevToCore(near, core) < here and _isBeltOrCore(bot, ct, near, core):
            return travelFacing(target, near)
        if _isBeltOrCore(bot, ct, near, core) and _relayReachesCore(
            bot, ct, near, core
        ):
            return travelFacing(target, near)

    for d in CARDINALS:
        near = target.add(d)
        far = near.add(d)
        if not inBounds(ct, near) or not inBounds(ct, far):
            continue
        if chebyshevToCore(far, core) >= here:
            continue
        if not _layableTile(ct, near):
            continue
        if _isBeltOrCore(bot, ct, far, core):
            return travelFacing(target, near)

    return None


def _tryPatchDanglingBelts(
    bot,
    ct: Controller,
    candidates: list[tuple[Position, Position]],
    core: Position,
    *,
    allow_walk: bool = True,
) -> bool:
    from logistics import moveToward, placeRelayBelt

    actionable: list[tuple[Position, Direction]] = []
    for _belt, target in candidates:
        facing = _reconnectFacing(bot, ct, target, core)
        if facing is None:
            continue
        actionable.append((target, facing))
    if not actionable:
        return False

    pos = ct.get_position()
    for target, facing in actionable:
        if not isAdjacent(pos, target):
            continue
        if placeRelayBelt(ct, target, facing) == "placed":
            return True

    if not allow_walk:
        return False
    return moveToward(bot, ct, actionable[0][0])


def _adjacentToCoreFootprint(core: Position, tile: Position) -> bool:
    return any(isAdjacent(tile, foot) for foot in coreFootprint(core))


def patchAdjacentCoreBelt(bot, ct: Controller) -> bool:
    core = bot.corePos
    if core is None or bot.inRelay or getattr(bot, "recheckingOre", False):
        return False
    pos = ct.get_position()
    from logistics import placeRelayBelt

    for _belt, target in _danglingBeltEnds(bot, ct, anchor=core, max_dist=2):
        if not isAdjacent(pos, target):
            continue
        if not _adjacentToCoreFootprint(core, target):
            continue
        facing = _reconnectFacing(bot, ct, target, core)
        if facing is None:
            continue
        if placeRelayBelt(ct, target, facing) == "placed":
            return True
    return False


def reconnectBelts(bot, ct: Controller, *, allow_walk: bool = True) -> bool:
    core = bot.corePos
    if core is None or bot.inRelay or getattr(bot, "recheckingOre", False):
        return False

    from logistics import guardianLeash

    candidates = _danglingBeltEnds(bot, ct, max_dist=guardianLeash(ct))
    if not candidates:
        return False
    return _tryPatchDanglingBelts(bot, ct, candidates, core, allow_walk=allow_walk)


def finishNearbyBelt(bot, ct: Controller, *, allow_walk: bool = True) -> bool:
    from main import DANGLING_BELT_SELF_CHEBYSHEV

    core = bot.corePos
    if core is None or bot.inRelay or getattr(bot, "recheckingOre", False):
        return False

    candidates = _danglingBeltEnds(
        bot,
        ct,
        anchor=ct.get_position(),
        max_dist=DANGLING_BELT_SELF_CHEBYSHEV,
        respect_owner=False,
    )
    if not candidates:
        return False
    return _tryPatchDanglingBelts(bot, ct, candidates, core, allow_walk=allow_walk)


def _patrolWaypoints(bot, ct: Controller) -> list[Position]:
    core = bot.corePos
    if core is None:
        return []
    from main import GUARDIAN_PATROL_OFFSET

    o = GUARDIAN_PATROL_OFFSET
    local_map = bot.hivemind.local_map
    claimed: set[tuple[int, int]] = set()

    def usable(tile: Position) -> bool:
        key = (tile.x, tile.y)
        if key in claimed:
            return False
        if not inBounds(ct, tile):
            return False
        return walkPassable(ct, tile, local_map=local_map)

    sides = [
        (
            Position(core.x, core.y - o),  # N
            [Position(core.x - o, core.y - o), Position(core.x + 1 + o, core.y - o)],
        ),
        (
            Position(core.x + 1 + o, core.y),  # E
            [Position(core.x + 1 + o, core.y - o), Position(core.x + 1 + o, core.y + 1 + o)],
        ),
        (
            Position(core.x, core.y + 1 + o),  # S
            [Position(core.x - o, core.y + 1 + o), Position(core.x + 1 + o, core.y + 1 + o)],
        ),
        (
            Position(core.x - o, core.y),  # W
            [Position(core.x - o, core.y - o), Position(core.x - o, core.y + 1 + o)],
        ),
    ]
    out: list[Position] = []
    for primary, fallbacks in sides:
        if usable(primary):
            out.append(primary)
            claimed.add((primary.x, primary.y))
            continue
        for alt in fallbacks:
            if usable(alt):
                out.append(alt)
                claimed.add((alt.x, alt.y))
                break
    return out


def patrolHome(bot, ct: Controller) -> bool:
    if bot.corePos is None:
        return False
    waypoints = _patrolWaypoints(bot, ct)
    if not waypoints:
        return False

    idx = getattr(bot, "patrolIdx", 0) % len(waypoints)
    target = waypoints[idx]
    pos = ct.get_position()
    if pos.x == target.x and pos.y == target.y:
        bot.patrolIdx = (idx + 1) % len(waypoints)
        target = waypoints[bot.patrolIdx]
    elif getattr(bot, "stuck", 0) >= 3:
        bot.patrolIdx = (idx + 1) % len(waypoints)
        target = waypoints[bot.patrolIdx]

    from logistics import moveToward

    return moveToward(bot, ct, target)