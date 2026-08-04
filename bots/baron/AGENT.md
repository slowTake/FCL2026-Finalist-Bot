# Baron — agent playbook

## Key (module map)

Quick index: where to edit and main entrypoints. Update this table when you add public functions.

| Module | Owns | Key entrypoints |
|--------|------|-----------------|
| `main.py` | Policy caps, store slot constants, `Player.run` dispatch | `Player.run`, `_run_core`, `_run_builder` |
| `builder.py` | Role dispatch (assignment TBD) | `run_builder_roles` |
| `logistics.py` | Default role (economy, relay) | `run_default`, `run_economy`, `pick_economy_target`, `try_ore_site_task` |
| `defence.py` | Home defence helpers (default role) | `run_phalanx`, `update_phalanx_builder_threat` |
| `offense.py` | Raider role execution | `run_raider`, `run_raiders`, `run_attack_builder` |
| `guns.py` | Placement geometry + turret operation | `place_gunner_aggressive`, `place_sentinel_aggressive`, `run_gunner`, `run_sentinel`, `top_up_ammo` |
| `pathing.py` | A*, walk + relay routing | `astar`, `manhattan` |
| `respawn.py` | Core spawn schedule | `Respawn.run_core_spawn`, `SpawnSchedule` |
| `hivemind.py` (symlink) | Map fingerprint, vision broadcast | `HiveMind.tick`, `unpack_cores` — change in `components/` only |

**Policy knobs** (builders, harvesters, ammo) → always the top block in `main.py`.

Human-readable **role task lists** (default + raider only) → top comments in `builder.py`.

Domain **terms** → `CONTEXT.md` (glossary, not a code index).

---

## Workflows

1. **Read first**: repo [`AGENT.md`](../../AGENT.md) (fcode API), this file, `CONTEXT.md`, `builder.py` task lists, `README.md` store table.
2. **Changing builder roles**: task lists at top of `builder.py` (**default** and **raider** names only). Implement in `logistics.run_default` / `defence` / `offense.run_raider`; wire assignment in `run_builder_roles` when designed (document in ADR). Update Key above.
3. **Guns vs offense**: valid pads, fallbacks, `place_*`, turret fire/rotate in `guns.py`; raider strategy and task order in `offense.py`. Default home defence in `defence.py`. Role modules call `guns` — do not duplicate placement rules.
4. **Changing limits**: only edit `main.py` policy block (spawn, harvester caps, ammo caps/max) — not inside `logistics` or `guns`.
5. **HiveMind**: never write store indices 7–15 from Player; ally/enemy cores via `unpack_cores` on slot 15; extend HiveMind in [`components/`](../../components/) when the shared mind updates.
6. **Pathing / relays**: generic routing in `pathing.py`; per-builder relay state on `Player` in `main` until it becomes unwieldy.
7. **Verification**: `fcode run baron starter` and `fcode run baron starter maps/hive.map26` after non-trivial changes.
8. **No third builder role unless ADR**: extend default or raider task lists, or record a new role in an ADR first.
