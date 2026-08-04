# baron

Multi-module bot on **HiveMind v2**. Default builders: harvesters + inbound relay. Every even-indexed builder (2,4,6,…) is a **harasser**.

## Layout

- `main.py` — policy caps and `Player` dispatch
- `builder.py` — role task lists + orchestrators (`runThreatHome`, `runDefault`, `runBuilderRoles`)
- `offense.py` — harasser helpers (`avoidFire`, `offGun`, `seekHarvester`, `seekCore`)
- `logistics.py` — default helpers (`buildHarvester`, `relayToCore`, …)
- `pathing.py` — A*, `walkPassable`, `relayPassable`, `findPathAroundObstruction`
- `respawn.py` — core spawn (uses HiveMind slot 4 registry)
- `hivemind.py` → `components/hivemind_v2.py` (+ `vision_v2.py` symlink)

Agents: [`AGENT.md`](AGENT.md). Terms: [`CONTEXT.md`](CONTEXT.md).

## Store slots

| Slot | Owner | Purpose |
|------|-------|---------|
| 0 | Player | `SLOT_RELAY_COMPLETE` |
| 1 | Player | `SLOT_THREAT` — core HP hysteresis (on &lt;400, off ≥500) |
| 2–3 | Player | free |
| 4 | HiveMind | Builder registry count |
| 5–12 | HiveMind | Delta ledger |
| 13 | HiveMind | Metronome |
| 14 | HiveMind | Map id |
| 15 | HiveMind | Packed cores |

Never write 4–15 from Player.

## Run

```bash
source .venv/bin/activate
fcode run baron starter
fcode run baron starter maps/hive.map26
```
