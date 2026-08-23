"""Healing an ally: walk to it, heal it. Same shape as raidBot's build step —
adjacency check, act if there, otherwise let movement.walkTo close the gap.

heal() is a builder-bot ability (ct.heal(pos)), not a turret one: 1 Ti, one
action cooldown, heals every friendly entity on an orthogonally adjacent
tile by 4 HP. can_heal already requires a damaged friendly entity on the
target tile, so this doesn't re-derive that from HP queries at range — it
trusts the engine's own legality check once adjacent, same as raidBot trusts
can_build_sentinel rather than reasoning about facing itself.
"""

from __future__ import annotations

from fcode import Controller, EntityType, Position

import debug
import pathfindingHelpers as ph
from movement import walkTo
from pathing import manhattan

# Economic infrastructure ecoBot watches for — its own investment, not combat
# assets. `ph.BELT_TYPES` is (CONVEYOR, SPLITTER).
ECO_HEAL_TYPES = (EntityType.CORE, EntityType.HARVESTER) + ph.BELT_TYPES

# What the raider watches for — the turrets it exists to place, not itself.
TURRET_HEAL_TYPES = (EntityType.GUNNER, EntityType.SENTINEL)


def nearestDamagedAlly(ct: Controller, types, accept=None) -> Position | None:
    """Nearest ally of one of `types`, within vision, that's actually damaged.
    None if nothing qualifies.

    "Nearby" is exactly "in vision" — the same engine-defined radius
    guns.visibleHostiles already trusts for the equivalent enemy-side scan,
    rather than an invented threshold.

    `accept(pos)`, when given, further narrows which tiles qualify. That exists
    for a caller that is itself bounded to a region: vision is measured from the
    healer, so an unfiltered target can sit outside a leash the caller must stay
    inside, and walking out to it only to be recalled is an oscillation rather
    than a heal. Callers with no such bound omit it and are unaffected.
    """
    myTeam = ct.get_team()
    here = ct.get_position()
    best = None
    bestDist = None

    for ent in bot.hivemind.visible_entities:
        if ent["team"] != myTeam:
            continue
        etype = ent["type"]
        if etype not in types:
            continue
        eid = ent["id"]
        if ct.get_hp(eid) >= ct.get_max_hp(eid):
            continue

        pos = ent["pos"]
        # The core is a 2x2 building but get_position reports only its min-x/min-y
        # anchor tile, so every builder that targets it converges on that one
        # corner's two stand tiles and piles up on a single side. Expand it to its
        # footprint and aim each builder at the nearest of the four tiles instead,
        # so they spread around all sides — can_heal works off any of them since
        # the core occupies them all.
        if etype == EntityType.CORE:
            cand = min(ph.coreFootprint(pos),
                       key=lambda t: (manhattan(here, t), t.x, t.y))
        else:
            cand = pos
        if cand.x == here.x and cand.y == here.y:
            continue  # can't heal our own tile — can_heal requires adjacency
        if accept is not None and not accept(cand):
            continue
        d = manhattan(here, cand)
        if bestDist is None or d < bestDist:
            best, bestDist = cand, d

    return best


def healNearby(bot, ct: Controller, types, accept=None) -> bool:
    """Heal the nearest damaged ally of one of `types` within vision, if any.
    True if the turn was used — either actually healing, or walking toward
    one that isn't adjacent yet. `accept` narrows the candidates — see
    nearestDamagedAlly."""
    target = nearestDamagedAlly(ct, types, accept)
    if debug.ENABLED:
        debug.log(
            "heal",
            f"idx={bot.hivemind.my_index} pos={ct.get_position()} "
            f"nearestDamagedAlly={target}",
        )
    if target is None:
        return False
    return healAlly(bot, ct, target)


def healAlly(bot, ct: Controller, target: Position) -> bool:
    """Walk to `target` and heal it. True if the turn was used.

    `target` is a tile carrying a damaged ally — deciding *who* to heal is the
    caller's job, same as raid.py decides the sentinel site and raidBot only
    walks there and builds. If the target has healed up by the time we
    arrive (someone else got there first, or it was never actually damaged),
    can_heal simply refuses and this is a harmless no-op rather than a wasted
    walk — the caller should stop calling this with a healthy target, not
    this function detecting it.

    Pathfinding is movement.walkTo's: if the tile we'd need to stand on to
    reach `target` is occupied or otherwise blocked, walkTo's own A*/BFS
    fallback and avoid-set find another approach, same machinery every other
    role already relies on for this.
    """
    pos = ct.get_position()

    if ph.beltFacing(pos, target) is not None:
        healed = ct.get_action_cooldown() == 0 and ct.can_heal(target)
        if debug.ENABLED:
            debug.log("heal", f"idx={bot.hivemind.my_index} pos={pos} "
                               f"adjacent target={target} healed={healed}")
        if healed:
            ct.heal(target)
        return True

    def beside(p: Position) -> bool:
        return ph.beltFacing(p, target) is not None

    used = walkTo(bot, ct, beside, target, ("heal", target.x, target.y))
    if debug.ENABLED:
        debug.log("heal", f"idx={bot.hivemind.my_index} pos={pos} "
                           f"walking_to target={target} walkTo_returned={used}")
    return used
