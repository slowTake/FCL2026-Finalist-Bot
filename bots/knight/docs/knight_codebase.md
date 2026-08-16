"""knight — Centralized Hyperparameters & Configuration Registry.

No magic numbers allowed. All macro thresholds, range limits, store slot IDs,
and cost boundaries live here as clean leaf configuration constants.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar


@dataclass(frozen=True)
class Config:
    """Centralized configuration registry for knight RTS bot."""

    # -------------------------------------------------------------
    # Macro & Game Phase Hyperparameters
    # -------------------------------------------------------------
    PHASE_EARLY_TURNS: int = 40
    PHASE_MID_TURNS: int = 300
    PHASE_TIEBREAKER_TURN: int = 850
    FORCE_DISABLE_FINGERPRINT: bool = True
    DISABLE_MAP_REGISTRY: bool = True
    
    # Logging flags
    COMPETITION_MODE: bool = False
    DEBUG_VERBOSE: bool = False
    DEBUG_ECONOMY: bool = True
    DEBUG_DEFENSE: bool = True
    DEBUG_METRICS: bool = True

    MAX_BUILDERS_EARLY: int = 8
    MAX_BUILDERS_BASE: int = 14
    MAX_BUILDERS_CEILING: int = 28

    # Dynamic resource brackets for spawning: [(min_ti, target_builders)]
    BUILDER_TARGET_BRACKETS: ClassVar[tuple[tuple[int, int], ...]] = (
        (2000, 28),
        (500, 20),
        (200, 14),
        (50, 10),
    )

    # -------------------------------------------------------------
    # Store Slot IPC Assignments (Slots 0-15)
    # -------------------------------------------------------------
    # Primary Writers:
    # Core-only: 0, 2, 5, 6, 7, 8, 10, 11, 12, 13, 14, 15
    # Builder-only: 9 (new discoveries only)
    # Reserved: 1, 3, 4
    SLOT_CORE_POS: int = 0          # Core: Offset-encoded Position
    SLOT_ENEMY_CORE: int = 1        # Reserved
    SLOT_AMMO_RESERVE: int = 2      # Core: Global ammo reserve count
    SLOT_ORE_STATUS: int = 3        # Builders/Core: Repurposed from ORE_CLAIMS for shared invalidation
    SLOT_ORE_CLAIMS: int = 3        # Legacy alias for SLOT_ORE_STATUS
    SLOT_ROLE_DEFICITS: int = 4     # Reserved
    SLOT_TACTICAL_MODE: int = 5     # Core: 0=Eco, 1=Balanced, 2=Defense
    SLOT_ALERTS: int = 6            # Core: Packed position of nearest threat
    SLOT_TURRET_COUNT: int = 7      # Core: Active defense turret count
    SLOT_BUILDER_COUNT: int = 8     # Core: Active builder count
    SLOT_ORE_DISCOVERY: int = 9     # Builders: Packed Position & round mod
    SLOT_PROACTIVE_TURRET: int = 10 # Core: 1 if turret construction allowed
    SLOT_GLOBAL_FLAGS: int = 11     # Core: Scout and threat bitmask
    SLOT_EXPANSION_POLICY: int = 12 # Core: 0=Free, 1=Partial, 2=Leashed
    SLOT_BASE_INTEGRITY: int = 13   # Core: Core HP percentage (0-100)
    SLOT_BUILDER_INTENT: int = 14   # Core: Packed target, action, priority
    SLOT_ROLE_ASSIGNMENT: int = 15  # Core: Primary & secondary builder IDs
    SLOT_GLOBAL_STATE: int = 0      # Legacy alias for SLOT_CORE_POS

    # -------------------------------------------------------------
    # Phase 13: Shared Ore Invalidation Status Enums
    # -------------------------------------------------------------
    ORE_STATUS_NONE: int = 0
    ORE_STATUS_OCCUPIED: int = 1
    ORE_STATUS_NOT_ORE: int = 2
    ORE_STATUS_CORE_FOOTPRINT: int = 3
    ORE_STATUS_UNREACHABLE: int = 4

    # -------------------------------------------------------------
    # Phase 13: Local Ore Blacklist TTLs & Intent Lease
    # -------------------------------------------------------------
    ORE_BLACKLIST_OCCUPIED_TTL: int = 500
    ORE_BLACKLIST_NOT_ORE_TTL: int = 1000
    ORE_BLACKLIST_CORE_TTL: int = 100000
    ORE_BLACKLIST_UNREACHABLE_TTL: int = 80
    INTENT_LEASE_TURNS: int = 12
    NO_ORE_SCOUT_THRESHOLD_TURNS: int = 3

    # -------------------------------------------------------------
    # Expansion Policy Blackboard Enums & Hysteresis
    # -------------------------------------------------------------
    EXPANSION_FREE: int = 0
    EXPANSION_PARTIAL: int = 1
    EXPANSION_LEASHED: int = 2
    POLICY_UPGRADE_STABLE_TURNS: int = 8
    POLICY_DOWNGRADE_STABLE_TURNS: int = 2

    # -------------------------------------------------------------
    # Core-Authored Builder Intent Enums
    # -------------------------------------------------------------
    INTENT_NONE: int = 0
    INTENT_EXPAND_ORE: int = 1
    INTENT_BUILD_BELT: int = 2
    INTENT_DEFEND_CORE: int = 3
    INTENT_SCOUT: int = 4
    INTENT_REPAIR: int = 5

    # -------------------------------------------------------------
    # Base Security & Unshackling Thresholds
    # -------------------------------------------------------------
    CORE_HP_SECURE_PCT: int = 80
    CORE_HP_CRITICAL_PCT: int = 55
    RECENT_DAMAGE_WINDOW_TURNS: int = 25
    UNSHACKLE_MIN_DEFENSE_TURRETS: int = 1
    UNSHACKLE_MIN_BARRIER_COUNT: int = 3
    UNSHACKLE_MAX_THREAT_PRESSURE: int = 3
    LEASH_RESERVED_BUILDER_IDS: ClassVar[tuple[int, ...]] = (1, 2)
    GUARDIAN_EXTERIOR_PANIC_HP: int = 25
    GUARDIAN_ALLOW_EXTERIOR_BUILD_WHEN_NO_THREAT: bool = True
    GUARDIAN_EXTERIOR_BUILD_MAX_PRESSURE: int = 0
    DEBUG_DEFENSE: bool = True
    DEBUG_METRICS: bool = True
    METRIC_INTERVAL_ROUNDS: int = 25

    # -------------------------------------------------------------
    # Action Type IPC Enums
    # -------------------------------------------------------------
    ACTION_MOVE: int = 0
    ACTION_BUILD_HARVESTER: int = 1
    ACTION_BUILD_BELT: int = 2
    ACTION_DEFEND: int = 3

    # -------------------------------------------------------------
    # Vision & Range Geometry Constants
    # -------------------------------------------------------------
    BUILDER_BOT_VISION_RADIUS_SQ: int = 20
    GUNNER_VISION_RADIUS_SQ: int = 13
    SENTINEL_VISION_RADIUS_SQ: int = 32
    THREAT_RADIUS_SQ: int = 16
    SENTINEL_SHIELD_MIN_DIST_SQ: int = 25

    # -------------------------------------------------------------
    # Phase 14: Threat Distance Calibration & Leash Breaker
    # -------------------------------------------------------------
    IGNORE_SCOUT_DIST_SQ: int = 64
    ENEMY_BUILDER_ALERT_DIST_SQ: int = 36
    ARMED_ALERT_DIST_SQ: int = 49
    ARMED_BREACH_DIST_SQ: int = 16
    MAX_BASE_DEFENDERS: int = 2

    # -------------------------------------------------------------
    # Two-Tier Defense Spending Hyperparameters
    # -------------------------------------------------------------
    BASELINE_DEFENSE_ROUND: int = 40
    BASELINE_DEFENSE_MIN_BANK: int = 20
    BASELINE_AMMO_RESERVE: int = 20
    DEFENSE_BANK_FLOOR: int = 25

    DEFENSE_SPENDING_GATE_ROUND: int = 150
    DEFENSE_SPENDING_MIN_BANK: int = 500
    DEFENSE_SPENDING_MIN_HARVESTERS: int = 3
    PATH_STUCK_THRESHOLD: int = 8
    UNREACHABLE_ORE_TIMEOUT: int = 50

    # -------------------------------------------------------------
    # Global Flags (SLOT_GLOBAL_FLAGS Bitmask)
    # -------------------------------------------------------------
    FLAG_BALANCED_ALERT: int = 0x01
    FLAG_CORE_THREAT: int = 0x02
    FLAG_NEED_SCOUTS: int = 0x04
    FLAG_EMERGENCY_EXPLORATION: int = 0x08

    # -------------------------------------------------------------
    # Phase 11R: Spread Zoning Hyperparameters
    # -------------------------------------------------------------
    SPREAD_ZONE_ENABLED: bool = True
    SPREAD_ZONE_NEAR_DIST_SQ: int = 100
    SPREAD_ZONE_MID_DIST_SQ: int = 400
    SPREAD_ZONE_RELAX_AFTER_TURNS: int = 30

    # -------------------------------------------------------------
    # Phase 11R: Symmetry Soft Hints
    # -------------------------------------------------------------
    SYMMETRY_ENABLED: bool = True
    SYMMETRY_HINT_TTL_TURNS: int = 120
    SYMMETRY_MAX_HINTS: int = 8
    SYMMETRY_CONFIDENCE_INITIAL: int = 3
    SYMMETRY_CONFIDENCE_DECAY_TURNS: int = 60

    # -------------------------------------------------------------
    # Phase 11R: Frontier Exploration & Maze Traversal
    # -------------------------------------------------------------
    FRONTIER_BFS_NODE_LIMIT: int = 350
    FRONTIER_CACHE_TTL_TURNS: int = 6
    ORE_TARGET_COOLDOWN_TURNS: int = 40
    MAZE_MAP_MAX_DIMENSION: int = 18
    WALL_FOLLOW_STUCK_THRESHOLD: int = 6
    ENCLOSURE_PANIC_THRESHOLD: int = 3

    # -------------------------------------------------------------
    # Phase 11R: Emergency Exploration & Scout Directives
    # -------------------------------------------------------------
    EXPLORATION_EMERGENCY_ROUND: int = 40
    NO_HARVESTER_EMERGENCY_ROUND: int = 60
    SCOUT_MIN_BUILDERS_NO_HARVESTER: int = 6
    EXPLORATION_BANK_FLOOR: int = 20
    ALLOW_RESERVED_SCOUT_AFTER_ROUND: int = 90
    ORE_DISCOVERY_TTL_ROUNDS: int = 80

    # -------------------------------------------------------------
    # Logistics Hyperparameters
    # -------------------------------------------------------------
    ORPHAN_SEARCH_RADIUS: int = 12
    MAX_BELT_RELAY_HOPS: int = 40
    FAR_ORE_MIN_DIST: int = 5
    BELT_OBSTRUCTION_PATIENCE: int = 3

    # -------------------------------------------------------------
    # Combat & Defensive Hyperparameters
    # -------------------------------------------------------------
    RETREAT_HP_THRESHOLD: int = 15
    CORE_LEASH_WIDE: int = 6
    CORE_LEASH_TIGHT: int = 3
    TURRET_THREAT_RADIUS: int = 12
    DECAY_QUIET_TURNS: int = 20
    AMMO_CONVERT_TI_THRESHOLD: int = 200
    AMMO_CONVERT_MAX_RESERVE: int = 30
    MIN_AMMO_FOR_SENTINEL: int = 5
    PROACTIVE_TURRET_START_ROUND: int = 60
    PROACTIVE_TURRET_MIN_TI: int = 150
    PROACTIVE_TURRET_TARGET_COUNT: int = 3
    FORWARD_CREEP_MIN_BANK: int = 800

    # -------------------------------------------------------------
    # Hotfix 2 & 3: Guardian Deferral & Secondary Expansion
    # -------------------------------------------------------------
    BARRIER_SKIP_DELAY_TURNS: int = 35
    MIN_BARRIERS_BEFORE_TURRET: int = 3
    TURRET_PRIORITY_ROUND: int = 80
    DEFENSE_DEADLINE_ROUND: int = 120
    EMERGENCY_TURRET_PRESSURE: int = 1
    SECONDARY_EXPANSION_MIN_DIST_SQ: int = 36
    SECONDARY_LOCAL_FALLBACK_TURNS: int = 25

    # -------------------------------------------------------------
    # CPU & Survival Safety Constraints
    # -------------------------------------------------------------
    CPU_SAFETY_LIMIT_MICROSEC: int = 8500  # 8.5 ms (1.5 ms margin below 10 ms limit)
    CPU_SEARCH_VALVE_MICROSEC: int = 6000  # 6.0 ms budget valve for pathfinding
"""knight — Entry Point & Unit Execution Router.

Principal Python Systems Architecture for knight RTS bot. Routes per-tick
unit execution into domain-specific Priority Pipelines with SafeController protection,
lazy role instantiation, CPU time safety valves, and global survival exception wrapping.
"""

from __future__ import annotations

import sys
from typing import Any, Optional
from fcode import Controller, Direction, EntityType

from config import Config
from core.ledger import Ledger
from core.macro import CoreMacro
from engine.safe_controller import SafeController
from engine.world import WorldView


class Player:
    """Isolated unit instance state class loaded by fcode engine per unit."""

    def __init__(self) -> None:
        self.safe = SafeController(None)  # type: ignore
        self.world = WorldView(self.safe)
        self.ledger = Ledger()
        self.role_handler: Optional[Any] = None
        self.role_type: Optional[str] = None
        self.tick_counter: int = 0

    def run(self, ct: Controller) -> None:
        """Main execution entry point invoked once per tick per active unit."""
        # CPU Safety Valve: if elapsed CPU time exceeds limit, yield immediately
        if ct.get_cpu_time_elapsed() > Config.CPU_SAFETY_LIMIT_MICROSEC:
            return

        self.safe.begin_turn(ct)

        try:
            self._safe_run(ct)
        except Exception as e:
            import traceback

            print(f"[KNIGHT SURVIVAL] Unit {ct.get_id()} caught exception: {e!r}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            # Survival fallback: step away to prevent fatal inaction
            try:
                if ct.get_entity_type() == EntityType.BUILDER_BOT:
                    if ct.get_move_cooldown() == 0 and not self.safe.major_action_used:
                        for d in (Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST):
                            if ct.can_move(d):
                                self.safe.safe_move(d)
                                break
            except Exception:
                pass

    def _safe_run(self, ct: Controller) -> None:
        self.tick_counter += 1

        # Step 1: Refresh cached WorldView and synchronize blackboard Store ledger
        self.world.refresh(ct)
        self.ledger.sync_vision(ct)

        # Step 2: Lazy role resolution on unit activation
        etype = ct.get_entity_type()

        if self.role_handler is None:
            if etype == EntityType.CORE:
                self.role_type = "core"
                self.role_handler = CoreMacro()
            elif etype == EntityType.BUILDER_BOT:
                my_idx = ct.get_id()
                from utils.geometry import get_reserved_builder_ids
                reserved_ids = get_reserved_builder_ids(ct)
                guardian_id = reserved_ids[0] if reserved_ids else 0

                # Assign Guardian role to the primary reserved defense builder
                if my_idx == guardian_id or (guardian_id == 0 and my_idx in (1, 2, 3)):
                    self.role_type = "guardian"
                    from combat.tactics import CombatTactics
                    self.role_handler = CombatTactics(self.safe)
                else:
                    self.role_type = "economy"
                    from logistics.economy import EconomyBuilder
                    self.role_handler = EconomyBuilder(self.safe)
            elif etype in (EntityType.GUNNER, EntityType.SENTINEL):
                self.role_type = "turret"
                from combat.tactics import CombatTactics
                self.role_handler = CombatTactics(self.safe)

        # Step 3: Execute active role pipeline
        if self.role_type == "core":
            self.role_handler.run_core_turn(ct)
        elif self.role_type == "guardian":
            self.role_handler.set_safe_controller(self.safe)
            self.role_handler.run_guardian(ct)
        elif self.role_type == "economy":
            self.role_handler.set_safe_controller(self.safe)
            self.role_handler.run_economy_builder(ct)
        elif self.role_type == "turret":
            self.role_handler.set_safe_controller(self.safe)
            self.role_handler.run_turret(ct)
"""knight — Turn-Scoped Engine Query Cache.

