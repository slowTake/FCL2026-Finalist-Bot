"""Default builder logistics: harvesters, inbound relay, heal."""

from __future__ import annotations

from fcode import Controller, Direction, EntityType, Environment, Position

from pathing import (
    CARDINALS,
    astarFromSeeds,
    findPathAroundObstruction,
    inBounds,
    manhattan,
    nearestCardinal,
    relayPassable,
    walkPassable,
)

PATH_MAX_ITERS = 800

_ORE_BLOCK_CLEAR = (
    EntityType.GUNNER,
    EntityType.SENTINEL,
    EntityType.CONVEYOR,
    EntityType.SPLITTER,
    EntityType.LAUNCHER,
    EntityType.BARRIER,
)


def _coreFootprint(core_pos: Position) -> list[Position]:
    return [
        core_pos,
        Position(core_pos.x + 1, core_pos.y),
        Position(core_pos.x, core_pos.y + 1),
        Position(core_pos.x + 1, core_pos.y + 1),
    ]


def clearRelay(bot) -> None:
    bot.inRelay = False
    bot.relayOrigin = None
    bot.relayPath = None
    bot.relayBuiltTo = -1
    bot.relayStuck = 0


def abortRelay(bot) -> None:
    clearRelay(bot)


def relayEverComplete(ct: Controller) -> bool:
    """True after the team has finished at least one inbound relay (slot 0)."""
    from main import SLOT_RELAY_COMPLETE

    return ct.read_store(SLOT_RELAY_COMPLETE) != 0


def enemyTurretInVision(ct: Controller) -> bool:
    """True if any enemy gunner, sentinel, or launcher is in vision."""
    me = ct.get_team()
    for eid in ct.get_nearby_entities():
        if ct.get_team(eid) == me:
            continue
        if ct.get_entity_type(eid) in (
            EntityType.GUNNER,
            EntityType.SENTINEL,
            EntityType.LAUNCHER,
        ):
            return True
    return False


def nearestEnemyTurret(ct: Controller) -> Position | None:
    """Nearest enemy gunner/sentinel/launcher position in vision, else None."""
    me = ct.get_team()
    pos = ct.get_position()
    best: Position | None = None
    best_d = 10**9
    for eid in ct.get_nearby_entities():
        if ct.get_team(eid) == me:
            continue
        if ct.get_entity_type(eid) not in (
            EntityType.GUNNER,
            EntityType.SENTINEL,
            EntityType.LAUNCHER,
        ):
            continue
        epos = ct.get_position(eid)
        d = manhattan(pos, epos)
        if d < best_d:
            best_d = d
            best = epos
    return best


def contestTurret(bot, ct: Controller) -> bool:
    """Place a gunner toward the nearest enemy turret in vision."""
    target = nearestEnemyTurret(ct)
    if target is None:
        return False
    from guns import placeGun

    return placeGun(bot, ct, target)


def markRelayComplete(ct: Controller) -> None:
    from main import SLOT_RELAY_COMPLETE

    ct.write_store(SLOT_RELAY_COMPLETE, 1)


def isCoreAdjacent(
    ct: Controller, pos: Position, core_pos: Position | None
) -> bool:
    """True if pos is adjacent to an *ally* core (vision check or ally core_pos)."""
    for d in CARDINALS:
        neighbor = pos.add(d)
        if not inBounds(ct, neighbor):
            continue
        if ct.is_in_vision(neighbor):
            bid = ct.get_tile_building_id(neighbor)
            if (
                bid is not None
                and ct.get_entity_type(bid) == EntityType.CORE
                and ct.get_team(bid) == ct.get_team()
            ):
                return True
    if core_pos is None:
        return False
    for tile in _coreFootprint(core_pos):
        if abs(pos.x - tile.x) + abs(pos.y - tile.y) == 1:
            return True
    return False


def _allyCorePos(bot, ct: Controller) -> Position | None:
    """Refresh ally core from packed store; never use enemy core."""
    from hivemind import CoreState, unpack_cores
    from main import SLOT_PACKED_CORES

    ally, _enemy = unpack_cores(ct.read_store(SLOT_PACKED_CORES))
    if ally[0] != CoreState.UNKNOWN:
        bot.corePos = Position(ally[1], ally[2])
        return bot.corePos
    return bot.corePos


def _isEnemyCoreAdjacent(
    ct: Controller, tile: Position, enemy_core: Position | None
) -> bool:
    """True if tile is adjacent to a known/visible enemy core footprint."""
    me = ct.get_team()
    for d in CARDINALS:
        neighbor = tile.add(d)
        if not inBounds(ct, neighbor):
            continue
        if not ct.is_in_vision(neighbor):
            continue
        bid = ct.get_tile_building_id(neighbor)
        if (
            bid is not None
            and ct.get_entity_type(bid) == EntityType.CORE
            and ct.get_team(bid) != me
        ):
            return True
    if enemy_core is None:
        return False
    for ctile in _coreFootprint(enemy_core):
        if abs(tile.x - ctile.x) + abs(tile.y - ctile.y) == 1:
            return True
    return False


