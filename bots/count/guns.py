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
import pathfindingHelpers as ph
import raid
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

# rotate()'s own Ti cost — skip the 8-direction scan outright if we can't
# afford to act on whatever it finds.
_ROTATE_MIN_TI = GameConstants.GUNNER_ROTATE_COST


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

# A gunner that hasn't fired in this many rounds is on a dead position — the
# harvester it was placed to suppress is gone, or nothing ever crossed its arc.
# It still holds a slot and draws ammo top-up (topUpAmmo banks a reload per built
# turret), so recycle it rather than leave a mute turret parked; the raider drops
# a fresh one when a new harvester is worth suppressing. Measured in engine rounds
# via get_current_round().
IDLE_SELF_DESTRUCT_ROUNDS = 50


def gunnerLoop(bot, ct: Controller) -> None:
    """Fire before rotating — turning costs Ti and a tick, so the free option
    (something already on-facing) is always worth checking first."""
    round_ = ct.get_current_round()
    # Idle recycle. Seed the clock on the first loop (the gunner is born this
    # turn), then self-destruct once IDLE_SELF_DESTRUCT_ROUNDS pass with no shot.
    # Checked ahead of the cooldown gate so a mute gunner still ages out while on
    # cooldown; self_destruct needs no action and costs no cooldown.
    if bot._gunnerLastFired is None:
        bot._gunnerLastFired = round_
    elif round_ - bot._gunnerLastFired >= IDLE_SELF_DESTRUCT_ROUNDS:
        if debug.ENABLED:
            debug.log("gun", f"idle self-destruct at {ct.get_position()} "
                             f"round={round_} lastFired={bot._gunnerLastFired}")
        ct.self_destruct()
        return

    if ct.get_action_cooldown() != 0:
        return

    hostiles = visibleHostiles(ct, GUNNER_PRIORITY)
    here = ct.get_position()

    onFacing = _bestCandidate(here, hostiles, GUNNER_PRIORITY, ct.can_fire)
    if onFacing is not None:
        ct.fire(onFacing[0])
        bot._gunnerLastFired = round_
        return

    if ct.get_global_resources() < _ROTATE_MIN_TI:
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
    if ct.get_action_cooldown() != 0:
        return
    hostiles = visibleHostiles(ct, SENTINEL_PRIORITY)
    here = ct.get_position()
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

# What a gunner is placed to suppress: enemy harvesters — the source of the
# supply. Belts are the raider's job to cut and barrier (roles/raidBot.siege),
# not to gun, so they're deliberately absent here. HARVESTER is a GUNNER_PRIORITY
# target, so gunnerLoop keeps firing on it.
_GUN_TARGET_TYPES = (EntityType.HARVESTER,)

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

    The shared pad-scan behind both placeGunner (harvester denial) and defendCore
    (home defence); the callers differ only in which targets they pass and which
    economy gate they clear first.

    A pad already covered by a visible enemy turret's current facing is skipped:
    bearing on the target is not enough if the turret we're answering (or one
    beside it) shoots the pad the moment the gunner appears. `turrets` is fetched
    lazily — this runs every raider turn via placeGunner, and most turns no pad
    bears at all, so the scan shouldn't be paid before it's needed."""
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
            turrets = enemyTurrets(ct)
        if siteInDirectTurretLos(ct, pad, turrets, cache=losCache):
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
    trades an enemy sentinel where a gunner just feeds it a kill. Reach geometry
    comes from raid._reachOffsets (offsets a sentinel hits, per facing), inverted
    the same way raid._candidates picks emplacements — a sentinel on `pad` facing
    `d` hits `target` iff (target - pad) is one of `d`'s offsets. can_build_sentinel
    is the final legality gate, matching raidBot.buildSentinel.

    Pads already in a visible enemy turret's current line of fire are skipped, the
    same veto raidBot.buildSentinel and raid.fallbackEmplacement apply. Without it
    this function reliably plants the counter-sentinel *in front of* the enemy
    sentinel it was called to answer — the whole point here is to bear on that
    threat, and the pad that bears is often the one it already shoots."""
    myTeam = ct.get_team()
    if _coveredByAllySentinel(ct, myTeam, target):
        return False
    here = ct.get_position()
    reach = raid._reachOffsets(ct)  # eight engine calls, only on a live threat
    turrets = enemyTurrets(ct)
    losCache: dict = {}
    for pad in ph.orthogonalTiles(here):
        off = (target.x - pad.x, target.y - pad.y)
        for d, offsets in reach.items():
            if off not in offsets:
                continue
            if siteInDirectTurretLos(ct, pad, turrets, cache=losCache):
                break  # exposed pad; the bearing facing is unique, so try the next pad
            if ct.can_build_sentinel(pad, d):
                ct.build_sentinel(pad, d)
                if debug.ENABLED:
                    debug.log(
                        "gun",
                        f"home-defence sentinel at {pad} facing {d} -> {target} "
                        f"rnd={ct.get_current_round()}",
                    )
                return True
            break  # this facing is the only one that bears; pad is unbuildable
    return False


