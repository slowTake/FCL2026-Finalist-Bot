# Baron builder roles — human task lists (check behavior here first)
#
# ## default — priority order (orchestrator: runDefault below)
# 1. buildHarvester — empty ore in vision / HiveMind → build (respect caps)
# 2. maintainHarvester
#    2a. checkOre — harvester → relayToCore; empty → build; ally block → destroy;
#        enemy gunner/sentinel/belt/launcher/barrier → placeGunnerOnOre (stub)
#    2b. relayToCore — pickRelayPath → neededBeltFacing → beltFacingOk /
#        clearWrongBelt → placeRelayBelt → watermark
# 3. defGun — near core only; enemy builder/gunner/sentinel in vision → placeGun
#        (gunners only — never sentinels)
# 4. defendHome — near core only; attack enemy in vision, else heal core if HP < 500
# 5. healAlly — core → gunner/sentinel → harvester
# 6. recheckOreNodes — clear sticky oreSite; prefer empty then any harvester
# # idleHome — commented out (soft-idle trap)
#
# ## raider (offense.py) — assignment TBD
# 1. siege — TBD
# 2. belt_raid — TBD
# 3. push_core — TBD

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


def runDefault(bot, ct: Controller) -> None:
    """Default role: build → maintain → defGun → defend → heal → recheck."""
    if bot.corePos is None:
        from main import SLOT_PACKED_CORES

        ally, enemy = unpack_cores(ct.read_store(SLOT_PACKED_CORES))
        if ally[0] != CoreState.UNKNOWN:
            bot.corePos = Position(ally[1], ally[2])
        if enemy[0] != CoreState.UNKNOWN:
            bot.enemyCorePos = Position(enemy[1], enemy[2])
        elif bot.hivemind.enemy_core_pos is not None:
            bot.enemyCorePos = bot.hivemind.enemy_core_pos

    if defGun(bot, ct):
        return
    if buildHarvester(bot, ct):
        return
    if maintainHarvester(bot, ct):
        return
    if defendHome(bot, ct):
        return
    if healAlly(bot, ct):
        return
    recheckOreNodes(bot, ct)


def runBuilderRoles(bot, ct: Controller) -> None:
    """Role menu — all builders use default until assignment is wired."""
    runDefault(bot, ct)


# Back-compat
def run_builder_roles(bot, ct: Controller) -> None:
    runBuilderRoles(bot, ct)