def travelFacing(from_pos: Position, to_pos: Position) -> Direction:
    return nearestCardinal(from_pos.direction_to(to_pos))


def facingIntoCore(pos: Position, core_pos: Position) -> Direction:
    best: Direction | None = None
    best_dist = 10**9
    for tile in _coreFootprint(core_pos):
        dist = abs(pos.x - tile.x) + abs(pos.y - tile.y)
        if dist != 1:
            continue
        if dist < best_dist:
            best_dist = dist
            best = travelFacing(pos, tile)
    if best is not None and best != Direction.CENTRE:
        return best
    return nearestCardinal(pos.direction_to(core_pos))


def moveToward(bot, ct: Controller, target: Position) -> bool:
    if ct.get_move_cooldown() != 0:
        return False
    pos = ct.get_position()
    if pos == target:
        return False
    local_map = bot.hivemind.local_map

    def ok(p: Position) -> bool:
        if p == pos:
            return True
        if not inBounds(ct, p):
            return False
        if ct.is_in_vision(p) and ct.get_tile_builder_bot_id(p) is not None:
            return False
        return walkPassable(ct, p, local_map=local_map)

    path = None
    cached = getattr(bot, "cachedPath", None)
    cached_t = getattr(bot, "cachedPathTarget", None)
    if (
        cached
        and cached_t == target
        and len(cached) > 1
        and cached[0] == pos
    ):
        path = cached
    else:
        path = astarFromSeeds(
            [pos],
            is_passable=ok,
            is_goal=lambda p: p == target,
            heuristic=lambda p: manhattan(p, target),
            max_iters=PATH_MAX_ITERS,
        )
        bot.cachedPath = path
        bot.cachedPathTarget = target

    if not path or len(path) < 2:
        step = nearestCardinal(pos.direction_to(target))
        if step != Direction.CENTRE and ct.can_move(step):
            ct.move(step)
            return True
        return False

    nxt = path[1]
    step = nearestCardinal(pos.direction_to(nxt))
    if step != Direction.CENTRE and ct.can_move(step):
        ct.move(step)
        bot.cachedPath = path[1:]
        return True
    bot.cachedPath = None
    return False


# ------------------------------------------------------------------
# Ore helpers
# ------------------------------------------------------------------


def knownOreTiles(bot) -> list[Position]:
    ores: list[Position] = []
    local_map = bot.hivemind.local_map
    for (x, y), cell in local_map.items():
        if cell.get("ore"):
            ores.append(Position(x, y))
    if ores:
        return ores
    from features import ORE_LOCATIONS

    map_id = bot.hivemind.map_id
    for ox, oy in ORE_LOCATIONS.get(map_id, []):
        ores.append(Position(ox, oy))
    return ores


def _allowOreSite(bot, ore: Position) -> bool:
    """During home-guard, only ore within DEFEND_HOME_CHEBYSHEV of ally core."""
    if not getattr(bot, "nearCoreEconomy", False):
        return True
    if bot.corePos is None:
        return True
    return _chebyshevToCore(ore, bot.corePos) <= DEFEND_HOME_CHEBYSHEV


def _selfNearOre(ct: Controller, ore: Position) -> bool:
    """True if this unit is on ore or cardinally adjacent."""
    pos = ct.get_position()
    return abs(pos.x - ore.x) + abs(pos.y - ore.y) <= 1


def allyBuilderNearOre(ct: Controller, ore: Position) -> bool:
    """True if a visible ally builder (not self) is on ore or a cardinal neighbor."""
    me = ct.get_team()
    self_id = ct.get_id()
    tiles = [ore]
    for d in CARDINALS:
        tiles.append(ore.add(d))
    for tile in tiles:
        if not inBounds(ct, tile):
            continue
        if not ct.is_in_vision(tile):
            continue
        bid = ct.get_tile_builder_bot_id(tile)
        if bid is None or bid == self_id:
            continue
        if ct.get_team(bid) == me:
            return True
    return False


def allyBuilderOnOre(ct: Controller, ore: Position) -> bool:
    """True if a visible ally builder (not self) is standing on the ore tile."""
    if not ct.is_in_vision(ore):
        return False
    bid = ct.get_tile_builder_bot_id(ore)
    if bid is None or bid == ct.get_id():
        return False
    return ct.get_team(bid) == ct.get_team()


def _defaultOreRank(bot) -> int:
    """Stable rank among economy defaults (idx % 5 == 3): 3→0, 8→1, 13→2, …"""
    idx = bot.hivemind.my_index
    if idx <= 0 or idx % 5 != 3:
        return 0
    return (idx - 3) // 5


def _oreStaggerKey(ore: Position, core: Position | None) -> tuple:
    """Team-stable sort: closer to core first, then x/y."""
    if core is not None:
        return (manhattan(ore, core), ore.x, ore.y)
    return (ore.x, ore.y)


