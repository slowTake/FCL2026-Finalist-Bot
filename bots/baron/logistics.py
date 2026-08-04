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


def harvesterCap(round_n: int) -> int | None:
    from main import (
        HARVESTER_CAP_BEFORE_50,
        HARVESTER_CAP_BEFORE_100,
        HARVESTER_CAP_BEFORE_200,
        HARVESTER_CAP_BEFORE_250,
        HARVESTER_CAP_BEFORE_400,
    )

    if round_n < 50:
        return HARVESTER_CAP_BEFORE_50
    if round_n < 100:
        return HARVESTER_CAP_BEFORE_100
    if round_n < 200:
        return HARVESTER_CAP_BEFORE_200
    if round_n < 250:
        return HARVESTER_CAP_BEFORE_250
    if round_n < 400:
        return HARVESTER_CAP_BEFORE_400
    return None


def atHarvesterCap(ct: Controller) -> bool:
    from main import SLOT_HARVESTER_COUNT

    cap = harvesterCap(ct.get_current_round())
    if cap is None:
        return False
    return ct.read_store(SLOT_HARVESTER_COUNT) >= cap


def clearRelay(bot) -> None:
    bot.inRelay = False
    bot.relayOrigin = None
    bot.relayPath = None
    bot.relayBuiltTo = -1
    bot.relayStuck = 0


def abortRelay(bot) -> None:
    clearRelay(bot)


def markRelayComplete(ct: Controller) -> None:
    from main import SLOT_RELAY_COMPLETE

    ct.write_store(SLOT_RELAY_COMPLETE, 1)


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
    for tile in _coreFootprint(core_pos):
        if abs(pos.x - tile.x) + abs(pos.y - tile.y) == 1:
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


def pickEmptyOre(bot, ct: Controller) -> Position | None:
    pos = ct.get_position()
    core = bot.corePos
    best: Position | None = None
    best_key = None

    candidates: list[Position] = []
    for tile in ct.get_nearby_tiles():
        if ct.get_tile_env(tile) != Environment.ORE_TITANIUM:
            continue
        if ct.get_tile_building_id(tile) is not None:
            continue
        candidates.append(tile)

    for ore in knownOreTiles(bot):
        if ore in candidates:
            continue
        entry = bot.hivemind.local_map.get((ore.x, ore.y), {})
        ent = entry.get("entity")
        if ent and ent.get("type") == EntityType.HARVESTER:
            continue
        if ct.is_in_vision(ore) and ct.get_tile_building_id(ore) is not None:
            continue
        candidates.append(ore)

    for ore in candidates:
        if core is not None:
            key = (manhattan(ore, core), manhattan(ore, pos))
        else:
            key = (manhattan(ore, pos),)
        if best_key is None or key < best_key:
            best_key = key
            best = ore
    return best


def pickMaintainOre(bot, ct: Controller) -> Position | None:
    """Prefer sticky site, else nearest known ore with harvester or empty."""
    if bot.oreSite is not None:
        return bot.oreSite
    pos = ct.get_position()
    core = bot.corePos
    best: Position | None = None
    best_key = None
    for ore in knownOreTiles(bot):
        if core is not None:
            key = (manhattan(ore, core), manhattan(ore, pos))
        else:
            key = (manhattan(ore, pos),)
        if best_key is None or key < best_key:
            best_key = key
            best = ore
    # Vision harvesters
    for tile in ct.get_nearby_tiles():
        if ct.get_tile_env(tile) != Environment.ORE_TITANIUM:
            continue
        bid = ct.get_tile_building_id(tile)
        if bid is None:
            continue
        if ct.get_entity_type(bid) == EntityType.HARVESTER:
            if core is not None:
                key = (manhattan(tile, core), manhattan(tile, pos))
            else:
                key = (manhattan(tile, pos),)
            if best_key is None or key < best_key:
                best_key = key
                best = tile
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
    """Orchestrator: path → facing check → clear/place → move."""
    if bot.corePos is None:
        abortRelay(bot)
        return

    core_pos = bot.corePos
    if bot.relayOrigin is None:
        bot.relayOrigin = bot.oreSite or ct.get_position()
    bot.inRelay = True

    path = bot.relayPath
    if path is not None:
        bot.relayBuiltTo = extendRelayWatermark(
            ct, path, core_pos, bot.relayBuiltTo
        )

    if path is None:
        path = pickRelayPath(bot, ct, bot.relayOrigin, core_pos)
        bot.relayPath = path
        bot.relayBuiltTo = -1
        if path is None:
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
    if atHarvesterCap(ct):
        return False
    if bot.inRelay:
        return False

    from main import SLOT_HARVESTER_COUNT

    # Adjacent empty ore first
    if ct.get_action_cooldown() == 0:
        pos = ct.get_position()
        for d in Direction:
            if d == Direction.CENTRE:
                continue
            tile = pos.add(d)
            if not inBounds(ct, tile):
                continue
            if ct.can_build_harvester(tile):
                ct.build_harvester(tile)
                count = ct.read_store(SLOT_HARVESTER_COUNT)
                ct.write_store(SLOT_HARVESTER_COUNT, count + 1)
                bot.oreSite = tile
                bot.relayOrigin = tile
                bot.inRelay = True
                bot.relayPath = None
                bot.relayBuiltTo = -1
                return True

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
        # Empty — build if adjacent / move
        if atHarvesterCap(ct) and not bot.inRelay:
            return False
        from main import SLOT_HARVESTER_COUNT

        pos = ct.get_position()
        if abs(pos.x - tile.x) + abs(pos.y - tile.y) == 1:
            if ct.get_action_cooldown() == 0 and ct.can_build_harvester(tile):
                ct.build_harvester(tile)
                count = ct.read_store(SLOT_HARVESTER_COUNT)
                ct.write_store(SLOT_HARVESTER_COUNT, count + 1)
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
        return True

    if etype in _ORE_BLOCK_CLEAR:
        if team == mine:
            pos = ct.get_position()
            if abs(pos.x - tile.x) + abs(pos.y - tile.y) == 1:
                if ct.can_destroy(tile):
                    ct.destroy(tile)
                    return True
            return moveToward(bot, ct, tile)
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


