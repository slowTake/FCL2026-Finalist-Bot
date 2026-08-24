"""Walking. Plan once, cache, follow; replan only when the route actually breaks."""

from __future__ import annotations

from fcode import Controller, Position

import debug
import pathfindingHelpers as ph
from patience import BLOCKED_WAIT_TURNS, clearHold, holdTask
from pathing import astar, bfs

_WALK_MAX_ITERS = 450

# Turns a failed walk search is trusted before it is re-run for an unmoved unit
# against an unchanged goal. Short so a blocker clearing (or a freshly cut path)
# is re-probed within a couple of turns; long enough that a genuinely stuck unit
# stops paying a full A*+BFS every single turn — see the no-route memo in walkTo.
_NOROUTE_RECHECK_TURNS = 4

# Turn-elapsed CPU (microseconds) past which _plan's searches abandon rather
# than finish, and past which a fresh plan is not started at all. Same value and
# same reasoning as relays._PLAN_DEADLINE_US, kept separate for the same reason
# that one is not imported from telemetry: these are independent budgets that
# must each hold on their own.
#
# _WALK_MAX_ITERS bounds the search in *expansions*, not time. A failed walk runs
# A* to exhaustion and then a full BFS behind it — ~9ms together (raidBot's
# pressCore backoff is sized off exactly that measurement) — which with the tick
# preamble on top exceeds the 10ms turn outright.
_WALK_DEADLINE_US = 7000


