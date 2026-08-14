# observations.md

Facts about the game and this bot's design. Things that were expensive to learn
and would be expensive to relearn.

---

## Engine mechanics

- Conveyors accept from 3 sides and output the 4th. Splitters accept **only**
  from the back. Grafting onto a belt therefore means not feeding its output
  face, and grafting onto a splitter is legal only on its back tile.
- **A belt can be built under a builder bot**, ally or enemy — a bot is not a
  building, so the tile still counts as empty. Only the builder's *own* tile is
  forbidden. This does **not** generalise to other structures. Treating an
  occupied tile as an obstruction made builders blacklist it and re-path across a
  neighbour's relay, which is what started the belt war on yulerune.
- Builder bots move **cardinal-only**. `move(<diagonal>)` raises.
- Build, heal, attack and destroy all require an **orthogonally adjacent** tile
  — never diagonal, never the builder's own tile. This is why belt-laying needs
  the build-ahead-then-backfill rule.
- Acting and moving are mutually exclusive per turn for a builder.
- `destroy()` on an allied building is free, unlimited per turn, no cooldown.
- Builder attack is 2 Ti → 2 damage, one per turn. Clearing an enemy conveyor
  (20 HP) is 10 builder-turns; a barrier or harvester (30 HP) is 15. That ratio,
  not intuition, is what sets the obstruction weights in
  `pathfindingHelpers.py`.
- An uncaught exception in `run()` **permanently destroys that unit** for the
  rest of the match. Timeouts only skip the turn. Always wrap.
- Passive income is 10 Ti every 4 rounds. A conveyor is 3 Ti.
- The core is a 2x2 footprint.
- **A harvester feeds any cardinally adjacent building regardless of team.** A
  belt beside an enemy harvester pulls its stack into our network without
  ownership ever changing — the node costs us belts alone. Confirmed working on
  fjordgate. Diagonal adjacency does **not** work.
- The repo-level `observations/combat_findings.md` says a builder may only target
  its own tile. That is **stale** — fcode 2.3.7 is the opposite: orthogonally
  adjacent, never its own tile. Check the installed stub, not that file.
- **`is_tile_passable` lies about the allied core.** Its docstring says a builder
  can stand on it; the engine refuses the move. Pricing the core as traversable
  made every route out of a cramped spawn plan straight through it, and five
  builders deadlocked against it for a whole match on midgard. The core is a hard
  block for both teams.

## Design

- **The network is the core.** A relay paths to the nearest tile of the
  existing connected network, not to the core itself. This grafts new lines
  onto the tree instead of running redundant lanes home (approximates a Steiner
  tree).
- **Tree invariant.** Every belt points at its parent; the root is the core. New
  tiles are only ever attached pointing *into* an already-connected member, so
  connectivity holds by induction and never needs re-proving. This is what
  replaces baron's five-function merge check.
- **Only walls and ally harvesters are truly impassable.** Everything else is
  priced by `tileCost`, so A* can decide that smashing through one enemy
  conveyor beats a 15-tile detour. Planning uses `isImpassable` + `tileCost`;
  the per-turn move uses `canStandNow`. Conflating the two forces a replan every
  time anything is in the way.
- **Hold, don't abandon.** A builder that can't act this turn usually can in two
  or three. `patience.holdTask` is the single place that judgement lives.
- **The relay goal is a tile *beside* the network, not on it.** The anchor the
  last belt points into is usually the core, which can be neither stood on nor
  built on, so a path that had to reach it could never be walked to the end.
- **Being broke is not being blocked.** Titanium shortage must never blacklist a
  tile or drop an ore claim — passive income is guaranteed, so waiting always
  beats tearing up a good plan. Getting this wrong made builders progressively
  blacklist their own route until no path home existed.
- **A target must be abandonable.** A builder that commits to a node it cannot
  reach will hold that target for the rest of the match — four of them sat on the
  same enemy harvester for 400+ turns. `ecoBot._skipOre` expires the claim so the
  builder moves on, and expires the skip so a transient blocker doesn't cost the
  node permanently.
- **`walkTo` reports arrival and failure both as False.** Callers must check
  "am I already there?" themselves, or the builder wanders off on the exact turn
  it arrives.
- **Filter at the point of choice, not at the point of gathering.** `knownOre`
  originally dropped every ore tile carrying a harvester — including enemy ones —
  so the picker never saw a hijack target and the whole feature was dead. Tiering
  hostile strictly below free had the same effect: it was never reached while any
  free ore was known. Hostile now competes on distance with a bonus.
- **A relay is anchored at its harvester, not at the builder.** `planRelay` seeds
  from the placeable tiles cardinally adjacent to `relayFrom`. Seeding from the
  builder's current position meant every mid-relay replan re-rooted the line
  wherever the builder was standing — on yulerune that left harvester (4,3) with
  no adjacent belt at all, its line starting two tiles away.
- **Never point a belt into an ally belt that already points back at it.** That
  is a closed 2-cycle reaching nothing. If the tile pointing back is our *anchor*,
  the network told us "connected" about something that isn't — route around
  rather than trust it. This is a local O(1) guard that holds even when two
  builders' `local_map`s disagree, which they genuinely can.
