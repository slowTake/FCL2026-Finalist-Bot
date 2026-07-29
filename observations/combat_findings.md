# Combat findings

Measured against fcode 2.2.0 with throwaway probe bots, not inferred from the spec.
Every number here came from a real game.

## Bot stdout is discarded by the engine

`print()` from inside a bot never reaches the terminal. Confirmed by running
`mechanics_explorer`, whose `log()` helper is a bare `print`, and grepping for its
`[TEST]` prefix: nothing appears, in `fcode run` or via `run_game`.

This means `bots/mechanics_explorer/main.py` has never actually reported anything,
and it is why this folder had no combat data.

**Use file logging instead.** This works from inside a bot:

```python
def log(msg):
    with open("/tmp/probe.log", "a") as f:
        f.write(msg + "\n")
```

Wrap probe logic in `try/except` and log the exception — an uncaught error in a
bot is otherwise silent, and the unit just does nothing for the rest of the game.

## A harvester feeds an adjacent turret directly, with no conveyor

Build a harvester on ore **cardinally** adjacent to a gunner and it delivers a
full stack immediately. The gunner had `ammo=10` one turn after being built.

The turret refuses input from the tile it faces, so the harvester must be on a
cardinal side other than the facing side.

### Diagonal feeding does NOT work

The spec only says a harvester "outputs one stack to an adjacent building", which
reads as though diagonals might count. They do not. Same probe, same map, same
harvester at `(3,8)`, gunner moved from `(4,8)` (cardinal) to `(4,7)` (diagonal):

```
t11 built GUNNER at 4,7 facing EAST
t25  ammo=0 target=CORE@7,7 hp=500 fired=False
t50  ammo=0 target=CORE@7,7 hp=500 fired=False
...  still ammo=0 at turn 1000, core HP never moved
```

The gunner acquires the core correctly and simply never receives ammo. This is a
silent failure: nothing errors, the turret just never fires. It is the same
failure mode `STRATEGY.md` blames for the starter bot's decorative gunners.

**Any siege placement must require cardinal adjacency between feeder and turret.**
Treating adjacency as chebyshev distance 1 produces plans that look valid and do
nothing.

## A single fed gunner kills a 500 HP core in about 50 turns

End-to-end probe on `sprint.map26` versus a passive opponent:

```
t10 built HARVESTER at 3,8
t11 built GUNNER at 4,8 facing EAST
t12 ammo=10 target=CORE@7,8 hp=500 fired=True
t13 ammo=8  target=CORE@7,8 hp=490 fired=True
...
50 shots, core destroyed at turn 62
```

Two things here contradict a natural reading of the spec:

- **The gunner fires every turn.** `GUNNER_FIRE_COOLDOWN = 1` means ready again
  next turn, not one turn of waiting between shots. So it is 10 damage per turn.
- **Ammo refills the moment it empties.** `ammo` cycles 10, 8, 6, 4, 2, then back
  to 10. The harvester's "one stack every 4 rounds" is not the limiting factor,
  so a single harvester sustains continuous fire from one gunner.

So the real cost of a core kill is 50 shots, 100 Ti of titanium as ammo, plus
20 Ti harvester and 10 Ti gunner.

## Firing geometry

A gunner at distance 3 from a core tile, cardinally aligned, acquires it via
`get_gunner_target()` and `can_fire()` returns True. Range is sqrt(13), so
distances 1, 2 and 3 along the facing line all work.

Confirmed by search over map data: on 14 of the 15 maps there is a tile that is
cardinally aligned within 3 tiles of the enemy core, has a clear line, and touches
an ore tile on a non-facing side. `quarry` is the exception.

## A builder can only attack the tile it stands on

`can_fire()` documents it plainly: "Builder bots may only target their own tile
and only damage the building on it." The entity table repeats it — a builder
"can build and heal in action radius, and attack own tile".

This kills any plan where a builder clears an enemy building from a neighbouring
tile, and it explains two separate dead ends:

- The siege raider reached the ore beside the enemy core (logged adjacent, at
  `best_d2=1`) and then sat there for 40 turns, because the ore already held an
  enemy harvester and `can_fire()` on that tile is never legal from beside it.
- `Player._try_contest_enemy_harvester` checks adjacency and then fires at the
  neighbouring tile, so that pre-existing path has never once worked.

A builder and a building can occupy the same tile — `get_tile_builder_bot_id` is
separate from `get_tile_building_id` — so standing *on* the enemy building and
firing at your own tile is the legal way to clear it. Untested so far.

## Standing on an enemy belt to destroy it does work — 10 shots

The untested idea above is confirmed. A probe that hunted enemy conveyors on
`vault`, stood on them and fired at its own tile logged 35 successful mounts and
**31 destroyed belts, almost all at exactly 10 shots**, matching the spec
arithmetic: a conveyor has 20 HP and an attack deals 2 damage for 2 Ti.

