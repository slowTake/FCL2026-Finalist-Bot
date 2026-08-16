
from __future__ import annotations

from fcode import Controller, Direction, EntityType, Environment, Position

from geometry import (
    BELT_TYPES,
    DEFEND_HOME_CHEBYSHEV,
    TURRET_TYPES,
    beltNeighbors,
    chebyshevToCore,
    coreFootprint,
    harvesterClaimedByAlly,
    isAdjacent,
    manhattanToCore,
    nearCore,
    tileEntity,
)
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

_ORE_ENEMY_BLOCKERS = _ORE_BLOCK_CLEAR + (EntityType.CORE,)


def clearRelay(bot) -> None:
    bot.inRelay = False
    bot.relayOrigin = None
    bot.relayPath = None
    bot.relayBuiltTo = -1
    bot.relayStuck = 0
    bot.beltFireTile = None
    bot.beltFireTurns = 0
    bot.relayVerify = False


def abortRelay(bot) -> None:
    clearRelay(bot)


_ORPHAN_TURN_BITS = 10
_ORPHAN_TURN_MASK = (1 << _ORPHAN_TURN_BITS) - 1
_ORPHAN_COORD_BITS = 7
_ORPHAN_COORD_MASK = (1 << _ORPHAN_COORD_BITS) - 1
_ORPHAN_OCCUPIED_BIT = 1 << (_ORPHAN_COORD_BITS * 2 + _ORPHAN_TURN_BITS)


def _orphanSlots() -> tuple[int, int, int]:
    from main import SLOT_ORPHAN_1, SLOT_ORPHAN_2, SLOT_ORPHAN_3

    return (SLOT_ORPHAN_1, SLOT_ORPHAN_2, SLOT_ORPHAN_3)


def _packOrphan(x: int, y: int, turn: int) -> int:
    return (
        _ORPHAN_OCCUPIED_BIT
        | ((x & _ORPHAN_COORD_MASK) << (_ORPHAN_COORD_BITS + _ORPHAN_TURN_BITS))
        | ((y & _ORPHAN_COORD_MASK) << _ORPHAN_TURN_BITS)
        | (turn & _ORPHAN_TURN_MASK)
    )


def _unpackOrphan(val: int) -> tuple[int, int, int] | None:
    if val == 0:
        return None
    turn = val & _ORPHAN_TURN_MASK
    y = (val >> _ORPHAN_TURN_BITS) & _ORPHAN_COORD_MASK
    x = (val >> (_ORPHAN_COORD_BITS + _ORPHAN_TURN_BITS)) & _ORPHAN_COORD_MASK
    return x, y, turn


def _readOrphanRegistry(ct: Controller) -> list[tuple[int, tuple[int, int, int]]]:
    out = []
    for slot in _orphanSlots():
        rec = _unpackOrphan(ct.read_store(slot))
        if rec is not None:
            out.append((slot, rec))
    return out


def _rememberedAllyHarvester(bot, ct: Controller, tile: Position) -> bool:
    ent = tileEntity(ct, tile, local_map=bot.hivemind.local_map)
    return (
        ent is not None
        and ent.get("type") == EntityType.HARVESTER
        and ent.get("team") == ct.get_team()
    )


def reportOrphanRelay(bot, ct: Controller, tile: Position) -> None:
    if not _rememberedAllyHarvester(bot, ct, tile):
        return  # not actually a harvester we own — nothing to report
    turn = ct.get_current_round() & _ORPHAN_TURN_MASK
    entries = _readOrphanRegistry(ct)
    for slot, (x, y, _t) in entries:
        if (x, y) == (tile.x, tile.y):
            ct.write_store(slot, _packOrphan(tile.x, tile.y, turn))
            return
    occupied = {slot for slot, _ in entries}
    free = [s for s in _orphanSlots() if s not in occupied]
    if free:
        ct.write_store(free[0], _packOrphan(tile.x, tile.y, turn))
        return
    oldest_slot, _ = min(entries, key=lambda e: e[1][2])
    ct.write_store(oldest_slot, _packOrphan(tile.x, tile.y, turn))


def clearOrphanRelay(ct: Controller, tile: Position) -> None:
    for slot, (x, y, _t) in _readOrphanRegistry(ct):
        if (x, y) == (tile.x, tile.y):
            ct.write_store(slot, 0)
            return


def _reportAndAbort(bot, ct: Controller) -> None:
    if bot.relayOrigin is not None:
        reportOrphanRelay(bot, ct, bot.relayOrigin)
    abortRelay(bot)


def _pickOrphanRelay(bot, ct: Controller) -> Position | None:
    from main import ORPHAN_REGISTRY_TTL_TURNS

    skipped = getattr(bot, "skippedOres", set())
    now = ct.get_current_round()
    pos = ct.get_position()
    best: Position | None = None
    best_d = 10**9
    for slot, (x, y, turn) in _readOrphanRegistry(ct):
        if now - turn > ORPHAN_REGISTRY_TTL_TURNS:
            ct.write_store(slot, 0)
            continue
        if (x, y) in skipped:
            continue
        tile = Position(x, y)
        if not _allowOreSite(bot, tile):
            continue
        if ct.is_in_vision(tile):
            bid = ct.get_tile_building_id(tile)
            if (
                bid is None
                or ct.get_entity_type(bid) != EntityType.HARVESTER
                or ct.get_team(bid) != ct.get_team()
            ):
                ct.write_store(slot, 0)  # visibly no longer a live ally harvester
                continue
            if allyBuilderNearOre(ct, tile):
                continue  # someone's already there working it
        d = manhattan(pos, tile)
        if d < best_d:
            best_d = d
            best = tile
    return best


def relayEverComplete(ct: Controller) -> bool:
    from main import RELAY_COMPLETE_BIT, SLOT_RELAY_COMPLETE

    return (ct.read_store(SLOT_RELAY_COMPLETE) & RELAY_COMPLETE_BIT) != 0


def coreThreatened(ct: Controller) -> bool:
    from main import CORE_THREAT_BIT, SLOT_RELAY_COMPLETE

    return (ct.read_store(SLOT_RELAY_COMPLETE) & CORE_THREAT_BIT) != 0


def setCoreThreat(ct: Controller, on: bool) -> None:
    from main import CORE_THREAT_BIT, SLOT_RELAY_COMPLETE

    val = ct.read_store(SLOT_RELAY_COMPLETE)
    new = (val | CORE_THREAT_BIT) if on else (val & ~CORE_THREAT_BIT)
    if new != val:
        ct.write_store(SLOT_RELAY_COMPLETE, new)


