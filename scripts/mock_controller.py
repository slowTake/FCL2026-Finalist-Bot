from __future__ import annotations
import math
from fcode import (Position, Direction, EntityType, Environment, Team, 
                   ResourceType, GameConstants, GameError)
from replay_state import WorldState, EntityRecord

VISION_RADIUS_SQ = {
    EntityType.CORE: 36,
    EntityType.BUILDER_BOT: 20,
    EntityType.GUNNER: 13,
    EntityType.SENTINEL: 32,
    EntityType.LAUNCHER: 26,
    EntityType.HARVESTER: 0,
    EntityType.CONVEYOR: 0,
    EntityType.SPLITTER: 0,
    EntityType.BARRIER: 0,
}

ACTION_RADIUS_SQ = {
    EntityType.CORE: 8,
    EntityType.BUILDER_BOT: 2,
}

ATTACK_RADIUS_SQ = {
    EntityType.GUNNER: 13,
    EntityType.SENTINEL: 32,
    EntityType.LAUNCHER: 26,
}

BASE_COSTS = {
    'CONVEYOR': 3, 'SPLITTER': 6, 'HARVESTER': 20, 'BARRIER': 3,
    'GUNNER': 10, 'SENTINEL': 30, 'LAUNCHER': 20, 'BUILDER_BOT': 30,
}

SCALE_PER_BUILD = {
    'CONVEYOR': 1, 'SPLITTER': 1, 'BARRIER': 1,
    'HARVESTER': 5, 'GUNNER': 10, 'LAUNCHER': 10,
    'SENTINEL': 20, 'BUILDER_BOT': 20,
}


