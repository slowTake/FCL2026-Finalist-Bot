"""Offensive execution for harasser builders — avoid fire, place turrets, seek."""

from __future__ import annotations

from fcode import Controller, Direction, EntityType, Environment, Position

from guns import (
    GUNNER_RANGE_SQ,
    _canAffordGunPlace,
    allyGunCovering,
    gunnerHasLos,
    placeGun,
    placeSentinel,
)
from logistics import moveToward
from pathing import CARDINALS, inBounds, manhattan, walkPassable

_AVOID_RADIUS = 4
_ORBIT_OFFSET = 3
_BELT_TYPES = frozenset({EntityType.CONVEYOR, EntityType.SPLITTER})
_FIRE_TI_COST = 2

# Sentinel: caller did not pass a goal — fall back to _avoidGoal(bot).
# Distinct from an explicit None, which means "no preferred direction".
_GOAL_DEFAULT = object()


def harasserRank(bot) -> int:
    """Spawn-order rank for Nth-target stagger (unique under wave too)."""
    idx = bot.hivemind.my_index
    if idx <= 0:
        return 0
    return idx - 1


def _nth(items: list, rank: int):
    """Pick items[rank % len]; None if empty."""
    if not items:
        return None
    return items[rank % len(items)]


def _coreFootprint(core_pos: Position) -> list[Position]:
    return [
        core_pos,
        Position(core_pos.x + 1, core_pos.y),
        Position(core_pos.x, core_pos.y + 1),
        Position(core_pos.x + 1, core_pos.y + 1),
    ]


def _chebyshevToFootprint(pos: Position, core_pos: Position) -> int:
    return min(
        max(abs(pos.x - t.x), abs(pos.y - t.y)) for t in _coreFootprint(core_pos)
    )


def _orbitMidSide(bot, ct: Controller, core: Position) -> list[Position]:
    """Four mid-side tiles around enemy 2x2 at ORBIT_OFFSET."""
    o = _ORBIT_OFFSET
    raw = [
        Position(core.x, core.y - o),  # N
        Position(core.x + 1 + o, core.y),  # E
        Position(core.x, core.y + 1 + o),  # S
        Position(core.x - o, core.y),  # W
    ]
    local_map = bot.hivemind.local_map
    out: list[Position] = []
    for tile in raw:
        if not inBounds(ct, tile):
            continue
        if not walkPassable(ct, tile, local_map=local_map):
            continue
        out.append(tile)
    return out


def _orbitRing(bot, ct: Controller, core: Position) -> list[Position]:
    """Walkable tiles at Chebyshev ORBIT_OFFSET from nearest footprint tile."""
    local_map = bot.hivemind.local_map
    o = _ORBIT_OFFSET
    # Bounding box around footprint expanded by o
    xs = [t.x for t in _coreFootprint(core)]
    ys = [t.y for t in _coreFootprint(core)]
    scored: list[Position] = []
    for y in range(min(ys) - o, max(ys) + o + 1):
        for x in range(min(xs) - o, max(xs) + o + 1):
            tile = Position(x, y)
            if _chebyshevToFootprint(tile, core) != o:
                continue
            if not inBounds(ct, tile):
                continue
            if not walkPassable(ct, tile, local_map=local_map):
                continue
            scored.append(tile)
    scored.sort(key=lambda p: (p.y, p.x))
    return scored


def _orbitJitter(bot, ct: Controller) -> bool:
    """Step to any walkable cardinal neighbor."""
    if ct.get_move_cooldown() != 0:
        return False
    pos = ct.get_position()
    local_map = bot.hivemind.local_map
    for d in (Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST):
        cand = pos.add(d)
        if not inBounds(ct, cand):
            continue
        if ct.is_in_vision(cand) and ct.get_tile_builder_bot_id(cand) is not None:
            continue
        if not walkPassable(ct, cand, local_map=local_map):
            continue
        if ct.can_move(d):
            ct.move(d)
            return True
    return False


