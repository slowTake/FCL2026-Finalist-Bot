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
from patience import BLOCKED_WAIT_TURNS, clearHold, holdTask, stallCheck, stallForget
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
# Rounds from game start before a raider may build any turret (gunner or sentinel).
# On a small map the opening pair can reach a target and dump the whole bank on
# turrets in the first few turns, starving the harvesters the economy is built on —
# this holds their spend so the eco opens first. Launchers are not gated (the first
# raider still plants the core launcher in main._runBuilder). Measured from round 0,
# so replenishment raiders spawned long after are always past it.
_TURRET_BUILD_DELAY_ROUNDS = 10


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

    # Opening hold: no turret of any kind for the first _TURRET_BUILD_DELAY_ROUNDS
    # (see the constant). Below this the raider still travels and presses the core —
    # it just can't spend the opening bank on turrets before the harvesters are down.
    canBuildTurrets = ct.get_current_round() >= _TURRET_BUILD_DELAY_ROUNDS

    # Harvesters get a gunner dropped beside them and nothing else — no
    # hand-grinding. Surplus-gated and persistent. The enemy core is passed so
    # placeGunner can rank a core-feeding harvester above answering a turret (the
    # `and` short-circuits, so it's only computed once we're past the opening hold).
    if canBuildTurrets and guns.placeGunner(bot, ct, raid.enemyCorePos(bot, ct)):
        return

    # Turrets are bought out of surplus only, so the economy is never starved to
    # arm a raider. This gates *spending*, not the role — below it the raider
    # keeps pressing, it just can't build yet.
    if canBuildTurrets and guns.surplusReady(ct):
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
#
# Only the 8 orthogonal edge tiles are walled — the 4 diagonal corners are left
# open (_siege uses coreEdgeRing, not coreRing). A barrier on a corner blocks a
# diagonal line into the core, which is exactly the line our own turrets fire on
# (gunner facing allows perfect diagonals; a sentinel's reach covers them), so
# walling the corners was sealing off our own guns. The tradeoff is 4 gaps a
# repairing enemy builder can slip through — accepted: supply is still cut,
# because every feeding belt sits on an edge tile, which we still wall.

# Manhattan-to-core within which the raider switches from travelling to siege
# work. The 12 core-ring tiles sit at Manhattan 1-2 from the footprint and the
# raider works from a tile beside them, so ~3 covers being in position.
_SIEGE_RADIUS = 3

# Don't spend the bank to zero on 2-3 Ti siege actions — keep this much back for
# the economy. A floor, not the combat surplus gate: the siege is cheap and is
# the raider's whole job below that gate.
_SIEGE_MIN_TI = 15

# How long pressCore holds off re-running the travel search after it found no route
# to the enemy core. The failed A*+BFS is ~9ms and, left ungated, reruns every turn
# while the core is unreachable — e.g. after a hostile launcher flings the raider
# outside the core-ring wall its committed waypoint sits behind. The no-route state
# won't change on its own, and _siege/requestLaunch still run every turn to catch it
# clearing, so holding the re-search off this many rounds costs nothing and stops the
# TLE. On expiry it re-probes once in case the ground opened.
_PRESS_CORE_BACKOFF_ROUNDS = 25

# How long a tile the raider fled from gunner fire stays barred (kept in
# bot._siegeDodgeBlacklist). Long enough that a gunner rotating to track can't
# lure it straight back onto the tile it just left and take the hit on arrival.
_SIEGE_DODGE_MEMORY_TURNS = 3

# How far to look for a tile out of a gunner's live line when stepping off it.
# Small on purpose — the raider dances just off the line, it does not retreat.
_DODGE_RADIUS = 2

