"""Script to run knight vs baron across all 15 maps (2 seeds each) and gather detailed diagnostic metrics.
Usage: python3 run_baron_diagnostic.py
"""

import os
import re
import subprocess
import sys

MAPS = [
    "antler", "archipelago", "auroraveil", "drakkarfjord", "drumlin",
    "fjordgate", "frostgate", "glacierkeep", "icefloe", "midgard",
    "nordkap", "ragnarok", "royale", "valkyrie", "yulerune"
]

def run_match(map_name, seed):
    cmd = [".venv/bin/fcode", "run", "bots/knight", "bots/baron", f"maps/{map_name}.map26", "--seed", str(seed)]
    res = subprocess.run(cmd, capture_output=True, text=True, cwd=os.path.dirname(os.path.abspath(__file__)))
    err = res.stderr
    out = res.stdout

    # Winner
    winner = "unknown"
    w_match = re.search(r"Winner:\s+(\w+)", out)
    if w_match:
        winner = w_match.group(1)

    # Win mechanism
    reason_match = re.search(r"Winner:\s+\w+\s+\(([^)]+)\)", out)
    mechanism = reason_match.group(1) if reason_match else "unknown"

    # Titanium Mined
    match_ti = re.search(r"Titanium\s+(\d+)\s+\((\d+)\s+mined\)\s+(\d+)\s+\((\d+)\s+mined\)", out)
    if match_ti:
        k_tot, k_mined, b_tot, b_mined = int(match_ti.group(1)), int(match_ti.group(2)), int(match_ti.group(3)), int(match_ti.group(4))
    else:
        k_tot, k_mined, b_tot, b_mined = 0, 0, 0, 0

    # Units & Buildings
    ub_match = re.search(r"Units\s+(\d+)\s+(\d+)\s+Buildings\s+(\d+)\s+(\d+)", out)
    if ub_match:
        k_units, b_units, k_bldg, b_bldg = int(ub_match.group(1)), int(ub_match.group(2)), int(ub_match.group(3)), int(ub_match.group(4))
    else:
        k_units, b_units, k_bldg, b_bldg = 0, 0, 0, 0

    # STUCK occurrences
    stuck_cnt = len(re.findall(r"STUCK builder_id=", err))

    return {
        "map": map_name,
        "seed": seed,
        "winner": winner,
        "mechanism": mechanism,
        "k_mined": k_mined,
        "b_mined": b_mined,
        "k_units": k_units,
        "b_units": b_units,
        "k_bldg": k_bldg,
        "b_bldg": b_bldg,
        "stuck": stuck_cnt,
    }

def main():
    print("Running Diagnostic Audit (knight vs baron) across 15 maps x 2 seeds = 30 matches...\n")
    print(f"{'Map':<13} | {'Seed':<4} | {'Winner':<8} | {'Mechanism':<20} | {'k_Mined':<7} | {'b_Mined':<7} | {'Stuck':<5}")
    print("-" * 75)
    results = []
    wins = 0
    total = 0
    for m in MAPS:
        for s in [1, 2]:
            r = run_match(m, s)
            results.append(r)
            total += 1
            if r['winner'] == "knight":
                wins += 1
            print(f"{r['map']:<13} | {r['seed']:<4} | {r['winner']:<8} | {r['mechanism']:<20} | {r['k_mined']:<7} | {r['b_mined']:<7} | {r['stuck']:<5}")
    
    print("-" * 75)
    print(f"Summary: {wins}/{total} Wins ({wins/total*100:.1f}%)")

if __name__ == "__main__":
    main()
