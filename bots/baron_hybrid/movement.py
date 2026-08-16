"""Generic movement: pathing to a tile/region, core-adjacency checks, and
clearing a route blocked by a demolishable ally/enemy building.

Split out of logistics.py — this module has no notion of ore, harvesters, or
belts. `logistics.py` (relay/belt mechanics) and `logistics_helpers.py`
(ore economy, home upkeep) both build on top of it; it imports nothing from
either, so there is no import cycle to worry about.
"""

from __future__ import annotations

from fcode import Controller, Direction, EntityType, Position

from geometry import (
    DEFEND_HOME_CHEBYSHEV,
    chebyshevToCore,
    coreFootprint,
    isAdjacent,
    tileEntity,
)
from pathing import (
    CARDINALS,
    astarFromSeeds,
    inBounds,
    manhattan,
    nearestCardinal,
    relayPassable,
    walkPassable,
)

PATH_MAX_ITERS = 800


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
    for tile in coreFootprint(core_pos):
        if isAdjacent(pos, tile):
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
    for ctile in coreFootprint(enemy_core):
        if isAdjacent(tile, ctile):
            return True
    return False


def travelFacing(from_pos: Position, to_pos: Position) -> Direction:
    return nearestCardinal(from_pos.direction_to(to_pos))


def facingIntoCore(pos: Position, core_pos: Position) -> Direction:
    """Cardinal facing from `pos` into the core it is standing next to.

    First adjacent footprint tile wins — the old "nearest" tie-break was
    vestigial, since every candidate that passes the adjacency filter is at
    distance 1 by definition and so could never beat the first one found.
    Falls back to a crow-flies heading when `pos` is not actually adjacent.
    """
    for tile in coreFootprint(core_pos):
        if not isAdjacent(pos, tile):
            continue
        best = travelFacing(pos, tile)
        if best != Direction.CENTRE:
            return best
    return nearestCardinal(pos.direction_to(core_pos))


def _notePathVerdict(bot, target: Position, failed: bool) -> None:
    """Record whether A* could reach `target` on the known map.

    `moveToward` returns a bare False for three unrelated situations — move
    cooldown, a legal step that `can_move` refused, and *no route exists at all* —
    so nothing upstream could tell "wait a turn" from "that ore is walled off".
    `breakOreDeadlock` therefore blamed the ore node and re-picked another one
    behind the same wall, forever. The verdict is paired with the target because a
    single turn may call `moveToward` for several different goals as the role chain
    falls through; a bare flag would describe whichever ran last.

    Only set on turns that actually planned — under cooldown the unit has learned
    nothing new, so the previous verdict stands.
    """
    bot.lastPathFailed = failed
    bot.lastPathFailedTarget = (target.x, target.y) if failed else None


def pathKnownBlocked(bot, target: Position) -> bool:
    """True if the last planned route to `target` proved unreachable.

    Conservative by construction. `walkPassable` treats tiles that are neither in
    vision nor in `local_map` as passable, so A* routes optimistically through fog
    and only fails once the known map *proves* the region is sealed. It errs toward
    "reachable", which is the right direction — we would rather walk out and
    discover a wall than wrongly abandon ore.
    """
    if not getattr(bot, "lastPathFailed", False):
        return False
    return getattr(bot, "lastPathFailedTarget", None) == (target.x, target.y)


def _blockedByEnemyBot(ct: Controller, p: Position) -> bool:
    """True only if an *enemy* builder bot currently occupies `p`.

    An ally bot on the tile is not a real obstacle — build legality
    (`can_build_*`) never cares who else is standing nearby, and the occupant will
    almost certainly have moved on by the time a multi-tile route actually reaches
    it. Treating it as passable here still costs nothing: the one genuinely
    immovable case, stepping onto a tile that is occupied *right now*, is already
    caught downstream by `can_move` refusing that single step. Blocking on it in
    A* instead used to make every route through a patrolling guardian read as
    sealed, stalling relays and leash returns that were never actually blocked
    (see the entity-11/entity-14 contention in the 2026-08-09 drumlin game).
    An enemy bot is kept as a real obstacle: unlike an ally, it may be parked
    there on purpose and isn't something we can coordinate around.
    """
    if not ct.is_in_vision(p):
        return False
    occ = ct.get_tile_builder_bot_id(p)
    return occ is not None and ct.get_team(occ) != ct.get_team()