# How long a tile the siege walk found no route to stays barred (kept in
# bot._siegeUnreachable). Covers both tiles the siege walks to: the ring tiles it
# works (_nextSiegeTile) and the tiles it dodges to (_stepOffFire).
#
# Neither walk had a dead-search guard: unlike pressCore's travel (gated by
# _PRESS_CORE_BACKOFF_ROUNDS) they re-ran the full ~9ms A*+BFS at an unreachable
# tile every single turn, which on a 10ms budget is a TLE every turn — the raider
# stops acting entirely. On the ring it shows up right after the apron launcher
# goes in: that seals the near edge tile, so _nextSiegeTile hands back a tile on
# the far side of the core that may have no route at all. On the dodge it shows up
# because canStandNow answers "legally occupiable", not "reachable" — a tile two
# out across a wall passes it. Barring lets the next pick be one we can actually
# get to; once every candidate is barred the caller holds without searching.
#
# Long enough to stop the grind, short enough that ground which opens up is
# re-probed within a few builder-actions. Not shorter: the gunner-branch walk
# (safeBeside) carries fire-line constraints, so its failures can be transient
# geometry rather than true unreachability, and this window is the cost of that
# coarseness — see _markIfNoRoute.
_SIEGE_UNREACHABLE_MEMORY_TURNS = 20

# Belt-cut give-up thresholds (kept in bot._beltGrindLow / bot._beltGaveUp). The
# belt-cut loop fires at an adjacent enemy belt every turn it can; with no
# progress check, an enemy that repairs the belt as fast as we grind it bleeds us
# 2 Ti/shot forever. _BELT_STALL_TURNS is how many consecutive engaged looks (via
# patience.stallCheck, not elapsed rounds — see there for why) we tolerate without
# the belt's HP hitting a new low before ruling it out-repaired and moving on —
# long enough to break a belt we're genuinely winning (a 20 HP belt is ~10
# unopposed shots), short enough to cap the wasted Ti at a bad trade.
# _BELT_GIVEUP_MEMORY_TURNS is how long the tile then stays barred before we
# re-probe it, so a belt the enemy stops tending is picked back up — matching the
# self-healing window on the siege blacklists above.
_BELT_STALL_TURNS = 11
_BELT_GIVEUP_MEMORY_TURNS = 25


def _siegeTileNeedsWork(bot, ct: Controller, tile: Position) -> bool:
    """True if this core-ring tile is either an enemy belt to cut or an open gap
    to wall. Already-barriered tiles, our own turrets, walls, ore, and occupied
    tiles are all 'done' and skipped. A belt in bot._beltGaveUp (ruled out-repaired
    by the cut loop) is also 'done' for targeting purposes — otherwise the nearest
    tile 'needing work' is one this raider has explicitly sworn off, and it parks
    beside it doing nothing for the whole give-up window instead of circling to
    real work elsewhere on the ring."""
    t = ph.readTile(ct, bot.hivemind, tile)
    if t.enemy and t.etype in ph.BELT_TYPES:
        return (tile.x, tile.y) not in bot._beltGaveUp  # incoming belt to sever
    if t.etype is None and not t.wall and not t.ore and not t.builder:
        return True  # open gap to wall in
    return False


def _coreClear(bot, ct: Controller, enemyCore: Position) -> bool:
    """True if, in the raider's *current* vision, no enemy supply belt and no enemy
    builder sits on the core ring — meaning a wall would seal nothing, so the raider
    plants gunners on the gaps instead (they seal the tile too, and grind the core).

    Only tiles in vision this turn count: a belt or builder remembered from the
    ledger but not seen right now does not veto the switch, matching 'current
    vision'. An unseen tile is treated as clear. The full 12-tile ring is scanned,
    not just the edges — a builder can stand on a diagonal corner too."""
    for t in ph.coreRing(enemyCore):
        if not ph.inBounds(ct, t):
            continue
        if not ct.is_in_vision(t):
            continue
        tile = ph.readTile(ct, bot.hivemind, t)
        if tile.enemy and tile.etype in ph.BELT_TYPES:
            return False
        if tile.builder and tile.builderEnemy:
            return False
    return True


def _beltRepairGuardTiles(ct: Controller) -> set[tuple[int, int]]:
    """Tiles a visible enemy builder could repair this turn: each builder's own
    tile plus its 4 orthogonal neighbours. Build/fire/repair are adjacent-only and
    diagonals don't qualify, so orthogonal adjacency is the stand-in for "is it
    repairing" — the engine exposes no enemy action state (no target/order getter),
    so we read position, not intent. Same adjacency rule guns._builderThreatening
    Structure used before it was removed.

    An entity scan on purpose, not readTile().builderEnemy: the tile read falls back
    to the shared ledger outside current vision and tracks a mobile builder
    unreliably, and this set must mean *right now* — the guard that consumes it
    re-decides every single turn (skip, never bar).

    Built once per siege action turn and shared across the whole ring, never per
    candidate belt — the raider runs near the 10ms budget (module header)."""
    myTeam = ct.get_team()
    guarded: set[tuple[int, int]] = set()
    for ent in bot.hivemind.visible_entities:
        if ent["team"] == myTeam:
            continue
        if ent["type"] != EntityType.BUILDER_BOT:
            continue
        epos = ent["pos"]
        guarded.add((epos.x, epos.y))
        for n in ph.orthogonalTiles(epos):
            guarded.add((n.x, n.y))
    return guarded


