"""Core spawning: how many builders, and which tile each one is born on.

The cap is a *lifetime* cap. `ct.read_store(store.BUILDERS)` counts builders ever spawned and has
to stay monotonic — it is the `my_index` allocator — so a builder lost at turn 200 is
never replaced. Despite the module name nothing here respawns anything yet; when that
changes the hook is a living count off the ledger (every builder broadcasts its own
position first on every rotation precisely to report liveness) and only the
`built >= MAX_BUILDERS` test below needs to move.

Where a builder is born is not cosmetic: reversing the order this module walks the
core's ring was measured at ~10k titanium on drakkarfjord. So the ring is ordered
against where the newborn is actually going, rather than against the compass.
"""

from __future__ import annotations

from fcode import Controller, Position

import pathfindingHelpers as ph
import raid
import store
from ecoBot import chooseOre

MAX_BUILDERS = 5
# Builders cost +20% each and are useless without titanium to build with. Past the
# opening pair, keep enough banked for a harvester and its line — spending the
# whole 500 on bodies starves every relay at once.
MIN_BUILDERS = 2
BUILDER_RESERVE = 60

# Raider replenishment. Once the bank first reaches REPLENISH_TI the core keeps a
# steady trickle of extra raiders coming: one the moment it arrives at the
# threshold, then one more every REPLENISH_INTERVAL rounds for as long as we're
# sitting on that much titanium. Every body it spawns is past MAX_BUILDERS and is
# always a raider (main.py routes them by index via isReplenishmentRaider) — if
# the bank is this deep the economy is already saturated and the marginal builder
# is worth more pressing the enemy core than farming.
REPLENISH_TI = 650
REPLENISH_INTERVAL = 100

# The opening two builders are the raiders — the first out of the gate, so they
# reach the enemy core soonest, and the first also plants the launcher (see
# launcher.placeCoreLauncher) that flings the rest of the crew outbound. This is
# a reassignment, not extra bodies: the economy runs on the later builders once
# the pair departs (was: eco on all, one raider last). Even on a map we cannot
# fingerprint the raiders are still designated — raid.enemyCorePos falls back to a
# point-mirror of our own core, which the core knows from turn 0, so _raidReady is
# satisfied at the first spawn and tier 1 corrects the aim once the core is seen.
#
# Keyed by *my_index*, not by respawn's pre-spawn count. The newborn's my_index
# is `built + 2`, not `built + 1`: the spawn's slot-4 write has already landed by
# the time the builder captures the count on its birth round (hivemind index
# timing), so the count it reads is one past what respawn saw here. The first two
# builders therefore self-identify as my_index 2 and 3 — those are the values
# main.py compares against, so those are the values we publish.
_MYINDEX_OFFSET = 2
_OPENING_RAIDERS = {2: store.ROLE_RAIDER, 3: store.ROLE_RAIDER_2}

# A spawn tile needs somewhere to go. One open neighbour is a corridor the builder
# can still be pushed back into; below that it is a pocket.
_MIN_ESCAPE_NEIGHBOURS = 2


def _hasEscapeRoute(ct: Controller, hm, tile: Position) -> bool:
    """Can a builder born here get anywhere.

    `can_spawn` vets the tile itself but not what lies beyond it, and the core only
    ever sees the tile — so a legal candidate walled in on every other side looks
    fine and costs a builder for the whole match rather than the turn it would have
    taken to pick elsewhere.
    """
    open_sides = 0
    for n in ph.orthogonalTiles(tile):
        if not ph.isImpassable(ct, hm, n):
            open_sides += 1
            if open_sides >= _MIN_ESCAPE_NEIGHBOURS:
                return True
    return False


def spawnSites(ct: Controller, hm, corePos: Position) -> list[Position]:
    """Ring tiles worth trying, in `coreRing` order.

    `coreRing` does no bounds checking of its own, so filtering here is required
    rather than defensive — a core in a map corner has ring tiles off the edge.
    """
    return [
        t for t in ph.coreRing(corePos)
        if ph.inBounds(ct, t) and _hasEscapeRoute(ct, hm, t)
    ]


def _predictTarget(bot, ct: Controller) -> Position | None:
    """Where the builder we are about to spawn will probably head first.

    A guess, and deliberately nothing more: it picks the birth tile and is then
    thrown away. Nothing is written to the store and nothing is handed to the
    builder, which runs `pickOre` from scratch on its first turn exactly as it always
    has. Being wrong costs one suboptimal tile, never a commitment.

    `built + _MYINDEX_OFFSET` (== `read_store(store.BUILDERS) + 2`) is the index the
    newborn will be allocated — the spawn write has already landed by the time it
    reads, so the count is inclusive of itself; see observations.md on the empirical
    +2 offset. Reproducing the `ORE_SPREAD` fan-out at that index is what makes
    consecutive builders leave by different faces of the core.

    Only attempted on a fingerprinted map. `ORE_LOCATIONS` is what gives the core the
    whole ore field at turn 0 (hivemind loads it once `map_id` is known); without it
    the core is guessing from its own vision, and it spawns its first builders within
    a few turns of the match starting, so that vision is a radius-6 keyhole. Steering
    off that sample measured *worse* than not steering — saga, which is not in
    `MAP_ID_TO_NAME`, went from an even mirror to 1.64 skewed. Unknown map, no guess.

    Opening raiders are the exception: they don't want ore at all, they want the
    enemy-facing side of the core. Steer them at the enemy core so they're born on
    the front — that's where the first raider plants the launcher (behind the core
    is useless defensively) and where both are headed anyway.
    """
    if bot.corePos is None:
        return None

    # Raiders first, before the fingerprint gate. They don't steer off the static
    # ore field (the reason for the map_id==0 gate below — off-vision ore steering
    # measured worse), they steer at the enemy core, which raid.enemyCorePos
    # supplies from the mirror even at map_id 0. Gating them out left opening
    # raiders on an unfingerprinted map born on an arbitrary ring face for no
    # reason tied to that gate's actual rationale.
    myIndex = ct.read_store(store.BUILDERS) + _MYINDEX_OFFSET
    if myIndex in _OPENING_RAIDERS or isReplenishmentRaider(myIndex):
        return raid.enemyCorePos(bot, ct)

    if bot.hivemind.map_id == 0:
        return None
    core = bot.corePos

    def distTo(ore: Position) -> int:
        return ph.manhattanToCore(ore, core)

    return chooseOre(bot, ct, distTo, myIndex)


