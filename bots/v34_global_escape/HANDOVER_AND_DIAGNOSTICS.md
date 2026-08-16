# Handover & Technical Diagnostics: `v34_global_escape` Porting to `fcode`

## Executive Summary & Current Status
`v34_global_escape` has been successfully migrated from legacy `cambc` to full `fcode` compliance.

### Benchmark Results
- **Vs `earl` (5 maps, 10 paired games)**: **9 / 10 wins (90.0% Win Rate, Elo +382)**.
- **Vs `baron` (5 maps, 10 paired games)**: **0 / 10 wins (Core destroyed on turns 60–136)**.

---

## 1. Accomplished Major Fixes

1. **`Environment.ORE_AXIONITE` Monkey-Patch Collision (CRITICAL)**:
   - `constants.py` patched `Environment.ORE_AXIONITE = Environment.EMPTY`. When pathfinders checked if a tile was an obstacle (`tile in (WALL, ORE_TITANIUM, ORE_AXIONITE)`), empty tiles evaluated as `True`, blocking all movement.
   - *Fix*: Set `Environment.ORE_AXIONITE = None`.

2. **Diagonal Movement Elimination (`GameError`)**:
   - `economy_pathfinder.py` defaulted `cardinal_only = False`, generating 8-directional diagonal steps prohibited by `fcode`.
   - *Fix*: Set `cardinal_only = True` across pathfinders and enforced `_CARDINAL_DIRECTION_BY_DELTA`.

3. **`fcode` Spatial Invariants & Step-Off Mechanics**:
   - `fcode` prohibits building on the tile a Builder Bot is standing on ($\Delta x = 0, \Delta y = 0$).
   - *Fix*: Added step-off logic in `_build_bridge_on_tile` so units step off target tiles before constructing conveyors.

4. **Conveyor Facing Vector Direction Clamping**:
   - When building multi-tile conveyor chains, `_build_bridge_on_tile` passed non-cardinal distance vectors, causing conveyor orientations to default to `Direction.SOUTH`.
   - *Fix*: Added Manhattan vector clamping (`abs(dx) >= abs(dy)`) to guarantee conveyors face directly along the supply route towards the destination.

5. **Eliminating Uncaught Exception Crashes**:
   - Exception handlers contained un-nested `/tmp/trace.log` file writes. File IO failures raised uncaught `OSError`s, causing the engine to destroy unit/conveyor entities at 20 HP.
   - *Fix*: Removed file writes from exception handlers.

---

## 2. Diagnostics: Why `baron` Wins Early (Turns 60–136)

Despite achieving 90% win rate against `earl`, `v34_global_escape` loses to `baron` early due to 3 specific strategic flaws:

### Flaw A: Delayed Early Defense (The Gunner Rush Vulnerability)
- **Symptom**: `baron` sends early harassers/gunners around turn 50–70.
- **Root Cause**: `v34_global_escape` only initializes `target_defender_count = 1` when Core HP drops below 70%. By the time Core HP reaches 70%, `baron`'s Gunners are already dealing maximum DPS to the Core, leading to Core destruction 10 turns later.
- **Next Step**: Enable an early defensive Gunner build trigger (`target_defender_count = 1` or automatic Gunner placement) on round 30 or when `ti >= 100`.

### Flaw B: Modulo 12 IPC Store Slot Collisions
- **Symptom**: Builder Bots occasionally oscillate between roles or abandon conveyor building.
- **Root Cause**: `main.py` maps spawned bot IDs to 12 IPC store slots via `unit_slot = (spawned_id % 12) + 4`. When more than 12 units spawn, store slots collide and overwrite older unit roles. Unwritten slots return `0`, falling back to `STORE_SPAWN_ROLE` which may contain `ROLE_RAIDER`.
- **Next Step**: Store assigned roles permanently in the unit's local `self.role` instance attribute on tick 1, and never re-query shared IPC store slots on subsequent ticks.

### Flaw C: CPU Execution Timeouts
- **Symptom**: `fcv` reports ~600 CPU timeouts across 5 maps when `COMPETITION_MODE = False`.
- **Root Cause**: Diagnostic microsecond timer calls (`time.perf_counter_ns()`) and heavy logging overhead consume CPU budget.
- **Next Step**: Set `COMPETITION_MODE = True` in `bots/v34_global_escape/constants.py` for tournament evaluation.

---

## 3. Recommended Next Steps Checklist

1. [ ] Set `COMPETITION_MODE = True` in `bots/v34_global_escape/constants.py`.
2. [ ] In `bots/v34_global_escape/main.py`, trigger an early Defender bot or Gunner placement when enemy units are in vision or round >= 30.
3. [ ] Refactor role persistence in `main.py` to ensure unit roles are stored purely in local instance memory (`self.role`) rather than re-read from shared IPC store slots.
4. [ ] Run `fcv run v34_global_escape bots/baron --maps sprint vault crossfire duel vase --seeds 1 --jobs 4` to verify win rate improvements against `baron`.