Memoizes expensive engine queries per tick (get_nearby_*, get_tile_*, etc.)
to prevent redundant C-extension calls and preserve the 10ms CPU safety budget.
"""

from __future__ import annotations

from typing import Any, Callable, Optional
from fcode import Controller


class TurnCache:
    """Memoizes expensive engine queries per tick to save CPU budget."""

    def __init__(self, ct: Optional[Controller] = None) -> None:
        self.ct = ct
        self.current_round: int = -1
        self._cache: dict[str, Any] = {}

    def reset(self, ct: Controller) -> None:
        """Explicitly reset and bind controller for the new turn."""
        self.ct = ct
        current_round = ct.get_current_round()
        if current_round != self.current_round:
            self.current_round = current_round
            self._cache.clear()

    def get(self, key: str, factory: Callable[[], Any]) -> Any:
        """Retrieve cached value or compute and store using factory callable."""
        if self.ct is not None:
            current_round = self.ct.get_current_round()
            if current_round != self.current_round:
                self.current_round = current_round
                self._cache.clear()

        if key not in self._cache:
            self._cache[key] = factory()
        return self._cache[key]

    def clear(self) -> None:
        """Manually clear the cache buffer."""
        self._cache.clear()
"""knight — Safe Controller Outcomes & Reason Enums.

Defines deterministic outcome receipts for all unit actions and engine interactions.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Any


class Reason(Enum):
    """Enumeration of possible execution outcomes for unit actions."""

    OK = auto()
    ALREADY_ACTED = auto()
    COOLDOWN = auto()
    WRONG_UNIT_TYPE = auto()
    CPU_BUDGET = auto()
    EXCEPTION = auto()
    OUT_OF_BOUNDS = auto()
    NOT_ADJACENT = auto()
    BLOCKED = auto()
    INVALID_DIRECTION = auto()
    NO_RESOURCES = auto()
    ENGINE_REJECTED = auto()


@dataclass(frozen=True)
class Outcome:
    """Immutable execution outcome receipt returned by SafeController methods."""

    success: bool
    reason: Reason
    message: str = ""
    value: Any = None
"""knight — Safe Controller Engine Wrapper.

Wraps fcode.Controller to guarantee mutual exclusivity between movement and actions,
enforce the 10ms CPU safety limit, prevent engine exceptions, and provide deterministic Outcome receipts.
"""

from __future__ import annotations

from typing import Any, Optional
from fcode import Controller, Direction, EntityType, Position

from config import Config
from engine.cache import TurnCache
from engine.outcome import Outcome, Reason
from utils.geometry import is_adjacent, is_in_bounds


class SafeController:
    """Defensive wrapper around fcode.Controller enforcing strict engine contracts."""

    def __init__(self, ct: Optional[Controller] = None) -> None:
        self.ct = ct  # type: ignore
        self.cache = TurnCache(ct)
        self._major_action_used: bool = False

    def begin_turn(self, ct: Controller) -> None:
        """Reset tick-scoped action flags and cache at the start of a unit's turn."""
        self.ct = ct
        self.cache.reset(ct)
        self._major_action_used = False

    def get_nearby_entities(self, dist_sq: Optional[int] = None) -> list[int]:
        """Memoized ct.get_nearby_entities() query."""
        key = f"nearby_entities_{dist_sq}"
        return self.cache.get(key, lambda: self.ct.get_nearby_entities(dist_sq))

    def get_nearby_buildings(self, dist_sq: Optional[int] = None) -> list[int]:
        """Memoized ct.get_nearby_buildings() query."""
        key = f"nearby_buildings_{dist_sq}"
        return self.cache.get(key, lambda: self.ct.get_nearby_buildings(dist_sq))

    def get_nearby_units(self, dist_sq: Optional[int] = None) -> list[int]:
        """Memoized ct.get_nearby_units() query."""
        key = f"nearby_units_{dist_sq}"
        return self.cache.get(key, lambda: self.ct.get_nearby_units(dist_sq))

    def get_nearby_tiles(self, dist_sq: Optional[int] = None) -> list[Position]:
        """Memoized ct.get_nearby_tiles() query."""
        key = f"nearby_tiles_{dist_sq}"
        return self.cache.get(key, lambda: self.ct.get_nearby_tiles(dist_sq))

    @property
    def major_action_used(self) -> bool:
        """True if a move or major action has already been executed this tick."""
        return self._major_action_used

    def _check_preconditions(self, require_adjacent: Optional[Position] = None) -> Optional[Outcome]:
        """Check CPU budget, single action per tick, and positional bounds."""
        if self.ct.get_cpu_time_elapsed() > Config.CPU_SAFETY_LIMIT_MICROSEC:
            return Outcome(False, Reason.CPU_BUDGET, "CPU safety budget exceeded")

        if self._major_action_used:
            return Outcome(False, Reason.ALREADY_ACTED, "Major action already performed this tick")

        if require_adjacent is not None:
            if not is_in_bounds(self.ct, require_adjacent):
                return Outcome(False, Reason.OUT_OF_BOUNDS, f"Target {require_adjacent} out of map bounds")
            my_pos = self.ct.get_position()
            if not is_adjacent(my_pos, require_adjacent):
                return Outcome(False, Reason.NOT_ADJACENT, f"Target {require_adjacent} is not orthogonally adjacent to {my_pos}")

        return None

    def safe_move(self, direction: Direction) -> Outcome:
        """Safely execute a 1-step move in a cardinal direction.
        
        Outcomes:
        - OK: Movement command successfully dispatched.
        - CPU_BUDGET: CPU elapsed exceeded safety threshold.
        - ALREADY_ACTED: Major action already executed this tick.
        - INVALID_DIRECTION: Direction is CENTRE or non-cardinal.
        - COOLDOWN: Move cooldown is non-zero.
        - ENGINE_REJECTED: Engine can_move returned False (e.g. wall/obstacle).
        """
        err = self._check_preconditions()
        if err is not None:
            return err

        if not direction.is_cardinal() or direction == Direction.CENTRE:
            return Outcome(False, Reason.INVALID_DIRECTION, f"Direction {direction} is not a valid cardinal move")

        if self.ct.get_move_cooldown() != 0:
            return Outcome(False, Reason.COOLDOWN, f"Move cooldown active: {self.ct.get_move_cooldown()}")

        if not self.ct.can_move(direction):
            return Outcome(False, Reason.ENGINE_REJECTED, f"can_move({direction}) returned False")

        self.ct.move(direction)
        self._major_action_used = True
        return Outcome(True, Reason.OK, f"Moved {direction}")

    def safe_build_barrier(self, pos: Position) -> Outcome:
        """Safely construct a 3-Ti Barrier on an adjacent tile.
        
        Outcomes:
        - OK: Barrier successfully built.
        - CPU_BUDGET, ALREADY_ACTED, OUT_OF_BOUNDS, NOT_ADJACENT.
        - COOLDOWN: Action cooldown is non-zero.
        - NO_RESOURCES: Insufficient global Titanium for barrier cost.
        - ENGINE_REJECTED: Tile occupied or impassable.
        """
        err = self._check_preconditions(require_adjacent=pos)
        if err is not None:
            return err

        if self.ct.get_action_cooldown() != 0:
            return Outcome(False, Reason.COOLDOWN, f"Action cooldown active: {self.ct.get_action_cooldown()}")

        if self.ct.get_global_resources() < self.ct.get_barrier_cost():
            return Outcome(False, Reason.NO_RESOURCES, f"Insufficient Titanium ({self.ct.get_global_resources()}) for barrier")

        if not self.ct.can_build_barrier(pos):
            return Outcome(False, Reason.ENGINE_REJECTED, f"can_build_barrier({pos}) returned False")

        bid = self.ct.build_barrier(pos)
        self._major_action_used = True
        return Outcome(True, Reason.OK, f"Built barrier at {pos}", value=bid)

    def safe_build_conveyor(self, pos: Position, direction: Direction) -> Outcome:
        """Safely construct a Conveyor belt on an adjacent tile facing direction.
        
        Outcomes:
        - OK: Conveyor successfully built.
        - CPU_BUDGET, ALREADY_ACTED, OUT_OF_BOUNDS, NOT_ADJACENT, INVALID_DIRECTION.
        - COOLDOWN: Action cooldown is non-zero.
        - NO_RESOURCES: Insufficient global Titanium for conveyor cost.
        - ENGINE_REJECTED: Tile occupied or impassable.
        """
        err = self._check_preconditions(require_adjacent=pos)
        if err is not None:
            return err

        if not direction.is_cardinal() or direction == Direction.CENTRE:
            return Outcome(False, Reason.INVALID_DIRECTION, f"Direction {direction} is not a valid cardinal facing")

        if self.ct.get_action_cooldown() != 0:
            return Outcome(False, Reason.COOLDOWN, f"Action cooldown active: {self.ct.get_action_cooldown()}")

        if self.ct.get_global_resources() < self.ct.get_conveyor_cost():
            return Outcome(False, Reason.NO_RESOURCES, f"Insufficient Titanium ({self.ct.get_global_resources()}) for conveyor")

        if not self.ct.can_build_conveyor(pos, direction):
            return Outcome(False, Reason.ENGINE_REJECTED, f"can_build_conveyor({pos}, {direction}) returned False")

        bid = self.ct.build_conveyor(pos, direction)
        self._major_action_used = True
        return Outcome(True, Reason.OK, f"Built conveyor at {pos} facing {direction}", value=bid)

    def safe_build_splitter(self, pos: Position, direction: Direction) -> Outcome:
        """Safely construct a Splitter on an adjacent tile facing direction."""
        err = self._check_preconditions(require_adjacent=pos)
        if err is not None:
            return err

        if not direction.is_cardinal() or direction == Direction.CENTRE:
            return Outcome(False, Reason.INVALID_DIRECTION, f"Direction {direction} is not a valid cardinal facing")

        if self.ct.get_action_cooldown() != 0:
            return Outcome(False, Reason.COOLDOWN, f"Action cooldown active: {self.ct.get_action_cooldown()}")

        if self.ct.get_global_resources() < self.ct.get_splitter_cost():
            return Outcome(False, Reason.NO_RESOURCES, f"Insufficient Titanium ({self.ct.get_global_resources()}) for splitter")

        if not self.ct.can_build_splitter(pos, direction):
            return Outcome(False, Reason.ENGINE_REJECTED, f"can_build_splitter({pos}, {direction}) returned False")

        bid = self.ct.build_splitter(pos, direction)
        self._major_action_used = True
        return Outcome(True, Reason.OK, f"Built splitter at {pos} facing {direction}", value=bid)

    def safe_build_harvester(self, pos: Position) -> Outcome:
        """Safely construct a Harvester on an adjacent ore tile.
        
        Outcomes:
        - OK: Harvester successfully built.
        - CPU_BUDGET, ALREADY_ACTED, OUT_OF_BOUNDS, NOT_ADJACENT.
        - COOLDOWN: Action cooldown is non-zero.
        - NO_RESOURCES: Insufficient global Titanium for harvester cost.
        - ENGINE_REJECTED: Tile not ore or already occupied.
        """
        err = self._check_preconditions(require_adjacent=pos)
        if err is not None:
            return err

        if self.ct.get_action_cooldown() != 0:
            return Outcome(False, Reason.COOLDOWN, f"Action cooldown active: {self.ct.get_action_cooldown()}")

        if self.ct.get_global_resources() < self.ct.get_harvester_cost():
            return Outcome(False, Reason.NO_RESOURCES, f"Insufficient Titanium ({self.ct.get_global_resources()}) for harvester")

        if not self.ct.can_build_harvester(pos):
            return Outcome(False, Reason.ENGINE_REJECTED, f"can_build_harvester({pos}) returned False")

        bid = self.ct.build_harvester(pos)
        self._major_action_used = True
        return Outcome(True, Reason.OK, f"Built harvester at {pos}", value=bid)

    def safe_build_sentinel(self, pos: Position, direction: Direction) -> Outcome:
        """Safely construct a Sentinel on an adjacent tile facing direction.
        
        Outcomes:
        - OK: Sentinel successfully built.
        - CPU_BUDGET, ALREADY_ACTED, OUT_OF_BOUNDS, NOT_ADJACENT, INVALID_DIRECTION.
        - COOLDOWN: Action cooldown is non-zero.
        - NO_RESOURCES: Insufficient global Titanium or Ammo.
        - ENGINE_REJECTED: Tile occupied or invalid.
        """
        err = self._check_preconditions(require_adjacent=pos)
        if err is not None:
            return err

        if not direction.is_cardinal() or direction == Direction.CENTRE:
            return Outcome(False, Reason.INVALID_DIRECTION, f"Direction {direction} is not a valid cardinal facing")

        if self.ct.get_action_cooldown() != 0:
            return Outcome(False, Reason.COOLDOWN, f"Action cooldown active: {self.ct.get_action_cooldown()}")

        if self.ct.get_global_resources() < self.ct.get_sentinel_cost():
            return Outcome(False, Reason.NO_RESOURCES, f"Insufficient Titanium ({self.ct.get_global_resources()}) for sentinel")

        if not self.ct.can_build_sentinel(pos, direction):
            return Outcome(False, Reason.ENGINE_REJECTED, f"can_build_sentinel({pos}, {direction}) returned False")

        bid = self.ct.build_sentinel(pos, direction)
        self._major_action_used = True
        return Outcome(True, Reason.OK, f"Built sentinel at {pos} facing {direction}", value=bid)

    def safe_build_gunner(self, pos: Position, direction: Direction) -> Outcome:
        """Safely construct a Gunner on an adjacent tile facing direction.
        
        Outcomes:
        - OK: Gunner successfully built.
        - CPU_BUDGET, ALREADY_ACTED, OUT_OF_BOUNDS, NOT_ADJACENT, INVALID_DIRECTION.
        - COOLDOWN: Action cooldown is non-zero.
        - NO_RESOURCES: Insufficient global Titanium or Ammo.
        - ENGINE_REJECTED: Tile occupied or invalid.
        """
        err = self._check_preconditions(require_adjacent=pos)
        if err is not None:
            return err

        if not direction.is_cardinal() or direction == Direction.CENTRE:
            return Outcome(False, Reason.INVALID_DIRECTION, f"Direction {direction} is not a valid cardinal facing")

        if self.ct.get_action_cooldown() != 0:
            return Outcome(False, Reason.COOLDOWN, f"Action cooldown active: {self.ct.get_action_cooldown()}")

        if self.ct.get_global_resources() < self.ct.get_gunner_cost():
            return Outcome(False, Reason.NO_RESOURCES, f"Insufficient Titanium ({self.ct.get_global_resources()}) for gunner")

        if not self.ct.can_build_gunner(pos, direction):
            return Outcome(False, Reason.ENGINE_REJECTED, f"can_build_gunner({pos}, {direction}) returned False")

        bid = self.ct.build_gunner(pos, direction)
        self._major_action_used = True
        return Outcome(True, Reason.OK, f"Built gunner at {pos} facing {direction}", value=bid)

    def safe_heal(self, pos: Position) -> Outcome:
        """Safely heal adjacent friendly unit or building (+4 HP for 1 Ti).
        
        Outcomes:
        - OK: Target healed.
        - CPU_BUDGET, ALREADY_ACTED, OUT_OF_BOUNDS, NOT_ADJACENT.
        - COOLDOWN: Action cooldown is non-zero.
        - NO_RESOURCES: Global Titanium balance is 0.
        - ENGINE_REJECTED: Target not damaged or not friendly.
        """
        err = self._check_preconditions(require_adjacent=pos)
        if err is not None:
            return err

        if self.ct.get_action_cooldown() != 0:
            return Outcome(False, Reason.COOLDOWN, f"Action cooldown active: {self.ct.get_action_cooldown()}")

        if self.ct.get_global_resources() < 1:
            return Outcome(False, Reason.NO_RESOURCES, "Insufficient Titanium for heal action")

        if not self.ct.can_heal(pos):
            return Outcome(False, Reason.ENGINE_REJECTED, f"can_heal({pos}) returned False")

        self.ct.heal(pos)
        self._major_action_used = True
        return Outcome(True, Reason.OK, f"Healed entity at {pos}")

    def safe_fire(self, target: Position) -> Outcome:
        """Safely fire turret or unit weapon at target tile.
        
        Outcomes:
        - OK: Weapon discharged at target.
        - CPU_BUDGET, ALREADY_ACTED, OUT_OF_BOUNDS.
        - COOLDOWN: Action cooldown is non-zero.
        - ENGINE_REJECTED: Target not in firing arc/range or ammo empty.
        """
        err = self._check_preconditions()
        if err is not None:
            return err

        if not is_in_bounds(self.ct, target):
            return Outcome(False, Reason.OUT_OF_BOUNDS, f"Target {target} out of map bounds")

        if self.ct.get_action_cooldown() != 0:
            return Outcome(False, Reason.COOLDOWN, f"Action cooldown active: {self.ct.get_action_cooldown()}")

        if not self.ct.can_fire(target):
            return Outcome(False, Reason.ENGINE_REJECTED, f"can_fire({target}) returned False")

        self.ct.fire(target)
        self._major_action_used = True
        return Outcome(True, Reason.OK, f"Fired at target {target}")

    def safe_rotate(self, direction: Direction) -> Outcome:
        """Safely rotate turret facing direction (Gunner only).
        
        Outcomes:
        - OK: Turret rotated.
        - CPU_BUDGET, ALREADY_ACTED, INVALID_DIRECTION.
        - COOLDOWN: Action cooldown is non-zero.
        - WRONG_UNIT_TYPE: Unit is not a Gunner.
        - ENGINE_REJECTED: Rotation rejected by engine.
        """
        err = self._check_preconditions()
        if err is not None:
            return err

        if not direction.is_cardinal() or direction == Direction.CENTRE:
            return Outcome(False, Reason.INVALID_DIRECTION, f"Direction {direction} is not a valid cardinal facing")

        if self.ct.get_action_cooldown() != 0:
            return Outcome(False, Reason.COOLDOWN, f"Action cooldown active: {self.ct.get_action_cooldown()}")

        if self.ct.get_entity_type() != EntityType.GUNNER:
            return Outcome(False, Reason.WRONG_UNIT_TYPE, "Rotate is valid only on Gunner units")

        if not self.ct.can_rotate(direction):
            return Outcome(False, Reason.ENGINE_REJECTED, f"can_rotate({direction}) returned False")

        self.ct.rotate(direction)
        self._major_action_used = True
        return Outcome(True, Reason.OK, f"Rotated gunner to {direction}")

    def safe_destroy(self, pos: Position) -> Outcome:
        """Safely demolish friendly building on adjacent tile (e.g. gate cycling)."""
        err = self._check_preconditions(require_adjacent=pos)
        if err is not None:
            return err

        if self.ct.get_action_cooldown() != 0:
            return Outcome(False, Reason.COOLDOWN, f"Action cooldown active: {self.ct.get_action_cooldown()}")

        if not self.ct.can_destroy(pos):
            return Outcome(False, Reason.ENGINE_REJECTED, f"can_destroy({pos}) returned False")

        self.ct.destroy(pos)
        self._major_action_used = True
        return Outcome(True, Reason.OK, f"Demolished building at {pos}")

    def safe_spawn_builder(self, pos: Position) -> Outcome:
        """Safely spawn a Builder Bot at adjacent tile (Core only).
        
        Outcomes:
        - OK: Builder spawned.
        - CPU_BUDGET, ALREADY_ACTED, OUT_OF_BOUNDS, NOT_ADJACENT.
        - COOLDOWN: Action cooldown is non-zero.
        - WRONG_UNIT_TYPE: Unit is not a Core.
        - NO_RESOURCES: Insufficient global Titanium balance.
        - ENGINE_REJECTED: Tile blocked or unit cap reached.
        """
        err = self._check_preconditions(require_adjacent=pos)
        if err is not None:
            return err

        if self.ct.get_entity_type() != EntityType.CORE:
            return Outcome(False, Reason.WRONG_UNIT_TYPE, "Spawn is valid only on Core unit")

        if self.ct.get_action_cooldown() != 0:
            return Outcome(False, Reason.COOLDOWN, f"Action cooldown active: {self.ct.get_action_cooldown()}")

        if self.ct.get_global_resources() < self.ct.get_builder_bot_cost():
            return Outcome(False, Reason.NO_RESOURCES, f"Insufficient Titanium for builder spawn")

        if not self.ct.can_spawn(pos):
            return Outcome(False, Reason.ENGINE_REJECTED, f"can_spawn({pos}) returned False")

        uid = self.ct.spawn_builder(pos)
        self._major_action_used = True
        return Outcome(True, Reason.OK, f"Spawned builder at {pos}", value=uid)
