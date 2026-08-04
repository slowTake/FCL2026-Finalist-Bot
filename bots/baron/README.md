# baron

Multi-module bot template (successor layout to earl). Shared HiveMind via symlinks to [`components/`](../../components/). **Template stubs** — not a competitive bot yet.

## Layout

- `main.py` — policy caps and `Player` dispatch only
- `builder.py` — **default** and **raider** task lists (top of file) + `run_builder_roles`
- `logistics.py` — default role; `defence.py` — helpers for default
- `offense.py` — raider role
- `guns.py` — turret placement and operation
- `pathing.py` / `respawn.py` — routing and core spawn

Agents: read [`AGENT.md`](AGENT.md). Terms: [`CONTEXT.md`](CONTEXT.md).

## Store slots

| Slot | Owner | Purpose |
|------|-------|---------|
| 0 | Player | `SLOT_RELAY_COMPLETE` — TBD |
| 1 | Player | `SLOT_PHALANX_THREAT` — TBD |
| 2 | Player | `SLOT_HARVESTER_COUNT` — harvesters built |
| 3 | Player | `SLOT_SENTINEL_COUNT` — TBD |
| 4–6 | — | Free |
| 7–15 | HiveMind | Never write from Player |
| 15 | HiveMind | Packed cores (`unpack_cores`) |

## Run

```bash
source .venv/bin/activate
fcode run baron starter
fcode run baron starter maps/hive.map26
```
