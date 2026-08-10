"""Economic warfare: siphon their belts, and spend surplus making them spend.

Bandit's replacement for baron's `offense.py`. The difference is the whole point
of the fork, so it is worth stating plainly:

Baron's offensive steps existed to *remove* things — cut a belt with a barrier,
snipe a harvester, orbit and harass. Every one of them traded our titanium for
theirs at roughly even odds, and even odds is not a plan when the opponent may
simply be better at fighting.

Bandit's exist to *take* things, or to make the opponent pay to keep them:

  * `beltHijack` puts our conveyor in front of their belt. Any team's conveyor
    can pull any belt's or harvester's output — the same engine rule that makes
    harvester-stealing work in `logistics._harvesterOwnerAllowed` — so a 3 Ti
    conveyor turns their line into our supply. If they destroy it, they spent a
    turn and some Ti to undo 3 Ti. Either branch is a win.
  * `gunnerCordon` and `coreSentinel` are bought only out of surplus, and are
    aimed at what is expensive to replace, not at what is close to dying. They
    are not a push. A gunner sitting 6-8 tiles off their core that they must
    answer is doing its job whether or not it ever kills anything.

Nothing in here may outrank our own economy — everything is wired low in
`builder.runBuilder`, reached only on a turn with no ore or relay work to do.
"""

from __future__ import annotations

from fcode import Controller, Direction, EntityType, Environment, Position

from geometry import (
    BELT_TYPES,
    chebyshevToCore,
    harvesterClaimedByAlly,
    isAdjacent,
)
from guns import placeGun, placeSentinel
from logistics import moveToward, travelFacing
from pathing import inBounds, manhattan


def surplusReady(ct: Controller) -> bool:
    """True once the bank can afford to burn titanium on pressure.

    Live read, no store slot and no per-bot state — mirrors the existing
    `GUN_PLACE_MIN_TI` pattern. Every builder derives the same answer from the
    same shared bank on the same turn, so there is nothing to coordinate.
    """
    from main import TI_SURPLUS_THRESHOLD

    return ct.get_global_resources() >= TI_SURPLUS_THRESHOLD


# ------------------------------------------------------------------
# Belt hijack
# ------------------------------------------------------------------


def _hijackPads(bot, ct: Controller) -> list[Position]:
    """Empty tiles one step ahead of a visible enemy belt's facing.

    The scan is baron's `offense._forwardBarrierPads`, minus its filters. Baron
    used these tiles to place *barriers*, and gated them on `_oreCloserToAlly`
    (only cut a line feeding ore nearer us than them) plus a skip-list of pads it
    had failed on. Neither survives here:

    * The ore-proximity gate existed to avoid wasting a barrier on a line we had
      no stake in. A hijack has a stake in every line by construction — whatever
      it carries ends up in our core.
    * The skip-list and cooldown are gone deliberately. See `beltHijack`.

    Sorted nearest-first so the caller acts on what it can reach this turn.
    """
    me = ct.get_team()
    pads: list[Position] = []
    seen: set[tuple[int, int]] = set()
    for eid in ct.get_nearby_entities():
        if ct.get_team(eid) == me:
            continue
        if ct.get_entity_type(eid) not in BELT_TYPES:
            continue
        try:
            facing = ct.get_direction(eid)
        except Exception:
            continue
        if facing == Direction.CENTRE:
            continue
        belt = ct.get_position(eid)
        pad = belt.add(facing)
        key = (pad.x, pad.y)
        if key in seen:
            continue
        seen.add(key)
        if not inBounds(ct, pad):
            continue
        if not ct.is_in_vision(pad):
            continue
        if ct.get_tile_env(pad) == Environment.WALL:
            continue
        if not ct.is_tile_empty(pad):
            continue
        pads.append(pad)
    pads.sort(key=lambda p: manhattan(ct.get_position(), p))
    return pads


