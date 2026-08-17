# knight RTS Bot — Systems Architecture & Design Specification

---

## 1. Executive Design Principles

`knight` is engineered around a modular, decoupled architecture where 100% of game-state mutations flow through a crash-proof safety layer with mutual exclusivity and CPU valve protection.

---

## 2. Subsystem Hierarchy

```
bots/knight/
├── config.py                     # Centralized leaf constants & hyperparameters
├── main.py                       # Safe entrypoint, Player router, exception handling
├── engine/                       # Execution & Memoization Foundation
│   ├── cache.py                  # Per-turn Euclidean & distance query cache
│   ├── outcome.py                # ActionOutcome & OutcomeReason structured receipts
│   ├── safe_controller.py        # 100% of engine side-effects & cooldown guards
│   └── world.py                  # WorldView centralized turn context
├── blackboard/                   # IPC & Protocol Layer
│   ├── storecoding.py            # Deterministic offset-encoded bitpacking
│   └── ore_protocol.py           # OreLifecycle, multi-tier validation & invalidation
├── strategy/                     # Decoupled Macro Policies
│   ├── threat.py                 # ThreatClassifier & calibrated distance ranges
│   ├── defense_policy.py         # DefensePolicy & guardian retreat logic
│   └── economy_policy.py         # EconomyPolicy & commitment preservation
├── behavior/                     # Domain Skills
│   ├── base_defense.py           # BaseDefenseSkill (apron stand tiles & barriers)
│   └── movement_skill.py         # MovementSkill (Bug2 & BFS navigation via SafeController)
├── combat/                       # Defense & Guardian Tactics
│   └── tactics.py                # CombatTactics (turret placement & guardian priority)
├── logistics/                    # Resource Economy
│   └── economy.py                # EconomyBuilder (state machine & belt paving)
├── spatial/                      # Geometry & Navigation
│   ├── geometry.py               # Spatial re-exports
│   └── movement.py               # BFS pathfinders & Bug2 steering
└── exploration/                  # Map Discovery
    ├── frontier.py               # FrontierExplorer
    ├── spread_zone.py            # Quadrant/band geometric partitioning
    └── symmetry.py               # Turn-1 symmetry projection hints
```

---

## 3. Threat Classification Semantics (`strategy/threat.py`)

| Threat Level | Trigger Condition | Tactical Mode | Macro Impact |
| :--- | :--- | :---: | :--- |
| **`ThreatClass.SAFE (0)`** | No enemy units within alert range | `0 (Eco)` | `EXPANSION_FREE` |
| **`ThreatClass.SCOUT_SIGHTED (1)`** | Enemy builder within $d^2 \le 36$ (6 tiles) | `1 (Balanced)` | `EXPANSION_FREE` (if Core HP $\ge 80\%$) |
| **`ThreatClass.PERIMETER_PRESSURE (2)`** | Armed combat unit within $d^2 \le 49$ (7 tiles) | `1 (Balanced)` | `EXPANSION_PARTIAL` (if Core HP $< 80\%$) |
| **`ThreatClass.BREACH (3)`** | Armed combat unit within $d^2 \le 16$ (4 tiles) or Core damaged | `2 (Defense)` | `EXPANSION_LEASHED` |

*Distant enemy builders ($d^2 > 64$) are strictly ignored by macro policy.*

---

## 4. Ore Target Lifecycle Protocol (`blackboard/ore_protocol.py`)

1. **Plausible vs Confirmed Validation**:
   - `is_plausible_ore_target()`: Validates grid boundaries and Core footprint exclusion. Unseen tiles in fog of war are accepted as soft targets.
   - `is_confirmed_ore_target()`: Requires direct line-of-sight (`is_in_vision`), `get_tile_env == ORE_TITANIUM`, and `get_tile_building_id is None`.
2. **Local Ore Blacklisting**:
   - Occupied nodes: Blacklisted locally for 500 rounds (`ORE_BLACKLIST_OCCUPIED_TTL`).
   - Non-ore tiles: Blacklisted locally for 1000 rounds (`ORE_BLACKLIST_NOT_ORE_TTL`).
   - Core footprint / Out of bounds: Blacklisted permanently.
