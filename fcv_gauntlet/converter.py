"""Converter from fcv findings into structured DiagnosticEvent objects."""

from __future__ import annotations

from typing import Iterable, Any
from .events import DiagnosticEvent, Severity, Confidence


def finding_to_event(
    finding: Any,
    map_name: str = "",
    side: str = "",
    candidate_team: str = "TEAM_A",
) -> DiagnosticEvent:
    """Convert an fcv.analysis.Finding object or dict into a DiagnosticEvent."""
    if hasattr(finding, "to_dict"):
        d = finding.to_dict()
    elif isinstance(finding, dict):
        d = finding
    else:
        d = {
            "kind": getattr(finding, "kind", "unknown"),
            "severity": getattr(finding, "severity", "medium"),
            "turn": getattr(finding, "turn", 0),
            "message": getattr(finding, "message", ""),
            "actor": getattr(finding, "actor", None),
            "pos": getattr(finding, "pos", None),
            "detail": getattr(finding, "detail", {}),
        }

    kind = d.get("kind", "unknown")
    raw_sev = d.get("severity", "medium")
    sev = Severity.from_str(raw_sev)
    turn = d.get("turn", 0)
    msg = d.get("message", "")
    actor = d.get("actor")
    pos = d.get("pos")
    detail = d.get("detail", {})

    unit_ids = [actor] if actor is not None else []
    positions = [pos] if pos is not None else []

    raw_team = detail.get("team")
    team = "candidate" if raw_team == candidate_team else ("opponent" if raw_team else "")

    return DiagnosticEvent(
        detector=kind,
        severity=sev,
        confidence=Confidence.HIGH,
        map_name=map_name,
        side=side,
        turn_start=turn,
        turn_end=turn,
        team=team or raw_team or "",
        unit_ids=unit_ids,
        entity_ids=unit_ids,
        positions=positions,
        message=msg,
        metadata=detail,
    )


def collect_events_from_findings(
    findings: Iterable[Any],
    map_name: str = "",
    side: str = "",
    candidate_team: str = "TEAM_A",
) -> list[DiagnosticEvent]:
    """Convert a sequence of fcv findings into a list of DiagnosticEvents."""
    return [finding_to_event(f, map_name=map_name, side=side, candidate_team=candidate_team) for f in findings]
