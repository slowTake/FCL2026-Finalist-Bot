"""Wall-building helpers — barrier placement around a target or enemy core."""

from __future__ import annotations

from fcode import Controller, EntityType, Position


# will wall off the entire perimeter of the core, will need repeated calls to complete
# each non-wall tile will need a separete call to finish the job, if done in a loop then it'll repair any gaps that the enemy makes
def wallEnemyCore(ct: Controller, core: Position) -> bool:
    """Advance the perimeter wall by one action: build/destroy at most one tile.

    Returns True the turn it takes an action, False if the whole pass found
    nothing to do — matches the `-> bool` step convention the other role
    helpers use, so a caller can dispatch on it (`if wallEnemyCore(...): return`).
    """
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


# version that just builds wall at a specified target
# gets rid of anything in the way and then will build
def wallAtTarget(ct: Controller, target: Position) -> None:
    bid = ct.get_tile_building_id(target)
    if bid is not None:
        etype = ct.get_entity_type(bid)
        if etype == EntityType.BARRIER:
            return
        if etype in (EntityType.CONVEYOR, EntityType.SPLITTER):
            if ct.can_destroy(target) and ct.get_action_cooldown() == 0:
                ct.destroy(target)

    if ct.get_action_cooldown() != 0:
        return
    cost = ct.get_barrier_cost()
    if cost > ct.get_global_resources():
        return
    if ct.can_build_barrier(target):
        ct.build_barrier(target)
