"""Builder logistics: ore selection, harvesters, inbound relay.

Bandit's economy engine, ported from baron with the guardian/harasser support
machinery removed. What changed, and why:

  * `_harvesterOwnerAllowed` is hardcoded True — stealing an enemy harvester's
    output by relaying it is bandit's default behavior, not a toggle.
  * `_allowOreSite` is hardcoded True — no leash, no near-core phase, no roam
    cap. Ore candidates come from the team ledger unioned with the static map
    table, and separation between builders is a *score* (see
    `_oreConvenienceKey`'s spread term), never a boundary.
  * The 3-slot orphan registry collapsed to one last-write-wins hint slot, and
    `adoptDanglingBelt` is new: an idle builder near an abandoned belt line takes
    it over and finishes it through the ordinary `relayToCore` machine.
  * Gone: the guardian leash (`guardianLeash`, `coreThreatened`, `setCoreThreat`,
    `moveTowardLeash`), turret reactions (`enemyTurretInVision`,
    `nearestEnemyTurret`, `contestTurret`), `defendHome`/`_safeCoreStandTile`,
    and the `ORE_STAGGER` selection mode with its `_defaultOreRank` index.
    `healAlly` moved to `defence.repair`.
"""

from __future__ import annotations

from fcode import Controller, Direction, EntityType, Environment, Position

from geometry import (
    BELT_TYPES,
    coreFootprint,
    isAdjacent,
    tileEntity,
)
from pathing import (
    CARDINALS,
    astarFromSeeds,
    findPathAroundObstruction,
    inBounds,
    manhattan,
    nearestCardinal,
    relayPassable,
    walkPassable,
)

PATH_MAX_ITERS = 800

_ORE_BLOCK_CLEAR = (
    EntityType.GUNNER,
    EntityType.SENTINEL,
    EntityType.CONVEYOR,
    EntityType.SPLITTER,
    EntityType.LAUNCHER,
    EntityType.BARRIER,
)

# Enemy types that actually block an ore tile, for pickEmptyOre's ledger-memory
# check — the same set checkOre can't get past (_ORE_BLOCK_CLEAR) plus CORE.
# BUILDER_BOT is deliberately excluded: it's a mobile unit, not a blocker
# (get_tile_building_id is None for one), so a single fly-by sighting must not
# disqualify the node. HARVESTER is handled separately (team-agnostic — any
# harvester, ally or enemy, means the node is already claimed).
_ORE_ENEMY_BLOCKERS = _ORE_BLOCK_CLEAR + (EntityType.CORE,)


def clearRelay(bot) -> None:
    bot.inRelay = False
    bot.relayOrigin = None
    bot.relayPath = None
    bot.relayBuiltTo = -1
    bot.relayStuck = 0
    # Threat blacklist is scoped to one relay attempt: a tile is blocked because a
    # gunner covered it *then*, and that gunner may be gone by the next relay.
    bot.relayThreatBlocked = set()


def abortRelay(bot) -> None:
    clearRelay(bot)


def _escalateThreatAbort(bot, site: Position) -> None:
    """Blacklist `site` into skippedOres after repeated threat-driven aborts.

    A relay walled off by a firing line replans from relayOrigin and finds no
    route, aborts, and — because clearRelay wipes relayThreatBlocked — the
    next attempt from the same origin rediscovers the identical dead end.
    bot.stuck/relayStuck don't catch this: the builder keeps genuinely
    moving/acting each lap, it's the *node* that's bad, not the builder. See
    CONTEXT.md's RelayToCore section ("Two known limits") — this is that
    section's own suggested fix.
    """
    from main import RELAY_THREAT_ABORT_LIMIT

    if RELAY_THREAT_ABORT_LIMIT is None or RELAY_THREAT_ABORT_LIMIT <= 0:
        return
    key = (site.x, site.y)
    if getattr(bot, "threatAbortSite", None) == key:
        bot.threatAbortCount = getattr(bot, "threatAbortCount", 0) + 1
    else:
        bot.threatAbortSite = key
        bot.threatAbortCount = 1
    if bot.threatAbortCount >= RELAY_THREAT_ABORT_LIMIT:
        if not hasattr(bot, "skippedOres"):
            bot.skippedOres = set()
        bot.skippedOres.add(key)
        bot.oreSite = None
        bot.threatAbortSite = None
        bot.threatAbortCount = 0


# ------------------------------------------------------------------
# Orphan relay hint — store slot 1 (main.SLOT_ORPHAN_HINT)
# ------------------------------------------------------------------
# `abortRelay` above wipes only the aborting builder's own state; nothing else
# ever learns the harvester it was relaying from still needs a belt unless
# this same builder happens to re-find that exact tile itself later. This is a
# small, team-wide "still needs a belt" mailbox so any *other* idle builder can
# pick it up.
#
# Baron spent three store slots on this with dedup and stalest-entry eviction,
# because its guardians were leash-bound: a broken relay outside the leash was
# genuinely unreachable for most of the crew, so the registry had to hold
# several tiles at once and hold them reliably. Bandit has no leash — every
# builder roams the whole map and scans its own vision and ledger freely — so
# nearly all of that justification is gone, and what remains is the narrow case
# of a dead relay whose origin no other builder happens to wander past.
#
# Hence one slot, last-write-wins, no dedup and no eviction policy: a second
# report simply overwrites the first, and a stale entry is discarded on read via
# TTL or a vision check. Losing a report is acceptable here in a way it was not
# in baron. `adoptDanglingBelt` and `finishNearbyBelt` are the primary recovery
# paths now; this is the long-range backstop behind them.
#
# UNDER REVIEW: if match observation shows this slot rarely populates, or
# populates and is never consumed, delete this whole section and free slot 1.
#
# Record = one u32: an explicit occupied bit (so a real (0,0)-at-turn-0
# record is never confused with an empty slot's bare 0) plus 7-bit x, 7-bit y
# (map dimensions top out at 40x32 — mirrors pack_cores's coordinate width in
# hivemind.py, not the ledger's narrower 5-bit one in vision_v2.py) and a
# 10-bit report turn (MAX_TURNS=1000 fits with room to spare — no wraparound
# handling needed).
_ORPHAN_TURN_BITS = 10
_ORPHAN_TURN_MASK = (1 << _ORPHAN_TURN_BITS) - 1
_ORPHAN_COORD_BITS = 7
_ORPHAN_COORD_MASK = (1 << _ORPHAN_COORD_BITS) - 1
_ORPHAN_OCCUPIED_BIT = 1 << (_ORPHAN_COORD_BITS * 2 + _ORPHAN_TURN_BITS)


def _orphanSlot() -> int:
    from main import SLOT_ORPHAN_HINT

    return SLOT_ORPHAN_HINT


def _packOrphan(x: int, y: int, turn: int) -> int:
    return (
        _ORPHAN_OCCUPIED_BIT
        | ((x & _ORPHAN_COORD_MASK) << (_ORPHAN_COORD_BITS + _ORPHAN_TURN_BITS))
        | ((y & _ORPHAN_COORD_MASK) << _ORPHAN_TURN_BITS)
        | (turn & _ORPHAN_TURN_MASK)
    )


def _unpackOrphan(val: int) -> tuple[int, int, int] | None:
    """(x, y, reportedAtTurn), or None if the slot is empty."""
    if val == 0:
        return None
    turn = val & _ORPHAN_TURN_MASK
    y = (val >> _ORPHAN_TURN_BITS) & _ORPHAN_COORD_MASK
    x = (val >> (_ORPHAN_COORD_BITS + _ORPHAN_TURN_BITS)) & _ORPHAN_COORD_MASK
    return x, y, turn


def _readOrphanHint(ct: Controller) -> tuple[int, int, int] | None:
    """(x, y, reportedAtTurn) from the hint slot, or None if empty."""
    return _unpackOrphan(ct.read_store(_orphanSlot()))


def _rememberedAllyHarvester(bot, ct: Controller, tile: Position) -> bool:
    """True if `tile` is known — live or remembered — to hold an ally harvester.

    Mirrors the fog-tolerant live-vision/local_map fallback pattern
    `pickMaintainOre`/`_pickRecheckOre` already use elsewhere in this file:
    ground truth when we have it, last-remembered state otherwise. The
    reporting builder is very often *not* standing at `relayOrigin` anymore
    by the time an abort fires (it aborts at whatever tile along the route it
    got stuck on), so requiring live vision here would silently drop most
    reports — but this must not accept an arbitrary tile either, since
    `relayOrigin` can fall back to the builder's own bare position
    (`relayToCore`'s `bot.oreSite or ct.get_position()`), which is why this
    checks the *entity type*, not just presence.
    """
    ent = tileEntity(ct, tile, local_map=bot.hivemind.local_map)
    return (
        ent is not None
        and ent.get("type") == EntityType.HARVESTER
        and ent.get("team") == ct.get_team()
    )


def reportOrphanRelay(bot, ct: Controller, tile: Position) -> None:
    """Broadcast `tile` (a harvester whose relay we're giving up on) for pickup.

    Last-write-wins, single slot: a later report simply overwrites an earlier
    one. Baron dedup'd by tile and evicted the stalest of three entries; with one
    slot there is nothing to dedup against and nothing to choose between, and a
    lost report costs a hint, not a harvester — the tile is still discoverable by
    `adoptDanglingBelt`, `finishNearbyBelt`, or any builder's own ore scan.

    Guarded by `_rememberedAllyHarvester` for the same reason baron guards it:
    `relayOrigin` falls back to the builder's bare position when there was no ore
    site, so an unguarded write would publish an arbitrary tile.
    """
    if not _rememberedAllyHarvester(bot, ct, tile):
        return  # not actually a harvester we own — nothing to report
    turn = ct.get_current_round() & _ORPHAN_TURN_MASK
    ct.write_store(_orphanSlot(), _packOrphan(tile.x, tile.y, turn))


def clearOrphanRelay(ct: Controller, tile: Position) -> None:
    """Clear the hint if it names `tile` — that relay is done.

    Checks the tile before clearing: with one shared slot, an unconditional
    write would let a builder finishing its own relay wipe an unrelated hint
    somebody else had just published.
    """
    rec = _readOrphanHint(ct)
    if rec is not None and (rec[0], rec[1]) == (tile.x, tile.y):
        ct.write_store(_orphanSlot(), 0)


def _reportAndAbort(bot, ct: Controller) -> None:
    """abortRelay, but first offer the harvester up for another builder to finish.

    Only called from relayToCore's builder-local / fog-dependent abort sites —
    a different builder, with different fog knowledge or a different approach
    angle, genuinely has a shot where this one didn't. Deliberately *not*
    called from the enemy-core-adjacent abort (every builder re-derives the
    same geometric verdict — reporting it just thrashes the hint) or the
    titanium-starved placement abort (every builder shares one bank and fails
    identically; it self-resolves once Ti arrives, so reporting it publishes a
    tile that isn't actually stuck).
    """
    if bot.relayOrigin is not None:
        reportOrphanRelay(bot, ct, bot.relayOrigin)
        if bot.relayThreatBlocked:
            _escalateThreatAbort(bot, bot.relayOrigin)
    abortRelay(bot)


