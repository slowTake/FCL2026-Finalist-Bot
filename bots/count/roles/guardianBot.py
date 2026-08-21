"""Guardian role: the standing home-defence body.

The first eco-builder to *deliver* a harvester (relay complete — income already
flowing) claims this role and stops farming to guard the core. It carries a
liveness heartbeat in the store (store.stampGuardian): each turn it completes it
re-stamps the current round, so if it dies — or wedges on a repeating fault — its
claim goes stale and the next eligible eco-builder re-claims it (claimIfOpen).
That heartbeat is what makes the role permanent without any builder-respawn
machinery — see docs/adr.

Orchestrator only, per AGENTS.md: runGuardian is a priority ladder over
single-purpose steps. The threat response itself is guns.defendCore, unchanged
and shared with the eco path — the guardian's contribution is being the body
that is always home to run it, so a rush draws fire immediately instead of only
when some farming builder happens to be near and looking.

Combat depth deliberately left for a second increment (see the ladder note):
baron's blockGunLine (a ~3-Ti barrier dropped in a gunner's line to the core,
cheaper than fielding a turret) and defSentinel (answering an out-of-leash
sentinel). The v1 ladder below is shippable without them.
"""

from __future__ import annotations

from fcode import Controller, EntityType, Position

import debug
import emplace
import store
from movement import walkOrRetarget, walkTo
from patience import BLOCKED_WAIT_TURNS, holdTask
import pathfindingHelpers as ph
from pathing import manhattan

# How far, in Manhattan tiles from the core's edge, the guardian roams while the
# core is quiet — wide enough to reach a belt the enemy cut on the perimeter and
# to patrol for rear vision. Manhattan, not Chebyshev: builders and belts are
# 4-connected here, and observations.md rules Chebyshev out (it under-reads 45°).
# Matches ecoBot.CORE_DEFENSE_RADIUS so the guardian's reach lines up with the
# radius defendCore/repairCoreBelts already reason about.
GUARDIAN_LEASH = 8

# A guardian claim whose heartbeat is older than this many rounds is treated as
# dead, freeing the next eligible eco-builder to re-claim. The live guardian
# re-stamps every turn, so a healthy claim's age is 0-1; this only has to exceed
# the longest run of turns a live guardian could miss (a handful, if it faults).
# Small on purpose — a large TTL just delays defence recovery after a death.
GUARDIAN_ALIVE_TTL = 15


def eligible(bot) -> bool:
    """This builder has delivered a harvester and could take guardian duty.

    Set in relays.relayDone the turn a relay completes, so eligibility means
    income is already flowing before we pull a body onto defence."""
    return getattr(bot, "_harvesterDelivered", False)


def _stale(ct: Controller, ts: int) -> bool:
    """Is a heartbeat stamped at masked round `ts` older than the TTL?

    Wrap-aware against the store's heartbeat modulus, though a ~1000-round match
    never actually wraps the 12-bit field — kept correct regardless."""
    mask = store.guardianHeartbeatMask()
    now = ct.get_current_round() & mask
    age = (now - ts) & mask
    return age > GUARDIAN_ALIVE_TTL


