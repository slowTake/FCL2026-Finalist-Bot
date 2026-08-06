"""Home defence orchestrators — defGun, guardian clear/patrol."""

from __future__ import annotations

from fcode import Controller, Direction, EntityType, Environment, Position

from guns import (
    allyGunCovering,
    allySentinelCovering,
    placeDefSentinel,
    placeGun,
)
from pathing import CARDINALS, inBounds, manhattan, walkPassable

DEFEND_HOME_CHEBYSHEV = 4
_PATROL_OFFSET = 2


def _coreFootprint(core_pos: Position) -> list[Position]:
    return [
        core_pos,
        Position(core_pos.x + 1, core_pos.y),
        Position(core_pos.x, core_pos.y + 1),
        Position(core_pos.x + 1, core_pos.y + 1),
    ]


def _chebyshevToCore(pos: Position, core_pos: Position) -> int:
    best = 10**9
    for tile in _coreFootprint(core_pos):
        d = max(abs(pos.x - tile.x), abs(pos.y - tile.y))
        if d < best:
            best = d
    return best


def _nearCore(bot, ct: Controller) -> bool:
    if bot.corePos is None:
        return False
    return _chebyshevToCore(ct.get_position(), bot.corePos) <= DEFEND_HOME_CHEBYSHEV


def _pickDefGunTarget(ct: Controller, core_pos: Position | None) -> Position | None:
    """Nearest enemy gunner/sentinel/launcher (prefer) or builder in vision.

    `core_pos` set (guardians, via `homeOnly`) restricts targets to the leash: a
    gunner pad must sit within sqrt(13) ≈ 3.6 of its target, so aiming further
    out sends the guardian to a pad the leash step then marches it back from —
    it ping-pongs and never places. Out-of-leash turrets are `defSentinel`'s
    job. Defaults pass None: they have no leash and can go finish the job.
    """
    me = ct.get_team()
    pos = ct.get_position()
    best_turret: Position | None = None
    best_turret_d = 10**9
    best_builder: Position | None = None
    best_builder_d = 10**9
    for eid in ct.get_nearby_units():
        if ct.get_team(eid) == me:
            continue
        etype = ct.get_entity_type(eid)
        epos = ct.get_position(eid)
        if (
            core_pos is not None
            and _chebyshevToCore(epos, core_pos) > DEFEND_HOME_CHEBYSHEV
        ):
            continue
        dist = abs(pos.x - epos.x) + abs(pos.y - epos.y)
        if etype in (EntityType.GUNNER, EntityType.SENTINEL, EntityType.LAUNCHER):
            if dist < best_turret_d:
                best_turret_d = dist
                best_turret = epos
        elif etype == EntityType.BUILDER_BOT:
            if dist < best_builder_d:
                best_builder_d = dist
                best_builder = epos
    if best_turret is not None:
        return best_turret
    return best_builder


def defGun(bot, ct: Controller, *, homeOnly: bool = False) -> bool:
    """Near-core defensive gunner placement (gunners only — never sentinels).

    `homeOnly` forbids any action that would need the caller to leave the leash
    — guardians pass it, defaults do not.
    """
    if bot.inRelay or getattr(bot, "recheckingOre", False):
        return False
    if not _nearCore(bot, ct):
        return False
    target = _pickDefGunTarget(ct, bot.corePos if homeOnly else None)
    if target is None:
        return False
    return placeGun(bot, ct, target)


_TURRET_TYPES = (EntityType.GUNNER, EntityType.SENTINEL, EntityType.LAUNCHER)

# Which parked turret to answer first: the ones that out-range the leash.
_TURRET_PRIORITY = {
    EntityType.SENTINEL: 0,  # reach sqrt(32) ≈ 5.7 — shells the core from 5–6
    EntityType.LAUNCHER: 1,  # reach sqrt(26) ≈ 5.1
    EntityType.GUNNER: 2,  # reach sqrt(13) ≈ 3.6 — defGun already covers these
}


