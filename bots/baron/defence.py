"""Home defence orchestrators — defGun, guardian clear/patrol."""

from __future__ import annotations

from fcode import Controller, Direction, EntityType, Environment, Position

from geometry import (
    BELT_TYPES,
    DEFEND_HOME_CHEBYSHEV,
    TURRET_TYPES,
    chebyshevToCore,
    coreFootprint,
    harvesterClaimedByAlly,
    isAdjacent,
    nearCore,
    tileEntity,
)
from guns import (
    GUNNER_RANGE_SQ,
    allyGunCovering,
    allySentinelCovering,
    placeDefSentinel,
    placeGun,
)
from pathing import CARDINALS, inBounds, manhattan, walkPassable


def _pickDefGunTarget(
    ct: Controller, core_pos: Position | None, max_cheb: int = DEFEND_HOME_CHEBYSHEV
) -> Position | None:
    """Nearest enemy gunner/sentinel/launcher in vision — never a builder.

    `core_pos` set (guardians, via `homeOnly`) restricts targets to the leash: a
    gunner pad must sit within sqrt(13) ≈ 3.6 of its target, so aiming further
    out sends the guardian to a pad the leash step then marches it back from —
    it ping-pongs and never places. Out-of-leash turrets are `defSentinel`'s
    job. Defaults pass None: they have no leash and can go finish the job.

    Skips anything an ally gunner already covers (`allyGunCovering`) — without
    this, `defGun` re-picks the same already-answered threat every turn a target
    stays in vision and never yields the turn to `defendHome`/`healAlly`, so a
    gunner that took damage placing itself never gets repaired.

    A lone enemy `BUILDER_BOT` used to be a fallback target when no turret was
    in range — spending Ti to place a gunner over a passing builder that was
    never going to do real damage. Dropped so `defGun` matches `offGun`
    (offense.py), which never targeted builders either. An already-built
    gunner still opportunistically shoots a builder that wanders into its face
    (guns.py's `_GUNNER_PRIORITY` is unchanged) — this only stops us from
    *placing* a new one over one.
    """
    me = ct.get_team()
    pos = ct.get_position()
    best: Position | None = None
    best_d = 10**9
    for eid in ct.get_nearby_units():
        if ct.get_team(eid) == me:
            continue
        if ct.get_entity_type(eid) not in TURRET_TYPES:
            continue
        epos = ct.get_position(eid)
        if (
            core_pos is not None
            and chebyshevToCore(epos, core_pos) > max_cheb
        ):
            continue
        if allyGunCovering(ct, epos):
            continue  # already answered — don't stack gunners on one threat
        dist = abs(pos.x - epos.x) + abs(pos.y - epos.y)
        if dist < best_d:
            best_d = dist
            best = epos
    return best


def defGun(bot, ct: Controller, *, homeOnly: bool = False) -> bool:
    """Near-core defensive gunner placement (gunners only — never sentinels).

    `homeOnly` forbids any action that would need the caller to leave the leash
    — guardians pass it, defaults do not.
    """
    from logistics import guardianLeash

    if bot.inRelay or getattr(bot, "recheckingOre", False):
        return False
    leash = guardianLeash(ct)
    if not nearCore(bot, ct, leash):
        return False
    target = _pickDefGunTarget(ct, bot.corePos if homeOnly else None, leash)
    if target is None:
        return False
    return placeGun(bot, ct, target)


def _gunnerFiringAtCore(
    ct: Controller, core: Position
) -> tuple[Position, Direction, Position] | None:
    """Nearest enemy gunner currently landing a shot on a core footprint tile.

    Engine ground truth (`can_fire_from`) only — this wants a shot that is
    actually connecting right now, not merely a gunner in range. Returns
    (gunner_pos, facing, core_tile) for the closest such gunner, else None.
    """
    me = ct.get_team()
    pos = ct.get_position()
    best: tuple[Position, Direction, Position] | None = None
    best_d = 10**9
    for eid in ct.get_nearby_units():
        if ct.get_team(eid) == me:
            continue
        if ct.get_entity_type(eid) != EntityType.GUNNER:
            continue
        gpos = ct.get_position(eid)
        facing = ct.get_direction(eid)
        for tile in coreFootprint(core):
            if gpos.distance_squared(tile) > GUNNER_RANGE_SQ:
                continue
            if not ct.can_fire_from(gpos, facing, EntityType.GUNNER, tile):
                continue
            d = abs(pos.x - gpos.x) + abs(pos.y - gpos.y)
            if d < best_d:
                best_d = d
                best = (gpos, facing, tile)
    return best


