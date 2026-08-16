"""Shared tile geometry and entity predicates.

The lowest layer of baron: imports `fcode` and nothing else from the bot, so any
module may import it without risking a cycle. Everything here was previously
copy-pasted per module — `_coreFootprint` existed five times, chebyshev-to-core
four times under three names, `_nearCore` and `DEFEND_HOME_CHEBYSHEV` twice each
— which meant a fix or a tuning change had to be mirrored by hand and could
silently drift between copies.
"""

from __future__ import annotations

import math

from fcode import Controller, Direction, EntityType, Position

# Chebyshev radius around the ally core that counts as "home": the guardian
# leash, the near-core gate on defGun/defendHome/defSentinel, and the ore limit
# under nearCoreEconomy. Single definition — logistics.py and defence.py each
# used to declare their own `DEFEND_HOME_CHEBYSHEV = 4`.
DEFEND_HOME_CHEBYSHEV = 4

# Buildings a builder can walk over and a relay can join.
BELT_TYPES = (EntityType.CONVEYOR, EntityType.SPLITTER)

# Enemy buildings that shoot or throw. Not a threat *set* for every purpose:
# builder.py's _ENEMY_THREAT_TYPES adds BUILDER_BOT on top of this.
TURRET_TYPES = (EntityType.GUNNER, EntityType.SENTINEL, EntityType.LAUNCHER)

# Not imported from pathing.CARDINALS/inBounds: pathing.py imports *from* this
# module (see the module docstring), so pulling pathing's helpers in here would
# create the exact cycle that ordering forbids. Both are a handful of lines that
# never change — a cheap price to stay the bottom of the import graph.
_CARDINALS = (Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST)


def _inBounds(ct: Controller, pos: Position) -> bool:
    return 0 <= pos.x < ct.get_map_width() and 0 <= pos.y < ct.get_map_height()


_coreFootprintCache: dict[tuple[int, int], list[Position]] = {}


def coreFootprint(core_pos: Position) -> list[Position]:
    """The four tiles of a 2x2 core, anchored at its min-x/min-y corner.

    Memoized on (x, y): `chebyshevToCore` is the A* heuristic in `astarFromSeeds`
    (pathing.py), evaluated once per node popped off the frontier — a single
    search near `PATH_MAX_ITERS` (800) calls it, and therefore this, over a
    thousand times. Core position is fixed for the whole game once known (ally
    and enemy — at most 2 distinct keys), so caching it is free and can never
    go stale.

    Measured via cProfile on a live mirror match: this cache is a small,
    correct win, but not the fix for the guardian TLE investigation it was
    added during — a single `astarFromSeeds` call was measured costing
    15-19ms outright (the search itself, not this list churn), already over
    the ~10ms per-turn budget on its own with no stacking of multiple
    searches required. See [[guardian-tle-danglingbeltends-scans]] memory:
    that scan-count hypothesis was measured and ruled out (sub-millisecond
    even at max observed map size); the real cost sits inside a single A*
    search and is still unresolved.
    """
    key = (core_pos.x, core_pos.y)
    cached = _coreFootprintCache.get(key)
    if cached is None:
        cached = [
            core_pos,
            Position(core_pos.x + 1, core_pos.y),
            Position(core_pos.x, core_pos.y + 1),
            Position(core_pos.x + 1, core_pos.y + 1),
        ]
        _coreFootprintCache[key] = cached
    return cached


def chebyshevToCore(pos: Position, core_pos: Position) -> int:
    """Chebyshev distance from pos to the *nearest* core footprint tile.

    Measuring to the anchor alone would read one tile too far from the east and
    south sides of the 2x2, which is why every leash check uses this instead.
    """
    return min(
        max(abs(pos.x - t.x), abs(pos.y - t.y)) for t in coreFootprint(core_pos)
    )


def manhattanToCore(pos: Position, core_pos: Position) -> int:
    """Manhattan distance from pos to the *nearest* core footprint tile.

    Measuring to the anchor alone would read one tile too far from the east and
    south sides of the 2x2 — the same pitfall chebyshevToCore exists to avoid,
    but the manhattan-distance ore pickers (logistics_helpers._oreConvenienceKey,
    pickMaintainOre, _pickRecheckOre; offense._oreCloserToAlly and denyOre)
    never got the equivalent treatment. On a point-mirrored map
    this raw single-tile bias flips which of two mirror-equivalent ore nodes
    reads as "closer" depending on which corner of its 2x2 a team's core anchor
    happens to occupy — the mirror match then sends one team's builders into a
    contested node the other team ignores.
    """
    return min(
        abs(pos.x - t.x) + abs(pos.y - t.y) for t in coreFootprint(core_pos)
    )