def _pickOrphanRelay(bot, ct: Controller) -> Position | None:
    """The hinted orphan tile if it still looks usable, else None.

    Applies the same filters every other candidate in `pickMaintainOre` /
    `_pickRecheckOre` goes through — `_allowOreSite` and `skippedOres` (so a
    builder that already gave up on this exact tile doesn't immediately re-pick
    it here). Clears the slot when live vision proves the record stale
    (TTL-expired, or the harvester is visibly gone); a not-in-vision record is
    trusted as-is, same fog tolerance as the rest of this file.
    """
    from main import ORPHAN_HINT_TTL_TURNS

    rec = _readOrphanHint(ct)
    if rec is None:
        return None
    x, y, turn = rec
    if ct.get_current_round() - turn > ORPHAN_HINT_TTL_TURNS:
        ct.write_store(_orphanSlot(), 0)
        return None
    if (x, y) in getattr(bot, "skippedOres", set()):
        return None
    tile = Position(x, y)
    if not _allowOreSite(bot, tile):
        return None
    if ct.is_in_vision(tile):
        bid = ct.get_tile_building_id(tile)
        if (
            bid is None
            or ct.get_entity_type(bid) != EntityType.HARVESTER
            or ct.get_team(bid) != ct.get_team()
        ):
            ct.write_store(_orphanSlot(), 0)  # visibly not a live ally harvester
            return None
        if allyBuilderNearOre(ct, tile):
            return None  # someone's already there working it
    return tile


def relayEverComplete(ct: Controller) -> bool:
    """True after the team has finished at least one inbound relay (slot 0 bit 0).

    Still masked rather than a bare `!= 0`, even though bandit writes no other
    bit into slot 0 — baron packed CORE_THREAT_BIT alongside it, and keeping the
    mask means slot 0 stays extensible without a silent misread waiting for
    whoever adds the next bit.

    **Currently has no caller.** In baron this gated the guardian's chore
    strictness ("don't let an enemy builder in vision keep a guardian off ore
    before the economy exists"), and bandit has no such gate — its builders never
    stop working. `markRelayComplete` still writes the bit, so the signal is live
    and free to read; this is the reader, kept so the slot has a documented
    meaning rather than becoming an unexplained write. Worth deleting along with
    the write if nothing has claimed it after a few matches.
    """
    from main import RELAY_COMPLETE_BIT, SLOT_RELAY_COMPLETE

    return (ct.read_store(SLOT_RELAY_COMPLETE) & RELAY_COMPLETE_BIT) != 0


def markRelayComplete(ct: Controller) -> None:
    """Set slot 0's relay bit, leaving the rest of the slot alone.

    Read-modify-write rather than a bare `write_store(0, 1)`: nothing else
    currently occupies slot 0, but store writes are buffered a full round and
    this fires from every builder, so a non-destructive write costs nothing and
    keeps the slot safe to share later.
    """
    from main import RELAY_COMPLETE_BIT, SLOT_RELAY_COMPLETE

    val = ct.read_store(SLOT_RELAY_COMPLETE)
    if not (val & RELAY_COMPLETE_BIT):
        ct.write_store(SLOT_RELAY_COMPLETE, val | RELAY_COMPLETE_BIT)


def isCoreAdjacent(
    ct: Controller, pos: Position, core_pos: Position | None
) -> bool:
    """True if pos is adjacent to an *ally* core (vision check or ally core_pos)."""
    for d in CARDINALS:
        neighbor = pos.add(d)
        if not inBounds(ct, neighbor):
            continue
        if ct.is_in_vision(neighbor):
            bid = ct.get_tile_building_id(neighbor)
            if (
                bid is not None
                and ct.get_entity_type(bid) == EntityType.CORE
                and ct.get_team(bid) == ct.get_team()
            ):
                return True
    if core_pos is None:
        return False
    for tile in coreFootprint(core_pos):
        if isAdjacent(pos, tile):
            return True
    return False


def _allyCorePos(bot, ct: Controller) -> Position | None:
    """Refresh ally core from packed store; never use enemy core."""
    from hivemind import CoreState, unpack_cores
    from main import SLOT_PACKED_CORES

    ally, _enemy = unpack_cores(ct.read_store(SLOT_PACKED_CORES))
    if ally[0] != CoreState.UNKNOWN:
        bot.corePos = Position(ally[1], ally[2])
        return bot.corePos
    return bot.corePos


def _isEnemyCoreAdjacent(
    ct: Controller, tile: Position, enemy_core: Position | None
) -> bool:
    """True if tile is adjacent to a known/visible enemy core footprint."""
    me = ct.get_team()
    for d in CARDINALS:
        neighbor = tile.add(d)
        if not inBounds(ct, neighbor):
            continue
        if not ct.is_in_vision(neighbor):
            continue
        bid = ct.get_tile_building_id(neighbor)
        if (
            bid is not None
            and ct.get_entity_type(bid) == EntityType.CORE
            and ct.get_team(bid) != me
        ):
            return True
    if enemy_core is None:
        return False
    for ctile in coreFootprint(enemy_core):
        if isAdjacent(tile, ctile):
            return True
    return False


def travelFacing(from_pos: Position, to_pos: Position) -> Direction:
    return nearestCardinal(from_pos.direction_to(to_pos))


def facingIntoCore(pos: Position, core_pos: Position) -> Direction:
    """Cardinal facing from `pos` into the core it is standing next to.

    First adjacent footprint tile wins — the old "nearest" tie-break was
    vestigial, since every candidate that passes the adjacency filter is at
    distance 1 by definition and so could never beat the first one found.
    Falls back to a crow-flies heading when `pos` is not actually adjacent.
    """
    for tile in coreFootprint(core_pos):
        if not isAdjacent(pos, tile):
            continue
        best = travelFacing(pos, tile)
        if best != Direction.CENTRE:
            return best
    return nearestCardinal(pos.direction_to(core_pos))


def _notePathVerdict(bot, target: Position, failed: bool) -> None:
    """Record whether A* could reach `target` on the known map.

    `moveToward` returns a bare False for three unrelated situations — move
    cooldown, a legal step that `can_move` refused, and *no route exists at all* —
    so nothing upstream could tell "wait a turn" from "that ore is walled off".
    `breakOreDeadlock` therefore blamed the ore node and re-picked another one
    behind the same wall, forever. The verdict is paired with the target because a
    single turn may call `moveToward` for several different goals as the role chain
    falls through; a bare flag would describe whichever ran last.

    Only set on turns that actually planned — under cooldown the unit has learned
    nothing new, so the previous verdict stands.
    """
    bot.lastPathFailed = failed
    bot.lastPathFailedTarget = (target.x, target.y) if failed else None


def pathKnownBlocked(bot, target: Position) -> bool:
    """True if the last planned route to `target` proved unreachable.

    Conservative by construction. `walkPassable` treats tiles that are neither in
    vision nor in `local_map` as passable, so A* routes optimistically through fog
    and only fails once the known map *proves* the region is sealed. It errs toward
    "reachable", which is the right direction — we would rather walk out and
    discover a wall than wrongly abandon ore.
    """
    if not getattr(bot, "lastPathFailed", False):
        return False
    return getattr(bot, "lastPathFailedTarget", None) == (target.x, target.y)


def _blockedByEnemyBot(ct: Controller, p: Position) -> bool:
    """True only if an *enemy* builder bot currently occupies `p`.

    An ally bot on the tile is not a real obstacle — build legality
    (`can_build_*`) never cares who else is standing nearby, and the occupant will
    almost certainly have moved on by the time a multi-tile route actually reaches
    it. Treating it as passable here still costs nothing: the one genuinely
    immovable case, stepping onto a tile that is occupied *right now*, is already
    caught downstream by `can_move` refusing that single step. Blocking on it in
    A* instead used to make every route through a patrolling guardian read as
    sealed, stalling relays and leash returns that were never actually blocked
    (see the entity-11/entity-14 contention in the 2026-08-09 drumlin game).
    An enemy bot is kept as a real obstacle: unlike an ally, it may be parked
    there on purpose and isn't something we can coordinate around.
    """
    if not ct.is_in_vision(p):
        return False
    occ = ct.get_tile_builder_bot_id(p)
    return occ is not None and ct.get_team(occ) != ct.get_team()


def moveToward(
    bot, ct: Controller, target: Position, *, avoid: set[tuple[int, int]] | None = None
) -> bool:
    """`avoid`: tiles to route around even though they're otherwise walkable —
    e.g. a harasser's recent-dodge memory, which stays valid after the threat
    that made a tile dangerous drops out of vision (see offense._dodgeMemory).
    Plain walkability (`walkPassable`) has no notion of that, so callers that
    care about it must pass it in explicitly; default is unchanged behaviour.
    """
    if ct.get_move_cooldown() != 0:
        return False
    pos = ct.get_position()
    if pos == target:
        return False
    local_map = bot.hivemind.local_map

    def ok(p: Position) -> bool:
        if p == pos:
            return True
        if not inBounds(ct, p):
            return False
        if avoid and (p.x, p.y) in avoid:
            return False
        if _blockedByEnemyBot(ct, p):
            return False
        return walkPassable(ct, p, local_map=local_map)

    path = None
    cached = getattr(bot, "cachedPath", None)
    cached_t = getattr(bot, "cachedPathTarget", None)
    if (
        cached
        and cached_t == target
        and len(cached) > 1
        and cached[0] == pos
        and not (avoid and any((p.x, p.y) in avoid for p in cached))
    ):
        path = cached
        _notePathVerdict(bot, target, False)  # cache hit — a route was found
    else:
        from main import MAX_PATH_SEARCHES_PER_TURN

        searches = getattr(bot, "pathSearchesThisTurn", 0)
        if MAX_PATH_SEARCHES_PER_TURN and searches >= MAX_PATH_SEARCHES_PER_TURN:
            # Budget spent for this turn — a role chain that falls through several
            # move-toward steps (denyOre → seekHarvester's per-candidate loop →
            # seekCore's orbit fallback) can otherwise stack 5-15 full 800-iteration
            # searches in one call, which is the actual cost behind an exec-time
            # TLE. Skip straight to the direct-step fallback below instead.
            path = None
        else:
            bot.pathSearchesThisTurn = searches + 1
            path = astarFromSeeds(
                [pos],
                is_passable=ok,
                is_goal=lambda p: p == target,
                heuristic=lambda p: manhattan(p, target),
                max_iters=PATH_MAX_ITERS,
            )
            bot.cachedPath = path
            bot.cachedPathTarget = target
            # Only `path is None` means "no route on the known map". A short path is
            # a planner quirk, not proof of blockage, so it must not read as
            # unreachable.
            _notePathVerdict(bot, target, path is None)

    if not path or len(path) < 2:
        step = nearestCardinal(pos.direction_to(target))
        if step != Direction.CENTRE and ct.can_move(step):
            ct.move(step)
            return True
        return False

    nxt = path[1]
    step = nearestCardinal(pos.direction_to(nxt))
    if step != Direction.CENTRE and ct.can_move(step):
        ct.move(step)
        bot.cachedPath = path[1:]
        return True
    bot.cachedPath = None
    return False


