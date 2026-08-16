#!/usr/bin/env python3
"""CLI Entrypoint for Advanced FCV Bot Gauntlet & Diagnostic Engine."""

import sys
import argparse
from pathlib import Path

# Add project root to sys.path
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from fcv_gauntlet.runner import run_full_gauntlet, run_standalone_replay_analysis, run_json_conversion


def main() -> int:
    parser = argparse.ArgumentParser(description="FCV Bot Gauntlet & Diagnostic Engine")
    
    # Direct Input Modes
    parser.add_argument("--replay", type=str, help="Analyze a single existing .replay26 file directly without running matches")
    parser.add_argument("--from-json", type=str, help="Convert an existing JSON report file directly into Markdown & summary")

    # Positional bot arguments (optional if --replay is used)
    parser.add_argument("bot_name", type=str, nargs="?", default="baron", help="Name of candidate bot")
    parser.add_argument("opponent_name", type=str, nargs="?", default="baron", help="Name of opponent bot")

    # Map Selection Flags
    parser.add_argument("-m", "--map", action="append", help="Specific map name(s) to test")
    parser.add_argument("--all-maps", action="store_true", help="Run against all .map26 files in maps directory")

    # Detector Configuration Flags
    parser.add_argument("--config", type=str, help="Path to YAML detector configuration file")
    parser.add_argument("--profile", type=str, help="Profile name in detector config (e.g. baron, baseline)")

    # Output Flags
    parser.add_argument("--json-output", type=str, help="Path to write structured JSON report")
    parser.add_argument("--csv-metrics", type=str, help="Path to write CSV per-game metrics")
    parser.add_argument("--markdown-report", type=str, help="Path to write automated Markdown audit report")
    parser.add_argument("--badges-only", action="store_true", help="Compact output mode")

    # Baseline & Regression Flags
    parser.add_argument("--save-baseline", type=str, help="Save run metrics to baseline JSON file")
    parser.add_argument("--compare-baseline", type=str, help="Compare run against baseline JSON file")

    # CI Gating Rules
    parser.add_argument("--fail-on-crash", action="store_true", help="Fail CI run if any bot crash occurs")
    parser.add_argument("--fail-on-orphan", type=int, metavar="COUNT", help="Fail CI run if orphan harvesters exceed threshold")
    parser.add_argument("--fail-on-cpu", type=int, metavar="US", help="Fail CI run if P95/P99 CPU time exceeds threshold (us)")
    parser.add_argument("--fail-on-regression", action="store_true", help="Fail CI run if metrics regress against baseline")
    parser.add_argument("--warn-on-oscillation", type=int, metavar="COUNT", help="Warn if path oscillation count exceeds threshold")

    # AI Oracle Diagnosis (costs real API money — see fcv_gauntlet/oracle/AGENT.md)
    parser.add_argument("--oracle", action="store_true",
                         help="Run AI root-cause diagnosis over the findings (fcv_gauntlet.oracle). "
                              "Without --oracle-yes, only prints a cost estimate and makes no API call.")
    parser.add_argument("--oracle-yes", action="store_true",
                         help="Confirm explicit human sign-off to spend money on this run's oracle diagnosis. "
                              "Requires --oracle. Never pass this without the account owner's explicit approval "
                              "for this specific run.")

    args = parser.parse_args()

    if args.from_json:
        return run_json_conversion(
            json_input_path=args.from_json,
            markdown_report_path=args.markdown_report,
        )

    if args.replay:
        return run_standalone_replay_analysis(
            replay_path=args.replay,
            bot1=args.bot_name,
            bot2=args.opponent_name,
            config_path=args.config,
            profile_name=args.profile,
            json_output_path=args.json_output,
            csv_metrics_path=args.csv_metrics,
            markdown_report_path=args.markdown_report,
            oracle=args.oracle,
            oracle_yes=args.oracle_yes,
        )

    return run_full_gauntlet(
        bot_name=args.bot_name,
        opponent_name=args.opponent_name,
        map_names=args.map,
        all_maps=args.all_maps,
        config_path=args.config,
        profile_name=args.profile,
        json_output_path=args.json_output,
        csv_metrics_path=args.csv_metrics,
        markdown_report_path=args.markdown_report,
        save_baseline_path=args.save_baseline,
        compare_baseline_path=args.compare_baseline,
        fail_on_crash=args.fail_on_crash,
        fail_on_orphan=args.fail_on_orphan,
        fail_on_cpu=args.fail_on_cpu,
        fail_on_regression=args.fail_on_regression,
        warn_on_oscillation=args.warn_on_oscillation,
        badges_only=args.badges_only,
        oracle=args.oracle,
        oracle_yes=args.oracle_yes,
    )


if __name__ == "__main__":
    sys.exit(main())