"""knight — Engine Domain: Cached WorldView / Turn Context.

Provides centralized, per-turn memoized views of game state, blackboard slots,
and spatial layout to minimize redundant computations and CPU time.
"""

from __future__ import annotations

from typing import Optional
from fcode import Controller, Position

from config import Config
from engine.safe_controller import SafeController
from utils.geometry import get_core_footprint


class WorldView:
    """Per-turn cached worldview shared across unit role handlers."""

    def __init__(self, safe: SafeController) -> None:
        self.safe = safe
        self.turn: int = -1
        self._core_pos: Optional[Position] = None
        self._core_footprint: list[Position] = []
        self._expansion_policy: int = Config.EXPANSION_FREE
        self._tactical_mode: int = 0
        self._global_flags: int = 0
        self._ammo_reserve: int = 0
        self._turret_count: int = 0
        self._builder_count: int = 0
        self._core_hp_pct: int = 100
        self._current_round: int = 0

    def refresh(self, ct: Controller) -> None:
        """Refresh cached properties on tick activation."""
        rnd = ct.get_current_round()
        if self.turn == rnd and self._core_footprint:
            return
        self.turn = rnd
        self._current_round = rnd

        # Core Footprint (memoized)
        self._core_footprint = get_core_footprint(ct)
        self._core_pos = self._core_footprint[0] if self._core_footprint else ct.get_position()

        # Blackboard Store Reads (memoized per tick)
        self._expansion_policy = ct.read_store(Config.SLOT_EXPANSION_POLICY)
        self._tactical_mode = ct.read_store(Config.SLOT_TACTICAL_MODE)
        self._global_flags = ct.read_store(Config.SLOT_GLOBAL_FLAGS)
        self._ammo_reserve = ct.read_store(Config.SLOT_AMMO_RESERVE)
        self._turret_count = ct.read_store(Config.SLOT_TURRET_COUNT)
        self._builder_count = ct.read_store(Config.SLOT_BUILDER_COUNT)
        self._core_hp_pct = ct.read_store(Config.SLOT_BASE_INTEGRITY)
        if self._core_hp_pct == 0:
            self._core_hp_pct = 100

    @property
    def core_pos(self) -> Position:
        return self._core_pos or Position(0, 0)

    @property
    def core_footprint(self) -> list[Position]:
        return self._core_footprint

    @property
    def expansion_policy(self) -> int:
        return self._expansion_policy

    @property
    def tactical_mode(self) -> int:
        return self._tactical_mode

    @property
    def global_flags(self) -> int:
        return self._global_flags

    @property
    def need_scouts(self) -> bool:
        return bool(self._global_flags & Config.FLAG_NEED_SCOUTS)

    @property
    def emergency_exploration(self) -> bool:
        return bool(self._global_flags & Config.FLAG_EMERGENCY_EXPLORATION)

    @property
    def ammo_reserve(self) -> int:
        return self._ammo_reserve

    @property
    def turret_count(self) -> int:
        return self._turret_count

    @property
    def builder_count(self) -> int:
        return self._builder_count

    @property
    def core_hp_pct(self) -> int:
        return self._core_hp_pct

    @property
    def current_round(self) -> int:
        return self._current_round
"""knight — Blackboard Store Encoding & Bitpacking Utilities.

Provides deterministic, offset-encoded 32-bit and 16-bit integer packing
for blackboard IPC communication across units.
"""

from __future__ import annotations

from typing import Optional, Tuple
from fcode import Position


def pack_position(pos: Position) -> int:
    """Pack Position into a 32-bit integer with +1 offset (supports x=0, y=0)."""
    return (((pos.x + 1) & 0xFFFF) << 16) | ((pos.y + 1) & 0xFFFF)


def unpack_position(value: int) -> Optional[Position]:
    """Unpack 32-bit integer into Position. Returns None if value == 0 (uninitialized)."""
    if value == 0:
        return None
    x = ((value >> 16) & 0xFFFF) - 1
    y = (value & 0xFFFF) - 1
    if x < 0 or y < 0:
        return None
    return Position(x, y)


def pack_ore_discovery(pos: Position, round_num: int) -> int:
    """Pack ore position (+1 offset) and round number (mod 256) into 32 bits:
    Bits 20-31: X + 1 (12 bits, max 4095)
    Bits 8-19:  Y + 1 (12 bits, max 4095)
    Bits 0-7:   Round Mod 256 (8 bits)
    """
    return (
        (((pos.x + 1) & 0xFFF) << 20)
        | (((pos.y + 1) & 0xFFF) << 8)
        | (round_num & 0xFF)
    )


def unpack_ore_discovery(value: int) -> tuple[Optional[Position], int]:
    """Unpack 32-bit integer into (Position | None, round_mod)."""
    if value == 0:
        return None, 0

    x = ((value >> 20) & 0xFFF) - 1
    y = ((value >> 8) & 0xFFF) - 1
    round_mod = value & 0xFF

    if x < 0 or y < 0:
        return None, 0

    return Position(x, y), round_mod


def pack_role_assignment(primary_id: int, secondary_id: int) -> int:
    """Pack primary (Guardian) and secondary (Reserve) builder IDs into 32-bit integer."""
    return ((primary_id & 0xFFFF) << 16) | (secondary_id & 0xFFFF)


def unpack_role_assignment(value: int) -> tuple[int, int]:
    """Unpack a 32-bit integer into (primary_id, secondary_id)."""
    primary = (value >> 16) & 0xFFFF
    secondary = value & 0xFFFF
    return primary, secondary


def pack_builder_intent(x: int, y: int, action: int, priority: int) -> int:
    """Pack Core-authored intent into a 32-bit integer:
    Bits 0-11:  Target X (0-4095)
    Bits 12-23: Target Y (0-4095)
    Bits 24-27: Action Type (0-15)
    Bits 28-31: Priority/Urgency (0-15)
    """
    return (x & 0xFFF) | ((y & 0xFFF) << 12) | ((action & 0xF) << 24) | ((priority & 0xF) << 28)


def unpack_builder_intent(value: int) -> tuple[int, int, int, int]:
    """Unpack a 32-bit integer into (x, y, action, priority)."""
    x = value & 0xFFF
    y = (value >> 12) & 0xFFF
    action = (value >> 24) & 0xF
    priority = (value >> 28) & 0xF
    return x, y, action, priority


def pack_ore_status(pos: Position, status: int) -> int:
    """Pack invalidation status and coordinate (+1 offset) into 32 bits:
    Bits 20-31: X + 1 (12 bits)
    Bits 8-19:  Y + 1 (12 bits)
    Bits 0-7:   Status (8 bits)
    """
    return (
        (((pos.x + 1) & 0xFFF) << 20)
        | (((pos.y + 1) & 0xFFF) << 8)
        | (status & 0xFF)
    )


def unpack_ore_status(value: int) -> tuple[Optional[Position], int]:
    """Unpack 32-bit integer into (Position | None, status)."""
    if value == 0:
        return None, 0

    x = ((value >> 20) & 0xFFF) - 1
    y = ((value >> 8) & 0xFFF) - 1
    status = value & 0xFF

    if x < 0 or y < 0:
        return None, 0

    return Position(x, y), status


# Backward-compatible aliases
pack_pos = pack_position
unpack_pos = unpack_position
pack_intent = pack_builder_intent
unpack_intent = unpack_builder_intent
"""knight — Spatial & Coordinate Geometry Utilities.

Centralizes pure geometric functions, packing/unpacking, and vision validation.
"""

from __future__ import annotations

from typing import Any, Optional
from fcode import Controller, Direction, EntityType, Environment, Position

from config import Config
from utils.storecoding import (
    pack_builder_intent,
    pack_intent,
    pack_ore_discovery,
    pack_ore_status,
    pack_pos,
    pack_position,
    pack_role_assignment,
    unpack_builder_intent,
    unpack_intent,
    unpack_ore_discovery,
    unpack_ore_status,
    unpack_pos,
    unpack_position,
    unpack_role_assignment,
)


def get_reserved_builder_ids(ct: Controller) -> tuple[int, ...]:
    """Retrieve dynamic reserved builder IDs from SLOT_ROLE_ASSIGNMENT, falling back to Config."""
    packed = ct.read_store(Config.SLOT_ROLE_ASSIGNMENT)
    primary, secondary = unpack_role_assignment(packed)
    if primary != 0:
        return (primary, secondary)
    return Config.LEASH_RESERVED_BUILDER_IDS


def is_in_bounds(ct: Controller, pos: Position) -> bool:
    """True if position is within map grid boundaries."""
    return 0 <= pos.x < ct.get_map_width() and 0 <= pos.y < ct.get_map_height()


