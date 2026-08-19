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

from fcode import Controller, Position

import debug
import store
from movement import walkOrRetarget, walkTo
from patience import BLOCKED_WAIT_TURNS, holdTask
import pathfindingHelpers as ph

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

    Also never claims with a pending orphan. The guardian ladder never runs
    reconnectOrphan, so a builder holding `_orphanHarvester` (a line that aborted
    or was flung, awaiting re-relay) would strand that harvester the same way a
    mid-relay switch does. Defer until it reconnects — the pre-existing orphan
    machinery clears the flag once the line is restored or the harvester is gone,
    and the guardian TTL absorbs the wait."""
    if not eligible(bot) or bot.inRelay or getattr(bot, "_orphanHarvester", None) is not None:
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
    """Defend -> return-to-leash -> quiet economy (belt repair / leash-bounded
    heal) -> patrol.

    defendCore leads and is bounded to CORE_THREAT_RADIUS of the core, so a
    threat is answered immediately and the guardian never chases one far from
    home. Everything below it only runs when the core is quiet. Ore-seeking is
    deliberately *not* in the quiet economy: committing to a fresh harvester
    would pull the guardian into a relay away from the core, which is the one
    thing a guardian must not do. Restoring a *cut* line (repairCoreBelts) is the
    exception — it recovers a whole harvester's output and stays within the
    core-defence radius by construction. Widening this to leash-bounded
    ore-seeking is the noted next step."""
    # Threat response — shared with the eco path, function-level import to avoid
    # the guns -> respawn -> ecoBot import cycle (same reason ecoBot imports it
    # this way). defendCore answers turrets, launchers, and enemy builders that are
    # grinding our buildings (never a passing builder — see guns.py). It sits above
    # the leash step on purpose: its own target set is bounded to the core-threat
    # radius, so it cannot wander, and answering a live threat outranks coming home.
    import guns
    if guns.defendCore(bot, ct):
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
    if heal.healNearby(bot, ct, heal.ECO_HEAL_TYPES, accept=_inLeash(bot)):
        return

    _patrol(bot, ct)


def _inLeash(bot):
    """Predicate: is a tile inside this guardian's leash. For the heal filter."""
    core = bot.corePos

    def test(p: Position) -> bool:
        return ph.manhattanToCore(p, core) <= GUARDIAN_LEASH

    return test
