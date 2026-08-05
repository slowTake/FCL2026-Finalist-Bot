# Baron — agent playbook

## Key (module map)

Quick index: where to edit and main entrypoints. Update when adding public functions.

| Module | Owns | Key entrypoints |
|--------|------|-----------------|
| `main.py` | Policy caps, store slots, `Player.run` | `Player.run`, `_run_core`, `_run_builder` |
| `builder.py` | Role orchestrators + task lists | `runBuilderRoles`, `runThreatHome`, `runDefault`, `runHarasser` |
| `defence.py` | Defensive orchestrators | `defGun` (gunners only → `placeGun`) |
| `logistics.py` | Default helpers | `buildHarvester`, `maintainHarvester`, `checkOre`, `relayToCore`, `defendHome`, `healAlly`, `recheckOreNodes` |
| `pathing.py` | A*, passable, obstruction fallback | `astar`, `astarFromSeeds`, `walkPassable`, `relayPassable`, `findPathAroundObstruction` |
| `offense.py` | Harasser helpers | `avoidFire`, `offGun`, `seekHarvester`, `seekCore` |
| `guns.py` | Placement + turret loops | `placeGun`, `allyGunCovering`, `gunnerLoop`, `placeSentinel`, `sentinelLoop`, `topUpAmmo` |
| `respawn.py` | Core spawn schedule | `Respawn.run_core_spawn`, `SpawnSchedule` |
| `hivemind.py` → `hivemind_v2` | Map fingerprint, delta ledger | `HiveMind.tick`, `unpack_cores`, `spawn_builder` |
| `vision_v2.py` | Ledger pack/unpack (required by v2) | `pack_ledger_slot` |

**Policy knobs** (builders, ammo, wave, home-guard, threat, Ti tiers) → top block in `main.py` (`SPAWN_INTERVAL_BY_TI`, `WAVE_ATTACK_*`, `BUILDER_HOME_TURNS`, `THREAT_CORE_HP_*`, `GUN_PLACE_MIN_TI`).

Role **task lists** → top comments in `builder.py`.

Domain **terms** → `CONTEXT.md`.

---

## Conventions

### camelCase

Baron public functions use **camelCase** (`buildHarvester`, `relayToCore`, `runDefault`, `defGun`, `placeGun`, `runHarasser`). Do not introduce new `snake_case` gameplay entrypoints in baron modules.

### Main → helpers

Each gameplay phase is **one orchestrator** that calls a short list of **single-purpose helpers**. Prefer fixing a helper over growing a monolith.

Example — `relayToCore`:

1. `pickRelayPath` / `findPathAroundObstruction`
2. `neededBeltFacing`
3. `beltFacingOk`
4. `clearWrongBelt` (ally destroy / enemy fire)
5. `placeRelayBelt`
6. `extendRelayWatermark`

Example — `defGun` → `placeGun` only (never sentinels).

Example — `runHarasser` → `avoidFire` → `offGun` → `seekHarvester` → `seekCore`.

---

## Workflows

1. **Read first**: repo [`AGENT.md`](../../AGENT.md), this file, `CONTEXT.md`, `builder.py` task lists, `README.md`.
2. **Threat recall**: core writes `SLOT_THREAT` with HP hysteresis (`THREAT_CORE_HP_ON` / `OFF`). While set, default builders run `runThreatHome` (abort relay → move to core → defend/heal). Harassers skip.
3. **Default role tasks**: orchestrator `runDefault` in `builder.py`; helpers in `logistics.py` / `defence.py` / `guns.py`. Empty ore picks are index-staggered (`pickEmptyOre`) and skip ally-occupied pads. Enemy turret in vision → abort relay + contest before economy. For `BUILDER_HOME_TURNS` after `birth_round`, default uses defend-first + near-core economy (`≤0` disables). Harassers never enter this phase.
4. **Harasser**: `my_index % 2 == 0` (builders 2,4,6,…) → `builder.runHarasser`; helpers in `offense.py`. Wave: set `WAVE_ATTACK_TURN` / `WAVE_ATTACK_TI` (>0) in `main.py` to switch **all** builders to harasser once the gate hits. Unaffected by `SLOT_THREAT`.
5. **Relay / pathing**: geometry in `pathing.py`; belt facing and clear/place in `logistics.py` helpers. `relayToCore` aborts on enemy turret only after `SLOT_RELAY_COMPLETE`.
6. **Caps**: only `main.py` policy block.
7. **HiveMind v2**: never write store **4–15** from Player (slot 4 = builder registry). Player slot 0 = first relay done; slot 1 = threat latch. Ally/enemy cores via `unpack_cores` on slot 15. Extend shared code in [`components/hivemind_v2.py`](../../components/hivemind_v2.py).
8. **Guns**: `defGun` places gunners only; harasser `offGun` may place gunners and sentinels (≤1 gunner covering each enemy harvester). Neither places on ore.
9. **Verification**: `fcode run baron starter` and `fcode run baron starter maps/hive.map26`.
