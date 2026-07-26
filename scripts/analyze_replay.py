#!/usr/bin/env python3
"""Assert logistics health from a .replay26 file (Team A = magnum_grokus)."""

from __future__ import annotations

import sys

import florent_replay_pb2

TEAM_A = florent_replay_pb2.TEAM_A


def analyze(path: str) -> dict:
    with open(path, "rb") as f:
        replay = florent_replay_pb2.Replay()
        replay.ParseFromString(f.read())

    a_harvesters = 0
    a_conveyors = 0
    a_splitters = 0
    harvesters_by_turn_200 = 0
    a_collected = 0

    for turn_i, turn in enumerate(replay.turns):
        for update in turn.updates:
            kind = update.WhichOneof("kind")
            if kind == "placeEntity":
                e = update.placeEntity.entity
                if e.team != TEAM_A:
                    continue
                if e.HasField("harvester"):
                    a_harvesters += 1
                    if turn_i <= 200:
                        harvesters_by_turn_200 += 1
                elif e.HasField("conveyor"):
                    a_conveyors += 1
                elif e.HasField("splitter"):
                    a_splitters += 1
            elif kind == "updatePlayers":
                a_collected = update.updatePlayers.players.a.titaniumCollected

    winner = None
    if replay.HasField("winner"):
        winner = "A" if replay.winner == TEAM_A else "B"

    return {
        "path": path,
        "turns": len(replay.turns),
        "winner": winner,
        "a_collected": a_collected,
        "a_harvesters": a_harvesters,
        "a_conveyors": a_conveyors,
        "a_splitters": a_splitters,
        "harvesters_by_turn_200": harvesters_by_turn_200,
    }


def check(stats: dict, map_name: str) -> list[str]:
    errors: list[str] = []
    if stats["a_collected"] <= 0:
        errors.append(f"{map_name}: team A titaniumCollected={stats['a_collected']} (want > 0)")
    if "fjord" in map_name and stats["harvesters_by_turn_200"] < 2:
        errors.append(
            f"{map_name}: harvesters by turn 200={stats['harvesters_by_turn_200']} (want >= 2)"
        )
    if stats["a_conveyors"] < stats["a_splitters"]:
        errors.append(
            f"{map_name}: conveyors={stats['a_conveyors']} < splitters={stats['a_splitters']}"
        )
    return errors


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python analyze_replay.py <replay1.replay26> [replay2 ...]")
        sys.exit(1)

    all_errors: list[str] = []
    for path in sys.argv[1:]:
        stats = analyze(path)
        map_name = path.split("/")[-1].replace(".replay26", "")
        print(f"--- {path} ---")
        print(
            f"  turns={stats['turns']} winner={stats['winner']} "
            f"collected={stats['a_collected']} "
            f"harvesters={stats['a_harvesters']} "
            f"conveyors={stats['a_conveyors']} splitters={stats['a_splitters']}"
        )
        all_errors.extend(check(stats, map_name))

    if all_errors:
        print("\nFAIL:")
        for err in all_errors:
            print(f"  - {err}")
        sys.exit(1)

    print("\nOK: all replay checks passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
