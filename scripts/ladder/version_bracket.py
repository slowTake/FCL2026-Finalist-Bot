#!/usr/bin/env python3
"""Throwaway single-elimination bracket across every submission we've ever
put on the ladder — v1 (turtleHive) through whatever's active today.

For fun, not a serious strength comparison: most of the field is
years-old earl/turtleHive/early-baron code that current count already
routs. Re-run it as many times as you like — the bracket's SLOTTING is
deterministic (see seeding below), but which maps get sampled per matchup
is still random each run, so close matchups can go either way run to run.

Seeded by recency, not shuffled: the newest version is seed 1 (assumed
strongest), the oldest is the highest (weakest) seed, and entrants are
placed using the standard tournament bracket slot order (1v16, 8v9, 5v12,
4v13, ... — the classic construction that keeps strong seeds apart as long
as possible and hands the byes, when padding to a power of two, to the
TOP seeds rather than random ones). This also means "expected result" is
well-defined: a chalk simulation (better seed always wins) is run
alongside the real bracket, and anyone who reaches a later round for real
than chalk predicted is reported as an overperformer at the end — that's
how you tell an old version "went further than expected."

For an actual development tool (curated milestones, real fcv_gauntlet
diagnostics, focused on the bot under test), see milestone_gauntlet.py
instead — this script is deliberately the dumb, fast, just-for-kicks one.

Each matchup gets its own `fcv run` subprocess call (never one giant batch
covering the whole bracket) — same reasoning as optuna/tune_bot.py: the
sandbox enforces a cumulative-CPU ceiling per process tree, and a 100+
entrant bracket run as one long-lived process would eventually hit it.

Usage:
    python3 scripts/ladder/version_bracket.py
    python3 scripts/ladder/version_bracket.py --games-per-match 5
    python3 scripts/ladder/version_bracket.py --map-seed 42   # reproduce one run's map sampling
    python3 scripts/ladder/version_bracket.py --jobs 8
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))       # sibling: submission_cache
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # scripts/: live_map_pool
from live_map_pool import live_map_pool  # noqa: E402
from submission_cache import REPO_ROOT, list_submissions, ensure_local, slug  # noqa: E402


def find_fcv_bin() -> str:
    venv_fcv = REPO_ROOT / ".venv" / "bin" / "fcv"
    if venv_fcv.exists() and os.access(venv_fcv, os.X_OK):
        return str(venv_fcv)
    path_fcv = shutil.which("fcv")
    if path_fcv:
        return path_fcv
    print("Could not locate `fcv`. See optuna/README.md's setup section.", file=sys.stderr)
    sys.exit(1)


def play_match(
    fcv_bin: str, a_dir: Path, b_dir: Path, maps: list[str], jobs: int, out_dir: Path
) -> tuple[int, int]:
    """One fcv subprocess call, every map played once. Returns (a_wins, b_wins)."""
    cmd = [
        fcv_bin, "run", str(a_dir), str(b_dir),
        "--maps", *maps, "--seeds", "1", "--jobs", str(jobs), "--out", str(out_dir),
    ]
    subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, timeout=60 * len(maps) + 120)
    batch_path = out_dir / "batch.json"
    if not batch_path.exists():
        return 0, 0
    data = json.loads(batch_path.read_text(encoding="utf-8"))
    a_wins = b_wins = 0
    for g in data.get("games_detail", []):
        if not g.get("ok"):
            continue
        a_is_side_a = Path(g["bot_a"]).resolve() == a_dir.resolve()
        a_won = (g.get("winner") == "A") if a_is_side_a else (g.get("winner") == "B")
        if a_won:
            a_wins += 1
        else:
            b_wins += 1
    return a_wins, b_wins


def _seed_slot_order(n: int) -> list[int]:
    """Standard tournament bracket seeding order for a full 2^k bracket —
    the classic recursive construction (1v8,4v5,2v7,3v6 for n=8) that keeps
    strong seeds apart as long as possible. At the final doubling step
    every seed i is paired with (n+1-i), so when the field is padded with
    high (weak) seed numbers standing in for "no entrant", those byes land
    on the TOP seeds' first-round matches, exactly like a real tournament."""
    order = [1]
    while len(order) < n:
        size = len(order) * 2
        order = [x for seed in order for x in (seed, size + 1 - seed)]
    return order


def build_bracket(entrants: list[dict]) -> list[dict | None]:
    """Seeded by recency — newest version = seed 1 (strongest expected),
    oldest = the weakest seed. See module docstring for why this matters:
    it's what makes "went further than expected" a well-defined question."""
    ranked = sorted(entrants, key=lambda s: s["version"], reverse=True)
    n = 2 ** math.ceil(math.log2(len(ranked)))
    return [ranked[s - 1] if s <= len(ranked) else None for s in _seed_slot_order(n)]


