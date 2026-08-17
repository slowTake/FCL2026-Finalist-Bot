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

import emplace
import pathfindingHelpers as ph
import store
from hivemind import CoreState, unpack_cores

# Packing order matters only in that pack and unpack agree — and they do by
# construction now, both indexing the one canonical facing order. See the
# wire-format warning on emplace.DIRS before touching it.
_DIRS = emplace.DIRS
_DIR_INDEX = {d: i for i, d in enumerate(_DIRS)}


def packTarget(pos: Position, facing: Direction) -> int:
    """valid(1) | dir(3) | x(7) | y(7). Map is 30x30 at most, so 7 bits is ample.

    Returns 0 (nothing published) for a coordinate that will not fit. Shifting a
    negative or oversized x silently overflows into the direction field and
    decodes back as a plausible tile somewhere else entirely — a wrong answer is
    far worse here than no answer.
    """
    if not (0 <= pos.x < 128 and 0 <= pos.y < 128):
        return 0
    return (1 << 17) | (_DIR_INDEX[facing] << 14) | (pos.x << 7) | pos.y


def unpackTarget(val: int):
    """Returns (Position, Direction) or None if nothing has been published."""
    if not (val >> 17) & 1:
        return None
    if val >> 18:
        return None  # bits we never write — not ours, do not guess at it
    return Position((val >> 7) & 0x7F, val & 0x7F), _DIRS[(val >> 14) & 0x7]


def _seenEnemyCore(bot, ct: Controller) -> Position | None:
    """The enemy core's anchor if any unit has seen its full 2x2, from the shared
    ledger.

    The ledger records the core at each of its footprint tiles, so a *partial*
    sighting has no well-defined anchor — `min(x), min(y)` taken independently over
    two or three tiles lands on a corner that isn't the top-left one. So require a
    complete 2x2 block: `(x, y)` is the anchor only when all four of its footprint
    tiles read as an enemy core. A builder sees the whole core at once from within
    vision r²=36, and it broadcasts at top priority, so this resolves within a turn
    of the first raider reaching it; until then we fall through to the fingerprint.
    """
    me = ct.get_team()
    core_tiles = {
        (x, y)
        for (x, y), cell in bot.hivemind.local_map.items()
        if (ent := cell.get("entity")) is not None
        and ent.get("type") == EntityType.CORE
        and ent.get("team") != me
    }
    for (x, y) in core_tiles:
        if {(x, y), (x + 1, y), (x, y + 1), (x + 1, y + 1)} <= core_tiles:
            return Position(x, y)
    return None


def enemyCorePos(bot, ct: Controller) -> Position | None:
    """The enemy core, for any unit. Three tiers, most-authoritative first:

    1. **What we've actually seen.** Ground truth — overrides a wrong or absent
       fingerprint, so a brand-new or mis-fingerprinted map self-corrects the
       moment a raider lays eyes on the core. Cached (`bot._seenEnemyCore`): it
       never moves, and the ledger propagates it to everyone.
    2. **The fingerprint** (`hm.enemy_core_pos` on the core, else the packed-cores
       slot every unit reads). Correct on every map in features.KNOWN_MAPS.
    3. **Mirror fallback.** Maps are point-symmetric, so on an unfingerprinted map
       the enemy core is the reflection of ours (always in packed-cores):
       `((w-2)-x, (h-2)-y)`. Gives raiders/launcher a target from turn 0 even on
       a map we've never seen. Corrected by tier 1 once the real core is sighted.
    """
    seen = getattr(bot, "_seenEnemyCore", None)
    if seen is None:
        # At most one sighting scan per unit per round. The positive result is
        # cached forever (the core never moves), but the *negative* one has to be
        # re-derived as vision grows — and the scan walks the whole of local_map,
        # which only ever gets bigger. Uncapped, every unit that never sees the
        # enemy core (every eco builder, all match) paid that walk on each call,
        # and a raider calls this several times a turn via pressCore /
        # buildLocalSentinel / fallbackEmplacement. Builders sit at p90 9.4ms
        # against a 10ms limit, so this is not a hypothetical cost.
        rnd = ct.get_current_round()
        if getattr(bot, "_seenEnemyCoreRound", -1) != rnd:
            bot._seenEnemyCoreRound = rnd
            seen = _seenEnemyCore(bot, ct)
            if seen is not None:
                bot._seenEnemyCore = seen
    if seen is not None:
        return seen

    pos = getattr(bot.hivemind, "enemy_core_pos", None)
    if pos is not None:
        return pos

    ally, enemy = unpack_cores(ct.read_store(store.PACKED_CORES))
    if enemy[0] != CoreState.UNKNOWN:
        return Position(enemy[1], enemy[2])

    # Mirror fallback needs our own core. Prefer the packed-cores slot (every unit
    # reads it), but fall back to bot.corePos when that slot is still UNKNOWN. This
    # matters on exactly one turn that matters a lot: the core's own turn 0, when
    # it spawns the first raider. The core's PACKED_CORES write is buffered a round
    # (write_store), so the slot reads UNKNOWN on turn 0 — yet _runCore has already
    # set bot.corePos from ct.get_position() this same turn. Without this fallback
    # enemyCorePos returns None at that spawn, _raidReady is False, ROLE_RAIDER is
    # never assigned, and on an *unfingerprinted* map (tier 2 absent) the first
    # raider — and therefore the launcher it plants — is silently never created.
    # A fingerprinted map is unaffected: tier 2 returns above before we get here.
    ax = ay = None
    if ally[0] != CoreState.UNKNOWN:
        ax, ay = ally[1], ally[2]
    else:
        core = getattr(bot, "corePos", None)
        if core is not None:
            ax, ay = core.x, core.y
    if ax is not None:
        w, h = ct.get_map_width(), ct.get_map_height()
        return Position(w - 2 - ax, h - 2 - ay)
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


