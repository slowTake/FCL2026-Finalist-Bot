
from __future__ import annotations

from fcode import Controller, Direction, EntityType, Position

DEFEND_HOME_CHEBYSHEV = 4

BELT_TYPES = (EntityType.CONVEYOR, EntityType.SPLITTER)

TURRET_TYPES = (EntityType.GUNNER, EntityType.SENTINEL, EntityType.LAUNCHER)

_CARDINALS = (Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST)


def _inBounds(ct: Controller, pos: Position) -> bool:
    return 0 <= pos.x < ct.get_map_width() and 0 <= pos.y < ct.get_map_height()


def coreFootprint(core_pos: Position) -> list[Position]:
    return [
        core_pos,
        Position(core_pos.x + 1, core_pos.y),
        Position(core_pos.x, core_pos.y + 1),
        Position(core_pos.x + 1, core_pos.y + 1),
    ]


def chebyshevToCore(pos: Position, core_pos: Position) -> int:
    return min(
        max(abs(pos.x - t.x), abs(pos.y - t.y)) for t in coreFootprint(core_pos)
    )


def manhattanToCore(pos: Position, core_pos: Position) -> int:
    return min(
        abs(pos.x - t.x) + abs(pos.y - t.y) for t in coreFootprint(core_pos)
    )


def nearCore(bot, ct: Controller, radius: int | None = None) -> bool:
    if bot.corePos is None:
        return False
    if radius is None:
        radius = DEFEND_HOME_CHEBYSHEV
    return chebyshevToCore(ct.get_position(), bot.corePos) <= radius


def isAdjacent(a: Position, b: Position) -> bool:
    return abs(a.x - b.x) + abs(a.y - b.y) == 1


def tileEntity(
    ct: Controller, tile: Position, *, local_map: dict | None = None
) -> dict | None:
    if ct.is_in_vision(tile):
        bid = ct.get_tile_building_id(tile)
        if bid is None:
            return None
        return {"type": ct.get_entity_type(bid), "team": ct.get_team(bid)}
    if local_map is None:
        return None
    cell = local_map.get((tile.x, tile.y))
    if not cell:
        return None
    return cell.get("entity") or None


def beltNeighbors(
    ct: Controller, tile: Position, *, local_map: dict | None = None
) -> list[tuple[Position, dict]]:
    out: list[tuple[Position, dict]] = []
    for d in _CARDINALS:
        n = tile.add(d)
        if not _inBounds(ct, n):
            continue
        ent = tileEntity(ct, n, local_map=local_map)
        if ent is not None and ent.get("type") in BELT_TYPES:
            out.append((n, ent))
    return out


def harvesterClaimedByAlly(
    ct: Controller, tile: Position, *, local_map: dict | None = None
) -> bool:
    me = ct.get_team()
    for _n, ent in beltNeighbors(ct, tile, local_map=local_map):
        if ent.get("team") == me:
            return True
    return False