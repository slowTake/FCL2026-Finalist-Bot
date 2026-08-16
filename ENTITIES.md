# `fcode` RTS Engine — Entity & Action Reference Manual (`ENTITIES.md`)

This document serves as the definitive reference guide for all entities, action capabilities, spatial constraints, resource costs, and grid invariants in the `fcode` RTS engine.

---

## 1. Entity Breakdown

The table below details all 9 entity types in the `fcode` engine. Each entity has a base health pool (HP), physical footprint size on the tile grid, base Titanium cost, cost-scaling multiplier per built instance, vision radius squared ($r^2$), and action/attack radius squared.

| Entity Type | Entity Enum | Base HP | Footprint | Base Cost | Cost Scaling | Vision Radius ($r^2$) | Action / Attack Radius | Category |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **Core** | `EntityType.CORE` | 500 HP | 2×2 | — | 0.0% | $r^2 = 36$ (6 tiles) | Action: $r^2 = 8$ (Spawn) | Building / Unit |
| **Builder Bot** | `EntityType.BUILDER_BOT` | 40 HP | 1×1 | 30 Ti | +20.0% | $r^2 = 20$ (~4.47 tiles) | Action: Orthogonal ($dx+dy=1$) | Mobile Unit |
| **Harvester** | `EntityType.HARVESTER` | 30 HP | 1×1 | 20 Ti | +5.0% | None | Ore Tile Only (Outputs every 4 rounds) | Economic Building |
| **Conveyor** | `EntityType.CONVEYOR` | 20 HP | 1×1 | 3 Ti | +1.0% | None | Cardinal Facing (Transports 10 Ti stack) | Transport Building |
| **Splitter** | `EntityType.SPLITTER` | 20 HP | 1×1 | 6 Ti | +1.0% | None | Cardinal Facing (Splits stack 3 ways) | Transport Building |
| **Barrier** | `EntityType.BARRIER` | 30 HP | 1×1 | 3 Ti | +1.0% | None | Passive Structural Obstacle | Defense Building |
| **Gunner** | `EntityType.GUNNER` | 40 HP | 1×1 | 10 Ti | +10.0% | $r^2 = 13$ (~3.6 tiles) | Attack: $r^2 = 13$ (Facing column) | Defense Turret |
| **Sentinel** | `EntityType.SENTINEL` | 30 HP | 1×1 | 30 Ti | +20.0% | $r^2 = 32$ (~5.65 tiles) | Attack: $r^2 = 32$ (Facing beam) | Defense Turret |
| **Launcher** | `EntityType.LAUNCHER` | 30 HP | 1×1 | 20 Ti | +10.0% | $r^2 = 26$ (~5.1 tiles) | Action: $r^2 = 26$ (Throw Bot) | Utility Turret |

> [!NOTE]
> **Cost Scaling Formula**: The actual Titanium cost to build an entity instance is calculated dynamically as:
> $$\text{Actual Cost} = \lfloor \text{Scale Multiplier} \times \text{Base Cost} \rfloor$$
> Where `Scale Multiplier` starts at `1.0` (100%) and increases by the entity's **Cost Scaling** percentage for every active building of that category currently built by your team. Destroying or losing an entity refunds its scaling contribution.

---

## 2. Action & Capability Matrix

The following matrix documents every API command available on the unit `Controller` (`ct`), categorized by acting entity type.

### 2.1 Core Commands (`EntityType.CORE`)

| API Method | Description & Effect | Spatial Constraints | Titanium Cost | Ammo Cost | Cooldown Impact |
| :--- | :--- | :--- | :---: | :---: | :--- |
| `ct.can_spawn(pos)`<br>`ct.spawn_builder(pos)` | Spawns a new **Builder Bot** at `pos`. Returns the new unit ID. | Target `pos` must be empty, in bounds, and adjacent to the Core's 2×2 footprint ($r^2 \le 8$). | Scaled Builder Cost (Base: 30 Ti) | 0 Ammo | Sets Core `action_cooldown` to `1`. |
| `ct.can_convert_ammo(amount)`<br>`ct.convert_ammo(amount)` | Converts `amount` global Titanium into global Team Ammunition 1:1. | Global action; no spatial constraint. | `amount` Ti | Adds `amount` Ammo | **Zero Cooldown Impact!** Does not trigger `action_cooldown`. Maximum once per team per turn. |

