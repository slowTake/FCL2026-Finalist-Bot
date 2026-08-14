"""Builder economy role. Orchestrator only — mechanics live in the modules it calls."""

from __future__ import annotations

from fcode import Controller, EntityType, Position

import pathfindingHelpers as ph
from movement import exploreStep, walkTo
from patience import clearHold, waitTi
from relays import hijackRelay, relayToHome

SLOT_HARVESTERS = 0

# How many of the nearest ore nodes builders fan out across. Deterministic off
# my_index, so two builders with different indices rarely pick the same node.
# Not a claim — see observations.md.
ORE_SPREAD = 4


def runEcoBot(bot, ct: Controller) -> None:
    if bot.inRelay and relayToHome(bot, ct):
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
    ct.write_store(SLOT_HARVESTERS, ct.read_store(SLOT_HARVESTERS) + 1)

    bot.relayFrom = ore
    bot.inRelay = True
    bot.oreTarget = None
    bot.walkPath = None
    clearHold(bot)
    return True


def seekOre(bot, ct: Controller) -> bool:
    if bot.oreTarget is None or not _stillFree(bot, ct, bot.oreTarget):
        bot.oreTarget = pickOre(bot, ct)
        bot.walkPath = None
    ore = bot.oreTarget
    if ore is None:
        return False

    if _enemyHarvesterAt(bot, ct, ore):
        return hijackRelay(bot, ct)

    def beside(p: Position) -> bool:
        return p.distance_squared(ore) <= 2 and abs(p.x - ore.x) + abs(p.y - ore.y) == 1

    return walkTo(bot, ct, beside, ore)


def pickOre(bot, ct: Controller) -> Position | None:
    """Nearest free ore, offset by builder index so builders fan out."""
    pos = ct.get_position()
    cands = [o for o in knownOre(bot, ct) if _stillFree(bot, ct, o)]
    if not cands:
        return None

    cands.sort(key=lambda o: abs(o.x - pos.x) + abs(o.y - pos.y))
    return cands[bot.hivemind.my_index % min(len(cands), ORE_SPREAD)]


def knownOre(bot, ct: Controller) -> list[Position]:
    out = []
    for (x, y), entry in bot.hivemind.local_map.items():
        if not entry.get("ore"):
            continue
        ent = entry.get("entity")
        if ent is not None and ent.get("type") in (
            EntityType.HARVESTER, EntityType.BARRIER, "WALL"
        ):
            continue
        out.append(Position(x, y))
    return out


def _stillFree(bot, ct: Controller, ore: Position) -> bool:
    t = ph.readTile(ct, bot.hivemind, ore)
    return t.ore and (t.etype is None or t.etype == EntityType.HARVESTER and t.enemy)


def _enemyHarvesterAt(bot, ct: Controller, ore: Position) -> bool:
    t = ph.readTile(ct, bot.hivemind, ore)
    return t.etype == EntityType.HARVESTER and t.enemy