def _nextSiegeTile(bot, ct: Controller, ringTiles, gunners, cache) -> Position | None:
    """Nearest core-ring tile that still needs cutting or walling, or None once
    the ring is fully severed and sealed.

    With gunners in sight, a tile whose every approach is in a live line is
    deprioritised so the raider circles to a safe side of the core. With none,
    this is byte-for-byte the original nearest-by-manhattan pick — `noSafe` is a
    constant False, and strict `<` keeps the first tile at the min distance in
    ring order, exactly as before. Keeping the no-gunner path identical matters:
    perturbing tie-breaks or the walk target here (an earlier version did) shifts
    which belt is cut turn to turn and stalls the siege even with no threat.

    Tiles the walk already proved unreachable are skipped (_markIfNoRoute). That
    skip is what turns a dead search into a retarget instead of a per-turn TLE,
    and it costs the untouched path nothing: the bar list is empty until a search
    actually fails, so a healthy siege picks exactly the tile it always did."""
    pos = ct.get_position()
    barred = bot._siegeUnreachable
    best = None
    bestKey = None
    for t in ringTiles:
        if not ph.inBounds(ct, t):
            continue
        if (t.x, t.y) in barred:
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


def _pruneUnreachable(bot, ct: Controller) -> dict[tuple[int, int], int]:
    """Drop siege-unreachable entries older than _SIEGE_UNREACHABLE_MEMORY_TURNS;
    return the live dict (bot._siegeUnreachable). Same shape as _pruneDodge."""
    mem = bot._siegeUnreachable
    if mem:
        rnd = ct.get_current_round()
        for k in [k for k, marked in mem.items()
                  if rnd - marked >= _SIEGE_UNREACHABLE_MEMORY_TURNS]:
            del mem[k]
    return mem


def _pruneBeltGaveUp(bot, ct: Controller) -> dict[tuple[int, int], int]:
    """Drop belt give-up entries older than _BELT_GIVEUP_MEMORY_TURNS and forget
    the matching HP-progress memory; return the live dict (bot._beltGaveUp). Same
    age-window shape as _pruneUnreachable. Expiry is what lets a raider re-probe a
    belt the enemy has stopped repairing."""
    mem = bot._beltGaveUp
    if mem:
        rnd = ct.get_current_round()
        for k in [k for k, marked in mem.items()
                  if rnd - marked >= _BELT_GIVEUP_MEMORY_TURNS]:
            del mem[k]
            bot._beltGrindLow.pop(k, None)
    return mem