def _gunLineBlocked(bot, ct: Controller, tile: Position, core: Position) -> bool:
    """True if tile already holds an ally BARRIER, or is part of the core footprint."""
    for foot in coreFootprint(core):
        if tile.x == foot.x and tile.y == foot.y:
            return True
    ent = tileEntity(ct, tile, local_map=bot.hivemind.local_map)
    if not ent or ent.get("team") != ct.get_team():
        return False
    return ent.get("type") == EntityType.BARRIER


def _placeGunLineBarrier(ct: Controller, tile: Position) -> bool:
    """Build the barrier that blocks a gunner's line of fire. True if placed this turn.

    Barrier, not conveyor: this tile has no economic role, it exists purely
    to eat shots, and a barrier is 30 HP against a conveyor's 20 for the same
    ~3 Ti — 50% more shots the gunner has to spend to break through for equal
    cost on our side.
    """
    if not ct.is_in_vision(tile):
        return False
    if ct.get_tile_building_id(tile) is not None:
        return False
    if ct.get_action_cooldown() != 0:
        return False
    if ct.can_build_barrier(tile):
        ct.build_barrier(tile)
        return True
    return False


def blockGunLine(bot, ct: Controller) -> bool:
    """Barrier-cheap defence: drop a barrier in a gunner's current line of fire.

    Per `gunnerHasLos`'s chew-through model, sitting squarely in the shot
    works because the engine's own `can_fire_from` already refuses to fire
    through an occupied tile — the gunner must destroy the barrier before it
    can land another hit on the core. That is far cheaper than fielding a
    gunner to kill the threat outright (30+ Ti and several turns), so this
    runs ahead of the turret-response chain (`_reactEnemyTurret` / `defGun` /
    `defSentinel`) — those spend Ti to remove the threat, this only spends Ti
    to blunt it, and a cheaper answer should always be tried first.

    Gunners only. The LoS/chew-through machinery (`gunnerHasLos`,
    `tileThreatenedByGunner`) only exists for gunners in this codebase —
    sentinels/launchers aren't modeled the same way and fall through to the
    normal turret responses instead.

    Records the tile on `bot.gunLineBlockTile` so `maintainGunLineBlock` can
    follow up and rebuild it the moment it's gone, rather than this being a
    one-off placement that only gets reactively re-triggered once the gunner
    has already landed another hit through the gap.
    """
    core = bot.corePos
    if core is None or bot.inRelay or getattr(bot, "recheckingOre", False):
        return False
    hit = _gunnerFiringAtCore(ct, core)
    if hit is None:
        return False
    gpos, facing, core_tile = hit
    del gpos
    block = core_tile.add(facing.opposite())
    if not inBounds(ct, block):
        return False
    if _gunLineBlocked(bot, ct, block, core):
        return False  # already blocked
    if not _layableTile(ct, block):
        return False
    if _placeGunLineBarrier(ct, block):
        bot.gunLineBlockTile = (block.x, block.y)
        return True
    return False


def maintainGunLineBlock(bot, ct: Controller) -> bool:
    """Rebuild this guardian's own gun-line barrier the moment it's destroyed.

    `blockGunLine` is a one-off placement; without this, the block only ever
    gets reactively re-placed once `_gunnerFiringAtCore` proves the gunner
    has already landed *another* hit through the gap (that's the only way it
    notices a break), and only if whichever guardian's turn comes up next
    happens to have that exact tile in vision. This makes the specific
    builder that placed a block own its upkeep instead: check every turn the
    tile is in vision, rebuild the instant it's gone — cheap for us (~3 Ti a
    barrier), and every rebuild the enemy forces is shots and ammo they spent
    for nothing.

    Opportunistic, not a dedicated sortie: this only acts when the tile is
    already in vision and needs rebuilding, at the same priority tier as
    `blockGunLine` itself — it does not pull the guardian off patrol/economy
    to go stand next to a barrier that's still standing. Ownership needs no
    explicit release either: once the tile holds a barrier again, this is a
    no-op every turn until something breaks it again, and if the owning
    guardian dies, `bot.gunLineBlockTile` simply dies with it — any other
    guardian's own `blockGunLine` picks the gap back up fresh if the gunner
    is still connecting.
    """
    core = bot.corePos
    tile_xy = getattr(bot, "gunLineBlockTile", None)
    if core is None or tile_xy is None:
        return False
    if bot.inRelay or getattr(bot, "recheckingOre", False):
        return False
    tile = Position(tile_xy[0], tile_xy[1])
    if _gunLineBlocked(bot, ct, tile, core):
        return False  # still standing, nothing to do
    if not ct.is_in_vision(tile):
        return False  # can't confirm or act — wait for vision, don't chase it
    if not _layableTile(ct, tile):
        bot.gunLineBlockTile = None  # tile is occupied by something else now
        return False
    return _placeGunLineBarrier(ct, tile)


