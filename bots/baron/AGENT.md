# Baron — agent playbook

## Key (module map)

Quick index: where to edit and main entrypoints. Update when adding public functions.

| Module | Owns | Key entrypoints |
|--------|------|-----------------|
| `main.py` | Policy caps, store slots, `Player.run` | `Player.run`, `_run_core`, `_run_builder` |
| `builder.py` | Role orchestrators + task lists | `runBuilderRoles`, `isGuardian`, `isHarasser`, `runGuardian`, `runDefault`, `runHarasser` |
| `defence.py` | Defensive orchestrators | `defGun`, `defSentinel`, `threateningTurret`, `clearBarriers`, `reconnectBelts`, `patrolHome` |
| `logistics.py` | Default helpers | `buildHarvester`, `maintainHarvester`, `checkOre`, `relayToCore`, `defendHome`, `healAlly`, `recheckOreNodes`, `openBlockedRoute`, `pathKnownBlocked` |
| `pathing.py` | A*, passable, obstruction fallback, encirclement breakout | `astar`, `astarFromSeeds`, `walkPassable`, `relayPassable`, `findPathAroundObstruction`, `breakEncirclement` |
| `offense.py` | Harasser helpers | `snipeHarvester`, `offGun`, `avoidFire`, `denyOre`, `denyBelt`, `breakBeltDeadlock`, `seekHarvester`, `seekCore` |
| `guns.py` | Placement + turret loops | `placeGun`, `placeDefSentinel`, `allyGunCovering`, `allySentinelCovering`, `gunnerLoop`, `placeSentinel`, `sentinelLoop`, `topUpAmmo` |
| `respawn.py` | Core spawn schedule | `Respawn.run_core_spawn`, `SpawnSchedule` |
| `hivemind.py` → `hivemind_v2` | Map fingerprint, delta ledger | `HiveMind.tick`, `unpack_cores`, `spawn_builder` |
| `vision_v2.py` | Ledger pack/unpack (required by v2) | `pack_ledger_slot` |

**Policy knobs** (builders, ammo, wave, home-guard, ore stagger, ore deadlock, Ti tiers) → top block in `main.py` (`BUILDER_TARGET_BY_TI`, `MAX_BUILDERS`, `WAVE_ATTACK_*`, `BUILDER_HOME_TURNS`, `ORE_STAGGER`, `ORE_DEADLOCK_TURNS`, `GUN_PLACE_MIN_TI`, `AMMO_CONVERT_MIN_TI`).

Role **task lists** → top comments in `builder.py`.

Domain **terms** → `CONTEXT.md`.

---

## Conventions

### camelCase

Baron public functions use **camelCase** (`buildHarvester`, `relayToCore`, `runDefault`, `defGun`, `placeGun`, `runHarasser`). Do not introduce new `snake_case` gameplay entrypoints in baron modules.

### Main → helpers

Each gameplay phase is **one orchestrator** that calls a short list of **single-purpose helpers**. Prefer fixing a helper over growing a monolith.

Example — `relayToCore`:

1. `pickRelayPath` / `findPathAroundObstruction`
2. `neededBeltFacing`
3. `beltFacingOk`
4. `clearWrongBelt` (ally destroy / enemy fire)
5. `placeRelayBelt`
6. `extendRelayWatermark`

Example — `defGun` → `placeGun` only (never sentinels); out-of-leash turrets → `defSentinel` → `placeDefSentinel`.

Example — `runGuardian` → leash → `_reactEnemyTurret` → `defGun` → `defSentinel` → `avoidFire` → `defendHome` → `healAlly` → *[not `underAttack`]* `clearBarriers` → *[not `underAttack`]* `reconnectBelts` → *[not `underAttack`]* economy → `patrolHome`. No builder-attack anywhere in the chain except `clearBarriers`; turrets answer turrets.

Example — `runHarasser` → `snipeHarvester` → `offGun` → `avoidFire` → `denyOre` → `denyBelt` → `seekHarvester` → `seekCore`.

---

## Workflows

