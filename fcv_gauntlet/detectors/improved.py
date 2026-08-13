"""Phase 2 — Improved Detectors for FCV Bot Gauntlet."""

from __future__ import annotations

from typing import Any, Iterable
from fcv_gauntlet.events import DiagnosticEvent, Severity, Confidence
from fcv.analysis import chain_reaches_core, MEANINGFUL
from fcv import constants as C

# Base costs for Ti estimation
UNIT_BASE_COSTS = {
    "builder": 40,
    "harvester": 20,
    "gunner": 60,
    "sentinel": 100,
    "launcher": 120,
    "conveyor": 5,
    "splitter": 10,
    "barrier": 10,
    "core": 1000,
}


class ImprovedCrashDetector:
    """Detects unit removals without lethal damage, classifying CRASH_TLE vs CRASH_EXCEPTION."""

    def __init__(self, config: Any = None):
        self.config = config
        self.tle_threshold_us = getattr(config, "cpu_tle_threshold_us", 9000)

    def analyze_replay(self, replay: Any, map_name: str = "", side: str = "") -> list[DiagnosticEvent]:
        events: list[DiagnosticEvent] = []
        recent_slow_exec: dict[int, tuple[int, int]] = {}  # actor_id -> (turn, exec_time_us)

        for t, before, after in replay.iter_worlds():
            # Track exec times
            for step in replay.substeps(t):
                if step.actor is not None and step.exec_time_us:
                    if step.exec_time_us >= self.tle_threshold_us or step.tled:
                        recent_slow_exec[step.actor] = (t, step.exec_time_us)

                damaged: dict[int, int] = {}
                for a in step.actions:
                    if a.kind == "updateHp":
                        eid = a.payload.get("id")
                        if eid is not None:
                            damaged[eid] = damaged.get(eid, 0) + a.payload.get("delta", 0)
                    elif a.kind == "removeEntity":
                        eid = a.payload.get("id")
                        if eid is None:
                            continue
                        e = before.entities.get(eid)
                        if e is None or e.hp + damaged.get(eid, 0) <= 0:
                            continue

                        # Unit was removed while still having >0 HP!
                        role = e.kind
                        base_cost = UNIT_BASE_COSTS.get(role, 30)
                        ti_loss = float(base_cost + 20)  # Replacement cost + task disruption

                        # Classify subtype
                        slow_info = recent_slow_exec.get(eid)
                        if slow_info and (t - slow_info[0] <= 1):
                            subtype = "CRASH_TLE"
                            cause = f"Unit #{eid} ({role}) exceeded CPU budget ({slow_info[1]} us)"
                        else:
                            subtype = "CRASH_EXCEPTION"
                            cause = f"Uncaught exception in unit #{eid} ({role})"

                        # Severity: CRITICAL if builder or core early/mid game (turn <= 200)
                        if role in ("builder", "core") and t <= 200:
                            sev = Severity.CRITICAL
                        else:
                            sev = Severity.HIGH

                        events.append(
                            DiagnosticEvent(
                                detector="crash",
                                severity=sev,
                                confidence=Confidence.HIGH,
                                map_name=map_name,
                                side=side,
                                turn_start=t,
                                turn_end=t,
                                team=e.team,
                                unit_ids=[eid],
                                entity_ids=[eid],
                                positions=[(e.x, e.y)],
                                estimated_ti_loss=ti_loss,
                                likely_cause=cause,
                                suggested_module="builder" if role == "builder" else "pathing",
                                message=f"{subtype}: {role} #{eid} at ({e.x},{e.y}) removed with {e.hp} HP at turn {t}",
                                metadata={
                                    "subtype": subtype,
                                    "role": role,
                                    "hp_before": e.hp,
                                    "exec_time_us": slow_info[1] if slow_info else 0,
                                },
                            )
                        )
        return events


