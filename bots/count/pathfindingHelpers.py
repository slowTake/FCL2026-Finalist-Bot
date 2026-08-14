"""Tile checks, blocker classification, network connectivity, and the cost table.

All game knowledge for pathing lives here; pathing.py stays a bare search.
"""

from __future__ import annotations

from typing import NamedTuple

from fcode import Controller, Direction, EntityType, Environment, Position

BELT_TYPES = (EntityType.CONVEYOR, EntityType.SPLITTER)
TURRET_TYPES = (EntityType.GUNNER, EntityType.SENTINEL, EntityType.LAUNCHER)

CARDINALS = (Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST)
_ORTHO = ((0, -1), (1, 0), (0, 1), (-1, 0))

# --- Cost table -------------------------------------------------------------
# Hostile weights are anchored to turns-to-clear: a builder attack is 2 Ti for
# 2 damage, one per turn, so a 20 HP belt is ten builder-turns and a 30 HP
# barrier is fifteen. Tuned by play, not by feel.

BASE = 10
NETWORK = 1           # riding our own rails is near-free, but never 0 —
                      # a 0 would drop MIN_STEP_COST to 0 and force Dijkstra
DEFENDED = 5          # discount near our own defences (nothing defensive yet)
CHOKE = 50
ENEMY_FIRE = 500
ENEMY_BELT = 50
ENEMY_BARRIER = 75
ENEMY_HARVESTER = 75
ENEMY_TURRET = 500
ALLY_BUILD = 300      # ours and worth keeping: path around, don't bulldoze
UNIT = 8              # a builder in the way moves on its own

# Cheapest step any profile can produce. The A* heuristic is scaled by this so
# it stays admissible; deriving it here means changing a weight above can never
# silently break the search.
MIN_STEP_COST = min(NETWORK, BASE - DEFENDED)

_ENEMY_BUILD_COST = {
    EntityType.CONVEYOR: ENEMY_BELT,
    EntityType.SPLITTER: ENEMY_BELT,
    EntityType.BARRIER: ENEMY_BARRIER,
    EntityType.HARVESTER: ENEMY_HARVESTER,
    EntityType.GUNNER: ENEMY_TURRET,
    EntityType.SENTINEL: ENEMY_TURRET,
    EntityType.LAUNCHER: ENEMY_TURRET,
}

_MAX_THREAT_TURRETS = 8


class Tile(NamedTuple):
    known: bool
    wall: bool
    ore: bool
    etype: EntityType | None      # building on the tile, if any
    enemy: bool                   # that building belongs to the enemy
    facing: Direction | None      # belts and turrets only
    builder: bool                 # a builder bot stands here
    builderEnemy: bool


_UNKNOWN = Tile(False, False, False, None, False, None, False, False)


# --- Tile reads -------------------------------------------------------------


def inBounds(ct: Controller, pos: Position) -> bool:
    return 0 <= pos.x < ct.get_map_width() and 0 <= pos.y < ct.get_map_height()


def readTile(ct: Controller, hm, pos: Position) -> Tile:
    """Own eyes first, hivemind ledger second. Never trust the ledger over vision.

    Memoised for the current round. A builder gets 10ms and the cost function reads
    every expanded node (plus its four neighbours for the chokepoint test), so
    re-deriving a tile from the Controller each time is what puts 30x30 maps over
    the limit. The cache lives on the hivemind instance because that is the one
    per-unit object every caller already has — no edit to the shared module.
    """
    if not inBounds(ct, pos):
        return _UNKNOWN

    rnd = ct.get_current_round()
    cache = getattr(hm, "_tileCache", None)
    if cache is None or hm._tileCacheRound != rnd:
        cache = hm._tileCache = {}
        hm._tileCacheRound = rnd

    key = (pos.x, pos.y)
    hit = cache.get(key)
    if hit is not None:
        return hit

    if ct.is_in_vision(pos):
        tile = _readVisible(ct, pos)
    else:
        tile = _readLedger(hm, pos, ct.get_team())
    cache[key] = tile
    return tile


def invalidateTile(hm, pos: Position) -> None:
    """Drop a memoised tile after we change it ourselves."""
    cache = getattr(hm, "_tileCache", None)
    if cache is not None:
        cache.pop((pos.x, pos.y), None)


