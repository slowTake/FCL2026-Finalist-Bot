"""Turret targeting: priority tables, the shared candidate gatherer, the live
fire/rotate loops for gunners and sentinels, and the core-side ammo top-up.

Both turret types trust the engine's own can_fire/can_fire_from as ground
truth for line of sight — a gunner's line is blocked by obstacles, a
sentinel's line ignores them, and the engine already knows which rule applies
to the unit asking. Re-deriving that with a manual ray-walk only creates a
second place for the two answers to disagree (see mistakes.md).

Sentinel placement lives in raid.py. Gunner placement (`placeGunner`) lives
here, re-derived against a real caller as the old deleted board-wide search
never could be: `can_build_gunner` only answers for a tile orthogonally
adjacent to the *calling builder*, so the raider is the one that scans its own
pads. `main.py` dispatches GUNNER here, and `raidBot` calls `placeGunner`.
"""

from __future__ import annotations

from fcode import Controller, Direction, EntityType, GameConstants, Position

import debug
import emplace
import pathfindingHelpers as ph
import store
from movement import walkTo
from patience import BLOCKED_WAIT_TURNS, holdTask
from pathing import manhattan
from respawn import MAX_BUILDERS

_DIRS = tuple(d for d in Direction if d != Direction.CENTRE)

# --- Combat economy gate ----------------------------------------------------
# Nothing offensive is bought below this bank. One dial for "how rich must we be
# before we burn Ti on turrets". A live read, no store slot — every unit derives
# the same answer from the same shared bank on the same turn. Untuned; a starting
# point sized so a normal opening (5 builders + their harvesters) clears it
# before raiders arrive at the enemy core, but a starved one gates combat off.
TI_SURPLUS_THRESHOLD = 150


def surplusReady(ct: Controller) -> bool:
    return ct.get_global_resources() >= TI_SURPLUS_THRESHOLD

# Lower rank fires/rotates first. Equal ranks break on distance only — see
# _bestCandidate.
GUNNER_PRIORITY: dict[EntityType, int] = {
    EntityType.HARVESTER: 0,
    EntityType.CORE: 1,
    EntityType.BUILDER_BOT: 2,
    EntityType.GUNNER: 3,
    EntityType.SENTINEL: 3,
    EntityType.LAUNCHER: 3,
    EntityType.CONVEYOR: 4,
    EntityType.SPLITTER: 4,
    EntityType.BARRIER: 5,
}

# Enemy gunners and sentinels outrank the core here (below only builder_bot) —
# a live turret keeps hurting us, and anything standing near it, every turn
# it's left up, where the core is a one-time win condition that isn't going
# anywhere; trading a shot at the core for a shot that drops a turret is worth
# it. Everything else (harvester, launcher, belts, barrier) keeps its original
# rank — only gunner/sentinel and core swapped places.
SENTINEL_PRIORITY: dict[EntityType, int] = {
    EntityType.BUILDER_BOT: 0,
    EntityType.GUNNER: 1,
    EntityType.SENTINEL: 1,
    EntityType.HARVESTER: 2,
    EntityType.CORE: 3,
    EntityType.LAUNCHER: 3,
    EntityType.CONVEYOR: 4,
    EntityType.SPLITTER: 4,
    EntityType.BARRIER: 5,
}

# Surplus floor for rotating a gunner, NOT the raw cost. Turning costs
# GUNNER_ROTATE_COST (10 Ti) and a tick every time, so a gunner allowed to re-aim
# whenever it can merely *afford* the cost churns the bank tracking targets it may
# never get a shot at. Require a real buffer above the cost before spending a
# rotation. Ported from baron (its GUNNER_ROTATE_MIN_TI = 50, tuned in a top-20
# bot) as a starting point — not yet re-tuned against count's own economy. Also
# gates the 8-direction scan: below this we skip it outright, since we could not act
# on whatever it found.
GUNNER_ROTATE_MIN_TI = 50


# --- Targeting ----------------------------------------------------------


def hostileTiles(etype: EntityType, epos: Position) -> list[Position]:
    """A core is 2x2 and get_position() reports only its anchor tile — aiming
    only at that tile leaves a turret built against one corner permanently
    unable to answer a core it can't geometrically reach from there (a bug
    baron shipped once). Every other entity is one tile."""
    if etype == EntityType.CORE:
        return ph.coreFootprint(epos)
    return [epos]