def is_adjacent(p1: Position, p2: Position) -> bool:
    """True if p1 and p2 are orthogonally adjacent (Manhattan distance == 1)."""
    return abs(p1.x - p2.x) + abs(p1.y - p2.y) == 1


def is_in_vision(ct: Controller, my_pos: Position, target_pos: Position, vision_radius_sq: int) -> bool:
    """True if target_pos is within map bounds and within Euclidean vision radius squared of my_pos."""
    if not is_in_bounds(ct, target_pos):
        return False
    return my_pos.distance_squared(target_pos) <= vision_radius_sq


def get_core_footprint(ct: Controller) -> list[Position]:
    """Return the 4 grid positions occupied by the ally Core (2x2 footprint) via 4-tier priority."""
    # Tier 1: Current unit is Core
    if ct.get_entity_type() == EntityType.CORE:
        cpos = ct.get_position()
        return [
            cpos,
            Position(cpos.x + 1, cpos.y),
            Position(cpos.x, cpos.y + 1),
            Position(cpos.x + 1, cpos.y + 1),
        ]

    # Tier 2: Read from SLOT_CORE_POS
    raw_core_pos = ct.read_store(Config.SLOT_CORE_POS)
    if raw_core_pos != 0:
        cpos = unpack_position(raw_core_pos)
        if cpos is not None and is_in_bounds(ct, cpos):
            return [
                cpos,
                Position(cpos.x + 1, cpos.y),
                Position(cpos.x, cpos.y + 1),
                Position(cpos.x + 1, cpos.y + 1),
            ]

    # Tier 3: Scan nearby visible buildings
    my_team = ct.get_team()
    for bid in ct.get_nearby_buildings():
        if ct.get_team(bid) == my_team and ct.get_entity_type(bid) == EntityType.CORE:
            cpos = ct.get_position(bid)
            return [
                cpos,
                Position(cpos.x + 1, cpos.y),
                Position(cpos.x, cpos.y + 1),
                Position(cpos.x + 1, cpos.y + 1),
            ]

    # Tier 4: Map-center fallback
    cx = ct.get_map_width() // 2
    cy = ct.get_map_height() // 2
    return [Position(cx, cy)]


def get_core_bbox(core_footprint: list[Position]) -> tuple[int, int, int, int]:
    """Return (min_x, min_y, max_x, max_y) bounding box for the Core footprint."""
    xs = [p.x for p in core_footprint]
    ys = [p.y for p in core_footprint]
    return min(xs), min(ys), max(xs), max(ys)


def is_inside_core_area(pos: Position, core_footprint: list[Position], margin: int = 1) -> bool:
    """True if pos is inside the Core bounding box expanded by margin."""
    min_x, min_y, max_x, max_y = get_core_bbox(core_footprint)
    return (min_x - margin) <= pos.x <= (max_x + margin) and (min_y - margin) <= pos.y <= (max_y + margin)


def clamp_to_core_area(pos: Position, core_footprint: list[Position], margin: int = 1) -> Position:
    """Return the closest clamped Position inside the expanded Core bounding box."""
    min_x, min_y, max_x, max_y = get_core_bbox(core_footprint)
    clamped_x = max(min_x - margin, min(max_x + margin, pos.x))
    clamped_y = max(min_y - margin, min(max_y + margin, pos.y))
    return Position(clamped_x, clamped_y)


def get_core_center_tile(core_footprint: list[Position]) -> Position:
    """Return the nearest Core footprint tile to use as an anchor."""
    return core_footprint[0]


def choose_guardian_stand_tile(
    ct: Controller,
    target_tile: Position,
    core_footprint: list[Position],
    traversable_fn: Any,
) -> Optional[Position]:
    """Choose a safe interior stand tile for the Guardian while building target_tile.
    
    Prefers candidates inside the Core area and closer to the Core than target_tile.
    Returns None if all available candidates are exterior without being closer to Core.
    """
    core_center = get_core_center_tile(core_footprint)
    target_dist_to_core = target_tile.distance_squared(core_center)

    candidates: list[tuple[bool, bool, int, Position]] = []
    for d in (Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST):
        cand = target_tile.add(d)
        if not is_in_bounds(ct, cand):
            continue
        if not traversable_fn(cand):
            continue

        inside = is_inside_core_area(cand, core_footprint, margin=1)
        d_core = cand.distance_squared(core_center)
        closer = d_core < target_dist_to_core

        if not inside and not closer:
            continue

        # Sort priority: (inside descending, closer descending, d_core ascending)
        candidates.append((inside, closer, d_core, cand))

    if not candidates:
        return None

    candidates.sort(key=lambda item: (item[0], item[1], -item[2]), reverse=True)
    return candidates[0][3]


def get_greedy_step(from_pos: Position, to_pos: Position) -> Direction:
    """Compute single greedy cardinal step toward target position."""
    dx = to_pos.x - from_pos.x
    dy = to_pos.y - from_pos.y

    if abs(dx) > abs(dy):
        if dx > 0:
            return Direction.EAST
        if dx < 0:
            return Direction.WEST
    else:
        if dy > 0:
            return Direction.SOUTH
        if dy < 0:
            return Direction.NORTH

    return Direction.CENTRE


def is_plausible_ore_target(ct: Controller, pos: Position, core_footprint: list[Position]) -> bool:
    """True if pos is in bounds, not Core footprint, and not known invalid (allowed as soft target if not visible)."""
    if not is_in_bounds(ct, pos):
        return False
    if is_inside_core_area(pos, core_footprint, margin=0):
        return False
    my_pos = ct.get_position()
    if is_in_vision(ct, my_pos, pos, Config.BUILDER_BOT_VISION_RADIUS_SQ):
        try:
            if ct.get_tile_building_id(pos) is not None:
                return False
            if ct.get_tile_env(pos) != Environment.ORE_TITANIUM:
                return False
        except Exception:
            return False
    return True


def is_confirmed_ore_target(ct: Controller, pos: Position, core_footprint: list[Position]) -> bool:
    """True if pos is in bounds, not Core footprint, in vision, has no building, and is Environment.ORE_TITANIUM."""
    if not is_in_bounds(ct, pos):
        return False
    if is_inside_core_area(pos, core_footprint, margin=0):
        return False
    my_pos = ct.get_position()
    if not is_in_vision(ct, my_pos, pos, Config.BUILDER_BOT_VISION_RADIUS_SQ):
        return False
    try:
        if ct.get_tile_building_id(pos) is not None:
            return False
        if ct.get_tile_env(pos) != Environment.ORE_TITANIUM:
            return False
    except Exception:
        return False
    return True


def is_valid_ore_target(ct: Controller, pos: Position, core_footprint: list[Position]) -> bool:
    """Validate that pos is a plausible ore target (in-bounds, not inside Core footprint, not known invalid)."""
    return is_plausible_ore_target(ct, pos, core_footprint)


def get_invalid_ore_reason(ct: Controller, pos: Position, core_footprint: list[Position]) -> Optional[str]:
    """Return explicit reason string for invalid ore target for telemetry."""
    if not is_in_bounds(ct, pos):
        return "OUT_OF_BOUNDS"
    if is_inside_core_area(pos, core_footprint, margin=0):
        return "CORE_FOOTPRINT"
    my_pos = ct.get_position()
    if is_in_vision(ct, my_pos, pos, Config.BUILDER_BOT_VISION_RADIUS_SQ):
        try:
            if ct.get_tile_building_id(pos) is not None:
                return "BUILDING_OCCUPIED"
            if ct.get_tile_env(pos) != Environment.ORE_TITANIUM:
                return "NOT_ORE"
        except Exception:
            pass
    return None
"""knight — Spatial Geometry Re-export Module.

Exposes geometry, bounding box, and packing helpers under the spatial namespace.
"""

from __future__ import annotations

from utils.geometry import (
    clamp_to_core_area,
    choose_guardian_stand_tile,
    get_core_bbox,
    get_core_center_tile,
    get_core_footprint,
    get_greedy_step,
    get_invalid_ore_reason,
    get_reserved_builder_ids,
    is_adjacent,
    is_confirmed_ore_target,
    is_in_bounds,
    is_in_vision,
    is_inside_core_area,
    is_plausible_ore_target,
    is_valid_ore_target,
    pack_builder_intent,
    pack_intent,
    pack_ore_discovery,
    pack_ore_status,
    pack_pos,
    pack_position,
    pack_role_assignment,
    unpack_builder_intent,
    unpack_intent,
    unpack_ore_discovery,
    unpack_ore_status,
    unpack_pos,
    unpack_position,
    unpack_role_assignment,
)
"""knight — Shared Support Skills (Heal Duty & Field Repairs).

Provides reusable support abilities across combat and logistics roles.
"""

from __future__ import annotations

from typing import Optional
from fcode import Controller, Direction, Position

from config import Config
from engine.safe_controller import SafeController
from utils.geometry import is_adjacent, is_in_bounds, is_in_vision


class SupportSkill:
    """Shared support capabilities including structure repair and unit healing."""

    def __init__(self, safe: SafeController) -> None:
        self.safe = safe

    def try_heal_damaged_structures(self, ct: Controller) -> bool:
        """Priority: Heal damaged friendly buildings/units on orthogonally adjacent tiles (+4 HP for 1 Ti)."""
        if not ct.can_act() or ct.get_global_resources() < 1:
            return False

        pos = ct.get_position()
        my_team = ct.get_team()

        for d in (Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST):
            adj = pos.add(d)
            if is_in_vision(ct, pos, adj, Config.BUILDER_BOT_VISION_RADIUS_SQ):
                bid = ct.get_tile_building_id(adj)
                if bid is not None and ct.get_team(bid) == my_team:
                    if ct.get_hp(bid) < ct.get_max_hp(bid):
                        outcome = self.safe.safe_heal(adj)
                        if outcome.success:
                            return True
                uid = ct.get_tile_builder_bot_id(adj)
                if uid is not None and ct.get_team(uid) == my_team:
                    if ct.get_hp(uid) < ct.get_max_hp(uid):
                        outcome = self.safe.safe_heal(adj)
                        if outcome.success:
                            return True
        return False
"""knight — Behavior Domain: MovementSkill Navigation Adapter.

Wraps all unit movement through SafeController to enforce mutual exclusivity,
cooldown validation, and structured Outcome receipts.
"""

from __future__ import annotations

from typing import Optional, Sequence, Set
from fcode import Controller, Direction, Position

from engine.outcome import Outcome, Reason
from engine.safe_controller import SafeController
from spatial.movement import choose_bug2_direction, get_greedy_step, is_traversable


class MovementSkill:
    """High-level movement coordinator executing side-effect actions via SafeController."""

    def __init__(self, safe: SafeController) -> None:
        self.safe = safe

    def move_direction(self, direction: Direction) -> Outcome:
        """Execute directional movement through SafeController."""
        return self.safe.safe_move(direction)

    def move_towards(
        self,
        ct: Controller,
        current_pos: Position,
        target_pos: Position,
        pos_history: Optional[Sequence[Position]] = None,
        seen_walls: Optional[Set[Position]] = None,
    ) -> Outcome:
        """Plan and execute a step toward target_pos using BFS and bug2 evasion."""
        if current_pos == target_pos:
            return Outcome(success=True, reason=Reason.ALREADY_VALID)

        dir_choice = choose_bug2_direction(
            ct, current_pos, target_pos, pos_history=pos_history, seen_walls=seen_walls
        )
        if dir_choice is not None and dir_choice != Direction.CENTRE:
            return self.safe.safe_move(dir_choice)

        return Outcome(success=False, reason=Reason.BLOCKED)

    def step_greedy(self, current_pos: Position, target_pos: Position) -> Outcome:
        """Execute single direct greedy cardinal step toward target_pos."""
        direction = get_greedy_step(current_pos, target_pos)
        if direction == Direction.CENTRE:
            return Outcome(success=True, reason=Reason.ALREADY_VALID)
        return self.safe.safe_move(direction)
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
"""knight — Core Domain: Shared Blackboard & Store Slot IPC Ledger.

Synchronizes local unit vision with Store Slots 0-15. Provides packed
intent readers/writers for strategic coordination without 10ms CPU bloat.
"""

from __future__ import annotations

from typing import Any, Optional
from fcode import Controller, Position

from config import Config
from utils.geometry import (
    pack_builder_intent,
    pack_pos,
    unpack_builder_intent,
    unpack_pos,
    unpack_role_assignment,
)


class Ledger:
    """Blackboard interface for unit-to-unit communication via Store slots."""

    def __init__(self) -> None:
        self.core_pos: Optional[Position] = None
        self.global_flags: int = 0
        self.core_threat_active: bool = False
        self.builder_intent: Optional[tuple[Position, int, int]] = None  # (Position, action_type, priority)
        self.reserved_builders: tuple[int, ...] = Config.LEASH_RESERVED_BUILDER_IDS

    def sync_vision(self, ct: Controller) -> None:
        """Synchronize local unit vision state with global Store blackboard.
        
        Args:
            ct: Controller instance for the active unit.
        """
        # Read core position from Slot 0 (SLOT_CORE_POS)
        raw_core_pos = ct.read_store(Config.SLOT_CORE_POS)
        self.core_pos = unpack_pos(raw_core_pos)

        # Read global flags from Slot 11 (SLOT_GLOBAL_FLAGS)
        self.global_flags = ct.read_store(Config.SLOT_GLOBAL_FLAGS)
        self.core_threat_active = bool(self.global_flags & 0x02)

        # Read builder intent from Slot 14 (SLOT_BUILDER_INTENT)
        raw_intent = ct.read_store(Config.SLOT_BUILDER_INTENT)
        if raw_intent != 0:
            x, y, action, priority = unpack_builder_intent(raw_intent)
            self.builder_intent = (Position(x, y), action, priority)
        else:
            self.builder_intent = None

        # Read dynamic role assignment from Slot 15 (SLOT_ROLE_ASSIGNMENT)
        raw_role = ct.read_store(Config.SLOT_ROLE_ASSIGNMENT)
        if raw_role != 0:
            primary, secondary = unpack_role_assignment(raw_role)
            if primary != 0:
                self.reserved_builders = (primary, secondary)

    def broadcast_builder_intent(self, ct: Controller, target: Position, action: int, priority: int) -> None:
        """Broadcast 32-bit packed Builder Intent to Store Slot 14 (Core only)."""
        val = pack_builder_intent(target.x, target.y, action, priority)
        ct.write_store(Config.SLOT_BUILDER_INTENT, val)

    def report_threat_alert(self, ct: Controller, threat_pos: Position) -> None:
        """Write threat alert position to Store Slot 6 (SLOT_ALERTS)."""
        ct.write_store(Config.SLOT_ALERTS, pack_pos(threat_pos))

    def read_threat_alert(self, ct: Controller) -> Optional[Position]:
        """Read threat alert position from Store Slot 6 (SLOT_ALERTS)."""
        val = ct.read_store(Config.SLOT_ALERTS)
        return unpack_pos(val)
"""knight — Core Domain: Macro Evaluation, Threat Detection & Gate Cycling Spawner.

Evaluates game phases, threat detection, tactical mode transitions, predictive ammo
conversion, smoothed expansion policy (with hysteresis), role assignment broadcasts,
authoritative builder intents, and Core gate cycling to spawn Builder Bots through barrier walls.
"""

