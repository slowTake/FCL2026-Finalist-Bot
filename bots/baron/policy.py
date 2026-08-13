"""baron — tuning constants and store-slot layout. No internal imports (leaf
module), so every other baron module can import from here at module scope
without risking a cycle with main.py — unlike main.py itself, which can't be
imported at module scope by builder/guns/offense/etc. because it imports them
back (see main.py's own docstring for the store-slot contract this module's
SLOT_*/bit constants are part of).

main.py re-exports everything here via `from policy import *`, so existing
`from main import X` call sites elsewhere in the codebase keep working
unchanged — this move only changes where the values live, not how they're
read. New code should prefer `from policy import X` directly.

DEBUG and SPAWN_SCHEDULE stay in main.py: DEBUG is a debug flag, not a
tunable, and SPAWN_SCHEDULE is a constructed SpawnSchedule object (built from
some of the constants below plus respawn.SpawnSchedule), not a plain value.
"""

from __future__ import annotations

# --- Builder spawn policy ---
# Crew size tracks the bank, counted in *living* builders so losses are replaced.
# Rush MAX_BUILDERS_EARLY unpaced, then hold whatever the bank justifies.
# Previously the target was `MAX_BUILDERS_EARLY + round_n // interval`, i.e. a
# function of round number with the current Ti tier applied retroactively: crossing
# a bracket leapt the target and spawned a run of builders back to back, and falling
# back froze spawning for hundreds of rounds because it compared against a lifetime
# counter that never decreases. Both are gone with the round arithmetic.
# MAX_BUILDERS_EARLY = 1  # unpaced rush; also the poor-state floor
# MAX_BUILDERS = 1  # hard ceiling on living builders

# Richer bank → bigger crew. (min_ti, target); highest matching min_ti wins.
# Below all tiers the target is MAX_BUILDERS_EARLY.
BUILDER_TARGET_BY_TI: list[tuple[int, int]] = [
    (2000, 10),
    (1000, 7),
]

# Rounds after game start before the crew may grow past MAX_BUILDERS_EARLY
# (≤0 disables). The bank-tier target above is otherwise a pure stock check —
# passive income alone (10 Ti/4 rounds) crosses the 1000 Ti / 7-builder tier
# at turn 200 on the nose regardless of whether there's any unclaimed ore left
# to justify two more units, and since nothing paces the spawns themselves,
# both land back-to-back the moment the bracket is crossed. This clamps the
# target back to MAX_BUILDERS_EARLY until the delay elapses; the bank tiers
# still gate everything past that point exactly as before — this only holds
# the door shut a while longer, it doesn't replace the bank check.
BUILDER_GROWTH_DELAY_TURNS = 0

# --- Guardian cap (≤0 / None disables) ---
# Guardian slots are idx % 5 in (0, 1) — 2 per 5 spawns. Guardians past this rank
# dispatch as non-guardians instead. Inert while MAX_BUILDERS is 10: guardians at a
# crew of 10 are ranks 1-4, so the cap first binds at builder 11. Kept as a safety
# net for raising the crew ceiling later. Rank is derived from my_index alone
# (builder.guardianRank), so role stays a pure function of the index: deterministic,
# permanent per unit, no coordination, no oscillation.
MAX_GUARDIANS: int | None = 2

# --- Wave attack (≤0 / None = unset; positive = active gate) ---
WAVE_ATTACK_TURN: int | None = -1  # e.g. 300 → all builders harass from that turn
WAVE_ATTACK_TI: int | None = 1000  # e.g. 2000 → all harass once bank ≥ this

# --- Default early defend (age after birth_round; ≤0 disables) ---
# Only applies inside runDefault — harassers/guardians never see this.
BUILDER_HOME_TURNS = 0

# --- Defensive sentinel (False disables) ---
# An enemy sentinel reaches sqrt(32) ≈ 5.7 and shells the core from outside the
# Chebyshev-4 leash, where a gunner (reach sqrt(13) ≈ 3.6) cannot answer it
# without leaving home. Our own sentinel has the same 5.7, so a pad *inside* the
# leash covers the gap — the guardian never breaks the leash. Detection reads the
# HiveMind ledger, not just own vision (builder vision is only sqrt(20) ≈ 4.5).
DEF_SENTINEL = True
TURRET_THREAT_CHEBYSHEV = 6  # enemy turret this close to core counts as a threat
TURRET_MEMORY_TURNS = 20  # how stale a ledger sighting may be and still count

