"""Repair, and patching cut belt lines back together.

What is left of baron's `defence.py` after the guardian role went away. Baron
used this module for home defence orchestration — `defGun`, `defSentinel`,
`blockGunLine`, `clearBarriers`, `patrolHome`, `threateningTurret` — every one of
which existed because two builders in five were garrison units with a leash and
a patrol ring. Bandit has no such role, and answers turrets with `econwar`'s
surplus-gated placements instead, so all of it is gone.

Two things survive, plus one arrival:

  * `repair` — baron's `logistics.healAlly`, moved here and unchanged. Now run by
    *every* builder at priority 3, with no HP threshold and no near-core gate,
    because there is no guardian reserving the job.
  * the dangling-belt cluster (`_danglingBeltEnds`, `_reconnectFacing`,
    `_tryPatchDanglingBelts`, `finishNearbyBelt`) — self-anchored belt repair,
    which was never guardian-specific to begin with. `reconnectBelts`, its
    core-leash-anchored sibling, is dropped: with no leash there is nothing it
    could reach that `finishNearbyBelt` and `logistics.adoptDanglingBelt` cannot.
  * `_isBeltOrCore` / `_allyBuilderNear` stay because `logistics` imports them.
"""

from __future__ import annotations

from fcode import Controller, Direction, EntityType, Environment, Position

from geometry import (
    BELT_TYPES,
    DEFEND_HOME_CHEBYSHEV,
    chebyshevToCore,
    coreFootprint,
    isAdjacent,
    tileEntity,
)
from pathing import CARDINALS, inBounds, manhattan


def repair(bot, ct: Controller) -> bool:
    """Heal the highest-value damaged ally building cardinally adjacent to us.

    Baron ran this as `logistics.healAlly`, one step in a guardian's defensive
    chain and a late chore for everyone else. In bandit it is priority 3 for
    every builder, above even a committed relay, on the reasoning that a bot
    whose economy is its weapon cannot afford to walk past damage: the belt line
    being chewed is the thing paying for everything else, and 4 HP a turn spent
    now is cheaper than rebuilding.

    Order is `CORE > GUNNER > SENTINEL > HARVESTER > CONVEYOR` — by replacement
    cost and by how much is lost while the thing is down. No HP threshold:
    baron gated the core case on `HP < 500` because only guardians could act on
    it and they had a patrol to get back to. Any damage is worth a turn here.

    Adjacency only, deliberately. This is "I am standing next to something
    hurt", not a callout — nothing in bandit walks toward damage, because a
    builder crossing the map to heal is a builder not building, and the thing
    doing the damage is usually still there when it arrives.
    """
    if ct.get_action_cooldown() != 0:
        return False
    pos = ct.get_position()
    priority = (
        EntityType.CORE,
        EntityType.GUNNER,
        EntityType.SENTINEL,
        EntityType.HARVESTER,
        EntityType.CONVEYOR,
    )
    for want in priority:
        for d in CARDINALS:
            check = pos.add(d)
            if not inBounds(ct, check):
                continue
            if not ct.can_heal(check):
                continue
            bid = ct.get_tile_building_id(check)
            bot_id = ct.get_tile_builder_bot_id(check)
            eid = bid if bid is not None else bot_id
            if eid is None:
                continue
            if ct.get_entity_type(eid) != want:
                continue
            if ct.get_team(eid) != ct.get_team():
                continue
            ct.heal(check)
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
) -> list[tuple[Position, Position]]:
    """Ally conveyors within `max_dist` of `anchor` whose output tile leads nowhere.

    `anchor`/`max_dist` default to the core / `DEFEND_HOME_CHEBYSHEV`, which in
    baron served a guardian's near-core patrol repair. Bandit has no patrol and
    both of its callers pass `anchor=ct.get_position()` explicitly — the default
    is kept only so the signature stays honest about what it means.

    Both callers are self-anchored but want different deference:
    `finishNearbyBelt` passes `respect_owner=False`, `logistics.adoptDanglingBelt`
    passes True. `respect_owner=False` skips the `_allyBuilderNear` exclusion
    below. That check means "any *other* ally builder nearby" (not "the specific
    owner"), so it is a reasonable cheap proxy when the caller is about to commit
    to a whole route (adoption — colliding there costs both builders their
    pathing) but becomes a bystander-effect trap for a one-tile opportunistic
    patch: several idle units converging on the same known gap each see *each
    other* nearby and defer, and nobody ever acts, even though none of them is
    actually the relay's owner. For the cheap case the facing proof in
    `_reconnectFacing` is the real safety net, not proximity to other units.

    Candidates are sorted nearest-core-first regardless of anchor, since the most
    downstream break reconnects the most line.

    Returns `(belt, output)` pairs. Detection reads the HiveMind ledger, not own
    vision: a builder cannot see a whole belt run at sqrt(20), and the break it
    most wants to fix is often the one just outside that.

    Conveyors only. A splitter rotates its output among three directions, so
    "the way it points" does not identify a break.
    """
    from logistics import tileThreatenedByGunner

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
        if tileThreatenedByGunner(ct, target):
            continue
        out.append((chebyshevToCore(belt, core), manhattan(pos, target), belt, target))
    out.sort(key=lambda t: (t[0], t[1]))
    return [(belt, target) for _d, _m, belt, target in out]


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
) -> bool:
    """Shared placement/walk logic for a dangling-belt-end candidate list.

    Resolves the facing before committing to anything, including the walk: a break
    whose next hop cannot be proven is not ours to fix, so it must not draw the
    bot toward it either. Candidates are tried in order and a failed placement
    falls through to the next rather than retrying, so it cannot thrash.
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

    # Nothing adjacent to act on — walk to the most downstream provable break.
    return moveToward(bot, ct, actionable[0][0])


def finishNearbyBelt(bot, ct: Controller) -> bool:
    """Opportunistically finish a dangling ally belt end near this bot's own tile.

    The cheap half of bandit's belt-repair pair. This lays *one* proven tile and
    returns; `logistics.adoptDanglingBelt` takes over a whole abandoned route.
    Split by gap size, not by priority — a one-tile pop does not need a relay
    commitment, and a half-built route is not fixed by one tile. Adoption runs
    first in the chain; this catches what is left.

    Exists because a relay that dies mid-build (destroyed builder, no on-death
    hook anywhere in this bot — see main.py's per-entity `run` dispatch) never
    reaches one of relayToCore's own abort sites, so it never publishes an orphan
    hint either. Nothing will ever revisit it unless another unit happens to
    notice the dangling end itself. Small fixed radius
    (`DANGLING_BELT_SELF_CHEBYSHEV`) on purpose: this is a cheap "I'm right here"
    check, not a search — it must never pull a builder off its own task chasing a
    belt tile it merely glimpsed from a distance.

    Facing-proved via `_reconnectFacing` (core-relative, no crow-flies guessing)
    and no A* — deliberately opportunistic, not a substitute for `relayToCore`'s
    own planned repair. Passes `respect_owner=False` to `_danglingBeltEnds`:
    several idle units converging on the same known gap (exactly the scenario
    this function exists for) would otherwise each see *each other* nearby and
    defer, and nobody would ever act.
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
    return _tryPatchDanglingBelts(bot, ct, candidates, core)