def _readVisible(ct: Controller, pos: Position) -> Tile:
    env = ct.get_tile_env(pos)
    if env == Environment.WALL:
        return Tile(True, True, False, None, False, None, False, False)

    myTeam = ct.get_team()
    etype = None
    enemy = False
    facing = None
    bid = ct.get_tile_building_id(pos)
    if bid is not None:
        etype = ct.get_entity_type(bid)
        enemy = ct.get_team(bid) != myTeam
        if etype in BELT_TYPES or etype in TURRET_TYPES:
            try:
                facing = ct.get_direction(bid)
            except Exception:
                facing = None

    builder = False
    builderEnemy = False
    uid = ct.get_tile_builder_bot_id(pos)
    if uid is not None:
        builder = True
        builderEnemy = ct.get_team(uid) != myTeam

    return Tile(
        True, False, env == Environment.ORE_TITANIUM,
        etype, enemy, facing, builder, builderEnemy,
    )


def _readLedger(hm, pos: Position, myTeam) -> Tile:
    entry = hm.local_map.get((pos.x, pos.y))
    if entry is None:
        return _UNKNOWN

    ore = bool(entry.get("ore"))
    ent = entry.get("entity")
    if ent is None:
        return Tile(True, entry.get("passable") is False, ore,
                    None, False, None, False, False)

    etype = ent.get("type")
    # vision_v2 encodes terrain as pseudo-entities before the map is fingerprinted
    if etype == "WALL":
        return Tile(True, True, False, None, False, None, False, False)
    if etype == "ORE":
        return Tile(True, False, True, None, False, None, False, False)

    enemy = ent.get("team") != myTeam
    if etype == EntityType.BUILDER_BOT:
        return Tile(True, False, ore, None, False, None, True, enemy)
    return Tile(True, False, ore, etype, enemy, ent.get("direction"), False, False)


def isWall(ct: Controller, hm, pos: Position) -> bool:
    return readTile(ct, hm, pos).wall


def isOre(ct: Controller, hm, pos: Position) -> bool:
    return readTile(ct, hm, pos).ore


def isImpassable(ct: Controller, hm, pos: Position) -> bool:
    """The only hard blocks. Everything else is priced by tileCost, not forbidden."""
    if not inBounds(ct, pos):
        return True
    t = readTile(ct, hm, pos)
    if t.wall:
        return True
    if t.etype == EntityType.HARVESTER and not t.enemy:
        return True  # ours, sits on ore, never worth a shortcut
    if t.etype == EntityType.CORE:
        # Either team's. is_tile_passable claims the allied core is standable but
        # the engine refuses the move, so pricing it as traversable made every
        # route out of a cramped spawn plan straight through the core and
        # deadlock there permanently.
        return True
    return False


def canStandNow(ct: Controller, pos: Position) -> bool:
    """Can a builder legally occupy this tile *this turn*. For the move step only —
    planning uses isImpassable + tileCost so A* may route through what it intends
    to clear."""
    if not inBounds(ct, pos):
        return False
    return ct.is_tile_passable(pos)


def beltPassable(ct: Controller, hm, pos: Position) -> bool:
    """A belt can be laid here, or one already is."""
    if isImpassable(ct, hm, pos):
        return False
    t = readTile(ct, hm, pos)
    if t.etype is None:
        return not t.ore  # keep ore tiles free for harvesters
    return t.etype in BELT_TYPES


# --- Adjacency --------------------------------------------------------------


def orthogonalTiles(pos: Position):
    for dx, dy in _ORTHO:
        yield Position(pos.x + dx, pos.y + dy)


def adjacentTiles(pos: Position):
    for d in Direction:
        if d != Direction.CENTRE:
            yield pos.add(d)


class Blocker(NamedTuple):
    pos: Position
    etype: EntityType | None   # None means a builder bot
    enemy: bool
    unit: bool                 # builder bot: it will move on its own


def adjacentBlockers(ct: Controller, hm, pos: Position) -> list[Blocker]:
    """Everything orthogonally adjacent that a builder would have to get past.
    The one place blocker classification lives; callers branch on the result."""
    out = []
    for n in orthogonalTiles(pos):
        if not inBounds(ct, n):
            continue
        t = readTile(ct, hm, n)
        if t.wall:
            out.append(Blocker(n, None, False, False))
        elif t.builder:
            out.append(Blocker(n, None, t.builderEnemy, True))
        elif t.etype is not None:
            out.append(Blocker(n, t.etype, t.enemy, False))
    return out


