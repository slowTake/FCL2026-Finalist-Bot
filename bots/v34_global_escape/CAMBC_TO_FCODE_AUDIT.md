# v34_global_escape → fcode Migration Audit

## Methodology
Compared the working `bots/baron` codebase (native fcode) against `bots/v34_global_escape` (ported from legacy `cambc`) file-by-file, cross-referenced against the authoritative `ENTITIES.md` API specification.

---

## Architecture Comparison

| Aspect | Baron (working) | v34_global_escape (broken) |
|--------|----------------|---------------------------|
| **Codebase size** | ~190KB across 10 files | ~740KB across 13 files |
| **Movement** | Direct `ct.can_move(dir)` → `ct.move(dir)` | Internal A*, local map, `_execute_step_toward()` with road-building |
| **Economy** | Stateless per-tick: find ore → move → build | 15,000-line FSM with 100+ phases |
| **Map tracking** | HiveMind ledger (dict-based) | Full 2D array `UnitLocalMap` with symmetry inference |
| **Ore approach** | Walk to ore, build harvester when adjacent | A* to goal tile, build roads along path, build harvester |
| **Resource model** | Single `int` (Titanium only) | Dual-currency (Titanium + Axionite) with fallback |

---

## CRITICAL ISSUES (Cause 0 Mined / 0 Buildings)

### 1. `_execute_step_toward` Infinite Replan Loop ⚠️ SHOWSTOPPER
**File**: `economy.py:15267`