def _oreConvenienceKey(
    ore: Position, core: Position | None, self_pos: Position
) -> tuple:
    """Near core (defendable), then near builder (reachable), then x/y."""
    if core is not None:
        return (manhattan(ore, core), manhattan(ore, self_pos), ore.x, ore.y)
    return (manhattan(ore, self_pos), ore.x, ore.y)


def _clearStickyIfAllyClaimed(bot, ct: Controller) -> None:
    """Drop sticky empty ore if another ally already sits on the pad."""
    site = bot.oreSite
    if site is None:
        return
    if not ct.is_in_vision(site):
        return
    if ct.get_tile_building_id(site) is not None:
        return
    if allyBuilderNearOre(ct, site) and not _selfNearOre(ct, site):
        bot.oreSite = None


def _stickyEmptyTarget(bot, ct: Controller) -> Position | None:
    """Keep committed empty oreSite if still valid; else clear and return None."""
    _clearStickyIfAllyClaimed(bot, ct)
    site = bot.oreSite
    if site is None:
        return None
    if not _allowOreSite(bot, site):
        bot.oreSite = None
        return None
    if oreThreatenedByGunner(ct, site):
        bot.oreSite = None
        return None
    if ct.is_in_vision(site) and ct.get_tile_building_id(site) is not None:
        bot.oreSite = None
        return None
    return site


def pickEmptyOre(bot, ct: Controller) -> Position | None:
    """Pick empty ore: convenience or index-stagger; skip ally-on-tile pads."""
    from main import ORE_STAGGER

    core = bot.corePos
    self_pos = ct.get_position()

    seen: set[tuple[int, int]] = set()
    raw: list[Position] = []

    def add(ore: Position) -> None:
        key = (ore.x, ore.y)
        if key in seen:
            return
        seen.add(key)
        raw.append(ore)

    for tile in ct.get_nearby_tiles():
        if ct.get_tile_env(tile) != Environment.ORE_TITANIUM:
            continue
        if ct.get_tile_building_id(tile) is not None:
            continue
        add(tile)

    for ore in knownOreTiles(bot):
        entry = bot.hivemind.local_map.get((ore.x, ore.y), {})
        ent = entry.get("entity")
        if ent and ent.get("type") == EntityType.HARVESTER:
            continue
        if ct.is_in_vision(ore) and ct.get_tile_building_id(ore) is not None:
            continue
        add(ore)

    eligible: list[Position] = []
    for ore in raw:
        if not _allowOreSite(bot, ore):
            continue
        if oreThreatenedByGunner(ct, ore):
            continue
        eligible.append(ore)

    if not eligible:
        return None

    # Honour the deadlock/recheck blacklist, but fall back if it empties the pool
    skipped = getattr(bot, "skippedOres", set())
    unskipped = [o for o in eligible if (o.x, o.y) not in skipped]
    if unskipped:
        eligible = unskipped

    # On-tile claim only — adjacent would thrash as builders approach neighboring pads
    free = [o for o in eligible if not allyBuilderOnOre(ct, o)]
    pool = free if free else eligible

    if not ORE_STAGGER:
        pool.sort(key=lambda o: _oreConvenienceKey(o, core, self_pos))
        return pool[0]

    pool.sort(key=lambda o: _oreStaggerKey(o, core))
    rank = _defaultOreRank(bot)
    return pool[rank % len(pool)]


def oreThreatenedByGunner(ct: Controller, tile: Position) -> bool:
    """True if a visible enemy gunner is currently aimed at tile with LoS."""
    from guns import GUNNER_RANGE_SQ, gunnerHasLos

    me = ct.get_team()
    for eid in ct.get_nearby_entities():
        if ct.get_team(eid) == me:
            continue
        if ct.get_entity_type(eid) != EntityType.GUNNER:
            continue
        gpos = ct.get_position(eid)
        if tile.distance_squared(gpos) > GUNNER_RANGE_SQ:
            continue
        facing = ct.get_direction(eid)
        # Their gunner, their ray: their own buildings shield the tile, ours get chewed.
        if gunnerHasLos(ct, gpos, facing, tile, shooterIsAlly=False):
            return True
        if ct.can_fire_from(gpos, facing, EntityType.GUNNER, tile):
            return True
    return False


def _allyOreBlock(ct: Controller, tile: Position) -> EntityType | None:
    """Ally _ORE_BLOCK_CLEAR type on tile, else None."""
    if not ct.is_in_vision(tile):
        return None
    bid = ct.get_tile_building_id(tile)
    if bid is None:
        return None
    if ct.get_team(bid) != ct.get_team():
        return None
    etype = ct.get_entity_type(bid)
    if etype not in _ORE_BLOCK_CLEAR:
        return None
    return etype


def _shouldClearAllyOreBlock(ct: Controller, tile: Position) -> bool:
    """Clear ally ore blockers unless gunner/sentinel and that ore is threatened."""
    etype = _allyOreBlock(ct, tile)
    if etype is None:
        return False
    if etype in (EntityType.GUNNER, EntityType.SENTINEL) and oreThreatenedByGunner(
        ct, tile
    ):
        return False
    return True


