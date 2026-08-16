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

import debug
import guns
import heal
import launcher
import pathfindingHelpers as ph
import raid
import store
from movement import walkTo
from patience import BLOCKED_WAIT_TURNS, clearHold, holdTask
from pathing import manhattan

# Far enough to make progress, short enough that one A* stays inside budget.
_CHUNK = 8
# How far from an unreachable waypoint to look for a usable one.
_WAYPOINT_SLACK = 3
# Manhattan-to-core cutoff for even attempting a local emplacement. A sentinel's
# diagonal standoff is Manhattan 8 from the core and the raider stands one tile
# off its pad, so ~9 is the real reach; 10 is slack. Past it `fallbackEmplacement`
# would find nothing anyway — this just skips its eight engine calls while the
# raider is still travelling (it runs near the 10ms limit).
_LOCAL_SENTINEL_RADIUS = 10


def runRaidBot(bot, ct: Controller) -> None:
    # A raider is a harasser, not an eco bot. It never builds harvesters, never
    # relays, never hijacks for economy — those divert the opening pressure into
    # farming (the bug this chain was rewritten to kill). Its whole job is to
    # press the enemy core and grind the supply feeding it.

    # Checked first, every turn: a turret we placed is a standing investment with
    # nothing else defending it out here, so responding to it taking fire
    # outranks continuing to travel or starting the next build.
    if heal.healNearby(bot, ct, heal.TURRET_HEAL_TYPES):
        return

    # Harvesters get a gunner dropped beside them and nothing else — no
    # hand-grinding. Surplus-gated and persistent.
    if guns.placeGunner(bot, ct):
        return

    # Turrets are bought out of surplus only, so the economy is never starved to
    # arm a raider. This gates *spending*, not the role — below it the raider
    # keeps pressing, it just can't build yet.
    if guns.surplusReady(ct):
        # Parked in range of the core? Densify the sentinel ring locally instead
        # of walking the whole map back for the next published target.
        if buildLocalSentinel(bot, ct):
            return

        target = raid.readTarget(ct)
        if target is not None:
            site, facing = target
            if debug.ENABLED:
                debug.log(
                    "raidBot",
                    f"idx={bot.hivemind.my_index} id={ct.get_id()} "
                    f"rnd={ct.get_current_round()} pos={ct.get_position()} "
                    f"site={site} facing={facing} "
                    f"dist={manhattan(ct.get_position(), site)} "
                    f"hp={ct.get_hp()}/{ct.get_max_hp()}",
                )
            if buildSentinel(bot, ct, site, facing):
                return
            # A long walk from the emplacement? Get thrown toward it instead of
            # stepping there a tile at a time. Held turns fall through to the walk.
            if launcher.requestLaunch(bot, ct, site):
                return
            if approach(bot, ct, site):
                return
            advance(bot, ct, site)
            return

    # Below the surplus gate, or nothing to build: press and besiege the core.
    # Never economy.
    pressCore(bot, ct)


# --- Core siege -------------------------------------------------------------
# The raider's endgame at the enemy core: sever the belts feeding it and wall
# its ring with barriers, so supply is cut and the enemy's own builders can't
# reach the core to repair it or rebuild the line — leaving the sentinels at
# standoff to grind it down uncontested. Harvesters are not touched here; they
# get a gunner (guns.placeGunner) and nothing else.

# Manhattan-to-core within which the raider switches from travelling to siege
# work. The 12 core-ring tiles sit at Manhattan 1-2 from the footprint and the
# raider works from a tile beside them, so ~3 covers being in position.
_SIEGE_RADIUS = 3

# Don't spend the bank to zero on 2-3 Ti siege actions — keep this much back for
# the economy. A floor, not the combat surplus gate: the siege is cheap and is
# the raider's whole job below that gate.
_SIEGE_MIN_TI = 15


def _siegeTileNeedsWork(bot, ct: Controller, tile: Position) -> bool:
    """True if this core-ring tile is either an enemy belt to cut or an open gap
    to wall. Already-barriered tiles, our own turrets, walls, ore, and occupied
    tiles are all 'done' and skipped."""
    t = ph.readTile(ct, bot.hivemind, tile)
    if t.enemy and t.etype in ph.BELT_TYPES:
        return True  # incoming belt to sever
    if t.etype is None and not t.wall and not t.ore and not t.builder:
        return True  # open gap to wall in
    return False


def _nextSiegeTile(bot, ct: Controller, ringTiles) -> Position | None:
    """Nearest core-ring tile that still needs cutting or walling, or None once
    the ring is fully severed and sealed."""
    pos = ct.get_position()
    best = None
    bestD = None
    for t in ringTiles:
        if not ph.inBounds(ct, t):
            continue
        if not _siegeTileNeedsWork(bot, ct, t):
            continue
        d = manhattan(pos, t)
        if bestD is None or d < bestD:
            best, bestD = t, d
    return best