class MockController:
    """Mock Controller for testing."""

    def __init__(self, state: WorldState, unit_id: int, team: Team):
        self.state = state
        self.unit_id = unit_id
        self.team = team
        self._action_log: list[str] = []
        self._store: dict[int, int] = {i: 0 for i in range(16)}
        self._visible_tiles = self._compute_visible_tiles()

    def _compute_visible_tiles(self) -> set[tuple[int, int]]:
        visible = set()
        for entity in self.state.entities.values():
            if entity.team == self.team:
                radius_sq = VISION_RADIUS_SQ.get(entity.entity_type, 0)
                if radius_sq > 0:
                    origins = [(entity.x, entity.y)]
                    if entity.entity_type == EntityType.CORE:
                        origins = [
                            (entity.x, entity.y),
                            (entity.x + 1, entity.y),
                            (entity.x, entity.y + 1),
                            (entity.x + 1, entity.y + 1)
                        ]
                    
                    for ox, oy in origins:
                        r = int(math.sqrt(radius_sq)) + 1
                        for dx in range(-r, r + 1):
                            for dy in range(-r, r + 1):
                                nx, ny = ox + dx, oy + dy
                                if self._in_bounds(nx, ny) and self._dist_sq(ox, oy, nx, ny) <= radius_sq:
                                    visible.add((nx, ny))
        return visible

    def _resolve_id(self, id: int | None) -> int:
        return self.unit_id if id is None else id

    def _get_entity(self, id: int) -> EntityRecord:
        if id not in self.state.entities:
            raise GameError(f"Entity {id} not found in state.")
        return self.state.entities[id]

    def _dist_sq(self, x1: int, y1: int, x2: int, y2: int) -> int:
        return (x1 - x2) ** 2 + (y1 - y2) ** 2

    def _in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self.state.map_width and 0 <= y < self.state.map_height

    def _direction_delta(self, d: Direction) -> tuple[int, int]:
        if d == Direction.NORTH: return (0, -1)
        if d == Direction.EAST: return (1, 0)
        if d == Direction.SOUTH: return (0, 1)
        if d == Direction.WEST: return (-1, 0)
        return (0, 0)

    # Entity Info Queries
    def get_id(self) -> int:
        return self.unit_id

    def get_team(self, id: int | None = None) -> Team:
        return self._get_entity(self._resolve_id(id)).team

    def get_position(self, id: int | None = None) -> Position:
        ent = self._get_entity(self._resolve_id(id))
        return Position(ent.x, ent.y)

    def get_hp(self, id: int | None = None) -> int:
        return self._get_entity(self._resolve_id(id)).hp

    def get_max_hp(self, id: int | None = None) -> int:
        return self._get_entity(self._resolve_id(id)).max_hp

    def get_entity_type(self, id: int | None = None) -> EntityType:
        return self._get_entity(self._resolve_id(id)).entity_type

    def get_direction(self, id: int | None = None) -> Direction:
        ent = self._get_entity(self._resolve_id(id))
        if ent.direction is None:
            raise GameError(f"Entity {self._resolve_id(id)} has no direction.")
        return ent.direction

    def get_stored_resource(self, id: int | None = None) -> ResourceType | None:
        return self._get_entity(self._resolve_id(id)).stored_resource

    def get_stored_resource_id(self, id: int | None = None) -> int | None:
        return None

    def get_action_cooldown(self) -> int:
        return self._get_entity(self.unit_id).action_cooldown

    def get_move_cooldown(self) -> int:
        return self._get_entity(self.unit_id).move_cooldown

    def can_act(self) -> bool:
        return self.get_action_cooldown() == 0

    def get_vision_radius_sq(self, id: int | None = None) -> int:
        ent = self._get_entity(self._resolve_id(id))
        return VISION_RADIUS_SQ.get(ent.entity_type, 0)

    def get_ammo_amount(self) -> int:
        return self._get_entity(self.unit_id).ammo_amount

    def get_ammo_type(self) -> ResourceType | None:
        return self._get_entity(self.unit_id).ammo_type

    # Map & Vision Queries
    def get_map_width(self) -> int:
        return self.state.map_width

    def get_map_height(self) -> int:
        return self.state.map_height

    def get_tile_env(self, pos: Position) -> Environment:
        if not self._in_bounds(pos.x, pos.y):
            raise GameError("Position out of bounds.")
        return self.state.tile_grid[pos.y][pos.x]

    def get_tile_building_id(self, pos: Position) -> int | None:
        return self.state.tile_buildings.get((pos.x, pos.y))

    def get_tile_builder_bot_id(self, pos: Position) -> int | None:
        return self.state.tile_bots.get((pos.x, pos.y))

    def is_tile_empty(self, pos: Position) -> bool:
        return self.get_tile_building_id(pos) is None and self.get_tile_env(pos) != Environment.WALL

    def is_tile_passable(self, pos: Position) -> bool:
        if not self._in_bounds(pos.x, pos.y):
            return False
        if self.get_tile_env(pos) == Environment.WALL:
            return False
        b_id = self.get_tile_building_id(pos)
        if b_id is None:
            return True
        b_type = self._get_entity(b_id).entity_type
        return b_type in (EntityType.CONVEYOR, EntityType.SPLITTER)

    def is_in_vision(self, pos: Position) -> bool:
        return (pos.x, pos.y) in self._visible_tiles

    def get_nearby_tiles(self, dist_sq: int | None = None) -> list[Position]:
        if dist_sq is None:
            dist_sq = self.get_vision_radius_sq()
        pos = self.get_position()
        r = int(math.sqrt(dist_sq)) + 1
        tiles = []
        for dx in range(-r, r + 1):
            for dy in range(-r, r + 1):
                nx, ny = pos.x + dx, pos.y + dy
                if self._in_bounds(nx, ny) and self._dist_sq(pos.x, pos.y, nx, ny) <= dist_sq:
                    p = Position(nx, ny)
                    if self.is_in_vision(p):
                        tiles.append(p)
        return tiles

    def get_nearby_entities(self, dist_sq: int | None = None) -> list[int]:
        if dist_sq is None:
            dist_sq = self.get_vision_radius_sq()
        pos = self.get_position()
        entities = []
        for eid, ent in self.state.entities.items():
            if self.is_in_vision(Position(ent.x, ent.y)) and self._dist_sq(pos.x, pos.y, ent.x, ent.y) <= dist_sq:
                entities.append(eid)
        return entities

    def get_nearby_buildings(self, dist_sq: int | None = None) -> list[int]:
        building_types = {EntityType.CORE, EntityType.CONVEYOR, EntityType.SPLITTER, EntityType.HARVESTER,
                          EntityType.BARRIER, EntityType.GUNNER, EntityType.SENTINEL, EntityType.LAUNCHER}
        return [eid for eid in self.get_nearby_entities(dist_sq) if self._get_entity(eid).entity_type in building_types]

    def get_nearby_units(self, dist_sq: int | None = None) -> list[int]:
        unit_types = {EntityType.CORE, EntityType.BUILDER_BOT, EntityType.GUNNER, EntityType.SENTINEL, EntityType.LAUNCHER}
        return [eid for eid in self.get_nearby_entities(dist_sq) if self._get_entity(eid).entity_type in unit_types]

    # Match & Economy Queries
    def get_current_round(self) -> int:
        return self.state.round_number

    def get_global_resources(self) -> int:
        return self.state.team_titanium[self.team]

    def get_global_ammo(self) -> int:
        return self.state.team_ammo[self.team]

    def get_scale_percent(self) -> float:
        return 100.0

    def get_cpu_time_elapsed(self) -> int:
        return 0

    def get_unit_count(self) -> int:
        unit_types = {EntityType.CORE, EntityType.BUILDER_BOT, EntityType.GUNNER, EntityType.SENTINEL, EntityType.LAUNCHER}
        count = 0
        for ent in self.state.entities.values():
            if ent.team == self.team and ent.entity_type in unit_types:
                count += 1
        return count

    # Cost Getters
    def _get_cost(self, name: str) -> int:
        base = BASE_COSTS[name]
        scale = SCALE_PER_BUILD[name]
        et_map = {
            'CONVEYOR': EntityType.CONVEYOR,
            'SPLITTER': EntityType.SPLITTER,
            'BARRIER': EntityType.BARRIER,
            'HARVESTER': EntityType.HARVESTER,
            'GUNNER': EntityType.GUNNER,
            'LAUNCHER': EntityType.LAUNCHER,
            'SENTINEL': EntityType.SENTINEL,
            'BUILDER_BOT': EntityType.BUILDER_BOT
        }
        et_key = str(et_map[name])
        count = self.state.entity_build_counts[self.team].get(et_key, 0)
        return math.floor((1.0 + count * scale / 100.0) * base)

    def get_conveyor_cost(self) -> int: return self._get_cost('CONVEYOR')
    def get_splitter_cost(self) -> int: return self._get_cost('SPLITTER')
    def get_harvester_cost(self) -> int: return self._get_cost('HARVESTER')
    def get_barrier_cost(self) -> int: return self._get_cost('BARRIER')
    def get_gunner_cost(self) -> int: return self._get_cost('GUNNER')
    def get_sentinel_cost(self) -> int: return self._get_cost('SENTINEL')
    def get_launcher_cost(self) -> int: return self._get_cost('LAUNCHER')
    def get_builder_bot_cost(self) -> int: return self._get_cost('BUILDER_BOT')

    # Movement
    def can_move(self, direction: Direction) -> bool:
        ent = self._get_entity(self.unit_id)
        if ent.entity_type != EntityType.BUILDER_BOT: return False
        if ent.move_cooldown > 0 or ent.action_cooldown > 0: return False
        dx, dy = self._direction_delta(direction)
        pos = Position(ent.x + dx, ent.y + dy)
        return self.is_tile_passable(pos)

    def move(self, direction: Direction) -> None:
        self._action_log.append(f"move {direction.name}")

    # Building
    def _check_can_build(self, cost: int, pos: Position) -> bool:
        ent = self._get_entity(self.unit_id)
        if ent.entity_type != EntityType.BUILDER_BOT: return False
        if ent.action_cooldown > 0: return False
        if self.get_global_resources() < cost: return False
        if self._dist_sq(ent.x, ent.y, pos.x, pos.y) > ACTION_RADIUS_SQ[EntityType.BUILDER_BOT]: return False
        return True

    def can_build_conveyor(self, pos: Position, direction: Direction) -> bool:
        return self._check_can_build(self.get_conveyor_cost(), pos) and self.is_tile_empty(pos)
    def build_conveyor(self, pos: Position, direction: Direction) -> int:
        self._action_log.append(f"build_conveyor {pos.x} {pos.y} {direction.name}")
        return -1

    def can_build_splitter(self, pos: Position, direction: Direction) -> bool:
        return self._check_can_build(self.get_splitter_cost(), pos) and self.is_tile_empty(pos)
    def build_splitter(self, pos: Position, direction: Direction) -> int:
        self._action_log.append(f"build_splitter {pos.x} {pos.y} {direction.name}")
        return -1

    def can_build_harvester(self, pos: Position) -> bool:
        if not self._check_can_build(self.get_harvester_cost(), pos): return False
        return self.is_tile_empty(pos) and self.get_tile_env(pos) == Environment.ORE_TITANIUM
    def build_harvester(self, pos: Position) -> int:
        self._action_log.append(f"build_harvester {pos.x} {pos.y}")
        return -1

    def can_build_barrier(self, pos: Position) -> bool:
        return self._check_can_build(self.get_barrier_cost(), pos) and self.is_tile_empty(pos)
    def build_barrier(self, pos: Position) -> int:
        self._action_log.append(f"build_barrier {pos.x} {pos.y}")
        return -1

    def can_build_gunner(self, pos: Position, direction: Direction) -> bool:
        return self._check_can_build(self.get_gunner_cost(), pos) and self.is_tile_empty(pos)
    def build_gunner(self, pos: Position, direction: Direction) -> int:
        self._action_log.append(f"build_gunner {pos.x} {pos.y} {direction.name}")
        return -1

    def can_build_sentinel(self, pos: Position, direction: Direction) -> bool:
        return self._check_can_build(self.get_sentinel_cost(), pos) and self.is_tile_empty(pos)
    def build_sentinel(self, pos: Position, direction: Direction) -> int:
        self._action_log.append(f"build_sentinel {pos.x} {pos.y} {direction.name}")
        return -1

    def can_build_launcher(self, pos: Position, direction: Direction) -> bool:
        return self._check_can_build(self.get_launcher_cost(), pos) and self.is_tile_empty(pos)
    def build_launcher(self, pos: Position, direction: Direction) -> int:
        self._action_log.append(f"build_launcher {pos.x} {pos.y} {direction.name}")
        return -1

    def can_build(self, entity_type: EntityType, pos: Position, extra: Direction | None = None) -> bool:
        if entity_type == EntityType.CONVEYOR and extra: return self.can_build_conveyor(pos, extra)
        if entity_type == EntityType.SPLITTER and extra: return self.can_build_splitter(pos, extra)
        if entity_type == EntityType.HARVESTER: return self.can_build_harvester(pos)
        if entity_type == EntityType.BARRIER: return self.can_build_barrier(pos)
        if entity_type == EntityType.GUNNER and extra: return self.can_build_gunner(pos, extra)
        if entity_type == EntityType.SENTINEL and extra: return self.can_build_sentinel(pos, extra)
        if entity_type == EntityType.LAUNCHER and extra: return self.can_build_launcher(pos, extra)
        return False

    def build(self, entity_type: EntityType, pos: Position, extra: Direction | None = None) -> int:
        self._action_log.append(f"build {entity_type.name} {pos.x} {pos.y} {extra.name if extra else 'None'}")
        return -1

    # Core Spawning
    def can_spawn(self, pos: Position) -> bool:
        ent = self._get_entity(self.unit_id)
        if ent.entity_type != EntityType.CORE: return False
        if ent.action_cooldown > 0: return False
        if self.get_global_resources() < self.get_builder_bot_cost(): return False
        if self.get_unit_count() >= 50: return False
        
        # Check if pos is adjacent to core
        valid = False
        core_origins = [(ent.x, ent.y), (ent.x + 1, ent.y), (ent.x, ent.y + 1), (ent.x + 1, ent.y + 1)]
        for cx, cy in core_origins:
            if self._dist_sq(cx, cy, pos.x, pos.y) <= ACTION_RADIUS_SQ[EntityType.CORE]:
                valid = True
                break
        if not valid: return False
        
        return self.is_tile_passable(pos) and self.get_tile_builder_bot_id(pos) is None

    def spawn_builder(self, pos: Position) -> int:
        self._action_log.append(f"spawn_builder {pos.x} {pos.y}")
        return -1

    def can_convert_ammo(self, amount: int) -> bool:
        ent = self._get_entity(self.unit_id)
        if ent.entity_type != EntityType.CORE: return False
        return self.get_global_resources() >= amount

    def convert_ammo(self, amount: int) -> None:
        self._action_log.append(f"convert_ammo {amount}")

    # Healing & Destruction
    def can_heal(self, pos: Position) -> bool:
        ent = self._get_entity(self.unit_id)
        if ent.entity_type != EntityType.BUILDER_BOT: return False
        if ent.action_cooldown > 0: return False
        if self.get_global_resources() < 1: return False
        if self._dist_sq(ent.x, ent.y, pos.x, pos.y) > ACTION_RADIUS_SQ[EntityType.BUILDER_BOT]: return False
        
        bot_id = self.get_tile_builder_bot_id(pos)
        b_id = self.get_tile_building_id(pos)
        
        if bot_id and self.get_team(bot_id) == self.team: return True
        if b_id and self.get_team(b_id) == self.team: return True
        return False

    def heal(self, pos: Position) -> None:
        self._action_log.append(f"heal {pos.x} {pos.y}")

    def can_destroy(self, pos: Position) -> bool:
        ent = self._get_entity(self.unit_id)
        if ent.entity_type != EntityType.BUILDER_BOT: return False
        if self._dist_sq(ent.x, ent.y, pos.x, pos.y) > ACTION_RADIUS_SQ[EntityType.BUILDER_BOT]: return False
        b_id = self.get_tile_building_id(pos)
        if b_id and self.get_team(b_id) == self.team: return True
        return False

    def destroy(self, pos: Position) -> None:
        self._action_log.append(f"destroy {pos.x} {pos.y}")

    def self_destruct(self) -> None:
        self._action_log.append("self_destruct")

    def resign(self, msg: str | None = None) -> None:
        self._action_log.append(f"resign {msg}")

    # Communication Store
    def read_store(self, index: int) -> int:
        return self._store[index]

    def write_store(self, index: int, value: int) -> None:
        self._store[index] = value
        self._action_log.append(f"write_store {index} {value}")

    # Turret Actions
    def can_fire(self, target: Position) -> bool:
        ent = self._get_entity(self.unit_id)
        if ent.entity_type not in (EntityType.GUNNER, EntityType.SENTINEL, EntityType.LAUNCHER): return False
        if ent.action_cooldown > 0: return False
        return self._dist_sq(ent.x, ent.y, target.x, target.y) <= ATTACK_RADIUS_SQ[ent.entity_type]

    def can_fire_from(self, pos: Position, direction: Direction, turret_type: EntityType, target: Position) -> bool:
        return False

    def fire(self, target: Position) -> None:
        self._action_log.append(f"fire {target.x} {target.y}")

    def can_rotate(self, direction: Direction) -> bool:
        ent = self._get_entity(self.unit_id)
        if ent.entity_type not in (EntityType.GUNNER, EntityType.SENTINEL, EntityType.LAUNCHER): return False
        if ent.action_cooldown > 0: return False
        if self.get_global_resources() < 10: return False
        return True

    def rotate(self, direction: Direction) -> None:
        self._action_log.append(f"rotate {direction.name}")

    def get_gunner_target(self) -> Position | None:
        return None

    def get_attackable_tiles(self) -> list[Position]:
        return []

    def get_attackable_tiles_from(self, pos: Position, direction: Direction, turret_type: EntityType) -> list[Position]:
        return []

    def can_launch(self, bot_pos: Position, target: Position) -> bool:
        return False

    def launch(self, bot_pos: Position, target: Position) -> None:
        self._action_log.append(f"launch {bot_pos.x} {bot_pos.y} {target.x} {target.y}")

    # Visualizer
    def draw_indicator_line(self, *args, **kwargs) -> None:
        pass

    def draw_indicator_dot(self, *args, **kwargs) -> None:
        pass

    # Utility
    def get_action_log(self) -> list[str]:
        return self._action_log

    def set_store_values(self, store: dict[int, int]) -> None:
        for k, v in store.items():
            self._store[k] = v
