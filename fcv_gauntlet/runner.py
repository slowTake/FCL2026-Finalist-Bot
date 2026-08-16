"""Main Orchestrator for FCV Bot Gauntlet with Symmetrical Bot-Agnostic Analytics."""

from __future__ import annotations

import datetime
import sys
import uuid
from pathlib import Path
from typing import Any

from fcode.config import find_config
from fcode.fcode_engine import run_game

from fcv.replay import Replay
from fcv.metrics import compute as compute_metrics

from fcv_gauntlet.events import DiagnosticEvent, Severity, Confidence
from fcv_gauntlet.config import DetectorConfig, load_config
from fcv_gauntlet.detectors.improved import (
    ImprovedCrashDetector,
    ImprovedOrphanHarvesterDetector,
    ImprovedShotDetector,
    ImprovedRebuildChurnDetector,
    ImprovedOscillationDetector,
    ImprovedIdleDetector,
    ImprovedHoardingDetector,
)
from fcv_gauntlet.detectors.economy import (
    RelayCompletionDetector,
    HarvesterNoOutputDetector,
    ConveyorMisrouteDetector,
    StoreContentionDetector,
    OreClaimCollisionDetector,
)
from fcv_gauntlet.impact import compute_event_impact, top_cost_leaks, total_estimated_loss
from fcv_gauntlet.causal import build_causal_chains
from fcv_gauntlet.reporting import build_full_report_dict, write_json_report, write_csv_metrics, write_markdown_report
from fcv_gauntlet.baseline import save_baseline, compare_baseline, evaluate_ci_gates

COMPETITIVE_MAPS = [
    "antler", "archipelago", "auroraveil", "drakkarfjord", "drumlin",
    "fjordgate", "frostgate", "glacierkeep", "icefloe", "midgard",
    "nordkap", "ragnarok", "royale", "valkyrie", "yulerune",
]

CONDITION_LABELS = {
    "core_destroyed": "Core destroyed",
    "resigned": "Resigned",
    "resources": "Resources",
    "titanium_collected": "Titanium collected",
    "harvesters": "Harvesters",
    "titanium_stored": "Titanium stored",
    "coinflip": "Coinflip",
    "timeout": "Draw",
}


def resolve_maps(maps_dir: Path, override: list[str] | None, all_maps: bool) -> list[Path]:
    if override:
        maps = []
        for name in override:
            path = maps_dir / name
            if not path.suffix:
                path = path.with_suffix(".map26")
            if not path.exists():
                raise FileNotFoundError(f"Map not found: {path}")
            maps.append(path)
        return maps

    if all_maps:
        return sorted(maps_dir.glob("*.map26"))

    maps = []
    for name in COMPETITIVE_MAPS:
        path = (maps_dir / name).with_suffix(".map26")
        if path.exists():
            maps.append(path)
        else:
            print(f"Warning: competitive map missing: {path}", file=sys.stderr)
    return maps


def _maybe_run_oracle(
    report_data: dict[str, Any],
    run_oracle: bool,
    oracle_yes: bool,
) -> tuple[str | None, float | None]:
    """Optionally run fcv_gauntlet.oracle's AI root-cause diagnosis.

    Costs real API money from a personal budget — never calls the API unless
    both run_oracle and oracle_yes are True. With run_oracle=True and
    oracle_yes=False this prints a cost estimate and returns without spending
    anything, so `--oracle` alone is always a safe dry run. See
    fcv_gauntlet/oracle/AGENT.md before setting oracle_yes=True programmatically.
    """
    if not run_oracle:
        return None, None

    try:
        from fcv_gauntlet.oracle import (
            diagnose, check_budget, estimate_cost, format_budget_report, OracleError,
        )
    except ImportError:
        print(
            "Warning: --oracle requested but the 'anthropic' package is not installed "
            "(pip install anthropic). Skipping AI diagnosis.",
            file=sys.stderr,
        )
        return None, None

    status = check_budget()
    print(format_budget_report(status))
    if not status.ok:
        print(f"Skipping AI diagnosis: {status.reason}", file=sys.stderr)
        return None, None

    if not oracle_yes:
        est_low = estimate_cost(3_000, 800)
        est_high = estimate_cost(30_000, 3_000)
        print(
            f"\n--oracle given without --oracle-yes: estimated cost "
            f"${est_low:.3f}-${est_high:.3f}. No API call made — this would spend "
            f"real money from a personal budget. Re-run with --oracle-yes only "
            f"after a human has explicitly approved this run's cost.",
            file=sys.stderr,
        )
        return None, None

    try:
        result = diagnose(report_data, confirmed=True)
    except OracleError as exc:
        print(f"Oracle diagnosis failed: {exc}", file=sys.stderr)
        return None, None

    print(
        f"Oracle diagnosis complete — cost ${result.cost_usd:.4f} "
        f"({result.tool_calls_used} file reads, stopped: {result.stopped_reason})"
    )
    return result.text, result.cost_usd


