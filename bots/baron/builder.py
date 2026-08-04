# Baron builder roles — human task lists (check behavior here first)
#
# Two role *names* for the rework (no preset assignment yet):
#
# ## default (logistics.py + defence.py when needed)
# 1. economy / relay — TBD
# 2. home defence — TBD
# 3. heal — TBD
# 4. move — TBD
#
# ## raider (offense.py)
# 1. siege_enemy_harvester — TBD
# 2. siege_loop — TBD
# 3. belt_raid — TBD
# 4. push_core — TBD

"""Builder role dispatch — assignment logic TBD; all builders use default until wired."""

from __future__ import annotations

from fcode import Controller


def run_builder_roles(bot, ct: Controller) -> None:
    """Dispatch one builder turn. Caller handles HiveMind tick and stuck tracking."""
    from logistics import run_default

    run_default(bot, ct)
