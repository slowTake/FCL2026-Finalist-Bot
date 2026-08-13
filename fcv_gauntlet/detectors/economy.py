"""Phase 3 — New Economy Detectors for FCV Bot Gauntlet."""

from __future__ import annotations

from typing import Any
from fcv_gauntlet.events import DiagnosticEvent, Severity, Confidence
from fcv.analysis import chain_reaches_core
from fcv import constants as C


class RelayCompletionDetector:
    """Tracks relay lifecycle: RELAY_STARTED, RELAY_COMPLETED, RELAY_ABANDONED."""

    def analyze_replay(self, replay: Any, map_name: str = "", side: str = "") -> list[DiagnosticEvent]:
        events: list[DiagnosticEvent] = []
        active_relays: dict[int, tuple[int, tuple[int, int]]] = {}  # harvester_id -> (start_turn, pos)
        completion_times: list[int] = []

        for t, before, after in replay.iter_worlds():
            harvesters = [e for e in after.entities.values() if e.kind == "harvester"]
            for h in harvesters:
                connected = chain_reaches_core(after, replay, h.x, h.y, h.team)
                if not connected and h.id not in active_relays:
                    # Check if conveyor adjacent to harvester exists
                    for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0)):
                        b = after.building_at(h.x + dx, h.y + dy)
                        if b and b.kind in ("conveyor", "splitter") and b.team == h.team:
                            active_relays[h.id] = (t, (h.x, h.y))
                            events.append(
                                DiagnosticEvent(
                                    detector="relay_completion",
                                    severity=Severity.INFO,
                                    confidence=Confidence.HIGH,
                                    map_name=map_name,
                                    side=side,
                                    turn_start=t,
                                    turn_end=t,
                                    team=h.team,
                                    unit_ids=[h.id],
                                    positions=[(h.x, h.y)],
                                    message=f"RELAY_STARTED for harvester #{h.id} at ({h.x},{h.y}) at turn {t}",
                                    metadata={"stage": "RELAY_STARTED", "harvester_id": h.id},
                                )
                            )
                            break

                elif connected and h.id in active_relays:
                    start_t, pos = active_relays.pop(h.id)
                    duration = t - start_t
                    completion_times.append(duration)
                    events.append(
                        DiagnosticEvent(
                            detector="relay_completion",
                            severity=Severity.INFO,
                            confidence=Confidence.HIGH,
                            map_name=map_name,
                            side=side,
                            turn_start=start_t,
                            turn_end=t,
                            team=h.team,
                            unit_ids=[h.id],
                            positions=[pos],
                            message=f"RELAY_COMPLETED for harvester #{h.id} in {duration} turns",
                            metadata={"stage": "RELAY_COMPLETED", "duration": duration},
                        )
                    )

        # Check for abandoned relays
        last_turn = len(list(replay.iter_worlds()))
        for hid, (start_t, pos) in active_relays.items():
            if (last_turn - start_t) >= 40:
                events.append(
                    DiagnosticEvent(
                        detector="relay_completion",
                        severity=Severity.MEDIUM,
                        confidence=Confidence.HIGH,
                        map_name=map_name,
                        side=side,
                        turn_start=start_t,
                        turn_end=last_turn,
                        unit_ids=[hid],
                        positions=[pos],
                        estimated_ti_loss=30.0,
                        likely_cause="RELAY_ABANDONED_MID_BUILD",
                        suggested_module="builder",
                        message=f"RELAY_ABANDONED for harvester #{hid} at {pos} after {last_turn - start_t} turns",
                        metadata={"stage": "RELAY_ABANDONED", "duration": last_turn - start_t},
                    )
                )

        return events


class HarvesterNoOutputDetector:
    """Flags harvesters that exist for >= 20 turns without delivering titanium."""

    def analyze_replay(self, replay: Any, map_name: str = "", side: str = "") -> list[DiagnosticEvent]:
        events: list[DiagnosticEvent] = []
        harvester_born: dict[int, int] = {}
        reported: set[int] = set()

        for t, before, after in replay.iter_worlds():
            for e in after.entities.values():
                if e.kind == "harvester":
                    harvester_born.setdefault(e.id, t)
                    age = t - harvester_born[e.id]
                    if age >= 20 and e.id not in reported:
                        connected = chain_reaches_core(after, replay, e.x, e.y, e.team)
                        if not connected:
                            reported.add(e.id)
                            events.append(
                                DiagnosticEvent(
                                    detector="harvester_no_output",
                                    severity=Severity.HIGH,
                                    confidence=Confidence.HIGH,
                                    map_name=map_name,
                                    side=side,
                                    turn_start=harvester_born[e.id],
                                    turn_end=t,
                                    team=e.team,
                                    unit_ids=[e.id],
                                    positions=[(e.x, e.y)],
                                    estimated_ti_loss=float(age * 10 / 4),
                                    likely_cause="NO_DELIVERY_ROUTE",
                                    suggested_module="builder",
                                    message=f"Harvester #{e.id} at ({e.x},{e.y}) active for {age} turns without delivering output",
                                    metadata={"age": age},
                                )
                            )
        return events


