# earl

Starter economy loop + HiveMind (`components/` symlinks) + **inbound steal-relay**
belts to the **ally** core only. Default **`ACTIVE_RAID_ORDER = "phalanx"`**
(`main.py`): **even** builders → home phalanx (`defence.py`) once the enemy
core is known; fire-and-forget even waves leave at **300** / **600** by spawn
cohort (later even spawns refill phalanx and never raid); **odd** builders →
economy (`logistics.py`), graduating to harrass on **relay+100** or **age 200**
(whichever first). Set to **`raiders`** for the offensive belt-raid →
gunner/sentinel siege loop (`attack.py`); set to **`harrass`** for harvester
siege + seek (`harrass.py`). Economy harvester caps (**2** until 50, **3**
until 100, **4** until 200, **5** until 250, **6** until 400, then unlimited);
idle-recheck ore every 50 turns. Full role task lists: [`role.md`](role.md).
Core tops ammo from turn 10 (phalanx) or 30 (raiders / harrass).

## Store slots

| Slot | Owner | Purpose |
|------|-------|---------|
| 0 | Player | `SLOT_RELAY_COMPLETE` — 1 after first inbound relay finishes |
| 1 | Player | `SLOT_PHALANX_BUILDER_THREAT` — 0 off; `1+side` arms wings/apron (20-turn sticky from core) |
| 2 | Player | `SLOT_HARVESTER_COUNT` — total harvesters we built |
| 3 | Player | `SLOT_SENTINEL_COUNT` — living phalanx wing/guard gunners or siege sentinels (bookkeeping) |
| 4–6 | — | Free |
| 7 | HiveMind | Builder registry count (spawn increments; builders claim as `my_index` on the turn after birth — store write buffer) |
| 8–9 | HiveMind | Packed enemy intel (up to 4 targets) |
| 10–12 | HiveMind | Packed vision grid (passable bitmask broadcast) |
| 13 | HiveMind | Vision metronome — which builder broadcasts this even turn |
| 14 | HiveMind | Map id (fingerprint) |
| 15 | HiveMind | Packed ally/enemy cores (`unpack_cores`) |

Never write 7–15 from Player. Ally core is read from slot 15.

## Core spawn

Builders spawn on an apron tile around the full **2×2 core footprint**
(not just neighbors of the NW tile), preferring the tile **closest to the
enemy core**. Enemy position comes from map fingerprint (`KNOWN_MAPS` in
`features.py`) on the same turn as detect (HiveMind cache), then slot 15
ASSUMED/KNOWN. Unknown maps fall back to map center. Tries candidates via
`hivemind.spawn_builder` / `can_spawn`.

**Schedule:** rush the first **5** as soon as Ti allows, then spawn **one more
every 25 turns** starting at turn **10**, up to **7** (`spawn_target`).
After 7, grow **one every 50 turns** up to **9**. After 9, grow **one
every 200 turns** as a late fallback (until `MAX_TEAM_UNITS`). From turn
**75**, **replace ASAP** when a builder vanishes from *interior* core vision
(leaving the fog edge does not count — that was causing replenish spam).
Before 75, only the scheduled growth gates run. Turret threat does **not**
raise the early cap (`SPAWN_EXTRA_UNDER_THREAT = False`).

From the ammo start turn the core tops team ammo to
`12 + 2×ally gunners + 10×ally sentinels` (counts from HiveMind `local_map`)
via `convert_ammo` (before the living-cap early return). Phalanx starts at
turn **10**; raiders at **30**.

## Builder task order

Each turn (`_run_builder`), after HiveMind `tick` and stuck tracking. Role
details and phase lists live in [`role.md`](role.md).

0. **Raid gate** — `on_raid_tasklist` (default: even indices once enemy core
   known → `phalanx` or fire-and-forget `raiders` by spawn cohort at 300/600;
   or full `raiders` / `harrass` override); abort relay/ore; move toward site
   and return.
1. **Economy** (`run_economy` / `ECONOMY` in `logistics.py`) — odd builders:
   1. **If `in_relay`** → finish inbound relay (always, even at harvester cap).
   2. **Harrass graduate** — sticky `run_harrasser` when **either** first
      completed relay + **100** turns **or** age ≥ **200** from spawn
      (whichever first; needs enemy core known).
   3. **Snipe enemy harvester** — uncovered enemy harvester in vision →
      place/path gunner (`attack._run_siege_enemy_harvester`).
   4. **Idle ore recheck** (every 50 turns): clear skips; allow ally-harvester
      chain repair. While at cap, do **not** seek new empty/steal sites.
   5. **Ore-site task** — build / steal / clear belts (skipped when at cap
      unless site is already a harvester for re-relay).
   6. **Hunt enemy turret** — if orthogonally adjacent to an enemy
      gunner/sentinel, fire/destroy (even under cap). Else **at cap only**:
      path toward a turret in vision (far chase does not interrupt under-cap
      harvester placement).
   7. **Heal** → **move** toward site.

### Harvester caps (economy)

Built count from slot 2. At cap: finish active chains only; no new seeks.

| Rounds | Cap |
|--------|-----|
| `< 50` | **2** |
| `50–99` | **3** |
| `100–199` | **4** |
| `200–249` | **5** |
| `250–399` | **6** |
| `≥ 400` | unlimited |

### Phalanx (`defence.py`) — default (`ACTIVE_RAID_ORDER = "phalanx"`)

Even builders home-defend once the enemy core is known. Fire-and-forget waves
by `spawn_round` cohort (via `effective_raid_order`):

| Cohort | Behavior |
|--------|----------|
| `spawn < 300` | Phalanx until **300**, then sticky raiders |
| `300 ≤ spawn < 600` | Phalanx until **600**, then sticky raiders |
| `spawn ≥ 600` | Phalanx forever (home refill after wave 2) |