def guardianLeash(ct: Controller) -> int:
    from main import GUARDIAN_LEASH_TIGHT, GUARDIAN_LEASH_WIDE

    return GUARDIAN_LEASH_TIGHT if coreThreatened(ct) else GUARDIAN_LEASH_WIDE


def enemyTurretInVision(ct: Controller) -> bool:
    me = ct.get_team()
    for eid in ct.get_nearby_entities():
        if ct.get_team(eid) == me:
            continue
        if ct.get_entity_type(eid) in TURRET_TYPES:
            return True
    return False


def nearestEnemyTurret(ct: Controller) -> Position | None:
    me = ct.get_team()
    pos = ct.get_position()
    best: Position | None = None
    best_d = 10**9
    for eid in ct.get_nearby_entities():
        if ct.get_team(eid) == me:
            continue
        if ct.get_entity_type(eid) not in TURRET_TYPES:
            continue
        epos = ct.get_position(eid)
        d = manhattan(pos, epos)
        if d < best_d:
            best_d = d
            best = epos
    return best


def contestTurret(bot, ct: Controller) -> bool:
    target = nearestEnemyTurret(ct)
    if target is None:
        return False
    from guns import placeGun

    return placeGun(bot, ct, target)


def markRelayComplete(ct: Controller) -> None:
    from main import RELAY_COMPLETE_BIT, SLOT_RELAY_COMPLETE

    val = ct.read_store(SLOT_RELAY_COMPLETE)
    if not (val & RELAY_COMPLETE_BIT):
        ct.write_store(SLOT_RELAY_COMPLETE, val | RELAY_COMPLETE_BIT)


def isCoreAdjacent(
    ct: Controller, pos: Position, core_pos: Position | None
) -> bool:
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
    for tile in coreFootprint(core_pos):
        if isAdjacent(pos, tile):
            return True
    return False


def _allyCorePos(bot, ct: Controller) -> Position | None:
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
    for ctile in coreFootprint(enemy_core):
        if isAdjacent(tile, ctile):
            return True
    return False


def travelFacing(from_pos: Position, to_pos: Position) -> Direction:
    return nearestCardinal(from_pos.direction_to(to_pos))


def facingIntoCore(pos: Position, core_pos: Position) -> Direction:
    for tile in coreFootprint(core_pos):
        if not isAdjacent(pos, tile):
            continue
        best = travelFacing(pos, tile)
        if best != Direction.CENTRE:
            return best
    return nearestCardinal(pos.direction_to(core_pos))


def _notePathVerdict(bot, target: Position, failed: bool) -> None:
    bot.lastPathFailed = failed
    bot.lastPathFailedTarget = (target.x, target.y) if failed else None


def pathKnownBlocked(bot, target: Position) -> bool:
    if not getattr(bot, "lastPathFailed", False):
        return False
    return getattr(bot, "lastPathFailedTarget", None) == (target.x, target.y)


def _blockedByEnemyBot(ct: Controller, p: Position) -> bool:
    if not ct.is_in_vision(p):
        return False
    occ = ct.get_tile_builder_bot_id(p)
    return occ is not None and ct.get_team(occ) != ct.get_team()


def moveToward(
    bot, ct: Controller, target: Position, *, avoid: set[tuple[int, int]] | None = None
) -> bool:
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
        if avoid and (p.x, p.y) in avoid:
            return False
        if _blockedByEnemyBot(ct, p):
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
        and not (avoid and any((p.x, p.y) in avoid for p in cached))
    ):
        path = cached
        _notePathVerdict(bot, target, False)  # cache hit — a route was found
    else:
        from main import MAX_PATH_SEARCHES_PER_TURN

        searches = getattr(bot, "pathSearchesThisTurn", 0)
        if MAX_PATH_SEARCHES_PER_TURN and searches >= MAX_PATH_SEARCHES_PER_TURN:
            path = None
        else:
            bot.pathSearchesThisTurn = searches + 1
            path = astarFromSeeds(
                [pos],
                is_passable=ok,
                is_goal=lambda p: p == target,
                heuristic=lambda p: manhattan(p, target),
                max_iters=PATH_MAX_ITERS,
            )
            bot.cachedPath = path
            bot.cachedPathTarget = target
            _notePathVerdict(bot, target, path is None)

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




def knownOreTiles(bot, *, includeStatic: bool = False) -> list[Position]:
    local_map = bot.hivemind.local_map
    seen: set[tuple[int, int]] = set()
    ores: list[Position] = []
    for (x, y), cell in local_map.items():
        if cell.get("ore"):
            seen.add((x, y))
            ores.append(Position(x, y))
    if ores and not includeStatic:
        return ores
    from features import ORE_LOCATIONS

    map_id = bot.hivemind.map_id
    for ox, oy in ORE_LOCATIONS.get(map_id, []):
        if (ox, oy) in seen:
            continue
        ores.append(Position(ox, oy))
    return ores


def _allowOreSite(bot, ore: Position) -> bool:
    if not getattr(bot, "nearCoreEconomy", False):
        return True
    if bot.corePos is None:
        return True
    return chebyshevToCore(ore, bot.corePos) <= DEFEND_HOME_CHEBYSHEV


def _selfNearOre(ct: Controller, ore: Position) -> bool:
    pos = ct.get_position()
    return abs(pos.x - ore.x) + abs(pos.y - ore.y) <= 1


def allyBuilderNearOre(ct: Controller, ore: Position) -> bool:
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
    if not ct.is_in_vision(ore):
        return False
    bid = ct.get_tile_builder_bot_id(ore)
    if bid is None or bid == ct.get_id():
        return False
    return ct.get_team(bid) == ct.get_team()


def _defaultOreRank(bot) -> int:
    idx = bot.hivemind.my_index
    if idx <= 0 or idx % 5 != 3:
        return 0
    return (idx - 3) // 5


def _oreStaggerKey(ore: Position, core: Position | None) -> tuple:
    if core is not None:
        return (manhattanToCore(ore, core), ore.x, ore.y)
    return (ore.x, ore.y)


