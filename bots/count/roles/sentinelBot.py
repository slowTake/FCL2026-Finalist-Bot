"""Sentinel role: fire only — facing is permanent once built, so there is no
rotate step. Deliberately thin, same split as raid.py/roles/raidBot.py."""

from __future__ import annotations

from fcode import Controller

from guns import sentinelLoop


def runSentinelBot(bot, ct: Controller) -> None:
    sentinelLoop(bot, ct)
