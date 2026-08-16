"""Belt relay: laying/repairing the conveyor line from a harvester to the core.

Split out of the old monolithic logistics.py — ore/harvester economy and home
upkeep now live in `logistics_helpers.py`, generic movement/pathing in
`movement.py`. This module owns the relay lifecycle: the orphan-relay handoff
registry, belt-facing/connectivity proofs, and `relayToCore` itself. It builds
on `movement.py` (moveToward, core-adjacency, facing) but never imports from
`logistics_helpers.py` — nothing here needs ore-picking or economy state.
"""

from __future__ import annotations

from fcode import Controller, Direction, EntityType, Position, Team

from geometry import BELT_TYPES, coreFootprint, isAdjacent
from movement import (
    PATH_MAX_ITERS,
    _allyCorePos,
    _isEnemyCoreAdjacent,
    facingIntoCore,
    isCoreAdjacent,
    moveToward,
    travelFacing,
)
from pathing import (
    CARDINALS,
    astarFromSeeds,
    findPathAroundObstruction,
    inBounds,
    manhattan,
    nearestCardinal,
    relayPassable,
)


def clearRelay(bot) -> None:
    bot.inRelay = False
    bot.relayOrigin = None
    bot.relayPath = None
    bot.relayBuiltTo = -1
    bot.relayStuck = 0
    bot.beltFireTile = None
    bot.beltFireTurns = 0
    # Vision-only proof is a property of one verification pass, never of the
    # builder — leaking it into the next relay would make every ordinary build
    # re-walk its own corridor. See verifyHomeHarvester.
    bot.relayVerify = False
    # Set only while this relay's origin came from the hijack registry (see
    # pursueHijackTarget) — _reportAndAbort reads it to decide which side of
    # the shared registry to re-offer a stalled relay to.
    bot.hijackOrigin = False


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
# hivemind.py, not the ledger's narrower 5-bit one in vision_v2.py), a 10-bit
# report turn (MAX_TURNS=1000 fits with room to spare — no wraparound handling
# needed), and a 1-bit kind (0 = harvester orphan, 1 = hijack tap — see
# offense.tapHijackTarget/reportHijackTarget). Both kinds share this same
# 3-slot pool rather than getting a slot each: all 16 store slots are already
# allocated, and both kinds reduce to the exact same thing once picked up — a
# relayOrigin waiting for a builder to walk it home — so splitting the pool
# would only shrink both for no behavioral gain. 25 of 32 bits used before the
# kind bit, 26 after — plenty of headroom left.
_ORPHAN_TURN_BITS = 10
_ORPHAN_TURN_MASK = (1 << _ORPHAN_TURN_BITS) - 1
_ORPHAN_COORD_BITS = 7
_ORPHAN_COORD_MASK = (1 << _ORPHAN_COORD_BITS) - 1
_ORPHAN_KIND_SHIFT = _ORPHAN_COORD_BITS * 2 + _ORPHAN_TURN_BITS
_ORPHAN_OCCUPIED_BIT = 1 << (_ORPHAN_KIND_SHIFT + 1)

_ORPHAN_KIND_HARVESTER = 0
_ORPHAN_KIND_HIJACK = 1


def _orphanSlots() -> tuple[int, int, int]:
    from main import SLOT_ORPHAN_1, SLOT_ORPHAN_2, SLOT_ORPHAN_3

    return (SLOT_ORPHAN_1, SLOT_ORPHAN_2, SLOT_ORPHAN_3)


def _packOrphan(x: int, y: int, turn: int, kind: int = _ORPHAN_KIND_HARVESTER) -> int:
    return (
        _ORPHAN_OCCUPIED_BIT
        | ((kind & 1) << _ORPHAN_KIND_SHIFT)
        | ((x & _ORPHAN_COORD_MASK) << (_ORPHAN_COORD_BITS + _ORPHAN_TURN_BITS))
        | ((y & _ORPHAN_COORD_MASK) << _ORPHAN_TURN_BITS)
        | (turn & _ORPHAN_TURN_MASK)
    )


def _unpackOrphan(val: int) -> tuple[int, int, int, int] | None:
    """(x, y, reportedAtTurn, kind), or None if the slot is empty."""
    if val == 0:
        return None
    turn = val & _ORPHAN_TURN_MASK
    y = (val >> _ORPHAN_TURN_BITS) & _ORPHAN_COORD_MASK
    x = (val >> (_ORPHAN_COORD_BITS + _ORPHAN_TURN_BITS)) & _ORPHAN_COORD_MASK
    kind = (val >> _ORPHAN_KIND_SHIFT) & 1
    return x, y, turn, kind


def _readOrphanRegistry(
    ct: Controller, *, kind: int | None = None
) -> list[tuple[int, tuple[int, int, int, int]]]:
    """[(slot, (x, y, reportedAtTurn, kind)), ...] for every occupied slot.

    `kind=None` (default) returns both kinds mixed — used only by the shared
    report/dedup/evict path, which must see the whole pool regardless of kind
    to evict correctly. Every reader-side consumer (`_pickOrphanRelay`,
    `_pickHijackTarget`) passes its own kind explicitly.
    """
    out = []
    for slot in _orphanSlots():
        rec = _unpackOrphan(ct.read_store(slot))
        if rec is None:
            continue
        if kind is not None and rec[3] != kind:
            continue
        out.append((slot, rec))
    return out


