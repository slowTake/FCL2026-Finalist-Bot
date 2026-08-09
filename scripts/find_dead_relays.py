#!/usr/bin/env python3
"""Scan a replay for harvesters never (or no-longer) belt-connected to their core.

Ground-truth connectivity check: BFS from each harvester tile through same-team
CONVEYOR/SPLITTER/HARVESTER tiles to a same-team CORE footprint tile. No facing
check (a mis-facing belt still "counts" as a completed relay for this purpose —
we only care whether the builder ever finished laying it and whether it's still
intact), no fog-of-war (ground truth from the replay).

Built to investigate/verify the baron "orphan relay registry" feature
(bots/baron/logistics.py, see CONTEXT.md's OrphanRelayRegistry section) —
run before/after a change and compare each side's dead/eligible rate.

Usage:
    python scripts/find_dead_relays.py <replay.replay26>
"""
import sys
from collections import deque

import florent_replay_pb2 as pb

_CONNECTOR = {"conveyor", "splitter", "harvester"}
_CARD = ((1, 0), (-1, 0), (0, 1), (0, -1))


def core_footprint(pos):
    x, y = pos
    return {(x, y), (x + 1, y), (x, y + 1), (x + 1, y + 1)}


def load(path):
    with open(path, "rb") as f:
        r = pb.Replay()
        r.ParseFromString(f.read())
    return r


def analyze(path, checkpoints=(200, 500, 999)):
    r = load(path)
    cores = {}  # team -> footprint set
    for c in r.map.cores:
        cores[c.team] = core_footprint((c.position.x, c.position.y))

    pos = {}
    kind = {}
    team = {}
    alive = set()
    placed_turn = {}

    results = {}  # team -> {harvester_id: (last_seen_turn, pos, age)}
    eligible = {}  # team -> set of harvester ids that ever reached age >= 30

    def snapshot_and_check(turn_i):
        # tile -> id, per team, for connector-type buildings
        by_team_tiles = {t: {} for t in cores}
        harvesters = {t: [] for t in cores}
        for eid in alive:
            k = kind.get(eid)
            tm = team.get(eid)
            if k not in _CONNECTOR or tm not in cores:
                continue
            by_team_tiles[tm][pos[eid]] = eid
            if k == "harvester":
                harvesters[tm].append((eid, pos[eid]))

        for tm, hlist in harvesters.items():
            goal = cores[tm]
            tiles = by_team_tiles[tm]
            for eid, hpos in hlist:
                age = turn_i - placed_turn.get(eid, turn_i)
                if age < 30:
                    continue  # too young to fairly judge
                eligible.setdefault(tm, set()).add(eid)
                # BFS from harvester tile through connector tiles to core footprint
                seen = {hpos}
                q = deque([hpos])
                connected = False
                while q:
                    p = q.popleft()
                    if p in goal:
                        connected = True
                        break
                    for dx, dy in _CARD:
                        np_ = (p[0] + dx, p[1] + dy)
                        if np_ in seen:
                            continue
                        if np_ in goal or np_ in tiles:
                            seen.add(np_)
                            q.append(np_)
                if not connected:
                    results.setdefault(tm, {})[eid] = (turn_i, hpos, age)
                else:
                    results.get(tm, {}).pop(eid, None)  # reconnected since

    next_cp = list(checkpoints)
    for turn_i, turn in enumerate(r.turns):
        for u in turn.updates:
            k = u.WhichOneof("kind")
            if k == "placeEntity":
                e = u.placeEntity.entity
                pos[e.id] = (e.position.x, e.position.y)
                kind[e.id] = e.WhichOneof("kind")
                team[e.id] = e.team
                alive.add(e.id)
                if e.id not in placed_turn:
                    placed_turn[e.id] = turn_i
            elif k == "removeEntity":
                alive.discard(u.removeEntity.id)
        if next_cp and turn_i >= next_cp[0]:
            snapshot_and_check(turn_i)
            next_cp.pop(0)
    snapshot_and_check(len(r.turns) - 1)  # final check

    return results, eligible


if __name__ == "__main__":
    import json

    path = sys.argv[1]
    res, elig = analyze(path)
    out = {}
    teams = set(res) | set(elig)
    for tm in teams:
        dead = res.get(tm, {})
        out[str(tm)] = {
            "eligible": len(elig.get(tm, set())),
            "dead": len(dead),
            "dead_detail": [
                {"harvester_id": eid, "last_seen_turn": t, "pos": p, "age": age}
                for eid, (t, p, age) in dead.items()
            ],
        }
    print(json.dumps(out, indent=2))
