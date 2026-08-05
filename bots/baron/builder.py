# Baron builder roles — human task lists (check behavior here first)
#
# ## threatHome — SLOT_THREAT set (core HP latch); default + guardian
#    Harassers skip. Abort relay → move to core → react/defGun/defend/heal.
#
# ## Assignment (my_index > 0; idx % 5) — 2 guardians / 2 harassers / 1 default
#    1 → guardian · 2 → harasser · 3 → default · 4 → harasser · 0 → guardian
#
# ## guardian (orchestrator: runGuardian below; helpers in defence.py)
#    idx % 5 in (1, 0) (builders 1,5,6,10,11,…)
#    Ignores wave. Shares SLOT_THREAT recall with default.
# 1. leash — outside Chebyshev 4 of core → moveToward core
# 2. reactEnemyTurret — contest / defGun / defendHome / healAlly
# 3. defGun — near core; gunners only
# 4. defendHome — attack enemy in vision, else heal core if HP < 500
# 5. healAlly — core → gunner/sentinel → harvester
# 6. clearBarriers — fire / move to nearest enemy barrier
# 7. patrolHome — cycle mid-side waypoints around core
#
# ## default — priority order (orchestrator: runDefault below)
#    idx % 5 == 3 (builders 3,8,13,…)
#    0. reactEnemyTurret — enemy gunner/sentinel/launcher in vision → abortRelay (only
#        after first completed relay) + placeGun / defGun / defendHome / healAlly;
#        skip economy this turn. While first relay in progress: ignore (keep belt).
#    Early phase (age < BUILDER_HOME_TURNS after birth_round): defend-first +
#    nearCoreEconomy (Chebyshev 4 ore). ≤0 disables. Harassers/guardians never enter.
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
# 3. defGun — near core only; enemy builder/gunner/sentinel/launcher in vision → placeGun
#        (gunners only — never sentinels)
# 4. defendHome — near core only; attack enemy in vision, else heal core if HP < 500
# 5. healAlly — core → gunner/sentinel → harvester
# 6. recheckOreNodes — clear sticky oreSite; prefer empty → ally-blocked → harvester
# # idleHome — commented out (soft-idle trap)
#
# ## harasser (orchestrator: runHarasser below; helpers in offense.py)
#    idx % 5 in (2, 4) (builders 2,4,7,9,…)
#    OR wave attack: WAVE_ATTACK_TURN / WAVE_ATTACK_TI in main.py (>0 = set)
#    Unaffected by SLOT_THREAT recall. Guardians never enter this path.
# 1. offGun — placeGun/placeSentinel: core → gunner → sentinel → harvester → launcher;
#        ≤1 ally gunner covering each enemy harvester
# 2. seekHarvester — local_map enemy harvesters / scout ore nodes (vision updates map)
# 3. seekCore — moveToward enemy core

"""Builder role orchestrators — dispatch into logistics / offence helpers."""

from __future__ import annotations

from fcode import Controller, Position

from hivemind import CoreState, unpack_cores
from logistics import (
    _nearCore,
    abortRelay,
    buildHarvester,
    contestTurret,
    defendHome,
    enemyTurretInVision,
    healAlly,
    maintainHarvester,
    moveToward,
    recheckOreNodes,
    relayEverComplete,
)
from defence import clearBarriers, defGun, patrolHome
from offense import offGun, seekCore, seekHarvester


def _resolveCores(bot, ct: Controller) -> None:
    from main import SLOT_PACKED_CORES

    ally, enemy = unpack_cores(ct.read_store(SLOT_PACKED_CORES))
    if bot.corePos is None and ally[0] != CoreState.UNKNOWN:
        bot.corePos = Position(ally[1], ally[2])
    if enemy[0] != CoreState.UNKNOWN:
        bot.enemyCorePos = Position(enemy[1], enemy[2])
    elif bot.enemyCorePos is None and bot.hivemind.enemy_core_pos is not None:
        bot.enemyCorePos = bot.hivemind.enemy_core_pos


def isGuardian(idx: int) -> bool:
    """True for builders 1,5,6,10,… (idx % 5 in {0, 1})."""
    return idx > 0 and idx % 5 in (0, 1)


def isHarasser(idx: int) -> bool:
    """True for builders 2,4,7,9,… (idx % 5 in {2, 4})."""
    return idx > 0 and idx % 5 in (2, 4)


def isDefaultRole(idx: int) -> bool:
    """True for builders 3,8,13,… (idx % 5 == 3)."""
    return idx > 0 and idx % 5 == 3


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


def threatActive(ct: Controller) -> bool:
    """True while core has latched SLOT_THREAT (HP hysteresis)."""
    from main import SLOT_THREAT

    return ct.read_store(SLOT_THREAT) != 0


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


def runThreatHome(bot, ct: Controller) -> None:
    """Default/guardian recall: abort work, return to core, defend/heal only."""
    _resolveCores(bot, ct)
    bot.nearCoreEconomy = False
    if bot.inRelay:
        abortRelay(bot)
    if bot.corePos is None:
        return
    if not _nearCore(bot, ct):
        moveToward(bot, ct, bot.corePos)
        return
    if _reactEnemyTurret(bot, ct):
        return
    if defGun(bot, ct):
        return
    if defendHome(bot, ct):
        return
    healAlly(bot, ct)


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


def runGuardian(bot, ct: Controller) -> None:
    """Guardian: leash → react → defGun → defendHome → healAlly → clearBarriers → patrol."""
    _resolveCores(bot, ct)
    bot.nearCoreEconomy = False
    if bot.corePos is None:
        return
    if not _nearCore(bot, ct):
        moveToward(bot, ct, bot.corePos)
        return
    if _reactEnemyTurret(bot, ct):
        return
    if defGun(bot, ct):
        return
    if defendHome(bot, ct):
        return
    if healAlly(bot, ct):
        return
    if clearBarriers(bot, ct):
        return
    patrolHome(bot, ct)


def runHarasser(bot, ct: Controller) -> None:
    """Harasser: offGun → seekHarvester → seekCore."""
    _resolveCores(bot, ct)
    bot.nearCoreEconomy = False
    if offGun(bot, ct):
        return
    if seekHarvester(bot, ct):
        return
    seekCore(bot, ct)


def runBuilderRoles(bot, ct: Controller) -> None:
    """Dispatch: guardian → harasser/wave → threat home → default."""
    idx = bot.hivemind.my_index
    if isGuardian(idx):
        if threatActive(ct):
            runThreatHome(bot, ct)
        else:
            runGuardian(bot, ct)
        return
    if waveAttackActive(ct) or isHarasser(idx):
        runHarasser(bot, ct)
        return
    if threatActive(ct):
        runThreatHome(bot, ct)
        return
    runDefault(bot, ct)


# Back-compat
def run_builder_roles(bot, ct: Controller) -> None:
    runBuilderRoles(bot, ct)
