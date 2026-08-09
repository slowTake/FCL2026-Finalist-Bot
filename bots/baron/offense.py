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
from geometry import (
    BELT_TYPES,
    beltNeighbors,
    chebyshevToCore,
    coreFootprint,
    harvesterClaimedByAlly,
    isAdjacent,
)
from logistics import moveToward, pathKnownBlocked
from pathing import CARDINALS, inBounds, manhattan, walkPassable
from walls import wallEnemyCore

_AVOID_RADIUS = 4
_ORBIT_OFFSET = 3
_FIRE_TI_COST = 2

# How long a tile we just fled stays blacklisted from future dodges, in turns.
# A stationary gunner just rotates to track us, so the tile we left reads as
# "safe again" the very next check — without this memory avoidFire ping-pongs
# between the same two tiles every turn.
_AVOID_FIRE_MEMORY_TURNS = 3

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
    xs = [t.x for t in coreFootprint(core)]
    ys = [t.y for t in coreFootprint(core)]
    scored: list[Position] = []
    for y in range(min(ys) - o, max(ys) + o + 1):
        for x in range(min(xs) - o, max(xs) + o + 1):
            tile = Position(x, y)
            if chebyshevToCore(tile, core) != o:
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

    return _harassMove(bot, ct, target)


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
    *,
    cache: dict | None = None,
) -> bool:
    for gpos, facing in gunners:
        if tile.distance_squared(gpos) > GUNNER_RANGE_SQ:
            continue
        # Their gunner, their ray: their own buildings shield the tile, ours get chewed.
        if gunnerHasLos(ct, gpos, facing, tile, shooterIsAlly=False, cache=cache):
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


def _dodgeMemory(bot) -> dict[tuple[int, int], int]:
    mem = getattr(bot, "dodgeBlacklist", None)
    if mem is None:
        bot.dodgeBlacklist = {}
        return bot.dodgeBlacklist
    return mem


def _pruneDodgeMemory(bot, ct: Controller) -> dict[tuple[int, int], int]:
    """Drop entries older than _AVOID_FIRE_MEMORY_TURNS; return the live dict."""
    mem = _dodgeMemory(bot)
    if not mem:
        return mem
    round_n = ct.get_current_round()
    stale = [
        k for k, marked in mem.items() if round_n - marked >= _AVOID_FIRE_MEMORY_TURNS
    ]
    for k in stale:
        del mem[k]
    return mem


def _harassMove(bot, ct: Controller, target: Position) -> bool:
    """moveToward that also routes around this harasser's recent-dodge memory.

    Every other harasser step (offGun, denyOre, denyBelt, seekHarvester,
    seekCore, orbit) wants a plain goal-directed step and has no notion of
    gunfire at all — so once a gunner drops out of vision (the harasser
    stepped out of its range/LoS), these steps happily path straight back
    onto the tile avoidFire just fled, because nothing there re-checks the
    threat. Reusing avoidFire's own dodge blacklist here means the memory
    survives losing sight of the gunner, not just its current facing.
    """
    avoid = _pruneDodgeMemory(bot, ct)
    return moveToward(bot, ct, target, avoid=set(avoid) if avoid else None)


def avoidFire(bot, ct: Controller, *, goal=_GOAL_DEFAULT) -> bool:
    """If under enemy gunner LoS, step to a safe tile that still advances toward goal.

    goal defaults to the harass target / enemy core. Pass an explicit Position to
    retreat elsewhere (relaying builders pass the ally core); pass None for the
    nearest safe tile with no preferred direction.

    A tile we just fled is blacklisted for `_AVOID_FIRE_MEMORY_TURNS`: a
    stationary gunner just rotates to track us, so on the next check the tile
    we left reads as "safe" again purely because the gunner's facing moved —
    without the blacklist that reads as a valid dodge target and we ping-pong
    back into it, taking a hit at the exact moment we arrive.
    """
    pos = ct.get_position()
    gunners = _enemyGunners(ct)
    if not gunners:
        return False
    # Shared per-tile memo (env / entity lookups), same pattern as
    # _gunfireSafePads/_gunnerPadsNearTarget in guns.py — the dy/dx box below
    # can re-check the same gunner's ray from many candidate tiles in one call.
    cache: dict = {}
    if not _tileUnderGunnerFire(ct, pos, gunners, cache=cache):
        return False

    if goal is _GOAL_DEFAULT:
        goal = _avoidGoal(bot)
    goal_d = manhattan(pos, goal) if goal is not None else None
    local_map = bot.hivemind.local_map
    dodgeMem = _pruneDodgeMemory(bot, ct)

    safe: list[tuple[Position, int, int]] = []  # cand, dist_to_goal, step
    for dy in range(-_AVOID_RADIUS, _AVOID_RADIUS + 1):
        for dx in range(-_AVOID_RADIUS, _AVOID_RADIUS + 1):
            if dx == 0 and dy == 0:
                continue
            cand = Position(pos.x + dx, pos.y + dy)
            if not inBounds(ct, cand):
                continue
            if (cand.x, cand.y) in dodgeMem:
                continue
            if not walkPassable(ct, cand, local_map=local_map):
                continue
            if ct.is_in_vision(cand) and ct.get_tile_builder_bot_id(cand) is not None:
                continue
            if _tileUnderGunnerFire(ct, cand, gunners, cache=cache):
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

    # Blacklist the tile we're fleeing so a tracking gunner's rotation can't
    # make it look safe again next turn and pull us straight back into it.
    dodgeMem[(pos.x, pos.y)] = ct.get_current_round()
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


