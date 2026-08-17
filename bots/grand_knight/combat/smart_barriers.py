"""Grand_knight — Combat Domain: Strategic Smart Barrier Placement.

Detects enemy Gunner / Sentinel lines of fire directed toward our Core or Harvesters,
and places strategic barriers along the trajectory to absorb incoming fire.
"""

from __future__ import annotations

from typing import Optional, Set
from fcode import Controller, Direction, EntityType, Environment, Position

from config import Config
from engine.safe_controller import SafeController
from spatial.movement import is_in_bounds
from utils.geometry import get_core_footprint, is_adjacent, is_in_vision


def find_gunner_threat_lines(
    ct: Controller,
    safe: SafeController,
    core_footprint: list[Position],
) -> list[tuple[Position, Position]]:
    """Identify enemy gunners and their target tiles hitting friendly assets."""
    my_team = ct.get_team()
    my_pos = ct.get_position()
    threat_lines: list[tuple[Position, Position]] = []

    for bid in safe.get_nearby_buildings(Config.BUILDER_BOT_VISION_RADIUS_SQ):
        try:
            if ct.get_team(bid) == my_team:
                continue

            btype = ct.get_entity_type(bid)
            if btype not in (EntityType.GUNNER, EntityType.SENTINEL):
                continue

            gpos = ct.get_position(bid)
            facing = ct.get_direction(bid)
            if not facing or facing == Direction.CENTRE:
                continue

            # Trace ray along facing up to range 6
            curr = gpos
            for _ in range(6):
                curr = curr.add(facing)
                if not is_in_bounds(ct, curr):
                    break

                # If the ray hits our Core or friendly Harvester
                if curr in core_footprint:
                    threat_lines.append((gpos, curr))
                    break

                target_bid = ct.get_tile_building_id(curr)
                if target_bid is not None:
                    if ct.get_team(target_bid) == my_team:
                        target_type = ct.get_entity_type(target_bid)
                        if target_type in (EntityType.CORE, EntityType.HARVESTER):
                            threat_lines.append((gpos, curr))
                    break  # Blocked by building
        except Exception:
            continue

    return threat_lines


def try_block_gunner_lines(
    ct: Controller,
    safe: SafeController,
    core_footprint: list[Position],
) -> bool:
    """Place a strategic barrier between an enemy gunner and our Core/Harvester."""
    if not ct.can_act() or ct.get_global_resources() < ct.get_barrier_cost() or safe.major_action_used:
        return False

    my_pos = ct.get_position()
    threat_lines = find_gunner_threat_lines(ct, safe, core_footprint)

    for gun_pos, target_pos in threat_lines:
        # Trace line from gun_pos towards target_pos
        facing = gun_pos.cardinal_direction_to(target_pos)
        if facing == Direction.CENTRE:
            continue

        curr = gun_pos.add(facing)
        while curr != target_pos and is_in_bounds(ct, curr):
            if is_adjacent(my_pos, curr):
                if is_in_vision(ct, my_pos, curr, Config.BUILDER_BOT_VISION_RADIUS_SQ):
                    if ct.get_tile_building_id(curr) is None and ct.get_tile_env(curr) != Environment.WALL:
                        outcome = safe.safe_build_barrier(curr)
                        if outcome.success:
                            return True
            curr = curr.add(facing)

    return False
