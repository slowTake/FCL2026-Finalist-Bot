"""knight — Logistics Domain: Economy Pipelines, Wall-Aware BFS Paving & Frontier Exploration.

Manages Harvester targeting, single-path wall-aware BFS belt paving, spread zoning,
symmetry hints, frontier boundary exploration, and maze wall-following.
"""

from __future__ import annotations

from collections import deque
import sys
from typing import Optional, Set, Tuple
from fcode import Controller, Direction, EntityType, Environment, GameConstants, Position

from config import Config
from engine.outcome import Reason
from engine.safe_controller import SafeController
from exploration.frontier import FrontierExplorer
from exploration.spread_zone import SpreadZone, assign_spread_zone, in_spread_zone
from exploration.symmetry import SymmetryHints
from spatial.movement import (
    execute_bug2_evasion,
    find_builder_navigation_path,
    get_greedy_step,
    is_in_bounds,
    is_traversable,
)
from utils.geometry import (
    get_core_footprint,
    get_invalid_ore_reason,
    get_reserved_builder_ids,
    is_adjacent,
    is_in_vision,
    is_valid_ore_target,
    pack_ore_discovery,
    pack_pos,
    unpack_builder_intent,
    unpack_ore_discovery,
    unpack_pos,
)
from utils.support import SupportSkill


def get_nearest_core_tile(pos: Position, core_footprint: list[Position]) -> Position:
    """Find tile in core_footprint closest to pos."""
    best_tile = core_footprint[0]
    best_dist = pos.distance_squared(best_tile)
    for c in core_footprint[1:]:
        d = pos.distance_squared(c)
        if d < best_dist:
            best_dist = d
            best_tile = c
    return best_tile


def find_wall_aware_belt_path(
    ct: Controller,
    harvester_pos: Position,
    max_hops: int = 40,
    seen_walls: Optional[Set[Position]] = None,
) -> list[Position]:
    """Single-path Wall-Aware BFS pathfinder to lay conveyor belt toward Core with CPU valve."""
    core_footprint = get_core_footprint(ct)
    target_core = get_nearest_core_tile(harvester_pos, core_footprint)
    seen = seen_walls if seen_walls is not None else set()

    queue: deque[tuple[Position, list[Position]]] = deque([(harvester_pos, [])])
    visited: set[Position] = {harvester_pos}

    for c in core_footprint:
        visited.add(c)

    while queue:
        if ct.get_cpu_time_elapsed() > Config.CPU_SEARCH_VALVE_MICROSEC:
            return []

        curr, path = queue.popleft()

        for d in (Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST):
            nxt = curr.add(d)

            if not is_in_bounds(ct, nxt):
                continue

            if nxt in core_footprint:
                return path

            if nxt in visited:
                continue

            visited.add(nxt)

            if nxt in seen:
                continue

            my_pos = ct.get_position()
            if is_in_vision(ct, my_pos, nxt, Config.BUILDER_BOT_VISION_RADIUS_SQ):
                try:
                    if ct.get_tile_env(nxt) == Environment.WALL:
                        seen.add(nxt)
                        continue

                    bid = ct.get_tile_building_id(nxt)
                    if bid is not None:
                        btype = ct.get_entity_type(bid)
                        if btype not in (EntityType.CONVEYOR, EntityType.SPLITTER):
                            continue
                except Exception:
                    pass

            if len(path) + 1 > max_hops:
                continue

            queue.append((nxt, path + [nxt]))

    return []


def determine_conveyor_direction(curr: Position, nxt: Position) -> Direction:
    """Determine Direction required for conveyor at curr to feed nxt."""
    dx = nxt.x - curr.x
    dy = nxt.y - curr.y

    if dx == 1 and dy == 0:
        return Direction.EAST
    elif dx == -1 and dy == 0:
        return Direction.WEST
    elif dx == 0 and dy == 1:
        return Direction.SOUTH
    elif dx == 0 and dy == -1:
        return Direction.NORTH
    return Direction.CENTRE


