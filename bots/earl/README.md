# earl

Starter economy loop + HiveMind (`components/` symlinks) + **inbound steal-relay**
belts to the core. **Raiders** (even builders): push enemy → clear belts on the
way → gunners @30 (face into enemy core) → sentinels @80 (`attack.py`). Builders
1/5 home-defend at mid-game; late spawns defend (`defence.py`). Core tops ammo
from turn 30.

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
every 100 turns** up to a hard cap of **9** (turns 100 / 200 / 300 / 400).

From turn ≥ 30 the core tops team ammo to `5 + 20 × max(1, living sentinels)`
via `convert_ammo` (before the spawn-cap early return), reading slot 3.

## Builder task order

Each turn (`_run_builder`), after HiveMind `tick` and stuck tracking:

0. **Defender gate** (`my_index >= 6` always, or indices `1`/`5` when turn ≥ 80)
   → `defence.py`: heal adjacent friendlies, else fire adjacent enemies (prefer
   builders); linger on the core apron. No ore / relay / siege. Gunners deferred.
1. **Raider gate** (even `my_index`, enemy core known, any turn) → abort any
   relay/ore task; run `attack.py` phases below; move toward the chosen site and
   return. Builders 1/5 defend at mid-game (gate 0); builder 3 keeps the economy
   loop below.
2. **If `in_relay`** → only run inbound relay (`_relay_toward_core`); return.
3. **Ore-site task** (`_try_ore_site_task`) — sticky `ore_site` via `_pick_ore_site`:
   - Empty → step off if on tile; adjacent → build harvester, then enter inbound relay.
   - Any harvester (ally or enemy) → enter inbound relay (steal / claim).
   - Enemy belt on ore → stand on tile and `fire`.
   - Allied belt on ore → adjacent `destroy`, then treat as empty next turns.
   - Blocked → skip ore, pick another.
4. **If relay just started** → run `_relay_toward_core`; return.
5. **Heal** adjacent damaged friendlies (if action free).
6. **Move** toward `ore_site` / `_pick_target` (no pave-while-walking).

### Raiders (`attack.py`)

Three phases once the enemy core is known (`my_index` even, > 0):

1. **Belt raid** (`round < 30`): head toward the enemy core; if an enemy
   conveyor/splitter is in vision, divert to the **nearest** one and stand-fire
   (builders fire own tile only); then resume toward the core.
2. **Gunners** (`30 ≤ round < 80`): place up to **3** ally gunners on empty tiles
   at Chebyshev distance **1–3** from the enemy 2×2 footprint; **face into the
   known enemy core** (nearest footprint tile; prefer cardinal aim); raiders
   side-stagger. Still stand-fire if on a belt. After the cap, idle until
   sentinel phase.
3. **Sentinels** (`round ≥ 80`): place up to **5** siege sentinels at r² 16–32
   facing into the enemy core; sync count to slot 3; idle after cap.

- **Ammo:** from turn 30, core keeps pool at `5 + 20 × max(1, n)` (prestock 25,
  then +20 per living sentinel). Turrets use global ammo.
- **Gunner fire:** core → builders → harvesters → belts (conveyor/splitter).
- **Sentinel fire:** core → builders → harvesters.

### Ore targeting

- Known sites from `ORE_LOCATIONS` once the map is fingerprinted.
- Prefer empty ore, then enemy/unknown harvester (steal).
- Skip our harvesters and locally finished sites (`skipped_ores`).
- **Builder stagger:** each builder uses HiveMind `my_index` and takes the
  `(my_index - 1) % n`-th nearest eligible ore (builder 1 → nearest, builder 2
  → 2nd, …). Stops everyone racing the same node. Unregistered index `0` falls
  back to nearest.

### Inbound relay

After building or claiming a harvester, BFS a conveyor path to a core-adjacent
tile. Belts face toward the core. Wrong-facing allied belts are destroyed and
rebuilt; enemy belts are cleared via stand-and-fire, then replaced. Completing
or aborting the path clears relay state; builder then picks a new ore.

## Run

```bash
source .venv/bin/activate
fcode run earl starter
fcode run earl starter maps/hive.map26
```
