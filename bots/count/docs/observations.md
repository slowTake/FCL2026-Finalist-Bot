# observations.md

Facts about the game and this bot's design. Things that were expensive to learn
and would be expensive to relearn.

---

## Engine mechanics

- Conveyors accept from 3 sides and output the 4th. Splitters accept **only**
  from the back. Grafting onto a belt therefore means not feeding its output
  face, and grafting onto a splitter is legal only on its back tile.
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
- **A blocked step must change something.** If the planner prices a tile as
  traversable and the engine then refuses the move, retrying the same step every
  turn deadlocks the unit for the match. `movement.stepToward` waits on builders
  (they move on their own) and blacklists anything else.

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

Mirror matches are crash-clean but not balanced: antler 14500 vs 5040, saga
9350 vs 20550 on symmetric maps. Something is orientation-dependent — the fixed
N/E/S/W tie-break order in `_ORTHO` and `_SPAWN_DIRS` is the first suspect, since
a reflected map turns a fixed direction preference into a real advantage for one
side. Not yet investigated.

## Known gaps

- No store-backed ore claim, so two builders can race the same node. Spread is
  currently deterministic off `hivemind.my_index` only.
- HiveMind slot 4 never decrements on builder death, so the metronome cycles
  through dead indices and wastes broadcast turns.
- No splitters are built. Ore lines are pure conveyor chains.