# --- Guardian idle economy (False disables) ---
# While no enemy unit is in vision, guardians build harvesters on ore inside the
# leash and finish/repair belt relays back to core instead of only patrolling.
GUARDIAN_ECONOMY = True
# Extra Chebyshev slack on the guardian leash while it is committed to a node or
# relay — an ore at the leash edge is worked from an adjacent tile one step out.
# A delta, not an absolute: it rides on top of whatever the current leash is.
GUARDIAN_ECON_SLACK = 1

# --- Dynamic guardian leash ---
# How far a guardian may roam from the core. WIDE while home is quiet, so it can
# reach enemy barriers cutting our belts and repair relays further out;
# TIGHT the moment the *core* sees an enemy turret or builder, which is the only
# way a roaming guardian learns home is being probed (a builder's ledger is
# personal-vision only, and its own vision is ~4.5 against the core's ~6).
# The core writes CORE_THREAT_BIT; guardians read it via logistics_helpers.guardianLeash.
GUARDIAN_LEASH_WIDE = 8
GUARDIAN_LEASH_TIGHT = 4
# Stay tight this long after the last sighting, so an enemy skirting the edge of
# the core's vision can't make guardians oscillate between the two radii.
# Mirrors TURRET_MEMORY_TURNS's shape and value.
GUARDIAN_LEASH_HOLD_TURNS = 20

# --- Opportunistic nearby-belt finish (≤0 disables) ---
# Chebyshev radius, anchored on the acting bot's own tile (not the core), that
# finishNearbyBelt searches for a dangling ally belt end to patch. Deliberately
# small and self-anchored, unlike reconnectBelts' core-anchored
# DEFEND_HOME_CHEBYSHEV: this is a cheap "I'm right here" check for a relay tail
# abandoned mid-build (most commonly its builder died with no on-death hook to
# report it), not a search that should pull a builder off its own task.
DANGLING_BELT_SELF_CHEBYSHEV = 3

# --- Harvester line verification (≤0 disables) ---
# How long a builder waits between verification passes, and how long one of its
# own harvesters stays "recently verified" before it is eligible again.
# logistics_helpers.verifyHomeHarvester re-walks a harvester's belt line with
# bot.relayVerify set, which forces vision-only proof — the only way to notice a
# belt destroyed out of sight, since a ghost entry in local_map otherwise reads
# as still-built for RELAY_LEDGER_TRUST_TURNS(30). Deliberately longer than that
# window (so a pass can't be fooled by the very staleness it exists to catch)
# but short enough that a cut line is found within a few dozen turns. Raise it
# if verification trips start displacing real economy.
HARVESTER_VERIFY_INTERVAL_TURNS = 40

# --- Guardian: build on ore uncovered by clearBarriers ---
# Consecutive Ti-starved turns a guardian holds position on an ore tile it just
# cleared a barrier off of before giving up on it. Waits rather than abandoning
# outright — the shared bank is usually just temporarily short, not permanently
# — but caps it so a guardian can't camp one tile forever if Ti never arrives;
# once it gives up, clearBarriers is free to move it on to the next barrier.
GUARDIAN_ORE_WAIT_TURNS = 3

