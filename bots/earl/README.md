# earl

Starter economy loop + HiveMind (`components/` symlinks) + **inbound steal-relay**
belts to the **ally** core only. **Even builders** run the active raid order
(`ACTIVE_RAID_ORDER` in `main.py`): default **`phalanx`** (home apron + wing
gunners in `defence.py`; pre-600 **odd** builders pivot to raiders at **300**,
pre-600 **even** at **600**, fire-and-forget; post-600 odds → economy, post-600
evens → phalanx); set to **`raiders`** for the old offensive belt-raid →
gunner/sentinel siege loop (`attack.py`). Economy builders (1/3/5) mine under
a staged harvester cap (**3** until 200, **5** until 500, then unlimited) in
[`logistics.py`](logistics.py) and idle-recheck ore every 50 turns; late
spawns 6+ defend and lay a home apron (`defence.py`). Full role task lists:
[`role.md`](role.md). Core tops ammo from turn 10 (phalanx) or 30 (raiders).

## Store slots

| Slot | Owner | Purpose |
|------|-------|---------|
| 0–1 | — | Free |
| 2 | Player | `SLOT_HARVESTER_COUNT` — total harvesters we built |
| 3 | Player | `SLOT_SENTINEL_COUNT` — living phalanx wing gunners or siege sentinels (ammo) |
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
every 25 turns** starting at turn **10**, up to **9** total spawned (turns
10 / 35 / 60 / 85). After that, **replenish ASAP** whenever living builders
drop below 9. Under turret threat, spawn ASAP up to **15 living** (hard
lifetime `num_spawned` cap still **15** so vision undercount cannot run away).
If the rush finishes after turn 10, take the next interval slot.

From the ammo start turn the core tops team ammo to
`5 + 20 × max(1, living count in slot 3)` via `convert_ammo` (before the
living-cap early return). Phalanx starts at turn **10**; raiders at **30**.

## Builder task order

Each turn (`_run_builder`), after HiveMind `tick` and stuck tracking. Role
details and phase lists live in [`role.md`](role.md).

0. **Raid gate** — `on_raid_tasklist` → `ACTIVE_RAID_ORDER` (`phalanx` or
   `raiders`); abort relay/ore; move toward site and return.
1. **Defender gate** (`my_index >= 6`) → `defence.py` side apron / linger.
2. **Economy** (`run_economy` / `ECONOMY` in `logistics.py`) — odd builders
   off the raid path:
   1. **If `in_relay`** → finish inbound relay (always, even at harvester cap).
   2. **Idle ore recheck** (every 50 turns): clear skips; allow ally-harvester
      chain repair. While at cap, do **not** seek new empty/steal sites.
   3. **Ore-site task** — build / steal / clear belts (skipped when at cap
      unless site is already a harvester for re-relay).
   4. **Heal** → **move** toward site.

### Harvester caps (economy)

Built count from slot 2. At cap: finish active chains only; no new seeks.

| Rounds | Cap |
|--------|-----|
| `< 200` | 3 |
| `200–499` | 5 |
| `≥ 500` | unlimited |

### Phalanx (`defence.py`) — default (`ACTIVE_RAID_ORDER = "phalanx"`)

Even builders stay home once the enemy core is known:

1. **Hunt turrets** — if an enemy gunner or sentinel is in vision, fire if
   orthogonally adjacent, else move to attack it.
2. **Place wing gunners** from turn **10**: fill **every in-bounds side**
   (not only the approach). Depth rings fill before stacking (all sides get
   depth-1 before approach depth-2); within a depth prefer the enemy approach
   side. Up to **4 pads per flank** at depths 1–4; face toward the enemy;
   sync living count to slot 3. Raiders stagger across missing pads.
3. **Assist apron** on the enemy approach side: 3-deep inward conveyors (no
   barriers).
4. **Guard harvesters**: place **1 gunner** cardinally beside each ally harvester
   within Chebyshev **20** of the core footprint (skip apron/core tiles; face
   enemy). Stagger raiders across missing pads.
5. **Clear economy** — if an enemy harvester is in vision, fire when adjacent
   else move to it; else path onto the nearest enemy belt and stand-fire.
6. **Soft idle** — repair missing apron / rebuild dead pads; otherwise linger on
   the approach depth-1 strip.

While an enemy gunner/sentinel is in **core vision**, the core keeps spawning
extra builders past the normal 9 up to **15 living** (lifetime `num_spawned`
hard-capped at 15). Even-index
builders run phalanx (including 6+); odd late spawns still defend assigned sides
until their pivot.

