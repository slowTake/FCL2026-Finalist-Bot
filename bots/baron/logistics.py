"""Default builder logistics: harvesters, inbound relay, heal."""

from __future__ import annotations

from fcode import Controller, Direction, EntityType, Environment, Position

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


def _coreFootprint(core_pos: Position) -> list[Position]:
    return [
        core_pos,
        Position(core_pos.x + 1, core_pos.y),
        Position(core_pos.x, core_pos.y + 1),
        Position(core_pos.x + 1, core_pos.y + 1),
    ]


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


# ------------------------------------------------------------------
# Orphan relay registry — store slots 1-3 (main.SLOT_ORPHAN_1/2/3)
# ------------------------------------------------------------------
# `abortRelay` above wipes only the aborting builder's own state; nothing else
# ever learns the harvester it was relaying from still needs a belt unless
# this same builder happens to re-find that exact tile itself later. This is a
# small, team-wide "still needs a belt" mailbox so any *other* idle builder
# can pick it up — see CONTEXT.md's RelayToCore section for the full design
# rationale (why report-only with no claim field, why only some abort sites
# report, why 3 slots).
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


def _orphanSlots() -> tuple[int, int, int]:
    from main import SLOT_ORPHAN_1, SLOT_ORPHAN_2, SLOT_ORPHAN_3

    return (SLOT_ORPHAN_1, SLOT_ORPHAN_2, SLOT_ORPHAN_3)


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


def _readOrphanRegistry(ct: Controller) -> list[tuple[int, tuple[int, int, int]]]:
    """[(slot, (x, y, reportedAtTurn)), ...] for every occupied slot."""
    out = []
    for slot in _orphanSlots():
        rec = _unpackOrphan(ct.read_store(slot))
        if rec is not None:
            out.append((slot, rec))
    return out


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
    if ct.is_in_vision(tile):
        bid = ct.get_tile_building_id(tile)
        return (
            bid is not None
            and ct.get_entity_type(bid) == EntityType.HARVESTER
            and ct.get_team(bid) == ct.get_team()
        )
    entry = bot.hivemind.local_map.get((tile.x, tile.y), {})
    ent = entry.get("entity")
    return (
        bool(ent)
        and ent.get("type") == EntityType.HARVESTER
        and ent.get("team") == ct.get_team()
    )


def reportOrphanRelay(bot, ct: Controller, tile: Position) -> None:
    """Broadcast `tile` (a harvester whose relay we're giving up on) for pickup.

    Dedups by tile — if already registered (by us or someone else), just
    refreshes its turn instead of taking a second slot; two builders
    reporting the same tile in the same round write byte-identical values, so
    that collision is harmless by construction. Otherwise takes a free slot,
    or evicts whichever entry is stalest if all three are already holding
    *different* tiles.
    """
    if not _rememberedAllyHarvester(bot, ct, tile):
        return  # not actually a harvester we own — nothing to report
    turn = ct.get_current_round() & _ORPHAN_TURN_MASK
    entries = _readOrphanRegistry(ct)
    for slot, (x, y, _t) in entries:
        if (x, y) == (tile.x, tile.y):
            ct.write_store(slot, _packOrphan(tile.x, tile.y, turn))
            return
    occupied = {slot for slot, _ in entries}
    free = [s for s in _orphanSlots() if s not in occupied]
    if free:
        ct.write_store(free[0], _packOrphan(tile.x, tile.y, turn))
        return
    oldest_slot, _ = min(entries, key=lambda e: e[1][2])
    ct.write_store(oldest_slot, _packOrphan(tile.x, tile.y, turn))


def clearOrphanRelay(ct: Controller, tile: Position) -> None:
    """Clear a registry entry for `tile`, if one exists — its relay is done."""
    for slot, (x, y, _t) in _readOrphanRegistry(ct):
        if (x, y) == (tile.x, tile.y):
            ct.write_store(slot, 0)
            return


