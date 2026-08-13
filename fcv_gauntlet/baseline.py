"""Phase 7 — Baseline Comparison & Regression Gating for FCV Bot Gauntlet."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable
from fcv_gauntlet.events import DiagnosticEvent, Severity


def save_baseline(report_data: dict[str, Any], path: str | Path) -> None:
    """Save run report data as a baseline JSON file."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(report_data, f, indent=2)


def compare_baseline(current_report: dict[str, Any], baseline_path: str | Path) -> dict[str, Any]:
    """Compare current run against saved baseline and compute deltas."""
    bp = Path(baseline_path)
    if not bp.is_file():
        return {"error": f"Baseline file not found: {bp}"}

    with open(bp, "r", encoding="utf-8") as f:
        baseline = json.load(f)

    curr_counts = current_report.get("summary", {}).get("detector_counts", {})
    base_counts = baseline.get("summary", {}).get("detector_counts", {})

    all_detectors = set(curr_counts.keys()) | set(base_counts.keys())
    detector_deltas = {}
    for d in all_detectors:
        c = curr_counts.get(d, 0)
        b = base_counts.get(d, 0)
        detector_deltas[d] = {
            "current": c,
            "baseline": b,
            "delta": c - b,
            "status": "IMPROVED" if c < b else ("REGRESSED" if c > b else "UNCHANGED"),
        }

    # Compare events by unique key (detector + map + side + turn_start + position)
    curr_events = {f"{e['detector']}:{e.get('map_name')}:{e.get('side')}:{e.get('turn_start')}" for e in current_report.get("events", [])}
    base_events = {f"{e['detector']}:{e.get('map_name')}:{e.get('side')}:{e.get('turn_start')}" for e in baseline.get("events", [])}

    new_incidents = list(curr_events - base_events)
    resolved_incidents = list(base_events - curr_events)

    regressed = any(d["delta"] > 0 for d in detector_deltas.values()) or len(new_incidents) > 0

    return {
        "regressed": regressed,
        "detector_deltas": detector_deltas,
        "new_incidents_count": len(new_incidents),
        "resolved_incidents_count": len(resolved_incidents),
        "new_incidents": new_incidents[:10],
        "resolved_incidents": resolved_incidents[:10],
    }


def evaluate_ci_gates(
    events: Iterable[DiagnosticEvent],
    summary_data: dict[str, Any],
    baseline_delta: dict[str, Any] | None = None,
    fail_on_crash: bool = False,
    fail_on_orphan: int | None = None,
    fail_on_cpu_us: int | None = None,
    fail_on_regression: bool = False,
    warn_on_oscillation: int | None = None,
) -> tuple[bool, list[str], list[str]]:
    """Evaluate CI gate flags and return (should_fail, errors, warnings)."""
    errors: list[str] = []
    warnings: list[str] = []

    # Check crash gate
    if fail_on_crash:
        crashes = [e for e in events if e.detector == "crash" and e.severity in (Severity.CRITICAL, Severity.HIGH)]
        if crashes:
            errors.append(f"CI Gate Fail: Found {len(crashes)} CRITICAL/HIGH crash(es)")

    # Check orphan gate
    if fail_on_orphan is not None:
        orphans = sum(1 for e in events if e.detector == "orphan_harvester")
        if orphans > fail_on_orphan:
            errors.append(f"CI Gate Fail: Orphan count ({orphans}) exceeds limit ({fail_on_orphan})")

    # Check CPU gate
    if fail_on_cpu_us is not None:
        max_cpu = summary_data.get("cpu_p99_us", 0)
        if max_cpu > fail_on_cpu_us:
            errors.append(f"CI Gate Fail: CPU p99 ({max_cpu} us) exceeds limit ({fail_on_cpu_us} us)")

    # Check regression gate
    if fail_on_regression and baseline_delta:
        if baseline_delta.get("regressed", False):
            errors.append("CI Gate Fail: Regression detected vs baseline")

    # Check oscillation warning gate
    if warn_on_oscillation is not None:
        oscillations = sum(1 for e in events if e.detector == "oscillation")
        if oscillations > warn_on_oscillation:
            warnings.append(f"CI Gate Warning: Oscillation count ({oscillations}) exceeds threshold ({warn_on_oscillation})")

    should_fail = len(errors) > 0
    return should_fail, errors, warnings
