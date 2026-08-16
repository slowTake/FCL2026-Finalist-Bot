# Authoritative `fcode` Engine API Reference (Ground-Truth Contract)

> **Source of Truth**: Introspected directly from installed package `fcode` (`v2.3.8`) at `/home/markb/florent_league/teaPot/.venv/lib/python3.12/site-packages/fcode`.
> All bot implementations in `bots/knight` must conform strictly to this contract.

---

## 1. Unit Lifecycle & Engine Runtime Semantics

- **Per-Unit Instance State Persistence (Empirically Verified)**: A unit's `Player` instance and its `self` attributes **persist across all turns of that unit's lifespan** (`tick_counter` increments 1, 2, 3... 20 across turns). Units can safely cache routes, waypoints, and target states in `self`.
- **Cross-Unit Memory Isolation**: Units operate in separate subinterpreters and **do not share in-memory state** with other units. Inter-unit coordination MUST occur via `ct.read_store(index)` and `ct.write_store(index, value)`.
- **CPU Time Budget**: 10,000 $\mu$s (10 ms) limit per unit per turn. Exceeding the CPU budget forfeits the turn.
- **Uncaught Exceptions**: Uncaught exceptions permanently destroy the unit instance.

---

## 2. Core Game Constants (`GameConstants`)

| Constant | Value | Description |
|---|---|---|
| `MAX_TURNS` | `1000` | Maximum game turn count |
| `MAX_TEAM_UNITS` | `50` | Maximum living units per team (including Core) |
| `STORE_SIZE` | `16` | Communication Store size (Slots 0..15) |
| `STARTING_TITANIUM` | `500` | Team initial Titanium balance |
| `CORE_MAX_HP` | `500` | Core maximum health |
| `CORE_SPAWNING_RADIUS_SQ` | `2` | Max distance squared for Core builder spawning (orthogonal adjacent) |
| `BUILDER_BOT_BASE_COST` | `30` | Base cost to spawn Builder Bot |
| `BUILDER_BOT_MAX_HP` | `40` | Builder Bot maximum health |
| `BUILDER_BOT_VISION_RADIUS_SQ` | `20` | Builder Bot vision range squared |
| `CONVEYOR_BASE_COST` | `3` | Base cost for Conveyor belt |
| `SPLITTER_BASE_COST` | `6` | Base cost for Splitter |
| `HARVESTER_BASE_COST` | `20` | Base cost for Harvester |
| `BARRIER_BASE_COST` | `3` | Base cost for Barrier |
| `GUNNER_BASE_COST` | `20` | Base cost for Gunner turret |
| `SENTINEL_BASE_COST` | `30` | Base cost for Sentinel turret |
| `LAUNCHER_BASE_COST` | `20` | Base cost for Launcher turret |
| `GUNNER_VISION_RADIUS_SQ` | `13` | Gunner vision / fire range squared |
| `SENTINEL_VISION_RADIUS_SQ` | `32` | Sentinel vision / fire range squared |
| `GUNNER_DAMAGE` | `7` | Gunner damage per shot |
| `SENTINEL_DAMAGE` | `18` | Sentinel damage per shot |
| `HEAL_AMOUNT` | `4` | HP restored per heal action |

---

## 3. Controller Method Reference (`fcode.Controller`)

### Info & Queries
- `get_id() -> int`: Return this unit's entity ID.
- `get_team(id: int | None = None) -> Team`: Return team (`Team.A` or `Team.B`).
- `get_position(id: int | None = None) -> Position`: Return unit position `Position(x, y)`.
- `get_entity_type(id: int | None = None) -> EntityType`: Return entity type enum (`EntityType.CORE`, `BUILDER_BOT`, `GUNNER`, `SENTINEL`, `HARVESTER`, `CONVEYOR`, `SPLITTER`, `BARRIER`, `LAUNCHER`).
- `get_hp(id: int | None = None) -> int`: Current health points.
- `get_max_hp(id: int | None = None) -> int`: Maximum health points.
- `get_direction(id: int | None = None) -> Direction`: Facing direction for conveyor, splitter, or turret.
- `get_tile_env(pos: Position) -> Environment`: `Environment.EMPTY`, `Environment.WALL`, or `Environment.ORE_TITANIUM`.
- `get_tile_building_id(pos: Position) -> int | None`: Building ID on tile, or `None`.
- `get_tile_builder_bot_id(pos: Position) -> int | None`: Builder Bot ID on tile, or `None`.
- `is_tile_empty(pos: Position) -> bool`: True if tile has no building and is not a wall.
- `is_tile_passable(pos: Position) -> bool`: True if friendly builder bot can stand on tile.
  - `CONVEYOR`: `True` (Conveyor belts are passable for friendly units)
  - `HARVESTER`: `False` (Impassable building)
  - `BARRIER`: `False` (Impassable building)
  - `SPLITTER`: `False` (Impassable building)
  - `GUNNER` / `SENTINEL`: `False` (Impassable building)
  - `CORE`: `False` (Impassable 2x2 building)
- `can_build_conveyor(pos: Position, dir: Direction) -> bool`:
  - Returns `False` whenever `pos` is already occupied by ANY building (Conveyor, Harvester, Barrier, Splitter, Gunner, Core). The "one building per tile" rule is absolute.
  - Requires builder to be orthogonally adjacent (Manhattan distance == 1). Diagonal build distance returns `False`.