def _approachOreBlock(bot, ct: Controller, tile: Position) -> bool:
    """Move to a walkable cardinal neighbor of blocked ore (not onto the blocker)."""
    pos = ct.get_position()
    if abs(pos.x - tile.x) + abs(pos.y - tile.y) == 1:
        return False
    local_map = bot.hivemind.local_map
    best: Position | None = None
    best_d = 10**9
    for d in CARDINALS:
        cand = tile.add(d)
        if not inBounds(ct, cand):
            continue
        if not walkPassable(ct, cand, local_map=local_map):
            continue
        if ct.is_in_vision(cand) and ct.get_tile_builder_bot_id(cand) is not None:
            continue
        dist = manhattan(pos, cand)
        if dist < best_d:
            best_d = dist
            best = cand
    if best is None:
        return moveToward(bot, ct, tile)
    return moveToward(bot, ct, best)


def pickMaintainOre(bot, ct: Controller) -> Position | None:
    """Prefer sticky site, else staggered empty or nearest harvester/block."""
    _clearStickyIfAllyClaimed(bot, ct)
    if bot.oreSite is not None:
        if _allowOreSite(bot, bot.oreSite):
            return bot.oreSite
        bot.oreSite = None

    pos = ct.get_position()
    core = bot.corePos
    best: Position | None = None
    best_key = None

    def score(ore: Position) -> tuple:
        if core is not None:
            return (manhattan(ore, core), manhattan(ore, pos))
        return (manhattan(ore, pos),)

    def consider(ore: Position) -> None:
        nonlocal best, best_key
        if not _allowOreSite(bot, ore):
            return
        key = score(ore)
        if best_key is None or key < best_key:
            best_key = key
            best = ore

    for ore in knownOreTiles(bot):
        if ct.is_in_vision(ore):
            bid = ct.get_tile_building_id(ore)
            if bid is None:
                continue  # empties via pickEmptyOre
            if ct.get_entity_type(bid) == EntityType.HARVESTER:
                consider(ore)
            elif _shouldClearAllyOreBlock(ct, ore):
                consider(ore)
        else:
            entry = bot.hivemind.local_map.get((ore.x, ore.y), {})
            ent = entry.get("entity")
            if ent and ent.get("type") == EntityType.HARVESTER:
                consider(ore)
            elif ent:
                # Fog: possible ally block / other — keep old consider behavior
                consider(ore)

    for tile in ct.get_nearby_tiles():
        if ct.get_tile_env(tile) != Environment.ORE_TITANIUM:
            continue
        bid = ct.get_tile_building_id(tile)
        if bid is None:
            continue
        if ct.get_entity_type(bid) == EntityType.HARVESTER:
            consider(tile)
        elif _shouldClearAllyOreBlock(ct, tile):
            consider(tile)

    empty = pickEmptyOre(bot, ct)
    if empty is None:
        return best
    if best is None:
        return empty
    # Closer work wins; empties are already index-staggered among themselves
    if score(empty) <= score(best):
        return empty
    return best


def placeGunnerOnOre(bot, ct: Controller, tile: Position) -> bool:
    """Stub — gun placement deferred."""
    del bot, ct, tile
    return False


# ------------------------------------------------------------------
# Relay helpers
# ------------------------------------------------------------------


def neededBeltFacing(
    path: list[Position], idx: int, core_pos: Position
) -> Direction:
    """Facing for path[idx]: toward next hop, or into core on last tile."""
    if idx >= len(path) - 1:
        return facingIntoCore(path[idx], core_pos)
    return travelFacing(path[idx], path[idx + 1])


def beltFacingOk(ct: Controller, pos: Position, needed: Direction) -> bool:
    if not ct.is_in_vision(pos):
        return False
    bid = ct.get_tile_building_id(pos)
    if bid is None:
        return False
    if ct.get_entity_type(bid) not in (EntityType.CONVEYOR, EntityType.SPLITTER):
        return False
    try:
        return ct.get_direction(bid) == needed
    except Exception:
        return False


def clearWrongBelt(bot, ct: Controller, tile: Position) -> str:
    """Clear wrong-facing belt. Returns acted|waiting|clear|failed."""
    if not ct.is_in_vision(tile):
        return "waiting"
    bid = ct.get_tile_building_id(tile)
    if bid is None:
        return "clear"
    etype = ct.get_entity_type(bid)
    if etype not in (EntityType.CONVEYOR, EntityType.SPLITTER):
        return "failed"
    pos = ct.get_position()
    ally = ct.get_team(bid) == ct.get_team()

    if pos == tile:
        if ct.get_move_cooldown() != 0:
            return "waiting"
        for d in CARDINALS:
            if ct.can_move(d):
                ct.move(d)
                return "acted"
        return "failed"

    adjacent = abs(pos.x - tile.x) + abs(pos.y - tile.y) == 1
    if not adjacent:
        if moveToward(bot, ct, tile):
            return "acted"
        return "waiting"

    if ally:
        if ct.can_destroy(tile):
            ct.destroy(tile)
            return "acted"
        return "failed"

    # Enemy: stand-and-fire from adjacent (builder fires own tile only for belts
    # on the work tile — move onto tile if needed next turns after clear attempt).
    if ct.get_action_cooldown() == 0 and ct.can_fire(tile):
        ct.fire(tile)
        return "acted"
    return "waiting"


