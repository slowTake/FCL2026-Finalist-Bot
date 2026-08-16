

from __future__ import annotations

from fcode import Controller, EntityType, Position

from geometry import (
    DEFEND_HOME_CHEBYSHEV,
    TURRET_TYPES,
    chebyshevToCore,
    nearCore,
)
from hivemind import CoreState, unpack_cores
from logistics import (
    abortRelay,
    breakOreDeadlock,
    buildHarvester,
    openBlockedRoute,
    contestTurret,
    defendHome,
    enemyTurretInVision,
    finishNearbyHarvester,
    healAlly,
    maintainHarvester,
    moveToward,
    guardianLeash,
    moveTowardLeash,
    exploreFrontier,
    recheckOreNodes,
    relayEverComplete,
    verifyHomeHarvester,
)
from defence import (
    _buildHarvesterOnClearedBarrier,
    blockGunLine,
    clearBarriers,
    defGun,
    defSentinel,
    finishNearbyBelt,
    maintainGunLineBlock,
    patchAdjacentCoreBelt,
    patrolHome,
    reconnectBelts,
)
from pathing import breakEncirclement
from offense import (
    avoidFire,
    breakBeltDeadlock,
    denyBelt,
    denyHarvesterSupply,
    denyOre,
    offGun,
    seekCore,
    seekHarvester,
    snipeHarvester,
    wallCore,
)


def _resolveCores(bot, ct: Controller) -> None:
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


def isGuardian(idx: int) -> bool:
    return idx > 0 and idx % 5 in (0, 1)


