"""Builder economy role. Orchestrator only — mechanics live in the modules it calls."""

from __future__ import annotations

from fcode import Controller, EntityType, Position

import debug
import heal
import launcher
import pathfindingHelpers as ph
from movement import exploreStep, walkTo
from patience import BLOCKED_WAIT_TURNS, clearHold, holdTask, stallCheck, stallForget, waitTi
import store
from relays import beltCorrect, hijackRelay, relayAbort, relayToHome, traceToCore

# How many of the nearest ore nodes builders fan out across. Deterministic off
# my_index, so two builders with different indices rarely pick the same node.
# Not a claim — see observations.md.
ORE_SPREAD = 4

# The spread only fans across nodes that are near-*equidistant* — this is how much
# farther than the nearest a node may be and still count as a peer to spread onto,
# in the detour-aware step units `distTo` returns. A blind `index % ORE_SPREAD`
# ignored distance and walked a builder 20-30 tiles past a free node because its
# index landed on a farther rank — and once early builders became the guardian or
# raiders, the surviving indices stopped covering the nearest ranks at all, so easy
# close nodes went unworked. Bounding the fan-out to a distance band takes the
# nearest node whenever the next one is meaningfully farther, while still splitting a
# genuine spawn cluster. Small: a cluster is tight; anything past it is a real trek.
SPREAD_BAND = 5

# Manhattan is blind to walls: a node behind one reads as near as an open node at
# the same beeline distance, but the walk around the wall is longer. Add an estimate
# of that detour back — each wall tile on the straight line to a node costs a builder
# roughly this many extra steps to round. A cheap proxy for a true path cost (too
# expensive to run per node inside the 10ms budget); it only has to rank walled-off
# nodes below clear ones, not price them exactly. Tuned on mirror_gauntlet; 0 restores
# the old pure-Manhattan behaviour.
DETOUR_PER_WALL = 3

# A remembered ore sighting older than this is worth walking back to verify. The
# harvester we think is on it may be long dead: nothing refreshes a tile once a
# builder has wandered out of vision, so without this a node is written off for
# the rest of the match on one stale glimpse.
ORE_RECHECK_TURNS = 120

# How long a builder leaves alone a node it couldn't reach. Long enough not to
# thrash, short enough that a transient blocker doesn't cost us the node.
ORE_SKIP_TURNS = 150

# How far from our core we will chase an enemy harvester to tap it. Past this the
# belt run home costs more than the ore returns — so the thing being bounded is belt
# count, which is 4-connected, hence Manhattan and measured from the core's *edge*.
# This is a strictly tighter leash than the Chebyshev it replaced, tightest at 45°
# (a node at (12, 12) used to read 12 and now reads 24, which is what it actually
# costs). 12 has not been re-tuned since the metric changed — first place to look.
HIJACK_MAX_MANHATTAN = 12

# Tiles of head start a tappable enemy harvester gets over free ore when picking a
# target. It saves the (scaling) harvester cost and denies them the node, so it is
# worth a short detour — but not an expedition.
HIJACK_BONUS = 4

# An ore node with an enemy barrier on it is claimable — the builder walks up and
# grinds the barrier off (buildHarvester), then harvests — but only when it's this
# close to our core. Grinding a full barrier is ~10 shots of Ti, a real commitment,
# so we only make it for a node near home; a far barriered node is still skipped in
# favour of open ore. Tighter than HIJACK_MAX_MANHATTAN on purpose: tapping a
# harvester is one action, clearing a barrier is a multi-turn grind.
ORE_BARRIER_CLEAR_RADIUS = 7

# Rank cost added to a barriered ore node's distance, ~ the Ti/turns to grind the
# barrier down. Keeps a comparably-near clear node preferred: we only route to the
# barriered one when it's clearly the best remaining option nearby.
ORE_BARRIER_CLEAR_PENALTY = 20

# Grinding an ore barrier that hasn't hit a new low HP for this many turns is being
# repaired as fast as we cut — a losing Ti trade. Give the node up (oreSkip) and
# move on. Long enough to break a barrier we're genuinely winning (a full barrier
# is ~10 unopposed shots), short enough to cap the wasted Ti.
ORE_BARRIER_STALL_TURNS = 11

# Manhattan radius from our core within which an idle builder will clear an enemy
# barrier that has severed one of our supply lines and re-lay the belt. Bounded to
# "near the core" on purpose: this is a defensive bandage for the enemy's
# core-siege (mirror of raidBot._siege), not a map-wide relay-repair system.
CORE_DEFENSE_RADIUS = 8

# A builder only ever moves one tile per turn, so a larger jump is an external
# displacement — a launcher throw. Bounded above by the engine throw range
# (r**2 <= 26 -> Chebyshev 5) plus one intervening self-move, so a stale
# last-position (e.g. after a guardian stint) reads as "too far" and is ignored
# rather than mistaken for a fling. See _handleFling.
FLING_JUMP_MIN = 2
FLING_JUMP_MAX = 6

# Net-progress backstop for the approach phase (seekOre). walkTo clears the hold
# on every successful step, so a builder that keeps being shoved back — a launcher
# that flings it *toward* the goal short of it, a swap-deadlock that steps it back
# and forth, any knockback _handleFling's single-jump test misses — reports
# progress each turn yet never actually closes on its node, and the ordinary
# blocked-patience give-up in seekOre never fires. So track the closest we have
# ever been to the current node; if that best doesn't improve for this many turns,
# concede the node (skip + re-pick) rather than churn on it forever. Generous on
# purpose: a legitimate detour around a wall can plateau the manhattan distance for
# a good stretch, and this must never fire on one. Approach-only — a relay
# legitimately walks *away* from its harvester as it lays belt home, so distance to
# the source is meaningless there and the tracker never runs mid-relay. Tunable.
STALL_NO_PROGRESS_TURNS = 30

# How long reconnectOrphan waits between attempts on a harvester it couldn't reroute
# home (a launcher still covers every approach, or no route exists yet). Bounds the
# retry to one relay-plan every this-many turns rather than one every turn — the
# re-search-every-turn cost the pressCore/TLE work warns against — while still giving
# the blocking launcher time to be answered between tries. Tunable.
RECONNECT_RETRY_TURNS = 30