def placeRelayBelt(ct: Controller, tile: Position, facing: Direction) -> str:
    """Build conveyor. Returns placed|waiting|failed."""
    if not ct.is_in_vision(tile):
        return "waiting"
    if ct.get_tile_building_id(tile) is not None:
        return "failed"
    if ct.get_action_cooldown() != 0:
        return "waiting"
    if ct.can_build_conveyor(tile, facing):
        ct.build_conveyor(tile, facing)
        return "placed"
    return "failed"


def extendRelayWatermark(
    ct: Controller,
    path: list[Position],
    core_pos: Position,
    built_to: int,
) -> int:
    """Raise watermark through contiguous visible correct-facing belts."""
    i = built_to + 1
    while i < len(path):
        tile = path[i]
        if not ct.is_in_vision(tile):
            break
        needed = neededBeltFacing(path, i, core_pos)
        if not beltFacingOk(ct, tile, needed):
            break
        built_to = i
        i += 1
    return built_to


def pickRelayPath(
    bot,
    ct: Controller,
    origin: Position,
    core_pos: Position,
) -> list[Position] | None:
    local_map = bot.hivemind.local_map
    ore_blocked: set[tuple[int, int]] = set()
    if bot.oreSite is not None:
        ore_blocked.add((bot.oreSite.x, bot.oreSite.y))

    def is_goal(p: Position) -> bool:
        return isCoreAdjacent(ct, p, core_pos)

    def ok(p: Position) -> bool:
        return relayPassable(
            ct, p, ore_blocked=ore_blocked, local_map=local_map
        )

    seeds: list[Position] = []
    if ok(origin):
        seeds.append(origin)
    else:
        for d in CARDINALS:
            n = origin.add(d)
            if ok(n):
                seeds.append(n)

    path = None
    if seeds:
        path = astarFromSeeds(
            seeds,
            is_passable=ok,
            is_goal=is_goal,
            heuristic=lambda p: manhattan(p, core_pos),
            max_iters=PATH_MAX_ITERS,
        )
    if path is None:
        path = findPathAroundObstruction(
            ct,
            origin,
            is_goal=is_goal,
            heuristic_to=core_pos,
            ore_blocked=ore_blocked,
            local_map=local_map,
            max_iters=PATH_MAX_ITERS,
        )
    return path


def relayToCore(bot, ct: Controller) -> None:
    """Orchestrator: path → facing check → clear/place → move (ally core only)."""
    core_pos = _allyCorePos(bot, ct)
    if core_pos is None:
        abortRelay(bot)
        return

    enemy_core = getattr(bot, "enemyCorePos", None)
    if bot.relayOrigin is None:
        bot.relayOrigin = bot.oreSite or ct.get_position()
    bot.inRelay = True

    path = bot.relayPath
    if path is not None:
        if _isEnemyCoreAdjacent(ct, path[-1], enemy_core):
            bot.relayPath = None
            abortRelay(bot)
            return
        bot.relayBuiltTo = extendRelayWatermark(
            ct, path, core_pos, bot.relayBuiltTo
        )

    if path is None:
        path = pickRelayPath(bot, ct, bot.relayOrigin, core_pos)
        bot.relayPath = path
        bot.relayBuiltTo = -1
        if path is None or _isEnemyCoreAdjacent(ct, path[-1], enemy_core):
            bot.relayPath = None
            abortRelay(bot)
            return
        bot.relayBuiltTo = extendRelayWatermark(ct, path, core_pos, -1)

    work_from = bot.relayBuiltTo + 1
    if work_from >= len(path):
        markRelayComplete(ct)
        clearRelay(bot)
        bot.oreSite = None
        return

    idx = work_from
    # Skip already-correct tiles that watermark may have missed in fog edge
    while idx < len(path):
        tile = path[idx]
        needed = neededBeltFacing(path, idx, core_pos)
        if ct.is_in_vision(tile) and beltFacingOk(ct, tile, needed):
            bot.relayBuiltTo = idx
            idx += 1
            continue
        break
    else:
        markRelayComplete(ct)
        clearRelay(bot)
        bot.oreSite = None
        return

    tile = path[idx]
    needed = neededBeltFacing(path, idx, core_pos)
    pos = ct.get_position()

    # Don't spend titanium on a tile a visible enemy gunner is covering
    if oreThreatenedByGunner(ct, tile):
        abortRelay(bot)
        return

    # Non-belt building blocking — replan
    if ct.is_in_vision(tile):
        block_id = ct.get_tile_building_id(tile)
        if block_id is not None:
            etype = ct.get_entity_type(block_id)
            if etype not in (EntityType.CONVEYOR, EntityType.SPLITTER):
                bot.relayPath = None
                bot.relayBuiltTo = -1
                return

    if ct.is_in_vision(tile) and bid_is_belt(ct, tile):
        if not beltFacingOk(ct, tile, needed):
            result = clearWrongBelt(bot, ct, tile)
            if result == "acted":
                return
            if result == "failed":
                bot.relayStuck = getattr(bot, "relayStuck", 0) + 1
                if bot.relayStuck >= 3:
                    abortRelay(bot)
                return
            if result == "waiting":
                return
            # clear — fall through to place

    if beltFacingOk(ct, tile, needed):
        bot.relayBuiltTo = idx
        return

    # Need empty tile to place — step off if standing on it
    if pos == tile:
        if ct.get_move_cooldown() != 0:
            return
        for d in CARDINALS:
            if ct.can_move(d):
                ct.move(d)
                return
        bot.relayStuck = getattr(bot, "relayStuck", 0) + 1
        if bot.relayStuck >= 3:
            abortRelay(bot)
        return

    adjacent = abs(pos.x - tile.x) + abs(pos.y - tile.y) == 1
    if adjacent:
        result = placeRelayBelt(ct, tile, needed)
        if result == "placed":
            bot.relayStuck = 0
            bot.relayBuiltTo = max(bot.relayBuiltTo, idx)
            if ct.get_move_cooldown() == 0:
                step = nearestCardinal(pos.direction_to(tile))
                if step != Direction.CENTRE and ct.can_move(step):
                    ct.move(step)
            return
        if result == "waiting":
            return
        bot.relayStuck = getattr(bot, "relayStuck", 0) + 1
        if bot.relayStuck >= 3:
            abortRelay(bot)
        return

    if moveToward(bot, ct, tile):
        bot.relayStuck = 0
    else:
        bot.relayStuck = getattr(bot, "relayStuck", 0) + 1
        if bot.relayStuck >= 3:
            abortRelay(bot)