def guardianRank(idx: int) -> int:
    return (idx // 5) * 2 + (1 if idx % 5 else 0)


def guardianCapped(idx: int) -> bool:
    from main import MAX_GUARDIANS

    if MAX_GUARDIANS is None or MAX_GUARDIANS <= 0:
        return False
    return guardianRank(idx) > MAX_GUARDIANS


def guardianOverflowIsHarasser(idx: int) -> bool:
    from main import MAX_GUARDIANS

    if MAX_GUARDIANS is None or MAX_GUARDIANS <= 0:
        return False  # guardianCapped is already False in this case too
    excess = guardianRank(idx) - MAX_GUARDIANS
    if excess <= 0:
        return False  # not actually capped; guardianCapped(idx) is False
    return excess % 2 == 1


def isHarasser(idx: int) -> bool:
    return idx > 0 and idx % 5 == 2


def isDefaultRole(idx: int) -> bool:
    return idx > 0 and idx % 5 in (3, 4)


def homeGuardActive(bot, ct: Controller) -> bool:
    from main import BUILDER_HOME_TURNS

    if BUILDER_HOME_TURNS is None or BUILDER_HOME_TURNS <= 0:
        return False
    birth = bot.hivemind.birth_round
    if birth is None:
        return True  # brand-new — stay near core until indexed
    return ct.get_current_round() - birth < BUILDER_HOME_TURNS


def waveAttackActive(ct: Controller) -> bool:
    from main import WAVE_ATTACK_TI, WAVE_ATTACK_TURN

    turn = WAVE_ATTACK_TURN
    if turn is not None and turn > 0 and ct.get_current_round() >= turn:
        return True
    ti = WAVE_ATTACK_TI
    if ti is not None and ti > 0 and ct.get_global_resources() >= ti:
        return True
    return False


def _reactEnemyTurret(
    bot, ct: Controller, *, skipEconomy: bool = True, homeOnly: bool = False
) -> bool:
    if not enemyTurretInVision(ct):
        bot.reactFrozen = 0
        return False
    if bot.inRelay:
        return False
    if not homeOnly and contestTurret(bot, ct):
        bot.reactFrozen = 0
        return True
    if defGun(bot, ct, homeOnly=homeOnly):
        bot.reactFrozen = 0
        return True
    if defendHome(bot, ct):
        bot.reactFrozen = 0
        return True
    if healAlly(bot, ct):
        bot.reactFrozen = 0
        return True
    if skipEconomy:
        bot.stuck = 0

        from main import REACT_FREEZE_CAP_TURNS

        if REACT_FREEZE_CAP_TURNS and REACT_FREEZE_CAP_TURNS > 0:
            bot.reactFrozen = getattr(bot, "reactFrozen", 0) + 1
            if bot.reactFrozen > REACT_FREEZE_CAP_TURNS:
                return False
        return True
    return False


def _dodgeWhileRelaying(bot, ct: Controller) -> bool:
    if not bot.inRelay:
        return False
    return avoidFire(bot, ct, goal=bot.corePos)


def _runDefaultHome(bot, ct: Controller) -> None:
    bot.nearCoreEconomy = True
    if _reactEnemyTurret(bot, ct, skipEconomy=relayEverComplete(ct)):
        return
    if _dodgeWhileRelaying(bot, ct):
        return
    if openBlockedRoute(bot, ct):
        return
    breakOreDeadlock(bot, ct)  # state-only; economy below re-picks this turn

    if defGun(bot, ct):
        return
    if healAlly(bot, ct):
        return
    if finishNearbyBelt(bot, ct):
        return
    if buildHarvester(bot, ct):
        return
    if maintainHarvester(bot, ct):
        return

    if finishNearbyHarvester(bot, ct):
        return
    if recheckOreNodes(bot, ct):
        return
    exploreFrontier(bot, ct, outward=False)


def runDefault(bot, ct: Controller) -> None:
    _resolveCores(bot, ct)
    if homeGuardActive(bot, ct):
        _runDefaultHome(bot, ct)
        return

    bot.nearCoreEconomy = False
    if _reactEnemyTurret(bot, ct, skipEconomy=relayEverComplete(ct)):
        return
    if _dodgeWhileRelaying(bot, ct):
        return
    if openBlockedRoute(bot, ct):
        return
    breakOreDeadlock(bot, ct)  # state-only; economy below re-picks this turn
    if buildHarvester(bot, ct):
        return
    if maintainHarvester(bot, ct):
        return
    if defGun(bot, ct):
        return
    if defendHome(bot, ct):
        return
    if healAlly(bot, ct):
        return
    if finishNearbyBelt(bot, ct):
        return
    if finishNearbyHarvester(bot, ct):
        return
    if verifyHomeHarvester(bot, ct):
        return
    if recheckOreNodes(bot, ct):
        return
    exploreFrontier(bot, ct, outward=False)


_ENEMY_THREAT_TYPES = TURRET_TYPES + (EntityType.BUILDER_BOT,)


def guardianEconomyOn() -> bool:
    from main import GUARDIAN_ECONOMY

    return bool(GUARDIAN_ECONOMY)


def _enemyUnitInVision(ct: Controller) -> bool:
    me = ct.get_team()
    for eid in ct.get_nearby_units():
        if ct.get_team(eid) == me:
            continue
        if ct.get_entity_type(eid) in _ENEMY_THREAT_TYPES:
            return True
    return False


def _guardianLeashOk(bot, ct: Controller) -> bool:
    leash = guardianLeash(ct)
    if nearCore(bot, ct, leash):
        return True
    if not guardianEconomyOn():
        return False
    if not (bot.inRelay or bot.oreSite is not None):
        return False
    from main import GUARDIAN_ECON_SLACK

    slack = GUARDIAN_ECON_SLACK or 0
    if slack <= 0:
        return False
    reach = leash + slack
    return chebyshevToCore(ct.get_position(), bot.corePos) <= reach


def _harassLeashOk(bot, ct: Controller) -> bool:
    from main import HARASS_LEASH_CHEBYSHEV

    if bot.enemyCorePos is None:
        return True
    if HARASS_LEASH_CHEBYSHEV is None or HARASS_LEASH_CHEBYSHEV <= 0:
        return True
    return chebyshevToCore(ct.get_position(), bot.enemyCorePos) <= HARASS_LEASH_CHEBYSHEV


def _guardianEconomy(bot, ct: Controller) -> bool:
    if not guardianEconomyOn():
        return False
    bot.nearCoreEconomy = True  # _allowOreSite → ore within the leash only
    breakOreDeadlock(bot, ct)  # state-only; picks below re-choose this turn
    if buildHarvester(bot, ct):
        return True
    return maintainHarvester(bot, ct)


_RELAY_ABORT_EXEMPT_TILES = 1


def _relayNearlyComplete(bot) -> bool:
    path = bot.relayPath
    if not bot.inRelay or path is None:
        return False
    remaining = len(path) - (bot.relayBuiltTo + 1)
    return 0 <= remaining <= _RELAY_ABORT_EXEMPT_TILES


def runGuardian(bot, ct: Controller) -> None:
    _resolveCores(bot, ct)
    bot.nearCoreEconomy = False
    if bot.corePos is None:
        return

    if relayEverComplete(ct):
        underAttack = _enemyUnitInVision(ct)
    else:
        underAttack = enemyTurretInVision(ct)
    if underAttack and (bot.inRelay or bot.oreSite is not None):
        if not _relayNearlyComplete(bot):
            abortRelay(bot)
            bot.oreSite = None

    if not _guardianLeashOk(bot, ct):
        moveTowardLeash(bot, ct, bot.corePos, radius=guardianLeash(ct))
        return
    if blockGunLine(bot, ct):
        return
    if maintainGunLineBlock(bot, ct):
        return
    if _reactEnemyTurret(bot, ct, skipEconomy=False, homeOnly=True):
        return
    if defGun(bot, ct, homeOnly=True):
        return
    if defSentinel(bot, ct):
        return
    if avoidFire(bot, ct, goal=bot.corePos):
        return
    if patchAdjacentCoreBelt(bot, ct):
        return
    if defendHome(bot, ct):
        return
    if healAlly(bot, ct):
        return
    if _buildHarvesterOnClearedBarrier(bot, ct):
        return
    if not underAttack and clearBarriers(bot, ct):
        return
    if reconnectBelts(bot, ct, allow_walk=not underAttack):
        return
    if finishNearbyBelt(bot, ct, allow_walk=not underAttack):
        return
    if not underAttack and finishNearbyHarvester(bot, ct):
        return
    if not underAttack and _guardianEconomy(bot, ct):
        return
    patrolHome(bot, ct)


def runHarasser(bot, ct: Controller) -> None:
    _resolveCores(bot, ct)
    bot.nearCoreEconomy = False
    breakBeltDeadlock(bot, ct)
    if not _harassLeashOk(bot, ct):
        from main import HARASS_LEASH_CHEBYSHEV

        moveTowardLeash(bot, ct, bot.enemyCorePos, radius=HARASS_LEASH_CHEBYSHEV)
        return
    if snipeHarvester(bot, ct):
        return
    if denyHarvesterSupply(bot, ct):
        return
    if offGun(bot, ct):
        return
    if avoidFire(bot, ct):
        return
    if denyOre(bot, ct):
        return
    if denyBelt(bot, ct):
        return
    if seekHarvester(bot, ct):
        return
    if seekCore(bot, ct):
        return
    exploreFrontier(bot, ct, outward=True)


def runBuilderRoles(bot, ct: Controller) -> None:
    if breakEncirclement(bot, ct, local_map=bot.hivemind.local_map):
        return
    idx = bot.hivemind.my_index
    if isGuardian(idx) and not guardianCapped(idx):
        runGuardian(bot, ct)
        return
    capped_guardian_as_harasser = isGuardian(idx) and guardianOverflowIsHarasser(idx)
    if waveAttackActive(ct) or isHarasser(idx) or capped_guardian_as_harasser:
        runHarasser(bot, ct)
        return
    runDefault(bot, ct)