# Which parked turret to answer first: the ones that out-range the leash.
_TURRET_PRIORITY = {
    EntityType.SENTINEL: 0,  # reach sqrt(32) ≈ 5.7 — shells the core from 5–6
    EntityType.LAUNCHER: 1,  # reach sqrt(26) ≈ 5.1
    EntityType.GUNNER: 2,  # reach sqrt(13) ≈ 3.6 — defGun already covers these
}


def threateningTurret(bot, ct: Controller) -> Position | None:
    """Enemy turret parked within threat range of home, worst out-ranger first.

    Reads the HiveMind ledger rather than this unit's own vision: a builder sees
    sqrt(20) ≈ 4.5, so a guardian on its patrol ring usually cannot see the
    sentinel shelling the core from 6 — the core (sqrt(36)) or a passing builder
    spots it and the ledger carries it. Own vision is folded into `local_map` by
    `tick`, so this covers both.
    """
    from main import TURRET_MEMORY_TURNS, TURRET_THREAT_CHEBYSHEV

    core = bot.corePos
    if core is None or not TURRET_THREAT_CHEBYSHEV:
        return None
    hm = bot.hivemind
    me = ct.get_team()
    memory = TURRET_MEMORY_TURNS or 0
    best: Position | None = None
    best_key = None
    for (x, y), cell in hm.local_map.items():
        ent = cell.get("entity")
        if not ent or ent.get("team") == me:
            continue
        etype = ent.get("type")
        if etype not in TURRET_TYPES:
            continue
        if memory > 0:
            age = hm.global_turn - cell.get("last_seen_turn", -(10**9))
            if age > memory:
                continue
        epos = Position(x, y)
        d = chebyshevToCore(epos, core)
        if d > TURRET_THREAT_CHEBYSHEV:
            continue
        key = (_TURRET_PRIORITY.get(etype, 3), d, x, y)
        if best_key is None or key < best_key:
            best_key = key
            best = epos
    return best


def defSentinel(bot, ct: Controller) -> bool:
    """Answer an out-of-leash turret with our own sentinel, from inside the leash.

    Sits below `defGun` — anything close enough for a gunner is cheaper to
    answer that way — but above `defendHome`, whose no-enemy branch heals the
    core. Healing loses that trade: a sentinel hits for 18 and core heal is
    4 HP/turn, so killing it is the defence.
    """
    from main import DEF_SENTINEL

    if not DEF_SENTINEL or bot.corePos is None:
        return False
    from logistics import guardianLeash

    if bot.inRelay or getattr(bot, "recheckingOre", False):
        return False
    leash = guardianLeash(ct)
    if not nearCore(bot, ct, leash):
        return False
    target = threateningTurret(bot, ct)
    if target is None:
        return False
    if allySentinelCovering(ct, target) or allyGunCovering(ct, target):
        return False  # already answered — don't stack turrets on one threat
    return placeDefSentinel(bot, ct, target, bot.corePos, leash)


def clearBarriers(bot, ct: Controller) -> bool:
    """Attack nearest enemy barrier inside the guardian leash.

    Barriers beyond the leash are ignored on purpose: walking out to one puts the
    guardian outside the leash, so `runGuardian`'s leash step marches it home
    next turn and it ping-pongs forever without ever patrolling. The bound is the
    *current* leash (`guardianLeash`), which is wide while home is quiet — that
    is what lets a guardian reach a barrier cutting one of our belt lines — and
    snaps back to tight the moment the core sees a threat. Barriers beyond even
    the wide leash are the harassers' problem.
    """
    from logistics import guardianLeash

    me = ct.get_team()
    pos = ct.get_position()
    core = bot.corePos
    leash = guardianLeash(ct)
    best: Position | None = None
    best_d = 10**9
    for eid in ct.get_nearby_entities():
        if ct.get_team(eid) == me:
            continue
        if ct.get_entity_type(eid) != EntityType.BARRIER:
            continue
        epos = ct.get_position(eid)
        if core is not None and chebyshevToCore(epos, core) > leash:
            continue
        dist = abs(pos.x - epos.x) + abs(pos.y - epos.y)
        if dist < best_d:
            best_d = dist
            best = epos
    if best is None:
        return False

    if isAdjacent(pos, best):
        if ct.get_action_cooldown() == 0 and ct.can_fire(best):
            # Remember it if it's sitting on ore — _buildHarvesterOnClearedBarrier
            # picks this up once the barrier's HP actually runs out. Set on every
            # firing turn (idempotent, same tile) rather than only once so a
            # guardian that gets reassigned mid-clear still has it recorded.
            if ct.get_tile_env(best) == Environment.ORE_TITANIUM:
                bot.guardianOreBarrier = best
            ct.fire(best)
            return True
        return False

    from logistics import moveToward

    return moveToward(bot, ct, best)


