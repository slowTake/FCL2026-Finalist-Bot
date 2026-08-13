"""Phase 4 — Impact Scoring & Cost Model for FCV Bot Gauntlet."""

from __future__ import annotations

from typing import Iterable, Any
from fcv_gauntlet.events import DiagnosticEvent

# Costs table
UNIT_COSTS = {
    "builder": 40.0,
    "harvester": 20.0,
    "gunner": 60.0,
    "sentinel": 100.0,
    "launcher": 120.0,
    "conveyor": 5.0,
    "splitter": 10.0,
    "barrier": 10.0,
    "core": 1000.0,
}


def estimate_crash_cost(event: DiagnosticEvent, world_context: Any = None) -> float:
    role = event.metadata.get("role", "builder")
    base = UNIT_COSTS.get(role, 40.0)
    # Task disruption: builder loss disrupts economy by at least 20 Ti
    return base + 20.0


def estimate_orphan_cost(event: DiagnosticEvent, world_context: Any = None) -> float:
    age = event.metadata.get("age", 20)
    # Harvesters produce 10 Ti every 4 turns -> 2.5 Ti/turn lost
    return float(age * 2.5)


def estimate_churn_cost(event: DiagnosticEvent, world_context: Any = None) -> float:
    count = event.metadata.get("rebuild_count", 3)
    kind = event.metadata.get("kind", "building")
    base = UNIT_COSTS.get(kind, 15.0)
    return float(count * base + count * 5.0)  # Rebuild cost + opportunity cost


def estimate_friendly_fire_cost(event: DiagnosticEvent, world_context: Any = None) -> float:
    ammo = event.metadata.get("ammo_cost", 4.0)
    victim_kind = event.metadata.get("victim_kind")
    if victim_kind:
        victim_cost = UNIT_COSTS.get(victim_kind, 20.0)
        return float(ammo + victim_cost * 0.5)
    return float(ammo)


def estimate_idle_cost(event: DiagnosticEvent, world_context: Any = None) -> float:
    turns = event.turn_end - event.turn_start if event.turn_end else 8
    return float(turns * 2.5)  # Idle builder wastes ~2.5 Ti opportunity per turn


def estimate_belt_break_cost(event: DiagnosticEvent, world_context: Any = None) -> float:
    return 25.0  # Repair cost + lost delivery time


def estimate_relay_abandon_cost(event: DiagnosticEvent, world_context: Any = None) -> float:
    duration = event.metadata.get("duration", 20)
    return float(20.0 + duration * 1.5)  # Harvester cost + wasted turns


def compute_event_impact(event: DiagnosticEvent, world_context: Any = None) -> float:
    """Compute and assign estimated_ti_loss for a DiagnosticEvent."""
    if event.estimated_ti_loss is not None and event.estimated_ti_loss > 0:
        return event.estimated_ti_loss

    detector = event.detector
    if detector == "crash":
        cost = estimate_crash_cost(event, world_context)
    elif detector == "orphan_harvester":
        cost = estimate_orphan_cost(event, world_context)
    elif detector == "rebuild_churn":
        cost = estimate_churn_cost(event, world_context)
    elif detector == "friendly_fire":
        cost = estimate_friendly_fire_cost(event, world_context)
    elif detector == "idle":
        cost = estimate_idle_cost(event, world_context)
    elif detector == "belt_break":
        cost = estimate_belt_break_cost(event, world_context)
    elif detector == "relay_completion" and event.metadata.get("stage") == "RELAY_ABANDONED":
        cost = estimate_relay_abandon_cost(event, world_context)
    else:
        cost = 10.0

    event.estimated_ti_loss = round(cost, 1)
    return event.estimated_ti_loss


def top_cost_leaks(events: Iterable[DiagnosticEvent], top_n: int = 5) -> list[DiagnosticEvent]:
    """Return top N events sorted by estimated Titanium loss descending."""
    valid_events = [e for e in events if e.estimated_ti_loss is not None]
    return sorted(valid_events, key=lambda e: e.estimated_ti_loss or 0.0, reverse=True)[:top_n]


def total_estimated_loss(events: Iterable[DiagnosticEvent]) -> float:
    """Return total estimated Titanium loss across all events."""
    return round(sum(e.estimated_ti_loss or 0.0 for e in events), 1)
