"""Builder economy role. Orchestrator only — mechanics live in the modules it calls."""

from __future__ import annotations

from fcode import Controller, EntityType, Position

import debug
import heal
import launcher
import pathfindingHelpers as ph
from movement import exploreStep, walkTo
from patience import BLOCKED_WAIT_TURNS, clearHold, holdTask, waitTi
import store
from relays import hijackRelay, relayToHome

# How many of the nearest ore nodes builders fan out across. Deterministic off
# my_index, so two builders with different indices rarely pick the same node.
# Not a claim — see observations.md.
ORE_SPREAD = 4

# Manhattan is blind to walls: a node behind one reads as near as an open node at
# the same beeline distance, but the walk around the wall is longer. Add an estimate
# of that detour back — each wall tile on the straight line to a node costs a builder
# roughly this many extra steps to round. A cheap proxy for a true path cost (too
# expensive to run per node inside the 10ms budget); it only has to rank walled-off
# nodes below clear ones, not price them exactly. Tuned on mirror_gauntlet; 0 restores
# the old pure-Manhattan behaviour.
DETOUR_PER_WALL = 2

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

# Manhattan radius from our core within which an idle builder will clear an enemy
# barrier that has severed one of our supply lines and re-lay the belt. Bounded to
# "near the core" on purpose: this is a defensive bandage for the enemy's
# core-siege (mirror of raidBot._siege), not a map-wide relay-repair system.
CORE_DEFENSE_RADIUS = 8


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

    # Only once nothing above claimed the turn — relaying, seizing, and
    # building are all commitments in progress and stay protected. Heal
    # ahead of seekOre, not after: seekOre almost always returns True (walk
    # or hold), so a check placed later would rarely get a turn to run.
    if heal.healNearby(bot, ct, heal.ECO_HEAL_TYPES):
        return

    # Home defence before repair or new ore: shoot an attacker closing on the core
    # rather than only patch the belt it cut. Function-level import — a module-level
    # `import guns` cycles ecoBot -> guns -> respawn -> ecoBot (respawn imports
    # chooseOre from here). Note emplace.py does NOT fix this one: it broke the
    # guns<->raid edges by moving *emplacement mechanism* out, but this edge is
    # `guns -> respawn` for MAX_BUILDERS in topUpAmmo, which is unrelated. Closing
    # it means moving that constant somewhere neutral, or the ore.py split that
    # takes chooseOre out of a role file (roadmap.md, 2026-08-15 item 3).
    import guns
    if guns.defendCore(bot, ct):
        return

    # Restore a supply line the enemy cut near our core (barrier over a belt tile)
    # before seeking new ore — a severed line loses a whole harvester's output,
    # worth more than another node. Below build so it never interrupts one.
    if repairCoreBelts(bot, ct):
        return

    if seekOre(bot, ct):
        return

    exploreStep(bot, ct)


# ---------------------------------------------------------------------------
# Core-line repair — clear enemy barriers that sever our supply near the core
# ---------------------------------------------------------------------------


def _hasFeeder(bot, ct: Controller, tile: Position, network_side: Position) -> bool:
    """Is `tile` fed by one of ours that would resume delivering once it carries a
    belt into `network_side`.

    A feeder is an ally harvester (delivers to any adjacent building), or an ally
    belt whose facing points at `tile` — which, because its output currently runs
    into `tile` and `tile` is not a belt, is by definition disconnected from the
    core. Requiring a real feeder is what stops us grinding a barrier that isn't
    actually cutting anything.
    """
    for f in ph.orthogonalTiles(tile):
        if (f.x, f.y) == (network_side.x, network_side.y):
            continue
        t = ph.readTile(ct, bot.hivemind, f)
        if t.enemy:
            continue
        if t.etype == EntityType.HARVESTER:
            return True
        if (
            t.etype in ph.BELT_TYPES
            and t.facing is not None
            and t.facing == ph.beltFacing(f, tile)
        ):
            return True
    return False


