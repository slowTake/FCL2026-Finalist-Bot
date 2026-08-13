"""Ore/harvester economy, home upkeep, and shared team-state readers.

Split out of the old monolithic logistics.py — belt/relay mechanics now live
in `logistics.py`, generic movement/pathing in `movement.py`. This is the
"everything else" bucket: picking which ore tile to work, building and
maintaining harvesters, healing, defending the core, and the small
team-state flags (guardian leash, enemy-turret sighting) that don't belong to
either of the other two.

Builds on both `movement.py` and `logistics.py` (e.g. `checkOre` hands a
claimed harvester off to `logistics.relayToCore`). Neither of those modules
imports back from here, so there is no cycle.
"""

from __future__ import annotations

from fcode import Controller, Direction, EntityType, Environment, GameConstants, Position

from geometry import (
    BELT_TYPES,
    DEFEND_HOME_CHEBYSHEV,
    TURRET_TYPES,
    angleBucket,
    beltNeighbors,
    chebyshevToCore,
    coreFootprint,
    harvesterClaimedByAlly,
    isAdjacent,
    manhattanToCore,
    nearCore,
)
from logistics import clearRelay, coreThreatened, relayToCore
from movement import allyOccupied, moveToward, pathKnownBlocked
from pathing import CARDINALS, inBounds, manhattan, walkPassable

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


def guardianLeash(ct: Controller) -> int:
    """Chebyshev radius a guardian may roam from core — tight while home is probed."""
    from main import GUARDIAN_LEASH_TIGHT, GUARDIAN_LEASH_WIDE

    return GUARDIAN_LEASH_TIGHT if coreThreatened(ct) else GUARDIAN_LEASH_WIDE


def _enemyTurretPositions(ct: Controller):
    """Yield positions of enemy gunner/sentinel/launcher entities in vision.

    Shared scan behind enemyTurretInVision/nearestEnemyTurret — both used to
    walk get_nearby_entities() independently with the same team/type filter.
    """
    me = ct.get_team()
    for eid in ct.get_nearby_entities():
        if ct.get_team(eid) == me:
            continue
        if ct.get_entity_type(eid) in TURRET_TYPES:
            yield ct.get_position(eid)


def enemyTurretInVision(ct: Controller) -> bool:
    """True if any enemy gunner, sentinel, or launcher is in vision."""
    return next(_enemyTurretPositions(ct), None) is not None


def nearestEnemyTurret(ct: Controller) -> Position | None:
    """Nearest enemy gunner/sentinel/launcher position in vision, else None."""
    pos = ct.get_position()
    best: Position | None = None
    best_d = 10**9
    for epos in _enemyTurretPositions(ct):
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


def allyBeltDamagedNearCore(ct: Controller, core_pos: Position, radius: int) -> bool:
    """True if an ally conveyor/splitter within `radius` Chebyshev of the core is hurt.

    Second guardian-leash trigger, alongside `enemyTurretInVision` in
    `_run_core` (main.py) — that one is deliberately turret-only, since a
    lone enemy builder merely scouting past the core isn't yet a threat worth
    tightening the leash for. That leaves a real gap: an enemy builder
    actively tearing through belts *before* it ever places a gun is exactly a
    threat, and produces no turret sighting at all until the gun already
    exists — by which point the belts it walked past are already gone. A
    damaged ally belt is unambiguous evidence something hostile just hit it
    (belts don't lose HP any other way), so this closes that gap without
    reopening the "any scouting builder tightens the leash" false-positive
    the turret-only design exists to avoid — an enemy builder that hasn't hit
    anything yet still doesn't trip it.

    Core-only, same as `enemyTurretInVision` there: the core's own vision
    (r²=36, ~6 tiles) already covers `radius` (DEFEND_HOME_CHEBYSHEV, 4) with
    room to spare, and `HiveMind.tick` has already enumerated
    `get_nearby_entities()` this turn, so the scan is effectively free.
    """
    me = ct.get_team()
    for eid in ct.get_nearby_entities():
        if ct.get_team(eid) != me:
            continue
        if ct.get_entity_type(eid) not in BELT_TYPES:
            continue
        epos = ct.get_position(eid)
        if chebyshevToCore(epos, core_pos) > radius:
            continue
        if ct.get_hp(eid) < ct.get_max_hp(eid):
            return True
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
    return chebyshevToCore(ore, bot.corePos) <= DEFEND_HOME_CHEBYSHEV


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


