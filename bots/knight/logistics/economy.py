from __future__ import annotations

from collections import deque
from enum import Enum
import sys
from typing import Optional, Set, Tuple
from fcode import Controller, Direction, EntityType, Environment, GameConstants, Position

from blackboard.belt_protocol import (
    belt_alert_is_fresh,
    clear_belt_alert_if_matches,
    pack_belt_alert,
    unpack_belt_alert,
    write_belt_alert,
)
from blackboard.ore_protocol import (
    OreLifecycle,
    get_invalid_ore_reason,
    is_confirmed_ore_target,
    is_plausible_ore_target,
    pos_key,
)
from blackboard.storecoding import (
    pack_ore_discovery,
    pack_ore_status,
    pack_pos,
    unpack_builder_intent,
    unpack_ore_discovery,
    unpack_ore_status,
    unpack_pos,
)
from behavior.movement_skill import MovementSkill
from combat.dismantle import try_dismantle_adjacent_hostile, try_demolition_frenzy_action
from combat.smart_barriers import try_block_gunner_lines
from config import Config
from engine.outcome import Reason
from engine.safe_controller import SafeController
from engine.step_result import StepResult
from exploration.frontier import FrontierExplorer
from exploration.spread_zone import SpreadZone, assign_spread_zone, in_spread_zone
from exploration.symmetry import SymmetryHints
from logistics.belt_memory import BeltMemory, RelayRoute, RelayStatus
from logistics.belt_repair import BeltRepairSkill, TileState
from spatial.movement import (
    execute_bug2_evasion,
    find_builder_navigation_path,
    get_greedy_step,
    is_in_bounds,
    is_traversable,
)
from utils.geometry import (
    get_core_footprint,
    get_reserved_builder_ids,
    is_adjacent,
    is_in_friendly_half,
    is_in_vision,
    is_valid_ore_target,
)
from utils.support import SupportSkill


class EconomyRoleState(str, Enum):
    """Explicit lifecycle states for Economy Builder Bots."""
    IDLE = "IDLE"
    SCOUTING = "SCOUTING"
    COMMITTING = "COMMITTING"
    BUILDING_HARVESTER = "BUILDING_HARVESTER"
    PAVING_BELT = "PAVING_BELT"
    RELAY_COMPLETE = "RELAY_COMPLETE"
    EXPANDING_AGAIN = "EXPANDING_AGAIN"
    DEFENDING = "DEFENDING"
    RETREATING = "RETREATING"


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

    # If diagonal or separated, use primary cardinal direction
    card = curr.cardinal_direction_to(nxt)
    if card.is_cardinal() and card != Direction.CENTRE:
        return card
    return Direction.EAST


def get_cardinal_core_step(pos: Position, core_footprint: list[Position]) -> Direction:
    """Find cardinal direction from pos into an orthogonally adjacent core tile."""
    for d in (Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST):
        if pos.add(d) in core_footprint:
            return d
    nearest = get_nearest_core_tile(pos, core_footprint)
    card = pos.cardinal_direction_to(nearest)
    return card if (card.is_cardinal() and card != Direction.CENTRE) else Direction.EAST


