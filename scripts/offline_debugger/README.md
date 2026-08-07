# Offline Replay Debugger

The **Offline Replay Debugger** allows AI agents and developers to reconstruct, inspect, and step through past `.replay26` match recordings. It simulates the `fcode.Controller` API offline (`MockController`), enabling live bot decision logic (`Player.run`) to execute against historical turn snapshots without needing to run the full C++ game engine.

---

## 🎯 Primary Use Cases for AI Agents

1. **Root Cause Analysis**: When a match triage report identifies a loss, TLE, or unexpected unit behavior, use this debugger to inspect the exact state at the turn of failure.
2. **Automatic Debug Overrides & Profiling**: Automatically overrides `main.DEBUG = True` during offline execution, enabling all debug prints while benchmarking execution time in microseconds (µs) and generating a bottleneck profiling report to pinpoint slow functions causing TLEs.
3. **Communication Store Audit**: View the state of team shared store registers (slots 0–15) or simulate ledger broadcasting using `--rebuild-store`.

---

## 🚀 CLI Usage & Commands

All commands are run from the `teaPot/` project root using `.venv/bin/python`:

### 1. View World State at a Specific Round
Inspect unit positions, HPs, resources, and building counts:
```bash
.venv/bin/python scripts/offline_debugger/replay_debugger.py ranked/triage/<replay_name>.replay26 -r 40
```

### 2. Trace a Specific Unit's Decision
Run `Player.run(ct)` for Unit 3 on Team A at Round 40 and log all actions taken (move, build, fire, write_store):
```bash
.venv/bin/python scripts/offline_debugger/replay_debugger.py ranked/triage/<replay_name>.replay26 -r 40 -u 3 -t A
```

### 3. Rebuild Shared Store / Communication Ledger
Simulate turns 0 through 39 to reconstruct the team's shared store slots (slots 0–15) before executing the unit:
```bash
.venv/bin/python scripts/offline_debugger/replay_debugger.py ranked/triage/<replay_name>.replay26 -r 40 -u 3 -t A --rebuild-store
```

### 4. Multi-Turn Decision Range Debugging
Step through consecutive turns (e.g., Round 40 to 60) to observe how unit decisions evolve:
```bash
.venv/bin/python scripts/offline_debugger/replay_debugger.py ranked/triage/<replay_name>.replay26 -r 40 -e 60 -u 3 -t A --rebuild-store
```

---

## 📁 Architecture & Component Map

| File | Description |
| :--- | :--- |
| **[`replay_debugger.py`](file:///home/nraatika/projects/florent/teaPot/scripts/offline_debugger/replay_debugger.py)** | Main CLI entry point. Parses CLI flags, manages turn ranges, instantiates `MockController`, and invokes `Player.run(ct)`. |
| **[`replay_state.py`](file:///home/nraatika/projects/florent/teaPot/scripts/offline_debugger/replay_state.py)** | Replay parser engine. Reads `.replay26` protobuf frames and builds `WorldState` and `EntityRecord` data structures. |
| **[`mock_controller.py`](file:///home/nraatika/projects/florent/teaPot/scripts/offline_debugger/mock_controller.py)** | Mock `fcode.Controller` implementation. Exposes map vision, unit state, and action methods (`move`, `build`, `fire`, `write_store`) for offline execution. |

---

## 💡 Agent Workflow Tips

- **Step 1**: Find problematic replays using `scripts/triage_matches.py --hours 3`.
- **Step 2**: Identify the round number and unit ID from the triage log or stack trace.
- **Step 3**: Execute `replay_debugger.py` with `--rebuild-store` on that target round.
- **Step 4**: Inspect the `Action log` and `EXCEPTION` outputs to diagnose logic errors or invalid state transitions.
