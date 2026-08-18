"""Don't abandon a task over something transient.

A builder that can't act this turn can usually act in two or three. Dropping the
task instead wastes the walk out, orphans a half-built relay, and often re-picks
the same target next turn anyway. Every "should I give up yet?" question goes
through holdTask — one counter, not one per call site.
"""

from __future__ import annotations

from fcode import Controller

TI_WAIT_TURNS = 5      # 10 Ti arrives every 4 rounds; a conveyor is 3
BLOCKED_WAIT_TURNS = 4  # an ally builder in the way will move
CLEAR_WAIT_TURNS = 12   # grinding down an enemy belt is ~10 builder-turns


def holdTask(bot, reason: str, limit: int) -> bool:
    """True = keep holding, caller returns without abandoning.
    False = patience spent, caller may give up.

    A different reason resets the counter, so a builder blocked by two things in
    sequence doesn't silently burn one budget on both.
    """
    if getattr(bot, "holdReason", None) != reason:
        bot.holdReason = reason
        bot.holdTurns = 0
    bot.holdTurns += 1
    return bot.holdTurns <= limit


def clearHold(bot) -> None:
    """Call on any real progress."""
    bot.holdReason = None
    bot.holdTurns = 0


def waitTi(bot, ct: Controller, cost: int) -> bool:
    """True if we're short on titanium and should wait rather than abandon."""
    if ct.get_global_resources() >= cost:
        return False
    return holdTask(bot, "ti", TI_WAIT_TURNS)


def stallCheck(mem: dict, key, value: int, window: int) -> bool:
    """Track whether `value` is still falling (progress) for `key` across calls —
    e.g. an enemy building's HP while grinding it down. False while `value` keeps
    setting a new low; True the `window`-th consecutive call in a row that fails
    to, meaning something is undoing the progress as fast as it's made (a repair)
    and the caller should give up the trade.

    Call only on a turn `key` is actually being engaged. Counting *consecutive
    checks*, not elapsed rounds, is the point: an absence (busy elsewhere, pushed
    off, broke) simply leaves the entry untouched instead of accruing against
    wall-clock time, so resuming after one never reads as an instant stall on the
    very first look back. The caller owns cleanup — call stallForget on give-up
    and whenever `key` is confirmed gone (destroyed, rebuilt, no longer matches),
    so a stale low can never misjudge a fresh occurrence of the same key.
    """
    low, stale = mem.get(key, (value + 1, 0))
    if value < low:
        mem[key] = (value, 0)
        return False
    stale += 1
    if stale >= window:
        return True
    mem[key] = (low, stale)
    return False


def stallForget(mem: dict, key) -> None:
    """Drop tracked progress for `key`. Call on give-up, and whenever `key` is
    confirmed gone (destroyed, rebuilt, no longer matches) so a stale low can't
    misjudge a fresh occurrence of it."""
    mem.pop(key, None)
