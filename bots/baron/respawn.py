"""Core builder spawn — schedule knobs come from main."""

from __future__ import annotations

from dataclasses import dataclass

from fcode import (
    Controller,
    Direction,
    EntityType,
    GameConstants,
    Position,
)

from geometry import coreFootprint, manhattanToCore
from pathing import CARDINALS, inBounds


@dataclass(frozen=True)
class SpawnSchedule:
    max_builders_early: int
    max_builders: int
    builder_target_by_ti: tuple[tuple[int, int], ...] = ()
    # Rounds after game start before the crew may grow past max_builders_early.
    # ≤0 disables — see main.BUILDER_GROWTH_DELAY_TURNS.
    growth_delay_turns: int = 0


def _apronSites(ct: Controller, core_pos: Position) -> list[Position]:
    footprint = coreFootprint(core_pos)
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

    # Growth past the early rush is held back until growth_delay_turns has
    # elapsed. Passive income alone crosses most bank tiers well before any
    # real turn count justifies the extra crew, and nothing paces spawns
    # once the target is unlocked, so without this a bracket crossing spawns
    # its full jump back-to-back. Only clamps growth — never used to shrink
    # below max_builders_early, so the unpaced rush at game start is untouched.
    if schedule.growth_delay_turns and schedule.growth_delay_turns > 0:
        if ct.get_current_round() < schedule.growth_delay_turns:
            target = min(target, schedule.max_builders_early)
    return max(schedule.max_builders_early, min(target, schedule.max_builders))


def _safeSites(
    ct: Controller, sites: list[Position]
) -> tuple[list[Position], list[Position]]:
    """Split spawn candidates into (not under enemy gunner fire, everything).

    A brand-new builder has no HP margin to shrug off a shot it spawned
    straight into, so a covered apron tile must never be preferred over an
    uncovered one just because it's closer to `goal`. Only fall back to the
    unsafe list — last resort — when nothing on the apron is clear.
    """
    from offense import _enemyGunners, _tileUnderGunnerFire

    gunners = _enemyGunners(ct)
    if not gunners:
        return sites, sites
    safe = [s for s in sites if not _tileUnderGunnerFire(ct, s, gunners)]
    return safe, sites


def _aliveBuilders(hivemind, ct: Controller) -> int:
    """Living ally builders, from the core's ledger.

    Slot 4 counts builders *ever spawned* and must stay monotonic — it is the
    `my_index` allocator — so it cannot answer "how many are left". The ledger can:
    every builder broadcasts itself, and the core already garbage-collects ally
    BUILDER_BOT entries unseen for `total_builders + 2` turns, so this reads
    machinery that is maintained anyway.

    Clamped to the lifetime count so a duplicate or stale entry can never inflate it.
    Under-counting is still possible if a live builder misses a broadcast rotation;
    that costs an extra spawn, which is the safer direction to be wrong in.
    """
    me = ct.get_team()
    alive = 0
    for cell in hivemind.local_map.values():
        ent = cell.get("entity")
        if not ent:
            continue
        if ent.get("type") == EntityType.BUILDER_BOT and ent.get("team") == me:
            alive += 1
    return min(alive, ct.read_store(4))


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

        # Predict the about-to-spawn builder's index and, if it'll be a
        # default (idx % 5 in (3, 4), builder.py's role split), bias the
        # spawn tile toward whichever ore side this alternation slot owns,
        # instead of the shared goal — otherwise every default exits the core
        # toward the same side, so one ore cluster draws multiple builders
        # while an equally-close cluster on the other side sits unclaimed.
        # spawn_builder reads slot 4 *before* incrementing (hivemind.py:211),
        # so the next spawn's index is one past the current value.
        predicted_idx = ct.read_store(4) + 1
        spawn_goal = goal
        if predicted_idx % 5 in (3, 4) and hivemind.map_id:
            from features import oreSideBuckets

            left, right = oreSideBuckets(hivemind.map_id, core_pos, goal)

            # Rank the two buckets by distance to home, not by which one
            # oreSideBuckets happened to call "left" — "left"/"right" come from
            # a fixed 90deg-CCW rotation of *this* core's own core->enemy
            # vector, which has no notion of which side is actually nearer
            # home. On a map where both cores share a coordinate (e.g. heart:
            # both at y=9), that rotation's handedness is a function of which
            # side of the map a team's core sits on — so it deterministically
            # handed the *same* team the richer/nearer bucket first, every
            # match, regardless of which side genuinely was nearer. Distance-
            # ranking instead makes "group 0 goes first" mean the same thing
            # (closer, faster to reach) for both teams.
            def _nearestInPool(pool: list[Position]) -> int:
                return min(
                    (manhattanToCore(o, core_pos) for o in pool), default=10**9
                )

            near_pool, far_pool = (
                (left, right) if _nearestInPool(left) <= _nearestInPool(right)
                else (right, left)
            )
            side_pool = near_pool if (predicted_idx // 5) % 2 == 0 else far_pool
            if not side_pool:
                side_pool = far_pool or near_pool
            if side_pool:
                # manhattanToCore, not raw distance to core_pos (the core's
                # top-left/min-corner anchor tile) — the anchor is one specific
                # corner of the 2x2 footprint, so raw distance is biased by up
                # to 2 tiles depending which corner a team's core happens to
                # occupy. On a mirrored map that bias flips per team: the same
                # pitfall fixed in logistics.py's ore pickers (see
                # geometry.manhattanToCore's docstring), just here for which
                # ore a fresh default's spawn tile gets biased toward.
                spawn_goal = min(side_pool, key=lambda o: manhattanToCore(o, core_pos))

        sites = _apronSites(ct, core_pos)
        sites.sort(key=lambda p: abs(p.x - spawn_goal.x) + abs(p.y - spawn_goal.y))
        safe_sites, all_sites = _safeSites(ct, sites)

        cardinal_sites = [core_pos.add(d) for d in CARDINALS]
        safe_cardinals, all_cardinals = _safeSites(ct, cardinal_sites)

        # Safe pass first (apron, then cardinal fallback); only once every safe
        # option is exhausted do we retry the same order accepting gunfire —
        # spawning into a covered tile is a last resort, not a preference.
        for site in safe_sites:
            if hivemind.spawn_builder(ct, site):
                self.num_spawned += 1
                return
        for site in safe_cardinals:
            if hivemind.spawn_builder(ct, site):
                self.num_spawned += 1
                return
        for site in all_sites:
            if hivemind.spawn_builder(ct, site):
                self.num_spawned += 1
                return
        for site in all_cardinals:
            if hivemind.spawn_builder(ct, site):
                self.num_spawned += 1
                return