def walkTo(bot, ct: Controller, isGoal, heuristicGoal: Position | None, goal) -> bool:
    """Advance one step toward any tile matching isGoal. True if the turn was used.

    `goal` is an opaque hashable naming *what we are walking to*; the cached
    path is reused only while it is unchanged. Without it the cache is keyed by
    nothing — a caller that retargets while standing on the old route keeps
    following it to the old destination — and correctness has to be propped up
    by every caller remembering to null bot.walkPath, which is a callee's
    private state being invalidated from three other modules. It also makes
    this helper safe for a goal that *moves*, which is what a defence role
    chasing a raider needs and what the economy callers never exercised.
    """
    if ct.get_move_cooldown() != 0:
        if debug.ENABLED:
            debug.log("walkTo", f"idx={bot.hivemind.my_index} goal={goal} "
                                 f"COOLDOWN cooldown={ct.get_move_cooldown()}")
        return True

    pos = ct.get_position()
    if isGoal(pos):
        if debug.ENABLED:
            debug.log("walkTo", f"idx={bot.hivemind.my_index} goal={goal} "
                                 f"pos={pos} ARRIVED (isGoal true)")
        clearHold(bot)
        return False

    path = bot.walkPath if bot.walkGoal == goal else None
    idx = _indexOf(path, pos) if path else None
    if idx is None or idx >= len(path) - 1:
        # A route this walk just failed to find can't have appeared while we stood
        # on the same tile with the same goal — so within a short window, take the
        # earlier "no route" as read and hold, instead of re-running a full A*+BFS
        # that will only exhaust again. This is what stops a unit wedged at an
        # unreachable goal from burning ~900 search iterations a turn (the TLE).
        rnd = ct.get_current_round()
        # TLE tombstone. The no-route memo below is written only *after* _plan
        # returns, so a plan the CPU limit kills mid-search never arms it: the
        # next turn finds no memo, re-runs the identical doomed search, and is
        # killed again — a builder wedged for the rest of the match, doing
        # nothing, which is what this pattern already fixes for relay plans
        # (main.Player._relayPlanRound, #115). A stamp left standing from an
        # earlier round is proof of a kill; arm the memo on the dead search's
        # behalf and hold, rather than re-entering it.
        # Goal-stamped, not just round-stamped: the cooldown and arrival returns
        # above leave a stamp standing, so a kill during goal A's plan would
        # otherwise fire against whatever goal next reaches this branch and
        # memoise a target that was never searched.
        if bot._walkPlanStamp is not None and bot._walkPlanStamp != (rnd, goal):
            killedGoal = bot._walkPlanStamp[1]
            bot._walkPlanStamp = None
            if killedGoal == goal:
                bot._noRouteGoal = goal
                bot._noRoutePos = (pos.x, pos.y)
                bot._noRouteRound = rnd
                bot.noRouteSeq += 1
                if debug.ENABLED:
                    debug.log("walkTo", f"idx={bot.hivemind.my_index} goal={goal} "
                                         f"pos={pos} PLAN KILLED last round — memoised")
                return holdTask(bot, "noRoute", BLOCKED_WAIT_TURNS)
            # A different goal was in flight when the turn died. Clearing the
            # stamp is the whole job — this goal was never searched, so there is
            # nothing to conclude about it and it plans normally below.
            if debug.ENABLED:
                debug.log("walkTo", f"idx={bot.hivemind.my_index} goal={goal} "
                                     f"pos={pos} stale stamp for {killedGoal} cleared")
        if (bot._noRouteGoal == goal
                and bot._noRoutePos == (pos.x, pos.y)
                and rnd - bot._noRouteRound < _NOROUTE_RECHECK_TURNS):
            if debug.ENABLED:
                debug.log("walkTo", f"idx={bot.hivemind.my_index} goal={goal} "
                                     f"pos={pos} NO PATH (memoised, skip replan)")
            bot.noRouteSeq += 1
            return holdTask(bot, "noRoute", BLOCKED_WAIT_TURNS)
        # A plan the deadline stopped proves nothing about the goal, so it gets
        # its own short memo rather than the no-route one: re-running a search
        # that just ran out of time would only run out of time again, but
        # calling the goal unreachable would have callers blacklist a target
        # they can very likely still get to (raidBot._siegeUnreachable for 20
        # turns, the 25-round pressCore backoff, ecoBot's 150-turn ore skip).
        if (bot._walkBudgetGoal == goal
                and bot._walkBudgetPos == (pos.x, pos.y)
                and rnd - bot._walkBudgetRound < _NOROUTE_RECHECK_TURNS):
            if debug.ENABLED:
                debug.log("walkTo", f"idx={bot.hivemind.my_index} goal={goal} "
                                     f"pos={pos} OVER BUDGET (memoised, skip replan)")
            return holdTask(bot, "planBudget", BLOCKED_WAIT_TURNS)
        # Entry gate, checked once before any search work. overBudget reads
        # *turn*-elapsed CPU, so entering on a turn that has already burned past
        # the deadline would trip the first poll and abandon a plan that was
        # never really measured. Already-over on entry indicts the turn, not the
        # goal: hold and retry on a fresh budget, without arming the no-route
        # memo — nothing was searched, so there is nothing to conclude. No stamp
        # is written here either; nothing expensive runs for it to catch.
        # A distinct hold reason, so a caller reading holdReason cannot mistake
        # a busy turn for a goal it should give up on (relays uses "planBudget"
        # for the identical case).
        if ct.get_cpu_time_elapsed() > _WALK_DEADLINE_US:
            if debug.ENABLED:
                debug.log("walkTo", f"idx={bot.hivemind.my_index} goal={goal} "
                                     f"pos={pos} DEFERRED (turn already over budget)")
            return holdTask(bot, "planBudget", BLOCKED_WAIT_TURNS)
        bot._walkPlanStamp = (rnd, goal)
        path, tripped = _plan(bot, ct, isGoal, heuristicGoal)
        bot._walkPlanStamp = None
        if tripped:
            bot._walkBudgetGoal = goal
            bot._walkBudgetPos = (pos.x, pos.y)
            bot._walkBudgetRound = rnd
            if debug.ENABLED:
                debug.log("walkTo", f"idx={bot.hivemind.my_index} goal={goal} "
                                     f"pos={pos} PLAN ABANDONED on deadline")
            return holdTask(bot, "planBudget", BLOCKED_WAIT_TURNS)
        if path is None:
            bot._noRouteGoal = goal
            bot._noRoutePos = (pos.x, pos.y)
            bot._noRouteRound = rnd
            # Tick the dead-search counter. `holdReason` alone can't answer "did the
            # search I just ran come up empty?": it is sticky until some other call
            # clears it, and the move-cooldown early return above exits before
            # touching it — so a caller testing the reason can read a "noRoute" left
            # over from an earlier turn, or from a different goal entirely, and give
            # up on a target it never searched for.
            #
            # A counter and not a round stamp, because a raider can run two walks in
            # one turn (runRaidBot: a failed `approach` to a sentinel site falls
            # straight through to `pressCore`, which walks again). A stamp reads
            # equal for both, so the second walk inherits the first one's failure and
            # the caller blacklists a target it actually reached. Callers snapshot
            # this before their walk and compare after — see raidBot._markIfNoRoute.
            bot.noRouteSeq += 1
            if debug.ENABLED:
                debug.log("walkTo", f"idx={bot.hivemind.my_index} goal={goal} "
                                     f"pos={pos} NO PATH FOUND (_plan returned None)")
            return holdTask(bot, "noRoute", BLOCKED_WAIT_TURNS)
        bot._noRouteGoal = None  # found a route — drop any stale no-route memo
        bot.walkGoal = goal
        idx = 0
        if debug.ENABLED:
            debug.log("walkTo", f"idx={bot.hivemind.my_index} goal={goal} "
                                 f"pos={pos} REPLANNED path={path}")

    nextTile = path[idx + 1]
    if debug.ENABLED:
        debug.log("walkTo", f"idx={bot.hivemind.my_index} goal={goal} pos={pos} "
                             f"pathIdx={idx}/{len(path)-1} nextTile={nextTile}")
    return stepToward(bot, ct, nextTile)


