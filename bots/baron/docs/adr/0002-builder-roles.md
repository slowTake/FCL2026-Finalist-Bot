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
| **harasser** | `builder.runHarasser` | Helpers in `offense.py`: `offGun` → `seekHarvester` → `seekCore` |

**Assignment** (`hivemind.my_index > 0`):

- `% 4 == 1` → guardian (builders **1, 5, 9, …**)
- `% 2 == 0` → harasser (builders **2, 4, 6, …**)
- else → default (builders **3, 7, 11, …**)

Index `0` means unassigned — stay on default until HiveMind sets the registry index.

Guardians ignore wave gates; defaults and even builders switch to harasser when a wave gate fires. Guardians share `SLOT_THREAT` recall with default.

Enemy harvester / ore scouting uses HiveMind `local_map` (no new store slots).

## Consequences

- Roughly 25% guardians / 50% harassers / 25% economy at steady state.
- `run_raider` remains a thin alias to `runHarasser` for back-compat.