def adjacentBuildable(ct: Controller, hm, pos: Position) -> list[Position]:
    """Orthogonal neighbours we may build on. Build actions are never diagonal
    and never the builder's own tile."""
    return [
        n for n in orthogonalTiles(pos)
        if inBounds(ct, n) and ct.is_tile_empty(n)
    ]


# --- Network ----------------------------------------------------------------


def coreFootprint(corePos: Position) -> list[Position]:
    return [
        Position(corePos.x, corePos.y),
        Position(corePos.x + 1, corePos.y),
        Position(corePos.x, corePos.y + 1),
        Position(corePos.x + 1, corePos.y + 1),
    ]


def beltFacing(a: Position, b: Position) -> Direction | None:
    """Cardinal direction from a to b. None if they aren't orthogonally adjacent."""
    dx, dy = b.x - a.x, b.y - a.y
    if dx == 0 and dy == -1:
        return Direction.NORTH
    if dx == 1 and dy == 0:
        return Direction.EAST
    if dx == 0 and dy == 1:
        return Direction.SOUTH
    if dx == -1 and dy == 0:
        return Direction.WEST
    return None


def buildNetwork(ct: Controller, hm, corePos: Position) -> set[tuple[int, int]]:
    """Tiles that provably flow into the core: the footprint, plus every ally belt
    whose facing chains into it.

    Grown outward from the core rather than by walking each belt's chain inward —
    same answer, but connectivity holds by construction, so no belt ever needs
    re-proving. This is the whole of the tree invariant.
    """
    connected = {(p.x, p.y) for p in coreFootprint(corePos)}

    belts: dict[tuple[int, int], tuple[int, int]] = {}
    myTeam = ct.get_team()

    for bid in ct.get_nearby_buildings():
        if ct.get_team(bid) != myTeam:
            continue
        if ct.get_entity_type(bid) not in BELT_TYPES:
            continue
        bp = ct.get_position(bid)
        try:
            d = ct.get_direction(bid)
        except Exception:
            continue
        dx, dy = d.delta()
        belts[(bp.x, bp.y)] = (bp.x + dx, bp.y + dy)

    for (x, y), entry in hm.local_map.items():
        if (x, y) in belts:
            continue
        ent = entry.get("entity")
        if not ent or ent.get("type") not in BELT_TYPES:
            continue
        if ent.get("team") != myTeam:
            continue
        d = ent.get("direction")
        if d is None:
            continue
        dx, dy = d.delta()
        belts[(x, y)] = (x + dx, y + dy)

    # Flood outward along reversed edges: O(belts), where repeated passes over the
    # belt table were O(chain length x belts) and showed up as a real TLE on 30x30.
    feeders: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for tile, target in belts.items():
        feeders.setdefault(target, []).append(tile)

    queue = list(connected)
    while queue:
        cur = queue.pop()
        for f in feeders.get(cur, ()):
            if f not in connected:
                connected.add(f)
                queue.append(f)

    return connected


# --- Cost -------------------------------------------------------------------


_MISS = object()


class CostContext:
    """Everything expensive, computed once per unit-turn instead of per expanded node."""

    __slots__ = ("ct", "hm", "profile", "network", "threat", "avoid", "costs")

    def __init__(self, ct: Controller, hm, corePos: Position, profile: str,
                 network=None, threat=None):
        self.ct = ct
        self.hm = hm
        self.profile = profile
        if network is None:
            network = buildNetwork(ct, hm, corePos) if corePos is not None else set()
        self.network = network
        self.threat = _threatTiles(ct, hm) if threat is None else threat
        self.avoid: set[tuple[int, int]] = set()
        self.costs: dict[tuple[int, int], int | None] = {}

    def isNetworkMember(self, pos: Position) -> bool:
        return (pos.x, pos.y) in self.network

    def penalise(self, pos: Position) -> None:
        """Mark a tile as not-worth-it for the next replan (see clearRelayObstruction)."""
        self.avoid.add((pos.x, pos.y))


