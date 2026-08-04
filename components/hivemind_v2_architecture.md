# Hivemind V2 Architecture: Metronome Delta Sync

The `hivemind_v2` architecture is a complete overhaul of the state-sharing mechanics for the `earl` bots. Because the engine restricts inter-bot communication to a 14-slot 32-bit integer ledger (`ct.read_store` / `ct.write_store`), the new architecture abandons the lossy full-map compression in favor of a **Collision-Free Delta Sync Protocol**.

## 1. Ledger Layout
The 14 storage slots are strictly divided between the Core and the Builders to prevent write-collisions:

- **Slots 0-4 (Core Memory)**: Managed exclusively by the Core. Contains global strategic state.
  - `Slot 0`: Fingerprint (Map ID, Team)
  - `Slot 1`: Enemy Core Position
  - `Slot 2`: Enemy Core HP & Minimum Turn to Attack
  - `Slot 3`: Squad Sizes (Attack vs Defense limits)
  - `Slot 4`: **Total Builders Alive** (Used to dictate the Metronome tempo)
- **Slots 5-13 (Delta Broadcasts)**: Used by Builders to broadcast newly discovered tiles, enemy movements, and their own positions.

## 2. The Metronome Sync Protocol
Because multiple builders attempting to write to Slots 5-13 simultaneously will result in race conditions (where later-executing bots overwrite the earlier bots' discoveries), the Hivemind enforces a strict **Metronome**.

1. The Core tracks the `total_builders` count and publishes it in Slot 4.
2. Every builder calculates its own stable `my_index` (based on spawn order).
3. A builder is only allowed to broadcast its vision to the ledger if `global_turn % total_builders == my_index`.
4. This mathematically guarantees that exactly **one builder per turn** has undisputed write-access to the broadcast slots.

> [!TIP]
> The engine evaluates bots sequentially in order of creation. The Metronome leverages this deterministic evaluation to cycle through all builders one by one.

## 3. Bitpacking and Data Compression
Because we can only broadcast delta updates, we must pack as much tile data as possible into slots 5-13.
Using `vision_v2.py`, map tiles and entities are compressed into 32-bit integers.
- Entity types are mapped to a custom **6-bit ID** system.
- Coordinates (X, Y) and Health are packed alongside the Entity ID.
- The very first slot of a builder's broadcast (usually Slot 5) is **always** reserved to broadcast its own current position.

## 4. Local Vision Precedence
Because network data takes a full cycle to reach all bots, a bot's own eyes are always more trustworthy than the network.
Inside the `tick()` function:
1. The bot unpacks all data from the Ledger into its `local_map`.
2. The bot sweeps its immediate surroundings using `ct.get_nearby_entities()`.
3. The bot writes its fresh local vision *over* the `local_map`, ensuring that out-of-date network ghosts are overridden by reality before `A*` pathing executes.

## 5. Ghost Busters (Garbage Collection)
A critical flaw in delta-sync architectures is that moving entities (like allied builders) leave behind "ghosts". If a builder moves away, its previous tile remains permanently marked as occupied in the network map because no one broadcasts an "empty tile" update.

The **Allied Builder Garbage Collection Sweep** solves this:
1. Every time an entity is updated in the map, we tag it with a `last_seen_turn = global_turn`.
2. Because of the Metronome, every builder *must* get a turn to broadcast its own position exactly once every `total_builders` cycle.
3. At the end of `tick()`, the Core loops through the `local_map`. If it finds any allied Builder that hasn't updated its `last_seen_turn` within a `total_builders + 2` threshold, it determines the builder is dead, recalled, or moved out of sight.
4. The ghost is instantly deleted from the map, freeing up the tile for `A*` pathfinding.

> [!IMPORTANT]
> The garbage collection sweep is currently only executed by the Core, as the Core serves as the central pathing authority for logistics and spawn decisions.
