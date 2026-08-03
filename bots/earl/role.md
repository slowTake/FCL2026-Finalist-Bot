# earl builder roles

Who runs what is decided each turn in `roles.run_builder_roles` (called from
`_run_builder` in `main.py`), after HiveMind `tick` and stuck tracking. Gate
order: **raid path** → **economy**.

| Role | Who | Module |
|------|-----|--------|
| **economy** | Odd builders (`my_index` odd) under default phalanx | `logistics.py` (`ECONOMY`, `run_economy`) |
| **phalanx** | Even builders once enemy core known (`ACTIVE_RAID_ORDER = "phalanx"`) | `defence.py` (`PHALANX`) |
| **raiders** | When `ACTIVE_RAID_ORDER = "raiders"` (override) | `attack.py` (`RAIDERS`) |
| **harrass** | When `ACTIVE_RAID_ORDER = "harrass"` (override) | `harrass.py` (`HARRASS`) |

[`roles.py`](roles.py) is the role menu: raid path (evens / overrides including
harrass) → `run_economy` (odds). Toggle `ACTIVE_RAID_ORDER` there.

Turrets (gunner / sentinel) are not builder roles; they use shared fire helpers
in `attack.py`.

---

## economy

Mine and inbound-relay (`logistics.py`). Named phases (`ECONOMY`):

1. **finish_relay** — if `in_relay`, only pave the inbound chain to the ally core.
2. **harrass_after_relay** — sticky `run_harrasser` when **either** the
   **first** completed inbound relay plus **100** turns
   (`ECONOMY_TO_HARRASS_DELAY`) **or** age ≥ **200** from spawn
   (`ECONOMY_MIN_AGE_BEFORE_HARRASS`) — whichever fires first. Requires
   enemy core known.
3. **snipe_enemy_harvester** — if enemy core known and an uncovered enemy
   harvester is in vision, path/place a gunner that can hit it
   (`place_gunner_aggressive` via `_run_siege_enemy_harvester`). Skips mining
   that turn.
4. **idle_ore_recheck** — every 50 turns: clear `skipped_ores` / `ore_site` and
   allow ally-harvester candidates so broken inbound belts can be rebuilt.
5. **ore_site** — sticky site via `pick_ore_site` / `try_ore_site_task`
   (vision-first, then nearest by core then builder distance):
   - Empty → step off if on tile; adjacent → build harvester, then enter relay.
   - Any harvester (ally or enemy) → enter inbound relay (steal / claim / re-relay).
   - Enemy belt on ore → stand on tile and `fire`.
   - Allied belt on ore → adjacent `destroy`, then treat as empty next turns.
   - Blocked → skip ore, pick another.
6. **hunt_enemy_turret** — if orthogonally adjacent to an enemy gunner or
   sentinel, always fire/destroy (even under cap; before ore seek). Else
   **at harvester cap only**: path toward a turret in vision. Far chase
   under cap never runs (harvester placement wins).
7. **heal** — adjacent damaged friendlies (if action free).
8. **move** — toward `ore_site` / target (no pave-while-walking).
### Harvester caps (built count, store slot 2)

Satisfied once the team has built enough harvesters **and** chains are
finished (active relays always complete). While at cap, do **not** seek new
empty ores or steals; idle recheck may still re-relay **ally** harvesters.

| Rounds | Cap |
|--------|-----|
| `< 50` | **2** |
| `50 .. 99` | **3** |
| `100 .. 199` | **4** |
| `200 .. 249` | **5** |
| `250 .. 399` | **6** |
| `≥ 400` | unlimited |

Constants: `HARVESTER_CAP_1`…`_5` (values 2…6), `HARVESTER_CAP_TO_2_TURN` (50),
`TO_3` (100), `TO_4` (200), `TO_5` (250), `UNLIMITED` (400) in `logistics.py`.

