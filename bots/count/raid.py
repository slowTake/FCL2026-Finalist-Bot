"""Where to park a sentinel so it can shoot the enemy core.

The geometry does most of the deciding. A sentinel's line ignores obstacles, so
there is no line-of-sight reason to get close — closing the distance buys
nothing and costs everything. Reach is r**2 <= 32, which is 5 tiles orthogonally
and 4 diagonally; the diagonal is the better emplacement despite the smaller k,
because 4 diagonal is Manhattan 8 from the target while 5 orthogonal is only
Manhattan 5. Farther out means outside gunner reach (r**2 = 13, ~3.6 tiles), a
longer walk for any enemy builder coming to break it, and a shallower, safer
approach for our own raider. So: maximum standoff, never minimum.

Facing is permanent — `rotate()` is gunner-only, so a sentinel points wherever it
was built forever. Tile and direction are therefore one decision, not two.

Selection runs on the core. Measured headroom is the reason: builders sit at p90
9.4ms against a 10ms limit and already drop turns, while the core peaks at 1.6ms.
The core can afford a search a builder cannot, and the answer is one tile, which
fits in a store slot.
"""

from __future__ import annotations

from fcode import Controller, Direction, EntityType, Position

import pathfindingHelpers as ph

# Slots 1-3 are the only ones not owned by HiveMind (see main.py).
SLOT_RAID_TARGET = 1
SLOT_RAIDER_INDEX = 2

# Packing order matters only in that pack and unpack agree.
_DIRS = (
    Direction.NORTH, Direction.NORTHEAST, Direction.EAST, Direction.SOUTHEAST,
    Direction.SOUTH, Direction.SOUTHWEST, Direction.WEST, Direction.NORTHWEST,
)
_DIR_INDEX = {d: i for i, d in enumerate(_DIRS)}


def packTarget(pos: Position, facing: Direction) -> int:
    """valid(1) | dir(3) | x(7) | y(7). Map is 30x30 at most, so 7 bits is ample."""
    return (1 << 17) | (_DIR_INDEX[facing] << 14) | (pos.x << 7) | pos.y


def unpackTarget(val: int):
    """Returns (Position, Direction) or None if nothing has been published."""
    if not (val >> 17) & 1:
        return None
    return Position((val >> 7) & 0x7F, val & 0x7F), _DIRS[(val >> 14) & 0x7]


def enemyCorePos(bot, ct: Controller) -> Position | None:
    """Fingerprinted maps hand this over at turn 0; otherwise it stays unknown."""
    hm = bot.hivemind
    pos = getattr(hm, "enemy_core_pos", None)
    if pos is not None:
        return pos
    return None


def _buildable(ct: Controller, hm, pos: Position, enemyFootprint) -> bool:
    """Can a sentinel stand here, and can a raider reach a tile to build it from.

    The second half is not fussiness: `build_sentinel` requires the builder to be
    on an orthogonally adjacent tile, so an emplacement ringed by walls is one the
    raider can walk to and then never finish.
    """
    if not ph.inBounds(ct, pos):
        return False
    if (pos.x, pos.y) in enemyFootprint:
        return False
    if ph.isImpassable(ct, hm, pos):
        return False
    t = ph.readTile(ct, hm, pos)
    if t.etype is not None or t.ore:
        return False  # occupied, or ore we should not be squatting on
    for n in ph.orthogonalTiles(pos):
        if not ph.isImpassable(ct, hm, n):
            return True
    return False


