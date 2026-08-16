# fcode API reference

This is the repository's practical reference for the Florent Code League Python
package, `fcode`. It documents the bot-facing surface used by this competition,
the controller supplied by the engine, game types, local tooling, and the rules
that affect whether a call is legal.

> **Version note.** The package is not installed in this checkout. This reference
> is aligned with the current upstream `AGENT.md` supplied for the competition;
> the checked-in `AGENT.md` may be older. Pin the version installed for a match
> and validate additions with `python3 -c 'import fcode'`; the engine may add API
> members in a later release. The API below is deliberately bot-facing, not an
> invitation to rely on private `fcode.*` implementation modules.

## At a glance

Your uploaded bot is Python code whose archive contains `main.py` and a
top-level `Player` class. The engine calls `Player.run(ct)` once per round for
every living entity on your team. `ct` is a **unit-scoped `Controller`**:
methods without an explicit entity ID operate on that current unit.

```python
from fcode import Controller, Direction, EntityType, Environment, Position


class Player:
    def run(self, ct: Controller) -> None:
        kind = ct.get_entity_type()
        if kind == EntityType.CORE:
            self.run_core(ct)
        elif kind == EntityType.BUILDER_BOT:
            self.run_builder(ct)

    def run_core(self, ct: Controller) -> None:
        if ct.can_convert_ammo(10):
            ct.convert_ammo(10)
        for direction in (Direction.NORTH, Direction.EAST,
                          Direction.SOUTH, Direction.WEST):
            position = ct.get_position().add(direction)
            if ct.can_spawn(position):
                ct.spawn_builder(position)
                return

    def run_builder(self, ct: Controller) -> None:
        for position in ct.get_nearby_tiles(dist_sq=2):
            if (ct.get_tile_env(position) == Environment.ORE_TITANIUM
                    and ct.can_build_harvester(position)):
                ct.build_harvester(position)
                return
```

Important runtime rules:

- A unit gets 10 ms CPU time per turn (plus a small rolling buffer). A timeout
  interrupts that turn.
- An uncaught exception—including `GameError` from an illegal action—destroys
  that unit permanently. Use the corresponding `can_*` method before every
  state-changing call.
- Controller state is scoped by the current unit's vision. Do not assume a
  controller is a global omniscient game object.
- All living entities on a team run in the same round. The 16-slot team store is
  the supported shared state; its writes are visible next round, not immediately.

## Imports and public game types

The normal import is:

```python
from fcode import (
    Controller, Direction, Position, EntityType, Environment,
    Team, ResourceType, GameConstants, GameError,
)
```

| Name | What it represents |
| --- | --- |
| `Controller` | Per-unit query/action interface passed to `run`. |
| `Position` | Immutable grid coordinate, `Position(x, y)`. |
| `Direction` | Eight compass directions plus `CENTRE`. |
| `EntityType` | Kind of entity/building. |
| `Environment` | Terrain on a tile. |
| `Team` | `Team.A` or `Team.B`. |
| `ResourceType` | Currently `ResourceType.TITANIUM`. |
| `GameConstants` | Rules and numeric constants; prefer it to magic numbers. |
| `GameError` | Raised by an illegal engine action. Catch only when a safe fallback is intended. |

### `Position`

`Position(x, y)` is a named tuple-like immutable coordinate. The origin is the
north-west map corner: `x` increases east and `y` increases south.

| Member | Result |
| --- | --- |
| `position.x`, `position.y` | Coordinate components. |
| `position.add(direction)` | A new position offset by the direction's delta. |
| `position.distance_squared(other)` | Squared Euclidean distance; radii use this unit. |
| `position.direction_to(other)` | Nearest 45-degree direction, potentially diagonal. |
| `position.cardinal_direction_to(other)` | Best legal cardinal movement step, or `Direction.CENTRE` when equal. |

`Position.add()` can point off-map. Test the resulting position using an
appropriate `can_*` query before acting on it.

### `Direction`

Members: `NORTH`, `NORTHEAST`, `EAST`, `SOUTHEAST`, `SOUTH`, `SOUTHWEST`,
`WEST`, `NORTHWEST`, and `CENTRE`.

| Member | Result |
| --- | --- |
| `direction.delta()` | `(dx, dy)` offset. |
| `direction.rotate_left()` / `rotate_right()` | Adjacent 45-degree orientation. |
| `direction.opposite()` | Opposite orientation. |
| `direction.is_cardinal()` | True only for N/E/S/W. |

Builder bots may move only cardinally. All eight compass directions are valid
for facing oriented buildings and turrets. `CENTRE` is not a movement or facing
direction.

### Enums

