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

from enum import Enum, auto

from fcode import Controller, EntityType, Position

import pathfindingHelpers as ph
from movement import walkTo
from patience import (
    BLOCKED_WAIT_TURNS,
    CLEAR_WAIT_TURNS,
    clearHold,
    holdTask,
    stallCheck,
    stallForget,
    waitTi,
)
from pathing import astarFromSeeds, bfsFromSeeds

_RELAY_MAX_ITERS = 450


class _Plan(Enum):
    """The outcome of planRelay, three-way. A single bool conflated two of these —
    'already feeding the network' (a real delivery) with the failures — so a failed
    plan could be mistaken for a completed line and forge a delivery. Kept distinct
    so a delivery is credited only when the harvester genuinely reaches the network.
    """
    ROUTED = auto()     # a path was produced into bot.relayPath; keep laying it
    CONNECTED = auto()  # harvester already feeds the network; delivered, nothing to lay
    FAILED = auto()     # no route this turn (no seed / no path / no anchor); NOT delivered

# How many times this relay will re-lay one tile before conceding it. A belt we
# place and someone else immediately replaces is contention, not progress —
# taking it a third time only restarts the loop.
BELT_CONTEST_LIMIT = 2

# How far a merge check follows a belt's facing chain toward the core looking for a
# *visible* break before giving up and trusting it. A cut the builder can see sits
# within a few tiles; past that we're out of vision and won't reject on a guess.
_CHAIN_MAX_HOPS = 8

# How many consecutive *engaged* looks (patience.stallCheck — a count of checks, not
# elapsed rounds, so a builder flung or retargeted off its lane mid-grind doesn't
# resume into an instant give-up) at an obstruction whose HP fails to set a new low
# before we rule it out-repaired and stop grinding it. Without this, clearRelay
# Obstruction's re-plan-first branch re-selects a true chokepoint every turn and
# grinds it forever — an enemy repairing the barrier >=2 HP/turn bleeds us 2 Ti/shot
# with no end (the gap docs/observations.md flagged). Matches ORE_BARRIER_STALL_TURNS
# and raidBot._BELT_STALL_TURNS (both 11): long enough never to trip on a grind we're
# winning (a falling HP zeroes the stale count every look; a 30 HP barrier is 15
# unopposed 2-dmg shots), short enough to cap the wasted Ti at ~22 before we concede.
CLEAR_STALL_CHECKS = 11


def relayToHome(bot, ct: Controller) -> bool:
    """Cover every tile from the harvester back to the network, harvester end first.

    One rule: build the next uncovered tile if we're standing beside it, otherwise
    get beside it. Expressed that way the builder never needs to occupy a specific
    tile, so the terminal case (anchor = the 2x2 core) needs no special handling
    and a displaced builder self-corrects.
    """
    if bot.relayPath is None:
        plan = planRelay(bot, ct)
        if plan is _Plan.CONNECTED:
            return relayDone(bot, ct)   # harvester feeds the network — a real delivery
        if plan is _Plan.FAILED:
            # A plan that failed this turn, NOT a completed line. Marking it delivered
            # would orphan the harvester and forge guardian eligibility (see
            # relayAbort). Tear down without the flag and fall through to other work
            # this turn, exactly as relayDone does on a genuine completion.
            return relayAbort(bot, ct)
        # _Plan.ROUTED: bot.relayPath is now set — fall through and lay it.

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


