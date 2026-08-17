"""Grand_knight — Testing Foundation: Mock Controller.

Provides a fully featured, deterministic mock of the fcode Controller interface
for isolated unit testing and regression verification.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Set, Tuple
from fcode import Direction, EntityType, Environment, Position


class MockController:
    """Mock implementation of the fcode Controller for regression tests."""

    def __init__(
        self,
        unit_id: int = 1,
        entity_type: EntityType = EntityType.BUILDER_BOT,
        team: int = 0,
        pos: Position = Position(5, 5),
        map_width: int = 30,
        map_height: int = 30,
    ) -> None:
        self.unit_id = unit_id
        self.entity_type = entity_type
        self.team = team
        self.pos = pos
        self.map_width = map_width
        self.map_height = map_height

        self.current_round: int = 0
        self.hp: int = 50
        self.max_hp: int = 50
        self.global_resources: int = 500
        self.global_ammo: int = 30
        self.move_cooldown: int = 0
        self.action_cooldown: int = 0
        self.cpu_time_elapsed: int = 100  # microseconds

        self.store: list[int] = [0] * 16
        self.env_grid: Dict[Tuple[int, int], Environment] = {}
        self.building_grid: Dict[Tuple[int, int], int] = {}
        self.entities: Dict[int, Dict] = {}  # id -> {type, team, pos, hp, max_hp}

        # Action history tracking for assertions
        self.moves_executed: list[Direction] = []
        self.builds_executed: list[Tuple[str, Position]] = []
        self.heals_executed: list[Position] = []
        self.fires_executed: list[Position] = []
        self.destroys_executed: list[Position] = []
        self.converts_executed: list[int] = []

        # Default self in entities dict
        self.entities[self.unit_id] = {
            "type": self.entity_type,
            "team": self.team,
            "pos": self.pos,
            "hp": self.hp,
            "max_hp": self.max_hp,
        }

    def spawn_building(self, pos: Position, btype: EntityType, team: Optional[int] = None) -> int:
        bid = len(self.entities) + 100
        t = self.team if team is None else team
        self.entities[bid] = {
            "type": btype,
            "team": t,
            "pos": pos,
            "hp": 30,
            "max_hp": 30,
        }
        self.building_grid[(pos.x, pos.y)] = bid
        return bid

    # Time & Identification
    def get_current_round(self) -> int:
        return self.current_round

    def get_id(self) -> int:
        return self.unit_id

    def get_team(self, entity_id: Optional[int] = None) -> int:
        if entity_id is None or entity_id == self.unit_id:
            return self.team
        return self.entities.get(entity_id, {}).get("team", self.team)

    def get_entity_type(self, entity_id: Optional[int] = None) -> EntityType:
        if entity_id is None or entity_id == self.unit_id:
            return self.entity_type
        return self.entities.get(entity_id, {}).get("type", self.entity_type)

    def get_position(self, entity_id: Optional[int] = None) -> Position:
        if entity_id is None or entity_id == self.unit_id:
            return self.pos
        return self.entities.get(entity_id, {}).get("pos", self.pos)

    # Health & Resources
    def get_hp(self, entity_id: Optional[int] = None) -> int:
        if entity_id is None or entity_id == self.unit_id:
            return self.hp
        return self.entities.get(entity_id, {}).get("hp", self.hp)

    def get_max_hp(self, entity_id: Optional[int] = None) -> int:
        if entity_id is None or entity_id == self.unit_id:
            return self.max_hp
        return self.entities.get(entity_id, {}).get("max_hp", self.max_hp)

    def get_global_resources(self) -> int:
        return self.global_resources

    def get_global_ammo(self) -> int:
        return self.global_ammo

    def get_unit_count(self) -> int:
        return sum(1 for e in self.entities.values() if e.get("team") == self.team and e.get("type") == EntityType.BUILDER_BOT) + (1 if self.entity_type == EntityType.CORE else 0)

    # Cooldowns & CPU
    def get_move_cooldown(self) -> int:
        return self.move_cooldown

    def get_action_cooldown(self) -> int:
        return self.action_cooldown

    def get_cpu_time_elapsed(self) -> int:
        return self.cpu_time_elapsed

    # Map Dimensions
    def get_map_width(self) -> int:
        return self.map_width

    def get_map_height(self) -> int:
        return self.map_height

    # Blackboard Store
    def read_store(self, slot: int) -> int:
        if 0 <= slot < 16:
            return self.store[slot]
        return 0

    def write_store(self, slot: int, value: int) -> None:
        if 0 <= slot < 16:
            self.store[slot] = value

    # Spatial Queries
    def get_tile_env(self, pos: Position) -> Environment:
        return self.env_grid.get((pos.x, pos.y), Environment.EMPTY)

    def set_tile_env(self, pos: Position, env: Environment) -> None:
        self.env_grid[(pos.x, pos.y)] = env

    def get_tile_building_id(self, pos: Position) -> Optional[int]:
        return self.building_grid.get((pos.x, pos.y), None)

    def get_tile_builder_bot_id(self, pos: Position) -> Optional[int]:
        for eid, data in self.entities.items():
            if data["type"] == EntityType.BUILDER_BOT and data["pos"] == pos:
                return eid
        return None

    def get_nearby_entities(self, dist_sq: Optional[int] = None) -> list[int]:
        res = []
        for eid, data in self.entities.items():
            if eid == self.unit_id:
                continue
            epos = data["pos"]
            d = self.pos.distance_squared(epos)
            if dist_sq is None or d <= dist_sq:
                res.append(eid)
        return res

    def get_nearby_units(self, dist_sq: Optional[int] = None) -> list[int]:
        res = []
        for eid, data in self.entities.items():
            if eid == self.unit_id:
                continue
            if data["type"] == EntityType.BUILDER_BOT:
                d = self.pos.distance_squared(data["pos"])
                if dist_sq is None or d <= dist_sq:
                    res.append(eid)
        return res

    def get_nearby_buildings(self, dist_sq: Optional[int] = None) -> list[int]:
        res = []
        for eid, data in self.entities.items():
            if eid == self.unit_id:
                continue
            if data["type"] != EntityType.BUILDER_BOT:
                d = self.pos.distance_squared(data["pos"])
                if dist_sq is None or d <= dist_sq:
                    res.append(eid)
        return res

    def get_nearby_tiles(self, dist_sq: Optional[int] = None) -> list[Position]:
        res = []
        radius = 5
        for dx in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                p = Position(self.pos.x + dx, self.pos.y + dy)
                if 0 <= p.x < self.map_width and 0 <= p.y < self.map_height:
                    d = self.pos.distance_squared(p)
                    if dist_sq is None or d <= dist_sq:
                        res.append(p)
        return res

    # Costs
    def get_harvester_cost(self) -> int:
        return 15

    def get_conveyor_cost(self) -> int:
        return 1

    def get_gunner_cost(self) -> int:
        return 12

    def get_sentinel_cost(self) -> int:
        return 18

    def get_barrier_cost(self) -> int:
        return 2

    def get_builder_bot_cost(self) -> int:
        return 20

    # Actions & Preconditions
    def can_act(self) -> bool:
        return self.action_cooldown == 0

    def can_move(self, direction: Direction) -> bool:
        if self.move_cooldown != 0:
            return False
        nxt = self.pos.add(direction)
        if not (0 <= nxt.x < self.map_width and 0 <= nxt.y < self.map_height):
            return False
        if self.get_tile_env(nxt) == Environment.WALL:
            return False
        if self.get_tile_building_id(nxt) is not None:
            return False
        return True

    def move(self, direction: Direction) -> None:
        if not self.can_move(direction):
            raise RuntimeError(f"Illegal move direction {direction}")
        self.moves_executed.append(direction)
        self.pos = self.pos.add(direction)
        self.entities[self.unit_id]["pos"] = self.pos
        self.move_cooldown = 1

    def _is_adjacent(self, pos: Position) -> bool:
        return abs(self.pos.x - pos.x) + abs(self.pos.y - pos.y) == 1

    def can_build_harvester(self, pos: Position) -> bool:
        return (
            self.action_cooldown == 0
            and self.global_resources >= self.get_harvester_cost()
            and self.get_tile_building_id(pos) is None
            and self.get_tile_env(pos) == Environment.ORE_TITANIUM
            and self._is_adjacent(pos)
        )

    def build_harvester(self, pos: Position) -> None:
        if not self.can_build_harvester(pos):
            raise RuntimeError(f"Illegal build_harvester at {pos}")
        self.builds_executed.append(("harvester", pos))
        self.global_resources -= self.get_harvester_cost()
        bid = 1000 + len(self.builds_executed)
        self.building_grid[(pos.x, pos.y)] = bid
        self.entities[bid] = {"type": EntityType.HARVESTER, "team": self.team, "pos": pos, "hp": 50, "max_hp": 50}
        self.action_cooldown = 1

    def get_direction(self, entity_id: Optional[int] = None) -> Direction:
        if entity_id is None or entity_id == self.unit_id:
            return Direction.NORTH
        return self.entities.get(entity_id, {}).get("direction", Direction.NORTH)

    def can_build_conveyor(self, pos: Position, direction: Direction) -> bool:
        return (
            self.action_cooldown == 0
            and self.global_resources >= self.get_conveyor_cost()
            and self.get_tile_building_id(pos) is None
            and self.get_tile_env(pos) != Environment.WALL
            and self._is_adjacent(pos)
        )

    def build_conveyor(self, pos: Position, direction: Direction) -> None:
        if not self.can_build_conveyor(pos, direction):
            raise RuntimeError(f"Illegal build_conveyor at {pos}")
        self.builds_executed.append(("conveyor", pos))
        self.global_resources -= self.get_conveyor_cost()
        bid = 2000 + len(self.builds_executed)
        self.building_grid[(pos.x, pos.y)] = bid
        self.entities[bid] = {
            "type": EntityType.CONVEYOR,
            "team": self.team,
            "pos": pos,
            "direction": direction,
            "hp": 10,
            "max_hp": 10,
        }
        self.action_cooldown = 1

    def can_build_barrier(self, pos: Position) -> bool:
        return (
            self.action_cooldown == 0
            and self.global_resources >= self.get_barrier_cost()
            and self.get_tile_building_id(pos) is None
            and self._is_adjacent(pos)
        )

    def build_barrier(self, pos: Position) -> None:
        if not self.can_build_barrier(pos):
            raise RuntimeError(f"Illegal build_barrier at {pos}")
        self.builds_executed.append(("barrier", pos))
        self.global_resources -= self.get_barrier_cost()
        bid = 3000 + len(self.builds_executed)
        self.building_grid[(pos.x, pos.y)] = bid
        self.entities[bid] = {"type": EntityType.BARRIER, "team": self.team, "pos": pos, "hp": 30, "max_hp": 30}
        self.action_cooldown = 1

    def can_build_sentinel(self, pos: Position, direction: Direction) -> bool:
        return (
            self.action_cooldown == 0
            and self.global_resources >= self.get_sentinel_cost()
            and self.get_tile_building_id(pos) is None
            and self._is_adjacent(pos)
        )

    def build_sentinel(self, pos: Position, direction: Direction) -> None:
        if not self.can_build_sentinel(pos, direction):
            raise RuntimeError(f"Illegal build_sentinel at {pos}")
        self.builds_executed.append(("sentinel", pos))
        self.global_resources -= self.get_sentinel_cost()
        bid = 4000 + len(self.builds_executed)
        self.building_grid[(pos.x, pos.y)] = bid
        self.entities[bid] = {
            "type": EntityType.SENTINEL,
            "team": self.team,
            "pos": pos,
            "direction": direction,
            "hp": 40,
            "max_hp": 40,
        }
        self.action_cooldown = 1

    def can_build_gunner(self, pos: Position, direction: Direction) -> bool:
        return (
            self.action_cooldown == 0
            and self.global_resources >= self.get_gunner_cost()
            and self.get_tile_building_id(pos) is None
            and self._is_adjacent(pos)
        )

    def build_gunner(self, pos: Position, direction: Direction) -> None:
        if not self.can_build_gunner(pos, direction):
            raise RuntimeError(f"Illegal build_gunner at {pos}")
        self.builds_executed.append(("gunner", pos))
        self.global_resources -= self.get_gunner_cost()
        bid = 5000 + len(self.builds_executed)
        self.building_grid[(pos.x, pos.y)] = bid
        self.entities[bid] = {
            "type": EntityType.GUNNER,
            "team": self.team,
            "pos": pos,
            "direction": direction,
            "hp": 30,
            "max_hp": 30,
        }
        self.action_cooldown = 1

    def can_spawn_builder(self, pos: Position) -> bool:
        return (
            self.action_cooldown == 0
            and self.global_resources >= self.get_builder_bot_cost()
            and self.get_tile_building_id(pos) is None
            and self._is_adjacent(pos)
        )

    def can_spawn(self, pos: Position) -> bool:
        return self.can_spawn_builder(pos)

    def spawn_builder(self, pos: Position) -> None:
        if not self.can_spawn_builder(pos):
            raise RuntimeError(f"Illegal spawn_builder at {pos}")
        self.builds_executed.append(("builder", pos))
        self.global_resources -= self.get_builder_bot_cost()
        uid = 500 + len(self.builds_executed)
        self.entities[uid] = {"type": EntityType.BUILDER_BOT, "team": self.team, "pos": pos, "hp": 25, "max_hp": 25}
        self.action_cooldown = 1

    def spawn(self, pos: Position) -> None:
        self.spawn_builder(pos)

    def can_fire(self, pos: Position) -> bool:
        return self.action_cooldown == 0 and self._is_adjacent(pos)

    def fire(self, pos: Position) -> None:
        if not self.can_fire(pos):
            raise RuntimeError(f"Illegal fire at {pos}")
        self.fires_executed.append(pos)
        self.action_cooldown = 1

    def can_destroy(self, pos: Position) -> bool:
        return self.action_cooldown == 0 and self.get_tile_building_id(pos) is not None and self._is_adjacent(pos)

    def destroy(self, pos: Position) -> None:
        if not self.can_destroy(pos):
            raise RuntimeError(f"Illegal destroy at {pos}")
        self.destroys_executed.append(pos)
        bid = self.building_grid.pop((pos.x, pos.y), None)
        if bid in self.entities:
            del self.entities[bid]
        self.action_cooldown = 1

    def can_heal(self, pos: Position) -> bool:
        return self.action_cooldown == 0 and self.global_resources >= 1 and self._is_adjacent(pos)

    def heal(self, pos: Position) -> None:
        if not self.can_heal(pos):
            raise RuntimeError(f"Illegal heal at {pos}")
        self.heals_executed.append(pos)
        self.global_resources -= 1
        self.action_cooldown = 1

    def can_convert_ammo(self, amount: int) -> bool:
        return self.global_resources >= amount

    def convert_ammo(self, amount: int) -> None:
        if not self.can_convert_ammo(amount):
            raise RuntimeError(f"Illegal convert_ammo {amount}")
        self.converts_executed.append(amount)
        self.global_resources -= amount
        self.global_ammo += amount