def _candidates(ct: Controller, hm, enemyCore: Position):
    """Every tile that could hold a sentinel aimed at the enemy core, best first.

    Built by inverting the attack pattern: if facing `d` hits everything at
    `offset` from the turret, then a turret at `aim - offset` facing `d` hits
    `aim`. Eight engine calls total, and every candidate is in range by
    construction — no verification pass, and nothing to cut off early.
    """
    footprint = ph.coreFootprint(enemyCore)
    enemySet = {(p.x, p.y) for p in footprint}

    reach = emplace.reachOffsets(ct)  # eight engine calls, hoisted out of the loops
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
    with the facing it has to be built pointing. None if there is no such tile.

    Deliberately does no threat vetting. It used to filter against the cost
    layer's threat set, which could never work twice over: that set is only
    populated as a side effect of path planning, which the core never does, and
    it is built from the *core's own* vision while every candidate here sits
    beside the enemy core. Two disjoint places on the map. Vetting an
    emplacement for enemy fire is the raider's job — it is the only unit that
    can see the ground it is about to build on (see docs/observations.md).
    """
    for e, d, _aim, _standoff in _candidates(ct, bot.hivemind, enemyCore):
        return e, d
    return None


def fallbackEmplacement(bot, ct: Controller, primarySite: Position | None = None):
    """A buildable sentinel emplacement among the raider's own orthogonal
    neighbours — one whose sentinel reaches the enemy core. Two callers:

    * `buildSentinel` passes the `primarySite` it just failed on, to skip it —
      typically the core's ledger hasn't yet learned that site was claimed.
    * `buildLocalSentinel` passes nothing, to lay another sentinel right where
      the raider already stands, densifying the ring around the core instead of
      walking back for the next published target.

    Only the raider's own neighbours either way: a fresh board search is the
    core's job, not a stuck builder's. Standoff is not preferred here — closer is
    fine, getting a sentinel down beats waiting on the core to republish.

    Threat filtering *is* done here, unlike chooseEmplacement: this runs on the
    raider, which can see the ground beside the enemy core, so it drops any pad a
    visible enemy turret already bears on at its current facing — no free shot on
    the sentinel the instant it's built. chooseEmplacement (core-side, no vision)
    still can't, so buildSentinel re-vets the published target the same way.
    """
    enemyCore = enemyCorePos(bot, ct)
    if enemyCore is None:
        return None

    hm = bot.hivemind
    footprint = ph.coreFootprint(enemyCore)
    enemySet = {(p.x, p.y) for p in footprint}

    # Policy — which pads are even eligible — is ours; the bearing/LoS/buildable
    # scan over them is emplace's. Never emplace where a visible enemy gun already
    # bears (that veto lives in sentinelPadBearing): a stationary sentinel eats
    # that shot with no rotation needed, and an enemy sentinel's line ignores the
    # belt cover a gunner's respects.
    pads = [
        pad for pad in ph.orthogonalTiles(ct.get_position())
        if not (primarySite is not None
                and (pad.x, pad.y) == (primarySite.x, primarySite.y))
        and _buildable(ct, hm, pad, enemySet)
    ]
    return emplace.sentinelPadBearing(ct, pads, footprint)


def publishTarget(bot, ct: Controller) -> None:
    """Core-side: pick an emplacement once and leave it in the store.

    Recomputed only while nothing is published. Re-picking every turn would hand
    the raider a different destination each round and it would never arrive.
    """
    if ct.read_store(store.RAID_TARGET):
        return
    enemyCore = enemyCorePos(bot, ct)
    if enemyCore is None:
        return
    choice = chooseEmplacement(bot, ct, enemyCore)
    if choice is None:
        return
    pos, facing = choice
    store.put(ct, store.RAID_TARGET, packTarget(pos, facing))


def readTarget(ct: Controller):
    return unpackTarget(ct.read_store(store.RAID_TARGET))
