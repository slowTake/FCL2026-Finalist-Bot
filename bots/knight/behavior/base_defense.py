"""knight — Behavior Domain: BaseDefenseSkill.

Extracts and unifies base defense barrier construction, deferred barrier management,
safe interior positioning, and prioritized turret deployment for Guardian and flex builders.
"""

from __future__ import annotations

import sys
from typing import Any, Optional, Set
from fcode import Controller, Direction, EntityType, Environment, Position

from config import Config
from engine.outcome import Outcome, Reason
from engine.safe_controller import SafeController
from engine.world import WorldView
from spatial.movement import find_builder_navigation_path, get_greedy_step, is_in_bounds, is_traversable
from utils.geometry import choose_guardian_stand_tile, is_adjacent, is_in_vision, is_inside_core_area


class BaseDefenseSkill:
    """Consolidated base defense skill managing barriers and defensive turrets."""

    def __init__(self, safe: SafeController, world: Optional[WorldView] = None) -> None:
        self.safe = safe
        self.world = world or WorldView(safe)
        self.built_barriers: set[Position] = set()
        self.built_sentinels: set[Position] = set()
        self.built_gunners: set[Position] = set()
        self.deferred_barrier_tiles: dict[Position, int] = {}
        self.last_unsafe_skip_log_round: dict[Position, int] = {}

    def should_prioritize_turret(
        self,
        ct: Controller,
        barriers_built: int,
        turret_count: int,
        threat_pressure: int,
        round_num: int,
    ) -> tuple[bool, str]:
        """Determine if turret construction should precede remaining barrier construction."""
        if turret_count == 0 and threat_pressure >= Config.EMERGENCY_TURRET_PRESSURE:
            return True, "EMERGENCY_PRESSURE"

        if turret_count == 0 and round_num >= Config.TURRET_PRIORITY_ROUND:
            return True, "PRIORITY_ROUND"

        if turret_count == 0 and round_num >= Config.DEFENSE_DEADLINE_ROUND:
            return True, "DEADLINE_ROUND"

        if barriers_built >= Config.MIN_BARRIERS_BEFORE_TURRET:
            return True, "BARRIERS_MET"

        return False, ""

    def try_build_barrier_ring(self, ct: Controller, seen_walls: Optional[Set[Position]] = None) -> bool:
        """Construct 3-Ti barrier ring on Core apron tiles from safe interior stand tiles."""
        if ct.get_global_resources() < ct.get_barrier_cost():
            return False

        mode = self.world.tactical_mode
        proactive = ct.read_store(Config.SLOT_PROACTIVE_TURRET)
        if mode == 0 and proactive == 0:
            return False

        core_footprint = self.world.core_footprint
        cx, cy = core_footprint[0].x, core_footprint[0].y

        # Core apron tiles (excluding NORTH spawn gates cx, cy-1 and cx+1, cy-1)
        raw_apron = [
            Position(cx - 1, cy),
            Position(cx - 1, cy + 1),
            Position(cx + 2, cy),
            Position(cx + 2, cy + 1),
            Position(cx, cy + 2),
            Position(cx + 1, cy + 2),
        ]
        barrier_apron = [p for p in raw_apron if is_in_bounds(ct, p)]
        pos = ct.get_position()
        curr_rnd = ct.get_current_round()

        # Clean expired deferred barrier tiles
        self.deferred_barrier_tiles = {
            p: exp for p, exp in self.deferred_barrier_tiles.items() if curr_rnd < exp
        }

        missing_barriers: list[Position] = []
        for b_tile in barrier_apron:
            if b_tile in self.deferred_barrier_tiles:
                continue
            if is_in_vision(ct, pos, b_tile, Config.BUILDER_BOT_VISION_RADIUS_SQ):
                try:
                    if ct.get_tile_env(b_tile) == Environment.WALL:
                        continue
                    bid = ct.get_tile_building_id(b_tile)
                    if bid is not None:
                        btype = ct.get_entity_type(bid)
                        if btype in (
                            EntityType.BARRIER,
                            EntityType.CONVEYOR,
                            EntityType.SPLITTER,
                            EntityType.GUNNER,
                            EntityType.SENTINEL,
                        ):
                            continue
                except Exception:
                    pass
                missing_barriers.append(b_tile)
            elif b_tile not in self.built_barriers:
                missing_barriers.append(b_tile)

        if not missing_barriers:
            return False

        traversable_fn = lambda p: is_traversable(ct, p, seen_walls)
        threat_alert = ct.read_store(Config.SLOT_ALERTS)
        threat_pressure = 1 if (mode >= 1 or threat_alert != 0) else 0
        core_hp_pct = self.world.core_hp_pct

        for target_tile in missing_barriers:
            stand_tile = choose_guardian_stand_tile(ct, target_tile, core_footprint, traversable_fn)
            if stand_tile is None:
                if (
                    Config.GUARDIAN_ALLOW_EXTERIOR_BUILD_WHEN_NO_THREAT
                    and threat_pressure <= Config.GUARDIAN_EXTERIOR_BUILD_MAX_PRESSURE
                    and core_hp_pct >= Config.CORE_HP_SECURE_PCT
                ):
                    fallback_cands = [
                        target_tile.add(d)
                        for d in (Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST)
                        if is_in_bounds(ct, target_tile.add(d)) and traversable_fn(target_tile.add(d))
                    ]
                    if fallback_cands:
                        stand_tile = fallback_cands[0]
                        if Config.DEBUG_DEFENSE:
                            print(
                                f"GUARDIAN_EXTERIOR_BUILD_FALLBACK round={curr_rnd} tile={target_tile}",
                                file=sys.stderr,
                            )
                    else:
                        self.deferred_barrier_tiles[target_tile] = curr_rnd + Config.BARRIER_SKIP_DELAY_TURNS
                        continue
                else:
                    self.deferred_barrier_tiles[target_tile] = curr_rnd + Config.BARRIER_SKIP_DELAY_TURNS
                    last_log = self.last_unsafe_skip_log_round.get(target_tile, -100)
                    if Config.DEBUG_DEFENSE and (last_log < 0 or curr_rnd - last_log >= 20):
                        print(
                            f"GUARDIAN_UNSAFE_BARRIER_SKIP round={curr_rnd} tile={target_tile}",
                            file=sys.stderr,
                        )
                        self.last_unsafe_skip_log_round[target_tile] = curr_rnd
                    continue

            # If Guardian is already on a valid stand tile adjacent to target_tile
            if is_adjacent(pos, target_tile):
                if ct.can_act():
                    outcome = self.safe.safe_build_barrier(target_tile)
                    if outcome.success:
                        self.built_barriers.add(target_tile)
                        return True
                    elif outcome.reason == Reason.NO_RESOURCES:
                        return False
                    elif outcome.reason == Reason.BLOCKED:
                        self.built_barriers.add(target_tile)
                        continue

            # Otherwise navigate toward stand_tile
            if ct.get_move_cooldown() == 0:
                allowed_fn = (
                    (lambda p: is_inside_core_area(p, core_footprint, margin=1))
                    if threat_pressure > 0
                    else None
                )
                path = find_builder_navigation_path(
                    ct,
                    pos,
                    stand_tile,
                    max_hops=15,
                    seen_walls=seen_walls,
                    allowed_fn=allowed_fn,
                )
                if path:
                    outcome = self.safe.safe_move(path[0])
                    if outcome.success:
                        return True

                step = get_greedy_step(pos, stand_tile)
                if step != Direction.CENTRE and ct.can_move(step):
                    nxt = pos.add(step)
                    if allowed_fn is None or allowed_fn(nxt):
                        outcome = self.safe.safe_move(step)
                        if outcome.success:
                            return True
                    if is_inside_core_area(nxt, core_footprint, margin=1) and ct.can_move(step):
                        outcome = self.safe.safe_move(step)
                        if outcome.success:
                            return True

        return False

    def try_build_proactive_sentinel(self, ct: Controller) -> bool:
        """Construct 2 Sentinels on Core apron corners facing outward approach vectors."""
        if ct.get_global_resources() < ct.get_sentinel_cost():
            return False

        ammo_reserve = self.world.ammo_reserve
        if ammo_reserve < Config.MIN_AMMO_FOR_SENTINEL:
            return False

        core_footprint = self.world.core_footprint
        cx, cy = core_footprint[0].x, core_footprint[0].y

        apron_turrets = [
            (Position(cx - 1, cy - 1), Direction.NORTH),
            (Position(cx + 2, cy + 2), Direction.SOUTH),
        ]

        pos = ct.get_position()
        target_pos: Optional[Position] = None
        target_facing: Direction = Direction.NORTH

        for tpos, facing in apron_turrets:
            if is_in_bounds(ct, tpos):
                if is_in_vision(ct, pos, tpos, Config.BUILDER_BOT_VISION_RADIUS_SQ):
                    bid = ct.get_tile_building_id(tpos)
                    if bid is None:
                        target_pos = tpos
                        target_facing = facing
                        break
                elif tpos not in self.built_sentinels:
                    target_pos = tpos
                    target_facing = facing
                    break

        if target_pos is None:
            return False

        # If standing on target_pos, step off first
        if pos == target_pos and ct.get_move_cooldown() == 0:
            for d in (Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST):
                nxt = pos.add(d)
                if is_in_bounds(ct, nxt) and is_inside_core_area(nxt, core_footprint, margin=1) and ct.can_move(d):
                    outcome = self.safe.safe_move(d)
                    if outcome.success:
                        return True

        if is_adjacent(pos, target_pos) and ct.can_act():
            outcome = self.safe.safe_build_sentinel(target_pos, target_facing)
            if outcome.success:
                self.built_sentinels.add(target_pos)
                return True

        if pos != target_pos and ct.get_move_cooldown() == 0:
            step = get_greedy_step(pos, target_pos)
            if step != Direction.CENTRE:
                nxt = pos.add(step)
                if is_inside_core_area(nxt, core_footprint, margin=1) and ct.can_move(step):
                    outcome = self.safe.safe_move(step)
                    if outcome.success:
                        return True

        return False

    def try_build_proactive_gunner(self, ct: Controller) -> bool:
        """Construct Gunners on remaining apron corners for 360-degree omnidirectional coverage."""
        if ct.get_global_resources() < ct.get_gunner_cost():
            return False

        ammo_reserve = self.world.ammo_reserve
        if ammo_reserve < 5:
            return False

        core_footprint = self.world.core_footprint
        cx, cy = core_footprint[0].x, core_footprint[0].y

        gunner_spots = [
            (Position(cx - 1, cy + 2), Direction.WEST),
            (Position(cx + 2, cy - 1), Direction.EAST),
        ]

        pos = ct.get_position()
        target_pos: Optional[Position] = None
        target_facing: Direction = Direction.NORTH

        for tpos, facing in gunner_spots:
            if is_in_bounds(ct, tpos):
                if is_in_vision(ct, pos, tpos, Config.BUILDER_BOT_VISION_RADIUS_SQ):
                    bid = ct.get_tile_building_id(tpos)
                    if bid is None:
                        target_pos = tpos
                        target_facing = facing
                        break
                elif tpos not in self.built_gunners:
                    target_pos = tpos
                    target_facing = facing
                    break

        if target_pos is None:
            return False

        # If standing on target_pos, step off first
        if pos == target_pos and ct.get_move_cooldown() == 0:
            for d in (Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST):
                nxt = pos.add(d)
                if is_in_bounds(ct, nxt) and is_inside_core_area(nxt, core_footprint, margin=1) and ct.can_move(d):
                    outcome = self.safe.safe_move(d)
                    if outcome.success:
                        return True

        if is_adjacent(pos, target_pos) and ct.can_act():
            outcome = self.safe.safe_build_gunner(target_pos, target_facing)
            if outcome.success:
                self.built_gunners.add(target_pos)
                if Config.DEBUG_DEFENSE:
                    print(f"GUNNER_BUILT builder_id={ct.get_id()} pos={target_pos} facing={target_facing}", file=sys.stderr)
                return True

        if pos != target_pos and ct.get_move_cooldown() == 0:
            step = get_greedy_step(pos, target_pos)
            if step != Direction.CENTRE:
                nxt = pos.add(step)
                if is_inside_core_area(nxt, core_footprint, margin=1) and ct.can_move(step):
                    outcome = self.safe.safe_move(step)
                    if outcome.success:
                        return True

        return False