def costContext(bot, ct: Controller, profile: str) -> CostContext:
    """One context per unit-turn per profile, with the network and threat sets
    computed once and shared by both. Building those walks local_map and the turret
    list — far too expensive to redo per replan, let alone per profile."""
    rnd = ct.get_current_round()
    cache = getattr(bot, "_ctxCache", None)
    if cache is None or bot._ctxRound != rnd:
        cache = bot._ctxCache = {}
        bot._ctxRound = rnd
        bot._ctxNetwork = (
            buildNetwork(ct, bot.hivemind, bot.corePos)
            if bot.corePos is not None else set()
        )
        bot._ctxThreat = _threatTiles(ct, bot.hivemind)

    ctx = cache.get(profile)
    if ctx is None:
        ctx = cache[profile] = CostContext(
            ct, bot.hivemind, bot.corePos, profile,
            network=bot._ctxNetwork, threat=bot._ctxThreat,
        )
    return ctx


def _threatTiles(ct: Controller, hm) -> set[tuple[int, int]]:
    """Tiles covered by a visible enemy turret. Capped — a builder has 10ms."""
    out: set[tuple[int, int]] = set()
    myTeam = ct.get_team()
    seen = 0
    for bid in ct.get_nearby_buildings():
        if seen >= _MAX_THREAT_TURRETS:
            break
        if ct.get_team(bid) == myTeam:
            continue
        etype = ct.get_entity_type(bid)
        if etype not in TURRET_TYPES:
            continue
        try:
            tpos = ct.get_position(bid)
            facing = ct.get_direction(bid) if etype != EntityType.LAUNCHER else Direction.NORTH
            for tile in ct.get_attackable_tiles_from(tpos, facing, etype):
                out.add((tile.x, tile.y))
        except Exception:
            continue
        seen += 1
    return out


def _isChokepoint(ct: Controller, hm, pos: Position) -> bool:
    """Memoised for the whole match: this depends only on walls and map edges,
    and neither ever changes."""
    cache = getattr(hm, "_chokeCache", None)
    if cache is None:
        cache = hm._chokeCache = {}
    key = (pos.x, pos.y)
    hit = cache.get(key)
    if hit is not None:
        return hit

    blocked = 0
    for dx, dy in _ORTHO:
        n = Position(pos.x + dx, pos.y + dy)
        if not inBounds(ct, n) or readTile(ct, hm, n).wall:
            blocked += 1
            if blocked >= 2:
                break
    result = blocked >= 2
    cache[key] = result
    return result


def makeCostFn(ctx: CostContext):
    """Closure over a prepared context — keeps the A* inner loop cheap.

    Memoises per tile: a builder often replans two or three times in one turn and
    the terrain has not moved between them.
    """
    ct, hm = ctx.ct, ctx.hm
    walk = ctx.profile == "walk"
    network = ctx.network
    threat = ctx.threat
    avoid = ctx.avoid
    memo = ctx.costs

    def costFn(pos: Position) -> int | None:
        key = (pos.x, pos.y)
        if key in avoid:
            return None
        hit = memo.get(key, _MISS)
        if hit is not _MISS:
            return hit

        if isImpassable(ct, hm, pos):
            memo[key] = None
            return None

        if walk and key in network:
            memo[key] = NETWORK
            return NETWORK

        cost = BASE
        t = readTile(ct, hm, pos)

        if t.etype is not None:
            if t.enemy:
                cost += _ENEMY_BUILD_COST.get(t.etype, ALLY_BUILD)
            elif t.etype in BELT_TYPES:
                pass  # ours and walkable, no surcharge
            elif t.etype != EntityType.CORE:
                cost += ALLY_BUILD
        elif t.builder:
            cost += UNIT

        if key in threat:
            cost += ENEMY_FIRE
        if _isChokepoint(ct, hm, pos):
            cost += CHOKE

        if cost < MIN_STEP_COST:
            cost = MIN_STEP_COST
        memo[key] = cost
        return cost

    return costFn


def makeHeuristic(goal: Position | None):
    """Scaled by MIN_STEP_COST so it stays admissible whatever the weights become."""
    if goal is None:
        return None

    def h(pos: Position) -> int:
        return (abs(pos.x - goal.x) + abs(pos.y - goal.y)) * MIN_STEP_COST

    return h
