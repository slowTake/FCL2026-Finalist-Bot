from fcode import EntityType, Direction, Team

ENTITY_TO_INT = {
    EntityType.CORE: 1,
    EntityType.BUILDER_BOT: 2,
    EntityType.HARVESTER: 3,
    EntityType.CONVEYOR: 4,
    EntityType.SPLITTER: 5,
    EntityType.GUNNER: 6,
    EntityType.SENTINEL: 7,
    EntityType.LAUNCHER: 8,
    EntityType.BARRIER: 9,
}

INT_TO_ENTITY = {v: k for k, v in ENTITY_TO_INT.items()}


def encode_target_type(etype: EntityType | str | None, is_enemy: bool, direction: Direction = Direction.NORTH) -> int:
    if etype is None: return 63 # Special code for Deleted
    if etype == EntityType.CORE: return 2 if is_enemy else 1
    if etype == EntityType.GUNNER: return 4 if is_enemy else 3
    if etype == EntityType.SENTINEL: return 6 if is_enemy else 5
    if etype == EntityType.BUILDER_BOT: return 8 if is_enemy else 7
    if etype == EntityType.HARVESTER: return 10 if is_enemy else 9

    if etype == EntityType.LAUNCHER: return 28 if is_enemy else 27
    if etype == EntityType.BARRIER: return 30 if is_enemy else 29
    if etype == "WALL": return 31
    if etype == "ORE": return 32

    dir_offset = 0
    if direction == Direction.EAST: dir_offset = 1
    elif direction == Direction.SOUTH: dir_offset = 2
    elif direction == Direction.WEST: dir_offset = 3

    if etype == EntityType.CONVEYOR:
        base = 15 if is_enemy else 11
        return base + dir_offset
    if etype == EntityType.SPLITTER:
        base = 23 if is_enemy else 19
        return base + dir_offset

    return 0 # Unknown


def decode_target_type(val: int):
    if val == 0: return None
    if val == 63: return (None, False, Direction.NORTH) # Deleted

    if val == 1: return (EntityType.CORE, False, Direction.NORTH)
    if val == 2: return (EntityType.CORE, True, Direction.NORTH)
    if val == 3: return (EntityType.GUNNER, False, Direction.NORTH)
    if val == 4: return (EntityType.GUNNER, True, Direction.NORTH)
    if val == 5: return (EntityType.SENTINEL, False, Direction.NORTH)
    if val == 6: return (EntityType.SENTINEL, True, Direction.NORTH)
    if val == 7: return (EntityType.BUILDER_BOT, False, Direction.NORTH)
    if val == 8: return (EntityType.BUILDER_BOT, True, Direction.NORTH)
    if val == 9: return (EntityType.HARVESTER, False, Direction.NORTH)
    if val == 10: return (EntityType.HARVESTER, True, Direction.NORTH)

    if val == 27: return (EntityType.LAUNCHER, False, Direction.NORTH)
    if val == 28: return (EntityType.LAUNCHER, True, Direction.NORTH)
    if val == 29: return (EntityType.BARRIER, False, Direction.NORTH)
    if val == 30: return (EntityType.BARRIER, True, Direction.NORTH)
    if val == 31: return ("WALL", False, Direction.NORTH)
    if val == 32: return ("ORE", False, Direction.NORTH)

    if 11 <= val <= 14:
        d = [Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST][val - 11]
        return (EntityType.CONVEYOR, False, d)
    if 15 <= val <= 18:
        d = [Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST][val - 15]
        return (EntityType.CONVEYOR, True, d)
    if 19 <= val <= 22:
        d = [Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST][val - 19]
        return (EntityType.SPLITTER, False, d)
    if 23 <= val <= 26:
        d = [Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST][val - 23]
        return (EntityType.SPLITTER, True, d)

    return None


def pack_ledger_slot(target0: tuple | None, target1: tuple | None) -> int:
    def pack_target(t):
        if not t: return 0
        x, y, etype, is_enemy, d = t
        code = encode_target_type(etype, is_enemy, d)
        if code == 0: return 0
        return (x << 11) | (y << 6) | code

    val0 = pack_target(target0)
    val1 = pack_target(target1)

    return (val0 << 16) | val1


def unpack_ledger_slot(slot_val: int):
    def unpack_target(val):
        if val == 0: return None
        code = val & 0x3F
        decoded = decode_target_type(code)
        if not decoded: return None
        etype, is_enemy, d = decoded
        y = (val >> 6) & 0x1F
        x = (val >> 11) & 0x1F
        return (x, y, etype, is_enemy, d)

    t0 = unpack_target((slot_val >> 16) & 0xFFFF)
    t1 = unpack_target(slot_val & 0xFFFF)

    return t0, t1