def _chainVisiblyIntact(ctx, ct: Controller, hm, belt: Position) -> bool:
    """Follow `belt`'s facing chain toward the core. True if it reaches a network
    member, loops, or runs out of *visible* knowledge — we never reject on a tile we
    cannot currently see. False only on a break we can see right now: a belt facing
    into an in-vision tile that is neither an ally belt nor a network member.

    The cheap half of "is this line still connected": it catches a belt dead-ending
    into a barrier or gap inside the builder's own vision, the common cut. A cut
    hidden downstream needs the shared-ledger fix (out of scope — see observations.md
    on destroyed belts living forever in local_map). `belt` is already known to be an
    ally belt (the caller gated on isAllyBelt), so cur is always one as we advance."""
    cur = belt
    seen: set[tuple[int, int]] = set()
    for _ in range(_CHAIN_MAX_HOPS):
        if ctx.isNetworkMember(cur):
            return True
        t = ph.readTile(ct, hm, cur)
        if t.facing is None:
            return True  # unreadable facing — can't trace, don't invent a cut
        dx, dy = t.facing.delta()
        nxt = Position(cur.x + dx, cur.y + dy)
        if ctx.isNetworkMember(nxt):
            return True
        if not ct.is_in_vision(nxt):
            return True  # out of vision — cannot disprove; keep the old trust
        nt = ph.readTile(ct, hm, nxt)
        if nt.etype not in ph.BELT_TYPES or nt.enemy:
            return False  # visible break: belt runs into a non-belt we can see
        if (nxt.x, nxt.y) in seen:
            return True  # loop guard; 2-cycles are caught at lay time (_pointsBackAtUs)
        seen.add((nxt.x, nxt.y))
        cur = nxt
    return True


def planRelay(bot, ct: Controller) -> _Plan:
    if bot.corePos is None:
        return _Plan.FAILED

    ctx = ph.costContext(bot, ct, "relay")
    ctx.avoid |= bot.relayAvoid

    # Seed at the harvester, not at wherever the builder happens to be standing.
    # A relay exists to serve one harvester, so its first belt must be cardinally
    # adjacent to it — otherwise a mid-relay replan silently re-roots the line and
    # leaves the harvester with nothing beside it.
    source = bot.relayFrom
    if source is None:
        return _Plan.FAILED

    # Merge onto a proven-network tile, or a bare ally belt we can't see to be cut.
    # buildNetwork only recognises a belt whose whole chain to the core this unit
    # personally knows (vision + ledger); a builder beside a belt usually can't see
    # its downstream, so the strict check would fail and plow through toward the
    # core, re-facing a line already delivering. So we still trust a bare ally belt —
    # but only when _chainVisiblyIntact finds no *visible* break along its facing
    # chain, which stops us grafting onto a belt we can watch dead-end into a barrier
    # or gap. Out-of-vision chains stay trusted, so the healthy case is unchanged.
    # Memoised: the A* isGoal loop asks this once per frontier tile.
    _cut: dict[tuple[int, int], bool] = {}

    def mergeable(p: Position) -> bool:
        if ctx.isNetworkMember(p):
            return True
        if bot._strictRelay:
            # Reconnection: the harvester's own abandoned partial line reads intact
            # from a distance (_chainVisiblyIntact trusts an out-of-vision chain), so
            # a strict relay merges only onto a proven network member and re-lays a
            # real route home instead of trusting the dead end.
            return False
        if not ph.isAllyBelt(ct, bot.hivemind, p):
            return False
        key = (p.x, p.y)
        hit = _cut.get(key)
        if hit is None:
            hit = _cut[key] = _chainVisiblyIntact(ctx, ct, bot.hivemind, p)
        return hit

    if any(mergeable(n) for n in ph.orthogonalTiles(source)):
        return _Plan.CONNECTED  # already feeding a belt; nothing to lay, but delivered

    seeds = [
        n for n in ph.orthogonalTiles(source)
        if ph.beltPassable(ct, bot.hivemind, n) and (n.x, n.y) not in ctx.avoid
    ]
    if not seeds:
        return _Plan.FAILED

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
        return _Plan.FAILED

    anchor = next(
        (n for n in ph.orthogonalTiles(path[-1]) if mergeable(n)), None
    )
    if anchor is None:
        bot.relayPath = None
        return _Plan.FAILED

    bot.relayPath = path
    bot.relayAnchor = anchor
    return _Plan.ROUTED


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
    if planRelay(bot, ct) is not _Plan.ROUTED:
        return False
    clearHold(bot)  # new route, fresh patience — otherwise the spent counter
    return True     # would abandon it again immediately


