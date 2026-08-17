"""Guardian role: the standing home-defence body.

The first eco-builder to *deliver* a harvester (relay complete — income already
flowing) claims this role and stops farming to guard the core. It carries a
liveness heartbeat in the store (store.stampGuardian): every turn it runs it
re-stamps the current round, so if it dies its claim goes stale and the next
eligible eco-builder re-claims it (claimIfOpen). That heartbeat is what makes the
role permanent without any builder-respawn machinery — see docs/adr.

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
from movement import walkTo
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

    Called at the top of runEcoBot. Writes this builder's index + a fresh
    heartbeat; from the next turn main._runBuilder routes it to runGuardian. A
    no-op for a builder that hasn't delivered a harvester, or while a live
    guardian holds the slot. First eligible builder to reach an open slot wins —
    that is the "first to finish its harvester" trigger. If two claim the same
    round, last-write-wins picks one and the other sees a live holder next turn."""
    if not eligible(bot):
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


def _patrolWaypoints(bot) -> list[Position]:
    """Four tiles at the leash edge, N/E/S/W of the core, for rear vision.

    Bounded to inside the leash so cycling them never trips _returnToLeash. Built
    off the core edge, not its anchor, so both mirror sides patrol symmetrically."""
    core = bot.corePos
    off = max(1, GUARDIAN_LEASH // 2)
    cx, cy = core.x, core.y  # anchor = min-x/min-y tile of the 2x2
    return [
        Position(cx, cy - off),
        Position(cx + 1 + off, cy),
        Position(cx, cy + 1 + off),
        Position(cx - off, cy),
    ]


def _patrol(bot, ct: Controller) -> bool:
    """Cycle the leash waypoints for vision coverage when nothing else is doing.

    Advances the ring index on arrival, or when walkTo can't make progress, so a
    blocked waypoint doesn't pin the guardian on it."""
    waypoints = _patrolWaypoints(bot)
    idx = bot._guardianPatrolIdx % len(waypoints)
    target = waypoints[idx]
    pos = ct.get_position()
    if pos.x == target.x and pos.y == target.y:
        bot._guardianPatrolIdx = (idx + 1) % len(waypoints)
        target = waypoints[bot._guardianPatrolIdx]
    return walkTo(bot, ct, lambda p: p.x == target.x and p.y == target.y, target,
                  ("guardPatrol", target.x, target.y))


def runGuardian(bot, ct: Controller) -> None:
    """Heartbeat -> defend -> return-to-leash -> quiet economy (belt repair /
    heal, both inherently near-core) -> patrol.

    defendCore leads and is bounded to CORE_THREAT_RADIUS of the core, so a
    threat is answered immediately and the guardian never chases one far from
    home. Everything below it only runs when the core is quiet. Ore-seeking is
    deliberately *not* in the quiet economy: committing to a fresh harvester
    would pull the guardian into a relay away from the core, which is the one
    thing a guardian must not do. Restoring a *cut* line (repairCoreBelts) is the
    exception — it recovers a whole harvester's output and stays within the
    core-defence radius by construction. Widening this to leash-bounded
    ore-seeking is the noted next step."""
    if bot.corePos is None:
        return

    # Re-stamp liveness first and cheapest — a turn we fault after this still
    # counts as alive up to the TTL, and one we fault before does not, which is
    # the safe direction (a truly wedged guardian ages out and gets replaced).
    idx = bot.hivemind.my_index
    if idx > 0:
        store.stampGuardian(ct, idx, ct.get_current_round())

    # Threat response — shared with the eco path, function-level import to avoid
    # the guns -> respawn -> ecoBot import cycle (same reason ecoBot imports it
    # this way).
    import guns
    if guns.defendCore(bot, ct):
        return

    # Quiet from here down. Pull back inside the leash before doing chores so a
    # guardian that walked out to a perimeter belt last turn comes home.
    if not _withinLeash(bot, ct):
        if _returnToLeash(bot, ct):
            return

    # Restore a supply line the enemy severed near the core (high-value, near-core
    # by construction), then top up core/belt HP. Both are ecoBot mechanics reused
    # wholesale.
    from ecoBot import repairCoreBelts
    import heal
    if repairCoreBelts(bot, ct):
        return
    if heal.healNearby(bot, ct, heal.ECO_HEAL_TYPES):
        return

    _patrol(bot, ct)
