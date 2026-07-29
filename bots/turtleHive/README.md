# turtleHive

Turtle + updated HiveMind (`components/` symlinks) + **committed relay belts**
(magnum-style) + a **2-deep inward conveyor apron** around the core.

## Belt model

- **Relays:** after placing a harvester, the builder plans a BFS conveyor path once
  and sticks to it until a **core-adjacent (depth-1)** tile (no per-turn replan).
  Existing outer-apron belts are joinable mid-path. If `relay_origin` leaves
  vision, advance it to the **last built path tile** and keep laying toward the
  core (replan from the new origin only if the path is lost).
- **Core apron:** N/E/S/W only, two tiles deep, all conveyors facing inward.
  Starts after **1 harvester** (`TARGET_HARVESTERS_APRON`). No splitters. Outer
  ring is the easy plug-in surface for incoming relays.
- **Pave-while-walking:** `PAVE_WHILE_MOVE = True` — when heading to a known ore,
  `_try_move` may join/build a belt toward the core, but **never destroys** allied
  belts (relays/apron may still destroy+rebuild).
- **Clear-ore task:** after heal, visit nearest known `ORE_LOCATIONS` site: skip
  our harvesters / blocked tiles; `destroy` allied belts from adjacent; stand on
  enemy belts and `fire` own tile; **enemy harvester → steal relay** to our core
  (clear enemy belts on the route via stand-and-fire, then lay ours); empty →
  build harvester + relay.
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
| 4–6 | — | Free |
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
