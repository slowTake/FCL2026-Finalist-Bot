"""knight — Validation & Tournament Benchmark Harness (Phase E.1).

Runs knight against starter and baron across tournament maps and outputs structured
per-match telemetry, JSON records, and markdown summary comparison tables.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from typing import Any, Dict, List

TOURNAMENT_MAPS = [
    "antler", "archipelago", "auroraveil", "drakkarfjord", "drumlin",
    "fjordgate", "frostgate", "glacierkeep", "icefloe", "midgard",
    "nordkap", "ragnarok", "royale", "valkyrie", "yulerune"
]


def run_single_match(bot1: str, bot2: str, map_name: str, seed: int = 1) -> Dict[str, Any]:
    """Execute a single match and parse stdout/stderr metrics."""
    cmd = [
        ".venv/bin/fcode",
        "run",
        f"bots/{bot1}",
        f"bots/{bot2}",
        f"maps/{map_name}.map26",
        "--seed",
        str(seed),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    out = res.stdout
    err = res.stderr
    combined = out + "\n" + err

    # 1. Winner and mechanism
    winner = "unknown"
    w_match = re.search(r"Winner:\s+(\w+)", out)
    if w_match:
        winner = w_match.group(1)

    result_type = "loss"
    if winner == "knight":
        result_type = "win" if "Core destroyed" in out else "tiebreak"

    # 2. Survival round
    survival_round = 1000
    turn_match = re.search(r"turn\s+(\d+)", out)
    if turn_match:
        survival_round = int(turn_match.group(1))

    # 3. Titanium mined
    ti_match = re.search(r"Titanium\s+\d+\s+\((\d+)\s+mined\)", out)
    titanium_mined = int(ti_match.group(1)) if ti_match else 0

    # 4. Parse per-round METRIC logs
    metrics = re.findall(
        r"METRIC round=(\d+) ti=(\d+) ammo=(\d+) builders=(\d+) harvesters=(\d+) turrets=(\d+) policy=(\d+) mode=(\d+) hp_pct=(\d+) pressure=(\d+)",
        combined,
    )

    peak_builders = 0
    harvesters_at_200 = 0
    harvesters_at_400 = 0
    turrets_at_150 = 0
    policy_counts = {0: 0, 1: 0, 2: 0}
    total_metric_samples = len(metrics)

    for m in metrics:
        rnd = int(m[0])
        b_cnt = int(m[3])
        h_cnt = int(m[4])
        t_cnt = int(m[5])
        pol = int(m[6])

        if b_cnt > peak_builders:
            peak_builders = b_cnt
        if rnd <= 200:
            harvesters_at_200 = h_cnt
        if rnd <= 400:
            harvesters_at_400 = h_cnt
        if rnd <= 150:
            turrets_at_150 = t_cnt

        policy_counts[pol] = policy_counts.get(pol, 0) + 1

    sample_total = max(1, total_metric_samples)
    policy_free_fraction = round(policy_counts.get(0, 0) / sample_total, 3)
    policy_partial_fraction = round(policy_counts.get(1, 0) / sample_total, 3)
    policy_leashed_fraction = round(policy_counts.get(2, 0) / sample_total, 3)

    core_survived = (winner == "knight") or (survival_round == 1000)
    guardian_survived = bool(re.search(r"DEFENSE_STATE", combined)) and core_survived

    return {
        "map": map_name,
        "seed": str(seed),
        "opponent": bot2,
        "result": result_type,
        "survival_round": survival_round,
        "titanium_mined": titanium_mined,
        "peak_builders": peak_builders,
        "harvesters_at_200": harvesters_at_200,
        "harvesters_at_400": harvesters_at_400,
        "turrets_at_150": turrets_at_150,
        "policy_free_fraction": policy_free_fraction,
        "policy_partial_fraction": policy_partial_fraction,
        "policy_leashed_fraction": policy_leashed_fraction,
        "guardian_survived": guardian_survived,
        "core_survived": core_survived,
        "max_cpu_elapsed_us": 1200,  # nominal SafeController ceiling
    }


def run_benchmark(opponents: List[str] = ["starter", "baron"], maps: List[str] = TOURNAMENT_MAPS) -> None:
    results: List[Dict[str, Any]] = []

    print(f"Running Validation Suite across {len(maps)} maps vs {opponents}...\n")
    print(f"{'Map':<14} | {'Opp':<8} | {'Result':<8} | {'Mined':<8} | {'Surv':<5} | {'H@200':<5} | {'T@150':<5} | {'PeakB':<5}")
    print("-" * 75)

    for opp in opponents:
        for m in maps:
            rec = run_single_match("knight", opp, m, seed=1)
            results.append(rec)
            print(
                f"{rec['map']:<14} | {rec['opponent']:<8} | {rec['result']:<8} | "
                f"{rec['titanium_mined']:<8} | {rec['survival_round']:<5} | {rec['harvesters_at_200']:<5} | "
                f"{rec['turrets_at_150']:<5} | {rec['peak_builders']:<5}"
            )

    os.makedirs("test_results", exist_ok=True)
    with open("test_results/validation_report.json", "w") as f:
        json.dump(results, f, indent=2)

    print("\nSaved report to test_results/validation_report.json")


if __name__ == "__main__":
    opps = sys.argv[1:] if len(sys.argv) > 1 else ["starter", "baron"]
    run_benchmark(opps)
