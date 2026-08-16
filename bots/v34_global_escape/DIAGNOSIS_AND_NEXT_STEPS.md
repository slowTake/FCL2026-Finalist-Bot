# `v34_global_escape` Modernization & Diagnostic Breakdown

## 1. Overview & Goal
`v34_global_escape` is being modernized from legacy engine standards (`cambc`) to the current `fcode` RTS engine API (defined in `ENTITIES.md`). The bot previously threw swallowed runtime trace exceptions, crashed unit role assignment via shared memory IPC, and failed to move or mine Titanium (0 Titanium mined in matches against `baron`).

---

## 2. Completed Bug Fixes & API Alignments

### A. IPC Shared Memory Role Overwrites
- **Root Cause**: Core was writing newly spawned unit roles to global store slot `0` (`STORE_SPAWN_ROLE`). Every time a new unit (e.g., Healer or Defender) spawned, store slot 0 was overwritten, causing existing Economy bots on tick 20+ to read slot 0, change their `role`, and overwrite their state machine with `DefenderState` or `HealerState`.
- **Fix**: Implemented dedicated per-unit store slots indexed by unit ID hash `(unit_id % 12) + 4` in [`main.py`](file:///home/markb/florent_league/teaPot/bots/v34_global_escape/main.py). Spawners write to `(unit_id % 12) + 4`, and bots permanently lock their role on initial tick.

### B. Runtime Signature & API Contract Mismatches
1. **`c.get_nearby_units()` -> `c.get_nearby_entities()`**: Fixed `AttributeError` in [`main.py`](file:///home/markb/florent_league/teaPot/bots/v34_global_escape/main.py#L211) where Core unit tracking crashed every round.
2. **`c.can_build_conveyor(pos, direction)` & `c.build_conveyor(pos, direction)`**: Updated single-argument calls across [`builder.py`](file:///home/markb/florent_league/teaPot/bots/v34_global_escape/builder.py), [`economy.py`](file:///home/markb/florent_league/teaPot/bots/v34_global_escape/economy.py), and [`miner.py`](file:///home/markb/florent_league/teaPot/bots/v34_global_escape/miner.py) to pass the explicit `Direction` required by `fcode`.
3. **`c.get_global_resources()`**: Fixed `TypeError: 'int' object is not subscriptable` in [`economy.py`](file:///home/markb/florent_league/teaPot/bots/v34_global_escape/economy.py) where 2-tuple unpacking (`res[0]`, `res[1]`) was attempted on `fcode`'s single integer return value.
4. **`UnitLocalMap` Interface Additions**:
   - Added `.get(x, y)` tile query method in [`local_map.py`](file:///home/markb/florent_league/teaPot/bots/v34_global_escape/local_map.py).
   - Added `MAP_ORE_AXIONITE` to `constants` import in [`local_map.py`](file:///home/markb/florent_league/teaPot/bots/v34_global_escape/local_map.py) to prevent `NameError` during map scans.
   - Initialized default `planner_self_xy`, `enable_unit_halo_planning`, `unit_halo_blocked_tiles` attributes on `UnitLocalMap`.

### C. Economy State Machine & Tile Passability
1. **Initial Phase & Launcher Elimination**: Updated initial `EconomyState` phase in [`economy_state.py`](file:///home/markb/florent_league/teaPot/bots/v34_global_escape/economy_state.py) from `"seek_launcher"` to `"harvest_enter"` with `launcher_xy = core_xy`, bypassing legacy pre-launch launcher escape routines.
2. **`WALKABLE_TILES`**: Included `MAP_FREE` and `MAP_UNKNOWN` in `WALKABLE_TILES` in [`constants.py`](file:///home/markb/florent_league/teaPot/bots/v34_global_escape/constants.py) so pathfinders recognize empty ground tiles as directly walkable by builder bots.
3. **Ore Core Exclusion**: Set `_ORE_CORE_EXCLUSION_MIN_CHEB = 0` in [`economy_state.py`](file:///home/markb/florent_league/teaPot/bots/v34_global_escape/economy_state.py) so Titanium ore near the Core is not filtered out as an excluded barrier zone.

---

## 3. Current Diagnostic State & Remaining Root Cause

With all exception crashes resolved, unit trace logs in `/tmp/trace.log` show the exact execution loop:
1. `Economy Bot #3` starts in `harvest_enter`, transitions to `harvest_pick_ore`, picks Titanium Ore at distance 2-3, and transitions to `harvest_plan_goal`.
2. `harvest_plan_goal` computes an A* path step `nxt` to an adjacent tile.
3. In `harvest_follow_plan`, `_execute_step_toward(c, local_map, cur_xy, nxt)` is called.
4. If `c.can_move(move_dir)` returns `False` (for instance, when `c.get_move_cooldown() > 0` or when blocked by adjacent unit/building), `_execute_step_toward` attempts to build a conveyor on `nxt`.
5. Because `nxt` is an empty tile or occupied by Core/unit or action cooldown is active, `c.can_build_conveyor` returns `False`, causing `_execute_step_toward` to return `"road_invalid"`.
6. When `_run_harvest_follow_plan` receives `"road_invalid"`, it clears `plan_steps` and sets `state.phase = "harvest_plan_goal"`.
7. This causes an infinite replan loop (`harvest_plan_goal` -> `harvest_follow_plan` -> `road_invalid` -> `harvest_plan_goal`), preventing the bot from stepping forward onto the target tile.

---

## 4. Next Recommended Steps for Resolution

1. **Refactor `_execute_step_toward` in [`economy.py`](file:///home/markb/florent_league/teaPot/bots/v34_global_escape/economy.py#L15264)**:
   - Ensure that if `nxt_env == Environment.EMPTY` or if `nxt_pos` has `EntityType.CORE`/`CONVEYOR`/`SPLITTER`, `needs_road` is strictly `False`.
   - If `c.get_move_cooldown() > 0` and `needs_road` is `False`, return `"wait_cd"` (wait for move cooldown to expire) instead of falling through to conveyor building and returning `"road_invalid"`.
   - If `c.can_move(move_dir)` returns `False` due to temporary unit collision, return `"move_blocked"` (wait/retry step next tick) instead of returning `"road_invalid"`.

2. **Verify `_is_titanium_goal_valid`**:
   - Inspect [`_is_titanium_goal_valid`](file:///home/markb/florent_league/teaPot/bots/v34_global_escape/economy.py#L7989) to ensure it returns `True` when the target tile adjacent to Titanium ore is empty or has a conveyor.

3. **Validate Harvester & Conveyor Build Sequence**:
   - Once Economy Bot reaches the goal tile adjacent to Titanium ore, verify transition to `harvest_build`.
   - Check `c.can_build_harvester(ore_pos)` call in `_run_harvest_build` (1 argument) and `_build_conveyor_on_tile` (2 arguments: `bp, out_dir`).

4. **Verification Command**:
   - Run match validation:
     ```bash
     /home/markb/florent_league/teaPot/.venv/bin/fcode run v34_global_escape baron maps/antler.map26
     ```
   - Confirm `v34_global_escape` places `HARVESTER` and `CONVEYOR` structures and registers positive mined Titanium in the match summary table.