def visibleHostiles(ct: Controller, priority: dict[EntityType, int]) -> list[tuple[Position, EntityType]]:
    """Enemy tiles worth shooting at, within this unit's own vision (the engine
    sizes it to match attack range). The one place enumeration, footprint
    expansion, and the ally/hostile split live — callers bring their own
    reachability test (can_fire for now, can_fire_from for a hypothetical
    facing or pad) and their own tie-break, instead of each reimplementing
    this scan."""
    myTeam = ct.get_team()
    out: list[tuple[Position, EntityType]] = []
    for eid in ct.get_nearby_entities():
        if ct.get_team(eid) == myTeam:
            continue
        etype = ct.get_entity_type(eid)
        if etype not in priority:
            continue
        for tile in hostileTiles(etype, ct.get_position(eid)):
            out.append((tile, etype))
    return out


def _bestCandidate(here, candidates, priority, reachable):
    """Lowest priority rank wins; manhattan distance from `here` breaks ties.
    Returns (pos, etype) or None."""
    best = None
    bestKey = None
    for pos, etype in candidates:
        if not reachable(pos):
            continue
        key = (priority[etype], manhattan(here, pos))
        if bestKey is None or key < bestKey:
            best, bestKey = (pos, etype), key
    return best


# --- Live loops -------------------------------------------------------------

# A gunner with an empty arc — no target worth shooting in range — for this many
# rounds is on a dead position: the harvester it was placed to suppress is gone,
# or nothing ever crossed its arc. It still holds a slot and draws ammo top-up
# (topUpAmmo banks a reload per built turret), so recycle it rather than leave it
# parked; the raider drops a fresh one when a new harvester is worth suppressing.
# Keyed on target-in-range, not shots-fired, so an ammo-starved gunner sitting on
# a live target is NOT recycled (see gunnerLoop). Measured in engine rounds via
# get_current_round().
IDLE_SELF_DESTRUCT_ROUNDS = 50

# The sentinel counterpart, on a longer clock. A sentinel is the long-range core-
# siege investment (30 Ti vs a gunner's 20) and legitimately sits with an empty arc
# for long stretches between waves, so it ages out later than a gunner. Same
# rationale otherwise: a permanent turret holds a MAX_TEAM_UNITS slot and draws
# ammo top-up whether or not it ever fires, so a sentinel on a genuinely dead
# position (its target destroyed, or aimed at ground nothing crosses) is recycled.
# Keyed on a target on its *fixed arc*, not merely in vision: a sentinel cannot
# rotate, so a hostile it can never bear on must not keep it alive (this is the one
# deliberate difference from gunnerLoop, whose visible-keyed clock is correct only
# because a gunner rotates onto any visible target).
SENTINEL_IDLE_SELF_DESTRUCT_ROUNDS = 150


def gunnerLoop(bot, ct: Controller) -> None:
    """Fire before rotating — turning costs Ti and a tick, so the free option
    (something already on-facing) is always worth checking first."""
    round_ = ct.get_current_round()
    here = ct.get_position()
    hostiles = visibleHostiles(ct, GUNNER_PRIORITY)

    # Idle recycle, keyed on "a target worth shooting is in range" — NOT on
    # "fired this turn". Any turn a hostile is visible the position is live, so
    # the clock refreshes even if we can't shoot (empty ammo pool, or cooldown).
    # This is the whole point: a bad economy empties the ammo pool (topUpAmmo
    # converts nothing below AMMO_TI_RESERVE), muting every gunner at once — a
    # clock-on-fire would then self-destruct the entire turret layer in a wave
    # IDLE_SELF_DESTRUCT_ROUNDS later, exactly when we can least afford to lose
    # it. Only a genuinely empty arc for that many rounds recycles now. Seeded at
    # birth so a never-targeted gunner still ages out. Checked ahead of the
    # cooldown gate — self_destruct needs no action and costs no cooldown, so a
    # mute gunner ages out regardless.
    if bot._gunnerLastActive is None or hostiles:
        bot._gunnerLastActive = round_
    elif round_ - bot._gunnerLastActive >= IDLE_SELF_DESTRUCT_ROUNDS:
        if debug.ENABLED:
            debug.log("gun", f"idle self-destruct at {here} "
                             f"round={round_} lastActive={bot._gunnerLastActive}")
        ct.self_destruct()
        return

    if ct.get_action_cooldown() != 0:
        return

    onFacing = _bestCandidate(here, hostiles, GUNNER_PRIORITY, ct.can_fire)
    if onFacing is not None:
        ct.fire(onFacing[0])
        return

    if ct.get_global_resources() < GUNNER_ROTATE_MIN_TI:
        return

    # can_fire accounts for ammo; can_fire_from explicitly does not. Without
    # this the empty-pool case — guaranteed early, since topUpAmmo waits for
    # the full builder opening — has the gunner paying 10 Ti a turn to keep
    # turning toward targets it has nothing to shoot at.
    if ct.get_global_ammo() < GameConstants.GUNNER_AMMO_COST:
        return

    facing = ct.get_direction()
    bestDir = None
    bestKey = None
    for d in _DIRS:
        if d == facing:
            continue  # already tried above, and it just failed
        pick = _bestCandidate(
            here, hostiles, GUNNER_PRIORITY,
            lambda pos, d=d: ct.can_fire_from(here, d, EntityType.GUNNER, pos),
        )
        if pick is None:
            continue
        pos, etype = pick
        key = (GUNNER_PRIORITY[etype], manhattan(here, pos))
        if bestKey is None or key < bestKey:
            bestDir, bestKey = d, key

    if bestDir is not None and ct.can_rotate(bestDir):
        ct.rotate(bestDir)