def _buildHarvesterOnClearedBarrier(bot, ct: Controller) -> bool:
    """Build a harvester on ore this guardian's own clearBarriers just uncovered.

    `bot.guardianOreBarrier` is set by `clearBarriers` while it's actively
    firing on a barrier sitting on `Environment.ORE_TITANIUM`. Runs ahead of
    `clearBarriers` in `runGuardian` so the guardian claims the tile the turn
    after the barrier dies instead of immediately wandering off to the next
    barrier — it's still standing right there for free.

    Ti-starved holds position (returns True, consuming the turn) for up to
    `main.GUARDIAN_ORE_WAIT_TURNS` turns rather than giving up on the first
    empty bank — see that constant's docstring — then forgets the tile so
    `clearBarriers` is free to move on.

    Deliberately narrow: only acts while still adjacent to the remembered
    tile. If the guardian got pulled elsewhere (combat, a closer barrier) in
    the meantime this just quietly does nothing rather than walking back —
    unlike a belt gap, a single missed ore tile is not worth breaking
    position over, and a later pass (this guardian's own patrol, or any
    other builder's normal ore search) will pick it up regardless.
    """
    tile = getattr(bot, "guardianOreBarrier", None)
    if tile is None:
        return False

    # get_tile_env/get_tile_building_id both raise outside vision — same guard
    # _layableTile uses. A guardian pulled elsewhere by combat before this step
    # runs (avoidFire/defendHome/healAlly all sit above it) can lose vision of
    # its own remembered tile; don't crash, and don't forget it either — it's
    # cheap to just wait for vision to come back around.
    if not ct.is_in_vision(tile):
        return False

    # Not ore (shouldn't happen) or something else already sits there —
    # forget it either way, nothing left for this helper to do.
    if ct.get_tile_env(tile) != Environment.ORE_TITANIUM:
        bot.guardianOreBarrier = None
        bot.guardianOreWaitTurns = 0
        return False

    bid = ct.get_tile_building_id(tile)
    if bid is not None:
        # Barrier (or whatever is there) hasn't actually died yet — keep
        # waiting without spending guardianOreWaitTurns, that budget is for
        # the Ti-starved case below, not "still fighting the barrier."
        return False

    if not isAdjacent(ct.get_position(), tile):
        return False

    from main import GUARDIAN_ORE_WAIT_TURNS

    if ct.get_global_resources() < ct.get_harvester_cost():
        bot.guardianOreWaitTurns = getattr(bot, "guardianOreWaitTurns", 0) + 1
        if bot.guardianOreWaitTurns > GUARDIAN_ORE_WAIT_TURNS:
            bot.guardianOreBarrier = None
            bot.guardianOreWaitTurns = 0
            return False
        return True  # hold position; Ti may still arrive next turn

    if ct.get_action_cooldown() == 0 and ct.can_build_harvester(tile):
        ct.build_harvester(tile)
        bot.guardianOreBarrier = None
        bot.guardianOreWaitTurns = 0
        return True
    return False


def _isBeltOrCore(bot, ct: Controller, tile: Position, core: Position) -> bool:
    """True if tile carries an ally belt or is part of the ally core footprint."""
    for foot in coreFootprint(core):
        if tile.x == foot.x and tile.y == foot.y:
            return True
    ent = tileEntity(ct, tile, local_map=bot.hivemind.local_map)
    if not ent or ent.get("team") != ct.get_team():
        return False
    return ent.get("type") in BELT_TYPES


_RELAY_OWNER_CHEBYSHEV = 2


def _layableTile(ct: Controller, tile: Position) -> bool:
    """Empty ground a belt could be built on, confirmed by vision."""
    if not ct.is_in_vision(tile):
        return False
    if ct.get_tile_building_id(tile) is not None:
        return False
    return ct.get_tile_env(tile) == Environment.EMPTY


