# ADR 0001: Baron module layout

## Status

Accepted (template scaffold).

## Context

We need a clean multi-file bot with Earl-like capabilities but clearer seams for agents and humans: policy in one place, roles easy to scan, placement separated from offensive strategy.

## Decision

- **`main.py`**: All caps and schedules at the top (builder spawn, harvester caps, ammo policy). `Player.run` dispatches only.
- **`builder.py`**: Two builder roles only — **default** and **raider**; task lists as comments at the top of the file.
- **`logistics.py`**: `run_default` (economy, relay); **`defence.py`**: home helpers used by default, not a separate role.
- **`offense.py`**: `run_raider` — calls `guns`.
- **`guns.py`**: Placement geometry, turret operation, `top_up_ammo` (ammo constants from `main`).
- **`pathing.py`**, **`respawn.py`**: Shared utilities and core spawn.
- **HiveMind**: Symlink `hivemind.py`, `features.py`, `vision.py` from `components/`; extend shared components, not bot-local copies. **Superseded for baron by ADR 0003** (HiveMind v2 + `vision_v2.py`).
- **Docs**: `AGENT.md` Key for code navigation; `CONTEXT.md` for domain terms only.
- **API style**: camelCase public functions; orchestrator → helpers (see `AGENT.md` Conventions / ADR 0003).

## Consequences

- Porting from earl splits monolithic `attack.py` into `guns` + `offense`.
- No `harrass.py` until an ADR adds it or offense grows an explicit harass path.