def _markIfNoRoute(bot, ct: Controller, tile: Position, seq: int) -> None:
    """Bar `tile` from siege walk targeting if the walk that just ran found no route
    to it. `seq` is bot.noRouteSeq sampled immediately before that walk. Serves both
    walk targets the siege picks — ring tiles to work, and dodge tiles to step to.

    Keyed on the counter movement.walkTo bumps, not on holdReason and not on a
    round stamp. holdReason is sticky across turns and goals; a round stamp is
    ambiguous once two walks share a turn, which they do (runRaidBot lets a failed
    `approach` fall through to `pressCore` in the same turn). Either would bar a
    ring tile the raider actually reached — and a wrongly barred tile is a gap left
    unwalled for the whole memory window.

    Known coarseness, accepted: this records "the walk failed", which is not always
    "the tile is unreachable". The gunner-branch goal (safeBeside) also demands the
    approach be out of every gunner's live line, so a failure there can be transient
    fire geometry — and because ring bars and dodge bars share one list, that tile
    leaves the dodge pool too. It needs the geometry to line up (the _safeStandTile
    precheck means a qualifying tile existed when the walk ran) and it self-heals on
    expiry. If a replay ever shows a raider holding under fire beside a visibly open
    tile, the fix is to tag bars with the goal kind and expire the constrained ones
    sooner — not to drop the bar, which is what stops the per-turn TLE."""
    if bot.noRouteSeq == seq:
        return
    bot._siegeUnreachable[(tile.x, tile.y)] = ct.get_current_round()
    # Same cleanup walkOrRetarget does when it gives up on a goal: the cached path
    # and the avoid-set were built for a route we've now abandoned, and leaving the
    # avoid-set behind poisons the *next* tile's search with tiles that were only
    # ever refused on the way to this one — which would bar that tile too, and so on
    # around the ring.
    bot.walkPath = None
    bot.walkGoal = None
    bot.walkAvoid.clear()
    # Drop the hold too, for the same reason. We are not waiting on this tile any
    # more — we've given up on it — so leaving "noRoute" set hands a later
    # approach/advance a reason it never earned, which is the exact sticky-flag
    # failure this whole change exists to remove.
    clearHold(bot)
    if debug.ENABLED:
        debug.log("raidBot", f"siege-unreachable {tile} barred "
                             f"rnd={ct.get_current_round()}")


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
    barred = bot._siegeUnreachable
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
            # Standable is not reachable: a tile two out can sit across a wall, and
            # walking to it is a dead A*+BFS re-run on every exposed turn — the same
            # per-turn TLE the ring walk had. Skip the ones already proved routeless.
            if (cand.x, cand.y) in barred:
                continue
            if emplace.tileUnderGunnerFire(ct, cand, gunners, cache=cache):
                continue
            key = (manhattan(cand, objective), manhattan(pos, cand), cand.x, cand.y)
            if bestKey is None or key < bestKey:
                best, bestKey = cand, key
    if best is None:
        return False
    dodge[(pos.x, pos.y)] = ct.get_current_round()
    seq = bot.noRouteSeq
    used = walkTo(bot, ct, lambda p, b=best: p.x == b.x and p.y == b.y,
                  best, ("siegeDodge", best.x, best.y))
    # Barring here strictly beats the old behaviour: an unreachable pick used to
    # cost the search *and* fail to move anyway. Now the next exposed turn spends
    # its dodge on a tile it can actually get to, or correctly reports boxed-in.
    _markIfNoRoute(bot, ct, best, seq)
    return used


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

    # Edge ring, not the full 12: the 4 diagonal corners are left open on purpose
    # (see the siege comment block above). This one set feeds the cut loop, the
    # wall loop and _nextSiegeTile alike, so dropping the corners here keeps the
    # raider off them entirely — it never walls one and never routes to one — with
    # no per-tile branch in the hot loops. No belt cut is lost: belts feed the core
    # cardinally, so a feeding belt is always on an edge, never a diagonal corner.
    ringTiles = ph.coreEdgeRing(enemyCore)
    canSpend = ct.get_global_resources() >= _SIEGE_MIN_TI

    gunners = emplace.enemyGunners(ct)
    fireCache: dict = {}
    dodge = _pruneDodge(bot, ct)
    _pruneUnreachable(bot, ct)
    _pruneBeltGaveUp(bot, ct)
    exposed = bool(gunners) and emplace.tileUnderGunnerFire(ct, pos, gunners, cache=fireCache)

    # Act only from a tile outside every gunner's live line. Standing in one, we
    # skip the cut/wall this turn and reposition below — a supply cut is never
    # worth handing the gun a free shot.
    if ct.get_action_cooldown() == 0 and canSpend and not exposed:
        # Enemy builders in repair position, gathered once for the whole ring — not
        # per candidate belt, and not up in the every-turn prep block above: only the
        # cut loop consumes it, so only action turns pay the scan.
        repairGuard = _beltRepairGuardTiles(ct)
        # 1) Sever an adjacent incoming belt — supply denial first.
        for t in ringTiles:
            if manhattan(pos, t) != 1:
                continue
            tile = ph.readTile(ct, bot.hivemind, t)
            key = (t.x, t.y)
            if not (tile.enemy and tile.etype in ph.BELT_TYPES):
                # Not a live enemy belt right now (destroyed, rebuilt as something
                # else, or captured) — forget any grind memory from a previous belt
                # here so a later rebuild on this tile is judged fresh, not given up
                # on sight against a stale low from the one we already killed.
                stallForget(bot._beltGrindLow, key)
                continue
            if not ct.can_fire(t):
                continue
            # Already ruled out-repaired within the give-up window: don't re-open
            # the trade. Skip it as a cut target — _siegeTileNeedsWork also treats
            # it as done, so the reposition step below won't park beside it either.
            if key in bot._beltGaveUp:
                continue
            # Repairer guard: a visible enemy builder standing on or orthogonally
            # adjacent to this belt is almost certainly repairing it, and repair
            # keeps pace with our cut (2 Ti/shot both ways — docs/observations.md), so
            # firing now is a guaranteed losing trade. Skip THIS TURN only —
            # deliberately no bot._beltGaveUp entry: the builder is mobile, this
            # re-checks from fresh vision every turn, and the cut resumes the instant
            # it steps away (a give-up bar would waste that window).
            #
            # Leave _beltGrindLow untouched — do NOT stallForget here. A skipped turn
            # is an absence to stallCheck (its entry is left as-is), so the stale count
            # survives a *flickering* repairer that steps on and off, and the ~22-Ti
            # out-repair backstop below still fires against it. Forgetting instead would
            # reset that count on every unguarded resumption, so an alternating repairer
            # would bleed us ~1 Ti/turn forever without ever barring — worse than the
            # capped path this guard is meant to beat. Not forgetting can't cause a
            # false give-up either: a fully repaired 20-HP belt is at most ~10
            # no-new-low looks from setting a fresh low, under _BELT_STALL_TURNS (11).
            if key in repairGuard:
                if debug.ENABLED:
                    debug.log("raidBot", f"siege-cut-guarded {t} "
                                         f"rnd={ct.get_current_round()}")
                continue
            # Give-up check: only bleed Ti into a belt we're actually grinding
            # down. If HP is unreadable (no building id), fall through and fire as
            # before.
            bid = ct.get_tile_building_id(t)
            hp = ct.get_hp(bid) if bid is not None else None
            if hp is not None and stallCheck(bot._beltGrindLow, key, hp, _BELT_STALL_TURNS):
                bot._beltGaveUp[key] = ct.get_current_round()
                stallForget(bot._beltGrindLow, key)
                if debug.ENABLED:
                    debug.log("raidBot", f"siege-cut-giveup {t} hp={hp} "
                                         f"stalled>={_BELT_STALL_TURNS} rnd={ct.get_current_round()}")
                continue
            ct.fire(t)
            if debug.ENABLED:
                debug.log("raidBot", f"siege-cut {pos}->{t} rnd={ct.get_current_round()}")
            return True
        # A clear core (no belt to sever, no enemy builder to lock out in current
        # vision) means a wall would seal nothing and a launcher has no builders to
        # fling — so against a build-nothing rush the raider goes gunners-only:
        # no launcher, no barrier fallback, just gunners to grind the core (or
        # nothing this turn and retry once funded). Read once per action turn now,
        # since it gates both the launcher and the wall below; a successful cut
        # above still returns before this, but a belt skipped as given-up or
        # unfireable falls through and pays for it, same as an all-sealed ring.
        coreClear = _coreClear(bot, ct, enemyCore)
        # 1.5) Embed the one apron launcher in the ring, in place of a barrier on
        # the enemy-homeward edge tile — surplus-gated, so it never displaces the
        # cheap cut/wall grind. A no-op once one is already planted. Skipped on a
        # clear core: no enemy builders to trap.
        if not coreClear and buildApronLauncher(bot, ct, enemyCore):
            return True
        # 2) Seal an adjacent open ring tile (also seals a belt tile we just
        #    cleared). Normally a barrier — it cuts supply and locks out repairing
        #    builders. But when the core is clear (_coreClear) a wall seals nothing;
        #    plant a gunner instead: it occupies the gap exactly as a barrier would
        #    AND grinds the core. If a gunner is unaffordable this turn, leave the
        #    gap open for a funded turn rather than wasting the tile on a wall.
        for t in ringTiles:
            if manhattan(pos, t) != 1:
                continue
            if not ct.can_build_barrier(t):
                continue
            if coreClear:
                if (ct.get_global_resources() >= ct.get_gunner_cost() + _SIEGE_MIN_TI
                        and guns.buildSiegeGunner(ct, t, enemyCore)):
                    return True
                continue  # clear core: gunner-or-nothing, never a wall
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
            seq = bot.noRouteSeq
            walkTo(bot, ct, lambda p: manhattan(p, nxt) == 1, nxt, ("siege", nxt.x, nxt.y))
            _markIfNoRoute(bot, ct, nxt, seq)
        return True

    # Gunners in sight: approach nxt only from a tile off their live line, when we
    # can actually identify one.
    if nxt is not None:
        if _safeStandTile(bot, ct, nxt, gunners, fireCache) is not None:
            def safeBeside(p, tgt=nxt):
                return (manhattan(p, tgt) == 1
                        and not emplace.tileUnderGunnerFire(ct, p, gunners, cache=fireCache)
                        and (p.x, p.y) not in bot._siegeDodgeBlacklist)

            seq = bot.noRouteSeq
            walkTo(bot, ct, safeBeside, nxt, ("siege", nxt.x, nxt.y))
            _markIfNoRoute(bot, ct, nxt, seq)
            return True

        if not exposed:
            # No *identifiable* safe approach, but we are not under fire where we
            # stand. Close in anyway with the plain walk instead of holding.
            #
            # Holding here was a hard freeze, and the common cause is not danger but
            # ignorance: canStandNow answers False for a tile out of vision (it
            # cannot commit to standing somewhere it can't see), so every approach to
            # a ring tile more than ~4.5 tiles off reads unsafe, _safeStandTile
            # returns None, and the raider refuses to walk to the one thing that
            # would resolve it — getting close enough to look. Measured on a drumlin
            # mirror: a raider sat on the same tile for 204 straight turns with a
            # live work target and a full bank, while the enemy core it was besieging
            # sat at full HP.
            #
            # Safe to close in because exposure is re-checked every turn from the
            # tile we actually occupy: the moment this walk does step into a live
            # line, `exposed` is True next turn and _stepOffFire dances us off it.
            # Approaching and dodging is the loop this role is built on; standing
            # still is the only state it cannot recover from.
            seq = bot.noRouteSeq
            walkTo(bot, ct, lambda p: manhattan(p, nxt) == 1, nxt, ("siege", nxt.x, nxt.y))
            _markIfNoRoute(bot, ct, nxt, seq)
            return True

    # Ring sealed (nxt is None), or every approach is covered and we are standing in
    # a live line. If we're in one, step off it (biased toward the work / the core)
    # instead of dying in place; otherwise hold — the sentinel ring keeps grinding.
    if exposed:
        _stepOffFire(bot, ct, gunners, fireCache, dodge,
                     nxt if nxt is not None else enemyCore)
    elif debug.ENABLED:
        # The one remaining do-nothing state. Logged because "raider parked at the
        # enemy core" is invisible in a replay until you count rounds — see the
        # 204-turn freeze this branch used to swallow.
        debug.log("raidBot", f"siege-park pos={pos} nxt={nxt} gunners={len(gunners)} "
                             f"canSpend={canSpend} rnd={ct.get_current_round()}")
    return True