def claimIfOpen(bot, ct: Controller) -> None:
    """An eligible eco-builder grabs guardian duty if the slot is empty or dead.

    Writes this builder's index + a fresh heartbeat, after which it is routed to
    runGuardian. A no-op for a builder that hasn't delivered a harvester, or while
    a live guardian holds the slot. First eligible builder to reach an open slot
    wins — that is the "first to finish its harvester" trigger. If two claim the
    same round, last-write-wins picks one and the other sees a live holder next
    turn.

    Never claims mid-relay. The guardian ladder has no notion of `inRelay`, so a
    builder that switched roles part-way through a line would abandon it — losing
    the belts *and* stranding the harvester it already paid for, which nothing
    re-relays (rankOre skips a tile already carrying our own harvester). Only the
    very first claim is naturally safe here (it fires the turn relayDone ran); a
    re-claim after a guardian death lands on a builder that is usually mid-line,
    which is exactly the case this guard exists for. Waiting a few rounds for the
    line to finish costs nothing against a TTL the design already tolerates.

    Also never claims with a pending orphan or an in-progress verification walk. The
    guardian ladder runs neither reconnectOrphan nor verifyRelay, so a builder holding
    `_orphanHarvester` (a line awaiting re-relay) or `_verifyHarvester` (a line it is
    walking to check) would strand that work the same way a mid-relay switch does — and
    the verify case is worst by timing, since a builder becomes guardian-eligible on the
    very delivery that arms the walk. Defer until both clear; the guardian TTL absorbs
    the wait and the pre-existing machinery clears the flags on its own."""
    if (
        not eligible(bot)
        or bot.inRelay
        or getattr(bot, "_orphanHarvester", None) is not None
        or getattr(bot, "_verifyHarvester", None) is not None
    ):
        return
    idx = bot.hivemind.my_index
    if idx <= 0:
        return
    held, ts = store.readGuardian(ct)
    if held == idx:
        return  # already ours
    if held != 0 and not _stale(ct, ts):
        return  # a live guardian holds it
    store.stampGuardian(ct, idx, ct.get_current_round())
    if debug.ENABLED:
        debug.log("guardian", f"idx={idx} CLAIMED (prev={held}) rnd={ct.get_current_round()}")


# --- Combat depth ------------------------------------------------------------
# Re-derived from baron's defence.py (docs/adr/0001-guardian-role.md's deferred
# item), not ported: same two ideas, count's own helpers. blockGunLine answers a
# gunner shelling the core with a ~3-Ti barrier in its line of fire instead of a
# ~30-Ti turret — far cheaper, so it runs ahead of defendCore in the ladder.
# defSentinel answers a sentinel/launcher parked beyond the leash, which
# defendCore's own pad-scan (bound to wherever the guardian already stands)
# structurally cannot reach without first repositioning.


def _gunnerFiringAtCore(ct: Controller, core: Position):
    """Nearest enemy gunner currently landing a shot on a core footprint tile,
    as (gunner_pos, facing, core_tile), or None.

    Engine ground truth only (`can_fire_from`) — this wants a shot that is
    actually connecting right now, not merely a gunner somewhere in range."""
    myTeam = ct.get_team()
    here = ct.get_position()
    footprint = ph.coreFootprint(core)
    best = None
    bestD = None
    for eid in ct.get_nearby_units():
        if ct.get_team(eid) == myTeam:
            continue
        if ct.get_entity_type(eid) != EntityType.GUNNER:
            continue
        gpos = ct.get_position(eid)
        facing = ct.get_direction(eid)
        for tile in footprint:
            if not ct.can_fire_from(gpos, facing, EntityType.GUNNER, tile):
                continue
            d = manhattan(here, gpos)
            if bestD is None or d < bestD:
                best, bestD = (gpos, facing, tile), d
            break  # one connecting tile is enough to know this gunner counts
    return best


def _gunLineBlocked(ct: Controller, hm, tile: Position, core: Position) -> bool:
    """True if `tile` is already an ally barrier, or is part of the core footprint
    (nothing to block — the core itself can't be stood on to fire past)."""
    for foot in ph.coreFootprint(core):
        if tile.x == foot.x and tile.y == foot.y:
            return True
    t = ph.readTile(ct, hm, tile)
    return t.etype == EntityType.BARRIER and not t.enemy


def _layableTile(ct: Controller, hm, tile: Position) -> bool:
    """Can a barrier be placed on `tile` this turn: in vision (so the check is
    trustworthy), empty, action-ready, and the engine agrees."""
    if not ct.is_in_vision(tile):
        return False
    if ph.readTile(ct, hm, tile).etype is not None:
        return False
    if ct.get_action_cooldown() != 0:
        return False
    return ct.can_build_barrier(tile)


