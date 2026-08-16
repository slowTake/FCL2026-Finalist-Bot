"""Phase 6 — Structured JSON, CSV, and Markdown Reporting for FCV Bot Gauntlet."""

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


def write_markdown_report(
    report_data: dict[str, Any],
    path: str | Path,
    oracle_text: str | None = None,
    oracle_cost_usd: float | None = None,
) -> None:
    """Generate an automated, self-contained Markdown Diagnostic Audit Report.

    oracle_text/oracle_cost_usd are optional — pass them only when the caller
    already ran fcv_gauntlet.oracle.diagnose() (which costs real API money and
    requires explicit human confirmation; see fcv_gauntlet/oracle/AGENT.md).
    This function never calls the API itself and has no dependency on the
    oracle package — it just renders whatever text it's handed.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    summary = report_data.get("summary", {})
    tot_loss = summary.get("total_estimated_ti_loss", 0.0)
    counts = summary.get("detector_counts", {})
    leaks = report_data.get("top_cost_leaks", [])[:10]
    chains = report_data.get("causal_chains", [])[:5]

    lines: list[str] = []
    lines.append(f"# 🛡️ Automated FCV Diagnostic Audit Report")
    lines.append(f"**Run ID**: `{report_data.get('run_id')}` | **Timestamp**: `{report_data.get('timestamp')}`")
    lines.append(f"**Bots**: `{report_data.get('candidate_bot')}` vs `{report_data.get('opponent_bot')}`\n")

    lines.append(f"## 📊 Executive Summary")
    lines.append(f"- **Total Estimated Titanium Loss**: **{tot_loss} Ti**")
    lines.append(f"- **Total Games Analyzed**: {len(report_data.get('games', []))}")
    lines.append(f"- **Primary Failure Modes**:")

    sorted_counts = sorted(counts.items(), key=lambda x: x[1], reverse=True)
    for det, count in sorted_counts[:6]:
        lines.append(f"  - `{det}`: **{count} incidents**")

    lines.append("\n## 🚨 Top 10 Estimated Titanium Leaks")
    lines.append("| Detector | Map (Side) | Message | Est. Loss |")
    lines.append("| :--- | :--- | :--- | :---: |")
    for l in leaks:
        lines.append(f"| `{l.get('detector')}` | {l.get('map_name')} ({l.get('side')}) | {l.get('message')} | **{l.get('estimated_ti_loss')} Ti** |")

    if chains:
        lines.append("\n## 🔗 Identified Causal Chains")
        for c in chains:
            lines.append(f"- **[{c.get('chain_id')}]**: {c.get('summary')}")

    if oracle_text:
        lines.append("\n## 🔮 Oracle Root-Cause Diagnosis")
        lines.append(
            "*AI-generated from the diagnostics below plus the actual bot source "
            f"(cost: ${oracle_cost_usd:.4f}). Read alongside, not instead of, the "
            "static checks below.*\n"
        )
        lines.append(oracle_text)

    lines.append("\n## 💡 Automated Actionable Fix Suggestions (static heuristics)")

    suggestions = 0
    if counts.get("orphan_harvester", 0) > 10 or counts.get("harvester_no_output", 0) > 10:
        suggestions += 1
        lines.append("### 1. ⚠️ Un-Routed Harvester & Relay Lock (`bots/baron/builder.py`)")
        lines.append("- **Issue**: Builders abort active relay claims when non-threatening enemy units enter vision, stranding harvesters.")
        lines.append("- **Fix**: Require builders to complete conveyor lines before starting new tasks; do not abort relays unless an enemy turret specifically threatens the relay path.\n")

    if counts.get("crash", 0) > 0:
        suggestions += 1
        lines.append("### 2. 💥 Crash Prevention & Safety Valve (`bots/baron/defence.py` & `pathing.py`)")
        lines.append("- **Issue**: Tile placement near Core footprint or unbudgeted A* loops causing TLEs/exceptions.")
        lines.append("- **Fix**: Exclude Core footprint `(14,2)` in `patchAdjacentCoreBelt`; enforce a 3ms `time.perf_counter()` limit in `astarFromSeeds`.\n")

    if counts.get("friendly_fire", 0) > 5:
        suggestions += 1
        lines.append("### 3. 🔥 Friendly Fire Raycasting (`bots/baron/guns.py`)")
        lines.append("- **Issue**: Turrets firing through friendly builders or structures.")
        lines.append("- **Fix**: Enforce `_isFriendlyInLineOfFire` raycast checks before building sentinel turrets or gunners.\n")

    if counts.get("oscillation", 0) > 10:
        suggestions += 1
        lines.append("### 4. 🔀 Path Hysteresis & Conveyor Navigation (`bots/baron/movement.py`)")
        lines.append("- **Issue**: Builders oscillating between fleeing and harvesting, or getting trapped behind friendly conveyors.")
        lines.append("- **Fix**: Add a 3-turn target lock during threat avoidance; allow A* to cross friendly conveyors when no open path exists.\n")

    if suggestions == 0:
        lines.append("No critical behavioral flaws detected. Code is performing cleanly!")

    with open(p, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


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