def _creepToward(bot, ct: Controller, target: Position) -> bool:
    """One greedy cardinal step that strictly shortens the distance to `target`.
    True if it moved (or is holding a move cooldown). No planner, no cache: four
    neighbours, four engine calls, no A*.

    This is what the raider does *instead of standing still* while the press-core
    back-off suppresses real searching. The back-off exists to stop a dead ~9ms
    A*+BFS re-running every turn, but on its own it also stopped the raider
    moving at all for 25 rounds at a stretch, which in a replay is exactly the
    reported "raider freezes mid-traversal" — the unit is fine, it is just
    forbidden from planning and given nothing else to do.

    Strictly-decreasing only, so it can never oscillate and never wanders: it
    cannot round an obstacle by itself, which is fine, because rounding obstacles
    is the planner's job and the back-off re-probes with a real search when it
    expires. What the creep buys is that the re-probe happens from a *different
    tile* — a raider flung against the outside of a wall drifts along it, and the
    search that failed from the landing tile often succeeds a few tiles on.

    Deliberately does not clear the back-off on success: a creep that moves most
    turns would otherwise re-enable the search every turn and bring the TLE back."""
    if ct.get_move_cooldown() != 0:
        return True
    pos = ct.get_position()
    best = None
    bestD = ph.manhattanToCore(pos, target)
    for n in ph.orthogonalTiles(pos):
        d = ph.manhattanToCore(n, target)
        if d >= bestD:
            continue
        if not ph.canStandNow(ct, n):
            continue
        facing = ph.beltFacing(pos, n)
        if facing is None or not ct.can_move(facing):
            continue
        best, bestD = facing, d
    if best is None:
        return False
    ct.move(best)
    if debug.ENABLED:
        debug.log("raidBot", f"press-creep {pos} dir={best} "
                             f"rnd={ct.get_current_round()}")
    return True


