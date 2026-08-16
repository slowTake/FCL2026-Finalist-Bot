import sys
from pathlib import Path
from dataclasses import dataclass, field

import os
sys.path.insert(0, "/home/nraatika/projects/florent/.venv/lib/python3.12/site-packages")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
import florent_replay_pb2 as pb
from fcode import Position, Direction, EntityType, Environment, Team, ResourceType, GameConstants


@dataclass
class EntityRecord:
    """Represents a snapshot of a game entity's state."""
    id: int
    team: Team
    x: int
    y: int
    hp: int
    max_hp: int
    entity_type: EntityType
    direction: Direction | None = None
    action_cooldown: int = 0
    move_cooldown: int = 0
    stored_resource: ResourceType | None = None
    ammo_type: ResourceType | None = None
    ammo_amount: int = 0
    harvester_cooldown: int = 0


@dataclass
class WorldState:
    """Represents the complete reconstructed game state at a specific round."""
    round_number: int
    map_width: int
    map_height: int
    tile_grid: list[list[Environment]]
    entities: dict[int, EntityRecord]
    tile_buildings: dict[tuple[int, int], int]
    tile_bots: dict[tuple[int, int], int]
    team_titanium: dict[Team, int]
    team_ammo: dict[Team, int]
    entity_build_counts: dict[Team, dict[str, int]]
    bot_outputs: dict[int, str]
    bot_exec_times: dict[int, int]
    bot_tled: dict[int, bool]