# --- Opportunistic nearby-harvester relay (≤0 disables) ---
# Chebyshev radius, anchored on the acting bot's own tile, that
# finishNearbyHarvester searches for an un-relayed harvester (ally or, if
# STEAL_ENEMY_HARVESTERS, enemy) to start relaying. Same "cheap I'm-right-here
# check, not a search" shape as DANGLING_BELT_SELF_CHEBYSHEV, kept as its own
# knob since the false-positive risk profile differs (this one can commit a
# builder to a brand-new multi-turn relay, not just patch one tile).
NEARBY_HARVESTER_SELF_CHEBYSHEV = 3
# --- Guardian patrol offset ---
# How far patrolHome's mid-side waypoints sit from the core footprint. Was a
# fixed 2 (defence.py's old _PATROL_OFFSET). Raised, but deliberately to 3, one
# short of DEFEND_HOME_CHEBYSHEV(4), not to 4 itself: _guardianLeashOk grants
# no slack while merely patrolling (the GUARDIAN_ECON_SLACK widening only
# applies while inRelay/oreSite is set), so a waypoint sitting exactly on the
# leash boundary would leave zero margin — any single A*-forced detour around
# an obstacle between waypoints puts the guardian at Chebyshev 5, trips
# moveTowardLeash, and yanks it home mid-patrol. That is the same oscillation
# class this pass exists to fix, so the offset stays one tile inside the wall
# rather than touching it. A guardian's own vision (~Chebyshev 4.5) from a
# waypoint out here still reaches meaningfully closer to
# TURRET_THREAT_CHEBYSHEV(6) than the old offset ever did — a sentinel parked
# right at that boundary was observed going completely undetected (its
# distance² from the core was one over the core's own vision r²) for the
# entire back half of a match, with no ally unit ever patrolling far enough
# out to spot it and let defSentinel answer.
#
# Deliberately NOT raised alongside GUARDIAN_LEASH_WIDE(8). Patrol is idle
# behaviour; the chores (clearBarriers / reconnectBelts) are what need the
# roam. Left at 3 it now sits five tiles inside the wide leash instead of one,
# i.e. strictly more margin than before, and it avoids pushing waypoints out
# to 7 where they are far likelier to be walled and fall through to the corner
# fallbacks. Raising it is a separate, independently-testable change.
GUARDIAN_PATROL_OFFSET = 3

# --- Relay merge (False disables) ---
# pickRelayPath's only goal used to be core-adjacency — a builder relaying a
# harvester near an already-connected ally line would happily build/walk a
# full independent line the rest of the way home rather than joining the
# existing one a few tiles away, because relayPassable already treats an
# existing belt as passable at the same cost as empty ground (no incentive to
# prefer it, only crosses one when it happens to be on the shortest route).
# With this on, the relay's goal is "core-adjacent OR cardinally adjacent to
# an ally CONVEYOR PROVABLY already connected to the core" (logistics.
# _relayReachesCore — a bounded forward-walk following each tile's own stored
# facing, CONVEYOR only, never SPLITTER, since a splitter rotates its output
# among three directions and a sighting can't say which one the chain
# actually takes; not "any ally belt" either — an unconditional accept risks
# landing on a dead/orphaned stub or a sibling's not-yet-committed head,
# indistinguishable from a live line at the tile level alone). The final
# tile's needed facing is likewise never recomputed if it's already a
# provably-connected conveyor in its own right — see neededBeltFacing.
RELAY_MERGE_ENABLED = True
# Hop budget for _relayReachesCore's forward-walk. Also bounds worst-case
# per-turn cost, since the merge goal is evaluated on every node A* expands —
# capped low enough to stay well inside the 10ms/turn CPU budget even against
# a long belt line, generous enough to span a normal relay's length.
RELAY_MERGE_MAX_HOPS = 10

# --- Relay merge anchor heuristic (≤0 / None disables) ---
# Chebyshev radius around the relay's origin that logistics._mergeAnchorCandidates
# scans for already-built, provably core-connected ally CONVEYOR tiles to feed
# pickRelayPath's search heuristic. Without this, the heuristic is plain
# manhattan(p, core_pos), which *overestimates* true remaining cost whenever a
# nearer merge target exists (is_goal accepts that closer point too) —
# inadmissible, and it silently starves A* of any pull toward a nearby line:
# the search keeps preferring nodes that look closer to the raw core position
# even when walking toward a provably-connected line a few tiles away would
# resolve the search far cheaper. Observed directly: a relay planned near an
# already-built, provably-connected line ~7 tiles away instead wandered 130+
# tiles around a wall maze before reaching any goal at all. A heuristic aid
# only — the real proof is still _relayReachesCore, run here per candidate the
# same way is_goal runs it per node — so this only needs to be cheap and
# roughly useful, not exhaustive. 20 comfortably covers the observed miss (7
# tiles) with room to spare without scanning the whole map's local_map.
RELAY_MERGE_ANCHOR_CHEBYSHEV = 20

