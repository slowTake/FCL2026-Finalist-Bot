"""Grand_knight — Exploration Domain: Spread Zoning & Soft Sector Distribution.

Distributes economy builders across angular quadrants and radial distance bands
to prevent clustering around home and achieve rapid map-wide ore coverage.
"""

from __future__ import annotations

from dataclasses import dataclass
from fcode import Position

from config import Config


@dataclass(frozen=True)
class SpreadZone:
    """Soft directional wedge and radial distance band assignment."""

    quadrant: int  # 0: North, 1: East, 2: South, 3: West
    band: int  # 0: Near (<=100), 1: Mid (101-400), 2: Far (>400)


def assign_spread_zone(builder_id: int) -> SpreadZone:
    """Assign deterministic spread zone based on unit ID."""
    quadrant = builder_id % 4
    band = (builder_id // 4) % 3
    return SpreadZone(quadrant, band)


def get_quadrant(pos: Position, core_pos: Position) -> int:
    """Determine dominant cardinal quadrant of pos relative to core_pos."""
    dx = pos.x - core_pos.x
    dy = pos.y - core_pos.y

    abs_dx = abs(dx)
    abs_dy = abs(dy)

    if abs_dy >= abs_dx:
        return 0 if dy <= 0 else 2
    else:
        return 1 if dx >= 0 else 3


def get_distance_band(dist_sq: int) -> int:
    """Determine radial distance band based on squared Euclidean distance."""
    if dist_sq <= Config.SPREAD_ZONE_NEAR_DIST_SQ:
        return 0
    elif dist_sq <= Config.SPREAD_ZONE_MID_DIST_SQ:
        return 1
    else:
        return 2


def in_spread_zone(pos: Position, core_pos: Position, zone: SpreadZone) -> bool:
    """True if pos strictly matches assigned quadrant and distance band."""
    if not Config.SPREAD_ZONE_ENABLED:
        return True

    q = get_quadrant(pos, core_pos)
    if q != zone.quadrant:
        return False

    dist_sq = pos.distance_squared(core_pos)
    b = get_distance_band(dist_sq)
    return b == zone.band


def zone_score(pos: Position, core_pos: Position, zone: SpreadZone) -> int:
    """Compute soft alignment score (higher is better) for pos within assigned zone."""
    if not Config.SPREAD_ZONE_ENABLED:
        return 100

    q = get_quadrant(pos, core_pos)
    dist_sq = pos.distance_squared(core_pos)
    b = get_distance_band(dist_sq)

    if q == zone.quadrant:
        if b == zone.band:
            return 100
        else:
            band_diff = abs(b - zone.band)
            return 60 - (band_diff * 15)
    else:
        # Penalize mismatched quadrant
        quad_diff = min(abs(q - zone.quadrant), 4 - abs(q - zone.quadrant))
        return max(5, 25 - (quad_diff * 10))
