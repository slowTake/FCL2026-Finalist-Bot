"""Builder economy role. Orchestrator only — mechanics live in the modules it calls."""

from __future__ import annotations

from fcode import Controller, EntityType, Position

import pathfindingHelpers as ph
from movement import exploreStep, walkTo
from patience import clearHold, waitTi
import store
from relays import hijackRelay, relayToHome

# How many of the nearest ore nodes builders fan out across. Deterministic off
# my_index, so two builders with different indices rarely pick the same node.
# Not a claim — see observations.md.
ORE_SPREAD = 4

# A remembered ore sighting older than this is worth walking back to verify. The
# harvester we think is on it may be long dead: nothing refreshes a tile once a
# builder has wandered out of vision, so without this a node is written off for
# the rest of the match on one stale glimpse.
ORE_RECHECK_TURNS = 120

# How long a builder leaves alone a node it couldn't reach. Long enough not to
# thrash, short enough that a transient blocker doesn't cost us the node.
ORE_SKIP_TURNS = 150

# How far from our core we will chase an enemy harvester to tap it. Past this the
# belt run home costs more than the ore returns — so the thing being bounded is belt
# count, which is 4-connected, hence Manhattan and measured from the core's *edge*.
# This is a strictly tighter leash than the Chebyshev it replaced, tightest at 45°
# (a node at (12, 12) used to read 12 and now reads 24, which is what it actually
# costs). 12 has not been re-tuned since the metric changed — first place to look.
HIJACK_MAX_MANHATTAN = 12

# Tiles of head start a tappable enemy harvester gets over free ore when picking a
# target. It saves the (scaling) harvester cost and denies them the node, so it is
# worth a short detour — but not an expedition.
HIJACK_BONUS = 4


def runEcoBot(bot, ct: Controller) -> None:
    if bot.inRelay and relayToHome(bot, ct):
        return

    # Seize before build: on an enemy-harvester tile `can_build_harvester` is
    # False, and buildHarvester answers that by clearing oreTarget — which used
    # to leave seizeHarvester reading None and bailing, seekOre re-picking the
    # same tile, and the builder idling beside it forever with a cold action
    # cooldown. seizeHarvester is guarded by _enemyHarvester, so it is a no-op
    # on every ordinary target and costs the build path nothing.
    if seizeHarvester(bot, ct):
        return

    if buildHarvester(bot, ct):
        return

    if seekOre(bot, ct):
        return

    exploreStep(bot, ct)


def buildHarvester(bot, ct: Controller) -> bool:
    """Plant a harvester on the claimed ore, then commit to relaying it home."""
    ore = bot.oreTarget
    if ore is None or ct.get_action_cooldown() != 0:
        return False

    pos = ct.get_position()
    if abs(pos.x - ore.x) + abs(pos.y - ore.y) != 1:
        return False  # builds are orthogonal-only; diagonal isn't close enough

    if waitTi(bot, ct, ct.get_harvester_cost()):
        return True
    if ct.get_global_resources() < ct.get_harvester_cost():
        return True  # broke, not blocked — keep the claim rather than re-picking

    if not ct.can_build_harvester(ore):
        bot.oreTarget = None
        return False

    ct.build_harvester(ore)
    store.put(ct, store.HARVESTERS, ct.read_store(store.HARVESTERS) + 1)

    bot.relayFrom = ore
    bot.inRelay = True
    bot.oreTarget = None
    clearHold(bot)
    return True


def seizeHarvester(bot, ct: Controller) -> bool:
    """We've walked up to an enemy harvester — tap it rather than build our own."""
    ore = bot.oreTarget
    if ore is None or not _enemyHarvester(bot, ct, ore):
        return False

    pos = ct.get_position()
    if abs(pos.x - ore.x) + abs(pos.y - ore.y) != 1:
        return False  # output is cardinal-only, so the belt must be cardinal too

    bot.oreTarget = None
    return hijackRelay(bot, ct, ore)


def seekOre(bot, ct: Controller) -> bool:
    if bot.oreTarget is None or not _oreWorthGoing(bot, ct, bot.oreTarget):
        bot.oreTarget = pickOre(bot, ct)
    ore = bot.oreTarget
    if ore is None:
        return False

    pos = ct.get_position()
    if abs(pos.x - ore.x) + abs(pos.y - ore.y) == 1:
        # Already in position. Hold it: walkTo reports arrival as False, and
        # falling through on that would send the builder off exploring on the
        # exact turn it got where it was going.
        return True

    def beside(p: Position) -> bool:
        return abs(p.x - ore.x) + abs(p.y - ore.y) == 1

    if walkTo(bot, ct, beside, ore, ("ore", ore.x, ore.y)):
        return True

    # Out of patience getting there. Without this a builder commits to one
    # unreachable node for the rest of the match — four of them sat on the same
    # enemy harvester for 400 turns.
    _skipOre(bot, ore)
    bot.oreTarget = None
    return False


def pickOre(bot, ct: Controller) -> Position | None:
    """Nearest workable node, then a stale site to verify.

    Free ore and a tappable enemy harvester compete on distance rather than in
    strict tiers: tapping spends belts alone — no harvester cost, which scales
    +5% every time — and denies the node, so a close enemy harvester beats a
    distant empty node. Ranking hostile strictly below free meant it was never
    reached at all while any free ore remained known.

    The stale tier is what keeps builders cycling: once every node we remember is
    spoken for, they go re-check the oldest sighting instead of exploring forever.
    """
    pos = ct.get_position()

    # Built once per call, not once per ore — this sits inside the 10ms budget.
    def distTo(ore: Position) -> int:
        return abs(ore.x - pos.x) + abs(ore.y - pos.y)

    return chooseOre(bot, ct, distTo, bot.hivemind.my_index)


