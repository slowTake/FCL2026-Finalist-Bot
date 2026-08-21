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
from patience import clearHold, holdTask
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

# A launched *friendly* builder must be able to walk back out of where it lands.
# Some maps (royale) have fully walled rooms whose interior tiles read
# passable-and-clear but are reachable only by being thrown in over the wall — a
# permanent trap. A landing tile counts as escapable only if the walkable area
# reachable from it is at least this big: open ground clears it in a ring or two,
# and the largest sealed royale pocket (its 12-tile centre room) never does.
# Tunable — anything in (12, ~25] separates trap from open there. Applied only to
# our own builders; enemies are still flung into such rooms on purpose (a trap for
# them is a win). See _escapable / _bestThrow(requireEscapable=True).
ESCAPE_MIN_TILES = 20

# --- Pocket self-rescue (rescueFromPocket) ---------------------------------
# A builder can end up sealed in a walled pocket it walked into (or that closed
# behind it as both sides built): every role funnels through walkTo, no route out
# exists, and it stands there bleeding turns until the match ends. As a last
# resort it buys a launcher on an adjacent tile, is thrown out over the wall, and
# the launcher — useless where it sits, deep in a one-body pocket — self-detonates
# after the throw.

# Turns a builder must read as pocketed before it spends a launcher on escape.
# Long enough that every *transient* enclosure resolves on its own first — an ally
# body stepping off (BLOCKED_WAIT_TURNS), an enemy barrier the role code is already
# grinding (a 30 HP barrier is ~15 builder-turns, deliberately the same scale), a
# walkAvoid detour reopening — so only a genuine seal survives the count. Short
# next to the hundreds of rounds a sealed builder would otherwise waste.
POCKET_STUCK_TURNS = 15

# Strict lower bound on the Ti bank before a rescue is affordable (checked with
# `>`). A rescue spends a whole launcher to recover one body, so the floor sits
# well above the launcher cost: it must never starve the opening economy or a
# turret the guardian is waiting on just to un-wedge a single stuck builder.
RESCUE_TI_MIN = 80

# Turns the pocketed builder re-posts its rescue request before giving up and
# resetting the cycle. Must clear LAUNCH_WAIT_TURNS' store latency *plus* the
# freshly-built launcher's spawn action cooldown; generous because the launcher is
# already paid for and a sealed builder has nowhere better to be. On expiry the
# counter resets and a later retry re-adopts the standing launcher rather than
# buying a second.
RESCUE_WAIT_TURNS = 10


# ---------------------------------------------------------------------------
# Request packing:  rescue(1) | valid(1) | rx(7) | ry(7) | tx(7) | ty(7)  = 30 bits
# ---------------------------------------------------------------------------


def packRequest(requester: Position, target: Position, *, rescue: bool = False) -> int:
    """Pack (requester, target) into one u32, or 0 if a coord will not fit.

    Same defensive stance as raid.packTarget: a coordinate that overflows its
    field would decode as a plausible tile somewhere else, and a wrong pickup or
    throw is worse than no launch at all, so refuse rather than truncate.

    `rescue` rides in bit 29 (previously "bits we never write"): a pocket
    self-rescue throw is served only by the launcher sitting adjacent inside the
    pocket, never by the core launcher, so the two readers of this single slot can
    tell whose request it is (see runLauncher).
    """
    for p in (requester, target):
        if not (0 <= p.x < 128 and 0 <= p.y < 128):
            return 0
    return (
        (1 << 29 if rescue else 0)
        | (1 << 28)
        | (requester.x << 21)
        | (requester.y << 14)
        | (target.x << 7)
        | target.y
    )


def unpackRequest(val: int):
    """Return (requester, target, rescue) or None if nothing is posted / bits are unours."""
    if not (val >> 28) & 1:
        return None
    if val >> 30:
        return None  # bits we never write — not ours, do not guess at it
    rescue = bool((val >> 29) & 1)
    requester = Position((val >> 21) & 0x7F, (val >> 14) & 0x7F)
    target = Position((val >> 7) & 0x7F, val & 0x7F)
    return requester, target, rescue


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
# Pocket self-rescue (builder side, every role, last resort)
# ---------------------------------------------------------------------------