def runEcoBot(bot, ct: Controller) -> None:
    # An eco-builder that has delivered a harvester grabs guardian duty if the
    # role is open or its holder has died. A no-op otherwise. Function-level
    # import so ecoBot keeps no module-level dependency on guardianBot (which
    # imports back into ecoBot for its quiet-economy step). Runs even mid-relay:
    # claiming only writes the store slot; this builder finishes its current work
    # as an eco-bot this turn and main routes it to runGuardian from the next.
    import guardianBot
    guardianBot.claimIfOpen(bot, ct)

    # Concede a node the enemy just flung us off, before re-driving the relay:
    # otherwise we march the harvester end straight back into the launch lane and
    # get thrown again. Pure state cleanup — it never consumes the turn, so the
    # ladder below re-picks and walks a fresh node this same turn.
    _handleFling(bot, ct)

    if bot.inRelay and relayToHome(bot, ct):
        return

    # Seize before build: on an enemy-harvester tile `can_build_harvester` is
    # False, and buildHarvester answers that by clearing oreTarget — which used
    # to leave seizeHarvester reading None and bailing, seekOre re-picking the
    # same tile, and the builder idling beside it forever with a cold action
    # cooldown. seizeHarvester is guarded by _enemyHarvester, so it is a no-op
    # on every ordinary target and costs the build path nothing.
    if seizeHarvester(bot, ct):
        return

    if buildHarvester(bot, ct):
        return

    # Only once nothing above claimed the turn — relaying, seizing, and
    # building are all commitments in progress and stay protected. Heal
    # ahead of seekOre, not after: seekOre almost always returns True (walk
    # or hold), so a check placed later would rarely get a turn to run.
    if heal.healNearby(bot, ct, heal.ECO_HEAL_TYPES):
        return

    # Home defence before repair or new ore: shoot an attacker closing on the core
    # rather than only patch the belt it cut. Function-level import — a module-level
    # `import guns` cycles ecoBot -> guns -> respawn -> ecoBot (respawn imports
    # chooseOre from here). Note emplace.py does NOT fix this one: it broke the
    # guns<->raid edges by moving *emplacement mechanism* out, but this edge is
    # `guns -> respawn` for MAX_BUILDERS in topUpAmmo, which is unrelated. Closing
    # it means moving that constant somewhere neutral, or the ore.py split that
    # takes chooseOre out of a role file (roadmap.md, 2026-08-15 item 3).
    import guns
    if guns.defendCore(bot, ct):
        return

    # Restore a supply line the enemy cut near our core (barrier over a belt tile)
    # before seeking new ore — a severed line loses a whole harvester's output,
    # worth more than another node. Below build so it never interrupts one.
    if repairCoreBelts(bot, ct):
        return

    # Periodic maintenance: every 2nd delivery, walk the just-connected line back to
    # the core and confirm it's intact (catches a belt destroyed out of vision — the
    # ledger never expires a ghost). Below the urgent work above so a threat or a
    # core-cut pre-empts it, but above reconnectOrphan (a break found hands off there
    # this same turn) and seekOre (so it runs before the builder leaves for new ore).
    if verifyRelay(bot, ct):
        return

    # Re-relay one of our own harvesters whose line was abandoned mid-build (a
    # launcher fling, a death) and now feeds nothing — nobody else ever returns to
    # it (rankOre skips own-harvester tiles, repairCoreBelts is barrier-only).
    if reconnectOrphan(bot, ct):
        return

    if seekOre(bot, ct):
        return

    exploreStep(bot, ct)


# ---------------------------------------------------------------------------
# Fling recovery — concede a node an enemy launcher just threw us off
# ---------------------------------------------------------------------------


def _handleFling(bot, ct: Controller) -> None:
    """Concede the current node if an enemy launcher just flung us off it.

    A launcher throw is the only way a builder moves more than one tile in a
    turn. Our own core launcher ferries builders *toward* their ore, so the
    direction of the jump — closer to the goal (friendly ferry) vs farther
    (hostile fling) — is what tells the two apart. On a hostile fling we skip
    the node (bounded by ORE_SKIP_TURNS) and drop any in-progress relay, so
    seekOre picks a different node instead of walking straight back into the
    launch lane to be thrown again.

    State cleanup only — it never consumes the turn; the runEcoBot ladder below
    it re-picks and walks a fresh node the same turn.
    """
    cur = ct.get_position()
    last = bot._ecoLastPos
    bot._ecoLastPos = cur
    if last is None:
        return
    cheb = max(abs(cur.x - last.x), abs(cur.y - last.y))
    if not (FLING_JUMP_MIN <= cheb <= FLING_JUMP_MAX):
        return  # a normal 1-tile move, or a stale last-position — not a fling
    goal = bot.relayFrom if bot.inRelay else bot.oreTarget
    if goal is None:
        return  # displaced with no committed task to strand
    md_last = abs(last.x - goal.x) + abs(last.y - goal.y)
    md_cur = abs(cur.x - goal.x) + abs(cur.y - goal.y)
    if md_cur <= md_last:
        return  # thrown *toward* the goal — our own ferry launcher, keep going

    # Thrown away from the goal by a hostile launcher: concede the node.
    _skipOre(bot, goal)
    if bot.inRelay:
        # Remember the harvester we were relaying (goal == relayFrom here) so we come
        # back to reconnect it once the launcher is gone — conceding alone orphans it
        # (nobody re-targets an own harvester). Delay the first retry so we don't walk
        # straight back into a launcher that is still live.
        bot._orphanHarvester = goal
        bot._orphanNextTry = ct.get_current_round() + RECONNECT_RETRY_TURNS
        _concedeRelay(bot, ct)
    bot.oreTarget = None
    clearHold(bot)
    if debug.ENABLED:
        debug.log(
            "fling",
            f"flung {last}->{cur} off goal {goal}; conceding "
            f"rnd={ct.get_current_round()}",
        )


def _concedeRelay(bot, ct: Controller) -> None:
    """Abandon a relay the enemy flung us off, without marking a delivery.

    The harvester already built is orphaned: accepted, since we cannot finish its
    belt while the launcher keeps throwing us off the lane, and an oscillating
    builder delivers nothing anyway. Delegates to relays.relayAbort — which is
    deliberately not relayDone (that sets _harvesterDelivered / guardian
    eligibility, and a flung relay did not deliver) — so the delivery-free teardown
    lives in one place, in the module that owns the relay state. _handleFling has
    already recorded the orphan (with its launcher-avoidance cooldown) before this
    call; relayAbort re-records the same own harvester, which is idempotent here.
    """
    relayAbort(bot, ct)


