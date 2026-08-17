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
import emplace
import guns
import heal
import launcher
import pathfindingHelpers as ph
import raid
import store
from movement import walkOrRetarget, walkTo
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
            handled, stuck = approach(bot, ct, site)
            if stuck:
                # No route to this emplacement at all — abandon it and press
                # the core instead, same turn (a stuck turn never moved us,
                # so nothing is wasted). The core will pick a fresh site.
                _giveUpOnSite(bot, ct)
                pressCore(bot, ct)
                return
            if handled:
                return
            if advance(bot, ct, site):
                _giveUpOnSite(bot, ct)
                pressCore(bot, ct)
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

# How long a tile the raider fled from gunner fire stays barred (kept in
# bot._siegeDodgeBlacklist). Long enough that a gunner rotating to track can't
# lure it straight back onto the tile it just left and take the hit on arrival.
_SIEGE_DODGE_MEMORY_TURNS = 3

# How far to look for a tile out of a gunner's live line when stepping off it.
# Small on purpose — the raider dances just off the line, it does not retreat.
_DODGE_RADIUS = 2


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


def _nextSiegeTile(bot, ct: Controller, ringTiles, gunners, cache) -> Position | None:
    """Nearest core-ring tile that still needs cutting or walling, or None once
    the ring is fully severed and sealed.

    With gunners in sight, a tile whose every approach is in a live line is
    deprioritised so the raider circles to a safe side of the core. With none,
    this is byte-for-byte the original nearest-by-manhattan pick — `noSafe` is a
    constant False, and strict `<` keeps the first tile at the min distance in
    ring order, exactly as before. Keeping the no-gunner path identical matters:
    perturbing tie-breaks or the walk target here (an earlier version did) shifts
    which belt is cut turn to turn and stalls the siege even with no threat."""
    pos = ct.get_position()
    best = None
    bestKey = None
    for t in ringTiles:
        if not ph.inBounds(ct, t):
            continue
        if not _siegeTileNeedsWork(bot, ct, t):
            continue
        noSafe = bool(gunners) and _safeStandTile(bot, ct, t, gunners, cache) is None
        key = (noSafe, manhattan(pos, t))
        if bestKey is None or key < bestKey:
            best, bestKey = t, key
    return best


def _pruneDodge(bot, ct: Controller) -> dict[tuple[int, int], int]:
    """Drop dodge-blacklist entries older than _SIEGE_DODGE_MEMORY_TURNS; return
    the live dict (bot._siegeDodgeBlacklist)."""
    mem = bot._siegeDodgeBlacklist
    if mem:
        rnd = ct.get_current_round()
        for k in [k for k, marked in mem.items()
                  if rnd - marked >= _SIEGE_DODGE_MEMORY_TURNS]:
            del mem[k]
    return mem


def _safeStandTile(bot, ct: Controller, ringTile: Position, gunners, cache) -> Position | None:
    """The standable tile beside `ringTile` that is out of every gunner's live line
    and closest to where the raider already stands — minimal displacement, never a
    flight away from the gunner. None if every side is in a live line this turn.

    Belt-shielded tiles qualify for free: a gunner's line is blocked by the enemy's
    own belt, so the far side of a hostile relay reads safe. Tiles the raider just
    fled are skipped so a tracking gunner can't lure it back onto one."""
    pos = ct.get_position()
    mem = bot._siegeDodgeBlacklist
    best = None
    bestKey = None
    for n in ph.orthogonalTiles(ringTile):
        if not ph.canStandNow(ct, n):
            continue
        if (n.x, n.y) in mem:
            continue
        if emplace.tileUnderGunnerFire(ct, n, gunners, cache=cache):
            continue
        key = (manhattan(pos, n), n.x, n.y)
        if bestKey is None or key < bestKey:
            best, bestKey = n, key
    return best