def blockGunLine(bot, ct: Controller) -> bool:
    """Barrier-cheap defence: drop a barrier in a gunner's current line of fire.

    The engine's own `can_fire_from` already refuses to fire through an
    occupied tile, so a ~3-Ti barrier square in the shot forces the gunner to
    destroy it before landing another hit — far cheaper than fielding a ~30-Ti
    gunner or sentinel to remove the threat outright, so this runs ahead of
    `defendCore` in the ladder: a cheaper answer should always be tried first.

    Gunners only, matching `_gunnerFiringAtCore` — sentinels/launchers aren't
    modelled the same connecting-shot way here and fall through to `defendCore`.

    Remembers the tile on `bot._gunLineBlockTile` so `maintainGunLineBlock` can
    rebuild it once the gunner (or something else) destroys it — once blocked,
    the shot stops connecting and this function alone would never notice it
    again, since its own detection is "is a shot landing right now".
    """
    core = bot.corePos
    if core is None or bot.inRelay:
        return False
    hit = _gunnerFiringAtCore(ct, core)
    if hit is None:
        return False
    _gpos, facing, core_tile = hit
    block = core_tile.add(facing.opposite())
    if not ph.inBounds(ct, block):
        return False
    if _gunLineBlocked(ct, bot.hivemind, block, core):
        return False  # already blocked
    if not _layableTile(ct, bot.hivemind, block):
        return False
    ct.build_barrier(block)
    bot._gunLineBlockTile = (block.x, block.y)
    if debug.ENABLED:
        debug.log("guardian", f"idx={bot.hivemind.my_index} blockGunLine at {block} rnd={ct.get_current_round()}")
    return True


def maintainGunLineBlock(bot, ct: Controller) -> bool:
    """Rebuild this guardian's own gun-line barrier the moment it's gone.

    Opportunistic: only acts when the remembered tile is already in vision and
    actually needs rebuilding. No ownership handoff needed — if this guardian
    dies, `_gunLineBlockTile` dies with it (it's a plain Player field, not
    stored), and the next guardian's own `blockGunLine` re-detects the live
    gunner and picks the gap back up from scratch.
    """
    core = bot.corePos
    tile_xy = bot._gunLineBlockTile
    if core is None or tile_xy is None or bot.inRelay:
        return False
    tile = Position(tile_xy[0], tile_xy[1])
    if _gunLineBlocked(ct, bot.hivemind, tile, core):
        return False  # still standing, nothing to do
    if not ct.is_in_vision(tile):
        return False  # can't confirm or act — wait for vision, don't chase it
    if ph.readTile(ct, bot.hivemind, tile).etype is not None:
        bot._gunLineBlockTile = None  # something else occupies it now
        return False
    if not _layableTile(ct, bot.hivemind, tile):
        return False
    ct.build_barrier(tile)
    if debug.ENABLED:
        debug.log("guardian", f"idx={bot.hivemind.my_index} rebuilt gun-line block at {tile} rnd={ct.get_current_round()}")
    return True


def _outOfLeashThreat(bot, ct: Controller) -> Position | None:
    """Nearest enemy sentinel or launcher within guns.CORE_THREAT_RADIUS of the
    core but beyond this guardian's own leash — the gap `defendCore`'s own
    pad-scan (bound to wherever the guardian already stands) cannot answer
    without first repositioning. Gunners excluded: their range (~3.6) means one
    beyond the leash (8) essentially never actually bears on the core, so
    answering it here would just spend Ti chasing a non-threat.
    """
    import guns

    core = bot.corePos
    if core is None:
        return None
    myTeam = ct.get_team()
    best = None
    bestD = None
    for eid in ct.get_nearby_entities():
        if ct.get_team(eid) == myTeam:
            continue
        if ct.get_entity_type(eid) not in (EntityType.SENTINEL, EntityType.LAUNCHER):
            continue
        p = ct.get_position(eid)
        d = ph.manhattanToCore(p, core)
        if d <= GUARDIAN_LEASH or d > guns.CORE_THREAT_RADIUS:
            continue
        if bestD is None or d < bestD:
            best, bestD = p, d
    return best


def _standable(ct: Controller, hm, pos: Position) -> bool:
    """Empty, in-bounds, non-ore tile a sentinel could occupy."""
    if not ph.inBounds(ct, pos):
        return False
    if ph.isImpassable(ct, hm, pos):
        return False
    t = ph.readTile(ct, hm, pos)
    return t.etype is None and not t.ore


