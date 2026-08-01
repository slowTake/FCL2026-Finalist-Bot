from fcode import EntityType

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

def pack_vision_grid(x: int, y: int, grid_81: int) -> tuple[int, int, int]:
    """
    Packs X (5 bits), Y (5 bits), and an 81-bit grid into three 32-bit slots.
    Slot 10: 32 bits
    Slot 11: 32 bits
    Slot 12: 27 bits
    """
    payload = (x << 86) | (y << 81) | (grid_81 & ((1 << 81) - 1))
    
    slot_10 = (payload >> 59) & 0xFFFFFFFF
    slot_11 = (payload >> 27) & 0xFFFFFFFF
    slot_12 = payload & 0x7FFFFFF  
    
    return slot_10, slot_11, slot_12

def unpack_vision_grid(slot_10: int, slot_11: int, slot_12: int) -> tuple[int, int, int]:
    """Unpacks the 3 slots back into X, Y, and the 81-bit grid."""
    payload = ((slot_10 & 0xFFFFFFFF) << 59) | ((slot_11 & 0xFFFFFFFF) << 27) | (slot_12 & 0x7FFFFFF)
    
    x = (payload >> 86) & 0x1F
    y = (payload >> 81) & 0x1F
    grid_81 = payload & ((1 << 81) - 1)
    
    return x, y, grid_81

def pack_intel_slot(target0: tuple[int, int, int] | None, target1: tuple[int, int, int] | None) -> int:
    """
    Packs two targets into a single 32-bit slot.
    Each target: X (5 bits), Y (5 bits), Type (4 bits) = 14 bits.
    Total used per slot: 28 bits.
    """
    def pack_target(t):
        if not t: return 0
        etype_int = ENTITY_TO_INT.get(t[2], 0)
        return (t[0] << 9) | (t[1] << 4) | (etype_int & 0xF)
        
    val0 = pack_target(target0)
    val1 = pack_target(target1)
    
    return (val0 << 14) | val1

def unpack_intel_slot(slot_val: int):
    """Unpacks a slot back into two target tuples (x, y, type) or None."""
    def unpack_target(val):
        if val == 0: return None
        return ((val >> 9) & 0x1F, (val >> 4) & 0x1F, val & 0xF)
        
    t0 = unpack_target((slot_val >> 14) & 0x3FFF)
    t1 = unpack_target(slot_val & 0x3FFF)
    return t0, t1

def pack_infra(deltas: list[tuple[int, int, int]]) -> tuple[int, int, int, int, int]:
    val = 0
    for i in range(11):
        if i < len(deltas):
            x, y, t = deltas[i]
        else:
            x, y, t = 0, 0, 15
        chunk = (x & 31) | ((y & 31) << 5) | ((t & 15) << 10)
        val |= (chunk << (i * 14))
    
    s0 = (val >> 0) & 0xFFFFFFFF
    s1 = (val >> 32) & 0xFFFFFFFF
    s2 = (val >> 64) & 0xFFFFFFFF
    s3 = (val >> 96) & 0xFFFFFFFF
    s4 = (val >> 128) & 0xFFFFFFFF
    return s0, s1, s2, s3, s4

def unpack_infra(s0: int, s1: int, s2: int, s3: int, s4: int) -> list[tuple[int, int, int]]:
    val = s0 | (s1 << 32) | (s2 << 64) | (s3 << 96) | (s4 << 128)
    deltas = []
    for i in range(11):
        chunk = (val >> (i * 14)) & 0x3FFF
        t = (chunk >> 10) & 15
        if t != 15:
            x = chunk & 31
            y = (chunk >> 5) & 31
            deltas.append((x, y, t))
    return deltas