def _build_badge_str(counts: dict[str, int]) -> str:
    crashes = counts.get("crash", 0)
    orphans = counts.get("orphan_harvester", 0)
    ff = counts.get("friendly_fire", 0)
    churn = counts.get("rebuild_churn", 0)
    osc = counts.get("oscillation", 0)

    badge_parts = []
    if crashes > 0:
        badge_parts.append(f"💥{crashes} crash")
    if orphans > 0:
        badge_parts.append(f"⚠️{orphans} orphan")
    if ff > 0:
        badge_parts.append(f"🔥{ff} FF")
    if churn > 0:
        badge_parts.append(f"🔄{churn} churn")
    if osc > 0:
        badge_parts.append(f"🔀{osc} osc")

    return (" [" + ", ".join(badge_parts) + "]") if badge_parts else ""


def analyze_replay_all_detectors(
    replay_path: str,
    map_name: str,
    side: str,
    team_a_bot: str,
    team_b_bot: str,
    cfg: DetectorConfig,
) -> tuple[list[DiagnosticEvent], dict[str, Any]]:
    p = Path(replay_path)
    if not p.is_file():
        return [], {}

    try:
        replay = Replay.load(p)
        events: list[DiagnosticEvent] = []

        detectors = [
            ImprovedCrashDetector(cfg),
            ImprovedOrphanHarvesterDetector(cfg),
            ImprovedShotDetector(cfg),
            ImprovedRebuildChurnDetector(cfg),
            ImprovedOscillationDetector(cfg),
            ImprovedIdleDetector(cfg),
            ImprovedHoardingDetector(cfg),
            RelayCompletionDetector(),
            HarvesterNoOutputDetector(),
            ConveyorMisrouteDetector(),
            StoreContentionDetector(),
            OreClaimCollisionDetector(),
        ]

        for det in detectors:
            evts = det.analyze_replay(replay, map_name=map_name, side=side)
            for e in evts:
                if e.team == "TEAM_A":
                    e.metadata["bot_name"] = team_a_bot
                elif e.team == "TEAM_B":
                    e.metadata["bot_name"] = team_b_bot
                compute_event_impact(e)
            events.extend(evts)

        _, summary_metrics = compute_metrics(replay)
        a_metrics = summary_metrics.get("TEAM_A")
        b_metrics = summary_metrics.get("TEAM_B")

        ti_a = a_metrics.income_per_turn if a_metrics else 0.0
        ti_b = b_metrics.income_per_turn if b_metrics else 0.0

        dmg_a_dealt = a_metrics.damage_dealt if a_metrics else 0
        dmg_a_taken = a_metrics.damage_taken if a_metrics else 0
        dr_a = (dmg_a_dealt / max(1, dmg_a_taken)) if dmg_a_taken > 0 else float(dmg_a_dealt)

        dmg_b_dealt = b_metrics.damage_dealt if b_metrics else 0
        dmg_b_taken = b_metrics.damage_taken if b_metrics else 0
        dr_b = (dmg_b_dealt / max(1, dmg_b_taken)) if dmg_b_taken > 0 else float(dmg_b_dealt)

        counts_a: dict[str, int] = {}
        counts_b: dict[str, int] = {}

        for e in events:
            if e.team == "TEAM_A":
                counts_a[e.detector] = counts_a.get(e.detector, 0) + 1
            elif e.team == "TEAM_B":
                counts_b[e.detector] = counts_b.get(e.detector, 0) + 1

        badge_a = _build_badge_str(counts_a)
        badge_b = _build_badge_str(counts_b)

        metrics_dict = {
            "ti_rate_a": round(ti_a, 1),
            "ti_rate_b": round(ti_b, 1),
            "d_ratio_a": round(dr_a, 2),
            "d_ratio_b": round(dr_b, 2),
            "badge_a": badge_a,
            "badge_b": badge_b,
        }

        return events, metrics_dict

    except Exception as exc:
        print(f"Error analyzing replay {replay_path}: {exc}", file=sys.stderr)
        return [], {"badge_a": f" [fcv err: {exc}]", "badge_b": f" [fcv err: {exc}]"}


