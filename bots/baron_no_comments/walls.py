
from __future__ import annotations

from fcode import Controller, EntityType, Position

from pathing import inBounds


def wallEnemyCore(ct: Controller, core: Position) -> bool:
    targets = [
        Position(core.x, core.y - 1),
        Position(core.x + 1, core.y - 1),
        Position(core.x, core.y + 2),
        Position(core.x + 1, core.y + 2),
        Position(core.x - 1, core.y),
        Position(core.x - 1, core.y + 1),
        Position(core.x + 2, core.y),
        Position(core.x + 2, core.y + 1),
    ]

    for tile in targets:
        if not inBounds(ct, tile):
            continue  # a core near the map edge pushes some of the 8 offsets off it
        if not ct.is_in_vision(tile):
            continue
        bid = ct.get_tile_building_id(tile)
        if bid is not None:
            etype = ct.get_entity_type(bid)
            if etype == EntityType.BARRIER:
                continue
            if etype in (EntityType.CONVEYOR, EntityType.SPLITTER):
                if ct.can_destroy(tile) and ct.get_action_cooldown() == 0:
                    ct.destroy(tile)
                    return True
                continue
            continue

        if ct.get_action_cooldown() != 0:
            continue
        cost = ct.get_barrier_cost()
        if cost > ct.get_global_resources():
            continue
        if ct.can_build_barrier(tile):
            ct.build_barrier(tile)
            return True
    return False