def _rememberedAllyHarvester(bot, ct: Controller, tile: Position) -> bool:
    """True if `tile` is known — live or remembered — to hold an ally harvester.

    Mirrors the fog-tolerant live-vision/local_map fallback pattern
    `pickMaintainOre`/`_pickRecheckOre` already use in `logistics_helpers.py`:
    ground truth when we have it, last-remembered state otherwise. The
    reporting builder is very often *not* standing at `relayOrigin` anymore
    by the time an abort fires (it aborts at whatever tile along the route it
    got stuck on), so requiring live vision here would silently drop most
    reports — but this must not accept an arbitrary tile either, since
    `relayOrigin` can fall back to the builder's own bare position
    (`relayToCore`'s `bot.oreSite or ct.get_position()`), which is why this
    checks the *entity type*, not just presence.
    """
    from geometry import tileEntity

    ent = tileEntity(ct, tile, local_map=bot.hivemind.local_map)
    return (
        ent is not None
        and ent.get("type") == EntityType.HARVESTER
        and ent.get("team") == ct.get_team()
    )


def _reportRelayTarget(ct: Controller, tile: Position, kind: int) -> None:
    """Shared pack/dedup/evict body for reportOrphanRelay/reportHijackTarget.

    Dedups by (tile, kind) — if already registered (by us or someone else, of
    the same kind), just refreshes its turn instead of taking a second slot;
    two builders reporting the same tile in the same round write
    byte-identical values, so that collision is harmless by construction.
    Otherwise takes a free slot, or evicts whichever entry is stalest *across
    both kinds* if all three are already holding different tiles — the two
    kinds share one scarce 3-slot pool (see the record-format comment above
    `_orphanSlots`), not a slot each.
    """
    turn = ct.get_current_round() & _ORPHAN_TURN_MASK
    entries = _readOrphanRegistry(ct)
    for slot, (x, y, _t, k) in entries:
        if (x, y) == (tile.x, tile.y) and k == kind:
            ct.write_store(slot, _packOrphan(tile.x, tile.y, turn, kind))
            return
    occupied = {slot for slot, _ in entries}
    free = [s for s in _orphanSlots() if s not in occupied]
    if free:
        ct.write_store(free[0], _packOrphan(tile.x, tile.y, turn, kind))
        return
    oldest_slot, _ = min(entries, key=lambda e: e[1][2])
    ct.write_store(oldest_slot, _packOrphan(tile.x, tile.y, turn, kind))


def reportOrphanRelay(bot, ct: Controller, tile: Position) -> None:
    """Broadcast `tile` (a harvester whose relay we're giving up on) for pickup."""
    if not _rememberedAllyHarvester(bot, ct, tile):
        return  # not actually a harvester we own — nothing to report
    _reportRelayTarget(ct, tile, _ORPHAN_KIND_HARVESTER)


def reportHijackTarget(bot, ct: Controller, tile: Position) -> None:
    """Broadcast `tile` (a cleared tap point in a live enemy relay) for pickup.

    Validated against "empty, or already our own belt" rather than
    `_rememberedAllyHarvester` — there's no harvester to verify here.
    `offense.tapHijackTarget` calls `clearBeltObstacle` until the tile reads
    empty before ever calling this, so the normal case is a bare tile; "or
    already our own belt" additionally covers `_reportAndAbort` re-offering a
    hijack relay that had already placed its first belt before stalling —
    still a valid pickup point, just not a bare tile anymore. Anything else
    (enemy building, an unrelated ally building) means the tile isn't
    actually available and this silently declines to report it.
    """
    from geometry import tileEntity

    ent = tileEntity(ct, tile, local_map=bot.hivemind.local_map)
    if ent is not None and (
        ent.get("team") != ct.get_team() or ent.get("type") not in BELT_TYPES
    ):
        return
    _reportRelayTarget(ct, tile, _ORPHAN_KIND_HIJACK)


def clearOrphanRelay(ct: Controller, tile: Position) -> None:
    """Clear a registry entry for `tile`, if one exists — its relay is done.

    Kind-agnostic: matches by tile only. A tile can't hold both a harvester-
    orphan and a hijack-tap entry at once, and completion doesn't care which
    kind it started as — `markRelayComplete`'s callers already call this
    generically regardless of why the relay began.
    """
    for slot, (x, y, _t, _k) in _readOrphanRegistry(ct):
        if (x, y) == (tile.x, tile.y):
            ct.write_store(slot, 0)
            return


def _reportAndAbort(bot, ct: Controller) -> None:
    """abortRelay, but first offer the tile up for another builder to finish.

    Only called from relayToCore's builder-local / fog-dependent abort sites —
    a different builder, with different fog knowledge or a different approach
    angle, genuinely has a shot where this one didn't. Deliberately *not*
    called from the enemy-core-adjacent abort (every builder re-derives the
    same geometric verdict — reporting it just thrashes the registry) or the
    titanium-starved placement abort (every builder shares one bank and fails
    identically; it self-resolves once Ti arrives, so reporting it floods the
    registry with a tile that isn't actually stuck).

    Branches on `bot.hijackOrigin` (set by `logistics.pursueHijackTarget`) so
    a stalled hijack relay goes back to the hijack side of the registry
    instead of being silently dropped — without this, a hijack relay that
    stalls partway just vanishes, since `reportOrphanRelay` would reject its
    origin (not a harvester).
    """
    if bot.relayOrigin is not None:
        if getattr(bot, "hijackOrigin", False):
            reportHijackTarget(bot, ct, bot.relayOrigin)
        else:
            reportOrphanRelay(bot, ct, bot.relayOrigin)
    abortRelay(bot)