---

### 2.2 Builder Bot Commands (`EntityType.BUILDER_BOT`)

| API Method | Description & Effect | Spatial Constraints | Titanium Cost | Ammo Cost | Cooldown Impact |
| :--- | :--- | :--- | :---: | :---: | :--- |
| `ct.can_move(direction)`<br>`ct.move(direction)` | Steps the Builder Bot 1 tile in `direction`. | **Cardinal Only** (`NORTH`, `SOUTH`, `EAST`, `WEST`). Target tile must be passable. | 0 Ti | 0 Ammo | Sets `move_cooldown` to `1`. Mutually exclusive with acting. |
| `ct.can_build_*(pos, ...)`<br>`ct.build_*(pos, ...)` | Builds a structure (Conveyor, Splitter, Harvester, Barrier, Gunner, Sentinel, Launcher) at `pos`. | **Orthogonal Adjacency Required** ($\|dx\| + \|dy\| = 1$). No diagonals. Cannot build on own tile. Harvester requires Titanium Ore tile. | Scaled Structure Cost | 0 Ammo | Sets `action_cooldown` to `1`. Mutually exclusive with moving. |
| `ct.can_fire(pos)`<br>`ct.fire(pos)` | Builder Attack: Deals **2 DMG** to an enemy building at `pos`. | **Orthogonal Adjacency Required** ($\|dx\| + \|dy\| = 1$). Target must contain an enemy building. | 2 Ti | 0 Ammo | Sets `action_cooldown` to `1`. |
| `ct.can_heal(pos)`<br>`ct.heal(pos)` | Builder Heal: Restores **+4 HP** to all friendly entities on `pos`. | **Orthogonal Adjacency Required** ($\|dx\| + \|dy\| = 1$). Target must contain allied entities. | 1 Ti | 0 Ammo | Sets `action_cooldown` to `1`. |
| `ct.can_destroy(pos)`<br>`ct.destroy(pos)` | Dismantles an allied structure at `pos`. | **Orthogonal Adjacency Required** ($\|dx\| + \|dy\| = 1$). Target must be an allied building. | **0 Ti** | 0 Ammo | **Zero Cooldown!** Unlimited executions per turn. |
| `ct.self_destruct()` | Permanently destroys this Builder Bot (no explosion damage). | Self unit only. | 0 Ti | 0 Ammo | Kills unit immediately. |
| `ct.resign(message=None)` | Immediately forfeits the match by destroying the team's Core. | Global action. | 0 Ti | 0 Ammo | Ends game. |

---

### 2.3 Turret & Utility Commands

#### Gunner (`EntityType.GUNNER`)
- **Primary Attack** (`ct.can_fire(target)` / `ct.fire(target)`):
  - **Spatial Constraints**: Target must lie within vision/attack radius $r^2 \le 13$ in a **straight cardinal line along the Gunner's current facing direction**. Non-piercing (projectile stops at the first entity/obstacle hit).
  - **Damage**: **10 HP** to hit target.
  - **Resource Cost**: **2 Global Ammunition** per shot (withdrawn from team pool).
  - **Cooldown**: 1 turn reload (sets `action_cooldown` to `1`).
- **Rotate** (`ct.can_rotate(direction)` / `ct.rotate(direction)`):
  - **Spatial Constraints**: Changes facing to any of the 8 compass directions.
  - **Resource Cost**: **10 Titanium**.
  - **Cooldown**: Sets `action_cooldown` to `1`.

#### Sentinel (`EntityType.SENTINEL`)
- **Primary Attack** (`ct.can_fire(target)` / `ct.fire(target)`):
  - **Spatial Constraints**: Target must lie within vision/attack radius $r^2 \le 32$ in a **straight cardinal line along the Sentinel's facing direction**. **Piercing Beam**: Penetrates and damages all entities/buildings along the 1-tile-wide line.
  - **Damage**: **18 HP** to every entity in the beam line.
  - **Resource Cost**: **10 Global Ammunition** per shot.
  - **Cooldown**: 3 turns reload (sets `action_cooldown` to `3`).
  - **Rotation**: Fixed facing upon construction; **cannot rotate**.