from __future__ import annotations

import sys
from typing import Optional
from fcode import Controller, Direction, EntityType, GameConstants, Position

from config import Config
from engine.safe_controller import SafeController
from utils.geometry import (
    get_core_footprint,
    is_plausible_ore_target,
    is_valid_ore_target,
    pack_builder_intent,
    pack_ore_discovery,
    pack_pos,
    pack_position,
    pack_role_assignment,
    unpack_builder_intent,
    unpack_ore_discovery,
    unpack_ore_status,
    unpack_pos,
    unpack_position,
    unpack_role_assignment,
)


class CoreMacro:
    """Macro state evaluator executing on the Core unit."""

    def __init__(self, safe: Optional[SafeController] = None) -> None:
        self.safe = safe or SafeController(None)  # type: ignore
        self.spawn_count: int = 0
        self.quiet_turns: int = 0
        self.last_core_hp: Optional[int] = None
        self.last_core_damage_round: int = -10000
        self.last_policy: Optional[int] = None

        # Hysteresis state for expansion policy
        self.current_policy: int = Config.EXPANSION_FREE
        self.candidate_policy: int = Config.EXPANSION_FREE
        self.candidate_stable_turns: int = 0

        # Phase 13: Core Intent Lease State
        self.current_intent_target: Optional[Position] = None
        self.intent_created_round: int = 0
        self.intent_last_supported_round: int = 0

        # Dynamic role assignment state
        self.guardian_builder_id: int = 0
        self.reserve_builder_id: int = 0

    def run_core_turn(self, ct: Controller) -> None:
        """Main turn loop for the Core entity."""
        self.safe.begin_turn(ct)
        core_pos = ct.get_position()
        curr_round = ct.get_current_round()

        # Slot 0: Core Position (unified offset-encoded packing)
        ct.write_store(Config.SLOT_CORE_POS, pack_position(core_pos))

        # Synchronize threat assessments and tactical mode
        tactical_mode = self._evaluate_threats_and_tactical_mode(ct)

        # Evaluate and smooth expansion policy hysteresis
        policy = self._evaluate_expansion_policy(ct, tactical_mode)

        # Broadcast strategic intent for builders
        self._broadcast_strategic_intent(ct, policy)

        # Update Slot 7 (SLOT_TURRET_COUNT) and Slot 8 (SLOT_BUILDER_COUNT)
        turret_count = sum(
            1 for bid in self.safe.get_nearby_buildings()
            if ct.get_team(bid) == ct.get_team() and ct.get_entity_type(bid) in (EntityType.SENTINEL, EntityType.GUNNER)
        )
        ct.write_store(Config.SLOT_TURRET_COUNT, turret_count)
        ct.write_store(Config.SLOT_BUILDER_COUNT, max(0, ct.get_unit_count() - 1))

        # Manage predictive and reactive ammo reserves
        self._manage_ammo_reserves(ct)

        # Spawn economy and guardian builders
        self._evaluate_builder_production(ct)

        # Periodic telemetry broadcast
        if Config.DEBUG_METRICS and (curr_round % Config.METRIC_INTERVAL_ROUNDS == 0):
            self._emit_validation_metrics(ct, tactical_mode, policy)

    def _get_core_hp_pct(self, ct: Controller) -> int:
        """Return integer percentage of Core HP remaining (0-100)."""
        return int(100 * ct.get_hp() / max(1, ct.get_max_hp()))

    def _track_core_damage(self, ct: Controller) -> bool:
        """Track damage events and return True if Core took damage within RECENT_DAMAGE_WINDOW_TURNS."""
        curr_hp = ct.get_hp()
        curr_round = ct.get_current_round()
        if self.last_core_hp is not None and curr_hp < self.last_core_hp:
            self.last_core_damage_round = curr_round
        self.last_core_hp = curr_hp
        return (curr_round - self.last_core_damage_round) <= Config.RECENT_DAMAGE_WINDOW_TURNS

    def _get_threat_pressure(self, ct: Controller, core_footprint: list[Position]) -> tuple[int, bool]:
        """Compute aggregate threat pressure and check for armed perimeter breach."""
        my_team = ct.get_team()
        threat_pressure = 0
        breach = False

        for eid in self.safe.get_nearby_entities(Config.ARMED_ALERT_DIST_SQ):
            if ct.get_team(eid) != my_team:
                etype = ct.get_entity_type(eid)
                epos = ct.get_position(eid)
                d = min(epos.distance_squared(cp) for cp in core_footprint)

                if etype == EntityType.BUILDER_BOT:
                    if d <= Config.ENEMY_BUILDER_ALERT_DIST_SQ:
                        threat_pressure += 1
                elif etype in (EntityType.GUNNER, EntityType.SENTINEL, EntityType.LAUNCHER):
                    if d <= Config.ARMED_BREACH_DIST_SQ:
                        threat_pressure += 3
                        breach = True
                    elif d <= Config.ARMED_ALERT_DIST_SQ:
                        threat_pressure += 2
                else:
                    if d <= Config.ARMED_ALERT_DIST_SQ:
                        threat_pressure += 1

        if ct.get_hp() < ct.get_max_hp():
            breach = True

        return threat_pressure, breach

    def _get_defense_counts(self, ct: Controller, core_footprint: list[Position]) -> tuple[int, int]:
        """Count operational defense turrets and intact barrier apron tiles."""
        my_team = ct.get_team()
        core_pos = core_footprint[0]
        turret_count = 0
        barrier_count = 0

        for bid in self.safe.get_nearby_buildings():
            if ct.get_team(bid) == my_team:
                btype = ct.get_entity_type(bid)
                bpos = ct.get_position(bid)
                if btype in (EntityType.GUNNER, EntityType.SENTINEL):
                    turret_count += 1
                elif btype == EntityType.BARRIER and bpos.distance_squared(core_pos) <= 8:
                    barrier_count += 1

        return turret_count, barrier_count

    def _smooth_expansion_policy(self, raw_policy: int, ct: Controller) -> int:
        """Apply hysteresis smoothing to prevent expansion policy flapping."""
        old_policy = self.current_policy

        if raw_policy == Config.EXPANSION_LEASHED:
            # Immediate safety downgrade
            self.candidate_policy = Config.EXPANSION_LEASHED
            self.candidate_stable_turns = 0
            self.current_policy = Config.EXPANSION_LEASHED
            if old_policy != self.current_policy and (Config.DEBUG_ECONOMY or Config.DEBUG_DEFENSE):
                print(
                    f"POLICY_STABLE_CHANGE round={ct.get_current_round()} old={old_policy} "
                    f"new={self.current_policy} raw={raw_policy} stable_turns=0",
                    file=sys.stderr,
                )
            return self.current_policy

        if raw_policy == self.candidate_policy:
            self.candidate_stable_turns += 1
        else:
            self.candidate_policy = raw_policy
            self.candidate_stable_turns = 1

        required_turns = Config.POLICY_UPGRADE_STABLE_TURNS
        if raw_policy == Config.EXPANSION_PARTIAL and self.candidate_policy == Config.EXPANSION_FREE:
            required_turns = Config.POLICY_DOWNGRADE_STABLE_TURNS

        if self.candidate_stable_turns >= required_turns:
            self.current_policy = self.candidate_policy
            if old_policy != self.current_policy and (Config.DEBUG_ECONOMY or Config.DEBUG_DEFENSE):
                print(
                    f"POLICY_STABLE_CHANGE round={ct.get_current_round()} old={old_policy} "
                    f"new={self.current_policy} raw={raw_policy} stable_turns={self.candidate_stable_turns}",
                    file=sys.stderr,
                )
            self.candidate_stable_turns = 0

        return self.current_policy

    def _evaluate_expansion_policy(self, ct: Controller, tactical_mode: int) -> int:
        """Decouple economy expansion leashing from combat mode with hysteresis smoothing."""
        core_hp_pct = self._get_core_hp_pct(ct)
        recently_damaged = self._track_core_damage(ct)
        core_footprint = get_core_footprint(ct)
        threat_pressure, breach = self._get_threat_pressure(ct, core_footprint)
        turret_count, barrier_count = self._get_defense_counts(ct, core_footprint)
        curr_rnd = ct.get_current_round()

        # Phase 14.2: Break Expansion Policy Circularity
        if breach:
            raw_policy = Config.EXPANSION_LEASHED
        elif core_hp_pct < Config.CORE_HP_CRITICAL_PCT:
            raw_policy = Config.EXPANSION_LEASHED
        elif recently_damaged:
            raw_policy = Config.EXPANSION_LEASHED
        elif tactical_mode == 2:
            if core_hp_pct >= Config.CORE_HP_SECURE_PCT:
                raw_policy = Config.EXPANSION_PARTIAL
            else:
                raw_policy = Config.EXPANSION_LEASHED
        elif tactical_mode == 1:
            if core_hp_pct >= Config.CORE_HP_SECURE_PCT:
                raw_policy = Config.EXPANSION_FREE
            else:
                raw_policy = Config.EXPANSION_PARTIAL
        else:
            raw_policy = Config.EXPANSION_FREE

        policy = self._smooth_expansion_policy(raw_policy, ct)

        ct.write_store(Config.SLOT_EXPANSION_POLICY, policy)
        ct.write_store(Config.SLOT_BASE_INTEGRITY, core_hp_pct)

        if (Config.DEBUG_ECONOMY or Config.DEBUG_DEFENSE) and policy != self.last_policy:
            print(
                f"POLICY_RAW round={curr_rnd} raw={raw_policy} final={policy} "
                f"mode={tactical_mode} hp_pct={core_hp_pct} turrets={turret_count} "
                f"barriers={barrier_count} pressure={threat_pressure} breach={breach}",
                file=sys.stderr,
            )
            self.last_policy = policy

        return policy

    def _broadcast_strategic_intent(self, ct: Controller, policy: int) -> None:
        """Author authoritative strategic intent into SLOT_BUILDER_INTENT with lease & invalidation checks."""
        curr_rnd = ct.get_current_round()
        if policy == Config.EXPANSION_LEASHED:
            self.current_intent_target = None
            core_pos = ct.get_position()
            val = pack_builder_intent(core_pos.x, core_pos.y, Config.INTENT_DEFEND_CORE, 15)
            ct.write_store(Config.SLOT_BUILDER_INTENT, val)
            return

        val_ore = ct.read_store(Config.SLOT_ORE_DISCOVERY)
        ore_pos, round_mod = unpack_ore_discovery(val_ore)

        val_status = ct.read_store(Config.SLOT_ORE_STATUS)
        inv_pos, inv_status = unpack_ore_status(val_status)

        core_footprint = get_core_footprint(ct)

        # Check shared invalidation
        if inv_pos is not None and ore_pos is not None and inv_pos == ore_pos and inv_status != Config.ORE_STATUS_NONE:
            if Config.DEBUG_ECONOMY:
                print(f"INTENT_CLEAR round={curr_rnd} target={ore_pos} reason=STATUS_{inv_status}", file=sys.stderr)
            ct.write_store(Config.SLOT_ORE_DISCOVERY, 0)
            ore_pos = None

        if ore_pos is not None:
            age = (curr_rnd - round_mod) % 256
            fresh = age <= Config.ORE_DISCOVERY_TTL_ROUNDS
            plausible = is_plausible_ore_target(ct, ore_pos, core_footprint)

            if fresh and plausible:
                if self.current_intent_target == ore_pos:
                    self.intent_last_supported_round = curr_rnd
                else:
                    self.current_intent_target = ore_pos
                    self.intent_created_round = curr_rnd
                    self.intent_last_supported_round = curr_rnd

                val = pack_builder_intent(ore_pos.x, ore_pos.y, Config.INTENT_EXPAND_ORE, 10)
                ct.write_store(Config.SLOT_BUILDER_INTENT, val)
                return
            else:
                reason = "STALE" if not fresh else "IMPLAUSIBLE"
                if Config.DEBUG_ECONOMY:
                    print(f"INTENT_CLEAR round={curr_rnd} target={ore_pos} reason={reason}", file=sys.stderr)
                self.current_intent_target = None

        if self.current_intent_target is not None:
            if (curr_rnd - self.intent_last_supported_round) > Config.INTENT_LEASE_TURNS:
                if Config.DEBUG_ECONOMY:
                    print(f"INTENT_CLEAR round={curr_rnd} target={self.current_intent_target} reason=LEASE_EXPIRED", file=sys.stderr)
                self.current_intent_target = None

        ct.write_store(Config.SLOT_BUILDER_INTENT, 0)

    def _should_request_scouts(self, ct: Controller) -> bool:
        """True if team is starving for harvesters/ore discovery on unexplored maps."""
        my_team = ct.get_team()
        curr_rnd = ct.get_current_round()
        harvester_count = sum(
            1 for bid in self.safe.get_nearby_buildings()
            if ct.get_team(bid) == my_team and ct.get_entity_type(bid) == EntityType.HARVESTER
        )
        if curr_rnd >= Config.EXPLORATION_EMERGENCY_ROUND and harvester_count == 0:
            return True
        if curr_rnd >= Config.NO_HARVESTER_EMERGENCY_ROUND and ct.get_global_resources() < 100:
            return True
        return False

    def _evaluate_threats_and_tactical_mode(self, ct: Controller) -> int:
        """Scan nearby entities for threats with calibrated distance thresholds and set SLOT_TACTICAL_MODE."""
        core_pos = ct.get_position()
        core_footprint = get_core_footprint(ct)
        my_team = ct.get_team()
        curr_round = ct.get_current_round()

        nearest_threat_pos: Optional[Position] = None
        nearest_threat_dist = 10**9
        enemy_spotted = False
        armed_combat_near_core = False
        breach = False
        threat_pressure = 0

        # Core is taking damage
        core_damaged = ct.get_hp() < ct.get_max_hp()

        for eid in self.safe.get_nearby_entities():
            if ct.get_team(eid) != my_team:
                epos = ct.get_position(eid)
                etype = ct.get_entity_type(eid)
                d = min(epos.distance_squared(cp) for cp in core_footprint)

                if etype == EntityType.BUILDER_BOT:
                    if d > Config.IGNORE_SCOUT_DIST_SQ:
                        if Config.DEBUG_DEFENSE and (curr_round % 50 == 0):
                            print(f"THREAT_IGNORED round={curr_round} etype={etype} dist_sq={d}", file=sys.stderr)
                        continue
                    elif d <= Config.ENEMY_BUILDER_ALERT_DIST_SQ:
                        enemy_spotted = True
                        threat_pressure += 1
                        if d < nearest_threat_dist:
                            nearest_threat_dist = d
                            nearest_threat_pos = epos
                elif etype in (EntityType.GUNNER, EntityType.SENTINEL, EntityType.LAUNCHER):
                    if d <= Config.ARMED_BREACH_DIST_SQ:
                        armed_combat_near_core = True
                        breach = True
                        threat_pressure += 3
                        if d < nearest_threat_dist:
                            nearest_threat_dist = d
                            nearest_threat_pos = epos
                    elif d <= Config.ARMED_ALERT_DIST_SQ:
                        enemy_spotted = True
                        threat_pressure += 2
                        if d < nearest_threat_dist:
                            nearest_threat_dist = d
                            nearest_threat_pos = epos
                else:
                    # Enemy turrets / buildings / conveyors
                    if d <= Config.ARMED_ALERT_DIST_SQ:
                        enemy_spotted = True
                        threat_pressure += 1
                        if d < nearest_threat_dist:
                            nearest_threat_dist = d
                            nearest_threat_pos = epos

        flags = 0
        old_mode = ct.read_store(Config.SLOT_TACTICAL_MODE)

        if core_damaged or armed_combat_near_core or breach:
            self.quiet_turns = 0
            mode = 2  # All-Out Defense
            ct.write_store(Config.SLOT_TACTICAL_MODE, 2)
            flags |= Config.FLAG_CORE_THREAT
            if nearest_threat_pos is not None:
                ct.write_store(Config.SLOT_ALERTS, pack_pos(nearest_threat_pos))
        elif enemy_spotted:
            self.quiet_turns = 0
            mode = 1  # Balanced
            ct.write_store(Config.SLOT_TACTICAL_MODE, 1)
            flags |= Config.FLAG_BALANCED_ALERT
            if nearest_threat_pos is not None:
                ct.write_store(Config.SLOT_ALERTS, pack_pos(nearest_threat_pos))
        else:
            self.quiet_turns += 1
            if self.quiet_turns >= Config.DECAY_QUIET_TURNS:
                mode = 0  # Decay to Eco
                ct.write_store(Config.SLOT_TACTICAL_MODE, 0)
            else:
                mode = old_mode

        if Config.DEBUG_DEFENSE and mode != old_mode:
            print(f"THREAT_ALERT round={curr_round} mode={mode} pressure={threat_pressure}", file=sys.stderr)

        # Count active team harvesters (memoized query)
        harvester_count = sum(
            1 for bid in self.safe.get_nearby_buildings()
            if ct.get_team(bid) == my_team and ct.get_entity_type(bid) == EntityType.HARVESTER
        )

        curr_round = ct.get_current_round()
        curr_bank = ct.get_global_resources()

        # Emergency Exploration Request
        if self._should_request_scouts(ct):
            flags |= Config.FLAG_NEED_SCOUTS
            if curr_round >= Config.NO_HARVESTER_EMERGENCY_ROUND and harvester_count == 0:
                flags |= Config.FLAG_EMERGENCY_EXPLORATION
                if Config.DEBUG_ECONOMY and (curr_round % 25 == 0):
                    print(
                        f"EXPLORATION_EMERGENCY round={curr_round} harvesters={harvester_count} ti={curr_bank}",
                        file=sys.stderr,
                    )

        ct.write_store(Config.SLOT_GLOBAL_FLAGS, flags)

        # Two-Tier Defense Spending Gate:
        # Tier 1 — Baseline defense (always allowed early, threat-independent)
        baseline_allowed = (
            curr_round >= Config.BASELINE_DEFENSE_ROUND and
            curr_bank >= Config.BASELINE_DEFENSE_MIN_BANK
        )

        # Tier 2 — Reactive defense (threat-triggered or late-game surplus)
        reactive_allowed = (
            mode >= 1 or (
                curr_round > Config.DEFENSE_SPENDING_GATE_ROUND and
                curr_bank > Config.DEFENSE_SPENDING_MIN_BANK and
                harvester_count >= Config.DEFENSE_SPENDING_MIN_HARVESTERS
            )
        )

        proactive_turret = 1 if (baseline_allowed or reactive_allowed) else 0
        ct.write_store(Config.SLOT_PROACTIVE_TURRET, proactive_turret)

        return mode

    def _manage_ammo_reserves(self, ct: Controller) -> None:
        """Predictively convert Titanium to Ammo for baseline, emergency, and reactive defense."""
        current_ammo = ct.get_global_ammo()
        current_ti = ct.get_global_resources()
        my_team = ct.get_team()
        curr_round = ct.get_current_round()
        mode = ct.read_store(Config.SLOT_TACTICAL_MODE)
        threat_alert = ct.read_store(Config.SLOT_ALERTS)
        threat_pressure = 1 if (mode >= 1 or threat_alert != 0) else 0

        # Count active team turrets near Core
        turret_count = sum(
            1 for bid in self.safe.get_nearby_buildings()
            if ct.get_team(bid) == my_team and ct.get_entity_type(bid) in (EntityType.SENTINEL, EntityType.GUNNER)
        )

        # Count active team harvesters (memoized query)
        harvester_count = sum(
            1 for bid in self.safe.get_nearby_buildings()
            if ct.get_team(bid) == my_team and ct.get_entity_type(bid) == EntityType.HARVESTER
        )

        # Hotfix 4: Defense Ammo Safety (convert ammo before turret construction)
        if turret_count == 0:
            if curr_round >= Config.BASELINE_DEFENSE_ROUND or threat_pressure > 0:
                if current_ammo < 10 and current_ti >= 40:
                    if ct.can_convert_ammo(10):
                        ct.convert_ammo(10)
                        current_ammo += 10
                        current_ti -= 10
        elif threat_pressure > 0:
            if current_ammo < 10 and current_ti >= 40:
                if ct.can_convert_ammo(10):
                    ct.convert_ammo(10)
                    current_ammo += 10
                    current_ti -= 10

        # Tier 1 Baseline Ammo Seed: Convert up to BASELINE_AMMO_RESERVE (~20) whenever bank >= 40 Ti
        baseline_ammo_allowed = (
            curr_round >= Config.BASELINE_DEFENSE_ROUND and
            current_ti >= 40
        )

        # Tier 2 Reactive Ammo Scaling: Scale with active turret count when threatened
        reactive_ammo_allowed = (
            mode >= 1 or (
                curr_round > Config.DEFENSE_SPENDING_GATE_ROUND and
                current_ti > Config.DEFENSE_SPENDING_MIN_BANK and
                harvester_count >= Config.DEFENSE_SPENDING_MIN_HARVESTERS
            )
        )

        desired_ammo = 0
        if reactive_ammo_allowed:
            desired_ammo = max(Config.AMMO_CONVERT_MAX_RESERVE, turret_count * 10)
        elif baseline_ammo_allowed:
            desired_ammo = Config.BASELINE_AMMO_RESERVE

        # Scaled ammo conversion based on turret count and Ti bank
        if current_ammo < desired_ammo and current_ti >= 10:
            if ct.can_convert_ammo(10):
                ct.convert_ammo(10)
                current_ammo += 10

        ct.write_store(Config.SLOT_AMMO_RESERVE, current_ammo)

    def _evaluate_builder_production(self, ct: Controller) -> None:
        """Evaluate Titanium bank balance and spawn builders if affordable.
        
        Executes Gate Cycling if all adjacent spawn tiles are blocked by friendly barriers.
        Broadcasts dynamic role assignments to SLOT_ROLE_ASSIGNMENT.
        """
        if ct.get_action_cooldown() != 0:
            return

        living_units = ct.get_unit_count()
        if living_units >= GameConstants.MAX_TEAM_UNITS:
            return

        target_builders = self._get_target_builders(ct)
        builder_cost = ct.get_builder_bot_cost()
        flags = ct.read_store(Config.SLOT_GLOBAL_FLAGS)
        bank_floor = (
            Config.EXPLORATION_BANK_FLOOR
            if bool(flags & Config.FLAG_EMERGENCY_EXPLORATION)
            else Config.DEFENSE_BANK_FLOOR
        )
        curr_ti = ct.get_global_resources()

        # Check affordability respecting DEFENSE_BANK_FLOOR
        if living_units < target_builders and curr_ti >= builder_cost and (curr_ti - builder_cost) >= bank_floor:
            core_pos = ct.get_position()
            # 2x2 footprint adjacent spawn candidates
            spawn_candidates = [
                core_pos.add(Direction.NORTH),
                Position(core_pos.x + 1, core_pos.y).add(Direction.NORTH),
                core_pos.add(Direction.WEST),
                Position(core_pos.x, core_pos.y + 1).add(Direction.WEST),
                Position(core_pos.x + 1, core_pos.y).add(Direction.EAST),
                Position(core_pos.x + 1, core_pos.y + 1).add(Direction.EAST),
                Position(core_pos.x, core_pos.y + 1).add(Direction.SOUTH),
                Position(core_pos.x + 1, core_pos.y + 1).add(Direction.SOUTH),
            ]

            spawned_uid: Optional[int] = None

            # Try direct spawn on open tile via SafeController
            for spawn_pos in spawn_candidates:
                if ct.can_spawn(spawn_pos):
                    outcome = self.safe.safe_spawn_builder(spawn_pos)
                    if outcome.success:
                        spawned_uid = outcome.value
                        self.spawn_count += 1
                        break

            # Gate Cycling: If all spawn tiles blocked, demolish barrier at gate tile and return to spawn next tick
            if spawned_uid is None:
                gate_pos = spawn_candidates[0]  # Designated gate tile
                bid = ct.get_tile_building_id(gate_pos)
                if bid is not None and ct.get_entity_type(bid) == EntityType.BARRIER:
                    destroy_outcome = self.safe.safe_destroy(gate_pos)
                    if destroy_outcome.success:
                        return

            # Update dynamic role assignment on spawn
            if spawned_uid is not None and spawned_uid > 0:
                if self.guardian_builder_id == 0:
                    self.guardian_builder_id = spawned_uid
                elif self.reserve_builder_id == 0:
                    self.reserve_builder_id = spawned_uid

                ct.write_store(
                    Config.SLOT_ROLE_ASSIGNMENT,
                    pack_role_assignment(self.guardian_builder_id, self.reserve_builder_id),
                )

    def _get_target_builders(self, ct: Controller) -> int:
        policy = ct.read_store(Config.SLOT_EXPANSION_POLICY)
        flags = ct.read_store(Config.SLOT_GLOBAL_FLAGS)
        curr_round = ct.get_current_round()
        ti = ct.get_global_resources()
        ore_disc, rmod = unpack_ore_discovery(ct.read_store(Config.SLOT_ORE_DISCOVERY))
        is_fresh_ore = ore_disc is not None and (((curr_round - rmod) % 256) <= Config.ORE_DISCOVERY_TTL_ROUNDS)

        # Fast-start behavior: in first 10 rounds, spawn up to 4 builders unconditionally
        if curr_round <= 10:
            target = max(4, Config.MAX_BUILDERS_EARLY)
        elif ti >= 50 and is_fresh_ore:
            target = min(14, Config.MAX_BUILDERS_CEILING)
        else:
            target = Config.MAX_BUILDERS_EARLY
            for min_ti, t in Config.BUILDER_TARGET_BRACKETS:
                if ti >= min_ti:
                    target = min(t, Config.MAX_BUILDERS_CEILING)
                    break

        if bool(flags & Config.FLAG_NEED_SCOUTS):
            target = max(target, Config.SCOUT_MIN_BUILDERS_NO_HARVESTER)

        if policy != Config.EXPANSION_LEASHED and curr_round >= 60:
            target = max(target, Config.MAX_BUILDERS_BASE)

        if policy == Config.EXPANSION_FREE and ti >= 500:
            target = max(target, Config.MAX_BUILDERS_CEILING)

        return min(target, GameConstants.MAX_TEAM_UNITS)

    def _emit_validation_metrics(self, ct: Controller, mode: int, policy: int) -> None:
        """Emit structured metric line every METRIC_INTERVAL_ROUNDS for match validation."""
        core_hp_pct = self._get_core_hp_pct(ct)
        core_footprint = get_core_footprint(ct)
        pressure, _ = self._get_threat_pressure(ct, core_footprint)
        turrets, _ = self._get_defense_counts(ct, core_footprint)
        my_team = ct.get_team()
        harvester_count = sum(
            1 for bid in self.safe.get_nearby_buildings()
            if ct.get_team(bid) == my_team and ct.get_entity_type(bid) == EntityType.HARVESTER
        )
        builder_count = ct.get_unit_count()
        ti = ct.get_global_resources()
        ammo = ct.get_global_ammo()

        print(
            f"METRIC round={ct.get_current_round()} ti={ti} ammo={ammo} builders={builder_count} "
            f"harvesters={harvester_count} turrets={turrets} policy={policy} mode={mode} "
            f"hp_pct={core_hp_pct} pressure={pressure}",
            file=sys.stderr,
        )