def sentinelLoop(bot, ct: Controller) -> None:
    """No rotation — a sentinel's facing is permanent once built."""
    round_ = ct.get_current_round()
    here = ct.get_position()
    hostiles = visibleHostiles(ct, SENTINEL_PRIORITY)

    # Idle recycle, mirroring gunnerLoop on the longer SENTINEL_IDLE_SELF_DESTRUCT_
    # ROUNDS clock. "Active" is a target on the sentinel's fixed arc — an ammo- and
    # cooldown-independent can_fire_from against its current facing — NOT a target
    # merely in vision: a sentinel can't rotate, so an off-arc hostile is one it can
    # never engage and must not reset the clock. Keying on presence (not shots)
    # means an ammo-starved sentinel still bearing on a live target is not recycled,
    # so an empty ammo pool can't wave-destruct the whole ring (see gunnerLoop).
    # Seeded at birth so a never-targeted sentinel still ages out; checked ahead of
    # the cooldown gate because self_destruct needs no action and no cooldown.
    facing = ct.get_direction()
    onArc = _bestCandidate(
        here, hostiles, SENTINEL_PRIORITY,
        lambda p: ct.can_fire_from(here, facing, EntityType.SENTINEL, p),
    )
    if bot._sentinelLastActive is None or onArc is not None:
        bot._sentinelLastActive = round_
    elif round_ - bot._sentinelLastActive >= SENTINEL_IDLE_SELF_DESTRUCT_ROUNDS:
        if debug.ENABLED:
            debug.log("gun", f"sentinel idle self-destruct at {here} "
                             f"round={round_} lastActive={bot._sentinelLastActive}")
        ct.self_destruct()
        return

    if ct.get_action_cooldown() != 0:
        return
    target = _bestCandidate(here, hostiles, SENTINEL_PRIORITY, ct.can_fire)
    if target is not None:
        ct.fire(target[0])


# --- Gunner placement -------------------------------------------------------
# Re-derived against a real caller, the way the deleted board-wide search could
# not be: the raider drops a gunner on an *adjacent* pad from which it can hit a
# nearby enemy harvester. Gunners are cheap (10 Ti), tough (40 HP) and
# short-range (r**2 = 13) — the close-in harvester-denial tool, complementing the
# raider's long-range sentinels aimed at the core itself.

GUNNER_RANGE_SQ = GameConstants.GUNNER_VISION_RADIUS_SQ

# What a raider drops a gunner on: enemy harvesters (deny the supply) and enemy
# gunners/sentinels (answer a turret that's shooting us and our investments). Belts
# are the raider's job to cut and barrier (roles/raidBot.siege), not to gun, so
# they're deliberately absent. All three are GUNNER_PRIORITY targets, so gunnerLoop
# keeps firing on whichever one it's placed against.
_GUN_TARGET_TYPES = (EntityType.HARVESTER, EntityType.GUNNER, EntityType.SENTINEL)
_GUN_TURRET_TYPES = (EntityType.GUNNER, EntityType.SENTINEL)