# --- Relay path length cap (≤0 / None disables — currently disabled) ---
# pickRelayPath can reject a found path (treats it as "no route", same as A*
# finding nothing) if it's longer than straight_line_distance * RATIO + SLACK,
# where straight_line_distance is the shorter of manhattan-to-core and
# manhattan-to-nearest-merge-anchor. Intended as a backstop against a
# pathological route that satisfies is_goal but is still far too long to
# actually walk. Disabled by default (RELAY_MAX_PATH_RATIO = None) after
# direct measurement showed straight-line manhattan distance is not a
# reliable proxy for "how long a legitimate path should be" on a wall-heavy
# map: on `heart`, two perfectly ordinary domestic relays (one 13 tiles from
# the core in a straight line, one whose nearest merge anchor was 4 tiles
# away by manhattan) needed real, A*-verified paths of 62 and 77 tiles
# respectively to actually walk around the walls — both got wrongly rejected
# at a 3x+15 cap, silently abandoning two already-built harvesters (20 Ti
# each) while the builder wandered off to build more instead of retrying
# them. The values below are what was being tested when that was found —
# kept as a starting point, not a tuned setting, if this is revisited with a
# passability-aware distance estimate instead of raw manhattan. The
# _mergeAnchorCandidates heuristic fix (this constant's neighbor,
# RELAY_MERGE_ANCHOR_CHEBYSHEV) is what actually fixes the original
# pathological-wander bug report — it corrects A*'s search order rather than
# rejecting results after the fact, so it doesn't share this failure mode.
RELAY_MAX_PATH_RATIO = None
RELAY_MAX_PATH_SLACK = 15

# --- Steal enemy harvesters (False disables) ---
# A default's ore-scan (pickMaintainOre / _pickRecheckOre / checkOre) normally
# only ever relays a harvester it owns. With this on, an enemy-owned harvester
# is treated exactly like one we're maintaining, minus the build step (it
# already exists) — just relay it to our own core. The engine lets a conveyor
# pull output off an opposing team's harvester without ever changing the
# harvester's own team (see AGENT.md: "Resources can still be pushed onto an
# opposing team's conveyor network or core"), so this captures its titanium
# for the cost of belts alone. There is no store slot for "this harvester is
# claimed" — all 16 are already spoken for — so the claim is derived instead:
# geometry.harvesterClaimedByAlly reads True the moment our own belt lands
# next to it, which is exactly what relayToCore does the instant a default
# commits. That derived signal is also what offense.py's snipeHarvester/offGun
# read to stop shooting a harvester we're actively stealing, and what
# denyHarvesterSupply reads to go sever the enemy's own competing belt on it
# instead. This one knob gates every consumer — logistics_helpers._harvesterOwnerAllowed
# for the picking/relaying side, offense.denyHarvesterSupply for the supply cut.
STEAL_ENEMY_HARVESTERS = True

# --- Steal-harvester distance cap (≤0 / None disables the bound) ---
# Chebyshev radius from the *ally* core beyond which an enemy harvester is not
# offered as a steal target, checked in the same _harvesterOwnerAllowed choke
# point as STEAL_ENEMY_HARVESTERS itself. Before the HiveMind ledger became
# team-shared, a builder could only ever discover an enemy harvester it had
# personally scouted, which implicitly kept steal targets close to wherever
# that builder actually was. Now every builder's local_map includes every
# teammate's sightings team-wide, so without this a builder near our own core
# can commit to a multi-turn relay toward a harvester clear across the map
# that only a scout ever personally saw — observed directly: team-0 conveyors
# landing next to enemy harvesters up to Chebyshev 19 from the ally core on a
# 28-wide map, far past anywhere team 0 actually operates. Measured from the
# ally core, not the enemy core, and not a midline comparison like
# offense._oreCloserToAlly (that answers "is this ore on our side," a
# different question — enemy harvesters are almost always on their side by
# construction) — this is "how far will we actually go for this," the same
# shape as HARASS_LEASH_CHEBYSHEV/DEFEND_HOME_CHEBYSHEV. 12 is a first guess,
# not a tuned value — worth adjusting after a few multiseed_eval.py passes,
# since it directly trades economic reach against exposure.
STEAL_HARVESTER_MAX_CHEBYSHEV = 12