While still on phalanx:

1. **Place wing gunners** while threat is armed — core slot **1** (sticky
   **20** turns) **or** this builder sees an enemy builder / gunner / sentinel
   (same turn): pads on that **threat side** — soft depths **1–5**; live
   builder/turret rush uses the same depths; fog OK to path; nearest open pad
   (no stagger); face toward the enemy; sync living count to slot 3.
   Home wings do not fill the siege gunner cap. Until armed, skip (soft-idle).
2. **Hunt turrets** — if an enemy gunner or sentinel is in vision, fire if
   orthogonally adjacent, else move to attack it (after open wing pads are
   filled while armed).
3. **Assist apron** on the **threat side** while armed: 3-deep
   inward conveyors (no barriers; `LAY_APRON_BELTS` currently **off**).
4. **Guard harvesters**: place **1 gunner** cardinally beside each ally harvester
   within Chebyshev **20** of the core footprint (skip apron/core tiles; face
   enemy). From turn **10**. Stagger raiders across missing pads.
5. **Clear economy** — if an enemy harvester is in vision, fire when adjacent
   else move to it; else path onto the nearest enemy belt and stand-fire.
6. **Heal** — adjacent damaged friendlies (if action free), then soft idle.
7. **Soft idle** — linger on threat/approach depth-1 strip.

While an enemy gunner/sentinel is in **core vision**, threat-spawn extras
are **off** by default (`SPAWN_EXTRA_UNDER_THREAT`); set that flag to raise
the living cap toward **15**. Past 15 the normal fallback cadence is every
**200** turns; team size is still limited by `MAX_TEAM_UNITS`.

Home wing gunners stay; phalanx ammo stays active while
`ACTIVE_RAID_ORDER = "phalanx"`.

Gunner fire uses shared `run_gunner`: non-belt on current line
(core → gunner → sentinel → builder → harvester) → rotate to core/combat →
belts last (belts never block a rotate).

### Raiders (`attack.py`) — `ACTIVE_RAID_ORDER = "raiders"`

Packaged as the `RAIDERS` task list (`run_raiders_tasklist` /
`run_attack_builder`). Once the enemy core is known (`my_index` even, > 0):

1. **Siege enemy harvester** (top priority): if an enemy harvester is in vision
   and no ally gunner can already **hit** it (`can_fire_from`, not mere range):
   fire/destroy if orthogonally adjacent; else path/place a gunner on an empty
   in-range pad (prefer LOS-ok; fall back to any empty pad so bots already
   behind the core are not stuck while fog approachers can still place).
2. **Siege loop** (from turn 30, every **40** turns; epochs restart at **300**
   and **600** so each even wave gets a fresh gunner→sentinel window):
   - **Gunners** (first **20** turns of each cycle): place up to **8** siege
     gunners (count only pads within Chebyshev **3** of the enemy 2×2 — home
     wings do not fill the cap) on empty tiles at distance **1–3**; only sites
     that can **actually hit** the core (`can_fire_from`, cardinals before
     diagonals); raiders side-stagger. Still stand-fire if on a belt.
   - **Sentinels** (last **20** turns of each cycle): place up to **5** siege
     sentinels at r² 16–32 facing into the enemy core (prefer max range); if no
     site fits, fall back to ~**3** tiles from the footprint; sync count to slot 3.
3. **Belt raid** (last): if an enemy conveyor/splitter is in vision, divert to
   the **nearest** one and stand-fire (builders fire own tile only); else push
   the enemy core. Also the pre-30 fallthrough when the siege loop is inactive.

Examples: gunners 30–49, sentinels 50–69, …; after 300: gunners 300–319,
sentinels 320–339, …; after 600: gunners 600–619, …

- **Ammo:** from turn 30 (phalanx: 10), core keeps pool at
  `12 + 2×gunners + 10×sentinels` from known ally turrets in `local_map`.
  Turrets use global ammo.
- **Gunner fire:** non-belt on current line (core → gunner → sentinel →
  builder → harvester) → rotate to core/combat → belts last.
- **Sentinel fire:** core → gunner → sentinel → builder → harvester (fixed facing).

### Harrass (`harrass.py`)

Packaged as the `HARRASS` task list (`run_harrasser`) when
`ACTIVE_RAID_ORDER = "harrass"`. Under default phalanx, odds stay on economy
but may sticky-graduate into this order via the relay+100 / age-200 OR gate.

1. **Phalanx home** (`round < 50`) — same duties as phalanx until
   `HARRASS_START_TURN`.
2. **Siege enemy harvester** — reuse `attack._run_siege_enemy_harvester`
   first (adjacent melee; gunner pads prefer LOS then empty fallback;
   nearest uncovered first).
3. **Siege enemy core** — reuse `attack._run_raid_core_siege` (raider
   gunner/sentinel loop; no near-core gate — path/place while approaching).
4. **Push enemy core** — when no siege site: path toward the enemy core.

### Ore targeting

- Known sites from `ORE_LOCATIONS` once the map is fingerprinted.
- Prefer empty ore, then enemy/unknown harvester (steal).
- Rank by **(Chebyshev to ally core, then builder distance)** so near-core
  nodes win, with nearby-to-builder breaking ties; if core unknown, builder
  distance only.
- **Vision first:** in-vision empty/steal ores beat fogged known map ores.
- Skip our harvesters and locally finished sites (`skipped_ores`), except on
  idle recheck when ally harvesters are allowed so inbound relays can be fixed.
- While at the economy harvester cap, empty/steal picks are disabled; ally
  recheck for chain repair still runs.
- Always take the nearest candidate (no index stagger).

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