def _reportAndAbort(bot, ct: Controller) -> None:
    """abortRelay, but first offer the harvester up for another builder to finish.

    Only called from relayToCore's builder-local / fog-dependent abort sites —
    a different builder, with different fog knowledge or a different approach
    angle, genuinely has a shot where this one didn't. Deliberately *not*
    called from the enemy-core-adjacent abort (every builder re-derives the
    same geometric verdict — reporting it just thrashes the registry) or the
    titanium-starved placement abort (every builder shares one bank and fails
    identically; it self-resolves once Ti arrives, so reporting it floods the
    registry with a tile that isn't actually stuck).
    """
    if bot.relayOrigin is not None:
        reportOrphanRelay(bot, ct, bot.relayOrigin)
    abortRelay(bot)


def _pickOrphanRelay(bot, ct: Controller) -> Position | None:
    """Nearest usable orphan from the shared registry, else None.

    Applies the same filters every other candidate in `pickMaintainOre` /
    `_pickRecheckOre` already goes through — `_allowOreSite` (so a guardian's
    leash confines this to in-leash orphans for free, no guardian-specific
    code needed) and `skippedOres` (so a builder that already gave up on this
    exact tile doesn't immediately re-pick it here). Also evicts entries that
    live vision now proves stale (TTL-expired, or the harvester is visibly
    gone) — not-in-vision entries are trusted as-is, same fog tolerance as
    the rest of this file.
    """
    from main import ORPHAN_REGISTRY_TTL_TURNS

    skipped = getattr(bot, "skippedOres", set())
    now = ct.get_current_round()
    pos = ct.get_position()
    best: Position | None = None
    best_d = 10**9
    for slot, (x, y, turn) in _readOrphanRegistry(ct):
        if now - turn > ORPHAN_REGISTRY_TTL_TURNS:
            ct.write_store(slot, 0)
            continue
        if (x, y) in skipped:
            continue
        tile = Position(x, y)
        if not _allowOreSite(bot, tile):
            continue
        if ct.is_in_vision(tile):
            bid = ct.get_tile_building_id(tile)
            if (
                bid is None
                or ct.get_entity_type(bid) != EntityType.HARVESTER
                or ct.get_team(bid) != ct.get_team()
            ):
                ct.write_store(slot, 0)  # visibly no longer a live ally harvester
                continue
            if allyBuilderNearOre(ct, tile):
                continue  # someone's already there working it
        d = manhattan(pos, tile)
        if d < best_d:
            best_d = d
            best = tile
    return best


def relayEverComplete(ct: Controller) -> bool:
    """True after the team has finished at least one inbound relay (slot 0)."""
    from main import SLOT_RELAY_COMPLETE

    return ct.read_store(SLOT_RELAY_COMPLETE) != 0


def enemyTurretInVision(ct: Controller) -> bool:
    """True if any enemy gunner, sentinel, or launcher is in vision."""
    me = ct.get_team()
    for eid in ct.get_nearby_entities():
        if ct.get_team(eid) == me:
            continue
        if ct.get_entity_type(eid) in (
            EntityType.GUNNER,
            EntityType.SENTINEL,
            EntityType.LAUNCHER,
        ):
            return True
    return False


def nearestEnemyTurret(ct: Controller) -> Position | None:
    """Nearest enemy gunner/sentinel/launcher position in vision, else None."""
    me = ct.get_team()
    pos = ct.get_position()
    best: Position | None = None
    best_d = 10**9
    for eid in ct.get_nearby_entities():
        if ct.get_team(eid) == me:
            continue
        if ct.get_entity_type(eid) not in (
            EntityType.GUNNER,
            EntityType.SENTINEL,
            EntityType.LAUNCHER,
        ):
            continue
        epos = ct.get_position(eid)
        d = manhattan(pos, epos)
        if d < best_d:
            best_d = d
            best = epos
    return best


def contestTurret(bot, ct: Controller) -> bool:
    """Place a gunner toward the nearest enemy turret in vision."""
    target = nearestEnemyTurret(ct)
    if target is None:
        return False
    from guns import placeGun

    return placeGun(bot, ct, target)


