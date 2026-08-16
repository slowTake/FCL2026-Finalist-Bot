"""Turret targeting: priority tables, the shared candidate gatherer, the live
fire/rotate loops for gunners and sentinels, and the core-side ammo top-up.

Both turret types trust the engine's own can_fire/can_fire_from as ground
truth for line of sight — a gunner's line is blocked by obstacles, a
sentinel's line ignores them, and the engine already knows which rule applies
to the unit asking. Re-deriving that with a manual ray-walk only creates a
second place for the two answers to disagree (see mistakes.md).

Placement lives in raid.py, not here. A generic gunner-pad search briefly
existed in this module and was deleted: `can_build_gunner` only answers for a
tile orthogonally adjacent to the *calling builder*, so a board-wide search
was structurally incapable of working, and its pad filter had already lost
the escape-neighbour rule raid.py paid for. Re-derive it against a real
caller rather than restoring it.

gunnerLoop is correct but currently unreachable: nothing builds a gunner, so
main.py has no GUNNER dispatch. Wiring is one branch plus a builder.
"""

from __future__ import annotations

from fcode import Controller, Direction, EntityType, GameConstants, Position

import pathfindingHelpers as ph
import store
from pathing import manhattan
from respawn import MAX_BUILDERS

_DIRS = tuple(d for d in Direction if d != Direction.CENTRE)

# Lower rank fires/rotates first. Equal ranks (the three turret types, in
# both tables) break on distance only — see _bestCandidate.
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

SENTINEL_PRIORITY: dict[EntityType, int] = {
    EntityType.BUILDER_BOT: 0,
    EntityType.CORE: 1,
    EntityType.HARVESTER: 2,
    EntityType.GUNNER: 3,
    EntityType.SENTINEL: 3,
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


def gunnerLoop(bot, ct: Controller) -> None:
    """Fire before rotating — turning costs Ti and a tick, so the free option
    (something already on-facing) is always worth checking first."""
    if ct.get_action_cooldown() != 0:
        return

    hostiles = visibleHostiles(ct, GUNNER_PRIORITY)
    here = ct.get_position()

    onFacing = _bestCandidate(here, hostiles, GUNNER_PRIORITY, ct.can_fire)
    if onFacing is not None:
        ct.fire(onFacing[0])
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
