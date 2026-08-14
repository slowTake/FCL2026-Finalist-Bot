"""Relay laying. The network is the core: a new line grafts onto the nearest
connected tile rather than running its own lane home.

Tree invariant: every belt points at its parent, the root is the core, and a new
tile is only ever attached pointing into an already-connected member. Connectivity
therefore holds by construction and is never re-proven.

A relay is planned as `belts` (tiles we must cover) plus an `anchor` — the network
tile the last belt points into. The anchor is never walked on and never built on:
it is often the core, which a builder cannot stand on.
"""

from __future__ import annotations

from fcode import Controller, EntityType, Position

import pathfindingHelpers as ph
from movement import walkTo
from patience import (
    BLOCKED_WAIT_TURNS,
    CLEAR_WAIT_TURNS,
    clearHold,
    holdTask,
    waitTi,
)
from pathing import astarFromSeeds, bfs

_RELAY_MAX_ITERS = 450


def relayToHome(bot, ct: Controller) -> bool:
    """Cover every tile from the harvester back to the network, harvester end first.

    One rule: build the next uncovered tile if we're standing beside it, otherwise
    get beside it. Expressed that way the builder never needs to occupy a specific
    tile, so the terminal case (anchor = the 2x2 core) needs no special handling
    and a displaced builder self-corrects.
    """
    if bot.relayPath is None and not planRelay(bot, ct):
        return relayDone(bot, ct)

    belts, anchor = bot.relayPath, bot.relayAnchor
    idx = _nextUncovered(bot, ct, belts, anchor)
    if idx is None:
        return relayDone(bot, ct)

    tile = belts[idx]
    nxt = belts[idx + 1] if idx + 1 < len(belts) else anchor
    pos = ct.get_position()

    if pos.x == tile.x and pos.y == tile.y:
        return _stepOff(bot, ct, belts, idx)

    if abs(pos.x - tile.x) + abs(pos.y - tile.y) == 1:
        return layBelt(bot, ct, tile, nxt)

    return _walkBeside(bot, ct, tile)


def hijackRelay(bot, ct: Controller) -> bool:
    """Enemy harvester encountered: take over its output instead of building our own.

    Will call cutRelay(bot, ct) first once that exists — sever the enemy's line so
    the ore we're about to tap stops feeding their core — then relay it to ours.
    """
    return relayToHome(bot, ct)


# --- Planning ---------------------------------------------------------------


def planRelay(bot, ct: Controller) -> bool:
    if bot.corePos is None:
        return False

    ctx = ph.costContext(bot, ct, "relay")
    ctx.avoid |= bot.relayAvoid
    pos = ct.get_position()

    if ctx.isNetworkMember(pos):
        return False  # standing on the network: the harvester already feeds it

    # Goal is a tile *beside* the network, not on it. The anchor is frequently the
    # core, which no builder can stand on or build on, so a path that had to reach
    # it could never be walked.
    def isGoal(p: Position) -> bool:
        return any(ctx.isNetworkMember(n) for n in ph.orthogonalTiles(p))

    # No heuristic: the goal is a set, not a point, and manhattan-to-core can
    # overestimate the distance to a nearer network tile. Dijkstra is correct and
    # stops the moment it reaches the network, which is usually close.
    path = astarFromSeeds(
        [pos],
        costFn=ph.makeCostFn(ctx),
        isGoal=isGoal,
        maxIters=_RELAY_MAX_ITERS,
    )

    if path is None:
        path = bfs(
            pos,
            isPassable=lambda p: ph.beltPassable(ct, bot.hivemind, p),
            isGoal=isGoal,
            maxIters=_RELAY_MAX_ITERS,
        )

    if not path:
        bot.relayPath = None
        return False

    anchor = next(
        (n for n in ph.orthogonalTiles(path[-1]) if ctx.isNetworkMember(n)), None
    )
    if anchor is None:
        bot.relayPath = None
        return False

    bot.relayPath = path
    bot.relayAnchor = anchor
    bot.relayCtx = ctx
    return True


def _replanAvoiding(bot, ct: Controller, tile: Position) -> bool:
    ctx = bot.relayCtx
    if ctx is not None and ctx.isNetworkMember(tile):
        return False  # never blacklist the thing we are trying to reach
    bot.relayAvoid.add((tile.x, tile.y))
    bot.relayPath = None
    if not planRelay(bot, ct):
        return False
    clearHold(bot)  # new route, fresh patience — otherwise the spent counter
    return True     # would abandon it again immediately