# A harvester within this Manhattan range of the enemy core is feeding it directly,
# so sniping it is top-value supply denial and outranks answering a turret. Past it a
# harvester is ordinary economic denial and ranks below a live turret threat. Without
# this tier a turret ring around the enemy core fills the probe cap and the core's own
# harvesters never get probed. Measured to the 2x2 footprint edge (ph.manhattanToCore).
_CORE_SNIPE_RADIUS = 5

# Cap on targets probed per turn. The raider runs near the 10ms limit, so the
# scan is bounded rather than exhaustive — nearest few are the ones in reach.
_GUN_TARGET_LIMIT = 3


def _gunFacing(pad: Position, target: Position) -> Direction | None:
    """The facing a gunner on `pad` needs to bear on `target`, or None if the
    target isn't on a straight line within range. Geometry pre-check so we spend
    one `can_fire_from` (the LoS oracle) per pad, not eight."""
    dx = target.x - pad.x
    dy = target.y - pad.y
    if dx == 0 and dy == 0:
        return None
    if not (dx == 0 or dy == 0 or abs(dx) == abs(dy)):
        return None  # not on a cardinal or perfect-diagonal ray
    if pad.distance_squared(target) > GUNNER_RANGE_SQ:
        return None
    return pad.direction_to(target)


def _coveredByAllyGunner(ct: Controller, myTeam, target: Position) -> bool:
    """Is an ally gunner already in range of `target` — don't stack a second."""
    for bid in ct.get_nearby_buildings():
        if ct.get_team(bid) != myTeam:
            continue
        if ct.get_entity_type(bid) != EntityType.GUNNER:
            continue
        if ct.get_position(bid).distance_squared(target) <= GUNNER_RANGE_SQ:
            return True
    return False


def _dropGunnerOn(ct: Controller, target: Position) -> bool:
    """Build a gunner on the first orthogonal pad of the calling unit that bears on
    `target`, unless an ally gunner already covers it. True iff it built one.

    A pure pad-scan: the target set and the economy gate are the caller's to
    decide, so the same scan serves both harvester denial and home defence.

    A pad already covered by a visible enemy turret's current facing is skipped:
    bearing on the target is not enough if the turret we're answering (or one
    beside it) shoots the pad the moment the gunner appears. `turrets` is fetched
    lazily — most turns no pad bears at all, so the scan shouldn't be paid before
    it's needed."""
    myTeam = ct.get_team()
    if _coveredByAllyGunner(ct, myTeam, target):
        return False
    here = ct.get_position()
    turrets = None
    losCache: dict = {}
    for pad in ph.orthogonalTiles(here):
        facing = _gunFacing(pad, target)
        if facing is None:
            continue
        if not ct.can_fire_from(pad, facing, EntityType.GUNNER, target):
            continue
        if turrets is None:
            turrets = emplace.enemyTurrets(ct)
        if emplace.siteInDirectTurretLos(ct, pad, turrets, cache=losCache):
            continue
        if ct.can_build_gunner(pad, facing):
            ct.build_gunner(pad, facing)
            if debug.ENABLED:
                debug.log(
                    "gun",
                    f"placed at {pad} facing {facing} -> {target} "
                    f"rnd={ct.get_current_round()}",
                )
            return True
    return False


def _coveredByAllySentinel(ct: Controller, myTeam, target: Position) -> bool:
    """Is an ally sentinel already firing on `target` — don't stack a second.

    A sentinel's facing is permanent (no rotate), so unlike a gunner it isn't
    enough to be in range: it must actually *bear* on the tile. Read the real
    facing and coverage from the engine, the same way pathfindingHelpers builds
    its threat set (get_direction -> get_attackable_tiles_from)."""
    tkey = (target.x, target.y)
    for bid in ct.get_nearby_buildings():
        if ct.get_team(bid) != myTeam:
            continue
        if ct.get_entity_type(bid) != EntityType.SENTINEL:
            continue
        pos = ct.get_position(bid)
        facing = ct.get_direction(bid)
        for tile in ct.get_attackable_tiles_from(pos, facing, EntityType.SENTINEL):
            if (tile.x, tile.y) == tkey:
                return True
    return False