def _allyBuilderNear(ct: Controller, tile: Position) -> bool:
    """Visible ally builder (not self) within `_RELAY_OWNER_CHEBYSHEV` of tile.

    A relaying default owns a legitimately dangling head — its next belt simply is
    not laid yet — and must not have it patched out from under it. Cardinal
    adjacency was too tight: `relayToCore` steps the builder *onto* the belt it just
    placed and falls back to `moveToward` when the next path tile is distant, so the
    owner is regularly two or more tiles from its own head.

    Scans units rather than tiles: O(visible units) instead of O(radius squared).
    """
    me = ct.get_team()
    self_id = ct.get_id()
    for eid in ct.get_nearby_units():
        if eid == self_id or ct.get_team(eid) != me:
            continue
        if ct.get_entity_type(eid) != EntityType.BUILDER_BOT:
            continue
        p = ct.get_position(eid)
        if max(abs(p.x - tile.x), abs(p.y - tile.y)) <= _RELAY_OWNER_CHEBYSHEV:
            return True
    return False


def _danglingBeltEnds(
    bot,
    ct: Controller,
    *,
    anchor: Position | None = None,
    max_dist: int | None = None,
    respect_owner: bool = True,
    include_harvesters: bool = True,
) -> list[tuple[Position, Position]]:
    """Ally conveyors (and harvesters) within `max_dist` of `anchor` with a dead end.

    `anchor`/`max_dist` default to the core / `DEFEND_HOME_CHEBYSHEV` — reconnectBelts'
    near-core patrol repair. Pass `anchor=ct.get_position()` with a smaller radius for
    an opportunistic "finish a belt I happen to be standing next to" search instead
    (see `finishNearbyBelt`) — the candidates are still sorted nearest-core-first
    either way, since the most downstream break reconnects the most line regardless
    of why we're looking.

    `respect_owner=False` skips the `_allyBuilderNear` exclusion below. That check
    means "any *other* ally builder nearby" (not "the specific owner"), so it's a
    reasonable cheap proxy when the search is scanning broadly (reconnectBelts, core-
    anchored) but becomes a bystander-effect trap for a self-anchored opportunistic
    search: several idle units converging on the same known gap each see *each
    other* nearby and defer, and nobody ever acts, even though none of them is
    actually the relay's owner. finishNearbyBelt passes False for exactly this
    reason — the facing proof in `_reconnectFacing` is the real safety net there,
    not proximity to other units.

    Returns `(source, target)` pairs. Detection reads the HiveMind ledger, not own
    vision, for the same reason `threateningTurret` does: a guardian on its patrol
    ring cannot see the whole near-core belt run at sqrt(20).

    Conveyors only for the belt branch. A splitter rotates its output among
    three directions, so "the way it points" does not identify a break.

    `include_harvesters=True` adds a second source kind: an ally harvester has
    no facing at all (`ct.get_direction` raises `GameError` on one), so instead
    of one `facing`-derived target it contributes up to 4 cardinal-neighbor
    candidates, skipped entirely once `harvesterClaimedByAlly` says an ally belt
    already touches it — that harvester belongs to `finishNearbyHarvester`/
    `relayToCore` from here, not this cheap patch. This still can't scoop up a
    genuinely fresh, never-relayed harvester: `_reconnectFacing` only returns a
    facing when it can prove a hop toward the core or an existing connected
    belt within 1-2 tiles, so a harvester with nothing nearby to prove against
    simply produces no actionable candidate, same as a conveyor pointed into
    the void would.
    """
    core = bot.corePos
    if core is None:
        return []
    if anchor is None:
        anchor = core
    if max_dist is None:
        max_dist = DEFEND_HOME_CHEBYSHEV
    me = ct.get_team()
    pos = ct.get_position()
    out: list[tuple[int, int, Position, Position]] = []
    for (x, y), cell in bot.hivemind.local_map.items():
        ent = cell.get("entity")
        if not ent or ent.get("team") != me:
            continue
        if ent.get("type") != EntityType.CONVEYOR:
            continue
        facing = ent.get("direction")
        if facing is None or facing == Direction.CENTRE:
            continue
        belt = Position(x, y)
        if max(abs(belt.x - anchor.x), abs(belt.y - anchor.y)) > max_dist:
            continue
        target = belt.add(facing)
        if not inBounds(ct, target):
            continue
        # Already feeding a belt or the core — not a break.
        if _isBeltOrCore(bot, ct, target, core):
            continue
        # Layable only. A belt aimed at a wall or a building is wrong-facing,
        # which is clearBeltObstacle's problem, not a cut line.
        if not _layableTile(ct, target):
            continue
        # A default mid-relay has a legitimately dangling head — leave it alone
        # (unless the caller has already decided proximity isn't a useful signal
        # here — see respect_owner above).
        if respect_owner and _allyBuilderNear(ct, target):
            continue
        out.append((chebyshevToCore(belt, core), manhattan(pos, target), belt, target))

    if include_harvesters:
        for (x, y), cell in bot.hivemind.local_map.items():
            ent = cell.get("entity")
            if not ent or ent.get("team") != me:
                continue
            if ent.get("type") != EntityType.HARVESTER:
                continue
            harv = Position(x, y)
            if max(abs(harv.x - anchor.x), abs(harv.y - anchor.y)) > max_dist:
                continue
            # Already claimed — finishNearbyHarvester/relayToCore own it now.
            if harvesterClaimedByAlly(ct, harv, local_map=bot.hivemind.local_map):
                continue
            for d in CARDINALS:
                target = harv.add(d)
                if not inBounds(ct, target):
                    continue
                if _isBeltOrCore(bot, ct, target, core):
                    continue
                if not _layableTile(ct, target):
                    continue
                if respect_owner and _allyBuilderNear(ct, target):
                    continue
                out.append(
                    (chebyshevToCore(harv, core), manhattan(pos, target), harv, target)
                )

    out.sort(key=lambda t: (t[0], t[1]))
    return [(source, target) for _d, _m, source, target in out]


