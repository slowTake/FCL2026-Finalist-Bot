"""baron_v2 — Threat-Aware Offensive Push & Guns Micro Skill.

Ports baron's Launcher/Sentinel creep push, Gunner rotation micro, line-of-sight
raycasting, and enemy infrastructure harassment into a crash-proof SafeController layer.
"""

from __future__ import annotations

import sys
from typing import Optional, Sequence, Set
from fcode import Controller, Direction, EntityType, Environment, Position

from config import Config
from engine.outcome import ActionOutcome, OutcomeReason
from engine.safe_controller import SafeController
from engine.world import WorldView
from spatial.movement import choose_bug2_direction
from utils.geometry import is_adjacent, is_in_bounds, is_in_vision


# Firing & Rotation Priority (Lower integer = higher kill priority)
TARGET_PRIORITY: dict[EntityType, int] = {
    EntityType.CORE: 1,
    EntityType.BUILDER_BOT: 2,
    EntityType.GUNNER: 3,
    EntityType.SENTINEL: 4,
    EntityType.LAUNCHER: 5,
    EntityType.HARVESTER: 6,
    EntityType.BARRIER: 7,
    EntityType.CONVEYOR: 8,
    EntityType.SPLITTER: 8,
}

# Line of Sight: Ray passes through conveyors, splitters, harvesters, but is blocked by walls/barriers/cores
LOS_TRANSPARENT = frozenset({
    EntityType.CONVEYOR,
    EntityType.SPLITTER,
    EntityType.HARVESTER,
    EntityType.BUILDER_BOT,
})


class OffenseSkill:
    """High-efficiency threat-aware offensive pressure and turret micro."""

    def __init__(self, safe: SafeController, world: WorldView) -> None:
        self.safe = safe
        self.world = world
        self.last_sentinel_round: int = -999
        self.seen_walls: set[Position] = set()

    def run_turret_turn(self, ct: Controller) -> ActionOutcome:
        """Micro execution for stationary turrets (Gunner & Sentinel)."""
        my_type = ct.get_entity_type()
        pos = ct.get_position()
        my_team = ct.get_team()

        if ct.get_gun_cooldown() != 0:
            return ActionOutcome(success=False, reason=OutcomeReason.ACTION_COOLDOWN)

        range_sq = 13 if my_type == EntityType.GUNNER else 32
        best_target: Optional[int] = None
        best_priority: int = 999
        best_hp: int = 9999

        for eid in self.safe.get_nearby_entities(range_sq):
            if ct.get_team(eid) != my_team:
                epos = ct.get_position(eid)
                etype = ct.get_entity_type(eid)

                # Verify Line-of-Sight
                if not self._has_line_of_sight(ct, pos, epos):
                    continue

                prio = TARGET_PRIORITY.get(etype, 9)
                ehp = ct.get_hp(eid)

                if prio < best_priority or (prio == best_priority and ehp < best_hp):
                    best_priority = prio
                    best_hp = ehp
                    best_target = eid

        if best_target is not None:
            target_pos = ct.get_position(best_target)
            # For Gunners, ensure facing direction aligns
            if my_type == EntityType.GUNNER:
                req_dir = pos.cardinal_direction_to(target_pos)
                curr_dir = ct.get_direction()
                if req_dir != Direction.CENTRE and req_dir != curr_dir:
                    return self.safe.safe_rotate(req_dir)

            return self.safe.safe_fire(target_pos)

        return ActionOutcome(success=False, reason=OutcomeReason.NO_TARGET)

    def run_offensive_builder_turn(self, ct: Controller, target_core_pos: Position) -> ActionOutcome:
        """Micro execution for offensive creep builders placing forward turrets."""
        pos = ct.get_position()
        curr_rnd = ct.get_current_round()
        dist_sq = pos.distance_squared(target_core_pos)

        # Update wall memory
        for t in ct.get_nearby_tiles():
            if ct.get_tile_env(t) == Environment.WALL:
                self.seen_walls.add(t)

        # 1. Check if we can deploy a forward Sentinel (standoff range: d² in 16..32)
        if 16 <= dist_sq <= 32 and (curr_rnd - self.last_sentinel_round >= 10):
            if ct.can_act() and ct.get_global_resources() >= ct.get_sentinel_cost():
                # Find an open adjacent placement tile
                for d in (Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST):
                    adj = pos.add(d)
                    if is_in_bounds(ct, adj) and ct.is_tile_passable(adj) and ct.get_tile_building_id(adj) is None:
                        outcome = self.safe.safe_build_sentinel(adj)
                        if outcome.success:
                            self.last_sentinel_round = curr_rnd
                        return outcome

        # 2. Check if we can deploy a forward Gunner (standoff range: d² in 4..13)
        if 4 <= dist_sq <= 13:
            if ct.can_act() and ct.get_global_resources() >= ct.get_gunner_cost():
                for d in (Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST):
                    adj = pos.add(d)
                    if is_in_bounds(ct, adj) and ct.is_tile_passable(adj) and ct.get_tile_building_id(adj) is None:
                        aim_dir = adj.cardinal_direction_to(target_core_pos)
                        if aim_dir != Direction.CENTRE:
                            return self.safe.safe_build_gunner(adj, aim_dir)

        # 3. Creep advance toward enemy core while respecting threat radius
        if ct.get_move_cooldown() == 0:
            step_dir = choose_bug2_direction(ct, pos, target_core_pos, seen_walls=self.seen_walls)
            if step_dir is not None:
                return self.safe.safe_move(step_dir)

        return ActionOutcome(success=False, reason=OutcomeReason.NO_OP)

    def _has_line_of_sight(self, ct: Controller, start_pos: Position, target_pos: Position) -> bool:
        """Bresenham raycast checking if projectile trajectory is unobstructed."""
        x0, y0 = start_pos.x, start_pos.y
        x1, y1 = target_pos.x, target_pos.y

        dx = abs(x1 - x0)
        dy = abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx - dy

        curr_x, curr_y = x0, y0
        while (curr_x, curr_y) != (x1, y1):
            p = Position(curr_x, curr_y)
            if p != start_pos:
                if not is_in_bounds(ct, p):
                    return False
                if ct.get_tile_env(p) == Environment.WALL:
                    return False
                bid = ct.get_tile_building_id(p)
                if bid is not None:
                    btype = ct.get_entity_type(bid)
                    if btype not in LOS_TRANSPARENT and p != target_pos:
                        return False

            e2 = 2 * err
            if e2 > -dy:
                err -= dy
                curr_x += sx
            if e2 < dx:
                err += dx
                curr_y += sy

        return True