**Pivot to raiders** (`run_attack_builder`) — fire-and-forget (no recall):
- Round **300+**: pre-600 odd-index builders (1, 3, 5, 7, …)
- Round **600+**: pre-600 even-index builders (2, 4, 6, …) leave phalanx

**Post-600 home refill** (by spawn round):
- New **odds** → economy (mine / defender gate)
- New **evens** → phalanx forever

Home wing gunners stay; threat-spawn and phalanx ammo stay active while
`ACTIVE_RAID_ORDER = "phalanx"` (new evens keep the home role after 600).

Gunner fire uses shared `run_gunner` (core → gunner → sentinel → builder →
harvester → belts; rotates toward core/combat/harvester in vision).

### Raiders (`attack.py`) — `ACTIVE_RAID_ORDER = "raiders"`

Packaged as the `RAIDERS` task list (`run_raiders_tasklist` /
`run_attack_builder`). Once the enemy core is known (`my_index` even, > 0):

1. **Siege enemy harvester** (top priority): if an enemy harvester is in vision
   and no ally gunner covers it within gunner range, place a gunner on an empty
   tile in range facing the harvester (index-staggered).
2. **Belt raid** (`round < 30`): head toward the enemy core; if an enemy
   conveyor/splitter is in vision, divert to the **nearest** one and stand-fire
   (builders fire own tile only); then resume toward the core.
3. **Siege loop** (from turn 30, every **40** turns):
   - **Gunners** (first **20** turns of each cycle): place up to **3** ally
     gunners on empty tiles at Chebyshev distance **1–3** from the enemy 2×2
     footprint (**prefer closer**); **face into the known enemy core** (nearest
     footprint tile; prefer cardinal aim); raiders side-stagger. Still stand-fire
     if on a belt.
   - **Sentinels** (last **20** turns of each cycle): place up to **5** siege
     sentinels at r² 16–32 facing into the enemy core (prefer max range); if no
     site fits, fall back to ~**3** tiles from the footprint; sync count to slot 3.
4. **Hunt vision** (when gunner/sentinel placement is idle at cap): attack the
   nearest enemy **gunner → sentinel → harvester → belt** in vision (adjacent
   fire for buildings; stand-fire for belts); else push the enemy core.

Examples: gunners 30–49, sentinels 50–69, gunners 70–89, …

- **Ammo:** from turn 30, core keeps pool at `5 + 20 × max(1, n)` (prestock 25,
  then +20 per living sentinel). Turrets use global ammo.
- **Gunner fire:** core → gunner → sentinel → builder → harvester → belts.
  Rotates toward core or combat targets in vision when off the fire line.
- **Sentinel fire:** core → gunner → sentinel → builder → harvester (fixed facing).

### Ore targeting

- Known sites from `ORE_LOCATIONS` once the map is fingerprinted.
- Prefer empty ore, then enemy/unknown harvester (steal).
- Skip our harvesters and locally finished sites (`skipped_ores`), except on
  idle recheck when ally harvesters are allowed so inbound relays can be fixed.
- While at the economy harvester cap, empty/steal picks are disabled; ally
  recheck for chain repair still runs.
- **Builder stagger:** each builder uses HiveMind `my_index` and takes the
  `(my_index - 1) % n`-th nearest eligible ore (builder 1 → nearest, builder 2
  → 2nd, …). Stops everyone racing the same node. Unregistered index `0` falls
  back to nearest.

### Pathfinding (`pathing.py`)

Builder walks and inbound conveyor routes use **A\*** (cardinal grid). Walks
treat fog as optimistic passable and fall back to greedy step selection if A\*
finds no path. Relays search layable/joinable tiles toward the ally core.

### Inbound relay

After building or claiming a harvester, A\* a conveyor path to an **ally**
core-adjacent tile (`is_core_adjacent` requires our team). Entry prefers the
**nearest** core face; builder-index side stagger is only a tiebreak when
lengths match (avoids routing around the core and tearing up inbound belts).
Falling back to any core-adjacent tile if that face is unreachable. If a path onto a
**visible allied belt** at least Chebyshev **4** from the core is at least
**8** tiles shorter than the core path, prefer that merge spur instead (build
up to the join; do not rebuild the existing network). Near-core / apron belts
are not merge targets. Belts face toward the next hop / ally core. Wrong-facing
allied belts on the spur are destroyed and rebuilt; enemy belts are cleared via
stand-and-fire, then replaced. Progress uses a contiguous vision watermark
(`relay_built_to`) so fogged mid-path gaps are not skipped. Completing or
aborting the path clears relay state; builder then picks a new ore.

## Run

```bash
source .venv/bin/activate
fcode run earl starter
fcode run earl starter maps/hive.map26
```