# --- Spread zoning (logistics_helpers.spreadZone) ---
# Ported from bandit, which uses this as its sole anti-clumping mechanism in
# place of baron's role split. `my_index` maps to one of SPREAD_ANGLE_BUCKETS
# wedges around the ally core (bucket 0 centred on the core->enemy-core axis)
# and one of SPREAD_DISTANCE_BANDS radial bands (0 = our half, 1 = push toward
# the enemy core). Replaces the old ORE_STAGGER flag/_oreStaggerKey, which only
# covered default-role builders, could collide multiple builders on the same
# slot, and carried an unfixed mirrored-map tiebreak bug.
#
# Soft penalty: wrong wedge or band costs an ore candidate points in the same
# weighted blend as ORE_PICK_SELF/CORE_WEIGHT below, never disqualifies it — a
# zone with no ore should drift its builder into a neighbour's leftovers, not
# starve it. Raise to spread harder, lower to prioritise convenience.
SPREAD_ANGLE_BUCKETS = 4
SPREAD_DISTANCE_BANDS = 2
SPREAD_ANGLE_PENALTY = 6
SPREAD_BAND_PENALTY = 4

# --- Ore-pick priority blend (self-distance vs core-distance) ---
# _oreConvenienceKey blends these instead of a strict core-first lexicographic
# sort, which let core-distance veto a much closer pick outright and slowed
# down how fast a fresh harvester actually starts producing.
ORE_PICK_SELF_WEIGHT = 2
ORE_PICK_CORE_WEIGHT = 1

# --- Ore-block ledger memory (≤0 disables the age bound) ---
# How stale a remembered enemy blocker (most commonly a denyOre barrier) on an
# ore tile may be and still disqualify it in pickEmptyOre/_pickRecheckOre.
# Without this the rejection is permanent: nothing ever expires entity memory in
# a builder's own local_map short of regaining vision of that exact tile, and
# the rejection is exactly what stops a builder from ever walking back to look
# — self-reinforcing, forever. Mirrors TURRET_MEMORY_TURNS's role for
# threateningTurret: an old sighting is still useful signal, just not forever.
ORE_BLOCK_MEMORY_TURNS = 30

# --- Relay ledger trust (≤0 disables the fallback outright) ---
# beltFacingOk/extendRelayWatermark normally require live ct.is_in_vision to
# confirm a belt tile's facing. A relay longer than one unit's own vision
# (sqrt(20)) can never be verified "already fully built" from a single vantage
# point without this: a builder parked near one end of its own already-complete
# corridor, with the other end out of sight, would otherwise keep re-walking
# the whole route on every relay re-entry — pure movement, no action, forever
# — and since bot.inRelay stays True the entire time, it locks the unit out of
# defendHome/defGun/defSentinel/blockGunLine (all `if bot.inRelay: return
# False`). A stale sighting is worse than no sighting though — the segment may
# have been destroyed since — so this bounds how old a ledger belt entry may
# be and still count; older falls through to "unknown," same as before this
# knob existed. Mirrors ORE_BLOCK_MEMORY_TURNS's shape, own dial: a wrongly
# trusted belt entry can silently mark a broken relay complete, a materially
# worse failure than misjudging one ore tile.
RELAY_LEDGER_TRUST_TURNS = 30

# --- Relay stuck breaker ---
# Consecutive failed attempts (blocked-obstacle clear, in-place placement,
# move-toward) relayToCore tolerates at one blocking point before giving up on
# it — replan around it, abort the relay, or report-and-abort, depending on
# which of the five call sites hit it. Previously a bare `3` inlined
# separately at each of those five sites with no shared knob at all; named
# here so it's one dial instead of five copies of the same number.
RELAY_STUCK_TURNS = 3