# ------------------------------------------------------------------
# Ore helpers
# ------------------------------------------------------------------


def knownOreTiles(bot, *, includeStatic: bool = False) -> list[Position]:
    """Ore tiles from the ledger; optionally unioned with the static map table.

    By default `features.ORE_LOCATIONS` is a *fallback*, consulted only when the ledger
    holds no ore at all — which, since our core sees ore on turn 0, means effectively
    never. That is right for economy callers, who should prefer observed ore.

    `includeStatic` unions the two instead. Baron's offence needed that (it hunted
    ore in the enemy's expansion ring, which the ledger cannot know about until
    someone has walked there); bandit has no caller that passes True today. The
    parameter stays because the union is the natural thing to want the moment any
    step needs to reason about ore it has not personally seen — and because the
    default path, ledger-first, is the one every economy caller should take.

    Note this is the *only* source of ore candidates, and it is deliberately
    unbounded: the whole map is eligible from the moment HiveMind fingerprints
    it. Nothing here or downstream caps how far a builder may roam for ore —
    separation is `_oreConvenienceKey`'s scoring, not a radius.
    """
    local_map = bot.hivemind.local_map
    seen: set[tuple[int, int]] = set()
    ores: list[Position] = []
    for (x, y), cell in local_map.items():
        if cell.get("ore"):
            seen.add((x, y))
            ores.append(Position(x, y))
    if ores and not includeStatic:
        return ores
    from features import ORE_LOCATIONS

    map_id = bot.hivemind.map_id
    for ox, oy in ORE_LOCATIONS.get(map_id, []):
        if (ox, oy) in seen:
            continue
        ores.append(Position(ox, oy))
    return ores


def _allowOreSite(bot, ore: Position) -> bool:
    """Whether this builder may claim `ore`. Always true in bandit.

    Baron used this to confine a guardian (or a home-phase default) to ore
    within DEFEND_HOME_CHEBYSHEV of the core. Bandit has no leash and no
    home phase: **there is no hard roam cap anywhere in this bot.** What keeps
    builders from piling onto one ore cluster is the score blend in
    `_oreConvenienceKey` — distance-to-self, distance-to-core, and the
    spread-zone penalty — all of them soft preferences.

    Kept as a function rather than deleted because six callers gate on it and
    this is the one place to reintroduce a restriction if testing ever wants
    one.
    """
    return True


def _selfNearOre(ct: Controller, ore: Position) -> bool:
    """True if this unit is on ore or cardinally adjacent."""
    pos = ct.get_position()
    return abs(pos.x - ore.x) + abs(pos.y - ore.y) <= 1


def allyBuilderNearOre(ct: Controller, ore: Position) -> bool:
    """True if a visible ally builder (not self) is on ore or a cardinal neighbor."""
    me = ct.get_team()
    self_id = ct.get_id()
    tiles = [ore]
    for d in CARDINALS:
        tiles.append(ore.add(d))
    for tile in tiles:
        if not inBounds(ct, tile):
            continue
        if not ct.is_in_vision(tile):
            continue
        bid = ct.get_tile_builder_bot_id(tile)
        if bid is None or bid == self_id:
            continue
        if ct.get_team(bid) == me:
            return True
    return False


def allyBuilderOnOre(ct: Controller, ore: Position) -> bool:
    """True if a visible ally builder (not self) is standing on the ore tile."""
    if not ct.is_in_vision(ore):
        return False
    bid = ct.get_tile_builder_bot_id(ore)
    if bid is None or bid == ct.get_id():
        return False
    return ct.get_team(bid) == ct.get_team()


def _oreConvenienceKey(
    bot, ore: Position, core: Position | None, self_pos: Position
) -> tuple:
    """Blend of near-builder (fast to reach), near-core (defendable), and zone.

    Baron's blend was a weighted sum of the first two, replacing an older strict
    lexicographic (core_dist, self_dist) sort in which core distance always won
    outright — so a builder would walk past ore right next to it to reach ore
    merely closer to the core, slowing down how fast a fresh harvester starts
    producing. The weighted sum keeps core proximity a real factor (it still
    breaks close ties toward the more defendable node) without letting it
    override a much closer pick.

    Bandit adds a third term, `builder._spreadScore`, in the same units: the
    builder's static angle wedge and distance band from `spreadZone`. This is
    what replaces baron's role split as the anti-clumping mechanism, and it is
    deliberately a *penalty*, not a filter — a wedge containing no ore must not
    starve its builder, it should just lose to a neighbour's leftovers.

    Takes `bot` (baron's version did not) purely to reach that spread zone.
    """
    from builder import _spreadScore
    from main import ORE_PICK_CORE_WEIGHT, ORE_PICK_SELF_WEIGHT

    self_d = manhattan(ore, self_pos)
    spread = _spreadScore(bot, ore)
    if core is None:
        return (self_d + spread, ore.x, ore.y)
    core_d = manhattan(ore, core)
    score = (
        ORE_PICK_SELF_WEIGHT * self_d + ORE_PICK_CORE_WEIGHT * core_d + spread
    )
    return (score, ore.x, ore.y)


def _clearStickyIfAllyClaimed(bot, ct: Controller) -> None:
    """Drop sticky empty ore if another ally already sits on the pad."""
    site = bot.oreSite
    if site is None:
        return
    if not ct.is_in_vision(site):
        return
    if ct.get_tile_building_id(site) is not None:
        return
    if allyBuilderNearOre(ct, site) and not _selfNearOre(ct, site):
        bot.oreSite = None


def _stickyEmptyTarget(bot, ct: Controller) -> Position | None:
    """Keep committed empty oreSite if still valid; else clear and return None."""
    _clearStickyIfAllyClaimed(bot, ct)
    site = bot.oreSite
    if site is None:
        return None
    if not _allowOreSite(bot, site):
        bot.oreSite = None
        return None
    if tileThreatenedByGunner(ct, site):
        bot.oreSite = None
        return None
    if ct.is_in_vision(site) and ct.get_tile_building_id(site) is not None:
        bot.oreSite = None
        return None
    return site


def _allyBarrierOnOre(ct: Controller, tile: Position) -> bool:
    """Our own barrier sitting on an ore tile — reclaimable, not a blocker.

    Baron placed these deliberately (`offense.denyOre` walled empty ore in the
    enemy's expansion ring) and had to make sure they did not lock *us* out of
    the node forever. Bandit never places an ore barrier — denial is not part of
    this bot — but the reclaim path is kept: barriers still arrive here via
    `breakEncirclement`'s door-opening and anything a future step adds, and a
    tile we own must never become a tile we cannot use. `checkOre` already
    destroys an ally ore-block and builds the harvester; the node simply never got
    selected,
    because `pickEmptyOre` skipped any ore carrying a building on both of its
    collection paths, leaving only `recheckOreNodes` — a last-resort helper — able to
    reach it.

    Barrier-only on purpose. The broader `_shouldClearAllyOreBlock` set would make the
    primary pick path willing to demolish ally turrets standing on ore, which is a
    different decision and not one this change is asking for.
    """
    if not ct.is_in_vision(tile):
        return False
    bid = ct.get_tile_building_id(tile)
    if bid is None:
        return False
    return (
        ct.get_entity_type(bid) == EntityType.BARRIER
        and ct.get_team(bid) == ct.get_team()
    )


def _oreLedgerStale(bot, entry: dict) -> bool:
    """True if a remembered ore-tile entity is old enough to stop disqualifying it.

    See ORE_BLOCK_MEMORY_TURNS. `entry` is a `local_map` cell (the same dict its
    caller already read `entity` off of), which carries `last_seen_turn`.
    """
    from main import ORE_BLOCK_MEMORY_TURNS

    memory = ORE_BLOCK_MEMORY_TURNS
    if not memory or memory <= 0:
        return False
    age = bot.hivemind.global_turn - entry.get("last_seen_turn", -(10**9))
    return age > memory


def pickEmptyOre(bot, ct: Controller) -> Position | None:
    """Pick empty ore by the convenience+spread blend; skip ally-on-tile pads.

    "Empty" admits ore carrying one of our own barriers — `checkOre` clears it
    and builds on arrival, so a barrier is never a lock-out.

    Baron had a second selection mode here (`ORE_STAGGER`, a team-stable
    core-distance sort indexed by the builder's rank among the `idx % 5 == 3`
    economy defaults). Bandit drops it along with the role split it indexed
    into: `spreadZone` is the anti-clumping mechanism now, and it lives inside
    the convenience score rather than beside it.
    """
    core = bot.corePos
    self_pos = ct.get_position()
    me = ct.get_team()

    seen: set[tuple[int, int]] = set()
    raw: list[Position] = []

    def add(ore: Position) -> None:
        key = (ore.x, ore.y)
        if key in seen:
            return
        seen.add(key)
        raw.append(ore)

    for tile in ct.get_nearby_tiles():
        if ct.get_tile_env(tile) != Environment.ORE_TITANIUM:
            continue
        if ct.get_tile_building_id(tile) is not None and not _allyBarrierOnOre(
            ct, tile
        ):
            continue
        add(tile)

    for ore in knownOreTiles(bot):
        entry = bot.hivemind.local_map.get((ore.x, ore.y), {})
        ent = entry.get("entity")
        if ent and _oreLedgerStale(bot, entry):
            # Old enough to stop trusting — treat as if never seen, so a builder
            # can walk back and re-check rather than being permanently locked out
            # (see _oreLedgerStale / ORE_BLOCK_MEMORY_TURNS).
            ent = None
        if ent:
            if ent.get("type") == EntityType.HARVESTER:
                continue
            # Anything else the ledger remembers as an enemy-owned blocker — most
            # commonly their own denyOre barrier — disqualifies the tile until
            # it's back in vision to re-check (or the memory goes stale). Previously
            # only a remembered HARVESTER was rejected here, so a remembered enemy
            # barrier stayed "eligible" indefinitely while out of vision:
            # pickEmptyOre could send a builder to chase ore it had already been
            # walled off from, while _pickRecheckOre's stricter ledger read (rejects
            # any remembered entity) never would have. A remembered ally barrier
            # still passes — that's the "empty" this function's docstring promises:
            # checkOre clears our own denyOre-style barriers and builds on arrival.
            if ent.get("team") != me and ent.get("type") in _ORE_ENEMY_BLOCKERS:
                continue
        if (
            ct.is_in_vision(ore)
            and ct.get_tile_building_id(ore) is not None
            and not _allyBarrierOnOre(ct, ore)
        ):
            continue
        add(ore)

    eligible: list[Position] = []
    for ore in raw:
        if not _allowOreSite(bot, ore):
            continue
        if tileThreatenedByGunner(ct, ore):
            continue
        eligible.append(ore)

    if not eligible:
        return None

    # Honour the deadlock/recheck blacklist, but fall back if it empties the pool
    skipped = getattr(bot, "skippedOres", set())
    unskipped = [o for o in eligible if (o.x, o.y) not in skipped]
    if unskipped:
        eligible = unskipped

    # On-tile claim only — adjacent would thrash as builders approach neighboring pads
    free = [o for o in eligible if not allyBuilderOnOre(ct, o)]
    pool = free if free else eligible

    pool.sort(key=lambda o: _oreConvenienceKey(bot, o, core, self_pos))
    return pool[0]