def pressCore(bot, ct: Controller) -> None:
    """The raider's default when it has nothing to build: besiege the core.

    At the ring, run the siege (cut belts, wall gaps). Otherwise close in — a
    launcher fling toward the core if one's adjacent, then the chunked walk.
    """
    enemyCore = raid.enemyCorePos(bot, ct)
    if enemyCore is None:
        return  # designated raiders always know the core; nothing to press

    # Unverified core (mirror guess on an unfingerprinted map): the two opening
    # raiders probe *different* candidates so the pair splits the point/y-flip/
    # x-flip ambiguity instead of both walking to the same wrong point-mirror. The
    # second raider takes the second live candidate; whichever lays eyes on the
    # real core first broadcasts it (tier 1) and both — plus the core — converge.
    if not raid.enemyCoreVerified(bot, ct):
        live = raid.enemyCoreCandidatesAlive(bot, ct)
        if (len(live) >= 2
                and store.readRole(ct, store.ROLE_RAIDER_2) == bot.hivemind.my_index):
            enemyCore = live[1]

    # A target that *changes* (a candidate was disproven and the guess advanced)
    # must re-probe now, not sit out a stale no-route back-off keyed to the old
    # tile — the route to the new candidate is a different problem.
    if bot._lastPressTarget != (enemyCore.x, enemyCore.y):
        bot._pressCoreBackoffUntil = 0
        bot._lastPressTarget = (enemyCore.x, enemyCore.y)

    if _siege(bot, ct, enemyCore):
        bot._pressCoreBackoffUntil = 0  # in siege range: the travel problem is solved
        return
    if launcher.requestLaunch(bot, ct, enemyCore):
        return

    # Back-off. The enemy core is the terminal target — there is nothing to
    # retarget to — so a raider that currently has no route to it (e.g. flung
    # outside the core-ring wall) would otherwise re-run the ~9ms A*+BFS every turn
    # and TLE. The no-route state won't change on its own, and _siege/requestLaunch
    # above still run every turn to catch it clearing, so once a search comes up
    # empty we hold off re-searching for a spell. Re-probes when the window expires.
    #
    # Suppressing the *search* is not a reason to stand still: creep one greedy
    # step coreward instead (no planner, so the back-off's whole purpose holds).
    # Idling here was the second half of the reported freeze — see _creepToward.
    if ct.get_current_round() < bot._pressCoreBackoffUntil:
        _creepToward(bot, ct, enemyCore)
        return

    seq = bot.noRouteSeq
    handled, stuck = approach(bot, ct, enemyCore)
    if not handled:
        stuck = advance(bot, ct, enemyCore)
    # `stuck` is the patience-spent signal; the counter catches the earlier turns
    # too, so the back-off trips on the first failed search, not the fourth. The
    # counter and not holdReason: the reason is sticky across turns and goals, so a
    # raider could inherit a "noRoute" left by an unrelated earlier walk and put
    # itself in a 25-round back-off without ever having failed a search for the core.
    if stuck or bot.noRouteSeq != seq:
        bot._pressCoreBackoffUntil = ct.get_current_round() + _PRESS_CORE_BACKOFF_ROUNDS


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
    # Don't densify a sentinel ring around an unverified mirror guess — same
    # reason as publishTarget's gate; wait until vision or the fingerprint confirms.
    if not raid.enemyCoreVerified(bot, ct):
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


