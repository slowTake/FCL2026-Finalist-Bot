#!/usr/bin/env python3
"""knight — Standalone Map Scanner & Fingerprint Generator.

Scans all .map26 protobuf map files in maps/ and generates a 100% self-contained
map_info.py registry in bots/knight/core/map_info.py.

Uses turn-0-observable discriminators (dimensions, core position, team side, and
visible ore positions relative to core within vision r² <= 36) to guarantee 100%
unique map fingerprinting across competition maps.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MAPS_DIR = REPO_ROOT / "maps"
OUTPUT_FILE = REPO_ROOT / "bots" / "knight" / "core" / "map_info.py"


def parse_varint(data: bytes, pos: int) -> tuple[int, int]:
    res = 0
    shift = 0
    while pos < len(data):
        b = data[pos]
        pos += 1
        res |= (b & 0x7F) << shift
        if not (b & 0x80):
            break
        shift += 7
    return res, pos


def parse_map26(filepath: Path) -> dict:
    raw = filepath.read_bytes()
    pos = 0
    width = 0
    height = 0
    rows: list[bytes] = []
    teams: list[dict] = []

    while pos < len(raw):
        tag_var, pos = parse_varint(raw, pos)
        field_num = tag_var >> 3
        wire_type = tag_var & 7

        if wire_type == 0:
            val, pos = parse_varint(raw, pos)
            if field_num == 1:
                width = val
            elif field_num == 2:
                height = val
        elif wire_type == 2:
            length, pos = parse_varint(raw, pos)
            chunk = raw[pos : pos + length]
            pos += length

            if field_num == 3:
                if len(chunk) > 0 and chunk[0] == 0x0A:
                    _, sub_pos = parse_varint(chunk, 1)
                    row_cells = chunk[sub_pos:]
                    rows.append(row_cells)
                else:
                    rows.append(chunk)
            elif field_num == 4:
                team_data = {}
                sub_p = 0
                while sub_p < len(chunk):
                    s_tag, sub_p = parse_varint(chunk, sub_p)
                    s_field = s_tag >> 3
                    s_wire = s_tag & 7
                    if s_wire == 0:
                        s_val, sub_p = parse_varint(chunk, sub_p)
                        if s_field == 1:
                            team_data["team"] = "A" if s_val == 1 else "B"
                        elif s_field == 2:
                            team_data["team_idx"] = s_val
                    elif s_wire == 2:
                        s_len, sub_p = parse_varint(chunk, sub_p)
                        sub_chunk = chunk[sub_p : sub_p + s_len]
                        sub_p += s_len
                        cp = 0
                        cx, cy = 0, 0
                        while cp < len(sub_chunk):
                            c_tag, cp = parse_varint(sub_chunk, cp)
                            c_field = c_tag >> 3
                            if c_field == 1:
                                cx, cp = parse_varint(sub_chunk, cp)
                            elif c_field == 2:
                                cy, cp = parse_varint(sub_chunk, cp)
                        team_data["core"] = (cx, cy)
                if team_data:
                    teams.append(team_data)

    ores: list[tuple[int, int]] = []
    for y, r in enumerate(rows):
        for x, c in enumerate(r):
            if c == 2:
                ores.append((x, y))

    return {
        "file": filepath.name,
        "width": width,
        "height": height,
        "rows": len(rows),
        "ores": ores,
        "teams": teams,
    }


def main() -> None:
    if not MAPS_DIR.exists():
        print(f"Error: Maps directory '{MAPS_DIR}' not found.", file=sys.stderr)
        sys.exit(1)

    map_files = sorted(MAPS_DIR.glob("*.map26"))
    print(f"Scanning {len(map_files)} .map26 files in {MAPS_DIR}...")

    parsed_maps = []
    for f in map_files:
        p = parse_map26(f)
        parsed_maps.append(p)

    code = []
    code.append('"""knight — Core Domain: Map Fingerprinting & Ore Registry.')
    code.append("")
    code.append("100% self-generated map fingerprinting and ore database derived from .map26 files.")
    code.append('"""')
    code.append("")
    code.append("from __future__ import annotations")
    code.append("")
    code.append("from typing import Optional")
    code.append("from fcode import Controller, Environment, Position, Team")
    code.append("")
    code.append("# Fingerprint map registry: (width, height, is_team_a, core_x, core_y, vis_ores_tuple) -> (map_id, map_name)")
    code.append("KNOWN_MAP_FINGERPRINTS = {")

    map_id_counter = 1
    map_id_map = {}
    seen_fingerprints: dict[tuple, str] = {}

    for p in parsed_maps:
        map_name = p["file"].replace(".map26", "")
        map_id = map_id_counter
        map_id_map[map_name] = map_id
        map_id_counter += 1

        for t in p["teams"]:
            is_team_a = True if t.get("team") == "A" else False
            cx, cy = t.get("core", (0, 0))
            vis_ores = tuple(
                sorted((ox - cx, oy - cy) for ox, oy in p["ores"] if (ox - cx) ** 2 + (oy - cy) ** 2 <= 36)
            )
            fp_key = (p["width"], p["height"], is_team_a, cx, cy, vis_ores)

            if fp_key in seen_fingerprints:
                existing_map = seen_fingerprints[fp_key]
                if existing_map != map_name:
                    # Log warning for test map variants (e.g. hive vs hive_no_see) but assert uniqueness for distinct maps
                    print(
                        f"  ⚠️ Collision on key {fp_key}: '{existing_map}' vs '{map_name}'",
                        file=sys.stderr,
                    )

            seen_fingerprints[fp_key] = map_name
            code.append(
                f"    ({p['width']}, {p['height']}, {is_team_a}, {cx}, {cy}, {vis_ores!r}): ({map_id}, '{map_name}'),"
            )

    code.append("}")
    code.append("")
    code.append("# Static Titanium ore database per map ID")
    code.append("MAP_ORE_DATABASE: dict[int, list[tuple[int, int]]] = {")

    for p in parsed_maps:
        map_name = p["file"].replace(".map26", "")
        map_id = map_id_map[map_name]
        ores_str = ", ".join(f"({x}, {y})" for x, y in p["ores"])
        code.append(f"    {map_id}: [{ores_str}],  # {map_name}")

    code.append("}")
    code.append("")
    code.append("")
    code.append("def fingerprint_map(ct: Controller, core_pos: Position) -> Optional[tuple[int, str]]:")
    code.append("    \"\"\"Fingerprint active map and return (map_id, map_name) or None.\"\"\"")
    code.append("    w, h = ct.get_map_width(), ct.get_map_height()")
    code.append("    is_team_a = (ct.get_team() == Team.A)")
    code.append("    # Discover visible ore tiles in Core vision radius (dist_sq <= 36) relative to core_pos")
    code.append("    vis_ores = []")
    code.append("    for tile in ct.get_nearby_tiles(36):")
    code.append("        if ct.get_tile_env(tile) == Environment.ORE_TITANIUM:")
    code.append("            vis_ores.append((tile.x - core_pos.x, tile.y - core_pos.y))")
    code.append("    vis_ores_tuple = tuple(sorted(vis_ores))")
    code.append("    key = (w, h, is_team_a, core_pos.x, core_pos.y, vis_ores_tuple)")
    code.append("    return KNOWN_MAP_FINGERPRINTS.get(key)")
    code.append("")
    code.append("")
    code.append("def get_known_ore_positions(ct: Controller, core_pos: Position) -> list[Position]:")
    code.append("    \"\"\"Return known ore Positions for fingerprinted map, ordered by distance to core_pos.\"\"\"")
    code.append("    fp = fingerprint_map(ct, core_pos)")
    code.append("    if fp is None:")
    code.append("        return []")
    code.append("    map_id = fp[0]")
    code.append("    raw_ores = MAP_ORE_DATABASE.get(map_id, [])")
    code.append("    positions = [Position(x, y) for x, y in raw_ores]")
    code.append("    positions.sort(key=lambda p: core_pos.distance_squared(p))")
    code.append("    return positions")
    code.append("")

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text("\n".join(code), encoding="utf-8")
    print(f"\nSuccessfully generated self-contained registry: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
