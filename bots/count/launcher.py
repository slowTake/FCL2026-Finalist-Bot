"""Launchers: throw a friendly builder ~5 tiles toward its goal in one action.

A launcher is a turret (r**2 <= 26 throw range, no ammo, no facing) that picks up
an adjacent builder and flings it to a passable tile. One is placed beside our
core by the first raider (the first builder out of the gate), and it exists to
shorten the long outbound walk every builder otherwise makes one tile at a
time — raiders toward the enemy core, eco builders toward their ore.

**Why a builder posts its own target.** A launcher acts on its own turn and can
read neither another unit's role nor its ore claim (both are per-unit Python
state). So the launch is request-driven: a builder standing beside the launcher
writes *where it wants to go* into `store.LAUNCH_REQUEST` and holds still; the
launcher reads that next turn and throws it at the in-range tile nearest that
goal. Role-agnostic, and always aimed at the right place because the body itself
chose it.

**Defence.** Before serving its own team's throw requests the launcher checks
for an enemy builder within reach and flings it back toward the enemy core —
free (no ammo), and a builder thrown five tiles home is five tiles of walking
it has to redo before it can threaten our launcher or economy again. Gated on
`can_launch`, which is the ground truth on whether an enemy body is grabbable.

All launcher logic lives here (placement, the turret loop with its defensive and
request-serving steps, and the builder-side request). `main.py` only wires the
LAUNCHER dispatch and calls `placeCoreLauncher` for the first raider; the role
files each make one `requestLaunch` call. See docs/roadmap.md / store.py for the
slot-3 trade-off.
"""

from __future__ import annotations

from fcode import Controller, EntityType, Position

import debug
import pathfindingHelpers as ph
import raid
import store
from patience import holdTask
from pathing import manhattan

# Below this Manhattan gap a ~5-tile throw buys too little to be worth a builder
# holding a turn for — and, for a raider, avoids flinging it the last few tiles
# straight onto the enemy core's doorstep.
LAUNCH_MIN_GAP = 5

# Turns a builder waits beside a launcher to be thrown before giving up and
# walking. The launch needs one round of store latency, so this must be >= 2.
LAUNCH_WAIT_TURNS = 3

# Turns the first raider will linger by the core to afford / clear cooldown for
# the launcher before abandoning it and leaving to raid.
PLACE_WAIT_TURNS = 6

# The core launcher is only ever planted within this Manhattan distance of the
# core. The first raider qualifies a turn or two after spawning — still in the
# apron — so this is slack, not a tight fit; past it, skip placement entirely.
APRON_RADIUS = 4

# A launcher sitting this close to the *enemy* core is an offensive apron launcher
# (planted by a sieging raider, roles/raidBot.buildApronLauncher), not the core
# launcher beside our own base. It flips runLauncher into the offensive mode:
# fling enemy repair/relay builders *away* from their core instead of ferrying
# our own. Distinct from APRON_RADIUS above, which is the spawn apron around *our*
# core — a launcher there is far outside this radius and keeps the defensive path.
ENEMY_APRON_RADIUS = 3


# ---------------------------------------------------------------------------
# Request packing:  valid(1) | rx(7) | ry(7) | tx(7) | ty(7)  = 29 bits
# ---------------------------------------------------------------------------


def packRequest(requester: Position, target: Position) -> int:
    """Pack (requester, target) into one u32, or 0 if a coord will not fit.

    Same defensive stance as raid.packTarget: a coordinate that overflows its
    field would decode as a plausible tile somewhere else, and a wrong pickup or
    throw is worse than no launch at all, so refuse rather than truncate.
    """
    for p in (requester, target):
        if not (0 <= p.x < 128 and 0 <= p.y < 128):
            return 0
    return (
        (1 << 28)
        | (requester.x << 21)
        | (requester.y << 14)
        | (target.x << 7)
        | target.y
    )