class ImprovedOrphanHarvesterDetector:
    """Tracks harvesters without active delivery routes to core, emitting BELT_BREAK on disconnect."""

    def __init__(self, config: Any = None):
        self.grace_turns = getattr(config, "orphan_grace_turns", 10)

    def analyze_replay(self, replay: Any, map_name: str = "", side: str = "") -> list[DiagnosticEvent]:
        events: list[DiagnosticEvent] = []
        born_turns: dict[int, int] = {}
        was_connected: dict[int, bool] = {}
        reported_orphans: set[int] = set()

        for t, before, after in replay.iter_worlds():
            # Identify all harvesters in world after turn
            current_harvesters = [e for e in after.entities.values() if e.kind == "harvester"]
            for h in current_harvesters:
                born_turns.setdefault(h.id, t)
                age = t - born_turns[h.id]

                connected = chain_reaches_core(after, replay, h.x, h.y, h.team)

                # Check for BELT_BREAK transition (was connected -> now disconnected)
                if h.id in was_connected and was_connected[h.id] and not connected:
                    events.append(
                        DiagnosticEvent(
                            detector="belt_break",
                            severity=Severity.HIGH,
                            confidence=Confidence.HIGH,
                            map_name=map_name,
                            side=side,
                            turn_start=t,
                            turn_end=t,
                            team=h.team,
                            unit_ids=[h.id],
                            entity_ids=[h.id],
                            positions=[(h.x, h.y)],
                            estimated_ti_loss=25.0,
                            likely_cause="CONVEYOR_DESTROYED_OR_FACING_CHANGED",
                            suggested_module="logistics",
                            message=f"Belt line for harvester #{h.id} at ({h.x},{h.y}) broke at turn {t}",
                            metadata={"harvester_id": h.id},
                        )
                    )

                was_connected[h.id] = connected

                # Check for orphan condition past grace period
                if age >= self.grace_turns and not connected and h.id not in reported_orphans:
                    reported_orphans.add(h.id)

                    if was_connected.get(h.id, False):
                        classification = "WAS_CONNECTED_THEN_DISCONNECTED"
                    elif age < 25:
                        classification = "STILL_UNDER_CONSTRUCTION"
                    else:
                        classification = "NEVER_CONNECTED"

                    ti_loss = float(age * 10 / 4)  # Harvester delivers ~10 Ti per 4 turns

                    events.append(
                        DiagnosticEvent(
                            detector="orphan_harvester",
                            severity=Severity.HIGH if classification != "STILL_UNDER_CONSTRUCTION" else Severity.MEDIUM,
                            confidence=Confidence.HIGH,
                            map_name=map_name,
                            side=side,
                            turn_start=born_turns[h.id],
                            turn_end=t,
                            team=h.team,
                            unit_ids=[h.id],
                            entity_ids=[h.id],
                            positions=[(h.x, h.y)],
                            estimated_ti_loss=ti_loss,
                            likely_cause=f"Classification: {classification}",
                            suggested_module="builder",
                            message=f"Orphan harvester #{h.id} ({classification}) at ({h.x},{h.y}) un-routed for {age} turns",
                            metadata={"classification": classification, "age": age, "built_turn": born_turns[h.id]},
                        )
                    )
        return events