def markRelayComplete(ct: Controller) -> None:
    from main import SLOT_RELAY_COMPLETE

    ct.write_store(SLOT_RELAY_COMPLETE, 1)


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
    for tile in _coreFootprint(core_pos):
        if abs(pos.x - tile.x) + abs(pos.y - tile.y) == 1:
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
    for ctile in _coreFootprint(enemy_core):
        if abs(tile.x - ctile.x) + abs(tile.y - ctile.y) == 1:
            return True
    return False


def travelFacing(from_pos: Position, to_pos: Position) -> Direction:
    return nearestCardinal(from_pos.direction_to(to_pos))


def facingIntoCore(pos: Position, core_pos: Position) -> Direction:
    best: Direction | None = None
    best_dist = 10**9
    for tile in _coreFootprint(core_pos):
        dist = abs(pos.x - tile.x) + abs(pos.y - tile.y)
        if dist != 1:
            continue
        if dist < best_dist:
            best_dist = dist
            best = travelFacing(pos, tile)
    if best is not None and best != Direction.CENTRE:
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

    `includeStatic` unions the two instead. Offence needs it: `denyOre` targets ore in
    the enemy's expansion ring, but the ledger only ever holds ore *we have already
    scouted* — our own side early on, which the midline guard then correctly rejects.
    Without the union `denyOre` has no candidates until a harasser has walked to enemy
    ore, which is the trip denyOre exists to motivate.
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
    """During home-guard, only ore within DEFEND_HOME_CHEBYSHEV of ally core."""
    if not getattr(bot, "nearCoreEconomy", False):
        return True
    if bot.corePos is None:
        return True
    return _chebyshevToCore(ore, bot.corePos) <= DEFEND_HOME_CHEBYSHEV


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


def _defaultOreRank(bot) -> int:
    """Stable rank among economy defaults (idx % 5 == 3): 3→0, 8→1, 13→2, …"""
    idx = bot.hivemind.my_index
    if idx <= 0 or idx % 5 != 3:
        return 0
    return (idx - 3) // 5


def _oreStaggerKey(ore: Position, core: Position | None) -> tuple:
    """Team-stable sort: closer to core first, then x/y."""
    if core is not None:
        return (manhattan(ore, core), ore.x, ore.y)
    return (ore.x, ore.y)


def _oreConvenienceKey(
    ore: Position, core: Position | None, self_pos: Position
) -> tuple:
    """Near core (defendable), then near builder (reachable), then x/y."""
    if core is not None:
        return (manhattan(ore, core), manhattan(ore, self_pos), ore.x, ore.y)
    return (manhattan(ore, self_pos), ore.x, ore.y)


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

    `offense.denyOre` walls empty ore in the enemy's expansion ring, and those
    barriers must not lock *us* out of the node forever. `checkOre` already destroys
    an ally ore-block and builds the harvester; the node simply never got selected,
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
    """Pick empty ore: convenience or index-stagger; skip ally-on-tile pads.

    "Empty" admits ore carrying one of our own `denyOre` barriers — `checkOre` clears
    it and builds on arrival, so denial is never a lock-out.
    """
    from main import ORE_STAGGER

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

    if not ORE_STAGGER:
        pool.sort(key=lambda o: _oreConvenienceKey(o, core, self_pos))
        return pool[0]

    pool.sort(key=lambda o: _oreStaggerKey(o, core))
    rank = _defaultOreRank(bot)
    return pool[rank % len(pool)]


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
    if abs(pos.x - tile.x) + abs(pos.y - tile.y) == 1:
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


def pickMaintainOre(bot, ct: Controller) -> Position | None:
    """Prefer sticky site, else a reported orphan, else staggered empty/harvester/block."""
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

    def score(ore: Position) -> tuple:
        if core is not None:
            return (manhattan(ore, core), manhattan(ore, pos))
        return (manhattan(ore, pos),)

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
            if ct.get_entity_type(bid) == EntityType.HARVESTER:
                consider(ore)
            elif _shouldClearAllyOreBlock(ct, ore):
                consider(ore)
        else:
            entry = bot.hivemind.local_map.get((ore.x, ore.y), {})
            ent = entry.get("entity")
            if ent and ent.get("type") == EntityType.HARVESTER:
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
        if ct.get_entity_type(bid) == EntityType.HARVESTER:
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


