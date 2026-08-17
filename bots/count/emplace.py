"""Turret emplacement geometry and enemy-fire reads — the *mechanism*, never the policy.

Neutral by construction: this module imports only `fcode`, `debug` and
`pathfindingHelpers`, all of which import nothing of ours. That is the whole
point. The three things below had each grown a second or third copy precisely
because the modules that needed them could not import each other:

* `reachOffsets` was `raid._reachOffsets`, and `guns._dropSentinelOn` reached
  across the module boundary for it through the *private* name.
* `enemyTurrets` / `siteInDirectTurretLos` lived in `guns`, and `raid` pulled
  them in with a function-level `import guns` to dodge a cycle.
* `sentinelPadBearing` is the pad-scan + offset-inversion loop that
  `raid.fallbackEmplacement` and `guns._dropSentinelOn` were each running
  their own copy of.

With them here, `guns` no longer imports `raid` at all and `raid` no longer
imports `guns`. (`guns -> respawn -> ecoBot -> guns` still exists, on
`respawn.MAX_BUILDERS` in `topUpAmmo` — a different edge, not this one.)

What deliberately does NOT live here is policy: *which* tiles are candidates
(the whole board vs the unit's own neighbours), how they rank (max standoff vs
nearest), whether an already-covered target should be skipped at all. Those
differ per caller for real reasons — see the module docstrings of `raid` and
`guns` — and they stay with their callers. Callers pass a pad list they have
already filtered however their policy requires.
"""

from __future__ import annotations

from fcode import Controller, Direction, EntityType, Position

import debug
import pathfindingHelpers as ph

# The eight facings a turret can hold, in a fixed order. `raid` imports this and
# indexes into it for the store's packed-target bit format, so the ORDER IS A
# WIRE FORMAT — appending is safe, reordering silently re-decodes every published
# emplacement as a different direction.
DIRS = (
    Direction.NORTH, Direction.NORTHEAST, Direction.EAST, Direction.SOUTHEAST,
    Direction.SOUTH, Direction.SOUTHWEST, Direction.WEST, Direction.NORTHWEST,
)


