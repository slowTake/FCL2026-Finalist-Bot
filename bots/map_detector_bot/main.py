from fcode import Controller, EntityType, Team
from enum import IntEnum
from .features import ORE_LOCATIONS, WALL_LOCATIONS, KNOWN_MAPS, MAP_ID_TO_NAME

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
    ally = (ally_bits >> 14, (ally_bits >> 7) & 0x7F, ally_bits & 0x7F)
    enemy = (enemy_bits >> 14, (enemy_bits >> 7) & 0x7F, enemy_bits & 0x7F)
    return ally, enemy

class Player:
    def __init__(self):
        self.initialized = False

    def _detect_and_store_map(self, ct: Controller) -> None:
        w = ct.get_map_width()
        h = ct.get_map_height()
        team = ct.get_team()
        pos = ct.get_position()

        fingerprint = (w, h, team, pos.x, pos.y)

        map_id = 0
        enemy_state = CoreState.UNKNOWN
        enemy_x = 0
        enemy_y = 0

        if fingerprint in KNOWN_MAPS:
            map_id, map_name, enemy_x, enemy_y = KNOWN_MAPS[fingerprint]
            enemy_state = CoreState.ASSUMED

        ct.write_store(14, map_id)

        packed = pack_cores(
            CoreState.KNOWN, pos.x, pos.y,
            enemy_state, enemy_x, enemy_y
        )
        ct.write_store(15, packed)

        if not self.initialized:
            ally, enemy = unpack_cores(packed)
            print(f"DETECTED MAP {MAP_ID_TO_NAME.get(map_id, 'UNKNOWN')} (ID: {map_id}) | ALLY: state={ally[0].name}, pos={ally[1]},{ally[2]} | ENEMY: state={enemy[0].name}, pos={enemy[1]},{enemy[2]}")
            self.initialized = True

    def run(self, ct: Controller) -> None:
        if ct.get_entity_type() == EntityType.CORE:
            self._detect_and_store_map(ct)
