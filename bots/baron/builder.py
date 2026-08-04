# Baron builder roles — human task lists (check behavior here first)
#
# ## default (logistics.py) — priority order
# 1. buildHarvester — empty ore in vision / HiveMind → build (respect caps)
# 2. maintainHarvester
#    2a. checkOre — harvester → relayToCore; empty → build; ally block → destroy;
#        enemy gunner/sentinel/belt/launcher/barrier → placeGunnerOnOre (stub)
#    2b. relayToCore — pickRelayPath → neededBeltFacing → beltFacingOk /
#        clearWrongBelt → placeRelayBelt → watermark
# 3. defendHome — near core only (Chebyshev 8); not while relaying/rechecking;
#        attack enemy gunner/sentinel/builder in vision, else heal core if HP < 500
# 4. healAlly — core → gunner/sentinel → harvester
# 5. recheckOreNodes — clear sticky oreSite; prefer empty then any harvester
# # idleHome — commented out (soft-idle trap)
#
# ## raider (offense.py) — assignment TBD
# 1. siege — TBD
# 2. belt_raid — TBD
# 3. push_core — TBD

"""Builder role dispatch — all builders use default until assignment is wired."""

from __future__ import annotations

from fcode import Controller

from logistics import runDefault


def runBuilderRoles(bot, ct: Controller) -> None:
    """Dispatch one builder turn. Caller handles HiveMind tick and stuck tracking."""
    runDefault(bot, ct)


# Back-compat
def run_builder_roles(bot, ct: Controller) -> None:
    runBuilderRoles(bot, ct)