def run_json_conversion(
    json_input_path: str,
    markdown_report_path: str | None = None,
) -> int:
    """Convert an existing JSON report file into Markdown and terminal output."""
    p = Path(json_input_path)
    if not p.is_file():
        print(f"Error: JSON report file not found at {json_input_path}", file=sys.stderr)
        return 1

    try:
        import json
        with open(p, "r", encoding="utf-8") as f:
            report_data = json.load(f)

        if markdown_report_path:
            write_markdown_report(report_data, markdown_report_path)
            print(f"Successfully converted {json_input_path} -> {markdown_report_path}")
        else:
            # Default markdown path if not specified
            out_path = p.with_suffix(".md")
            write_markdown_report(report_data, out_path)
            print(f"Successfully converted {json_input_path} -> {out_path}")

        summary = report_data.get("summary", {})
        print(f"\nTotal Estimated Titanium Loss: {summary.get('total_estimated_ti_loss', 0.0)} Ti")

        leaks = report_data.get("top_cost_leaks", [])[:5]
        if leaks:
            print("\n=== Top 5 Estimated Ti Leaks ===")
            for l in leaks:
                bname = l.get("metadata", {}).get("bot_name", l.get("team", "Side"))
                print(f"  • [{bname}] {l.get('detector')} at {l.get('map_name')}: {l.get('message')} (Est Loss: {l.get('estimated_ti_loss')} Ti)")

        chains = report_data.get("causal_chains", [])[:5]
        if chains:
            print("\n=== Causal Chains Identified ===")
            for c in chains:
                print(f"  • [{c.get('chain_id')}] {c.get('summary')}")

        return 0
    except Exception as exc:
        print(f"Error reading JSON report {json_input_path}: {exc}", file=sys.stderr)
        return 1


