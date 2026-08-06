"""Core builder spawn — schedule knobs come from main."""

from __future__ import annotations

from dataclasses import dataclass

from fcode import Controller, Direction, GameConstants, Position

from pathing import CARDINALS, inBounds


@dataclass(frozen=True)
class SpawnSchedule:
    max_builders_early: int
    max_builders: int
    builder_target_by_ti: tuple[tuple[int, int], ...] = ()


def _coreFootprint(core_pos: Position) -> list[Position]:
    return [
        core_pos,
        Position(core_pos.x + 1, core_pos.y),
        Position(core_pos.x, core_pos.y + 1),
        Position(core_pos.x + 1, core_pos.y + 1),
    ]


def _apronSites(ct: Controller, core_pos: Position) -> list[Position]:
    footprint = _coreFootprint(core_pos)
    keys = {(t.x, t.y) for t in footprint}
    sites: list[Position] = []
    seen: set[tuple[int, int]] = set()
    for tile in footprint:
        for d in Direction:
            if d == Direction.CENTRE:
                continue
            cand = tile.add(d)
            key = (cand.x, cand.y)
            if key in seen or key in keys:
                continue
            if not inBounds(ct, cand):
                continue
            seen.add(key)
            sites.append(cand)
    return sites


def _builderTarget(ct: Controller, schedule: SpawnSchedule) -> int:
    """How many *living* builders the current bank justifies.

    Replaces `max_builders_early + round_n // interval`, which applied the current
    Ti tier to the whole game so far: crossing a bracket leapt the target and spawned
    a run of builders back to back, and falling back froze spawning for hundreds of
    rounds because the lifetime counter it compared against never decreases. Reading
    the bank fresh each turn has neither failure — a falling bank simply stops growth.
    """
    ti = ct.get_global_resources()
    target = schedule.max_builders_early
    for min_ti, count in sorted(
        schedule.builder_target_by_ti, key=lambda t: t[0], reverse=True
    ):
        if ti >= min_ti:
            target = count
            break
    return max(schedule.max_builders_early, min(target, schedule.max_builders))


def _aliveBuilders(hivemind, ct: Controller) -> int:
    """Living ally builders, derived from the exact unit count.

    Slot 4 counts builders *ever spawned* and must stay monotonic — it is the
    `my_index` allocator — so it cannot answer "how many are left".

    Counting ally BUILDER_BOT cells in the ledger cannot either, which is what this
    used to do. A builder broadcasts its *current* position each rotation and nothing
    removes its previous cell; a vacated tile clears only once some unit sees it empty
    and that clear wins one of 16 delta slots, at the lowest priority. So the ledger
    holds a trail of stale cells and the count over-reports — and clamping it to the
    lifetime total then pinned the answer to exactly that. Growth still worked, but
    replacement never fired: after a death both the lifetime count and the trail were
    unchanged, so the crew shrank permanently.

    `get_unit_count()` is exact. We never build launchers, so living units are the core
    plus builders plus turrets, and turrets are immobile — no trail — so subtracting
    them from the ledger is sound.
    """
    from guns import allyTurretCounts

    gunners, sentinels = allyTurretCounts(ct, hivemind.local_map)
    alive = ct.get_unit_count() - 1 - gunners - sentinels
    return max(0, min(alive, ct.read_store(4)))


class Respawn:
    def __init__(self) -> None:
        self.num_spawned = 0

    def run_core_spawn(
        self,
        ct: Controller,
        *,
        schedule: SpawnSchedule,
        goal: Position,
        hivemind,
    ) -> None:
        """Keep as many living builders as the bank justifies.

        No pacing: the engine already allows one spawn per turn and the target is
        bounded, so growth is gradual by construction. Spending on a builder can drop
        the bank back under a bracket, which self-limits — and cannot oscillate,
        because the target is re-read from the bank each turn while the living count
        only falls through death.
        """
        if ct.get_action_cooldown() != 0:
            return
        if ct.get_unit_count() >= GameConstants.MAX_TEAM_UNITS:
            return
        if ct.get_global_resources() < ct.get_builder_bot_cost():
            return

        if _aliveBuilders(hivemind, ct) >= _builderTarget(ct, schedule):
            return

        core_pos = ct.get_position()
        sites = _apronSites(ct, core_pos)
        sites.sort(key=lambda p: abs(p.x - goal.x) + abs(p.y - goal.y))
        for site in sites:
            if hivemind.spawn_builder(ct, site):
                self.num_spawned += 1
                return
        for d in CARDINALS:
            site = core_pos.add(d)
            if hivemind.spawn_builder(ct, site):
                self.num_spawned += 1
                return