# --- Ore deadlock breaker (≤0 disables) ---
# Turns a default builder may sit motionless outside a relay before it drops its
# ore claim, blacklists the node and re-picks. Guards against builders piling up
# on one node and never yielding it.
ORE_DEADLOCK_TURNS = 8

# Turns a harasser may sit motionless before it blacklists its sticky belt/harvest
# target and re-picks. Mirrors ORE_DEADLOCK_TURNS for the offence side: denyBelt's
# Mode B (cut the belt) is gated off whenever the bank can afford a gun, so a funded
# harasser correctly decides it cannot profitably touch a belt — and then had no way
# to give up on it. ≤0 disables.
HARASS_DEADLOCK_TURNS = 8

# --- Harasser leash (≤0 disables) ---
# Chebyshev radius around the *enemy* core a harasser may roam, mirroring
# GUARDIAN_LEASH_WIDE's shape but anchored on the opposite core and fixed
# (no threat-based tight/wide toggle — a harasser has no "home" to sense
# threats against). Keeps snipeHarvester/offGun/denyOre/denyBelt/seekHarvester
# from reaching far-out enemy harvesters that a steal-relay could claim alive
# instead of the harasser destroying them.
HARASS_LEASH_CHEBYSHEV = 4

# --- Kill-core commit (≤0 / None disables the override) ---
# Once a harasser lands a sentinel directly on the enemy core (offense.
# killCore), it keeps cutting the core's own belts for this many rounds even
# if the bank recovers enough to afford another gun — without this, offGun's
# gun-place branch would reclaim the turn from the belt-cut the instant
# _canAffordGunPlace flips back to True, abandoning a cut mid-fire. Belt
# candidates are restricted to tiles directly touching the core's own
# footprint (geometry.coreFootprint + isAdjacent), not a chebyshev radius —
# DISCONNECT_CORE_CHEBYSHEV is removed since "near the core" is no longer
# the criterion, only "touching" is.
KILL_CORE_COMMIT_TURNS = 25

# --- Blocked-route breaker (False disables) ---
# When A* proves the ore claim unreachable, replan with enemy barriers passable to
# find which one seals the lane, and answer it with a gunner: 3 shots / 6 Ti and ~3
# turns, against a builder's 15 turns and 30 Ti. The gunner then stays and kills
# re-walls. No barrier on the route (terrain or ally buildings) → the node is
# blacklisted instead. Known case: the walled-off centre ore on atoll.
OPEN_BLOCKED_ROUTE = True

# --- Harasser belt-cut throttle (≤0 disables) ---
# Rounds a harasser waits between *starting* belt cuts. A cut is 20 HP at 2 dmg per
# attack: 10 shots, 20 Ti, 10 turns — worth it when it completes (they pay 9-33 Ti to
# reopen and the downstream line delivers nothing meanwhile), pure waste when it does
# not. Only starts are throttled; a cut already underway always runs to the barrier.
DENY_CUT_COOLDOWN = 50

# --- Harasser forward-barrier throttle (≤0 disables) ---
# Rounds a harasser waits between *placing* a new Mode A forward barrier
# (_tryForwardBarrier). Unlike DENY_CUT_COOLDOWN (which only gates starting a
# belt cut), Mode A barrier placement was completely unthrottled — a fresh
# barrier could land every single turn. A barrier is ally-owned but still
# blocks relayPassable/_RELAY_BLOCK_TYPES like an enemy one (pathing.py:20-27),
# so an unthrottled harasser can repeatedly wall off a live relay corridor.
DENY_BELT_COOLDOWN = 10

# --- Undefended belt cut (≤0 disables the extra allowance) ---
# A Mode B cut normally starts only while a gun is unaffordable — a gun beats a
# cut in value whenever we can field one. But a belt with no enemy builder
# within this Chebyshev radius has nobody able to heal it back (4 HP/turn for
# 1 Ti against our 2 dmg for 2 Ti) or rebuild it once it's gone: the denial is
# effectively permanent, so it's worth taking regardless of what else the bank
# could buy. Read by offense._worthwhileCutTarget. Live vision only, not the
# HiveMind ledger — a stale sighting could mark an abandoned belt "defended"
# forever, or miss a builder that has since arrived; worst case on a wrong read
# either way is falling back to the existing gun-affordability gate, not a
# wasted cut. ≤0 falls back to that gate alone (today's behavior).
UNDEFENDED_BELT_CHEBYSHEV = 5

