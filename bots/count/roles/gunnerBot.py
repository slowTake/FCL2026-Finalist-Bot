"""Gunner role: fire, then rotate toward the next best target. Deliberately
thin — the logic lives in guns.py, same split as raid.py/roles/raidBot.py."""

from __future__ import annotations

from fcode import Controller

from guns import gunnerLoop


def runGunnerBot(bot, ct: Controller) -> None:
    gunnerLoop(bot, ct)
