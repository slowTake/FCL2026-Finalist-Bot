# Audit of `v34_global_escape`: High-Value Features & Reusable Components

This document contains a comprehensive technical audit of `v34_global_escape`, identifying high-value strategies, architectural mechanisms, and reusable components that can be extracted and integrated into current and future bots (`economy_goes_brr`, `baron`, `turtleHive`, etc.).

---

## Executive Summary

While `v34_global_escape` suffered from legacy `cambc` porting issues (such as state machine replan loops and complex IPC slot collisions), it contains several **exceptionally advanced tactical, logistical, and offensive features**. 

The key strengths worth adopting are:
1. **Symmetry-Based Enemy & Resource Inference** (`symmetry.py`)
2. **Reverse-Path Conveyor Stamping & Step-Off Mechanics** (`miner.py`)
3. **Spoke Exploration Strategy** (`miner.py`)
4. **Automated Infrastructure Repair Patrol** (`repair_patrol.py`)
5. **Feed-Line Hijacking & Raider Sabotage** (`raider.py`)
6. **Defensive Ring & Launcher Landing Pad System** (`builder.py`)
7. **Weighted A* Pathfinder & Vector Clamping** (`economy_pathfinder.py`)

---

## 1. Symmetry Inference & Instant Enemy Localization (`symmetry.py`)

### Concept & Value
Instead of waiting turns to scout the entire map, `symmetry.py` uses the friendly Core's coordinates and map bounds to infer map symmetry on Turn 1:
- **Symmetry Types**: Rotational (180°), Vertical Mirror, Horizontal Mirror.
- **Enemy Core Candidates**: Computes potential enemy Core positions immediately.
- **Obstacle & Ore Mirroring**: Observed walls and ores on one half of the map are projected to the opposite side to predict enemy resource clusters and choked corridors without spending exploration ticks.

### How to Adopt
- Use `detect_symmetry` and `mirror_position` in early-game pathfinders to direct Raiders straight toward candidate enemy Cores on Turn 1.
- Pre-seed high-value contested ore locations for miners.

---

## 2. Reverse-Path Conveyor Stamping & Step-Off Mechanics (`miner.py`)

### Concept & Value
- **Step-Off Mechanics**: In `fcode`, units cannot construct buildings on tiles they currently occupy ($\Delta x = 0, \Delta y = 0$). `miner.py` handles this with explicit step-off routines (`_step_off_for_bridge`), stepping onto adjacent tiles before placing conveyors, bridges, or harvesters.
- **Reverse-Path Stamping**: Rather than attempting to pave conveyors while walking outward (which risks building dead-end belts if interrupted), the miner walks outward, builds a Harvester on ore, and then retraces its steps back to the Core.
- **Chain Contiguity & Demolition**: As it steps backward toward the Core, it stamps conveyors pointing home (`_advance_with_stamp`). If it encounters an enemy conveyor or misaligned friendly belt, it destroys it first, ensuring a 100% contiguous supply line.

### How to Adopt
- Adopt `_advance_with_stamp` in economy bots to guarantee that supply chains facing the Core are built cleanly without gaps or reverse-facing belts.

---

## 3. Spoke Exploration Strategy (`miner.py`)

### Concept & Value
- **Orthogonal Spoke Vectors**: Early-game miners are assigned cardinal spoke directions based on unit ID (`bot_id % 4` -> North, East, South, West).
- **Prevents Early Crowding**: Ensures new miners radiate outward in different directions rather than clumping around the first visible ore tile near the Core.
- **Rotation on Obstruction**: If a spoke path hits a map edge or major barrier (`_handle_stuck_rotation`), the miner rotates its spoke vector 90° to explore adjacent sectors.

### How to Adopt
- Use `spoke_dx` / `spoke_dy` logic in `economy_goes_brr` or `starter` variants to fan out early builders across 4 map quadrants.

---

## 4. Automated Infrastructure Repair Patrol (`repair_patrol.py`)

### Concept & Value
- **Patrol Route Generation**: Scans friendly infrastructure (conveyors, harvesters, splitters, bridges) and sorts them by distance from the Core (`_build_patrol_route`).
- **Chain Healing & Rebuilding**: Loops sequentially along the route:
  - If a conveyor link is destroyed or replaced by an enemy, it rebuilds a friendly conveyor facing the Core (`"missing"`).
  - If a harvester or conveyor is damaged (HP < MAX_HP), it heals it (`"damaged"`).