def _orbitEnemyCore(bot, ct: Controller, core: Position) -> bool:
    """Cycle mid-side / ring waypoints; jitter if none."""
    waypoints = _orbitMidSide(bot, ct, core)
    if len(waypoints) < 2:
        ring = _orbitRing(bot, ct, core)
        if ring:
            waypoints = ring
    if not waypoints:
        return _orbitJitter(bot, ct)

    idx = getattr(bot, "harassOrbitIdx", 0) % len(waypoints)
    target = waypoints[idx]
    pos = ct.get_position()
    if pos.x == target.x and pos.y == target.y:
        bot.harassOrbitIdx = (idx + 1) % len(waypoints)
        target = waypoints[bot.harassOrbitIdx]
    elif getattr(bot, "stuck", 0) >= 3:
        bot.harassOrbitIdx = (idx + 1) % len(waypoints)
        target = waypoints[bot.harassOrbitIdx]

    return moveToward(bot, ct, target)


def _enemyGunners(ct: Controller) -> list[tuple[Position, Direction]]:
    me = ct.get_team()
    out: list[tuple[Position, Direction]] = []
    for eid in ct.get_nearby_entities():
        if ct.get_team(eid) == me:
            continue
        if ct.get_entity_type(eid) != EntityType.GUNNER:
            continue
        out.append((ct.get_position(eid), ct.get_direction(eid)))
    return out


def _tileUnderGunnerFire(
    ct: Controller,
    tile: Position,
    gunners: list[tuple[Position, Direction]],
) -> bool:
    for gpos, facing in gunners:
        if tile.distance_squared(gpos) > GUNNER_RANGE_SQ:
            continue
        # Their gunner, their ray: their own buildings shield the tile, ours get chewed.
        if gunnerHasLos(ct, gpos, facing, tile, shooterIsAlly=False):
            return True
        # Optimistic: gunner aimed at us even if engine LoS disagrees on fog
        if facing == gpos.direction_to(tile) and tile.distance_squared(gpos) <= GUNNER_RANGE_SQ:
            if ct.can_fire_from(gpos, facing, EntityType.GUNNER, tile):
                return True
    return False


def _avoidGoal(bot) -> Position | None:
    """Prefer sticky harass target, else enemy core."""
    sticky = getattr(bot, "harassTarget", None)
    if sticky is not None:
        return Position(sticky[0], sticky[1])
    return getattr(bot, "enemyCorePos", None)


def avoidFire(bot, ct: Controller, *, goal=_GOAL_DEFAULT) -> bool:
    """If under enemy gunner LoS, step to a safe tile that still advances toward goal.

    goal defaults to the harass target / enemy core. Pass an explicit Position to
    retreat elsewhere (relaying builders pass the ally core); pass None for the
    nearest safe tile with no preferred direction.
    """
    pos = ct.get_position()
    gunners = _enemyGunners(ct)
    if not gunners:
        return False
    if not _tileUnderGunnerFire(ct, pos, gunners):
        return False

    if goal is _GOAL_DEFAULT:
        goal = _avoidGoal(bot)
    goal_d = manhattan(pos, goal) if goal is not None else None
    local_map = bot.hivemind.local_map

    safe: list[tuple[Position, int, int]] = []  # cand, dist_to_goal, step
    for dy in range(-_AVOID_RADIUS, _AVOID_RADIUS + 1):
        for dx in range(-_AVOID_RADIUS, _AVOID_RADIUS + 1):
            if dx == 0 and dy == 0:
                continue
            cand = Position(pos.x + dx, pos.y + dy)
            if not inBounds(ct, cand):
                continue
            if not walkPassable(ct, cand, local_map=local_map):
                continue
            if ct.is_in_vision(cand) and ct.get_tile_builder_bot_id(cand) is not None:
                continue
            if _tileUnderGunnerFire(ct, cand, gunners):
                continue
            step = abs(dx) + abs(dy)
            if goal is not None:
                safe.append((cand, manhattan(cand, goal), step))
            else:
                safe.append((cand, step, step))

    if not safe:
        return False

    if goal is not None and goal_d is not None:
        progress = [s for s in safe if s[1] < goal_d]
        pool = progress if progress else safe
        pool.sort(key=lambda s: (s[1], s[2]))
    else:
        # No goal — nearest safe step (legacy)
        pool = sorted(safe, key=lambda s: s[2])

    return moveToward(bot, ct, pool[0][0])