def angleBucket(
    core_pos: Position,
    enemy_core_pos: Position,
    tile: Position,
    n_buckets: int,
) -> int:
    """Bucket `tile`'s bearing from `core_pos` into `n_buckets` equal wedges.

    Measured against the core->enemy-core axis rather than compass/absolute
    coordinates, so the same bucket means the same thing for both teams on a
    point-mirrored map.
    """
    if n_buckets <= 1:
        return 0
    axis = math.atan2(
        enemy_core_pos.y - core_pos.y, enemy_core_pos.x - core_pos.x
    )
    theta = math.atan2(tile.y - core_pos.y, tile.x - core_pos.x) - axis
    theta = (theta + math.pi) % (2 * math.pi) - math.pi
    width = 2 * math.pi / n_buckets
    return int((theta + math.pi) // width) % n_buckets


def nearCore(bot, ct: Controller, radius: int | None = None) -> bool:
    """True while this unit is inside `radius` of home. False if home is unknown.

    `radius` defaults to the static `DEFEND_HOME_CHEBYSHEV`. Callers that mean
    "inside the guardian leash" pass `logistics_helpers.guardianLeash(ct)`, which varies
    with whether the core is currently threatened; callers that mean "am I home
    enough to act at all" leave it alone. Taking it as a parameter keeps this
    module free of a `logistics` import (logistics already imports geometry).
    """
    if bot.corePos is None:
        return False
    if radius is None:
        radius = DEFEND_HOME_CHEBYSHEV
    return chebyshevToCore(ct.get_position(), bot.corePos) <= radius


def isAdjacent(a: Position, b: Position) -> bool:
    """Cardinally adjacent — the reach of every builder action.

    Strictly `manhattan == 1`, so a unit is never adjacent to its own tile.
    Build, attack, heal and destroy all require exactly this relationship
    (diagonals do not count), which is why the check appears so often.
    """
    return abs(a.x - b.x) + abs(a.y - b.y) == 1


def tileEntity(
    ct: Controller, tile: Position, *, local_map: dict | None = None
) -> dict | None:
    """What is on `tile`: ground truth if visible, else the HiveMind ledger.

    Returns a `{"type": EntityType, "team": Team}` dict — the same shape the
    ledger stores, so both branches read identically at the call site — or None
    for "nothing there, as far as we know".

    Buildings only. Builder bots are deliberately not reported: they are mobile,
    so a single fly-by sighting must not be remembered as occupancy, and every
    caller of this predicate is asking about something immovable (a harvester, a
    barrier, a belt, a turret sealing a lane).

    Fog is trusted rather than treated as empty: an unseen tile falls back to
    whatever the team last observed there. Callers that must not act on stale
    memory age it themselves (see `_oreLedgerStale` / ORE_BLOCK_MEMORY_TURNS).
    """
    if ct.is_in_vision(tile):
        bid = ct.get_tile_building_id(tile)
        if bid is None:
            return None
        return {"type": ct.get_entity_type(bid), "team": ct.get_team(bid)}
    if local_map is None:
        return None
    cell = local_map.get((tile.x, tile.y))
    if not cell:
        return None
    return cell.get("entity") or None


def beltNeighbors(
    ct: Controller, tile: Position, *, local_map: dict | None = None
) -> list[tuple[Position, dict]]:
    """(neighbor, entity) for every cardinally-adjacent CONVEYOR/SPLITTER, any team.

    Ground truth via `tileEntity`, so it inherits the same vision/ledger fallback.
    Team-agnostic on purpose — callers that care about ownership filter the
    returned entities themselves; that is a business rule, not tile geometry.

    Unlike `tileEntity`'s other call sites in this codebase, `tile` here is not
    already known to be in-bounds (it can be any harvester tile from the
    ledger) — a neighbor one step off the map edge would otherwise raise
    `GameError` deep inside `ct.get_tile_building_id`/`is_in_vision`, so bounds
    are checked here rather than pushed onto every caller.
    """
    out: list[tuple[Position, dict]] = []
    for d in _CARDINALS:
        n = tile.add(d)
        if not _inBounds(ct, n):
            continue
        ent = tileEntity(ct, n, local_map=local_map)
        if ent is not None and ent.get("type") in BELT_TYPES:
            out.append((n, ent))
    return out


def harvesterClaimedByAlly(
    ct: Controller, tile: Position, *, local_map: dict | None = None
) -> bool:
    """True if an ally CONVEYOR/SPLITTER already sits cardinally adjacent to tile.

    The derived "we're already stealing/relaying this harvester" signal — no
    store slot exists for it (all 16 are spoken for; see main.STEAL_ENEMY_HARVESTERS
    and CONTEXT.md's StealEnemyHarvesters section), and this is exactly the
    footprint `relayToCore` naturally leaves the moment a default commits to
    relaying from it: its first belt tile lands cardinally adjacent to the
    harvester. No new state, just a read through the existing ledger.
    """
    me = ct.get_team()
    for _n, ent in beltNeighbors(ct, tile, local_map=local_map):
        if ent.get("team") == me:
            return True
    return False