def find_disconnected_harvester(
    ct: Controller,
    core_footprint: list[Position],
    safe: SafeController,
) -> Optional[Position]:
    """Soft check: returns position of an existing friendly harvester that is NOT connected to Core."""
    my_team = ct.get_team()
    for bid in safe.get_nearby_buildings(400):
        try:
            if ct.get_team(bid) == my_team and ct.get_entity_type(bid) == EntityType.HARVESTER:
                hpos = ct.get_position(bid)
                connected = False
                for d in (Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST):
                    adj = hpos.add(d)
                    if not is_in_bounds(ct, adj):
                        continue
                    if adj in core_footprint:
                        connected = True
                        break
                    abid = ct.get_tile_building_id(adj)
                    if abid is not None and ct.get_team(abid) == my_team and ct.get_entity_type(abid) == EntityType.CONVEYOR:
                        curr = adj
                        seen_tiles = {hpos, adj}
                        for _ in range(Config.MAX_BELT_RELAY_HOPS):
                            facing = ct.get_direction(abid)
                            if not facing or facing == Direction.CENTRE:
                                break
                            nxt = curr.add(facing)
                            if not is_in_bounds(ct, nxt) or nxt in seen_tiles:
                                break
                            if nxt in core_footprint:
                                connected = True
                                break
                            seen_tiles.add(nxt)
                            abid = ct.get_tile_building_id(nxt)
                            if abid is None or ct.get_team(abid) != my_team or ct.get_entity_type(abid) != EntityType.CONVEYOR:
                                break
                            curr = nxt
                        if connected:
                            break
                if not connected:
                    return hpos
        except Exception:
            continue
    return None