def rescueFromPocket(bot, ct: Controller) -> bool:
    """Last-resort escape for a builder sealed in a walled pocket. Returns True if
    it used the turn (caller must return without running its role), False otherwise.

    Reuses `_escapable` — the same bounded flood the launcher uses to reject a
    trap landing tile — run from the builder's *own* tile: if fewer than
    ESCAPE_MIN_TILES are reachable, it is boxed in. Every role funnels through
    walkTo, so a boxed builder can do nothing any role asks; this check sits above
    role dispatch and covers raiders, guardian and eco alike.

    Turn by turn:
      T1..T15  pocketed → `_pocketStuckTurns` climbs; any escapable turn resets it.
               The role still runs those turns (its no-route memo bounds the waste).
      T15      with the Ti gate met, adopt an adjacent friendly launcher if one is
               already there, else buy one on a clear orthogonal neighbour.
      T16+     hold still and re-post a rescue-flagged request every turn; the
               adjacent launcher throws us out (runLauncher's rescue branch) and
               then self-detonates.
      landing  the flood reads escapable again → clear state, fall through to role.

    Uses its own int counter, not patience.holdTask, for the same reason
    guns._defendStuckTurns does (main.py): the pocketed builder's role keeps calling
    walkTo every turn, and walkTo clears the shared hold on arrival — which would
    silently make this patience infinite.

    Two builders pocketed at once share the single request slot (last writer wins);
    bounded by RESCUE_WAIT_TURNS patience and retry, and accepted as rare. A fogged
    pocket wall reads open, so an unseen seal never triggers — consistent with
    `_escapable`'s own degradation note.
    """
    if bot.corePos is None:
        return False
    pos = ct.get_position()
    pocketed = not _escapable(bot, ct, pos)

    # A rescue is already under way: a launcher is standing by for us.
    if bot._rescueSite is not None:
        if not pocketed:
            # Thrown clear (or the wall opened). Drop the rescue and let the role run.
            # clearHold too: the success path is the one exit that doesn't route
            # through walkTo (which clears it), so a leftover "awaitRescue" count
            # would otherwise shorten a *later* rescue's wait window for this unit.
            bot._rescueSite = None
            bot._pocketStuckTurns = 0
            clearHold(bot)
            return False
        # Check patience *before* posting, and clear the slot when it's spent. The
        # rescue request is served only by the adjacent launcher (which consumes it
        # first thing); but if that launcher is destroyed mid-wait, a request left in
        # the slot on the give-up turn would dangle forever — no other launcher
        # consumes a rescue flag — and could later fling an innocent builder that
        # wanders onto the recorded tile. So on give-up we un-post our own request.
        if not holdTask(bot, "awaitRescue", RESCUE_WAIT_TURNS):
            # Throw never came (launcher shot, or no escapable landing tile in reach).
            # Reset and let a later cycle re-adopt the standing launcher for free.
            store.put(ct, store.LAUNCH_REQUEST, 0)
            if debug.ENABLED:
                debug.log(
                    "launcher",
                    f"rescue gave up on {pos} idx={bot.hivemind.my_index} "
                    f"rnd={ct.get_current_round()}",
                )
            bot._rescueSite = None
            bot._pocketStuckTurns = 0
            return False
        # Post straight to the slot, *not* via requestLaunch: its LAUNCH_MIN_GAP
        # gate would refuse a short throw and its enemy-apron guard would refuse a
        # pocket that happens to sit near the enemy core — both wrong here, and the
        # rescue branch in runLauncher deliberately runs before the apron branch to
        # serve exactly this case. Target is the core purely as a direction bias;
        # requireEscapable already guarantees the landing tile is outside the pocket.
        store.put(ct, store.LAUNCH_REQUEST, packRequest(pos, bot.corePos, rescue=True))
        return True

    if not pocketed:
        bot._pocketStuckTurns = 0
        return False

    bot._pocketStuckTurns += 1
    if bot._pocketStuckTurns < POCKET_STUCK_TURNS:
        return False
    # Sealed long enough. Gate on the bank *after* the count, so the counter keeps
    # accruing while Ti is short and the rescue fires the moment it can afford one.
    if ct.get_global_resources() <= RESCUE_TI_MIN:
        return False

    existing = _adjacentAllyLauncher(bot, ct)
    if existing is not None:
        # A launcher is already beside us (a leftover, or one built for this) — no
        # need to buy another; start requesting next turn.
        bot._rescueSite = existing
        return True

    site = _launcherSite(bot, ct)
    if site is None:
        return False  # solid single-tile pocket, no neighbour to build on — unrescuable
    if ct.get_action_cooldown() != 0 or ct.get_global_resources() < ct.get_launcher_cost():
        return holdTask(bot, "rescueBuild", PLACE_WAIT_TURNS)
    if ct.can_build_launcher(site):
        ct.build_launcher(site)
        ph.invalidateTile(bot.hivemind, site)
        bot._rescueSite = site
        if debug.ENABLED:
            debug.log(
                "launcher",
                f"rescue launcher at {site} for pocketed {pos} "
                f"idx={bot.hivemind.my_index} rnd={ct.get_current_round()}",
            )
        return True
    # Legal-looking tile the engine still refuses: wait a beat, it usually clears.
    return holdTask(bot, "rescueBuild", PLACE_WAIT_TURNS)


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
    launcher = _adjacentAllyLauncher(bot, ct)
    if launcher is None:
        return False

    # An apron launcher (planted at the enemy core, within ENEMY_APRON_RADIUS of it)
    # is a pure denial turret: runLauncher takes the away-repel branch and returns
    # before it ever reads LAUNCH_REQUEST, so a request posted to it can never be
    # served. Posting anyway strands us on a hold for a throw that isn't coming, and
    # overwrites a genuinely-waiting builder's request in the single shared slot.
    # Same geometry test runLauncher uses to pick that branch — applied to the
    # launcher's tile, not ours, since that is the body whose behaviour we predict.
    enemyCore = raid.enemyCorePos(bot, ct)
    if enemyCore is not None and ph.manhattanToCore(launcher, enemyCore) <= ENEMY_APRON_RADIUS:
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