def _nearestUnclaimedEnemyHarvester(bot, ct: Controller) -> Position | None:
    """Nearest visible enemy harvester, excluding one our own steal-relay claimed.

    `harvesterClaimedByAlly` (geometry.py) reads True once our own belt lands
    cardinally adjacent — see main.STEAL_ENEMY_HARVESTERS. A harvester we're
    actively stealing is worth more alive than dead: shooting it down here
    would throw away the belts we already paid for. `denyHarvesterSupply`
    handles a claimed harvester instead, by cutting the enemy's own competing
    belt on it rather than the harvester itself.
    """
    me = ct.get_team()
    pos = ct.get_position()
    local_map = bot.hivemind.local_map
    best: Position | None = None
    best_d = 10**9
    for eid in ct.get_nearby_entities():
        if ct.get_team(eid) == me:
            continue
        if ct.get_entity_type(eid) != EntityType.HARVESTER:
            continue
        epos = ct.get_position(eid)
        if harvesterClaimedByAlly(ct, epos, local_map=local_map):
            continue
        d = manhattan(pos, epos)
        if d < best_d:
            best_d = d
            best = epos
    return best


def snipeHarvester(bot, ct: Controller) -> bool:
    """Opportunistic: place gun on nearest visible enemy harvester not ally-covered
    or already claimed by our own steal-relay."""
    target = _nearestUnclaimedEnemyHarvester(bot, ct)
    if target is None:
        return False
    if allyGunCovering(ct, target):
        return False
    return placeGun(bot, ct, target)


def _corePushReady(ct: Controller) -> bool:
    """True once the bank justifies shooting at the enemy core (CORE_PUSH_MIN_TI).

    Stacks on top of `_canAffordGunPlace`'s generic GUN_PLACE_MIN_TI floor already
    enforced inside `placeGun`/`placeSentinel` — the core specifically needs the
    higher bar; every other target only needs the generic one.
    """
    from main import CORE_PUSH_MIN_TI

    if CORE_PUSH_MIN_TI is None or CORE_PUSH_MIN_TI <= 250:
        return True
    return ct.get_global_resources() >= CORE_PUSH_MIN_TI


def offGun(bot, ct: Controller) -> bool:
    """Place gunner/sentinel toward core (once CORE_PUSH_MIN_TI) or visible threats."""
    enemy_core = bot.enemyCorePos
    if enemy_core is not None and _corePushReady(ct):
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
        if etype == EntityType.HARVESTER and (
            allyGunCovering(ct, target)
            or harvesterClaimedByAlly(ct, target, local_map=bot.hivemind.local_map)
        ):
            continue
        if placeGun(bot, ct, target):
            return True
    return False


def wallCore(bot, ct: Controller) -> bool:
    """Opportunistically barrier an empty tile beside the enemy core, if known.

    Ungated by CORE_PUSH_MIN_TI — a barrier is 3 Ti and unthrottled, so unlike
    offGun's core branch it's worth taking any time a harasser is in position,
    independent of bank.
    """
    core = bot.enemyCorePos
    if core is None:
        return False
    return wallEnemyCore(ct, core)


