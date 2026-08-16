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
    SLOT_BELT_ALERT: int = 4        # Broken belt tile alert (packed Pos + round)
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
    ORE_BLACKLIST_OCCUPIED_TTL: int = 35
    ORE_BLACKLIST_NOT_ORE_TTL: int = 1000
    ORE_BLACKLIST_CORE_TTL: int = 100000
    ORE_BLACKLIST_UNREACHABLE_TTL: int = 40
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
    FLAG_BELT_REPAIR_NEEDED: int = 0x10

    # -------------------------------------------------------------
    # Phase G: Belt Integrity, Relay Memory & Repair Protocol
    # -------------------------------------------------------------
    BELT_ALERT_TTL_ROUNDS: int = 80
    BELT_PATROL_INTERVAL_TURNS: int = 60
    BELT_INCOME_STALL_TURNS: int = 50
    BELT_REPAIR_MAX_FIRE_ATTEMPTS: int = 3
    BELT_REPAIR_CONTESTED_COOLDOWN_TURNS: int = 80

    # -------------------------------------------------------------
    # Phase 11R: Spread Zoning Hyperparameters
    # -------------------------------------------------------------
    SPREAD_ZONE_ENABLED: bool = True
    SPREAD_ZONE_NEAR_DIST_SQ: int = 100
    SPREAD_ZONE_MID_DIST_SQ: int = 400
    SPREAD_ZONE_RELAX_AFTER_TURNS: int = 30

    # -------------------------------------------------------------
    # Phase 11R: Symmetry Soft Hints (Disabled to prevent pathing to enemy base)
    # -------------------------------------------------------------
    SYMMETRY_ENABLED: bool = False
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
    BELT_OBSTRUCTION_PATIENCE: int = 30  # Consecutive blocked turns before re-planning belt

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
    MAX_DEFENSIVE_TURRETS: int = 4
    FORWARD_CREEP_MIN_BANK: int = 800

    # -------------------------------------------------------------
    # Hotfix 2 & 3: Guardian Deferral & Secondary Expansion
    # -------------------------------------------------------------
    BARRIER_SKIP_DELAY_TURNS: int = 35
    MIN_BARRIERS_BEFORE_TURRET: int = 3
    TURRET_PRIORITY_ROUND: int = 80
    DEFENSE_DEADLINE_ROUND: int = 120

    # -------------------------------------------------------------
    # Phase H: Intent Latching, Holding Gates & Retreat Hysteresis
    # -------------------------------------------------------------
    HOLD_MAX_TURNS: int = 8
    RETREAT_STICKY_TURNS: int = 6
    RETREAT_CLEAR_HP: int = 25
    RETREAT_MEMORY_TURNS: int = 12
    DEFENSE_COMMITMENT_ENABLED: bool = True
    OFFENSIVE_COMMITMENT_ENABLED: bool = False
    EMERGENCY_TURRET_PRESSURE: int = 1
    SECONDARY_EXPANSION_MIN_DIST_SQ: int = 36
    SECONDARY_LOCAL_FALLBACK_TURNS: int = 25

    # -------------------------------------------------------------
    # CPU & Survival Safety Constraints
    # -------------------------------------------------------------
    CPU_SAFETY_LIMIT_MICROSEC: int = 8500  # 8.5 ms (1.5 ms margin below 10 ms limit)
    CPU_SEARCH_VALVE_MICROSEC: int = 6000  # 6.0 ms budget valve for pathfinding