def run_standalone_replay_analysis(
    replay_path: str,
    bot1: str = "bot1",
    bot2: str = "bot2",
    config_path: str | None = None,
    profile_name: str | None = None,
    json_output_path: str | None = None,
    csv_metrics_path: str | None = None,
    markdown_report_path: str | None = None,
    oracle: bool = False,
    oracle_yes: bool = False,
) -> int:
    """Analyze a single .replay26 file directly without running matches."""
    p = Path(replay_path)
    if not p.is_file():
        print(f"Error: replay file not found at {replay_path}", file=sys.stderr)
        return 1
    run_id = f"single-{uuid.uuid4().hex[:8]}"
    now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()
    map_name = p.stem
    cfg = load_config(config_path, profile_name or bot1)

    events, metrics = analyze_replay_all_detectors(str(p), map_name, "A", bot1, bot2, cfg)
    causal_chains = build_causal_chains(events)

    report_data = build_full_report_dict(
        run_id=run_id,
        timestamp=now_str,
        candidate_bot=bot1,
        opponent_bot=bot2,
        maps=[map_name],
        games=[{
            "map": map_name, "side": "A", "team_a_bot": bot1, "team_b_bot": bot2,
            "ti_rate_a": metrics.get("ti_rate_a", 0.0), "ti_rate_b": metrics.get("ti_rate_b", 0.0),
        }],
        events=events,
        causal_chains=causal_chains,
        verdict_counts={"SINGLE_REPLAY": 1},
    )

    oracle_text, oracle_cost = _maybe_run_oracle(report_data, oracle, oracle_yes)

    if json_output_path:
        write_json_report(report_data, json_output_path)
    if csv_metrics_path:
        write_csv_metrics(report_data["games"], csv_metrics_path)
    if markdown_report_path:
        write_markdown_report(report_data, markdown_report_path, oracle_text=oracle_text, oracle_cost_usd=oracle_cost)

    tot_loss = total_estimated_loss(events)
    print(f"FCV Standalone Replay Diagnostic: {replay_path}")
    print(f"Total Estimated Ti Loss: {tot_loss} Ti | Diagnostic Badges: {metrics.get('badge_a', '')}")

    leaks = top_cost_leaks(events, top_n=5)
    if leaks:
        print("\n=== Top 5 Estimated Ti Leaks ===")
        for l in leaks:
            bname = l.metadata.get("bot_name", f"Side {l.side}")
            print(f"  • [{bname}] {l.detector} at {l.map_name}: {l.message} (Est Loss: {l.estimated_ti_loss} Ti)")

    if causal_chains:
        print("\n=== Causal Chains Identified ===")
        for c in causal_chains[:5]:
            print(f"  • [{c.chain_id}] {c.summary}")

    return 0


