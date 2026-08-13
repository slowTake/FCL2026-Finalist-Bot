#!/usr/bin/env python3
"""Bot Gauntlet with integrated fcv behavioural analytics, causal chains, and CI gating.

Runs candidate bot vs opponent on active competitive maps (Side A and Side B)
and leverages fcv to extract crashes, orphan harvesters, friendly fire, rebuild
churn, path oscillations, and damage/mining efficiency.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Add project root to sys.path so fcv_gauntlet package can be imported directly
project_root = Path(__file__).resolve().parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from fcv_gauntlet.runner import run_full_gauntlet


def main() -> None:
    parser = argparse.ArgumentParser(
        description="FCV Bot Gauntlet — Advanced Diagnostic Engine & CI Gating."
    )
    parser.add_argument("bot1", nargs="?", default="baron", help="Candidate bot directory name")
    parser.add_argument("bot2", nargs="?", default="baron", help="Opponent bot directory name")
    parser.add_argument("--bot1", dest="bot1_flag", help="Candidate bot directory name (override)")
    parser.add_argument("--bot2", dest="bot2_flag", help="Opponent bot directory name (override)")
    parser.add_argument("--map", "-m", action="append", help="Specific map name(s) to run")
    parser.add_argument("--all-maps", action="store_true", help="Run on all map26 files")
    
    # Phase 6 Flags
    parser.add_argument("--json-output", type=str, help="Path to write structured JSON report")
    parser.add_argument("--csv-metrics", type=str, help="Path to write CSV per-game metrics")
    parser.add_argument("--badges-only", action="store_true", help="Compact output mode")

    # Phase 7 Flags
    parser.add_argument("--save-baseline", type=str, help="Path to save current run as baseline JSON")
    parser.add_argument("--compare-baseline", type=str, help="Path to compare current run against baseline JSON")
    parser.add_argument("--fail-on-crash", action="store_true", help="Exit code 1 if any CRITICAL crash occurs")
    parser.add_argument("--fail-on-orphan", type=int, help="Exit code 1 if orphan harvester count > N")
    parser.add_argument("--fail-on-cpu", type=int, help="Exit code 1 if CPU p99 > N us")
    parser.add_argument("--fail-on-regression", action="store_true", help="Exit code 1 if any metric regressed vs baseline")
    parser.add_argument("--warn-on-oscillation", type=int, help="Print warning annotation if oscillation count > N")

    # Phase 8 Flags
    parser.add_argument("--config", type=str, help="Path to JSON detector configuration file")
    parser.add_argument("--profile", type=str, help="Named profile configuration (baron, bandit, baseline)")

    args = parser.parse_args()

    candidate = args.bot1_flag or args.bot1
    opponent = args.bot2_flag or args.bot2

    code = run_full_gauntlet(
        bot_name=candidate,
        opponent_name=opponent,
        map_names=args.map,
        all_maps=args.all_maps,
        config_path=args.config,
        profile_name=args.profile,
        json_output_path=args.json_output,
        csv_metrics_path=args.csv_metrics,
        save_baseline_path=args.save_baseline,
        compare_baseline_path=args.compare_baseline,
        fail_on_crash=args.fail_on_crash,
        fail_on_orphan=args.fail_on_orphan,
        fail_on_cpu=args.fail_on_cpu,
        fail_on_regression=args.fail_on_regression,
        warn_on_oscillation=args.warn_on_oscillation,
        badges_only=args.badges_only,
    )
    sys.exit(code)


if __name__ == "__main__":
    main()