# --- Harasser ore denial (≤0 / None disables) ---
# Barrier empty ore in the enemy's expansion ring: 3 Ti makes the tile unbuildable and
# prevents a 20 Ti harvester plus its whole belt line; they pay 6-30 Ti to clear it.
# Two guards keep it off our own side. _oreCloserToAlly draws the midline, and the
# floor below covers what the midline cannot: on a small map an ore can be marginally
# nearer their core yet sit well inside our working range. Chosen from the map tables,
# not guessed — across all 21 known maps at radius 6, floors 0-6 barely bite (64->61
# candidate nodes) and leave atoll's contested centre deposit (9,8) exposed; 8 drops it
# to 52 and correctly silences the feature on sprint (10x10) and sweden, where the two
# cores are close enough that "near theirs" is "near ours". 10 over-corrects, killing 5.
DENY_ORE_CHEBYSHEV = 6
DENY_ORE_MIN_ALLY_CHEBYSHEV = 8

# --- Frontier scouting (≤0 / None disables) ---
# Last-resort exploration when the normal chains have no target. Needed because a
# missed map fingerprint leaves map_id 0, so ORE_LOCATIONS is empty and enemyCorePos is
# never set — and nothing in baron explores. On heart/sweden/vase the core sees no ore
# in its own vision at turn 0, so defaults had no ore claim and no way to find one, and
# the economy never started at all. Inert on maps we did fingerprint, because the static
# tables supply targets before this step is reached. Value is the re-pick interval: the
# ledger scan is ~900 cells against a 10ms budget, so raise it if turns get truncated.
FRONTIER_REFRESH_TURNS = 20

# --- Encirclement breaker (≤0 disables) ---
# Turns a builder may sit motionless with no legal move in any direction before
# it destroys/fires at an adjacent building to open a door. Catches a harasser
# that fenced itself in with its own denyBelt barriers.
ENCIRCLED_TURNS = 8

# --- Oscillation window (bot.stuck; ≤1 disables the extra check) ---
# `bot.stuck` used to count only exact standstill (identical tile turn over turn),
# which never trips while a unit bounces between two or more tiles — e.g. denyOre
# pulling toward an unreachable ore while avoidFire's dodge memory pushes back the
# next turn. That left every stuck-counter consumer (breakOreDeadlock,
# breakBeltDeadlock, breakEncirclement) dead code against a harasser oscillating in
# place: it never accumulates ENCIRCLED_TURNS/ORE_DEADLOCK_TURNS/HARASS_DEADLOCK_TURNS
# and, worse, never falls through to yield the turn to whatever comes after it in the
# role chain (see denyBelt never getting a turn behind a looping denyOre). A unit
# confined to OSCILLATION_DISTINCT_LIMIT or fewer distinct tiles across the last
# OSCILLATION_WINDOW turns now also counts as stuck. Was 2 (catches a straight
# A-B ping-pong); raised to 3 after a guardian was observed bouncing across
# three distinct tiles ((15,9)/(16,9)/(17,9), near its own core) for 100+ turns
# without ever tripping this — 2 was one tile too tight for that shape of loop,
# and every downstream consumer (patrolHome's waypoint-advance escape,
# breakOreDeadlock, breakEncirclement) inherits the same blind spot.
OSCILLATION_WINDOW = 6
OSCILLATION_DISTINCT_LIMIT = 3

# --- Path-search budget (≤0 / None disables) ---
# Hard cap on full A* searches (`astarFromSeeds`, PATH_MAX_ITERS=800 each) one bot
# may run in a single turn. Without this, a harasser whose role chain falls through
# several move-toward steps in one turn (denyOre → seekHarvester's per-candidate
# loop → seekCore's orbit fallback) can trigger 5-15 full searches in that one call,
# which is the dominant cost behind an "Exec Time TLE" turn — one failed search
# already burns its full iteration budget (no early exit, unlike a hit), and nothing
# upstream bounded how many of those a single turn could stack. Once the budget is
# spent, `moveToward` falls back to a single direct cardinal step instead of
# searching, same as its existing no-path fallback.
MAX_PATH_SEARCHES_PER_TURN = 3