def _pickOrphanRelay(bot, ct: Controller) -> Position | None:
    """Nearest usable orphan from the shared registry, else None.

    Applies the same filters every other candidate in `logistics_helpers`'s
    `pickMaintainOre` / `_pickRecheckOre` already goes through —
    `_allowOreSite` (so a guardian's leash confines this to in-leash orphans
    for free, no guardian-specific code needed) and `skippedOres` (so a
    builder that already gave up on this exact tile doesn't immediately
    re-pick it here). Also evicts entries that live vision now proves stale
    (TTL-expired, or the harvester is visibly gone) — not-in-vision entries
    are trusted as-is, same fog tolerance as the rest of this file.
    """
    from logistics_helpers import _allowOreSite, allyBuilderNearOre
    from main import ORPHAN_REGISTRY_TTL_TURNS

    skipped = getattr(bot, "skippedOres", set())
    now = ct.get_current_round()
    pos = ct.get_position()
    best: Position | None = None
    best_d = 10**9
    for slot, (x, y, turn, _kind) in _readOrphanRegistry(ct, kind=_ORPHAN_KIND_HARVESTER):
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


def _pickHijackTarget(bot, ct: Controller) -> Position | None:
    """Nearest usable hijack tap from the shared registry, else None.

    Mirrors `_pickOrphanRelay`'s shape exactly (same TTL/leash/skip filters,
    same nearest-wins tie-break) — validated against "still empty or still
    our own belt" instead of "still a live ally harvester", since that's what
    a hijack tap point actually is (see `reportHijackTarget`).
    """
    from geometry import tileEntity
    from logistics_helpers import _allowOreSite, allyBuilderNearOre
    from main import ORPHAN_REGISTRY_TTL_TURNS

    skipped = getattr(bot, "skippedOres", set())
    now = ct.get_current_round()
    pos = ct.get_position()
    best: Position | None = None
    best_d = 10**9
    for slot, (x, y, turn, _kind) in _readOrphanRegistry(ct, kind=_ORPHAN_KIND_HIJACK):
        if now - turn > ORPHAN_REGISTRY_TTL_TURNS:
            ct.write_store(slot, 0)
            continue
        if (x, y) in skipped:
            continue
        tile = Position(x, y)
        if not _allowOreSite(bot, tile):
            continue
        if ct.is_in_vision(tile):
            ent = tileEntity(ct, tile, local_map=bot.hivemind.local_map)
            if ent is not None and (
                ent.get("team") != ct.get_team() or ent.get("type") not in BELT_TYPES
            ):
                ct.write_store(slot, 0)  # visibly claimed/blocked by something else
                continue
            if allyBuilderNearOre(ct, tile):
                continue  # someone's already there working it
        d = manhattan(pos, tile)
        if d < best_d:
            best_d = d
            best = tile
    return best


def relayEverComplete(ct: Controller) -> bool:
    """True after the team has finished at least one inbound relay (slot 0 bit 0).

    Masked, not `!= 0`: slot 0 also carries CORE_THREAT_BIT (see coreThreatened).
    A bare truthiness test would read the threat bit as "a relay finished" and
    flip the guardian's underAttack strictness on during the opening — exactly
    what that gate exists to prevent.
    """
    from main import RELAY_COMPLETE_BIT, SLOT_RELAY_COMPLETE

    return (ct.read_store(SLOT_RELAY_COMPLETE) & RELAY_COMPLETE_BIT) != 0


def coreThreatened(ct: Controller) -> bool:
    """True while the core can see an enemy turret/builder (slot 0 bit 1).

    Written only by the core (`setCoreThreat` from `_run_core`), read by
    guardians via `logistics_helpers.guardianLeash`. The core's vision is
    r²=36 (~6 tiles) against a builder's ~4.5, and a builder's ledger is
    personal-vision only, so this is the only way a roaming guardian learns
    home is being probed.
    """
    from main import CORE_THREAT_BIT, SLOT_RELAY_COMPLETE

    return (ct.read_store(SLOT_RELAY_COMPLETE) & CORE_THREAT_BIT) != 0


def setCoreThreat(ct: Controller, on: bool) -> None:
    """Core-only: set/clear the threat bit without disturbing the relay bit."""
    from main import CORE_THREAT_BIT, SLOT_RELAY_COMPLETE

    val = ct.read_store(SLOT_RELAY_COMPLETE)
    new = (val | CORE_THREAT_BIT) if on else (val & ~CORE_THREAT_BIT)
    if new != val:
        ct.write_store(SLOT_RELAY_COMPLETE, new)


def markRelayComplete(ct: Controller) -> None:
    """Set slot 0's relay bit, preserving the core's threat bit.

    Read-modify-write rather than the old bare `write_store(0, 1)`, which would
    clobber CORE_THREAT_BIT. Store writes are buffered a full round, so if a
    builder lands here on the same round the core writes the threat bit, one
    clobbers the other — accepted, because both are self-healing: the core
    rewrites the threat bit every turn, and this fires on every relay
    completion, not just the first. Worst case is a one-round-stale flag.
    """
    from main import RELAY_COMPLETE_BIT, SLOT_RELAY_COMPLETE

    val = ct.read_store(SLOT_RELAY_COMPLETE)
    if not (val & RELAY_COMPLETE_BIT):
        ct.write_store(SLOT_RELAY_COMPLETE, val | RELAY_COMPLETE_BIT)