def placeGunner(bot, ct: Controller) -> bool:
    """Opportunistically plant a gunner to suppress a nearby enemy harvester.

    Cheap early-exits first (this runs every raider turn, and the raider is
    CPU-tight): must be able to build this turn, must be past the surplus gate,
    must afford it. Only then does it scan. Builds on the first adjacent pad from
    which a gunner bears on an uncovered target; `gunnerLoop` does the aiming and
    firing thereafter. Returns True iff it built one (consuming the turn).
    """
    if ct.get_action_cooldown() != 0:
        return False
    if not surplusReady(ct):
        return False
    if ct.get_global_resources() < ct.get_gunner_cost():
        return False

    myTeam = ct.get_team()
    here = ct.get_position()
    targets = [
        ct.get_position(eid)
        for eid in ct.get_nearby_entities()
        if ct.get_team(eid) != myTeam
        and ct.get_entity_type(eid) in _GUN_TARGET_TYPES
    ]
    if not targets:
        return False
    targets.sort(key=lambda p: manhattan(here, p))

    for target in targets[:_GUN_TARGET_LIMIT]:
        if _dropGunnerOn(ct, target):
            return True
    return False


# --- Enemy fire (line-of-sight reads) ---------------------------------------
# Two reads of enemy turret fire, both built on `get_attackable_tiles_from` —
# the turret's *raw attack pattern*.
#
# It must be that call and not `can_fire_from`, which is what this originally
# used and why the check silently did nothing: per the engine, `can_fire_from`
# answers "is this a legal target right now", and "for gunners, the target tile
# must be occupied". Every tile we ask about here is deliberately *empty* — an
# open pad we want to build a sentinel on, or an open tile we want to step onto
# — so the answer was always False and both vetoes were no-ops. Sentinels kept
# getting planted in front of gunners because nothing was actually checking.
#
# `get_attackable_tiles_from` instead "ignores ammo, cooldown, occupancy", and
# "for gunners this includes the full firing line within range" — which is the
# occupancy-independent question we mean: *would* a unit standing/built here be
# on this turret's line. Same primitive `pathfindingHelpers._threatTiles` and
# `_coveredByAllySentinel` already use.
#
# Trade-off, deliberate: the raw pattern reaches "even behind walls", so it does
# not model a blocker shielding the tile — the enemy-belt-as-cover nicety is
# gone, and a tile shielded by a building now reads as threatened. That is the
# safe direction (we decline a spot we might have gotten away with) and there is
# no occupancy-independent API that respects cover; deriving one means a manual
# ray-walk, which this module's docstring rules out.
#
# Both reads see only this unit's own vision: a raider can see short-range
# gunners but not the sentinels/launchers that out-range builder vision, which
# is why the siege body-check below is gunner-only while the placement check
# takes whatever turrets are actually visible.

# Cap on turrets folded into one coverage set — the raider runs near the 10ms
# limit and each turret costs an engine call. Mirrors _MAX_THREAT_TURRETS.
_MAX_LOS_TURRETS = 8


def _coverage(ct: Controller, turrets, cache: dict | None, key: str) -> set[tuple[int, int]]:
    """Union of the raw attack patterns of `turrets`, memoised in `cache`.

    One build per cache dict, so a siege turn that probes a dozen candidate tiles
    pays the engine calls once rather than per tile."""
    if cache is not None:
        hit = cache.get(key)
        if hit is not None:
            return hit
    out: set[tuple[int, int]] = set()
    for i, (tpos, facing, etype) in enumerate(turrets):
        if i >= _MAX_LOS_TURRETS:
            break
        try:
            for tile in ct.get_attackable_tiles_from(tpos, facing, etype):
                out.add((tile.x, tile.y))
        except Exception:
            continue
    if cache is not None:
        cache[key] = out
    return out


def enemyGunners(ct: Controller) -> list[tuple[Position, Direction]]:
    """Visible enemy gunners as (position, current facing). Engine order, so a
    caller that iterates this to a move/build decision stays deterministic."""
    myTeam = ct.get_team()
    out: list[tuple[Position, Direction]] = []
    for bid in ct.get_nearby_buildings():
        if ct.get_team(bid) == myTeam:
            continue
        if ct.get_entity_type(bid) != EntityType.GUNNER:
            continue
        out.append((ct.get_position(bid), ct.get_direction(bid)))
    return out