# ---------------------------------------------------------------------------
# Core-line repair — clear enemy barriers that sever our supply near the core
# ---------------------------------------------------------------------------


def _hasFeeder(bot, ct: Controller, tile: Position, network_side: Position) -> bool:
    """Is `tile` fed by one of ours that would resume delivering once it carries a
    belt into `network_side`.

    A feeder is an ally harvester (delivers to any adjacent building), or an ally
    belt whose facing points at `tile` — which, because its output currently runs
    into `tile` and `tile` is not a belt, is by definition disconnected from the
    core. Requiring a real feeder is what stops us grinding a barrier that isn't
    actually cutting anything.
    """
    for f in ph.orthogonalTiles(tile):
        if (f.x, f.y) == (network_side.x, network_side.y):
            continue
        t = ph.readTile(ct, bot.hivemind, f)
        if t.enemy:
            continue
        if t.etype == EntityType.HARVESTER:
            return True
        if (
            t.etype in ph.BELT_TYPES
            and t.facing is not None
            and t.facing == ph.beltFacing(f, tile)
        ):
            return True
    return False


def _grindBarrier(bot, ct: Controller, tile: Position, waitReason: str, fireReason: str):
    """One turn of grinding an enemy barrier down (2 Ti / 2 dmg a shot).

    Returns `(fired, keepTask)`:

    * `fired` — the shot actually went off this turn. Only then may a caller
      commit state off the back of it (e.g. latch a multi-turn grind target); a
      turn we merely held must not.
    * `keepTask` — what the caller should return: True to stay on the task
      (we fired, or we're still inside the patience budget), False once patience
      is spent and the caller may give up. This is `holdTask`'s own contract,
      passed straight through.

    Includes the cooldown branch even where a call site already returns early on a
    busy cooldown: harmless there, and it stops the fire/hold ladder from only
    working from one specific place.
    """
    if ct.get_action_cooldown() != 0:
        return False, holdTask(bot, waitReason, BLOCKED_WAIT_TURNS)
    if not ct.can_fire(tile):
        return False, holdTask(bot, fireReason, BLOCKED_WAIT_TURNS)
    ct.fire(tile)
    return True, True


def _nearestBarrier(bot, ct: Controller, ctx):
    """Nearest (barrier_tile, network_side) near the core where the enemy has cut a
    line: an enemy barrier adjacent to a still-connected network member, with a
    disconnected feeder on another side. None if no line is barriered.

    Only ever an enemy *barrier* — never a plain empty gap. A gap near the core is
    indistinguishable from a normal relay still being built (relays lay from the
    harvester end first, so the core end is the last gap), and treating those as
    severances made idle builders fight live relays and churn belts. An enemy
    barrier is unambiguous: a healthy line never has one on it.
    """
    core = bot.corePos
    pos = ct.get_position()
    best = None
    bestD = None
    for (nx, ny) in ctx.network:
        N = Position(nx, ny)
        if ph.manhattanToCore(N, core) > CORE_DEFENSE_RADIUS:
            continue
        for T in ph.orthogonalTiles(N):
            if (T.x, T.y) in ctx.network or not ph.inBounds(ct, T):
                continue
            if ph.manhattanToCore(T, core) > CORE_DEFENSE_RADIUS:
                continue
            t = ph.readTile(ct, bot.hivemind, T)
            if not (t.etype == EntityType.BARRIER and t.enemy):
                continue
            if not _hasFeeder(bot, ct, T, N):
                continue
            d = abs(pos.x - T.x) + abs(pos.y - T.y)
            if bestD is None or d < bestD:
                best, bestD = (T, N), d
    return best


def _clearRepair(bot) -> None:
    bot._repairTile = None
    bot._repairFace = None


def repairCoreBelts(bot, ct: Controller) -> bool:
    """Clear an enemy barrier severing a supply line near our core, then re-lay the
    belt back into the network. True while committed to a repair (walking, grinding
    or re-laying); False when there's no barriered line to fix.

    Commits to one barrier via `bot._repairTile` and sees it through in
    `_finishRepair` — so the belt goes back the instant the barrier falls, and the
    now-empty tile isn't re-scanned as a fresh target."""
    if bot.corePos is None:
        return False

    if bot._repairTile is not None:
        return _finishRepair(bot, ct)

    ctx = ph.costContext(bot, ct, "relay")
    found = _nearestBarrier(bot, ct, ctx)
    if found is None:
        return False
    tile, network_side = found

    pos = ct.get_position()
    if abs(pos.x - tile.x) + abs(pos.y - tile.y) != 1:
        def beside(p: Position) -> bool:
            return abs(p.x - tile.x) + abs(p.y - tile.y) == 1

        if walkTo(bot, ct, beside, tile, ("repair", tile.x, tile.y)):
            return True
        return holdTask(bot, "repairApproach", BLOCKED_WAIT_TURNS)

    fired, keepTask = _grindBarrier(bot, ct, tile, "repairWait", "repairFire")
    if not fired:
        return keepTask

    bot._repairTile = tile          # commit: grind it down, then re-lay the belt
    bot._repairFace = network_side
    if debug.ENABLED:
        debug.log("repair", f"grind barrier {tile} rnd={ct.get_current_round()}")
    return True


def _finishRepair(bot, ct: Controller) -> bool:
    """Drive a committed repair: keep grinding the barrier, then lay the belt back
    into the network once it's gone. Clears the commitment on completion, on being
    displaced with no way back, or if the tile becomes something we don't handle."""
    tile, network_side = bot._repairTile, bot._repairFace
    if not ph.inBounds(ct, tile):
        _clearRepair(bot)
        return False

    pos = ct.get_position()
    if abs(pos.x - tile.x) + abs(pos.y - tile.y) != 1:
        def beside(p: Position) -> bool:
            return abs(p.x - tile.x) + abs(p.y - tile.y) == 1

        if walkTo(bot, ct, beside, tile, ("repair", tile.x, tile.y)):
            return True
        _clearRepair(bot)  # shoved off and can't get back — drop it
        return False

    t = ph.readTile(ct, bot.hivemind, tile)

    if t.etype == EntityType.BARRIER and t.enemy:
        return _grindBarrier(bot, ct, tile, "repairWait", "repairFire")[1]

    if t.etype is None and not t.wall and not t.ore:
        facing = ph.beltFacing(tile, network_side)
        if ct.get_action_cooldown() != 0:
            return holdTask(bot, "repairWait", BLOCKED_WAIT_TURNS)
        if facing is not None and ct.can_build_conveyor(tile, facing):
            ct.build_conveyor(tile, facing)
            ph.invalidateTile(bot.hivemind, tile)
            _clearRepair(bot)
            if debug.ENABLED:
                debug.log(
                    "repair",
                    f"restore belt {tile}->{network_side} rnd={ct.get_current_round()}",
                )
            return True
        return holdTask(bot, "repairBuild", BLOCKED_WAIT_TURNS)

    _clearRepair(bot)  # already filled (ally belt), or the enemy replaced it — done
    return False