def _dropSentinelOn(ct: Controller, target: Position) -> bool:
    """Build a sentinel on the first orthogonal pad of the calling unit whose
    permanent facing bears on `target`, unless an ally sentinel already covers it.
    True iff it built one.

    The sentinel counterpart to _dropGunnerOn: a sentinel out-ranges and out-
    trades an enemy sentinel where a gunner just feeds it a kill. The pad scan,
    the reach inversion and the enemy-turret veto are all
    `emplace.sentinelPadBearing` — the same call `raid.fallbackEmplacement`
    makes, differing only in the aim set (one threat tile here, the enemy core's
    footprint there). Our policy is just the two lines around it: don't stack on
    a target an ally sentinel already bears on, and build what comes back.

    The veto matters more here than anywhere: without it this reliably plants the
    counter-sentinel *in front of* the enemy sentinel it was called to answer —
    the whole point is to bear on that threat, and the pad that bears is often
    the one it already shoots."""
    myTeam = ct.get_team()
    if _coveredByAllySentinel(ct, myTeam, target):
        return False
    found = emplace.sentinelPadBearing(
        ct, ph.orthogonalTiles(ct.get_position()), (target,)
    )
    if found is None:
        return False
    pad, d = found
    ct.build_sentinel(pad, d)
    if debug.ENABLED:
        debug.log(
            "gun",
            f"home-defence sentinel at {pad} facing {d} -> {target} "
            f"rnd={ct.get_current_round()}",
        )
    return True


def placeGunner(bot, ct: Controller, enemyCore: Position | None) -> bool:
    """Opportunistically plant a gunner beside a nearby enemy harvester (deny the
    supply) or enemy turret (answer fire). Surplus-gated like all raider spending,
    so it never starves the economy to arm the raider. `enemyCore` is passed in (the
    caller already knows it) rather than imported, to dodge the raid->guns cycle.

    Cheap early-exits first (this runs every raider turn, and the raider is
    CPU-tight): must be able to build this turn, must be past the surplus gate,
    must afford it. Only then does it scan. Builds on the first adjacent pad from
    which a gunner bears on an uncovered target — `_dropGunnerOn` skips a target an
    ally gunner already covers (no stacking a second) and a pad an enemy turret
    already bears on (no free kill on the newborn), which together are the anti-spam.
    `gunnerLoop` aims and fires thereafter. Returns True iff it built one (consuming
    the turn).

    Targets rank in three tiers, nearest-first within each: (1) a harvester within
    _CORE_SNIPE_RADIUS of the enemy core — feeding it directly, so cutting it is the
    top-value snipe; (2) an enemy gunner/sentinel actively hurting us; (3) a harvester
    elsewhere. The tiers stop a turret ring around the enemy core from crowding the
    core's own harvesters off the probe cap, while a live turret still outranks an
    ordinary far harvester.
    """
    if ct.get_action_cooldown() != 0:
        return False
    if not surplusReady(ct):
        return False
    if ct.get_global_resources() < ct.get_gunner_cost():
        return False

    myTeam = ct.get_team()
    here = ct.get_position()
    targets: list[tuple[Position, EntityType]] = []
    for eid in ct.get_nearby_entities():
        if ct.get_team(eid) == myTeam:
            continue
        etype = ct.get_entity_type(eid)
        if etype not in _GUN_TARGET_TYPES:
            continue
        targets.append((ct.get_position(eid), etype))
    if not targets:
        return False

    def rank(pt: tuple[Position, EntityType]) -> tuple[int, int]:
        pos, etype = pt
        if etype in _GUN_TURRET_TYPES:
            group = 1
        elif enemyCore is not None and ph.manhattanToCore(pos, enemyCore) <= _CORE_SNIPE_RADIUS:
            group = 0
        else:
            group = 2
        return group, manhattan(here, pos)

    targets.sort(key=rank)

    for target, _etype in targets[:_GUN_TARGET_LIMIT]:
        if _dropGunnerOn(ct, target):
            return True
    return False


# The enemy-fire line-of-sight reads (`enemyGunners`, `tileUnderGunnerFire`,
# `enemyTurrets`, `siteInDirectTurretLos`) live in emplace.py now, together with
# the long note on why they must use `get_attackable_tiles_from` and not
# `can_fire_from`. They are mechanism shared with raid.py, which previously had
# to reach them through a function-level `import guns` to dodge a cycle.



# --- Home defence -----------------------------------------------------------
# First-cut guardian: an eco-bot near our own core drops a gunner on an enemy
# closing on it. Reactive and vision-limited — only a bot that can already see the
# threat responds, and it never abandons a relay/build to march home (it sits below
# those in runEcoBot). What it buys is the mechanic: a rush at the core draws fire
# instead of nothing. gunnerLoop aims and fires the gunner after; the idle
# self-destruct recycles it once the threat clears.