def run_bracket(
    bracket: list[dict | None], decide: Callable[[dict, dict, int], dict]
) -> tuple[dict, dict[str, int], int]:
    """Generic single-elimination runner, shared by the real playthrough and
    the chalk (better-seed-always-wins) simulation used to compute
    "expected" results — same structure, different `decide` function, so
    the two are guaranteed to be comparing apples to apples.

    decide(a, b, round_num) -> winner (a or b). Only called for real
    (non-bye) pairings. Returns (champion, elim_round, total_rounds), where
    elim_round maps each entrant's slug to the round they were eliminated
    in (the champion is absent from the dict).
    """
    elim_round: dict[str, int] = {}
    round_num = 1
    while len(bracket) > 1:
        next_round: list[dict | None] = []
        for i in range(0, len(bracket), 2):
            a, b = bracket[i], bracket[i + 1]
            if a is None and b is None:
                next_round.append(None)
                continue
            if a is None:
                next_round.append(b)
                continue
            if b is None:
                next_round.append(a)
                continue
            winner = decide(a, b, round_num)
            loser = b if winner is a else a
            elim_round[slug(loser)] = round_num
            next_round.append(winner)
        bracket = next_round
        round_num += 1
    return bracket[0], elim_round, round_num - 1


def _chalk_decide(a: dict, b: dict, _round_num: int) -> dict:
    return a if a["version"] > b["version"] else b


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--games-per-match", type=int, default=3,
                     help="Random maps per matchup, one game each, majority wins (default: 3)")
    ap.add_argument("--map-seed", type=int, default=None,
                     help="RNG seed for per-matchup map sampling — omit for fresh sampling each run")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--out", default="test_results/version_bracket")
    args = ap.parse_args()

    fcv_bin = find_fcv_bin()
    map_rng = random.Random(args.map_seed)

    print("Fetching submission list...")
    subs = list_submissions()
    print(f"{len(subs)} submissions found (v{subs[0]['version']}..v{subs[-1]['version']}).")
    print("Downloading/caching any not already local (first run only)...")
    for s in subs:
        ensure_local(s)
    print("All cached.\n")

    pool = live_map_pool(REPO_ROOT / "bots" / "count")
    if not pool:
        pool = sorted(p.stem for p in (REPO_ROOT / "maps").glob("*.map26"))

    out_root = REPO_ROOT / args.out
    if out_root.exists():
        shutil.rmtree(out_root)

    bracket = build_bracket(subs)
    total_entrants = len(subs)
    print(f"Seeded: {slug(bracket[0])} (seed 1) .. {slug([e for e in bracket if e][-1])} "
          f"(seed {total_entrants}), {len(bracket) - total_entrants} byes to the top seeds.\n")

    def real_decide(a: dict, b: dict, round_num: int) -> dict:
        a_dir, b_dir = ensure_local(a), ensure_local(b)
        maps = map_rng.sample(pool, min(args.games_per_match, len(pool)))
        match_out = out_root / f"r{round_num}_{slug(a)}_vs_{slug(b)}"
        a_wins, b_wins = play_match(fcv_bin, a_dir, b_dir, maps, args.jobs, match_out)
        winner = a if a_wins >= b_wins else b
        print(f"  R{round_num}: {slug(a)} vs {slug(b)} on {maps}: {a_wins}-{b_wins} -> {slug(winner)}")
        return winner

    print("=== Playing the real bracket ===")
    champion, real_elim, total_rounds = run_bracket(bracket, real_decide)
    print(f"\n\U0001f3c6 Champion: {slug(champion)}\n")

    _, chalk_elim, _ = run_bracket(bracket, _chalk_decide)

    def effective_round(entrant: dict, elim: dict[str, int]) -> int:
        return elim.get(slug(entrant), total_rounds + 1)  # +1 = "won it all"

    overperformers = []
    for e in subs:
        actual = effective_round(e, real_elim)
        expected = effective_round(e, chalk_elim)
        if actual > expected:
            overperformers.append((actual - expected, e, actual, expected))
    overperformers.sort(key=lambda t: -t[0])

    if overperformers:
        print("=== Went further than seeding predicted ===")
        for delta, e, actual, expected in overperformers:
            print(f"  {slug(e):<28} reached round {actual} (seed expected round {expected}, +{delta})")
    else:
        print("No upsets — every result matched what recency-seeding predicted.")


if __name__ == "__main__":
    main()