def allyOccupied(ct: Controller, p: Position) -> bool:
    """True if a visible ally builder bot other than self currently stands on `p`.

    Distinct from `_blockedByEnemyBot` / A*'s ally-blind pathing above: that
    design is deliberately about routing *through* an ally, which is fine
    since the ally will likely have moved on by the time a multi-tile path
    gets there. Picking a *destination* tile is different — if a teammate is
    already standing on the one good tile, targeting it anyway just means
    walking up to it and getting refused by can_move forever. Candidate
    selection call sites should skip an ally-occupied tile, not target it.
    """
    if not ct.is_in_vision(p):
        return False
    occ = ct.get_tile_builder_bot_id(p)
    if occ is None or occ == ct.get_id():
        return False
    return ct.get_team(occ) == ct.get_team()


def moveToward(
    bot, ct: Controller, target: Position, *, avoid: set[tuple[int, int]] | None = None
) -> bool:
    """`avoid`: tiles to route around even though they're otherwise walkable —
    e.g. a harasser's recent-dodge memory, which stays valid after the threat
    that made a tile dangerous drops out of vision (see offense._dodgeMemory).
    Plain walkability (`walkPassable`) has no notion of that, so callers that
    care about it must pass it in explicitly; default is unchanged behaviour.
    """
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
            # Budget spent for this turn — a role chain that falls through several
            # move-toward steps (denyOre → seekHarvester's per-candidate loop →
            # seekCore's orbit fallback) can otherwise stack 5-15 full 800-iteration
            # searches in one call, which is the actual cost behind an exec-time
            # TLE. Skip straight to the direct-step fallback below instead.
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
            # Only `path is None` means "no route on the known map". A short path is
            # a planner quirk, not proof of blockage, so it must not read as
            # unreachable.
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


def moveTowardLeash(
    bot, ct: Controller, core_pos: Position, *, radius: int = DEFEND_HOME_CHEBYSHEV
) -> bool:
    """Path back inside `radius` Chebyshev of the core — not toward `core_pos` itself.

    `moveToward(bot, ct, core_pos)` targets the literal core tile, which is a
    building and therefore never passes `is_passable`. A*'s neighbour expansion
    filters on that before a node is ever added to the open set (`pathing.py`'s
    `astarFromSeeds`), so the core can never be reached as a goal — the search
    always returns `None` and every leash recall silently degrades to
    `moveToward`'s blind single-cardinal-step fallback, which has no obstacle
    avoidance at all. On an open map that nudge usually happens to work; on a
    tight map (e.g. Heart) a wall between the guardian and home blocks that one
    step outright, and the guardian is stuck outside the leash for good — it
    reads as "not returning" but is actually failing to path at all.

    Targeting the *region* (any walkable tile within `radius`) instead of a
    single unreachable point lets A* actually route around the wall.
    """
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
        # Last resort only — no obstacle avoidance, same as moveToward's own
        # fallback. Kept so open ground still gets a nudge if A* found nothing.
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


# ------------------------------------------------------------------
# Blocked-route recovery — demolish or gun down whatever is sealing a lane
# ------------------------------------------------------------------


def _enemyBarrierAt(bot, ct: Controller, tile: Position) -> bool:
    """Enemy barrier on tile, from vision or the ledger."""
    ent = tileEntity(ct, tile, local_map=bot.hivemind.local_map)
    if not ent or ent.get("team") == ct.get_team():
        return False
    return ent.get("type") == EntityType.BARRIER


# Ally buildings we are willing to demolish to reopen a sealed route. `destroy` is
# allied-only, free and costs no action cooldown, so an ally door is the cheapest of
# all — but the list is deliberately short. The CORE is never a candidate, and
# neither is a HARVESTER: 20 Ti and actively producing, so if one seals the only
# corridor the node gets blacklisted instead. Conveyors and splitters are already
# walkable, so they never block in the first place.
_ALLY_DOOR_TYPES = (
    EntityType.BARRIER,
    EntityType.LAUNCHER,
    EntityType.SENTINEL,
    EntityType.GUNNER,
)


def _allyDoorAt(bot, ct: Controller, tile: Position) -> bool:
    """Ally building on tile that we would demolish to reopen a route."""
    ent = tileEntity(ct, tile, local_map=bot.hivemind.local_map)
    if not ent or ent.get("team") != ct.get_team():
        return False
    return ent.get("type") in _ALLY_DOOR_TYPES


def _isDoor(bot, ct: Controller, tile: Position) -> bool:
    return _enemyBarrierAt(bot, ct, tile) or _allyDoorAt(bot, ct, tile)


