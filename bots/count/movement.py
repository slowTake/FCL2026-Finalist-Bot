"""Walking. Plan once, cache, follow; replan only when the route actually breaks."""

from __future__ import annotations

from fcode import Controller, Position

import debug
import pathfindingHelpers as ph
from patience import BLOCKED_WAIT_TURNS, clearHold, holdTask
from pathing import astar, bfs

_WALK_MAX_ITERS = 450


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
        path = _plan(bot, ct, isGoal, heuristicGoal)
        if path is None:
            if debug.ENABLED:
                debug.log("walkTo", f"idx={bot.hivemind.my_index} goal={goal} "
                                     f"pos={pos} NO PATH FOUND (_plan returned None)")
            return holdTask(bot, "noRoute", BLOCKED_WAIT_TURNS)
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
    if bot.corePos is None:
        return None

    ctx = ph.costContext(bot, ct, "walk")
    ctx.avoid |= bot.walkAvoid
    pos = ct.get_position()

    path = astar(
        pos,
        costFn=ph.makeCostFn(ctx),
        isGoal=isGoal,
        heuristic=ph.makeHeuristic(heuristicGoal),
        maxIters=_WALK_MAX_ITERS,
    )
    if path is None:
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
        )

    bot.walkPath = path
    return path


def _indexOf(path, pos: Position) -> int | None:
    for i, p in enumerate(path):
        if p.x == pos.x and p.y == pos.y:
            return i
    return None
