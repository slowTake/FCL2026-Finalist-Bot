"""Economy: ore targeting, harvesters, inbound relay — default builder role."""

from __future__ import annotations

from fcode import Controller, Position


def run_default(bot, ct: Controller) -> None:
    """Default builder role: economy and home duties (see builder.py task list)."""
    run_economy(bot, ct)


def run_economy(bot, ct: Controller) -> None:
    del bot, ct


def pick_economy_target(bot, ct: Controller) -> Position | None:
    del bot, ct
    return None


def try_ore_site_task(bot, ct: Controller) -> bool:
    del bot, ct
    return False