- **Movement vs Action Mutual Exclusivity**: Moving (`ct.move()`) and Acting (`ct.build_*()`, `ct.heal()`, `ct.destroy()`) are **mutually exclusive per round**. Calling one sets both cooldowns, preventing the other in the same tick.
- `get_nearby_tiles(dist_sq: int | None = None) -> list[Position]`
- `get_nearby_entities(dist_sq: int | None = None) -> list[int]`
- `get_nearby_buildings(dist_sq: int | None = None) -> list[int]`
- `get_nearby_units(dist_sq: int | None = None) -> list[int]`
- `get_map_width() -> int`, `get_map_height() -> int`
- `get_current_round() -> int`: Current 0-indexed round number.
- `get_global_resources() -> int`: Team Titanium balance.
- `get_global_ammo() -> int`: Team global ammo balance.
- `get_unit_count() -> int`: Living units count on team (including Core). Max is `50`.
- `get_cpu_time_elapsed() -> int`: Elapsed CPU time in microseconds.

### Cooldowns & Cost Scale
- `get_action_cooldown() -> int`: Action cooldown turns remaining. Must be 0 to act.
- `get_move_cooldown() -> int`: Move cooldown turns remaining. Must be 0 to move.
- `can_act() -> bool`: True if action cooldown is 0.
- `get_builder_bot_cost() -> int`, `get_conveyor_cost() -> int`, `get_harvester_cost() -> int`, `get_barrier_cost() -> int`, `get_gunner_cost() -> int`, `get_sentinel_cost() -> int`, `get_launcher_cost() -> int`.

### Movement & Spawning
- `can_move(direction: Direction) -> bool`: True if builder can move in cardinal `direction`.
- `move(direction: Direction) -> None`: Move builder 1 step. Direction must be cardinal (`NORTH`, `EAST`, `SOUTH`, `WEST`).
- `can_spawn(position: Position) -> bool`: Core-only. True if core can spawn builder at `position`. `position` must be orthogonally adjacent to Core footprint.
- `spawn_builder(position: Position) -> int`: Core-only. Spawns builder bot at `position`. Returns new unit ID.

### Ammo & Macro
- `can_convert_ammo(amount: int) -> bool`: Core-only. True if core can convert Titanium to Ammo this turn.
- `convert_ammo(amount: int) -> None`: Core-only. Converts Titanium to Ammo (1:1). Does not use action cooldown. Allowed max once per turn per team.

### Building
*(Position parameter must always be an orthogonally adjacent tile to the builder bot)*
- `can_build_conveyor(position: Position, direction: Direction) -> bool` / `build_conveyor(position, direction) -> int`
- `can_build_splitter(position: Position, direction: Direction) -> bool` / `build_splitter(position, direction) -> int`
- `can_build_harvester(position: Position) -> bool` / `build_harvester(position) -> int` (must be ore tile)
- `can_build_barrier(position: Position) -> bool` / `build_barrier(position) -> int`
- `can_build_gunner(position: Position, direction: Direction) -> bool` / `build_gunner(position, direction) -> int`
- `can_build_sentinel(position: Position, direction: Direction) -> bool` / `build_sentinel(position, direction) -> int`
- `can_build_launcher(position: Position) -> bool` / `build_launcher(position) -> int`
- Generic alternative: `can_build(entity_type, position, extra)` / `build(entity_type, position, extra)`

### Combat & Actions
- `can_fire(target: Position) -> bool` / `fire(target: Position) -> None`: Fires turret or builder at `target` tile.
- `get_gunner_target() -> Position | None`: Valid ONLY on Gunner units. Returns closest targetable position in facing direction.
- `can_rotate(direction: Direction) -> bool` / `rotate(direction: Direction) -> None`: Gunner-only facing rotation. Costs 10 Ti.
- `can_heal(position: Position) -> bool` / `heal(position: Position) -> None`: Restores 4 HP to friendly units on orthogonally adjacent tile. Costs 1 Ti.
- `can_destroy(building_pos: Position) -> bool` / `destroy(building_pos: Position) -> None`: Demolishes allied building.

### Communication Store
- `read_store(index: int) -> int`: Returns u32 value from snapshot at turn start. Index 0..15.
- `write_store(index: int, value: int) -> None`: Buffers u32 write taking effect next turn. Index 0..15.

---

## 4. Helper Types

### `Position` (NamedTuple)
- `x: int`, `y: int`
- `add(d: Direction) -> Position`: Offset position by direction delta.
- `cardinal_direction_to(other: Position) -> Direction`: Returns `NORTH`, `EAST`, `SOUTH`, `WEST`, or `CENTRE`.
- `direction_to(other: Position) -> Direction`: Returns 8-way compass direction.
- `distance_squared(other: Position) -> int`: Squared Manhattan/Euclidean distance $dx^2 + dy^2$.

### `Direction` (Enum)
- Members: `NORTH`, `NORTHEAST`, `EAST`, `SOUTHEAST`, `SOUTH`, `SOUTHWEST`, `WEST`, `NORTHWEST`, `CENTRE`.
- `delta() -> tuple[int, int]`
- `is_cardinal() -> bool`
- `rotate_left() -> Direction`, `rotate_right() -> Direction`, `opposite() -> Direction`