# ---------------------------------------------------------------------------
# Relay verification — every 2nd delivery, walk the line to the core to catch a
# belt destroyed out of vision (the ledger never expires a ghost). Detect only:
# the fix is delegated to reconnectOrphan.
# ---------------------------------------------------------------------------


def _clearVerify(bot) -> None:
    bot._verifyHarvester = None
    bot._verifyLine = None
    bot._verifyAnchor = None
    bot._verifyIdx = 0


def _handoffVerify(bot, ct: Controller) -> None:
    """A break was found on the walk: hand the harvester to reconnectOrphan, which
    owns the actual repair (strict re-relay, grab-ring avoidance, obstruction
    grinding, delivery accounting). `_orphanNextTry = now` so it fires this same turn
    — relayAbort deliberately leaves that field untouched, and a stale future value
    from an earlier fling would otherwise defer the fix for the cooldown."""
    bot._orphanHarvester = bot._verifyHarvester
    bot._orphanNextTry = ct.get_current_round()
    _clearVerify(bot)


def verifyRelay(bot, ct: Controller) -> bool:
    """Walk the line of a just-connected harvester back to the core, confirming each
    belt still exists. This is the one way to catch a belt destroyed out of vision:
    the ledger keeps a ghost belt forever, so nothing else re-checks a completed line
    — but walking the line refreshes this unit's vision tile-by-tile and exposes the
    gap (see relays.traceToCore). Detect only; the first break is handed to
    reconnectOrphan (`_handoffVerify`). Best-effort: abandons on a blocked walk or a
    vanished harvester rather than committing turns to it.

    Armed by relays.relayDone (`_verifyHarvester`). Returns True only while actively
    walking, so it owns the turn then; False the moment it finishes, hands off, or
    gives up, letting the builder resume normal work at once."""
    h = bot._verifyHarvester
    if h is None:
        return False

    # Harvester gone or taken by the enemy — nothing left to verify.
    th = ph.readTile(ct, bot.hivemind, h)
    if not (th.etype == EntityType.HARVESTER and not th.enemy):
        _clearVerify(bot)
        return False

    # First turn: trace the (possibly ghost) line to the core. A line we can't even
    # trace — the belt beside the harvester is already gone, or the chain never
    # reaches home — is itself a break; hand it straight to reconnect.
    if bot._verifyLine is None:
        traced = traceToCore(bot, ct, h)
        if traced is None:
            _handoffVerify(bot, ct)
            return False
        bot._verifyLine, bot._verifyAnchor = traced
        belts = bot._verifyLine
        # Start at whichever end is nearer and walk toward the other, so a delivery
        # that finished at the core end (a laid line) and one that finished at the
        # harvester end (planRelay -> CONNECTED, zero belts laid) both traverse once
        # without backtracking.
        pos = ct.get_position()
        d_harv = abs(pos.x - belts[0].x) + abs(pos.y - belts[0].y)
        d_core = abs(pos.x - belts[-1].x) + abs(pos.y - belts[-1].y)
        near_harv = d_harv <= d_core
        bot._verifyIdx = 0 if near_harv else len(belts) - 1
        bot._verifyStep = 1 if near_harv else -1
        # Bounded backstop: a walk that can't finish in this many rounds is abandoned.
        # A launcher flinging us back onto the line loops walkTo forever otherwise
        # (_handleFling doesn't fire mid-verify — inRelay is False). Generous; the
        # vision-skip means a healthy line finishes in ~len/vision walked turns.
        bot._verifyDeadline = ct.get_current_round() + 2 * len(belts) + 20

    belts = bot._verifyLine
    anchor = bot._verifyAnchor

    # Consume every tile already in vision that reads intact; stop at the first tile
    # out of vision (walk to it) or the first that's broken (hand off). Walking off
    # either end (idx out of range) means the whole line checked out.
    while 0 <= bot._verifyIdx < len(belts):
        tile = belts[bot._verifyIdx]
        nxt = belts[bot._verifyIdx + 1] if bot._verifyIdx + 1 < len(belts) else anchor
        if not ct.is_in_vision(tile):
            break
        if beltCorrect(bot, ct, tile, nxt):
            bot._verifyIdx += bot._verifyStep
            continue
        _handoffVerify(bot, ct)  # a ghost revealed as a real gap
        return False

    if not (0 <= bot._verifyIdx < len(belts)):
        _clearVerify(bot)  # whole line confirmed intact
        return False

    # Past the deadline (a persistent launcher, a wall-locked approach): abandon.
    # Maintenance is never worth pinning a builder off the economy.
    if ct.get_current_round() > bot._verifyDeadline:
        _clearVerify(bot)
        return False

    # Walk beside the first not-yet-seen tile; standing adjacent puts it (and a vision
    # radius of neighbours) in view, so the loop above skips several tiles per tile
    # walked. walkTo returns True while it still has route-patience and False once it
    # has genuinely given up — abandon then rather than idle beside an unreachable tile.
    target = belts[bot._verifyIdx]

    def beside(p: Position) -> bool:
        return abs(p.x - target.x) + abs(p.y - target.y) <= 1

    if walkTo(bot, ct, beside, target, ("verify", target.x, target.y)):
        return True
    _clearVerify(bot)
    return False


# ---------------------------------------------------------------------------
# Orphan reconnection — re-relay an own harvester whose line was abandoned
# ---------------------------------------------------------------------------


def _launcherGrabTiles(bot, ct: Controller) -> set[tuple[int, int]]:
    """The 8-neighbourhoods of every visible enemy launcher — the tiles from which it
    grabs and flings a builder. A reconnection relay hard-avoids these so its new line
    routes around a launcher still standing, rather than back into the same fling.
    Threat-*cost* alone (makeCostFn) isn't enough: it prices the grab ring dear but
    still routes through it when that's the only way to the core."""
    me = ct.get_team()
    out: set[tuple[int, int]] = set()
    for bid in ct.get_nearby_buildings():
        if ct.get_team(bid) == me:
            continue
        if ct.get_entity_type(bid) != EntityType.LAUNCHER:
            continue
        for n in ph.adjacentTiles(ct.get_position(bid)):
            out.add((n.x, n.y))
    return out


