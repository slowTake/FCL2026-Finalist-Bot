"""Phase 8 — Detector Configuration & Profiles for FCV Bot Gauntlet."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


@dataclass
class DetectorConfig:
    oscillation_window: int = 6
    oscillation_min_hits: int = 3
    idle_min_turns: int = 8
    hoarding_min_ti: int = 250
    hoarding_min_turns: int = 25
    orphan_grace_turns: int = 10
    churn_min_rebuilds: int = 3
    belt_break_min_turns: int = 5
    cpu_tle_threshold_us: int = 9000

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> DetectorConfig:
        return cls(
            oscillation_window=int(d.get("oscillation_window", 6)),
            oscillation_min_hits=int(d.get("oscillation_min_hits", 3)),
            idle_min_turns=int(d.get("idle_min_turns", 8)),
            hoarding_min_ti=int(d.get("hoarding_min_ti", 250)),
            hoarding_min_turns=int(d.get("hoarding_min_turns", 25)),
            orphan_grace_turns=int(d.get("orphan_grace_turns", 10)),
            churn_min_rebuilds=int(d.get("churn_min_rebuilds", 3)),
            belt_break_min_turns=int(d.get("belt_break_min_turns", 5)),
            cpu_tle_threshold_us=int(d.get("cpu_tle_threshold_us", 9000)),
        )

    @classmethod
    def load_file(cls, path: str | Path) -> DetectorConfig:
        p = Path(path)
        if not p.is_file():
            raise FileNotFoundError(f"Config file not found: {p}")
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)


PROFILES: dict[str, DetectorConfig] = {
    "baseline": DetectorConfig(),
    "baron": DetectorConfig(
        orphan_grace_turns=8,
        cpu_tle_threshold_us=8000,
        idle_min_turns=6,
    ),
    "bandit": DetectorConfig(
        hoarding_min_ti=200,
        churn_min_rebuilds=2,
    ),
}


def load_config(config_path: str | None = None, profile_name: str | None = None) -> DetectorConfig:
    """Load config from path or named profile, defaulting to baseline."""
    if config_path:
        return DetectorConfig.load_file(config_path)
    if profile_name and profile_name.lower() in PROFILES:
        return PROFILES[profile_name.lower()]
    return DetectorConfig()