class ReplayStateBuilder:
    """Parses a game replay file and reconstructs WorldState incrementally."""

    def __init__(self, replay_path: str):
        """Initializes the builder by reading and parsing the replay file."""
        self.replay_path = replay_path
        self.replay = pb.Replay()
        with open(replay_path, 'rb') as f:
            self.replay.ParseFromString(f.read())
            
        self.map_width = self.replay.map.width
        self.map_height = self.replay.map.height

    def get_total_rounds(self) -> int:
        """Returns the total number of rounds recorded in the replay."""
        return len(self.replay.turns)

    def get_winner(self) -> str | None:
        """Returns the winning team's name, or None if there is no clear winner."""
        if self.replay.HasField('winner'):
            return 'A' if self.replay.winner == pb.TEAM_A else 'B'
        return None

    def _pb_direction_to_fcode(self, d: int) -> Direction | None:
        """Maps protobuf direction int to fcode Direction."""
        if d == pb.DIR_NORTH:
            return Direction.NORTH
        elif d == pb.DIR_EAST:
            return Direction.EAST
        elif d == pb.DIR_SOUTH:
            return Direction.SOUTH
        elif d == pb.DIR_WEST:
            return Direction.WEST
        return None

    def _pb_resource_to_fcode(self, r: int) -> ResourceType | None:
        """Maps protobuf resource type int to fcode ResourceType."""
        if r == pb.RESOURCE_TITANIUM:
            return ResourceType.TITANIUM
        return None
        
    def _pb_env_to_fcode(self, e: int) -> Environment:
        """Maps protobuf environment int to fcode Environment."""
        if e == pb.ENV_WALL:
            return Environment.WALL
        elif e == pb.ENV_ORE_TITANIUM:
            return Environment.ORE_TITANIUM
        return Environment.EMPTY

    def build_state(self, target_round: int) -> WorldState:
        """Reconstructs and returns the WorldState up to the specified target_round."""
        # Initialize tile grid
        tile_grid = []
        for r in self.replay.map.rows:
            row = [self._pb_env_to_fcode(t) for t in r.tiles]
            tile_grid.append(row)

        entities: dict[int, EntityRecord] = {}
        tile_buildings: dict[tuple[int, int], int] = {}
        tile_bots: dict[tuple[int, int], int] = {}
        team_titanium = {Team.A: 500, Team.B: 500}
        team_ammo = {Team.A: 0, Team.B: 0}
        entity_build_counts: dict[Team, dict[str, int]] = {Team.A: {}, Team.B: {}}

        # Place initial cores
        for core in self.replay.map.cores:
            t = Team.A if core.team == pb.TEAM_A else Team.B
            e = EntityRecord(
                id=core.id,
                team=t,
                x=core.position.x,
                y=core.position.y,
                hp=500,
                max_hp=500,
                entity_type=EntityType.CORE
            )
            entities[e.id] = e
            # A core occupies 2x2 space
            for dx in range(2):
                for dy in range(2):
                    tile_buildings[(e.x + dx, e.y + dy)] = e.id

        bot_outputs = {}
        bot_exec_times = {}
        bot_tled = {}

        for turn_idx in range(min(target_round + 1, len(self.replay.turns))):
            bot_outputs.clear()
            bot_exec_times.clear()
            bot_tled.clear()
            
            turn = self.replay.turns[turn_idx]
            for update in turn.updates:
                kind = update.WhichOneof('kind')
                
                if kind == 'placeEntity':
                    pe = update.placeEntity
                    ent_pb = pe.entity
                    entity_kind = ent_pb.WhichOneof('kind')
                    
                    t = Team.A if ent_pb.team == pb.TEAM_A else Team.B
                    
                    fcode_entity_type = None
                    direction = None
                    ammo_type = None
                    ammo_amount = 0
                    stored_resource = None
                    action_cooldown = 0
                    harvester_cooldown = 0
                    max_hp = ent_pb.maxHp
                    hp = ent_pb.hp
                    
                    # Map protobuf Entity kind to fcode EntityType and extract fields
                    if entity_kind == 'builderBot':
                        fcode_entity_type = EntityType.BUILDER_BOT
                        action_cooldown = ent_pb.builderBot.actionCooldown
                        move_cooldown = ent_pb.builderBot.moveCooldown
                    elif entity_kind == 'conveyor':
                        fcode_entity_type = EntityType.CONVEYOR
                        direction = self._pb_direction_to_fcode(ent_pb.conveyor.direction)
                        stored_resource = self._pb_resource_to_fcode(ent_pb.conveyor.stored)
                    elif entity_kind == 'splitter':
                        fcode_entity_type = EntityType.SPLITTER
                        direction = self._pb_direction_to_fcode(ent_pb.splitter.direction)
                        stored_resource = self._pb_resource_to_fcode(ent_pb.splitter.stored)
                    elif entity_kind == 'harvester':
                        fcode_entity_type = EntityType.HARVESTER
                        harvester_cooldown = ent_pb.harvester.cooldown
                        # resourceType is not mapped directly unless needed
                    elif entity_kind == 'barrier':
                        fcode_entity_type = EntityType.BARRIER
                    elif entity_kind == 'core':
                        fcode_entity_type = EntityType.CORE
                        action_cooldown = ent_pb.core.actionCooldown
                    elif entity_kind == 'gunner':
                        fcode_entity_type = EntityType.GUNNER
                        direction = self._pb_direction_to_fcode(ent_pb.gunner.direction)
                        ammo_type = self._pb_resource_to_fcode(ent_pb.gunner.ammoType)
                        ammo_amount = ent_pb.gunner.ammoAmount
                    elif entity_kind == 'sentinel':
                        fcode_entity_type = EntityType.SENTINEL
                        direction = self._pb_direction_to_fcode(ent_pb.sentinel.direction)
                        ammo_type = self._pb_resource_to_fcode(ent_pb.sentinel.ammoType)
                        ammo_amount = ent_pb.sentinel.ammoAmount
                    elif entity_kind == 'launcher':
                        fcode_entity_type = EntityType.LAUNCHER
                        ammo_type = self._pb_resource_to_fcode(ent_pb.launcher.ammoType)
                        ammo_amount = ent_pb.launcher.ammoAmount
                    
                    move_cooldown_val = move_cooldown if fcode_entity_type == EntityType.BUILDER_BOT else 0
                    e = EntityRecord(
                        id=ent_pb.id,
                        team=t,
                        x=ent_pb.position.x,
                        y=ent_pb.position.y,
                        hp=hp,
                        max_hp=max_hp,
                        entity_type=fcode_entity_type,
                        direction=direction,
                        action_cooldown=action_cooldown,
                        move_cooldown=move_cooldown_val,
                        stored_resource=stored_resource,
                        ammo_type=ammo_type,
                        ammo_amount=ammo_amount,
                        harvester_cooldown=harvester_cooldown
                    )
                    entities[e.id] = e
                    
                    type_name = str(fcode_entity_type)
                    entity_build_counts[t][type_name] = entity_build_counts[t].get(type_name, 0) + 1
                    
                    if fcode_entity_type == EntityType.BUILDER_BOT:
                        tile_bots[(e.x, e.y)] = e.id
                    else:
                        if fcode_entity_type == EntityType.CORE:
                            for dx in range(2):
                                for dy in range(2):
                                    tile_buildings[(e.x + dx, e.y + dy)] = e.id
                        else:
                            tile_buildings[(e.x, e.y)] = e.id
                            
                elif kind == 'moveBuilderBot':
                    mv = update.moveBuilderBot
                    e = entities.get(mv.id)
                    if e:
                        if (e.x, e.y) in tile_bots and tile_bots[(e.x, e.y)] == e.id:
                            del tile_bots[(e.x, e.y)]
                        e.x = mv.to.x
                        e.y = mv.to.y
                        tile_bots[(e.x, e.y)] = e.id
                        
                elif kind == 'removeEntity':
                    rm = update.removeEntity
                    e = entities.get(rm.id)
                    if e:
                        if e.entity_type == EntityType.BUILDER_BOT:
                            if (e.x, e.y) in tile_bots and tile_bots[(e.x, e.y)] == e.id:
                                del tile_bots[(e.x, e.y)]
                        else:
                            if e.entity_type == EntityType.CORE:
                                for dx in range(2):
                                    for dy in range(2):
                                        if (e.x + dx, e.y + dy) in tile_buildings and tile_buildings[(e.x + dx, e.y + dy)] == e.id:
                                            del tile_buildings[(e.x + dx, e.y + dy)]
                            else:
                                if (e.x, e.y) in tile_buildings and tile_buildings[(e.x, e.y)] == e.id:
                                    del tile_buildings[(e.x, e.y)]
                                    
                        type_name = str(e.entity_type)
                        if type_name in entity_build_counts[e.team]:
                            entity_build_counts[e.team][type_name] -= 1
                        del entities[rm.id]
                        
                elif kind == 'updateHp':
                    uhp = update.updateHp
                    e = entities.get(uhp.id)
                    if e:
                        e.hp = max(0, min(e.max_hp, e.hp + uhp.delta))
                        
                elif kind == 'updatePlayers':
                    upl = update.updatePlayers.players
                    team_titanium[Team.A] = upl.a.titanium
                    team_titanium[Team.B] = upl.b.titanium
                    
                elif kind == 'setActionCooldown':
                    sac = update.setActionCooldown
                    e = entities.get(sac.id)
                    if e:
                        e.action_cooldown = sac.value
                        
                elif kind == 'setMoveCooldown':
                    smc = update.setMoveCooldown
                    e = entities.get(smc.id)
                    if e:
                        e.move_cooldown = smc.value
                        
                elif kind == 'distributeResources':
                    dr = update.distributeResources
                    for rm in dr.moves:
                        from_pos = (getattr(rm, 'from').x, getattr(rm, 'from').y)
                        to_pos = (rm.to.x, rm.to.y)
                        # Find source entity at from_pos
                        src_id = tile_buildings.get(from_pos)
                        dst_id = tile_buildings.get(to_pos)
                        if src_id and src_id in entities:
                            entities[src_id].stored_resource = None
                        if dst_id and dst_id in entities:
                            dst = entities[dst_id]
                            if dst.entity_type in (EntityType.GUNNER, EntityType.SENTINEL, EntityType.LAUNCHER):
                                dst.ammo_type = ResourceType.TITANIUM
                                dst.ammo_amount += 10  # stacks of 10
                            else:
                                dst.stored_resource = ResourceType.TITANIUM
                                
                elif kind == 'botOutput':
                    bo = update.botOutput
                    bot_outputs[bo.id] = bo.stdout
                    bot_exec_times[bo.id] = bo.execTimeUs
                    bot_tled[bo.id] = bo.tled
                    
                elif kind == 'fireTurret':
                    ft = update.fireTurret
                    turret_id = None
                    ft_from = getattr(ft, 'from')
                    if (ft_from.x, ft_from.y) in tile_buildings:
                        turret_id = tile_buildings[(ft_from.x, ft_from.y)]
                    
                    if turret_id is not None:
                        e = entities.get(turret_id)
                        if e:
                            if e.entity_type == EntityType.GUNNER:
                                team_ammo[e.team] -= 2
                            elif e.entity_type == EntityType.SENTINEL:
                                team_ammo[e.team] -= 10
                                
                # Skip indicatorLine, indicatorDot, builderAttack as requested

        return WorldState(
            round_number=target_round,
            map_width=self.map_width,
            map_height=self.map_height,
            tile_grid=tile_grid,
            entities=entities,
            tile_buildings=tile_buildings,
            tile_bots=tile_bots,
            team_titanium=team_titanium,
            team_ammo=team_ammo,
            entity_build_counts=entity_build_counts,
            bot_outputs=bot_outputs,
            bot_exec_times=bot_exec_times,
            bot_tled=bot_tled
        )
