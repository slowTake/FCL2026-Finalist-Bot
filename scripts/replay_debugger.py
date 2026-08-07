#!/usr/bin/env python3
"""Replay State Debugger — Reconstruct game state from .replay26 and re-run bot logic.

Parses a .replay26 protobuf file, incrementally replays all events to reconstruct
the full world state at any round T, then optionally re-runs your bot's Player.run()
against a MockController backed by that state. This lets you reproduce tournament
bugs locally with full debug output, even without the opponent's code.

Run from the teaPot/ directory using the project venv:
    .venv/bin/python scripts/replay_debugger.py <replay_file> [options]

═══════════════════════════════════════════════════════════════════════════════
  MODES
═══════════════════════════════════════════════════════════════════════════════

  1. DUMP — Print a summary of world state (economy, entity counts)
     .venv/bin/python scripts/replay_debugger.py replay.replay26 -r 150 --dump

  2. LIST ENTITIES — Print every entity with position, HP, direction, cooldowns
     .venv/bin/python scripts/replay_debugger.py replay.replay26 -r 150 --list-entities
     .venv/bin/python scripts/replay_debugger.py replay.replay26 -r 150 --list-entities -t B

  3. SHOW OUTPUT — Display the bot's actual stdout captured during the game
     .venv/bin/python scripts/replay_debugger.py replay.replay26 -r 150 -u 7 --show-output

  4. RUN UNIT — Re-execute Player.run() for a specific unit against reconstructed state
     .venv/bin/python scripts/replay_debugger.py replay.replay26 -r 150 -u 7 -t A

  5. RUN UNIT + REBUILD STORE — Same as above, but first replays the bot from round 0
     to reconstruct the communication store (slots 0-15) before running the target round
     .venv/bin/python scripts/replay_debugger.py replay.replay26 -r 150 -u 7 -t A --rebuild-store

  If no mode flags are given, defaults to --dump + --list-entities.

═══════════════════════════════════════════════════════════════════════════════
  FLAGS
═══════════════════════════════════════════════════════════════════════════════

  replay              Path to .replay26 file (required, positional)
  -r, --round         Round number to reconstruct, 0-indexed (required)
  -t, --team          Team perspective: A or B (default: A)
  -u, --unit          Entity ID to debug (required for run/show-output modes)
  --dump              Print world state summary
  --list-entities     Print detailed entity table for the specified team
  --show-output       Show the unit's actual stdout from the replay recording
  --rebuild-store     Rebuild store slots by replaying bot from round 0
                      (slow but gives accurate read_store values)

═══════════════════════════════════════════════════════════════════════════════
  TYPICAL WORKFLOWS
═══════════════════════════════════════════════════════════════════════════════

  Investigating a timeout (TLE):
    1. Find the round/unit that TLE'd:
       .venv/bin/python scripts/replay_debugger.py game.replay26 -r 300 --show-output -u 5
    2. Re-run with debug prints in your bot code:
       .venv/bin/python scripts/replay_debugger.py game.replay26 -r 300 -u 5 -t A
       (no CPU limit in mock — add timing/prints to find the slow function)

  Debugging pathfinding:
    1. Dump state to see entity layout:
       .venv/bin/python scripts/replay_debugger.py game.replay26 -r 200 --dump --list-entities
    2. Re-run the stuck builder with debug prints:
       .venv/bin/python scripts/replay_debugger.py game.replay26 -r 200 -u 12 -t A
    3. Add prints inside your A*/walkPassable to trace the path search

  Checking HiveMind store state:
    .venv/bin/python scripts/replay_debugger.py game.replay26 -r 150 -u 3 -t A --rebuild-store
    (prints reconstructed store values before running the unit)

═══════════════════════════════════════════════════════════════════════════════
  LIMITATIONS
═══════════════════════════════════════════════════════════════════════════════

  - Store slots (0-15) are NOT in replays. Use --rebuild-store to reconstruct
    them by re-running your bot from round 0 (slow, O(rounds × units)).
  - Global ammo is estimated from turret fire events, may drift slightly.
  - Actions (build, move, fire) are logged but do NOT mutate the state.
    The mock is single-step read-only.
  - Vision fog is computed from your team's entities. Replay has ground truth
    for all entities, but get_nearby_* respects fog of war.
  - Cost scaling is derived from living entity counts; may not match exactly
    if the engine tracks lifetime builds differently.

═══════════════════════════════════════════════════════════════════════════════
  FILES
═══════════════════════════════════════════════════════════════════════════════

  scripts/replay_debugger.py  — This CLI harness
  scripts/replay_state.py     — ReplayStateBuilder + WorldState + EntityRecord
  scripts/mock_controller.py  — MockController (implements full Controller API)
"""

import sys
import argparse
from pathlib import Path

# Ensure imports work from scripts/ directory
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from replay_state import ReplayStateBuilder, WorldState, EntityRecord
from mock_controller import MockController
from fcode import Team, EntityType, Direction, Environment, Position


def team_from_str(s: str) -> Team:
    s = s.upper().strip()
    if s in ('A', 'TEAM_A'): return Team.A
    if s in ('B', 'TEAM_B'): return Team.B
    raise ValueError(f"Unknown team: {s}")


