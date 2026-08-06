from fcode import Controller, EntityType, Position, Environment, Direction, Team
from enum import IntEnum
from features import KNOWN_MAPS, MAP_ID_TO_NAME, WALL_LOCATIONS, ORE_LOCATIONS
from vision_v2 import pack_ledger_slot, unpack_ledger_slot


class CoreState(IntEnum):
    UNKNOWN = 0
    ASSUMED = 1
    KNOWN = 2


def pack_cores(ally_state, ally_x, ally_y, enemy_state, enemy_x, enemy_y) -> int:
    ally_bits = (ally_state << 14) | (ally_x << 7) | ally_y
    enemy_bits = (enemy_state << 14) | (enemy_x << 7) | enemy_y
    return (enemy_bits << 16) | ally_bits


def unpack_cores(val: int):
    ally_bits = val & 0xFFFF
    enemy_bits = (val >> 16) & 0xFFFF
    ally = (CoreState(ally_bits >> 14), (ally_bits >> 7) & 0x7F, ally_bits & 0x7F)
    enemy = (CoreState(enemy_bits >> 14), (enemy_bits >> 7) & 0x7F, enemy_bits & 0x7F)
    return ally, enemy


class HiveMind:
    def __init__(self):
        self.local_map = {}
        self.global_turn = 0
        self.my_index = 0
        self.birth_round: int | None = None
        self.map_id = 0
        self.map_name = "UNKNOWN"
        self.enemy_core_pos = None  # set on fingerprint from KNOWN_MAPS
        self._map_detected = False
        self._walls_loaded = False
        self.last_communicated_ledger = {}  # (x,y) -> (etype, is_enemy, d)

    def _detect_and_store_map(self, ct: Controller) -> None:
        if self._map_detected: return

        w = ct.get_map_width()
        h = ct.get_map_height()
        team = ct.get_team()
        pos = ct.get_position()

        wall_count = 0
        for tile in ct.get_nearby_tiles():
            if ct.get_tile_env(tile) == Environment.WALL:
                wall_count += 1

        fingerprint = (w, h, team, pos.x, pos.y, wall_count)
        enemy_state = CoreState.UNKNOWN
        enemy_x = 0
        enemy_y = 0

        if fingerprint in KNOWN_MAPS:
            self.map_id, self.map_name, enemy_x, enemy_y = KNOWN_MAPS[fingerprint]
            enemy_state = CoreState.ASSUMED
            self.enemy_core_pos = Position(enemy_x, enemy_y)

        ct.write_store(14, self.map_id)

        packed = pack_cores(
            CoreState.KNOWN, pos.x, pos.y,
            enemy_state, enemy_x, enemy_y
        )
        ct.write_store(15, packed)
        self._map_detected = True

    def _update_local_vision(self, ct: Controller):
        pos = ct.get_position()
        w = ct.get_map_width()
        h = ct.get_map_height()

        entity_positions = {}
        for eid in ct.get_nearby_entities():
            epos = ct.get_position(eid)
            etype = ct.get_entity_type(eid)
            eteam = ct.get_team(eid)
            ent_data = {"type": etype, "team": eteam}
            if etype in (EntityType.CONVEYOR, EntityType.SPLITTER, EntityType.GUNNER, EntityType.SENTINEL):
                try:
                    ent_data["direction"] = ct.get_direction(eid)
                except Exception:
                    pass
            if etype == EntityType.CORE:
                entity_positions[(epos.x, epos.y)] = ent_data
                entity_positions[(epos.x + 1, epos.y)] = ent_data
                entity_positions[(epos.x, epos.y + 1)] = ent_data
                entity_positions[(epos.x + 1, epos.y + 1)] = ent_data
            else:
                entity_positions[(epos.x, epos.y)] = ent_data

        for dy in range(-6, 7):
            for dx in range(-6, 7):
                px = pos.x + dx
                py = pos.y + dy
                if 0 <= px < w and 0 <= py < h:
                    pos_obj = Position(px, py)
                    if not ct.is_in_vision(pos_obj):
                        continue
                            
                    if (px, py) not in self.local_map:
                        self.local_map[(px, py)] = {}
                    self.local_map[(px, py)]["last_seen_turn"] = self.global_turn
                    if (px, py) in entity_positions:
                        ent_type = entity_positions[(px, py)]["type"]
                        if ent_type in (EntityType.CONVEYOR, EntityType.SPLITTER):
                            self.local_map[(px, py)]["passable"] = True
                        else:
                            self.local_map[(px, py)]["passable"] = False
                        self.local_map[(px, py)]["entity"] = entity_positions[(px, py)]
                    else:
                        env_tile = ct.get_tile_env(pos_obj)
                        is_wall = (env_tile == Environment.WALL)
                        self.local_map[(px, py)]["passable"] = not is_wall
                        if env_tile == Environment.ORE_TITANIUM:
                            self.local_map[(px, py)]["ore"] = True
                            
                        if "entity" in self.local_map[(px, py)]:
                            del self.local_map[(px, py)]["entity"]

    def _broadcast_ledger(self, ct: Controller):
        pos = ct.get_position()
        w = ct.get_map_width()
        h = ct.get_map_height()
        deltas = []
        
        # Always include our own position FIRST!
        # This guarantees liveness reporting.
        my_team = ct.get_team()
        deltas.append((pos.x, pos.y, EntityType.BUILDER_BOT, False, Direction.NORTH))
        self.last_communicated_ledger[(pos.x, pos.y)] = (EntityType.BUILDER_BOT, False, Direction.NORTH)

        # Iterate vision to find changed entities
        for priority_pass in range(3):
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    if dx*dx + dy*dy > 16: continue
                    px = pos.x + dx
                    py = pos.y + dy
                    if 0 <= px < w and 0 <= py < h:
                        pos_tuple = (px, py)
                        ent = self.local_map.get(pos_tuple, {}).get("entity")
                        
                        etype = ent.get("type") if ent else None
                        is_enemy = (ent.get("team") != my_team) if ent else False
                        d = ent.get("direction", Direction.NORTH) if ent else Direction.NORTH
                        
                        # Priority 1: Enemy Core, Gunners, Sentinels
                        # Priority 2: Enemy Harvesters, Builders
                        # Priority 3: Ally logistics / other
                        if priority_pass == 0:
                            if not is_enemy or etype not in (EntityType.CORE, EntityType.GUNNER, EntityType.SENTINEL):
                                continue
                        elif priority_pass == 1:
                            if not is_enemy or etype not in (EntityType.HARVESTER, EntityType.BUILDER_BOT):
                                continue
                        elif priority_pass == 2:
                            if is_enemy and etype in (EntityType.CORE, EntityType.GUNNER, EntityType.SENTINEL, EntityType.HARVESTER, EntityType.BUILDER_BOT):
                                continue
                                
                        current_state = (etype, is_enemy, d)
                        last_state = self.last_communicated_ledger.get(pos_tuple)
                        
                        # Only broadcast if changed
                        if current_state != last_state:
                            deltas.append((px, py, etype, is_enemy, d))
                            self.last_communicated_ledger[pos_tuple] = current_state
                            
                            if len(deltas) >= 16:
                                break
                if len(deltas) >= 16:
                    break
            if len(deltas) >= 16:
                break
                
        # Fill rest with None
        while len(deltas) < 16:
            deltas.append(None)
            
        # Pack into Slots 5 through 12
        for i in range(8):
            val = pack_ledger_slot(deltas[i*2], deltas[i*2 + 1])
            ct.write_store(5 + i, val)

    def _sync_ledger(self, ct: Controller):
        all_zero = True
        slots_data = []
        for i in range(8):
            val = ct.read_store(5 + i)
            if val != 0: all_zero = False
            slots_data.append(val)
            
        return all_zero, slots_data
        
    def spawn_builder(self, ct: Controller, pos) -> bool:
        """Helper to spawn a builder and instantly register it with the HiveMind registry."""
        if ct.can_spawn(pos):
            ct.spawn_builder(pos)
            # Slot 4 is now the total builders
            total = ct.read_store(4)
            ct.write_store(4, total + 1)
            return True
        return False

    def tick(self, ct: Controller):
        """Must be called at the very top of every unit's run() method."""
        is_core = (ct.get_entity_type() == EntityType.CORE)

        # 1. Sync global turn natively from the engine
        self.global_turn = ct.get_current_round()

        # 2. Assign builder index once the spawn write to slot 4 is visible.
        if ct.get_entity_type() == EntityType.BUILDER_BOT and self.my_index == 0:
            if self.birth_round is None:
                self.birth_round = self.global_turn
            elif self.global_turn == self.birth_round + 1:
                idx = ct.read_store(4)
                if idx > 0:
                    self.my_index = idx

        # 3. Process Map Detection (Core Only)
        if is_core and self.global_turn == 0:
            self._detect_and_store_map(ct)

        # 4. Read static map knowledge if we haven't
        if self.map_id == 0 and self.global_turn > 0:
            self.map_id = ct.read_store(14)
            
        if self.map_id != 0 and not self._walls_loaded:
            if self.map_id in WALL_LOCATIONS:
                for wx, wy in WALL_LOCATIONS[self.map_id]:
                    if (wx, wy) not in self.local_map:
                        self.local_map[(wx, wy)] = {}
                    self.local_map[(wx, wy)]["passable"] = False
                    
            if self.map_id in ORE_LOCATIONS:
                for ox, oy in ORE_LOCATIONS[self.map_id]:
                    if (ox, oy) not in self.local_map:
                        self.local_map[(ox, oy)] = {}
                    self.local_map[(ox, oy)]["passable"] = True
                    self.local_map[(ox, oy)]["ore"] = True
                    
            self._walls_loaded = True

        # 5. Execute Metronome Sync Protocol (Core reads, Builders write)
        if is_core:
            # First, check if the previously assigned builder reported
            if self.global_turn > 0:
                is_dead, slots_data = self._sync_ledger(ct)
                
                if not is_dead:
                    # Unpack everything
                    for val in slots_data:
                        t0, t1 = unpack_ledger_slot(val)
                        for t in (t0, t1):
                            if t:
                                tx, ty, etype, is_enemy, d = t
                                pos_tuple = (tx, ty)
                                if pos_tuple not in self.local_map:
                                    self.local_map[pos_tuple] = {}
                                    
                                if etype is None:
                                    # It's an empty/deleted entity
                                    if "entity" in self.local_map[pos_tuple]:
                                        del self.local_map[pos_tuple]["entity"]
                                else:
                                    # Record the entity
                                    my_team = ct.get_team()
                                    other_team = Team.B if my_team == Team.A else Team.A
                                    actual_team = other_team if is_enemy else my_team
                                    
                                    self.local_map[pos_tuple]["entity"] = {
                                        "type": etype,
                                        "team": actual_team,
                                        "direction": d
                                    }
                                    if etype in (EntityType.CONVEYOR, EntityType.SPLITTER):
                                        self.local_map[pos_tuple]["passable"] = True
                                    elif etype not in (EntityType.CONVEYOR, EntityType.SPLITTER):
                                        self.local_map[pos_tuple]["passable"] = False
                                    self.local_map[pos_tuple]["last_seen_turn"] = self.global_turn
                                        
                # Then advance metronome and clear slots
                total_builders = ct.read_store(4)
                if total_builders > 0:
                    current = ct.read_store(13)
                    if current == 0:
                        current = 1
                    else:
                        current += 1
                        if current > total_builders:
                            current = 1
                    ct.write_store(13, current)
                    # Clear slots
                    for i in range(8):
                        ct.write_store(5 + i, 0)
        else:
            # BUILDER: Check if it's our turn to broadcast
            current = ct.read_store(13)
            if current == self.my_index:
                self._broadcast_ledger(ct)

        # 6. Always update personal vision LAST (so it overrides stale network data)
        self._update_local_vision(ct)
        
        # 7. Garbage Collect Dead Builders (Core Only)
        if is_core:
            total_builders = ct.read_store(4)
            stale_threshold = self.global_turn - (total_builders + 2)
            for p, cell in list(self.local_map.items()):
                if "entity" in cell:
                    ent = cell["entity"]
                    if ent["type"] == EntityType.BUILDER_BOT and ent["team"] == ct.get_team():
                        if cell.get("last_seen_turn", 0) < stale_threshold:
                            del cell["entity"]
                            cell["passable"] = True