def spreadZoneForIndex(idx: int) -> tuple[int, int]:
    """(angle_bucket, distance_band) for a builder index. Pure.

    Consecutive indices land in adjacent wedges, not adjacent bands: the first
    SPREAD_ANGLE_BUCKETS builders fan out around the core, and only the next
    one starts a second band further out.
    """
    from main import SPREAD_ANGLE_BUCKETS, SPREAD_DISTANCE_BANDS

    if idx < 0:
        idx = 0
    buckets = max(1, SPREAD_ANGLE_BUCKETS)
    bands = max(1, SPREAD_DISTANCE_BANDS)
    return (idx % buckets, (idx // buckets) % bands)


def spreadZone(bot) -> tuple[int, int]:
    """This builder's (angle_bucket, distance_band), cached on bot._spreadZone."""
    cached = getattr(bot, "_spreadZone", None)
    if cached is not None:
        return cached
    zone = spreadZoneForIndex(bot.hivemind.my_index)
    bot._spreadZone = zone
    return zone


def _spreadScore(bot, ore: Position) -> int:
    """Penalty, in ore-score points, for `ore` being outside this bot's zone.

    Soft, always additive into the convenience blend below, never a filter —
    a zone with no ore should drift its builder into a neighbour's leftovers,
    not starve it. Ported from bandit, which uses this as its sole
    anti-clumping mechanism. Returns 0 when the enemy core isn't known yet, so
    an unidentified map degrades to the plain distance blend.
    """
    from main import (
        SPREAD_ANGLE_BUCKETS,
        SPREAD_ANGLE_PENALTY,
        SPREAD_BAND_PENALTY,
        SPREAD_DISTANCE_BANDS,
    )

    core = bot.corePos
    enemy = getattr(bot, "enemyCorePos", None)
    if core is None or enemy is None:
        return 0

    want_angle, want_band = spreadZone(bot)
    buckets = max(1, SPREAD_ANGLE_BUCKETS)
    got_angle = angleBucket(core, enemy, ore, buckets)
    # Circular distance: with 4 buckets, wedge 0 and wedge 3 are neighbours
    # (1 apart), not opposites (3 apart).
    d_angle = min(
        (got_angle - want_angle) % buckets, (want_angle - got_angle) % buckets
    )
    penalty = d_angle * SPREAD_ANGLE_PENALTY

    # Band is radial along the core->enemy-core span, measured as a fraction
    # of that span so it scales with map size.
    bands = max(1, SPREAD_DISTANCE_BANDS)
    span = manhattanToCore(enemy, core) or 1
    frac = manhattanToCore(ore, core) / span
    got_band = min(int(frac * bands), bands - 1)
    penalty += abs(got_band - want_band) * SPREAD_BAND_PENALTY
    return int(penalty)


def _oreConvenienceKey(
    bot, ore: Position, core: Position | None, self_pos: Position
) -> tuple:
    """Blend of near-builder (fast to reach), near-core (defendable), and zone spread.

    Previously a strict lexicographic (core_dist, self_dist, ...) sort — core
    distance always won outright, so a builder would walk past ore right next
    to it to reach ore merely closer to the core, slowing down how fast a
    fresh harvester actually starts producing. The weighted-sum blend keeps
    core proximity as a real factor (still breaks close ties toward the more
    defendable option) without letting it override a much closer pick.

    `core_d` is `manhattanToCore`, not raw `manhattan` to the core's anchor
    tile — the anchor is one corner of a 2x2 footprint, so raw distance is
    biased by up to 2 tiles depending which corner a team's core happens to
    occupy. On a mirrored map that bias flips per team and can make one
    team's builders read a contested node as closer than their own
    mirror-equivalent safe node while the other team reads it correctly.
    """
    from main import ORE_PICK_CORE_WEIGHT, ORE_PICK_SELF_WEIGHT

    self_d = manhattan(ore, self_pos)
    spread = _spreadScore(bot, ore)
    if core is None:
        return (self_d + spread, ore.x, ore.y)
    core_d = manhattanToCore(ore, core)
    score = ORE_PICK_SELF_WEIGHT * self_d + ORE_PICK_CORE_WEIGHT * core_d + spread
    return (score, ore.x, ore.y)


def _clearStickyIfAllyClaimed(bot, ct: Controller) -> None:
    """Drop a sticky ore/harvester claim if another ally already sits on it.

    Non-harvester buildings still exempt (an ally block awaiting
    _shouldClearAllyOreBlock, a turret, etc. — those have their own handling
    and aren't "claimed" in this sense). Harvesters used to be exempt too —
    any building at all short-circuited this — which meant a builder blocked
    by an ally converging on the same harvester (pre-belt, so
    harvesterClaimedByAlly reads False) never dropped the stale claim: it
    just re-picked the identical contested tile every turn after each
    RELAY_STUCK_TURNS abort (abortRelay/clearRelay never touch bot.oreSite —
    see buildHarvester's docstring for why that's deliberate elsewhere), the
    two builders stepping around each other with no coordination
    (relayToCore/moveToward don't treat ally builders as obstacles). Only
    ever reached with bot.inRelay already False (pickMaintainOre's caller
    short-circuits to relayToCore directly whenever inRelay is True), so this
    can never yank oreSite out from under an active relay.
    """
    site = bot.oreSite
    if site is None:
        return
    if not ct.is_in_vision(site):
        return
    bid = ct.get_tile_building_id(site)
    if bid is not None and ct.get_entity_type(bid) != EntityType.HARVESTER:
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
    """Pick empty ore by convenience+spread score; skip ally-on-tile pads.

    "Empty" admits ore carrying one of our own `denyOre` barriers — `checkOre`
    clears it and builds on arrival, so denial is never a lock-out.
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


def _shouldClearAllyOreBlock(
    ct: Controller, tile: Position, *, bot=None
) -> bool:
    """Clear ally ore blockers unless gunner/sentinel and that ore is threatened.

    A **barrier** on the enemy's side of the midline (`bot` supplied, and
    `manhattanToCore(tile, ally_core) > manhattanToCore(tile, enemy_core)`) is
    left alone. That's the exact test `offense.denyOre` uses to decide a tile is worth
    denying in the first place — a barrier sitting there is presumptively a
    live denial, not stray clutter. Without this, a default/guardian running
    low on nearby ore (`_allowOreSite` has no leash outside `nearCoreEconomy`,
    so nothing stops it reaching this far) would destroy it to build a
    harvester, while the harasser's `denyOre` — which has no notion that
    another ally wants the tile — immediately re-picks the same now-empty
    tile as its best candidate and rebuilds the barrier. Two of our own units
    then fight over one tile forever: place, destroy, place, on repeat, for
    no gain on either side. Gunner/sentinel/launcher blockers are unaffected
    — those are a different, more deliberate placement mistake worth
    reclaiming regardless of side.
    """
    etype = _allyOreBlock(ct, tile)
    if etype is None:
        return False
    if etype in (EntityType.GUNNER, EntityType.SENTINEL) and tileThreatenedByGunner(
        ct, tile
    ):
        return False
    if etype == EntityType.BARRIER and bot is not None:
        core = bot.corePos
        enemy = getattr(bot, "enemyCorePos", None)
        if (
            core is not None
            and enemy is not None
            and manhattanToCore(tile, core) > manhattanToCore(tile, enemy)
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


def _harvesterOwnerAllowed(bot, ct: Controller, owner_team, tile: Position) -> bool:
    """True if a harvester owned by owner_team, sitting at tile, may be picked/relayed by us.

    Always true for our own. For an enemy-owned harvester this is the single
    choke point every picker reads — see main.STEAL_ENEMY_HARVESTERS and
    CONTEXT.md's StealEnemyHarvesters section for the mechanic this gates: an
    ally conveyor can pull output off an opposing team's harvester without its
    ownership ever changing, so relaying one captures its titanium for the
    cost of belts alone. Every consumer (checkOre, pickMaintainOre,
    _pickRecheckOre, finishNearbyHarvester) reads this one function, so the
    whole feature is a single off-switch.

    Also bounds how far we'll go for one — see STEAL_HARVESTER_MAX_CHEBYSHEV's
    comment for why this needs an explicit distance cap now that the ledger is
    team-shared. Fails open (no cap applied) if the ally core isn't known yet,
    matching _allowOreSite's own convention for the same situation.
    """
    if owner_team == ct.get_team():
        return True
    from main import STEAL_ENEMY_HARVESTERS, STEAL_HARVESTER_MAX_CHEBYSHEV

    if not STEAL_ENEMY_HARVESTERS:
        return False
    if STEAL_HARVESTER_MAX_CHEBYSHEV is not None and STEAL_HARVESTER_MAX_CHEBYSHEV > 0:
        core = bot.corePos
        if core is not None and chebyshevToCore(tile, core) > STEAL_HARVESTER_MAX_CHEBYSHEV:
            return False
    return True


def pickMaintainOre(bot, ct: Controller) -> Position | None:
    """Prefer sticky site, else a reported orphan, else nearest harvester/block.

    Harvester candidates already claimed by another ally builder
    (allyBuilderNearOre) are skipped, same idea as pickEmptyOre's
    allyBuilderOnOre check for empty ore — without it, several builders can
    independently score the same pre-belt harvester as "best" and converge
    on it with no coordination (relayToCore/moveToward don't treat ally
    builders as obstacles), then keep re-picking the identical contested
    tile after every RELAY_STUCK_TURNS abort since abortRelay never touches
    bot.oreSite. Observed directly on glacierkeep, whose dense 3x3 central
    ore cluster makes the convergence especially likely.
    """
    from logistics import _pickOrphanRelay

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

    # Known ally belt tiles, snapshotted once per call — a cheap proximity
    # proxy (not a connectivity proof; that's logistics._relayReachesCore,
    # run once relayToCore actually plans the path) so scoring below can
    # prefer a candidate near an already-built line over one merely closer
    # to the core in straight-line terms. See score()'s connect_d.
    me = ct.get_team()
    ally_belts = [
        Position(x, y)
        for (x, y), cell in bot.hivemind.local_map.items()
        if (ent := cell.get("entity")) is not None
        and ent.get("team") == me
        and ent.get("type") == EntityType.CONVEYOR
    ]

    def score(ore: Position) -> tuple:
        # manhattanToCore, not raw manhattan to the core's anchor tile — see
        # _oreConvenienceKey's docstring for why raw anchor distance is biased
        # per team on a mirrored map.
        spread = _spreadScore(bot, ore)
        if core is not None:
            connect_d = manhattanToCore(ore, core)
            # A candidate near an existing, already-core-connected belt line is
            # about as cheap to reach as walking to that belt — RELAY_MERGE_*
            # (logistics.pickRelayPath) finishes the rest in a few tiles
            # instead of a fresh line all the way back to core. Without this,
            # a harvester (most visibly an enemy one worth stealing) that
            # merely reads "closer to the core" in straight-line terms but
            # sits relay-isolated deep in unclaimed territory could get
            # picked over one that's actually a short merge away, costing a
            # full-length relay's worth of Ti to reach the same result.
            if ally_belts:
                connect_d = min(connect_d, min(manhattan(ore, b) for b in ally_belts))
            return (connect_d + spread, manhattan(ore, pos))
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
            if (
                ct.get_entity_type(bid) == EntityType.HARVESTER
                and _harvesterOwnerAllowed(bot, ct, ct.get_team(bid), ore)
                and not allyBuilderNearOre(ct, ore)
            ):
                consider(ore)
            elif _shouldClearAllyOreBlock(ct, ore, bot=bot):
                consider(ore)
        else:
            entry = bot.hivemind.local_map.get((ore.x, ore.y), {})
            ent = entry.get("entity")
            is_harvester = ent and ent.get("type") == EntityType.HARVESTER
            if is_harvester and allyBuilderNearOre(ct, ore):
                continue  # ally already working this harvester
            if is_harvester and _harvesterOwnerAllowed(bot, ct, ent.get("team"), ore):
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
        if (
            ct.get_entity_type(bid) == EntityType.HARVESTER
            and _harvesterOwnerAllowed(bot, ct, ct.get_team(bid), tile)
            and not allyBuilderNearOre(ct, tile)
        ):
            consider(tile)
        elif _shouldClearAllyOreBlock(ct, tile, bot=bot):
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
# Default role tasks
# ------------------------------------------------------------------


def buildHarvester(bot, ct: Controller) -> bool:
    """Build on empty ore in vision / known map. Returns True if acted."""
    if bot.inRelay:
        return False
    # A relay attempt can abort on its very first relayToCore call (no route
    # found at all) without bot.inRelay having protected the harvester for
    # more than one turn — abortRelay/clearRelay never touch bot.oreSite, so
    # it's left pointing at the harvester we already built. Below,
    # _stickyEmptyTarget would (correctly, for its own purpose of finding
    # *empty* ore) see a tile with a building on it, conclude the sticky
    # target is invalid, and clear bot.oreSite — silently abandoning an
    # already-built harvester instead of giving maintainHarvester a chance to
    # retry relaying it next. Observed directly: a builder building harvester
    # after harvester, never finishing a relay until the 3rd or 4th, each
    # earlier one costing 20 Ti for nothing. Bail out here instead, before
    # touching oreSite, so runDefault's next step (maintainHarvester ->
    # pickMaintainOre's sticky-oreSite branch) sees it still set and retries.
    if bot.oreSite is not None and ct.is_in_vision(bot.oreSite):
        bid = ct.get_tile_building_id(bot.oreSite)
        if (
            bid is not None
            and ct.get_entity_type(bid) == EntityType.HARVESTER
            and ct.get_team(bid) == ct.get_team()
        ):
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
        # team != mine here means an enemy-owned harvester — relaying it anyway
        # is the steal mechanic (main.STEAL_ENEMY_HARVESTERS); see
        # _harvesterOwnerAllowed. Disallowed → leave it alone, same as any
        # other tile this function has nothing to do on.
        if not _harvesterOwnerAllowed(bot, ct, team, tile):
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
            if not _shouldClearAllyOreBlock(ct, tile, bot=bot):
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


def _repairNearbyBeltSegment(bot, ct: Controller, harvesterTile: Position) -> bool:
    """Heal a damaged ally segment at/around harvesterTile if reachable this turn.

    Checks the harvester itself and its cardinally-adjacent ally belts
    (`beltNeighbors`) for actual damage (`get_hp` &lt; `get_max_hp`) before doing
    anything — this is a repair action, not a search, so it never walks a
    builder toward a segment that turns out to be fine. Heals immediately if
    already adjacent to a damaged one, else takes one step toward the nearest.
    """
    from logistics import _repairBeltIfDamaged

    local_map = bot.hivemind.local_map
    me = ct.get_team()
    targets = [harvesterTile] + [
        n for n, ent in beltNeighbors(ct, harvesterTile, local_map=local_map)
        if ent.get("team") == me
    ]
    damaged: list[Position] = []
    for t in targets:
        if not ct.is_in_vision(t):
            continue
        bid = ct.get_tile_building_id(t)
        if bid is None or ct.get_team(bid) != me:
            continue
        if ct.get_hp(bid) >= ct.get_max_hp(bid):
            continue
        damaged.append(t)
    if not damaged:
        return False
    pos = ct.get_position()
    for t in damaged:
        if isAdjacent(pos, t) and _repairBeltIfDamaged(ct, t):
            return True
    return moveToward(bot, ct, damaged[0])


def finishNearbyHarvester(bot, ct: Controller) -> bool:
    """Opportunistically start or repair a relay for an un/under-relayed
    harvester near this bot's own tile.

    Twin of defence.finishNearbyBelt for harvesters instead of dangling
    belt ends: no claim, no reporting, any builder that happens to be near
    just acts. Exists because the HiveMind ledger is per-bot, not team-shared
    (see CONTEXT.md) — a builder otherwise only ever works a harvester its own
    vision has touched, and only once its own sticky claim frees up. This lets
    ANY passing builder within a small radius kick off (or top up) a relay the
    moment it notices one, instead of waiting for the formal economy loop to
    happen to pick that exact tile.

    `harvesterClaimedByAlly` False (no ally belt touches it yet) → commit
    fresh, exactly like checkOre's own harvester branch — this is the core
    fix. True (someone's relay, in progress or complete) → don't take it over;
    just try `_repairNearbyBeltSegment` instead. Also skips (no repair
    attempt either, just move on) a harvester an ally builder is already
    standing on/adjacent to but hasn't belted yet (allyBuilderNearOre) —
    without this, a passing builder could commit to the exact harvester
    another ally is mid-approach to, the two converging with no coordination.
    """
    if bot.corePos is None or bot.inRelay or getattr(bot, "recheckingOre", False):
        return False

    from main import NEARBY_HARVESTER_SELF_CHEBYSHEV

    if NEARBY_HARVESTER_SELF_CHEBYSHEV is None or NEARBY_HARVESTER_SELF_CHEBYSHEV <= 0:
        return False

    core = bot.corePos
    pos = ct.get_position()
    skipped = getattr(bot, "skippedOres", set())
    local_map = bot.hivemind.local_map
    candidates: list[tuple[int, int, Position]] = []
    for (x, y), cell in local_map.items():
        ent = cell.get("entity")
        if not ent or ent.get("type") != EntityType.HARVESTER:
            continue
        if (x, y) in skipped:
            continue
        tile = Position(x, y)
        if not _harvesterOwnerAllowed(bot, ct, ent.get("team"), tile):
            continue
        if max(abs(tile.x - pos.x), abs(tile.y - pos.y)) > NEARBY_HARVESTER_SELF_CHEBYSHEV:
            continue
        if not _allowOreSite(bot, tile):
            continue
        candidates.append((chebyshevToCore(tile, core), manhattan(pos, tile), tile))
    candidates.sort()

    for _d, _m, tile in candidates:
        if harvesterClaimedByAlly(ct, tile, local_map=local_map):
            if _repairNearbyBeltSegment(bot, ct, tile):
                return True
            continue
        if allyBuilderNearOre(ct, tile):
            continue  # an ally is already mid-approach — don't converge on it
        bot.oreSite = tile
        return checkOre(bot, ct, tile)
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


def _pickVerifyHarvester(bot, ct: Controller, core: Position) -> Position | None:
    """Nearest-core ally harvester this builder hasn't re-verified lately.

    Ledger-sourced on purpose: the whole point is to reach harvesters the
    builder can no longer see. Same `(chebyshevToCore, manhattan-to-self)`
    ordering and same `_allowOreSite`/`skippedOres` gates every other picker in
    this file uses, so a guardian's leash and a builder's own give-ups keep
    working here for free.
    """
    from main import HARVESTER_VERIFY_INTERVAL_TURNS

    me = ct.get_team()
    pos = ct.get_position()
    round_n = ct.get_current_round()
    skipped = getattr(bot, "skippedOres", set())
    verified = getattr(bot, "verifiedHarvesters", {})
    best: Position | None = None
    best_key = None
    for (x, y), cell in bot.hivemind.local_map.items():
        ent = cell.get("entity")
        if not ent or ent.get("team") != me:
            continue
        if ent.get("type") != EntityType.HARVESTER:
            continue
        if (x, y) in skipped:
            continue
        if round_n - verified.get((x, y), -(10**9)) < HARVESTER_VERIFY_INTERVAL_TURNS:
            continue
        tile = Position(x, y)
        if not _allowOreSite(bot, tile):
            continue
        # Someone is already standing on it working — leave it to them.
        if allyBuilderNearOre(ct, tile):
            continue
        key = (chebyshevToCore(tile, core), manhattan(tile, pos))
        if best_key is None or key < best_key:
            best_key = key
            best = tile
    return best


def verifyHomeHarvester(bot, ct: Controller) -> bool:
    """Re-walk an ally harvester's belt line to prove it still feeds the core.

    Exists because nothing else ever asks the question. `pickMaintainOre`
    already ranks ally harvesters nearest-core-first, so a cut home harvester
    is usually a builder's *top* candidate — but `relayToCore` then declares it
    complete without looking, because `beltFacingOk`'s ledger fallback accepts
    a remembered facing for up to `RELAY_LEDGER_TRUST_TURNS`, and a belt
    destroyed out of vision lingers in `local_map` as a ghost. The builder is
    told the line is fine, returns False, and the chain drops it all the way to
    `exploreFrontier` — a nearest-unknown walk that reads as aimless circling
    mid-map, repeated identically every turn.

    The fix is `bot.relayVerify`, which turns that fallback off for this pass
    only: the watermark can then advance only over tiles actually seen, so the
    builder walks its own line home and lays a belt wherever a real gap is.
    An intact line costs one traversal (and tops up damaged belts on the way
    via `relayToCore`'s own `_repairBeltIfDamaged`); a cut one gets repaired.

    Sits below every real-work step in `runDefault`, so it only ever fires on a
    builder that would otherwise be wandering, and never displaces active
    economy. Returns `bot.inRelay`, matching `checkOre`'s convention: a line
    proven intact within this same turn reports False so the chain continues
    rather than burning the turn on nothing.
    """
    if bot.inRelay or getattr(bot, "recheckingOre", False):
        return False
    core = bot.corePos
    if core is None:
        return False

    from main import HARVESTER_VERIFY_INTERVAL_TURNS

    if HARVESTER_VERIFY_INTERVAL_TURNS <= 0:
        return False
    round_n = ct.get_current_round()
    last = getattr(bot, "lastHarvesterVerifyRound", -(10**9))
    if round_n - last < HARVESTER_VERIFY_INTERVAL_TURNS:
        return False

    tile = _pickVerifyHarvester(bot, ct, core)
    if tile is None:
        return False

    bot.lastHarvesterVerifyRound = round_n
    if not hasattr(bot, "verifiedHarvesters"):
        bot.verifiedHarvesters = {}
    bot.verifiedHarvesters[(tile.x, tile.y)] = round_n
    bot.oreSite = tile
    bot.relayOrigin = tile
    bot.relayPath = None
    bot.relayBuiltTo = -1
    bot.inRelay = True
    bot.relayVerify = True
    relayToCore(bot, ct)
    return bot.inRelay


ORE_RECHECK_CLEAR_INTERVAL = 50


def _safeCoreStandTile(bot, ct: Controller, core: Position) -> Position | None:
    """Walkable tile cardinally adjacent to the core footprint, no gunner covering it.

    Healing is done from *beside* the core, and the 2x2 footprint offers eight such
    tiles — so when a gunner covers the approach there is usually another side that
    works. Picking one is strictly better than walking at the footprint itself.

    Returns None when every side is covered, which is a deliberate refusal: healing
    restores 4 HP/turn, and standing in a gunner's line to do it feeds a 40 HP
    builder to 10 dmg/turn. Losing a 30 Ti builder to heal 16 HP is not a defence.

    Also skips a side another guardian is already standing on (`allyOccupied`):
    without this, both guardians independently converge on the same "nearest"
    tile when reacting to the same attack, and the second one just re-targets
    an occupied tile every turn forever instead of taking the next-best side.
    """
    from offense import _enemyGunners, _tileUnderGunnerFire

    gunners = _enemyGunners(ct)
    pos = ct.get_position()
    footprint = coreFootprint(core)
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
            if cand != pos and allyOccupied(ct, cand):
                # Another guardian is already standing here — targeting it too
                # just means walking up to an occupied tile and getting
                # refused by can_move every turn. Prefer the next-best side.
                continue
            key = (manhattan(pos, cand), cand.x, cand.y)
            if best_key is None or key < best_key:
                best_key = key
                best = cand
    return best


# Stop healing 100 HP short of GameConstants.CORE_MAX_HP (500) — see defendHome.
_CORE_HEAL_STOP_HP = GameConstants.CORE_MAX_HP - 100


def defendHome(bot, ct: Controller, stop_hp: int = _CORE_HEAL_STOP_HP) -> bool:
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

    `stop_hp` defaults to `_CORE_HEAL_STOP_HP` (400 of 500) everywhere this is
    called as an urgent response, so chip damage during a fight doesn't
    re-trigger healing every turn at the expense of belt repair/economy below
    it. `runGuardian` also calls this a second time, last in its chain and
    gated `not underAttack`, with `stop_hp=GameConstants.CORE_MAX_HP` — once
    there's genuinely nothing else to do, top the core the rest of the way up
    instead of leaving it parked 100 HP short forever.
    """
    if bot.inRelay or getattr(bot, "recheckingOre", False):
        return False
    if not nearCore(bot, ct):
        return False

    if bot.corePos is None or ct.get_action_cooldown() != 0:
        return False
    for tile in coreFootprint(bot.corePos):
        bid = None
        if ct.is_in_vision(tile):
            bid = ct.get_tile_building_id(tile)
        if bid is None or ct.get_entity_type(bid) != EntityType.CORE:
            continue
        if ct.get_team(bid) != ct.get_team():
            continue
        if ct.get_hp(bid) >= stop_hp:
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


def _pickRecheckOre(bot, ct: Controller) -> Position | None:
    """Prefer a reported orphan, then empty, then ally-blocked, then harvester."""
    from logistics import _pickOrphanRelay

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
        # manhattanToCore, not raw manhattan to the core's anchor tile — see
        # _oreConvenienceKey's docstring for why raw anchor distance is biased
        # per team on a mirrored map.
        if core is not None:
            return (manhattanToCore(ore, core), manhattan(ore, pos))
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
        elif (
            ct.get_entity_type(bid) == EntityType.HARVESTER
            and _harvesterOwnerAllowed(bot, ct, ct.get_team(bid), tile)
            and not allyBuilderNearOre(ct, tile)
        ):
            consider(tile, kind="harvester")
        elif _shouldClearAllyOreBlock(ct, tile, bot=bot):
            consider(tile, kind="blocked")

    for ore in knownOreTiles(bot):
        key_xy = (ore.x, ore.y)
        if key_xy in skipped:
            continue
        if ct.is_in_vision(ore):
            bid = ct.get_tile_building_id(ore)
            if bid is None:
                consider(ore, kind="empty")
            elif (
                ct.get_entity_type(bid) == EntityType.HARVESTER
                and _harvesterOwnerAllowed(bot, ct, ct.get_team(bid), ore)
                and not allyBuilderNearOre(ct, ore)
            ):
                consider(ore, kind="harvester")
            elif _shouldClearAllyOreBlock(ct, ore, bot=bot):
                consider(ore, kind="blocked")
            continue
        entry = bot.hivemind.local_map.get(key_xy, {})
        ent = entry.get("entity")
        if ent and _oreLedgerStale(bot, entry):
            # See pickEmptyOre's identical treatment / ORE_BLOCK_MEMORY_TURNS —
            # this is the recheck path, so leaving stale memory permanently
            # disqualifying here would be the last resort that ever rescues it.
            ent = None
        if (
            ent
            and ent.get("type") == EntityType.HARVESTER
            and _harvesterOwnerAllowed(bot, ct, ent.get("team"), ore)
            and not allyBuilderNearOre(ct, ore)
        ):
            consider(ore, kind="harvester")
        elif not ent:
            consider(ore, kind="empty")

    if empty_best is not None:
        return empty_best
    if blocked_best is not None:
        return blocked_best
    return harv_best


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