class EconomyBuilder:
    """Logistics and economic expansion state machine for Builder Bots."""

    def __init__(self, safe: Optional[SafeController] = None) -> None:
        self.safe = safe or SafeController(None)  # type: ignore
        self.support = SupportSkill(self.safe)
        self.mover = MovementSkill(self.safe)
        self.lifecycle = OreLifecycle()
        self.belt_repair = BeltRepairSkill(self.safe)
        self.belt_memory = BeltMemory()
        self.patrol_relay_id: Optional[int] = None
        self.patrol_index: int = 0
        self.repair_tile: Optional[Position] = None
        self.repair_attempts: int = 0
        self.role_state: EconomyRoleState = EconomyRoleState.IDLE

        # Phase H: Holding Gate & Retreat Hysteresis State
        self.hold_target: Optional[Position] = None
        self.hold_reason: Optional[str] = None
        self.hold_turns: int = 0
        self.retreat_timer: int = 0
        self.retreat_target: Optional[Position] = None
        self.last_threat_round: int = -10000
        self.last_threat_pos: Optional[Position] = None

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
        self.mover.safe = safe
        self.belt_repair.safe = safe

    def _transition_state(self, ct: Controller, new_state: EconomyRoleState) -> None:
        """Record state transition and emit structured telemetry."""
        if self.role_state != new_state:
            if Config.DEBUG_ECONOMY:
                print(
                    f"ROLE_STATE_CHANGE builder_id={ct.get_id()} old={self.role_state.value} "
                    f"new={new_state.value} round={ct.get_current_round()}",
                    file=sys.stderr,
                )
            self.role_state = new_state

    def _is_valid_ore(self, ct: Controller, pos: Position) -> bool:
        """Check if an ore coordinate is valid, combining blacklisting and confirmation."""
        core_footprint = get_core_footprint(ct)
        return self.lifecycle.is_valid_candidate(ct, pos, core_footprint)

    def _report_invalid_ore(self, ct: Controller, pos: Position, reason: str) -> None:
        """Report invalid ore to shared blackboard and local blacklist."""
        self.lifecycle.report_invalid_ore(ct, pos, reason)

    def run_economy_builder(self, ct: Controller) -> None:
        """Main tick execution for Economy Builder Bot."""
        self.safe.begin_turn(ct)
        pos = ct.get_position()
        curr_rnd = ct.get_current_round()
        my_id = ct.get_id()

        if self.last_pos == pos:
            if ct.get_move_cooldown() == 0:
                self.stuck_turns += 1
        else:
            self.stuck_turns = 0
        self.last_pos = pos

        if not self.pos_history or self.pos_history[-1] != pos:
            self.pos_history.append(pos)
            if len(self.pos_history) > 10:
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

        # 3. Active Counter-Attack: Dismantle adjacent enemy weapons/turrets/units immediately
        if try_dismantle_adjacent_hostile(ct, self.safe):
            self.stuck_turns = 0
            return

        # 4. Decoupled Expansion Leashing & Proximity Check
        policy = ct.read_store(Config.SLOT_EXPANSION_POLICY)
        dist_sq_to_core = pos.distance_squared(core_tile)
        reserved_ids = get_reserved_builder_ids(ct)
        reserved_builder = my_id in reserved_ids
        near_core = dist_sq_to_core <= (Config.CORE_LEASH_WIDE ** 2)

        threat_alert = ct.read_store(Config.SLOT_ALERTS)
        mode = ct.read_store(Config.SLOT_TACTICAL_MODE)

        # 5. Demolition Defense: Swarm and dismantle enemy weapons/sentinels near base when healthy
        if (near_core or mode >= 1 or threat_alert != 0) and ct.get_hp() >= Config.RETREAT_HP_THRESHOLD:
            if try_demolition_frenzy_action(ct, self.safe, self.pos_history, self.seen_walls):
                self.stuck_turns = 0
                return

        # 6. Evasion: Step away if damaged or facing lethal turret fire
        if self._try_evade_threat(ct):
            self.current_step = "evading"
            self.stuck_turns = 0
            return

        # Strategic Smart Barriers: Block enemy gunner lines if near base
        if near_core and try_block_gunner_lines(ct, self.safe, core_footprint):
            self.stuck_turns = 0
            return

        global_flags = ct.read_store(Config.SLOT_GLOBAL_FLAGS)
        need_scouts = bool(global_flags & Config.FLAG_NEED_SCOUTS)
        emergency_exp = bool(global_flags & Config.FLAG_EMERGENCY_EXPLORATION)
        emergency_recall = bool(global_flags & Config.FLAG_CORE_THREAT) and (dist_sq_to_core <= (Config.CORE_LEASH_TIGHT ** 2))

        # Allow reserved builders to scout after ALLOW_RESERVED_SCOUT_AFTER_ROUND in emergency
        if emergency_exp and curr_rnd >= Config.ALLOW_RESERVED_SCOUT_AFTER_ROUND:
            reserved_builder = False

        # Phase 14.3: Role-Isolated Commitment Abort Logic
        my_team = ct.get_team()
        harvester_count = sum(
            1 for bid in self.safe.get_nearby_buildings()
            if ct.get_team(bid) == my_team and ct.get_entity_type(bid) == EntityType.HARVESTER
        )

        if policy == Config.EXPANSION_LEASHED and self.committed_ore is not None and harvester_count > 0:
            if reserved_builder or emergency_recall:
                if not (is_adjacent(pos, self.committed_ore) and ct.can_act() and ct.get_global_resources() >= ct.get_harvester_cost()):
                    if Config.DEBUG_ECONOMY:
                        print(
                            f"EXPANSION_ABORT builder_id={my_id} ore={self.committed_ore} "
                            f"round={curr_rnd} policy={policy}",
                            file=sys.stderr,
                        )
                    self.committed_ore = None
                    self.target_ore = None
            else:
                if Config.DEBUG_ECONOMY and (curr_rnd % 50 == 0):
                    print(
                        f"ECONOMY_CONTINUATION builder_id={my_id} round={curr_rnd} "
                        f"policy={policy} ore={self.committed_ore}",
                        file=sys.stderr,
                    )

        # Opportunistic belt repair if adjacent to any broken conveyor gap
        if self.belt_repair.try_repair_adjacent_belt(ct, core_footprint):
            self.stuck_turns = 0
            return
        else:
            self.flexed_to_defense = False
            self.unshackled_logged = False

        # 5. Scouting state transitions
        global_flags = ct.read_store(Config.SLOT_GLOBAL_FLAGS)
        need_scouts = bool(global_flags & Config.FLAG_NEED_SCOUTS)
        emergency_exp = bool(global_flags & Config.FLAG_EMERGENCY_EXPLORATION)

        # Soft check: if an existing harvester is disconnected, prioritize completing its belt line
        if self.target_ore is None and not self.harvester_built:
            disc_h = find_disconnected_harvester(ct, core_footprint, self.safe)
            if disc_h is not None and not OreLifecycle.is_claimed_by_other(disc_h, my_id, curr_rnd):
                self.target_ore = disc_h
                self.committed_ore = disc_h
                self.harvester_built = True
                OreLifecycle.claim_ore(disc_h, my_id, curr_rnd)
                self.belt_path = find_wall_aware_belt_path(
                    ct, disc_h, Config.MAX_BELT_RELAY_HOPS, self.seen_walls
                )
                self.paving_index = 0
                self.belt_block_counter = 0
                if Config.DEBUG_ECONOMY:
                    print(f"DISCONNECTED_HARVESTER_RESCUE builder_id={my_id} harvester={disc_h} round={curr_rnd}", file=sys.stderr)

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

        if self._try_patrol_belts(ct):
            self.current_step = "patrol_repair"
            self.stuck_turns = 0
            return

        self.current_step = "exploring"

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
            if Config.DEBUG_ECONOMY or Config.DEBUG_DEFENSE:
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
            self.hold_target = None
            return False

        if is_adjacent(pos, target_tile):
            if ct.can_act() and ct.get_global_resources() >= ct.get_barrier_cost():
                outcome = self.safe.safe_build_barrier(target_tile)
                if outcome.success:
                    self.hold_target = None
                    return True
            res = self._hold_position(ct, target_tile, "COOLDOWN" if not ct.can_act() else "NO_RESOURCES")
            return res.consumed

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
                if is_in_friendly_half(ct, tile, core_footprint) and is_valid_ore_target(ct, tile, core_footprint):
                    bid = ct.get_tile_building_id(tile)
                    if bid is not None and ct.get_entity_type(bid) == EntityType.HARVESTER:
                        OreLifecycle.record_harvester_built(tile)
                    else:
                        OreLifecycle.record_discovered_ore(tile)
                        if tile not in self.broadcasted_ores:
                            ct.write_store(Config.SLOT_ORE_DISCOVERY, pack_ore_discovery(tile, curr_rnd))
                            self.broadcasted_ores.add(tile)

    def _try_evade_threat(self, ct: Controller) -> bool:
        """Priority 1: Sticky retreat hysteresis when damaged or facing hostile turret fire."""
        curr_rnd = ct.get_current_round()
        my_id = ct.get_id()
        hp = ct.get_hp()
        pos = ct.get_position()
        core_footprint = get_core_footprint(ct)
        nearest_core = min(core_footprint, key=lambda c: pos.distance_squared(c)) if core_footprint else Position(0, 0)
        dist_sq_to_core = pos.distance_squared(nearest_core)

        # If already at Core and healthy, do not lock in retreat — allow active defense/demolition
        if dist_sq_to_core <= 4 and hp >= Config.RETREAT_HP_THRESHOLD:
            self.retreat_timer = 0
            self.retreat_target = None
            return False

        threat_detected = False
        threat_pos: Optional[Position] = None
        if hp < Config.RETREAT_HP_THRESHOLD:
            threat_detected = True
        else:
            for bid in self.safe.get_nearby_buildings(Config.BUILDER_BOT_VISION_RADIUS_SQ):
                if ct.get_team(bid) != ct.get_team() and ct.get_entity_type(bid) in (EntityType.GUNNER, EntityType.SENTINEL):
                    threat_detected = True
                    threat_pos = ct.get_position(bid)
                    break

        if threat_detected:
            if self.retreat_timer == 0:
                print(
                    f"RETREAT_LATCH builder_id={my_id} timer={Config.RETREAT_STICKY_TURNS} "
                    f"reason={'HP_LOW' if hp < Config.RETREAT_HP_THRESHOLD else 'TURRET_THREAT'} round={curr_rnd}",
                    file=sys.stderr,
                )
            self.retreat_timer = Config.RETREAT_STICKY_TURNS
            self.retreat_target = nearest_core
            self.last_threat_round = curr_rnd
            self.last_threat_pos = threat_pos

        if self.retreat_timer > 0:
            self.retreat_timer -= 1
            if ct.get_move_cooldown() == 0 and self.retreat_target is not None:
                execute_bug2_evasion(ct, pos, self.retreat_target, self.pos_history, self.seen_walls)

            if self.retreat_timer == 0 or dist_sq_to_core <= 4:
                if hp >= Config.RETREAT_CLEAR_HP or (curr_rnd - self.last_threat_round >= Config.RETREAT_MEMORY_TURNS) or dist_sq_to_core <= 4:
                    if self.retreat_target is not None:
                        print(f"RETREAT_CLEAR builder_id={my_id} round={curr_rnd}", file=sys.stderr)
                    self.retreat_target = None
                    self.retreat_timer = 0

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

            # Phase G.5: Save relay in local BeltMemory before resetting belt_path
            if self.target_ore is not None and self.belt_path:
                harvester_pos = self.target_ore
                relay_tuple = tuple(self.belt_path)
                curr_rnd = ct.get_current_round()
                self.belt_memory.add_relay(harvester_pos, relay_tuple, curr_rnd)
                print(
                    f"RELAY_SAVED builder_id={ct.get_id()} harvester={harvester_pos} "
                    f"path_len={len(relay_tuple)} round={curr_rnd}",
                    file=sys.stderr,
                )

            # Transition to new expansion ore target immediately
            self.harvester_built = False
            self.target_ore = None
            self.committed_ore = None
            self.belt_path = []
            self.paving_index = 0
            self.belt_block_counter = 0
            self.is_scouting = True
            self.no_ore_turns = Config.NO_ORE_SCOUT_THRESHOLD_TURNS + 1

            if self.spread_zone is not None:
                new_band = min(2, self.spread_zone.band + 1)
                self.spread_zone = SpreadZone(self.spread_zone.quadrant, new_band)
                if Config.DEBUG_ECONOMY:
                    print(
                        f"RELAY_COMPLETE builder_id={ct.get_id()} round={ct.get_current_round()} "
                        f"next_zone={self.spread_zone.quadrant}/{self.spread_zone.band}",
                        file=sys.stderr,
                    )
            return False

        target_tile = self.belt_path[self.paving_index]

        # Determine feed direction toward next tile in path or Core
        core_footprint = get_core_footprint(ct)
        if self.paving_index + 1 < len(self.belt_path):
            nxt_dest = self.belt_path[self.paving_index + 1]
            facing = determine_conveyor_direction(target_tile, nxt_dest)
        else:
            facing = get_cardinal_core_step(target_tile, core_footprint)
            nxt_dest = target_tile.add(facing)

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
                    self.hold_target = None
                    return True
                elif outcome.reason == Reason.BLOCKED:
                    self.belt_block_counter += 1
                    if self.belt_block_counter >= Config.BELT_OBSTRUCTION_PATIENCE:
                        self.belt_path = []
                        self.belt_block_counter = 0
                    return True
            res = self._hold_position(ct, target_tile, "COOLDOWN" if not ct.can_act() else "NO_RESOURCES")
            return res.consumed

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

        return True

    def _try_patrol_belts(self, ct: Controller) -> bool:
        """Phase G.6: Execute patrol inspection and repairs along locally known conveyor relays."""
        if ct.get_hp() < Config.RETREAT_HP_THRESHOLD:
            return False
        if ct.get_cpu_time_elapsed() > Config.CPU_SEARCH_VALVE_MICROSEC:
            return False

        curr_rnd = ct.get_current_round()
        my_id = ct.get_id()
        core_footprint = get_core_footprint(ct)
        flags = ct.read_store(Config.SLOT_GLOBAL_FLAGS)
        repair_needed_flag = bool(flags & Config.FLAG_BELT_REPAIR_NEEDED)

        # 1. Resolve or activate active patrol relay
        relay: Optional[RelayRoute] = None
        if self.patrol_relay_id is not None:
            relay = self.belt_memory.get_relay(self.patrol_relay_id)
            if relay is None or relay.status == RelayStatus.RETIRED:
                self.patrol_relay_id = None
                self.patrol_index = 0
                relay = None

        if relay is None:
            # Check conditions to activate patrol:
            # A. FLAG_BELT_REPAIR_NEEDED is set
            # B. Builder has no active ore target
            # C. Builder is idle/scouting and a relay is patrol-due
            # D. Builder sees a fresh SLOT_BELT_ALERT that matches one of its relays
            belt_alert_raw = ct.read_store(Config.SLOT_BELT_ALERT)
            alert_pos: Optional[Position] = None
            if belt_alert_is_fresh(ct, belt_alert_raw):
                alert_pos, _ = unpack_belt_alert(belt_alert_raw)

            matching_relay: Optional[RelayRoute] = None
            if alert_pos is not None:
                for r in self.belt_memory.active_relays():
                    if alert_pos in r.path:
                        matching_relay = r
                        break

            due_relays = self.belt_memory.patrol_due(curr_rnd, Config.BELT_PATROL_INTERVAL_TURNS)

            if matching_relay is not None:
                relay = matching_relay
            elif (repair_needed_flag or self.target_ore is None or self.is_scouting) and due_relays:
                relay = due_relays[0]

            if relay is not None:
                self.patrol_relay_id = relay.relay_id
                self.patrol_index = 0
                print(
                    f"BELT_PATROL_START builder_id={my_id} relay_id={relay.relay_id} round={curr_rnd}",
                    file=sys.stderr,
                )

        if relay is None:
            return False

        # 2. Advance patrol along relay.path from self.patrol_index
        path_len = len(relay.path)
        if self.patrol_index >= path_len:
            # Reached the end of relay and verified connection to Core
            relay.status = RelayStatus.ACTIVE
            relay.last_verified_round = curr_rnd
            relay.broken_index = None
            print(
                f"BELT_RELAY_VERIFIED builder_id={my_id} relay_id={relay.relay_id} round={curr_rnd}",
                file=sys.stderr,
            )
            self.patrol_relay_id = None
            self.patrol_index = 0
            return True

        target_tile = relay.path[self.patrol_index]
        state = self.belt_repair.inspect_relay_tile(ct, relay, self.patrol_index, core_footprint)

        if state == TileState.OK:
            # Tile is fine, advance index and step toward next tile
            self.patrol_index += 1
            if self.patrol_index < path_len:
                nxt_tile = relay.path[self.patrol_index]
                if ct.get_move_cooldown() == 0:
                    execute_bug2_evasion(ct, ct.get_position(), nxt_tile, self.pos_history, self.seen_walls)
            return True

        # Tile is broken / blocked / missing / unknown
        if state != TileState.UNKNOWN:
            relay.status = RelayStatus.BROKEN
            relay.broken_index = self.patrol_index
            print(
                f"BELT_GAP_FOUND builder_id={my_id} relay_id={relay.relay_id} "
                f"tile={target_tile} state={state.name} round={curr_rnd}",
                file=sys.stderr,
            )

        # Attempt repair
        outcome = self.belt_repair.try_repair_tile(
            ct, relay, self.patrol_index, core_footprint, self.pos_history, self.seen_walls
        )
        if outcome.success:
            new_state = self.belt_repair.inspect_relay_tile(ct, relay, self.patrol_index, core_footprint)
            if new_state == TileState.OK:
                print(
                    f"BELT_REPAIR_SUCCESS builder_id={my_id} relay_id={relay.relay_id} "
                    f"tile={target_tile} action={outcome.reason.name} round={curr_rnd}",
                    file=sys.stderr,
                )
                self.patrol_index += 1
            return True
        else:
            if outcome.reason not in (Reason.NOT_ADJACENT, Reason.ACTION_PERFORMED, Reason.OUT_OF_VISION):
                print(
                    f"BELT_REPAIR_FAIL builder_id={my_id} relay_id={relay.relay_id} "
                    f"tile={target_tile} reason={outcome.reason.name} round={curr_rnd}",
                    file=sys.stderr,
                )
            return True

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
                OreLifecycle.release_claim(self.committed_ore, my_id)
                self._report_invalid_ore(ct, self.committed_ore, reason)
                self.committed_ore = None
                self.target_ore = None
            elif self.commit_round > 0 and (curr_rnd - self.commit_round) > Config.UNREACHABLE_ORE_TIMEOUT:
                if Config.DEBUG_ECONOMY:
                    print(f"EXPANSION_TIMEOUT builder_id={my_id} ore={self.committed_ore} round={curr_rnd}", file=sys.stderr)
                OreLifecycle.release_claim(self.committed_ore, my_id)
                self.unreachable_ores[self.committed_ore] = curr_rnd
                self.committed_ore = None
                self.target_ore = None
            else:
                self.target_ore = self.committed_ore

        reserved_ids = get_reserved_builder_ids(ct)
        reserved_builder = my_id in reserved_ids
        global_flags = ct.read_store(Config.SLOT_GLOBAL_FLAGS)
        emergency_exp = bool(global_flags & Config.FLAG_EMERGENCY_EXPLORATION)
        my_team = ct.get_team()
        harvester_count = sum(
            1 for bid in self.safe.get_nearby_buildings()
            if ct.get_team(bid) == my_team and ct.get_entity_type(bid) == EntityType.HARVESTER
        )

        if self.target_ore is None or not self._is_valid_ore(ct, self.target_ore):
            self.target_ore = self.find_ore_target(ct)
            allow_commit = (
                policy in (Config.EXPANSION_FREE, Config.EXPANSION_PARTIAL)
                or (not reserved_builder)
                or harvester_count == 0
                or emergency_exp
            )
            if self.target_ore is not None and allow_commit:
                self.committed_ore = self.target_ore
                self.commit_round = curr_rnd
                self.commit_policy = policy
                OreLifecycle.claim_ore(self.committed_ore, my_id, curr_rnd)
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
                self._report_invalid_ore(ct, self.target_ore, reason)
                self.target_ore = None
                self.committed_ore = None
                return False
            if ct.can_act() and ct.get_global_resources() >= ct.get_harvester_cost():
                outcome = self.safe.safe_build_harvester(self.target_ore)
                if outcome.success:
                    OreLifecycle.record_harvester_built(self.target_ore)
                    self.harvester_built = True
                    self.committed_ore = None
                    self.belt_path = find_wall_aware_belt_path(ct, self.target_ore, Config.MAX_BELT_RELAY_HOPS, self.seen_walls)
                    self.paving_index = 0
                    self.belt_block_counter = 0
                    self.hold_target = None
                    return True
            res = self._hold_position(ct, self.target_ore, "COOLDOWN" if not ct.can_act() else "NO_RESOURCES")
            return res.consumed

        # Navigate toward target_ore using BFS & sideways-first evasion
        if ct.get_move_cooldown() == 0:
            if execute_bug2_evasion(ct, pos, self.target_ore, self.pos_history, self.seen_walls):
                return True
            elif self.stuck_turns > 20:
                # Pathing blocked to target_ore — abort and pick alternative target
                if Config.DEBUG_ECONOMY:
                    print(f"PATH_BLOCKED_ABORT builder_id={my_id} ore={self.target_ore} stuck={self.stuck_turns} round={curr_rnd}", file=sys.stderr)
                self.unreachable_ores[self.target_ore] = curr_rnd
                self.target_ore = None
                self.committed_ore = None
                self.stuck_turns = 0
                return False

        return True

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

        # Candidate 4: Shared known unmined ore registry from OreLifecycle
        for cand_ore in OreLifecycle.get_unmined_candidates(my_id, curr_rnd):
            if cand_ore not in candidates and cand_ore not in self.unreachable_ores and self._is_valid_ore(ct, cand_ore):
                candidates.append(cand_ore)

        # Filter out candidates actively claimed by other friendly builders
        candidates = [c for c in candidates if not OreLifecycle.is_claimed_by_other(c, my_id, curr_rnd)]

        if not candidates:
            return None

        # Check secondary expansion preferences
        my_team = ct.get_team()
        harvester_count = sum(
            1 for bid in self.safe.get_nearby_buildings()
            if ct.get_team(bid) == my_team and ct.get_entity_type(bid) == EntityType.HARVESTER
        )
        prefer_far = (harvester_count >= 2)

        scored: list[tuple[int, Position]] = []
        has_far_candidate = False

        for cand in candidates:
            dist_sq = cand.distance_squared(core_tile)
            is_far = dist_sq >= Config.SECONDARY_EXPANSION_MIN_DIST_SQ
            if is_far:
                has_far_candidate = True

            if not prefer_far:
                # Home security: prioritize close ore (< 100 dist_sq) first
                score = 150 if not is_far else 50
            else:
                # Expansion phase: prioritize far ore
                score = 150 if is_far else 30

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
            my_cand_dist_sq = pos.distance_squared(cand)
            score -= my_cand_dist_sq // 2

            # Deconfliction: if another friendly builder is adjacent or closer to this candidate, discount
            for uid in self.safe.get_nearby_units():
                if uid != my_id and ct.get_team(uid) == my_team:
                    upos = ct.get_position(uid)
                    if is_adjacent(upos, cand):
                        score -= 300
                        break
                    other_cand_dist_sq = upos.distance_squared(cand)
                    if other_cand_dist_sq < my_cand_dist_sq:
                        score -= 80

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

        if not scored or scored[0][0] <= 0:
            return None

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

        # If builder crossed into enemy half, dismantle hostiles or return to friendly territory
        if not is_in_friendly_half(ct, pos, core_footprint):
            if try_demolition_frenzy_action(ct, self.safe, self.pos_history, self.seen_walls):
                return True
            if execute_bug2_evasion(ct, pos, core_pos, self.pos_history, self.seen_walls):
                return True

        # On friendly territory: assist with demolition frenzy, repairs, or smart barriers
        if try_demolition_frenzy_action(ct, self.safe, self.pos_history, self.seen_walls):
            return True
        if self.belt_repair.try_repair_adjacent_belt(ct, core_footprint):
            return True
        if self.support.try_heal_damaged_structures(ct):
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
        recent_visited = set(self.pos_history[-4:]) if self.pos_history else set()
        for d in (primary_dir, primary_dir.rotate_right(), primary_dir.rotate_left(), primary_dir.opposite()):
            if d.is_cardinal():
                nxt = pos.add(d)
                if is_traversable(ct, nxt, self.seen_walls) and ct.can_move(d) and nxt not in recent_visited:
                    exits = sum(1 for ed in cardinals if is_traversable(ct, nxt.add(ed), self.seen_walls))
                    valid_steps.append((exits, d == primary_dir, d))
        if valid_steps:
            valid_steps.sort(key=lambda item: (item[1], item[0]), reverse=True)
            outcome = self.safe.safe_move(valid_steps[0][2])
            if outcome.success:
                return True

        return False

    def _is_valid_ore(self, ct: Controller, ore_pos: Position) -> bool:
        """True if ore_pos is in bounds, in friendly half, not inside Core footprint, and not occupied/non-ore when in vision."""
        core_footprint = get_core_footprint(ct)
        if not is_in_friendly_half(ct, ore_pos, core_footprint):
            return False
        return is_valid_ore_target(ct, ore_pos, core_footprint)
