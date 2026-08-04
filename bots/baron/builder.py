# Baron builder roles — human task lists (check behavior here first)
#
# ## default — priority order (orchestrator: runDefault below)
# 1. buildHarvester — empty ore in vision / HiveMind → build (respect caps);
#        skip ore if visible enemy gunner is aiming at that tile
# 2. maintainHarvester
#    2a. checkOre — harvester → relayToCore; empty → build (same gunner-aim gate);
#        ally block → destroy; enemy gunner/sentinel/belt/launcher/barrier → placeGunnerOnOre
#    2b. relayToCore — pickRelayPath → neededBeltFacing → beltFacingOk /
#        clearWrongBelt → placeRelayBelt → watermark
# 3. defGun — near core only; enemy builder/gunner/sentinel in vision → placeGun
#        (gunners only — never sentinels)
# 4. defendHome — near core only; attack enemy in vision, else heal core if HP < 500
# 5. healAlly — core → gunner/sentinel → harvester
# 6. recheckOreNodes — clear sticky oreSite; prefer empty then any harvester
# # idleHome — commented out (soft-idle trap)
#
# ## harasser (orchestrator: runHarasser below; helpers in offense.py)
#    my_index > 0 and my_index % 3 == 0 (builders 3,6,9,…)
#    OR wave attack: WAVE_ATTACK_TURN / WAVE_ATTACK_TI in main.py (>0 = set)
# 1. avoidFire — under enemy gunner LoS → move outside LoS
# 2. offGun — placeGun/placeSentinel: core → gunner → harvester → launcher
# 3. seekHarvester — local_map enemy harvesters / scout ore nodes (vision updates map)
# 4. seekCore — moveToward enemy core

"""Builder role orchestrators — dispatch into logistics / offence helpers."""

from __future__ import annotations

from fcode import Controller, Position

from hivemind import CoreState, unpack_cores
from logistics import (
    buildHarvester,
    defendHome,
    healAlly,
    maintainHarvester,
    recheckOreNodes,
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


def runDefault(bot, ct: Controller) -> None:
    """Default role: build → maintain → defGun → defend → heal → recheck."""
    _resolveCores(bot, ct)

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
    if avoidFire(bot, ct):
        return
    if offGun(bot, ct):
        return
    if seekHarvester(bot, ct):
        return
    seekCore(bot, ct)


def runBuilderRoles(bot, ct: Controller) -> None:
    """Dispatch: every 3rd builder, or all builders once a wave gate fires."""
    idx = bot.hivemind.my_index
    if waveAttackActive(ct) or (idx > 0 and idx % 3 == 0):
        runHarasser(bot, ct)
    else:
        runDefault(bot, ct)


# Back-compat
def run_builder_roles(bot, ct: Controller) -> None:
    runBuilderRoles(bot, ct)
