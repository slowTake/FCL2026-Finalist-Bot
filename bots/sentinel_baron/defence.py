"""Home defence orchestrators — defGun, guardian clear/patrol."""

from __future__ import annotations

from fcode import Controller, EntityType, Position

from guns import placeGun
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


from guns import placeGun, placeSentinel

def defGun(bot, ct: Controller) -> bool:
    """Disabled for 100% offensive attack focus."""
    return False


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