#### Launcher (`EntityType.LAUNCHER`)
- **Launch Command** (`ct.can_launch(bot_pos, target)` / `ct.launch(bot_pos, target)`):
  - **Spatial Constraints**: `bot_pos` must contain an allied Builder Bot **orthogonally adjacent** to the Launcher ($\|dx\| + \|dy\| = 1$). `target` must be an empty, passable tile within $r^2 \le 26$ of the Launcher.
  - **Effect**: Picks up the Builder Bot at `bot_pos` and throws it directly to `target`.
  - **Resource Cost**: **0 Titanium**, **0 Ammo**.
  - **Cooldown**: Sets Launcher `action_cooldown` to `1`.

---

## 3. Movement & Grid Invariants

To avoid illegal action calls and uncaught engine exceptions, bots must adhere strictly to the following grid and engine invariants:

### 3.1 Movement Restrictions
- **Cardinal-Only Steps**: Builder Bots may **only move in the 4 cardinal directions**: `Direction.NORTH`, `Direction.SOUTH`, `Direction.EAST`, and `Direction.WEST`.
- **Diagonal Movement Prohibition**: Calling `ct.move(Direction.NORTH_EAST)` or any diagonal direction raises a `GameError` exception and will result in unit crash/removal by the engine if uncaught.
- **Passability**: A tile is passable if it contains no wall, no enemy building, and no other Builder Bot. Conveyors, splitters, and harvesters are walkable by friendly Builder Bots.

### 3.2 Spatial Adjacency Invariants
All Builder Bot interactions (building, attacking, healing, destroying, launching) require **Orthogonal Adjacency**:
$$\text{Manhattan Distance: } |x_{\text{target}} - x_{\text{unit}}| + |y_{\text{target}} - y_{\text{unit}}| = 1$$

- ❌ **No Diagonal Interactions**: You cannot build, heal, attack, or destroy on diagonal tiles ($\Delta x = 1, \Delta y = 1$).
- ❌ **No Self-Tile Interactions**: You cannot build, heal, attack, or destroy on the tile the Builder Bot is currently standing on ($\Delta x = 0, \Delta y = 0$).

### 3.3 Cooldown & Turn Execution Rules
- **Mutual Exclusivity**: A Builder Bot cannot both move and perform an action (build/heal/attack) in the same turn. Moving sets `move_cooldown` to `1`, while acting sets `action_cooldown` to `1`.
- **Core Conversion Exception**: `ct.convert_ammo()` does **not** consume the Core's action cooldown. The Core can convert Titanium to Ammo and spawn a Builder Bot on the exact same round.
- **CPU Time Limit**: Each unit receives **10 ms** of CPU execution time per turn. Exceeding the budget forfeits the remaining turn. Unhandled Python exceptions will permanently destroy the unit (`CrashDetector`).

---

## 4. Quick API Summary Reference

```python
from fcode import Controller, Direction, EntityType, Environment, Position

def tick(ct: Controller):
    # Queries
    my_hp = ct.get_hp()
    my_pos = ct.get_position()
    ti_bank = ct.get_global_resources()
    ammo_bank = ct.get_global_ammo()
    
    # Movement (Builder Bot)
    if ct.get_move_cooldown() == 0:
        step = Direction.NORTH
        if ct.can_move(step):
            ct.move(step)
            
    # Building (Builder Bot - Orthogonal Only)
    if ct.get_action_cooldown() == 0:
        target_tile = my_pos.add(Direction.EAST)
        if ct.can_build_conveyor(target_tile, Direction.EAST):
            ct.build_conveyor(target_tile, Direction.EAST)
            
    # Core Spawning & Ammo Conversion
    if ct.get_entity_type() == EntityType.CORE:
        if ct.can_convert_ammo(100):
            ct.convert_ammo(100)  # 0 cooldown cost
        spawn_tile = Position(my_pos.x + 2, my_pos.y)
        if ct.can_spawn(spawn_tile):
            ct.spawn_builder(spawn_tile)
```
