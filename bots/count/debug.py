"""Opt-in per-turn behaviour tracing — companion to telemetry.py's COUNT_CPU,
which answers "how slow" (timing); this answers "what did it think was
happening" (state), for when a replay shows a unit doing nothing and the
question is why.

Off unless COUNT_DEBUG=1 is in the environment. Callers must gate the call
site itself, not just rely on log() doing nothing:

    if debug.ENABLED:
        debug.log("raidBot", f"blocked at {pos} by {occupant}")

not

    debug.log("raidBot", f"blocked at {pos} by {occupant}")

The f-string is built by the caller before log() ever runs, so an unguarded
call site still pays the formatting cost every turn even when tracing is
off — exactly the kind of per-turn cost this codebase measures rather than
assumes away (see telemetry.py, observations.md Performance). A submission
zip with COUNT_DEBUG unset should pay one boolean check per call site and
nothing else, same guarantee telemetry.py already makes for COUNT_CPU.
"""

from __future__ import annotations

import os
import sys

ENABLED = os.environ.get("COUNT_DEBUG") == "1"


def log(tag: str, msg: str) -> None:
    """One line to stderr, prefixed for grep: `[tag] msg`. Callers gate with
    `if debug.ENABLED:` — see module docstring for why that's not optional."""
    sys.stderr.write(f"[{tag}] {msg}\n")
