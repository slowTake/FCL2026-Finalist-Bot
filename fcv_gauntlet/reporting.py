"""Phase 6 — Structured JSON & CSV Reporting for FCV Bot Gauntlet."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Iterable
from fcv_gauntlet.events import DiagnosticEvent
from fcv_gauntlet.causal import CausalChain
from fcv_gauntlet.impact import top_cost_leaks, total_estimated_loss


def write_json_report(report_data: dict[str, Any], path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(report_data, f, indent=2)


def write_csv_metrics(games_data: list[dict[str, Any]], path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "map", "side", "winner", "win_condition", "turns",
        "ti_rate", "d_ratio", "crashes", "orphans", "ff", "churn", "osc"
    ]
    with open(p, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for g in games_data:
            writer.writerow({
                "map": g.get("map"),
                "side": g.get("side"),
                "winner": g.get("winner"),
                "win_condition": g.get("win_condition"),
                "turns": g.get("turns"),
                "ti_rate": g.get("ti_rate", 0.0),
                "d_ratio": g.get("d_ratio", 0.0),
                "crashes": g.get("crashes", 0),
                "orphans": g.get("orphans", 0),
                "ff": g.get("ff", 0),
                "churn": g.get("churn", 0),
                "osc": g.get("osc", 0),
            })


def build_full_report_dict(
    run_id: str,
    timestamp: str,
    candidate_bot: str,
    opponent_bot: str,
    maps: list[str],
    games: list[dict[str, Any]],
    events: list[DiagnosticEvent],
    causal_chains: list[CausalChain],
    verdict_counts: dict[str, int],
) -> dict[str, Any]:
    detector_counts: dict[str, int] = {}
    for e in events:
        detector_counts[e.detector] = detector_counts.get(e.detector, 0) + 1

    top_leaks = top_cost_leaks(events, top_n=5)
    total_loss = total_estimated_loss(events)

    return {
        "run_id": run_id,
        "timestamp": timestamp,
        "candidate_bot": candidate_bot,
        "opponent_bot": opponent_bot,
        "maps": maps,
        "games": games,
        "events": [e.to_dict() for e in events],
        "causal_chains": [c.to_dict() for c in causal_chains],
        "top_cost_leaks": [e.to_dict() for e in top_leaks],
        "summary": {
            "total_estimated_ti_loss": total_loss,
            "verdict_counts": verdict_counts,
            "detector_counts": detector_counts,
        },
    }