def _nextUncovered(bot, ct: Controller, belts, anchor) -> int | None:
    """First belt tile, harvester end first, that isn't already a correct belt."""
    for i, tile in enumerate(belts):
        nxt = belts[i + 1] if i + 1 < len(belts) else anchor
        if beltCorrect(bot, ct, tile, nxt):
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

    # Never point a belt into a harvester: it delivers to adjacent buildings but
    # never forwards a belt's input, so the whole branch's Ti would dead-end in
    # it. The A* prices enemy harvesters as passable, so a path can run up to one
    # — route around it (to the belt it feeds, or another nearby line) instead.
    t = ph.readTile(ct, bot.hivemind, tile)
    replacing = False

    if t.etype in ph.BELT_TYPES and not t.enemy:
        if _contested(bot, tile):
            # We have already rebuilt this tile more than anyone should need to.
            # Someone else wants it facing another way; taking it again just
            # restarts the loop. Concede the tile and go around; if we can't route
            # around it the line is unfinished — abort WITHOUT claiming a delivery
            # (relayAbort, not relayDone: a conceded relay never reached the core).
            return _replanAvoiding(bot, ct, tile) or relayAbort(bot, ct)
        # A wrong-facing team-mate belt — route around it, don't bulldoze it.
        # Bulldozing is the adversarial-relaying war: two harvesters' lines converge
        # on the same tile (usually open ground on the shortest path to core, not a
        # real chokepoint) and each re-faces the other's belt for turns, because the
        # give-up counter is per-builder and never trips across the fight, and the
        # original static paths were planned before either belt existed so nothing
        # re-costs them. _replanAvoiding blacklists exactly this tile and re-plans, so
        # the search is forced onto the open detour beside it. This also covers the
        # early stub-vs-stub race a connectivity check would miss: we yield whether or
        # not the rival line has reached the network yet. (A relay-wide belt surcharge
        # was tried instead and dropped — it pushed lines apart on dense maps like
        # ragnarok, a measured regression, and the avoid-set already forces the detour.)
        if _replanAvoiding(bot, ct, tile):
            return True
        # No way around. Concede rather than fight a live line: aborting orphans the
        # harvester into reconnectOrphan, which retries once the winning line has
        # completed and joined the network (then even a strict re-relay grafts onto
        # it). Only reclaim the tile when it is a belt we can *see* is dead debris
        # (a visibly-cut stub), never one whose chain still reaches the network —
        # re-facing that would strand everything upstream that drains through it.
        ctx = ph.costContext(bot, ct, "relay")
        if _chainVisiblyIntact(ctx, ct, bot.hivemind, tile):
            return relayAbort(bot, ct)
        # Visibly-cut debris on our only route (no detour exists). Reclaim it in place.
        # The failed _replanAvoiding just blacklisted this tile and nulled the path on
        # its way to returning False; undo the blacklist so we don't build a belt on a
        # tile our own next-turn replan would then route around (that stranded belt is
        # the stub the reroute was meant to avoid). Safe to discard unconditionally:
        # this call reached `tile` by walking a path planned through ctx.avoid, so the
        # tile cannot have been in relayAvoid before now — the only entry we remove is
        # the one _replanAvoiding just added, never an earlier blacklist or a launcher
        # grab-ring tile. `facing` is still the original tile->nextTile step, which is
        # correct — with no detour this tile stays on route. destroy is free, so the
        # replacement goes down the same turn.
        bot.relayAvoid.discard((tile.x, tile.y))
        if not ct.can_destroy(tile):
            return holdTask(bot, "wrongFacing", BLOCKED_WAIT_TURNS)
        ct.destroy(tile)
        ph.invalidateTile(bot.hivemind, tile)
        replacing = True
    elif t.etype is not None:
        # Only a *building* blocks a belt. A builder standing here does not:
        # conveyors can be built under any bot, ours or theirs. Treating an
        # occupied tile as an obstruction is what made a builder blacklist it and
        # re-path across a neighbour's relay.
        return clearRelayObstruction(bot, ct, tile, hostile=t.enemy)
    else:
        # Tile reads clear: any grind memory here belongs to a building that no longer
        # exists (we ground it down, or someone else cleared it). Forget it so an
        # obstruction later rebuilt on this tile is judged fresh — a carried-over
        # (low, stale) pair would trip stallCheck near-instantly on the new building,
        # the stale-low trap its docstring warns about. Mirrors ecoBot's stallForget
        # on a cleared ore node.
        stallForget(bot._relayGrindLow, (tile.x, tile.y))

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
    """Route around this obstruction if a detour is worth it, else remove it.

    Hostile: a barrier the enemy *dropped onto an already-planned line* was never
    priced — the plan predates it, so A*'s "picked the route through it" premise is
    false. Re-plan so the barrier is priced now (ENEMY_BARRIER, ~6-7 open tiles of
    detour); take the detour if the fresh route drops this tile, else grind it down
    (2 Ti for 2 damage, one per turn). The grind is additionally bounded by a per-tile
    out-repair check (stallCheck / CLEAR_STALL_CHECKS): a chokepoint barrier repaired
    as fast as we cut it is conceded — blacklist-detour or clean abort — rather than
    ground forever. Friendly: path around by default — the cost isn't the free
    destroy(), it's losing our own building.
    """
    t = ph.readTile(ct, bot.hivemind, target)

    if not hostile and t.etype == EntityType.HARVESTER:
        return _replanAvoiding(bot, ct, target)  # ours, on ore. never.

    if hostile:
        # Re-price the barrier by re-planning (costContext rebuilds every round, so
        # it sees the now-present barrier). A cheaper detour drops this tile from the
        # fresh route; if the route still runs through it the detour costs more than
        # the ~15-turn grind. Re-plan (priced), not _replanAvoiding (infinite cost) —
        # the tuned barrier weight, not an absolute preference, decides detour-vs-grind.
        bot.relayPath = None
        plan = planRelay(bot, ct)
        if plan is _Plan.CONNECTED:
            return relayDone(bot, ct)  # a rival line grafted on — a real delivery
        if plan is _Plan.ROUTED and (target.x, target.y) not in {
            (p.x, p.y) for p in bot.relayPath
        }:
            clearHold(bot)  # detour found — fresh route, fresh patience
            return True
        # Fresh plan still routes through the barrier (or FAILED): grind it down. The
        # through-route stays in relayPath, so next turn re-enters here and grinds on;
        # no relayAvoid mutation, so a genuine chokepoint never spuriously aborts.
        key = (target.x, target.y)
        if ct.get_action_cooldown() == 0 and ct.can_fire(target):
            # But bound the grind by an out-repair check, mirrored from
            # ecoBot._barrierRepaired: read HP only on a turn we are actually about to
            # shoot (an engaged look — a held/broke turn must not advance the stall
            # clock), and give up once it hasn't set a new low for CLEAR_STALL_CHECKS
            # looks. HP unreadable (no building id) falls through and fires, same as
            # the raider belt-cut. This is the one bound the re-plan-first branch lacks:
            # a chokepoint priced cheaper than its detour is re-chosen every turn, so
            # without it the grind never ends.
            bid = ct.get_tile_building_id(target)
            hp = ct.get_hp(bid) if bid is not None else None
            if hp is not None and stallCheck(bot._relayGrindLow, key, hp, CLEAR_STALL_CHECKS):
                # Proven out-repaired: the grind is unwinnable, so the priced re-plan
                # above will keep choosing it forever. *Only now* is an infinite-cost
                # blacklist right — the tile's real cost is infinite, so it no longer
                # contradicts bb6ad66's tuned-weight detour-vs-grind call. Detour at any
                # length if one exists, else abort the relay cleanly (relayAbort tears
                # down inRelay/relayPath and hands the harvester to reconnectOrphan).
                stallForget(bot._relayGrindLow, key)
                return _replanAvoiding(bot, ct, target) or relayAbort(bot, ct)
            ct.fire(target)
            return True
        # Can't fire this turn (cooldown, or broke below the shot cost). Hold, but never
        # end on a bare False: patience expiry used to return one straight up, leaving
        # inRelay/relayPath live for ecoBot to re-enter the identical grind next turn
        # (the bb6ad66 regression). Restore the detour escape and add an explicit abort
        # so a spent "clear" budget tears the relay down instead — one turn sooner than
        # the old planRelay->FAILED->relayAbort route, and no longer dependent on the
        # replan failing again. The shared "clear" counter is non-monotone (lower ladder
        # rungs reset it with other reasons), which is fine: the forever-grind is bounded
        # by the per-tile stallCheck above, not by this hold.
        return (
            holdTask(bot, "clear", CLEAR_WAIT_TURNS)
            or _replanAvoiding(bot, ct, target)
            or relayAbort(bot, ct)
        )

    if _replanAvoiding(bot, ct, target):
        return True
    if ct.can_destroy(target):   # genuinely walled in: our own belt is the only door
        ct.destroy(target)
        return True
    return holdTask(bot, "blocked", BLOCKED_WAIT_TURNS)


