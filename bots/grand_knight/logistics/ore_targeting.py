"""Grand_knight — Logistics Domain: Ore Target Discovery, Scoring & Blacklisting.

Owns the local per-builder ore blacklist (reason-differentiated TTLs), the
unreachable-ore timeout registry, vision-scan discovery broadcast, and the
scored candidate dispatcher (secondary-expansion bias, zone matching, ally
deconfliction) used to pick the next ore target to commit to.
"""

from __future__ import annotations

import sys
from typing import Optional, Set
from fcode import Controller, EntityType, Environment, Position

from blackboard.ore_protocol import OreLifecycle, is_valid_ore_target, pos_key
from blackboard.storecoding import pack_ore_discovery, unpack_builder_intent, unpack_ore_discovery
from config import Config
from engine.safe_controller import SafeController
from exploration.frontier import FrontierExplorer
from exploration.spread_zone import SpreadZone, in_spread_zone
from logistics.raid_targeting import is_valid_raid_target
from utils.geometry import get_core_footprint, is_adjacent, is_in_friendly_half


class OreTargeting:
    """Ore candidate discovery, TTL-blacklisting, and scored target selection.

    role=TARGETING_ECONOMY (default) keeps the hard is_in_friendly_half() gate.
    role=TARGETING_RAIDER bypasses it to target a hijackable enemy Harvester
    outside friendly territory, gated instead by a threat check (_is_defended)
    that economy builders never need.
    """

    def __init__(self, safe: SafeController, role: int = Config.TARGETING_ECONOMY) -> None:
        self.safe = safe
        self.role = role
        self.lifecycle = OreLifecycle()
        self.ore_blacklist: dict[Position, int] = {}
        self.unreachable_ores: dict[Position, int] = {}
        self.broadcasted_ores: set[Position] = set()
        self.no_far_ore_turns: int = 0

    def is_valid(self, ct: Controller, pos: Position) -> bool:
        """Check if a target coordinate is valid for this instance's role."""
        curr_rnd = ct.get_current_round()
        k = pos_key(pos)
        self.ore_blacklist = {
            pk: exp for pk, exp in self.ore_blacklist.items() if curr_rnd < exp
        }
        if k in self.ore_blacklist:
            return False

        core_footprint = get_core_footprint(ct)
        if self.role == Config.TARGETING_RAIDER:
            return is_valid_raid_target(self.safe, ct, pos, core_footprint)

        if not is_in_friendly_half(ct, pos, core_footprint):
            return False
        return is_valid_ore_target(ct, pos, core_footprint)

    def find_hijack_target(self, ct: Controller) -> Optional[Position]:
        """Raider-only: nearest safe enemy Harvester, from local vision or
        the shared ore-discovery broadcast (SLOT_ORE_DISCOVERY -- the same
        channel scan_and_broadcast posts enemy-harvester sightings to).
        Local vision alone only catches a target a raider happens to
        already be standing near; broadcast is what gives raiding actual
        map-wide reach. Doesn't share find_target()'s economy scoring
        (zone/secondary-expansion bias means nothing to a raider)."""
        my_pos = ct.get_position()
        best: Optional[Position] = None
        best_dist = 10**9
        for tile in self.safe.get_nearby_tiles():
            bid = ct.get_tile_building_id(tile)
            if bid is None or ct.get_entity_type(bid) != EntityType.HARVESTER:
                continue
            if not self.is_valid(ct, tile):
                continue
            d = my_pos.distance_squared(tile)
            if d < best_dist:
                best_dist = d
                best = tile

        curr_rnd = ct.get_current_round()
        disc_pos, disc_round_mod = unpack_ore_discovery(ct.read_store(Config.SLOT_ORE_DISCOVERY))
        if disc_pos is not None:
            age = (curr_rnd - disc_round_mod) % 256
            if age <= Config.ORE_DISCOVERY_TTL_ROUNDS:
                d = my_pos.distance_squared(disc_pos)
                if d < best_dist and self.is_valid(ct, disc_pos):
                    best_dist = d
                    best = disc_pos

        return best

    def report_invalid(self, ct: Controller, pos: Position, reason: str) -> None:
        """Report invalid ore to shared blackboard and local blacklist."""
        curr_rnd = ct.get_current_round()
        ttl = Config.ORE_BLACKLIST_UNREACHABLE_TTL
        if reason == "BUILDING_OCCUPIED":
            ttl = Config.ORE_BLACKLIST_OCCUPIED_TTL
        elif reason in ("NOT_ORE", "NON_ORE_ENVIRONMENT"):
            ttl = Config.ORE_BLACKLIST_NOT_ORE_TTL
        elif reason == "CORE_FOOTPRINT":
            ttl = Config.ORE_BLACKLIST_CORE_TTL

        k = pos_key(pos)
        self.ore_blacklist[k] = curr_rnd + ttl
        self.lifecycle.report_invalid_ore(ct, pos, reason)
        if Config.DEBUG_ECONOMY:
            print(f"ORE_BLACKLIST builder_id={ct.get_id()} ore={pos} reason={reason} expiry={curr_rnd + ttl}", file=sys.stderr)

    def mark_unreachable(self, pos: Position, curr_rnd: int) -> None:
        """Record a target as temporarily unreachable (pathing timeout or commit timeout)."""
        self.unreachable_ores[pos] = curr_rnd

    def scan_and_broadcast(self, ct: Controller, seen_walls: Set[Position]) -> None:
        """Scan vision for ORE_TITANIUM and WALL, updating terrain memory and broadcasting fresh discoveries."""
        curr_rnd = ct.get_current_round()
        core_footprint = get_core_footprint(ct)
        for tile in self.safe.get_nearby_tiles():
            env = ct.get_tile_env(tile)
            if env == Environment.WALL:
                seen_walls.add(tile)
            elif env == Environment.ORE_TITANIUM:
                if is_valid_ore_target(ct, tile, core_footprint):
                    bid = ct.get_tile_building_id(tile)
                    is_friendly_harvester = (
                        bid is not None
                        and ct.get_entity_type(bid) == EntityType.HARVESTER
                        and ct.get_team(bid) == ct.get_team()
                    )
                    if is_friendly_harvester:
                        OreLifecycle.record_harvester_built(tile)
                    else:
                        # Unoccupied ore or an enemy harvester (hijackable) -- both
                        # are live candidates for another builder, so both must
                        # reach the one channel that actually crosses unit
                        # boundaries (OreLifecycle is per-unit, subinterpreters
                        # share no memory -- see blackboard/ore_protocol.py).
                        OreLifecycle.record_discovered_ore(tile)
                        if tile not in self.broadcasted_ores:
                            self._broadcast_ore_discovery(ct, tile, curr_rnd)

    def _broadcast_ore_discovery(self, ct: Controller, tile: Position, curr_rnd: int) -> None:
        """Write tile to SLOT_ORE_DISCOVERY unless a different, still-fresh
        discovery already occupies it -- otherwise every scanning builder's
        next tick immediately clobbers whatever the last one just broadcast,
        and no other builder ever gets a real window to read it first."""
        existing_pos, existing_round_mod = unpack_ore_discovery(ct.read_store(Config.SLOT_ORE_DISCOVERY))
        if existing_pos is not None and existing_pos != tile:
            age = (curr_rnd - existing_round_mod) % 256
            if age <= Config.ORE_DISCOVERY_TTL_ROUNDS:
                return
        ct.write_store(Config.SLOT_ORE_DISCOVERY, pack_ore_discovery(tile, curr_rnd))
        self.broadcasted_ores.add(tile)

    def find_target(
        self,
        ct: Controller,
        frontier: FrontierExplorer,
        spread_zone: Optional[SpreadZone],
    ) -> Optional[Position]:
        """Pure runtime target dispatcher with Secondary Expansion Bias and Deconfliction."""
        pos = ct.get_position()
        curr_rnd = ct.get_current_round()
        my_id = ct.get_id()
        core_footprint = get_core_footprint(ct)
        core_tile = core_footprint[0]
        timeout = Config.UNREACHABLE_ORE_TIMEOUT
        self.unreachable_ores = {
            p: r for p, r in self.unreachable_ores.items() if curr_rnd - r <= timeout
        }

        # Gather candidate ore targets
        candidates: list[Position] = []

        # Candidate 1: Core-authored strategic intent hint (SLOT_BUILDER_INTENT)
        raw_intent = ct.read_store(Config.SLOT_BUILDER_INTENT)
        if raw_intent != 0:
            ix, iy, action, _ = unpack_builder_intent(raw_intent)
            if action == Config.INTENT_EXPAND_ORE:
                intent_ore = Position(ix, iy)
                if intent_ore not in self.unreachable_ores and self.is_valid(ct, intent_ore):
                    candidates.append(intent_ore)

        # Candidate 2: Local vision scan via FrontierExplorer
        loc_ore = frontier.local_ore_target(ct)
        if loc_ore is not None and loc_ore not in self.unreachable_ores and self.is_valid(ct, loc_ore):
            if loc_ore not in candidates:
                candidates.append(loc_ore)

        # Candidate 3: Store broadcast far-ore discovery (freshness-verified)
        store_ore = frontier.store_ore_target(ct)
        if store_ore is not None and store_ore not in self.unreachable_ores and self.is_valid(ct, store_ore):
            if store_ore not in candidates:
                candidates.append(store_ore)

        # Candidate 4: Shared known unmined ore registry from OreLifecycle
        for cand_ore in OreLifecycle.get_unmined_candidates(my_id, curr_rnd):
            if cand_ore not in candidates and cand_ore not in self.unreachable_ores and self.is_valid(ct, cand_ore):
                candidates.append(cand_ore)

        # Filter out candidates actively claimed by other friendly builders
        candidates = [c for c in candidates if not OreLifecycle.is_claimed_by_other(c, my_id, curr_rnd)]

        if not candidates:
            return None

        # Check secondary expansion preferences
        my_team = ct.get_team()
        harvester_count = sum(
            1 for bid in self.safe.get_nearby_buildings()
            if ct.get_team(bid) == my_team and ct.get_entity_type(bid) == EntityType.HARVESTER
        )
        prefer_far = (harvester_count >= 2)

        scored: list[tuple[int, Position]] = []
        has_far_candidate = False

        for cand in candidates:
            dist_sq = cand.distance_squared(core_tile)
            is_far = dist_sq >= Config.SECONDARY_EXPANSION_MIN_DIST_SQ
            if is_far:
                has_far_candidate = True

            if not prefer_far:
                # Home security: prioritize close ore (< 100 dist_sq) first
                score = 150 if not is_far else 50
            else:
                # Expansion phase: prioritize far ore
                score = 150 if is_far else 30

            # Zone matching bonus
            zone_id = -1
            if spread_zone is not None:
                zone_id = spread_zone.quadrant
                if in_spread_zone(cand, core_tile, spread_zone):
                    score += 50
                if spread_zone.band >= 1 and is_far:
                    score += 50

            # Fresh store ore bonus
            if cand == store_ore:
                score += 30

            # Proximity to builder
            my_cand_dist_sq = pos.distance_squared(cand)
            score -= my_cand_dist_sq // 2

            # Deconfliction: if another friendly builder is adjacent or closer to this candidate, discount
            for uid in self.safe.get_nearby_units():
                if uid != my_id and ct.get_team(uid) == my_team:
                    upos = ct.get_position(uid)
                    if is_adjacent(upos, cand):
                        score -= 300
                        break
                    other_cand_dist_sq = upos.distance_squared(cand)
                    if other_cand_dist_sq < my_cand_dist_sq:
                        score -= 80

            if Config.DEBUG_ECONOMY:
                print(
                    f"EXPANSION_TARGET_SCORE builder_id={my_id} ore={cand} score={score} "
                    f"dist_sq={dist_sq} zone={zone_id} round={curr_rnd}",
                    file=sys.stderr,
                )
            scored.append((score, cand))

        if has_far_candidate:
            self.no_far_ore_turns = 0
        elif prefer_far:
            self.no_far_ore_turns += 1

        scored.sort(key=lambda item: item[0], reverse=True)

        if not scored or scored[0][0] <= 0:
            return None

        # If fallback needed when no far ore found for SECONDARY_LOCAL_FALLBACK_TURNS
        if prefer_far and not has_far_candidate and self.no_far_ore_turns < Config.SECONDARY_LOCAL_FALLBACK_TURNS:
            return None

        return scored[0][1]