- **A blocked step must change something.** If the planner prices a tile as
  traversable and the engine then refuses the move, retrying the same step every
  turn deadlocks the unit for the match. `movement.stepToward` waits on builders
  (they move on their own) and blacklists anything else.
- **The distance metric is Manhattan, everywhere.** Builders move cardinal-only and
  belts chain cardinally, so both travel time and belt cost are 4-connected — the
  metric has to match the connectivity or the threshold built on it is anisotropic.
  Chebyshev and octile belong to engines with diagonal movement and have no place
  here: `_withinHijackRange` measured Chebyshev, which reads a node at (12, 12) as
  12 when it costs ~24 belts, so the leash admitted a diamond's worth of budget over
  a square's worth of area. The only squared-Euclidean quantities in the game are
  engine-side radii (vision r²=36, core action r²=8, spawn r²=2); never re-derive
  those, ask `is_in_vision` / `can_spawn` — see the `is_tile_passable` entry above
  for what trusting our own copy of an engine rule costs.

## Performance

A builder gets 10ms/turn, and the whole gauntlet shares one CPU budget. Three
things were the difference between 30x30 maps working and timing out:

- `readTile` memoised per round — the cost function reads every expanded node
  plus its four neighbours, so re-deriving from the Controller dominated.
- Chokepoint memoised for the whole match: it depends only on walls and map
  edges, and neither ever changes.
- Network and threat sets built once per unit-turn and shared by both cost
  profiles; the network flood is O(belts) via reversed edges, not repeated
  passes over the belt table.

Caches hang off the hivemind or Player instance, never module globals — units
share a subinterpreter and module state would leak between them.

## Known asymmetry

Two separate orientation bugs live here. Keep them apart — they were originally
recorded as one suspect, and only one of them turned out to matter.

**The core anchor is not mirror-symmetric (fixed, but it was not the cause).**
`get_position()` on a core returns its min-x/min-y tile, and under the map mirror
the two *footprints* map onto each other while the *anchors do not* — the mirror of
team A's NW anchor is team B's **SE** footprint tile. Every anchor-relative read is
therefore off by (+1,+1) for exactly one team. On antler: A anchor (6,4) covers rows
{4,5}, B anchor (6,12) covers {12,13}; reflection about y=8.5 maps 4→13 and 5→12, so
footprints match and anchors are off by one row in opposite directions. Fixed by
`chebyshevToCore` (point-to-box, so distance is to the nearest footprint tile) and
`coreRing` (all 12 surrounding tiles, not 8 compass offsets from the anchor — three
of those land inside the core's own body and the five that don't are all N/W).
**Measured effect on the mirror gauntlet: exactly zero.** `spawn_builder` returns on
the first success and both the old and new orderings start at (x, y-1), so the tile
actually chosen never changed. The fix is correct and it removes a real bias in the
candidate *set*; it just is not what the numbers were about.

**The fixed N/E/S/W ordering is the real driver (still open).** Reversing
`coreRing`'s output — nothing else — moved drakkarfjord from 21910/11900 to
16560/18450: the skew flipped sides and fell from 1.84:1 to 1.11:1. So the ordering
is worth ~10k titanium on a 30x30 point-mirrored map. antler stayed balanced under
both (14720/14680 → 14670/14730), which is why an axis-mirrored sample alone hides
this; always include a point-mirrored map when measuring symmetry.

The fix is to order against something that mirrors with the map instead of against
the compass — the core→enemy-core axis is the natural choice, and
`hivemind.enemy_core_pos` is already populated from `KNOWN_MAPS` on all 15 ladder
maps and currently read by nobody. Same treatment is owed to `_ORTHO`,
`pathing.CARDINAL_DELTAS`, the A*/BFS insertion-order tie-breaks, and the
first-match anchor pick in `relays.planRelay`.

Note the earlier figures in this section (antler 14500 vs 5040, saga 9350 vs 20550)
no longer reproduce — at the current HEAD both maps are near-even. Only the
point-mirrored map is still badly skewed.

## Traps

- **A successful action is not progress.** `layBelt` cleared the patience counter
  after every successful `build_conveyor`. In a destroy-rebuild war *every turn
  contains a successful build*, so the counter never accumulated and no give-up
  path ever fired — two builders looped for 975 turns and one tile was rebuilt 42
  times. Any counter meant to detect "stuck" must not be reset by work that is
  itself the symptom. `relayRebuilds` counts re-lays of the same tile for exactly
  this reason.
- **A destroyed belt lives forever in `local_map`.** `hivemind._ingest_ledger`
  writes buildings with no expiry, and the GC sweep only clears stale ally
  *builder* ghosts. Any unit not currently looking at a tile still believes the
  belt it saw there is present, so `buildNetwork` can close a chain through a belt
  that no longer exists. Suspected cause of bad anchors; not yet confirmed.

## Known gaps

- No store-backed ore claim, so two builders can race the same node. Spread is
  currently deterministic off `hivemind.my_index` only.
- HiveMind slot 4 never decrements on builder death, so the metronome cycles
  through dead indices and wastes broadcast turns.
- No splitters are built. Ore lines are pure conveyor chains.
