# Baron builder roles — human task lists (check behavior here first)
#
# ## default — priority order (orchestrator: runDefault below)
#    0. reactEnemyTurret — enemy gunner/sentinel in vision → abortRelay (only
#        after first completed relay) + placeGun / defGun / defendHome / healAlly;
#        skip economy this turn. While first relay in progress: ignore (keep belt).
#    Early phase (age < BUILDER_HOME_TURNS after birth_round): defend-first +
#    nearCoreEconomy (Chebyshev 8 ore). Harassers never enter this path.
#    Early (after react):
#      1. defGun — near core; gunners only
#      2. defendHome — near core; attack / heal core
#      3. healAlly — core → gunner/sentinel → harvester
#      4. buildHarvester — empty ore near core only
#      5. maintainHarvester — near-core ore / finish in-flight relay
#      6. recheckOreNodes — near-core empty → ally-blocked → harvester
#    After age gate (after react):
# 1. buildHarvester — empty ore in vision / HiveMind → build;
#        skip ore if visible enemy gunner is aiming at that tile
# 2. maintainHarvester
#    2a. checkOre — harvester → relayToCore; empty → build (same gunner-aim gate);
#        ally block → destroy (skip gunner/sentinel if that ore is threatened);
#        enemy gunner/sentinel/belt/launcher/barrier → placeGunnerOnOre (stub)
#    2b. relayToCore — abort on enemy turret only after SLOT_RELAY_COMPLETE;
#        else path → facing / clearWrongBelt → placeRelayBelt → watermark
# 3. defGun — near core only; enemy builder/gunner/sentinel in vision → placeGun
#        (gunners only — never sentinels)
# 4. defendHome — near core only; attack enemy in vision, else heal core if HP < 500
# 5. healAlly — core → gunner/sentinel → harvester
# 6. recheckOreNodes — clear sticky oreSite; prefer empty → ally-blocked → harvester
# # idleHome — commented out (soft-idle trap)
#
# ## harasser (orchestrator: runHarasser below; helpers in offense.py)
#    my_index > 0 and my_index % 3 == 0 (builders 3,6,9,…)
#    OR wave attack: WAVE_ATTACK_TURN / WAVE_ATTACK_TI in main.py (>0 = set)
# 1. avoidFire — under enemy gunner LoS → move outside LoS
# 2. offGun — placeGun/placeSentinel: core → gunner → harvester → launcher;
#        ≤1 ally gunner covering each enemy harvester
# 3. seekHarvester — local_map enemy harvesters / scout ore nodes (vision updates map)
# 4. seekCore — moveToward enemy core

"""Builder role orchestrators — dispatch into logistics / offence helpers."""

from __future__ import annotations

from fcode import Controller, Position

from hivemind import CoreState, unpack_cores
from logistics import (
    abortRelay,
    buildHarvester,
    contestTurret,
    defendHome,
    enemyTurretInVision,
    healAlly,
    maintainHarvester,
    recheckOreNodes,
    relayEverComplete,
)
from defence import defGun
from offense import avoidFire, offGun, seekCore, seekHarvester


def _resolveCores(bot, ct: Controller) -> None:
    from main import SLOT_PACKED_CORES

    ally, enemy = unpack_cores(ct.read_store(SLOT_PACKED_CORES))
    if bot.corePos is None and ally[0] != CoreState.UNKNOWN:
        bot.corePos = Position(ally[1], ally[2])
    if enemy[0] != CoreState.UNKNOWN:
        bot.enemyCorePos = Position(enemy[1], enemy[2])
    elif bot.enemyCorePos is None and bot.hivemind.enemy_core_pos is not None:
        bot.enemyCorePos = bot.hivemind.enemy_core_pos


def homeGuardActive(bot, ct: Controller) -> bool:
    """True while default-role age is under BUILDER_HOME_TURNS after birth_round."""
    from main import BUILDER_HOME_TURNS

    if BUILDER_HOME_TURNS is None or BUILDER_HOME_TURNS <= 0:
        return False
    birth = bot.hivemind.birth_round
    if birth is None:
        return True  # brand-new — stay near core until indexed
    return ct.get_current_round() - birth < BUILDER_HOME_TURNS


def waveAttackActive(ct: Controller) -> bool:
    """True if a set wave gate (turn or Ti) has been reached."""
    from main import WAVE_ATTACK_TI, WAVE_ATTACK_TURN

    turn = WAVE_ATTACK_TURN
    if turn is not None and turn > 0 and ct.get_current_round() >= turn:
        return True
    ti = WAVE_ATTACK_TI
    if ti is not None and ti > 0 and ct.get_global_resources() >= ti:
        return True
    return False


def _reactEnemyTurret(bot, ct: Controller) -> bool:
    """Abort relay and contest/defend when an enemy turret is in vision."""
    if not enemyTurretInVision(ct):
        return False
    # Finish the first inbound relay before abandoning belts for combat
    if bot.inRelay and not relayEverComplete(ct):
        return False
    if bot.inRelay:
        abortRelay(bot)
    if contestTurret(bot, ct):
        return True
    if defGun(bot, ct):
        return True
    if defendHome(bot, ct):
        return True
    if healAlly(bot, ct):
        return True
    # Skip economy so maintain cannot re-enter relay while turret stays visible
    return True


def _runDefaultHome(bot, ct: Controller) -> None:
    """Default early phase: defend first, then near-core economy only."""
    bot.nearCoreEconomy = True
    if _reactEnemyTurret(bot, ct):
        return

    if defGun(bot, ct):
        return
    if defendHome(bot, ct):
        return
    if healAlly(bot, ct):
        return
    if buildHarvester(bot, ct):
        return
    if maintainHarvester(bot, ct):
        return
    recheckOreNodes(bot, ct)


def runDefault(bot, ct: Controller) -> None:
    """Default role: early near-core defend, then economy-first."""
    _resolveCores(bot, ct)
    if homeGuardActive(bot, ct):
        _runDefaultHome(bot, ct)
        return

    bot.nearCoreEconomy = False
    if _reactEnemyTurret(bot, ct):
        return
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
    recheckOreNodes(bot, ct)


def runHarasser(bot, ct: Controller) -> None:
    """Harasser: avoidFire → offGun → seekHarvester → seekCore."""
    _resolveCores(bot, ct)
    bot.nearCoreEconomy = False
    if avoidFire(bot, ct):
        return
    if offGun(bot, ct):
        return
    if seekHarvester(bot, ct):
        return
    seekCore(bot, ct)


def runBuilderRoles(bot, ct: Controller) -> None:
    """Dispatch: harasser vs default (home defend is default-only)."""
    idx = bot.hivemind.my_index
    if waveAttackActive(ct) or (idx > 0 and idx % 3 == 0):
        runHarasser(bot, ct)
    else:
        runDefault(bot, ct)


# Back-compat
def run_builder_roles(bot, ct: Controller) -> None:
    runBuilderRoles(bot, ct)
