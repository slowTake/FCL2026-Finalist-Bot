# Bandit builder — one role, one chain (check behavior here first)
#
# ## Assignment
#    None. Every builder runs `runBuilder` below. Baron split five builders into
#    2 guardians / 1 harasser / 2 defaults (`idx % 5`) and dispatched to three
#    separate orchestrators; bandit has one orchestrator and separates builders
#    *spatially* instead, via `spreadZone` — a static angle-wedge + distance-band
#    assignment derived from `my_index` alone. Same purity property baron's
#    `isGuardian(idx)` had: deterministic, permanent per unit, no coordination,
#    no store slot, no oscillation.
#
# ## Priority chain (runBuilder — first step that acts wins the turn)
#  1. bookkeeping — resolve corePos/enemyCorePos from the packed-cores slot,
#        ledger, or geometric guess.
#  2. breakEncirclement — boxed in on all four sides with nothing able to move →
#        destroy the cheapest adjacent ally building and step through. Reads
#        bot.motionless, its own counter, never reset by anything but real
#        movement.
#  3. repair — heal an adjacent damaged ally building
#        (CORE > GUNNER > SENTINEL > HARVESTER > CONVEYOR). Deliberately above
#        the relay step: in a bot whose economy is its weapon, damage to that
#        economy is never something to finish a chore before answering.
#  4. relayToCore (if already committed) — a started relay finishes before
#        anything else is reconsidered.
#  5. adoptDanglingBelt — not relaying, but someone's abandoned belt line is
#        within ADOPT_BELT_CHEBYSHEV → adopt it and fall into relayToCore this
#        same tick. Above ore work because 20 Ti of harvester is already sunk at
#        the far end of that line.
#  5b. openBlockedRoute / breakOreDeadlock — economy unblockers, role-agnostic.
#        openBlockedRoute sits above breakOreDeadlock because it needs the ore
#        claim breakOreDeadlock would clear.
#  6. buildHarvester — claim empty ore, spread-zone biased.
#  7. maintainHarvester — relay from an existing harvester. This is where the
#        steal happens: `_harvesterOwnerAllowed` is unconditionally true, so an
#        enemy harvester is just maintainable work.
#  7b. finishNearbyBelt — one-tile belt patch, the cheap complement to step 5.
#  8. beltHijack — no ore or relay work this turn → plant a conveyor in front of
#        an enemy belt and siphon it. Opportunistic by position in the chain: it
#        must never compete with our own economy.
#  9. gunnerCordon / coreSentinel — only past TI_SURPLUS_THRESHOLD, only when
#        still idle. Combat is what bandit does with money it has nothing better
#        to do with.
# 10. recheckOreNodes — revisit stale/blocked ore.
# 11. exploreFrontier — last resort, nothing known to work. Unbounded, like
#        every other movement in this bot.
#
# ## Dropped from baron's chain
#    The turret-visibility economy freeze (`_reactEnemyTurret`'s "stop and
#    stare"): bandit's builders keep working. The per-action threat checks inside
#    buildHarvester/relayToCore (skip a gunner-covered tile) are kept — those are
#    tile-level safety, not a role-level stall.

from __future__ import annotations

from fcode import Controller, EntityType, Position

from defence import finishNearbyBelt, repair
from econwar import beltHijack, coreSentinel, gunnerCordon, surplusReady
from geometry import angleBucket
from hivemind import CoreState, unpack_cores
from logistics import (
    adoptDanglingBelt,
    breakOreDeadlock,
    buildHarvester,
    exploreFrontier,
    maintainHarvester,
    openBlockedRoute,
    recheckOreNodes,
    relayToCore,
)
from pathing import breakEncirclement, manhattan


def _resolveCores(bot, ct: Controller) -> None:
    """Fill in corePos/enemyCorePos from the best source available."""
    from main import SLOT_PACKED_CORES

    ally, enemy = unpack_cores(ct.read_store(SLOT_PACKED_CORES))
    if bot.corePos is None and ally[0] != CoreState.UNKNOWN:
        bot.corePos = Position(ally[1], ally[2])
    if enemy[0] != CoreState.UNKNOWN:
        bot.enemyCorePos = Position(enemy[1], enemy[2])
    elif bot.enemyCorePos is None and bot.hivemind.enemy_core_pos is not None:
        bot.enemyCorePos = bot.hivemind.enemy_core_pos
    elif bot.enemyCorePos is None:
        bot.enemyCorePos = _enemyCoreFromLedger(bot, ct)
        if bot.enemyCorePos is None:
            w, h = ct.get_map_width(), ct.get_map_height()
            cp = bot.corePos or ct.get_position()
            ex = w - 1 - cp.x if cp.x > w // 2 else w - 2 - cp.x
            ey = h - 1 - cp.y if cp.y > h // 2 else h - 2 - cp.y
            bot.enemyCorePos = Position(max(0, ex), max(0, ey))


def _enemyCoreFromLedger(bot, ct: Controller) -> Position | None:
    """Enemy core position from anything this builder has actually seen.

    Both sources above are the turn-0 map fingerprint: slot 15 is packed once
    inside `_detect_and_store_map`, and `hivemind.enemy_core_pos` is set in the
    same call. So when the fingerprint misses, *seeing* the enemy core changed
    nothing — and in bandit that matters more than it did in baron, because the
    enemy core anchors `spreadZone`'s whole coordinate system as well as the
    cordon band. Without a fallback, an unidentifiable map would leave every
    builder in the same wedge.

    Reads only *this builder's* sightings: the delta ledger runs builder → core
    only, so another unit seeing the enemy core does not help this one. Returns
    the footprint anchor (min x, min y) to match the packed convention.
    """
    me = ct.get_team()
    tiles = [
        (x, y)
        for (x, y), cell in bot.hivemind.local_map.items()
        if (ent := cell.get("entity")) is not None
        and ent.get("type") == EntityType.CORE
        and ent.get("team") != me
    ]
    if not tiles:
        return None
    return Position(min(t[0] for t in tiles), min(t[1] for t in tiles))