def tileThreatenedByGunner(ct: Controller, tile: Position) -> bool:
    """True if a visible enemy gunner is currently aimed at tile with LoS."""
    from guns import GUNNER_RANGE_SQ, gunnerHasLos

    me = ct.get_team()
    for eid in ct.get_nearby_entities():
        if ct.get_team(eid) == me:
            continue
        if ct.get_entity_type(eid) != EntityType.GUNNER:
            continue
        gpos = ct.get_position(eid)
        if tile.distance_squared(gpos) > GUNNER_RANGE_SQ:
            continue
        facing = ct.get_direction(eid)
        # Their gunner, their ray: their own buildings shield the tile, ours get chewed.
        if gunnerHasLos(ct, gpos, facing, tile, shooterIsAlly=False):
            return True
        if ct.can_fire_from(gpos, facing, EntityType.GUNNER, tile):
            return True
    return False


def _allyOreBlock(ct: Controller, tile: Position) -> EntityType | None:
    """Ally _ORE_BLOCK_CLEAR type on tile, else None."""
    if not ct.is_in_vision(tile):
        return None
    bid = ct.get_tile_building_id(tile)
    if bid is None:
        return None
    if ct.get_team(bid) != ct.get_team():
        return None
    etype = ct.get_entity_type(bid)
    if etype not in _ORE_BLOCK_CLEAR:
        return None
    return etype


def _shouldClearAllyOreBlock(ct: Controller, tile: Position) -> bool:
    """Clear ally ore blockers unless gunner/sentinel and that ore is threatened."""
    etype = _allyOreBlock(ct, tile)
    if etype is None:
        return False
    if etype in (EntityType.GUNNER, EntityType.SENTINEL) and tileThreatenedByGunner(
        ct, tile
    ):
        return False
    return True


def _approachOreBlock(bot, ct: Controller, tile: Position) -> bool:
    """Move to a walkable cardinal neighbor of blocked ore (not onto the blocker)."""
    pos = ct.get_position()
    if isAdjacent(pos, tile):
        return False
    local_map = bot.hivemind.local_map
    best: Position | None = None
    best_d = 10**9
    for d in CARDINALS:
        cand = tile.add(d)
        if not inBounds(ct, cand):
            continue
        if not walkPassable(ct, cand, local_map=local_map):
            continue
        if ct.is_in_vision(cand) and ct.get_tile_builder_bot_id(cand) is not None:
            continue
        dist = manhattan(pos, cand)
        if dist < best_d:
            best_d = dist
            best = cand
    if best is None:
        return moveToward(bot, ct, tile)
    return moveToward(bot, ct, best)


def _harvesterOwnerAllowed(ct: Controller, owner_team) -> bool:
    """True if a harvester owned by `owner_team` may be picked/relayed by us.

    **Every harvester is fair game in bandit — no toggle.** An ally conveyor can
    pull output off an opposing team's harvester without its ownership ever
    changing, so relaying an enemy harvester captures its titanium for the cost
    of belts alone: 20 Ti of their investment producing for us, with no fight,
    no attrition, and nothing for them to notice until the numbers stop adding
    up. That is bandit's whole thesis in one engine rule, so where baron kept it
    behind a `STEAL_ENEMY_HARVESTERS` flag, here it is unconditional.

    Kept as a function because three callers (`checkOre`, `pickMaintainOre`,
    `_pickRecheckOre`) route through it, and it is the one place to reintroduce
    a restriction if one is ever wanted.
    """
    return True


def pickMaintainOre(bot, ct: Controller) -> Position | None:
    """Prefer sticky site, else the orphan hint, else best empty/harvester/block.

    "Harvester" here includes *enemy* harvesters — `_harvesterOwnerAllowed` is
    unconditionally true in bandit, so an enemy-owned node reads as ordinary
    maintainable work and `checkOre` relays it like any other. The steal is not a
    special case anywhere in this file; it is just what happens when the owner
    filter is open.
    """
    _clearStickyIfAllyClaimed(bot, ct)
    if bot.oreSite is not None:
        if _allowOreSite(bot, bot.oreSite):
            return bot.oreSite
        bot.oreSite = None

    # An orphaned harvester already has 20 Ti sunk into it and needs only belts —
    # cheaper to rescue than starting fresh, so it outranks pickEmptyOre below.
    orphan = _pickOrphanRelay(bot, ct)
    if orphan is not None:
        return orphan

    pos = ct.get_position()
    core = bot.corePos
    best: Position | None = None
    best_key = None

    from builder import _spreadScore

    def score(ore: Position) -> tuple:
        # Spread penalty folded into the leading term so it competes with core
        # distance in the same units, matching how `_oreConvenienceKey` blends
        # it on the pickEmptyOre side — the two scores are compared against each
        # other at the end of this function, so they have to agree on what a
        # point costs.
        spread = _spreadScore(bot, ore)
        if core is not None:
            return (manhattan(ore, core) + spread, manhattan(ore, pos))
        return (manhattan(ore, pos) + spread,)

    def consider(ore: Position) -> None:
        nonlocal best, best_key
        if not _allowOreSite(bot, ore):
            return
        key = score(ore)
        if best_key is None or key < best_key:
            best_key = key
            best = ore

    for ore in knownOreTiles(bot):
        if ct.is_in_vision(ore):
            bid = ct.get_tile_building_id(ore)
            if bid is None:
                continue  # empties via pickEmptyOre
            if ct.get_entity_type(bid) == EntityType.HARVESTER and _harvesterOwnerAllowed(
                ct, ct.get_team(bid)
            ):
                consider(ore)
            elif _shouldClearAllyOreBlock(ct, ore):
                consider(ore)
        else:
            entry = bot.hivemind.local_map.get((ore.x, ore.y), {})
            ent = entry.get("entity")
            if (
                ent
                and ent.get("type") == EntityType.HARVESTER
                and _harvesterOwnerAllowed(ct, ent.get("team"))
            ):
                consider(ore)
            elif ent:
                # Fog: possible ally block / other — keep old consider behavior
                consider(ore)

    for tile in ct.get_nearby_tiles():
        if ct.get_tile_env(tile) != Environment.ORE_TITANIUM:
            continue
        bid = ct.get_tile_building_id(tile)
        if bid is None:
            continue
        if ct.get_entity_type(bid) == EntityType.HARVESTER and _harvesterOwnerAllowed(
            ct, ct.get_team(bid)
        ):
            consider(tile)
        elif _shouldClearAllyOreBlock(ct, tile):
            consider(tile)

    empty = pickEmptyOre(bot, ct)
    if empty is None:
        return best
    if best is None:
        return empty
    # Closer work wins; empties are already index-staggered among themselves
    if score(empty) <= score(best):
        return empty
    return best


# ------------------------------------------------------------------
# Relay helpers
# ------------------------------------------------------------------


def _allyBeltAt(ct: Controller, tile: Position) -> bool:
    """True if a *live-visible* ally belt occupies tile.

    Team-checked counterpart to `bid_is_belt`, which is team-blind. Used to keep
    the merge-or-follow path off enemy belts, so those still fall through to
    `clearBeltObstacle`'s fire-on-it branch.
    """
    bid = ct.get_tile_building_id(tile)
    if bid is None:
        return False
    return (
        ct.get_entity_type(bid) in BELT_TYPES and ct.get_team(bid) == ct.get_team()
    )


def _beltDirectionAt(bot, ct: Controller, tile: Position) -> Direction | None:
    """Facing of the ally CONVEYOR at tile, vision first then ledger.

    CONVEYOR only — deliberately excludes SPLITTER, matching
    `_danglingBeltEnds`'s own precedent: a splitter rotates its output among
    three directions, so "the way it points" (whichever output was current at
    sighting time, live or via a possibly-stale ledger entry) does not
    identify where the chain actually goes. A wrong guess here isn't just a
    missed merge — it's a *false positive* connectivity proof, which is
    exactly what the walk below exists to prevent.

    None for anything else — enemy-owned, not a conveyor, no data, or a stale
    ledger entry (`_beltLedgerStale`). The connectivity walk below treats
    None as "chain broken here."
    """
    me = ct.get_team()
    if ct.is_in_vision(tile):
        bid = ct.get_tile_building_id(tile)
        if bid is None or ct.get_team(bid) != me:
            return None
        if ct.get_entity_type(bid) != EntityType.CONVEYOR:
            return None
        try:
            return ct.get_direction(bid)
        except Exception:
            return None
    entry = bot.hivemind.local_map.get((tile.x, tile.y))
    if not entry or _beltLedgerStale(bot, entry):
        return None
    ent = entry.get("entity")
    if not ent or ent.get("team") != me or ent.get("type") != EntityType.CONVEYOR:
        return None
    return ent.get("direction")


def _relayReachesCore(
    bot,
    ct: Controller,
    tile: Position,
    core_pos: Position,
    *,
    cache: dict | None = None,
) -> bool:
    """Bounded forward-walk proving `tile`'s ally belt chain actually feeds the core.

    Follows each tile's own stored facing (the same ledger `direction` field
    `_danglingBeltEnds` already reads) toward wherever it actually points,
    capped at `main.RELAY_MERGE_MAX_HOPS`. This is the forward counterpart to
    `defence._reconnectFacing`'s backward "what should feed this gap"
    reasoning — asking "does this chain actually get there" is what a merge
    target needs proven before a new relay may end at it instead of the core:
    an unconditional "any ally belt is fine" would risk landing on a
    dead/orphaned stub or a sibling's not-yet-committed head, neither
    distinguishable from a live line at the single-tile level (see
    `OrphanRelayRegistry`'s own rationale for why that distinction matters).

    `cache` (keyed by the starting tile) is a per-`pickRelayPath`-call memo —
    the merge goal is evaluated on every node A* expands, so without it the
    same candidate's chain could be re-walked many times in one search.
    """
    from main import RELAY_MERGE_MAX_HOPS

    key0 = (tile.x, tile.y)
    if cache is not None and key0 in cache:
        return cache[key0]
    max_hops = RELAY_MERGE_MAX_HOPS if RELAY_MERGE_MAX_HOPS and RELAY_MERGE_MAX_HOPS > 0 else 10
    cur = tile
    seen: set[tuple[int, int]] = set()
    result = False
    # max_hops + 1 iterations = max_hops actual steps: the footprint test runs at
    # the top, before any step, so `range(max_hops)` only ever walked max_hops-1
    # tiles and the knob silently meant one less hop than it says.
    for _ in range(max_hops + 1):
        if any(cur.x == t.x and cur.y == t.y for t in coreFootprint(core_pos)):
            result = True
            break
        key = (cur.x, cur.y)
        if key in seen:
            break  # loop — shouldn't happen on a real belt line, defensive only
        seen.add(key)
        d = _beltDirectionAt(bot, ct, cur)
        if d is None or d == Direction.CENTRE:
            break
        cur = cur.add(d)
        if not inBounds(ct, cur):
            break
    if cache is not None:
        cache[key0] = result
    return result


