"""Grand_knight — Combat Domain: Hostile Structure Dismantling, Sabotage Barriers & Demolition Frenzy.

Allows Builder Bots to actively hunt and dismantle enemy turrets, conveyors, harvesters,
and barriers, placing sabotage barriers in destroyed enemy tiles.
"""

from __future__ import annotations

from typing import Set
from fcode import Controller, EntityType, Position

from config import Config
from engine.safe_controller import SafeController
from spatial.movement import PathCache, execute_bug2_evasion
from utils.geometry import is_adjacent, get_core_footprint


def try_dismantle_adjacent_hostile(ct: Controller, safe: SafeController) -> bool:
    """If adjacent to an enemy building or unit, fire to dismantle it, or place sabotage barrier."""
    if ct.get_action_cooldown() != 0 or safe.major_action_used:
        return False

    my_team = ct.get_team()
    my_pos = ct.get_position()

    candidates: list[tuple[int, Position]] = []

    for bid in safe.get_nearby_buildings(Config.BUILDER_BOT_VISION_RADIUS_SQ):
        try:
            if ct.get_team(bid) == my_team:
                continue

            bpos = ct.get_position(bid)
            if not is_adjacent(my_pos, bpos):
                continue

            if not ct.can_fire(bpos):
                continue

            btype = ct.get_entity_type(bid)
            # Priority: Gunners/Sentinels (5), Launchers (4), Conveyors (2), Barriers (1)
            # Harvesters are preserved for resource hijacking/tapping
            if btype == EntityType.HARVESTER:
                continue

            priority = 1
            if btype in (EntityType.GUNNER, EntityType.SENTINEL):
                priority = 5
            elif btype == EntityType.LAUNCHER:
                priority = 4
            elif btype == EntityType.CONVEYOR:
                priority = 2
            elif btype == EntityType.BARRIER:
                priority = 1

            candidates.append((priority, bpos))
        except Exception:
            continue

    # Also check nearby enemy units (e.g. enemy builders in face)
    for uid in safe.get_nearby_units(Config.BUILDER_BOT_VISION_RADIUS_SQ):
        try:
            if ct.get_team(uid) == my_team:
                continue
            upos = ct.get_position(uid)
            if is_adjacent(my_pos, upos) and ct.can_fire(upos):
                candidates.append((3, upos))
        except Exception:
            continue

    if candidates:
        candidates.sort(key=lambda item: item[0], reverse=True)
        target_pos = candidates[0][1]
        outcome = safe.safe_fire(target_pos)
        return outcome.success

    return False


def try_demolition_frenzy_action(
    ct: Controller,
    safe: SafeController,
    path_cache: PathCache,
    pos_history: list[Position],
    seen_walls: Set[Position],
) -> bool:
    """Builder Frenzy: seek out enemy buildings, dismantle them, and place sabotage barriers."""
    my_team = ct.get_team()
    pos = ct.get_position()
    core_footprint = get_core_footprint(ct)

    # 1. First priority: Dismantle any adjacent hostile structure or unit
    if try_dismantle_adjacent_hostile(ct, safe):
        return True

    # 2. Second priority: Navigate toward and destroy visible hostile buildings/units
    hostile_targets: list[tuple[int, Position]] = []
    for bid in safe.get_nearby_buildings(Config.BUILDER_BOT_VISION_RADIUS_SQ):
        try:
            if ct.get_team(bid) != my_team:
                btype = ct.get_entity_type(bid)
                if btype == EntityType.HARVESTER:
                    # Preserved for hijacking via the economy ore pipeline (see
                    # logistics/economy.py's HARVESTER_HIJACK path) -- never a
                    # demolition target, matching try_dismantle_adjacent_hostile above.
                    continue

                bpos = ct.get_position(bid)
                d = pos.distance_squared(bpos)
                # Priority: Gunners/Sentinels (10), Conveyors (4), Barriers (1), other (3)
                prio = 3
                if btype in (EntityType.GUNNER, EntityType.SENTINEL):
                    prio = 10
                elif btype == EntityType.CONVEYOR:
                    prio = 4
                elif btype == EntityType.BARRIER:
                    prio = 1
                hostile_targets.append((prio, -d, bpos))
        except Exception:
            continue

    # Also target enemy units in vision
    for uid in safe.get_nearby_units(Config.BUILDER_BOT_VISION_RADIUS_SQ):
        try:
            if ct.get_team(uid) != my_team:
                upos = ct.get_position(uid)
                d = pos.distance_squared(upos)
                hostile_targets.append((8, -d, upos))
        except Exception:
            continue

    if hostile_targets:
        hostile_targets.sort(key=lambda item: (item[0], item[1]), reverse=True)
        best_target = hostile_targets[0][2]
        if ct.get_move_cooldown() == 0:
            if execute_bug2_evasion(ct, safe, path_cache, pos, best_target, pos_history, seen_walls):
                return True

    return False