def neededBeltFacing(
    path: list[Position], idx: int, core_pos: Position
) -> Direction:
    """Facing for path[idx]: toward next hop, or into core on last tile."""
    if idx >= len(path) - 1:
        return facingIntoCore(path[idx], core_pos)
    return travelFacing(path[idx], path[idx + 1])


def beltFacingOk(ct: Controller, pos: Position, needed: Direction) -> bool:
    if not ct.is_in_vision(pos):
        return False
    bid = ct.get_tile_building_id(pos)
    if bid is None:
        return False
    if ct.get_entity_type(bid) not in (EntityType.CONVEYOR, EntityType.SPLITTER):
        return False
    try:
        return ct.get_direction(bid) == needed
    except Exception:
        return False


def clearWrongBelt(bot, ct: Controller, tile: Position) -> str:
    """Clear wrong-facing belt. Returns acted|waiting|clear|failed."""
    if not ct.is_in_vision(tile):
        return "waiting"
    bid = ct.get_tile_building_id(tile)
    if bid is None:
        return "clear"
    etype = ct.get_entity_type(bid)
    if etype not in (EntityType.CONVEYOR, EntityType.SPLITTER):
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

    adjacent = abs(pos.x - tile.x) + abs(pos.y - tile.y) == 1
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
    if ct.can_build_conveyor(tile, facing):
        ct.build_conveyor(tile, facing)
        return "placed"
    return "failed"


def extendRelayWatermark(
    ct: Controller,
    path: list[Position],
    core_pos: Position,
    built_to: int,
) -> int:
    """Raise watermark through contiguous visible correct-facing belts."""
    i = built_to + 1
    while i < len(path):
        tile = path[i]
        if not ct.is_in_vision(tile):
            break
        needed = neededBeltFacing(path, i, core_pos)
        if not beltFacingOk(ct, tile, needed):
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

    def is_goal(p: Position) -> bool:
        return isCoreAdjacent(ct, p, core_pos)

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
            ct, path, core_pos, bot.relayBuiltTo
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
        bot.relayBuiltTo = extendRelayWatermark(ct, path, core_pos, -1)

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
        needed = neededBeltFacing(path, idx, core_pos)
        if ct.is_in_vision(tile) and beltFacingOk(ct, tile, needed):
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
    needed = neededBeltFacing(path, idx, core_pos)
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
        bot.relayBuiltTo = extendRelayWatermark(ct, new_path, core_pos, -1)
        return

    # Non-belt building blocking — replan
    if ct.is_in_vision(tile):
        block_id = ct.get_tile_building_id(tile)
        if block_id is not None:
            etype = ct.get_entity_type(block_id)
            if etype not in (EntityType.CONVEYOR, EntityType.SPLITTER):
                bot.relayPath = None
                bot.relayBuiltTo = -1
                return

    if ct.is_in_vision(tile) and bid_is_belt(ct, tile):
        if not beltFacingOk(ct, tile, needed):
            result = clearWrongBelt(bot, ct, tile)
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

    if beltFacingOk(ct, tile, needed):
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

    adjacent = abs(pos.x - tile.x) + abs(pos.y - tile.y) == 1
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
    return ct.get_entity_type(bid) in (EntityType.CONVEYOR, EntityType.SPLITTER)


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
    if abs(pos.x - target.x) + abs(pos.y - target.y) == 1:
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
        if abs(pos.x - tile.x) + abs(pos.y - tile.y) == 1:
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
            if abs(pos.x - tile.x) + abs(pos.y - tile.y) == 1:
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


DEFEND_HOME_CHEBYSHEV = 4
ORE_RECHECK_CLEAR_INTERVAL = 50


def _chebyshevToCore(pos: Position, core_pos: Position) -> int:
    best = 10**9
    for tile in _coreFootprint(core_pos):
        d = max(abs(pos.x - tile.x), abs(pos.y - tile.y))
        if d < best:
            best = d
    return best


def _nearCore(bot, ct: Controller) -> bool:
    if bot.corePos is None:
        return False
    return _chebyshevToCore(ct.get_position(), bot.corePos) <= DEFEND_HOME_CHEBYSHEV