def _mergeCandidateNeighbor(
    bot, ct: Controller, tile: Position, core_pos: Position, *, cache: dict | None = None
) -> Position | None:
    """A cardinal neighbor of tile that is a provably-connected ally belt, or None.

    Shared by the relay-path merge goal and the final-segment facing proof —
    both need the exact same answer, so there is only one place that answers
    it. Excludes the core footprint itself (isCoreAdjacent already owns that
    case).

    `_relayReachesCore` is memoized per `pickRelayPath` call, which matters
    because `is_goal` calls this on every node A* expands.
    """
    from defence import _isBeltOrCore

    footprint = coreFootprint(core_pos)
    for d in CARDINALS:
        near = tile.add(d)
        if not inBounds(ct, near):
            continue
        if any(near.x == t.x and near.y == t.y for t in footprint):
            continue
        if not _isBeltOrCore(bot, ct, near, core_pos):
            continue
        # No `_allyBuilderNear` guard here: it rejects on *any* other ally builder
        # within Chebyshev 2, and the thing it protects — a builder's
        # not-yet-committed relay head — by definition does not reach the core,
        # so the connectivity proof immediately below already excludes it. The
        # proximity heuristic only added false negatives, concentrated near the
        # core where ally builders congregate.
        if not _relayReachesCore(bot, ct, near, core_pos, cache=cache):
            continue
        return near
    return None


def neededBeltFacing(
    bot, ct: Controller, path: list[Position], idx: int, core_pos: Position
) -> Direction:
    """Facing for path[idx]: toward next hop; into the core on the last tile;
    or, when `RELAY_MERGE_ENABLED` and the path ended by merging into an
    existing ally line instead of reaching the core, into that line.

    The merge case is proof-based like `_reconnectFacing`, never a
    crow-flies guess — `facingIntoCore` used unconditionally here would aim
    at the core anchor regardless of which existing belt the path actually
    merged into, producing exactly the belt-pointing-at-nothing/torn-out-by-
    `clearBeltObstacle` cycle `_reconnectFacing`'s own docstring describes from
    the one other place this codebase tried a crow-flies fallback.

    **If `tile` is itself already an ally CONVEYOR whose own current facing
    already reaches the core, that facing is kept as-is — never recomputed.**
    This is not an optimization, it is load-bearing: `tile` reaching the goal
    condition proves it is *adjacent to* a provably-connected belt, but says
    nothing about whether `tile` is itself already a *member* of that same
    chain (e.g. two tiles mid-line, each already facing the other onward).
    Recomputing a fresh neighbor-based facing for such a tile can point it at
    a neighbor that is only "provably connected" *through `tile`'s own
    current facing* — accepting that would invert the pair into a 2-tile
    cycle, destroying a working ally belt via `clearBeltObstacle` and severing
    everything downstream of it from the core. Checking self-connectivity
    first closes that: an empty `tile` (the normal, first-build case) has no
    facing to read (`_beltDirectionAt` returns `None`), so this only ever
    applies to a tile that is already a real, working belt.
    """
    if idx >= len(path) - 1:
        tile = path[idx]
        if isCoreAdjacent(ct, tile, core_pos):
            return facingIntoCore(tile, core_pos)
        from main import RELAY_MERGE_ENABLED

        if RELAY_MERGE_ENABLED:
            own = _beltDirectionAt(bot, ct, tile)
            if own is not None and own != Direction.CENTRE and _relayReachesCore(
                bot, ct, tile, core_pos
            ):
                return own
            near = _mergeCandidateNeighbor(bot, ct, tile, core_pos)
            if near is not None:
                return travelFacing(tile, near)
        return facingIntoCore(tile, core_pos)
    return travelFacing(path[idx], path[idx + 1])


def _beltLedgerStale(bot, entry: dict) -> bool:
    """True if a remembered belt-tile sighting is too old to trust its facing.

    Mirrors `_oreLedgerStale`/`ORE_BLOCK_MEMORY_TURNS` — same shape, own knob
    (`RELAY_LEDGER_TRUST_TURNS`), because a wrongly-trusted belt ledger entry
    can silently mark a broken relay "complete" rather than merely mis-judge
    an ore tile, so it gets its own dial rather than sharing one tuned for a
    different risk.
    """
    from main import RELAY_LEDGER_TRUST_TURNS

    trust = RELAY_LEDGER_TRUST_TURNS
    if not trust or trust <= 0:
        return True  # knob disables the fallback outright — always "stale"
    age = bot.hivemind.global_turn - entry.get("last_seen_turn", -(10**9))
    return age > trust


def beltFacingOk(
    bot, ct: Controller, pos: Position, needed: Direction, *, local_map: dict | None = None
) -> bool:
    """True if pos already holds a belt facing `needed` — vision first, ledger fallback.

    A relay spanning more tiles than one unit's own vision (sqrt(20)) can cover
    at once could never confirm "already fully built" from a single vantage
    point without this — `extendRelayWatermark`/`relayToCore`'s fog-recovery
    loop would keep re-walking an already-complete corridor forever, treating
    every out-of-sight tile as unverified. The ledger already carries the
    `direction` field for CONVEYOR/SPLITTER sightings (`_danglingBeltEnds`
    reads the same field), so this is a read, not new state — bounded by
    `_beltLedgerStale` so a genuinely stale sighting still falls through to
    "unknown" (returns False) rather than trusting a segment that may have
    since been destroyed.

    Team-checked on both branches: an enemy belt whose facing happens to
    match `needed` must never read as "already built" for our own relay —
    that would mark the segment complete without ever laying a tile of ours.
    """
    me = ct.get_team()
    if ct.is_in_vision(pos):
        bid = ct.get_tile_building_id(pos)
        if bid is None or ct.get_team(bid) != me:
            return False
        if ct.get_entity_type(bid) not in BELT_TYPES:
            return False
        try:
            return ct.get_direction(bid) == needed
        except Exception:
            return False
    if local_map is None:
        return False
    entry = local_map.get((pos.x, pos.y))
    if not entry or _beltLedgerStale(bot, entry):
        return False
    ent = entry.get("entity")
    if not ent or ent.get("team") != me or ent.get("type") not in BELT_TYPES:
        return False
    return ent.get("direction") == needed


# Obstacle types this clears from a relay lane: a wrong-facing belt (destroy/
# fire and rebuild), or a barrier sitting square in the lane (ours or theirs).
# Any other building (harvester, core, gunner/sentinel/launcher) is not a
# candidate here — relayToCore replans around those instead.
_BELT_OBSTACLE_TYPES = BELT_TYPES + (EntityType.BARRIER,)


def clearBeltObstacle(bot, ct: Controller, tile: Position) -> str:
    """Clear a wrong-facing belt or a barrier blocking the relay lane.

    Returns acted|waiting|clear|failed.
    """
    if not ct.is_in_vision(tile):
        return "waiting"
    bid = ct.get_tile_building_id(tile)
    if bid is None:
        return "clear"
    etype = ct.get_entity_type(bid)
    if etype not in _BELT_OBSTACLE_TYPES:
        return "failed"
    pos = ct.get_position()
    ally = ct.get_team(bid) == ct.get_team()

    if pos == tile:
        if ct.get_move_cooldown() != 0:
            return "waiting"
        for d in CARDINALS:
            if ct.can_move(d):
                ct.move(d)
                return "acted"
        return "failed"

    adjacent = isAdjacent(pos, tile)
    if not adjacent:
        if moveToward(bot, ct, tile):
            return "acted"
        return "waiting"

    if ally:
        if ct.can_destroy(tile):
            ct.destroy(tile)
            return "acted"
        return "failed"

    # Enemy: stand-and-fire from adjacent (builder fires own tile only for belts
    # on the work tile — move onto tile if needed next turns after clear attempt).
    if ct.get_action_cooldown() == 0 and ct.can_fire(tile):
        ct.fire(tile)
        return "acted"
    return "waiting"


def placeRelayBelt(ct: Controller, tile: Position, facing: Direction) -> str:
    """Build conveyor. Returns placed|waiting|failed."""
    if not ct.is_in_vision(tile):
        return "waiting"
    if ct.get_tile_building_id(tile) is not None:
        return "failed"
    if ct.get_action_cooldown() != 0:
        return "waiting"
    # Ti-starved, not stuck: the shared bank can't afford a conveyor right
    # now but will recover, so treat this like any other "waiting" turn
    # instead of counting it toward relayStuck — the caller already holds
    # position adjacent to tile on a waiting turn, so this is exactly the
    # "wait in the vicinity until Ti arrives" fallback.
    if ct.get_global_resources() < ct.get_conveyor_cost():
        return "waiting"
    if ct.can_build_conveyor(tile, facing):
        ct.build_conveyor(tile, facing)
        return "placed"
    return "failed"


def extendRelayWatermark(
    bot,
    ct: Controller,
    path: list[Position],
    core_pos: Position,
    built_to: int,
) -> int:
    """Raise watermark through contiguous correct-facing belts, vision or ledger.

    A relay longer than one unit's own vision cone can never be confirmed
    "already fully built" from a single vantage point without the ledger
    fallback `beltFacingOk` now carries — see its docstring. Still stops at
    the first tile that fails either check (wrong facing, empty, or a stale/
    absent ledger entry), same as before.
    """
    i = built_to + 1
    while i < len(path):
        tile = path[i]
        needed = neededBeltFacing(bot, ct, path, i, core_pos)
        if not beltFacingOk(bot, ct, tile, needed, local_map=bot.hivemind.local_map):
            break
        built_to = i
        i += 1
    return built_to