def _towards(target: Position):
    """Order sites by closeness to `target`, Manhattan first.

    The Euclidean term only breaks ties, but it earns its place: a plain Manhattan
    `min` falls back to list order, and `coreRing`'s order is compass-derived — the
    orientation bug observations.md still has open. Euclidean is fine-grained enough
    that almost no tie survives to reach it.
    """
    def key(t: Position) -> tuple[int, int]:
        manhattan = abs(t.x - target.x) + abs(t.y - target.y)
        return manhattan, (t.x - target.x) ** 2 + (t.y - target.y) ** 2

    return key


def _raidReady(bot, ct: Controller) -> bool:
    """Is there anything for a raider to aim at.

    `enemy_core_pos` only exists on a fingerprinted map; without it a raider just
    wanders, and the body is worth more on ore.
    """
    return raid.enemyCorePos(bot, ct) is not None


def isReplenishmentRaider(myIndex: int) -> bool:
    """True for any builder spawned past MAX_BUILDERS.

    The REPLENISH_TI replenishment (_replenishDue) is the only thing that ever spawns
    beyond the cap, and every body it spawns is a raider. Keyed on index alone so
    it survives losing sight of the core, exactly like the opening pair — main.py
    routes on it the same way it routes the two role slots.
    """
    return myIndex >= MAX_BUILDERS + _MYINDEX_OFFSET


def _replenishDue(bot, ct: Controller) -> bool:
    """Should the REPLENISH_TI raider replenishment spawn a body this turn.

    True on the first round the bank reaches REPLENISH_TI, then once every
    REPLENISH_INTERVAL rounds while it stays at/above it. Gated on there being an
    enemy core to aim at — a raider with nothing to press just wanders, so a body
    with no target is not worth the +20% cost (see _raidReady). The interval clock
    (`bot._lastReplenishRound`) is only advanced by an actual spawn, so a turn we
    were rich but boxed out of the ring or capped at MAX_TEAM_UNITS does not burn
    the slot — the next open turn still spawns.
    """
    if ct.get_global_resources() < REPLENISH_TI:
        return False
    if not _raidReady(bot, ct):
        return False
    last = bot._lastReplenishRound
    if last is None:
        return True
    return ct.get_current_round() - last >= REPLENISH_INTERVAL


def runCoreSpawn(bot, ct: Controller) -> None:
    """Spawn at most one builder, on the tile that best starts its journey.

    Up to MAX_BUILDERS on the normal reserve-gated path; past the cap only the
    REPLENISH_TI raider replenishment spawns, and everything it spawns is a raider.
    """
    if bot.corePos is None or ct.get_action_cooldown() != 0:
        return

    built = ct.read_store(store.BUILDERS)

    replenish = False
    if built >= MAX_BUILDERS:
        # Past the normal cap: the only body that spawns now is a replenishment
        # raider, and only when the bank is deep enough to earn it.
        if not _replenishDue(bot, ct):
            return
        replenish = True
    else:
        cost = ct.get_builder_bot_cost()
        reserve = BUILDER_RESERVE if built >= MIN_BUILDERS else 0
        if ct.get_global_resources() < cost + reserve:
            return

    sites = spawnSites(ct, bot.hivemind, bot.corePos)
    if not sites:
        # Every ring tile is a pocket or off-map. Better a boxed-in builder than no
        # builder — the filter is a preference, not a licence to stop spawning.
        sites = ph.coreRing(bot.corePos)

    target = _predictTarget(bot, ct)
    if target is not None:
        sites.sort(key=_towards(target))

    for tile in sites:
        # spawn_builder here, not ct.spawn_builder: it increments slot 4, which is
        # what gives each builder its my_index and metronome turn.
        if bot.hivemind.spawn_builder(ct, tile):
            myIndex = built + _MYINDEX_OFFSET
            if replenish:
                # A raider identified by index (isReplenishmentRaider) — no role
                # slot to publish. Advancing the clock only on a real spawn is what
                # holds the every-100-rounds cadence.
                bot._lastReplenishRound = ct.get_current_round()
            else:
                role = _OPENING_RAIDERS.get(myIndex)
                if role is not None and _raidReady(bot, ct):
                    # Publish under the my_index this newborn will read for itself,
                    # so main.py's `my_index == readRole(...)` test names the right
                    # body.
                    store.assignRole(ct, role, myIndex)
            return