| Enum | Members |
| --- | --- |
| `EntityType` | `CORE`, `BUILDER_BOT`, `CONVEYOR`, `SPLITTER`, `HARVESTER`, `BARRIER`, `GUNNER`, `SENTINEL`, `LAUNCHER` |
| `Environment` | `EMPTY`, `WALL`, `ORE_TITANIUM` |
| `Team` | `A`, `B` |
| `ResourceType` | `TITANIUM` |

### `GameConstants`

Use constants rather than copying values into strategy code. The available
families include:

- Global: `MAX_TURNS` (1000), `STACK_SIZE` (10), `STARTING_TITANIUM` (500),
  `MAX_TEAM_UNITS` (50), `PASSIVE_TITANIUM_AMOUNT` (10),
  `PASSIVE_TITANIUM_INTERVAL` (4), and `STORE_SIZE` (16).
- Per entity: `*_BASE_COST`, `*_MAX_HP`, and radius-squared constants matching
  the entity mechanics below.

Build costs scale dynamically, so `ct.get_*_cost()` is generally more useful
for a build decision than a `*_BASE_COST` constant.

## Controller: read-only state

Optional `id` parameters default to the calling entity. Entity IDs and resource
stack IDs are separate integer namespaces.

| Method | Returns | Meaning |
| --- | --- | --- |
| `get_id()` | `int` | Current entity's ID. |
| `get_team(id=None)` | `Team` | Entity's team. |
| `get_position(id=None)` | `Position` | Entity position. |
| `get_entity_type(id=None)` | `EntityType` | Entity kind. |
| `get_hp(id=None)` / `get_max_hp(id=None)` | `int` | Current / maximum HP. |
| `get_direction(id=None)` | `Direction` | Facing of an oriented entity; invalid for entities without direction. |
| `get_vision_radius_sq(id=None)` | `int` | Vision radius squared. |
| `get_action_cooldown()` | `int` | Current unit action cooldown; zero means it may act. |
| `get_move_cooldown()` | `int` | Builder bot move cooldown; zero means it may move. |
| `get_current_round()` | `int` | Zero-indexed round number. |
| `get_map_width()` / `get_map_height()` | `int` | Map dimensions. |
| `get_global_resources()` | `int` | This team's global titanium balance. |
| `get_global_ammo()` | `int` | This team's global ammo balance. |
| `get_scale_percent()` | `float` | Current build-cost multiplier as a percentage. |
| `get_unit_count()` | `int` | Living team entities, including its core. |
| `get_cpu_time_elapsed()` | `int` | Current unit's CPU consumption in microseconds this turn. |

### Controller: map, vision, and nearby state

| Method | Returns | Meaning |
| --- | --- | --- |
| `get_tile_env(pos)` | `Environment` | Terrain at `pos`. |
| `get_tile_building_id(pos)` | `int | None` | Building occupying `pos`, if any. |
| `get_tile_builder_bot_id(pos)` | `int | None` | Builder bot occupying `pos`, if any. |
| `is_tile_empty(pos)` | `bool` | No building and no wall. |
| `is_tile_passable(pos)` | `bool` | A friendly builder bot can stand there. |
| `is_in_vision(pos)` | `bool` | Whether `pos` is in this unit's vision. |
| `get_nearby_tiles(dist_sq=None)` | `list[Position]` | In-bounds tiles within the radius; default is vision range. |
| `get_nearby_entities(dist_sq=None)` | `list[int]` | Entity IDs in the radius. |
| `get_nearby_buildings(dist_sq=None)` | `list[int]` | Building IDs in the radius. |
| `get_nearby_units(dist_sq=None)` | `list[int]` | Unit IDs in the radius. |
| `get_stored_resource(id=None)` | `ResourceType | None` | Resource held by a conveyor or splitter. |
| `get_stored_resource_id(id=None)` | `int | None` | ID of that physical resource stack. |

### Controller: live cost queries

All return the current scaled integer cost:

```text
get_conveyor_cost()     get_splitter_cost()     get_harvester_cost()
get_barrier_cost()      get_gunner_cost()       get_sentinel_cost()
get_launcher_cost()     get_builder_bot_cost()
```

## Controller: movement and building

Only a builder bot can move or construct. Movement and action are mutually
exclusive for a builder bot in a round; both cooldowns decrease at end of
round. Builder actions must target an orthogonally adjacent tile—never the
builder's own tile or a diagonal.

### Movement

| Method | Result |
| --- | --- |
| `can_move(direction) -> bool` | Tests a one-tile cardinal move for the current builder bot. |
| `move(direction) -> None` | Performs it or raises `GameError`. |

### Typed construction

