#!/usr/bin/env python3
"""Minimal reconstructed-state Controller, enough to call real baron logistics
functions (pickRelayPath, relayPassable, the orphan-relay registry, ...)
against ground-truth board state pulled from a .replay26 file at a given
turn. Not a full engine mock — see MockCT's methods for exactly what's
covered. Built during the investigation that led to the orphan relay
registry (bots/baron/logistics.py; see CONTEXT.md's OrphanRelayRegistry
section) and promoted here so it's reusable, not scratchpad-only.

`sys.path` is adjusted so this can be imported standalone:
    python -c "import sys; sys.path.insert(0, 'scripts'); from mock_ct import Snapshot, MockCT"
or run scripts from within scripts/ directly.
"""
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
_BARON_DIR = _SCRIPTS_DIR.parent / "bots" / "baron"
for _p in (_SCRIPTS_DIR, _BARON_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import florent_replay_pb2 as pb
from fcode import Team, EntityType, Environment, Direction, Position

TEAM_B = pb.TEAM_B
_KIND2TYPE = {
    "builderBot": EntityType.BUILDER_BOT,
    "conveyor": EntityType.CONVEYOR,
    "splitter": EntityType.SPLITTER,
    "harvester": EntityType.HARVESTER,
    "barrier": EntityType.BARRIER,
    "core": EntityType.CORE,
    "gunner": EntityType.GUNNER,
    "sentinel": EntityType.SENTINEL,
    "launcher": EntityType.LAUNCHER,
}
_ENV = {0: Environment.EMPTY, 1: Environment.WALL, 2: Environment.ORE_TITANIUM, 3: Environment.ORE_TITANIUM}


class Snapshot:
    """Ground-truth board state reconstructed up through turn `upto` (inclusive)."""

    def __init__(self, path, upto):
        with open(path, "rb") as f:
            r = pb.Replay()
            r.ParseFromString(f.read())
        self.width = r.map.width
        self.height = r.map.height
        self.env = {}
        for y, row in enumerate(r.map.rows):
            for x, t in enumerate(row.tiles):
                self.env[(x, y)] = _ENV.get(t, Environment.EMPTY)

        self.pos = {}
        self.kind = {}
        self.team = {}
        self.alive = set()
        self.fire_events = []  # (turn, from, to)
        self.round = upto
        for turn_i, turn in enumerate(r.turns):
            if turn_i > upto:
                break
            self.round = turn_i
            for u in turn.updates:
                k = u.WhichOneof("kind")
                if k == "placeEntity":
                    e = u.placeEntity.entity
                    self.pos[e.id] = (e.position.x, e.position.y)
                    self.kind[e.id] = e.WhichOneof("kind")
                    self.team[e.id] = e.team
                    self.alive.add(e.id)
                elif k == "moveBuilderBot":
                    m = u.moveBuilderBot
                    self.pos[m.id] = (m.to.x, m.to.y)
                elif k == "removeEntity":
                    self.alive.discard(u.removeEntity.id)
                elif k == "fireTurret":
                    ft = u.fireTurret
                    frm = getattr(ft, "from")
                    self.fire_events.append((turn_i, (frm.x, frm.y), (ft.to.x, ft.to.y)))

        # tile -> id, for BUILDINGS only (not builder bots)
        self.building_at = {}
        # tile -> id, for builder bots
        self.bot_at = {}
        for eid in self.alive:
            p = self.pos.get(eid)
            if p is None:
                continue
            kd = self.kind.get(eid)
            if kd == "builderBot":
                self.bot_at[p] = eid
            elif kd == "core":
                cx, cy = p
                for dx in (0, 1):
                    for dy in (0, 1):
                        self.building_at[(cx + dx, cy + dy)] = eid
            else:
                self.building_at[p] = eid


class MockCT:
    """Read-only-ish Controller stand-in over a Snapshot, scoped to team B ('me').

    Adds a plain in-memory communication store on top of the original
    pathing-only mock, so this can also drive `logistics.py`'s orphan-relay
    registry functions (`reportOrphanRelay`, `_pickOrphanRelay`, ...) in
    tests — unlike the real engine, writes here are visible immediately
    (no next-round buffering); tests that care about that distinction should
    model it explicitly rather than relying on this mock.
    """

    def __init__(self, snap: Snapshot, self_pos=None, store: dict | None = None, self_id: int = -1):
        self.snap = snap
        self._self_pos = self_pos
        self.store = store if store is not None else {}
        self._self_id = self_id

    def get_id(self) -> int:
        return self._self_id

    def get_map_width(self):
        return self.snap.width

    def get_map_height(self):
        return self.snap.height

    def is_in_vision(self, pos):
        return True  # full ground truth — see caveats in CONTEXT.md/plan notes

    def get_tile_env(self, pos):
        return self.snap.env.get((pos.x, pos.y), Environment.EMPTY)

    def get_tile_building_id(self, pos):
        return self.snap.building_at.get((pos.x, pos.y))

    def get_tile_builder_bot_id(self, pos):
        return self.snap.bot_at.get((pos.x, pos.y))

    def get_entity_type(self, eid):
        return _KIND2TYPE.get(self.snap.kind.get(eid))

    def get_team(self, eid=None):
        if eid is None:
            return Team.B
        t = self.snap.team.get(eid)
        return Team.B if t == TEAM_B else Team.A

    def get_position(self, eid=None):
        if eid is None:
            return self._self_pos
        p = self.snap.pos.get(eid)
        return Position(p[0], p[1]) if p else None

    def get_nearby_entities(self):
        return list(self.snap.alive)

    def get_nearby_units(self):
        return [e for e in self.snap.alive if self.snap.kind.get(e) != "conveyor" and self.snap.kind.get(e) != "splitter"]

    def can_fire_from(self, site, facing, entity_type, target):
        return False  # force gunnerHasLos's manual ray walk

    def get_direction(self, eid=None):
        return Direction.NORTH

    def get_current_round(self):
        return self.snap.round

    def read_store(self, index: int) -> int:
        return self.store.get(index, 0)

    def write_store(self, index: int, value: int) -> None:
        self.store[index] = value