def unpackRequest(val: int):
    """Return (requester, target) or None if nothing is posted / bits are unours."""
    if not (val >> 28) & 1:
        return None
    if val >> 29:
        return None  # bits we never write — not ours, do not guess at it
    requester = Position((val >> 21) & 0x7F, (val >> 14) & 0x7F)
    target = Position((val >> 7) & 0x7F, val & 0x7F)
    return requester, target


# ---------------------------------------------------------------------------
# Placement (builder side, one-off)
# ---------------------------------------------------------------------------


def _launcherSite(bot, ct: Controller) -> Position | None:
    """An orthogonal neighbour to build the launcher on, biased outbound.

    Nearest-to-enemy-core first so the launcher sits on the side outbound
    builders actually pass; can_build_launcher is still the real legality gate.
    """
    pos = ct.get_position()
    enemy = raid.enemyCorePos(bot, ct)
    cands = []
    for n in ph.orthogonalTiles(pos):
        if not ph.inBounds(ct, n):
            continue
        t = ph.readTile(ct, bot.hivemind, n)
        if t.wall or t.ore or t.builder or t.etype is not None:
            continue
        cands.append(n)
    if enemy is not None:
        cands.sort(key=lambda p: ph.manhattanToCore(p, enemy))
    return cands[0] if cands else None


def placeCoreLauncher(bot, ct: Controller) -> bool:
    """One-off: the first raider plants a launcher beside the core, then raids.

    Returns True while the builder should stay on this task (placed it, or
    waiting out a cooldown / titanium to place it); False once there is nothing
    to do here — it already placed one, it's too far from the core to plant the
    core launcher, or patience ran out — and the caller falls through to raiding.

    One-shot via a per-unit flag (`bot._placedLauncher`), not vision: the Player
    instance persists across this unit's turns, so once it has built its launcher
    it never builds another even after it walks out of sight of the first. A
    launcher lost to enemy fire is not rebuilt — accepted for now (see
    docs/roadmap.md); the alternative, re-detecting by vision, double-planted a
    launcher every turn the raider drifted past its own.
    """
    if bot.corePos is None:
        return False
    if getattr(bot, "_placedLauncher", False):
        return False
    # Only ever plant the *core* launcher near the core. If this body only became
    # the raider deep in the map, a launcher out there helps nobody — skip it.
    if ph.manhattanToCore(ct.get_position(), bot.corePos) > APRON_RADIUS:
        return False

    site = _launcherSite(bot, ct)
    if site is None:
        return False  # boxed in; raid instead

    if ct.get_action_cooldown() != 0:
        return holdTask(bot, "placeLauncher", PLACE_WAIT_TURNS)
    if ct.get_global_resources() < ct.get_launcher_cost():
        return holdTask(bot, "placeLauncher", PLACE_WAIT_TURNS)

    if ct.can_build_launcher(site):
        ct.build_launcher(site)
        ph.invalidateTile(bot.hivemind, site)
        bot._placedLauncher = True
        if debug.ENABLED:
            debug.log(
                "launcher",
                f"placed at {site} by idx={bot.hivemind.my_index} "
                f"core={bot.corePos} enemy={raid.enemyCorePos(bot, ct)} "
                f"rnd={ct.get_current_round()}",
            )
        return True
    # Legal-looking tile the engine still refuses (unit cap, a builder that
    # stepped on since the read): wait a beat, it usually clears.
    return holdTask(bot, "placeLauncher", PLACE_WAIT_TURNS)


# ---------------------------------------------------------------------------
# Request (builder side, every role)
# ---------------------------------------------------------------------------


def _adjacentAllyLauncher(bot, ct: Controller) -> Position | None:
    """Nearest friendly launcher in this builder's 8-neighbourhood, or None.

    Eight-way rather than orthogonal: pickup adjacency may include diagonals, and
    can_launch on the launcher side is the ground-truth gate anyway.
    """
    for tile in ph.adjacentTiles(ct.get_position()):
        t = ph.readTile(ct, bot.hivemind, tile)
        if t.etype == EntityType.LAUNCHER and not t.enemy:
            return tile
    return None