def threateningTurret(bot, ct: Controller) -> Position | None:
    """Enemy turret parked within threat range of home, worst out-ranger first.

    Reads the HiveMind ledger rather than this unit's own vision: a builder sees
    sqrt(20) ≈ 4.5, so a guardian on its patrol ring usually cannot see the
    sentinel shelling the core from 6 — the core (sqrt(36)) or a passing builder
    spots it and the ledger carries it. Own vision is folded into `local_map` by
    `tick`, so this covers both.
    """
    from main import TURRET_MEMORY_TURNS, TURRET_THREAT_CHEBYSHEV

    core = bot.corePos
    if core is None or not TURRET_THREAT_CHEBYSHEV:
        return None
    hm = bot.hivemind
    me = ct.get_team()
    memory = TURRET_MEMORY_TURNS or 0
    best: Position | None = None
    best_key = None
    for (x, y), cell in hm.local_map.items():
        ent = cell.get("entity")
        if not ent or ent.get("team") == me:
            continue
        etype = ent.get("type")
        if etype not in _TURRET_TYPES:
            continue
        if memory > 0:
            age = hm.global_turn - cell.get("last_seen_turn", -(10**9))
            if age > memory:
                continue
        epos = Position(x, y)
        d = _chebyshevToCore(epos, core)
        if d > TURRET_THREAT_CHEBYSHEV:
            continue
        key = (_TURRET_PRIORITY.get(etype, 3), d, x, y)
        if best_key is None or key < best_key:
            best_key = key
            best = epos
    return best


def defSentinel(bot, ct: Controller) -> bool:
    """Answer an out-of-leash turret with our own sentinel, from inside the leash.

    Sits below `defGun` — anything close enough for a gunner is cheaper to
    answer that way — but above `defendHome`, whose no-enemy branch heals the
    core. Healing loses that trade: a sentinel hits for 18 and core heal is
    4 HP/turn, so killing it is the defence.
    """
    from main import DEF_SENTINEL

    if not DEF_SENTINEL or bot.corePos is None:
        return False
    if bot.inRelay or getattr(bot, "recheckingOre", False):
        return False
    if not _nearCore(bot, ct):
        return False
    target = threateningTurret(bot, ct)
    if target is None:
        return False
    if allySentinelCovering(ct, target) or allyGunCovering(ct, target):
        return False  # already answered — don't stack turrets on one threat
    return placeDefSentinel(
        bot, ct, target, bot.corePos, DEFEND_HOME_CHEBYSHEV
    )


def clearBarriers(bot, ct: Controller) -> bool:
    """Attack nearest enemy barrier inside the guardian leash.

    Barriers beyond the leash are ignored on purpose: walking out to one puts the
    guardian outside Chebyshev 4, so `runGuardian`'s leash step marches it home
    next turn and it ping-pongs forever without ever patrolling. Distant barriers
    are the harassers' problem.
    """
    me = ct.get_team()
    pos = ct.get_position()
    core = bot.corePos
    best: Position | None = None
    best_d = 10**9
    for eid in ct.get_nearby_entities():
        if ct.get_team(eid) == me:
            continue
        if ct.get_entity_type(eid) != EntityType.BARRIER:
            continue
        epos = ct.get_position(eid)
        if core is not None and _chebyshevToCore(epos, core) > DEFEND_HOME_CHEBYSHEV:
            continue
        dist = abs(pos.x - epos.x) + abs(pos.y - epos.y)
        if dist < best_d:
            best_d = dist
            best = epos
    if best is None:
        return False

    if abs(pos.x - best.x) + abs(pos.y - best.y) == 1:
        if ct.get_action_cooldown() == 0 and ct.can_fire(best):
            ct.fire(best)
            return True
        return False

    from logistics import moveToward

    return moveToward(bot, ct, best)


_BELT_TYPES = (EntityType.CONVEYOR, EntityType.SPLITTER)


def _isBeltOrCore(bot, ct: Controller, tile: Position, core: Position) -> bool:
    """True if tile carries an ally belt or is part of the ally core footprint."""
    for foot in _coreFootprint(core):
        if tile.x == foot.x and tile.y == foot.y:
            return True
    me = ct.get_team()
    if ct.is_in_vision(tile):
        bid = ct.get_tile_building_id(tile)
        if bid is None:
            return False
        return (
            ct.get_entity_type(bid) in _BELT_TYPES and ct.get_team(bid) == me
        )
    cell = bot.hivemind.local_map.get((tile.x, tile.y))
    ent = cell.get("entity") if cell else None
    if not ent or ent.get("team") != me:
        return False
    return ent.get("type") in _BELT_TYPES


_RELAY_OWNER_CHEBYSHEV = 2