def _reconnectFacing(
    bot, ct: Controller, target: Position, core: Position
) -> Direction | None:
    """Provable next hop for a patch belt, or None when it cannot be proven.

    Three lookaheads, all of which *see* the far side of the gap:

    * **Core footprint** — a cardinal neighbour is one of the core's 4 footprint
      tiles. Face it unconditionally; the core is always the correct terminus
      regardless of Chebyshev distance. Needed because a tile diagonally against
      a footprint corner has *two* cardinal neighbours tied at the same Chebyshev
      distance as itself — neither is ever "strictly closer," so the plain
      distance check below can never fire there, and a belt approaching the core
      diagonally (routine for A*-laid relays) would otherwise never get patched.
    * **1 tile, radial** — a cardinal neighbour strictly closer to the core
      already holds an ally belt. Face it; the gap closes exactly.
    * **1 tile, off-axis** — a cardinal neighbour holds an ally belt that isn't
      locally closer to the core (a merge onto a line approaching from another
      angle, not straight toward us) but is *provably* connected via
      `_relayReachesCore`'s own forward-walk of stored facings — the same proof
      `logistics.pickRelayPath`'s merge goal already trusts. Plain Chebyshev
      distance can't tell a genuine non-radial merge from a random unconnected
      belt; the forward-walk can.
    * **2 tiles** — the radial neighbour is empty but layable, and the tile
      beyond it in the same direction reconnects. Face it anyway: we are filling
      the first of two, and the second is patched on a later turn from the same
      evidence.

    **There is deliberately no crow-flies fallback.** `facingIntoCore` used to
    fill this role — a heading at the core anchor with no route behind it. The
    belt it produced pointed at another empty tile, so it became a dangling end
    itself next turn and the guardian marched a line of belts at the core one per
    turn — and because those belts agree with no route, whatever `relayToCore`
    later planned disagreed and `clearBeltObstacle` tore them out again. Where the
    hop cannot be proven by one of the three lookaheads above, build nothing:
    `maintainHarvester` → `relayToCore` owns that repair and has an actual path
    to work from.
    """
    from logistics import _relayReachesCore, travelFacing

    footprint = coreFootprint(core)
    here = chebyshevToCore(target, core)

    for d in CARDINALS:
        near = target.add(d)
        if not inBounds(ct, near):
            continue
        if any(near.x == f.x and near.y == f.y for f in footprint):
            return travelFacing(target, near)
        if chebyshevToCore(near, core) < here and _isBeltOrCore(bot, ct, near, core):
            return travelFacing(target, near)
        if _isBeltOrCore(bot, ct, near, core) and _relayReachesCore(
            bot, ct, near, core
        ):
            return travelFacing(target, near)

    for d in CARDINALS:
        near = target.add(d)
        far = near.add(d)
        if not inBounds(ct, near) or not inBounds(ct, far):
            continue
        if chebyshevToCore(far, core) >= here:
            continue
        if not _layableTile(ct, near):
            continue
        if _isBeltOrCore(bot, ct, far, core):
            return travelFacing(target, near)

    return None


