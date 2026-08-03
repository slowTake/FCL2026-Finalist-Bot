"""Builder role menu for earl: who runs what, and the dispatch call sites.

Role implementations stay in defence / attack / harrass / logistics.
Toggle ACTIVE_RAID_ORDER here (or keep default phalanx).
"""

from __future__ import annotations

from attack import is_raider, run_attack_builder
from defence import run_phalanx
from fcode import Controller, Position
from harrass import run_harrasser
from logistics import clear_relay, run_economy

# "phalanx" = even home defence / odd economy; "raiders" / "harrass" = overrides.
ACTIVE_RAID_ORDER = "phalanx"
# Fire-and-forget even waves: spawn cohort leaves at these marks; later evens refill.
PHALANX_WAVE1_TURN = 300
PHALANX_WAVE2_TURN = 600


def effective_raid_order(
    round_n: int, my_index: int = 0, spawn_round: int | None = None
) -> str:
    """Raid-path order for builders already on the raid task list.

    Under default phalanx: fire-and-forget even waves by spawn cohort —
    spawn < 300 leave at 300; spawn < 600 leave at 600; spawn >= 600 stay
    phalanx forever. Overrides return ACTIVE_RAID_ORDER.
    """
    if ACTIVE_RAID_ORDER != "phalanx":
        return ACTIVE_RAID_ORDER
    spawned = 0 if spawn_round is None else spawn_round
    if spawned < PHALANX_WAVE1_TURN and round_n >= PHALANX_WAVE1_TURN:
        return "raiders"
    if spawned < PHALANX_WAVE2_TURN and round_n >= PHALANX_WAVE2_TURN:
        return "raiders"
    return "phalanx"


def home_phalanx_active(round_n: int) -> bool:
    """True while home phalanx role is active (late even spawns refill after waves)."""
    del round_n
    return ACTIVE_RAID_ORDER == "phalanx"


def on_raid_tasklist(
    my_index: int,
    enemy_core: Position | None,
    round_n: int,
    spawn_round: int | None = None,
) -> bool:
    """Who runs phalanx / harrass / raiders this turn (enemy core must be known)."""
    del round_n, spawn_round
    if enemy_core is None or my_index <= 0:
        return False
    if ACTIVE_RAID_ORDER != "phalanx":
        return is_raider(my_index, enemy_core)
    # Default: every even is on the raid path (phalanx or raiders by cohort).
    return my_index % 2 == 0


def is_economy(
    my_index: int,
    enemy_core: Position | None,
    round_n: int,
    spawn_round: int | None = None,
) -> bool:
    """Odd builders on the mine/relay loop — not on the raid path."""
    if my_index <= 0:
        return False
    return not on_raid_tasklist(my_index, enemy_core, round_n, spawn_round)


def run_builder_roles(bot, ct: Controller) -> None:
    """Dispatch phalanx / harrass / raiders / economy for one builder turn.

    Caller has already resolved cores and updated stuck tracking.
    """
    round_n = ct.get_current_round()
    if bot.spawn_round is None:
        bot.spawn_round = round_n

    if on_raid_tasklist(
        bot.hivemind.my_index,
        bot.enemy_core_pos,
        round_n,
        bot.spawn_round,
    ):
        enemy = bot.enemy_core_pos
        assert enemy is not None
        clear_relay(bot)
        bot.ore_site = None
        order = effective_raid_order(
            round_n, bot.hivemind.my_index, bot.spawn_round
        )
        if order == "phalanx":
            site = run_phalanx(
                ct,
                my_index=bot.hivemind.my_index,
                local_map=bot.hivemind.local_map,
                enemy_core=enemy,
                ally_core=bot.core_pos,
            )
        elif order == "harrass":
            site = run_harrasser(
                ct,
                my_index=bot.hivemind.my_index,
                local_map=bot.hivemind.local_map,
                enemy_core=enemy,
                ally_core=bot.core_pos,
            )
        else:
            site = run_attack_builder(
                ct,
                my_index=bot.hivemind.my_index,
                local_map=bot.hivemind.local_map,
                enemy_core=enemy,
                ally_core=bot.core_pos,
            )
        if site is not None:
            bot.target = site
        bot._move_toward_target(ct, retarget=False)
        return

    run_economy(bot, ct)
