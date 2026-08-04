"""Home defence helpers — used inside default role, not a separate builder role."""

from __future__ import annotations

from fcode import Controller


def run_phalanx(bot, ct: Controller) -> None:
    del bot, ct


def update_phalanx_builder_threat(
    ct: Controller,
    *,
    sticky_until: int,
    last_side: int,
) -> tuple[int, int]:
    del ct
    return sticky_until, last_side