class ImprovedShotDetector:
    """Classifies friendly fire, wasted shots, and inactive turrets."""

    def __init__(self, config: Any = None):
        self.config = config

    def analyze_replay(self, replay: Any, map_name: str = "", side: str = "") -> list[DiagnosticEvent]:
        events: list[DiagnosticEvent] = []
        turret_last_fire: dict[int, int] = {}
        turret_spawn: dict[int, tuple[int, str, tuple[int, int]]] = {}  # id -> (spawn_t, team, pos)

        for t, before, after in replay.iter_worlds():
            # Track active turrets
            for e in after.entities.values():
                if e.kind in ("gunner", "sentinel", "launcher"):
                    turret_spawn.setdefault(e.id, (t, e.team, (e.x, e.y)))

            for step in replay.substeps(t):
                fires = [a for a in step.actions if a.kind == "fireTurret"]
                if not fires:
                    continue
                hurt = [a.payload.get("id") for a in step.actions if a.kind == "updateHp" and a.payload.get("delta", 0) < 0]

                for i, a in enumerate(fires):
                    fx = a.payload.get("from", {}).get("x", 0)
                    fy = a.payload.get("from", {}).get("y", 0)
                    tx = a.payload.get("to", {}).get("x", 0)
                    ty = a.payload.get("to", {}).get("y", 0)
                    shooter = before.building_at(fx, fy) or after.building_at(fx, fy)
                    shooter_id = shooter.id if shooter else None
                    if shooter_id is not None:
                        turret_last_fire[shooter_id] = t

                    ammo_cost = 10.0 if (shooter and shooter.kind == "sentinel") else 4.0

                    if i >= len(hurt):
                        # Wasted shot
                        events.append(
                            DiagnosticEvent(
                                detector="friendly_fire",
                                severity=Severity.MEDIUM,
                                confidence=Confidence.MEDIUM,
                                map_name=map_name,
                                side=side,
                                turn_start=t,
                                turn_end=t,
                                team=shooter.team if shooter else "",
                                unit_ids=[shooter_id] if shooter_id else [],
                                positions=[(tx, ty)],
                                estimated_ti_loss=ammo_cost,
                                likely_cause="SHOT_INTO_WALL_OR_MISSED",
                                suggested_module="guns",
                                message=f"Turret at ({fx},{fy}) fired wasted shot at ({tx},{ty})",
                                metadata={"subtype": "WASTED_SHOT", "ammo_cost": ammo_cost},
                            )
                        )
                        continue

                    vid = hurt[i]
                    victim = before.entities.get(vid) or after.entities.get(vid)
                    if shooter and victim and victim.team == shooter.team:
                        events.append(
                            DiagnosticEvent(
                                detector="friendly_fire",
                                severity=Severity.HIGH,
                                confidence=Confidence.HIGH,
                                map_name=map_name,
                                side=side,
                                turn_start=t,
                                turn_end=t,
                                team=shooter.team,
                                unit_ids=[shooter_id],
                                entity_ids=[victim.id],
                                positions=[(victim.x, victim.y)],
                                estimated_ti_loss=ammo_cost + 20.0,
                                likely_cause="FRIENDLY_FIRE",
                                suggested_module="guns",
                                message=f"Turret #{shooter_id} at ({fx},{fy}) shot ally {victim.kind} #{victim.id}",
                                metadata={"subtype": "FRIENDLY_FIRE", "victim_kind": victim.kind},
                            )
                        )

        # Check for turrets with BAD_FACING (never fired for 100+ turns)
        last_turn = len(list(replay.iter_worlds()))
        for tid, (spawn_t, team, pos) in turret_spawn.items():
            last_f = turret_last_fire.get(tid, spawn_t)
            if (last_turn - last_f) >= 100 and (last_turn - spawn_t) >= 100:
                events.append(
                    DiagnosticEvent(
                        detector="friendly_fire",
                        severity=Severity.MEDIUM,
                        confidence=Confidence.MEDIUM,
                        map_name=map_name,
                        side=side,
                        turn_start=spawn_t,
                        turn_end=last_turn,
                        team=team,
                        unit_ids=[tid],
                        positions=[pos],
                        estimated_ti_loss=60.0,
                        likely_cause="SHOT_BAD_FACING_NEVER_FIRED",
                        suggested_module="guns",
                        message=f"Turret #{tid} at {pos} remained inactive for {last_turn - spawn_t} turns (possible bad facing)",
                        metadata={"subtype": "SHOT_BAD_FACING", "idle_turns": last_turn - spawn_t},
                    )
                )

        return events


