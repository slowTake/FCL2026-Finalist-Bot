#!/usr/bin/env python3
"""Verify WALL_LOCATIONS and ORE_LOCATIONS in features.py against actual .map26 protobuf map files."""

from __future__ import annotations

import sys
from pathlib import Path

# Add teaPot/scripts and teaPot/components to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "components"))

import florent_replay_pb2 as pb
import features

ROTATION_MAPS = [
    "antler", "archipelago", "atoll", "drumlin", "eider",
    "fjordgate", "heart", "hive", "jackpot", "lighthouse",
    "meander", "moonrise", "nordkap", "saga", "snowflake"
]


def verify_map_features():
    project_root = Path(__file__).resolve().parent.parent
    maps_dir = project_root / "maps"

    print("=" * 95)
    print("VERIFYING FEATURES.PY (WALL_LOCATIONS & ORE_LOCATIONS) AGAINST ACTUAL .MAP26 PROTOBUF FILES")
    print("=" * 95)
    print()

    # Map ID -> map_name
    map_id_to_name = {}
    for fp, val in features.KNOWN_MAPS.items():
        m_id, m_name = val[0], val[1]
        map_id_to_name[m_id] = m_name

    all_passed = True
    summary = []

    for m_name in ROTATION_MAPS:
        # Find map_id
        m_id = None
        for mid, name in map_id_to_name.items():
            if name == m_name:
                m_id = mid
                break

        if m_id is None:
            print(f"Error: Map '{m_name}' not found in KNOWN_MAPS dictionary!")
            all_passed = False
            continue

        map_path = maps_dir / f"{m_name}.map26"
        if not map_path.exists():
            print(f"Error: Map file '{map_path}' missing!")
            all_passed = False
            continue

        # Parse .map26 protobuf
        with open(map_path, "rb") as f:
            m_pb = pb.Map()
            m_pb.ParseFromString(f.read())

        pb_walls = set()
        pb_ores = set()
        for y, row in enumerate(m_pb.rows):
            for x, tile in enumerate(row.tiles):
                if tile == pb.ENV_WALL:
                    pb_walls.add((x, y))
                elif tile == pb.ENV_ORE_TITANIUM:
                    pb_ores.add((x, y))

        dict_walls = set(features.WALL_LOCATIONS.get(m_id, []))
        dict_ores = set(features.ORE_LOCATIONS.get(m_id, []))

        walls_match = (dict_walls == pb_walls)
        ores_match = (dict_ores == pb_ores)
        match_pass = walls_match and ores_match

        if not match_pass:
            all_passed = False

        wall_diff_missing = pb_walls - dict_walls
        wall_diff_extra = dict_walls - pb_walls

        ore_diff_missing = pb_ores - dict_ores
        ore_diff_extra = dict_ores - pb_ores

        summary.append({
            "name": m_name,
            "id": m_id,
            "pass": match_pass,
            "walls_dict": len(dict_walls),
            "walls_pb": len(pb_walls),
            "walls_match": walls_match,
            "wall_missing": len(wall_diff_missing),
            "wall_extra": len(wall_diff_extra),
            "ores_dict": len(dict_ores),
            "ores_pb": len(pb_ores),
            "ores_match": ores_match,
            "ore_missing": len(ore_diff_missing),
            "ore_extra": len(ore_diff_extra),
        })

        w_str = "MATCH" if walls_match else f"MISMATCH (dict={len(dict_walls)}, pb={len(pb_walls)})"
        o_str = "MATCH" if ores_match else f"MISMATCH (dict={len(dict_ores)}, pb={len(pb_ores)})"
        st = "PASS" if match_pass else "FAIL"

        print(f"[{m_id:>2}] {m_name:<16} Status: {st:<4} | Walls: {w_str:<32} | Ores: {o_str:<32}")

    print("\n" + "=" * 95)
    print("DETAILED VERIFICATION TABLE")
    print("=" * 95)
    print(f"{'ID':<4} {'Map Name':<16} {'Status':<6} {'Walls Dict/PB':<16} {'Walls Match':<13} {'Ores Dict/PB':<15} {'Ores Match':<12}")
    print("-" * 95)

    for s in summary:
        st = "PASS" if s["pass"] else "FAIL"
        wm = "OK" if s["walls_match"] else f"ERR (-{s['wall_missing']},+{s['wall_extra']})"
        om = "OK" if s["ores_match"] else f"ERR (-{s['ore_missing']},+{s['ore_extra']})"
        w_counts = f"{s['walls_dict']}/{s['walls_pb']}"
        o_counts = f"{s['ores_dict']}/{s['ores_pb']}"
        print(f"{s['id']:<4} {s['name']:<16} {st:<6} {w_counts:<16} {wm:<13} {o_counts:<15} {om:<12}")

    print("=" * 95)
    if all_passed:
        print("SUCCESS: ALL 15 MAPS HAVE 100% EXACT WALL & ORE LOCATION MATCHES WITH .MAP26 FILES!")
        return 0
    else:
        print("FAILURE: ONE OR MORE MAPS HAVE DISCREPANCIES IN WALL OR ORE LOCATIONS!")
        return 1


if __name__ == "__main__":
    sys.exit(verify_map_features())