"""knight — Combat Domain: Guardian Defense & Turret Pipelines.

Implements base apron defense (Guardian), barrier ring construction from safe interior stand tiles,
structure healing, Gunner omnidirectional defense, and Sentinel counter-battery fire.
"""

from __future__ import annotations

import sys
from typing import Optional, Set
from fcode import Controller, Direction, EntityType, Environment, Position

from config import Config
from engine.outcome import Reason
from engine.safe_controller import SafeController
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
        self.last_pos: Optional[Position] = None
        self.stuck_turns: int = 0
        self.pos_history: list[Position] = []
        self.seen_walls: set[Position] = set()
        self.built_barriers: set[Position] = set()
        self.built_sentinels: set[Position] = set()
        self.built_gunners: set[Position] = set()
        self.shield_sentinels: set[Position] = set()
        self.deferred_barrier_tiles: dict[Position, int] = {}
        self.last_unsafe_skip_log_round: dict[Position, int] = {}
        self.economy_fallback: Optional[Any] = None

    def set_safe_controller(self, safe: SafeController) -> None:
        """Inject active SafeController instance."""
        self.safe = safe
        self.support.safe = safe

    def _should_prioritize_turret(
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

    def _try_build_defensive_turrets(self, ct: Controller) -> bool:
        """Attempt Sentinel if affordable, otherwise attempt Gunner if affordable."""
        if self._try_build_proactive_sentinel(ct):
            return True
        if self._try_build_proactive_gunner(ct):
            return True
        return False

    def run_guardian(self, ct: Controller) -> None:
        """Execute Priority Pipeline for Guardian Builder Bot (Unit #1).
        
        1. Self-Preservation: Retreat inside core apron if exterior and under pressure/damaged.
        2. Heal Duty: Heal damaged friendly structures/units (1 Ti -> +4 HP).
        3. Emergency Turret: If under threat pressure and 0 turrets exist.
        4. Phased Barrier Apron: Build minimum barriers (non-deferred).
        5. Prioritized Turrets: Construct Sentinel (or Gunner fallback) on corners.
        6. Remaining Barriers: Complete remaining non-deferred apron barriers.
        7. Fallback: Safe interior patrol or economy mining when policy allows.
        """
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

        # Step 1: Self-Preservation & Interior Retreat
        if self._try_guardian_retreat_inside(ct, core_footprint, threat_pressure):
            return

        # Step 2: Heal Duty
        if self.support.try_heal_damaged_structures(ct):
            return

        # Step 3: Emergency Turret if threat pressure > 0 and 0 turrets
        if threat_pressure > 0 and turret_count == 0:
            if Config.DEBUG_DEFENSE:
                print(f"GUARDIAN_TURRET_PRIORITY round={curr_rnd} reason=EMERGENCY_PRESSURE", file=sys.stderr)
            if self._try_build_defensive_turrets(ct):
                return

        # Step 4: Minimum barriers (non-deferred)
        if barriers_built < Config.MIN_BARRIERS_BEFORE_TURRET:
            if self._try_build_barrier_ring(ct):
                return

        # Step 5: Turret Priority Check
        should_pri_turret, pri_reason = self._should_prioritize_turret(
            ct, barriers_built, turret_count, threat_pressure, curr_rnd
        )
        if should_pri_turret:
            if Config.DEBUG_DEFENSE and curr_rnd % 20 == 0:
                print(f"GUARDIAN_TURRET_PRIORITY round={curr_rnd} reason={pri_reason}", file=sys.stderr)
            if self._try_build_defensive_turrets(ct):
                return

        # Step 6: Remaining non-deferred barriers
        if self._try_build_barrier_ring(ct):
            return

        # Step 7: Remaining turrets
        if self._try_build_defensive_turrets(ct):
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
            candidates: list[tuple[int, Position]] = []

            # Scan nearby hostile buildings (memoized query)
            for bid in self.safe.get_nearby_buildings(Config.SENTINEL_VISION_RADIUS_SQ):
                if ct.get_team(bid) != my_team:
                    bpos = ct.get_position(bid)
                    if ct.can_fire(bpos):
                        btype = ct.get_entity_type(bid)
                        priority = 1
                        if btype in (EntityType.GUNNER, EntityType.SENTINEL, EntityType.LAUNCHER):
                            priority = 4
                        elif btype == EntityType.HARVESTER:
                            priority = 3
                        candidates.append((priority, bpos))

            # Scan nearby hostile units (memoized query)
            for uid in self.safe.get_nearby_units(Config.SENTINEL_VISION_RADIUS_SQ):
                if ct.get_team(uid) != my_team:
                    upos = ct.get_position(uid)
                    if ct.can_fire(upos):
                        candidates.append((3, upos))

            if candidates:
                candidates.sort(key=lambda item: item[0], reverse=True)
                best_target = candidates[0][1]
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
    is_confirmed_ore_target,
    is_in_vision,
    is_plausible_ore_target,
    is_valid_ore_target,
    pack_ore_discovery,
    pack_ore_status,
    pack_pos,
    unpack_builder_intent,
    unpack_ore_discovery,
    unpack_ore_status,
    unpack_pos,
)
from utils.support import SupportSkill