def _nearestEnemyOfType(ct: Controller, etype: EntityType) -> Position | None:
    me = ct.get_team()
    pos = ct.get_position()
    best: Position | None = None
    best_d = 10**9
    for eid in ct.get_nearby_entities():
        if ct.get_team(eid) == me:
            continue
        if ct.get_entity_type(eid) != etype:
            continue
        epos = ct.get_position(eid)
        d = manhattan(pos, epos)
        if d < best_d:
            best_d = d
            best = epos
    return best


def snipeHarvester(bot, ct: Controller) -> bool:
    """Opportunistic: place gun on nearest visible enemy harvester not ally-covered."""
    target = _nearestEnemyOfType(ct, EntityType.HARVESTER)
    if target is None:
        return False
    if allyGunCovering(ct, target):
        return False
    return placeGun(bot, ct, target)


def offGun(bot, ct: Controller) -> bool:
    """Place gunner/sentinel toward core or visible threats (priority order)."""
    enemy_core = bot.enemyCorePos
    if enemy_core is not None:
        if placeGun(bot, ct, enemy_core):
            return True
        if placeSentinel(bot, ct, enemy_core):
            return True

    for etype in (
        EntityType.GUNNER,
        EntityType.SENTINEL,
        EntityType.HARVESTER,
        EntityType.LAUNCHER,
    ):
        target = _nearestEnemyOfType(ct, etype)
        if target is None:
            continue
        if etype == EntityType.HARVESTER and allyGunCovering(ct, target):
            continue
        if placeGun(bot, ct, target):
            return True
    return False


def _enemyBeltAt(ct: Controller, tile: Position) -> bool:
    bid = ct.get_tile_building_id(tile)
    if bid is None:
        return False
    if ct.get_team(bid) == ct.get_team():
        return False
    return ct.get_entity_type(bid) in _BELT_TYPES


def _oreCloserToAlly(bot, ct: Controller, pad: Position) -> bool:
    """True if pad is ore nearer ally core than enemy (don't wall our expansion)."""
    if ct.get_tile_env(pad) != Environment.ORE_TITANIUM:
        return False
    ally = bot.corePos
    enemy = bot.enemyCorePos
    if ally is None or enemy is None:
        return False
    return manhattan(pad, ally) <= manhattan(pad, enemy)


def _forwardBarrierPads(bot, ct: Controller) -> list[Position]:
    """Empty tiles one step ahead of enemy belt facing."""
    me = ct.get_team()
    pads: list[Position] = []
    seen: set[tuple[int, int]] = set()
    for eid in ct.get_nearby_entities():
        if ct.get_team(eid) == me:
            continue
        if ct.get_entity_type(eid) not in _BELT_TYPES:
            continue
        try:
            facing = ct.get_direction(eid)
        except Exception:
            continue
        if facing == Direction.CENTRE:
            continue
        belt = ct.get_position(eid)
        pad = belt.add(facing)
        key = (pad.x, pad.y)
        if key in seen:
            continue
        seen.add(key)
        if not inBounds(ct, pad):
            continue
        if not ct.is_in_vision(pad):
            continue
        if ct.get_tile_env(pad) == Environment.WALL:
            continue
        if not ct.is_tile_empty(pad):
            continue
        if _oreCloserToAlly(bot, ct, pad):
            continue
        pads.append(pad)
    pads.sort(key=lambda p: manhattan(ct.get_position(), p))
    return _dropSkipped(bot, pads)


def _skippedTargets(bot) -> set[tuple[int, int]]:
    s = getattr(bot, "skippedTargets", None)
    if s is None:
        bot.skippedTargets = set()
        return bot.skippedTargets
    return s


def _dropSkipped(bot, tiles: list[Position]) -> list[Position]:
    """Filter blacklisted tiles, falling back to the full pool if that empties it.

    Same concession `pickEmptyOre` makes: a blacklist that leaves nothing to do is
    worse than a bad target, so it degrades rather than idling the unit.
    """
    skipped = _skippedTargets(bot)
    if not skipped:
        return tiles
    kept = [t for t in tiles if (t.x, t.y) not in skipped]
    return kept if kept else tiles


