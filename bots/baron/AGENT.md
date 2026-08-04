# Baron — agent playbook

## Key (module map)

Quick index: where to edit and main entrypoints. Update when adding public functions.

| Module | Owns | Key entrypoints |
|--------|------|-----------------|
| `main.py` | Policy caps, store slots, `Player.run` | `Player.run`, `_run_core`, `_run_builder` |
| `builder.py` | Role orchestrators + task lists | `runBuilderRoles`, `runDefault` |
| `logistics.py` | Default helpers (no role orchestrator) | `buildHarvester`, `maintainHarvester`, `checkOre`, `relayToCore`, `defendHome`, `healAlly`, `recheckOreNodes` |
| `pathing.py` | A*, passable, obstruction fallback | `astar`, `astarFromSeeds`, `walkPassable`, `relayPassable`, `findPathAroundObstruction` |
| `defence.py` | Home defence helpers (placeholders) | — |
| `offense.py` | Raider role (TBD) | `run_raider` |
| `guns.py` | Turret ops (stub — no placement yet) | `run_gunner`, `run_sentinel`, `top_up_ammo` |
| `respawn.py` | Core spawn schedule | `Respawn.run_core_spawn`, `SpawnSchedule` |
| `hivemind.py` → `hivemind_v2` | Map fingerprint, delta ledger | `HiveMind.tick`, `unpack_cores`, `spawn_builder` |
| `vision_v2.py` | Ledger pack/unpack (required by v2) | `pack_ledger_slot` |

**Policy knobs** (builders, harvesters, ammo) → top block in `main.py`.

Role **task lists** → top comments in `builder.py`.

Domain **terms** → `CONTEXT.md`.

---

## Conventions

### camelCase

Baron public functions use **camelCase** (`buildHarvester`, `relayToCore`, `runDefault`). Do not introduce new `snake_case` gameplay entrypoints in baron modules.

### Main → helpers

Each gameplay phase is **one orchestrator** that calls a short list of **single-purpose helpers**. Prefer fixing a helper over growing a monolith.

Example — `relayToCore`:

1. `pickRelayPath` / `findPathAroundObstruction`
2. `neededBeltFacing`
3. `beltFacingOk`
4. `clearWrongBelt` (ally destroy / enemy fire)
5. `placeRelayBelt`
6. `extendRelayWatermark`

---

## Workflows

1. **Read first**: repo [`AGENT.md`](../../AGENT.md), this file, `CONTEXT.md`, `builder.py` task lists, `README.md`.
2. **Default role tasks**: orchestrator `runDefault` in `builder.py`; helpers in `logistics.py`. Keep priority order in `runDefault` and the top-of-file task list.
3. **Relay / pathing**: geometry in `pathing.py`; belt facing and clear/place in `logistics.py` helpers.
4. **Caps**: only `main.py` policy block.
5. **HiveMind v2**: never write store **4–15** from Player (slot 4 = builder registry). Ally/enemy cores via `unpack_cores` on slot 15. Extend shared code in [`components/hivemind_v2.py`](../../components/hivemind_v2.py).
6. **Guns**: no real placement this branch — `placeGunnerOnOre` is a stub.
7. **Verification**: `fcode run baron starter` and `fcode run baron starter maps/hive.map26`.