def walkOrRetarget(bot, ct: Controller, isGoal, heuristicGoal: Position | None, goal) -> bool:
    """Like walkTo, but tells the caller when the *current goal* is the
    problem, so it can retarget instead of retrying the same dead search
    forever.

    walkTo returns False for three unrelated reasons a caller can't otherwise
    tell apart: arrival, a single blocked tile (stepToward already
    self-heals that one via its own blacklist), and _plan finding no route
    to the goal at all (the "noRoute" hold in walkTo, above). Only the last
    is actually stuck — bot.holdReason == "noRoute" once BLOCKED_WAIT_TURNS
    patience for it is spent. Arrival clears the hold itself; a blocked-tile
    retry leaves a different reason ("walkBlocked") or none.

    On that stuck case: wipe the stale path/goal/avoid state and the hold,
    and report True. Nothing here decides *what* to walk toward next — that
    stays the caller's call (see raidBot.py's site-vs-core fallback) — this
    just guarantees the next attempt, whatever the target, gets a clean
    search instead of one poisoned by a walkAvoid built for a route that's
    already given up on.
    """
    used = walkTo(bot, ct, isGoal, heuristicGoal, goal)
    if used or bot.holdReason != "noRoute":
        return False

    if debug.ENABLED:
        debug.log("walkTo", f"idx={bot.hivemind.my_index} goal={goal} "
                             f"pos={ct.get_position()} GIVING UP — no route found, retargeting")
    bot.walkPath = None
    bot.walkGoal = None
    bot.walkAvoid.clear()
    clearHold(bot)
    return True


