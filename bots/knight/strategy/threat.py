"""knight — Strategy Domain: Threat Classification & Perimeter Sensing.

Provides deterministic, testable threat classification and distance-calibrated
perimeter pressure evaluation for the Core and Base Defense systems.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Optional
from fcode import Controller, EntityType, Position

from config import Config
from engine.safe_controller import SafeController


class ThreatClass(IntEnum):
    """Calibrated threat severity classification levels."""
    SAFE = 0
    SCOUT_SIGHTED = 1
    PERIMETER_PRESSURE = 2
    BREACH = 3


@dataclass(frozen=True)
class ThreatAssessment:
    """Immutable snapshot of threat analysis around the Core."""
    classification: ThreatClass
    pressure: int
    nearest_threat_pos: Optional[Position]
    nearest_threat_dist_sq: int
    enemy_count: int
    armed_count: int
    breach: bool


class ThreatClassifier:
    """Deterministic threat evaluator analyzing entity proximity to Core."""

    def __init__(self, safe: SafeController) -> None:
        self.safe = safe

    def evaluate(self, ct: Controller, core_footprint: list[Position]) -> ThreatAssessment:
        """Classify threats against calibrated distance thresholds."""
        my_team = ct.get_team()
        curr_round = ct.get_current_round()

        nearest_threat_pos: Optional[Position] = None
        nearest_threat_dist = 10**9
        pressure = 0
        enemy_count = 0
        armed_count = 0
        breach = False
        highest_class = ThreatClass.SAFE

        # Core damaged is an immediate breach condition
        core_id = ct.get_tile_building_id(core_footprint[0]) if core_footprint else None
        if core_id is not None:
            if ct.get_hp(core_id) < ct.get_max_hp(core_id):
                breach = True
                highest_class = ThreatClass.BREACH
                pressure += 4
        elif ct.get_entity_type() == EntityType.CORE and ct.get_hp() < ct.get_max_hp():
            breach = True
            highest_class = ThreatClass.BREACH
            pressure += 4

        for eid in self.safe.get_nearby_entities():
            if ct.get_team(eid) == my_team:
                continue

            epos = ct.get_position(eid)
            etype = ct.get_entity_type(eid)
            d = min(epos.distance_squared(cp) for cp in core_footprint)

            if etype == EntityType.BUILDER_BOT:
                if d > Config.IGNORE_SCOUT_DIST_SQ:
                    continue
                elif d <= Config.ENEMY_BUILDER_ALERT_DIST_SQ:
                    enemy_count += 1
                    pressure += 1
                    if highest_class < ThreatClass.SCOUT_SIGHTED:
                        highest_class = ThreatClass.SCOUT_SIGHTED
                    if d < nearest_threat_dist:
                        nearest_threat_dist = d
                        nearest_threat_pos = epos

            elif etype in (EntityType.GUNNER, EntityType.SENTINEL, EntityType.LAUNCHER):
                enemy_count += 1
                armed_count += 1
                if d <= Config.ARMED_BREACH_DIST_SQ:
                    breach = True
                    pressure += 3
                    highest_class = ThreatClass.BREACH
                    if d < nearest_threat_dist:
                        nearest_threat_dist = d
                        nearest_threat_pos = epos
                elif d <= Config.ARMED_ALERT_DIST_SQ:
                    pressure += 2
                    if highest_class < ThreatClass.PERIMETER_PRESSURE:
                        highest_class = ThreatClass.PERIMETER_PRESSURE
                    if d < nearest_threat_dist:
                        nearest_threat_dist = d
                        nearest_threat_pos = epos

            else:
                # Harmless buildings (conveyors, harvesters, barriers) do not generate military threat
                pass

        return ThreatAssessment(
            classification=highest_class,
            pressure=pressure,
            nearest_threat_pos=nearest_threat_pos,
            nearest_threat_dist_sq=nearest_threat_dist if nearest_threat_pos is not None else 10**9,
            enemy_count=enemy_count,
            armed_count=armed_count,
            breach=breach,
        )