# Mirrors ecoBot.CORE_DEFENSE_RADIUS by value, kept local so guns does not depend on
# a role module (raidBot depends on guns, not the other way).
CORE_THREAT_RADIUS = 8

# Enemy types that actually threaten the core — mobile attackers and turrets. A
# harvester or the enemy core never comes to kill ours, so neither counts.
_CORE_THREAT_TYPES = (
    EntityType.BUILDER_BOT,
    EntityType.GUNNER,
    EntityType.SENTINEL,
    EntityType.LAUNCHER,
)


def _nearestCoreThreat(bot, ct: Controller):
    """Nearest visible enemy attacker within CORE_THREAT_RADIUS of our core as
    (Position, EntityType), or None. Distance is to the core — the thing we protect
    — so the threat closest to home is engaged first. The type rides along so
    defendCore can answer a sentinel with a sentinel and everything else with a
    gunner."""
    core = bot.corePos
    if core is None:
        return None
    myTeam = ct.get_team()
    best = None
    bestType = None
    bestD = None
    for eid in ct.get_nearby_entities():
        if ct.get_team(eid) == myTeam:
            continue
        etype = ct.get_entity_type(eid)
        if etype not in _CORE_THREAT_TYPES:
            continue
        p = ct.get_position(eid)
        d = ph.manhattanToCore(p, core)
        if d > CORE_THREAT_RADIUS:
            continue
        if bestD is None or d < bestD:
            best, bestType, bestD = p, etype, d
    if best is None:
        return None
    return best, bestType


def _defenceAnswers(ttype):
    """Turret answers to try for a core threat of type `ttype`, best first.

    Each is a `(cost_of, place)` pair: `cost_of(ct)` is what the turret costs now,
    `place(ct, threat)` builds it on a pad bearing the threat this turn (returning
    False when nothing bears — its own pad scan decides that).

    A sentinel out-ranges and out-trades an enemy turret, so it leads against a
    gunner or sentinel; a gunner is cheaper and can retrack a moving target, so it
    leads against everything mobile. Whichever leads, the other rides as a fallback
    for one case only: the preferred turret has no pad bearing the threat this turn
    but the other one does. Answering "faster" means "gets a shot off now", so the
    fallback fires exactly when the lead's pad scan comes up empty.

    Only gunner/sentinel threats get the sentinel option; a builder or launcher rush
    is a gunner job (a permanent single-arc sentinel is wasted on a mobile body)."""
    gunner = (lambda ct: ct.get_gunner_cost(), _dropGunnerOn)
    sentinel = (lambda ct: ct.get_sentinel_cost(), _dropSentinelOn)
    if ttype == EntityType.SENTINEL:
        return (sentinel, gunner)
    if ttype == EntityType.GUNNER:
        return (gunner, sentinel)
    return (gunner,)


def defendCore(bot, ct: Controller) -> bool:
    """Home defence: drop a turret on an enemy closing on our core, or move into
    range so a pad bears on it. True if it acted this turn. An enemy gunner or
    sentinel is answered with whichever of our own turrets bears this turn — a
    sentinel preferred against a sentinel, a gunner against a gunner, the other as a
    fallback when the preferred one has no bearing pad this turn. Everything else
    (builder/launcher) draws a gunner.

    Gated on affordability only — defence deliberately bypasses the offensive surplus
    gate, because a core under attack is worth spending the bank down for. But if we
    can't afford any answer, or we're at the unit cap, we bail rather than idle
    beside the threat: neither changes by walking closer, so better to go earn.

    Reactive v1: fires only for a threat this builder can see, and never leaves a
    relay/build to run home (it sits below those in runEcoBot)."""
    if bot.corePos is None:
        return False
    found = _nearestCoreThreat(bot, ct)
    if found is None:
        return False
    threat, ttype = found

    answers = _defenceAnswers(ttype)

    # Broke for every answer, or at the unit cap: neither is fixed by closing in, so
    # leave (fall through to seekOre) rather than march home to idle beside a threat
    # we can't shoot. The cap check now covers both turret kinds — the gunner path
    # used to lean on can_build failing at the cap while the sentinel path bailed.
    if ct.get_unit_count() >= GameConstants.MAX_TEAM_UNITS:
        return False
    bank = ct.get_global_resources()
    if all(bank < cost_of(ct) for cost_of, _ in answers):
        return False

    if ct.get_action_cooldown() == 0:
        for cost_of, place in answers:
            if bank >= cost_of(ct) and place(ct, threat):
                return True

    # Affordable, under the cap, but nothing bore this turn (cooldown, or no pad
    # bears yet). Close in and hold station rather than fall through to seekOre and
    # wander off the threat. walkTo tracks a moving goal, so a shifting raider stays
    # covered. Gunner range is the conservative reach to close to — a sentinel out-
    # ranges it, so a pad still bears once we're this near.
    def inRange(p: Position) -> bool:
        return p.distance_squared(threat) <= GUNNER_RANGE_SQ

    if walkTo(bot, ct, inRange, threat, ("defend", threat.x, threat.y)):
        return True
    return holdTask(bot, "defendHold", BLOCKED_WAIT_TURNS)