def pos_key(pos: Position) -> int:
    """Pack coordinate into integer key for fast set/dict indexing."""
    return (pos.x << 16) | (pos.y & 0xFFFF)


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
        self.ore_blacklist: dict[int, int] = {}

    def _blacklist_ore(self, ct: Controller, pos: Position, reason: str) -> None:
        """Blacklist an invalid/occupied ore coordinate to prevent re-targeting loops."""
        curr_rnd = ct.get_current_round()
        if reason == "BUILDING_OCCUPIED":
            ttl = Config.ORE_BLACKLIST_OCCUPIED_TTL
        elif reason == "NOT_ORE":
            ttl = Config.ORE_BLACKLIST_NOT_ORE_TTL
        elif reason in ("CORE_FOOTPRINT", "OUT_OF_BOUNDS"):
            ttl = Config.ORE_BLACKLIST_CORE_TTL
        elif reason == "UNREACHABLE":
            ttl = Config.ORE_BLACKLIST_UNREACHABLE_TTL
        else:
            ttl = 100

        expiry = curr_rnd + ttl
        k = pos_key(pos)
        self.ore_blacklist[k] = expiry
        self.frontier.failed_targets[k] = curr_rnd
        if Config.DEBUG_ECONOMY:
            print(
                f"ORE_BLACKLIST builder_id={ct.get_id()} ore={pos} reason={reason} expiry={expiry}",
                file=sys.stderr,
            )

    def _is_blacklisted(self, ct: Controller, pos: Position) -> bool:
        """Check if an ore position is currently blacklisted."""
        k = pos_key(pos)
        if k in self.ore_blacklist:
            if ct.get_current_round() < self.ore_blacklist[k]:
                return True
            del self.ore_blacklist[k]
        return False

    def _report_invalid_ore(self, ct: Controller, pos: Position, reason: str) -> None:
        """Publish shared invalidation to SLOT_ORE_STATUS and clear matching discovery."""
        if reason == "BUILDING_OCCUPIED":
            status = Config.ORE_STATUS_OCCUPIED
        elif reason == "NOT_ORE":
            status = Config.ORE_STATUS_NOT_ORE
        elif reason in ("CORE_FOOTPRINT", "OUT_OF_BOUNDS"):
            status = Config.ORE_STATUS_CORE_FOOTPRINT
        elif reason == "UNREACHABLE":
            status = Config.ORE_STATUS_UNREACHABLE
        else:
            status = Config.ORE_STATUS_NOT_ORE

        # Write invalidation status into slot 3
        ct.write_store(Config.SLOT_ORE_STATUS, pack_ore_status(pos, status))

        # Clear matching ore discovery if present in slot 9
        raw_disc = ct.read_store(Config.SLOT_ORE_DISCOVERY)
        disc_pos, _ = unpack_ore_discovery(raw_disc)
        if disc_pos is not None and disc_pos == pos:
            ct.write_store(Config.SLOT_ORE_DISCOVERY, 0)

        self._blacklist_ore(ct, pos, reason)

    def _is_valid_ore(self, ct: Controller, pos: Position) -> bool:
        """Check if an ore coordinate is valid, distinguishing visible confirmed truth from soft targets."""
        if self._is_blacklisted(ct, pos):
            return False
        core_footprint = get_core_footprint(ct)
        my_pos = ct.get_position()
        if is_in_vision(ct, my_pos, pos, Config.BUILDER_BOT_VISION_RADIUS_SQ):
            return is_confirmed_ore_target(ct, pos, core_footprint)
        return is_plausible_ore_target(ct, pos, core_footprint)

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
        emergency_recall = bool(global_flags & Config.FLAG_CORE_THREAT) and (dist_sq_to_core <= (Config.CORE_LEASH_TIGHT ** 2))

        # Allow reserved builders to scout after ALLOW_RESERVED_SCOUT_AFTER_ROUND in emergency
        if emergency_exp and curr_rnd >= Config.ALLOW_RESERVED_SCOUT_AFTER_ROUND:
            reserved_builder = False

        # Phase 14.3: Role-Isolated Commitment Abort Logic
        if policy == Config.EXPANSION_LEASHED and self.committed_ore is not None:
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

        # Phase 14.3 & 14.4: Role-Isolated Base Defense Leash
        should_leash = not need_scouts and (reserved_builder or emergency_recall) and near_core

        if should_leash:
            self.flexed_to_defense = True
            self.current_step = "flex_defense"
            if Config.DEBUG_DEFENSE and not self.unshackled_logged:
                print(
                    f"DEFENSE_LEASH builder_id={my_id} round={curr_rnd} "
                    f"reserved={reserved_builder} emergency={emergency_recall}",
                    file=sys.stderr,
                )
                self.unshackled_logged = True

            if self.support.try_heal_damaged_structures(ct):
                self.stuck_turns = 0
                return
            if (reserved_builder or emergency_recall) and self._try_build_barrier_ring(ct):
                self.stuck_turns = 0
                return
            if dist_sq_to_core > Config.CORE_LEASH_TIGHT ** 2 and ct.get_move_cooldown() == 0:
                if execute_bug2_evasion(ct, pos, core_tile, self.pos_history, self.seen_walls):
                    self.stuck_turns = 0
                    return
        else:
            self.flexed_to_defense = False
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
                self._report_invalid_ore(ct, self.committed_ore, reason)
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
                self._report_invalid_ore(ct, self.target_ore, reason)
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
"""knight — Spatial Domain: Navigation, BFS Pathfinding & Greedy Evasion.

Stateless micro movement interfaces implementing BFS unit navigation, sideways-first evasion,
terrain memory (seen_walls), and strict 2-turn oscillation guards without calling raw ct.move.
"""

from __future__ import annotations

from collections import deque
from typing import Any, Optional, Sequence, Set
from fcode import Controller, Direction, Position

from config import Config
from utils.geometry import is_adjacent, is_in_bounds, is_in_vision


def is_traversable(ct: Controller, pos: Position, seen_walls: Optional[Set[Position]] = None) -> bool:
    """Single ground-truth helper for movement validity based on engine truth & terrain memory.
    
    Checks in-bounds, checks seen_walls, verifies vision range, and defers to ct.is_tile_passable(pos).
    """
    if not is_in_bounds(ct, pos):
        return False
    if seen_walls is not None and pos in seen_walls:
        return False
    my_pos = ct.get_position()
    if not is_in_vision(ct, my_pos, pos, Config.BUILDER_BOT_VISION_RADIUS_SQ):
        return True
    try:
        return ct.is_tile_passable(pos)
    except Exception:
        return True


def get_greedy_step(current_pos: Position, target_pos: Position) -> Direction:
    """Calculate the primary cardinal step direction toward target_pos.
    
    Args:
        current_pos: Current unit position
        target_pos: Desired destination tile position

    Returns:
        Direction enum (NORTH, EAST, SOUTH, WEST) or CENTRE if already on target tile.
    """
    return current_pos.cardinal_direction_to(target_pos)


def find_builder_navigation_path(
    ct: Controller,
    start_pos: Position,
    target_pos: Position,
    max_hops: int = 40,
    seen_walls: Optional[Set[Position]] = None,
    allowed_fn: Optional[Any] = None,
) -> list[Direction]:
    """BFS pathfinder for unit navigation around walls and buildings to reach target_pos or adjacent."""
    if start_pos == target_pos or is_adjacent(start_pos, target_pos):
        return []

    goals: set[Position] = set()
    for d in (Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST):
        adj = target_pos.add(d)
        if is_in_bounds(ct, adj) and is_traversable(ct, adj, seen_walls):
            if allowed_fn is None or allowed_fn(adj):
                goals.add(adj)

    if not goals:
        if is_in_bounds(ct, target_pos) and is_traversable(ct, target_pos, seen_walls):
            goals.add(target_pos)

    if start_pos in goals:
        return []

    queue: deque[tuple[Position, list[Direction]]] = deque([(start_pos, [])])
    visited: set[Position] = {start_pos}

    while queue:
        if ct.get_cpu_time_elapsed() > Config.CPU_SEARCH_VALVE_MICROSEC:
            return []  # Abort pathfinding to protect unit from CPU timeout

        curr, path = queue.popleft()

        if curr in goals:
            return path

        if len(path) >= max_hops:
            continue

        for d in (Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST):
            nxt = curr.add(d)
            if not is_in_bounds(ct, nxt) or nxt in visited:
                continue
            if not is_traversable(ct, nxt, seen_walls):
                continue
            if allowed_fn is not None and not allowed_fn(nxt):
                continue

            visited.add(nxt)
            queue.append((nxt, path + [d]))

    return []


def choose_bug2_direction(
    ct: Controller,
    current_pos: Position,
    target_pos: Position,
    pos_history: Optional[Sequence[Position]] = None,
    seen_walls: Optional[Set[Position]] = None,
) -> Optional[Direction]:
    """Calculate best navigation Direction using BFS and sideways-first evasion without executing move.

    Args:
        ct: Unit Controller instance
        current_pos: Current unit position
        target_pos: Desired destination tile position
        pos_history: Optional sequence of recent unit positions for oscillation check
        seen_walls: Optional set of remembered wall positions

    Returns:
        Direction enum if a valid step exists, or None if completely blocked.
    """
    # Primary: Try BFS navigation path
    nav_path = find_builder_navigation_path(ct, current_pos, target_pos, seen_walls=seen_walls)
    if nav_path:
        first_step = nav_path[0]
        nxt = current_pos.add(first_step)
        if ct.can_move(first_step) and is_traversable(ct, nxt, seen_walls):
            return first_step

    primary_dir = get_greedy_step(current_pos, target_pos)
    if primary_dir == Direction.CENTRE:
        return None

    pos_t_minus_2 = pos_history[-2] if (pos_history and len(pos_history) >= 2) else None

    # Candidate directions: Primary -> Rotate Right -> Rotate Left -> Opposite
    candidates: list[Direction] = []
    if primary_dir.is_cardinal():
        candidates.append(primary_dir)
    side_right = primary_dir.rotate_right()
    if side_right.is_cardinal():
        candidates.append(side_right)
    side_left = primary_dir.rotate_left()
    if side_left.is_cardinal():
        candidates.append(side_left)
    back = primary_dir.opposite()
    if back.is_cardinal():
        candidates.append(back)

    # Pass 1: Strict oscillation prevention (reject steps returning to pos_t_minus_2)
    for d in candidates:
        nxt = current_pos.add(d)
        if ct.can_move(d) and is_traversable(ct, nxt, seen_walls):
            if pos_t_minus_2 is None or nxt != pos_t_minus_2:
                return d

    # Pass 2: Last resort (allow stepping to pos_t_minus_2 if no other step exists)
    for d in candidates:
        nxt = current_pos.add(d)
        if ct.can_move(d) and is_traversable(ct, nxt, seen_walls):
            return d

    return None


# Backward-compatible aliases
plan_bug2_step = choose_bug2_direction


def execute_bug2_evasion(
    ct: Controller,
    current_pos: Position,
    target_pos: Position,
    pos_history: Optional[Sequence[Position]] = None,
    seen_walls: Optional[Set[Position]] = None,
) -> bool:
    """Compatibility adapter: plans and steps toward target_pos."""
    if ct.get_move_cooldown() != 0:
        return False
    d = choose_bug2_direction(ct, current_pos, target_pos, pos_history, seen_walls)
    if d is not None and d != Direction.CENTRE and ct.can_move(d):
        ct.move(d)
        return True
    return False
"""knight — Exploration Domain: Spread Zoning & Soft Sector Distribution.

Distributes economy builders across angular quadrants and radial distance bands
to prevent clustering around home and achieve rapid map-wide ore coverage.
"""

from __future__ import annotations

from dataclasses import dataclass
from fcode import Position

from config import Config


@dataclass(frozen=True)
class SpreadZone:
    """Soft directional wedge and radial distance band assignment."""

    quadrant: int  # 0: North, 1: East, 2: South, 3: West
    band: int  # 0: Near (<=100), 1: Mid (101-400), 2: Far (>400)