def breakBeltDeadlock(bot, ct: Controller) -> bool:
    """Harasser anti-livelock: motionless too long → blacklist sticky targets.

    The offence mirror of `breakOreDeadlock`, and needed for the same reason: sticky
    state with no way to yield it. `denyBelt` runs Mode B only while gun placement is
    unaffordable, so a funded harasser adjacent to a belt correctly concludes it
    cannot profitably cut it — 10 shots and 20 Ti to destroy a 3 Ti conveyor — and
    then sat there, firing Mode A only when a passing enemy builder happened to
    create a fresh forward pad beside it.

    State-only: clears the claims so the same turn's chain re-picks. Costs no turn.
    """
    del ct
    from main import HARASS_DEADLOCK_TURNS

    if HARASS_DEADLOCK_TURNS is None or HARASS_DEADLOCK_TURNS <= 0:
        return False
    if getattr(bot, "stuck", 0) < HARASS_DEADLOCK_TURNS:
        return False

    skipped = _skippedTargets(bot)
    dropped = False
    for attr in ("denyBeltTile", "harassTarget"):
        target = getattr(bot, attr, None)
        if target is not None:
            skipped.add(target)
            setattr(bot, attr, None)
            dropped = True
    bot.cachedPath = None
    bot.cachedPathTarget = None
    bot.stuck = 0  # re-arm, so we don't blacklist again next turn
    return dropped


def _tryForwardBarrier(bot, ct: Controller, *, adjacent_only: bool) -> bool:
    pads = _forwardBarrierPads(bot, ct)
    if not pads:
        return False
    pos = ct.get_position()
    if ct.get_action_cooldown() == 0:
        for pad in pads:
            if abs(pos.x - pad.x) + abs(pos.y - pad.y) != 1:
                continue
            if ct.can_build_barrier(pad):
                ct.build_barrier(pad)
                return True
    if adjacent_only:
        return False
    target = _nth(pads, harasserRank(bot))
    if target is None:
        return False
    return moveToward(bot, ct, target)


def _enemyBeltsByDist(ct: Controller) -> list[Position]:
    me = ct.get_team()
    pos = ct.get_position()
    belts: list[Position] = []
    for eid in ct.get_nearby_entities():
        if ct.get_team(eid) == me:
            continue
        if ct.get_entity_type(eid) not in _BELT_TYPES:
            continue
        belts.append(ct.get_position(eid))
    belts.sort(key=lambda p: (manhattan(pos, p), p.x, p.y))
    return belts


def _stepOff(ct: Controller) -> bool:
    if ct.get_move_cooldown() != 0:
        return False
    for d in CARDINALS:
        if ct.can_move(d):
            ct.move(d)
            return True
    return False


def _cutInProgress(bot, ct: Controller) -> bool:
    """True while a belt cut is already underway and must not be interrupted.

    A cut is 20 HP at 2 dmg an attack — 10 shots, 20 Ti, 10 turns. Only the
    *marginal* cost matters once it has started, so the bank gate and the cooldown
    that decide whether to *begin* must not decide whether to *continue*. Dropping
    a cut half-finished spends the titanium and achieves nothing.

    In progress means the sticky target is:
      * not in vision — selected, still walking in; do not strand it part-way;
      * a damaged enemy belt — mid-cut;
      * an empty tile — the belt is already dead and the barrier is the next
        action, so gating here would abandon at the moment of payoff.

    A **full-HP** enemy belt is deliberately not in progress. That is the heal-race
    bail-out for free: their builder restores 4 HP/turn for 1 Ti against our 2 dmg
    for 2 Ti, so once they have healed it back to full we are losing 4:1 and should
    walk away rather than grind.
    """
    sticky = getattr(bot, "denyBeltTile", None)
    if sticky is None:
        return False
    tile = Position(sticky[0], sticky[1])
    if not ct.is_in_vision(tile):
        return True
    if ct.is_tile_empty(tile):
        return True
    if not _enemyBeltAt(ct, tile):
        return False
    bid = ct.get_tile_building_id(tile)
    if bid is None:
        return False
    try:
        return ct.get_hp(bid) < ct.get_max_hp(bid)
    except Exception:
        return False


def _cutCooldownElapsed(bot, ct: Controller) -> bool:
    """True if this harasser may *start* a new cut (≤0 disables the throttle)."""
    from main import DENY_CUT_COOLDOWN

    if DENY_CUT_COOLDOWN is None or DENY_CUT_COOLDOWN <= 0:
        return True
    last = getattr(bot, "lastCutRound", -(10**9))
    return ct.get_current_round() - last >= DENY_CUT_COOLDOWN