def beltHijack(bot, ct: Controller) -> bool:
    """Plant a conveyor in front of an enemy belt, facing home, and siphon it.

    Three titanium buys a tile that drains whatever their line pushes into it,
    straight back toward our core, for as long as they leave it there. There is
    no attrition in either outcome: they either keep feeding us, or they spend a
    turn and their own titanium clearing a blockage that cost us almost nothing.

    **No cooldown, and no guard against other builders' work.** This is the one
    place in the bot where builders are *meant* to converge. Several of them
    piling onto the same hijack lane finishes the siphon faster, exactly as
    `relayToCore`'s merge logic already lets any builder extend a line another
    started. The only collision guard needed is the engine's: `can_build_conveyor`
    returns false on a tile that is occupied or already carries a correctly-facing
    belt, so a second builder arriving at an already-hijacked pad simply finds
    nothing to do and the loop below falls through to its next candidate — or off
    the end of this step entirely, into the rest of the chain. No bookkeeping, no
    claim, no skip-list, no `lastHijackRound`.

    Placed low in `runBuilder` (step 8) instead: opportunistic by *position*, so
    it can never compete with a builder's own ore or relay work.
    """
    if bot.corePos is None:
        return False

    pads = _hijackPads(bot, ct)
    if not pads:
        return False

    pos = ct.get_position()
    if ct.get_action_cooldown() == 0:
        for pad in pads:
            if not isAdjacent(pos, pad):
                continue
            facing = travelFacing(pad, bot.corePos)
            if facing == Direction.CENTRE:
                continue
            if ct.get_global_resources() < ct.get_conveyor_cost():
                return False
            if ct.can_build_conveyor(pad, facing):
                ct.build_conveyor(pad, facing)
                return True
            # Occupied, or already correct — someone beat us here. Next pad.

    # Nothing adjacent to act on: walk to the nearest candidate. Cheap because
    # this step is only ever reached on a turn with no economy work to lose.
    return moveToward(bot, ct, pads[0])


# ------------------------------------------------------------------
# Surplus-gated turrets
# ------------------------------------------------------------------

# Ordered by what costs the enemy most to replace and most to be without, not by
# what is easiest to kill. The core is absent from both this list and
# `guns._GUNNER_PRIORITY`: bandit does not try to break through.
_CORDON_TARGET_ORDER = (
    EntityType.HARVESTER,
    EntityType.GUNNER,
    EntityType.SENTINEL,
)


def _cordonTargets(bot, ct: Controller) -> list[Position]:
    """Enemy entities inside the cordon band, best first.

    Band is measured from *their* core, not ours: the point is to sit at a range
    where they have to respond, without walking a turret into whatever already
    defends their core. Builders are never targeted (they move, so a static
    turret aimed at where one was is wasted) and neither is the core.
    """
    from main import CORDON_MAX_CHEBYSHEV, CORDON_MIN_CHEBYSHEV

    enemy_core = getattr(bot, "enemyCorePos", None)
    if enemy_core is None:
        return []

    me = ct.get_team()
    local_map = bot.hivemind.local_map
    pos = ct.get_position()
    found: list[tuple[int, int, Position]] = []
    for eid in ct.get_nearby_entities():
        if ct.get_team(eid) == me:
            continue
        etype = ct.get_entity_type(eid)
        if etype not in _CORDON_TARGET_ORDER:
            continue
        tile = ct.get_position(eid)
        band = chebyshevToCore(tile, enemy_core)
        if band < CORDON_MIN_CHEBYSHEV or band > CORDON_MAX_CHEBYSHEV:
            continue
        # A harvester we are already draining is worth far more alive than dead
        # — killing it destroys our own supply line's source. This is the same
        # signal `relayToCore` leaves behind when it commits to a harvester.
        if etype == EntityType.HARVESTER and harvesterClaimedByAlly(
            ct, tile, local_map=local_map
        ):
            continue
        found.append((_CORDON_TARGET_ORDER.index(etype), manhattan(pos, tile), tile))
    found.sort(key=lambda t: (t[0], t[1]))
    return [tile for _p, _d, tile in found]


def gunnerCordon(bot, ct: Controller) -> bool:
    """Ring their expansion with gunners once we can afford to.

    Not a siege — a tax. A gunner in the 6-8 band off their core has to be
    answered, and answering it costs them a turret of their own, a builder's
    turn, or a harvester. All placement geometry (in-range pads with a facing
    that has line of sight, affordability, `GUN_PLACE_MIN_TI`) is `guns.placeGun`
    unchanged; the only new things here are the surplus gate and which targets
    are worth a gunner at all.
    """
    if not surplusReady(ct):
        return False
    for target in _cordonTargets(bot, ct):
        if placeGun(bot, ct, target):
            return True
    return False


def coreSentinel(bot, ct: Controller) -> bool:
    """Park a sentinel on the enemy core once we can afford to.

    A sentinel's sqrt(32) reach lets it sit outside what a gunner defending the
    core can answer, so this is persistent pressure rather than a trade: they
    either spend on something that can reach it, or they bleed. `placeSentinel`
    already enforces SENTINEL_MIN_TURN, affordability, and the per-builder
    placement cooldown, so nothing is re-checked here.

    Aimed at the core, but `guns._SENTINEL_PRIORITY` puts BUILDER_BOT ahead of
    CORE — a builder crossing the line of fire is the transient worth
    interrupting for; the core will still be there next turn.
    """
    if not surplusReady(ct):
        return False
    enemy_core = getattr(bot, "enemyCorePos", None)
    if enemy_core is None:
        return False
    return placeSentinel(bot, ct, enemy_core)
