"""Raider role: walk to the published emplacement and plant a sentinel on it.

Deliberately thin. The expensive half of raiding — deciding *where* the sentinel
goes — happens on the core (see raid.py), because builders measure at p90 9.4ms
against a 10ms limit and already drop turns, while the core sits at 1.6ms. All
this role does is travel and build.

Travel is chunked. A full-map A* to the enemy core is roughly D=20-25, and the
frontier grows as ~2*D**2 against a 450-iteration cap, so the search would burn
the whole turn budget and then return None. Walking to a waypoint ~8 tiles out
keeps each search near ~130 expansions, which fits.
"""

from __future__ import annotations

from fcode import Controller, Position

import pathfindingHelpers as ph
from ecoBot import runEcoBot
from movement import walkTo
from pathing import manhattan
from raid import SLOT_RAID_TARGET, readTarget

# Far enough to make progress, short enough that one A* stays inside budget.
_CHUNK = 8
# How far from an unreachable waypoint to look for a usable one.
_WAYPOINT_SLACK = 3


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


def runRaidBot(bot, ct: Controller) -> None:
    target = readTarget(ct)
    if target is None:
        # No emplacement published — either the map was never fingerprinted, or
        # the core is one round behind re-picking after the last sentinel went up.
        # Either way the body is worth more on ore than standing still.
        runEcoBot(bot, ct)
        return

    site, facing = target
    pos = ct.get_position()

    # In position: build. beltFacing is the orthogonal-adjacency test — anything
    # else and build_sentinel would raise, which costs the unit the whole match.
    if ph.beltFacing(pos, site) is not None:
        if ct.get_action_cooldown() == 0 and ct.can_build_sentinel(site, facing):
            ct.build_sentinel(site, facing)
            # Clear the slot so the core picks the next emplacement. Store writes
            # are buffered, so the core sees this next round, not this one.
            ct.write_store(SLOT_RAID_TARGET, 0)
        return

    # Final approach: goal is a tile beside the emplacement, not the emplacement
    # itself — a builder standing on it cannot build on it.
    if manhattan(pos, site) <= _CHUNK:
        def beside(p: Position) -> bool:
            return ph.beltFacing(p, site) is not None

        walkTo(bot, ct, beside, site)
        return

    wp = _waypoint(bot, ct, site)
    if wp is None:
        return

    def atWaypoint(p: Position) -> bool:
        return p.x == wp.x and p.y == wp.y

    walkTo(bot, ct, atWaypoint, wp)