def rankOre(bot, ct: Controller, distTo) -> tuple[list[Position], list[Position]]:
    """(workable nodes best-first, stale sites nearest-first), measured by `distTo`.

    Takes a distance callable rather than an origin tile because the two callers
    measure from different things: a builder from its own position, the core from its
    2x2 footprint via `ph.manhattanToCore`. Handing the core's anchor over as a bare
    point would put the anchor bias back — at most two tiles, but enough to flip
    near-ties between mirror-equivalent nodes, which is exactly what symmetry rests on.
    """
    ranked: list[tuple[int, Position]] = []
    stale: list[Position] = []

    for ore in knownOre(bot, ct):
        if _skipped(bot, ore):
            continue
        t = ph.readTile(ct, bot.hivemind, ore)
        if not t.ore:
            continue
        d = distTo(ore)
        if t.etype is None:
            ranked.append((d, ore))
        elif t.etype == EntityType.HARVESTER and t.enemy:
            if _withinHijackRange(bot, ore) and not _alreadyTapped(bot, ct, ore):
                ranked.append((d - HIJACK_BONUS, ore))
        elif _sightingStale(bot, ct, ore):
            stale.append(ore)

    ranked.sort(key=lambda item: item[0])
    stale.sort(key=distTo)
    return [ore for _d, ore in ranked], stale


def chooseOre(bot, ct: Controller, distTo, index: int) -> Position | None:
    """Pick out of `rankOre` by `index`, fanning consecutive builders across nodes.

    The spread is `index % min(len, ORE_SPREAD)` and not a claim — see observations.md
    under Known gaps. `respawn` calls this with the index the *next* builder will be
    given, purely to guess where it will head; that guess never reaches the builder.
    """
    ranked, stale = rankOre(bot, ct, distTo)
    if ranked:
        return ranked[index % min(len(ranked), ORE_SPREAD)]
    if stale:
        return stale[index % min(len(stale), ORE_SPREAD)]
    return None


def knownOre(bot, ct: Controller) -> list[Position]:
    """Every ore tile we know about. Filtering is pickOre's job — doing it here
    hid enemy harvesters from the picker entirely, so they were never engaged."""
    return [
        Position(x, y)
        for (x, y), entry in bot.hivemind.local_map.items()
        if entry.get("ore")
    ]


def _skipOre(bot, ore: Position) -> None:
    bot.oreSkip[(ore.x, ore.y)] = bot.hivemind.global_turn


def _skipped(bot, ore: Position) -> bool:
    """Nodes this builder gave up reaching. Expires, because the thing in the way
    is usually another unit or a belt that won't be there forever."""
    when = bot.oreSkip.get((ore.x, ore.y))
    return when is not None and bot.hivemind.global_turn - when <= ORE_SKIP_TURNS


def _oreWorthGoing(bot, ct: Controller, ore: Position) -> bool:
    if _skipped(bot, ore):
        return False
    t = ph.readTile(ct, bot.hivemind, ore)
    if not t.ore:
        return False
    if t.etype is None:
        return True
    if t.etype == EntityType.HARVESTER and t.enemy:
        return _withinHijackRange(bot, ore) and not _alreadyTapped(bot, ct, ore)
    return _sightingStale(bot, ct, ore)


def _enemyHarvester(bot, ct: Controller, ore: Position) -> bool:
    t = ph.readTile(ct, bot.hivemind, ore)
    return t.etype == EntityType.HARVESTER and t.enemy


def _sightingStale(bot, ct: Controller, ore: Position) -> bool:
    if ct.is_in_vision(ore):
        return False  # we can see it right now; memory doesn't come into it
    entry = bot.hivemind.local_map.get((ore.x, ore.y))
    if entry is None:
        return True
    age = bot.hivemind.global_turn - entry.get("last_seen_turn", -(10 ** 9))
    return age > ORE_RECHECK_TURNS


def _alreadyTapped(bot, ct: Controller, ore: Position) -> bool:
    """One of our belts already feeds from this harvester.

    Without this, a successful hijack was invisible to the picker: the tile
    still reads HARVESTER + enemy forever, so it kept winning rankOre's
    HIJACK_BONUS against everything else and the same builder re-called
    hijackRelay on it turn after turn (935 times on one node in one match) —
    each call an immediate, harmless no-op (planRelay's own "already feeding
    a connected belt" check bails before touching anything), but the builder
    never moved on to new ore or a new hijack. Same network-membership check
    `planRelay` already makes before laying anything; costContext is
    round-cached, so asking here costs nothing extra once anything else has
    asked this round, and only the full buildNetwork cost the first time.
    """
    ctx = ph.costContext(bot, ct, "walk")
    return any(ctx.isNetworkMember(n) for n in ph.orthogonalTiles(ore))


def _withinHijackRange(bot, ore: Position) -> bool:
    core = bot.corePos
    if core is None:
        return True
    return ph.manhattanToCore(ore, core) <= HIJACK_MAX_MANHATTAN
