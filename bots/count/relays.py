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
from pathing import astarFromSeeds, bfsFromSeeds

_RELAY_MAX_ITERS = 450

# How many times this relay will re-lay one tile before conceding it. A belt we
# place and someone else immediately replaces is contention, not progress —
# taking it a third time only restarts the loop.
BELT_CONTEST_LIMIT = 2


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


def hijackRelay(bot, ct: Controller, harvester: Position) -> bool:
    """Tap an enemy harvester instead of building our own.

    A harvester delivers its stack to any cardinally adjacent building regardless
    of team, so a belt beside it feeds our network without its ownership ever
    changing — the node costs us belts alone.

    Will call cutRelay(bot, ct, harvester) first once that exists: sever the
    enemy's own line out of the node so the stack has nowhere to go but ours.
    """
    bot.relayFrom = harvester
    bot.inRelay = True
    # Tap it, don't demolish it — keep the route off the harvester's own tile.
    bot.relayAvoid.add((harvester.x, harvester.y))
    clearHold(bot)
    return relayToHome(bot, ct)


# --- Planning ---------------------------------------------------------------


def planRelay(bot, ct: Controller) -> bool:
    if bot.corePos is None:
        return False

    ctx = ph.costContext(bot, ct, "relay")
    ctx.avoid |= bot.relayAvoid

    # Seed at the harvester, not at wherever the builder happens to be standing.
    # A relay exists to serve one harvester, so its first belt must be cardinally
    # adjacent to it — otherwise a mid-relay replan silently re-roots the line and
    # leaves the harvester with nothing beside it.
    source = bot.relayFrom
    if source is None:
        return False

    # Merge onto any ally belt, not only a proven-network one. buildNetwork can
    # only recognise a belt whose whole chain to the core this unit personally
    # knows (vision + ledger); a builder beside a belt usually can't see that
    # belt's downstream, so it fails the proof and — with the strict check —
    # plows through the belt toward the core, re-facing a line that was already
    # delivering. Trusting any ally belt (core ∪ all ally belts) stops the relay
    # at the nearest existing belt instead. In the healthy case every nearby
    # ally belt is already a network member, so this is identical to the old
    # check; it only diverges where an unrecognised belt exists — the churn case.
    def mergeable(p: Position) -> bool:
        return ctx.isNetworkMember(p) or ph.isAllyBelt(ct, bot.hivemind, p)

    if any(mergeable(n) for n in ph.orthogonalTiles(source)):
        return False  # already feeding a belt; nothing to lay

    seeds = [
        n for n in ph.orthogonalTiles(source)
        if ph.beltPassable(ct, bot.hivemind, n) and (n.x, n.y) not in ctx.avoid
    ]
    if not seeds:
        return False

    # Goal is a tile *beside* a merge target, not on it: you can't build on an
    # occupied belt, and the target is frequently the core, which no builder can
    # stand on either.
    def isGoal(p: Position) -> bool:
        if mergeable(p):
            return False
        return any(mergeable(n) for n in ph.orthogonalTiles(p))

    # No heuristic: the goal is a set, not a point, and manhattan-to-core can
    # overestimate the distance to a nearer merge tile. Dijkstra is correct and
    # stops the moment it reaches one, which is usually close.
    path = astarFromSeeds(
        seeds,
        costFn=ph.makeCostFn(ctx),
        isGoal=isGoal,
        maxIters=_RELAY_MAX_ITERS,
    )

    if path is None:
        # Same avoid set the seeds were filtered against and A* just used —
        # otherwise the fallback routes straight back through tiles this relay
        # has already conceded.
        path = bfsFromSeeds(
            seeds,
            isPassable=lambda p: (
                ph.beltPassable(ct, bot.hivemind, p)
                and (p.x, p.y) not in ctx.avoid
            ),
            isGoal=isGoal,
            maxIters=_RELAY_MAX_ITERS,
        )

    if not path:
        bot.relayPath = None
        return False

    anchor = next(
        (n for n in ph.orthogonalTiles(path[-1]) if mergeable(n)), None
    )
    if anchor is None:
        bot.relayPath = None
        return False

    bot.relayPath = path
    bot.relayAnchor = anchor
    return True


def _replanAvoiding(bot, ct: Controller, tile: Position) -> bool:
    # Ask for the context rather than holding one from planRelay: costContext
    # rebuilds every round, so a stored one goes stale within a turn or two
    # while the network keeps growing under it — and this guard consulting an
    # old network is exactly how the anchor gets blacklisted. Round-cached, so
    # asking again costs nothing.
    ctx = ph.costContext(bot, ct, "relay")
    if ctx.isNetworkMember(tile):
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

    if _pointsBackAtUs(bot, ct, tile, nextTile):
        # nextTile is an ally belt already facing into `tile`. Building here would
        # make a closed 2-cycle that reaches nothing — and if nextTile is our
        # anchor, it means the network said "connected" about something that
        # isn't. Route around rather than trust it.
        return _replanAvoiding(bot, ct, tile)

    t = ph.readTile(ct, bot.hivemind, tile)
    replacing = False

    if t.etype in ph.BELT_TYPES and not t.enemy:
        if _contested(bot, tile):
            # We have already rebuilt this tile more than anyone should need to.
            # Someone else wants it facing another way; taking it again just
            # restarts the loop. Concede the tile and go around.
            return _replanAvoiding(bot, ct, tile) or relayDone(bot, ct)
        # ours, pointing the wrong way. destroy is free and costs no cooldown,
        # so the replacement goes down in the same turn.
        if not ct.can_destroy(tile):
            return holdTask(bot, "wrongFacing", BLOCKED_WAIT_TURNS) or _replanAvoiding(
                bot, ct, tile
            )
        ct.destroy(tile)
        ph.invalidateTile(bot.hivemind, tile)
        replacing = True
    elif t.etype is not None:
        # Only a *building* blocks a belt. A builder standing here does not:
        # conveyors can be built under any bot, ours or theirs. Treating an
        # occupied tile as an obstruction is what made a builder blacklist it and
        # re-path across a neighbour's relay.
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
        if replacing:
            # Churn, not progress. Clearing the hold here is what made a
            # destroy-rebuild war invisible: every turn of it contains a
            # successful build, so the counter never accumulated and no give-up
            # path ever fired.
            _countRebuild(bot, tile)
        else:
            clearHold(bot)
        return True

    return _replanAvoiding(bot, ct, tile) or holdTask(
        bot, "unbuildable", BLOCKED_WAIT_TURNS
    )


def _pointsBackAtUs(bot, ct: Controller, tile: Position, nextTile: Position) -> bool:
    """True if nextTile holds an ally belt already facing into `tile`."""
    t = ph.readTile(ct, bot.hivemind, nextTile)
    if t.etype not in ph.BELT_TYPES or t.enemy or t.facing is None:
        return False
    return t.facing == ph.beltFacing(nextTile, tile)


def _countRebuild(bot, tile: Position) -> None:
    key = (tile.x, tile.y)
    bot.relayRebuilds[key] = bot.relayRebuilds.get(key, 0) + 1


def _contested(bot, tile: Position) -> bool:
    return bot.relayRebuilds.get((tile.x, tile.y), 0) >= BELT_CONTEST_LIMIT


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

    if walkTo(bot, ct, beside, tile, ("relay", tile.x, tile.y)):
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
    bot.relayFrom = None
    bot.relayAvoid.clear()
    bot.relayRebuilds.clear()
    bot.inRelay = False
    clearHold(bot)
    return False