The movement function tries to walk to the next planned tile. If `c.can_move(dir)` returns `False` (move cooldown > 0, unit on tile, etc.), it falls through to a road-building branch. If the tile is `Environment.EMPTY`, road-building fails (`can_build_conveyor` returns `False` because you can't build a conveyor on an empty tile you need to just walk on). This returns `"road_invalid"`, which makes `harvest_follow_plan` clear the plan and restart from `harvest_plan_goal`. The bot replans every 3 ticks without ever moving.

**How baron does it**: Baron's `moveToward()` simply calls `ct.can_move(dir)` → `ct.move(dir)`. If it can't move, it returns `False` and the bot tries again next tick. No road-building, no `_execute_step_toward`, no intermediate FSM states. Movement is a single API call, not a multi-phase pipeline.

**Fix**: In `_execute_step_toward`, when `nxt_env == Environment.EMPTY`:
- If `move_cooldown > 0` → return `"wait_cd"` (already partially done but the `nxt_env` variable may be unset if `get_tile_env` threw)
- If `can_move(dir)` returns `False` (unit collision) → return `"move_blocked"` (retry next tick)
- **Never** fall through to road-building for empty tiles

### 2. Uninitialized `nxt_env` Variable ⚠️ CRASH RISK
**File**: `economy.py:15288`

```python
try:
    nxt_env = c.get_tile_env(nxt_pos)
    ...
except GameError:
    pass  # nxt_env is UNBOUND if this throws!

needs_road = (nxt_env != Environment.EMPTY) and not _is_builder_directly_walkable_tile(...)
```

If `get_tile_env()` raises `GameError` (tile outside vision), `nxt_env` is **never assigned** → `NameError` on line 15288. This is swallowed by the top-level `except Exception` in `main.py:405`, silently killing the unit's turn.

**How baron does it**: Baron checks `ct.is_in_vision(tile)` before ever calling `get_tile_env()`. Outside vision, it uses the HiveMind local map dictionary.

**Fix**: Initialize `nxt_env = None` before the try block, and handle the `None` case.

### 3. `EntityType.ROAD` — Does Not Exist in fcode ⚠️ LOGIC ERROR
**Files**: `economy.py` (30+ references), `miner.py`, `raider.py`, `economy_state.py`

`constants.py` line 5 patches `EntityType.ROAD = EntityType.CONVEYOR`, so `EntityType.ROAD` silently maps to `CONVEYOR`. This works for type comparisons but causes confusion in walkability checks:

```python
# economy.py:13560
if etype == EntityType.ROAD or etype in _TRANSPORT_ENTITY_TYPES:
    return True
```

Since `ROAD` == `CONVEYOR` and `CONVEYOR` is in `_TRANSPORT_ENTITY_TYPES`, this is redundant but harmless. However, **any code that tries to call `c.can_build_road()` or `c.build_road()` will crash** because these methods don't exist in fcode.

### 4. `c.can_build_road()` / `c.build_road()` — API Does Not Exist ⚠️ CRASH
**Files**: `raider.py:218-219, 445-446, 469-470`, `repair_patrol.py:350-351, 457-458`

```python
# raider.py:218
if c.can_build_road(next_pos):
    c.build_road(next_pos)
```

`fcode` has **no** `can_build_road` or `build_road` methods. These calls raise `AttributeError`, which is silently swallowed by `main.py`'s top-level except. Raider and RepairPatrol units crash every turn and do nothing.

**How baron does it**: Baron never references roads. It only builds `conveyor`, `splitter`, `harvester`, `barrier`, `gunner`, `sentinel`, `launcher`.

**Fix**: Replace all `can_build_road` / `build_road` calls with `can_build_conveyor(pos, direction)` / `build_conveyor(pos, direction)`.

---

## MODERATE ISSUES (Functional but Wasteful/Risky)

### 5. Dual-Currency Resource Model (Axionite)
**Files**: `economy.py:577-581, 700-760`, `economy_state.py:138-186`

v34 has an entire Axionite refinery pipeline with ~40 state machine phases (`axionite_enter`, `axionite_pick_ore`, `axionite_pick_foundry`, etc.) and `FOUNDRY` entity type references. fcode has **no Axionite** — only Titanium. The `constants.py` patches `ORE_AXIONITE = Environment.EMPTY` and `FOUNDRY = EntityType.HARVESTER`, so the code runs but wastes CPU cycles evaluating dead branches.

The resource check `int(res) if isinstance(res, (int, float)) else int(res[0])` works but is ugly — `get_global_resources()` always returns `int` in fcode.

**How baron does it**: `ct.get_global_resources()` used directly as `int`. No Axionite, no Foundry, no dual-currency tuples.

### 6. `_is_builder_directly_walkable_tile` Missing `Environment.EMPTY` ⚠️ KEY BUG
**File**: `economy.py:13547-13570`

```python
def _is_builder_directly_walkable_tile(local_map, x: int, y: int, ...):
    ...
    rec = local_map.get_known_building(x, y)
    if rec is not None:
        etype = rec.get("entity_type")
        if etype == EntityType.ROAD or etype in _TRANSPORT_ENTITY_TYPES:
            return True
        if etype == EntityType.CORE and rec.get("team") == local_map.my_team:
            return True
        return False  # ← any other building = not walkable
    # Falls through to check env
    env_tile = local_map.get(x, y)
    ...
```

The function checks buildings first, then falls through to env tiles. But **empty ground with no building** may fail the local_map lookup and return `False`, depending on how `get_known_building` and `get` interact. This is used to compute `needs_road` in `_execute_step_toward`, which is the root cause of the replan loop.

**How baron does it**: `walkPassable()` in `pathing.py:224-290` checks vision first (`ct.is_in_vision`), then: wall → False, no building + EMPTY → True, CONVEYOR/SPLITTER → True, else False. Simple, no local map indirection.

### 7. Per-Unit IPC Store Slot Scheme is Fragile
**File**: `main.py:769`

```python
unit_slot = (c.get_id() % 12) + 4
```

fcode has 16 store slots (0-15). Slots 4-15 = 12 slots. If two units have IDs that collide modulo 12, their roles overwrite each other. With only 3 units this rarely triggers, but it's a latent bug.

**How baron does it**: Baron uses the HiveMind system — slot 4 is a builder registry bitfield, slots 5-12 are a delta ledger. Roles are derived deterministically from spawn order index (`my_index % 5`), not from store slots. No collision possible.

### 8. Core Spawning Logic References `EntityType.LAUNCHER`
**File**: `main.py:401-404`

```python
elif etype == EntityType.LAUNCHER:
    self._run_launcher(c)
```

Launchers are buildings, not units — they don't run `tick()`. This branch is dead code in fcode. Not harmful but wasteful.

---

## MINOR ISSUES (Cleanup)

### 9. Legacy Constants Still Defined
**File**: `constants.py:136-140, 162-167`

```python
ROAD_BASE_COST = 1
FOUNDRY_BASE_COST = 20
BRIDGE_BASE_COST = 20
ARMOURED_CONVEYOR_BASE_COST = 5
BREACH_BASE_COST = 15
```

These constants are never used by fcode but occupy namespace and confuse readers.

### 10. Debug Trace Logs Left in Production Code
**Files**: `economy.py:15274, 15282`, `main.py:796-797`

```python
with open("/tmp/trace.log", "a") as f:
    f.write(...)
```

File I/O on every tick burns CPU budget. Should be gated behind `COMPETITION_MODE` or removed.

### 11. Logger Map Characters Include Legacy Types
**File**: `logger.py:113-117`

```python
map_chars = {
    3: "=",   # road
    6: "B",   # bridge
    7: "A",   # armoured conveyor
}
```

These tile types don't exist in fcode.

---

## Recommended Fix Priority

### Phase 1: Make It Move (Fixes 0 Mined)
1. **Fix `_execute_step_toward`** (Issue #1 + #2): Initialize `nxt_env`, treat `EMPTY` tiles as walkable (no road needed), return `"wait_cd"` or `"move_blocked"` instead of `"road_invalid"` for empty tiles
2. **Fix `_is_builder_directly_walkable_tile`** (Issue #6): Explicitly return `True` for empty ground tiles
3. **Test**: Run match, verify bot moves to ore and `Titanium mined > 0`

### Phase 2: Fix Crashing Roles
4. **Replace `build_road` calls** in `raider.py` and `repair_patrol.py` (Issue #4): Use `build_conveyor(pos, direction)` instead
5. **Test**: Verify raider and repair_patrol units don't crash silently

### Phase 3: Strip Dead Code
6. Remove entire Axionite pipeline from `economy.py` and `economy_state.py` (Issue #5)
7. Remove `EntityType.ROAD/BRIDGE/ARMOURED_CONVEYOR/FOUNDRY/BREACH` references
8. Remove legacy cost constants
9. Remove debug trace log writes
10. Simplify `get_global_resources()` calls to direct `int` usage

### Phase 4: Architecture Alignment
11. Consider simplifying the movement model to match baron's direct `can_move`/`move` pattern instead of the A*-then-build-roads-then-move pipeline
12. Consider replacing the 100+ phase FSM with a simpler priority-based task system like baron uses