# --- Gun placement (≤0 disables) ---
GUN_PLACE_MIN_TI = 100 # need bank ≥ this to place gunner/sentinel

# --- Gunner idle self-destruct (≤0 disables) ---
# Consecutive turns a gunner may go without firing before it self_destructs.
# A gunner that never gets a shot is a dead 60+ Ti sunk on a tile with nothing
# to answer — usually the front it was covering shifted or the enemy stopped
# probing there. self_destruct deals no explosion damage, so this is purely
# about freeing the unit cap / bank, not a weapon.
GUNNER_IDLE_TURNS = 15

# --- React-freeze cap (≤0 / None disables) ---
# _reactEnemyTurret's skipEconomy branch consumes a default builder's whole turn,
# every turn, for as long as an enemy turret stays in vision and nothing about it
# is actionable (most commonly can't afford GUN_PLACE_MIN_TI yet) — with no cap,
# observed freezing a builder in place for 100+ turns next to a turret it was
# never going to answer anyway. Past this many *consecutive* frozen turns, stop
# freezing and let the chain fall through to economy instead: buildHarvester /
# checkOre / relayToCore already carry their own per-tile threat checks
# (tileThreatenedByGunner), so this only removes the blanket "turret merely
# visible" freeze, not tile-level danger avoidance.
REACT_FREEZE_CAP_TURNS = 15

# --- Core push gate (≤0 / None disables) ---
# Harvesters are the easier, cheaper kill — denying one starves a whole belt line for
# 3-20 Ti. Shooting the enemy core itself only pays off once the bank shows a real
# economic lead; below this the harasser should keep working harvesters/ore instead of
# tickling the core for free damage it can't press home.
CORE_PUSH_MIN_TI = 150  # need bank ≥ this before offGun targets the enemy core

# --- Ammo policy (guns.topUpAmmo reads these) ---
AMMO_BASE = 12
AMMO_PER_GUNNER = 3
AMMO_PER_SENTINEL = 10
AMMO_CONVERT_CHUNK = 10
AMMO_CONVERT_MIN_TI = 30  # need bank ≥ this to convert; ≤0 disables
AMMO_MIN_POOL = 20
DEFAULT_AMMO_START_TURN = 1
RAIDER_AMMO_START_TURN = 30
MAX_AMMO_POOL: int | None = None

# --- Store slots (Player 0–3 only; 4–15 HiveMind-owned) ---
SLOT_RELAY_COMPLETE = 0
SLOT_ORPHAN_1 = 1
SLOT_ORPHAN_2 = 2
SLOT_ORPHAN_3 = 3
SLOT_PACKED_CORES = 15

# Slot 0 is bit-packed: no store slot is free (all 16 are allocated), but slot 0
# only ever used 1 of its 32 bits. Bit 0 = a relay has completed (written by
# builders); bit 1 = the core currently sees an enemy turret (gunner/sentinel/
# launcher — a bare builder doesn't count; written only by the core).
# Every reader must mask — see relayEverComplete / coreThreatened in logistics.
RELAY_COMPLETE_BIT = 1
CORE_THREAT_BIT = 2

# --- Orphan relay registry (≤0 disables reporting/pickup) ---
# How stale an orphan-registry entry (bots/baron/logistics.py) may be before a
# reader treats it as unreadable and evicts it on next write. No claim/heartbeat
# mechanism exists (see docs/adr or CONTEXT.md's RelayToCore section for why),
# so this is the only thing that ever frees a slot whose harvester died or got
# reconnected some way the explicit clear-on-success hook didn't see. Mirrors
# ORE_RECHECK_CLEAR_INTERVAL (logistics_helpers.py) rather than inventing a new figure.
ORPHAN_REGISTRY_TTL_TURNS = 50