Two API details make it legal, and both are easy to misread:

- `is_tile_passable` says a bot may stand on a tile that "has a conveyor,
  splitter, or the allied core". It does *not* say *allied* conveyor, and indeed
  enemy belts are walkable.
- `can_destroy` is allied-only, so `destroy()` cannot remove an enemy building.
  Firing at your own tile is the only route.

The handful of kills needing 11-16 shots were tiles two builders were fighting
over. Every `can_fire` refusal in the probe was `ti=0` — an attack costs 2 Ti, and
the probe had no economy, so plan for titanium droughts stalling a clear.

Note this does not generalise to other enemy buildings: harvesters, turrets and
cores are not bot-passable, so a builder can never get on top of them.

## A defensive battery on contested ore works, and is usually idle

Same feeding mechanic aimed at midfield ore instead of the enemy core. On
`atoll` the raider reliably built a harvester on the centre deposit `(8,8)` and
a gunner at `(8,7)`, which stayed loaded at `ammo=10 TITANIUM` for the rest of
the game. Over 8 games it fired in only 2 of them, because `starter` seldom
walks the lane.

Aiming matters more than placement. Scoring a facing by "covers tiles closer to
the enemy core" tied NORTH with EAST and picked NORTH, whose line `(8,6)`,
`(8,5)`, `(8,4)` saw zero traffic all game. Scoring instead by "covers tiles
adjacent to the contested deposits we are *not* holding" picks EAST, whose line
covers `(9,7)` — the tile an enemy builder must stand on to claim ore `(9,8)` —
and that version landed 18 hits in one game.

Measured effect on win rate: nothing. 150 games per variant across 15 maps and
10 seeds gave 82.7% with the raider off and 82.0% with it on.

## `get_gunner_target()` returns your own units too

The gunner driver was firing blind:

```python
target = ct.get_gunner_target()
if target is not None and ct.can_fire(target):
    ct.fire(target)
```

`get_gunner_target()` returns the first *occupied* tile along the firing line,
with no regard for team. On `vault` a gunner at `(10,11)` facing south found our
own conveyor at `(10,12)` and destroyed it **634 times in one game** — roughly
1268 Ti of ammo spent shooting ourselves, and a relay belt that never stayed
intact. Watching a replay is what caught it; the win-rate aggregate did not.

Always confirm the target's team before firing. A builder bot on the tile takes
the damage in place of any building beneath it, so check
`get_tile_builder_bot_id` first, then `get_tile_building_id`.

This was latent, not new: `BUILD_GUNNERS = False` meant the bot had never
actually owned a gunner until the raider built one.

## Relay builders paced because the route was replanned every turn

Builders were seen walking back and forth for hundreds of turns. A per-turn trace
on `fjord` showed one builder alternating between two entirely different routes:

```
t46 pos=(8,10) tile=(9,11) want=EAST  path=[(9,10),(9,11),(10,11),(10,12),...]
t47 pos=(8,11) tile=(8,9)  want=WEST  path=[(8,9),(7,9),(7,8),(6,8),...]
```

`plan_conveyor_path` seeds from the relay origin's *layable* neighbours, and the
origin is a harvester, so the seed set depends on what is currently in vision.
Moving one tile changed vision, which changed the seed, which produced a route
leaving in the opposite direction. East was blocked, so `_direction_candidates`
fell through to its shuffled all-directions list and stepped south; from there the
other route pointed north. Both moves "succeeded", so `relay_stuck` sat at 0 and
nothing ever aborted.

Worth knowing for future stuck-detection work: measuring progress as "did I move"
cannot see oscillation, and measuring distance to the *current* target cannot
either when the target itself alternates. Neither can a per-target counter, since
it resets each time the target flips.

The paced builders cost no measured titanium — mined totals were identical with
and without the pacing, because the chains involved never completed in either
case. It wastes one or two builders out of six to eight, and looks broken in a
replay, but it is not an economic leak.

## Per-map win rates need far more than 10 seeds

The bots call `random` unseeded, so a map cell of 10 games swings by about ±3
wins on its own. Two runs of the identical variants and seeds gave `atoll`
8/10 then 10/10 for the same build, and 9/10 then 5/10 for the other. A
"regression" of 4/10 down to 1/10 on one map is inside that noise. Only the
aggregate over all 15 maps was stable between runs.

## Caveats worth remembering

- The probe won because the opponent was passive. Against `starter` on `sprint`
  the same probe never got its harvester built: starter claimed the target ore
  first, so `can_build_harvester` returned False forever, and starter destroyed
  our core by turn 87. A siege needs a fallback when the planned ore is taken.
- Buildings and builder bots both block and are targetable on the firing line, so
  anything wandering in front of the gunner absorbs shots meant for the core.
- If a tile holds both a building and a builder bot, only the bot takes damage.