def dump_state(state: WorldState):
    """Print a human-readable summary of the world state."""
    print(f"\n{'='*60}")
    print(f"  WORLD STATE — Round {state.round_number}")
    print(f"  Map: {state.map_width}x{state.map_height}")
    print(f"{'='*60}")

    print(f"\n  Team A: {state.team_titanium[Team.A]} Ti, {state.team_ammo[Team.A]} ammo")
    print(f"  Team B: {state.team_titanium[Team.B]} Ti, {state.team_ammo[Team.B]} ammo")

    for team in (Team.A, Team.B):
        team_name = 'A' if team == Team.A else 'B'
        team_ents = [e for e in state.entities.values() if e.team == team]
        print(f"\n  Team {team_name} entities ({len(team_ents)}):")
        by_type = {}
        for e in team_ents:
            t = str(e.entity_type)
            by_type.setdefault(t, []).append(e)
        for t, ents in sorted(by_type.items()):
            print(f"    {t}: {len(ents)}")

    print(f"\n  Entity build counts:")
    for team in (Team.A, Team.B):
        team_name = 'A' if team == Team.A else 'B'
        counts = state.entity_build_counts[team]
        if counts:
            print(f"    Team {team_name}: {dict(counts)}")


def list_entities(state: WorldState, team_filter: Team | None = None):
    """Print detailed entity listing."""
    print(f"\n{'='*60}")
    print(f"  ENTITIES — Round {state.round_number}")
    print(f"{'='*60}")
    print(f"  {'ID':>5}  {'Team':>4}  {'Type':<15}  {'Pos':>8}  {'HP':>8}  {'Dir':>6}  {'CD':>5}  {'Ammo':>6}")
    print(f"  {'-'*5}  {'-'*4}  {'-'*15}  {'-'*8}  {'-'*8}  {'-'*6}  {'-'*5}  {'-'*6}")

    for eid in sorted(state.entities.keys()):
        e = state.entities[eid]
        if team_filter is not None and e.team != team_filter:
            continue
        team_name = 'A' if e.team == Team.A else 'B'
        dir_str = e.direction.name if e.direction else '-'
        hp_str = f"{e.hp}/{e.max_hp}"
        cd_str = f"{e.action_cooldown}"
        if e.entity_type == EntityType.BUILDER_BOT:
            cd_str = f"{e.action_cooldown}/{e.move_cooldown}"
        ammo_str = str(e.ammo_amount) if e.ammo_amount else '-'
        print(f"  {eid:>5}  {team_name:>4}  {str(e.entity_type):<15}  ({e.x:>2},{e.y:>2})  {hp_str:>8}  {dir_str:>6}  {cd_str:>5}  {ammo_str:>6}")


def show_output(state: WorldState, unit_id: int):
    """Show the bot's stdout output from the replay for a specific unit."""
    if unit_id in state.bot_outputs:
        print(f"\n  Bot output for unit {unit_id} at round {state.round_number}:")
        print(f"  Exec time: {state.bot_exec_times.get(unit_id, '?')} μs")
        print(f"  TLE'd: {state.bot_tled.get(unit_id, False)}")
        print(f"  stdout:\n{state.bot_outputs[unit_id]}")
    else:
        print(f"\n  No output recorded for unit {unit_id} at round {state.round_number}.")
        # Show which units DO have output
        if state.bot_outputs:
            print(f"  Units with output this round: {sorted(state.bot_outputs.keys())}")


def run_unit(state: WorldState, unit_id: int, team: Team, rebuild_store: bool = False,
             replay_builder: ReplayStateBuilder | None = None):
    """Run our bot's logic for a specific unit using the MockController."""
    if unit_id not in state.entities:
        print(f"\n  ERROR: Unit {unit_id} does not exist at round {state.round_number}.")
        available = [eid for eid, e in state.entities.items() if e.team == team]
        print(f"  Available units for team {'A' if team == Team.A else 'B'}: {sorted(available)}")
        return

    entity = state.entities[unit_id]
    if entity.team != team:
        print(f"\n  WARNING: Unit {unit_id} belongs to team {'A' if entity.team == Team.A else 'B'}, "
              f"not team {'A' if team == Team.A else 'B'}. Running anyway with specified team's perspective.")

    ct = MockController(state, unit_id, team)

    # Rebuild store state by replaying our bot from round 0
    if rebuild_store and replay_builder:
        print(f"\n  Rebuilding store state by replaying rounds 0..{state.round_number - 1}...")
        store = _rebuild_store(replay_builder, state.round_number, team)
        ct.set_store_values(store)
        print(f"  Store values: {dict(store)}")

    print(f"\n{'='*60}")
    print(f"  RUNNING UNIT {unit_id} — Round {state.round_number}")
    print(f"  Type: {entity.entity_type}")
    print(f"  Position: ({entity.x}, {entity.y})")
    print(f"  HP: {entity.hp}/{entity.max_hp}")
    print(f"  Team: {'A' if team == Team.A else 'B'}")
    print(f"{'='*60}")

    # Import and run the bot
    try:
        bot_path = Path(__file__).resolve().parent.parent / 'bots' / 'baron'
        sys.path.insert(0, str(bot_path))
        from main import Player

        player = Player()
        print(f"\n  Running Player.run(ct)...\n")
        player.run(ct)
        print(f"\n  Execution completed successfully.")
    except Exception as exc:
        print(f"\n  EXCEPTION during Player.run(): {type(exc).__name__}: {exc}")
        import traceback
        traceback.print_exc()

    # Print action log
    log = ct.get_action_log()
    if log:
        print(f"\n  Action log ({len(log)} actions):")
        for i, action in enumerate(log):
            print(f"    [{i}] {action}")
    else:
        print(f"\n  No actions taken.")


