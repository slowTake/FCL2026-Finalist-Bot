"""Raider role. Orchestrator only — mechanics live in the functions it calls,
same shape as ecoBot.py.

The expensive half of raiding — deciding *where* the sentinel goes — happens
on the core (see raid.py), because builders measure at p90 9.4ms against a
10ms limit and already drop turns, while the core sits at 1.6ms. All this
role does is travel and build.

Travel is chunked. A full-map A* to the enemy core is roughly D=20-25, and the
frontier grows as ~2*D**2 against a 450-iteration cap, so the search would burn
the whole turn budget and then return None. Walking to a waypoint ~8 tiles out
keeps each search near ~130 expansions, which fits.
"""

from __future__ import annotations

from fcode import Controller, Direction, EntityType, GameConstants, Position

import heal
import pathfindingHelpers as ph
import raid
import store
from ecoBot import runEcoBot
from movement import walkTo
from patience import BLOCKED_WAIT_TURNS, clearHold, holdTask
from pathing import manhattan

# Far enough to make progress, short enough that one A* stays inside budget.
_CHUNK = 8
# How far from an unreachable waypoint to look for a usable one.
_WAYPOINT_SLACK = 3


def runRaidBot(bot, ct: Controller) -> None:
    # Checked first, every turn: a turret we placed is a standing 30-Ti
    # investment with nothing else defending it out here, so responding to it
    # taking fire outranks continuing to travel or starting the next build.
    if heal.healNearby(bot, ct, heal.TURRET_HEAL_TYPES):
        return

    target = raid.readTarget(ct)
    if target is None:
        # No emplacement published — either the map was never fingerprinted, or
        # the core is one round behind re-picking after the last sentinel went up.
        # Either way the body is worth more on ore than standing still.
        runEcoBot(bot, ct)
        return

    site, facing = target

    if buildSentinel(bot, ct, site, facing):
        return
    if approach(bot, ct, site):
        return
    advance(bot, ct, site)


def buildSentinel(bot, ct: Controller, site: Position, facing: Direction) -> bool:
    """In position: build. True either way — beltFacing is the orthogonal-
    adjacency test, and anything else this turn just means waiting out the
    cooldown, trying a fallback, or holding, not moving off the spot."""
    pos = ct.get_position()
    if ph.beltFacing(pos, site) is None:
        return False

    if ct.get_action_cooldown() != 0:
        return True

    if ct.can_build_sentinel(site, facing):
        clearHold(bot)
        _finishBuild(ct, site, facing)
        return True

    # Most likely explanation: our own sentinel is already standing there —
    # the core's ledger view of a tile beside the *enemy* core can lag
    # several rounds behind our own build. Vision beats the ledger and we're
    # standing right next to it, so trust that over retrying or searching.
    t = ph.readTile(ct, bot.hivemind, site)
    if t.etype == EntityType.SENTINEL and not t.enemy:
        clearHold(bot)
        store.put(ct, store.RAID_TARGET, 0)
        return True

    if ct.get_global_resources() < ct.get_sentinel_cost():
        return True  # broke, not blocked — same reason waitTi would give,
                      # inlined so it doesn't reset the counter below

    if ct.get_unit_count() >= GameConstants.MAX_TEAM_UNITS:
        return True  # not a site problem — searching or clearing won't help

    choice = raid.fallbackEmplacement(bot, ct, site)
    if choice is not None:
        clearHold(bot)
        _finishBuild(ct, *choice)
        return True

    if not holdTask(bot, "raidSiteBlocked", BLOCKED_WAIT_TURNS):
        # Site and its neighbours have both been dead for a while. Clearing
        # the slot is what makes chooseEmplacement run again. This can still
        # republish the same tile if the core's ledger hasn't learned why
        # it's blocked (e.g. an enemy structure it can't see) — accepted,
        # bounded churn rather than an unresolvable stall, and rarer now that
        # the common cause (our own sentinel) is caught above.
        store.put(ct, store.RAID_TARGET, 0)
        clearHold(bot)
    return True


def _finishBuild(ct: Controller, site: Position, facing: Direction) -> None:
    ct.build_sentinel(site, facing)
    # Clear the slot so the core picks the next emplacement. Store writes are
    # buffered, so the core sees this next round, not this one.
    store.put(ct, store.RAID_TARGET, 0)


def approach(bot, ct: Controller, site: Position) -> bool:
    """Final stretch: goal is a tile beside the emplacement, not the emplacement
    itself — a builder standing on it cannot build on it."""
    pos = ct.get_position()
    if manhattan(pos, site) > _CHUNK:
        return False

    def beside(p: Position) -> bool:
        return ph.beltFacing(p, site) is not None

    walkTo(bot, ct, beside, site, ("raidSite", site.x, site.y))
    return True


def advance(bot, ct: Controller, site: Position) -> None:
    """Too far for one search: walk a waypoint at a time instead."""
    wp = _waypoint(bot, ct, site)
    if wp is None:
        return

    def atWaypoint(p: Position) -> bool:
        return p.x == wp.x and p.y == wp.y

    walkTo(bot, ct, atWaypoint, wp, ("waypoint", wp.x, wp.y))


def _towardTarget(pos: Position, target: Position, budget: int) -> Position:
    """A point `budget` steps along the way, splitting the move across both axes
    in proportion to how far each still has to go."""
    dx = target.x - pos.x
    dy = target.y - pos.y
    total = abs(dx) + abs(dy)
    if total <= budget:
        return target
    stepX = int(round(budget * (abs(dx) / total)))
    stepY = budget - stepX
    sx = 1 if dx > 0 else -1 if dx < 0 else 0
    sy = 1 if dy > 0 else -1 if dy < 0 else 0
    return Position(pos.x + sx * min(stepX, abs(dx)),
                    pos.y + sy * min(stepY, abs(dy)))


def _waypoint(bot, ct: Controller, target: Position) -> Position | None:
    """A reachable staging tile between here and the emplacement.

    The proportional point is a guess and often lands in a wall, so the nearby
    tiles are tried before giving up — a blocked guess should cost a slightly
    worse waypoint, never the turn.
    """
    pos = ct.get_position()
    guess = _towardTarget(pos, target, _CHUNK)
    if not ph.isImpassable(ct, bot.hivemind, guess):
        return guess

    best = None
    bestScore = None
    for dx in range(-_WAYPOINT_SLACK, _WAYPOINT_SLACK + 1):
        for dy in range(-_WAYPOINT_SLACK, _WAYPOINT_SLACK + 1):
            cand = Position(guess.x + dx, guess.y + dy)
            if ph.isImpassable(ct, bot.hivemind, cand):
                continue
            # Prefer tiles that are still progress, not just any open tile.
            score = manhattan(cand, target)
            if bestScore is None or score < bestScore:
                best, bestScore = cand, score
    return best