def _grindBarrier(bot, ct: Controller, tile: Position, waitReason: str, fireReason: str):
    """One turn of grinding an enemy barrier down (2 Ti / 2 dmg a shot).

    Returns `(fired, keepTask)`:

    * `fired` — the shot actually went off this turn. Only then may a caller
      commit state off the back of it (e.g. latch a multi-turn grind target); a
      turn we merely held must not.
    * `keepTask` — what the caller should return: True to stay on the task
      (we fired, or we're still inside the patience budget), False once patience
      is spent and the caller may give up. This is `holdTask`'s own contract,
      passed straight through.

    Includes the cooldown branch even where a call site already returns early on a
    busy cooldown: harmless there, and it stops the fire/hold ladder from only
    working from one specific place.
    """
    if ct.get_action_cooldown() != 0:
        return False, holdTask(bot, waitReason, BLOCKED_WAIT_TURNS)
    if not ct.can_fire(tile):
        return False, holdTask(bot, fireReason, BLOCKED_WAIT_TURNS)
    ct.fire(tile)
    return True, True


def _nearestBarrier(bot, ct: Controller, ctx):
    """Nearest (barrier_tile, network_side) near the core where the enemy has cut a
    line: an enemy barrier adjacent to a still-connected network member, with a
    disconnected feeder on another side. None if no line is barriered.

    Only ever an enemy *barrier* — never a plain empty gap. A gap near the core is
    indistinguishable from a normal relay still being built (relays lay from the
    harvester end first, so the core end is the last gap), and treating those as
    severances made idle builders fight live relays and churn belts. An enemy
    barrier is unambiguous: a healthy line never has one on it.
    """
    core = bot.corePos
    pos = ct.get_position()
    best = None
    bestD = None
    for (nx, ny) in ctx.network:
        N = Position(nx, ny)
        if ph.manhattanToCore(N, core) > CORE_DEFENSE_RADIUS:
            continue
        for T in ph.orthogonalTiles(N):
            if (T.x, T.y) in ctx.network or not ph.inBounds(ct, T):
                continue
            if ph.manhattanToCore(T, core) > CORE_DEFENSE_RADIUS:
                continue
            t = ph.readTile(ct, bot.hivemind, T)
            if not (t.etype == EntityType.BARRIER and t.enemy):
                continue
            if not _hasFeeder(bot, ct, T, N):
                continue
            d = abs(pos.x - T.x) + abs(pos.y - T.y)
            if bestD is None or d < bestD:
                best, bestD = (T, N), d
    return best


def _clearRepair(bot) -> None:
    bot._repairTile = None
    bot._repairFace = None


def repairCoreBelts(bot, ct: Controller) -> bool:
    """Clear an enemy barrier severing a supply line near our core, then re-lay the
    belt back into the network. True while committed to a repair (walking, grinding
    or re-laying); False when there's no barriered line to fix.

    Commits to one barrier via `bot._repairTile` and sees it through in
    `_finishRepair` — so the belt goes back the instant the barrier falls, and the
    now-empty tile isn't re-scanned as a fresh target."""
    if bot.corePos is None:
        return False

    if bot._repairTile is not None:
        return _finishRepair(bot, ct)

    ctx = ph.costContext(bot, ct, "relay")
    found = _nearestBarrier(bot, ct, ctx)
    if found is None:
        return False
    tile, network_side = found

    pos = ct.get_position()
    if abs(pos.x - tile.x) + abs(pos.y - tile.y) != 1:
        def beside(p: Position) -> bool:
            return abs(p.x - tile.x) + abs(p.y - tile.y) == 1

        if walkTo(bot, ct, beside, tile, ("repair", tile.x, tile.y)):
            return True
        return holdTask(bot, "repairApproach", BLOCKED_WAIT_TURNS)

    fired, keepTask = _grindBarrier(bot, ct, tile, "repairWait", "repairFire")
    if not fired:
        return keepTask

    bot._repairTile = tile          # commit: grind it down, then re-lay the belt
    bot._repairFace = network_side
    if debug.ENABLED:
        debug.log("repair", f"grind barrier {tile} rnd={ct.get_current_round()}")
    return True