def _stepOffFire(bot, ct: Controller, gunners, cache, dodge, objective: Position) -> bool:
    """Step to the nearest tile out of every gunner's live line, biased toward
    `objective` (the ring tile we want to work, or the enemy core). Records the
    vacated tile in dodge memory so a tracking gunner can't lure us straight back.

    True if it moved (or held the move out for a cooldown); False if no off-line
    tile is within reach — genuinely boxed in, and the caller just holds.
    Candidates rank by progress toward the objective first, then by how little we
    move, never by distance from the gunner: the raider dances just off the line
    and stays on the core, which also bleeds the enemy's ammo as it re-aims."""
    pos = ct.get_position()
    best = None
    bestKey = None
    for dy in range(-_DODGE_RADIUS, _DODGE_RADIUS + 1):
        for dx in range(-_DODGE_RADIUS, _DODGE_RADIUS + 1):
            if dx == 0 and dy == 0:
                continue
            cand = Position(pos.x + dx, pos.y + dy)
            if not ph.canStandNow(ct, cand):
                continue
            if (cand.x, cand.y) in dodge:
                continue
            if emplace.tileUnderGunnerFire(ct, cand, gunners, cache=cache):
                continue
            key = (manhattan(cand, objective), manhattan(pos, cand), cand.x, cand.y)
            if bestKey is None or key < bestKey:
                best, bestKey = cand, key
    if best is None:
        return False
    dodge[(pos.x, pos.y)] = ct.get_current_round()
    return walkTo(bot, ct, lambda p, b=best: p.x == b.x and p.y == b.y,
                  best, ("siegeDodge", best.x, best.y))


def _siege(bot, ct: Controller, enemyCore: Position) -> bool:
    """Cut a belt / wall a gap on the core ring, or reposition along it.

    Returns True once the raider is in siege range (committed to the ring),
    False while it's still too far — the caller then closes the distance.

    Safety: the raider never fires or walls from a tile in a gunner's live
    (current-facing) line, and never stands to work from one. When the near
    approach is exposed it circles to a covered side (_nextSiegeTile /
    _safeStandTile); when nothing near is safe it steps one tile off the line and
    dances rather than retreating (_stepOffFire).
    """
    pos = ct.get_position()
    if ph.manhattanToCore(pos, enemyCore) > _SIEGE_RADIUS:
        return False

    ringTiles = ph.coreRing(enemyCore)
    canSpend = ct.get_global_resources() >= _SIEGE_MIN_TI

    gunners = emplace.enemyGunners(ct)
    fireCache: dict = {}
    dodge = _pruneDodge(bot, ct)
    exposed = bool(gunners) and emplace.tileUnderGunnerFire(ct, pos, gunners, cache=fireCache)

    # Act only from a tile outside every gunner's live line. Standing in one, we
    # skip the cut/wall this turn and reposition below — a supply cut is never
    # worth handing the gun a free shot.
    if ct.get_action_cooldown() == 0 and canSpend and not exposed:
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

    # No adjacent work (done, broke, on cooldown, or this spot is exposed): move
    # toward the next ring tile that needs work.
    nxt = _nextSiegeTile(bot, ct, ringTiles, gunners, fireCache)

    if not gunners:
        # No threat: the original walk, untouched — head for any tile beside nxt,
        # hold once there. Keeping this path identical to before is what stops the
        # safety machinery from perturbing an unthreatened siege (see _nextSiegeTile).
        if nxt is not None:
            walkTo(bot, ct, lambda p: manhattan(p, nxt) == 1, nxt, ("siege", nxt.x, nxt.y))
        return True

    # Gunners in sight: approach nxt only from a tile off their live line, and
    # only if such a tile exists (else the ring's exposed here — dance below).
    if nxt is not None and _safeStandTile(bot, ct, nxt, gunners, fireCache) is not None:
        def safeBeside(p, tgt=nxt):
            return (manhattan(p, tgt) == 1
                    and not emplace.tileUnderGunnerFire(ct, p, gunners, cache=fireCache)
                    and (p.x, p.y) not in bot._siegeDodgeBlacklist)

        walkTo(bot, ct, safeBeside, nxt, ("siege", nxt.x, nxt.y))
        return True

    # Ring sealed, or every approach to nxt is in a live line right now. If we're
    # sitting in one, step off it (biased toward the work / the core) instead of
    # dying in place; otherwise hold — the sentinel ring keeps grinding the core.
    if exposed:
        _stepOffFire(bot, ct, gunners, fireCache, dodge,
                     nxt if nxt is not None else enemyCore)
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
    # The enemy core is already the terminal target — there's nothing further
    # to fall back to, so `stuck` is dropped on the floor here. walkOrRetarget
    # still did its job internally (clean walkPath/walkGoal/walkAvoid), which
    # is what keeps this from grinding the same dead search every turn.
    handled, _stuck = approach(bot, ct, enemyCore)
    if handled:
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

    # The core picks published targets blind (no vision at the enemy core). Re-vet
    # here with the raider's own eyes: never drop a sentinel where a visible enemy
    # gun already bears at its current facing — a free shot the instant it's built.
    # Take a vetted neighbour if one still reaches the core, else clear the target
    # so the core republishes.
    if emplace.siteInDirectTurretLos(ct, site, emplace.enemyTurrets(ct), cache={}):
        if debug.ENABLED:
            debug.log("raidBot", f"veto published site {site} — in enemy turret "
                                 f"line rnd={ct.get_current_round()}")
        choice = raid.fallbackEmplacement(bot, ct, site)
        if choice is not None:
            clearHold(bot)
            _finishBuild(ct, *choice)
            return True
        store.put(ct, store.RAID_TARGET, 0)
        clearHold(bot)
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