def _escapable(bot, ct: Controller, tile: Position) -> bool:
    """Can a builder dropped on `tile` walk back out — i.e. is `tile` in a walkable
    area of at least ESCAPE_MIN_TILES, rather than a fully walled-off pocket.

    Bounded flood over walkable tiles (`not ph.isImpassable`, which already forbids
    walls, cores and ally harvesters). Stops the instant the reachable set hits the
    threshold, so it visits at most ESCAPE_MIN_TILES tiles — cheap, and the launcher
    has spare budget. An out-of-vision tile reads wall=False, so an unseen region
    floods as open; the pocket walls sit beside our core and are in the ledger by
    the time a launcher fires, so a real trap is still caught, and the unknown case
    degrades to the old behaviour rather than regressing.
    """
    hm = bot.hivemind
    seen = {(tile.x, tile.y)}
    stack = [tile]
    while stack:
        if len(seen) >= ESCAPE_MIN_TILES:
            return True
        cur = stack.pop()
        for n in ph.orthogonalTiles(cur):
            key = (n.x, n.y)
            if key in seen or ph.isImpassable(ct, hm, n):
                continue
            seen.add(key)
            stack.append(n)
    return len(seen) >= ESCAPE_MIN_TILES


def _bestThrow(
    bot,
    ct: Controller,
    requester: Position,
    target: Position,
    *,
    requireEscapable: bool = False,
    mustImprove: bool = True,
):
    """The in-range tile that lands the builder closest to `target`.

    Only tiles strictly closer to the goal than the requester currently is are
    considered, so a launch is never wasted throwing a builder sideways or back.
    can_launch is the real legality check; this only ranks candidates.

    `requireEscapable` (friendly throws only) rejects a landing tile inside a fully
    walled-off pocket the builder could never walk out of. The flood is run only on
    a strictly-improving candidate, so at most a handful per throw. Left off for the
    enemy-repel callers on purpose: throwing an enemy into such a pocket traps it.

    `mustImprove=False` (pocket rescue only) drops the strictly-closer bound: any
    escapable landing tile in reach will do, because *out of the pocket* is the whole
    point even when every exit faces away from `target`. Pair it with
    requireEscapable so the relaxed pass can't land the body straight back into
    another trap.
    """
    try:
        tiles = ct.get_attackable_tiles()
    except Exception:
        return None

    best = None
    # A large sentinel when we don't require improvement, so the first escapable
    # candidate wins and every closer one after it still improves on that.
    bestD = manhattan(requester, target) if mustImprove else (1 << 30)
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
            if requireEscapable and not _escapable(bot, ct, tile):
                continue
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


def _chebyshev(a: Position, b: Position) -> int:
    """8-neighbour distance — 0/1 means grabbable-adjacent (the pickup ring)."""
    return max(abs(a.x - b.x), abs(a.y - b.y))