def _enemyBeltAt(ct: Controller, tile: Position) -> bool:
    if not ct.is_in_vision(tile):
        return False
    bid = ct.get_tile_building_id(tile)
    if bid is None:
        return False
    if ct.get_team(bid) == ct.get_team():
        return False
    return ct.get_entity_type(bid) in BELT_TYPES


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
        if ct.get_entity_type(eid) not in BELT_TYPES:
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
    for attr in ("denyBeltTile", "harassTarget", "supplyCutTile"):
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
            if not isAdjacent(pos, pad):
                continue
            if ct.can_build_barrier(pad):
                ct.build_barrier(pad)
                return True
    if adjacent_only:
        return False
    target = _nth(pads, harasserRank(bot))
    if target is None:
        return False
    return _harassMove(bot, ct, target)


def _enemyBeltsByDist(ct: Controller) -> list[Position]:
    me = ct.get_team()
    pos = ct.get_position()
    belts: list[Position] = []
    for eid in ct.get_nearby_entities():
        if ct.get_team(eid) == me:
            continue
        if ct.get_entity_type(eid) not in BELT_TYPES:
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


def _cutInProgress(bot, ct: Controller, *, attr: str = "denyBeltTile") -> bool:
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

    `attr` names the sticky-target field on `bot` — defaults to `denyBeltTile`
    (denyBelt's own cut). `denyHarvesterSupply` reuses this same mechanic under
    `supplyCutTile` instead, so the two cuts never share or collide on state.
    """
    sticky = getattr(bot, attr, None)
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


def _enemyBuilderNear(ct: Controller, tile: Position, radius: int) -> bool:
    """True if an enemy BUILDER_BOT is within Chebyshev radius of tile, in vision.

    Live vision only, not the HiveMind ledger — a stale sighting could mark a
    now-abandoned belt "defended" forever, and a builder that has since
    arrived wouldn't show up until this unit's own vision covers that exact
    tile again either way. Reading only what is currently visible means the
    worst case of getting it wrong is falling back to the existing
    gun-affordability gate, never committing to a cut on stale information.
    """
    me = ct.get_team()
    for eid in ct.get_nearby_entities():
        if ct.get_team(eid) == me:
            continue
        if ct.get_entity_type(eid) != EntityType.BUILDER_BOT:
            continue
        epos = ct.get_position(eid)
        if max(abs(epos.x - tile.x), abs(epos.y - tile.y)) <= radius:
            return True
    return False


def _worthwhileCutTarget(bot, ct: Controller) -> Position | None:
    """The belt worth *starting* a Mode B cut on this turn, or None.

    Two independent reasons to start: a gun is unaffordable (today's original
    gate — a gun beats a cut whenever we can field one), or the target is
    undefended (main.UNDEFENDED_BELT_CHEBYSHEV — nothing can heal or rebuild
    it, so the denial is effectively permanent regardless of the bank).

    Returns the same candidate `_cutAndBarrier`'s own default picker would
    choose (`_enemyBeltsByDist` + `_dropSkipped` + `harasserRank` stagger), so
    the undefended check and the tile that actually gets cut are guaranteed to
    be the same one — checking a different tile than the one we commit to
    would make the "undefended" reasoning meaningless.
    """
    candidate = _nth(_dropSkipped(bot, _enemyBeltsByDist(ct)), harasserRank(bot))
    if candidate is None:
        return None
    if not _canAffordGunPlace(ct):
        return candidate
    from main import UNDEFENDED_BELT_CHEBYSHEV

    radius = UNDEFENDED_BELT_CHEBYSHEV
    if radius and radius > 0 and not _enemyBuilderNear(ct, candidate, radius):
        return candidate
    return None


def _cutAndBarrier(
    bot,
    ct: Controller,
    *,
    attr: str = "denyBeltTile",
    cooldownAttr: str = "lastCutRound",
    pick=None,
) -> bool:
    """Cut an enemy belt from an adjacent tile, then barrier it (guns gated).

    Staying adjacent is the point: the barrier can go down the moment the belt
    dies, with no move onto the tile and no step off afterwards.

    `attr`/`cooldownAttr` name the sticky-target/last-start fields on `bot`,
    and `pick` overrides the default nearest-enemy-belt target search — see
    `denyHarvesterSupply`, which reuses this mechanic under its own
    `supplyCutTile`/`lastSupplyCutRound` pair and a claimed-harvester-anchored
    `pick`, fully independent of `denyBelt`'s own state.
    """
    sticky = getattr(bot, attr, None)
    tile: Position | None = None
    if sticky is not None:
        cand = Position(sticky[0], sticky[1])
        if ct.is_in_vision(cand):
            if _enemyBeltAt(ct, cand) or ct.is_tile_empty(cand):
                tile = cand
            else:
                setattr(bot, attr, None)
        else:
            tile = cand  # keep sticky while fogged / walking in
    if tile is None:
        finder = pick if pick is not None else (
            lambda: _nth(_dropSkipped(bot, _enemyBeltsByDist(ct)), harasserRank(bot))
        )
        tile = finder()
        if tile is None:
            return False
        setattr(bot, attr, (tile.x, tile.y))
        # Single stamp point: the cooldown spaces *starts*, so it is written where a
        # fresh target is claimed rather than chased across every exit path.
        setattr(bot, cooldownAttr, ct.get_current_round())

    pos = ct.get_position()
    adjacent = isAdjacent(pos, tile)

    # Belt cleared — plant the barrier from where we already stand
    if ct.is_in_vision(tile) and ct.is_tile_empty(tile):
        if _oreCloserToAlly(bot, ct, tile):
            setattr(bot, attr, None)
            return False
        if pos == tile:
            return _stepOff(ct)  # can't build on our own tile
        if adjacent:
            if ct.get_action_cooldown() == 0 and ct.can_build_barrier(tile):
                ct.build_barrier(tile)
                setattr(bot, attr, None)
                return True
            return False
        return _harassMove(bot, ct, tile)

    if not _enemyBeltAt(ct, tile):
        setattr(bot, attr, None)
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
        return _harassMove(bot, ct, tile)

    # Mounted fallback: a builder may always fire the tile it stands on
    if pos == tile:
        if ct.get_action_cooldown() != 0:
            return False
        if ct.can_fire(pos):
            ct.fire(pos)
            return True
        return False

    return _harassMove(bot, ct, tile)


def _claimedHarvesterSupplyTargets(bot, ct: Controller) -> list[Position]:
    """Enemy belts cardinally touching a harvester our own relay already claimed.

    Once claimed (geometry.harvesterClaimedByAlly — an ally belt already lands
    next to it), an enemy belt still beside the harvester leaks its output
    straight back into their network instead of ours: cut it so 100% of the
    output routes to us. Nearest-self first; `_cutAndBarrier`'s `pick` wraps
    this in `_nth(..., harasserRank(bot))` for the same target-stagger every
    other harasser picker uses.
    """
    me = ct.get_team()
    pos = ct.get_position()
    local_map = bot.hivemind.local_map
    out: list[Position] = []
    for (x, y), cell in local_map.items():
        ent = cell.get("entity")
        if not ent or ent.get("type") != EntityType.HARVESTER or ent.get("team") == me:
            continue
        tile = Position(x, y)
        if not harvesterClaimedByAlly(ct, tile, local_map=local_map):
            continue
        for n, belt_ent in beltNeighbors(ct, tile, local_map=local_map):
            if belt_ent.get("team") != me:
                out.append(n)
                break  # one enemy belt per claimed harvester is enough per turn
    out.sort(key=lambda p: manhattan(pos, p))
    return _dropSkipped(bot, out)


def denyHarvesterSupply(bot, ct: Controller) -> bool:
    """Cut an enemy belt still feeding a harvester our own steal-relay claimed.

    Reuses `_cutAndBarrier`'s fire-then-barrier mechanic under its own sticky
    field (`bot.supplyCutTile`/`bot.lastSupplyCutRound`), so it can never
    collide with `denyBelt` Mode B's `denyBeltTile` continuation lock or
    `DENY_CUT_COOLDOWN` — the two cuts run fully independently, and if they
    ever pick the same physical tile, whichever fires first destroys it; the
    other's next check sees an empty tile and falls through cleanly (its own
    barrier attempt, or a re-pick).

    Unthrottled on purpose: this defends an asset we're already actively
    capturing for free, not an opportunistic denial — there's no bank-based
    start condition to weigh the way `denyBelt`'s `_canAffordGunPlace` gate
    does for a generic cut.
    """
    from main import STEAL_ENEMY_HARVESTERS

    if not STEAL_ENEMY_HARVESTERS:
        return False
    return _cutAndBarrier(
        bot,
        ct,
        attr="supplyCutTile",
        cooldownAttr="lastSupplyCutRound",
        pick=lambda: _nth(_claimedHarvesterSupplyTargets(bot, ct), harasserRank(bot)),
    )


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
    for ore in knownOreTiles(bot):
        d_enemy = chebyshevToCore(ore, enemy)
        if d_enemy > radius:
            continue
        # Two independent guards on our own side. The midline alone is not enough on
        # a small map: an ore can be marginally nearer their core yet sit well inside
        # our working range — atoll's contested centre deposit is exactly that shape.
        if chebyshevToCore(ore, ally) < floor:
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
    # denyBelt's candidate lists (_forwardBarrierPads, _enemyBeltsByDist) both filter
    # through _dropSkipped; this one didn't, so a target blacklisted by denyOre's own
    # pathKnownBlocked check below (or by breakBeltDeadlock) was immediately re-picked
    # next call — the blacklist had no effect on this list at all.
    return _dropSkipped(bot, [ore for _de, _dm, ore in scored])


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
            if not isAdjacent(pos, ore):
                continue
            if ct.can_build_barrier(ore):
                ct.build_barrier(ore)
                return True

    target = candidates[0]
    bot.harassTarget = (target.x, target.y)
    moved = _harassMove(bot, ct, target)
    if not moved and pathKnownBlocked(bot, target):
        # A* just proved this ore unreachable (walled off / occupied with no way
        # around) — blacklist it immediately instead of waiting up to
        # HARASS_DEADLOCK_TURNS more full searches for bot.stuck to catch up.
        # Checked right here, right after the only moveToward call this function
        # makes, so the verdict is guaranteed to be this target's, not some other
        # step's leftover from earlier in the turn.
        _skippedTargets(bot).add((target.x, target.y))
        bot.harassTarget = None
    return moved


def denyBelt(bot, ct: Controller) -> bool:
    """Barrier ahead of enemy belt facing; under gun gate also cut-and-barrier.

    Mode A (forward barrier) is unthrottled: 3 Ti, no attrition, no engagement.
    Mode B (cut the belt) is the expensive one — 20 Ti and 10 turns — so it is
    rationed on the way in and committed to once begun. The bank gate is a sound
    *start* condition (a gun is better value than a cut when we can afford one) and
    a wrong *continue* condition: each attack pushes the bank toward the threshold
    while one harvester delivery pushes it back over, so it used to flip mid-cut and
    abandon the belt with the titanium already spent. Continuing is therefore never
    reconsidered once started, regardless of affordability or the undefended check
    below — only the decision to *start* reads either of them.

    Starting also has a second, independent reason beyond "a gun is unaffordable":
    a target with no enemy builder nearby (UNDEFENDED_BELT_CHEBYSHEV) has nobody
    able to heal it back or rebuild it once it's gone, so the denial is effectively
    permanent — worth taking regardless of what else the bank could buy. See
    `_worthwhileCutTarget`.
    """
    # Mode A: adjacent build first
    if _tryForwardBarrier(bot, ct, adjacent_only=True):
        return True
    # Mode B: finish what we started, no reconsideration.
    if _cutInProgress(bot, ct):
        if _cutAndBarrier(bot, ct):
            return True
    elif _cutCooldownElapsed(bot, ct):
        target = _worthwhileCutTarget(bot, ct)
        if target is not None and _cutAndBarrier(bot, ct, pick=lambda t=target: t):
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
    """Ore tiles to inspect: hostile entity on ore, or ore without ally harvester."""
    me = ct.get_team()
    pos = ct.get_position()
    seen = _scoutedOres(bot)
    scored: list[tuple[int, int, Position]] = []
    for (x, y), cell in bot.hivemind.local_map.items():
        if not cell.get("ore"):
            continue
        if (x, y) in seen:
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
        if sticky is not None and _harassMove(bot, ct, tile):
            return True

    for harv in _localEnemyHarvesters(bot, ct):
        bot.harassTarget = (harv.x, harv.y)
        if _harassMove(bot, ct, harv):
            return True

    for ore in _scoutOreTargets(bot, ct):
        # Already standing on / next to an empty scout ore → mark and skip
        if ct.is_in_vision(ore) and not _enemyHarvesterOnTile(ct, ore):
            pos = ct.get_position()
            if abs(pos.x - ore.x) + abs(pos.y - ore.y) <= 1:
                _markOreScouted(bot, ore)
                continue
        bot.harassTarget = (ore.x, ore.y)
        if _harassMove(bot, ct, ore):
            return True
    return False


def seekCore(bot, ct: Controller) -> bool:
    """Approach enemy core; if blocked, orbit mid-side / ring waypoints."""
    core = bot.enemyCorePos
    if core is None:
        return False
    if _harassMove(bot, ct, core):
        return True
    return _orbitEnemyCore(bot, ct, core)