def stepToward(bot, ct: Controller, tile: Position) -> bool:
    d = ph.beltFacing(ct.get_position(), tile)
    if d is not None and ph.canStandNow(ct, tile) and ct.can_move(d):
        ct.move(d)
        bot.walkAvoid.clear()
        clearHold(bot)
        return True

    # An *ally* builder will usually move on its own, so waiting it out is
    # cheaper than walking around it — but only up to a point, and only if
    # waiting doesn't itself cost anything. An enemy one will not: it is
    # pursuing its own goal, and in a mirror match that goal is the tile we
    # are standing on, which is how two raiders end up re-issuing the same
    # illegal step at each other for the rest of the match. Enemy builders
    # are standing obstacles, priced like anything else the planner called
    # traversable and the engine refused.
    t = ph.readTile(ct, bot.hivemind, tile)
    waitingOnAlly = t.builder and not t.builderEnemy

    ok = holdTask(bot, "walkBlocked", BLOCKED_WAIT_TURNS)
    if debug.ENABLED:
        debug.log(
            "walk",
            f"idx={bot.hivemind.my_index} pos={ct.get_position()} blocked_at={tile} "
            f"builder={t.builder} builderEnemy={t.builderEnemy} etype={t.etype} "
            f"holdTurns={getattr(bot, 'holdTurns', 0)} holding={ok} "
            f"waitingOnAlly={waitingOnAlly}",
        )

    if waitingOnAlly and ok:
        # Still within patience, and still worth waiting — hold position and
        # leave the cached path (and its goal key) alone. Clearing them
        # unconditionally on every blocked turn (the old behaviour) forced a
        # fresh A* each time, and against a multi-candidate goal (any tile
        # beside a target, not one point) a fresh search can tie-break to a
        # *different* candidate turn to turn — visible as a builder walking
        # back and forth between two tiles instead of just waiting, not
        # merely wasted CPU.
        return True

    # Either not an ally, or patience for one is spent. The spent-patience
    # case matters on its own: two allies each waiting on the other to move
    # first (a swap deadlock) would otherwise wait forever, since "it'll
    # move eventually" never becomes false by itself. Blacklisting forces a
    # real reroute instead of a standoff neither side can break.
    bot.walkPath = None
    bot.walkGoal = None
    bot.walkAvoid.add((tile.x, tile.y))
    return ok


def exploreStep(bot, ct: Controller) -> bool:
    """Head for the nearest tile nobody has seen yet."""
    hm = bot.hivemind

    def unknown(p: Position) -> bool:
        return (p.x, p.y) not in hm.local_map

    return walkTo(bot, ct, unknown, None, "explore")


def _plan(bot, ct: Controller, isGoal, heuristicGoal: Position | None):
    """Plan a walk. Returns (path, tripped).

    `tripped` says the deadline stopped a search before it finished, so a None
    path means "not found in the time available", not "no route exists" — the
    same distinction relays._planRelay draws between FAILED_BUDGET and FAILED,
    and for the same reason: the two demand opposite responses from the caller.
    """
    if bot.corePos is None:
        return None, False

    ctx = ph.costContext(bot, ct, "walk")
    ctx.avoid |= bot.walkAvoid
    pos = ct.get_position()

    tripped = [False]

    def overBudget() -> bool:
        if ct.get_cpu_time_elapsed() > _WALK_DEADLINE_US:
            tripped[0] = True
            return True
        return False

    path = astar(
        pos,
        costFn=ph.makeCostFn(ctx),
        isGoal=isGoal,
        heuristic=ph.makeHeuristic(heuristicGoal),
        maxIters=_WALK_MAX_ITERS,
        shouldStop=overBudget,
    )
    # `not tripped[0]`: a deadline that stopped A* stops the BFS on its first
    # poll too, so entering it only burns another poll stride for a result that
    # cannot arrive. Same guard relays puts on its own fallback.
    if path is None and not tripped[0]:
        # Honour the same blacklist A* just used. A* only fails on cramped or
        # contested ground, which is exactly where walkAvoid has entries — so
        # without this the fallback confidently returns a route through the
        # tiles the engine refused a moment ago, stepToward refuses again, and
        # the two searches disagree in the one case they both run.
        path = bfs(
            pos,
            isPassable=lambda p: (
                not ph.isImpassable(ct, bot.hivemind, p)
                and (p.x, p.y) not in bot.walkAvoid
            ),
            isGoal=isGoal,
            maxIters=_WALK_MAX_ITERS,
            shouldStop=overBudget,
        )

    bot.walkPath = path
    return path, tripped[0]


def _indexOf(path, pos: Position) -> int | None:
    for i, p in enumerate(path):
        if p.x == pos.x and p.y == pos.y:
            return i
    return None