def _rebuild_store(replay_builder: ReplayStateBuilder, target_round: int, team: Team) -> dict[int, int]:
    """Rebuild store state by running our bot from round 0 to target_round - 1.
    
    This captures all write_store calls to reconstruct the store state that
    our bot would see at the target round.
    """
    store = {i: 0 for i in range(16)}
    # Buffer for delayed writes (writes take effect next round)
    pending_writes: dict[int, int] = {}

    bot_path = Path(__file__).resolve().parent.parent / 'bots' / 'baron'
    sys.path.insert(0, str(bot_path))
    from main import Player

    players: dict[int, Player] = {}

    for r in range(target_round):
        # Apply pending writes from last round
        store.update(pending_writes)
        pending_writes.clear()

        state = replay_builder.build_state(r)
        for e in state.entities.values():
            e.action_cooldown = 0
            e.move_cooldown = 0

        team_units = [
            (eid, e) for eid, e in state.entities.items()
            if e.team == team and e.entity_type in (
                EntityType.CORE, EntityType.BUILDER_BOT,
                EntityType.GUNNER, EntityType.SENTINEL, EntityType.LAUNCHER
            )
        ]

        for eid, _ in team_units:
            if eid not in players:
                players[eid] = Player()
            ct = MockController(state, eid, team)
            ct.set_store_values(dict(store))
            try:
                players[eid].run(ct)
            except Exception:
                pass  # Ignore errors during store reconstruction
            # Capture writes
            for action in ct.get_action_log():
                if action.startswith('write_store'):
                    parts = action.split()
                    idx, val = int(parts[1]), int(parts[2])
                    if idx != 4:  # Do not let mock Core artificially inflate slot 4
                        pending_writes[idx] = val

        # Ensure slot 4 tracks actual cumulative builders from replay state
        builder_count = sum(1 for e in state.entities.values() if e.team == team and e.entity_type == EntityType.BUILDER_BOT)
        if builder_count > store[4]:
            store[4] = builder_count

    # Apply final pending writes
    store.update(pending_writes)
    return store


def main():
    parser = argparse.ArgumentParser(
        description='Replay State Debugger — reconstruct game state and debug bot logic',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument('replay', help='Path to .replay26 file')
    parser.add_argument('--round', '-r', type=int, required=True,
                        help='Round number (or start round) to reconstruct (0-indexed)')
    parser.add_argument('--end-round', '-e', type=int, default=None,
                        help='End round number for multi-turn range traces (inclusive)')
    parser.add_argument('--team', '-t', type=str, default='A',
                        help='Team perspective (A or B)')
    parser.add_argument('--unit', '-u', type=int, default=None,
                        help='Unit ID to debug')
    parser.add_argument('--dump', action='store_true',
                        help='Print world state summary')
    parser.add_argument('--list-entities', action='store_true',
                        help='Print detailed entity listing')
    parser.add_argument('--show-output', action='store_true',
                        help='Show bot stdout from replay for the unit')
    parser.add_argument('--rebuild-store', action='store_true',
                        help='Rebuild store state by replaying bot from round 0')

    args = parser.parse_args()
    team = team_from_str(args.team)

    print(f"  Loading replay: {args.replay}")
    builder = ReplayStateBuilder(args.replay)
    total = builder.get_total_rounds()
    winner = builder.get_winner()
    print(f"  Total rounds: {total}, Winner: {winner or 'N/A'}")

    start_r = args.round
    end_r = args.end_round if args.end_round is not None else start_r

    if start_r >= total:
        print(f"  ERROR: Round {start_r} out of range (max {total - 1})")
        sys.exit(1)

    for r in range(start_r, min(end_r + 1, total)):
        print(f"\n▶ Round {r}:")
        state = builder.build_state(r)

        if args.dump:
            dump_state(state)

        if args.list_entities:
            list_entities(state, team_filter=team)

        if args.show_output and args.unit is not None:
            show_output(state, args.unit)

        if args.unit is not None and not args.show_output:
            run_unit(state, args.unit, team,
                     rebuild_store=args.rebuild_store,
                     replay_builder=builder)

    if not args.dump and not args.list_entities and args.unit is None and args.end_round is None:
        state = builder.build_state(start_r)
        dump_state(state)
        list_entities(state, team_filter=team)


if __name__ == '__main__':
    main()
