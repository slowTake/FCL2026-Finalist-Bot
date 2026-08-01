# earl

Starter economy loop + HiveMind (`components/` symlinks) + **inbound steal-relay**
belts to the **ally** core only. **Raiders** (even builders): push enemy → clear
belts on the way → looping gunners/sentinels every 40 turns from turn 30
(`attack.py`). Builders 1/3/5 stay on economy (idle ore recheck every 50 turns);
late spawns 6+ defend and lay a home apron (`defence.py`). Core tops ammo from turn 30.

## Store slots

| Slot | Owner | Purpose |
|------|-------|---------|
| 0–1 | — | Free |
| 2 | Player | `SLOT_HARVESTER_COUNT` — total harvesters we built |
| 3 | Player | `SLOT_SENTINEL_COUNT` — living siege sentinels (for ammo top-up) |
| 4–6 | — | Free |
| 7 | HiveMind | Builder registry count (spawn increments; builders read as `my_index`) |
| 8–9 | HiveMind | Packed enemy intel (up to 4 targets) |
| 10–12 | HiveMind | Packed vision grid (passable bitmask broadcast) |
| 13 | HiveMind | Vision metronome — which builder broadcasts this even turn |
| 14 | HiveMind | Map id (fingerprint) |
| 15 | HiveMind | Packed ally/enemy cores (`unpack_cores`) |

Never write 7–15 from Player. Ally core is read from slot 15.

## Core spawn

Builders spawn on an apron tile around the full **2×2 core footprint**
(not just neighbors of the NW tile), preferring the tile **closest to the
enemy core** when known (ASSUMED/KNOWN from slot 15); otherwise closest to
**map center**. Tries candidates via `hivemind.spawn_builder` / `can_spawn`.

**Schedule:** rush the first **5** as soon as Ti allows, then spawn **one more
every 25 turns** starting at turn **10**, up to **9 living** (turns
10 / 35 / 60 / 85). After the initial 9 have been spawned, **replenish ASAP**
whenever living builders drop below 9. If the rush finishes after turn 10, take
the next interval slot.

From turn ≥ 30 the core tops team ammo to `5 + 20 × max(1, living sentinels)`
via `convert_ammo` (before the living-cap early return), reading slot 3.

## Builder task order

Each turn (`_run_builder`), after HiveMind `tick` and stuck tracking:

0. **Defender gate** (`my_index >= 6`) → `defence.py`, each on an assigned
   core side (`N→E→S→W`; in-bounds sides only — corner maps with 2 sides get
   **two defenders per side** via `(index - 6) % len(sides)`):
   1. **Enemy gunner in vision** — fire if orthogonally adjacent, else move
      toward it (skip the rest this turn).
   2. **Heal** adjacent friendlies.
   3. **Fire** adjacent enemies (prefer builders).
   4. **Apron** on assigned side only: 2-deep inward conveyors + clockwise
      corner barrier (N→NW, E→NE, S→SE, W→SW); repair forever when missing.
   5. **Linger** on that side’s depth-1 strip.
   No ore / relay / siege. Gunner building deferred.
1. **Raider gate** (even `my_index`, enemy core known, any turn) → abort any
   relay/ore task; run `attack.py` phases below; move toward the chosen site and
   return. Odd builders (1/3/5) keep the economy loop below.
2. **Idle ore recheck** (not in relay, every 50 turns): clear `skipped_ores` /
   `ore_site` and allow ally-harvester candidates so broken inbound belts can be
   rebuilt. Does not abort a live relay.
3. **If `in_relay`** → only run inbound relay (`_relay_toward_core`); return.
4. **Ore-site task** (`_try_ore_site_task`) — sticky `ore_site` via `_pick_ore_site`:
   - Empty → step off if on tile; adjacent → build harvester, then enter inbound relay.
   - Any harvester (ally or enemy) → enter inbound relay (steal / claim / re-relay).
   - Enemy belt on ore → stand on tile and `fire`.
   - Allied belt on ore → adjacent `destroy`, then treat as empty next turns.
   - Blocked → skip ore, pick another.
5. **If relay just started** → run `_relay_toward_core`; return.
6. **Heal** adjacent damaged friendlies (if action free).
7. **Move** toward `ore_site` / `_pick_target` (no pave-while-walking).

### Raiders (`attack.py`)

Once the enemy core is known (`my_index` even, > 0):

1. **Belt raid** (`round < 30`): head toward the enemy core; if an enemy
   conveyor/splitter is in vision, divert to the **nearest** one and stand-fire
   (builders fire own tile only); then resume toward the core.
2. **Siege loop** (from turn 30, every **40** turns):
   - **Gunners** (first **20** turns of each cycle): place up to **3** ally
     gunners on empty tiles at Chebyshev distance **1–3** from the enemy 2×2
     footprint (**prefer closer**); **face into the known enemy core** (nearest
     footprint tile; prefer cardinal aim); raiders side-stagger. Still stand-fire
     if on a belt. Idle at cap until the next gunner window (or if turrets die,
     rebuild).
   - **Sentinels** (last **20** turns of each cycle): place up to **5** siege
     sentinels at r² 16–32 facing into the enemy core (prefer max range); if no
     site fits, fall back to ~**3** tiles from the footprint; sync count to slot 3;
     idle after cap.

Examples: gunners 30–49, sentinels 50–69, gunners 70–89, …

- **Ammo:** from turn 30, core keeps pool at `5 + 20 × max(1, n)` (prestock 25,
  then +20 per living sentinel). Turrets use global ammo.
- **Gunner fire:** core → builders → harvesters → belts (conveyor/splitter).
- **Sentinel fire:** core → builders → harvesters.

### Ore targeting

- Known sites from `ORE_LOCATIONS` once the map is fingerprinted.
- Prefer empty ore, then enemy/unknown harvester (steal).
- Skip our harvesters and locally finished sites (`skipped_ores`), except on
  idle recheck when ally harvesters are allowed so inbound relays can be fixed.
- **Builder stagger:** each builder uses HiveMind `my_index` and takes the
  `(my_index - 1) % n`-th nearest eligible ore (builder 1 → nearest, builder 2
  → 2nd, …). Stops everyone racing the same node. Unregistered index `0` falls
  back to nearest.

### Inbound relay

After building or claiming a harvester, BFS a conveyor path to an **ally**
core-adjacent tile (`is_core_adjacent` requires our team). Belts face toward the
ally core. Wrong-facing allied belts are destroyed and rebuilt; enemy belts are
cleared via stand-and-fire, then replaced. Completing or aborting the path
clears relay state; builder then picks a new ore.

## Run

```bash
source .venv/bin/activate
fcode run earl starter
fcode run earl starter maps/hive.map26
```
