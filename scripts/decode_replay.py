#!/usr/bin/env python3
import sys
import florent_replay_pb2

def main():
    if len(sys.argv) < 2:
        print("Usage: python decode_replay.py <path_to_replay.replay26>")
        sys.exit(1)

    replay_path = sys.argv[1]
    
    with open(replay_path, "rb") as f:
        data = f.read()

    replay = florent_replay_pb2.Replay()
    replay.ParseFromString(data)

    print(f"--- Replay Summary: {replay_path} ---")
    print(f"Map size: {replay.map.width}x{replay.map.height}")
    print(f"Number of cores: {len(replay.map.cores)}")
    print(f"Total turns: {len(replay.turns)}")
    
    if replay.HasField('winner'):
        winner_name = "TEAM A" if replay.winner == florent_replay_pb2.TEAM_A else "TEAM B"
        print(f"Winner: {winner_name}")
    else:
        print("Winner: TIE / INCOMPLETE")
        
    print("\n--- Last 3 Turns Event Log ---")
    start_turn = max(0, len(replay.turns) - 3)
    for i in range(start_turn, len(replay.turns)):
        turn = replay.turns[i]
        print(f"\nTurn {i} ({len(turn.updates)} updates):")
        for j, update in enumerate(turn.updates):
            kind = update.WhichOneof("kind")
            payload = getattr(update, kind)
            # Make the payload a single line string to avoid blowing up the console
            payload_str = str(payload).replace("\n", " ").strip()
            print(f"  [{j}] {kind}: {payload_str}")
            
if __name__ == "__main__":
    main()