def _defSentinelSite(bot, ct: Controller, target: Position) -> Position | None:
    """Best leash-bound tile from which a sentinel would bear on `target`,
    nearest the core first, then nearest us. Geometry only — the actual
    build-legality probe (LoS veto, can_build_sentinel) happens once we're
    adjacent, via guns.dropSentinelOn, exactly like every other pad-drop
    helper in this codebase requires. Same inversion raid._candidates uses
    (aimed at the enemy core there, a single threat tile here) via
    emplace.reachOffsets, but home-bound instead of enemy-bound: a candidate
    outside the leash is dropped outright, not merely deprioritised, so this
    can never walk the guardian off the leash it exists to hold.
    """
    core = bot.corePos
    here = ct.get_position()
    reach = emplace.reachOffsets(ct)
    seen = set()
    best = None
    bestKey = None
    for offsets in reach.values():
        for ox, oy in offsets:
            site = Position(target.x - ox, target.y - oy)
            key = (site.x, site.y)
            if key in seen:
                continue
            seen.add(key)
            if ph.manhattanToCore(site, core) > GUARDIAN_LEASH:
                continue
            if not _standable(ct, bot.hivemind, site):
                continue
            rank = (ph.manhattanToCore(site, core), manhattan(here, site))
            if bestKey is None or rank < bestKey:
                best, bestKey = site, rank
    return best


def defSentinel(bot, ct: Controller) -> bool:
    """Answer an out-of-leash sentinel/launcher with our own sentinel, built
    from inside the leash.

    Sits below `defendCore` in the ladder — anything `defendCore` can already
    reach from where the guardian stands is cheaper to answer that way (no
    walk needed). This exists specifically for the threat it structurally
    can't: one beyond the guardian's wander radius that a sentinel's longer
    reach could still hit from the leash edge, if the guardian were
    positioned there. Tries building in place first (covers the case where
    the guardian already happens to be well positioned), then walks toward
    the best leash-bound site otherwise.

    _defSentinelSite re-picks fresh every call rather than committing to a
    waypoint, so the "nearest to us" tiebreak can shift the chosen site
    between turns as the guardian moves, churning walkTo's goal key —
    the same class of cost raidBot.advance was fixed for on cross-map travel
    (docs/observations.md's Performance section). Left as-is here: the walk
    is leash-bounded (≤ GUARDIAN_LEASH), a small search space compared to a
    cross-map A*, and this is a first cut — worth revisiting only if measured.
    """
    import guns

    core = bot.corePos
    if core is None or bot.inRelay:
        return False
    target = _outOfLeashThreat(bot, ct)
    if target is None:
        return False
    myTeam = ct.get_team()
    if guns.coveredByAllySentinel(ct, myTeam, target):
        return False
    if guns.dropSentinelOn(ct, target):
        return True
    site = _defSentinelSite(bot, ct, target)
    if site is None:
        return False

    # site is where the *sentinel* would stand, not a walk destination — build
    # requires standing orthogonally adjacent to it, never on it (same reason
    # heal.healAlly walks to be *beside* its target instead of onto it). Walk
    # toward site but stop the moment we're adjacent; dropSentinelOn's own
    # pad-scan (ph.orthogonalTiles(ct.get_position())) then finds site as one
    # of its candidates and does the actual build-legality/facing probe.
    def beside(p: Position) -> bool:
        return ph.beltFacing(p, site) is not None

    if walkTo(bot, ct, beside, site, ("defSentinel", site.x, site.y)):
        return True
    return holdTask(bot, "defSentinel", BLOCKED_WAIT_TURNS)


def _withinLeash(bot, ct: Controller) -> bool:
    return ph.manhattanToCore(ct.get_position(), bot.corePos) <= GUARDIAN_LEASH