def _teardownRelay(bot) -> None:
    """Drop all per-relay state. Shared by relayDone (a delivery) and relayAbort
    (not a delivery) so the field list lives in one place."""
    bot.relayPath = None
    bot.relayAnchor = None
    bot.relayFrom = None
    bot.relayAvoid.clear()
    bot.relayRebuilds.clear()
    # Per-relay grind memory (clearRelayObstruction's out-repair stallCheck). Cleared
    # here so both relayDone and relayAbort satisfy stallCheck's caller-owns-cleanup
    # contract in one place — a stale (low, stale) pair must never outlive its relay.
    bot._relayGrindLow.clear()
    bot.inRelay = False
    bot._strictRelay = False


def relayAbort(bot, ct: Controller) -> bool:
    """End a relay that did NOT deliver — the delivery-free sibling of relayDone.

    Same teardown and hold reset, but never sets `_harvesterDelivered`: for a relay
    that ended without the harvester reaching the network (a plan that failed, a
    contested tile conceded with no route around, a builder flung off its lane).
    Marking such a relay delivered would orphan the harvester — nothing re-relays it
    (rankOre skips own-harvester tiles, repairCoreBelts is barrier-only) — and forge
    the builder's guardian eligibility, which keys on that flag.

    Records `relayFrom` as an orphan before teardown, so a first-time abort — a
    failed plan, a conceded contested tile — enters ecoBot.reconnectOrphan's retry
    loop exactly as a fling does. Without this, a harvester whose line never finished
    on its first attempt is abandoned forever: nothing else ever re-targets an own
    harvester. Guarded to our own harvester so an aborted enemy-harvester tap
    (hijackRelay) is not chased — the same check reconnectOrphan makes. The retry
    cooldown (`_orphanNextTry`) is left untouched: the fling path sets its own
    (launcher-avoidance) before conceding, and reconnectOrphan self-manages it for
    the non-fling paths.

    Returns False, as relayDone does, so the builder falls through to its next task
    this turn rather than idling.
    """
    src = bot.relayFrom
    if src is not None:
        t = ph.readTile(ct, bot.hivemind, src)
        if t.etype == EntityType.HARVESTER and not t.enemy:
            bot._orphanHarvester = src
    _teardownRelay(bot)
    clearHold(bot)
    return False