def findBlockingDoor(
    bot, ct: Controller, target: Position, *, relay: bool = False
) -> Position | None:
    """The building sealing the route to `target`, or None.

    `pathKnownBlocked` says a route does not exist; it cannot say what to remove.
    Replanning with doors treated as *passable* answers that: if a route appears,
    the first door along it is the tile holding the lane shut.

    `relay=True` plans with `relayPassable` instead of `walkPassable`, because a
    relay needs a *layable* corridor, which is stricter than a walkable one — a
    belt cannot be laid where a builder can happily stand. In relay mode `target`
    is the ally core position, not an exact tile: the goal is core-adjacency
    (`isCoreAdjacent`, the same test `pickRelayPath` itself uses), because the
    core's 2x2 footprint has no single tile a route "arrives at". Passing the
    relay's *origin* here instead — the far end the corridor was built *from* —
    would search backward along belts that are already laid and already known
    passable, finding a path with no door on it every time, since the actual
    blockage sits forward, between here and the core.

    None means the blockage is something we will not open: terrain, the ally core,
    or an ally harvester. The caller falls through to blacklisting the node.

    This never second-guesses the pathfinder. Both planners already route *around*
    obstructions — `relayPassable` rejects `_RELAY_BLOCK_TYPES` and
    `findPathAroundObstruction` is the explicit fallback — so by the time we get
    here A* has proven there is no way around, which only happens at a choke.
    """
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
    """Forget the stale "no route" verdicts after acting on a door.

    Without this, `breakOreDeadlock` runs next turn on a verdict recorded *before*
    we opened the lane and blacklists the very node we just unblocked. `bot.stuck`
    matters for the same reason: a builder that stood still for twenty turns would
    otherwise trip the deadlock breaker the moment the door came down.
    """
    bot.lastPathFailed = False
    bot.lastPathFailedTarget = None
    bot.relayPathBlocked = False
    bot.relayPathBlockedFrom = None
    bot.cachedPath = None
    bot.cachedPathTarget = None
    bot.stuck = 0


def _blockedRouteTarget(bot, ct: Controller) -> tuple[Position, bool] | None:
    """What is sealed and how to plan for it: (target, relay_mode), or None.

    Walking comes first because it is the more common stall; a blocked relay only
    matters once the builder can actually stand where it needs to build.

    The relay branch's target is the ally core, not `relayPathBlockedFrom` (the
    relay's origin) — `findBlockingDoor(relay=True)` treats it as a core-adjacency
    goal, not an exact tile; see that function's docstring for why the origin
    itself is the wrong target to search toward.
    """
    site = bot.oreSite
    if site is not None and pathKnownBlocked(bot, site):
        return site, False
    if getattr(bot, "relayPathBlocked", False):
        core_pos = _allyCorePos(bot, ct)
        if core_pos is not None:
            return core_pos, True
    return None


def _openAllyDoor(bot, ct: Controller, tile: Position) -> bool:
    """Demolish an ally building sealing the lane. Free, no action cooldown."""
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
    """Open the door sealing our route instead of giving up on the node.

    Two kinds of door, and the cheaper one is ours. An **ally** building comes down
    with `destroy` — allied-only, free, no action cooldown. An **enemy** barrier gets
    a gunner aimed at it: 3 shots, 6 Ti of ammo, ~3 turns, against a builder's 15
    turns and 30 Ti, and the builder is free again after one turn instead of pinned
    for fifteen. `_GUNNER_PRIORITY` already ranks BARRIER above belts, so `gunnerLoop`
    shoots it with no new machinery, and the gunner then stays on the lane killing
    re-walls: they pay 3 Ti to rebuild, we pay 6 Ti of ammo to reopen — losing on Ti,
    but the lane stays open, which is worth more than the delta.

    Triggers on a blocked **walk** to the ore claim or a blocked **relay** from its
    origin. The relay case needs its own verdict because `pathKnownBlocked` only
    describes `moveToward`; a relay sealed by our own gunner aborts silently and
    would otherwise never reach here.

    The door is sticky. `placeGun` walks to its pad via `moveToward`, which rewrites
    the path verdict to describe the *pad* — so re-deriving the door each turn would
    lose it mid-approach and let the economy walk the builder back at the ore.
    Ping-pong is the failure mode this codebase keeps hitting, so the claim is held
    until the door is gone, an ally gun covers it, or the builder stalls out.
    """
    from main import OPEN_BLOCKED_ROUTE, ORE_DEADLOCK_TURNS

    if not OPEN_BLOCKED_ROUTE:
        return False
    # The relay-blocked verdict (`relayPathBlocked`) can only ever be set while
    # `bot.inRelay` is True — it's written by `pickRelayPath`, which only runs
    # from inside `relayToCore`. A bare `bot.inRelay` bail here (the pre-fix
    # behavior) therefore shut this whole function off in exactly the state the
    # relay branch below exists to handle: "a relay sealed by our own gunner" per
    # this function's own docstring. A relay reaching almost to the core and
    # dying on a self-placed gunner, with no attempt to knock it down, was that
    # gap — the walk-blocked branch still only makes sense outside a relay, so it
    # stays gated.
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

