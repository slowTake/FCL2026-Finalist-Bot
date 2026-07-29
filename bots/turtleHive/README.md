# turtleHive

Turtle + updated HiveMind (`components/` symlinks) + **committed relay belts**
(magnum-style) + a **2-deep inward conveyor apron** around the core.

## Belt model

- **Relays:** after placing a harvester, the builder plans a BFS conveyor path once
  and sticks to it until a **core-adjacent (depth-1)** tile (no per-turn replan).
  Existing outer-apron belts are joinable mid-path.
- **Core apron:** N/E/S/W only, two tiles deep, all conveyors facing inward.
  No splitters. Outer ring is the easy plug-in surface for incoming relays.
- **No opportunistic pave-while-walking** — belts only come from relays / apron.

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