def reconnectOrphan(bot, ct: Controller) -> bool:
    """Re-relay the harvester this builder was flung off before its line reached the
    core. Conceding a flung relay (via `_handleFling`) leaves the harvester feeding
    nothing, and nobody else returns to it: `rankOre` skips own-harvester tiles, and
    `repairCoreBelts` only reconnects a line an enemy *barrier* cut, not one that was
    never finished. So the builder that built it remembers it and comes back.

    Retried on a cooldown (`RECONNECT_RETRY_TURNS`), not every turn, so a harvester
    still walled off by a live launcher isn't re-planned every turn (the
    re-search-every-turn cost). Each attempt hard-avoids any launcher grab ring in
    view, so it either routes around the fling lane or fails cleanly and waits — the
    common case being the launcher gets answered and destroyed, after which the next
    attempt routes home normally.
    """
    orphan = bot._orphanHarvester
    if orphan is None or bot.inRelay or bot.corePos is None:
        return False
    if ct.get_current_round() < bot._orphanNextTry:
        return False   # cooling off between attempts

    t = ph.readTile(ct, bot.hivemind, orphan)
    if not (t.etype == EntityType.HARVESTER and not t.enemy):
        bot._orphanHarvester = None   # destroyed or taken by the enemy — forget it
        return False

    ctx = ph.costContext(bot, ct, "relay")
    if any(ctx.isNetworkMember(n) for n in ph.orthogonalTiles(orphan)):
        bot._orphanHarvester = None   # reconnected by other means — nothing owed
        return False

    # Re-relay it, strict: merge only onto a proven network member. Without this
    # planRelay reads the orphan's own dead-ended partial line as intact from afar
    # (_chainVisiblyIntact trusts an out-of-vision chain) and reports it connected,
    # laying nothing. Any enemy launcher grab ring *in view* is hard-avoided so the
    # line routes around a live one; a launcher out of vision isn't in the set, so a
    # still-live one can re-fling us — bounded, since that re-records the orphan
    # (_handleFling) and we retry after the cooldown.
    bot.relayFrom = orphan
    bot.inRelay = True
    bot._strictRelay = True
    bot.oreTarget = None            # commit — as buildHarvester/seizeHarvester do
    bot.relayAvoid |= _launcherGrabTiles(bot, ct)
    clearHold(bot)
    # Cooldown set before the attempt so it holds whether the line commits or fails.
    # Deliberately do NOT clear _orphanHarvester on commit: relayToHome can still
    # abort this line later without delivering (a contested tile, a collapsed
    # re-plan), and those aren't flings, so _handleFling won't re-record it —
    # clearing here would silently revert to the original orphan bug. The pre-check
    # above clears it once the harvester is observed connected; the inRelay gate at
    # the top keeps us from interfering while the line runs.
    bot._orphanNextTry = ct.get_current_round() + RECONNECT_RETRY_TURNS
    acted = relayToHome(bot, ct)
    if bot.inRelay and debug.ENABLED:
        debug.log("reconnect", f"re-relaying orphan {orphan} rnd={ct.get_current_round()}")
    return acted or bot.inRelay


def buildHarvester(bot, ct: Controller) -> bool:
    """Plant a harvester on the claimed ore, then commit to relaying it home.

    If the node is blocked when we arrive, deal with the blocker instead of just
    dropping the claim: tap an enemy harvester sitting on it, or grind down an
    enemy barrier denying it and build once the tile is clear.
    """
    ore = bot.oreTarget
    if ore is None or ct.get_action_cooldown() != 0:
        return False

    pos = ct.get_position()
    if abs(pos.x - ore.x) + abs(pos.y - ore.y) != 1:
        return False  # builds are orthogonal-only; diagonal isn't close enough

    # Blocked by an enemy? Handle it before the harvester-Ti gate: an enemy
    # harvester wants tapping (not building), and a barrier is far cheaper to
    # grind than the harvester we'd otherwise be saving up for.
    t = ph.readTile(ct, bot.hivemind, ore)
    if t.etype == EntityType.HARVESTER and t.enemy:
        # seizeHarvester normally catches this a step earlier in runEcoBot; kept
        # here so the build path itself never just abandons a tappable node.
        bot.oreTarget = None
        return hijackRelay(bot, ct, ore)
    if t.etype == EntityType.BARRIER and t.enemy:
        # Only a turn we're actually about to shoot counts as an engaged look —
        # patience.stallCheck wants that, and a builder merely holding (broke,
        # off-cooldown check aside) shouldn't advance the stall clock toward
        # giving up a barrier it never got to contest.
        if ct.can_fire(ore) and _barrierRepaired(bot, ct, ore):
            # Being out-repaired: the barrier's HP has stalled, so the enemy is
            # mending it as fast as we grind. Give the node up (oreSkip holds it
            # off for ORE_SKIP_TURNS, then it's re-probed) and re-pick rather than
            # bleed Ti.
            _skipOre(bot, ore)
            stallForget(bot._oreGrindLow, (ore.x, ore.y))
            bot.oreTarget = None
            if debug.ENABLED:
                debug.log("ore", f"barrier repaired, skip ore {ore} "
                                 f"rnd={ct.get_current_round()}")
            return False
        # Grind it down. Keep the claim: once it falls, the tile reads clear next
        # pass and we build as normal.
        fired, keepTask = _grindBarrier(
            bot, ct, ore, "clearOreBarrier", "clearOreBarrier"
        )
        if fired and debug.ENABLED:
            debug.log("ore", f"clear barrier on ore {ore} rnd={ct.get_current_round()}")
        return keepTask

    # No enemy barrier on the node now (cleared, or never there): drop any grind
    # memory so a barrier that later reappears here is judged fresh, not against a
    # stale low from a previous grind (which would skip it immediately).
    stallForget(bot._oreGrindLow, (ore.x, ore.y))

    # Plant only from a tile that can carry a belt home. Building from the far
    # (ore/pocket) side orphans the harvester — no belt can reach it — and can seal
    # the builder in behind its own harvesters, which are impassable
    # (ph.coreBeltReach). seekOre walks us to a core-reachable neighbour; if we
    # somehow arrived on the wrong side, defer to it rather than commit here.
    if (pos.x, pos.y) not in ph.coreBeltReach(bot, ct):
        return False

    # Don't sit in a gun's firing line waiting for titanium to build one harvester —
    # concede a threatened tile we have already been hit on (hp below max) and re-pick;
    # oreSkip holds it off so we don't march straight back, and seekOre's seed pick
    # prefers a safe tile when one exists. We still build outright when we can afford
    # it now (below): that is one turn of exposure, not an open-ended vigil.
    if (ct.get_global_resources() < ct.get_harvester_cost()
            and (pos.x, pos.y) in ph.costContext(bot, ct, "walk").threat
            and ct.get_hp() < ct.get_max_hp()):
        _skipOre(bot, ore)
        bot.oreTarget = None
        if debug.ENABLED:
            debug.log("ore", f"under fire at {pos} building {ore}; conceding "
                             f"rnd={ct.get_current_round()}")
        return False

    if waitTi(bot, ct, ct.get_harvester_cost()):
        return True
    if ct.get_global_resources() < ct.get_harvester_cost():
        return True  # broke, not blocked — keep the claim rather than re-picking

    if not ct.can_build_harvester(ore):
        bot.oreTarget = None
        return False

    ct.build_harvester(ore)
    store.put(ct, store.HARVESTERS, ct.read_store(store.HARVESTERS) + 1)

    bot.relayFrom = ore
    bot.inRelay = True
    bot.oreTarget = None
    clearHold(bot)
    return True


