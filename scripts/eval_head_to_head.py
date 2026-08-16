#!/usr/bin/env python3
import sys
import os
import glob
from pathlib import Path
import concurrent.futures

REPO_DIR = Path(__file__).resolve().parent.parent
FCV_DIR = REPO_DIR / "florent-code-visualiser"
if FCV_DIR.exists() and str(FCV_DIR) not in sys.path:
    sys.path.insert(0, str(FCV_DIR))

import fcv.runner as runner_mod
import fcv.replay as replay_mod
import fcv.metrics as metrics_mod

def run_match(bot_a, bot_b, map_name, seed=1, is_swapped=False, out_dir=None, fcode_bin=None):
    maps_dir = REPO_DIR / "maps"
    map_path = str(maps_dir / f"{map_name}.map26")
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
        return map_name, is_swapped, "ERROR", "No replay", 0, 0, 0
    rep = replay_mod.Replay.load(res.replay)
    series, _ = metrics_mod.compute(rep)
    
    a_team = "TEAM_A" if not is_swapped else "TEAM_B"
    b_team = "TEAM_B" if not is_swapped else "TEAM_A"
    
    a_data = series.data.get(a_team, {})
    b_data = series.data.get(b_team, {})
    a_ti = a_data.get("collected", [0])[-1] if a_data else 0
    b_ti = b_data.get("collected", [0])[-1] if b_data else 0

    if rep.winner == a_team:
        winner = "sprint"
    elif rep.winner == b_team:
        winner = "baron"
    else:
        winner = "DRAW"
    return map_name, is_swapped, winner, rep.win_condition, rep.total_turns, a_ti, b_ti

def main():
    maps_dir = REPO_DIR / "maps"
    map_filter = sys.argv[1:] if len(sys.argv) > 1 else None
    if map_filter:
        maps = [m.replace(".map26", "") for m in map_filter]
    else:
        maps = sorted([p.stem for p in maps_dir.glob("*.map26")])
    
    out_dir = REPO_DIR / "test_results"
    out_dir.mkdir(exist_ok=True)
    fcode_bin = runner_mod.find_fcode()
    
    results = {}
    
    def task_func(m, swapped):
        return run_match("bots/sprint", "bots/baron", m, seed=1, is_swapped=swapped, out_dir=out_dir, fcode_bin=fcode_bin)

    tasks = []
    for m in maps:
        tasks.append((m, False))
        tasks.append((m, True))

    print(f"{'Map':<20} {'Orientation':<15} {'Winner':<10} {'Condition':<22} {'Turns':<6} {'Sprint Ti':<10} {'Baron Ti':<10}")
    print("=" * 95)

    sprint_total = 0
    baron_total = 0
    draw_total = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(task_func, m, s): (m, s) for (m, s) in tasks}
        for future in concurrent.futures.as_completed(futures):
            m, s, winner, cond, turns, s_ti, b_ti = future.result()
            orient = "Sprint=Orange" if s else "Sprint=Blue"
            print(f"{m:<20} {orient:<15} {winner:<10} {cond:<22} {turns:<6} {s_ti:<10} {b_ti:<10}")
            sys.stdout.flush()
            if m not in results:
                results[m] = {}
            results[m][orient] = (winner, cond, turns, s_ti, b_ti)
            if winner == "sprint":
                sprint_total += 1
            elif winner == "baron":
                baron_total += 1
            else:
                draw_total += 1

    print("\n" + "=" * 95)
    print(f"SUMMARY SCORE: Sprint {sprint_total} - {baron_total} Baron (Draws: {draw_total}) out of {len(tasks)} matches")
    print("=" * 95)
    
    maps_lost = []
    maps_won = []
    maps_tied = []
    
    for m in sorted(results.keys()):
        m_res = results[m]
        w1 = m_res.get("Sprint=Blue", ("-",))[0]
        w2 = m_res.get("Sprint=Orange", ("-",))[0]
        if w1 == "sprint" and w2 == "sprint":
            maps_won.append(m)
        elif w1 == "baron" or w2 == "baron":
            maps_lost.append((m, w1, w2))
        else:
            maps_tied.append((m, w1, w2))

    print(f"\nPerfect Maps (Sprint 2-0): {len(maps_won)}/{len(maps)}")
    print(f"Maps with Baron wins: {len(maps_lost)}/{len(maps)}")
    for m, w1, w2 in maps_lost:
        print(f"  - {m}: Blue={w1}, Orange={w2}")
    print(f"Maps with Draws: {len(maps_tied)}/{len(maps)}")
    for m, w1, w2 in maps_tied:
        print(f"  - {m}: Blue={w1}, Orange={w2}")

if __name__ == "__main__":
    main()