def moveTowardLeash(
    bot, ct: Controller, core_pos: Position, *, radius: int = DEFEND_HOME_CHEBYSHEV
) -> bool:
    """Path back inside `radius` Chebyshev of the core — not toward `core_pos` itself.

    `moveToward(bot, ct, core_pos)` targets the literal core tile, which is a
    building and therefore never passes `is_passable`. A*'s neighbour expansion
    filters on that before a node is ever added to the open set (`pathing.py`'s
    `astarFromSeeds`), so the core can never be reached as a goal — the search
    always returns `None` and every leash recall silently degrades to
    `moveToward`'s blind single-cardinal-step fallback, which has no obstacle
    avoidance at all. On an open map that nudge usually happens to work; on a
    tight map (e.g. Heart) a wall between the guardian and home blocks that one
    step outright, and the guardian is stuck outside the leash for good — it
    reads as "not returning" but is actually failing to path at all.

    Targeting the *region* (any walkable tile within `radius`) instead of a
    single unreachable point lets A* actually route around the wall.
    """
    if ct.get_move_cooldown() != 0:
        return False
    pos = ct.get_position()
    if _chebyshevToCore(pos, core_pos) <= radius:
        return False
    local_map = bot.hivemind.local_map

    def ok(p: Position) -> bool:
        if p == pos:
            return True
        if not inBounds(ct, p):
            return False
        if _blockedByEnemyBot(ct, p):
            return False
        return walkPassable(ct, p, local_map=local_map)

    path = astarFromSeeds(
        [pos],
        is_passable=ok,
        is_goal=lambda p: _chebyshevToCore(p, core_pos) <= radius,
        heuristic=lambda p: max(0, _chebyshevToCore(p, core_pos) - radius),
        max_iters=PATH_MAX_ITERS,
    )

    if not path or len(path) < 2:
        # Last resort only — no obstacle avoidance, same as moveToward's own
        # fallback. Kept so open ground still gets a nudge if A* found nothing.
        step = nearestCardinal(pos.direction_to(core_pos))
        if step != Direction.CENTRE and ct.can_move(step):
            ct.move(step)
            return True
        return False

    nxt = path[1]
    step = nearestCardinal(pos.direction_to(nxt))
    if step != Direction.CENTRE and ct.can_move(step):
        ct.move(step)
        bot.cachedPath = None  # this path is region-goal-shaped, not moveToward's
        return True
    return False


def _safeCoreStandTile(bot, ct: Controller, core: Position) -> Position | None:
    """Walkable tile cardinally adjacent to the core footprint, no gunner covering it.

    Healing is done from *beside* the core, and the 2x2 footprint offers eight such
    tiles — so when a gunner covers the approach there is usually another side that
    works. Picking one is strictly better than walking at the footprint itself.

    Returns None when every side is covered, which is a deliberate refusal: healing
    restores 4 HP/turn, and standing in a gunner's line to do it feeds a 40 HP
    builder to 10 dmg/turn. Losing a 30 Ti builder to heal 16 HP is not a defence.
    """
    from offense import _enemyGunners, _tileUnderGunnerFire

    gunners = _enemyGunners(ct)
    pos = ct.get_position()
    footprint = _coreFootprint(core)
    on_core = {(t.x, t.y) for t in footprint}
    local_map = bot.hivemind.local_map
    best: Position | None = None
    best_key: tuple[int, int, int] | None = None
    for tile in footprint:
        for d in CARDINALS:
            cand = tile.add(d)
            if (cand.x, cand.y) in on_core:
                continue
            if not inBounds(ct, cand):
                continue
            if cand != pos and not walkPassable(ct, cand, local_map=local_map):
                continue
            if gunners and _tileUnderGunnerFire(ct, cand, gunners):
                continue
            key = (manhattan(pos, cand), cand.x, cand.y)
            if best_key is None or key < best_key:
                best_key = key
                best = cand
    return best


