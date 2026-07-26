# Strategy: Winning Florent Code League Matches

Synthesized from the [official tutorials](https://game.code.florent.vc/tutorials/), the in-package `spec.md`, and the gaps the starter bot itself calls out.

## Win conditions (what “winning” actually means)

Primary objective: **destroy the enemy Core** (500 HP).

If both Cores survive to round 1000, tiebreakers apply in order:

1. Titanium **delivered** to the Core (mined & routed)
2. Harvesters currently alive
3. Titanium currently stored
4. Coinflip

So a bot that never fights can still win on economy — but a bot that never mines loses the most important tiebreakers. Ideal play does both: snowball economy early, then convert surplus into a Core kill (or a decisive mined lead).

---

## North-star game plan

```
Explore → Claim ore → Route income → Expand → Arm → Push / Sabotage
```

Every Builder Bot should be in one explicit role (via the Store), not all running the same “do everything” brain. The tutorials’ final bot is a working demo; a ladder bot needs role assignment, complete logistics, and real ammo.

### Phase timeline (≈1000-round match)

| Phase | Rounds (soft) | Goal |
| --- | --- | --- |
| Opening | 1–80 | Spawn a small builder force, scout nearest ore, place first Harvester + full chain to Core |
| Mid | 80–400 | Grow to ~4–8 productive Harvesters, Splitters at Core, ammo + perimeter Gunners |
| Late | 400–1000 | Sentinels on chokepoints, sabotage enemy conveyors, or push for Core kill; keep mining for tiebreak |

Do **not** spam Builder Bots forever. Each one adds **+20%** to cost scale. Cheap, high-leverage builds first.

---

## Build order principles (from cost scaling)

Scale starts at 100% and only rises when you build (and falls when things die):

| Entity | Base cost | Scale bump |
| --- | --- | --- |
| Conveyor | 3 | +1% |
| Barrier | 3 | +1% |
| Splitter | 6 | +1% |
| Gunner | 10 | +10% |
| Harvester | 20 | +5% |
| Launcher | 20 | +10% |
| Builder Bot | 30 | +20% |
| Sentinel | 30 | +20% |

**Sequence deliberately:**

1. Few Builders (2–4 early; maybe 5–6 total — not 20)
2. Nearest ore → Harvester
3. Full conveyor chain ending in a **Splitter** into the Core (tutorials 3–4)
4. More nearby Harvesters before distant ones (shorter chains = less Ti and less scale tax)
5. Gunners only once income is real
6. Sentinels / Launchers only mid–late when Ti surplus exists

Passive income is only **10 Ti / 4 rounds**. Real growth requires Harvesters whose stacks actually reach the Core.

---

## Economy: the non-negotiable loop

Tutorials hammer this: a Harvester with nowhere to dump output **does not mine**. Output only reaches the **4 cardinal neighbours**.

### Must-haves beyond the starter

1. **Complete chains** — walk Harvester → Core laying conveyors each step; detect Core adjacency by `get_tile_building_id` + `EntityType.CORE`, **not** by `can_move` failing (Core is 2×2 and blocks movement).
2. **Last tile = Splitter facing Core** — same delivery to Core, plus two spare sides for turrets / more branches.
3. **Cardinal-only facings** for conveyors/splitters.
4. Prefer Harvesters where a short L-shaped (horizontal-then-vertical) path to Core exists; defer hard terrain until you have pathfinding.

Track **delivered titanium** in your head (and in replays). If `mined` stays 0, you are playing for a coinflip.

---

## Combat & ammo

### Turret roles

| Turret | Role |
| --- | --- |
| **Gunner** | Cheap corridor hold; line shot; rotate costs 10 Ti |
| **Sentinel** | Expensive anchor; wider coverage; 10 ammo/shot — mid/late |
| **Launcher** | Throws friendly Builders (reposition / raid), not damage |

Gunners fire along facing; friendlies block the ray — don’t park Builders in the firing lane.

### Ammo (critical starter gap)

Unsupplied Gunners never fire. In **fcode 2.2**, ammo comes **only via conveyor feed** — `convert_ammo` was removed from the API (outdated in older tutorials).

### Ammo feed (fcode 2.2)

- Route titanium stacks into a Gunner or Sentinel from **any side except the facing direction** (a Splitter side port works well).
- A turret holds **at most one stack** and only accepts input when **completely empty**.
- **Gunner:** 2 Ti per shot, reload 1.
- **Sentinel:** 10 Ti per shot, reload 3.
- Unfed turrets cannot fire — the starter bot builds Gunners but does not feed them.

Practical ladder rule: **start conveyor feed once ≥1 Gunner exists; ramp feed when enemies are visible; starve ammo while you still need Harvesters.**

### Builder combat tools

- **Heal** (1 Ti → +4 HP) on damaged friendlies in action radius — keep chains and Core alive.
- **Sabotage**: walk onto enemy Conveyor/Splitter and `fire` own tile (2 Ti → 2 damage). Cutting their logistics wins mined-tiebreaks and starves their army.
- **`self_destruct` deals 0 damage** — only for freeing the 50-unit cap or denying a bounty, never as a weapon.

---

## Coordination (Store protocol)

16 team-private `u32` slots; **writes appear next round**. Name slots; design for one-round lag.

Suggested layout:

| Slot | Purpose |
| --- | --- |
| 0–1 | Core x, y (Core writes every round) |
| 2 | Harvester count / economy stage |
| 3–4 | Shared uncovered ore (or packed pos) |
| 5 | Threat flag / enemy-seen |
| 6 | Next free Builder role ID |
| 7–10 | Role assignments / claimed ore claims |
| 11–15 | Raid target, ammo policy, reserved |

### Roles (fix “everyone piles the same ore”)

Assign each new Builder a role via Store:

1. **Scout** — systematic explore, broadcast ore
2. **Miner** — claim one ore tile, build Harvester, then become Router or idle near it
3. **Router** — finish / repair chains toward Core
4. **Defender** — place Gunners/Sentinels near Core + Splitter feeds; heal
5. **Raider** (late) — walk enemy logistics, sabotage conveyors; optionally Launcher-assisted jumps

Claim ore in the Store so two Miners don’t fight over one deposit.

### Exploration

Replace random targets with a sweep (spiral / row scan / frontier from Core). Share discoveries; don’t re-discover the same patch with every bot.

---

## Pathfinding & Core approach

Tutorial L-walks fail on complex walls and “arrive” falsely when blocked. Upgrade to:

- BFS / A* on walkable tiles (empty, ore, any-team conveyor/splitter)
- Explicit Core-neighbour check before placing the final conveyor/splitter
- Stuck detection (starter’s 3-round timeout) + repath
- Prefer cardinal steps for movement consistency with chain laying

---

## Map-aware heuristics

Maps are 8×8–30×30 and **symmetric**. Use that:

- Mirror of your Core ≈ enemy Core → aim raids / gun lines along the symmetry axis
- Chokepoints (pinch, strait, fjord-style maps): Gunner + Barrier walls; don’t leave open flanks
- Open maps (duel, sprint): faster expand + more Harvesters; fewer static Sentinels early
- Dense walls: fewer Builders, better pathfinding, Launchers to hop bottlenecks

Keep a Stage enum driven by Store (`OPENING / EXPAND / DEFEND / PUSH`) so all units agree.

---

## Concrete “win all matches” checklist

A bot that consistently beats mirror-starter and climbs ladder should:

### Opening

- [ ] Core publishes position; spawns capped Builders (afford + scale aware)
- [ ] Scouts find nearest ore; Miner claims it in Store
- [ ] Harvester + complete chain + Splitter into Core within ~100 rounds when ore is nearby

### Mid

- [ ] ≥3–5 Harvesters with **live** delivery (nonzero mined)
- [ ] Ammo policy active; ≥2–4 Gunners covering approaches, fed via conveyors
- [ ] Heal priority on Core / Splitter / critical conveyors
- [ ] Stop over-spawning Builders once roles are filled

### Late / clinch

- [ ] Sentinels on main approaches **or** Raiders cutting enemy chains
- [ ] Optional: Launchers fling Raiders deep
- [ ] If Core kill unlikely: maximize delivered Ti + protect Harvesters (tiebreak stack)
- [ ] Never leave Gunners dry while fighting; never dump entire treasury into ammo while expand is behind

### Hard rules

- [ ] `can_X` before `X` everywhere (avoid mid-match `GameError`)
- [ ] Stay under 10 ms CPU / unit / turn (pathfinding must be bounded)
- [ ] Stay under 50 units; cull idle Builders if needed
- [ ] Don’t block your own Gunner rays

---

## Why the starter loses (and what to fix first)

Current `bots/starter/main.py` is economy-first but incomplete:

1. Incomplete conveyor chains → weak mined score  
2. No ammo supply → Gunners are decoration  
3. No roles → Builders collide and duplicate work  
4. Random explore → slow first Harvester  
5. No Sentinels / Launchers / sabotage  

**Highest ROI upgrade order** (matches tutorial “where to go from here”):

1. Full Harvester→Core chains + Splitter last-mile  
2. Conveyor ammo feed from a Splitter side port the moment a Gunner exists  
3. Store roles + ore claims  
4. Real pathfinding  
5. Heal + logistics sabotage  
6. Sentinels / Launchers / map-specific layouts  

---

## One-sentence strategy

**Win the mined race with short, complete logistics and disciplined scale, keep turrets conveyor-fed so they actually shoot, assign Builders roles so they don’t trip over each other, then either snip the enemy’s conveyors or punch their Core — and if the clock hits 1000, leave with more delivered titanium and more living Harvesters than they do.**

---

## Sources

- Tutorials 1–5: [game.code.florent.vc/tutorials](https://game.code.florent.vc/tutorials/)
- Package spec: `fcode` → `data/docs/spec.md`
- Reference gaps: starter docstring in `bots/starter/main.py`
