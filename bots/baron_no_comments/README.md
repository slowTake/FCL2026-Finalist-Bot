# baron

Multi-module bot on **HiveMind v2**. Per five builders: **2 guardians** / **1 harasser** / **2 defaults** (`idx % 5`). Defaults run harvesters + inbound relay; guardians leash home; harassers pressure.

## Layout

- `main.py` — policy caps and `Player` dispatch (`BUILDER_HOME_TURNS`, `ORE_STAGGER`, …)
- `builder.py` — role task lists + orchestrators (`runGuardian`, `runDefault`, `runHarasser`, `runBuilderRoles`)
- `defence.py` — home defence (`defGun`, `clearBarriers`, `patrolHome`)
- `offense.py` — harasser helpers (`snipeHarvester`, `offGun`, `avoidFire`, `seekHarvester`, `seekCore`)
- `logistics.py` — default helpers (`buildHarvester`, `relayToCore`, …)
- `pathing.py` — A*, `walkPassable`, `relayPassable`, `findPathAroundObstruction`
- `respawn.py` — core spawn (uses HiveMind slot 4 registry)
- `hivemind.py` → `components/hivemind_v2.py` (+ `vision_v2.py` symlink)

Agents: [`AGENT.md`](AGENT.md). Terms: [`CONTEXT.md`](CONTEXT.md).

## Store slots

| Slot | Owner | Purpose |
|------|-------|---------|
| 0 | Player | `SLOT_RELAY_COMPLETE` |
| 1–3 | Player | `SLOT_ORPHAN_1/2/3` — orphaned-harvester registry (`logistics.py`) |
| 4 | HiveMind | Builder registry count |
| 5–12 | HiveMind | Delta ledger |
| 13 | HiveMind | Metronome |
| 14 | HiveMind | Map id |
| 15 | HiveMind | Packed cores |

Never write 4–15 from Player. 1–3 are now spoken for — nothing else may claim them.

## Run

```bash
source .venv/bin/activate
fcode run baron starter
fcode run baron starter maps/hive.map26
```