# --- Ammo ---------------------------------------------------------------

# One reload banked per built turret, plus a floor for whatever isn't built
# yet. The per-turret numbers are the engine's own GUNNER_AMMO_COST/
# SENTINEL_AMMO_COST — enough for one shot each, not a stockpile.
AMMO_FLOOR = 10
AMMO_PER_GUNNER = GameConstants.GUNNER_AMMO_COST
AMMO_PER_SENTINEL = GameConstants.SENTINEL_AMMO_COST

# Never convert if it would draw the bank below this. Ammo is worthless if
# there's nothing left to build or repair with — 30 is a starting point, not
# measured yet.
AMMO_TI_RESERVE = 30


def _allyTurretCounts(ct: Controller, hm) -> tuple[int, int]:
    """Team-wide gunner/sentinel counts: what's currently visible, plus
    whatever the ledger remembers that isn't already counted — so a turret
    raised out of the core's own sight (the raid sentinel, built by the enemy
    core) still counts. Mirrors buildNetwork's live-then-ledger merge in
    pathfindingHelpers.py.

    Over-counts a turret after it dies — the ledger has no GC for buildings,
    same as the lifetime builder count in respawn.py — which is the safe
    direction for a floor: a little unspent Ti banked as ammo costs nothing.
    """
    myTeam = ct.get_team()
    seen: set[tuple[int, int]] = set()
    gunners = sentinels = 0

    for bid in ct.get_nearby_buildings():
        if ct.get_team(bid) != myTeam:
            continue
        etype = ct.get_entity_type(bid)
        if etype not in (EntityType.GUNNER, EntityType.SENTINEL):
            continue
        pos = ct.get_position(bid)
        seen.add((pos.x, pos.y))
        if etype == EntityType.GUNNER:
            gunners += 1
        else:
            sentinels += 1

    for (x, y), entry in hm.local_map.items():
        if (x, y) in seen:
            continue
        ent = entry.get("entity")
        if not ent or ent.get("team") != myTeam:
            continue
        etype = ent.get("type")
        if etype == EntityType.GUNNER:
            gunners += 1
        elif etype == EntityType.SENTINEL:
            sentinels += 1

    return gunners, sentinels


def topUpAmmo(bot, ct: Controller) -> None:
    """Keep the shared ammo pool at AMMO_FLOOR plus one reload per built
    turret. Core-only, per the engine — call once from _runCore.

    Held until the opening batch of builders is out (respawn.MAX_BUILDERS) so
    the gate tracks real state rather than a round number. By the time it
    opens, runCoreSpawn is already a no-op every turn (see respawn.py), so
    there is nothing competing with this for Ti — except this reserve, which
    stops it converting Ti we'd rather keep for a build or a repair. Gated,
    not clamped: below AMMO_TI_RESERVE we convert nothing this turn, not a
    partial amount down to the floor.
    """
    if ct.read_store(store.BUILDERS) < MAX_BUILDERS:
        return

    if ct.get_global_resources() <= AMMO_TI_RESERVE:
        return  # broke, not blocked — same reserve-first spirit as respawn.BUILDER_RESERVE

    gunners, sentinels = _allyTurretCounts(ct, bot.hivemind)
    target = AMMO_FLOOR + AMMO_PER_GUNNER * gunners + AMMO_PER_SENTINEL * sentinels
    need = target - ct.get_global_ammo()
    if need <= 0:
        return

    amount = min(need, ct.get_global_resources())
    if amount > 0 and ct.can_convert_ammo(amount):
        ct.convert_ammo(amount)