def _oreConvenienceKey(
    ore: Position, core: Position | None, self_pos: Position
) -> tuple:
    from main import ORE_PICK_CORE_WEIGHT, ORE_PICK_SELF_WEIGHT

    self_d = manhattan(ore, self_pos)
    if core is None:
        return (self_d, ore.x, ore.y)
    core_d = manhattanToCore(ore, core)
    score = ORE_PICK_SELF_WEIGHT * self_d + ORE_PICK_CORE_WEIGHT * core_d
    return (score, ore.x, ore.y)


def _clearStickyIfAllyClaimed(bot, ct: Controller) -> None:
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
    _clearStickyIfAllyClaimed(bot, ct)
    site = bot.oreSite
    if site is None:
        return None
    if not _allowOreSite(bot, site):
        bot.oreSite = None
        return None
    if tileThreatenedByGunner(ct, site):
        bot.oreSite = None
        return None
    if ct.is_in_vision(site) and ct.get_tile_building_id(site) is not None:
        bot.oreSite = None
        return None
    return site


def _allyBarrierOnOre(ct: Controller, tile: Position) -> bool:
    if not ct.is_in_vision(tile):
        return False
    bid = ct.get_tile_building_id(tile)
    if bid is None:
        return False
    return (
        ct.get_entity_type(bid) == EntityType.BARRIER
        and ct.get_team(bid) == ct.get_team()
    )


def _oreLedgerStale(bot, entry: dict) -> bool:
    from main import ORE_BLOCK_MEMORY_TURNS

    memory = ORE_BLOCK_MEMORY_TURNS
    if not memory or memory <= 0:
        return False
    age = bot.hivemind.global_turn - entry.get("last_seen_turn", -(10**9))
    return age > memory


def pickEmptyOre(bot, ct: Controller) -> Position | None:
    from main import ORE_STAGGER

    core = bot.corePos
    self_pos = ct.get_position()
    me = ct.get_team()

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
        if ct.get_tile_building_id(tile) is not None and not _allyBarrierOnOre(
            ct, tile
        ):
            continue
        add(tile)

    for ore in knownOreTiles(bot):
        entry = bot.hivemind.local_map.get((ore.x, ore.y), {})
        ent = entry.get("entity")
        if ent and _oreLedgerStale(bot, entry):
            ent = None
        if ent:
            if ent.get("type") == EntityType.HARVESTER:
                continue
            if ent.get("team") != me and ent.get("type") in _ORE_ENEMY_BLOCKERS:
                continue
        if (
            ct.is_in_vision(ore)
            and ct.get_tile_building_id(ore) is not None
            and not _allyBarrierOnOre(ct, ore)
        ):
            continue
        add(ore)

    eligible: list[Position] = []
    for ore in raw:
        if not _allowOreSite(bot, ore):
            continue
        if tileThreatenedByGunner(ct, ore):
            continue
        eligible.append(ore)

    if not eligible:
        return None

    skipped = getattr(bot, "skippedOres", set())
    unskipped = [o for o in eligible if (o.x, o.y) not in skipped]
    if unskipped:
        eligible = unskipped

    free = [o for o in eligible if not allyBuilderOnOre(ct, o)]
    pool = free if free else eligible

    if not ORE_STAGGER:
        pool.sort(key=lambda o: _oreConvenienceKey(o, core, self_pos))
        return pool[0]

    pool.sort(key=lambda o: _oreStaggerKey(o, core))
    rank = _defaultOreRank(bot)
    return pool[rank % len(pool)]


def tileThreatenedByGunner(ct: Controller, tile: Position) -> bool:
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
        if gunnerHasLos(ct, gpos, facing, tile, shooterIsAlly=False):
            return True
        if ct.can_fire_from(gpos, facing, EntityType.GUNNER, tile):
            return True
    return False


def _allyOreBlock(ct: Controller, tile: Position) -> EntityType | None:
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


def _shouldClearAllyOreBlock(
    ct: Controller, tile: Position, *, bot=None
) -> bool:
    etype = _allyOreBlock(ct, tile)
    if etype is None:
        return False
    if etype in (EntityType.GUNNER, EntityType.SENTINEL) and tileThreatenedByGunner(
        ct, tile
    ):
        return False
    if etype == EntityType.BARRIER and bot is not None:
        core = bot.corePos
        enemy = getattr(bot, "enemyCorePos", None)
        if (
            core is not None
            and enemy is not None
            and manhattanToCore(tile, core) > manhattanToCore(tile, enemy)
        ):
            return False
    return True


def _approachOreBlock(bot, ct: Controller, tile: Position) -> bool:
    pos = ct.get_position()
    if isAdjacent(pos, tile):
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


def _harvesterOwnerAllowed(ct: Controller, owner_team) -> bool:
    if owner_team == ct.get_team():
        return True
    from main import STEAL_ENEMY_HARVESTERS

    return bool(STEAL_ENEMY_HARVESTERS)


def pickMaintainOre(bot, ct: Controller) -> Position | None:
    _clearStickyIfAllyClaimed(bot, ct)
    if bot.oreSite is not None:
        if _allowOreSite(bot, bot.oreSite):
            return bot.oreSite
        bot.oreSite = None

    orphan = _pickOrphanRelay(bot, ct)
    if orphan is not None:
        return orphan

    pos = ct.get_position()
    core = bot.corePos
    best: Position | None = None
    best_key = None

    def score(ore: Position) -> tuple:
        if core is not None:
            return (manhattanToCore(ore, core), manhattan(ore, pos))
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
            if ct.get_entity_type(bid) == EntityType.HARVESTER and _harvesterOwnerAllowed(
                ct, ct.get_team(bid)
            ):
                consider(ore)
            elif _shouldClearAllyOreBlock(ct, ore, bot=bot):
                consider(ore)
        else:
            entry = bot.hivemind.local_map.get((ore.x, ore.y), {})
            ent = entry.get("entity")
            if (
                ent
                and ent.get("type") == EntityType.HARVESTER
                and _harvesterOwnerAllowed(ct, ent.get("team"))
            ):
                consider(ore)
            elif ent:
                consider(ore)

    for tile in ct.get_nearby_tiles():
        if ct.get_tile_env(tile) != Environment.ORE_TITANIUM:
            continue
        bid = ct.get_tile_building_id(tile)
        if bid is None:
            continue
        if ct.get_entity_type(bid) == EntityType.HARVESTER and _harvesterOwnerAllowed(
            ct, ct.get_team(bid)
        ):
            consider(tile)
        elif _shouldClearAllyOreBlock(ct, tile, bot=bot):
            consider(tile)

    empty = pickEmptyOre(bot, ct)
    if empty is None:
        return best
    if best is None:
        return empty
    if score(empty) <= score(best):
        return empty
    return best