def _finishRepair(bot, ct: Controller) -> bool:
    """Drive a committed repair: keep grinding the barrier, then lay the belt back
    into the network once it's gone. Clears the commitment on completion, on being
    displaced with no way back, or if the tile becomes something we don't handle."""
    tile, network_side = bot._repairTile, bot._repairFace
    if not ph.inBounds(ct, tile):
        _clearRepair(bot)
        return False

    pos = ct.get_position()
    if abs(pos.x - tile.x) + abs(pos.y - tile.y) != 1:
        def beside(p: Position) -> bool:
            return abs(p.x - tile.x) + abs(p.y - tile.y) == 1

        if walkTo(bot, ct, beside, tile, ("repair", tile.x, tile.y)):
            return True
        _clearRepair(bot)  # shoved off and can't get back — drop it
        return False

    t = ph.readTile(ct, bot.hivemind, tile)

    if t.etype == EntityType.BARRIER and t.enemy:
        return _grindBarrier(bot, ct, tile, "repairWait", "repairFire")[1]

    if t.etype is None and not t.wall and not t.ore:
        facing = ph.beltFacing(tile, network_side)
        if ct.get_action_cooldown() != 0:
            return holdTask(bot, "repairWait", BLOCKED_WAIT_TURNS)
        if facing is not None and ct.can_build_conveyor(tile, facing):
            ct.build_conveyor(tile, facing)
            ph.invalidateTile(bot.hivemind, tile)
            _clearRepair(bot)
            if debug.ENABLED:
                debug.log(
                    "repair",
                    f"restore belt {tile}->{network_side} rnd={ct.get_current_round()}",
                )
            return True
        return holdTask(bot, "repairBuild", BLOCKED_WAIT_TURNS)

    _clearRepair(bot)  # already filled (ally belt), or the enemy replaced it — done
    return False


def buildHarvester(bot, ct: Controller) -> bool:
    """Plant a harvester on the claimed ore, then commit to relaying it home.

    If the node is blocked when we arrive, deal with the blocker instead of just
    dropping the claim: tap an enemy harvester sitting on it, or grind down an
    enemy barrier denying it and build once the tile is clear.
    """
    ore = bot.oreTarget
    if ore is None or ct.get_action_cooldown() != 0:
        return False

    pos = ct.get_position()
    if abs(pos.x - ore.x) + abs(pos.y - ore.y) != 1:
        return False  # builds are orthogonal-only; diagonal isn't close enough

    # Blocked by an enemy? Handle it before the harvester-Ti gate: an enemy
    # harvester wants tapping (not building), and a barrier is far cheaper to
    # grind than the harvester we'd otherwise be saving up for.
    t = ph.readTile(ct, bot.hivemind, ore)
    if t.etype == EntityType.HARVESTER and t.enemy:
        # seizeHarvester normally catches this a step earlier in runEcoBot; kept
        # here so the build path itself never just abandons a tappable node.
        bot.oreTarget = None
        return hijackRelay(bot, ct, ore)
    if t.etype == EntityType.BARRIER and t.enemy:
        # Grind it down. Keep the claim: once it falls, the tile reads clear next
        # pass and we build as normal.
        fired, keepTask = _grindBarrier(
            bot, ct, ore, "clearOreBarrier", "clearOreBarrier"
        )
        if fired and debug.ENABLED:
            debug.log("ore", f"clear barrier on ore {ore} rnd={ct.get_current_round()}")
        return keepTask

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

    # Beside the core launcher with the ore still a walk away? Get flung toward
    # it. Held turns fall through to walkTo once patience runs out.
    if launcher.requestLaunch(bot, ct, ore):
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


def _detourPenalty(ct: Controller, hm, origin: Position, ore: Position) -> int:
    """Extra steps a builder likely pays routing around walls between it and `ore`.

    Walks the straight 4-connected line origin->ore and counts tiles a path can't
    cross (walls, out-of-bounds, ally harvesters/cores — whatever `isImpassable`
    forbids). Deliberately ignores enemy buildings: those are priced-not-forbidden
    and transient, so they belong to A* and `_skipOre`, not to a static-geometry
    proxy. The node tile itself is skipped — it is ore, not an obstacle.

    Axis choice is magnitude-only, so the walk (and the penalty) is invariant under
    the map reflections and 180deg rotations symmetry rests on. `readTile` is
    round-memoised, so tiles shared by several candidates are read once.
    """
    x, y = origin.x, origin.y
    blocked = 0
    while (x, y) != (ore.x, ore.y):
        dx, dy = ore.x - x, ore.y - y
        if abs(dx) >= abs(dy):
            x += 1 if dx > 0 else -1
        else:
            y += 1 if dy > 0 else -1
        if (x, y) == (ore.x, ore.y):
            break
        if ph.isImpassable(ct, hm, Position(x, y)):
            blocked += 1
    return blocked * DETOUR_PER_WALL


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
    hm = bot.hivemind

    # distTo is built once per call; the wall-detour term inside it is the only
    # per-ore work, and it reads memoised tiles, so it stays inside the 10ms budget.
    def distTo(ore: Position) -> int:
        base = abs(ore.x - pos.x) + abs(ore.y - pos.y)
        return base + _detourPenalty(ct, hm, pos, ore)

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