1. **Read first**: repo [`AGENT.md`](../../AGENT.md), this file, `CONTEXT.md`, `builder.py` task lists, `README.md`.
2. **Guardian**: `idx % 5 in (0, 1)` (builders 1,5,6,10,…) and `guardianRank ≤ MAX_GUARDIANS` → `builder.runGuardian`; helpers in `defence.py`. Leashed home defence; ignores wave. Guardians are the *only* home defence — no core-HP recall pulls other roles back.
3. **Default role tasks**: orchestrator `runDefault` in `builder.py` (`idx % 5 in (3, 4)`); helpers in `logistics.py` / `defence.py` / `guns.py`. Empty ore via `pickEmptyOre` (`ORE_STAGGER` off = convenience; on = index slots; honours `skippedOres`). A default motionless for `ORE_DEADLOCK_TURNS` outside a relay drops and blacklists its ore claim (`breakOreDeadlock`) — the anti-pile-up guard. Enemy turret in vision → contest before economy, **unless `inRelay`**: a committed belt is always finished, and the builder dodges (`avoidFire` toward ally core) rather than fighting. For `BUILDER_HOME_TURNS` after `birth_round`, default uses defend-first + near-core economy (`≤0` disables). Harassers and guardians never enter this phase.
4. **Harasser**: `idx % 5 == 2` (builders 2,7,12,…) → `builder.runHarasser`; helpers in `offense.py`. A harasser motionless for `HARASS_DEADLOCK_TURNS` blacklists its sticky belt/harvest target into `skippedTargets` and re-picks (`breakBeltDeadlock`) — the offence mirror of `breakOreDeadlock`. Wave: set `WAVE_ATTACK_TURN` / `WAVE_ATTACK_TI` (>0) in `main.py` to switch **non-guardian** builders to harasser once the gate hits. `seekCore` approaches then orbits the enemy core.
5. **Relay / pathing**: geometry in `pathing.py`; belt facing and clear/place in `logistics.py` helpers. A next belt tile under a visible gunner (`tileThreatenedByGunner`) is blacklisted into `bot.relayThreatBlocked` and the path replanned from `relayOrigin` — `relayToCore` aborts only if that replan finds nothing. Never aborts merely because a turret is in vision. The blacklist resets in `clearRelay`, so it is scoped to one relay attempt.
6. **Caps**: only `main.py` policy block.
7. **HiveMind v2**: never write store **4–15** from Player (slot 4 = builder registry). Player slot 0 = first relay done; slots 1–3 free. Ally/enemy cores via `unpack_cores` on slot 15. Extend shared code in [`components/hivemind_v2.py`](../../components/hivemind_v2.py).
8. **Guns**: `defGun` places gunners only, at targets inside the leash; `defSentinel` answers turrets outside it with our own sentinel from a pad inside it (`DEF_SENTINEL`, `TURRET_THREAT_CHEBYSHEV`, `TURRET_MEMORY_TURNS`); harasser `offGun` may place gunners and sentinels (≤1 gunner covering each enemy harvester; sentinel pads prefer nearer-to-core). Neither places on ore.
9. **Verification**: `fcode run baron starter` and `fcode run baron starter maps/hive.map26`.
10. **Debugging TLEs/crashes/stuck units**: the engine swallows per-unit exceptions and keeps the match running, so a crash never shows up as a bad `run_game` result or exit code — only as a Python traceback inside that unit's captured `botOutput.stdout` in the replay. `scripts/mirror_gauntlet.py --bot baron` runs baron vs itself on the live map pool (`bots/baron/features.py`'s `MAP_ID_TO_NAME`) and scans for exactly that; it's what the `Stop` hook in `.claude/settings.json` runs automatically after any edit to `bots/baron/*.py` (gated by a dirty-flag from the paired `PostToolUse` `py_compile` hook, so it only fires when something actually changed). For a specific stuck/oscillating/TLE unit beyond "did it crash": reproduce with `fcode run bots/baron bots/baron <map> --replay <path> --seed 1`, then parse the `.replay26` directly against `scripts/florent_replay_pb2.py` (ad-hoc snippets, not a saved script) — `botOutput.execTimeUs`/`tled` for TLEs, `moveBuilderBot`/`placeEntity`/`removeEntity` for a unit's position history, `updateHp`/`fireTurret` for combat, `updatePlayers` for Ti over time. When the replay's events alone don't explain *why* (e.g. `bot.stuck`/`bot.oreSite`/`bot.skippedOres` internal state), add a temporary `print()` gated to one unit ID + turn range inside `Player._run_builder` (`main.py`) and read it back via that unit's `botOutput.stdout` in a fresh replay — revert before committing. `scripts/replay_debugger.py` looks like it should cover this (re-run `Player.run()` against reconstructed state) but is currently broken: it imports `replay_state.py` and `mock_controller.py`, neither of which was ever actually committed to the repo.