def pickRelayPath(
    bot,
    ct: Controller,
    origin: Position,
    core_pos: Position,
) -> list[Position] | None:
    local_map = bot.hivemind.local_map
    ore_blocked: set[tuple[int, int]] = set()
    if bot.oreSite is not None:
        ore_blocked.add((bot.oreSite.x, bot.oreSite.y))

    from main import RELAY_MERGE_ENABLED

    # Per-call memo: the merge goal is evaluated on every node A* expands, so
    # without this the same candidate belt's connectivity chain could be
    # walked repeatedly within one search. See _relayReachesCore.
    merge_cache: dict = {}

    def is_goal(p: Position) -> bool:
        if isCoreAdjacent(ct, p, core_pos):
            return True
        if not RELAY_MERGE_ENABLED:
            return False
        return _mergeCandidateNeighbor(bot, ct, p, core_pos, cache=merge_cache) is not None

    threat_blocked = getattr(bot, "relayThreatBlocked", None) or None

    def ok(p: Position) -> bool:
        return relayPassable(
            ct,
            p,
            ore_blocked=ore_blocked,
            threat_blocked=threat_blocked,
            local_map=local_map,
        )

    seeds: list[Position] = []
    if ok(origin):
        seeds.append(origin)
    else:
        for d in CARDINALS:
            n = origin.add(d)
            if ok(n):
                seeds.append(n)

    path = None
    if seeds:
        path = astarFromSeeds(
            seeds,
            is_passable=ok,
            is_goal=is_goal,
            heuristic=lambda p: manhattan(p, core_pos),
            max_iters=PATH_MAX_ITERS,
        )
    if path is None:
        path = findPathAroundObstruction(
            ct,
            origin,
            is_goal=is_goal,
            heuristic_to=core_pos,
            ore_blocked=ore_blocked,
            threat_blocked=threat_blocked,
            local_map=local_map,
            max_iters=PATH_MAX_ITERS,
        )
    # Both planners failing is the relay's own "no route exists" verdict. Without
    # recording it, relayToCore just aborts and the reason is indistinguishable from
    # every other abort — so a relay sealed by our own gunner never reached the door
    # search, which reads `moveToward`'s walking verdict only.
    bot.relayPathBlocked = path is None
    bot.relayPathBlockedFrom = (origin.x, origin.y) if path is None else None
    return path


def relayToCore(bot, ct: Controller) -> None:
    """Orchestrator: path → facing check → clear/place → move (ally core only)."""
    core_pos = _allyCorePos(bot, ct)
    if core_pos is None:
        abortRelay(bot)
        return

    enemy_core = getattr(bot, "enemyCorePos", None)
    if bot.relayOrigin is None:
        bot.relayOrigin = bot.oreSite or ct.get_position()
    bot.inRelay = True

    path = bot.relayPath
    if path is not None:
        if _isEnemyCoreAdjacent(ct, path[-1], enemy_core):
            bot.relayPath = None
            abortRelay(bot)
            return
        bot.relayBuiltTo = extendRelayWatermark(
            bot, ct, path, core_pos, bot.relayBuiltTo
        )

    if path is None:
        path = pickRelayPath(bot, ct, bot.relayOrigin, core_pos)
        bot.relayPath = path
        bot.relayBuiltTo = -1
        if path is None:
            bot.relayPath = None
            _reportAndAbort(bot, ct)
            return
        if _isEnemyCoreAdjacent(ct, path[-1], enemy_core):
            bot.relayPath = None
            abortRelay(bot)
            return
        bot.relayBuiltTo = extendRelayWatermark(bot, ct, path, core_pos, -1)

    work_from = bot.relayBuiltTo + 1
    if work_from >= len(path):
        markRelayComplete(ct)
        if bot.relayOrigin is not None:
            clearOrphanRelay(ct, bot.relayOrigin)
        clearRelay(bot)
        bot.oreSite = None
        return

    idx = work_from
    # Skip already-correct tiles that watermark may have missed in fog edge
    while idx < len(path):
        tile = path[idx]
        needed = neededBeltFacing(bot, ct, path, idx, core_pos)
        if beltFacingOk(bot, ct, tile, needed, local_map=bot.hivemind.local_map):
            bot.relayBuiltTo = idx
            idx += 1
            continue
        break
    else:
        markRelayComplete(ct)
        if bot.relayOrigin is not None:
            clearOrphanRelay(ct, bot.relayOrigin)
        clearRelay(bot)
        bot.oreSite = None
        return

    tile = path[idx]
    needed = neededBeltFacing(bot, ct, path, idx, core_pos)
    pos = ct.get_position()

    # Don't spend titanium on a tile a visible enemy gunner is covering. Route
    # around it rather than dropping the relay: blacklist the tile for the rest of
    # this attempt and replan from the origin. Belts already laid sit behind the
    # threat and stay valid — a path that retraces them costs nothing, because
    # extendRelayWatermark resumes at the first gap or wrong-facing tile. Abort is
    # kept as the fallback for when no clear route exists at all.
    if tileThreatenedByGunner(ct, tile):
        bot.relayThreatBlocked.add((tile.x, tile.y))
        new_path = pickRelayPath(bot, ct, bot.relayOrigin, core_pos)
        if new_path is None:
            _reportAndAbort(bot, ct)
            return
        if _isEnemyCoreAdjacent(ct, new_path[-1], enemy_core):
            abortRelay(bot)
            return
        bot.relayPath = new_path
        bot.relayBuiltTo = extendRelayWatermark(bot, ct, new_path, core_pos, -1)
        return

    # Barrier blocking — clear it (destroy ours, fire down theirs) instead of
    # abandoning the planned lane. Any other non-belt building still replans.
    if ct.is_in_vision(tile):
        block_id = ct.get_tile_building_id(tile)
        if block_id is not None:
            etype = ct.get_entity_type(block_id)
            if etype == EntityType.BARRIER:
                result = clearBeltObstacle(bot, ct, tile)
                if result == "acted":
                    return
                if result == "failed":
                    bot.relayStuck = getattr(bot, "relayStuck", 0) + 1
                    if bot.relayStuck >= 3:
                        _reportAndAbort(bot, ct)
                    return
                if result == "waiting":
                    return
                # clear — fall through to place
            elif etype not in BELT_TYPES:
                bot.relayPath = None
                bot.relayBuiltTo = -1
                return

    if ct.is_in_vision(tile) and bid_is_belt(ct, tile):
        if not beltFacingOk(bot, ct, tile, needed, local_map=bot.hivemind.local_map):
            # An ally belt already sits here facing its own way. Treat that as
            # the relay's terminus rather than an obstacle: path[idx-1] is built
            # and already faces into this tile (neededBeltFacing's travelFacing),
            # so our output flows in here and onward under this belt's own
            # facing — a merge, for free.
            #
            # Never rewrite it to our route's direction. That *diverts* whatever
            # this line already carried into ours and starves everything
            # downstream of the crossing, which nothing in the codebase detects:
            # _danglingBeltEnds only ever sees a line that dangles, never one
            # that was quietly re-pointed.
            #
            # No connectivity proof here on purpose. It can't be established
            # cheaply — pickRelayPath's merge goal already tried and failed at
            # plan time (a builder's ledger is personal-vision only, so it knows
            # nothing about the near-core run it never walked) — and measuring
            # the alternatives showed both cost more than they saved: walking the
            # chain to verify ran ~9% worse on Ti/turn, since it abandons the
            # relay mid-air to go wandering. If this belt turns out to be a dead
            # line, extending it is the dangling-end repair's job
            # (finishNearbyBelt / reconnectBelts), which is already anchored for
            # exactly that.
            if _allyBeltAt(ct, tile):
                markRelayComplete(ct)
                if bot.relayOrigin is not None:
                    clearOrphanRelay(ct, bot.relayOrigin)
                clearRelay(bot)
                bot.oreSite = None
                return
            result = clearBeltObstacle(bot, ct, tile)
            if result == "acted":
                return
            if result == "failed":
                bot.relayStuck = getattr(bot, "relayStuck", 0) + 1
                if bot.relayStuck >= 3:
                    _reportAndAbort(bot, ct)
                return
            if result == "waiting":
                return
            # clear — fall through to place

    if beltFacingOk(bot, ct, tile, needed, local_map=bot.hivemind.local_map):
        bot.relayBuiltTo = idx
        return

    # Need empty tile to place — step off if standing on it
    if pos == tile:
        if ct.get_move_cooldown() != 0:
            return
        for d in CARDINALS:
            if ct.can_move(d):
                ct.move(d)
                return
        bot.relayStuck = getattr(bot, "relayStuck", 0) + 1
        if bot.relayStuck >= 3:
            _reportAndAbort(bot, ct)
        return

    adjacent = isAdjacent(pos, tile)
    if adjacent:
        result = placeRelayBelt(ct, tile, needed)
        if result == "placed":
            bot.relayStuck = 0
            bot.relayBuiltTo = max(bot.relayBuiltTo, idx)
            if ct.get_move_cooldown() == 0:
                step = nearestCardinal(pos.direction_to(tile))
                if step != Direction.CENTRE and ct.can_move(step):
                    ct.move(step)
            return
        if result == "waiting":
            return
        bot.relayStuck = getattr(bot, "relayStuck", 0) + 1
        if bot.relayStuck >= 3:
            abortRelay(bot)
        return

    if moveToward(bot, ct, tile):
        bot.relayStuck = 0
    else:
        bot.relayStuck = getattr(bot, "relayStuck", 0) + 1
        if bot.relayStuck >= 3:
            _reportAndAbort(bot, ct)


def bid_is_belt(ct: Controller, tile: Position) -> bool:
    bid = ct.get_tile_building_id(tile)
    if bid is None:
        return False
    return ct.get_entity_type(bid) in BELT_TYPES


# ------------------------------------------------------------------
# Default role tasks
# ------------------------------------------------------------------


def buildHarvester(bot, ct: Controller) -> bool:
    """Build on empty ore in vision / known map. Returns True if acted."""
    if bot.inRelay:
        return False

    # Adjacent empty ore first
    if ct.get_action_cooldown() == 0:
        pos = ct.get_position()
        for d in Direction:
            if d == Direction.CENTRE:
                continue
            tile = pos.add(d)
            if not inBounds(ct, tile):
                continue
            if not _allowOreSite(bot, tile):
                continue
            if tileThreatenedByGunner(ct, tile):
                continue
            if ct.can_build_harvester(tile):
                ct.build_harvester(tile)
                bot.oreSite = tile
                bot.relayOrigin = tile
                bot.inRelay = True
                bot.relayPath = None
                bot.relayBuiltTo = -1
                return True

    # Commit to sticky empty target; only re-stagger when unset/invalid
    target = _stickyEmptyTarget(bot, ct)
    if target is None:
        target = pickEmptyOre(bot, ct)
    if target is None:
        return False
    bot.oreSite = target
    if moveToward(bot, ct, target):
        return True
    # Try stand next to it
    pos = ct.get_position()
    if isAdjacent(pos, target):
        return False
    return False


