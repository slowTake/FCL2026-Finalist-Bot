#!/usr/bin/env python3
import sys
from pathlib import Path
import concurrent.futures

REPO_DIR = Path(__file__).resolve().parent.parent
FCV_DIR = REPO_DIR / "florent-code-visualiser"
if FCV_DIR.exists() and str(FCV_DIR) not in sys.path:
    sys.path.insert(0, str(FCV_DIR))

import fcv.runner as runner_mod
import fcv.replay as replay_mod
import fcv.metrics as metrics_mod

def run_one(args):
    bot_a, bot_b, map_name, seed, is_swapped, out_dir, fcode_bin = args
    maps_dir = REPO_DIR / "maps"
    map_path = str(maps_dir / f"{map_name}.map26")
    try:
        res = runner_mod.run_game(
            bot_a if not is_swapped else bot_b,
            bot_b if not is_swapped else bot_a,
            map_path,
            seed=seed,
            out_dir=out_dir,
            fcode=fcode_bin,
            swapped=is_swapped
        )
        if not res.replay or not Path(res.replay).exists():
            return map_name, seed, is_swapped, "ERROR", "No replay", 0, 0, 0
        rep = replay_mod.Replay.load(res.replay)
        series, _ = metrics_mod.compute(rep)
        
        bot_a_team = "TEAM_A" if not is_swapped else "TEAM_B"
        bot_b_team = "TEAM_B" if not is_swapped else "TEAM_A"
        
        a_series_data = series.data.get(bot_a_team, {})
        b_series_data = series.data.get(bot_b_team, {})
        s_ti = a_series_data.get("collected", [0])[-1] if a_series_data else 0
        b_ti = b_series_data.get("collected", [0])[-1] if b_series_data else 0

        winner = "sprint" if rep.winner == bot_a_team else ("baron" if rep.winner == bot_b_team else "DRAW")
        return map_name, seed, is_swapped, winner, rep.win_condition, rep.total_turns, s_ti, b_ti
    except Exception as e:
        return map_name, seed, is_swapped, "ERROR", str(e), 0, 0, 0

def main():
    maps_dir = REPO_DIR / "maps"
    maps = sorted([p.stem for p in maps_dir.glob("*.map26")])
    if len(sys.argv) > 1:
        maps = sys.argv[1:]
    
    out_dir = REPO_DIR / "test_results"
    out_dir.mkdir(exist_ok=True)
    fcode_bin = runner_mod.find_fcode()
    
    tasks = []
    for m in maps:
        tasks.append(("bots/sprint", "bots/baron", m, 1, False, out_dir, fcode_bin))
        tasks.append(("bots/sprint", "bots/baron", m, 1, True, out_dir, fcode_bin))

    print(f"{'Map':<18} {'Side':<8} {'Winner':<8} {'Condition':<20} {'Turns':<6} {'Sprint Ti':<10} {'Baron Ti':<10}")
    print("-" * 85)
    sys.stdout.flush()

    sprint_wins = 0
    baron_wins = 0
    draws = 0
    
    map_results = {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(run_one, t): t for t in tasks}
        for future in concurrent.futures.as_completed(futures):
            map_name, seed, is_swapped, winner, cond, turns, s_ti, b_ti = future.result()
            side = "Orange" if is_swapped else "Blue"
            print(f"{map_name:<18} {side:<8} {winner:<8} {cond:<20} {turns:<6} {s_ti:<10} {b_ti:<10}")
            sys.stdout.flush()
            
            if map_name not in map_results:
                map_results[map_name] = []
            map_results[map_name].append(winner)
            
            if winner == "sprint":
                sprint_wins += 1
            elif winner == "baron":
                baron_wins += 1
            else:
                draws += 1

    print("=" * 85)
    print(f"TOTAL: Sprint {sprint_wins} - Baron {baron_wins} (Draws: {draws})")
    print("\nMAP SUMMARY:")
    for m in sorted(map_results.keys()):
        res = map_results[m]
        print(f"  {m:<20}: {res}")

if __name__ == "__main__":
    main()
