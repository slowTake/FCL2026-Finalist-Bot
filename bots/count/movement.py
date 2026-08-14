"""Walking. Plan once, cache, follow; replan only when the route actually breaks."""

from __future__ import annotations

from fcode import Controller, Position

import pathfindingHelpers as ph
from patience import BLOCKED_WAIT_TURNS, clearHold, holdTask
from pathing import astar, bfs

_WALK_MAX_ITERS = 450


def walkTo(bot, ct: Controller, isGoal, heuristicGoal: Position | None) -> bool:
    """Advance one step toward any tile matching isGoal. True if the turn was used."""
    if ct.get_move_cooldown() != 0:
        return True

    pos = ct.get_position()
    if isGoal(pos):
        clearHold(bot)
        return False

    path = bot.walkPath
    idx = _indexOf(path, pos) if path else None
    if idx is None or idx >= len(path) - 1:
        path = _plan(bot, ct, isGoal, heuristicGoal)
        if path is None:
            return holdTask(bot, "noRoute", BLOCKED_WAIT_TURNS)
        idx = 0

    return stepToward(bot, ct, path[idx + 1])


def stepToward(bot, ct: Controller, tile: Position) -> bool:
    d = ph.beltFacing(ct.get_position(), tile)
    if d is not None and ph.canStandNow(ct, tile) and ct.can_move(d):
        ct.move(d)
        bot.walkAvoid.clear()
        clearHold(bot)
        return True

    bot.walkPath = None

    # An *ally* builder will move on its own, so waiting one out is cheaper than
    # walking around it. An enemy one will not: it is pursuing its own goal, and
    # in a mirror match that goal is the tile we are standing on, which is how two
    # raiders end up re-issuing the same illegal step at each other for the rest of
    # the match. Enemy builders are standing obstacles, priced like anything else
    # the planner called traversable and the engine refused.
    t = ph.readTile(ct, bot.hivemind, tile)
    if not t.builder or t.builderEnemy:
        bot.walkAvoid.add((tile.x, tile.y))

    return holdTask(bot, "walkBlocked", BLOCKED_WAIT_TURNS)


def exploreStep(bot, ct: Controller) -> bool:
    """Head for the nearest tile nobody has seen yet."""
    hm = bot.hivemind

    def unknown(p: Position) -> bool:
        return (p.x, p.y) not in hm.local_map

    return walkTo(bot, ct, unknown, None)


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
        path = bfs(
            pos,
            isPassable=lambda p: not ph.isImpassable(ct, bot.hivemind, p),
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
