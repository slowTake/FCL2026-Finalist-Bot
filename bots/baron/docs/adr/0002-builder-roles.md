# ADR 0002: Two builder roles (default and harasser)

## Status

Accepted.

## Context

Earl-style menus (economy / phalanx / raiders / overrides) are too heavy for baron’s initial design. We need a thin second role for pressure without a full raid menu.

## Decision

Builder role **names** and task lists (top of `builder.py`):

| Role | Entry | Modules |
|------|--------|---------|
| **default** | `builder.runDefault` | Economy, relay, home (`defence` / `logistics`) |
| **harasser** | `builder.runHarasser` | Helpers in `offense.py`: `avoidFire` → `offGun` → `seekHarvester` → `seekCore` |

**Assignment**: `hivemind.my_index > 0 and my_index % 3 == 0` (spawned builders **3, 6, 9, …**). Index `0` means unassigned — stay on default until HiveMind sets the registry index.

Enemy harvester / ore scouting uses HiveMind `local_map` (no new store slots).

## Consequences

- Every third builder leaves economy for harassment.
- `run_raider` remains a thin alias to `runHarasser` for back-compat.