def _giveUpOnSite(bot, ct: Controller) -> None:
    """The published emplacement is unroutable — clear it so the core picks a
    fresh one. Sibling to buildSentinel's raidSiteBlocked cleanup above, for
    the walk-there half instead of the build-it half. No clearHold: the
    walkOrRetarget call that reported `stuck` already cleared it."""
    if debug.ENABLED:
        debug.log("raidBot", f"idx={bot.hivemind.my_index} pos={ct.get_position()} "
                              f"site unroutable — abandoning RAID_TARGET")
    store.put(ct, store.RAID_TARGET, 0)


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


def approach(bot, ct: Controller, site: Position) -> tuple[bool, bool]:
    """Final stretch: goal is a tile beside the emplacement, not the emplacement
    itself — a builder standing on it cannot build on it.

    Returns (handled, stuck). handled is unrelated to whether the walk
    succeeded — it's just "close enough that this is our call to make" — so
    callers keep checking it exactly as before. stuck is True only when
    walkOrRetarget gave up on reaching `site` specifically (no route at all,
    patience spent); callers use that to abandon this target rather than
    spin on it forever (see runRaidBot / pressCore).
    """
    pos = ct.get_position()
    if manhattan(pos, site) > _CHUNK:
        return False, False

    def beside(p: Position) -> bool:
        return ph.beltFacing(p, site) is not None

    stuck = walkOrRetarget(bot, ct, beside, site, ("raidSite", site.x, site.y))
    if debug.ENABLED:
        debug.log("raidBot", f"idx={bot.hivemind.my_index} approach pos={pos} "
                              f"site={site} stuck={stuck}")
    return True, stuck


def advance(bot, ct: Controller, site: Position) -> bool:
    """Too far for one search: walk a waypoint at a time instead.

    Returns True iff walkOrRetarget gave up reaching `site` — see approach's
    docstring for what callers do with that.
    """
    wp = _waypoint(bot, ct, site)
    if wp is None:
        return False

    def atWaypoint(p: Position) -> bool:
        return p.x == wp.x and p.y == wp.y

    return walkOrRetarget(bot, ct, atWaypoint, wp, ("waypoint", wp.x, wp.y))


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