def requestLaunch(bot, ct: Controller, target: Position | None) -> bool:
    """Ask an adjacent launcher to throw this builder toward `target`.

    True  → the builder should hold this turn (a request is posted and being
            waited on); the launcher throws it next turn.
    False → no launcher adjacent, the throw wouldn't help, or patience ran out;
            the caller should keep walking.

    The request carries this builder's own position and goal, so the launcher
    needs to know nothing about roles. Holds only while a launch is plausibly
    coming — holdTask caps the wait so a launcher that never serves this body
    (busy with another, or the target unreachable) doesn't strand it.
    """
    if target is None:
        return False
    pos = ct.get_position()
    if manhattan(pos, target) <= LAUNCH_MIN_GAP:
        return False
    if _adjacentAllyLauncher(bot, ct) is None:
        return False

    store.put(ct, store.LAUNCH_REQUEST, packRequest(pos, target))
    return holdTask(bot, "awaitLaunch", LAUNCH_WAIT_TURNS)


# ---------------------------------------------------------------------------
# Defence
# ---------------------------------------------------------------------------


def _grabbableHostiles(bot, ct: Controller) -> list[Position]:
    """Nearby enemy builder bots, nearest to this launcher first.

    Nearest first because that is both the most urgent threat and the one most
    likely to be inside pickup range; `can_launch` on the caller's side is the
    real gate on whether a body can actually be picked up, so out-of-reach
    candidates simply fail there and the caller's loop moves on. Shared by the
    defensive (throw toward enemy core) and offensive (throw away from it) repels.
    """
    me = ct.get_team()
    launcherPos = ct.get_position()
    hostiles = [
        ct.get_position(eid)
        for eid in ct.get_nearby_entities()
        if ct.get_team(eid) != me
        and ct.get_entity_type(eid) == EntityType.BUILDER_BOT
    ]
    hostiles.sort(key=lambda p: manhattan(launcherPos, p))
    return hostiles


def _repelHostile(bot, ct: Controller) -> bool:
    """Fling the nearest grabbable enemy builder back toward the enemy core.

    The throw goal is the enemy core, so `_bestThrow` sends the body toward its
    own side — away from our launcher and economy. This is the *defensive* repel,
    for the launcher beside our own core.
    """
    enemyCore = raid.enemyCorePos(bot, ct)
    if enemyCore is None:
        return False

    for hp in _grabbableHostiles(bot, ct):
        target = _bestThrow(bot, ct, hp, enemyCore)
        if target is None:
            continue
        if ct.can_launch(hp, target):
            ct.launch(hp, target)
            if debug.ENABLED:
                debug.log(
                    "launcher",
                    f"repelled {hp} -> {target} (enemyCore {enemyCore}) "
                    f"rnd={ct.get_current_round()}",
                )
            return True
    return False


def _repelAwayFromCore(bot, ct: Controller, enemyCore: Position) -> bool:
    """Offensive apron repel: fling the nearest grabbable enemy builder *away* from
    its own core, so a repair/relay body lands several tiles out and has to walk
    all the way back before it can touch the core again.

    Mirror of `_repelHostile` but with the throw inverted — `_flingAwayThrow`
    maximises distance from the enemy core instead of minimising it. Used only by
    an apron launcher (within ENEMY_APRON_RADIUS of the enemy core).
    """
    for hp in _grabbableHostiles(bot, ct):
        target = _flingAwayThrow(bot, ct, hp, enemyCore)
        if target is None:
            continue
        if ct.can_launch(hp, target):
            ct.launch(hp, target)
            if debug.ENABLED:
                debug.log(
                    "launcher",
                    f"repelled {hp} -> {target} away from core {enemyCore} "
                    f"rnd={ct.get_current_round()}",
                )
            return True
    return False


# ---------------------------------------------------------------------------
# The launcher turret loop
# ---------------------------------------------------------------------------