def reachOffsets(ct: Controller) -> dict:
    """Offsets from a sentinel to every tile it can hit, per facing.

    Read out of the engine rather than derived. r**2 <= 32 works out to 5 tiles
    orthogonally and 4 diagonally, and hand-deriving that is exactly the mistake
    this function exists to stop: ranking candidates by standoff without knowing
    the real reach puts the out-of-range ones first.

    Sampled from mid-map because `get_attackable_tiles_from` returns only in-bounds
    tiles, so a reference near an edge comes back clipped.
    """
    ref = Position(ct.get_map_width() // 2, ct.get_map_height() // 2)
    out = {}
    for d in DIRS:
        try:
            tiles = ct.get_attackable_tiles_from(ref, d, EntityType.SENTINEL)
        except Exception:
            continue
        out[d] = [(t.x - ref.x, t.y - ref.y) for t in tiles]
    return out


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
# `guns._coveredByAllySentinel` already use.
#
# Trade-off, deliberate: the raw pattern reaches "even behind walls", so it does
# not model a blocker shielding the tile — the enemy-belt-as-cover nicety is
# gone, and a tile shielded by a building now reads as threatened. That is the
# safe direction (we decline a spot we might have gotten away with) and there is
# no occupancy-independent API that respects cover; deriving one means a manual
# ray-walk, which guns.py's docstring rules out.
#
# Both reads see only this unit's own vision: a raider can see short-range
# gunners but not the sentinels/launchers that out-range builder vision, which
# is why raidBot's siege body-check is gunner-only while the placement check
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


# The *firing* turrets — TURRET_TYPES minus the launcher. Not the same set, and
# the difference is load-bearing here (see enemyTurrets).
_AIMED_TURRET_TYPES = (EntityType.GUNNER, EntityType.SENTINEL)


def enemyTurrets(ct: Controller) -> list[tuple[Position, Direction, EntityType]]:
    """Visible enemy *aimed* turrets (gunner/sentinel) as (pos, facing, type), in
    engine order. For vetting a sentinel emplacement against a free shot.

    Launchers are excluded, for two independent reasons — either one fatal:

    1) `get_direction` raises GameError on an entity with no facing, and a launcher
       has none. Every caller here runs inside `Player.run`'s bare except, which
       swallows the error without so much as a debug line, so the *whole turn* died
       — heal, siege, build, walk — silently, every turn an enemy launcher stood in
       vision. Measured on a drumlin mirror: raiders bricked from the exact round an
       enemy launcher appeared, standing still until they were shot to death, banks
       climbing past 650 Ti untouched. It reads as "the bot is idling", never as a
       crash, which is why it survived this long.
    2) Even guarded, a launcher does not belong in this set. It has no firing line;
       what it has is a throw-*landing* disc, which is harmless to stand in. Feeding
       it to `_coverage`/`get_attackable_tiles_from` would veto every pad in that
       disc and refuse emplacements that are perfectly safe. `ph._threatTiles`
       already documents this exact trap and special-cases launchers before asking
       for a direction — this function simply never got the same treatment.
    """
    myTeam = ct.get_team()
    out: list[tuple[Position, Direction, EntityType]] = []
    for bid in ct.get_nearby_buildings():
        if ct.get_team(bid) == myTeam:
            continue
        etype = ct.get_entity_type(bid)
        if etype not in _AIMED_TURRET_TYPES:
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


# --- The shared pad scan ----------------------------------------------------


def sentinelPadBearing(
    ct: Controller,
    pads,
    aims,
    *,
    reach: dict | None = None,
    turrets=None,
    losCache: dict | None = None,
):
    """First `(pad, facing)` among `pads` where a sentinel would bear on any tile
    in `aims`, the pad is not already in a visible enemy turret's line, and the
    engine will actually build it. None if nothing bears.

    Built by inverting the attack pattern: a sentinel on `pad` facing `d` hits
    `aim` iff `(aim - pad)` is one of `d`'s offsets. The same three gates in the
    same order serve both aim shapes — a 2x2 core footprint (four aim tiles) and a
    single threat tile — since `aims` is any iterable of Positions.

    Policy stays outside: filter `pads` before calling (to a unit's own buildable
    neighbours, minus any site already ruled out).

    The LoS veto is evaluated lazily — only once a facing actually bears from
    that pad — so a pad that could never have worked costs no engine calls. An
    exposed pad abandons its remaining facings entirely: the pad is the thing
    under fire, not the direction we would face from it.

    `can_build_sentinel` is probed with the facing we would really build, never a
    stand-in. Build legality is probably facing-independent, but "probably" is
    the word that earns a silently dead raider: an unchecked facing the engine
    refuses raises inside `build_sentinel`, `Player.run` swallows it, and the
    unit burns the rest of the match. A refused facing falls through to the next
    one rather than abandoning the pad — with a 2x2 footprint several facings can
    bear on different aim tiles.
    """
    if reach is None:
        reach = reachOffsets(ct)
    if turrets is None:
        turrets = enemyTurrets(ct)
    if losCache is None:
        losCache = {}

    hits = {d: set(offsets) for d, offsets in reach.items()}
    aimList = list(aims)

    for pad in pads:
        for d, offsets in hits.items():
            if not any((a.x - pad.x, a.y - pad.y) in offsets for a in aimList):
                continue
            if siteInDirectTurretLos(ct, pad, turrets, cache=losCache):
                if debug.ENABLED:
                    debug.log(
                        "emplace",
                        f"veto pad {pad} — in enemy turret line "
                        f"rnd={ct.get_current_round()}",
                    )
                break  # the pad is exposed; no facing from it is worth taking
            if ct.can_build_sentinel(pad, d):
                return pad, d
    return None
