#!/usr/bin/env python3
"""Compare bot variants across every map and several seeds.

Each game runs in its own process so results are independent of the order
games are played in, unlike bot_gauntlet.py which shares one interpreter
(and therefore one `random` state) across all 15 matches.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

WORKER = """
import json, sys
from fcode.fcode_engine import run_game
bot, starter, engine_root, map_path, seed = sys.argv[1:6]
res = run_game(bot, starter, engine_root, map_path,
               "/tmp/ms-%s.replay26" % seed, int(seed), 0)
print("RESULT" + json.dumps({
    "winner": res["winner"],
    "cond": res["win_condition"],
    "a": res["a_titanium_collected"],
    "b": res["b_titanium_collected"],
}))
"""


def play(args: tuple[str, str, str, str, int]) -> dict:
    variant, bot, map_path, engine_root, seed = args
    starter = str(Path("bots/starter/main.py").resolve())
    proc = subprocess.run(
        [sys.executable, "-c", WORKER, bot, starter, engine_root, map_path, str(seed)],
        capture_output=True,
        text=True,
    )
    line = next(
        (l for l in proc.stdout.splitlines() if l.startswith("RESULT")), None
    )
    if line is None:
        return {
            "variant": variant,
            "map": Path(map_path).stem,
            "seed": seed,
            "error": proc.stderr[-400:],
        }
    out = json.loads(line[len("RESULT"):])
    out.update(variant=variant, map=Path(map_path).stem, seed=seed)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants", nargs="+", required=True,
                    help="name=path/to/main.py pairs")
    ap.add_argument("--seeds", nargs="+", type=int, default=[1, 2, 3, 4, 5])
    ap.add_argument("--workers", type=int, default=12)
    args = ap.parse_args()

    engine_root = str(Path(__import__("fcode").__file__).resolve().parent)
    maps = sorted(Path("maps").glob("*.map26"))

    jobs = []
    for spec in args.variants:
        name, path = spec.split("=", 1)
        for m in maps:
            for seed in args.seeds:
                jobs.append((name, str(Path(path).resolve()), str(m.resolve()),
                             engine_root, seed))

    print(f"{len(jobs)} games "
          f"({len(args.variants)} variants x {len(maps)} maps x {len(args.seeds)} seeds)")

    results = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for i, res in enumerate(pool.map(play, jobs), 1):
            results.append(res)
            if i % 25 == 0:
                print(f"  {i}/{len(jobs)}", flush=True)

    errors = [r for r in results if "error" in r]
    if errors:
        print(f"\n{len(errors)} games errored, e.g.:\n{errors[0]['error']}")

    wins: dict[str, int] = defaultdict(int)
    played: dict[str, int] = defaultdict(int)
    per_map: dict[tuple[str, str], list[str]] = defaultdict(list)
    for r in results:
        if "error" in r:
            continue
        played[r["variant"]] += 1
        won = r["winner"] == "A"
        wins[r["variant"]] += won
        per_map[(r["variant"], r["map"])].append("W" if won else "L")

    names = [s.split("=", 1)[0] for s in args.variants]
    print("\n=== Win rate over all maps and seeds ===")
    for n in names:
        print(f"  {n:<12} {wins[n]}/{played[n]}  "
              f"({100 * wins[n] / max(1, played[n]):.1f}%)")

    print(f"\n=== Per map (one letter per seed {args.seeds}) ===")
    print(f"{'map':<12}" + "".join(f"{n:<14}" for n in names))
    for m in maps:
        cells = "".join(f"{''.join(per_map[(n, m.stem)]):<14}" for n in names)
        print(f"{m.stem:<12}{cells}")

    Path("/tmp/multiseed_results.json").write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