3. **Shared Invalidation Slot (`SLOT_ORE_STATUS = 3`)**:
   - Builders broadcast invalid coordinates via `pack_ore_status(pos, status)`.
   - Core clears stale or invalidated targets from `SLOT_ORE_DISCOVERY` (Slot 9).
4. **Core Intent Leasing (`INTENT_LEASE_TURNS = 12`)**:
   - Core revokes intent targets unsupported by fresh discovery after 12 rounds, eliminating stale re-commitment deadlocks.

---

## 5. Economy Builder State Machine (`EconomyRoleState`)

- **`IDLE`**: Initial spawn state awaiting assignment.
- **`SCOUTING`**: Exploring frontier boundaries and spread zones.
- **`COMMITTING`**: Moving toward an active ore deposit target.
- **`BUILDING_HARVESTER`**: Constructing Harvester adjacent to ore node.
- **`PAVING_BELT`**: Laying single-path conveyor belt toward Core footprint.
- **`RELAY_COMPLETE`**: Instant transition state upon paving completion.
- **`EXPANDING_AGAIN`**: Outward band promotion and immediate target acquisition without idling at base.
- **`DEFENDING`**: Reserved builder or emergency recall base defense.
- **`RETREATING`**: Pulling inside perimeter under low HP.

---

## 6. Shared Memory Slot Mapping (0–15)

| Slot | Name | Writer | Content |
| :---: | :--- | :---: | :--- |
| **0** | `SLOT_CORE_POS` | Core | $+1$ offset-encoded Core Position |
| **1** | `SLOT_ENEMY_CORE` | — | Reserved |
| **2** | `SLOT_AMMO_RESERVE` | Core | Global ammo reserve target count |
| **3** | `SLOT_ORE_STATUS` | Builders/Core | Shared invalidation status (12-bit X, 12-bit Y, 8-bit status) |
| **4** | `SLOT_BELT_ALERT` | Builders | Broken belt tile alert (packed Pos + round) |
| **5** | `SLOT_TACTICAL_MODE` | Core | `0=Eco`, `1=Balanced`, `2=Defense` |
| **6** | `SLOT_ALERTS` | Core | Position of nearest active threat |
| **7** | `SLOT_TURRET_COUNT` | Core | Count of operational friendly defense turrets |
| **8** | `SLOT_BUILDER_COUNT` | Core | Count of active friendly builders |
| **9** | `SLOT_ORE_DISCOVERY` | Builders | Packed Position & discovery round mod 256 |
| **10** | `SLOT_PROACTIVE_TURRET` | Core | Boolean flag enabling turret construction |
| **11** | `SLOT_GLOBAL_FLAGS` | Core | Scout request and threat bitmask |
| **12** | `SLOT_EXPANSION_POLICY` | Core | `0=Free`, `1=Partial`, `2=Leashed` |
| **13** | `SLOT_BASE_INTEGRITY` | Core | Core HP percentage (0–100) |
| **14** | `SLOT_BUILDER_INTENT` | Core | Authoritative intent (Target X, Y, Action, Priority) |
| **15** | `SLOT_ROLE_ASSIGNMENT` | Core | Packed Primary (Guardian) & Secondary builder IDs |

---

## 7. Structured Transition Telemetry Formats

```
ROLE_STATE_CHANGE builder_id={id} old={old} new={new} round={round}
EXPANSION_COMMIT builder_id={id} ore={pos} round={round} policy={policy}
EXPANSION_ABORT builder_id={id} ore={pos} round={round} policy={policy}
RELAY_COMPLETE builder_id={id} round={round} next_zone={quadrant}/{band}
ORE_BLACKLIST builder_id={id} ore={pos} reason={reason} expiry={round}
INTENT_CLEAR round={round} target={pos} reason={reason}
THREAT_ALERT round={round} mode={mode} pressure={pressure}
POLICY_RAW round={round} raw={raw} final={policy}
```
