"""Turret targeting: priority tables, the shared candidate gatherer, generic
gunner placement, and the live fire/rotate loops for gunners and sentinels.

Both turret types trust the engine's own can_fire/can_fire_from as ground
truth for line of sight — a gunner's line is blocked by obstacles, a
sentinel's line ignores them, and the engine already knows which rule applies
to the unit asking. Re-deriving that with a manual ray-walk only creates a
second place for the two answers to disagree (see mistakes.md).

raid.py's sentinel-vs-enemy-core placement is a separate, already-tuned path
for its one target and is untouched by this module. placeGunner here is the
generic form — any target, not just the enemy core — but nothing calls it yet
(see docs/roadmap.md): deciding when to spend Ti on a gunner and at what
target is a tactical policy question for a later pass.
"""

from __future__ import annotations

from fcode import Controller, Direction, EntityType, Position

import pathfindingHelpers as ph
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
_ROTATE_MIN_TI = 10


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


# --- Placement ----------------------------------------------------------


def _reachOffsets(ct: Controller, turret_type: EntityType):
    """Offsets from a hypothetical turret to every tile it could hit, per
    facing — read out of the engine rather than hand-derived, and sampled
    once from mid-map so an edge reference doesn't come back clipped. Same
    trick as raid.py's sentinel search, generalized to any turret type."""
    ref = Position(ct.get_map_width() // 2, ct.get_map_height() // 2)
    out: dict[Direction, list[tuple[int, int]]] = {}
    for d in _DIRS:
        try:
            tiles = ct.get_attackable_tiles_from(ref, d, turret_type)
        except Exception:
            continue
        out[d] = [(t.x - ref.x, t.y - ref.y) for t in tiles]
    return out


def chooseGunnerEmplacement(ct: Controller, hm, target: Position):
    """Nearest buildable, unobstructed pad from which a gunner would hit
    `target`, with the facing it has to be built pointing. None if there is
    no such pad. Pure search — no movement, no building, same split as
    raid.py's chooseEmplacement/publishTarget."""
    reach = _reachOffsets(ct, EntityType.GUNNER)
    best = None
    bestDist = None
    for d, offsets in reach.items():
        for ox, oy in offsets:
            pad = Position(target.x - ox, target.y - oy)
            if not ph.inBounds(ct, pad) or ph.isImpassable(ct, hm, pad):
                continue
            t = ph.readTile(ct, hm, pad)
            if t.etype is not None or t.ore:
                continue
            if not ct.can_build_gunner(pad, d):
                continue
            # Unlike a sentinel's line, a gunner's can be blocked by whatever
            # sits between pad and target — being in range by construction
            # isn't proof of a clear line, so this check (skippable in
            # raid.py's sentinel search) is the one thing that isn't.
            if not ct.can_fire_from(pad, d, EntityType.GUNNER, target):
                continue
            dist = manhattan(pad, target)
            if bestDist is None or dist < bestDist:
                best, bestDist = (pad, d), dist
    return best


def placeGunner(bot, ct: Controller, target: Position) -> bool:
    """Build a gunner able to hit `target`, if we're already standing next to
    the chosen pad this turn. Deciding when to call this, and getting a
    raider next to the right pad, is the caller's job — nothing does yet.
    Mirrors raid.py/roles/raidBot.py's split between choosing (search) and
    executing (walk + build), collapsed into one call since there's no
    store-published target for this to read yet."""
    if ct.get_action_cooldown() != 0:
        return False
    choice = chooseGunnerEmplacement(ct, bot.hivemind, target)
    if choice is None:
        return False
    pad, facing = choice
    if ph.beltFacing(ct.get_position(), pad) is None:
        return False  # not adjacent to the chosen pad this turn
    ct.build_gunner(pad, facing)
    return True


# --- Ammo ---------------------------------------------------------------

# One reload banked per built turret, plus a floor for whatever isn't built
# yet. The per-turret numbers are the engine's own GUNNER_AMMO_COST/
# SENTINEL_AMMO_COST — enough for one shot each, not a stockpile.
AMMO_FLOOR = 10
AMMO_PER_GUNNER = 4
AMMO_PER_SENTINEL = 10


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
    there is nothing competing with this for Ti.
    """
    if ct.read_store(4) < MAX_BUILDERS:
        return

    gunners, sentinels = _allyTurretCounts(ct, bot.hivemind)
    target = AMMO_FLOOR + AMMO_PER_GUNNER * gunners + AMMO_PER_SENTINEL * sentinels
    need = target - ct.get_global_ammo()
    if need <= 0:
        return

    amount = min(need, ct.get_global_resources())
    if amount > 0 and ct.can_convert_ammo(amount):
        ct.convert_ammo(amount)