def bid_is_belt(ct: Controller, tile: Position) -> bool:
    bid = ct.get_tile_building_id(tile)
    if bid is None:
        return False
    return ct.get_entity_type(bid) in (EntityType.CONVEYOR, EntityType.SPLITTER)


# ------------------------------------------------------------------
# Default role tasks
# ------------------------------------------------------------------


def buildHarvester(bot, ct: Controller) -> bool:
    """Build on empty ore in vision / known map. Returns True if acted."""
    if bot.inRelay:
        return False

    # Adjacent empty ore first
    if ct.get_action_cooldown() == 0:
        pos = ct.get_position()
        for d in Direction:
            if d == Direction.CENTRE:
                continue
            tile = pos.add(d)
            if not inBounds(ct, tile):
                continue
            if not _allowOreSite(bot, tile):
                continue
            if oreThreatenedByGunner(ct, tile):
                continue
            if ct.can_build_harvester(tile):
                ct.build_harvester(tile)
                bot.oreSite = tile
                bot.relayOrigin = tile
                bot.inRelay = True
                bot.relayPath = None
                bot.relayBuiltTo = -1
                return True

    # Commit to sticky empty target; only re-stagger when unset/invalid
    target = _stickyEmptyTarget(bot, ct)
    if target is None:
        target = pickEmptyOre(bot, ct)
    if target is None:
        return False
    bot.oreSite = target
    if moveToward(bot, ct, target):
        return True
    # Try stand next to it
    pos = ct.get_position()
    if abs(pos.x - target.x) + abs(pos.y - target.y) == 1:
        return False
    return False


def checkOre(bot, ct: Controller, tile: Position) -> bool:
    """Inspect ore node; returns True if this turn's action consumed."""
    if not ct.is_in_vision(tile):
        return moveToward(bot, ct, tile)

    bid = ct.get_tile_building_id(tile)

    if bid is None:
        # Empty — build if adjacent / move (skip if enemy gunner aims here)
        if oreThreatenedByGunner(ct, tile):
            return False

        pos = ct.get_position()
        if abs(pos.x - tile.x) + abs(pos.y - tile.y) == 1:
            if ct.get_action_cooldown() == 0 and ct.can_build_harvester(tile):
                ct.build_harvester(tile)
                bot.relayOrigin = tile
                bot.inRelay = True
                bot.relayPath = None
                bot.relayBuiltTo = -1
                return True
            return False
        if pos == tile:
            if ct.get_move_cooldown() == 0:
                for d in CARDINALS:
                    if ct.can_move(d):
                        ct.move(d)
                        return True
            return False
        return moveToward(bot, ct, tile)

    etype = ct.get_entity_type(bid)
    team = ct.get_team(bid)
    mine = ct.get_team()

    if etype == EntityType.HARVESTER:
        bot.relayOrigin = tile
        bot.inRelay = True
        relayToCore(bot, ct)
        # relayToCore clears inRelay when it aborts or completes, in both cases
        # without consuming an action. Report that honestly so maintainHarvester
        # falls through to recheckOreNodes instead of burning the turn.
        return bot.inRelay

    if etype in _ORE_BLOCK_CLEAR:
        if team == mine:
            if not _shouldClearAllyOreBlock(ct, tile):
                return False
            pos = ct.get_position()
            if abs(pos.x - tile.x) + abs(pos.y - tile.y) == 1:
                if ct.get_action_cooldown() == 0 and ct.can_destroy(tile):
                    ct.destroy(tile)
                    return True
                return False
            return _approachOreBlock(bot, ct, tile)
        # Enemy occupancy — gun placement deferred
        return placeGunnerOnOre(bot, ct, tile)

    return False