DEFEND_HOME_CHEBYSHEV = 8
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


def _findDefendTarget(ct: Controller) -> Position | None:
    """Nearest enemy gunner/sentinel (prefer) or builder in this unit's vision."""
    me = ct.get_team()
    pos = ct.get_position()
    best_turret: Position | None = None
    best_turret_d = 10**9
    best_builder: Position | None = None
    best_builder_d = 10**9
    for eid in ct.get_nearby_units():
        if ct.get_team(eid) == me:
            continue
        etype = ct.get_entity_type(eid)
        epos = ct.get_position(eid)
        dist = manhattan(pos, epos)
        if etype in (EntityType.GUNNER, EntityType.SENTINEL):
            if dist < best_turret_d:
                best_turret_d = dist
                best_turret = epos
        elif etype == EntityType.BUILDER_BOT:
            if dist < best_builder_d:
                best_builder_d = dist
                best_builder = epos
    if best_turret is not None:
        return best_turret
    return best_builder


def defendHome(bot, ct: Controller) -> bool:
    """Near-core only: attack enemy combat in vision, else heal damaged core."""
    if bot.inRelay or getattr(bot, "recheckingOre", False):
        return False
    if not _nearCore(bot, ct):
        return False

    target = _findDefendTarget(ct)
    if target is not None:
        pos = ct.get_position()
        if abs(pos.x - target.x) + abs(pos.y - target.y) == 1:
            if ct.get_action_cooldown() == 0 and ct.can_fire(target):
                ct.fire(target)
                return True
            return False
        return moveToward(bot, ct, target)

    # No enemy in vision — heal core if HP < 500
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
    """Prefer empty ores, then any harvester; honor skippedOres."""
    skipped = getattr(bot, "skippedOres", set())
    pos = ct.get_position()
    core = bot.corePos
    empty_best: Position | None = None
    empty_key = None
    harv_best: Position | None = None
    harv_key = None

    def consider(ore: Position, *, empty: bool) -> None:
        nonlocal empty_best, empty_key, harv_best, harv_key
        key_xy = (ore.x, ore.y)
        if key_xy in skipped:
            return
        if core is not None:
            key = (manhattan(ore, core), manhattan(ore, pos))
        else:
            key = (manhattan(ore, pos),)
        if empty:
            if empty_key is None or key < empty_key:
                empty_key = key
                empty_best = ore
        else:
            if harv_key is None or key < harv_key:
                harv_key = key
                harv_best = ore

    for tile in ct.get_nearby_tiles():
        if ct.get_tile_env(tile) != Environment.ORE_TITANIUM:
            continue
        bid = ct.get_tile_building_id(tile)
        if bid is None:
            consider(tile, empty=True)
        elif ct.get_entity_type(bid) == EntityType.HARVESTER:
            consider(tile, empty=False)

    for ore in knownOreTiles(bot):
        key_xy = (ore.x, ore.y)
        if key_xy in skipped:
            continue
        if ct.is_in_vision(ore):
            bid = ct.get_tile_building_id(ore)
            if bid is None:
                consider(ore, empty=True)
            elif ct.get_entity_type(bid) == EntityType.HARVESTER:
                consider(ore, empty=False)
            continue
        entry = bot.hivemind.local_map.get(key_xy, {})
        ent = entry.get("entity")
        if ent and ent.get("type") == EntityType.HARVESTER:
            consider(ore, empty=False)
        elif not ent:
            consider(ore, empty=True)

    return empty_best if empty_best is not None else harv_best


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


def runDefault(bot, ct: Controller) -> None:
    """buildHarvester → maintainHarvester → defendHome → healAlly → recheckOreNodes."""
    if bot.corePos is None:
        from hivemind import CoreState, unpack_cores
        from main import SLOT_PACKED_CORES

        ally, enemy = unpack_cores(ct.read_store(SLOT_PACKED_CORES))
        if ally[0] != CoreState.UNKNOWN:
            bot.corePos = Position(ally[1], ally[2])
        if enemy[0] != CoreState.UNKNOWN:
            bot.enemyCorePos = Position(enemy[1], enemy[2])
        elif bot.hivemind.enemy_core_pos is not None:
            bot.enemyCorePos = bot.hivemind.enemy_core_pos

    if buildHarvester(bot, ct):
        return
    if maintainHarvester(bot, ct):
        return
    if defendHome(bot, ct):
        return
    if healAlly(bot, ct):
        return
    recheckOreNodes(bot, ct)


# Back-compat aliases used by older stubs
def run_default(bot, ct: Controller) -> None:
    runDefault(bot, ct)


def run_economy(bot, ct: Controller) -> None:
    runDefault(bot, ct)
