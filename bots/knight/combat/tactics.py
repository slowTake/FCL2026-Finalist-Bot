"""knight — Combat Domain: Guardian Defense & Turret Pipelines.

Implements base apron defense (Guardian), barrier ring construction from safe interior stand tiles,
structure healing, Gunner omnidirectional defense, and Sentinel counter-battery fire.
"""

from __future__ import annotations

import sys
from typing import Any, Optional, Set
from fcode import Controller, Direction, EntityType, Environment, Position
from combat.dismantle import try_dismantle_adjacent_hostile, try_demolition_frenzy_action
from combat.smart_barriers import try_block_gunner_lines

from config import Config
from engine.outcome import Reason
from engine.safe_controller import SafeController
from engine.step_result import StepResult
from logistics.belt_repair import BeltRepairSkill
from spatial.movement import (
    execute_bug2_evasion,
    find_builder_navigation_path,
    get_greedy_step,
    is_traversable,
)
from utils.geometry import (
    choose_guardian_stand_tile,
    clamp_to_core_area,
    get_core_footprint,
    is_adjacent,
    is_in_bounds,
    is_in_vision,
    is_inside_core_area,
    unpack_pos,
)
from utils.support import SupportSkill


class CombatTactics:
    """Combat tactics evaluator for Guardian, Shield builders, and Stationary Turrets."""

    def __init__(self, safe: Optional[SafeController] = None) -> None:
        self.safe = safe or SafeController(None)  # type: ignore
        self.support = SupportSkill(self.safe)
        self.belt_repair = BeltRepairSkill(self.safe)
        self.built_barriers: set[Position] = set()
        self.built_sentinels: set[Position] = set()
        self.built_gunners: set[Position] = set()
        self.shield_sentinels: set[Position] = set()
        self.deferred_barrier_tiles: dict[Position, int] = {}
        self.last_unsafe_skip_log_round: dict[Position, int] = {}
        self.last_pos: Optional[Position] = None
        self.stuck_turns: int = 0
        self.seen_walls: set[Position] = set()
        self.pos_history: list[Position] = []
        self.economy_fallback: Optional[Any] = None

        # Phase H: Holding Gate State
        self.hold_target: Optional[Position] = None
        self.hold_reason: Optional[str] = None
        self.hold_turns: int = 0

    def set_safe_controller(self, safe: SafeController) -> None:
        """Inject active SafeController instance."""
        self.safe = safe
        self.support.safe = safe
        self.belt_repair.safe = safe

    def _hold_position(self, ct: Controller, target: Position, reason: str) -> StepResult:
        """Stationary holding gate: latch position when adjacent to valid target waiting on resources/cooldown."""
        curr_rnd = ct.get_current_round()
        my_id = ct.get_id()
        if self.hold_target == target:
            self.hold_turns += 1
        else:
            self.hold_target = target
            self.hold_reason = reason
            self.hold_turns = 1

        if self.hold_turns > Config.HOLD_MAX_TURNS:
            if Config.DEBUG_DEFENSE:
                print(
                    f"TASK_HOLD_EXPIRED builder_id={my_id} target={target} "
                    f"reason={reason} turns={self.hold_turns} round={curr_rnd}",
                    file=sys.stderr,
                )
            self.hold_target = None
            self.hold_reason = None
            self.hold_turns = 0
            return StepResult.FAILED

        print(
            f"TASK_HOLD builder_id={my_id} target={target} "
            f"reason={reason} turns={self.hold_turns} round={curr_rnd}",
            file=sys.stderr,
        )
        return StepResult.HOLDING

    def _should_prioritize_turret(
        self,
        ct: Controller,
        barriers_built: int,
        turret_count: int,
        threat_pressure: int,
        curr_rnd: int,
    ) -> tuple[bool, str]:
        """Determine if turret construction should be prioritized over barriers."""
        if turret_count < 2 and threat_pressure > 0:
            return True, "THREAT_PRESSURE"
        if turret_count < 1 and curr_rnd >= Config.TURRET_PRIORITY_ROUND:
            return True, "PRIORITY_ROUND"
        if barriers_built >= Config.MIN_BARRIERS_BEFORE_TURRET and turret_count < Config.PROACTIVE_TURRET_TARGET_COUNT:
            return True, "MIN_BARRIERS_MET"
        return False, ""

    def _try_build_defensive_turrets(self, ct: Controller) -> bool:
        """Construct defensive turrets (Sentinels and Gunners) with fallback to any adjacent valid tile."""
        if self._try_build_proactive_sentinel(ct):
            return True
        if self._try_build_proactive_gunner(ct):
            return True

        # Emergency Fallback: If under threat/alert or turret count < 2, place a Gunner on any valid adjacent tile facing threat
        mode = ct.read_store(Config.SLOT_TACTICAL_MODE)
        alert_pos = unpack_pos(ct.read_store(Config.SLOT_ALERTS))
        turret_count = ct.read_store(Config.SLOT_TURRET_COUNT)

        if (mode > 0 or alert_pos is not None or turret_count < 2) and ct.get_global_resources() >= ct.get_gunner_cost() and ct.can_act():
            pos = ct.get_position()
            core_footprint = get_core_footprint(ct)
            facing = pos.cardinal_direction_to(alert_pos) if alert_pos is not None else Direction.NORTH
            if facing == Direction.CENTRE:
                facing = Direction.NORTH

            for d in (facing, facing.rotate_right(), facing.rotate_left(), facing.opposite()):
                adj = pos.add(d)
                if is_in_bounds(ct, adj) and adj not in core_footprint:
                    if ct.get_tile_building_id(adj) is None and ct.get_tile_env(adj) != Environment.WALL:
                        outcome = self.safe.safe_build_gunner(adj, d)
                        if outcome.success:
                            self.built_gunners.add(adj)
                            if Config.DEBUG_DEFENSE:
                                print(f"EMERGENCY_GUNNER_BUILT builder_id={ct.get_id()} pos={adj} facing={d}", file=sys.stderr)
                            return True

        return False

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
        barriers_built = len(self.built_barriers)

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
        if try_demolition_frenzy_action(ct, self.safe, self.pos_history, self.seen_walls):
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

        # Step 5: Protective barrier ring on Core apron
        if self._try_build_barrier_ring(ct):
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

        # Step 8: Defense Commitment (Phase H.4)
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

        # Priority A: Sentinel Shield (protect knight's own far Harvesters!)
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

    def _try_build_barrier_ring(self, ct: Controller) -> bool:
        """Construct 3-Ti barrier ring on Core apron tiles from safe interior stand tiles."""
        if ct.get_global_resources() < ct.get_barrier_cost():
            return False

        mode = ct.read_store(Config.SLOT_TACTICAL_MODE)
        proactive = ct.read_store(Config.SLOT_PROACTIVE_TURRET)
        if mode == 0 and proactive == 0:
            return False

        core_footprint = get_core_footprint(ct)
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

        traversable_fn = lambda p: is_traversable(ct, p, self.seen_walls)

        threat_alert = ct.read_store(Config.SLOT_ALERTS)
        threat_pressure = 1 if (mode >= 1 or threat_alert != 0) else 0
        core_hp_pct = int(100 * ct.get_hp() / max(1, ct.get_max_hp()))

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
                if ct.can_act() and ct.get_global_resources() >= ct.get_barrier_cost():
                    outcome = self.safe.safe_build_barrier(target_tile)
                    if outcome.success:
                        self.built_barriers.add(target_tile)
                        self.hold_target = None
                        return True
                    elif outcome.reason == Reason.BLOCKED:
                        self.built_barriers.add(target_tile)
                        continue
                res = self._hold_position(ct, target_tile, "COOLDOWN" if not ct.can_act() else "NO_RESOURCES")
                return res.consumed

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
                    seen_walls=self.seen_walls,
                    allowed_fn=allowed_fn,
                )
                if path:
                    outcome = self.safe.safe_move(path[0])
                    if outcome.success:
                        return True
                else:
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

    def _try_build_proactive_sentinel(self, ct: Controller) -> bool:
        """Construct 2 Sentinels on Core apron corners facing outward approach vectors."""
        if ct.get_global_resources() < ct.get_sentinel_cost():
            return False

        ammo_reserve = ct.read_store(Config.SLOT_AMMO_RESERVE)
        if ammo_reserve < Config.MIN_AMMO_FOR_SENTINEL:
            return False

        core_footprint = get_core_footprint(ct)
        cx, cy = core_footprint[0].x, core_footprint[0].y

        # 2 outward-facing Sentinel positions on apron corners
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
                    if bid is None and ct.get_tile_env(tpos) != Environment.WALL:
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

        if is_adjacent(pos, target_pos):
            if ct.can_act() and ct.get_global_resources() >= ct.get_sentinel_cost():
                outcome = self.safe.safe_build_sentinel(target_pos, target_facing)
                if outcome.success:
                    self.built_sentinels.add(target_pos)
                    self.hold_target = None
                    return True
            res = self._hold_position(ct, target_pos, "COOLDOWN" if not ct.can_act() else "NO_RESOURCES")
            return res.consumed

        if pos != target_pos and ct.get_move_cooldown() == 0:
            step = get_greedy_step(pos, target_pos)
            if step != Direction.CENTRE:
                nxt = pos.add(step)
                if is_inside_core_area(nxt, core_footprint, margin=1) and ct.can_move(step):
                    outcome = self.safe.safe_move(step)
                    if outcome.success:
                        return True

        return False

    def _try_build_proactive_gunner(self, ct: Controller) -> bool:
        """Construct Gunners on remaining apron corners for 360-degree omnidirectional coverage."""
        if ct.get_global_resources() < ct.get_gunner_cost():
            return False

        ammo_reserve = ct.read_store(Config.SLOT_AMMO_RESERVE)
        if ammo_reserve < 5:
            return False

        core_footprint = get_core_footprint(ct)
        cx, cy = core_footprint[0].x, core_footprint[0].y

        # Apron corners for Gunners with outward facings
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
                    if bid is None and ct.get_tile_env(tpos) != Environment.WALL:
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

        if is_adjacent(pos, target_pos):
            if ct.can_act() and ct.get_global_resources() >= ct.get_gunner_cost():
                outcome = self.safe.safe_build_gunner(target_pos, target_facing)
                if outcome.success:
                    self.built_gunners.add(target_pos)
                    self.hold_target = None
                    if Config.DEBUG_DEFENSE:
                        print(f"GUNNER_BUILT builder_id={ct.get_id()} pos={target_pos} facing={target_facing}", file=sys.stderr)
                    return True
            res = self._hold_position(ct, target_pos, "COOLDOWN" if not ct.can_act() else "NO_RESOURCES")
            return res.consumed

        if pos != target_pos and ct.get_move_cooldown() == 0:
            step = get_greedy_step(pos, target_pos)
            if step != Direction.CENTRE:
                nxt = pos.add(step)
                if is_inside_core_area(nxt, core_footprint, margin=1) and ct.can_move(step):
                    outcome = self.safe.safe_move(step)
                    if outcome.success:
                        return True

        return False

    def _try_sentinel_shield(self, ct: Controller) -> bool:
        """Sentinel Shield: Protect knight's own far Harvesters (>5 tiles from Core) with Sentinels."""
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

        val = ct.read_store(Config.SLOT_ENEMY_CORE)
        enemy_core = unpack_pos(val)
        if enemy_core is None:
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
            if execute_bug2_evasion(ct, pos, sentinel_pos, self.pos_history, self.seen_walls):
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
                    bpos = ct.get_position(bid)
                    if ct.can_fire(bpos):
                        btype = ct.get_entity_type(bid)
                        priority = 1
                        if btype in (EntityType.GUNNER, EntityType.SENTINEL, EntityType.LAUNCHER):
                            priority = 5
                        elif btype == EntityType.HARVESTER:
                            priority = 2
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
                epos = ct.get_position(eid)
                d = pos.distance_squared(epos)
                btype = ct.get_entity_type(eid)
                priority = 1
                if btype in (EntityType.GUNNER, EntityType.SENTINEL, EntityType.LAUNCHER):
                    priority = 4
                elif btype == EntityType.BUILDER_BOT:
                    priority = 3
                elif btype == EntityType.HARVESTER:
                    priority = 2

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