def _serveRescue(bot, ct: Controller, requester: Position, target: Position) -> None:
    """Throw a pocketed builder out and arm self-detonation. Only ever called on the
    launcher adjacent to the requester (runLauncher's Chebyshev-1 gate).

    Consumes the slot first — this is the one launcher meant to serve the flagged
    request, so no other reader should. The requester must still be a friendly
    builder where it said it was (it may have died or been shoved); if it's gone we
    make no throw and leave the launcher un-armed, so it stays available for the next
    body that gets pocketed here and adopts it (rescueFromPocket's `existing` path)
    rather than detonating an unused turret.

    Detonation is armed (deferred one turn — `launch` and `self_destruct` never share
    a turn) only when the throw fired *and* this launcher's own tile is itself
    pocketed. The self-tile gate is the same idea as the ENEMY_APRON_RADIUS geometry
    test: a rescue launcher is planted on a clear neighbour of a pocketed builder, so
    it sits inside the same sealed region and reads pocketed; it protects the corner
    case of a legitimate, escapable-area launcher that got adopted for a throw — that
    one serves the rescue but survives to keep ferrying.
    """
    store.put(ct, store.LAUNCH_REQUEST, 0)
    t = ph.readTile(ct, bot.hivemind, requester)
    if not t.builder or t.builderEnemy:
        return

    # requireEscapable keeps the body out of a fresh trap; the mustImprove=False
    # fallback covers a pocket whose only exit faces away from the core (the target
    # is a direction bias, not a hard goal — see rescueFromPocket).
    best = _bestThrow(bot, ct, requester, target, requireEscapable=True)
    if best is None:
        best = _bestThrow(
            bot, ct, requester, target, requireEscapable=True, mustImprove=False
        )
    if best is None:
        return

    if ct.can_launch(requester, best):
        ct.launch(requester, best)
        # Arm detonation only if this launcher's own tile is itself sealed. A rescue
        # launcher is planted on a neighbour of a pocketed builder, so it reads
        # pocketed and detonates; a core/apron launcher in open ground reads
        # escapable, serves the throw, and survives. The one blind spot is a core
        # spawn niche under ESCAPE_MIN_TILES tiles (isImpassable's "cramped spawn"
        # case) — a core launcher there would read pocketed and detonate after a
        # rescue. Accepted: that niche is pathological, its lone launcher is nearly
        # useless anyway, and no pool map exhibits it (mirror gauntlet is clean).
        if not _escapable(bot, ct, ct.get_position()):
            bot._rescueThrown = True
        if debug.ENABLED:
            debug.log(
                "launcher",
                f"rescue threw {requester} -> {best} armed={bot._rescueThrown} "
                f"rnd={ct.get_current_round()}",
            )


def runLauncher(bot, ct: Controller) -> None:
    """One action per turn: defend first, then serve a posted launch request.

    Defence outranks ferrying our own builders — an enemy body at the door can
    destroy the launcher, and there's only one throw to spend. With no hostile
    and no request there is nothing to aim, so the launcher idles rather than
    guessing where an adjacent builder wants to go.
    """
    # A rescue launcher self-detonates the turn after it threw its builder out —
    # deferred one turn because self_destruct and the launch are both actions and
    # can't share a turn. self_destruct needs no action and no cooldown (see
    # guns.py), so this sits above the cooldown gate.
    if bot._rescueThrown:
        ct.self_destruct()
        return

    if ct.get_action_cooldown() != 0:
        return

    # Read the shared slot once, up front. A rescue-flagged request is served only
    # by the launcher sitting in the pocket beside the trapped builder, and must be
    # handled *before* the apron branch below: a rescue launcher can be planted
    # inside the enemy apron, and that branch would otherwise divert it to denial and
    # never throw. Chebyshev-1 is a pre-filter; can_launch inside _serveRescue is the
    # real reach gate.
    myPos = ct.get_position()
    req = unpackRequest(ct.read_store(store.LAUNCH_REQUEST))
    if req is not None and req[2] and _chebyshev(myPos, req[0]) <= 1:
        _serveRescue(bot, ct, req[0], req[1])
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
        and ph.manhattanToCore(myPos, enemyCore) <= ENEMY_APRON_RADIUS
    ):
        _repelAwayFromCore(bot, ct, enemyCore)
        return

    if _repelHostile(bot, ct):
        return

    if req is None:
        return
    requester, target, rescue = req

    # A rescue-flagged request we reach here is not ours to serve — the adjacent
    # rescue launcher owns it, and we (the core launcher) are not adjacent, or the
    # branch above would have taken it. Leave it in the slot; consuming it would
    # starve the launcher that can actually throw.
    if rescue:
        return

    # Only consume a request we can actually serve. Two launchers now read this slot
    # (core + a transient rescue launcher), and consume-on-read by the wrong one
    # would strand the other's requester. The staleness hazard the old
    # unconditional consume guarded against — a dead author's request flinging the
    # next passer-by — only bites a launcher the stale tile is adjacent to, and that
    # launcher still consumes here, so the guard is preserved where it matters.
    if _chebyshev(myPos, requester) > 1:
        return
    store.put(ct, store.LAUNCH_REQUEST, 0)

    # The requester must still be a friendly builder sitting where it said it
    # was — it holds for exactly this, but it may have died or been shoved.
    t = ph.readTile(ct, bot.hivemind, requester)
    if not t.builder or t.builderEnemy:
        return

    best = _bestThrow(bot, ct, requester, target, requireEscapable=True)
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