class ImprovedRebuildChurnDetector:
    """Classifies rebuild churn by structure type and estimates Titanium loss."""

    def __init__(self, config: Any = None):
        self.min_rebuilds = getattr(config, "churn_min_rebuilds", 3)

    def analyze_replay(self, replay: Any, map_name: str = "", side: str = "") -> list[DiagnosticEvent]:
        events: list[DiagnosticEvent] = []
        rebuild_counts: dict[tuple[int, int], int] = {}
        last_kinds: dict[tuple[int, int], str] = {}
        reported: set[tuple[int, int]] = set()

        for t, before, after in replay.iter_worlds():
            for step in replay.substeps(t):
                for a in step.actions:
                    if a.kind == "placeEntity":
                        pos_dict = a.payload.get("position", {})
                        p = (pos_dict.get("x", 0), pos_dict.get("y", 0))
                        kind = a.payload.get("entityType", "building").lower()

                        rebuild_counts[p] = rebuild_counts.get(p, 0) + 1
                        last_kinds[p] = kind

                        if rebuild_counts[p] >= self.min_rebuilds and p not in reported:
                            reported.add(p)
                            count = rebuild_counts[p]
                            base_cost = UNIT_BASE_COSTS.get(kind, 15)
                            ti_loss = float(count * base_cost + count * 5)

                            if kind == "conveyor":
                                subtype = "CONVEYOR_CHURN"
                            elif kind == "barrier":
                                subtype = "BARRIER_CHURN"
                            elif kind == "harvester":
                                subtype = "HARVESTER_CHURN"
                            elif kind in ("gunner", "sentinel"):
                                subtype = "DEFENSE_CHURN"
                            else:
                                subtype = "REPAIR_CHURN"

                            actor = step.actor or 0
                            e = after.entities.get(actor)
                            team = e.team if e else ""

                            events.append(
                                DiagnosticEvent(
                                    detector="rebuild_churn",
                                    severity=Severity.HIGH if subtype in ("DEFENSE_CHURN", "HARVESTER_CHURN") else Severity.MEDIUM,
                                    confidence=Confidence.HIGH,
                                    map_name=map_name,
                                    side=side,
                                    turn_start=t,
                                    turn_end=t,
                                    team=team,
                                    unit_ids=[actor] if actor else [],
                                    positions=[p],
                                    estimated_ti_loss=ti_loss,
                                    likely_cause=f"Subtype: {subtype}",
                                    suggested_module="defence" if "DEFENSE" in subtype else "logistics",
                                    message=f"{subtype}: Tile {p} rebuilt {count} times (last: {kind})",
                                    metadata={"subtype": subtype, "rebuild_count": count, "kind": kind},
                                )
                            )
        return events


class ImprovedOscillationDetector:
    """Detects unit position loops and infers likely pathing root causes."""

    def __init__(self, config: Any = None):
        self.window = getattr(config, "oscillation_window", 6)
        self.min_hits = getattr(config, "oscillation_min_hits", 3)

    def analyze_replay(self, replay: Any, map_name: str = "", side: str = "") -> list[DiagnosticEvent]:
        events: list[DiagnosticEvent] = []
        history: dict[int, list[tuple[int, int]]] = {}
        hits: dict[int, int] = {}
        reported: set[int] = set()

        for t, before, after in replay.iter_worlds():
            for e in after.entities.values():
                if e.is_building:
                    continue
                h = history.setdefault(e.id, [])
                pos = (e.x, e.y)

                if pos in h[-self.window:]:
                    hits[e.id] = hits.get(e.id, 0) + 1
                    if hits[e.id] >= self.min_hits and e.id not in reported:
                        reported.add(e.id)

                        # Infer root cause from surroundings
                        nearby_building = after.building_at(e.x + 1, e.y) or after.building_at(e.x - 1, e.y)
                        if nearby_building and nearby_building.team != e.team:
                            cause = "BLOCKED_ENEMY_BARRIER"
                        elif nearby_building and nearby_building.team == e.team:
                            cause = "BLOCKED_OWN_BUILDING"
                        else:
                            cause = "CONFLICTING_GOALS"

                        events.append(
                            DiagnosticEvent(
                                detector="oscillation",
                                severity=Severity.MEDIUM,
                                confidence=Confidence.HIGH if cause != "CONFLICTING_GOALS" else Confidence.MEDIUM,
                                map_name=map_name,
                                side=side,
                                turn_start=t,
                                turn_end=t,
                                team=e.team,
                                unit_ids=[e.id],
                                entity_ids=[e.id],
                                positions=[pos],
                                estimated_ti_loss=float(hits[e.id] * 5),
                                likely_cause=cause,
                                suggested_module="movement",
                                message=f"Unit #{e.id} oscillating near {pos} (likely cause: {cause})",
                                metadata={"cause": cause, "hits": hits[e.id]},
                            )
                        )
                h.append(pos)
        return events