Under default phalanx order, **odd** builders stay on the economy role menu
forever (including late spawns); they may graduate to sticky harrass inside
`run_economy` via the OR gate above.

---

## phalanx

Home defence for **even** builders once the enemy core is known
(`ACTIVE_RAID_ORDER = "phalanx"`). Named phases (`PHALANX`):

1. **place_phalanx_gunners** — while threat armed (core slot 1 sticky **or**
   local enemy builder / gunner / sentinel sight): wing pads on that side —
   soft depths **1–5**; live builder/turret rush uses the same depths; fog OK
   to path; nearest open pad; face enemy; sync living count to slot 3.
   Home wings do not fill the siege gunner cap.
2. **hunt_turrets** — enemy gunner/sentinel in vision: fire if adjacent, else
   move (after open wing pads are filled while armed).
3. **assist_apron** — 3-deep inward conveyors on the threat side while armed
   (`LAY_APRON_BELTS` in `defence.py`; currently **off**).
4. **guard_harvesters** — from turn 10: 1 gunner cardinally beside each ally
   harvester within Chebyshev 20 of the core footprint.
5. **clear_economy** — enemy harvester in vision → fire/move; else stand-fire
   nearest enemy belt.
6. **heal** — adjacent damaged friendlies (if action free), then soft idle.
7. **soft_idle** — linger on threat/approach depth-1 strip (idle until threat).

Fire-and-forget even waves (`PHALANX_WAVE1_TURN` / `PHALANX_WAVE2_TURN` in
`roles.py`): spawn `< 300` leave at 300; spawn `< 600` leave at 600; spawn
`≥ 600` stay phalanx forever (home refill). No global raid mode — later
even bots are unaffected.

---

## raiders

Offensive siege when `ACTIVE_RAID_ORDER = "raiders"` (or after a phalanx even
wave). Named phases (`RAIDERS`):

1. **siege_enemy_harvester** — if an enemy harvester is in vision and no ally
   gunner can already hit it (`can_fire_from`): `place_gunner_aggressive` on an
   empty in-range pad (prefer LOS-ok; on-pad places adjacent). Melee only when
   no pad exists. Index-staggered; nearest uncovered first.
2. **place_gunners** — first 20 turns of each 40-turn loop (epoch 30, or 300 /
   600 after each even-wave clock): up to 8 siege gunners via
   `place_gunner_aggressive` (near enemy core only) at Chebyshev 1–3 with
   verified core LOS.
3. **place_siege_sentinels** — last 20 turns of each loop: up to 5 sentinels via
   `place_sentinel_aggressive` at r² 16–32 (fallback ~3 tiles); sync count to
   slot 3.
4. **belt_raid** (last) — divert to nearest enemy conveyor/splitter in vision
   and stand-fire; else push enemy core. Also the pre-30 fallthrough when the
   siege loop is inactive.

Ammo from turn 30 (or turn 10 under phalanx): core tops pool to
`12 + 2×gunners + 10×sentinels` from known ally turrets in `local_map`.

---

## harrass

Harvester siege (`harrass.py`) when `ACTIVE_RAID_ORDER = "harrass"`. Named
phases (`HARRASS`):

1. **phalanx_home** (`round < 50`) — delegate to `run_phalanx` until
   `HARRASS_START_TURN`.
2. **siege_enemy_harvester** — same as raiders
   (`attack._run_siege_enemy_harvester` / `place_gunner_aggressive`); uncovered
   enemy harvester in vision first (gunner pad before melee; on-pad places
   adjacent).
3. **siege_enemy_core** — raider core gunner/sentinel siege
   (`attack._run_raid_core_siege`; gunners via `place_gunner_aggressive`); same
   loop as raiders, no near-core gate (path/place while approaching).
4. **push_enemy_core** — when siege has no site: path toward the enemy core.

Not assigned by index under default phalanx (odds may sticky-graduate via
economy's relay+100 / age-200 OR gate inside `run_economy`).