def maintainHarvester(bot, ct: Controller) -> bool:
    bot.recheckingOre = False
    if bot.inRelay and bot.relayOrigin is not None:
        relayToCore(bot, ct)
        # Finished or aborted this turn — don't block recheck/defend forever
        if not bot.inRelay:
            return False
        return True

    tile = pickMaintainOre(bot, ct)
    if tile is None:
        return False
    bot.oreSite = tile
    acted = checkOre(bot, ct, tile)
    # Instant complete/abort via checkOre→relayToCore
    if acted and not bot.inRelay and bot.oreSite is None:
        return False
    return acted


# def idleHome(bot, ct: Controller) -> bool:
#     """Removed from runDefault — soft-idle trap; use recheckOreNodes instead."""
#     del bot, ct
#     return False


DEFEND_HOME_CHEBYSHEV = 4
ORE_RECHECK_CLEAR_INTERVAL = 50


def _chebyshevToCore(pos: Position, core_pos: Position) -> int:
    best = 10**9
    for tile in _coreFootprint(core_pos):
        d = max(abs(pos.x - tile.x), abs(pos.y - tile.y))
        if d < best:
            best = d
    return best


def _nearCore(bot, ct: Controller) -> bool:
    if bot.corePos is None:
        return False
    return _chebyshevToCore(ct.get_position(), bot.corePos) <= DEFEND_HOME_CHEBYSHEV


def defendHome(bot, ct: Controller) -> bool:
    """Near-core only: keep a damaged ally core alive.

    This used to lead with an attack branch — nearest enemy turret (else builder),
    `moveToward` it, poke for 2 dmg at 2 Ti. That is dropped. The Ti rate alone was
    bad (1.0 Ti/dmg against a gunner's 0.20), but the approach was worse: a 40 HP
    builder closing on something dealing 10 dmg/turn at sqrt(13) dies in four turns
    and has to survive the walk before landing a single 2-damage hit. We were
    trading a 30 Ti builder for ~6 damage to a 10 Ti gunner. Turrets answer turrets
    — `defGun` and `defSentinel` sit above this in the chain.

    Healing is no longer gated on an empty vision either. The core is healed
    *because* something is shelling it; the old gate meant any enemy builder
    wandering into view flipped the guardian into the attack branch and it
    abandoned the core to give chase.
    """
    if bot.inRelay or getattr(bot, "recheckingOre", False):
        return False
    if not _nearCore(bot, ct):
        return False

    if bot.corePos is None or ct.get_action_cooldown() != 0:
        return False
    for tile in _coreFootprint(bot.corePos):
        bid = None
        if ct.is_in_vision(tile):
            bid = ct.get_tile_building_id(tile)
        if bid is None or ct.get_entity_type(bid) != EntityType.CORE:
            continue
        if ct.get_team(bid) != ct.get_team():
            continue
        if ct.get_hp(bid) >= 500:
            return False
        pos = ct.get_position()
        # Heal any adjacent core footprint tile
        for d in CARDINALS:
            check = pos.add(d)
            if not inBounds(ct, check):
                continue
            if ct.can_heal(check):
                hb = ct.get_tile_building_id(check)
                if (
                    hb is not None
                    and ct.get_entity_type(hb) == EntityType.CORE
                    and ct.get_team(hb) == ct.get_team()
                ):
                    ct.heal(check)
                    return True
        # Move adjacent to core to heal next turns
        return moveToward(bot, ct, tile)
    return False


def healAlly(bot, ct: Controller) -> bool:
    if ct.get_action_cooldown() != 0:
        return False
    pos = ct.get_position()
    priority = (
        EntityType.CORE,
        EntityType.GUNNER,
        EntityType.SENTINEL,
        EntityType.HARVESTER,
    )
    for want in priority:
        for d in CARDINALS:
            check = pos.add(d)
            if not inBounds(ct, check):
                continue
            if not ct.can_heal(check):
                continue
            bid = ct.get_tile_building_id(check)
            bot_id = ct.get_tile_builder_bot_id(check)
            eid = bid if bid is not None else bot_id
            if eid is None:
                continue
            if ct.get_entity_type(eid) != want:
                continue
            if ct.get_team(eid) != ct.get_team():
                continue
            ct.heal(check)
            return True
    return False