def _bestThrow(bot, ct: Controller, requester: Position, target: Position):
    """The in-range tile that lands the builder closest to `target`.

    Only tiles strictly closer to the goal than the requester currently is are
    considered, so a launch is never wasted throwing a builder sideways or back.
    can_launch is the real legality check; this only ranks candidates.
    """
    try:
        tiles = ct.get_attackable_tiles()
    except Exception:
        return None

    best = None
    bestD = manhattan(requester, target)
    for tile in tiles:
        if not ph.inBounds(ct, tile):
            continue
        if ph.isImpassable(ct, bot.hivemind, tile):
            continue
        t = ph.readTile(ct, bot.hivemind, tile)
        if t.etype is not None or t.builder or t.ore:
            continue  # must be a clear passable tile to land on
        d = manhattan(tile, target)
        if d < bestD:
            bestD = d
            best = tile
    return best


def _flingAwayThrow(bot, ct: Controller, requester: Position, enemyCore: Position):
    """The in-range tile that lands the builder *farthest* from the enemy core.

    The inverse of `_bestThrow`: only tiles strictly farther from the core than the
    requester currently is are considered, so a fling never drops the body closer to
    the thing we're denying it. can_launch is the real legality check; this only
    ranks candidates. Distance is the point-to-2x2 clamp `manhattanToCore`, so a
    landing tile is scored against the whole core body, not one anchor corner.
    """
    try:
        tiles = ct.get_attackable_tiles()
    except Exception:
        return None

    best = None
    bestD = ph.manhattanToCore(requester, enemyCore)
    for tile in tiles:
        if not ph.inBounds(ct, tile):
            continue
        if ph.isImpassable(ct, bot.hivemind, tile):
            continue
        t = ph.readTile(ct, bot.hivemind, tile)
        if t.etype is not None or t.builder or t.ore:
            continue  # must be a clear passable tile to land on
        d = ph.manhattanToCore(tile, enemyCore)
        if d > bestD:
            bestD = d
            best = tile
    return best


def runLauncher(bot, ct: Controller) -> None:
    """One action per turn: defend first, then serve a posted launch request.

    Defence outranks ferrying our own builders — an enemy body at the door can
    destroy the launcher, and there's only one throw to spend. With no hostile
    and no request there is nothing to aim, so the launcher idles rather than
    guessing where an adjacent builder wants to go.
    """
    if ct.get_action_cooldown() != 0:
        return

    # An apron launcher (planted at the enemy core by a sieging raider) is a pure
    # denial turret: it only ever flings enemy repair/relay builders *away* from
    # their core, and never ferries our own — no raider requests a throw this deep
    # in enemy ground. Told apart from the core launcher by geometry alone (it sits
    # within ENEMY_APRON_RADIUS of the enemy core; the core launcher is far outside
    # it), so no per-unit state or store slot is needed.
    enemyCore = raid.enemyCorePos(bot, ct)
    if (
        enemyCore is not None
        and ph.manhattanToCore(ct.get_position(), enemyCore) <= ENEMY_APRON_RADIUS
    ):
        _repelAwayFromCore(bot, ct, enemyCore)
        return

    if _repelHostile(bot, ct):
        return

    req = unpackRequest(ct.read_store(store.LAUNCH_REQUEST))
    if req is None:
        return
    requester, target = req

    # Consume the request on read, before deciding whether to serve it. A builder
    # genuinely waiting re-posts every turn it holds, so clearing here never strands
    # it; but a request whose author has since died, been shoved, or walked off when
    # its patience expired would otherwise persist in the slot and fling whoever
    # next wanders onto that tile toward a now-stale target. Consuming it bounds any
    # request to the single launcher turn after it was posted.
    store.put(ct, store.LAUNCH_REQUEST, 0)

    # The requester must still be a friendly builder sitting where it said it
    # was — it holds for exactly this, but it may have died or been shoved.
    t = ph.readTile(ct, bot.hivemind, requester)
    if not t.builder or t.builderEnemy:
        return

    best = _bestThrow(bot, ct, requester, target)
    if best is None:
        return

    if ct.can_launch(requester, best):
        ct.launch(requester, best)
        if debug.ENABLED:
            debug.log(
                "launcher",
                f"threw {requester} -> {best} (goal {target}) "
                f"rnd={ct.get_current_round()}",
            )