def run_full_gauntlet(
    bot_name: str,
    opponent_name: str,
    map_names: list[str] | None = None,
    all_maps: bool = False,
    config_path: str | None = None,
    profile_name: str | None = None,
    json_output_path: str | None = None,
    csv_metrics_path: str | None = None,
    save_baseline_path: str | None = None,
    compare_baseline_path: str | None = None,
    markdown_report_path: str | None = None,
    fail_on_crash: bool = False,
    fail_on_orphan: int | None = None,
    fail_on_cpu: int | None = None,
    fail_on_regression: bool = False,
    warn_on_oscillation: int | None = None,
    badges_only: bool = False,
    oracle: bool = False,
    oracle_yes: bool = False,
) -> int:
    bot1 = bot_name
    bot2 = opponent_name

    config_obj, project_root = find_config()
    bots_dir = (project_root / config_obj.bots_dir).resolve()
    maps_dir = (project_root / config_obj.maps_dir).resolve()
    seed = config_obj.seed
    engine_root = str(Path(__import__("fcode").__file__).resolve().parent)

    candidate = bots_dir / bot1 / "main.py"
    opponent = bots_dir / bot2 / "main.py"

    if not candidate.is_file():
        print(f"Error: bot1 not found at {candidate}", file=sys.stderr)
        return 1
    if not opponent.is_file():
        print(f"Error: bot2 not found at {opponent}", file=sys.stderr)
        return 1

    maps = resolve_maps(maps_dir, map_names, all_maps)
    if not maps:
        print(f"Error: no maps found in {maps_dir}", file=sys.stderr)
        return 1

    cfg = load_config(config_path, profile_name or bot1)
    run_id = f"run-{uuid.uuid4().hex[:8]}"
    now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()

    total_games = len(maps) * 2
    is_mirror = (bot1 == bot2)

    if not badges_only:
        title = f"FCV Bot Gauntlet: {bot1} (Mirror Match)" if is_mirror else f"FCV Bot Gauntlet: {bot1} vs {bot2}"
        print(f"{title} ({len(maps)} maps, {total_games} games with side-switching & FCV analytics, seed={seed})")
        print()
        print(f"{'Map':<12} {'Verdict':<12} {'Game 1 (Side A: 1st)':<34} {'Game 2 (Side B: 2nd)':<34}")
        print("-" * 96)

    all_events: list[DiagnosticEvent] = []
    games_summary: list[dict[str, Any]] = []

    bot1_wins = 0
    bot2_wins = 0
    side_a_wins = 0
    side_b_wins = 0

    sweeps_bot1 = 0
    sweeps_bot2 = 0
    sweeps_side_a = 0
    sweeps_side_b = 0
    splits = 0

    verdict_counts = {"CLEAN": 0, "SPLIT": 0, "LOSS": 0}

    for map_path in maps:
        mname = map_path.stem
        replay_a = f"/tmp/fcv-gauntlet-{bot1}-{bot2}-{mname}-sideA.replay26"
        replay_b = f"/tmp/fcv-gauntlet-{bot1}-{bot2}-{mname}-sideB.replay26"

        # Game 1: Side A = bot1 (Team A), Side B = bot2 (Team B)
        res_a = run_game(
            str(candidate.resolve()),
            str(opponent.resolve()),
            engine_root,
            str(map_path.resolve()),
            replay_a,
            seed,
            0,
        )
        game1_winner_side = res_a["winner"]  # "A", "B", "Draw"
        if game1_winner_side == "A":
            side_a_wins += 1
            bot1_wins += 1
        elif game1_winner_side == "B":
            side_b_wins += 1
            bot2_wins += 1

        cond_a = CONDITION_LABELS.get(res_a["win_condition"], res_a["win_condition"])
        evts_a, m_a = analyze_replay_all_detectors(replay_a, mname, "A", bot1, bot2, cfg)
        all_events.extend(evts_a)

        # Detail formatting for Game 1
        if is_mirror:
            winner_str_a = f"Side {game1_winner_side} won" if game1_winner_side in ("A", "B") else "Draw"
        else:
            winner_str_a = f"{bot1 if game1_winner_side == 'A' else bot2} won" if game1_winner_side in ("A", "B") else "Draw"

        badge_g1 = m_a.get("badge_a") if game1_winner_side == "A" else m_a.get("badge_b")
        detail_a = f"{winner_str_a} ({cond_a}, t{res_a['turns']}){badge_g1}"

        games_summary.append({
            "map": mname, "side": "A", "team_a_bot": bot1, "team_b_bot": bot2,
            "winner_side": game1_winner_side, "win_condition": cond_a, "turns": res_a["turns"],
            "ti_rate_a": m_a.get("ti_rate_a", 0.0), "ti_rate_b": m_a.get("ti_rate_b", 0.0),
        })

        # Game 2: Side A = bot2 (Team A), Side B = bot1 (Team B)
        res_b = run_game(
            str(opponent.resolve()),
            str(candidate.resolve()),
            engine_root,
            str(map_path.resolve()),
            replay_b,
            seed,
            0,
        )
        game2_winner_side = res_b["winner"]  # "A", "B", "Draw"
        if game2_winner_side == "A":
            side_a_wins += 1
            bot2_wins += 1
        elif game2_winner_side == "B":
            side_b_wins += 1
            bot1_wins += 1

        cond_b = CONDITION_LABELS.get(res_b["win_condition"], res_b["win_condition"])
        evts_b, m_b = analyze_replay_all_detectors(replay_b, mname, "B", bot2, bot1, cfg)
        all_events.extend(evts_b)

        # Detail formatting for Game 2
        if is_mirror:
            winner_str_b = f"Side {game2_winner_side} won" if game2_winner_side in ("A", "B") else "Draw"
        else:
            winner_str_b = f"{bot2 if game2_winner_side == 'A' else bot1} won" if game2_winner_side in ("A", "B") else "Draw"

        badge_g2 = m_b.get("badge_a") if game2_winner_side == "A" else m_b.get("badge_b")
        detail_b = f"{winner_str_b} ({cond_b}, t{res_b['turns']}){badge_g2}"

        games_summary.append({
            "map": mname, "side": "B", "team_a_bot": bot2, "team_b_bot": bot1,
            "winner_side": game2_winner_side, "win_condition": cond_b, "turns": res_b["turns"],
            "ti_rate_a": m_b.get("ti_rate_a", 0.0), "ti_rate_b": m_b.get("ti_rate_b", 0.0),
        })

        # Verdict calculation
        if is_mirror:
            if game1_winner_side == "A" and game2_winner_side == "A":
                verdict = "Side A 2-0"
                sweeps_side_a += 1
            elif game1_winner_side == "B" and game2_winner_side == "B":
                verdict = "Side B 2-0"
                sweeps_side_b += 1
            else:
                verdict = "SPLIT 1-1"
                splits += 1
        else:
            w1_bot = bot1 if game1_winner_side == "A" else (bot2 if game1_winner_side == "B" else "Draw")
            w2_bot = bot2 if game2_winner_side == "A" else (bot1 if game2_winner_side == "B" else "Draw")
            if w1_bot == w2_bot and w1_bot != "Draw":
                if w1_bot == bot1:
                    verdict = f"{bot1} 2-0"
                    sweeps_bot1 += 1
                    verdict_counts["CLEAN"] += 1
                else:
                    verdict = f"{bot2} 2-0"
                    sweeps_bot2 += 1
                    verdict_counts["LOSS"] += 1
            else:
                verdict = "SPLIT 1-1"
                splits += 1
                verdict_counts["SPLIT"] += 1

        if not badges_only:
            print(f"{mname:<12} {verdict:<12} {detail_a:<34} {detail_b:<34}")

    causal_chains = build_causal_chains(all_events)
    report_data = build_full_report_dict(
        run_id=run_id,
        timestamp=now_str,
        candidate_bot=bot1,
        opponent_bot=bot2,
        maps=[m.stem for m in maps],
        games=games_summary,
        events=all_events,
        causal_chains=causal_chains,
        verdict_counts=verdict_counts,
    )

    baseline_delta = None
    if compare_baseline_path:
        baseline_delta = compare_baseline(report_data, compare_baseline_path)

    # Evaluate CI Gating
    should_fail, gate_errors, gate_warnings = evaluate_ci_gates(
        events=all_events,
        summary_data=report_data["summary"],
        baseline_delta=baseline_delta,
        fail_on_crash=fail_on_crash,
        fail_on_orphan=fail_on_orphan,
        fail_on_cpu_us=fail_on_cpu,
        fail_on_regression=fail_on_regression,
        warn_on_oscillation=warn_on_oscillation,
    )

    oracle_text, oracle_cost = _maybe_run_oracle(report_data, oracle, oracle_yes)

    # Output generation
    if json_output_path:
        write_json_report(report_data, json_output_path)
    if csv_metrics_path:
        write_csv_metrics(games_summary, csv_metrics_path)
    if save_baseline_path:
        save_baseline(report_data, save_baseline_path)
    if markdown_report_path:
        write_markdown_report(report_data, markdown_report_path, oracle_text=oracle_text, oracle_cost_usd=oracle_cost)

    # Print GitHub Actions annotations if in CI
    for err in gate_errors:
        print(f"::error title=CI Gate Failure::{err}")
    for warn in gate_warnings:
        print(f"::warning title=CI Gate Warning::{warn}")

    if not badges_only:
        print("-" * 96)
        if is_mirror:
            print(f"Mirror Match Advantage: Side A (1st Player): {side_a_wins} wins ({sweeps_side_a} sweeps) | Side B (2nd Player): {side_b_wins} wins ({sweeps_side_b} sweeps) — {splits} splits")
        else:
            print(f"Match Result: {bot1} ({bot1_wins} wins, {sweeps_bot1} sweeps) vs {bot2} ({bot2_wins} wins, {sweeps_bot2} sweeps) — {splits} splits")

        # Bot Diagnostic Comparison
        if is_mirror:
            side_a_evts = [e for e in all_events if e.side == "A"]
            side_b_evts = [e for e in all_events if e.side == "B"]
            loss_a = total_estimated_loss(side_a_evts)
            loss_b = total_estimated_loss(side_b_evts)

            print("\n=== Mirror Match Side Comparison ===")
            print(f"{'Metric':<28} {'Side A (1st Player)':<22} {'Side B (2nd Player)':<22}")
            print("-" * 74)
            print(f"{'Total Games Won':<28} {f'{side_a_wins}/{total_games}':<22} {f'{side_b_wins}/{total_games}':<22}")
            print(f"{'Clean Map Sweeps (2-0)':<28} {f'{sweeps_side_a} maps':<22} {f'{sweeps_side_b} maps':<22}")
            print(f"{'Total Estimated Ti Loss':<28} {f'{loss_a} Ti':<22} {f'{loss_b} Ti':<22}")
            print(f"{'Total Crashes / TLEs':<28} {sum(1 for e in side_a_evts if e.detector == 'crash'):<22} {sum(1 for e in side_b_evts if e.detector == 'crash'):<22}")
            print(f"{'Orphan Harvesters':<28} {sum(1 for e in side_a_evts if e.detector == 'orphan_harvester'):<22} {sum(1 for e in side_b_evts if e.detector == 'orphan_harvester'):<22}")
            print(f"{'Friendly Fire Incidents':<28} {sum(1 for e in side_a_evts if e.detector == 'friendly_fire'):<22} {sum(1 for e in side_b_evts if e.detector == 'friendly_fire'):<22}")

        else:
            bot1_evts = [e for e in all_events if e.metadata.get("bot_name") == bot1]
            bot2_evts = [e for e in all_events if e.metadata.get("bot_name") == bot2]
            loss1 = total_estimated_loss(bot1_evts)
            loss2 = total_estimated_loss(bot2_evts)

            print("\n=== Bot Diagnostic Comparison ===")
            print(f"{'Metric':<28} {bot1:<22} {bot2:<22}")
            print("-" * 74)
            print(f"{'Total Games Won':<28} {f'{bot1_wins}/{total_games}':<22} {f'{bot2_wins}/{total_games}':<22}")
            print(f"{'Clean Map Sweeps (2-0)':<28} {f'{sweeps_bot1} maps':<22} {f'{sweeps_bot2} maps':<22}")
            print(f"{'Total Estimated Ti Loss':<28} {f'{loss1} Ti':<22} {f'{loss2} Ti':<22}")
            print(f"{'Total Crashes / TLEs':<28} {sum(1 for e in bot1_evts if e.detector == 'crash'):<22} {sum(1 for e in bot2_evts if e.detector == 'crash'):<22}")
            print(f"{'Orphan Harvesters':<28} {sum(1 for e in bot1_evts if e.detector == 'orphan_harvester'):<22} {sum(1 for e in bot2_evts if e.detector == 'orphan_harvester'):<22}")
            print(f"{'Friendly Fire Incidents':<28} {sum(1 for e in bot1_evts if e.detector == 'friendly_fire'):<22} {sum(1 for e in bot2_evts if e.detector == 'friendly_fire'):<22}")

        leaks = top_cost_leaks(all_events, top_n=5)
        if leaks:
            print("\n=== Top 5 Estimated Ti Leaks ===")
            for l in leaks:
                bname = l.metadata.get("bot_name", f"Side {l.side}")
                print(f"  • [{bname}] {l.detector} at {l.map_name} ({l.side}): {l.message} (Est Loss: {l.estimated_ti_loss} Ti)")

        if causal_chains:
            print("\n=== Causal Chains Identified ===")
            for c in causal_chains[:5]:
                print(f"  • [{c.chain_id}] {c.summary}")

    return 1 if should_fail else (0 if bot1_wins >= bot2_wins else 1)
