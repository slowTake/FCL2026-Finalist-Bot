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

# Half-width of the vision-ingest box _update_local_vision fills local_map from
# (dx,dy in -6..6, Chebyshev <=6). _eliminateVisibleCandidates clamps to it so it
# only crosses off candidates the sighting scan could actually have examined.
_VISION_INGEST_RADIUS = 6


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


def _mirrorCandidates(w: int, h: int, ax: int, ay: int) -> list[Position]:
    """The tiles the enemy core could occupy on a symmetric map we can't
    fingerprint, best-first. Given our own anchor `(ax, ay)`, the enemy core is
    one of exactly three reflections — verified against all 44 maps in
    features.KNOWN_MAPS, the real core is always in this set:

      point-mirror  `(w-2-ax, h-2-ay)`  — both axes flip
      y-flip        `(ax,     h-2-ay)`  — vertical  reflection
      x-flip        `(w-2-ax, ay)`      — horizontal reflection

    Ordered point → y-flip → x-flip: point is right on 36/44 known maps
    (including every map in LIVE_MAP_POOL, so the current ladder never leaves the
    first candidate), and of the 8 counterexamples 5 are y-flips (meander, pinch,
    strait, sweden) and 3 are x-flips (bridge, longship, vase). We cannot tell
    which type a map is before seeing the real core, so callers walk the list and
    `_eliminateVisibleCandidates` crosses off ones proven empty by vision.

    Deduped, and our own anchor is never a candidate — on an axis-centred core two
    reflections coincide (e.g. a core on the vertical mid-line has point == y-flip).
    """
    raw = [(w - 2 - ax, h - 2 - ay), (ax, h - 2 - ay), (w - 2 - ax, ay)]
    out: list[Position] = []
    seen = {(ax, ay)}
    for c in raw:
        if c not in seen:
            seen.add(c)
            out.append(Position(c[0], c[1]))
    return out


def _ownAnchor(bot, ct: Controller):
    """Our own core's top-left anchor, or None if not yet resolved. Prefer the
    packed-cores slot (every unit reads it); fall back to bot.corePos when that
    slot is still UNKNOWN — this matters on the core's own turn 0, when it spawns
    the first raider before its buffered PACKED_CORES write has landed (see the
    detailed note this replaced, still true: without the fallback the first raider,
    and the launcher it plants, are never created on an unfingerprinted map)."""
    ally, _ = unpack_cores(ct.read_store(store.PACKED_CORES))
    if ally[0] != CoreState.UNKNOWN:
        return ally[1], ally[2]
    core = getattr(bot, "corePos", None)
    if core is not None:
        return core.x, core.y
    return None


def _enemyCoreCandidates(bot, ct: Controller) -> list[Position]:
    """All mirror candidates for the enemy core, best-first, or [] if our own
    anchor is unknown. Not filtered by eliminations — see enemyCoreCandidatesAlive."""
    anchor = _ownAnchor(bot, ct)
    if anchor is None:
        return []
    return _mirrorCandidates(ct.get_map_width(), ct.get_map_height(), *anchor)


def _eliminateVisibleCandidates(bot, ct: Controller) -> None:
    """Cross off any mirror candidate whose full 2x2 footprint this unit can see —
    the sighting scan just ran this round and found no enemy core, so a fully
    visible footprint is confirmed empty. Permanent (cores never move), so
    `bot._deadMirror` only grows, to at most 3. Candidate footprints are always in
    bounds (anchor <= w-2 ⇒ x+1 <= w-1), so is_in_vision is never asked off-map.

    Per-unit, not shared: only the raiders actually visit candidates, and every
    other reader is fine with a one-round-stale guess — sharing this would cost
    scarce store bits for no real gain. The cost is that raider 2 may re-clear what
    raider 1 already cleared; pressCore splits their probe order to cut that."""
    dead = getattr(bot, "_deadMirror", None)
    if dead is None:
        return
    # Only trust tiles the sighting scan could actually have examined: _seenEnemyCore
    # reads local_map, which _update_local_vision fills from a Chebyshev-<=6 box
    # around this unit's own position. A tile can read is_in_vision True yet sit
    # outside that box for a 2x2 unit (is_in_vision may key off any footprint tile,
    # get_position returns the anchor) — trusting it there would let the core
    # *permanently* cross off a true candidate the scan never ingested. Clamp to the
    # ingest box so "visible + not seen by the scan ⇒ empty" is sound.
    px, py = ct.get_position().x, ct.get_position().y
    for c in _enemyCoreCandidates(bot, ct):
        if (c.x, c.y) in dead:
            continue
        tiles = ((c.x, c.y), (c.x + 1, c.y), (c.x, c.y + 1), (c.x + 1, c.y + 1))
        if all(abs(x - px) <= _VISION_INGEST_RADIUS
               and abs(y - py) <= _VISION_INGEST_RADIUS
               and ct.is_in_vision(Position(x, y))
               for x, y in tiles):
            dead.add((c.x, c.y))