def seizeHarvester(bot, ct: Controller) -> bool:
    """We've walked up to an enemy harvester — tap it rather than build our own."""
    ore = bot.oreTarget
    if ore is None or not _enemyHarvester(bot, ct, ore):
        return False

    pos = ct.get_position()
    if abs(pos.x - ore.x) + abs(pos.y - ore.y) != 1:
        return False  # output is cardinal-only, so the belt must be cardinal too

    bot.oreTarget = None
    return hijackRelay(bot, ct, ore)


def seekOre(bot, ct: Controller) -> bool:
    if bot.oreTarget is None or not _oreWorthGoing(bot, ct, bot.oreTarget):
        bot.oreTarget = pickOre(bot, ct)
    ore = bot.oreTarget
    if ore is None:
        return False

    pos = ct.get_position()

    # For a free node we build ourselves, approach the specific core-reachable
    # neighbour that becomes the relay seed — never the ore/pocket side, which is how
    # a builder seals itself in behind its own harvesters. Pick the seed nearest the
    # core (stable as the builder moves, and the natural belt start) and, below, walk
    # straight at it so A* stays directed — a bare "any adjacent" goal with the node
    # as the heuristic target makes A* search the whole ring around it. Enemy
    # harvesters (tapped) and barriers (grinded) keep the plain any-adjacent
    # approach: their belt geometry is the relay/hijack planner's job, not the walk's.
    seedGoal = None
    onSeed = False
    if ph.readTile(ct, bot.hivemind, ore).etype is None:
        seeds = _relaySeeds(bot, ct, ore)
        if not seeds:
            # Sealed into a pocket since it was picked (a fresh wall/harvester). Concede.
            _skipOre(bot, ore)
            bot.oreTarget = None
            _clearApproach(bot)
            return False
        onSeed = any(pos.x == n.x and pos.y == n.y for n in seeds)
        # Prefer a seed out of an enemy gun's firing line first — parking the build on
        # a shot tile is what leaves a waiting builder bleeding (soft: it still falls
        # back to a threatened seed when that is the only one). Then nearest to the
        # core (the natural belt start), then nearest to us — a second distance key
        # that mirrors with the map, so the pick is symmetric where the bare N,E,S,W
        # order would tie two lateral seeds and favour one side.
        threat = ph.costContext(bot, ct, "walk").threat
        seedGoal = min(seeds, key=lambda n: (
            (n.x, n.y) in threat,
            ph.manhattanToCore(n, bot.corePos),
            abs(n.x - pos.x) + abs(n.y - pos.y),
        ))

    # Already in position: standing on a relay seed for free ore — ready to build from
    # the relay side — or simply beside an enemy target. Hold it: walkTo reports
    # arrival as False, and falling through would send the builder off exploring on
    # the exact turn it got where it was going.
    if seedGoal is not None:
        if onSeed:
            return True
    elif abs(pos.x - ore.x) + abs(pos.y - ore.y) == 1:
        return True

    # Beside the core launcher with the ore still a walk away? Get flung toward
    # it. Held turns fall through to walkTo once patience runs out.
    if launcher.requestLaunch(bot, ct, ore):
        return True

    # Net-progress backstop: we are reporting walk "progress" every turn but not
    # actually getting closer to the node (shoved back, oscillating). Give it up
    # before spending the turn walking, so we re-pick next turn instead of churning.
    if _approachStalled(bot, ct, ore):
        _skipOre(bot, ore)
        bot.oreTarget = None
        _clearApproach(bot)
        if debug.ENABLED:
            debug.log(
                "fling",
                f"approach stalled on {ore} (no gain in "
                f"{STALL_NO_PROGRESS_TURNS} turns); conceding "
                f"rnd={ct.get_current_round()}",
            )
        return False

    if seedGoal is not None:
        def atSeed(p: Position) -> bool:
            return p.x == seedGoal.x and p.y == seedGoal.y

        # Seed coords in the goal key: if fog resolves a nearer seed, the cached path
        # to the old one is dropped and replanned, not blindly followed to it.
        if walkTo(bot, ct, atSeed, seedGoal,
                  ("ore", ore.x, ore.y, seedGoal.x, seedGoal.y)):
            return True
    else:
        def beside(p: Position) -> bool:
            return abs(p.x - ore.x) + abs(p.y - ore.y) == 1

        if walkTo(bot, ct, beside, ore, ("ore", ore.x, ore.y)):
            return True

    # Out of patience getting there. Without this a builder commits to one
    # unreachable node for the rest of the match — four of them sat on the same
    # enemy harvester for 400 turns.
    _skipOre(bot, ore)
    bot.oreTarget = None
    _clearApproach(bot)
    return False