Each typed builder has a matching `can_` predicate and build method. Successful
builds return the new entity ID; an illegal build raises `GameError`.

| Predicate / action | Signature |
| --- | --- |
| Conveyor | `can_build_conveyor(position, direction)` / `build_conveyor(position, direction)` |
| Splitter | `can_build_splitter(position, direction)` / `build_splitter(position, direction)` |
| Harvester | `can_build_harvester(position)` / `build_harvester(position)` |
| Barrier | `can_build_barrier(position)` / `build_barrier(position)` |
| Gunner | `can_build_gunner(position, direction)` / `build_gunner(position, direction)` |
| Sentinel | `can_build_sentinel(position, direction)` / `build_sentinel(position, direction)` |
| Launcher | `can_build_launcher(position)` / `build_launcher(position)` |

### Generic construction

```python
ct.can_build(entity_type, position, extra=None) -> bool
ct.build(entity_type, position, extra=None) -> int
```

Pass a `Direction` as `extra` for `CONVEYOR`, `SPLITTER`, `GUNNER`, or
`SENTINEL`; it is unused for `HARVESTER`, `BARRIER`, and `LAUNCHER`.

## Controller: repair, destruction, resigning, and team store

| Method | Who can use it | Meaning |
| --- | --- | --- |
| `can_heal(position)` / `heal(position)` | Builder bot | Spend 1 titanium to heal every friendly entity on an orthogonally adjacent tile by 4 HP. |
| `can_destroy(building_pos)` / `destroy(building_pos)` | Builder bot | Remove an allied building on an orthogonally adjacent tile. Free and not cooldown-limited; may be used repeatedly. |
| `self_destruct()` | Current unit | Destroy self; it does no explosion damage. |
| `resign(message=None)` | Current unit | Forfeit immediately by destroying the team's core. |
| `read_store(index)` | Every unit | Read a team-private integer from index 0–15. |
| `write_store(index, value)` | Every unit | Write a team-private integer. The change appears next round. |

Store semantics matter: every unit sees a consistent start-of-round snapshot.
Do not use a write for same-round coordination, and reserve indices centrally in
your bot design.

## Controller: combat and launchers

`can_fire(target)` / `fire(target)` is polymorphic:

- For a builder bot, it is its adjacent-tile attack (2 titanium for 2 damage).
- For gunners and sentinels, it fires the current turret weapon and spends team
  ammo.
- For a launcher, it is not the launch operation; use `can_launch` / `launch`.

| Method | Meaning |
| --- | --- |
| `can_fire(target) -> bool` / `fire(target) -> None` | Test / make a normal attack. |
| `can_fire_from(position, direction, turret_type, target) -> bool` | Test a hypothetical turret position/orientation. It ignores ammo and cooldown. |
| `get_attackable_tiles() -> list[Position]` | Current turret's raw pattern; ignores ammo, cooldown, and occupancy. |
| `get_attackable_tiles_from(position, direction, turret_type) -> list[Position]` | Raw pattern for a hypothetical turret. |
| `get_gunner_target() -> Position | None` | Nearest targetable tile in a gunner's facing line. |
| `can_rotate(direction) -> bool` / `rotate(direction) -> None` | Gunner only. Rotate for 10 titanium and action cooldown 1. |
| `can_launch(bot_pos, target) -> bool` / `launch(bot_pos, target) -> None` | Launcher only. Pick up an adjacent builder bot from either team and throw it onto a legal tile. |

Target forms are positions. Test the appropriate `can_*` method first; attack
patterns alone do not prove an action has ammo, a clear line, or a ready
cooldown.

## Controller: core actions

| Method | Meaning |
| --- | --- |
| `can_spawn(position) -> bool` / `spawn_builder(position) -> int` | Core-only test/spawn of a builder bot at a legal tile adjacent to its 2×2 footprint. Spawn consumes the core's action. |
| `can_convert_ammo(amount) -> bool` / `convert_ammo(amount) -> None` | Core-only conversion of team titanium to ammo at 1:1. At most once per team per round; it is usable immediately and does not consume the core's spawn action. |

## Controller: replay diagnostics

```python
ct.draw_indicator_line(pos_a, pos_b, r, g, b)
ct.draw_indicator_dot(pos, r, g, b)
```

These draw into the replay visualiser. Ordinary `print()` output is also
captured in the replay. Keep both sparse: bot CPU time is limited.

## Entity mechanics and constraints