def _allyBeltAt(ct: Controller, tile: Position) -> bool:
    bid = ct.get_tile_building_id(tile)
    if bid is None:
        return False
    return (
        ct.get_entity_type(bid) in BELT_TYPES and ct.get_team(bid) == ct.get_team()
    )


def _beltDirectionAt(bot, ct: Controller, tile: Position) -> Direction | None:
    me = ct.get_team()
    if ct.is_in_vision(tile):
        bid = ct.get_tile_building_id(tile)
        if bid is None or ct.get_team(bid) != me:
            return None
        if ct.get_entity_type(bid) != EntityType.CONVEYOR:
            return None
        try:
            return ct.get_direction(bid)
        except Exception:
            return None
    entry = bot.hivemind.local_map.get((tile.x, tile.y))
    if not entry or _beltLedgerStale(bot, entry):
        return None
    ent = entry.get("entity")
    if not ent or ent.get("team") != me or ent.get("type") != EntityType.CONVEYOR:
        return None
    return ent.get("direction")


def _relayReachesCore(
    bot,
    ct: Controller,
    tile: Position,
    core_pos: Position,
    *,
    cache: dict | None = None,
) -> bool:
    from main import RELAY_MERGE_MAX_HOPS

    key0 = (tile.x, tile.y)
    if cache is not None and key0 in cache:
        return cache[key0]
    max_hops = RELAY_MERGE_MAX_HOPS if RELAY_MERGE_MAX_HOPS and RELAY_MERGE_MAX_HOPS > 0 else 10
    cur = tile
    seen: set[tuple[int, int]] = set()
    result = False
    for _ in range(max_hops + 1):
        if any(cur.x == t.x and cur.y == t.y for t in coreFootprint(core_pos)):
            result = True
            break
        key = (cur.x, cur.y)
        if key in seen:
            break  # loop — shouldn't happen on a real belt line, defensive only
        seen.add(key)
        d = _beltDirectionAt(bot, ct, cur)
        if d is None or d == Direction.CENTRE:
            break
        cur = cur.add(d)
        if not inBounds(ct, cur):
            break
    if cache is not None:
        cache[key0] = result
    return result


def _mergeCandidateNeighbor(
    bot, ct: Controller, tile: Position, core_pos: Position, *, cache: dict | None = None
) -> Position | None:
    from defence import _allyBuilderNear, _isBeltOrCore

    footprint = coreFootprint(core_pos)
    for d in CARDINALS:
        near = tile.add(d)
        if not inBounds(ct, near):
            continue
        if any(near.x == t.x and near.y == t.y for t in footprint):
            continue
        if not _isBeltOrCore(bot, ct, near, core_pos):
            continue
        if not _relayReachesCore(bot, ct, near, core_pos, cache=cache):
            continue
        if _allyBuilderNear(ct, near):
            continue
        return near
    return None


def neededBeltFacing(
    bot, ct: Controller, path: list[Position], idx: int, core_pos: Position
) -> Direction:
    if idx >= len(path) - 1:
        tile = path[idx]
        if isCoreAdjacent(ct, tile, core_pos):
            return facingIntoCore(tile, core_pos)
        from main import RELAY_MERGE_ENABLED

        if RELAY_MERGE_ENABLED:
            own = _beltDirectionAt(bot, ct, tile)
            if own is not None and own != Direction.CENTRE and _relayReachesCore(
                bot, ct, tile, core_pos
            ):
                return own
            near = _mergeCandidateNeighbor(bot, ct, tile, core_pos)
            if near is not None:
                return travelFacing(tile, near)
        return facingIntoCore(tile, core_pos)
    return travelFacing(path[idx], path[idx + 1])


def _beltLedgerStale(bot, entry: dict) -> bool:
    from main import RELAY_LEDGER_TRUST_TURNS

    trust = RELAY_LEDGER_TRUST_TURNS
    if not trust or trust <= 0:
        return True  # knob disables the fallback outright — always "stale"
    age = bot.hivemind.global_turn - entry.get("last_seen_turn", -(10**9))
    return age > trust


def beltFacingOk(
    bot,
    ct: Controller,
    pos: Position,
    needed: Direction,
    *,
    local_map: dict | None = None,
    require_vision: bool = False,
) -> bool:
    me = ct.get_team()
    if ct.is_in_vision(pos):
        bid = ct.get_tile_building_id(pos)
        if bid is None or ct.get_team(bid) != me:
            return False
        if ct.get_entity_type(bid) not in BELT_TYPES:
            return False
        try:
            return ct.get_direction(bid) == needed
        except Exception:
            return False
    if require_vision or local_map is None:
        return False
    entry = local_map.get((pos.x, pos.y))
    if not entry or _beltLedgerStale(bot, entry):
        return False
    ent = entry.get("entity")
    if not ent or ent.get("team") != me or ent.get("type") not in BELT_TYPES:
        return False
    return ent.get("direction") == needed


_BELT_OBSTACLE_TYPES = BELT_TYPES + (EntityType.BARRIER,)


_BELT_FIRE_TURN_LIMIT = 11


def clearBeltObstacle(bot, ct: Controller, tile: Position) -> str:
    if not ct.is_in_vision(tile):
        return "waiting"
    bid = ct.get_tile_building_id(tile)
    if bid is None:
        bot.beltFireTile = None
        bot.beltFireTurns = 0
        return "clear"
    etype = ct.get_entity_type(bid)
    if etype not in _BELT_OBSTACLE_TYPES:
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

    adjacent = isAdjacent(pos, tile)
    if not adjacent:
        if moveToward(bot, ct, tile):
            return "acted"
        return "waiting"

    if ally:
        if ct.can_destroy(tile):
            ct.destroy(tile)
            return "acted"
        return "failed"

    key = (tile.x, tile.y)
    if getattr(bot, "beltFireTile", None) != key:
        bot.beltFireTile = key
        bot.beltFireTurns = 0
    if ct.get_action_cooldown() == 0 and ct.can_fire(tile):
        if bot.beltFireTurns >= _BELT_FIRE_TURN_LIMIT:
            bot.beltFireTile = None
            bot.beltFireTurns = 0
            return "failed"
        bot.beltFireTurns += 1
        ct.fire(tile)
        return "acted"
    return "waiting"


