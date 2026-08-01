# earl

Starter economy loop + HiveMind (`components/` symlinks) + **inbound steal-relay**
belts to the core. Mid-game: even builders place sentinels (`attack.py`).

## Store slots

| Slot | Owner | Purpose |
|------|-------|---------|
| 0–1 | — | Free |
| 2 | Player | `SLOT_HARVESTER_COUNT` — total harvesters we built |
| 3–6 | — | Free |
| 7 | HiveMind | Builder registry count (spawn increments; builders read as `my_index`) |
| 8–9 | HiveMind | Packed enemy intel (up to 4 targets) |
| 10–12 | HiveMind | Packed vision grid (passable bitmask broadcast) |
| 13 | HiveMind | Vision metronome — which builder broadcasts this even turn |
| 14 | HiveMind | Map id (fingerprint) |
| 15 | HiveMind | Packed ally/enemy cores (`unpack_cores`) |

Never write 7–15 from Player. Ally core is read from slot 15.

## Core spawn

Builders spawn on the adjacent tile **closest to the enemy core** when known
(ASSUMED/KNOWN from slot 15); otherwise closest to **map center**. Tries dirs
sorted by spawn-tile distance to that goal via `hivemind.spawn_builder`.

## Builder task order

Each turn (`_run_builder`), after HiveMind `tick` and stuck tracking:

0. **Attack gate** (turn ≥ 80, even `my_index`, enemy core known) → abort any
   relay/ore task; place up to 5 sentinels around the enemy core at max range
   (`attack.py`). Then move toward the chosen site and return. Odd builders
   skip this and keep the economy loop below.
1. **If `in_relay`** → only run inbound relay (`_relay_toward_core`); return.
2. **Ore-site task** (`_try_ore_site_task`) — sticky `ore_site` via `_pick_ore_site`:
   - Empty → step off if on tile; adjacent → build harvester, then enter inbound relay.
   - Any harvester (ally or enemy) → enter inbound relay (steal / claim).
   - Enemy belt on ore → stand on tile and `fire`.
   - Allied belt on ore → adjacent `destroy`, then treat as empty next turns.
   - Blocked → skip ore, pick another.
3. **If relay just started** → run `_relay_toward_core`; return.
4. **Heal** adjacent damaged friendlies (if action free).
5. **Move** toward `ore_site` / `_pick_target` (no pave-while-walking).

### Mid-game attack (`attack.py`)

- Trigger: `round >= 80`, `my_index % 2 == 0`, enemy core from slot 15.
- Cap: 5 ally sentinels (counted via HiveMind `local_map`).
- Placement: ring tiles at r² 16–32 from the 2×2 enemy core; builders side-stagger
  by index so they don’t pile on the same tile. Face sentinels at the enemy core.
- No belt feed / ammo conversion yet — placement only. After the cap, even
  builders idle.

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