class ImprovedIdleDetector:
    """Detects idle units, cross-referencing team Titanium reserves."""

    def __init__(self, config: Any = None):
        self.idle_turns = getattr(config, "idle_min_turns", 8)

    def analyze_replay(self, replay: Any, map_name: str = "", side: str = "") -> list[DiagnosticEvent]:
        events: list[DiagnosticEvent] = []
        streaks: dict[int, int] = {}
        reported: set[int] = set()

        for t, before, after in replay.iter_worlds():
            for step in replay.substeps(t):
                actor = step.actor
                if actor is None:
                    continue
                if any(a.kind in MEANINGFUL for a in step.actions):
                    streaks[actor] = 0
                    continue

                streaks[actor] = streaks.get(actor, 0) + 1
                if streaks[actor] == self.idle_turns and actor not in reported:
                    reported.add(actor)
                    e = after.entities.get(actor)
                    team = e.team if e else "TEAM_A"
                    player_state = after.players.get(team)
                    bank = player_state.titanium if player_state else 0

                    if bank >= 40:
                        sev = Severity.HIGH
                        cause = "IDLE_WITH_SUFFICIENT_BANK"
                    elif bank < 10:
                        sev = Severity.LOW
                        cause = "TI_STARVED_WAITING_FOR_BANK"
                    else:
                        sev = Severity.MEDIUM
                        cause = "ROLE_LOGIC_FALLTHROUGH"

                    events.append(
                        DiagnosticEvent(
                            detector="idle",
                            severity=sev,
                            confidence=Confidence.HIGH,
                            map_name=map_name,
                            side=side,
                            turn_start=t,
                            turn_end=t,
                            team=team,
                            unit_ids=[actor],
                            positions=[(e.x, e.y)] if e else [],
                            estimated_ti_loss=float(self.idle_turns * 2),
                            likely_cause=cause,
                            suggested_module="builder",
                            message=f"Unit #{actor} idle for {self.idle_turns} consecutive turns (Bank: {bank} Ti)",
                            metadata={"bank": bank, "cause": cause},
                        )
                    )
        return events


class ImprovedHoardingDetector:
    """Detects unspent Titanium hoard, classifying root causes."""

    def __init__(self, config: Any = None):
        self.hoard_ti = getattr(config, "hoarding_min_ti", 250)
        self.hoard_turns = getattr(config, "hoarding_min_turns", 25)

    def analyze_replay(self, replay: Any, map_name: str = "", side: str = "") -> list[DiagnosticEvent]:
        events: list[DiagnosticEvent] = []
        streaks = {"TEAM_A": 0, "TEAM_B": 0}
        reported = {"TEAM_A": False, "TEAM_B": False}

        for t, before, after in replay.iter_worlds():
            for team, ps in after.players.items():
                if ps.titanium >= self.hoard_ti:
                    streaks[team] += 1
                    if streaks[team] >= self.hoard_turns and not reported[team]:
                        reported[team] = True
                        builders = [e for e in after.entities.values() if e.team == team and e.kind == "builder"]
                        builder_count = len(builders)

                        if builder_count <= 2:
                            cause = "BUILDER_SHORTAGE"
                        else:
                            cause = "NO_SPAWN_SPACE_OR_LOGIC_SATURATION"

                        events.append(
                            DiagnosticEvent(
                                detector="hoarding",
                                severity=Severity.HIGH,
                                confidence=Confidence.HIGH,
                                map_name=map_name,
                                side=side,
                                turn_start=t,
                                turn_end=t,
                                team=team,
                                estimated_ti_loss=float(ps.titanium * 0.5),
                                likely_cause=cause,
                                suggested_module="policy",
                                message=f"Team {team} hoarding {ps.titanium} Ti for {streaks[team]} turns ({cause})",
                                metadata={"bank": ps.titanium, "builder_count": builder_count, "cause": cause},
                            )
                        )
                else:
                    streaks[team] = 0

        return events