def _nextUncovered(bot, ct: Controller, belts, anchor) -> int | None:
    """First belt tile, harvester end first, that isn't already a correct belt."""
    for i, tile in enumerate(belts):
        nxt = belts[i + 1] if i + 1 < len(belts) else anchor
        facing = ph.beltFacing(tile, nxt)
        if facing is None:
            continue
        t = ph.readTile(ct, bot.hivemind, tile)
        if t.etype in ph.BELT_TYPES and not t.enemy and t.facing == facing:
            continue
        return i
    return None


# --- Laying -----------------------------------------------------------------


def layBelt(bot, ct: Controller, tile: Position, nextTile: Position) -> bool:
    """Put a conveyor on `tile` facing `nextTile`. True if this consumed the turn."""
    facing = ph.beltFacing(tile, nextTile)
    if facing is None:
        return _replanAvoiding(bot, ct, tile)

    t = ph.readTile(ct, bot.hivemind, tile)

    if t.etype in ph.BELT_TYPES and not t.enemy:
        # ours, pointing the wrong way. destroy is free and costs no cooldown,
        # so the replacement goes down in the same turn.
        if not ct.can_destroy(tile):
            return holdTask(bot, "wrongFacing", BLOCKED_WAIT_TURNS) or _replanAvoiding(
                bot, ct, tile
            )
        ct.destroy(tile)
        ph.invalidateTile(bot.hivemind, tile)
    elif t.etype is not None or t.builder:
        return clearRelayObstruction(bot, ct, tile, hostile=t.enemy)

    if ct.get_action_cooldown() != 0:
        return holdTask(bot, "cooldown", BLOCKED_WAIT_TURNS)

    if waitTi(bot, ct, ct.get_conveyor_cost()):
        return True
    if ct.get_global_resources() < ct.get_conveyor_cost():
        # Waited out the patience budget and still broke. Keep waiting anyway:
        # passive income is guaranteed, so the tile is fine and only our balance
        # isn't. Falling through would blacklist it and tear up a good route.
        return True

    if ct.can_build_conveyor(tile, facing):
        ct.build_conveyor(tile, facing)
        ph.invalidateTile(bot.hivemind, tile)
        clearHold(bot)
        return True

    return _replanAvoiding(bot, ct, tile) or holdTask(
        bot, "unbuildable", BLOCKED_WAIT_TURNS
    )


def _stepOff(bot, ct: Controller, belts, idx: int) -> bool:
    """We're standing on the tile we need to belt. Move aside — preferring the next
    tile along the relay, so stepping off is also progress."""
    if ct.get_move_cooldown() != 0:
        return True

    pos = ct.get_position()
    prefer = belts[idx + 1] if idx + 1 < len(belts) else None
    options = [prefer] if prefer is not None else []
    options += [p for p in ph.orthogonalTiles(pos) if p != prefer]

    for tile in options:
        d = ph.beltFacing(pos, tile)
        if d is not None and ph.canStandNow(ct, tile) and ct.can_move(d):
            ct.move(d)
            clearHold(bot)
            return True

    return holdTask(bot, "boxedIn", BLOCKED_WAIT_TURNS)


def _walkBeside(bot, ct: Controller, tile: Position) -> bool:
    """Get orthogonally adjacent to `tile` without standing on it."""
    def beside(p: Position) -> bool:
        return abs(p.x - tile.x) + abs(p.y - tile.y) == 1

    if walkTo(bot, ct, beside, tile):
        return True
    return holdTask(bot, "approach", BLOCKED_WAIT_TURNS)


def clearRelayObstruction(bot, ct: Controller, target: Position, *, hostile: bool) -> bool:
    """A* already priced this obstruction and picked the route through it, so the
    question here is only *how*, not *whether*.

    Hostile: grind it down (2 Ti for 2 damage, one per turn). Friendly: path
    around by default — the cost isn't the free destroy(), it's losing our own
    building.
    """
    t = ph.readTile(ct, bot.hivemind, target)

    if not hostile and t.etype == EntityType.HARVESTER:
        return _replanAvoiding(bot, ct, target)  # ours, on ore. never.

    if hostile:
        if ct.get_action_cooldown() == 0 and ct.can_fire(target):
            ct.fire(target)
            return True
        return holdTask(bot, "clear", CLEAR_WAIT_TURNS) or _replanAvoiding(
            bot, ct, target
        )

    if _replanAvoiding(bot, ct, target):
        return True
    if ct.can_destroy(target):   # genuinely walled in: our own belt is the only door
        ct.destroy(target)
        return True
    return holdTask(bot, "blocked", BLOCKED_WAIT_TURNS)


def relayDone(bot, ct: Controller) -> bool:
    bot.relayPath = None
    bot.relayAnchor = None
    bot.relayCtx = None
    bot.relayFrom = None
    bot.relayAvoid.clear()
    bot.inRelay = False
    bot.walkPath = None
    clearHold(bot)
    return False