def enemyCoreVerified(bot, ct: Controller) -> bool:
    """True when the enemy core position is ground truth (tier 1 sighting) or the
    fingerprint (tier 2) — i.e. NOT a mirror guess. Cheap: no map scan. Gates the
    two places that spend titanium aimed at the enemy core (publishTarget,
    buildLocalSentinel): fine to *walk* toward a guess, never to build turrets at
    one. Always true on any fingerprinted map, so ladder behaviour is unchanged."""
    if getattr(bot, "_seenEnemyCore", None) is not None:
        return True
    if getattr(bot.hivemind, "enemy_core_pos", None) is not None:
        return True
    _, enemy = unpack_cores(ct.read_store(store.PACKED_CORES))
    return enemy[0] != CoreState.UNKNOWN


def enemyCoreCandidatesAlive(bot, ct: Controller) -> list[Position]:
    """Mirror candidates not yet disproven by vision, best-first. Empty once the
    core is verified (tier 1/2) — callers use enemyCorePos directly then. Lets the
    two opening raiders probe *different* candidates so the pair splits the mirror
    ambiguity instead of both walking to the same wrong point-mirror guess."""
    if enemyCoreVerified(bot, ct):
        return []
    dead = getattr(bot, "_deadMirror", ())
    return [c for c in _enemyCoreCandidates(bot, ct) if (c.x, c.y) not in dead]


def enemyCorePos(bot, ct: Controller) -> Position | None:
    """The enemy core, for any unit. Three tiers, most-authoritative first:

    1. **What we've actually seen.** Ground truth — overrides a wrong or absent
       fingerprint, so a brand-new or mis-fingerprinted map self-corrects the
       moment a raider lays eyes on the core. Cached (`bot._seenEnemyCore`): it
       never moves, and the ledger propagates it to everyone.
    2. **The fingerprint** (`hm.enemy_core_pos` on the core, else the packed-cores
       slot every unit reads). Correct on every map in features.KNOWN_MAPS.
    3. **Mirror fallback.** On an unfingerprinted symmetric map the enemy core is
       one of three reflections of ours (`_mirrorCandidates`): point, y-flip, or
       x-flip. We return the best *not-yet-disproven* candidate; a raider's press
       walk clears each empty guess (`_eliminateVisibleCandidates`) and advances to
       the next, until tier 1 locks the real core. The old code assumed pure point
       symmetry and was wrong on 8 of 44 known maps (bridge, longship, meander,
       pinch, strait, sweden, twins, vase) — invisible today because LIVE_MAP_POOL
       is all point-symmetric and fingerprinted, but a live hazard for a finals map
       we can't fingerprint.

    Note the tiny-map edge: on a small unfingerprinted map a candidate can land
    within a launcher's apron of our own core (a pre-existing quirk of the point
    mirror, not widened here — still exactly one guess returned at a time).
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
            else:
                # No sighting this round: advance mirror eliminations while the
                # scan's fresh negative licenses "visible footprint ⇒ empty".
                # Gated by the same once-per-round guard, so it never re-runs.
                _eliminateVisibleCandidates(bot, ct)
    if seen is not None:
        return seen

    pos = getattr(bot.hivemind, "enemy_core_pos", None)
    if pos is not None:
        return pos

    _, enemy = unpack_cores(ct.read_store(store.PACKED_CORES))
    if enemy[0] != CoreState.UNKNOWN:
        return Position(enemy[1], enemy[2])

    # Tier 3: mirror fallback. Best live (not-yet-disproven) candidate, else — if
    # every candidate has somehow been crossed off — fall back to the raw best
    # rather than None: a None here would flip _raidReady false and un-designate
    # raiders mid-match. A fingerprinted map never reaches this (tier 2 returns).
    dead = getattr(bot, "_deadMirror", ())
    cands = _enemyCoreCandidates(bot, ct)
    if not cands:
        return None
    for c in cands:
        if (c.x, c.y) not in dead:
            return c
    return cands[0]


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
    neighbours — one whose sentinel reaches the enemy core. `primarySite`, when
    given, is a pad to skip: a just-failed published site the core's ledger has
    not yet learned was claimed. With none, any bearing neighbour qualifies — used
    to densify the ring right where the raider stands rather than walk back for the
    next published target.

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
    # Never publish a sentinel emplacement aimed at a mirror guess — an unverified
    # core is fine to besiege on foot, but building the whole ring at a phantom
    # footprint burns titanium re-densifying turrets that self-destruct empty.
    if not enemyCoreVerified(bot, ct):
        return
    choice = chooseEmplacement(bot, ct, enemyCore)
    if choice is None:
        return
    pos, facing = choice
    store.put(ct, store.RAID_TARGET, packTarget(pos, facing))


def readTarget(ct: Controller):
    return unpackTarget(ct.read_store(store.RAID_TARGET))
