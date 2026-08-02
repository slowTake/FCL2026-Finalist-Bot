from fcode import Controller, EntityType, Position, Environment, Direction
from enum import IntEnum
from features import KNOWN_MAPS, MAP_ID_TO_NAME, WALL_LOCATIONS, ORE_LOCATIONS
from vision import pack_vision_grid, unpack_vision_grid, pack_intel_slot, unpack_intel_slot, INT_TO_ENTITY, pack_infra, unpack_infra

# (committed_mask << 16) | accumulating_mask — bit i set => builder index i alive.
SLOT_LIVING_BUILDERS = 4


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


def _living_masks(val: int) -> tuple[int, int]:
    return (val >> 16) & 0xFFFF, val & 0xFFFF


def _pack_living_masks(committed: int, accum: int) -> int:
    return ((committed & 0xFFFF) << 16) | (accum & 0xFFFF)


def living_builder_count(ct: Controller) -> int:
    """Alive builders from slot 4 (committed | accumulating heartbeat bits)."""
    committed, accum = _living_masks(ct.read_store(SLOT_LIVING_BUILDERS))
    return bin((committed | accum) & 0xFFFE).count("1")


def _mark_builder_alive(ct: Controller, index: int) -> None:
    """Set builder index bit in both masks (spawn / same-turn presence)."""
    if index <= 0 or index > 15:
        return
    bit = 1 << index
    committed, accum = _living_masks(ct.read_store(SLOT_LIVING_BUILDERS))
    ct.write_store(SLOT_LIVING_BUILDERS, _pack_living_masks(committed | bit, accum | bit))


def _heartbeat_living(ct: Controller, index: int) -> None:
    """Metronome sole-writer: OR this index into the accumulating mask."""
    if index <= 0 or index > 15:
        return
    bit = 1 << index
    committed, accum = _living_masks(ct.read_store(SLOT_LIVING_BUILDERS))
    ct.write_store(SLOT_LIVING_BUILDERS, _pack_living_masks(committed, accum | bit))


def _commit_living_cycle(ct: Controller) -> None:
    """End of metronome cycle: committed = who heartbeated; clear accum."""
    _, accum = _living_masks(ct.read_store(SLOT_LIVING_BUILDERS))
    ct.write_store(SLOT_LIVING_BUILDERS, _pack_living_masks(accum, 0))

