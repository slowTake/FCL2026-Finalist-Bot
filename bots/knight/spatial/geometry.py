"""knight — Spatial Geometry Re-export Module.

Exposes geometry, bounding box, and packing helpers under the spatial namespace.
"""

from __future__ import annotations

from utils.geometry import (
    clamp_to_core_area,
    choose_guardian_stand_tile,
    get_core_bbox,
    get_core_center_tile,
    get_core_footprint,
    get_greedy_step,
    get_invalid_ore_reason,
    get_reserved_builder_ids,
    is_adjacent,
    is_confirmed_ore_target,
    is_in_bounds,
    is_in_vision,
    is_inside_core_area,
    is_plausible_ore_target,
    is_valid_ore_target,
    pack_builder_intent,
    pack_intent,
    pack_ore_discovery,
    pack_ore_status,
    pack_pos,
    pack_position,
    pack_role_assignment,
    unpack_builder_intent,
    unpack_intent,
    unpack_ore_discovery,
    unpack_ore_status,
    unpack_pos,
    unpack_position,
    unpack_role_assignment,
)