| Entity | HP | Base cost | Scaling per live entity | Practical API / rule notes |
| --- | ---: | ---: | ---: | --- |
| Core | 500 | — | — | 2×2; vision r² 36; action r² 8; can spawn at most one builder per round. |
| Builder bot | 40 | 30 Ti | 20% | Only mobile entity; vision r² 20; builds, attacks, heals, destroys on cardinally adjacent tiles. |
| Conveyor | 20 | 3 Ti | 1% | Cardinal orientation; accepts from three sides and outputs forward. |
| Splitter | 20 | 6 Ti | 1% | Accepts from its back; cycles output among the other directions, least-recently-used first. |
| Harvester | 30 | 20 Ti | 5% | Must be built on titanium ore; first stack immediately, then a stack every 4 rounds. |
| Barrier | 30 | 3 Ti | 1% | Cheap blocking HP. |
| Gunner | 25 | 20 Ti | 20% | r² 13 vision/attack; straight line, 7 damage, reload 1, 4 ammo per shot. Can rotate. |
| Sentinel | 40 | 30 Ti | 20% | r² 32 vision/attack; obstacle-ignoring one-tile-wide line, 18 damage, reload 2, 10 ammo per shot. |
| Launcher | 30 | 20 Ti | 10% | r² 26; orientation-independent launcher that can throw an adjacent builder bot from either team; no ammo. |

Only builder bots are movable. All other rows are buildings. A team may have no
more than 50 living entities, including the core.

## Economy, cooldowns, and resolution order

- Each team begins with 500 global titanium and receives 10 passive titanium
  every four rounds.
- Titanium harvested into a conveyor network is transported as physical stacks
  of 10. It is distinct from the global titanium balance used to pay construction
  and healing costs.
- Gunners and sentinels consume one shared team ammo pool. Cores create ammo by
  converting global titanium; turrets never need a conveyor supply.
- Building a category increases its current cost scale: conveyors/splitters/
  barriers by 1%, harvesters by 5%, launchers by 10%, and builder bots/gunners/
  sentinels by 20%. Destroying a live entity removes its contribution.
- Action and movement cooldowns tick down at end of round. A builder's move and
  action cannot both be used in the same round.
- Resource distribution runs at end of round after all units act. It is possible
  to push resources into an opposing conveyor network or core; turrets do not
  store or receive resources.

The match ends when a core is destroyed, or after round 1000. The time-limit
tiebreakers are titanium delivered to core, surviving harvesters, titanium
stored, then a coin flip.

## Local `fcode` tooling used by this repository

These are CLI and local-runner interfaces, separate from the safe bot-facing
`Controller` API.

| Command / import | Purpose |
| --- | --- |
| `pip install fcode` | Installs the game package. |
| `fcode login` | Authenticates the local client. |
| `fcode starter` | Creates the starter scaffold when no bot exists. |
| `fcode run <bot-a> <bot-b>` | Runs a local match using `fcode.toml`. Example: `fcode run starter starter`. |
| `fcode watch <replay.replay26>` | Opens/watches a replay in the installed visualiser. |
| `from fcode.config import find_config` | Used by local scripts to locate `fcode.toml` and project root. Treat as tooling, not upload-bot API. |
| `from fcode.fcode_engine import run_game` | Local programmatic runner used by gauntlet scripts. Treat as version-sensitive internal tooling. |
| `from fcode.commands.watch import _find_dist` | Private visualiser helper used only by `scripts/watch_sync.py`; do not depend on it in a bot. |

The project config in `fcode.toml` sets `bots_dir`, `maps_dir`, default replay
path, and seed. The existing runners demonstrate the local `run_game` argument
order: bot A path, bot B path, fcode package directory, map path, replay path,
seed, then a final engine option. Because it is not public bot API, follow the
runner in this repository rather than embedding that call in submitted code.

## Safe controller pattern

```python
def try_build_gunner(ct: Controller, position: Position, direction: Direction) -> bool:
    if ct.get_action_cooldown() != 0:
        return False
    if ct.get_global_resources() < ct.get_gunner_cost():
        return False
    if not ct.can_build_gunner(position, direction):
        return False
    ct.build_gunner(position, direction)
    return True
```

This pattern avoids fatal exceptions and avoids assuming that resources,
cooldowns, range, occupancy, or unit type make an action legal. Apply the same
pattern to movement, spawn, fire, healing, conversion, rotation, and launch.

## Repository pointers

- `AGENT.md` is the short, immediately actionable game/API guide this reference
  consolidates.
- `bots/starter/main.py` is a minimal runnable player.
- `scripts/bot_gauntlet.py` and `scripts/extreme_gauntlet.py` show local-engine
  use and test bots across this repository's maps.
- `florent_lib.py` has reusable pure-Python helpers; copy needed helpers into an
  uploaded archive with the bot rather than expecting the project root at match
  time.
