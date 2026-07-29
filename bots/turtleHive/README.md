# turtleHive

Turtle + updated HiveMind (`components/` symlinks) + **committed relay belts**
(magnum-style) + a **2-deep inward conveyor apron** around the core.

## Belt model

- **Outbound relay:** when pursuing a known ore (`ore_site`) and not yet
  adjacent, plan a committed BFS path **core → ore neighbor**. Belts face
  **toward the core** (reversed along the walk). Join/build only — never
  destroy/replace allied belts (wrong-facing fix-up is left to inbound).
  Completing outbound does **not** grant a spawn credit; `ore_site` stays for
  build/steal. Enemy harvester on the ore is still a valid goal.
- **Inbound relays:** after placing (or stealing) a harvester, plan a BFS
  conveyor path once and stick to it until a **core-adjacent (depth-1)** tile
  (no per-turn replan). May destroy+rebuild wrong-facing allied belts. Existing
  outer-apron belts are joinable mid-path. If `relay_origin` leaves vision,
  advance it to the **last built path tile** and keep laying toward the core.
- **Core apron:** N/E/S/W only, two tiles deep, all conveyors facing inward.
  Starts after **1 harvester** (`TARGET_HARVESTERS_APRON`). No splitters. Outer
  ring is the easy plug-in surface for incoming relays.
- **Pave-while-walking:** `PAVE_WHILE_MOVE = False` — outbound/inbound relays
  own belts toward ore/core.
- **Clear-ore task:** after heal, visit nearest known `ORE_LOCATIONS` site: skip
  our harvesters / blocked tiles; `destroy` allied belts from adjacent; stand on
  enemy belts and `fire` own tile; **enemy harvester → steal relay** to our core
  (clear enemy belts on the route via stand-and-fire, then lay ours); empty →
  build harvester + inbound relay.
- **Start bootstrap (one-off):** builder 1 claims nearest ore ≥5 from the core
  footprint and publishes it to slot 4; builder 2 outbound-paves toward that ore
  (no claim). After each finishes, both permanently resume the normal ore loop.
- **Core spawn:** builders spawn toward map center (not random).
- **Gunners:** `BUILD_GUNNERS = False` for now (belt/apron debugging). Flip that flag to re-enable.
- **Orphan claim:** `CLAIM_ORPHAN_RELAY = False` for a test — aborted relays are not picked up.
- **Relay abort:** `ABORT_RELAY = False` for a test — stuck/no-path won't orphan or leave the relay.

## Store slots

| Slot | Owner | Purpose |
|------|-------|---------|
| 0 | Player | Spawn credits (earned by completing a relay) |
| 1 | Player | Orphaned relay origin, packed position |
| 2 | Player | Harvester count |
| 3 | Player | Shared ore position |
| 4 | Player | One-off start: packed far ore (builder 1→2) |
| 5–6 | — | Free |
| 7–15 | HiveMind | Builder registry, intel, vision, metronome, map id, packed cores |

Ally core is read from slot 15 via `unpack_cores`. Never write 7–15 from Player.

## Run

```bash
source .venv/bin/activate
fcode run turtleHive starter
fcode run turtleHive magnum_grokus
fcode watch replay.replay26
```

Branch: `dev/turtleHive`.