# ------------------------------------------------------------------
# Belt facing / connectivity proofs
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


def _beltDirectionAt(
    bot, ct: Controller, tile: Position, *, team=None
) -> Direction | None:
    """Facing of the `team`-owned CONVEYOR at tile, vision first then ledger.

    `team` defaults to our own (`ct.get_team()`), so every existing ally-only
    call site is unaffected. Generalized to take any team — not just ours —
    for `offense.findHijackTarget`'s use via `_relayReachesCore(..., team=
    enemy_team)`: the ledger already captures `direction` for enemy
    conveyors too (`hivemind_v2.py`'s `_update_local_vision` has no team
    filter on that capture), it just had nothing reading it before.

    CONVEYOR only — deliberately excludes SPLITTER, matching
    `defence._danglingBeltEnds`'s own precedent: a splitter rotates its output
    among three directions, so "the way it points" (whichever output was
    current at sighting time, live or via a possibly-stale ledger entry) does
    not identify where the chain actually goes. A wrong guess here isn't just
    a missed merge — it's a *false positive* connectivity proof, which is
    exactly what the walk below exists to prevent.

    None for anything else — wrong owner, not a conveyor, no data, or a stale
    ledger entry (`_beltLedgerStale`). The connectivity walk below treats
    None as "chain broken here."
    """
    if team is None:
        team = ct.get_team()
    if ct.is_in_vision(tile):
        bid = ct.get_tile_building_id(tile)
        if bid is None or ct.get_team(bid) != team:
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
    if not ent or ent.get("team") != team or ent.get("type") != EntityType.CONVEYOR:
        return None
    return ent.get("direction")


def _relayReachesCore(
    bot,
    ct: Controller,
    tile: Position,
    core_pos: Position,
    *,
    team=None,
    cache: dict | None = None,
) -> bool:
    """Bounded forward-walk proving `tile`'s `team`-owned belt chain reaches `core_pos`.

    Follows each tile's own stored facing (the same ledger `direction` field
    `defence._danglingBeltEnds` already reads) toward wherever it actually points,
    capped at `main.RELAY_MERGE_MAX_HOPS`. This is the forward counterpart to
    `defence._reconnectFacing`'s backward "what should feed this gap"
    reasoning — asking "does this chain actually get there" is what a merge
    target needs proven before a new relay may end at it instead of the core:
    an unconditional "any ally belt is fine" would risk landing on a
    dead/orphaned stub or a sibling's not-yet-committed head, neither
    distinguishable from a live line at the single-tile level (see
    `OrphanRelayRegistry`'s own rationale for why that distinction matters).

    `team` defaults to ours (see `_beltDirectionAt`) — pass the enemy team and
    the enemy core to prove an *enemy* belt chain reaches *their* core instead
    (`offense.findHijackTarget`'s hijack-target detection: "is this candidate
    tile genuinely part of a working relay, not a dead stub").

    `cache` (keyed by the starting tile) is a per-call memo — the merge goal
    is evaluated on every node A* expands in `pickRelayPath`, and
    `findHijackTarget`'s candidate scan is a similar repeated-check shape, so
    without it the same candidate's chain could be re-walked many times in
    one call. Keyed on tile only, not (tile, team) — safe as long as a caller
    never reuses one cache across both an ally-team and enemy-team walk in
    the same call, which none of today's callers do.
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
        d = _beltDirectionAt(bot, ct, cur, team=team)
        if d is None or d == Direction.CENTRE:
            break
        cur = cur.add(d)
        if not inBounds(ct, cur):
            break
    if cache is not None:
        cache[key0] = result
    return result


def _enemyBeltReachesEnemyCore(
    bot, ct: Controller, tile: Position, enemy_core: Position, *, cache: dict | None = None
) -> bool:
    """True if `tile`'s enemy belt chain is genuinely part of a working relay to their core.

    Thin team-flip of `_relayReachesCore` — see that function for the walk
    itself. `offense.findHijackTarget` uses this to turn "an enemy conveyor
    exists near their core" into "this specific tile is provably part of a
    live line," the same distinction `RELAY_MERGE_ENABLED` needs for our own
    merge candidates and for the identical reason: an unconditional "any
    enemy belt is fine" risks tapping a dead/orphaned stub instead of an
    actually-flowing line.
    """
    my_team = ct.get_team()
    enemy_team = Team.B if my_team == Team.A else Team.A
    return _relayReachesCore(bot, ct, tile, enemy_core, team=enemy_team, cache=cache)


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

    The merge case is proof-based like `defence._reconnectFacing`, never a
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

    Mirrors `logistics_helpers._oreLedgerStale`/`ORE_BLOCK_MEMORY_TURNS` — same
    shape, own knob (`RELAY_LEDGER_TRUST_TURNS`), because a wrongly-trusted
    belt ledger entry can silently mark a broken relay "complete" rather than
    merely mis-judge an ore tile, so it gets its own dial rather than sharing
    one tuned for a different risk.
    """
    from main import RELAY_LEDGER_TRUST_TURNS

    trust = RELAY_LEDGER_TRUST_TURNS
    if not trust or trust <= 0:
        return True  # knob disables the fallback outright — always "stale"
    age = bot.hivemind.global_turn - entry.get("last_seen_turn", -(10**9))
    return age > trust