def buildApronLauncher(bot, ct: Controller, enemyCore: Position) -> bool:
    """Plant one launcher embedded in the enemy core's edge ring, on the
    enemy-homeward side their repair/relay builders approach from — in place of a
    barrier we'd otherwise wall that tile with. Once up, runLauncher grabs those
    builders and flings them *away* from the core for free (no ammo), turning the
    open-corner repair gap (see the siege header) into a trap, and sealing that
    ring tile as a blocker besides.

    Called from `_siege`'s action block, so the raider is already at the ring,
    off cooldown and out of gunner fire. Opportunistic and non-starving: it only
    builds when the bank can afford the launcher *and still* clear _SIEGE_MIN_TI
    afterwards, so the cheap belt-cut/wall grind is never starved to plant it. (Not
    gated on guns.surplusReady — that fires the sentinel step, which returns before
    _siege ever runs, so _siege only sees sub-surplus turns.) One per siege: the
    ring scan suppresses a second and rebuilds if this one is destroyed, so no
    scarce store slot is spent tracking it.
    """
    if ct.get_global_resources() < ct.get_launcher_cost() + _SIEGE_MIN_TI:
        return False
    if ct.get_unit_count() >= GameConstants.MAX_TEAM_UNITS:
        return False

    pos = ct.get_position()

    # One per siege. Scan the full 12-tile ring (edges + corners) for a launcher we
    # already planted; self-healing, since a destroyed one leaves the scan empty and
    # the next raider rebuilds.
    for tile in ph.coreRing(enemyCore):
        t = ph.readTile(ct, bot.hivemind, tile)
        if t.etype == EntityType.LAUNCHER and not t.enemy:
            return False

    # Build on the edge-ring tile we can reach this turn that sits farthest from our
    # own core — the enemy-homeward side their builders come from, where the launcher
    # grabs them as they arrive. beltFacing is the orthogonal-adjacency test (must be
    # adjacent to build); can_build_launcher is the real legality gate. If none
    # qualifies now, keep sieging — a later turn parks the raider beside a usable one.
    best = None
    bestD = -1
    for tile in ph.coreEdgeRing(enemyCore):
        if ph.beltFacing(pos, tile) is None:
            continue
        if not ct.can_build_launcher(tile):
            continue
        d = ph.manhattanToCore(tile, bot.corePos) if bot.corePos is not None else 0
        if d > bestD:
            bestD = d
            best = tile
    if best is None:
        return False

    ct.build_launcher(best)
    ph.invalidateTile(bot.hivemind, best)
    if debug.ENABLED:
        debug.log(
            "raidBot",
            f"apron launcher placed at {best} enemyCore={enemyCore} "
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
    """Too far for one search: walk toward a committed waypoint, a chunk at a time.

    Returns True iff walkOrRetarget gave up reaching `site` — see approach's
    docstring for what callers do with that.

    The waypoint is committed on `bot._raidWaypoint` and reused until it is
    reached, the target changes, the raider drifts off it, or it goes impassable,
    so walkTo caches one ~8-tile hop and follows it rather than replanning a full
    A* every step. Recomputing it per turn was the raider's dominant search cost on
    a large map (docs/observations.md, Performance).
    """
    pos = ct.get_position()
    wp = bot._raidWaypoint
    if (wp is None
            or bot._raidWaypointSite != (site.x, site.y)
            or (pos.x, pos.y) == (wp.x, wp.y)
            or manhattan(pos, wp) > _CHUNK + _WAYPOINT_SLACK
            or ph.isImpassable(ct, bot.hivemind, wp)):
        wp = _waypoint(bot, ct, site)
        bot._raidWaypoint = wp
        bot._raidWaypointSite = (site.x, site.y)
    if wp is None:
        return False

    def atWaypoint(p: Position) -> bool:
        return p.x == wp.x and p.y == wp.y

    seq = bot.noRouteSeq
    stuck = walkOrRetarget(bot, ct, atWaypoint, wp, ("waypoint", wp.x, wp.y))
    if stuck or bot.noRouteSeq != seq:
        # No route to the committed waypoint from here — it can sit walled off after
        # a launcher fling drops us outside a wall it's behind, and neither the drift
        # nor the ledger-based impassable check catches that. Drop it so the next
        # attempt picks a fresh staging tile instead of re-grinding the same dead
        # search at the same unreachable tile. (`stuck` = patience spent, which
        # already cleared the hold; the counter catches it mid-patience.)
        #
        # The counter and not holdReason for the mid-patience half: the reason is
        # sticky across turns and goals, so a stale one threw away a perfectly good
        # waypoint every turn — and the commitment to a waypoint is precisely what
        # keeps this walk from re-planning a full A* on every step.
        bot._raidWaypoint = None
    return stuck


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