# ------------------------------------------------------------------
# Spread zoning
# ------------------------------------------------------------------


def spreadZoneForIndex(idx: int) -> tuple[int, int]:
    """(angle_bucket, distance_band) for a builder index. Pure.

    Split out from `spreadZone` because `respawn.run_core_spawn` needs the zone
    of a builder that does not exist yet — it predicts the next index off store
    slot 4 to bias the spawn tile, and has no bot to hand.

    Consecutive indices land in *adjacent wedges*, not adjacent bands: with
    SPREAD_ANGLE_BUCKETS=4 the first four builders fan out around the core, and
    only the fifth starts a second band further out. That ordering matters for
    the opening — early builders should cover angles, not distance.
    """
    from main import SPREAD_ANGLE_BUCKETS, SPREAD_DISTANCE_BANDS

    if idx < 0:
        idx = 0
    buckets = max(1, SPREAD_ANGLE_BUCKETS)
    bands = max(1, SPREAD_DISTANCE_BANDS)
    return (idx % buckets, (idx // buckets) % bands)


def spreadZone(bot) -> tuple[int, int]:
    """This builder's (angle_bucket, distance_band), cached on bot._spreadZone.

    Pure function of `my_index`, so the cache never needs invalidating — it
    exists only to skip the modulo arithmetic and the `main` import on the hot
    ore-scoring path, which runs once per candidate tile per turn.
    """
    cached = getattr(bot, "_spreadZone", None)
    if cached is not None:
        return cached
    zone = spreadZoneForIndex(bot.hivemind.my_index)
    bot._spreadZone = zone
    return zone


def _spreadScore(bot, ore: Position) -> int:
    """Penalty, in ore-score points, for `ore` being outside this bot's zone.

    **Soft, always.** This is added to `logistics._oreConvenienceKey`'s
    distance blend, never used as a filter — a wedge that happens to contain no
    ore must not starve the builder assigned to it; that builder should simply
    lose ties to its neighbours and drift into their leftovers. This is the
    entire anti-clumping mechanism, replacing baron's role split and its
    `ORE_STAGGER` rank indexing, and `SPREAD_ANGLE_PENALTY`/`SPREAD_BAND_PENALTY`
    are the dials: raise them to make builders commit to their wedge even when
    closer ore exists elsewhere, lower them to prioritise convenience.

    Returns 0 whenever the geometry is unknown (no core fix yet), so an
    unidentified map degrades to baron's plain distance blend rather than to
    a wrong answer.
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
    # Circular distance: with 4 buckets, wedge 0 vs wedge 3 are neighbours (1
    # apart), not opposites (3 apart). A plain difference would punish the seam.
    d_angle = min(
        (got_angle - want_angle) % buckets, (want_angle - got_angle) % buckets
    )
    penalty = d_angle * SPREAD_ANGLE_PENALTY

    # Band is radial along the core->enemy-core span: band 0 is our half, the
    # last band is pushing into theirs. Measured as a fraction of that span
    # rather than in absolute tiles so it scales with map size.
    bands = max(1, SPREAD_DISTANCE_BANDS)
    span = manhattan(core, enemy) or 1
    frac = manhattan(core, ore) / span
    got_band = min(int(frac * bands), bands - 1)
    penalty += abs(got_band - want_band) * SPREAD_BAND_PENALTY
    return int(penalty)


# ------------------------------------------------------------------
# The chain
# ------------------------------------------------------------------


def runBuilder(bot, ct: Controller) -> None:
    """Every builder, every turn. See the task list at the top of this file."""
    _resolveCores(bot, ct)

    # Ahead of everything: boxed in on all four sides, nothing below can move.
    if breakEncirclement(bot, ct, local_map=bot.hivemind.local_map):
        return

    # Above the relay step on purpose — see `defence.repair`.
    if repair(bot, ct):
        return

    if bot.inRelay:
        relayToCore(bot, ct)
        # relayToCore clears inRelay when it completes or aborts, in both cases
        # without consuming an action. Falling through on that is what keeps a
        # builder from wasting the turn it finished a relay on.
        if bot.inRelay:
            return

    if adoptDanglingBelt(bot, ct):
        return

    # Above breakOreDeadlock, which clears oreSite — this needs the claim to know
    # what route was blocked. Removing the blockage beats giving up on the node.
    if openBlockedRoute(bot, ct):
        return
    breakOreDeadlock(bot, ct)  # state-only; economy below re-picks this turn

    if buildHarvester(bot, ct):
        return
    if maintainHarvester(bot, ct):
        return

    # Cheap one-tile belt patch — the small-gap complement to adoptDanglingBelt.
    if finishNearbyBelt(bot, ct):
        return

    # Idle from here down. Nothing below may ever outrank our own economy.
    if beltHijack(bot, ct):
        return

    if surplusReady(ct):
        if gunnerCordon(bot, ct):
            return
        if coreSentinel(bot, ct):
            return

    if recheckOreNodes(bot, ct):
        return

    # Last resort: nothing known to build or maintain. Normal on an
    # unfingerprinted map. Unbounded — walks toward unexplored map, not a radius.
    exploreFrontier(bot, ct, outward=False)