def _approachStalled(bot, ct: Controller, ore: Position) -> bool:
    """Tick the net-progress tracker for the walk toward `ore`; True once the
    closest we have come to it hasn't improved for STALL_NO_PROGRESS_TURNS turns.

    Keyed on the node: a new target restarts the tracker. Resets on every new best
    (minimum) manhattan distance, which any real approach keeps achieving as it
    nears the node — so only a builder that genuinely stops closing the gap ages
    out. See STALL_NO_PROGRESS_TURNS for why this exists and why it is approach-only.
    """
    pos = ct.get_position()
    d = abs(pos.x - ore.x) + abs(pos.y - ore.y)
    key = (ore.x, ore.y)
    if bot._ecoStallGoal != key:
        bot._ecoStallGoal = key
        bot._ecoBestDist = d
        bot._ecoStallTurns = 0
        return False
    if d < bot._ecoBestDist:
        bot._ecoBestDist = d
        bot._ecoStallTurns = 0
        return False
    bot._ecoStallTurns += 1
    return bot._ecoStallTurns >= STALL_NO_PROGRESS_TURNS


def _clearApproach(bot) -> None:
    """Drop the net-progress tracker once we stop approaching a node (conceded it,
    or it was reached and handed to build/relay). A fresh target re-seeds it."""
    bot._ecoStallGoal = None
    bot._ecoBestDist = 0
    bot._ecoStallTurns = 0


def _detourPenalty(ct: Controller, hm, origin: Position, ore: Position) -> int:
    """Extra steps a builder likely pays routing around walls between it and `ore`.

    Walks the straight 4-connected line origin->ore and counts tiles a path can't
    cross (walls, out-of-bounds, ally harvesters/cores — whatever `isImpassable`
    forbids). Deliberately ignores enemy buildings: those are priced-not-forbidden
    and transient, so they belong to A* and `_skipOre`, not to a static-geometry
    proxy. The node tile itself is skipped — it is ore, not an obstacle.

    Axis choice is magnitude-only, so the walk (and the penalty) is invariant under
    the map reflections and 180deg rotations symmetry rests on. `readTile` is
    round-memoised, so tiles shared by several candidates are read once.
    """
    x, y = origin.x, origin.y
    blocked = 0
    while (x, y) != (ore.x, ore.y):
        dx, dy = ore.x - x, ore.y - y
        if abs(dx) >= abs(dy):
            x += 1 if dx > 0 else -1
        else:
            y += 1 if dy > 0 else -1
        if (x, y) == (ore.x, ore.y):
            break
        if ph.isImpassable(ct, hm, Position(x, y)):
            blocked += 1
    return blocked * DETOUR_PER_WALL


def pickOre(bot, ct: Controller) -> Position | None:
    """Nearest workable node, then a stale site to verify.

    Free ore and a tappable enemy harvester compete on distance rather than in
    strict tiers: tapping spends belts alone — no harvester cost, which scales
    +5% every time — and denies the node, so a close enemy harvester beats a
    distant empty node. Ranking hostile strictly below free meant it was never
    reached at all while any free ore remained known.

    The stale tier is what keeps builders cycling: once every node we remember is
    spoken for, they go re-check the oldest sighting instead of exploring forever.
    """
    pos = ct.get_position()
    hm = bot.hivemind

    # distTo is built once per call; the wall-detour term inside it is the only
    # per-ore work, and it reads memoised tiles, so it stays inside the 10ms budget.
    def distTo(ore: Position) -> int:
        base = abs(ore.x - pos.x) + abs(ore.y - pos.y)
        return base + _detourPenalty(ct, hm, pos, ore)

    return chooseOre(bot, ct, distTo, bot.hivemind.my_index)


def _relaySeeds(bot, ct: Controller, ore: Position) -> list[Position]:
    """Orthogonal neighbours of `ore` a relay can actually start from: connected to
    the core through open terrain (ph.coreBeltReach) AND a tile a belt can occupy and
    the builder can stand on to build (ph.beltPassable). The `beltPassable` gate
    matters because the flood ignores buildings, so `reach` also contains both cores'
    footprints, walls-of-turrets, and enemy barriers — none of which can seed a relay
    or be stood on; without it `min`-by-distance could pick the impassable core tile
    beside a core-adjacent node and strand the builder on a walk it can never finish."""
    reach = ph.coreBeltReach(bot, ct)
    return [
        n for n in ph.orthogonalTiles(ore)
        if (n.x, n.y) in reach and ph.beltPassable(ct, bot.hivemind, n)
    ]


def _relayReachable(bot, ct: Controller, ore: Position) -> bool:
    """A free ore node whose harvester can be carried home — it has an orthogonal
    neighbour connected to the core through open terrain (ph.coreBeltReach). A node
    walled off from the core by its own field has none, so we neither target it nor
    let a builder step inside and seal itself in behind its own harvesters.

    The cheap gate (bare set membership) for the per-node `rankOre` loop; `seekOre`
    then re-checks the chosen approach with `_relaySeeds`, which also demands the seed
    be stand-on/belt-able — a node that passes here but has every core-side neighbour
    blocked is conceded there, not built. Splitting it keeps `beltPassable`'s reads
    off the hot ranking loop (they pushed a builder turn from ~6ms to ~9ms)."""
    reach = ph.coreBeltReach(bot, ct)
    return any((n.x, n.y) in reach for n in ph.orthogonalTiles(ore))


def rankOre(bot, ct: Controller, distTo) -> tuple[list[Position], list[Position]]:
    """(workable nodes best-first, stale sites nearest-first), measured by `distTo`.

    Takes a distance callable rather than an origin tile because the two callers
    measure from different things: a builder from its own position, the core from its
    2x2 footprint via `ph.manhattanToCore`. Handing the core's anchor over as a bare
    point would put the anchor bias back — at most two tiles, but enough to flip
    near-ties between mirror-equivalent nodes, which is exactly what symmetry rests on.
    """
    ranked: list[tuple[int, Position]] = []
    stale: list[Position] = []

    for ore in knownOre(bot, ct):
        if _skipped(bot, ore):
            continue
        t = ph.readTile(ct, bot.hivemind, ore)
        if not t.ore:
            continue
        d = distTo(ore)
        if t.etype is None:
            if not _relayReachable(bot, ct, ore):
                continue  # pocketed — no belt can reach it home (see helper)
            ranked.append((d, ore))
        elif t.etype == EntityType.HARVESTER and t.enemy:
            if _withinHijackRange(bot, ore) and not _alreadyTapped(bot, ct, ore):
                ranked.append((d - HIJACK_BONUS, ore))
        elif t.etype == EntityType.BARRIER and t.enemy:
            if _worthClearing(bot, ore):
                ranked.append((d + ORE_BARRIER_CLEAR_PENALTY, ore))
        elif _sightingStale(bot, ct, ore):
            stale.append(ore)

    ranked.sort(key=lambda item: item[0])
    stale.sort(key=distTo)
    return [ore for _d, ore in ranked], stale