def relayDone(bot, ct: Controller) -> bool:
    delivered = bot.relayFrom   # captured before teardown clears it (verify trigger)
    _teardownRelay(bot)
    # This builder has now delivered a harvester end-to-end (income flowing), so it
    # is eligible to take guardian duty. Reached only on a genuine completion: every
    # belt placed (relayToHome's idx-is-None exit) or the harvester already feeding
    # the network (planRelay -> CONNECTED). A failed plan goes to relayAbort instead,
    # which leaves this flag untouched. See roles/guardianBot, which reads the flag.
    bot._harvesterDelivered = True

    # Every 2nd own-harvester delivery, arm a verification walk of the line we just
    # connected (ecoBot.verifyRelay): a ghost belt destroyed out of vision reads as
    # connected forever, so the only way to catch it is to walk the line and refresh
    # vision. Own harvesters only — a tapped enemy harvester (hijackRelay leaves an
    # enemy relayFrom) can't be repaired through reconnectOrphan, so verifying it is
    # pointless; the same guard reconnectOrphan and relayAbort use.
    if delivered is not None:
        t = ph.readTile(ct, bot.hivemind, delivered)
        if t.etype == EntityType.HARVESTER and not t.enemy:
            bot._relaysConnected += 1
            # Only arm when no orphan is already pending: verifyRelay's own handoff
            # writes the single _orphanHarvester slot, so arming over a pending orphan
            # (e.g. a harvester flung earlier and not yet reconnected) would clobber
            # and strand it — the exact bug cf549c0 fixed. Keep the count; once the
            # orphan clears, the next delivery arms.
            if bot._relaysConnected >= 2 and bot._orphanHarvester is None:
                bot._verifyHarvester = delivered
                bot._relaysConnected = 0

    clearHold(bot)
    return False