def _tryPatchDanglingBelts(
    bot,
    ct: Controller,
    candidates: list[tuple[Position, Position]],
    core: Position,
    *,
    allow_walk: bool = True,
) -> bool:
    """Shared placement/walk logic for a dangling-belt-end candidate list.

    Resolves the facing before committing to anything, including the walk: a break
    whose next hop cannot be proven is not ours to fix, so it must not draw the
    bot toward it either. Candidates are tried in order and a failed placement
    falls through to the next rather than retrying, so it cannot thrash.

    `allow_walk=False` keeps the "place if already adjacent" pass (a single
    build action, no movement — safe on any turn) but skips the trailing walk.
    Callers pass this while something hostile is in vision: closing a gap that
    is already free to patch shouldn't wait on the enemy leaving, but marching
    toward a *distant* break to chase it is a real repositioning risk and stays
    off the table until the coast is clear.
    """
    from logistics import moveToward, placeRelayBelt

    actionable: list[tuple[Position, Direction]] = []
    for _belt, target in candidates:
        facing = _reconnectFacing(bot, ct, target, core)
        if facing is None:
            continue
        actionable.append((target, facing))
    if not actionable:
        return False

    pos = ct.get_position()
    for target, facing in actionable:
        if not isAdjacent(pos, target):
            continue
        if placeRelayBelt(ct, target, facing) == "placed":
            return True

    if not allow_walk:
        return False
    # Nothing adjacent to act on — walk to the most downstream provable break.
    return moveToward(bot, ct, actionable[0][0])


def reconnectBelts(bot, ct: Controller, *, allow_walk: bool = True) -> bool:
    """Patch a cut belt line near the core back toward the core, one tile a turn.

    Guardian-only idle work. Exists because the normal repair path is
    *ore-anchored*: `maintainHarvester` re-enters `relayToCore` and the
    watermark rebuilds from the first gap, but a guardian's `_allowOreSite`
    confines it to ore inside the leash. A break near the core that belongs to
    a harvester outside the leash is therefore unreachable — the guardian can
    never claim the ore that would carry it there. Anchoring on the belt
    instead of the ore is the whole point.

    Greedy and deliberately unplanned: no A*, so a blocked coreward tile cannot be
    routed around. Candidates are tried in order and a failed placement falls through
    to the next rather than retrying, so it cannot thrash. Multi-tile gaps are walked
    one tile per turn and only while each step stays layable.

    `allow_walk=False` (passed by `runGuardian` while `underAttack`) restricts
    this to the zero-movement adjacent-patch case — see `_tryPatchDanglingBelts`.
    A belt actively being shot needs patching *most* on those turns: every turn
    it stays cut is lost Ti throughput on top of the belt's own repair cost, and
    patching it costs nothing but the turn's build action if the guardian is
    already standing right there.
    """
    core = bot.corePos
    if core is None or bot.inRelay or getattr(bot, "recheckingOre", False):
        return False

    from logistics import guardianLeash

    candidates = _danglingBeltEnds(bot, ct, max_dist=guardianLeash(ct))
    if not candidates:
        return False
    return _tryPatchDanglingBelts(bot, ct, candidates, core, allow_walk=allow_walk)


def finishNearbyBelt(bot, ct: Controller, *, allow_walk: bool = True) -> bool:
    """Opportunistically finish a dangling ally belt end near this bot's own tile.

    `reconnectBelts`/`_danglingBeltEnds` are core-leash-anchored — guardian patrol
    duty near the core only, regardless of where the guardian itself is standing.
    This is the self-anchored counterpart: any builder (guardian or default) that
    happens to be next to an abandoned relay tail can finish it, no matter how far
    that tail is from the core.

    Exists because a relay that dies mid-build (destroyed builder, no on-death hook
    anywhere in this bot — see main.py's per-entity `run` dispatch) never reaches
    one of relayToCore's own abort sites, so it never reports to
    `OrphanRelayRegistry` either — nothing else will ever revisit it unless another
    unit happens to notice the dangling end itself. Small fixed radius
    (`DANGLING_BELT_SELF_CHEBYSHEV`) on purpose: this is a cheap "I'm right here"
    check, not a search — it must never pull a builder off its own task chasing a
    belt tile it merely glimpsed from a distance.

    Same facing-proof as `reconnectBelts` (`_reconnectFacing`, core-relative, no
    crow-flies guessing) and no A* — deliberately opportunistic, not a substitute
    for `relayToCore`'s own planned repair. Passes `respect_owner=False` to
    `_danglingBeltEnds`: several idle units converging on the same known gap
    (exactly the scenario this function exists for) would otherwise each see
    *each other* nearby and defer, and nobody would ever act.

    `allow_walk=False` — see `reconnectBelts`/`_tryPatchDanglingBelts`: keeps the
    zero-movement adjacent patch available while hostile, drops the walk.
    """
    from main import DANGLING_BELT_SELF_CHEBYSHEV

    core = bot.corePos
    if core is None or bot.inRelay or getattr(bot, "recheckingOre", False):
        return False

    candidates = _danglingBeltEnds(
        bot,
        ct,
        anchor=ct.get_position(),
        max_dist=DANGLING_BELT_SELF_CHEBYSHEV,
        respect_owner=False,
    )
    if not candidates:
        return False
    return _tryPatchDanglingBelts(bot, ct, candidates, core, allow_walk=allow_walk)


