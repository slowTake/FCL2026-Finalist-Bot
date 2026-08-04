# ADR 0002: Two builder roles (default and raider)

## Status

Accepted (assignment deferred).

## Context

Earl-style menus (economy / phalanx / raiders / overrides) are too heavy for baron’s initial design. We also do not want preset even/odd or parity-based role assignment while roles are being reworked.

## Decision

Builder role **names** and task lists (top of `builder.py`):

| Role | Entry | Modules |
|------|--------|---------|
| **default** | `logistics.run_default` | Economy, relay, home (`defence` helpers as needed) |
| **raider** | `offense.run_raider` | Siege, belt raid, push core |

**Assignment**: not implemented in the template. `run_builder_roles` calls **default** only until a new scheme is designed (store, HiveMind, explicit per-builder state, etc.). No `my_index` parity, no `RAIDER_EVEN_BUILDERS`, no automatic raider when enemy core is known.

## Consequences

- `offense.run_raider` exists as the raider entrypoint but is not dispatched yet.
- Revisit this ADR when role assignment lands.
