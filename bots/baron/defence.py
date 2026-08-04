"""Home defence orchestrators — defGun and related gates."""

from __future__ import annotations

from fcode import Controller, EntityType, Position

from guns import placeGun

DEFEND_HOME_CHEBYSHEV = 8


def _coreFootprint(core_pos: Position) -> list[Position]:
    return [
        core_pos,
        Position(core_pos.x + 1, core_pos.y),
        Position(core_pos.x, core_pos.y + 1),
        Position(core_pos.x + 1, core_pos.y + 1),
    ]


def _nearCore(bot, ct: Controller) -> bool:
    if bot.corePos is None:
        return False
    pos = ct.get_position()
    best = 10**9
    for tile in _coreFootprint(bot.corePos):
        d = max(abs(pos.x - tile.x), abs(pos.y - tile.y))
        if d < best:
            best = d
    return best <= DEFEND_HOME_CHEBYSHEV


def _pickDefGunTarget(ct: Controller) -> Position | None:
    """Nearest enemy gunner/sentinel (prefer) or builder in vision."""
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
        if etype in (EntityType.GUNNER, EntityType.SENTINEL):
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