def _cutAndBarrier(bot, ct: Controller) -> bool:
    """Cut an enemy belt from an adjacent tile, then barrier it (guns gated).

    Staying adjacent is the point: the barrier can go down the moment the belt
    dies, with no move onto the tile and no step off afterwards.
    """
    sticky = getattr(bot, "denyBeltTile", None)
    tile: Position | None = None
    if sticky is not None:
        cand = Position(sticky[0], sticky[1])
        if ct.is_in_vision(cand):
            if _enemyBeltAt(ct, cand) or ct.is_tile_empty(cand):
                tile = cand
            else:
                bot.denyBeltTile = None
        else:
            tile = cand  # keep sticky while fogged / walking in
    if tile is None:
        tile = _nth(_dropSkipped(bot, _enemyBeltsByDist(ct)), harasserRank(bot))
        if tile is None:
            return False
        bot.denyBeltTile = (tile.x, tile.y)
        # Single stamp point: the cooldown spaces *starts*, so it is written where a
        # fresh target is claimed rather than chased across every exit path.
        bot.lastCutRound = ct.get_current_round()

    pos = ct.get_position()
    adjacent = abs(pos.x - tile.x) + abs(pos.y - tile.y) == 1

    # Belt cleared — plant the barrier from where we already stand
    if ct.is_in_vision(tile) and ct.is_tile_empty(tile):
        if _oreCloserToAlly(bot, ct, tile):
            bot.denyBeltTile = None
            return False
        if pos == tile:
            return _stepOff(ct)  # can't build on our own tile
        if adjacent:
            if ct.get_action_cooldown() == 0 and ct.can_build_barrier(tile):
                ct.build_barrier(tile)
                bot.denyBeltTile = None
                return True
            return False
        return moveToward(bot, ct, tile)

    if not _enemyBeltAt(ct, tile):
        bot.denyBeltTile = None
        return False

    if ct.get_global_resources() < _FIRE_TI_COST:
        return False

    # Preferred: shoot the belt from beside it, so we stay in place to barrier
    if adjacent:
        if ct.get_action_cooldown() != 0:
            return False
        if ct.can_fire(tile):
            ct.fire(tile)
            return True
        # Engine refused the adjacent shot — fall back to mounting the belt
        return moveToward(bot, ct, tile)

    # Mounted fallback: a builder may always fire the tile it stands on
    if pos == tile:
        if ct.get_action_cooldown() != 0:
            return False
        if ct.can_fire(pos):
            ct.fire(pos)
            return True
        return False

    return moveToward(bot, ct, tile)


def _denyOreCandidates(bot, ct: Controller) -> list[Position]:
    """Empty ore in the enemy's expansion ring that is safe for us to wall off.

    Ranked nearest the enemy core first — their most valuable expansion — then
    nearest us. Ore knowledge comes from `knownOreTiles`, which reads the ledger and
    falls back to `features.ORE_LOCATIONS`, so a harasser can head for enemy-side ore
    before anyone has scouted it.
    """
    from main import DENY_ORE_CHEBYSHEV, DENY_ORE_MIN_ALLY_CHEBYSHEV
    from logistics import knownOreTiles, tileThreatenedByGunner

    radius = DENY_ORE_CHEBYSHEV
    if radius is None or radius <= 0:
        return []
    ally, enemy = bot.corePos, getattr(bot, "enemyCorePos", None)
    if ally is None or enemy is None:
        return []
    floor = DENY_ORE_MIN_ALLY_CHEBYSHEV or 0

    pos = ct.get_position()
    scored: list[tuple[int, int, Position]] = []
    # includeStatic: the ledger only holds ore we have already scouted — our own side
    # early on — so without the map table there are no candidates until a harasser has
    # made the very trip this function exists to motivate.
    for ore in knownOreTiles(bot, includeStatic=True):
        d_enemy = _chebyshevToFootprint(ore, enemy)
        if d_enemy > radius:
            continue
        # Two independent guards on our own side. The midline alone is not enough on
        # a small map: an ore can be marginally nearer their core yet sit well inside
        # our working range — atoll's contested centre deposit is exactly that shape.
        if _chebyshevToFootprint(ore, ally) < floor:
            continue
        # Midline, inlined rather than via _oreCloserToAlly. That helper leads with
        # `ct.get_tile_env(pad)`, which every other call site in the codebase guards
        # behind is_in_vision — its existing callers only ever pass visible pads.
        # knownOreTiles reads the persistent ledger, so ore seen long ago arrives here
        # out of vision, and the env check is redundant anyway: it is ore by
        # construction. Getting this wrong is silent, because Player.run swallows the
        # exception and the harasser simply does nothing for the rest of the game.
        if manhattan(ore, ally) <= manhattan(ore, enemy):
            continue
        # Occupied nodes are somebody else's problem: a live enemy harvester belongs
        # to snipeHarvester, and anything else already denies the tile for us.
        if ct.is_in_vision(ore):
            if ct.get_tile_building_id(ore) is not None:
                continue
        else:
            entry = bot.hivemind.local_map.get((ore.x, ore.y), {})
            if entry.get("entity"):
                continue
        # A covered tile means the barrier is shot down and rebuilt forever, turning
        # a one-off 3 Ti into a drip.
        if tileThreatenedByGunner(ct, ore):
            continue
        scored.append((d_enemy, manhattan(pos, ore), ore))
    scored.sort(key=lambda s: (s[0], s[1]))
    return [ore for _de, _dm, ore in scored]