def checkOre(bot, ct: Controller, tile: Position) -> bool:
    """Inspect ore node; returns True if this turn's action consumed."""
    if not ct.is_in_vision(tile):
        return moveToward(bot, ct, tile)

    bid = ct.get_tile_building_id(tile)

    if bid is None:
        # Empty — build if adjacent / move (skip if enemy gunner aims here)
        if tileThreatenedByGunner(ct, tile):
            return False

        pos = ct.get_position()
        if isAdjacent(pos, tile):
            if ct.get_action_cooldown() == 0 and ct.can_build_harvester(tile):
                ct.build_harvester(tile)
                bot.relayOrigin = tile
                bot.inRelay = True
                bot.relayPath = None
                bot.relayBuiltTo = -1
                return True
            return False
        if pos == tile:
            if ct.get_move_cooldown() == 0:
                for d in CARDINALS:
                    if ct.can_move(d):
                        ct.move(d)
                        return True
            return False
        return moveToward(bot, ct, tile)

    etype = ct.get_entity_type(bid)
    team = ct.get_team(bid)
    mine = ct.get_team()

    if etype == EntityType.HARVESTER:
        # team != mine here means an enemy-owned harvester, and relaying it
        # anyway is the point — see `_harvesterOwnerAllowed`, which is
        # unconditionally true in bandit, so this branch treats their harvester
        # exactly like ours. The check stays as the single place to close that
        # door again if it ever needs closing.
        if not _harvesterOwnerAllowed(ct, team):
            return False
        bot.relayOrigin = tile
        bot.inRelay = True
        relayToCore(bot, ct)
        # relayToCore clears inRelay when it aborts or completes, in both cases
        # without consuming an action. Report that honestly so maintainHarvester
        # falls through to recheckOreNodes instead of burning the turn.
        return bot.inRelay

    if etype in _ORE_BLOCK_CLEAR:
        if team == mine:
            if not _shouldClearAllyOreBlock(ct, tile):
                return False
            pos = ct.get_position()
            if isAdjacent(pos, tile):
                if ct.get_action_cooldown() == 0 and ct.can_destroy(tile):
                    ct.destroy(tile)
                    return True
                return False
            return _approachOreBlock(bot, ct, tile)
        # Enemy occupancy — gun placement deferred
        return False

    return False


def maintainHarvester(bot, ct: Controller) -> bool:
    bot.recheckingOre = False
    if bot.inRelay and bot.relayOrigin is not None:
        relayToCore(bot, ct)
        # Finished or aborted this turn — don't block recheck/defend forever
        if not bot.inRelay:
            return False
        return True

    tile = pickMaintainOre(bot, ct)
    if tile is None:
        return False
    bot.oreSite = tile
    acted = checkOre(bot, ct, tile)
    # Instant complete/abort via checkOre→relayToCore
    if acted and not bot.inRelay and bot.oreSite is None:
        return False
    return acted


def adoptDanglingBelt(bot, ct: Controller) -> bool:
    """Adopt a nearby orphaned ally belt end and finish its relay as our own.

    New in bandit. A relay whose builder died mid-build leaves a belt line
    pointing at nothing: there is no on-death hook anywhere in this bot, so that
    line never reaches one of `relayToCore`'s own abort sites and never publishes
    an orphan hint either. Someone has to notice it, and with no leash confining
    anyone, "someone" can be whichever builder happens to be standing near it.

    The mechanism is deliberately thin: point `relayOrigin`/`oreSite` at the
    dangling belt, set `inRelay`, and hand off to the ordinary, unmodified
    `relayToCore` state machine — which will path from there to the core, merge
    into any existing line it meets, and clear itself on completion exactly as it
    would for a relay this builder had started itself. No parallel repair logic.

    Complements `defence.finishNearbyBelt` rather than duplicating it, split by
    gap size: `finishNearbyBelt` lays one proven tile and is cheaper, this takes
    over the whole remaining route. It runs first (priority 5, ahead of ore work)
    because 20 Ti of harvester is already sunk at the far end of that line.

    `respect_owner=True`, unlike `finishNearbyBelt`: a builder mid-relay has a
    legitimately dangling head — its next belt simply is not laid yet — and
    seizing its route out from under it would have both builders re-pathing the
    same line. Deferring costs a turn; colliding costs the relay.

    Returns `bot.inRelay` rather than True, matching `checkOre`'s convention:
    `relayToCore` can complete or abort without consuming an action, and
    reporting that honestly lets the caller fall through to the next priority
    step instead of burning the turn on nothing.
    """
    from defence import _danglingBeltEnds
    from main import ADOPT_BELT_CHEBYSHEV

    if bot.inRelay or bot.corePos is None:
        return False
    if getattr(bot, "recheckingOre", False):
        return False

    candidates = _danglingBeltEnds(
        bot,
        ct,
        anchor=ct.get_position(),
        max_dist=ADOPT_BELT_CHEBYSHEV,
        respect_owner=True,
    )
    if not candidates:
        return False

    belt, _target = candidates[0]
    bot.relayOrigin = belt
    bot.oreSite = belt
    bot.inRelay = True
    bot.relayPath = None
    bot.relayBuiltTo = -1
    relayToCore(bot, ct)
    return bot.inRelay


ORE_RECHECK_CLEAR_INTERVAL = 50


# Baron had three more functions here — `moveTowardLeash`, `_safeCoreStandTile`
# and `defendHome` — all dropped. The first two are leash machinery with no
# leash to serve; `defendHome` was a guardian-only "heal the core once it drops
# under 500 HP" step, which bandit replaces with `defence.repair`: same heal, no
# HP threshold, no near-core gate, run by every builder at priority 3 so damage
# is never ignored. `healAlly` moved wholesale into `defence.py` under that
# name.


def _pickRecheckOre(bot, ct: Controller) -> Position | None:
    """Prefer the orphan hint, then empty, then ally-blocked, then harvester."""
    orphan = _pickOrphanRelay(bot, ct)
    if orphan is not None:
        return orphan

    skipped = getattr(bot, "skippedOres", set())
    pos = ct.get_position()
    core = bot.corePos
    empty_best: Position | None = None
    empty_key = None
    blocked_best: Position | None = None
    blocked_key = None
    harv_best: Position | None = None
    harv_key = None

    def score(ore: Position):
        if core is not None:
            return (manhattan(ore, core), manhattan(ore, pos))
        return (manhattan(ore, pos),)

    def consider(ore: Position, *, kind: str) -> None:
        nonlocal empty_best, empty_key, blocked_best, blocked_key, harv_best, harv_key
        if not _allowOreSite(bot, ore):
            return
        key_xy = (ore.x, ore.y)
        if key_xy in skipped:
            return
        key = score(ore)
        if kind == "empty":
            if empty_key is None or key < empty_key:
                empty_key = key
                empty_best = ore
        elif kind == "blocked":
            if blocked_key is None or key < blocked_key:
                blocked_key = key
                blocked_best = ore
        else:
            if harv_key is None or key < harv_key:
                harv_key = key
                harv_best = ore

    for tile in ct.get_nearby_tiles():
        if ct.get_tile_env(tile) != Environment.ORE_TITANIUM:
            continue
        bid = ct.get_tile_building_id(tile)
        if bid is None:
            consider(tile, kind="empty")
        elif ct.get_entity_type(bid) == EntityType.HARVESTER and _harvesterOwnerAllowed(
            ct, ct.get_team(bid)
        ):
            consider(tile, kind="harvester")
        elif _shouldClearAllyOreBlock(ct, tile):
            consider(tile, kind="blocked")

    for ore in knownOreTiles(bot):
        key_xy = (ore.x, ore.y)
        if key_xy in skipped:
            continue
        if ct.is_in_vision(ore):
            bid = ct.get_tile_building_id(ore)
            if bid is None:
                consider(ore, kind="empty")
            elif ct.get_entity_type(bid) == EntityType.HARVESTER and _harvesterOwnerAllowed(
                ct, ct.get_team(bid)
            ):
                consider(ore, kind="harvester")
            elif _shouldClearAllyOreBlock(ct, ore):
                consider(ore, kind="blocked")
            continue
        entry = bot.hivemind.local_map.get(key_xy, {})
        ent = entry.get("entity")
        if ent and _oreLedgerStale(bot, entry):
            # See pickEmptyOre's identical treatment / ORE_BLOCK_MEMORY_TURNS —
            # this is the recheck path, so leaving stale memory permanently
            # disqualifying here would be the last resort that ever rescues it.
            ent = None
        if ent and ent.get("type") == EntityType.HARVESTER and _harvesterOwnerAllowed(
            ct, ent.get("team")
        ):
            consider(ore, kind="harvester")
        elif not ent:
            consider(ore, kind="empty")

    if empty_best is not None:
        return empty_best
    if blocked_best is not None:
        return blocked_best
    return harv_best


def _enemyBarrierAt(bot, ct: Controller, tile: Position) -> bool:
    """Enemy barrier on tile, from vision or the ledger."""
    ent = tileEntity(ct, tile, local_map=bot.hivemind.local_map)
    if not ent or ent.get("team") == ct.get_team():
        return False
    return ent.get("type") == EntityType.BARRIER


# Ally buildings we are willing to demolish to reopen a sealed route. `destroy` is
# allied-only, free and costs no action cooldown, so an ally door is the cheapest of
# all — but the list is deliberately short. The CORE is never a candidate, and
# neither is a HARVESTER: 20 Ti and actively producing, so if one seals the only
# corridor the node gets blacklisted instead. Conveyors and splitters are already
# walkable, so they never block in the first place.
_ALLY_DOOR_TYPES = (
    EntityType.BARRIER,
    EntityType.LAUNCHER,
    EntityType.SENTINEL,
    EntityType.GUNNER,
)


def _allyDoorAt(bot, ct: Controller, tile: Position) -> bool:
    """Ally building on tile that we would demolish to reopen a route."""
    ent = tileEntity(ct, tile, local_map=bot.hivemind.local_map)
    if not ent or ent.get("team") != ct.get_team():
        return False
    return ent.get("type") in _ALLY_DOOR_TYPES


def _isDoor(bot, ct: Controller, tile: Position) -> bool:
    return _enemyBarrierAt(bot, ct, tile) or _allyDoorAt(bot, ct, tile)


