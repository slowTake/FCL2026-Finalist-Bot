"""Production-grade, ultra-lightweight in-bot telemetry module for fcode bots.

Logs high-resolution per-turn unit telemetry (FSM state, task utility scores,
IPC slots, pathfinding CPU time) into JSON Lines (.jsonl) without violating
the 10ms CPU budget per turn.
"""

from __future__ import annotations

import json
import os
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from fcode import Controller, Position


class BotTelemetry:
    """Non-blocking, low-overhead telemetry logger for fcode units."""

    _instance: Optional["BotTelemetry"] = None

    def __init__(self, log_dir: str = "test_results/telemetry", enabled: bool = True):
        self.enabled = enabled
        self.log_dir = log_dir
        self.buffer: List[Dict[str, Any]] = []
        self.max_buffer_size = 500
        self.start_ns: int = 0
        self.log_path: Optional[str] = None

        if self.enabled:
            os.makedirs(self.log_dir, exist_ok=True)

    @classmethod
    def get_instance(cls) -> "BotTelemetry":
        if cls._instance is None:
            cls._instance = BotTelemetry()
        return cls._instance

    def start_tick(self) -> None:
        """Mark start of unit execution turn."""
        if not self.enabled:
            return
        self.start_ns = time.perf_counter_ns()

    def record_turn(
        self,
        ct: "Controller",
        role: str,
        state: str,
        task: Optional[str] = None,
        utility_score: float = 0.0,
        extra_metrics: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Record a single turn execution event for this unit."""
        if not self.enabled:
            return

        elapsed_us = (time.perf_counter_ns() - self.start_ns) // 1000
        cpu_engine_us = ct.get_cpu_time_elapsed()
        round_n = ct.get_current_round()
        unit_id = ct.get_id()
        entity_type = str(ct.get_entity_type())
        pos = ct.get_position()

        # Extract 16-slot IPC store snapshot (read 0..15)
        ipc_store = [ct.read_store(i) for i in range(16)]

        record: Dict[str, Any] = {
            "round": round_n,
            "unit_id": unit_id,
            "team": str(ct.get_team()),
            "entity_type": entity_type,
            "pos": [pos.x, pos.y] if pos else None,
            "role": role,
            "state": state,
            "task": task or "NONE",
            "utility": round(utility_score, 3),
            "cpu_us_python": elapsed_us,
            "cpu_us_engine": cpu_engine_us,
            "ipc_store": ipc_store,
            "action_cooldown": ct.get_action_cooldown(),
            "move_cooldown": ct.get_move_cooldown() if hasattr(ct, "get_move_cooldown") else 0,
        }

        if extra_metrics:
            record["extra"] = extra_metrics

        self.buffer.append(record)

        # Flush buffer if threshold is met or on turn 1000
        if len(self.buffer) >= self.max_buffer_size or round_n >= 999:
            self.flush()

    def flush(self) -> None:
        """Batch flush buffered records to JSONL file on disk."""
        if not self.enabled or not self.buffer:
            return

        if self.log_path is None:
            team_str = self.buffer[0].get("team", "TEAM")
            self.log_path = os.path.join(self.log_dir, f"bot_telemetry_{team_str}.jsonl")

        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                for entry in self.buffer:
                    f.write(json.dumps(entry) + "\n")
            self.buffer.clear()
        except OSError:
            # Fall back silently to avoid raising unhandled exceptions inside tick
            self.buffer.clear()