def denyOre(bot, ct: Controller) -> bool:
    """Barrier empty ore in the enemy's expansion ring.

    The cheapest denial on the map: 3 Ti makes the tile unbuildable, preventing a
    20 Ti harvester and the whole belt line that would have fed from it. Clearing it
    costs them 15 builder-turns / 30 Ti, or 3 gunner shots / 6 Ti — every route costs
    more than it cost us, and the node produces nothing while it stands.

    Self-correcting rather than permanent: if we later want the node, `pickEmptyOre`
    admits ore carrying an ally barrier and `checkOre` destroys it and builds the
    harvester. Denying a node never locks us out of it.
    """
    candidates = _denyOreCandidates(bot, ct)
    if not candidates:
        return False

    pos = ct.get_position()
    if ct.get_action_cooldown() == 0:
        for ore in candidates:
            if abs(pos.x - ore.x) + abs(pos.y - ore.y) != 1:
                continue
            if ct.can_build_barrier(ore):
                ct.build_barrier(ore)
                return True

    target = candidates[0]
    bot.harassTarget = (target.x, target.y)
    return moveToward(bot, ct, target)


def denyBelt(bot, ct: Controller) -> bool:
    """Barrier ahead of enemy belt facing; under gun gate also cut-and-barrier.

    Mode A (forward barrier) is unthrottled: 3 Ti, no attrition, no engagement.
    Mode B (cut the belt) is the expensive one — 20 Ti and 10 turns — so it is
    rationed on the way in and committed to once begun. The bank gate is a sound
    *start* condition (a gun is better value than a cut when we can afford one) and
    a wrong *continue* condition: each attack pushes the bank toward the threshold
    while one harvester delivery pushes it back over, so it used to flip mid-cut and
    abandon the belt with the titanium already spent.
    """
    # Mode A: adjacent build first
    if _tryForwardBarrier(bot, ct, adjacent_only=True):
        return True
    # Mode B: finish what we started; otherwise start only while a gun is
    # unaffordable and this harasser's cut cooldown has elapsed.
    if _cutInProgress(bot, ct) or (
        not _canAffordGunPlace(ct) and _cutCooldownElapsed(bot, ct)
    ):
        if _cutAndBarrier(bot, ct):
            return True
    # Mode A: walk toward a forward pad
    return _tryForwardBarrier(bot, ct, adjacent_only=False)


def _localEnemyHarvesters(bot, ct: Controller) -> list[Position]:
    me = ct.get_team()
    found: list[Position] = []
    for (x, y), cell in bot.hivemind.local_map.items():
        ent = cell.get("entity")
        if not ent:
            continue
        if ent.get("type") != EntityType.HARVESTER:
            continue
        if ent.get("team") == me:
            continue
        found.append(Position(x, y))
    found.sort(key=lambda p: manhattan(ct.get_position(), p))
    return _dropSkipped(bot, found)


def _scoutedOres(bot) -> set[tuple[int, int]]:
    s = getattr(bot, "scoutedOres", None)
    if s is None:
        bot.scoutedOres = set()
        return bot.scoutedOres
    return s


