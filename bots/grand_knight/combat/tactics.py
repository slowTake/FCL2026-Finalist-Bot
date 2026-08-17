"""Grand_knight — Combat Domain: Guardian Defense & Turret Pipelines.

Implements base apron defense (Guardian), barrier ring construction from safe interior stand tiles,
structure healing, Gunner omnidirectional defense, and Sentinel counter-battery fire.
"""

from __future__ import annotations

import sys
from typing import Any, Optional
from fcode import Controller, Direction, EntityType, Environment, Position
from combat.barriers import ApronDefenseSkill
from combat.dismantle import try_dismantle_adjacent_hostile, try_demolition_frenzy_action
from combat.smart_barriers import try_block_gunner_lines

from config import Config
from engine.safe_controller import SafeController
from logistics.belt_repair import BeltRepairSkill
from spatial.movement import (
    PathCache,
    execute_bug2_evasion,
    find_builder_navigation_path,
    get_greedy_step,
)
from utils.geometry import (
    clamp_to_core_area,
    get_core_footprint,
    is_adjacent,
    is_in_bounds,
    is_in_vision,
    is_inside_core_area,
)
from utils.support import SupportSkill


class CombatTactics:
    """Combat tactics evaluator for Guardian, Shield builders, and Stationary Turrets."""

    def __init__(self, safe: Optional[SafeController] = None) -> None:
        self.safe = safe or SafeController(None)  # type: ignore
        self.support = SupportSkill(self.safe)
        self.belt_repair = BeltRepairSkill(self.safe)
        self.barriers = ApronDefenseSkill(self.safe)
        self.shield_sentinels: set[Position] = set()
        self.last_pos: Optional[Position] = None
        self.stuck_turns: int = 0
        self.seen_walls: set[Position] = set()
        self.pos_history: list[Position] = []
        self.path_cache = PathCache()
        self.economy_fallback: Optional[Any] = None

    def set_safe_controller(self, safe: SafeController) -> None:
        """Inject active SafeController instance."""
        self.safe = safe
        self.support.safe = safe
        self.belt_repair.safe = safe
        self.barriers.safe = safe

    def run_guardian(self, ct: Controller) -> None:
        """Execute Priority Pipeline for Guardian Builder Bot (my_idx % 5 in (0, 1))."""
        self.safe.begin_turn(ct)

        core_footprint = get_core_footprint(ct)
        policy = ct.read_store(Config.SLOT_EXPANSION_POLICY)
        mode = ct.read_store(Config.SLOT_TACTICAL_MODE)
        threat_alert = ct.read_store(Config.SLOT_ALERTS)
        threat_pressure = 1 if (mode >= 1 or threat_alert != 0) else 0
        curr_rnd = ct.get_current_round()
        bank = ct.get_global_resources()
        ammo = ct.read_store(Config.SLOT_AMMO_RESERVE)

        turret_count = ct.read_store(Config.SLOT_TURRET_COUNT)
        barriers_built = len(self.barriers.built_barriers)

        if Config.DEBUG_DEFENSE and curr_rnd % 25 == 0:
            print(
                f"DEFENSE_STATE round={curr_rnd} barriers={barriers_built} turrets={turret_count} "
                f"bank={bank} ammo={ammo} pressure={threat_pressure}",
                file=sys.stderr,
            )
            if turret_count < 2 or barriers_built < Config.MIN_BARRIERS_BEFORE_TURRET:
                print(
                    f"DEFENSE_DEFICIT round={curr_rnd} turrets={turret_count} barriers={barriers_built}",
                    file=sys.stderr,
                )

        # Step 0: Active Counter-Attack: Dismantle adjacent enemy turrets/barriers/units
        if try_dismantle_adjacent_hostile(ct, self.safe):
            return

        # Step 1: Active Demolition Frenzy: Seek out and destroy enemy turrets/sentinels/harvesters
        if try_demolition_frenzy_action(ct, self.safe, self.path_cache, self.pos_history, self.seen_walls):
            return

        # Step 2: High Priority: Heal Damaged Core
        if self.support.try_heal_core_priority(ct, core_footprint, self.pos_history, self.seen_walls):
            return

        # Step 3: Reconnect and repair broken conveyor belt lines
        if self.belt_repair.try_reconnect_broken_belts(ct, core_footprint, allow_walk=True):
            return

        # Step 4: Heal Duty for other friendly structures/units
        if self.support.try_heal_damaged_structures(ct):
            return

        # Step 5/6: Barrier ring and proactive turrets, ordered by which is more urgent
        prioritize_turret, priority_reason = self.barriers.should_prioritize_turret(
            ct, barriers_built, turret_count, threat_pressure, curr_rnd
        )
        if prioritize_turret:
            if Config.DEBUG_DEFENSE:
                print(f"TURRET_PRIORITIZED round={curr_rnd} reason={priority_reason}", file=sys.stderr)
            if self.barriers.try_build_defensive_turrets(ct):
                return
            if self.barriers.try_build_barrier_ring(ct, self.seen_walls):
                return
        else:
            if self.barriers.try_build_barrier_ring(ct, self.seen_walls):
                return
            if self.barriers.try_build_defensive_turrets(ct):
                return

        # Step 7: Reactive barrier placement in enemy gunner firing lines
        if try_block_gunner_lines(ct, self.safe, core_footprint):
            return

        # Step 8: Safe interior patrol or economy fallback
        pos = ct.get_position()
        if not is_inside_core_area(pos, core_footprint, margin=1) and threat_pressure > 0:
            home = clamp_to_core_area(pos, core_footprint, margin=1)
            step = get_greedy_step(pos, home)
            if step != Direction.CENTRE and ct.get_move_cooldown() == 0:
                outcome = self.safe.safe_move(step)
                if outcome.success:
                    return

        # Step 9: Defense Commitment (Phase H.4)
        if Config.DEFENSE_COMMITMENT_ENABLED:
            mode = ct.read_store(Config.SLOT_TACTICAL_MODE)
            if mode >= 2 and is_inside_core_area(pos, core_footprint, margin=1):
                print(f"DEFENSE_COMMIT builder_id={ct.get_id()} round={curr_rnd} role=GUARDIAN", file=sys.stderr)
                return

        # Fallback to economy mining when allowed
        if policy != Config.EXPANSION_LEASHED:
            if self.economy_fallback is None:
                from logistics.economy import EconomyBuilder
                self.economy_fallback = EconomyBuilder(self.safe)
            self.economy_fallback.set_safe_controller(self.safe)
            self.economy_fallback.run_economy_builder(ct)

    def _try_guardian_retreat_inside(
        self,
        ct: Controller,
        core_footprint: list[Position],
        threat_pressure: int,
    ) -> bool:
        """Retreat Guardian inside Core perimeter if outside and under pressure or damaged."""
        pos = ct.get_position()

        if is_inside_core_area(pos, core_footprint, margin=1):
            return False

        if threat_pressure > 0 or ct.get_hp() <= Config.GUARDIAN_EXTERIOR_PANIC_HP:
            target = clamp_to_core_area(pos, core_footprint, margin=1)
            path = find_builder_navigation_path(
                ct,
                pos,
                target,
                seen_walls=self.seen_walls,
                allowed_fn=lambda p: is_inside_core_area(p, core_footprint, margin=1),
            )
            if path:
                outcome = self.safe.safe_move(path[0])
                if outcome.success:
                    if Config.DEBUG_DEFENSE:
                        print(
                            f"GUARDIAN_EXTERIOR_RETREAT round={ct.get_current_round()} pos={pos} threat_pressure={threat_pressure}",
                            file=sys.stderr,
                        )
                    return True

            step = get_greedy_step(pos, target)
            if step != Direction.CENTRE and ct.get_move_cooldown() == 0:
                nxt = pos.add(step)
                if is_inside_core_area(nxt, core_footprint, margin=1):
                    outcome = self.safe.safe_move(step)
                    if outcome.success:
                        if Config.DEBUG_DEFENSE:
                            print(
                                f"GUARDIAN_EXTERIOR_RETREAT round={ct.get_current_round()} pos={pos} threat_pressure={threat_pressure}",
                                file=sys.stderr,
                            )
                        return True

        return False

    def run_harasser_builder(self, ct: Controller) -> None:
        """Execute Priority Pipeline for Shield / Economy Builder Bot (my_idx % 5 == 4)."""
        self.safe.begin_turn(ct)
        pos = ct.get_position()
        if self.last_pos == pos:
            self.stuck_turns += 1
        else:
            self.stuck_turns = 0
        self.last_pos = pos

        self.pos_history.append(pos)
        if len(self.pos_history) > 5:
            self.pos_history.pop(0)

        # Scan nearby tiles for Environment.WALL to update self.seen_walls
        for tile in self.safe.get_nearby_tiles():
            if ct.get_tile_env(tile) == Environment.WALL:
                self.seen_walls.add(tile)

        if self.support.try_heal_damaged_structures(ct):
            return

        # Priority A: Sentinel Shield (protect our own far Harvesters!)
        if self._try_sentinel_shield(ct):
            return

        # Fallback to 100% economy mining & belt paving
        if self.economy_fallback is None:
            from logistics.economy import EconomyBuilder
            self.economy_fallback = EconomyBuilder(self.safe)
        self.economy_fallback.set_safe_controller(self.safe)
        self.economy_fallback.run_economy_builder(ct)

    def run_turret(self, ct: Controller) -> None:
        """Execute Priority Pipeline for Gunner / Sentinel Turret."""
        self.safe.begin_turn(ct)
        if self._try_fire_at_target(ct):
            return

        if self._try_rotate_facing(ct):
            return
    def _try_sentinel_shield(self, ct: Controller) -> bool:
        """Sentinel Shield: Protect our own far Harvesters (>5 tiles from Core) with Sentinels."""
        if ct.get_global_resources() < ct.get_sentinel_cost():
            return False

        ammo_reserve = ct.read_store(Config.SLOT_AMMO_RESERVE)
        if ammo_reserve < Config.MIN_AMMO_FOR_SENTINEL:
            return False

        core_footprint = get_core_footprint(ct)
        core_tile = core_footprint[0]
        my_team = ct.get_team()
        pos = ct.get_position()

        # Find friendly far Harvester (> 5 tiles from Core) lacking an adjacent Sentinel
        target_harvester: Optional[Position] = None
        for bid in self.safe.get_nearby_buildings():
            if ct.get_team(bid) == my_team and ct.get_entity_type(bid) == EntityType.HARVESTER:
                hpos = ct.get_position(bid)
                if hpos.distance_squared(core_tile) > Config.SENTINEL_SHIELD_MIN_DIST_SQ:
                    has_sentinel = False
                    for adj_d in (Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST):
                        adj_tile = hpos.add(adj_d)
                        if is_in_vision(ct, pos, adj_tile, Config.BUILDER_BOT_VISION_RADIUS_SQ):
                            abid = ct.get_tile_building_id(adj_tile)
                            if abid is not None and ct.get_entity_type(abid) == EntityType.SENTINEL:
                                has_sentinel = True
                                break
                    if not has_sentinel:
                        target_harvester = hpos
                        break

        if target_harvester is None:
            return False

        # Enemy core position is always estimated via map-symmetry mirroring --
        # nothing broadcasts a real one (SLOT_ORE_CLAIM_LEDGER, the old
        # SLOT_ENEMY_CORE, holds harvester claims now, not a position).
        map_w = ct.get_map_width()
        map_h = ct.get_map_height()
        enemy_core = Position(map_w - 1 - core_tile.x, map_h - 1 - core_tile.y)

        facing = target_harvester.cardinal_direction_to(enemy_core)
        if facing == Direction.CENTRE:
            facing = Direction.NORTH

        sentinel_pos: Optional[Position] = None
        for d in (facing, facing.rotate_right(), facing.rotate_left(), facing.opposite()):
            if d.is_cardinal():
                cand = target_harvester.add(d)
                if is_in_bounds(ct, cand) and cand not in self.shield_sentinels:
                    if is_in_vision(ct, pos, cand, Config.BUILDER_BOT_VISION_RADIUS_SQ):
                        if ct.get_tile_building_id(cand) is None and ct.get_tile_env(cand) != Environment.WALL:
                            sentinel_pos = cand
                            break

        if sentinel_pos is None:
            return False

        if is_adjacent(pos, sentinel_pos) and ct.can_act():
            outcome = self.safe.safe_build_sentinel(sentinel_pos, facing)
            if outcome.success:
                self.shield_sentinels.add(sentinel_pos)
                return True

        if pos != sentinel_pos and ct.get_move_cooldown() == 0:
            if execute_bug2_evasion(ct, self.safe, self.path_cache, pos, sentinel_pos, self.pos_history, self.seen_walls):
                return True

        return False

    def _try_fire_at_target(self, ct: Controller) -> bool:
        """Turret Priority 1: Counter-battery fire prioritizing Gunners, Sentinels, and Builders."""
        if ct.get_action_cooldown() != 0:
            return False

        etype = ct.get_entity_type()
        if etype == EntityType.GUNNER:
            target = ct.get_gunner_target()
            if target is not None and is_in_bounds(ct, target) and ct.can_fire(target):
                target_bid = ct.get_tile_building_id(target)
                is_harvester = target_bid is not None and ct.get_entity_type(target_bid) == EntityType.HARVESTER
                if not is_harvester:
                    outcome = self.safe.safe_fire(target)
                    if outcome.success:
                        return True
        elif etype == EntityType.SENTINEL:
            my_team = ct.get_team()
            pos = ct.get_position()
            candidates: list[tuple[int, int, Position]] = []

            # Scan nearby hostile buildings (memoized query)
            for bid in self.safe.get_nearby_buildings(Config.SENTINEL_VISION_RADIUS_SQ):
                if ct.get_team(bid) != my_team:
                    btype = ct.get_entity_type(bid)
                    if btype == EntityType.HARVESTER:
                        # Preserved for hijacking via the economy ore pipeline -- never fired on.
                        continue
                    bpos = ct.get_position(bid)
                    if ct.can_fire(bpos):
                        priority = 1
                        if btype in (EntityType.GUNNER, EntityType.SENTINEL, EntityType.LAUNCHER):
                            priority = 5
                        candidates.append((priority, -pos.distance_squared(bpos), bpos))

            # Scan nearby hostile units (memoized query)
            for uid in self.safe.get_nearby_units(Config.SENTINEL_VISION_RADIUS_SQ):
                if ct.get_team(uid) != my_team:
                    upos = ct.get_position(uid)
                    if ct.can_fire(upos):
                        candidates.append((4, -pos.distance_squared(upos), upos))

            if candidates:
                candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
                best_target = candidates[0][2]
                outcome = self.safe.safe_fire(best_target)
                if outcome.success:
                    return True

        return False

    def _try_rotate_facing(self, ct: Controller) -> bool:
        """Turret Priority 2: Rotate facing toward nearest high-priority enemy in range."""
        if ct.get_action_cooldown() != 0:
            return False

        etype = ct.get_entity_type()
        if etype != EntityType.GUNNER:
            return False

        pos = ct.get_position()
        my_team = ct.get_team()
        curr_dir = ct.get_direction()

        # Gunner attack range is r^2 <= 13 (memoized query)
        candidates = []
        for eid in self.safe.get_nearby_entities(Config.GUNNER_VISION_RADIUS_SQ):
            if ct.get_team(eid) != my_team:
                btype = ct.get_entity_type(eid)
                if btype == EntityType.HARVESTER:
                    # Preserved for hijacking via the economy ore pipeline -- don't face/target it.
                    continue
                epos = ct.get_position(eid)
                d = pos.distance_squared(epos)
                priority = 1
                if btype in (EntityType.GUNNER, EntityType.SENTINEL, EntityType.LAUNCHER):
                    priority = 4
                elif btype == EntityType.BUILDER_BOT:
                    priority = 3

                candidates.append((priority, -d, epos))

        if not candidates:
            return False

        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        best_enemy_pos = candidates[0][2]
        desired_dir = pos.cardinal_direction_to(best_enemy_pos)

        if desired_dir != Direction.CENTRE and desired_dir != curr_dir:
            outcome = self.safe.safe_rotate(desired_dir)
            if outcome.success:
                return True

        return False