def tileUnderGunnerFire(
    ct: Controller,
    tile: Position,
    gunners: list[tuple[Position, Direction]],
    *,
    cache: dict | None = None,
) -> bool:
    """True if `tile` is on any of `gunners`' firing line at its *current* facing —
    the tile it could shoot without turning, whether or not anything stands there
    yet (see the section note: occupancy-independent, which `can_fire_from` is not).

    Current facing only, deliberately. Treating every tile a gunner could rotate
    onto as unsafe would blacklist its whole range disc, leaving the nearest safe
    tiles outside gunner range and driving the raider away from the ring — when
    the wanted behaviour is to dance just off the live line and keep sieging (the
    dodge memory in raidBot handles the gunner rotating to track).

    `cache` (optional) memoises the coverage set across the many candidate-tile
    checks one siege turn makes."""
    if not gunners:
        return False
    covered = cache.get("_gunnerCov") if cache is not None else None
    if covered is None:
        covered = _coverage(
            ct, [(p, f, EntityType.GUNNER) for p, f in gunners], cache, "_gunnerCov"
        )
    return (tile.x, tile.y) in covered


def enemyTurrets(ct: Controller) -> list[tuple[Position, Direction, EntityType]]:
    """Visible enemy turrets (gunner/sentinel/launcher) as (pos, facing, type),
    in engine order. For vetting a sentinel emplacement against a free shot."""
    myTeam = ct.get_team()
    out: list[tuple[Position, Direction, EntityType]] = []
    for bid in ct.get_nearby_buildings():
        if ct.get_team(bid) == myTeam:
            continue
        etype = ct.get_entity_type(bid)
        if etype not in ph.TURRET_TYPES:
            continue
        out.append((ct.get_position(bid), ct.get_direction(bid), etype))
    return out


def siteInDirectTurretLos(
    ct: Controller,
    site: Position,
    turrets: list[tuple[Position, Direction, EntityType]],
    *,
    cache: dict | None = None,
) -> bool:
    """True if `site` lies on any visible enemy turret's firing line at its
    *current* facing — a sentinel built here would be on that line the instant it
    appears, no rotation needed. Occupancy-independent, which is the whole point:
    the pad is empty when we ask (see the section note).

    Current facing only, by design: a stationary sentinel accepts that a nearby
    gun can rotate onto it later, or simply sits in the vicinity; what we refuse
    is handing it a free shot on the build."""
    if not turrets:
        return False
    covered = cache.get("_turretCov") if cache is not None else None
    if covered is None:
        covered = _coverage(ct, turrets, cache, "_turretCov")
    return (site.x, site.y) in covered


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


def defendCore(bot, ct: Controller) -> bool:
    """Home defence: drop a turret on an enemy closing on our core, or move into
    range so a pad bears on it. True if it acted this turn. A sentinel threat draws
    our own sentinel (it out-ranges and out-trades a gunner); everything else draws
    a gunner.

    Gated on affordability only — defence deliberately bypasses the offensive surplus
    gate, because a core under attack is worth spending the bank down for. But if we
    can't afford the turret at all we bail rather than idle beside the threat: better
    to go earn than sit there useless.

    Reactive v1: fires only for a threat this builder can see, and never leaves a
    relay/build to run home (it sits below those in runEcoBot)."""
    if bot.corePos is None:
        return False
    found = _nearestCoreThreat(bot, ct)
    if found is None:
        return False
    threat, ttype = found

    # A sentinel out-ranges and out-trades a gunner, so answer an enemy sentinel
    # with our own; every other threat still draws a gunner. Only the cost gate and
    # the placement call differ — the walk-and-hold tail below is shared.
    if ttype == EntityType.SENTINEL:
        if ct.get_global_resources() < ct.get_sentinel_cost():
            return False
        if ct.get_unit_count() >= GameConstants.MAX_TEAM_UNITS:
            return False
        if ct.get_action_cooldown() == 0 and _dropSentinelOn(ct, threat):
            return True
    else:
        if ct.get_global_resources() < ct.get_gunner_cost():
            return False
        if ct.get_action_cooldown() == 0 and _dropGunnerOn(ct, threat):
            if debug.ENABLED:
                debug.log("gun", f"home-defence gunner vs {threat} "
                                 f"rnd={ct.get_current_round()}")
            return True
    # Affordable but not placed (cooldown, or no pad bears yet). Close in and hold
    # station rather than fall through to seekOre and wander off the threat. walkTo
    # tracks a moving goal, so a shifting raider stays covered.
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
