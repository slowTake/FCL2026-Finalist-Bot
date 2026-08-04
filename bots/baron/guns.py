"""Turret placement (valid pads, fallbacks) and gunner/sentinel operation."""

from __future__ import annotations

from typing import Any

from fcode import Controller


def top_up_ammo(
    ct: Controller,
    *,
    local_map: dict[Any, Any] | None = None,
    min_turn: int = 0,
) -> None:
    """Ammo targets and convert amounts come from main policy constants."""
    del local_map
    if ct.get_current_round() < min_turn:
        return
    # Lazy import — main imports guns at load time.
    from main import (
        AMMO_BASE,
        AMMO_CONVERT_CHUNK,
        AMMO_MIN_POOL,
        AMMO_PER_GUNNER,
        AMMO_PER_SENTINEL,
        MAX_AMMO_POOL,
    )

    del (
        AMMO_BASE,
        AMMO_PER_GUNNER,
        AMMO_PER_SENTINEL,
        AMMO_CONVERT_CHUNK,
        AMMO_MIN_POOL,
        MAX_AMMO_POOL,
    )


def run_gunner(ct: Controller) -> None:
    del ct


def run_sentinel(ct: Controller) -> None:
    del ct


def place_gunner_aggressive(bot, ct: Controller, **kwargs: Any) -> bool:
    del bot, ct, kwargs
    return False


def place_sentinel_aggressive(bot, ct: Controller, **kwargs: Any) -> bool:
    del bot, ct, kwargs
    return False