def _layableTile(ct: Controller, tile: Position) -> bool:
    """Empty ground a belt could be built on, confirmed by vision."""
    if not ct.is_in_vision(tile):
        return False
    if ct.get_tile_building_id(tile) is not None:
        return False
    return ct.get_tile_env(tile) == Environment.EMPTY


def _allyBuilderNear(ct: Controller, tile: Position) -> bool:
    """Visible ally builder (not self) within `_RELAY_OWNER_CHEBYSHEV` of tile.

    A relaying default owns a legitimately dangling head — its next belt simply is
    not laid yet — and must not have it patched out from under it. Cardinal
    adjacency was too tight: `relayToCore` steps the builder *onto* the belt it just
    placed and falls back to `moveToward` when the next path tile is distant, so the
    owner is regularly two or more tiles from its own head.

    Scans units rather than tiles: O(visible units) instead of O(radius squared).
    """
    me = ct.get_team()
    self_id = ct.get_id()
    for eid in ct.get_nearby_units():
        if eid == self_id or ct.get_team(eid) != me:
            continue
        if ct.get_entity_type(eid) != EntityType.BUILDER_BOT:
            continue
        p = ct.get_position(eid)
        if max(abs(p.x - tile.x), abs(p.y - tile.y)) <= _RELAY_OWNER_CHEBYSHEV:
            return True
    return False


def _danglingBeltEnds(bot, ct: Controller) -> list[tuple[Position, Position]]:
    """Ally conveyors inside the leash whose output tile leads nowhere.

    Returns `(belt, output)` pairs, nearest-core first — the most downstream break
    reconnects the most line. Detection reads the HiveMind ledger, not own vision,
    for the same reason `threateningTurret` does: a guardian on its patrol ring
    cannot see the whole near-core belt run at sqrt(20).

    Conveyors only. A splitter rotates its output among three directions, so
    "the way it points" does not identify a break.
    """
    from logistics import tileThreatenedByGunner

    core = bot.corePos
    if core is None:
        return []
    me = ct.get_team()
    pos = ct.get_position()
    out: list[tuple[int, int, Position, Position]] = []
    for (x, y), cell in bot.hivemind.local_map.items():
        ent = cell.get("entity")
        if not ent or ent.get("team") != me:
            continue
        if ent.get("type") != EntityType.CONVEYOR:
            continue
        facing = ent.get("direction")
        if facing is None or facing == Direction.CENTRE:
            continue
        belt = Position(x, y)
        d = _chebyshevToCore(belt, core)
        if d > DEFEND_HOME_CHEBYSHEV:
            continue
        target = belt.add(facing)
        if not inBounds(ct, target):
            continue
        # Already feeding a belt or the core — not a break.
        if _isBeltOrCore(bot, ct, target, core):
            continue
        # Layable only. A belt aimed at a wall or a building is wrong-facing,
        # which is clearWrongBelt's problem, not a cut line.
        if not _layableTile(ct, target):
            continue
        # A default mid-relay has a legitimately dangling head — leave it alone.
        if _allyBuilderNear(ct, target):
            continue
        if tileThreatenedByGunner(ct, target):
            continue
        out.append((d, manhattan(pos, target), belt, target))
    out.sort(key=lambda t: (t[0], t[1]))
    return [(belt, target) for _d, _m, belt, target in out]


def _reconnectFacing(
    bot, ct: Controller, target: Position, core: Position
) -> Direction | None:
    """Provable next hop for a patch belt, or None when it cannot be proven.

    Two lookaheads, both of which *see* the far side of the gap:

    * **1 tile** — a cardinal neighbour closer to the core already holds a belt or
      the core footprint. Face it; the gap closes exactly.
    * **2 tiles** — that neighbour is empty but layable, and the tile beyond it in
      the same direction reconnects. Face it anyway: we are filling the first of
      two, and the second is patched on a later turn from the same evidence.

    **There is deliberately no fallback.** `facingIntoCore` used to fill this role,
    which is a crow-flies heading at the core anchor with no route behind it. The
    belt it produced pointed at another empty tile, so it became a dangling end
    itself next turn and the guardian marched a line of belts at the core one per
    turn — and because those belts agree with no route, whatever `relayToCore` later
    planned disagreed and `clearWrongBelt` tore them out again. Where the hop cannot
    be proven, build nothing: `maintainHarvester` → `relayToCore` owns that repair
    and has an actual path to work from.
    """
    from logistics import travelFacing

    here = _chebyshevToCore(target, core)

    for d in CARDINALS:
        near = target.add(d)
        if not inBounds(ct, near):
            continue
        if _chebyshevToCore(near, core) >= here:
            continue
        if _isBeltOrCore(bot, ct, near, core):
            return travelFacing(target, near)

    for d in CARDINALS:
        near = target.add(d)
        far = near.add(d)
        if not inBounds(ct, near) or not inBounds(ct, far):
            continue
        if _chebyshevToCore(far, core) >= here:
            continue
        if not _layableTile(ct, near):
            continue
        if _isBeltOrCore(bot, ct, far, core):
            return travelFacing(target, near)

    return None