def _pickRecheckOre(bot, ct: Controller) -> Position | None:
    """Prefer empty, then clearable ally-blocked, then harvester; honor skippedOres."""
    skipped = getattr(bot, "skippedOres", set())
    pos = ct.get_position()
    core = bot.corePos
    empty_best: Position | None = None
    empty_key = None
    blocked_best: Position | None = None
    blocked_key = None
    harv_best: Position | None = None
    harv_key = None

    def score(ore: Position):
        if core is not None:
            return (manhattan(ore, core), manhattan(ore, pos))
        return (manhattan(ore, pos),)

    def consider(ore: Position, *, kind: str) -> None:
        nonlocal empty_best, empty_key, blocked_best, blocked_key, harv_best, harv_key
        if not _allowOreSite(bot, ore):
            return
        key_xy = (ore.x, ore.y)
        if key_xy in skipped:
            return
        key = score(ore)
        if kind == "empty":
            if empty_key is None or key < empty_key:
                empty_key = key
                empty_best = ore
        elif kind == "blocked":
            if blocked_key is None or key < blocked_key:
                blocked_key = key
                blocked_best = ore
        else:
            if harv_key is None or key < harv_key:
                harv_key = key
                harv_best = ore

    for tile in ct.get_nearby_tiles():
        if ct.get_tile_env(tile) != Environment.ORE_TITANIUM:
            continue
        bid = ct.get_tile_building_id(tile)
        if bid is None:
            consider(tile, kind="empty")
        elif ct.get_entity_type(bid) == EntityType.HARVESTER:
            consider(tile, kind="harvester")
        elif _shouldClearAllyOreBlock(ct, tile):
            consider(tile, kind="blocked")

    for ore in knownOreTiles(bot):
        key_xy = (ore.x, ore.y)
        if key_xy in skipped:
            continue
        if ct.is_in_vision(ore):
            bid = ct.get_tile_building_id(ore)
            if bid is None:
                consider(ore, kind="empty")
            elif ct.get_entity_type(bid) == EntityType.HARVESTER:
                consider(ore, kind="harvester")
            elif _shouldClearAllyOreBlock(ct, ore):
                consider(ore, kind="blocked")
            continue
        entry = bot.hivemind.local_map.get(key_xy, {})
        ent = entry.get("entity")
        if ent and ent.get("type") == EntityType.HARVESTER:
            consider(ore, kind="harvester")
        elif not ent:
            consider(ore, kind="empty")

    if empty_best is not None:
        return empty_best
    if blocked_best is not None:
        return blocked_best
    return harv_best


def breakOreDeadlock(bot, ct: Controller) -> bool:
    """Drop the ore claim when a builder has sat motionless too long.

    `bot.stuck` counts turns with no position change (maintained in main). Only
    harasser orbit and guardian patrol read it; economy builders never did, so a
    cluster stuck on one node could sit there for the rest of the game —
    `_clearStickyIfAllyClaimed` exempts anyone already adjacent, so nobody yields.

    State-only: clears the claim so the same turn's economy re-picks. Does not
    consume the turn. Relays are left alone — `relayStuck` already covers those.
    """
    from main import ORE_DEADLOCK_TURNS

    if ORE_DEADLOCK_TURNS is None or ORE_DEADLOCK_TURNS <= 0:
        return False
    if bot.inRelay:
        return False
    if getattr(bot, "stuck", 0) < ORE_DEADLOCK_TURNS:
        return False

    if bot.oreSite is not None:
        if not hasattr(bot, "skippedOres"):
            bot.skippedOres = set()
        bot.skippedOres.add((bot.oreSite.x, bot.oreSite.y))
    bot.oreSite = None
    bot.cachedPath = None
    bot.cachedPathTarget = None
    clearRelay(bot)
    bot.stuck = 0  # re-arm; don't fire again until it stalls afresh
    return True


def recheckOreNodes(bot, ct: Controller) -> bool:
    """Last-resort: clear sticky ore state and resume maintain/steal."""
    if bot.inRelay:
        return False

    round_n = ct.get_current_round()
    last = getattr(bot, "lastOreRecheckRound", -999)
    if round_n - last >= ORE_RECHECK_CLEAR_INTERVAL:
        bot.skippedOres = set()
        bot.lastOreRecheckRound = round_n

    # Break soft-idle: drop sticky site / path cache / orphaned stuck relay
    if bot.oreSite is not None:
        bot.skippedOres.add((bot.oreSite.x, bot.oreSite.y))
    bot.oreSite = None
    bot.cachedPath = None
    bot.cachedPathTarget = None
    if getattr(bot, "relayStuck", 0) >= 3 or (
        not bot.inRelay and bot.relayPath is not None
    ):
        clearRelay(bot)

    pick = _pickRecheckOre(bot, ct)
    if pick is None:
        bot.recheckingOre = False
        return False

    bot.recheckingOre = True
    bot.oreSite = pick
    acted = checkOre(bot, ct, pick)
    if bot.inRelay:
        bot.recheckingOre = False
    if not acted:
        bot.recheckingOre = False
    return acted


# Back-compat — orchestrator lives in builder.runDefault
def run_default(bot, ct: Controller) -> None:
    from builder import runDefault

    runDefault(bot, ct)


def run_economy(bot, ct: Controller) -> None:
    from builder import runDefault

    runDefault(bot, ct)