def findBlockingDoor(
    bot, ct: Controller, target: Position, *, relay: bool = False
) -> Position | None:
    """The building sealing the route to `target`, or None.

    `pathKnownBlocked` says a route does not exist; it cannot say what to remove.
    Replanning with doors treated as *passable* answers that: if a route appears,
    the first door along it is the tile holding the lane shut.

    `relay=True` plans with `relayPassable` instead of `walkPassable`, because a
    relay needs a *layable* corridor, which is stricter than a walkable one — a
    belt cannot be laid where a builder can happily stand.

    None means the blockage is something we will not open: terrain, the ally core,
    or an ally harvester. The caller falls through to blacklisting the node.

    This never second-guesses the pathfinder. Both planners already route *around*
    obstructions — `relayPassable` rejects `_RELAY_BLOCK_TYPES` and
    `findPathAroundObstruction` is the explicit fallback — so by the time we get
    here A* has proven there is no way around, which only happens at a choke.
    """
    local_map = bot.hivemind.local_map
    pos = ct.get_position()

    def ok(p: Position) -> bool:
        if p == pos:
            return True
        if not inBounds(ct, p):
            return False
        if _isDoor(bot, ct, p):
            return True  # pretend it is open — this is what we are looking for
        if relay:
            return relayPassable(ct, p, local_map=local_map)
        if _blockedByEnemyBot(ct, p):
            return False
        return walkPassable(ct, p, local_map=local_map)

    path = astarFromSeeds(
        [pos],
        is_passable=ok,
        is_goal=lambda p: p == target,
        heuristic=lambda p: manhattan(p, target),
        max_iters=PATH_MAX_ITERS,
    )
    if not path:
        return None
    for tile in path:
        if _isDoor(bot, ct, tile):
            return tile
    return None


def _clearBlockageState(bot) -> None:
    """Forget the stale "no route" verdicts after acting on a door.

    Without this, `breakOreDeadlock` runs next turn on a verdict recorded *before*
    we opened the lane and blacklists the very node we just unblocked. `bot.stuck`
    matters for the same reason: a builder that stood still for twenty turns would
    otherwise trip the deadlock breaker the moment the door came down.
    """
    bot.lastPathFailed = False
    bot.lastPathFailedTarget = None
    bot.relayPathBlocked = False
    bot.relayPathBlockedFrom = None
    bot.cachedPath = None
    bot.cachedPathTarget = None
    bot.stuck = 0


def _blockedRouteTarget(bot) -> tuple[Position, bool] | None:
    """What is sealed and how to plan for it: (target, relay_mode), or None.

    Walking comes first because it is the more common stall; a blocked relay only
    matters once the builder can actually stand where it needs to build.
    """
    site = bot.oreSite
    if site is not None and pathKnownBlocked(bot, site):
        return site, False
    if getattr(bot, "relayPathBlocked", False):
        origin = getattr(bot, "relayPathBlockedFrom", None)
        if origin is not None:
            return Position(origin[0], origin[1]), True
    return None


def _openAllyDoor(bot, ct: Controller, tile: Position) -> bool:
    """Demolish an ally building sealing the lane. Free, no action cooldown."""
    pos = ct.get_position()
    if isAdjacent(pos, tile):
        if not ct.can_destroy(tile):
            return False
        ct.destroy(tile)
        bot.blockadeDoor = None
        _clearBlockageState(bot)
        return True
    return moveToward(bot, ct, tile)


def openBlockedRoute(bot, ct: Controller) -> bool:
    """Open the door sealing our route instead of giving up on the node.

    Two kinds of door, and the cheaper one is ours. An **ally** building comes down
    with `destroy` — allied-only, free, no action cooldown. An **enemy** barrier gets
    a gunner aimed at it: 3 shots, 6 Ti of ammo, ~3 turns, against a builder's 15
    turns and 30 Ti, and the builder is free again after one turn instead of pinned
    for fifteen. `_GUNNER_PRIORITY` already ranks BARRIER above belts, so `gunnerLoop`
    shoots it with no new machinery, and the gunner then stays on the lane killing
    re-walls: they pay 3 Ti to rebuild, we pay 6 Ti of ammo to reopen — losing on Ti,
    but the lane stays open, which is worth more than the delta.

    Triggers on a blocked **walk** to the ore claim or a blocked **relay** from its
    origin. The relay case needs its own verdict because `pathKnownBlocked` only
    describes `moveToward`; a relay sealed by our own gunner aborts silently and
    would otherwise never reach here.

    The door is sticky. `placeGun` walks to its pad via `moveToward`, which rewrites
    the path verdict to describe the *pad* — so re-deriving the door each turn would
    lose it mid-approach and let the economy walk the builder back at the ore.
    Ping-pong is the failure mode this codebase keeps hitting, so the claim is held
    until the door is gone, an ally gun covers it, or the builder stalls out.
    """
    from main import OPEN_BLOCKED_ROUTE, ORE_DEADLOCK_TURNS

    if not OPEN_BLOCKED_ROUTE or bot.inRelay:
        return False

    from guns import allyGunCovering, placeGun

    door = getattr(bot, "blockadeDoor", None)
    if door is not None:
        tile = Position(door[0], door[1])
        stalled = (
            ORE_DEADLOCK_TURNS
            and getattr(bot, "stuck", 0) >= ORE_DEADLOCK_TURNS
        )
        if stalled or not _isDoor(bot, ct, tile):
            bot.blockadeDoor = None
        elif _allyDoorAt(bot, ct, tile):
            return _openAllyDoor(bot, ct, tile)
        elif allyGunCovering(ct, tile):
            bot.blockadeDoor = None  # already answered
        else:
            return placeGun(bot, ct, tile)

    blocked = _blockedRouteTarget(bot)
    if blocked is None:
        return False
    target, relay_mode = blocked
    found = findBlockingDoor(bot, ct, target, relay=relay_mode)
    if found is None:
        return False  # terrain, core or harvester — caller blacklists the node
    bot.blockadeDoor = (found.x, found.y)
    if _allyDoorAt(bot, ct, found):
        return _openAllyDoor(bot, ct, found)
    if allyGunCovering(ct, found):
        bot.blockadeDoor = None
        return False
    return placeGun(bot, ct, found)


def breakOreDeadlock(bot, ct: Controller) -> bool:
    """Drop the ore claim when a builder has sat motionless too long.

    `bot.stuck` counts turns with no position change (maintained in main). Only
    harasser orbit and guardian patrol read it; economy builders never did, so a
    cluster stuck on one node could sit there for the rest of the game —
    `_clearStickyIfAllyClaimed` exempts anyone already adjacent, so nobody yields.

    State-only: clears the claim so the same turn's economy re-picks. Does not
    consume the turn. Relays are left alone — `relayStuck` already covers those.
    """
    from main import ORE_DEADLOCK_TURNS

    if ORE_DEADLOCK_TURNS is None or ORE_DEADLOCK_TURNS <= 0:
        return False
    if bot.inRelay:
        return False
    # Proven unreachable beats the motionless heuristic: waiting out the full
    # ORE_DEADLOCK_TURNS is pointless once A* has shown there is no route at all.
    # Known case: the walled-off centre ore on atoll, where a builder blacklisted a
    # node, picked another behind the same wall, and cycled for the rest of the game.
    proven_blocked = bot.oreSite is not None and pathKnownBlocked(bot, bot.oreSite)
    if not proven_blocked and getattr(bot, "stuck", 0) < ORE_DEADLOCK_TURNS:
        return False

    if bot.oreSite is not None:
        if not hasattr(bot, "skippedOres"):
            bot.skippedOres = set()
        bot.skippedOres.add((bot.oreSite.x, bot.oreSite.y))
    bot.oreSite = None
    bot.cachedPath = None
    bot.cachedPathTarget = None
    clearRelay(bot)
    bot.stuck = 0  # re-arm; don't fire again until it stalls afresh
    return True


def recheckOreNodes(bot, ct: Controller) -> bool:
    """Last-resort: clear sticky ore state and resume maintain/steal."""
    if bot.inRelay:
        return False

    round_n = ct.get_current_round()
    last = getattr(bot, "lastOreRecheckRound", -999)
    if round_n - last >= ORE_RECHECK_CLEAR_INTERVAL:
        bot.skippedOres = set()
        bot.lastOreRecheckRound = round_n

    # Break soft-idle: drop sticky site / path cache / orphaned stuck relay
    if bot.oreSite is not None:
        bot.skippedOres.add((bot.oreSite.x, bot.oreSite.y))
    bot.oreSite = None
    bot.cachedPath = None
    bot.cachedPathTarget = None
    if getattr(bot, "relayStuck", 0) >= 3 or (
        not bot.inRelay and bot.relayPath is not None
    ):
        clearRelay(bot)

    pick = _pickRecheckOre(bot, ct)
    if pick is None:
        bot.recheckingOre = False
        return False

    bot.recheckingOre = True
    bot.oreSite = pick
    acted = checkOre(bot, ct, pick)
    if bot.inRelay:
        bot.recheckingOre = False
    if not acted:
        bot.recheckingOre = False
    return acted


def exploreFrontier(bot, ct: Controller, *, outward: bool = False) -> bool:
    """Last-resort scouting: walk toward unexplored space.

    Exists because a missed map fingerprint leaves the bot with nothing to aim at and
    no way to recover. `map_id` 0 makes `ORE_LOCATIONS` empty, and on the maps where
    the core sees no ore in its own vision (`heart`, `sweden`, `vase`) that leaves
    `pickEmptyOre` with nothing — and no other step in `runBuilder` moves a builder
    that has no ore claim, so the economy never starts. It also recovers
    `enemyCorePos` by walking into vision of it, which bandit needs for more than
    baron did: the spread zones and the cordon band are both measured from it.

    Sits last in the chain, so on a map we did fingerprint the static tables supply
    targets and this never runs — no knob needed to gate it on `map_id`.

    Unbounded, like everything else in this bot: it walks toward unexplored map,
    not within a radius of anything.

    The target is sticky. Rescanning every turn would walk the whole ledger (~900
    cells, four lookups each) for every unit against a 10 ms budget, and an overrun
    truncates the turn silently. It is re-picked only when missing, reached, no longer
    unknown, stalled, or older than `FRONTIER_REFRESH_TURNS`.
    """
    from main import FRONTIER_REFRESH_TURNS, ORE_DEADLOCK_TURNS
    from pathing import findFrontierTarget

    if FRONTIER_REFRESH_TURNS is None or FRONTIER_REFRESH_TURNS <= 0:
        return False
    if bot.inRelay:
        return False

    local_map = bot.hivemind.local_map
    pos = ct.get_position()
    round_n = ct.get_current_round()

    target = getattr(bot, "frontierTarget", None)
    if target is not None:
        stale = round_n - getattr(bot, "frontierRound", -(10**9)) >= FRONTIER_REFRESH_TURNS
        stalled = ORE_DEADLOCK_TURNS and getattr(bot, "stuck", 0) >= ORE_DEADLOCK_TURNS
        # Reaching it is not the point — seeing it is. Once the tile is in the ledger
        # it is no longer a frontier, so the scan must run again.
        if stale or stalled or target in local_map or (pos.x, pos.y) == target:
            target = None

    if target is None:
        found = findFrontierTarget(
            ct,
            local_map,
            origin=pos,
            home=bot.corePos,
            outward=outward,
        )
        if found is None:
            bot.frontierTarget = None
            return False
        target = (found.x, found.y)
        bot.frontierTarget = target
        bot.frontierRound = round_n

    return moveToward(bot, ct, Position(target[0], target[1]))