- **Route Refreshing**: Periodically rebuilds its route every 100 turns (`_ROUTE_REFRESH_INTERVAL`) to incorporate new supply chains built by miners.

### How to Adopt
- Assign 1 builder bot mid-game to the `repair_patrol` role to recover broken conveyor links destroyed by enemy raiders or stray turret fire.

---

## 5. Feed-Line Hijacking & Raider Sabotage (`raider.py`)

### Concept & Value
- **Hijacking Live Enemy Feed Lines**: When adjacent to enemy conveyors carrying active resources (`get_stored_resource(bid)`), the Raider builds a Gunner or Sentinel directly on/next to the live feed line (`_find_hijack_opportunity`). This steals or halts enemy resource flow while placing offensive turrets deep inside enemy territory.
- **Conveyor Sabotage**: Destroys enemy conveyors and replaces them with friendly conveyors pointing toward the friendly Core (`_sabotage_current_tile`).
- **Core Orbiting**: Circles the enemy Core at distance 2–3 (`_ORBIT_OFFSETS`), looking for weak entry angles while avoiding static turrets.
- **Bitwise Core Position Sharing**: When a Raider spots the enemy Core, it places a Marker encoding the enemy Core coordinates using bitwise flags (`_MARKER_ENEMY_CORE_FLAG = 1 << 30`). Other Raiders read this Marker and instantly align their assault targets.

### How to Adopt
- Integrate `_find_hijack_opportunity` and bitwise marker communication into raider/assault bots for high-impact economy disruption.

---

## 6. Defensive Ring & Launcher Landing Pad (`builder.py`)

### Concept & Value
- **Ring Layout**: Builds a structured barrier defense around the Core at Chebyshev distances:
  - **Chebyshev-2**: Inner walk/conveyor ring.
  - **Chebyshev-3**: Barrier wall ring.
  - **Chebyshev-4**: Landing pad.
- **Launcher Integration**: Builds a Launcher at Chebyshev-2/3 pointing to a landing pad at Chebyshev-4 (`_defender_phase_build_landing_pad`). Allows spawned units to be launched over defensive barrier walls directly into open ground.

### How to Adopt
- Use launcher-to-landing-pad setups on defensive maps (`vault`, `skerry`) to launch offensive raiders past narrow chokepoints.

---

## 7. Weighted A* & Vector Direction Clamping (`economy_pathfinder.py`)

### Concept & Value
- **Weighted A* Heuristics**: Uses $f = g + 1.25 \times h$ with Chebyshev/Octile distance for fast pathfinding convergence within strict microsecond CPU budgets.
- **Dominant Axis Vector Clamping**: Converts arbitrary coordinate deltas into cardinal directions (`_direction_toward_target`):
  ```python
  if abs(dx) >= abs(dy):
      return Direction.EAST if dx > 0 else Direction.WEST
  else:
      return Direction.SOUTH if dy > 0 else Direction.NORTH
  ```
  Prevents conveyors from defaulting to `Direction.SOUTH` when assigned multi-tile vector steps.

### How to Adopt
- Adopt `_direction_toward_target` across all conveyor-building helper functions.

---

## Summary Table of Components to Extract

| Component | Target File | Key Function / Class | Primary Benefit |
| :--- | :--- | :--- | :--- |
| **Symmetry Inference** | `symmetry.py` | `detect_symmetry()`, `mirror_position()` | Turn-1 enemy core & ore cluster localization |
| **Spoke Exploration** | `miner.py` | `_seed_outward_spoke_from_pos()` | Prevents early-game builder crowding near Core |
| **Reverse Paving** | `miner.py` | `_advance_with_stamp()` | Guarantees 100% contiguous supply chain facing Core |
| **Repair Patrol** | `repair_patrol.py` | `run_repair_patrol()`, `_check_infrastructure_integrity()` | Auto-heals and rebuilds broken conveyor lines |
| **Feed Hijacking** | `raider.py` | `_find_hijack_opportunity()` | Places turrets on enemy resource lines |
| **Marker IPC** | `raider.py` | `_raider_share_enemy_core()`, `_raider_read_enemy_markers()` | Zero-delay enemy target sharing between bots |
| **Vector Clamping** | `raider.py` / `economy_pathfinder.py` | `_direction_toward_target()` | Prevents misaligned conveyor facing directions |
