# ADR 0002: Builder roles (default, guardian, harasser)

## Status

Accepted.

## Context

Earl-style menus (economy / phalanx / raiders / overrides) are too heavy for baron’s initial design. We need thin roles for pressure and home defence without a full raid menu.

## Decision

Builder role **names** and task lists (top of `builder.py`):

| Role | Entry | Modules |
|------|--------|---------|
| **guardian** | `builder.runGuardian` | Home defence (`defence` / `logistics`): leash → react → `defGun` → `defendHome` → `healAlly` → `clearBarriers` → `patrolHome` |
| **default** | `builder.runDefault` | Economy, relay, home (`defence` / `logistics`) |
| **harasser** | `builder.runHarasser` | Helpers in `offense.py`: `snipeHarvester` → `offGun` → `avoidFire` → `seekHarvester` → `seekCore` |

**Assignment** (`hivemind.my_index > 0`, `idx % 5`):

- `1` or `0` → guardian (builders **1, 5, 6, 10, …**)
- `2` or `4` → harasser (builders **2, 4, 7, 9, …**)
- `3` → default (builders **3, 8, 13, …**)

Per five spawns: **2 guardians / 2 harassers / 1 default**. Index `0` means unassigned — stay on default until HiveMind sets the registry index.

Guardians ignore wave gates; non-guardians switch to harasser when a wave gate fires. Guardians share `SLOT_THREAT` recall with default.

Empty-ore stagger is optional (`ORE_STAGGER` in `main.py`); default off uses convenience pick (core then self distance).

Enemy harvester / ore scouting uses HiveMind `local_map` (no new store slots).

## Consequences

- Steady-state mix is ~40% guardians / 40% harassers / 20% economy.
- `run_raider` remains a thin alias to `runHarasser` for back-compat.