def assign_spread_zone(builder_id: int) -> SpreadZone:
    """Assign deterministic spread zone based on unit ID."""
    quadrant = builder_id % 4
    band = (builder_id // 4) % 3
    return SpreadZone(quadrant, band)


def get_quadrant(pos: Position, core_pos: Position) -> int:
    """Determine dominant cardinal quadrant of pos relative to core_pos."""
    dx = pos.x - core_pos.x
    dy = pos.y - core_pos.y

    abs_dx = abs(dx)
    abs_dy = abs(dy)

    if abs_dy >= abs_dx:
        return 0 if dy <= 0 else 2
    else:
        return 1 if dx >= 0 else 3


def get_distance_band(dist_sq: int) -> int:
    """Determine radial distance band based on squared Euclidean distance."""
    if dist_sq <= Config.SPREAD_ZONE_NEAR_DIST_SQ:
        return 0
    elif dist_sq <= Config.SPREAD_ZONE_MID_DIST_SQ:
        return 1
    else:
        return 2


def in_spread_zone(pos: Position, core_pos: Position, zone: SpreadZone) -> bool:
    """True if pos strictly matches assigned quadrant and distance band."""
    if not Config.SPREAD_ZONE_ENABLED:
        return True

    q = get_quadrant(pos, core_pos)
    if q != zone.quadrant:
        return False

    dist_sq = pos.distance_squared(core_pos)
    b = get_distance_band(dist_sq)
    return b == zone.band


def zone_score(pos: Position, core_pos: Position, zone: SpreadZone) -> int:
    """Compute soft alignment score (higher is better) for pos within assigned zone."""
    if not Config.SPREAD_ZONE_ENABLED:
        return 100

    q = get_quadrant(pos, core_pos)
    dist_sq = pos.distance_squared(core_pos)
    b = get_distance_band(dist_sq)

    if q == zone.quadrant:
        if b == zone.band:
            return 100
        else:
            band_diff = abs(b - zone.band)
            return 60 - (band_diff * 15)
    else:
        # Penalize mismatched quadrant
        quad_diff = min(abs(q - zone.quadrant), 4 - abs(q - zone.quadrant))
        return max(5, 25 - (quad_diff * 10))
"""knight — Exploration Domain: Central Symmetry & Soft Resource Inference.

Projects map symmetry from observed friendly Core and ore tiles to infer
enemy Core positions and high-value mirrored ore clusters early.
"""

from __future__ import annotations

import sys
from typing import Optional, Tuple
from fcode import Controller, Position

from config import Config
from exploration.spread_zone import SpreadZone, zone_score
from utils.geometry import get_core_footprint, is_in_bounds, is_inside_core_area, is_valid_ore_target


class SymmetryHints:
    """Soft guidance cache predicting enemy base and mirrored ore clusters."""

    def __init__(self) -> None:
        self.enabled: bool = Config.SYMMETRY_ENABLED
        self.enemy_core_hint: Optional[Position] = None
        self.ore_hints: list[tuple[Position, int, int]] = []  # (position, confidence, created_round)

    def initialize(self, ct: Controller, core_pos: Position) -> None:
        """Estimate enemy Core position via 180-degree central inversion."""
        if not self.enabled:
            return
        map_w = ct.get_map_width()
        map_h = ct.get_map_height()
        self.enemy_core_hint = Position(map_w - 1 - core_pos.x, map_h - 1 - core_pos.y)

    def mirror_position(self, ct: Controller, pos: Position) -> Position:
        """Compute centrally mirrored coordinate."""
        map_w = ct.get_map_width()
        map_h = ct.get_map_height()
        return Position(map_w - 1 - pos.x, map_h - 1 - pos.y)

    def add_ore_hint(self, ct: Controller, ore_pos: Position) -> None:
        """Add mirrored candidate position when a friendly local ore tile is discovered."""
        if not self.enabled:
            return

        core_footprint = get_core_footprint(ct)
        mirrored = self.mirror_position(ct, ore_pos)
        if not is_in_bounds(ct, mirrored) or is_inside_core_area(mirrored, core_footprint, margin=0):
            return

        # Check if already present
        for p, _, _ in self.ore_hints:
            if p == mirrored:
                return

        curr_rnd = ct.get_current_round()
        confidence = Config.SYMMETRY_CONFIDENCE_INITIAL

        if len(self.ore_hints) >= Config.SYMMETRY_MAX_HINTS:
            # Drop lowest confidence or oldest entry
            self.ore_hints.sort(key=lambda item: (item[1], -item[2]))
            self.ore_hints.pop(0)

        self.ore_hints.append((mirrored, confidence, curr_rnd))

        if Config.DEBUG_ECONOMY:
            print(f"SYMMETRY_HINT_ADDED hint={mirrored} confidence={confidence} round={curr_rnd}", file=sys.stderr)

    def best_ore_hint(
        self,
        ct: Controller,
        builder_pos: Position,
        zone: Optional[SpreadZone] = None,
        core_pos: Optional[Position] = None,
    ) -> Optional[Position]:
        """Return highest-value valid symmetry ore hint."""
        if not self.enabled or not self.ore_hints:
            return None

        self.prune_expired(ct)
        if not self.ore_hints:
            return None

        core_footprint = get_core_footprint(ct)
        curr_rnd = ct.get_current_round()
        candidates: list[tuple[int, Position]] = []

        for p, conf, created_rnd in self.ore_hints:
            if not is_valid_ore_target(ct, p, core_footprint):
                continue

            # Decay confidence over time
            age = curr_rnd - created_rnd
            eff_conf = conf - (age // Config.SYMMETRY_CONFIDENCE_DECAY_TURNS)
            if eff_conf <= 0:
                continue

            dist_to_builder = builder_pos.distance_squared(p)
            z_score = zone_score(p, core_pos, zone) if (zone is not None and core_pos is not None) else 50

            score = (eff_conf * 100) + z_score - (dist_to_builder // 4)
            candidates.append((score, p))

        if not candidates:
            return None

        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]

    def mark_hint_failed(self, ct: Controller, pos: Position) -> None:
        """Penalize or remove an ore hint upon discovering wall/obstruction/void."""
        curr_rnd = ct.get_current_round()
        updated_hints = []
        for p, conf, created_rnd in self.ore_hints:
            if p == pos or p.distance_squared(pos) <= 2:
                new_conf = conf - 2
                if new_conf > 0:
                    updated_hints.append((p, new_conf, created_rnd))
                if Config.DEBUG_ECONOMY:
                    print(f"SYMMETRY_HINT_FAILED hint={p} round={curr_rnd}", file=sys.stderr)
            else:
                updated_hints.append((p, conf, created_rnd))
        self.ore_hints = updated_hints

    def prune_expired(self, ct: Controller) -> None:
        """Discard symmetry hints older than SYMMETRY_HINT_TTL_TURNS."""
        curr_rnd = ct.get_current_round()
        self.ore_hints = [
            (p, c, r)
            for p, c, r in self.ore_hints
            if (curr_rnd - r) <= Config.SYMMETRY_HINT_TTL_TURNS
        ]
"""knight — Exploration Domain: Frontier Search & Maze-Breaking Navigation.

Maintains spatial occupancy memory (walkable/blocked/visited) and computes
frontier boundary targets to aggressively explore unmapped terrain and mazes.
"""

from __future__ import annotations

from collections import deque
import sys
from typing import Optional, Set
from fcode import Controller, Direction, EntityType, Environment, Position

from config import Config
from exploration.spread_zone import SpreadZone, in_spread_zone, zone_score
from exploration.symmetry import SymmetryHints
from utils.geometry import (
    get_core_footprint,
    is_adjacent,
    is_in_bounds,
    is_in_vision,
    is_valid_ore_target,
    unpack_ore_discovery,
    unpack_pos,
)


def pos_key(pos: Position) -> int:
    """Pack Position into uint32 for fast set/dict operations."""
    return ((pos.x & 0xFFFF) << 16) | (pos.y & 0xFFFF)


def key_to_pos(key: int) -> Position:
    """Unpack uint32 into Position."""
    return Position((key >> 16) & 0xFFFF, key & 0xFFFF)


class FrontierExplorer:
    """Frontier-based exploration state machine for Economy and Scout Builders."""

    def __init__(self) -> None:
        self.seen_walkable: set[int] = set()
        self.seen_blocked: set[int] = set()
        self.visited: set[int] = set()
        self.failed_targets: dict[int, int] = {}
        self.frontier_cache: list[Position] = []
        self.frontier_round: int = -100
        self.wall_follow_mode: bool = False
        self.wall_hand: Direction = Direction.EAST
        self.stuck_counter: int = 0
        self.last_pos: Optional[Position] = None
        self.zone_relax_turns: int = 0

    def observe(self, ct: Controller) -> None:
        """Scan local vision tiles and update spatial occupancy memory."""
        my_team = ct.get_team()
        pos = ct.get_position()
        self.visited.add(pos_key(pos))

        for tile in ct.get_nearby_tiles():
            k = pos_key(tile)
            env = ct.get_tile_env(tile)

            if env == Environment.WALL:
                self.seen_blocked.add(k)
                self.seen_walkable.discard(k)
            elif env == Environment.ORE_TITANIUM:
                self.seen_walkable.add(k)
            else:
                bid = ct.get_tile_building_id(tile)
                if bid is not None:
                    etype = ct.get_entity_type(bid)
                    if etype in (EntityType.CONVEYOR, EntityType.SPLITTER):
                        self.seen_walkable.add(k)
                    else:
                        self.seen_blocked.add(k)
                        self.seen_walkable.discard(k)
                else:
                    self.seen_walkable.add(k)

    def local_ore_target(self, ct: Controller) -> Optional[Position]:
        """Return nearest visible unmined ORE_TITANIUM tile."""
        pos = ct.get_position()
        core_footprint = get_core_footprint(ct)
        best_ore: Optional[Position] = None
        best_dist = 10**9

        for tile in ct.get_nearby_tiles():
            k = pos_key(tile)
            if k in self.failed_targets:
                continue
            if is_valid_ore_target(ct, tile, core_footprint):
                d = pos.distance_squared(tile)
                if d < best_dist:
                    best_dist = d
                    best_ore = tile

        return best_ore

    def store_ore_target(self, ct: Controller) -> Optional[Position]:
        """Read fresh unmined ore broadcast from SLOT_ORE_DISCOVERY."""
        curr_rnd = ct.get_current_round()
        core_footprint = get_core_footprint(ct)
        raw_val = ct.read_store(Config.SLOT_ORE_DISCOVERY)
        if raw_val == 0:
            return None

        # Check freshness via round_mod (mod 256)
        pos, round_mod = unpack_ore_discovery(raw_val)
        if pos is not None:
            age = (curr_rnd - round_mod) % 256
            if age <= Config.ORE_DISCOVERY_TTL_ROUNDS:
                if pos_key(pos) not in self.failed_targets and is_valid_ore_target(ct, pos, core_footprint):
                    return pos

        return None

    def frontier_targets(self, ct: Controller) -> list[Position]:
        """Return known walkable tiles that border unobserved terrain with CPU valve guard."""
        curr_rnd = ct.get_current_round()
        if curr_rnd - self.frontier_round < Config.FRONTIER_CACHE_TTL_TURNS and self.frontier_cache:
            return self.frontier_cache

        if ct.get_cpu_time_elapsed() > Config.CPU_SEARCH_VALVE_MICROSEC and self.frontier_cache:
            return self.frontier_cache

        frontiers: list[Position] = []
        map_w = ct.get_map_width()
        map_h = ct.get_map_height()

        for k in self.seen_walkable:
            p = key_to_pos(k)
            is_frontier = False
            for d in (Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST):
                adj = p.add(d)
                if 0 <= adj.x < map_w and 0 <= adj.y < map_h:
                    adj_k = pos_key(adj)
                    if adj_k not in self.seen_walkable and adj_k not in self.seen_blocked:
                        is_frontier = True
                        break

            if is_frontier:
                frontiers.append(p)

        self.frontier_cache = frontiers
        self.frontier_round = curr_rnd
        return frontiers

    def choose_scout_target(
        self,
        ct: Controller,
        zone: SpreadZone,
        symmetry: SymmetryHints,
        core_pos: Position,
    ) -> Optional[Position]:
        """Select best exploration target using tiered priority pipeline."""
        pos = ct.get_position()
        curr_rnd = ct.get_current_round()
        map_w = ct.get_map_width()
        map_h = ct.get_map_height()
        is_maze = max(map_w, map_h) <= Config.MAZE_MAP_MAX_DIMENSION

        # Increment zone relax turns once per turn
        self.zone_relax_turns += 1

        # Clean expired failed targets
        self.failed_targets = {
            k: r for k, r in self.failed_targets.items() if curr_rnd - r <= Config.ORE_TARGET_COOLDOWN_TURNS
        }

        # Priority 1: Visible local ore
        loc_ore = self.local_ore_target(ct)
        if loc_ore is not None:
            self.zone_relax_turns = 0
            return loc_ore

        # Priority 2: Fresh store ore discovery
        store_ore = self.store_ore_target(ct)
        if store_ore is not None:
            return store_ore

        # Priority 3 & 4: Frontier targets (Spread zone aligned first)
        frontiers = self.frontier_targets(ct)
        if frontiers:
            scored_frontiers: list[tuple[int, Position]] = []
            for fp in frontiers:
                if pos_key(fp) in self.failed_targets:
                    continue
                d = pos.distance_squared(fp)
                z_score = zone_score(fp, core_pos, zone) if self.zone_relax_turns < Config.SPREAD_ZONE_RELAX_AFTER_TURNS else 50
                score = z_score * 10 - d
                scored_frontiers.append((score, fp))

            if scored_frontiers:
                scored_frontiers.sort(key=lambda item: item[0], reverse=True)
                return scored_frontiers[0][1]

        # Priority 5: Symmetry hint (when local/mid-range frontier is fully mapped)
        if not is_maze and symmetry.enabled:
            sym_hint = symmetry.best_ore_hint(ct, pos, zone, core_pos)
            if sym_hint is not None and pos_key(sym_hint) not in self.failed_targets:
                return sym_hint

        # Priority 6: Directional vector toward enemy core
        if symmetry.enemy_core_hint is not None:
            return symmetry.enemy_core_hint

        return None

    def find_path_to_frontier(
        self,
        ct: Controller,
        start: Position,
        targets: list[Position],
    ) -> list[Direction]:
        """BFS pathfinder through known walkable tiles toward target set."""
        if not targets:
            return []

        target_set = set(targets)
        if start in target_set:
            return []

        queue: deque[tuple[Position, list[Direction]]] = deque([(start, [])])
        visited_nodes: set[int] = {pos_key(start)}
        nodes_checked = 0

        while queue:
            if ct.get_cpu_time_elapsed() > Config.CPU_SEARCH_VALVE_MICROSEC:
                return []

            nodes_checked += 1
            if nodes_checked > Config.FRONTIER_BFS_NODE_LIMIT:
                break

            curr, path = queue.popleft()

            if curr in target_set:
                return path

            for d in (Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST):
                nxt = curr.add(d)
                k = pos_key(nxt)
                if k in visited_nodes or k in self.seen_blocked:
                    continue
                if k not in self.seen_walkable:
                    continue

                visited_nodes.add(k)
                queue.append((nxt, path + [d]))

        return []

    def wall_follow_fallback(self, ct: Controller) -> Optional[Direction]:
        """Lightweight maze right-hand/left-hand wall follower."""
        if ct.get_move_cooldown() != 0:
            return None

        pos = ct.get_position()
        cardinals = [Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST]

        # Check for stuck condition
        if self.last_pos == pos:
            self.stuck_counter += 1
            if self.stuck_counter >= Config.WALL_FOLLOW_STUCK_THRESHOLD:
                # Switch hand rule
                self.wall_hand = self.wall_hand.opposite()
                self.stuck_counter = 0
        else:
            self.stuck_counter = 0
        self.last_pos = pos

        # Order attempts based on current wall_hand
        rotations = [
            self.wall_hand.rotate_right(),
            self.wall_hand,
            self.wall_hand.rotate_left(),
            self.wall_hand.opposite(),
        ]

        for d in rotations:
            if d.is_cardinal():
                nxt = pos.add(d)
                k = pos_key(nxt)
                if k not in self.seen_blocked and is_in_bounds(ct, nxt) and ct.can_move(d):
                    self.wall_hand = d
                    return d

        return None