def reconnectBelts(bot, ct: Controller) -> bool:
    """Patch a cut belt line near the core back toward the core, one tile a turn.

    Guardian-only idle work, gated with the economy on `not underAttack`. Exists
    because the normal repair path is *ore-anchored*: `maintainHarvester` re-enters
    `relayToCore` and the watermark rebuilds from the first gap, but a guardian's
    `_allowOreSite` confines it to ore inside the leash. A break near the core that
    belongs to a harvester outside the leash is therefore unreachable — the guardian
    can never claim the ore that would carry it there. Anchoring on the belt instead
    of the ore is the whole point.

    Greedy and deliberately unplanned: no A*, so a blocked coreward tile cannot be
    routed around. Candidates are tried in order and a failed placement falls through
    to the next rather than retrying, so it cannot thrash. Multi-tile gaps are walked
    one tile per turn and only while each step stays layable.
    """
    from logistics import moveToward, placeRelayBelt

    core = bot.corePos
    if core is None or bot.inRelay or getattr(bot, "recheckingOre", False):
        return False

    candidates = _danglingBeltEnds(bot, ct)
    if not candidates:
        return False

    # Resolve the facing before committing to anything, including the walk: a break
    # whose next hop cannot be proven is not ours to fix, so it must not draw the
    # guardian across the leash either.
    actionable: list[tuple[Position, Direction]] = []
    for _belt, target in candidates:
        facing = _reconnectFacing(bot, ct, target, core)
        if facing is None:
            continue
        actionable.append((target, facing))
    if not actionable:
        return False

    pos = ct.get_position()
    for target, facing in actionable:
        if abs(pos.x - target.x) + abs(pos.y - target.y) != 1:
            continue
        if placeRelayBelt(ct, target, facing) == "placed":
            return True

    # Nothing adjacent to act on — walk to the most downstream provable break.
    return moveToward(bot, ct, actionable[0][0])


def _patrolWaypoints(bot, ct: Controller) -> list[Position]:
    """Four mid-side tiles around the 2x2 footprint at patrol offset."""
    core = bot.corePos
    if core is None:
        return []
    o = _PATROL_OFFSET
    raw = [
        Position(core.x, core.y - o),  # N
        Position(core.x + 1 + o, core.y),  # E
        Position(core.x, core.y + 1 + o),  # S
        Position(core.x - o, core.y),  # W
    ]
    local_map = bot.hivemind.local_map
    out: list[Position] = []
    for tile in raw:
        if not inBounds(ct, tile):
            continue
        if not walkPassable(ct, tile, local_map=local_map):
            continue
        out.append(tile)
    return out


def patrolHome(bot, ct: Controller) -> bool:
    """Cycle leash waypoints around ally core for rear vision coverage."""
    if bot.corePos is None:
        return False
    waypoints = _patrolWaypoints(bot, ct)
    if not waypoints:
        return False

    idx = getattr(bot, "patrolIdx", 0) % len(waypoints)
    target = waypoints[idx]
    pos = ct.get_position()
    if pos.x == target.x and pos.y == target.y:
        bot.patrolIdx = (idx + 1) % len(waypoints)
        target = waypoints[bot.patrolIdx]
    elif getattr(bot, "stuck", 0) >= 3:
        bot.patrolIdx = (idx + 1) % len(waypoints)
        target = waypoints[bot.patrolIdx]

    from logistics import moveToward

    return moveToward(bot, ct, target)


def run_phalanx(bot, ct: Controller) -> None:
    del bot, ct


def update_phalanx_builder_threat(
    ct: Controller,
    *,
    sticky_until: int,
    last_side: int,
) -> tuple[int, int]:
    del ct
    return sticky_until, last_side