def _returnToLeash(bot, ct: Controller) -> bool:
    """Walk back inside the leash. Heuristic-guided toward the core; the goal is
    any tile within GUARDIAN_LEASH of it so the guardian stops at the perimeter
    rather than crowding the footprint."""
    def inLeash(p: Position) -> bool:
        return ph.manhattanToCore(p, bot.corePos) <= GUARDIAN_LEASH

    if walkTo(bot, ct, inLeash, bot.corePos, ("guardHome", bot.corePos.x, bot.corePos.y)):
        return True
    return holdTask(bot, "guardLeash", BLOCKED_WAIT_TURNS)


def _patrolWaypoints(bot, ct: Controller) -> list[Position]:
    """Tiles at the leash edge, N/E/S/W of the core, for rear vision.

    Bounded to inside the leash so cycling them never trips _returnToLeash. Built
    off the core edge, not its anchor, so both mirror sides patrol symmetrically.

    Filtered through `isImpassable`, which drops both the off-map case (a core
    within `off` of a map edge projects a waypoint outside it) and the standing
    blocker case (a wall, or one of our own harvesters — ore near the core is
    farmed early, so this is common). An unfiltered waypoint is one an exact-tile
    goal can never satisfy, which is a permanent stall, not a slow turn. May
    return fewer than four, or none at all in a very cramped pocket."""
    core = bot.corePos
    off = max(1, GUARDIAN_LEASH // 2)
    cx, cy = core.x, core.y  # anchor = min-x/min-y tile of the 2x2
    ring = (
        Position(cx, cy - off),
        Position(cx + 1 + off, cy),
        Position(cx, cy + 1 + off),
        Position(cx - off, cy),
    )
    return [p for p in ring if not ph.isImpassable(ct, bot.hivemind, p)]


def _patrol(bot, ct: Controller) -> bool:
    """Cycle the leash waypoints for vision coverage when nothing else is doing.

    Advances the ring index on arrival, and on `walkOrRetarget` reporting the
    current waypoint unroutable — the same "the goal itself is the problem"
    signal raidBot uses to abandon a site. Without that second case a waypoint
    that is reachable-looking but has no route pins the guardian on it for the
    match, re-running a full A* *and* its BFS fallback every quiet turn: a
    permanent stall with a standing CPU cost, on a unit that acts every turn.
    walkOrRetarget clears the poisoned path/goal/avoid state itself, so the next
    waypoint gets a clean search and no ad-hoc counter is needed here."""
    waypoints = _patrolWaypoints(bot, ct)
    if not waypoints:
        return False  # nowhere legal to stand; the ladder falls through
    idx = bot._guardianPatrolIdx % len(waypoints)
    target = waypoints[idx]
    pos = ct.get_position()
    if pos.x == target.x and pos.y == target.y:
        idx = bot._guardianPatrolIdx = (idx + 1) % len(waypoints)
        target = waypoints[idx]

    def atTarget(p: Position) -> bool:
        return p.x == target.x and p.y == target.y

    if walkOrRetarget(bot, ct, atTarget, target, ("guardPatrol", target.x, target.y)):
        bot._guardianPatrolIdx = (idx + 1) % len(waypoints)
        if debug.ENABLED:
            debug.log("guardian", f"idx={bot.hivemind.my_index} patrol waypoint "
                                   f"{target} unroutable — rotating")
    return True


def runGuardian(bot, ct: Controller) -> None:
    """Stamp liveness for the *previous* completed turn, then run the ladder.

    The stamp is deliberately conditional on `_guardianTurnOk`, which `_ladder`
    sets only by running to completion. Stamping unconditionally at the top would
    make the heartbeat mean "run() was entered" rather than "the guardian is
    working": Player.run swallows every exception and a TLE only truncates the
    turn, so a guardian faulting in the same place every turn would keep its claim
    fresh forever while home defence silently did nothing — unrecoverable for the
    match, and precisely the silent-failure mode mistakes.md warns about. This way
    a persistently faulting guardian stops stamping and is replaced within the TTL,
    while a healthy one is never a turn behind by more than the round it is on."""
    if bot.corePos is None:
        return

    idx = bot.hivemind.my_index
    if idx > 0 and bot._guardianTurnOk:
        store.stampGuardian(ct, idx, ct.get_current_round())

    bot._guardianTurnOk = False
    _ladder(bot, ct)
    bot._guardianTurnOk = True


def _ladder(bot, ct: Controller) -> None:
    """Gun-line block -> defend -> out-of-leash sentinel answer ->
    return-to-leash -> quiet economy (belt repair / leash-bounded heal) ->
    patrol.

    blockGunLine leads: a ~3-Ti barrier in a connecting gunner's line of fire
    is far cheaper than the ~30-Ti turret defendCore would spend on the same
    threat, so the cheaper answer always gets first refusal. defendCore comes
    next and is bounded to CORE_THREAT_RADIUS of the core, so a threat is
    answered immediately and the guardian never chases one far from home.
    defSentinel follows as the fallback for exactly what defendCore's own
    pad-scan structurally can't reach — a sentinel/launcher beyond the leash.
    Everything below it only runs when the core is quiet. Ore-seeking is
    deliberately *not* in the quiet economy: committing to a fresh harvester
    would pull the guardian into a relay away from the core, which is the one
    thing a guardian must not do. Restoring a *cut* line (repairCoreBelts) is the
    exception — it recovers a whole harvester's output and stays within the
    core-defence radius by construction. Widening this to leash-bounded
    ore-seeking is the noted next step."""
    # Cheapest answer first — see the module-level comment above blockGunLine.
    if blockGunLine(bot, ct):
        return
    if maintainGunLineBlock(bot, ct):
        return

    # Threat response — shared with the eco path, function-level import to avoid
    # the guns -> respawn -> ecoBot import cycle (same reason ecoBot imports it
    # this way). defendCore answers hostile turrets and launchers only — never an
    # enemy builder (chasing a mobile body with a gun is a losing Ti trade — see
    # guns.py). It sits above the leash step on purpose: answering a live threat
    # outranks coming home.
    #
    # chase=False: the guardian only *builds* against a threat (out to the full
    # CORE_THREAT_RADIUS — a sentinel out-ranges it) and never walks the body out to
    # chase. That keeps it home instead of being baited off the core by a mobile
    # gunner, while the leash/patrol below repositions it for a bearing across turns.
    # The eco path keeps chase=True so a farming builder can leave its spot to defend.
    import guns
    if guns.defendCore(bot, ct, chase=False):
        return

    # Beyond the leash, but a sentinel could still reach it from the edge —
    # see defSentinel's own docstring for why this can't just be folded into
    # defendCore above.
    if defSentinel(bot, ct):
        return

    # Quiet from here down. Pull back inside the leash before doing chores so a
    # guardian that walked out to a perimeter belt last turn comes home.
    if not _withinLeash(bot, ct):
        if _returnToLeash(bot, ct):
            return

    # Restore a supply line the enemy severed near the core, then top up HP.
    from ecoBot import repairCoreBelts
    import heal
    if repairCoreBelts(bot, ct):
        return
    # Leash-bounded, unlike the eco path's copy: heal's own "nearby" is measured
    # from the *healer's* vision, so an unbounded target beyond the leash makes
    # this step walk out and the leash step above walk back, every turn, for as
    # long as the building stays damaged — a 2-tile oscillation that also
    # replans A* each time as the walk goal alternates. repairCoreBelts needs no
    # such filter; its own target search is already bounded to the core radius.
    #
    # Includes TURRET_HEAL_TYPES: the home gunners/sentinels the guardian stands
    # beside are exactly what take fire during a defence, and nothing else heals
    # them (raidBot's own TURRET_HEAL_TYPES call only ever sees turrets the
    # raider itself placed at the enemy core). One combined call, same as the
    # eco path's single-type one — nearestDamagedAlly already picks whichever
    # damaged ally of either kind is nearest.
    if heal.healNearby(bot, ct, heal.ECO_HEAL_TYPES + heal.TURRET_HEAL_TYPES, accept=_inLeash(bot)):
        return

    _patrol(bot, ct)


def _inLeash(bot):
    """Predicate: is a tile inside this guardian's leash. For the heal filter."""
    core = bot.corePos

    def test(p: Position) -> bool:
        return ph.manhattanToCore(p, core) <= GUARDIAN_LEASH

    return test
