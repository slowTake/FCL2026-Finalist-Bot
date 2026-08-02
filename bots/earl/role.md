# earl builder roles

Who runs what is decided each turn in `_run_builder` (`main.py`), after
HiveMind `tick` and stuck tracking. Gate order: **raid path** → **defender**
→ **economy**.

| Role | Who | Module |
|------|-----|--------|
| **economy** | Odd builders off the raid path (typically 1/3/5; also post-600 odds) | `logistics.py` (`ECONOMY`, `run_economy`) |
| **phalanx** | Even builders (and pre-600 odds before their raider pivot) while `ACTIVE_RAID_ORDER = "phalanx"` | `defence.py` (`PHALANX`) |
| **raiders** | Offensive siege when `effective_raid_order` is `"raiders"` | `attack.py` (`RAIDERS`) |
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
3. **ore_site** — sticky site via `_pick_ore_site` / `_try_ore_site_task`:
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
| `< 200` | **3** |
| `200 .. 499` | **5** |
| `≥ 500` | unlimited |

Constants: `HARVESTER_CAP_UNTIL_200`, `HARVESTER_CAP_UNTIL_500`,
`HARVESTER_CAP_LIFT_TURN`, `HARVESTER_CAP_UNLIMITED_TURN` in `logistics.py`.

Under default phalanx order, pre-600 **odd** economy builders pivot to
**raiders** at turn **300** (fire-and-forget). Post-600 new odds stay on
economy (unlimited after 500).

---

## phalanx

Home defence for even builders once the enemy core is known
(`ACTIVE_RAID_ORDER = "phalanx"`). Named phases (`PHALANX`):

1. **hunt_turrets** — enemy gunner/sentinel in vision: fire if adjacent, else move.
2. **place_phalanx_gunners** — from turn 10: fill every in-bounds side; depth
   rings before stacking; up to 4 pads per flank at depths 1–4; face enemy;
   sync living count to slot 3.
3. **assist_apron** — 3-deep inward conveyors on the enemy approach side
   (`LAY_APRON_BELTS` in `defence.py`; currently **off**).
4. **guard_harvesters** — 1 gunner cardinally beside each ally harvester within
   Chebyshev 20 of the core footprint.
5. **clear_economy** — enemy harvester in vision → fire/move; else stand-fire
   nearest enemy belt.
6. **soft_idle** — repair missing apron / rebuild dead pads; else linger on
   approach depth-1 strip.

**Pivot to raiders** (fire-and-forget):

- Round **300+**: pre-600 odd-index builders
- Round **600+**: pre-600 even-index builders

**Post-600 home refill:** new odds → economy; new evens → phalanx forever.

---

## raiders

Offensive siege (`ACTIVE_RAID_ORDER = "raiders"`, or after a phalanx pivot).
Named phases (`RAIDERS`):

1. **siege_enemy_harvester** — if an enemy harvester is in vision and no ally
   gunner already covers it within gunner range, place a gunner on an empty
   tile in range (prefer closer; index-staggered), facing the harvester.
2. **belt_raid** (`round < 30`) — push enemy core; divert to nearest enemy
   conveyor/splitter in vision and stand-fire; resume toward core.
3. **place_gunners** — first 20 turns of each 40-turn loop from turn 30: up to
   3 ally gunners at Chebyshev 1–3 from enemy footprint, face into core.
4. **place_siege_sentinels** — last 20 turns of each loop: up to 5 sentinels at
   r² 16–32 (fallback ~3 tiles); sync count to slot 3.
5. **hunt_vision** — when placement is idle at cap: attack nearest enemy
   gunner → sentinel → harvester → belt; else push enemy core.

Ammo from turn 30 (or turn 10 under phalanx): core tops pool to
`5 + 20 × max(1, n)` living in slot 3.

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
