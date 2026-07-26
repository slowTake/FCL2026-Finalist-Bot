from fcode import Controller, EntityType
from enum import IntEnum
from features import KNOWN_MAPS, MAP_ID_TO_NAME, WALL_LOCATIONS
from vision import pack_vision_grid, unpack_vision_grid, pack_intel_slot, unpack_intel_slot

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


_INTEL_ENTITY_TO_ID = {
    EntityType.CORE: 1,
    EntityType.SENTINEL: 2,
    EntityType.GUNNER: 3,
    EntityType.HARVESTER: 4,
}
_INTEL_ID_TO_ENTITY = {value: key for key, value in _INTEL_ENTITY_TO_ID.items()}


class HiveMind:
    def __init__(self):
        self.local_map = {}
        self.global_turn = 0
        self.my_index = 0
        self.map_id = 0
        self.map_name = "UNKNOWN"
        self._map_detected = False
        self._terrain_seeded = False

    def _seed_static_terrain(self) -> None:
        if self.map_id not in WALL_LOCATIONS:
            return
        for x, y in WALL_LOCATIONS[self.map_id]:
            tile = self.local_map.setdefault((x, y), {})
            tile["passable"] = False
            tile["static"] = True

    def _ensure_terrain_seeded(self) -> None:
        if self._terrain_seeded or self.map_id == 0:
            return
        self._seed_static_terrain()
        self._terrain_seeded = True

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

        ct.write_store(14, self.map_id)

        packed = pack_cores(
            CoreState.KNOWN, pos.x, pos.y,
            enemy_state, enemy_x, enemy_y
        )
        ct.write_store(15, packed)
        self._map_detected = True
        self._ensure_terrain_seeded()

    def _update_local_vision(self, ct: Controller):
        pos = ct.get_position()
        w = ct.get_map_width()
        h = ct.get_map_height()

        entity_positions = {}
        for eid in ct.get_nearby_entities():
            epos = ct.get_position(eid)
            etype = ct.get_entity_type(eid)
            eteam = ct.get_team(eid)
            entity_positions[(epos.x, epos.y)] = {"type": etype, "team": eteam}

        for dy in range(-4, 5):
            for dx in range(-4, 5):
                if dx*dx + dy*dy <= 16:
                    px = pos.x + dx
                    py = pos.y + dy
                    if 0 <= px < w and 0 <= py < h:
                        if (px, py) not in self.local_map:
                            self.local_map[(px, py)] = {}
                        self.local_map[(px, py)]["last_seen_turn"] = self.global_turn
                        if (px, py) in entity_positions:
                            self.local_map[(px, py)]["passable"] = False
                            self.local_map[(px, py)]["entity"] = entity_positions[(px, py)]
                        else:
                            if not self.local_map[(px, py)].get("static"):
                                self.local_map[(px, py)]["passable"] = True
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

                entity = self.local_map.get((px, py), {}).get("entity")
                if entity:
                    etype = entity.get("type")
                    eteam = entity.get("team")
                    if etype in _INTEL_ENTITY_TO_ID and (
                        eteam is None or eteam != ct.get_team()
                    ):
                        if len(intel_targets) < 4:
                            intel_targets.append((px, py, _INTEL_ENTITY_TO_ID[etype]))

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
                tx, ty, ttype = t
                etype = _INTEL_ID_TO_ENTITY.get(ttype)
                if etype is None:
                    continue
                if (tx, ty) not in self.local_map:
                    self.local_map[(tx, ty)] = {}
                self.local_map[(tx, ty)]["entity"] = {"type": etype}

    def spawn_builder(self, ct: Controller, pos) -> bool:
        """Helper to spawn a builder and instantly register it with the HiveMind registry."""
        if ct.can_spawn(pos):
            ct.spawn_builder(pos)
            total = ct.read_store(7)
            ct.write_store(7, total + 1)
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
        if is_core and self.global_turn == 1:
            self._detect_and_store_map(ct)

        # 4. Read static map knowledge if we haven't
        if self.map_id == 0 and self.global_turn > 1:
            self.map_id = ct.read_store(14)
            self._ensure_terrain_seeded()

        # 5. Always update personal vision first
        self._update_local_vision(ct)

        # 6. Execute Metronome & Sync Protocol
        if self.global_turn % 2 == 0:
            if is_core:
                total_builders = ct.read_store(7)
                if total_builders > 0:
                    current = ct.read_store(13)
                    next_broadcaster = (current % total_builders) + 1
                    ct.write_store(13, next_broadcaster)
            elif ct.get_entity_type() == EntityType.BUILDER_BOT:
                if self.my_index == ct.read_store(13):
                    self._broadcast_vision(ct)
        else:
            self._sync_vision(ct)
