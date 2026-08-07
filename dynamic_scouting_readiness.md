# Dynamic Scouting Readiness Report

## 1. Fog of War & Vision Tracking
- **State Representation:** The bot does not use a dense 2D boolean array or bitwise packing. Instead, Fog of War is tracked via a sparse Python dictionary (`self.local_map = {}`) where keys are `(x, y)` coordinate tuples. Each entry stores a sub-dictionary containing properties like `last_seen_turn`, `passable`, `ore`, and an `entity` dictionary.
- **Update Cost:** The vision grid is updated in `_update_local_vision()` which naively iterates over a 13x13 grid (`dx` and `dy` from -6 to +6) around every unit, every turn. For 50 units, this results in `50 * 169 = 8,450` loop iterations and dictionary reads/writes per turn. Furthermore, builders scan another 9x9 grid (`3 * 81 = 243` iterations) in three priority passes to broadcast the ledger. This is a massive CPU drain in pure Python and risks silently truncating turns due to Time Limit Exceeded (TLE).

## 2. Frontier Routing & Target Acquisition 
- **The Blind Fallback:** If the `KNOWN_MAPS` fingerprint fails on Turn 0, `enemy_core_pos` remains `None`. In this scenario, the offensive chain (`seekCore`, `denyOre`, `offGun`'s core branch) goes entirely inert. The builder/harasser falls all the way down its priority chain to its last resort: `exploreFrontier(outward=True)`.
- **Frontier Identification:** Yes, `pathing.findFrontierTarget()` handles frontier logic. It scans the unit's `local_map` for any passable tile that has a cardinal neighbor *missing* from the dictionary (indicating an unseen tile). It scores candidates based on distance (`100 / (1 + distance)`) and heavily weights tiles that pull the unit outward from the ally core or toward known ore clusters.

## 3. Global Broadcast (IPC Memory)
- **Discovery Trigger:** Yes, an organic discovery trigger exists. In `hivemind.py`'s `_broadcast_ledger()`, if an enemy `CORE` entity is spotted, it falls into "Priority 0" and is immediately packed into the unit's broadcast ledger to be sent to the global 16-slot store.
- **16-Slot Store Mapping:** **CRITICAL FLAW DETECTED.** The scout packs the coordinates into the delta ledger (Slots 5–12), and the Ally Core *does* read it and updates its own map. However, the Core **never updates Slot 15** (the dedicated global `enemy_core_pos` slot) after Turn 0. Furthermore, because the delta ledger protocol is strictly "Builders Write, Core Reads," *no other builder reads the ledger*. Therefore, if Scout A finds the enemy Core, Scout A and the Ally Core know about it, but Scouts B, C, and D remain completely blind and will continue to wander the frontier aimlessly.

## 4. Tactical Recommendations for Blind Maps
To survive a sprint of unknown maps, the following three architectural components must be implemented immediately:

- **Global Enemy Core Synchronization:** When the Ally Core reads an enemy `CORE` entity from the delta ledger, it must dynamically repack and write those coordinates to Slot 15. This allows `builder._resolveCores()` to instantly update `bot.enemyCorePos` for the entire swarm mid-game.
- **Elevate Frontier Priority:** `exploreFrontier` is currently the absolute last resort. On blind maps, we need a dedicated "Scout" role or an elevated priority branch where a subset of units prioritize `exploreFrontier(outward=True)` *above* defensive clustering or idling, ensuring the map is aggressively mapped early on.
- **Shared Memory Frontier (Read-Back):** Currently, `findFrontierTarget` only calculates the frontier based on a unit's *personal* `local_map` sightings, leading to redundant scouting (multiple units exploring the exact same sector). The Core must either dictate frontier waypoints via the global store, or builders must be allowed to read the ledger to stitch together a shared `local_map`.