class EconomyBuilder:
    """Logistics and economic expansion state machine for Builder Bots."""

    def __init__(self, safe: Optional[SafeController] = None) -> None:
        self.safe = safe or SafeController(None)  # type: ignore
        self.support = SupportSkill(self.safe)
        self.target_ore: Optional[Position] = None
        self.harvester_built: bool = False
        self.belt_path: list[Position] = []
        self.paving_index: int = 0
        self.relay_completed: bool = False
        self.last_pos: Optional[Position] = None
        self.stuck_turns: int = 0
        self.seen_walls: set[Position] = set()
        self.unseen_wall_collisions: int = 0
        self.belt_block_counter: int = 0
        self.pos_history: list[Position] = []
        self.unreachable_ores: dict[Position, int] = {}
        self.current_step: str = "init"
        self.flexed_to_defense: bool = False
        self.unshackled_logged: bool = False
        self.ring_radius: int = 4
        self.ring_target: Optional[Position] = None
        self.explored_tiles: set[Position] = set()
        self.ring_stuck_counter: int = 0

        # Expansion commitment state
        self.committed_ore: Optional[Position] = None
        self.commit_round: int = -1
        self.commit_policy: int = Config.EXPANSION_FREE

        # Phase 11R: Frontier & Exploration State
        self.frontier = FrontierExplorer()
        self.symmetry = SymmetryHints()
        self.spread_zone: Optional[SpreadZone] = None
        self.is_scouting: bool = False
        self.no_ore_turns: int = 0
        self.no_far_ore_turns: int = 0
        self.scout_target: Optional[Position] = None
        self.last_scout_target: Optional[Position] = None
        self.broadcasted_ores: set[Position] = set()

    def set_safe_controller(self, safe: SafeController) -> None:
        """Inject active SafeController instance."""
        self.safe = safe
        self.support.safe = safe

    def run_economy_builder(self, ct: Controller) -> None:
        """Main tick execution for Economy Builder Bot."""
        self.safe.begin_turn(ct)
        pos = ct.get_position()
        curr_rnd = ct.get_current_round()
        my_id = ct.get_id()

        if self.last_pos == pos:
            self.stuck_turns += 1
        else:
            self.stuck_turns = 0
        self.last_pos = pos

        self.pos_history.append(pos)
        if len(self.pos_history) > 5:
            self.pos_history.pop(0)

        # 1. Update frontier observation memory
        self.frontier.observe(ct)

        # 2. Assign SpreadZone if uninitialized
        if self.spread_zone is None:
            self.spread_zone = assign_spread_zone(my_id)
            if Config.DEBUG_ECONOMY:
                print(
                    f"SPREAD_ZONE_ASSIGNED builder_id={my_id} quadrant={self.spread_zone.quadrant} "
                    f"band={self.spread_zone.band} round={curr_rnd}",
                    file=sys.stderr,
                )

        core_footprint = get_core_footprint(ct)
        core_tile = core_footprint[0]
        self.symmetry.initialize(ct, core_tile)

        # 3. Scan vision for ore and broadcast freshness-aware discovery
        self._scan_and_broadcast_ore(ct)

        if self._try_evade_threat(ct):
            self.current_step = "evading"
            self.stuck_turns = 0
            return

        # 4. Decoupled Expansion Leashing based on dynamic role assignment
        policy = ct.read_store(Config.SLOT_EXPANSION_POLICY)
        dist_sq_to_core = pos.distance_squared(core_tile)
        reserved_ids = get_reserved_builder_ids(ct)
        reserved_builder = my_id in reserved_ids
        near_core = dist_sq_to_core <= (Config.CORE_LEASH_WIDE ** 2)

        global_flags = ct.read_store(Config.SLOT_GLOBAL_FLAGS)
        need_scouts = bool(global_flags & Config.FLAG_NEED_SCOUTS)
        emergency_exp = bool(global_flags & Config.FLAG_EMERGENCY_EXPLORATION)

        # Allow reserved builders to scout after ALLOW_RESERVED_SCOUT_AFTER_ROUND in emergency
        if emergency_exp and curr_rnd >= Config.ALLOW_RESERVED_SCOUT_AFTER_ROUND:
            reserved_builder = False

        # Expansion commitment cancellation on severe leash
        if policy == Config.EXPANSION_LEASHED and self.committed_ore is not None:
            if not (is_adjacent(pos, self.committed_ore) and ct.can_act() and ct.get_global_resources() >= ct.get_harvester_cost()):
                if Config.DEBUG_ECONOMY:
                    print(
                        f"EXPANSION_ABORT builder_id={my_id} ore={self.committed_ore} "
                        f"round={curr_rnd} policy={policy}",
                        file=sys.stderr,
                    )
                self.committed_ore = None
                self.target_ore = None

        should_leash = (
            not need_scouts and (
                policy == Config.EXPANSION_LEASHED
                or (policy == Config.EXPANSION_PARTIAL and reserved_builder)
            ) and near_core
        )

        if should_leash:
            self.flexed_to_defense = True
            self.current_step = "flex_defense"
            if self.support.try_heal_damaged_structures(ct):
                self.stuck_turns = 0
                return
            if self._try_build_barrier_ring(ct):
                self.stuck_turns = 0
                return
            if dist_sq_to_core > Config.CORE_LEASH_TIGHT ** 2 and ct.get_move_cooldown() == 0:
                if execute_bug2_evasion(ct, pos, core_tile, self.pos_history, self.seen_walls):
                    self.stuck_turns = 0
                    return
        else:
            self.flexed_to_defense = False
            if policy != Config.EXPANSION_LEASHED and not reserved_builder and not self.unshackled_logged:
                if Config.DEBUG_ECONOMY:
                    print(
                        f"EXPANSION_UNSHACKLED builder_id={my_id} policy={policy} round={curr_rnd}",
                        file=sys.stderr,
                    )
                self.unshackled_logged = True
            elif policy == Config.EXPANSION_LEASHED:
                self.unshackled_logged = False

        # 5. Scouting state transitions
        global_flags = ct.read_store(Config.SLOT_GLOBAL_FLAGS)
        need_scouts = bool(global_flags & Config.FLAG_NEED_SCOUTS)
        emergency_exp = bool(global_flags & Config.FLAG_EMERGENCY_EXPLORATION)

        if not self.harvester_built:
            if self.target_ore is None:
                self.no_ore_turns += 1
                should_scout = (
                    self.no_ore_turns > 20
                    or need_scouts
                    or emergency_exp
                    or (my_id in (3, 4) and max(ct.get_map_width(), ct.get_map_height()) > 20)
                )
                if should_scout and not self.is_scouting:
                    self.is_scouting = True
                    if Config.DEBUG_ECONOMY:
                        reason = "emergency" if emergency_exp else ("need_scouts" if need_scouts else "no_ore")
                        print(f"SCOUT_ASSIGNED builder_id={my_id} round={curr_rnd} reason={reason}", file=sys.stderr)
            else:
                self.no_ore_turns = 0
                self.is_scouting = False

        if self._try_build_belt(ct):
            self.current_step = "conveyor"
            self.stuck_turns = 0
            return

        if self._try_mine_ore(ct):
            self.current_step = "mining"
            self.stuck_turns = 0
            return
        
        self.current_step = "exploring"

    def _try_build_barrier_ring(self, ct: Controller) -> bool:
        """Construct protective barrier ring on Core apron (excluding spawn gates)."""
        policy = ct.read_store(Config.SLOT_EXPANSION_POLICY)
        mode = ct.read_store(Config.SLOT_TACTICAL_MODE)
        if mode == 0 and policy == Config.EXPANSION_FREE:
            return False

        core_footprint = get_core_footprint(ct)
        cx, cy = core_footprint[0].x, core_footprint[0].y

        barrier_apron = [
            Position(cx - 1, cy),
            Position(cx - 1, cy + 1),
            Position(cx + 2, cy),
            Position(cx + 2, cy + 1),
            Position(cx, cy + 2),
            Position(cx + 1, cy + 2),
        ]

        pos = ct.get_position()
        target_tile: Optional[Position] = None
        for b_tile in barrier_apron:
            if is_in_bounds(ct, b_tile):
                if is_in_vision(ct, pos, b_tile, Config.BUILDER_BOT_VISION_RADIUS_SQ):
                    bid = ct.get_tile_building_id(b_tile)
                    if bid is None or ct.get_entity_type(bid) != EntityType.BARRIER:
                        target_tile = b_tile
                        break

        if target_tile is None:
            return False

        if is_adjacent(pos, target_tile) and ct.can_act():
            if ct.get_global_resources() >= ct.get_barrier_cost():
                outcome = self.safe.safe_build_barrier(target_tile)
                if outcome.success:
                    return True

        if ct.get_move_cooldown() == 0:
            if execute_bug2_evasion(ct, pos, target_tile, self.pos_history, self.seen_walls):
                return True

        return False

    def _scan_and_broadcast_ore(self, ct: Controller) -> None:
        """Scan vision for ORE_TITANIUM and WALL, updating terrain memory and symmetry hints."""
        curr_rnd = ct.get_current_round()
        core_footprint = get_core_footprint(ct)
        for tile in self.safe.get_nearby_tiles():
            env = ct.get_tile_env(tile)
            if env == Environment.WALL:
                self.seen_walls.add(tile)
            elif env == Environment.ORE_TITANIUM:
                if is_valid_ore_target(ct, tile, core_footprint):
                    if tile not in self.broadcasted_ores:
                        # Broadcast fresh ore discovery into slot 9
                        ct.write_store(Config.SLOT_ORE_DISCOVERY, pack_ore_discovery(tile, curr_rnd))
                        self.broadcasted_ores.add(tile)
                        # Add mirrored symmetry hint
                        self.symmetry.add_ore_hint(ct, tile)

    def _try_evade_threat(self, ct: Controller) -> bool:
        """Priority 1: Step away from immediate threats if damaged (HP < threshold)."""
        if ct.get_hp() < Config.RETREAT_HP_THRESHOLD:
            if ct.get_move_cooldown() == 0:
                pos = ct.get_position()
                for d in (Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST):
                    if is_traversable(ct, pos.add(d), self.seen_walls) and ct.can_move(d):
                        outcome = self.safe.safe_move(d)
                        if outcome.success:
                            return True
        return False

    def _try_build_belt(self, ct: Controller) -> bool:
        """Priority 3: Single-path conveyor paving directly along self.belt_path."""
        if not self.harvester_built:
            return False

        pos = ct.get_position()

        # Generate or re-plan belt path if empty
        if not self.belt_path and self.target_ore is not None:
            self.belt_path = find_wall_aware_belt_path(
                ct, self.target_ore, Config.MAX_BELT_RELAY_HOPS, self.seen_walls
            )
            self.paving_index = 0
            self.belt_block_counter = 0

        # Finished paving all tiles in path
        if self.paving_index >= len(self.belt_path):
            self.relay_completed = True
            # Transition to new expansion ore target
            self.harvester_built = False
            self.target_ore = None
            self.committed_ore = None
            self.belt_path = []
            self.paving_index = 0
            self.belt_block_counter = 0
            return False

        target_tile = self.belt_path[self.paving_index]

        # Determine feed direction toward next tile in path or Core
        core_footprint = get_core_footprint(ct)
        nxt_dest = (
            self.belt_path[self.paving_index + 1]
            if (self.paving_index + 1 < len(self.belt_path))
            else get_nearest_core_tile(target_tile, core_footprint)
        )
        facing = determine_conveyor_direction(target_tile, nxt_dest)

        # Check if already paved correctly
        if is_in_vision(ct, pos, target_tile, Config.BUILDER_BOT_VISION_RADIUS_SQ):
            bid = ct.get_tile_building_id(target_tile)
            if bid is not None and ct.get_entity_type(bid) == EntityType.CONVEYOR:
                self.paving_index += 1
                self.belt_block_counter = 0
                return True

        # Step-off if builder is standing on target_tile
        if pos == target_tile:
            if ct.get_move_cooldown() == 0:
                step_dir = pos.cardinal_direction_to(nxt_dest)
                if step_dir != Direction.CENTRE and ct.can_move(step_dir) and is_traversable(ct, pos.add(step_dir), self.seen_walls):
                    outcome = self.safe.safe_move(step_dir)
                    if outcome.success:
                        return True
                for d in (Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST):
                    if ct.can_move(d) and is_traversable(ct, pos.add(d), self.seen_walls):
                        outcome = self.safe.safe_move(d)
                        if outcome.success:
                            return True
            return False

        # Build conveyor if adjacent and affordable
        if is_adjacent(pos, target_tile):
            if ct.can_act() and ct.get_global_resources() >= ct.get_conveyor_cost():
                outcome = self.safe.safe_build_conveyor(target_tile, facing)
                if outcome.success:
                    self.paving_index += 1
                    self.belt_block_counter = 0
                    return True
                elif outcome.reason == Reason.BLOCKED:
                    self.belt_block_counter += 1
                    if self.belt_block_counter >= Config.BELT_OBSTRUCTION_PATIENCE:
                        self.belt_path = []
                        self.belt_block_counter = 0
                    return True
                return False

        # Navigate along belt path
        if ct.get_move_cooldown() == 0:
            if execute_bug2_evasion(ct, pos, target_tile, self.pos_history, self.seen_walls):
                return True
            else:
                self.belt_block_counter += 1
                if self.belt_block_counter >= Config.BELT_OBSTRUCTION_PATIENCE:
                    self.belt_path = []
                    self.belt_block_counter = 0
                return True

        return False

    def _try_mine_ore(self, ct: Controller) -> bool:
        """Priority 4: Navigate to unclaimed ore deposit and construct Harvester."""
        if self.harvester_built:
            return False

        pos = ct.get_position()
        curr_rnd = ct.get_current_round()
        my_id = ct.get_id()
        core_footprint = get_core_footprint(ct)
        policy = ct.read_store(Config.SLOT_EXPANSION_POLICY)

        if self.committed_ore is not None:
            if not self._is_valid_ore(ct, self.committed_ore):
                reason = get_invalid_ore_reason(ct, self.committed_ore, core_footprint) or "INVALID"
                if Config.DEBUG_ECONOMY:
                    print(f"INVALID_ORE_TARGET builder_id={my_id} ore={self.committed_ore} round={curr_rnd} reason={reason}", file=sys.stderr)
                self.committed_ore = None
                self.target_ore = None
            else:
                self.target_ore = self.committed_ore

        if self.target_ore is None or not self._is_valid_ore(ct, self.target_ore):
            self.target_ore = self.find_ore_target(ct)
            if self.target_ore is not None and policy in (Config.EXPANSION_FREE, Config.EXPANSION_PARTIAL):
                self.committed_ore = self.target_ore
                self.commit_round = curr_rnd
                self.commit_policy = policy
                if Config.DEBUG_ECONOMY:
                    print(
                        f"EXPANSION_COMMIT builder_id={ct.get_id()} ore={self.committed_ore} "
                        f"round={curr_rnd} policy={policy}",
                        file=sys.stderr,
                    )

        if self.target_ore is None:
            return self._execute_exploration_walk(ct)

        # If orthogonally adjacent to target_ore, attempt Harvester build
        if is_adjacent(pos, self.target_ore):
            if not self._is_valid_ore(ct, self.target_ore):
                reason = get_invalid_ore_reason(ct, self.target_ore, core_footprint) or "INVALID"
                if Config.DEBUG_ECONOMY:
                    print(f"INVALID_ORE_TARGET builder_id={my_id} ore={self.target_ore} round={curr_rnd} reason={reason}", file=sys.stderr)
                self.target_ore = None
                self.committed_ore = None
                return False
            if ct.can_act() and ct.get_global_resources() >= ct.get_harvester_cost():
                outcome = self.safe.safe_build_harvester(self.target_ore)
                if outcome.success:
                    self.harvester_built = True
                    self.committed_ore = None
                    self.belt_path = find_wall_aware_belt_path(ct, self.target_ore, Config.MAX_BELT_RELAY_HOPS, self.seen_walls)
                    self.paving_index = 0
                    self.belt_block_counter = 0
                    return True
            # Hold position adjacent to target_ore while waiting for resources or action cooldown
            return True

        # Navigate toward target_ore using BFS & sideways-first evasion
        if ct.get_move_cooldown() == 0:
            if execute_bug2_evasion(ct, pos, self.target_ore, self.pos_history, self.seen_walls):
                return True

        return False

    def find_ore_target(self, ct: Controller) -> Optional[Position]:
        """Pure runtime target dispatcher with Secondary Expansion Bias and Deconfliction."""
        pos = ct.get_position()
        curr_rnd = ct.get_current_round()
        my_id = ct.get_id()
        core_footprint = get_core_footprint(ct)
        core_tile = core_footprint[0]
        timeout = Config.UNREACHABLE_ORE_TIMEOUT
        self.unreachable_ores = {
            p: r for p, r in self.unreachable_ores.items() if curr_rnd - r <= timeout
        }

        # Gather candidate ore targets
        candidates: list[Position] = []

        # Candidate 1: Core-authored strategic intent hint (SLOT_BUILDER_INTENT)
        raw_intent = ct.read_store(Config.SLOT_BUILDER_INTENT)
        if raw_intent != 0:
            ix, iy, action, _ = unpack_builder_intent(raw_intent)
            if action == Config.INTENT_EXPAND_ORE:
                intent_ore = Position(ix, iy)
                if intent_ore not in self.unreachable_ores and self._is_valid_ore(ct, intent_ore):
                    candidates.append(intent_ore)

        # Candidate 2: Local vision scan via FrontierExplorer
        loc_ore = self.frontier.local_ore_target(ct)
        if loc_ore is not None and loc_ore not in self.unreachable_ores and self._is_valid_ore(ct, loc_ore):
            if loc_ore not in candidates:
                candidates.append(loc_ore)

        # Candidate 3: Store broadcast far-ore discovery (freshness-verified)
        store_ore = self.frontier.store_ore_target(ct)
        if store_ore is not None and store_ore not in self.unreachable_ores and self._is_valid_ore(ct, store_ore):
            if store_ore not in candidates:
                candidates.append(store_ore)

        if not candidates:
            return None

        # Check secondary expansion preferences
        my_team = ct.get_team()
        harvester_count = sum(
            1 for bid in self.safe.get_nearby_buildings()
            if ct.get_team(bid) == my_team and ct.get_entity_type(bid) == EntityType.HARVESTER
        )
        prefer_far = (harvester_count >= 1)

        scored: list[tuple[int, Position]] = []
        has_far_candidate = False

        for cand in candidates:
            dist_sq = cand.distance_squared(core_tile)
            is_far = dist_sq >= Config.SECONDARY_EXPANSION_MIN_DIST_SQ
            if is_far:
                has_far_candidate = True

            score = 100 if is_far else (50 if not prefer_far else -100)

            # Zone matching bonus
            zone_id = -1
            if self.spread_zone is not None:
                zone_id = self.spread_zone.quadrant
                if in_spread_zone(cand, core_tile, self.spread_zone):
                    score += 50
                if self.spread_zone.band >= 1 and is_far:
                    score += 50

            # Fresh store ore bonus
            if cand == store_ore:
                score += 30

            # Proximity to builder
            score -= pos.distance_squared(cand) // 2

            # Deconfliction: if another friendly builder is already adjacent to this candidate, heavily discount
            for uid in self.safe.get_nearby_units():
                if uid != my_id and ct.get_team(uid) == my_team:
                    upos = ct.get_position(uid)
                    if is_adjacent(upos, cand):
                        score -= 200
                        break

            if Config.DEBUG_ECONOMY:
                print(
                    f"EXPANSION_TARGET_SCORE builder_id={my_id} ore={cand} score={score} "
                    f"dist_sq={dist_sq} zone={zone_id} round={curr_rnd}",
                    file=sys.stderr,
                )
            scored.append((score, cand))

        if has_far_candidate:
            self.no_far_ore_turns = 0
        elif prefer_far:
            self.no_far_ore_turns += 1

        scored.sort(key=lambda item: item[0], reverse=True)

        # If fallback needed when no far ore found for SECONDARY_LOCAL_FALLBACK_TURNS
        if prefer_far and not has_far_candidate and self.no_far_ore_turns < Config.SECONDARY_LOCAL_FALLBACK_TURNS:
            return None

        return scored[0][1]

    def _try_escape_enclosure(self, ct: Controller) -> bool:
        """Wall-contour following for enclosed spawns (surrounded on 3+ sides or in tight corridor)."""
        if ct.get_move_cooldown() != 0:
            return False
        pos = ct.get_position()
        cardinals = (Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST)

        impassable_count = sum(
            1 for d in cardinals
            if not is_traversable(ct, pos.add(d), self.seen_walls)
        )

        # If surrounded on 3 sides or in a 1-tile dead end
        if impassable_count >= 3:
            open_steps = []
            for d in cardinals:
                nxt = pos.add(d)
                if is_traversable(ct, nxt, self.seen_walls) and ct.can_move(d):
                    exits = sum(1 for ed in cardinals if is_traversable(ct, nxt.add(ed), self.seen_walls))
                    open_steps.append((exits, d))
            if open_steps:
                open_steps.sort(key=lambda item: item[0], reverse=True)
                outcome = self.safe.safe_move(open_steps[0][1])
                if outcome.success:
                    return True
        return False

    def _execute_exploration_walk(self, ct: Controller) -> bool:
        """Frontier search & maze-breaking exploration walk."""
        if ct.get_move_cooldown() != 0:
            return False

        pos = ct.get_position()
        my_id = ct.get_id()
        curr_rnd = ct.get_current_round()
        core_footprint = get_core_footprint(ct)
        core_pos = core_footprint[0]

        # Step 0: Check enclosure escape
        if self._try_escape_enclosure(ct):
            return True

        if self.spread_zone is None:
            self.spread_zone = assign_spread_zone(my_id)

        # Step 1: Choose scout/frontier target
        scout_target = self.frontier.choose_scout_target(ct, self.spread_zone, self.symmetry, core_pos)
        if scout_target is not None:
            if Config.DEBUG_ECONOMY and scout_target != self.last_scout_target:
                print(f"FRONTIER_TARGET builder_id={my_id} target={scout_target} round={curr_rnd}", file=sys.stderr)
                self.last_scout_target = scout_target

            # Try BFS through known walkable tiles to frontier
            path = self.frontier.find_path_to_frontier(ct, pos, [scout_target])
            if path:
                outcome = self.safe.safe_move(path[0])
                if outcome.success:
                    return True

            # Fallback to Bug2 navigation toward target
            if execute_bug2_evasion(ct, pos, scout_target, self.pos_history, self.seen_walls):
                return True

        # Step 2: Maze wall follow fallback
        step = self.frontier.wall_follow_fallback(ct)
        if step is not None:
            if Config.DEBUG_ECONOMY and curr_rnd % 10 == 0:
                print(f"MAZE_WALL_FOLLOW builder_id={my_id} round={curr_rnd} hand={self.frontier.wall_hand}", file=sys.stderr)
            outcome = self.safe.safe_move(step)
            if outcome.success:
                return True

        # Step 3: Open direction step fallback
        cardinals = [Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST]
        primary_dir = cardinals[self.spread_zone.quadrant]
        valid_steps = []
        for d in (primary_dir, primary_dir.rotate_right(), primary_dir.rotate_left(), primary_dir.opposite()):
            if d.is_cardinal():
                nxt = pos.add(d)
                if is_traversable(ct, nxt, self.seen_walls) and ct.can_move(d):
                    exits = sum(1 for ed in cardinals if is_traversable(ct, nxt.add(ed), self.seen_walls))
                    valid_steps.append((exits, d == primary_dir, d))
        if valid_steps:
            valid_steps.sort(key=lambda item: (item[1], item[0]), reverse=True)
            outcome = self.safe.safe_move(valid_steps[0][2])
            if outcome.success:
                return True

        return False

    def _is_valid_ore(self, ct: Controller, ore_pos: Position) -> bool:
        """True if ore_pos is in bounds, not inside Core footprint, and not occupied/non-ore when in vision."""
        core_footprint = get_core_footprint(ct)
        return is_valid_ore_target(ct, ore_pos, core_footprint)