def beltCorrect(bot, ct: Controller, tile: Position, nxt: Position) -> bool:
    """True if `tile` already carries a correct ally belt facing `nxt`. Shared by
    _nextUncovered (what to (re)lay) and ecoBot.verifyRelay (what's intact). A tile
    not orthogonally adjacent to `nxt` returns True — there is nothing to judge, and
    _nextUncovered skipped it the same way."""
    facing = ph.beltFacing(tile, nxt)
    if facing is None:
        return True
    t = ph.readTile(ct, bot.hivemind, tile)
    return t.etype in ph.BELT_TYPES and not t.enemy and t.facing == facing


def traceToCore(bot, ct: Controller, harvester: Position):
    """Follow the belt line feeding `harvester` toward the core, returning
    `(belts, anchor)`: the ordered belt tiles from the harvester's adjacent belt to
    the last belt before the core, and the core-footprint tile that last belt points
    into. None if no ally belt is adjacent to the harvester or the facing chain never
    reaches the core (a break we can't even trace — handed straight to reconnect).

    Reads the ledger like _chainVisiblyIntact — so it follows a *ghost* chain to the
    core, which is the point: the walk that consumes this line is what reveals which
    of those ledger belts no longer exist. A seen-set and a W*H cap bound it against a
    facing cycle in stale ledger data (an uncapped follow would TLE — a silent unit)."""
    if bot.corePos is None:
        return None
    core = {(p.x, p.y) for p in ph.coreFootprint(bot.corePos)}
    start = next(
        (n for n in ph.orthogonalTiles(harvester) if ph.isAllyBelt(ct, bot.hivemind, n)),
        None,
    )
    if start is None:
        return None
    belts: list[Position] = []
    seen: set[tuple[int, int]] = set()
    cur = start
    cap = ct.get_map_width() * ct.get_map_height()
    for _ in range(cap):
        if (cur.x, cur.y) in core or (cur.x, cur.y) in seen:
            return None  # a belt on the core, or a facing cycle — untraceable
        seen.add((cur.x, cur.y))
        belts.append(cur)
        t = ph.readTile(ct, bot.hivemind, cur)
        if t.facing is None:
            return None
        dx, dy = t.facing.delta()
        nxt = Position(cur.x + dx, cur.y + dy)
        if (nxt.x, nxt.y) in core:
            return (belts, nxt)  # this belt points into the core — the line is home
        if not ph.isAllyBelt(ct, bot.hivemind, nxt):
            return None  # chain leaves our belts (a visible break or foreign facing)
        cur = nxt
    return None