def defendHome(bot, ct: Controller) -> bool:
    """Near-core only: keep a damaged ally core alive.

    This used to lead with an attack branch — nearest enemy turret (else builder),
    `moveToward` it, poke for 2 dmg at 2 Ti. That is dropped. The Ti rate alone was
    bad (1.0 Ti/dmg against a gunner's 0.20), but the approach was worse: a 40 HP
    builder closing on something dealing 10 dmg/turn at sqrt(13) dies in four turns
    and has to survive the walk before landing a single 2-damage hit. We were
    trading a 30 Ti builder for ~6 damage to a 10 Ti gunner. Turrets answer turrets
    — `defGun` and `defSentinel` sit above this in the chain.

    Healing is no longer gated on an empty vision either. The core is healed
    *because* something is shelling it; the old gate meant any enemy builder
    wandering into view flipped the guardian into the attack branch and it
    abandoned the core to give chase.
    """
    if bot.inRelay or getattr(bot, "recheckingOre", False):
        return False
    if not _nearCore(bot, ct):
        return False

    if bot.corePos is None or ct.get_action_cooldown() != 0:
        return False
    for tile in _coreFootprint(bot.corePos):
        bid = None
        if ct.is_in_vision(tile):
            bid = ct.get_tile_building_id(tile)
        if bid is None or ct.get_entity_type(bid) != EntityType.CORE:
            continue
        if ct.get_team(bid) != ct.get_team():
            continue
        if ct.get_hp(bid) >= 500:
            return False
        pos = ct.get_position()
        # Heal any adjacent core footprint tile
        for d in CARDINALS:
            check = pos.add(d)
            if not inBounds(ct, check):
                continue
            if ct.can_heal(check):
                hb = ct.get_tile_building_id(check)
                if (
                    hb is not None
                    and ct.get_entity_type(hb) == EntityType.CORE
                    and ct.get_team(hb) == ct.get_team()
                ):
                    ct.heal(check)
                    return True
        # Approach a side no gunner covers, rather than the footprint itself.
        # None means every side is covered — fall through instead of walking in.
        stand = _safeCoreStandTile(bot, ct, bot.corePos)
        if stand is None or stand == pos:
            return False
        return moveToward(bot, ct, stand)
    return False


def healAlly(bot, ct: Controller) -> bool:
    if ct.get_action_cooldown() != 0:
        return False
    pos = ct.get_position()
    priority = (
        EntityType.CORE,
        EntityType.GUNNER,
        EntityType.SENTINEL,
        EntityType.HARVESTER,
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


def _pickRecheckOre(bot, ct: Controller) -> Position | None:
    """Prefer a reported orphan, then empty, then ally-blocked, then harvester."""
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
        elif ct.get_entity_type(bid) == EntityType.HARVESTER:
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
            elif ct.get_entity_type(bid) == EntityType.HARVESTER:
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
        if ent and ent.get("type") == EntityType.HARVESTER:
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
    me = ct.get_team()
    if ct.is_in_vision(tile):
        bid = ct.get_tile_building_id(tile)
        if bid is None:
            return False
        return (
            ct.get_entity_type(bid) == EntityType.BARRIER
            and ct.get_team(bid) != me
        )
    cell = bot.hivemind.local_map.get((tile.x, tile.y))
    ent = cell.get("entity") if cell else None
    if not ent or ent.get("team") == me:
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
    me = ct.get_team()
    if ct.is_in_vision(tile):
        bid = ct.get_tile_building_id(tile)
        if bid is None:
            return False
        return (
            ct.get_team(bid) == me
            and ct.get_entity_type(bid) in _ALLY_DOOR_TYPES
        )
    cell = bot.hivemind.local_map.get((tile.x, tile.y))
    ent = cell.get("entity") if cell else None
    if not ent or ent.get("team") != me:
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
    if abs(pos.x - tile.x) + abs(pos.y - tile.y) == 1:
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
    `pickEmptyOre` with nothing — and no other step in `runDefault` moves a builder
    that has no ore claim, so the economy never starts. Harassers have the mirror
    problem: without `enemyCorePos`, `seekCore`, `denyOre` and `offGun`'s core branch
    are all inert.

    Sits last in both chains, so on a map we did fingerprint the static tables supply
    targets and this never runs — no knob needed to gate it on `map_id`.

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