def placeRelayBelt(ct: Controller, tile: Position, facing: Direction) -> str:
    if not ct.is_in_vision(tile):
        return "waiting"
    if ct.get_tile_building_id(tile) is not None:
        return "failed"
    if ct.get_action_cooldown() != 0:
        return "waiting"
    if ct.get_global_resources() < ct.get_conveyor_cost():
        return "waiting"
    if ct.can_build_conveyor(tile, facing):
        ct.build_conveyor(tile, facing)
        return "placed"
    return "failed"


def _repairBeltIfDamaged(ct: Controller, tile: Position) -> bool:
    if not ct.can_heal(tile):
        return False
    ct.heal(tile)
    return True


def extendRelayWatermark(
    bot,
    ct: Controller,
    path: list[Position],
    core_pos: Position,
    built_to: int,
    *,
    require_vision: bool = False,
) -> int:
    i = built_to + 1
    while i < len(path):
        tile = path[i]
        needed = neededBeltFacing(bot, ct, path, i, core_pos)
        if not beltFacingOk(
            bot,
            ct,
            tile,
            needed,
            local_map=bot.hivemind.local_map,
            require_vision=require_vision,
        ):
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

    from main import RELAY_MERGE_ENABLED

    merge_cache: dict = {}

    def is_goal(p: Position) -> bool:
        if isCoreAdjacent(ct, p, core_pos):
            return True
        if not RELAY_MERGE_ENABLED:
            return False
        return _mergeCandidateNeighbor(bot, ct, p, core_pos, cache=merge_cache) is not None

    def ok(p: Position) -> bool:
        return relayPassable(
            ct,
            p,
            ore_blocked=ore_blocked,
            local_map=local_map,
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
    bot.relayPathBlocked = path is None
    bot.relayPathBlockedFrom = (origin.x, origin.y) if path is None else None
    return path


def _replanRelayOrAbort(
    bot, ct: Controller, core_pos: Position, enemy_core: Position | None
) -> None:
    new_path = pickRelayPath(bot, ct, bot.relayOrigin, core_pos)
    if new_path is None:
        bot.relayPath = None
        _reportAndAbort(bot, ct)
        return
    if _isEnemyCoreAdjacent(ct, new_path[-1], enemy_core):
        bot.relayPath = None
        abortRelay(bot)
        return
    bot.relayPath = new_path
    bot.relayBuiltTo = extendRelayWatermark(bot, ct, new_path, core_pos, -1)


def relayToCore(bot, ct: Controller) -> None:
    core_pos = _allyCorePos(bot, ct)
    if core_pos is None:
        abortRelay(bot)
        return

    enemy_core = getattr(bot, "enemyCorePos", None)
    if bot.relayOrigin is None:
        bot.relayOrigin = bot.oreSite or ct.get_position()
    bot.inRelay = True
    verify = getattr(bot, "relayVerify", False)

    path = bot.relayPath
    if path is not None:
        if _isEnemyCoreAdjacent(ct, path[-1], enemy_core):
            bot.relayPath = None
            abortRelay(bot)
            return
        bot.relayBuiltTo = extendRelayWatermark(
            bot, ct, path, core_pos, bot.relayBuiltTo, require_vision=verify
        )

    if path is None:
        path = pickRelayPath(bot, ct, bot.relayOrigin, core_pos)
        bot.relayPath = path
        bot.relayBuiltTo = -1
        if path is None:
            bot.relayPath = None
            _reportAndAbort(bot, ct)
            return
        if _isEnemyCoreAdjacent(ct, path[-1], enemy_core):
            bot.relayPath = None
            abortRelay(bot)
            return
        bot.relayBuiltTo = extendRelayWatermark(
            bot, ct, path, core_pos, -1, require_vision=verify
        )

    work_from = bot.relayBuiltTo + 1
    if work_from >= len(path):
        markRelayComplete(ct)
        if bot.relayOrigin is not None:
            clearOrphanRelay(ct, bot.relayOrigin)
        clearRelay(bot)
        bot.oreSite = None
        return

    idx = work_from
    while idx < len(path):
        tile = path[idx]
        needed = neededBeltFacing(bot, ct, path, idx, core_pos)
        if beltFacingOk(
            bot,
            ct,
            tile,
            needed,
            local_map=bot.hivemind.local_map,
            require_vision=verify,
        ):
            bot.relayBuiltTo = idx
            idx += 1
            continue
        break
    else:
        markRelayComplete(ct)
        if bot.relayOrigin is not None:
            clearOrphanRelay(ct, bot.relayOrigin)
        clearRelay(bot)
        bot.oreSite = None
        return

    tile = path[idx]
    needed = neededBeltFacing(bot, ct, path, idx, core_pos)
    pos = ct.get_position()


    if ct.is_in_vision(tile):
        block_id = ct.get_tile_building_id(tile)
        if block_id is not None:
            etype = ct.get_entity_type(block_id)
            if etype == EntityType.BARRIER:
                result = clearBeltObstacle(bot, ct, tile)
                if result == "acted":
                    return
                if result == "waiting":
                    return
                if result == "failed":
                    bot.relayStuck = getattr(bot, "relayStuck", 0) + 1
                    if bot.relayStuck < 3:
                        return
                    bot.relayStuck = 0
                    _replanRelayOrAbort(bot, ct, core_pos, enemy_core)
                    return
            elif etype not in BELT_TYPES:
                _replanRelayOrAbort(bot, ct, core_pos, enemy_core)
                return

    if ct.is_in_vision(tile) and bid_is_belt(ct, tile):
        if not beltFacingOk(bot, ct, tile, needed, local_map=bot.hivemind.local_map):
            if _allyBeltAt(ct, tile):
                _repairBeltIfDamaged(ct, tile)
                markRelayComplete(ct)
                if bot.relayOrigin is not None:
                    clearOrphanRelay(ct, bot.relayOrigin)
                clearRelay(bot)
                bot.oreSite = None
                return
            result = clearBeltObstacle(bot, ct, tile)
            if result == "acted":
                return
            if result == "failed":
                bot.relayStuck = getattr(bot, "relayStuck", 0) + 1
                if bot.relayStuck >= 3:
                    _reportAndAbort(bot, ct)
                return
            if result == "waiting":
                return

    if beltFacingOk(bot, ct, tile, needed, local_map=bot.hivemind.local_map):
        bot.relayBuiltTo = idx
        _repairBeltIfDamaged(ct, tile)
        return

    if pos == tile:
        if ct.get_move_cooldown() != 0:
            return
        for d in CARDINALS:
            if ct.can_move(d):
                ct.move(d)
                return
        bot.relayStuck = getattr(bot, "relayStuck", 0) + 1
        if bot.relayStuck >= 3:
            _reportAndAbort(bot, ct)
        return

    adjacent = isAdjacent(pos, tile)
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
            _reportAndAbort(bot, ct)


def bid_is_belt(ct: Controller, tile: Position) -> bool:
    bid = ct.get_tile_building_id(tile)
    if bid is None:
        return False
    return ct.get_entity_type(bid) in BELT_TYPES




def buildHarvester(bot, ct: Controller) -> bool:
    if bot.inRelay:
        return False

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
            if tileThreatenedByGunner(ct, tile):
                continue
            if ct.can_build_harvester(tile):
                ct.build_harvester(tile)
                bot.oreSite = tile
                bot.relayOrigin = tile
                bot.inRelay = True
                bot.relayPath = None
                bot.relayBuiltTo = -1
                return True

    target = _stickyEmptyTarget(bot, ct)
    if target is None:
        target = pickEmptyOre(bot, ct)
    if target is None:
        return False
    bot.oreSite = target
    if moveToward(bot, ct, target):
        return True
    pos = ct.get_position()
    if isAdjacent(pos, target):
        return False
    return False


def checkOre(bot, ct: Controller, tile: Position) -> bool:
    if not ct.is_in_vision(tile):
        return moveToward(bot, ct, tile)

    bid = ct.get_tile_building_id(tile)

    if bid is None:
        if tileThreatenedByGunner(ct, tile):
            return False

        pos = ct.get_position()
        if isAdjacent(pos, tile):
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
        if not _harvesterOwnerAllowed(ct, team):
            return False
        bot.relayOrigin = tile
        bot.inRelay = True
        relayToCore(bot, ct)
        return bot.inRelay

    if etype in _ORE_BLOCK_CLEAR:
        if team == mine:
            if not _shouldClearAllyOreBlock(ct, tile, bot=bot):
                return False
            pos = ct.get_position()
            if isAdjacent(pos, tile):
                if ct.get_action_cooldown() == 0 and ct.can_destroy(tile):
                    ct.destroy(tile)
                    return True
                return False
            return _approachOreBlock(bot, ct, tile)
        return False

    return False


def _repairNearbyBeltSegment(bot, ct: Controller, harvesterTile: Position) -> bool:
    local_map = bot.hivemind.local_map
    me = ct.get_team()
    targets = [harvesterTile] + [
        n for n, ent in beltNeighbors(ct, harvesterTile, local_map=local_map)
        if ent.get("team") == me
    ]
    damaged: list[Position] = []
    for t in targets:
        if not ct.is_in_vision(t):
            continue
        bid = ct.get_tile_building_id(t)
        if bid is None or ct.get_team(bid) != me:
            continue
        if ct.get_hp(bid) >= ct.get_max_hp(bid):
            continue
        damaged.append(t)
    if not damaged:
        return False
    pos = ct.get_position()
    for t in damaged:
        if isAdjacent(pos, t) and _repairBeltIfDamaged(ct, t):
            return True
    return moveToward(bot, ct, damaged[0])


def finishNearbyHarvester(bot, ct: Controller) -> bool:
    if bot.corePos is None or bot.inRelay or getattr(bot, "recheckingOre", False):
        return False

    from main import NEARBY_HARVESTER_SELF_CHEBYSHEV

    if NEARBY_HARVESTER_SELF_CHEBYSHEV is None or NEARBY_HARVESTER_SELF_CHEBYSHEV <= 0:
        return False

    core = bot.corePos
    pos = ct.get_position()
    skipped = getattr(bot, "skippedOres", set())
    local_map = bot.hivemind.local_map
    candidates: list[tuple[int, int, Position]] = []
    for (x, y), cell in local_map.items():
        ent = cell.get("entity")
        if not ent or ent.get("type") != EntityType.HARVESTER:
            continue
        if (x, y) in skipped:
            continue
        if not _harvesterOwnerAllowed(ct, ent.get("team")):
            continue
        tile = Position(x, y)
        if max(abs(tile.x - pos.x), abs(tile.y - pos.y)) > NEARBY_HARVESTER_SELF_CHEBYSHEV:
            continue
        if not _allowOreSite(bot, tile):
            continue
        candidates.append((chebyshevToCore(tile, core), manhattan(pos, tile), tile))
    candidates.sort()

    for _d, _m, tile in candidates:
        if harvesterClaimedByAlly(ct, tile, local_map=local_map):
            if _repairNearbyBeltSegment(bot, ct, tile):
                return True
            continue
        bot.oreSite = tile
        return checkOre(bot, ct, tile)
    return False


def maintainHarvester(bot, ct: Controller) -> bool:
    bot.recheckingOre = False
    if bot.inRelay and bot.relayOrigin is not None:
        relayToCore(bot, ct)
        if not bot.inRelay:
            return False
        return True

    tile = pickMaintainOre(bot, ct)
    if tile is None:
        return False
    bot.oreSite = tile
    acted = checkOre(bot, ct, tile)
    if acted and not bot.inRelay and bot.oreSite is None:
        return False
    return acted


def _pickVerifyHarvester(bot, ct: Controller, core: Position) -> Position | None:
    from main import HARVESTER_VERIFY_INTERVAL_TURNS

    me = ct.get_team()
    pos = ct.get_position()
    round_n = ct.get_current_round()
    skipped = getattr(bot, "skippedOres", set())
    verified = getattr(bot, "verifiedHarvesters", {})
    best: Position | None = None
    best_key = None
    for (x, y), cell in bot.hivemind.local_map.items():
        ent = cell.get("entity")
        if not ent or ent.get("team") != me:
            continue
        if ent.get("type") != EntityType.HARVESTER:
            continue
        if (x, y) in skipped:
            continue
        if round_n - verified.get((x, y), -(10**9)) < HARVESTER_VERIFY_INTERVAL_TURNS:
            continue
        tile = Position(x, y)
        if not _allowOreSite(bot, tile):
            continue
        if allyBuilderNearOre(ct, tile):
            continue
        key = (chebyshevToCore(tile, core), manhattan(tile, pos))
        if best_key is None or key < best_key:
            best_key = key
            best = tile
    return best


def verifyHomeHarvester(bot, ct: Controller) -> bool:
    if bot.inRelay or getattr(bot, "recheckingOre", False):
        return False
    core = bot.corePos
    if core is None:
        return False

    from main import HARVESTER_VERIFY_INTERVAL_TURNS

    if HARVESTER_VERIFY_INTERVAL_TURNS <= 0:
        return False
    round_n = ct.get_current_round()
    last = getattr(bot, "lastHarvesterVerifyRound", -(10**9))
    if round_n - last < HARVESTER_VERIFY_INTERVAL_TURNS:
        return False

    tile = _pickVerifyHarvester(bot, ct, core)
    if tile is None:
        return False

    bot.lastHarvesterVerifyRound = round_n
    if not hasattr(bot, "verifiedHarvesters"):
        bot.verifiedHarvesters = {}
    bot.verifiedHarvesters[(tile.x, tile.y)] = round_n
    bot.oreSite = tile
    bot.relayOrigin = tile
    bot.relayPath = None
    bot.relayBuiltTo = -1
    bot.inRelay = True
    bot.relayVerify = True
    relayToCore(bot, ct)
    return bot.inRelay


ORE_RECHECK_CLEAR_INTERVAL = 50


def moveTowardLeash(
    bot, ct: Controller, core_pos: Position, *, radius: int = DEFEND_HOME_CHEBYSHEV
) -> bool:
    if ct.get_move_cooldown() != 0:
        return False
    pos = ct.get_position()
    if chebyshevToCore(pos, core_pos) <= radius:
        return False
    local_map = bot.hivemind.local_map

    def ok(p: Position) -> bool:
        if p == pos:
            return True
        if not inBounds(ct, p):
            return False
        if _blockedByEnemyBot(ct, p):
            return False
        return walkPassable(ct, p, local_map=local_map)

    path = astarFromSeeds(
        [pos],
        is_passable=ok,
        is_goal=lambda p: chebyshevToCore(p, core_pos) <= radius,
        heuristic=lambda p: max(0, chebyshevToCore(p, core_pos) - radius),
        max_iters=PATH_MAX_ITERS,
    )

    if not path or len(path) < 2:
        step = nearestCardinal(pos.direction_to(core_pos))
        if step != Direction.CENTRE and ct.can_move(step):
            ct.move(step)
            return True
        return False

    nxt = path[1]
    step = nearestCardinal(pos.direction_to(nxt))
    if step != Direction.CENTRE and ct.can_move(step):
        ct.move(step)
        bot.cachedPath = None  # this path is region-goal-shaped, not moveToward's
        return True
    return False


def _safeCoreStandTile(bot, ct: Controller, core: Position) -> Position | None:
    from offense import _enemyGunners, _tileUnderGunnerFire

    gunners = _enemyGunners(ct)
    pos = ct.get_position()
    footprint = coreFootprint(core)
    on_core = {(t.x, t.y) for t in footprint}
    local_map = bot.hivemind.local_map
    best: Position | None = None
    best_key: tuple[int, int, int] | None = None
    for tile in footprint:
        for d in CARDINALS:
            cand = tile.add(d)
            if (cand.x, cand.y) in on_core:
                continue
            if not inBounds(ct, cand):
                continue
            if cand != pos and not walkPassable(ct, cand, local_map=local_map):
                continue
            if gunners and _tileUnderGunnerFire(ct, cand, gunners):
                continue
            key = (manhattan(pos, cand), cand.x, cand.y)
            if best_key is None or key < best_key:
                best_key = key
                best = cand
    return best


def defendHome(bot, ct: Controller) -> bool:
    if bot.inRelay or getattr(bot, "recheckingOre", False):
        return False
    if not nearCore(bot, ct):
        return False

    if bot.corePos is None or ct.get_action_cooldown() != 0:
        return False
    for tile in coreFootprint(bot.corePos):
        bid = None
        if ct.is_in_vision(tile):
            bid = ct.get_tile_building_id(tile)
        if bid is None or ct.get_entity_type(bid) != EntityType.CORE:
            continue
        if ct.get_team(bid) != ct.get_team():
            continue
        if ct.get_hp(bid) >= 400:
            return False
        pos = ct.get_position()
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
        stand = _safeCoreStandTile(bot, ct, bot.corePos)
        if stand is None or stand == pos:
            return False
        return moveToward(bot, ct, stand)
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
        EntityType.CONVEYOR,
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
    orphan = _pickOrphanRelay(bot, ct)
    if orphan is not None:
        return orphan

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
            return (manhattanToCore(ore, core), manhattan(ore, pos))
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
        elif ct.get_entity_type(bid) == EntityType.HARVESTER and _harvesterOwnerAllowed(
            ct, ct.get_team(bid)
        ):
            consider(tile, kind="harvester")
        elif _shouldClearAllyOreBlock(ct, tile, bot=bot):
            consider(tile, kind="blocked")

    for ore in knownOreTiles(bot):
        key_xy = (ore.x, ore.y)
        if key_xy in skipped:
            continue
        if ct.is_in_vision(ore):
            bid = ct.get_tile_building_id(ore)
            if bid is None:
                consider(ore, kind="empty")
            elif ct.get_entity_type(bid) == EntityType.HARVESTER and _harvesterOwnerAllowed(
                ct, ct.get_team(bid)
            ):
                consider(ore, kind="harvester")
            elif _shouldClearAllyOreBlock(ct, ore, bot=bot):
                consider(ore, kind="blocked")
            continue
        entry = bot.hivemind.local_map.get(key_xy, {})
        ent = entry.get("entity")
        if ent and _oreLedgerStale(bot, entry):
            ent = None
        if ent and ent.get("type") == EntityType.HARVESTER and _harvesterOwnerAllowed(
            ct, ent.get("team")
        ):
            consider(ore, kind="harvester")
        elif not ent:
            consider(ore, kind="empty")

    if empty_best is not None:
        return empty_best
    if blocked_best is not None:
        return blocked_best
    return harv_best


def _enemyBarrierAt(bot, ct: Controller, tile: Position) -> bool:
    ent = tileEntity(ct, tile, local_map=bot.hivemind.local_map)
    if not ent or ent.get("team") == ct.get_team():
        return False
    return ent.get("type") == EntityType.BARRIER


_ALLY_DOOR_TYPES = (
    EntityType.BARRIER,
    EntityType.LAUNCHER,
    EntityType.SENTINEL,
    EntityType.GUNNER,
)


def _allyDoorAt(bot, ct: Controller, tile: Position) -> bool:
    ent = tileEntity(ct, tile, local_map=bot.hivemind.local_map)
    if not ent or ent.get("team") != ct.get_team():
        return False
    return ent.get("type") in _ALLY_DOOR_TYPES


def _isDoor(bot, ct: Controller, tile: Position) -> bool:
    return _enemyBarrierAt(bot, ct, tile) or _allyDoorAt(bot, ct, tile)


def findBlockingDoor(
    bot, ct: Controller, target: Position, *, relay: bool = False
) -> Position | None:
    local_map = bot.hivemind.local_map
    pos = ct.get_position()

    def ok(p: Position) -> bool:
        if p == pos:
            return True
        if not inBounds(ct, p):
            return False
        if _isDoor(bot, ct, p):
            return True  # pretend it is open — this is what we are looking for
        if relay:
            return relayPassable(ct, p, local_map=local_map)
        if _blockedByEnemyBot(ct, p):
            return False
        return walkPassable(ct, p, local_map=local_map)

    is_goal = (
        (lambda p: isCoreAdjacent(ct, p, target))
        if relay
        else (lambda p: p == target)
    )

    path = astarFromSeeds(
        [pos],
        is_passable=ok,
        is_goal=is_goal,
        heuristic=lambda p: manhattan(p, target),
        max_iters=PATH_MAX_ITERS,
    )
    if not path:
        return None
    for tile in path:
        if _isDoor(bot, ct, tile):
            return tile
    return None


def _clearBlockageState(bot) -> None:
    bot.lastPathFailed = False
    bot.lastPathFailedTarget = None
    bot.relayPathBlocked = False
    bot.relayPathBlockedFrom = None
    bot.cachedPath = None
    bot.cachedPathTarget = None
    bot.stuck = 0


def _blockedRouteTarget(bot, ct: Controller) -> tuple[Position, bool] | None:
    site = bot.oreSite
    if site is not None and pathKnownBlocked(bot, site):
        return site, False
    if getattr(bot, "relayPathBlocked", False):
        core_pos = _allyCorePos(bot, ct)
        if core_pos is not None:
            return core_pos, True
    return None


def _openAllyDoor(bot, ct: Controller, tile: Position) -> bool:
    pos = ct.get_position()
    if isAdjacent(pos, tile):
        if not ct.can_destroy(tile):
            return False
        ct.destroy(tile)
        bot.blockadeDoor = None
        _clearBlockageState(bot)
        return True
    return moveToward(bot, ct, tile)


def openBlockedRoute(bot, ct: Controller) -> bool:
    from main import OPEN_BLOCKED_ROUTE, ORE_DEADLOCK_TURNS

    if not OPEN_BLOCKED_ROUTE:
        return False
    if bot.inRelay and not getattr(bot, "relayPathBlocked", False):
        return False

    from guns import allyGunCovering, placeGun

    door = getattr(bot, "blockadeDoor", None)
    if door is not None:
        tile = Position(door[0], door[1])
        stalled = (
            ORE_DEADLOCK_TURNS
            and getattr(bot, "stuck", 0) >= ORE_DEADLOCK_TURNS
        )
        if stalled or not _isDoor(bot, ct, tile):
            bot.blockadeDoor = None
        elif _allyDoorAt(bot, ct, tile):
            return _openAllyDoor(bot, ct, tile)
        elif allyGunCovering(ct, tile):
            bot.blockadeDoor = None  # already answered
        else:
            return placeGun(bot, ct, tile)

    blocked = _blockedRouteTarget(bot, ct)
    if blocked is None:
        return False
    target, relay_mode = blocked
    found = findBlockingDoor(bot, ct, target, relay=relay_mode)
    if found is None:
        return False  # terrain, core or harvester — caller blacklists the node
    bot.blockadeDoor = (found.x, found.y)
    if _allyDoorAt(bot, ct, found):
        return _openAllyDoor(bot, ct, found)
    if allyGunCovering(ct, found):
        bot.blockadeDoor = None
        return False
    return placeGun(bot, ct, found)


def breakOreDeadlock(bot, ct: Controller) -> bool:
    from main import ORE_DEADLOCK_TURNS

    if ORE_DEADLOCK_TURNS is None or ORE_DEADLOCK_TURNS <= 0:
        return False
    if bot.inRelay:
        return False
    proven_blocked = bot.oreSite is not None and pathKnownBlocked(bot, bot.oreSite)
    if not proven_blocked and getattr(bot, "stuck", 0) < ORE_DEADLOCK_TURNS:
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
    if bot.inRelay:
        return False

    round_n = ct.get_current_round()
    last = getattr(bot, "lastOreRecheckRound", -999)
    if round_n - last >= ORE_RECHECK_CLEAR_INTERVAL:
        bot.skippedOres = set()
        bot.lastOreRecheckRound = round_n

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


def exploreFrontier(bot, ct: Controller, *, outward: bool = False) -> bool:
    from main import FRONTIER_REFRESH_TURNS, ORE_DEADLOCK_TURNS
    from pathing import findFrontierTarget

    if FRONTIER_REFRESH_TURNS is None or FRONTIER_REFRESH_TURNS <= 0:
        return False
    if bot.inRelay:
        return False

    local_map = bot.hivemind.local_map
    pos = ct.get_position()
    round_n = ct.get_current_round()

    target = getattr(bot, "frontierTarget", None)
    if target is not None:
        stale = round_n - getattr(bot, "frontierRound", -(10**9)) >= FRONTIER_REFRESH_TURNS
        stalled = ORE_DEADLOCK_TURNS and getattr(bot, "stuck", 0) >= ORE_DEADLOCK_TURNS
        if stale or stalled or target in local_map or (pos.x, pos.y) == target:
            target = None

    if target is None:
        found = findFrontierTarget(
            ct,
            local_map,
            origin=pos,
            home=bot.corePos,
            outward=outward,
        )
        if found is None:
            bot.frontierTarget = None
            return False
        target = (found.x, found.y)
        bot.frontierTarget = target
        bot.frontierRound = round_n

    return moveToward(bot, ct, Position(target[0], target[1]))