def _siege(bot, ct: Controller, enemyCore: Position) -> bool:
    """Cut a belt / wall a gap on the core ring, or reposition along it.

    Returns True once the raider is in siege range (committed to the ring),
    False while it's still too far — the caller then closes the distance.
    """
    pos = ct.get_position()
    if ph.manhattanToCore(pos, enemyCore) > _SIEGE_RADIUS:
        return False

    ringTiles = ph.coreRing(enemyCore)
    canSpend = ct.get_global_resources() >= _SIEGE_MIN_TI

    if ct.get_action_cooldown() == 0 and canSpend:
        # 1) Sever an adjacent incoming belt — supply denial first.
        for t in ringTiles:
            if manhattan(pos, t) != 1:
                continue
            tile = ph.readTile(ct, bot.hivemind, t)
            if tile.enemy and tile.etype in ph.BELT_TYPES and ct.can_fire(t):
                ct.fire(t)
                if debug.ENABLED:
                    debug.log("raidBot", f"siege-cut {pos}->{t} rnd={ct.get_current_round()}")
                return True
        # 2) Wall an adjacent open ring tile (also seals a belt tile we just cleared).
        for t in ringTiles:
            if manhattan(pos, t) != 1:
                continue
            if ct.can_build_barrier(t):
                ct.build_barrier(t)
                if debug.ENABLED:
                    debug.log("raidBot", f"siege-wall {pos}->{t} rnd={ct.get_current_round()}")
                return True

    # No adjacent work (done, broke, or on cooldown): move to the next ring tile
    # that needs it. If the ring is fully sealed, hold — the siege is maintained,
    # and re-engages the moment the enemy breaks a barrier or lays a new belt.
    nxt = _nextSiegeTile(bot, ct, ringTiles)
    if nxt is not None:
        walkTo(bot, ct, lambda p: manhattan(p, nxt) == 1, nxt, ("siege", nxt.x, nxt.y))
    return True


def pressCore(bot, ct: Controller) -> None:
    """The raider's default when it has nothing to build: besiege the core.

    At the ring, run the siege (cut belts, wall gaps). Otherwise close in — a
    launcher fling toward the core if one's adjacent, then the chunked walk.
    """
    enemyCore = raid.enemyCorePos(bot, ct)
    if enemyCore is None:
        return  # designated raiders always know the core; nothing to press

    if _siege(bot, ct, enemyCore):
        return
    if launcher.requestLaunch(bot, ct, enemyCore):
        return
    if approach(bot, ct, enemyCore):
        return
    advance(bot, ct, enemyCore)


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

    if debug.ENABLED:
        t = ph.readTile(ct, bot.hivemind, site)
        debug.log(
            "raidBot",
            f"idx={bot.hivemind.my_index} pos={pos} site={site} "
            f"can_build_sentinel=False site_etype={t.etype} site_enemy={t.enemy} "
            f"site_builder={t.builder} site_builderEnemy={t.builderEnemy}",
        )

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


def buildLocalSentinel(bot, ct: Controller) -> bool:
    """Lay another sentinel right where the raider stands, if a neighbour tile
    still reaches the enemy core. This is what turns one-and-done into a ring:
    after building the published emplacement a raider is already parked in range
    of the core, so instead of walking the whole map back for the next published
    target it fills the adjacent standoff tiles one per turn.

    Surplus is the caller's gate. `fallbackEmplacement` (no primarySite) does the
    work — it only returns pads whose sentinel bears on the enemy core and are
    buildable, so "in range of the core" and "not stacking on an existing turret"
    both fall out of it, and it never fires anywhere but beside the core.
    """
    if ct.get_action_cooldown() != 0:
        return False
    if ct.get_global_resources() < ct.get_sentinel_cost():
        return False
    if ct.get_unit_count() >= GameConstants.MAX_TEAM_UNITS:
        return False

    enemyCore = raid.enemyCorePos(bot, ct)
    if enemyCore is None:
        return False
    if ph.manhattanToCore(ct.get_position(), enemyCore) > _LOCAL_SENTINEL_RADIUS:
        return False

    choice = raid.fallbackEmplacement(bot, ct)
    if choice is None:
        return False
    site, facing = choice
    ct.build_sentinel(site, facing)
    if debug.ENABLED:
        debug.log(
            "raidBot",
            f"localSentinel at {site} facing {facing} "
            f"idx={bot.hivemind.my_index} rnd={ct.get_current_round()}",
        )
    return True


def approach(bot, ct: Controller, site: Position) -> bool:
    """Final stretch: goal is a tile beside the emplacement, not the emplacement
    itself — a builder standing on it cannot build on it."""
    pos = ct.get_position()
    if manhattan(pos, site) > _CHUNK:
        return False

    def beside(p: Position) -> bool:
        return ph.beltFacing(p, site) is not None

    used = walkTo(bot, ct, beside, site, ("raidSite", site.x, site.y))
    if debug.ENABLED:
        debug.log("raidBot", f"idx={bot.hivemind.my_index} approach pos={pos} "
                              f"site={site} walkTo_returned={used}")
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
