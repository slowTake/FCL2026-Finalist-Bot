"""Core spawning: how many builders, and which tile each one is born on.

The cap is a *lifetime* cap. `ct.read_store(4)` counts builders ever spawned and has
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
from ecoBot import chooseOre

MAX_BUILDERS = 5
# Builders cost +20% each and are useless without titanium to build with. Past the
# opening pair, keep enough banked for a harvester and its line — spending the
# whole 500 on bodies starves every relay at once.
MIN_BUILDERS = 2
BUILDER_RESERVE = 60

# The last of the opening batch is the raider, so it leaves with the same cost
# scale as its siblings rather than paying +20% for being an afterthought. It is a
# reassignment, not an extra body: the economy runs on four builders once this one
# departs. On a map we cannot fingerprint there is no enemy core to aim at, so the
# designation is skipped and the fifth stays on eco.
RAID_INDEX = MAX_BUILDERS

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

    `read_store(4) + 1` is the index the newborn will be allocated, so this reproduces
    the `ORE_SPREAD` fan-out that builder is itself going to apply — which is what
    makes consecutive builders leave by different faces of the core.

    Only attempted on a fingerprinted map. `ORE_LOCATIONS` is what gives the core the
    whole ore field at turn 0 (hivemind loads it once `map_id` is known); without it
    the core is guessing from its own vision, and it spawns its first builders within
    a few turns of the match starting, so that vision is a radius-6 keyhole. Steering
    off that sample measured *worse* than not steering — saga, which is not in
    `MAP_ID_TO_NAME`, went from an even mirror to 1.64 skewed. Unknown map, no guess.
    """
    if bot.corePos is None or bot.hivemind.map_id == 0:
        return None

    core = bot.corePos

    def distTo(ore: Position) -> int:
        return ph.manhattanToCore(ore, core)

    return chooseOre(bot, ct, distTo, ct.read_store(4) + 1)


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


def runCoreSpawn(bot, ct: Controller) -> None:
    """Spawn at most one builder, on the tile that best starts its journey."""
    if bot.corePos is None or ct.get_action_cooldown() != 0:
        return

    built = ct.read_store(4)
    if built >= MAX_BUILDERS:
        return

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
            if built + 1 == RAID_INDEX and _raidReady(bot, ct):
                # spawn_builder just wrote slot 4 = built + 1, and that value is
                # the my_index this newborn will read for itself, so it is the
                # name to publish it under.
                ct.write_store(raid.SLOT_RAIDER_INDEX, built + 1)
            return