def _reachOffsets(ct: Controller):
    """Offsets from a sentinel to every tile it can hit, per facing.

    Read out of the engine rather than derived. r**2 <= 32 works out to 5 tiles
    orthogonally and 4 diagonally, and hand-deriving that is exactly the mistake
    this function exists to stop: ranking candidates by standoff without knowing
    the real reach puts the out-of-range ones first.

    Sampled from mid-map because `get_attackable_tiles_from` returns only in-bounds
    tiles, so a reference near an edge comes back clipped.
    """
    ref = Position(ct.get_map_width() // 2, ct.get_map_height() // 2)
    out = {}
    for d in _DIRS:
        try:
            tiles = ct.get_attackable_tiles_from(ref, d, EntityType.SENTINEL)
        except Exception:
            continue
        out[d] = [(t.x - ref.x, t.y - ref.y) for t in tiles]
    return out


def _candidates(ct: Controller, hm, enemyCore: Position):
    """Every tile that could hold a sentinel aimed at the enemy core, best first.

    Built by inverting the attack pattern: if facing `d` hits everything at
    `offset` from the turret, then a turret at `aim - offset` facing `d` hits
    `aim`. Eight engine calls total, and every candidate is in range by
    construction — no verification pass, and nothing to cut off early.
    """
    footprint = ph.coreFootprint(enemyCore)
    enemySet = {(p.x, p.y) for p in footprint}

    reach = _reachOffsets(ct)  # eight engine calls, hoisted out of the loops
    out = []
    seen = set()
    for aim in footprint:
        for d, offsets in reach.items():
            for ox, oy in offsets:
                e = Position(aim.x - ox, aim.y - oy)
                key = (e.x, e.y, d)
                if key in seen:
                    continue
                seen.add(key)
                if not _buildable(ct, hm, e, enemySet):
                    continue
                out.append((e, d, aim, ph.manhattanToCore(e, enemyCore)))

    # Max standoff first — the whole point of the module.
    out.sort(key=lambda c: -c[3])
    return out


def chooseEmplacement(bot, ct: Controller, enemyCore: Position):
    """Farthest buildable tile from which a sentinel still reaches the enemy core,
    with the facing it has to be built pointing. None if there is no such tile."""
    threat = getattr(bot, "_ctxThreat", None) or set()
    for e, d, _aim, _standoff in _candidates(ct, bot.hivemind, enemyCore):
        if (e.x, e.y) in threat:
            continue  # a sentinel parked inside a known firing line is scenery
        return e, d
    return None


def fallbackEmplacement(bot, ct: Controller, primarySite: Position):
    """A nearby alternative when `primarySite` turns out to be unbuildable by
    the time the raider gets there — typically the core's ledger hasn't yet
    learned the site was already claimed. Only the raider's own orthogonal
    neighbours: a fresh board search is the core's job, not a stuck
    builder's. Standoff is not preferred here — closer is fine, getting a
    sentinel down beats waiting on the core to notice and republish.

    No threat filtering: chooseEmplacement's own threat check is a no-op on
    the core today (see docs/mistakes.md), so filtering this path against it
    would reject the safer local option while the unvetted remote one still
    gets built.
    """
    enemyCore = enemyCorePos(bot, ct)
    if enemyCore is None:
        return None

    hm = bot.hivemind
    footprint = ph.coreFootprint(enemyCore)
    enemySet = {(p.x, p.y) for p in footprint}
    reach = {d: set(offsets) for d, offsets in _reachOffsets(ct).items()}
    pos = ct.get_position()

    for pad in ph.orthogonalTiles(pos):
        if (pad.x, pad.y) == (primarySite.x, primarySite.y):
            continue
        if not _buildable(ct, hm, pad, enemySet):
            continue
        # Build legality doesn't depend on facing, so one probe covers all eight.
        if not ct.can_build_sentinel(pad, _DIRS[0]):
            continue
        for d, hits in reach.items():
            if any((aim.x - pad.x, aim.y - pad.y) in hits for aim in footprint):
                return pad, d
    return None


def publishTarget(bot, ct: Controller) -> None:
    """Core-side: pick an emplacement once and leave it in the store.

    Recomputed only while nothing is published. Re-picking every turn would hand
    the raider a different destination each round and it would never arrive.
    """
    if ct.read_store(SLOT_RAID_TARGET):
        return
    enemyCore = enemyCorePos(bot, ct)
    if enemyCore is None:
        return
    choice = chooseEmplacement(bot, ct, enemyCore)
    if choice is None:
        return
    pos, facing = choice
    ct.write_store(SLOT_RAID_TARGET, packTarget(pos, facing))


def readTarget(ct: Controller):
    return unpackTarget(ct.read_store(SLOT_RAID_TARGET))
