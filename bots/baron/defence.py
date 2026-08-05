"""Home defence orchestrators — defGun, guardian clear/patrol."""

from __future__ import annotations

from fcode import Controller, EntityType, Position

from guns import _canAffordGunPlace, allyGunCovering, placeGun
from pathing import inBounds, walkPassable

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


def _pickDefGunTarget(ct: Controller) -> Position | None:
    """Nearest enemy gunner/sentinel/launcher (prefer) or builder in vision."""
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


def defGun(bot, ct: Controller) -> bool:
    """Near-core defensive gunner placement (gunners only — never sentinels)."""
    if bot.inRelay or getattr(bot, "recheckingOre", False):
        return False
    if not _nearCore(bot, ct):
        return False
    target = _pickDefGunTarget(ct)
    if target is None:
        return False
    return placeGun(bot, ct, target)


_TURRET_TYPES = (EntityType.GUNNER, EntityType.SENTINEL, EntityType.LAUNCHER)

# Which parked turret to answer first: the ones that out-range the leash.
_TURRET_PRIORITY = {
    EntityType.SENTINEL: 0,  # reach sqrt(32) ≈ 5.7 — shells the core from 5–6
    EntityType.LAUNCHER: 1,  # reach sqrt(26) ≈ 5.1
    EntityType.GUNNER: 2,  # reach sqrt(13) ≈ 3.6 — must come close anyway
}


def sortieEnabled() -> bool:
    from main import GUARDIAN_SORTIE_CHEBYSHEV

    return (
        GUARDIAN_SORTIE_CHEBYSHEV is not None and GUARDIAN_SORTIE_CHEBYSHEV > 0
    )


def _enemyTurretOnTile(ct: Controller, tile: Position) -> bool:
    if not inBounds(ct, tile):
        return False
    bid = ct.get_tile_building_id(tile)
    if bid is None:
        return False
    if ct.get_team(bid) == ct.get_team():
        return False
    return ct.get_entity_type(bid) in _TURRET_TYPES


def _knownEnemyTurrets(bot, ct: Controller) -> list[tuple[EntityType, Position]]:
    """Enemy turrets from the shared map, not just this unit's own vision.

    A builder sees sqrt(20) ≈ 4.5 tiles, so a guardian sitting on its patrol
    ring usually cannot see the sentinel that is shelling the core from 6 — the
    core (sqrt(36)) or a passing builder spots it and the HiveMind ledger
    carries it. Own vision is already folded into `local_map` by `tick`.
    """
    from main import TURRET_MEMORY_TURNS

    hm = bot.hivemind
    me = ct.get_team()
    memory = TURRET_MEMORY_TURNS or 0
    out: list[tuple[EntityType, Position]] = []
    for (x, y), cell in hm.local_map.items():
        ent = cell.get("entity")
        if not ent or ent.get("team") == me:
            continue
        if ent.get("type") not in _TURRET_TYPES:
            continue
        if memory > 0:
            age = hm.global_turn - cell.get("last_seen_turn", -(10**9))
            if age > memory:
                continue
        out.append((ent["type"], Position(x, y)))
    return out


def threateningTurret(bot, ct: Controller) -> Position | None:
    """Nearest enemy turret parked within range of home, worst type first."""
    from main import TURRET_THREAT_CHEBYSHEV

    core = bot.corePos
    if core is None or not TURRET_THREAT_CHEBYSHEV:
        return None
    best: Position | None = None
    best_key = None
    for etype, epos in _knownEnemyTurrets(bot, ct):
        d = _chebyshevToCore(epos, core)
        if d > TURRET_THREAT_CHEBYSHEV:
            continue
        key = (_TURRET_PRIORITY.get(etype, 3), d, epos.x, epos.y)
        if best_key is None or key < best_key:
            best_key = key
            best = epos
    return best


def refreshSortie(bot, ct: Controller) -> Position | None:
    """Maintain the sticky sortie target. State only — costs no turn.

    Dropped when the turret is confirmed gone, drifts out of threat range, an
    ally gunner already covers it, or we could not afford the counter-gun
    anyway — in each case the widened leash collapses and the guardian walks
    home on its own.
    """
    from main import TURRET_THREAT_CHEBYSHEV

    if not sortieEnabled() or bot.corePos is None:
        bot.sortieTarget = None
        return None
    if not _canAffordGunPlace(ct):
        bot.sortieTarget = None  # nothing to answer with; don't walk out to die
        return None

    target = getattr(bot, "sortieTarget", None)
    if target is not None:
        if _chebyshevToCore(target, bot.corePos) > TURRET_THREAT_CHEBYSHEV:
            target = None
        elif ct.is_in_vision(target) and not _enemyTurretOnTile(ct, target):
            target = None  # dead or never there
    if target is None:
        target = threateningTurret(bot, ct)
    if target is not None and allyGunCovering(ct, target):
        target = None  # someone already has the shot — hold the line at home
    bot.sortieTarget = target
    return target


def interceptTurret(bot, ct: Controller) -> bool:
    """Answer a turret parked outside the leash with a counter-gunner.

    `defGun` cannot: it only fires while the guardian itself is within the
    leash, and a gunner pad must be inside sqrt(13) ≈ 3.6 of the target, which
    for a sentinel at 5–6 is outside it. Left to `placeGun`'s default pad order
    the guardian is dragged onto the target and the leash step marches it back
    the next turn — it ping-pongs and the sentinel is never answered.
    """
    from main import GUARDIAN_SORTIE_CHEBYSHEV

    target = getattr(bot, "sortieTarget", None)
    if target is None or bot.corePos is None:
        return False

    from guns import placeGun

    if placeGun(
        bot,
        ct,
        target,
        anchor_core=bot.corePos,
        max_cheb=GUARDIAN_SORTIE_CHEBYSHEV,
    ):
        return True

    # No usable pad yet — normally because the pad tiles around a turret we only
    # know from the ledger are not in anyone's vision. Close in until they are.
    if _chebyshevToCore(ct.get_position(), bot.corePos) >= GUARDIAN_SORTIE_CHEBYSHEV:
        return False

    from logistics import moveToward

    return moveToward(bot, ct, target)


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
