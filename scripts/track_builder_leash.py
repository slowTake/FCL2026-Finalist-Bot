#!/usr/bin/env python3
"""Track ally BUILDER_BOT distance-to-core over a whole replay; flag sustained
excursions past a leash radius.

Ground-truth (no fog of war), same technique as find_dead_relays.py: replay
PlaceEntity/MoveBuilderBot/RemoveEntity events to reconstruct every builder's
position on every turn, then compute Manhattan distance to the nearest tile
of that team's core footprint (2x2) each turn. Manhattan, not Chebyshev:
builders move cardinal-only and count's own guardian leash (GUARDIAN_LEASH,
guardianBot.py) is measured the same way — see docs/observations.md's "the
distance metric is Manhattan, everywhere" note. An earlier version of this
script measured Chebyshev, which under-reads diagonals and systematically
missed 45°-direction leash violations.

Built to debug "guardians not staying close to core" — a guardian is supposed
to stay within GUARDIAN_LEASH_WIDE(8, wide/quiet) or GUARDIAN_LEASH_TIGHT(4,
core under threat) of the core (bots/baron/policy.py, logistics_helpers.
guardianLeash). This can't identify *which* builders are guardians from the
replay alone (role is derived from hivemind.my_index, not recorded in the
replay) — it reports every ally builder's leash-violation runs, longest
first; cross-reference the reported entity id against the bot id shown in
the visualiser (fcode watch / the match URL) to confirm which ones are
guardians.

Usage:
    python scripts/track_builder_leash.py <replay.replay26> --team A [--leash 8] [--min-turns 15]
"""
from __future__ import annotations

import argparse
import sys

import florent_replay_pb2 as pb

TEAM_NAME = {pb.TEAM_A: "A", pb.TEAM_B: "B"}
NAME_TEAM = {v: k for k, v in TEAM_NAME.items()}


def core_footprint(pos: tuple[int, int]) -> set[tuple[int, int]]:
    x, y = pos
    return {(x, y), (x + 1, y), (x, y + 1), (x + 1, y + 1)}


def manhattan_to_footprint(pos: tuple[int, int], footprint: set[tuple[int, int]]) -> int:
    return min(abs(pos[0] - fx) + abs(pos[1] - fy) for fx, fy in footprint)


def load(path: str) -> pb.Replay:
    with open(path, "rb") as f:
        r = pb.Replay()
        r.ParseFromString(f.read())
    return r


def track(path: str, team_letter: str, leash: int, min_turns: int):
    r = load(path)
    team = NAME_TEAM[team_letter]
    enemy = pb.TEAM_B if team == pb.TEAM_A else pb.TEAM_A

    core_fp: set[tuple[int, int]] | None = None
    enemy_core_fp: set[tuple[int, int]] | None = None
    for c in r.map.cores:
        if c.team == team:
            core_fp = core_footprint((c.position.x, c.position.y))
        elif c.team == enemy:
            enemy_core_fp = core_footprint((c.position.x, c.position.y))
    if core_fp is None:
        print(f"No core found for team {team_letter}", file=sys.stderr)
        return [], {}

    pos: dict[int, tuple[int, int]] = {}
    kind: dict[int, str] = {}
    unit_team: dict[int, int] = {}
    alive: set[int] = set()

    # eid -> [start_turn, end_turn, max_ally_dist, sum_enemy_dist, sample_count]
    # for a contiguous over-leash run still open
    open_run: dict[int, list] = {}
    closed_runs: list[tuple[int, int, int, int, float | None]] = []
    tled_turns: dict[int, list[int]] = {}

    def close_run(eid: int, end_turn: int) -> None:
        run = open_run.pop(eid, None)
        if run is None:
            return
        start, _, max_d, sum_enemy_d, n = run
        length = end_turn - start
        if length >= min_turns:
            avg_enemy_d = (sum_enemy_d / n) if n and enemy_core_fp is not None else None
            closed_runs.append((eid, start, end_turn, max_d, avg_enemy_d))

    for turn_i, turn in enumerate(r.turns):
        for u in turn.updates:
            k = u.WhichOneof("kind")
            if k == "placeEntity":
                e = u.placeEntity.entity
                if e.WhichOneof("kind") == "builderBot":
                    pos[e.id] = (e.position.x, e.position.y)
                    kind[e.id] = "builderBot"
                    unit_team[e.id] = e.team
                    alive.add(e.id)
            elif k == "moveBuilderBot":
                m = u.moveBuilderBot
                if m.id in alive:
                    pos[m.id] = (m.to.x, m.to.y)
            elif k == "removeEntity":
                eid = u.removeEntity.id
                if eid in alive:
                    alive.discard(eid)
                    close_run(eid, turn_i)
            elif k == "botOutput":
                if u.botOutput.tled:
                    tled_turns.setdefault(u.botOutput.id, []).append(turn_i)

        for eid in list(alive):
            if unit_team.get(eid) != team or kind.get(eid) != "builderBot":
                continue
            d = manhattan_to_footprint(pos[eid], core_fp)
            if d > leash:
                enemy_d = (
                    manhattan_to_footprint(pos[eid], enemy_core_fp)
                    if enemy_core_fp is not None
                    else None
                )
                if eid not in open_run:
                    open_run[eid] = [turn_i, turn_i, d, enemy_d or 0, 1 if enemy_d is not None else 0]
                else:
                    run = open_run[eid]
                    run[1] = turn_i
                    run[2] = max(run[2], d)
                    if enemy_d is not None:
                        run[3] += enemy_d
                        run[4] += 1
            else:
                close_run(eid, turn_i)

    last_turn = len(r.turns) - 1
    for eid in list(open_run):
        close_run(eid, last_turn)

    closed_runs.sort(key=lambda row: row[2] - row[1], reverse=True)
    return closed_runs, tled_turns


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("replay")
    ap.add_argument("--team", choices=["A", "B"], default="A")
    ap.add_argument("--leash", type=int, default=8, help="Manhattan radius (default: count's GUARDIAN_LEASH=8)")
    ap.add_argument("--min-turns", type=int, default=15, help="only report runs at least this long")
    args = ap.parse_args()

    runs, tled = track(args.replay, args.team, args.leash, args.min_turns)

    if not runs:
        print(f"No ally builder stayed beyond Manhattan {args.leash} of the core for >= {args.min_turns} turns.")
        return

    print(
        f"{'Bot ID':>7} {'Start':>6} {'End':>6} {'Turns':>6} {'MaxDist':>8} "
        f"{'AvgEnemyDist':>13}  Note"
    )
    print("-" * 80)
    for eid, start, end, max_d, avg_enemy_d in runs:
        if avg_enemy_d is None:
            note = ""
            enemy_str = "n/a"
        elif avg_enemy_d <= 6:
            # HARASS_LEASH_CHEBYSHEV(4) + slack — consistently near the enemy
            # core looks like a harasser working as intended, not a stuck
            # guardian. Cross-check against the visualiser regardless.
            note = "near enemy core — likely a harasser (expected)"
            enemy_str = f"{avg_enemy_d:.1f}"
        else:
            note = "NOT near either core — worth checking"
            enemy_str = f"{avg_enemy_d:.1f}"
        print(f"{eid:>7} {start:>6} {end:>6} {end - start:>6} {max_d:>8} {enemy_str:>13}  {note}")
        tle_hits = [t for t in tled.get(eid, []) if start <= t <= end]
        if tle_hits:
            print(f"         TLE'd during this run at turns: {tle_hits}")


if __name__ == "__main__":
    main()