def _spreadPick(nodes: list[Position], distTo, index: int) -> Position:
    """Fan `index` across the leading band of near-equidistant `nodes` (nearest
    first), never skipping a node meaningfully closer than the band.

    `nodes` is sorted by `distTo`, so the band is a prefix: grow it while the next
    node is within SPREAD_BAND of the nearest and we are still under ORE_SPREAD. A
    lone nearest node (everything else a real trek) collapses the band to one, and
    everyone takes it — a brief race that self-resolves, since the first to build it
    turns the tile into an own-harvester rankOre then skips."""
    d0 = distTo(nodes[0])
    band = 1
    limit = min(len(nodes), ORE_SPREAD)
    while band < limit and distTo(nodes[band]) - d0 <= SPREAD_BAND:
        band += 1
    return nodes[index % band]


def chooseOre(bot, ct: Controller, distTo, index: int) -> Position | None:
    """Pick out of `rankOre` by `index`, fanning consecutive builders across the
    near-equidistant leading nodes (see `_spreadPick`) — not a claim, see
    observations.md under Known gaps.

    For the pre-spawn guess, `index` is the one the builder about to be spawned will
    be given (`built + _MYINDEX_OFFSET`, empirically +2 — see observations.md); the
    guess only predicts where it will head and never reaches the builder.
    """
    ranked, stale = rankOre(bot, ct, distTo)
    if ranked:
        return _spreadPick(ranked, distTo, index)
    if stale:
        return _spreadPick(stale, distTo, index)
    return None


def knownOre(bot, ct: Controller) -> list[Position]:
    """Every ore tile we know about. Filtering is pickOre's job — doing it here
    hid enemy harvesters from the picker entirely, so they were never engaged."""
    return [
        Position(x, y)
        for (x, y), entry in bot.hivemind.local_map.items()
        if entry.get("ore")
    ]


def _skipOre(bot, ore: Position) -> None:
    bot.oreSkip[(ore.x, ore.y)] = bot.hivemind.global_turn


def _skipped(bot, ore: Position) -> bool:
    """Nodes this builder gave up reaching. Expires, because the thing in the way
    is usually another unit or a belt that won't be there forever."""
    when = bot.oreSkip.get((ore.x, ore.y))
    return when is not None and bot.hivemind.global_turn - when <= ORE_SKIP_TURNS


def _oreWorthGoing(bot, ct: Controller, ore: Position) -> bool:
    if _skipped(bot, ore):
        return False
    t = ph.readTile(ct, bot.hivemind, ore)
    if not t.ore:
        return False
    if t.etype is None:
        return _relayReachable(bot, ct, ore)
    if t.etype == EntityType.HARVESTER and t.enemy:
        return _withinHijackRange(bot, ore) and not _alreadyTapped(bot, ct, ore)
    if t.etype == EntityType.BARRIER and t.enemy:
        return _worthClearing(bot, ore)
    return _sightingStale(bot, ct, ore)


def _enemyHarvester(bot, ct: Controller, ore: Position) -> bool:
    t = ph.readTile(ct, bot.hivemind, ore)
    return t.etype == EntityType.HARVESTER and t.enemy


def _sightingStale(bot, ct: Controller, ore: Position) -> bool:
    if ct.is_in_vision(ore):
        return False  # we can see it right now; memory doesn't come into it
    entry = bot.hivemind.local_map.get((ore.x, ore.y))
    if entry is None:
        return True
    age = bot.hivemind.global_turn - entry.get("last_seen_turn", -(10 ** 9))
    return age > ORE_RECHECK_TURNS


def _alreadyTapped(bot, ct: Controller, ore: Position) -> bool:
    """One of our belts already feeds from this harvester.

    Without this, a successful hijack was invisible to the picker: the tile
    still reads HARVESTER + enemy forever, so it kept winning rankOre's
    HIJACK_BONUS against everything else and the same builder re-called
    hijackRelay on it turn after turn (935 times on one node in one match) —
    each call an immediate, harmless no-op (planRelay's own "already feeding
    a connected belt" check bails before touching anything), but the builder
    never moved on to new ore or a new hijack. Same network-membership check
    `planRelay` already makes before laying anything; costContext is
    round-cached, so asking here costs nothing extra once anything else has
    asked this round, and only the full buildNetwork cost the first time.
    """
    ctx = ph.costContext(bot, ct, "walk")
    return any(ctx.isNetworkMember(n) for n in ph.orthogonalTiles(ore))


def _withinHijackRange(bot, ore: Position) -> bool:
    core = bot.corePos
    if core is None:
        return True
    return ph.manhattanToCore(ore, core) <= HIJACK_MAX_MANHATTAN


def _worthClearing(bot, ore: Position) -> bool:
    """True if an enemy-barriered ore node is worth claiming to grind clear: close
    enough to our core (ORE_BARRIER_CLEAR_RADIUS) and not one we already gave up on
    (repaired, held off in oreSkip). A far barriered node isn't worth a multi-turn
    grind — open ore is a better use of the builder."""
    if _skipped(bot, ore):
        return False
    core = bot.corePos
    return core is None or ph.manhattanToCore(ore, core) <= ORE_BARRIER_CLEAR_RADIUS


def _barrierRepaired(bot, ct: Controller, ore: Position) -> bool:
    """True if grinding the barrier on `ore` has stopped making progress — its HP
    hasn't hit a new low for ORE_BARRIER_STALL_TURNS consecutive engaged looks
    (patience.stallCheck — a count of checks, not elapsed turns, so a builder
    flung or retargeted off the node mid-grind doesn't return to an instant false
    give-up), i.e. the enemy repairs it as fast as we cut. Keeps grinding a barrier
    we're actually breaking (HP still falling). HP unreadable (no building id)
    reads as not-stalled, so we fall through and grind as before."""
    bid = ct.get_tile_building_id(ore)
    if bid is None:
        return False
    hp = ct.get_hp(bid)
    key = (ore.x, ore.y)
    return stallCheck(bot._oreGrindLow, key, hp, ORE_BARRIER_STALL_TURNS)