def beltFacingOk(
    bot,
    ct: Controller,
    pos: Position,
    needed: Direction,
    *,
    local_map: dict | None = None,
    require_vision: bool = False,
) -> bool:
    """True if pos already holds a belt facing `needed` — vision first, ledger fallback.

    A relay spanning more tiles than one unit's own vision (sqrt(20)) can cover
    at once could never confirm "already fully built" from a single vantage
    point without this — `extendRelayWatermark`/`relayToCore`'s fog-recovery
    loop would keep re-walking an already-complete corridor forever, treating
    every out-of-sight tile as unverified. The ledger already carries the
    `direction` field for CONVEYOR/SPLITTER sightings (`defence._danglingBeltEnds`
    reads the same field), so this is a read, not new state — bounded by
    `_beltLedgerStale` so a genuinely stale sighting still falls through to
    "unknown" (returns False) rather than trusting a segment that may have
    since been destroyed.

    Team-checked on both branches: an enemy belt whose facing happens to
    match `needed` must never read as "already built" for our own relay —
    that would mark the segment complete without ever laying a tile of ours.

    `require_vision=True` drops the ledger fallback entirely: an out-of-vision
    tile reads "unproven" rather than "built". `_update_local_vision` only
    deletes an `entity` when a unit *regains vision* of that exact tile, so a
    belt destroyed behind our back lingers as a ghost and the fallback above
    would mark a severed corridor complete without a single tile being looked
    at. A verification pass (`logistics_helpers.verifyHomeHarvester`) sets this
    so the builder physically re-walks the line and confirms it, tile by tile.
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
    if require_vision or local_map is None:
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


# Turns a builder may stand-and-fire the same enemy belt tile before giving up.
# A heal-racing enemy builder (HEAL_AMOUNT=4/turn) outheals our fire
# (BUILDER_BOT_ATTACK_DAMAGE=2/turn) 2:1, so without a cap this branch fires
# forever and never returns "failed" — see clearBeltObstacle's fire-on-it branch.
_BELT_FIRE_TURN_LIMIT = 11


def clearBeltObstacle(bot, ct: Controller, tile: Position) -> str:
    """Clear a wrong-facing belt or a barrier blocking the relay lane.

    Returns acted|waiting|clear|failed.
    """
    if not ct.is_in_vision(tile):
        return "waiting"
    bid = ct.get_tile_building_id(tile)
    if bid is None:
        bot.beltFireTile = None
        bot.beltFireTurns = 0
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
    #
    # Capped at _BELT_FIRE_TURN_LIMIT turns on the same tile: a heal-racing
    # enemy builder can keep this belt standing indefinitely (see the constant's
    # docstring above), and unlike offense.py's _cutInProgress — which only ever
    # has to decide whether to *start* a cut, so it can just check HP-vs-max —
    # this branch is already mid-fire every time it's reached, with no "before
    # the cut" moment to gate. A flat turn count is the simplest thing that
    # forces a "failed" out of this loop either way, so relayToCore's
    # relayStuck 3-strike counter (logistics.py's relayToCore) can see it and
    # reroute via _reportAndAbort instead of firing forever.
    key = (tile.x, tile.y)
    if getattr(bot, "beltFireTile", None) != key:
        bot.beltFireTile = key
        bot.beltFireTurns = 0
    if ct.get_action_cooldown() == 0 and ct.can_fire(tile):
        if bot.beltFireTurns >= _BELT_FIRE_TURN_LIMIT:
            bot.beltFireTile = None
            bot.beltFireTurns = 0
            return "failed"
        bot.beltFireTurns += 1
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


def _repairBeltIfDamaged(ct: Controller, tile: Position) -> bool:
    """Heal an ally belt at tile if adjacent, damaged, and affordable.

    `ct.can_heal` already validates adjacency, action cooldown, Ti
    affordability, and "at least one damaged friendly entity on the tile" —
    the same precondition `logistics_helpers.healAlly` relies on — so no
    separate HP bookkeeping is needed here. A belt is now cheap to keep topped
    up (HEAL_AMOUNT=4/turn for 1 Ti against GUNNER_DAMAGE=7/turn), so this is
    worth trying anywhere a belt was just confirmed correctly placed, not just
    reactively.
    """
    if not ct.can_heal(tile):
        return False
    ct.heal(tile)
    return True


def extendRelayWatermark(
    bot,
    ct: Controller,
    path: list[Position],
    core_pos: Position,
    built_to: int,
    *,
    require_vision: bool = False,
) -> int:
    """Raise watermark through contiguous correct-facing belts, vision or ledger.

    A relay longer than one unit's own vision cone can never be confirmed
    "already fully built" from a single vantage point without the ledger
    fallback `beltFacingOk` now carries — see its docstring. Still stops at
    the first tile that fails either check (wrong facing, empty, or a stale/
    absent ledger entry), same as before.

    `require_vision` forwards to `beltFacingOk`, turning that fallback off for
    a verification pass so the watermark can only advance over tiles this
    builder has actually seen this trip.
    """
    i = built_to + 1
    while i < len(path):
        tile = path[i]
        needed = neededBeltFacing(bot, ct, path, i, core_pos)
        if not beltFacingOk(
            bot,
            ct,
            tile,
            needed,
            local_map=bot.hivemind.local_map,
            require_vision=require_vision,
        ):
            break
        built_to = i
        i += 1
    return built_to


def _mergeAnchorCandidates(
    bot, ct: Controller, origin: Position, core_pos: Position, *, cache: dict | None = None
) -> list[Position]:
    """Ally CONVEYOR tiles from local_map, provably core-connected, near `origin`.

    Precomputed once per `pickRelayPath` call and fed into the search
    heuristic below it (not into `is_goal`, which already checks every
    expanded node exactly via `_mergeCandidateNeighbor`) — a plain
    `manhattan(p, core_pos)` heuristic *overestimates* the true remaining
    cost whenever a nearer merge target exists, since `is_goal` accepts that
    closer point too. An inadmissible heuristic like that silently starves
    A* of any pull toward a nearby line: the search keeps preferring nodes
    that look closer to the raw core position even when actually walking
    toward a provably-connected line a few tiles away would resolve the
    search far cheaper. Observed directly: a relay planned from a tile with
    an already-built, provably-connected line ~7 tiles away instead wandered
    130+ tiles around a wall maze before reaching any goal at all.

    Bounded to RELAY_MERGE_ANCHOR_CHEBYSHEV of `origin` — this is a heuristic
    aid, not a correctness check (the real proof is still `_relayReachesCore`,
    run here per candidate same as `is_goal` runs it per node), so it only
    needs to be cheap and roughly useful, not exhaustive.
    """
    from main import RELAY_MERGE_ANCHOR_CHEBYSHEV

    if RELAY_MERGE_ANCHOR_CHEBYSHEV is None or RELAY_MERGE_ANCHOR_CHEBYSHEV <= 0:
        return []
    me = ct.get_team()
    out: list[Position] = []
    for (x, y), cell in bot.hivemind.local_map.items():
        ent = cell.get("entity")
        if not ent or ent.get("team") != me or ent.get("type") != EntityType.CONVEYOR:
            continue
        if max(abs(x - origin.x), abs(y - origin.y)) > RELAY_MERGE_ANCHOR_CHEBYSHEV:
            continue
        tile = Position(x, y)
        if _relayReachesCore(bot, ct, tile, core_pos, cache=cache):
            out.append(tile)
    return out


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

    from main import RELAY_MAX_PATH_RATIO, RELAY_MAX_PATH_SLACK, RELAY_MERGE_ENABLED

    # Per-call memo: the merge goal is evaluated on every node A* expands, so
    # without this the same candidate belt's connectivity chain could be
    # walked repeatedly within one search. See _relayReachesCore. Also backs
    # _mergeAnchorCandidates' own _relayReachesCore calls below, so a tile
    # checked once as a heuristic anchor isn't re-walked when is_goal reaches
    # it again during the search proper.
    merge_cache: dict = {}

    merge_anchors = (
        _mergeAnchorCandidates(bot, ct, origin, core_pos, cache=merge_cache)
        if RELAY_MERGE_ENABLED
        else []
    )

    def is_goal(p: Position) -> bool:
        if isCoreAdjacent(ct, p, core_pos):
            return True
        if not RELAY_MERGE_ENABLED:
            return False
        return _mergeCandidateNeighbor(bot, ct, p, core_pos, cache=merge_cache) is not None

    def heuristic(p: Position) -> int:
        best = manhattan(p, core_pos)
        for anchor in merge_anchors:
            d = manhattan(p, anchor)
            if d < best:
                best = d
        return best

    def ok(p: Position) -> bool:
        return relayPassable(
            ct,
            p,
            ore_blocked=ore_blocked,
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
            heuristic=heuristic,
            max_iters=PATH_MAX_ITERS,
        )
    if path is None:
        path = findPathAroundObstruction(
            ct,
            origin,
            is_goal=is_goal,
            heuristic_to=core_pos,
            extra_heuristic_points=merge_anchors,
            ore_blocked=ore_blocked,
            local_map=local_map,
            max_iters=PATH_MAX_ITERS,
        )

    # Sanity cap: reject a path that's wildly longer than the straight-line
    # trip warrants, even if A* found one and it satisfies is_goal. A correct
    # heuristic (above) should make this rare, but it's a backstop against
    # any pathological case — wall mazes, a merge/is_goal edge case, anything
    # else — that produces a "valid" path no one should actually walk: a
    # relay that takes 100+ turns to lay is worse than no relay, since the
    # builder is committed (bot.inRelay) and unavailable to defend/economy
    # the whole time, and it can graze past and incidentally capture titanium
    # from unrelated enemy harvesters purely because the route happens to
    # pass near them (the engine pulls from any adjacent conveyor regardless
    # of team — see STEAL_HARVESTER_MAX_CHEBYSHEV, which only bounds a
    # *deliberate* steal pick and can't catch this). Ratio, not a flat cap,
    # so long-but-genuine trips on big maps aren't punished the same as a
    # short trip that somehow produced a huge detour; the flat slack on top
    # covers legitimately-walled short trips a pure ratio would reject.
    if path is not None and RELAY_MAX_PATH_RATIO is not None and RELAY_MAX_PATH_RATIO > 0:
        straight = manhattan(origin, core_pos)
        for anchor in merge_anchors:
            d = manhattan(origin, anchor)
            if d < straight:
                straight = d
        slack = RELAY_MAX_PATH_SLACK if RELAY_MAX_PATH_SLACK is not None else 0
        cap = straight * RELAY_MAX_PATH_RATIO + slack
        if len(path) > cap:
            path = None

    # Both planners failing (or the length cap rejecting the result) is the
    # relay's own "no route exists" verdict. Without recording it, relayToCore
    # just aborts and the reason is indistinguishable from every other abort —
    # so a relay sealed by our own gunner never reached the door search, which
    # reads `moveToward`'s walking verdict only.
    bot.relayPathBlocked = path is None
    bot.relayPathBlockedFrom = (origin.x, origin.y) if path is None else None
    return path


def _replanRelayOrAbort(
    bot, ct: Controller, core_pos: Position, enemy_core: Position | None
) -> None:
    """Replan from `relayOrigin` around whatever just blocked the lane.

    Shared by `relayToCore`'s barrier-clear-exhausted branch and its
    non-belt-building branch — both reach the same "something is sitting on
    the planned lane and isn't going away on its own" situation, just via
    different obstacles. `pickRelayPath` failing here is the relay's own
    "no route exists" verdict (see its docstring); `_reportAndAbort` is what
    eventually reaches `movement.openBlockedRoute`'s door search, the actual
    last resort that demolishes an ally turret to clear a genuine choke.
    Always terminal for the turn — callers return right after calling this.
    """
    new_path = pickRelayPath(bot, ct, bot.relayOrigin, core_pos)
    if new_path is None:
        bot.relayPath = None
        _reportAndAbort(bot, ct)
        return
    if _isEnemyCoreAdjacent(ct, new_path[-1], enemy_core):
        bot.relayPath = None
        abortRelay(bot)
        return
    bot.relayPath = new_path
    bot.relayBuiltTo = extendRelayWatermark(bot, ct, new_path, core_pos, -1)


def _relayStuckExceeded(bot) -> bool:
    """Increment bot.relayStuck; True once it reaches RELAY_STUCK_TURNS.

    relayToCore hits this same "count consecutive stalls, give up at N" shape
    at five separate points below (blocked-obstacle clear, in-place
    placement, move-toward) — this is the one place it's counted. Resetting
    the counter on success/give-up stays with each call site, since what
    "give up" means differs there (replan vs. abort vs. report-and-abort).
    """
    from main import RELAY_STUCK_TURNS

    bot.relayStuck = getattr(bot, "relayStuck", 0) + 1
    return bot.relayStuck >= RELAY_STUCK_TURNS


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
    # A verification pass proves the line with its own eyes — no ledger
    # shortcuts anywhere the "already built" verdict is reached.
    verify = getattr(bot, "relayVerify", False)

    path = bot.relayPath
    if path is not None:
        if _isEnemyCoreAdjacent(ct, path[-1], enemy_core):
            bot.relayPath = None
            abortRelay(bot)
            return
        bot.relayBuiltTo = extendRelayWatermark(
            bot, ct, path, core_pos, bot.relayBuiltTo, require_vision=verify
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
        bot.relayBuiltTo = extendRelayWatermark(
            bot, ct, path, core_pos, -1, require_vision=verify
        )

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
        if beltFacingOk(
            bot,
            ct,
            tile,
            needed,
            local_map=bot.hivemind.local_map,
            require_vision=verify,
        ):
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

    # A gunner covering this tile is no longer a reason to route around it —
    # killing a belt now costs the enemy more than it costs us to place or
    # repair one (GUNNER_DAMAGE=7/turn vs HEAL_AMOUNT=4/turn for 1 Ti;
    # CONVEYOR_BASE_COST=3 to rebuild outright vs GUNNER_AMMO_COST=4/shot to
    # keep shooting it), so belts are placed straight through gunner fire the
    # same as any other empty tile. See _repairBeltIfDamaged for the other
    # half of this: healing partial damage before it's destroyed.

    # Barrier blocking — clear it (destroy ours, fire down theirs) instead of
    # abandoning the planned lane. Any other non-belt building — our own
    # gunner/sentinel/launcher (a guardian dropping defence mid-relay), an
    # ally/enemy harvester, or an enemy turret — replans around it instead.
    if ct.is_in_vision(tile):
        block_id = ct.get_tile_building_id(tile)
        if block_id is not None:
            etype = ct.get_entity_type(block_id)
            if etype == EntityType.BARRIER:
                result = clearBeltObstacle(bot, ct, tile)
                if result == "acted":
                    return
                if result == "waiting":
                    return
                if result == "failed":
                    if not _relayStuckExceeded(bot):
                        return
                    # 3 failed clear attempts — most commonly a heal-racing
                    # enemy barrier (clearBeltObstacle's own fire branch is
                    # already capped, but a barrier that gets rebuilt between
                    # attempts has the same effect) — stop burning turns
                    # trying to clear it and route around it instead of
                    # aborting the whole relay. Mirrors the non-belt-building
                    # branch below; see `_replanRelayOrAbort`.
                    bot.relayStuck = 0
                    _replanRelayOrAbort(bot, ct, core_pos, enemy_core)
                    return
                # "clear" — fall through to place
            elif etype not in BELT_TYPES:
                # relayPassable already excludes these from a fresh plan, so
                # replan immediately instead of nulling the path and waiting a
                # full turn: on any map where a route around exists this finds
                # it the same turn and keeps building. Nulling and returning
                # here used to leave it to next turn's relayToCore call to
                # notice `bot.relayPath is None` and replan on its own — but
                # if *that* replan also failed (or never got a chance to run
                # before something else cleared relayOrigin), the outcome
                # looked identical to "stop the relay" instead of "path around
                # it". Only a genuine choke (no route exists at all) still
                # falls through to `_reportAndAbort`, which is what eventually
                # reaches `movement.openBlockedRoute`'s door search — the last
                # resort that demolishes an ally turret to clear the lane.
                _replanRelayOrAbort(bot, ct, core_pos, enemy_core)
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
            # defence._danglingBeltEnds only ever sees a line that dangles, never
            # one that was quietly re-pointed.
            #
            # No connectivity proof here on purpose. It can't be established
            # cheaply — pickRelayPath's merge goal already tried and failed at
            # plan time (a builder's ledger is personal-vision only, so it knows
            # nothing about the near-core run it never walked) — and measuring
            # the alternatives showed both cost more than they saved: walking the
            # chain to verify ran ~9% worse on Ti/turn, since it abandons the
            # relay mid-air to go wandering. If this belt turns out to be a dead
            # line, extending it is the dangling-end repair's job
            # (finishNearbyBelt / reconnectBelts, in defence.py), which is
            # already anchored for exactly that.
            if _allyBeltAt(ct, tile):
                _repairBeltIfDamaged(ct, tile)
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
                if _relayStuckExceeded(bot):
                    _reportAndAbort(bot, ct)
                return
            if result == "waiting":
                return
            # clear — fall through to place

    if beltFacingOk(bot, ct, tile, needed, local_map=bot.hivemind.local_map):
        bot.relayBuiltTo = idx
        _repairBeltIfDamaged(ct, tile)
        return

    # Need empty tile to place — step off if standing on it
    if pos == tile:
        if ct.get_move_cooldown() != 0:
            return
        for d in CARDINALS:
            if ct.can_move(d):
                ct.move(d)
                return
        if _relayStuckExceeded(bot):
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
        if _relayStuckExceeded(bot):
            abortRelay(bot)
        return

    if moveToward(bot, ct, tile):
        bot.relayStuck = 0
    elif _relayStuckExceeded(bot):
        _reportAndAbort(bot, ct)


def pursueHijackTarget(bot, ct: Controller) -> bool:
    """Default-builder step: continue or pick up a hijack-registry relay.

    Mirrors how `checkOre`'s HARVESTER branch commits to a steal (`bot.
    relayOrigin = tile; bot.inRelay = True; relayToCore(bot, ct)`) — same
    machinery, no new relay-building logic. The only hijack-specific state is
    `bot.hijackOrigin`, which exists purely so `_reportAndAbort` knows which
    side of the shared registry to re-offer a stalled relay to.

    Deliberately no claim-on-pickup, matching the harvester-orphan registry's
    own "report-only, no claim field" philosophy — two builders racing the
    same entry is harmless and self-resolving (relayToCore's merge logic and
    ordinary belt-placement conflict handling already cover a second builder
    arriving at a tile the first one already built on).

    One deliberate divergence from the harvester registry, though: a fresh
    pickup that fails on its very first `relayToCore` call is evicted
    outright instead of re-offered. `_reportAndAbort`'s premise for the
    harvester side is that a *different* builder's fog/vision or approach
    angle genuinely might succeed where this one didn't — reasonable for an
    own-side relay that stalled partway through. A hijack origin sitting deep
    in defended enemy territory is far more likely to be genuinely
    unreachable than fog-affected, and every builder that tries it fails
    identically — observed directly: the same tile re-offered and re-failed
    by three different builders across dozens of turns, each attempt burning
    a full `pickRelayPath` search (a real A* run) for nothing. `relayToCore`
    only ever takes one action (or aborts) per call before returning, so
    `not bot.inRelay` right after this first call already means "aborted
    with zero progress" — no separate check needed. A stall *after* real
    progress goes through relayToCore's own normal stuck/obstacle handling
    (`_reportAndAbort`) on a later call, unaffected by this.
    """
    if bot.inRelay and bot.hijackOrigin:
        relayToCore(bot, ct)
        return True
    if bot.inRelay:
        return False  # already committed to something else this doesn't own
    target = _pickHijackTarget(bot, ct)
    if target is None:
        return False
    bot.relayOrigin = target
    bot.hijackOrigin = True
    bot.inRelay = True
    relayToCore(bot, ct)
    if not bot.inRelay:
        # Aborted on the very first call, nothing built — see docstring. If
        # relayToCore instead completed the relay outright in one call (the
        # origin was already core-adjacent), clearOrphanRelay here is just a
        # harmless no-op — markRelayComplete's own success path already
        # called it.
        clearOrphanRelay(ct, target)
        bot.skippedOres.add((target.x, target.y))
    return True


def bid_is_belt(ct: Controller, tile: Position) -> bool:
    bid = ct.get_tile_building_id(tile)
    if bid is None:
        return False
    return ct.get_entity_type(bid) in BELT_TYPES