def _markOreScouted(bot, tile: Position) -> None:
    _scoutedOres(bot).add((tile.x, tile.y))


def _enemyHarvesterOnTile(ct: Controller, tile: Position) -> bool:
    bid = ct.get_tile_building_id(tile)
    return (
        bid is not None
        and ct.get_entity_type(bid) == EntityType.HARVESTER
        and ct.get_team(bid) != ct.get_team()
    )


def _scoutOreTargets(bot, ct: Controller) -> list[Position]:
    """Ore tiles to inspect: hostile entity on ore, or ore without ally harvester.

    Skips ore on our own half. Scoring is `(priority, distance)` and our near-core nodes
    are priority 1 *and* nearest, so they used to sort first — the harasser walked to our
    own ore, marked it scouted, walked to the next, and never left base. Our defaults
    already work those nodes; there is no offensive value in touring them. With the role
    split at one harasser per five builders, that tour was the only harasser activity
    visible.

    The midline test is inlined rather than using `_oreCloserToAlly`, which leads with
    `ct.get_tile_env(pad)` — safe only for tiles in vision, and ledger ore frequently is
    not. That exact mistake silently froze harassers before, because `Player.run` swallows
    the exception and the unit simply stops for the rest of the game.
    """
    me = ct.get_team()
    pos = ct.get_position()
    seen = _scoutedOres(bot)
    ally = bot.corePos
    enemy = getattr(bot, "enemyCorePos", None)
    scored: list[tuple[int, int, Position]] = []
    for (x, y), cell in bot.hivemind.local_map.items():
        if not cell.get("ore"):
            continue
        if (x, y) in seen:
            continue
        if ally is not None and enemy is not None:
            tile = Position(x, y)
            if manhattan(tile, ally) <= manhattan(tile, enemy):
                continue
        ent = cell.get("entity")
        # Skip friendly harvesters we're not clearing
        if ent and ent.get("type") == EntityType.HARVESTER and ent.get("team") == me:
            continue
        priority = 1
        if ent and ent.get("team") != me:
            priority = 0
        tile = Position(x, y)
        scored.append((priority, manhattan(pos, tile), tile))
    scored.sort()
    return [t for _, _, t in scored]


def seekHarvester(bot, ct: Controller) -> bool:
    """Move to known enemy harvesters or scout ore nodes; vision refreshes local_map."""
    sticky = getattr(bot, "harassTarget", None)
    if sticky is not None:
        sx, sy = sticky
        tile = Position(sx, sy)
        if ct.is_in_vision(tile):
            if _enemyHarvesterOnTile(ct, tile):
                pass  # keep sticky on live enemy harvester
            else:
                # Inspected empty / friendly / cleared — don't re-scout this pad
                _markOreScouted(bot, tile)
                bot.harassTarget = None
                sticky = None
        if sticky is not None and moveToward(bot, ct, tile):
            return True

    for harv in _localEnemyHarvesters(bot, ct):
        bot.harassTarget = (harv.x, harv.y)
        if moveToward(bot, ct, harv):
            return True

    for ore in _scoutOreTargets(bot, ct):
        # Already standing on / next to an empty scout ore → mark and skip
        if ct.is_in_vision(ore) and not _enemyHarvesterOnTile(ct, ore):
            pos = ct.get_position()
            if abs(pos.x - ore.x) + abs(pos.y - ore.y) <= 1:
                _markOreScouted(bot, ore)
                continue
        bot.harassTarget = (ore.x, ore.y)
        if moveToward(bot, ct, ore):
            return True
    return False


def seekCore(bot, ct: Controller) -> bool:
    """Approach enemy core; if blocked, orbit mid-side / ring waypoints."""
    core = bot.enemyCorePos
    if core is None:
        return False
    if moveToward(bot, ct, core):
        return True
    return _orbitEnemyCore(bot, ct, core)


def run_raider(bot, ct: Controller) -> None:
    from builder import runHarasser

    runHarasser(bot, ct)


def run_raiders(bot, ct: Controller) -> None:
    from builder import runHarasser

    runHarasser(bot, ct)


def run_attack_builder(bot, ct: Controller) -> None:
    from builder import runHarasser

    runHarasser(bot, ct)
