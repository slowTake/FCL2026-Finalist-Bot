"""Structured Diagnostic Event Schema for FCV Bot Gauntlet."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Severity(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFO = "INFO"

    @classmethod
    def from_str(cls, val: str) -> Severity:
        v = str(val).upper()
        if v == "CRITICAL":
            return cls.CRITICAL
        if v in ("HIGH", "ERROR"):
            return cls.HIGH
        if v in ("MEDIUM", "WARN", "WARNING"):
            return cls.MEDIUM
        if v in ("LOW", "INFO_LOW"):
            return cls.LOW
        return cls.INFO


class Confidence(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"

    @classmethod
    def from_str(cls, val: str) -> Confidence:
        v = str(val).upper()
        if v == "HIGH":
            return cls.HIGH
        if v == "MEDIUM":
            return cls.MEDIUM
        return cls.LOW


@dataclass
class DiagnosticEvent:
    detector: str
    severity: Severity = Severity.INFO
    confidence: Confidence = Confidence.HIGH
    map_name: str = ""
    side: str = ""  # "A" or "B"
    turn_start: int = 0
    turn_end: int | None = None
    team: str = ""  # "candidate", "opponent", "TEAM_A", "TEAM_B"
    unit_ids: list[int] = field(default_factory=list)
    entity_ids: list[int] = field(default_factory=list)
    positions: list[tuple[int, int]] = field(default_factory=list)
    estimated_ti_loss: float | None = None
    likely_cause: str | None = None
    evidence: str | None = None
    suggested_module: str | None = None
    message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "detector": self.detector,
            "severity": self.severity.value if isinstance(self.severity, Enum) else str(self.severity),
            "confidence": self.confidence.value if isinstance(self.confidence, Enum) else str(self.confidence),
            "map_name": self.map_name,
            "side": self.side,
            "turn_start": self.turn_start,
            "turn_end": self.turn_end,
            "team": self.team,
            "unit_ids": list(self.unit_ids),
            "entity_ids": list(self.entity_ids),
            "positions": [list(p) if isinstance(p, (tuple, list)) else p for p in self.positions],
            "estimated_ti_loss": round(self.estimated_ti_loss, 1) if self.estimated_ti_loss is not None else None,
            "likely_cause": self.likely_cause,
            "evidence": self.evidence,
            "suggested_module": self.suggested_module,
            "message": self.message,
            "metadata": self.metadata,
        }

    def to_json(self, indent: int | None = None) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> DiagnosticEvent:
        positions = [tuple(p) for p in d.get("positions", [])]
        sev = Severity.from_str(d.get("severity", "INFO"))
        conf = Confidence.from_str(d.get("confidence", "HIGH"))
        return cls(
            detector=d.get("detector", "unknown"),
            severity=sev,
            confidence=conf,
            map_name=d.get("map_name", ""),
            side=d.get("side", ""),
            turn_start=d.get("turn_start", 0),
            turn_end=d.get("turn_end"),
            team=d.get("team", ""),
            unit_ids=list(d.get("unit_ids", [])),
            entity_ids=list(d.get("entity_ids", [])),
            positions=positions,
            estimated_ti_loss=d.get("estimated_ti_loss"),
            likely_cause=d.get("likely_cause"),
            evidence=d.get("evidence"),
            suggested_module=d.get("suggested_module"),
            message=d.get("message", ""),
            metadata=dict(d.get("metadata", {})),
        )
