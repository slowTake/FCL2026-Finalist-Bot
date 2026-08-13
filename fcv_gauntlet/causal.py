"""Phase 5 — Causal Event Chains for FCV Bot Gauntlet."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Iterable, Any
from fcv_gauntlet.events import DiagnosticEvent


@dataclass
class CausalChain:
    chain_id: str
    events: list[DiagnosticEvent] = field(default_factory=list)
    root_cause: DiagnosticEvent | None = None
    total_estimated_ti_loss: float = 0.0
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "chain_id": self.chain_id,
            "events": [e.to_dict() for e in self.events],
            "root_cause": self.root_cause.to_dict() if self.root_cause else None,
            "total_estimated_ti_loss": round(self.total_estimated_ti_loss, 1),
            "summary": self.summary,
        }


KNOWN_PATTERNS = [
    ("belt_break", "orphan_harvester"),
    ("ore_claim_collision", "oscillation"),
    ("oscillation", "idle"),
    ("friendly_fire", "crash"),
    ("friendly_fire", "relay_completion"),
    ("crash", "orphan_harvester"),
    ("crash", "relay_completion"),
]


def build_causal_chains(events: Iterable[DiagnosticEvent], max_turn_gap: int = 20) -> list[CausalChain]:
    """Group diagnostic events into causal chains based on entity, position, pattern, and timing."""
    sorted_events = sorted(events, key=lambda e: (e.turn_start, e.detector))
    used: set[int] = set()
    chains: list[CausalChain] = []

    for i, root in enumerate(sorted_events):
        if i in used:
            continue

        chain_events = [root]
        used.add(i)

        for j in range(i + 1, len(sorted_events)):
            if j in used:
                continue

            child = sorted_events[j]
            gap = child.turn_start - (root.turn_end or root.turn_start)
            if gap < 0 or gap > max_turn_gap:
                continue

            # Matching criteria
            entity_match = bool(set(root.unit_ids + root.entity_ids) & set(child.unit_ids + child.entity_ids))
            pos_match = bool(set(root.positions) & set(child.positions))
            pattern_match = (root.detector, child.detector) in KNOWN_PATTERNS

            if entity_match or pos_match or pattern_match:
                chain_events.append(child)
                used.add(j)

        if len(chain_events) >= 2:
            cid = f"chain-{uuid.uuid4().hex[:8]}"
            total_loss = round(sum(e.estimated_ti_loss or 0.0 for e in chain_events), 1)
            root_evt = chain_events[0]
            last_evt = chain_events[-1]
            summary_str = f"Root: {root_evt.detector} at t{root_evt.turn_start} → Cascade: {last_evt.detector} at t{last_evt.turn_start} (Est Loss: {total_loss} Ti)"

            chains.append(
                CausalChain(
                    chain_id=cid,
                    events=chain_events,
                    root_cause=root_evt,
                    total_estimated_ti_loss=total_loss,
                    summary=summary_str,
                )
            )

    return chains