def _patrolWaypoints(bot, ct: Controller) -> list[Position]:
    """Mid-side tiles around the 2x2 footprint at patrol offset, one per side.

    Offset is `main.GUARDIAN_PATROL_OFFSET` — one tile inside
    `DEFEND_HOME_CHEBYSHEV`, not on it, so a single A*-forced detour around an
    obstacle between waypoints still has margin before `_guardianLeashOk`
    (which grants no slack while merely patrolling) trips and
    `moveTowardLeash` yanks the guardian home mid-cycle. Still meaningfully
    wider than the old fixed offset, giving a guardian's own vision
    (~Chebyshev 4.5) a real chance to reach out toward
    `TURRET_THREAT_CHEBYSHEV`(6) instead of sitting right against the core.

    Each side tries its straight mid-side tile first, then the two nearest
    diagonal corners on that same side if the mid-side tile is walled —
    confirmed on a real map (`heart`) that a core's N/S mid-side tiles can
    sit inside terrain, silently dropping `patrolHome` to only 2 of 4
    waypoints and leaving a guardian with nowhere to cycle to but back and
    forth between the two that remained. Adjacent sides share their nearest
    corner (e.g. N and W both list the NW corner first), so a corner already
    claimed by an earlier side is skipped rather than reused — otherwise two
    walled mid-side tiles could silently collapse to the *same* fallback
    tile, still short of 4 distinct waypoints despite the fallback existing.
    """
    core = bot.corePos
    if core is None:
        return []
    from main import GUARDIAN_PATROL_OFFSET

    o = GUARDIAN_PATROL_OFFSET
    local_map = bot.hivemind.local_map
    claimed: set[tuple[int, int]] = set()

    def usable(tile: Position) -> bool:
        key = (tile.x, tile.y)
        if key in claimed:
            return False
        if not inBounds(ct, tile):
            return False
        return walkPassable(ct, tile, local_map=local_map)

    # (mid-side tile, [nearest-corner fallbacks]) per side.
    sides = [
        (
            Position(core.x, core.y - o),  # N
            [Position(core.x - o, core.y - o), Position(core.x + 1 + o, core.y - o)],
        ),
        (
            Position(core.x + 1 + o, core.y),  # E
            [Position(core.x + 1 + o, core.y - o), Position(core.x + 1 + o, core.y + 1 + o)],
        ),
        (
            Position(core.x, core.y + 1 + o),  # S
            [Position(core.x - o, core.y + 1 + o), Position(core.x + 1 + o, core.y + 1 + o)],
        ),
        (
            Position(core.x - o, core.y),  # W
            [Position(core.x - o, core.y - o), Position(core.x - o, core.y + 1 + o)],
        ),
    ]
    out: list[Position] = []
    for primary, fallbacks in sides:
        if usable(primary):
            out.append(primary)
            claimed.add((primary.x, primary.y))
            continue
        for alt in fallbacks:
            if usable(alt):
                out.append(alt)
                claimed.add((alt.x, alt.y))
                break
    return out


def patrolHome(bot, ct: Controller) -> bool:
    """Cycle leash waypoints around ally core for rear vision coverage."""
    if bot.corePos is None:
        return False
    waypoints = _patrolWaypoints(bot, ct)
    if not waypoints:
        return False

    idx = getattr(bot, "patrolIdx", 0) % len(waypoints)
    target = waypoints[idx]
    pos = ct.get_position()
    if pos.x == target.x and pos.y == target.y:
        bot.patrolIdx = (idx + 1) % len(waypoints)
        target = waypoints[bot.patrolIdx]
    elif getattr(bot, "stuck", 0) >= 3:
        bot.patrolIdx = (idx + 1) % len(waypoints)
        target = waypoints[bot.patrolIdx]

    from logistics import moveToward

    return moveToward(bot, ct, target)