class ConveyorMisrouteDetector:
    """Flags conveyors facing into walls, dead ends, or forming loops."""

    def analyze_replay(self, replay: Any, map_name: str = "", side: str = "") -> list[DiagnosticEvent]:
        events: list[DiagnosticEvent] = []
        reported: set[tuple[int, int]] = set()

        for t, before, after in replay.iter_worlds():
            for e in after.entities.values():
                if e.kind not in ("conveyor", "splitter"):
                    continue
                p = (e.x, e.y)
                if p in reported:
                    continue

                vx, vy = C.DIRECTION_VECTOR.get(e.direction or "DIR_CENTRE", (0, 0))
                if (vx, vy) == (0, 0):
                    continue

                nx, ny = e.x + vx, e.y + vy
                # Check wall facing
                if not (0 <= nx < replay.width and 0 <= ny < replay.height):
                    reported.add(p)
                    events.append(
                        DiagnosticEvent(
                            detector="conveyor_misroute",
                            severity=Severity.MEDIUM,
                            confidence=Confidence.HIGH,
                            map_name=map_name,
                            side=side,
                            turn_start=t,
                            turn_end=t,
                            team=e.team,
                            entity_ids=[e.id],
                            positions=[p],
                            estimated_ti_loss=5.0,
                            likely_cause="FACING_MAP_EDGE",
                            suggested_module="logistics",
                            message=f"Conveyor #{e.id} at {p} faces map edge ({nx},{ny})",
                            metadata={"type": "MAP_EDGE", "facing": e.direction},
                        )
                    )

        return events


class StoreContentionDetector:
    """Detects multi-unit store writes to the same slot in a single turn."""

    def analyze_replay(self, replay: Any, map_name: str = "", side: str = "") -> list[DiagnosticEvent]:
        events: list[DiagnosticEvent] = []

        for t, before, after in replay.iter_worlds():
            slot_writes: dict[int, list[int]] = {}
            for step in replay.substeps(t):
                if step.actor is None:
                    continue
                for a in step.actions:
                    if a.kind == "writeStore":
                        slot = a.payload.get("slot", 0)
                        slot_writes.setdefault(slot, []).append(step.actor)

            for slot, actors in slot_writes.items():
                if len(set(actors)) > 1:
                    events.append(
                        DiagnosticEvent(
                            detector="store_contention",
                            severity=Severity.HIGH,
                            confidence=Confidence.HIGH,
                            map_name=map_name,
                            side=side,
                            turn_start=t,
                            turn_end=t,
                            unit_ids=list(set(actors)),
                            estimated_ti_loss=10.0,
                            likely_cause=f"MULTIPLE_UNITS_WRITE_SLOT_{slot}",
                            suggested_module="policy",
                            message=f"Store contention on slot {slot} at turn {t} by units {set(actors)}",
                            metadata={"slot": slot, "actors": list(set(actors))},
                        )
                    )
        return events


class OreClaimCollisionDetector:
    """Detects multiple builders pathing to or claiming the same ore tile within 5 turns."""

    def analyze_replay(self, replay: Any, map_name: str = "", side: str = "") -> list[DiagnosticEvent]:
        events: list[DiagnosticEvent] = []
        ore_claims: dict[tuple[int, int], list[tuple[int, int]]] = {}  # ore_pos -> [(turn, builder_id)]

        for t, before, after in replay.iter_worlds():
            for step in replay.substeps(t):
                if step.actor is None:
                    continue
                actor_e = after.entities.get(step.actor)
                if not actor_e or actor_e.kind != "builder":
                    continue

                for a in step.actions:
                    if a.kind == "builderBuild":
                        pos_dict = a.payload.get("position", {})
                        ore_pos = (pos_dict.get("x", 0), pos_dict.get("y", 0))
                        claims = ore_claims.setdefault(ore_pos, [])

                        recent = [c for c in claims if (t - c[0]) <= 5 and c[1] != step.actor]
                        if recent:
                            other_builder = recent[0][1]
                            events.append(
                                DiagnosticEvent(
                                    detector="ore_claim_collision",
                                    severity=Severity.MEDIUM,
                                    confidence=Confidence.HIGH,
                                    map_name=map_name,
                                    side=side,
                                    turn_start=t,
                                    turn_end=t,
                                    team=actor_e.team,
                                    unit_ids=[step.actor, other_builder],
                                    positions=[ore_pos],
                                    estimated_ti_loss=15.0,
                                    likely_cause="DUPLICATE_ORE_CLAIM",
                                    suggested_module="builder",
                                    message=f"Ore claim collision at {ore_pos} by builders #{step.actor} and #{other_builder}",
                                    metadata={"ore_pos": list(ore_pos), "other_builder": other_builder},
                                )
                            )
                        claims.append((t, step.actor))
        return events