class HiveMind:
    def __init__(self):
        self.local_map = {}
        self.global_turn = 0
        self.my_index = 0
        self.map_id = 0
        self.map_name = "UNKNOWN"
        self.enemy_core_pos = None  # set on fingerprint from KNOWN_MAPS
        self._map_detected = False
        self._walls_loaded = False
        self.last_communicated_infra = {}

    def _detect_and_store_map(self, ct: Controller) -> None:
        if self._map_detected: return

        w = ct.get_map_width()
        h = ct.get_map_height()
        team = ct.get_team()
        pos = ct.get_position()

        fingerprint = (w, h, team, pos.x, pos.y)
        enemy_state = CoreState.UNKNOWN
        enemy_x = 0
        enemy_y = 0

        if fingerprint in KNOWN_MAPS:
            self.map_id, self.map_name, enemy_x, enemy_y = KNOWN_MAPS[fingerprint]
            enemy_state = CoreState.ASSUMED
            self.enemy_core_pos = Position(enemy_x, enemy_y)
        
        print(f"HIVEMIND FINGERPRINT: {fingerprint} -> Map ID: {self.map_id} ({self.map_name})")

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

    def _broadcast_vision(self, ct: Controller):
        pos = ct.get_position()
        grid_81 = 0
        intel_targets = []

        for dy in range(-4, 5):
            for dx in range(-4, 5):
                px = pos.x + dx
                py = pos.y + dy
                idx = (dy + 4) * 9 + (dx + 4)

                if (px, py) in self.local_map and not self.local_map[(px, py)].get("passable", True):
                    grid_81 |= (1 << idx)

                if (px, py) in self.local_map and "entity" in self.local_map[(px, py)]:
                    etype = self.local_map[(px, py)]["entity"]["type"]
                    eteam = self.local_map[(px, py)]["entity"].get("team")
                    if eteam != ct.get_team() and etype in (EntityType.CORE, EntityType.SENTINEL, EntityType.GUNNER, EntityType.HARVESTER):
                        if len(intel_targets) < 4:
                            intel_targets.append((px, py, etype))

        s10, s11, s12 = pack_vision_grid(pos.x, pos.y, grid_81)
        ct.write_store(10, s10)
        ct.write_store(11, s11)
        ct.write_store(12, s12)

        t0 = intel_targets[0] if len(intel_targets) > 0 else None
        t1 = intel_targets[1] if len(intel_targets) > 1 else None
        t2 = intel_targets[2] if len(intel_targets) > 2 else None
        t3 = intel_targets[3] if len(intel_targets) > 3 else None

        ct.write_store(8, pack_intel_slot(t0, t1))
        ct.write_store(9, pack_intel_slot(t2, t3))

    def _sync_vision(self, ct: Controller):
        s10 = ct.read_store(10)
        s11 = ct.read_store(11)
        s12 = ct.read_store(12)

        if s10 == 0 and s11 == 0 and s12 == 0:
            return

        bx, by, grid_81 = unpack_vision_grid(s10, s11, s12)
        w = ct.get_map_width()
        h = ct.get_map_height()

        for dy in range(9):
            for dx in range(9):
                idx = dy * 9 + dx
                is_blocked = bool((grid_81 >> idx) & 1)
                px = bx - 4 + dx
                py = by - 4 + dy

                if 0 <= px < w and 0 <= py < h:
                    if (px, py) not in self.local_map or self.local_map[(px, py)].get("last_seen_turn", -1) < self.global_turn:
                        if (px, py) not in self.local_map:
                            self.local_map[(px, py)] = {}
                        self.local_map[(px, py)]["passable"] = not is_blocked
                        self.local_map[(px, py)]["last_seen_turn"] = self.global_turn

        i8 = ct.read_store(8)
        i9 = ct.read_store(9)
        t0, t1 = unpack_intel_slot(i8)
        t2, t3 = unpack_intel_slot(i9)
        for t in (t0, t1, t2, t3):
            if t:
                tx, ty, ttype_int = t
                etype = INT_TO_ENTITY.get(ttype_int)
                if etype:
                    if (tx, ty) not in self.local_map:
                        self.local_map[(tx, ty)] = {}
                    self.local_map[(tx, ty)]["entity"] = {"type": etype}

    def spawn_builder(self, ct: Controller, pos) -> bool:
        """Helper to spawn a builder and instantly register it with the HiveMind registry."""
        if ct.can_spawn(pos):
            ct.spawn_builder(pos)
            total = ct.read_store(7)
            new_index = total + 1
            ct.write_store(7, new_index)
            _mark_builder_alive(ct, new_index)
            return True
        return False

    def tick(self, ct: Controller):
        """Must be called at the very top of every unit's run() method."""
        is_core = (ct.get_entity_type() == EntityType.CORE)

        # 1. Sync global turn natively from the engine
        self.global_turn = ct.get_current_round()

        # 2. Assign Builder Index on first turn alive
        if ct.get_entity_type() == EntityType.BUILDER_BOT and self.my_index == 0:
            self.my_index = ct.read_store(7)

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

        # 5. Always update personal vision first
        self._update_local_vision(ct)

        # 6. Execute Metronome & Sync Protocol
        if is_core and self.global_turn % 2 == 1:
            total_builders = ct.read_store(7)
            if total_builders > 0:
                current = ct.read_store(13)
                if current == 0:
                    current = 1
                else:
                    nxt = (current % total_builders) + 1
                    # Full cycle complete: commit heartbeats, drop dead indices.
                    if nxt == 1:
                        _commit_living_cycle(ct)
                    current = nxt
                ct.write_store(13, current)

        if not is_core and ct.get_entity_type() == EntityType.BUILDER_BOT:
            if self.my_index == ct.read_store(13):
                if self.global_turn % 2 == 0:
                    # Sole writer on even turns (core commits on odd) — heartbeat.
                    _heartbeat_living(ct, self.my_index)
                    self._broadcast_vision(ct)
                else:
                    self._broadcast_infrastructure(ct)

        if self.global_turn > 0:
            if self.global_turn % 2 == 1:
                self._sync_vision(ct)
            else:
                self._sync_infrastructure(ct)

    def _get_infra_type(self, entity_data: dict | None) -> int:
        if not entity_data: return 0
        etype = entity_data.get("type")
        if etype == EntityType.HARVESTER: return 9
        
        d = entity_data.get("direction", Direction.NORTH)
        offset = 0
        if d == Direction.EAST: offset = 1
        elif d == Direction.SOUTH: offset = 2
        elif d == Direction.WEST: offset = 3
        
        if etype == EntityType.CONVEYOR: return 1 + offset
        if etype == EntityType.SPLITTER: return 5 + offset
        return 0

    def _broadcast_infrastructure(self, ct: Controller):
        pos = ct.get_position()
        w = ct.get_map_width()
        h = ct.get_map_height()
        deltas = []
        
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                if dx*dx + dy*dy > 16: continue
                px = pos.x + dx
                py = pos.y + dy
                if 0 <= px < w and 0 <= py < h:
                    pos_tuple = (px, py)
                    ent = self.local_map.get(pos_tuple, {}).get("entity")
                    
                    if ent and ent.get("team") != ct.get_team():
                        ent = None
                        
                    current_type = self._get_infra_type(ent)
                    last_type = self.last_communicated_infra.get(pos_tuple, 0)
                    
                    if current_type != last_type:
                        deltas.append((px, py, current_type))
                        self.last_communicated_infra[pos_tuple] = current_type
                        
                    if len(deltas) >= 11:
                        break
            if len(deltas) >= 11:
                break
                
        s0, s1, s2, s3, s4 = pack_infra(deltas)
        ct.write_store(8, s0)
        ct.write_store(9, s1)
        ct.write_store(10, s2)
        ct.write_store(11, s3)
        ct.write_store(12, s4)

    def _sync_infrastructure(self, ct: Controller):
        s0 = ct.read_store(8)
        s1 = ct.read_store(9)
        s2 = ct.read_store(10)
        s3 = ct.read_store(11)
        s4 = ct.read_store(12)
        
        deltas = unpack_infra(s0, s1, s2, s3, s4)
        for x, y, t in deltas:
            pos_tuple = (x, y)
            if pos_tuple not in self.local_map:
                self.local_map[pos_tuple] = {}
            
            if t == 0:
                if "entity" in self.local_map[pos_tuple] and self.local_map[pos_tuple]["entity"].get("team") == ct.get_team():
                    del self.local_map[pos_tuple]["entity"]
            elif t == 9:
                self.local_map[pos_tuple]["entity"] = {"type": EntityType.HARVESTER, "team": ct.get_team()}
            elif 1 <= t <= 4:
                d = [Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST][t - 1]
                self.local_map[pos_tuple]["entity"] = {"type": EntityType.CONVEYOR, "team": ct.get_team(), "direction": d}
                self.local_map[pos_tuple]["passable"] = True
            elif 5 <= t <= 8:
                d = [Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST][t - 5]
                self.local_map[pos_tuple]["entity"] = {"type": EntityType.SPLITTER, "team": ct.get_team(), "direction": d}
                self.local_map[pos_tuple]["passable"] = True
