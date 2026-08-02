# earl builder roles

Who runs what is decided each turn in `_run_builder` (`main.py`), after
HiveMind `tick` and stuck tracking. Gate order: **raid path** → **defender**
→ **economy**.

| Role | Who | Module |
|------|-----|--------|
| **economy** | Odd builders off the raid path (typically 1/3; also post-600 odds) | `logistics.py` (`ECONOMY`, `run_economy`) |
| **phalanx** | Even builders (and pre-600 odds before their raider pivot) while `ACTIVE_RAID_ORDER = "phalanx"` | `defence.py` (`PHALANX`) |
| **raiders** | Offensive siege when `effective_raid_order` is `"raiders"` | `attack.py` (`RAIDERS`) |
| **harrass** | Index **5**: phalanx home until **50**, then core siege (near enemy) / harvester / seek until **300**, then raiders; or full order if `ACTIVE_RAID_ORDER = "harrass"` | `harrass.py` (`HARRASS`) |
| **defender** | Late spawns `my_index >= 6` (when not on the raid path) | `defence.py` |

`main.py` is the role menu: raid → defender → `run_economy`.

Turrets (gunner / sentinel) are not builder roles; they use shared fire helpers
in `attack.py`.

---

## economy

Mine and inbound-relay (`logistics.py`). Named phases (`ECONOMY`):

1. **finish_relay** — if `in_relay`, only pave the inbound chain to the ally core.
2. **idle_ore_recheck** — every 50 turns: clear `skipped_ores` / `ore_site` and
   allow ally-harvester candidates so broken inbound belts can be rebuilt.
3. **ore_site** — sticky site via `pick_ore_site` / `try_ore_site_task`
   (ranked by core then builder distance; dense odd-index stagger):
   - Empty → step off if on tile; adjacent → build harvester, then enter relay.
   - Any harvester (ally or enemy) → enter inbound relay (steal / claim / re-relay).
   - Enemy belt on ore → stand on tile and `fire`.
   - Allied belt on ore → adjacent `destroy`, then treat as empty next turns.
   - Blocked → skip ore, pick another.
4. **heal** — adjacent damaged friendlies (if action free).
5. **move** — toward `ore_site` / target (no pave-while-walking).

### Harvester caps (built count, store slot 2)

Satisfied once the team has built enough harvesters **and** chains are
finished (active relays always complete). While at cap, do **not** seek new
empty ores or steals; idle recheck may still re-relay **ally** harvesters.

| Rounds | Cap |
|--------|-----|
| `< 50` | **1** |
| `50 .. 99` | **2** |
| `100 .. 199` | **3** |
| `200 .. 249` | **4** |
| `250 .. 399` | **5** |
| `≥ 400` | unlimited |

Constants: `HARVESTER_CAP_1`…`_5`, `HARVESTER_CAP_TO_2_TURN` (50),
`TO_3` (100), `TO_4` (200), `TO_5` (250), `UNLIMITED` (400) in `logistics.py`.

Under default phalanx order, pre-600 **odd** economy builders (1/3) pivot to
**raiders** at turn **300** (fire-and-forget). Index **5** runs harrass
(phalanx home until **50**, then core/harvester siege + seek) until 300, then joins that pivot.
Post-600 new odds stay on economy (unlimited after 400).

---

## phalanx

Home defence for even builders once the enemy core is known
(`ACTIVE_RAID_ORDER = "phalanx"`). Named phases (`PHALANX`):

1. **place_phalanx_gunners** — while threat armed (core slot 1 sticky **or**
   local enemy builder sight): wing pads on that side — soft depths **1–5**;
   live builder/turret rush uses the same depths; fog OK to path; nearest
   open pad; face enemy; sync living count to slot 3.
   Home wings do not fill the siege gunner cap.
2. **hunt_turrets** — enemy gunner/sentinel in vision: fire if adjacent, else
   move (after open wing pads are filled while armed).
3. **assist_apron** — 3-deep inward conveyors on the threat side while armed
   (`LAY_APRON_BELTS` in `defence.py`; currently **off**).
4. **guard_harvesters** — from turn 10: 1 gunner cardinally beside each ally
   harvester within Chebyshev 20 of the core footprint.
5. **clear_economy** — enemy harvester in vision → fire/move; else stand-fire
   nearest enemy belt.
6. **soft_idle** — linger on threat/approach depth-1 strip (idle until threat).

**Pivot to raiders** (fire-and-forget):

- Round **300+**: pre-600 odd-index builders
- Round **600+**: pre-600 even-index builders

**Post-600 home refill:** new odds → economy; new evens → phalanx forever.

---

## raiders

Offensive siege (`ACTIVE_RAID_ORDER = "raiders"`, or after a phalanx pivot).
Named phases (`RAIDERS`):

1. **siege_enemy_harvester** — if an enemy harvester is in vision and no ally
   gunner can already hit it (`can_fire_from`), place a gunner on an empty
   tile in range that can hit it (prefer closer; index-staggered; nearest
   uncovered harvester first).
2. **belt_raid** (`round < 30`) — push enemy core; divert to nearest enemy
   conveyor/splitter in vision and stand-fire; resume toward core.
3. **place_gunners** — first 20 turns of each 40-turn loop (epoch 30, or 300
   after odd pivot): up to 8 siege gunners (near enemy core only) at Chebyshev
   1–3 with verified core LOS.
4. **place_siege_sentinels** — last 20 turns of each loop: up to 5 sentinels at
   r² 16–32 (fallback ~3 tiles); sync count to slot 3.
5. **hunt_vision** — when placement is idle at cap: attack nearest enemy
   gunner → sentinel → harvester → belt; else push enemy core.

Ammo from turn 30 (or turn 10 under phalanx): core tops pool to
`12 + 2×gunners + 10×sentinels` from known ally turrets in `local_map`.

---

## harrass

Harvester siege (`harrass.py`). Named phases (`HARRASS`):

1. **phalanx_home** (`round < 50`) — delegate to `run_phalanx` until
   `HARRASS_START_TURN`.
2. **siege_enemy_core** — when Chebyshev ≤ **3** of the enemy 2×2 footprint:
   raider core gunner/sentinel siege (`attack._run_raid_core_siege`).
3. **siege_enemy_harvester** — same as raiders: if an enemy harvester is in
   vision and no ally gunner can already hit it (`can_fire_from`), place a
   gunner on an empty tile in range that can hit it (prefer closer;
   index-staggered; nearest uncovered first)
   (`attack._run_siege_enemy_harvester`).
4. **seek_harvesters** — when siege has no site: hunt enemy gunner → sentinel
   → harvester → belt in vision; else push enemy core (`attack._run_raid_hunt`).

**Assignment under phalanx:** builder index **5** (`HARRASS_INDEX`) once the
enemy core is known, until turn **300**, then joins the odd **raiders** pivot.
Also available as a full raid order via `ACTIVE_RAID_ORDER = "harrass"`.

---

## defender

Late spawns (`my_index >= 6`) that are **not** on the raid path. Assigned a
core side (`N→E→S→W`; in-bounds only — corner maps may double-up via
`(index - 6) % len(sides)`):

1. **Enemy gunner in vision** — fire if orthogonally adjacent, else move toward it.
2. **Heal** adjacent friendlies.
3. **Fire** adjacent enemy buildings (not builder bots).
4. **Apron** on assigned side only: 3-deep inward conveyors (no barriers);
   repair forever when missing.
5. **Linger** on that side’s depth-1 strip.

No ore / relay / siege. Wing gunners are phalanx-only.
