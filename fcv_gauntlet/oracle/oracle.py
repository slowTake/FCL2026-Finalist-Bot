"""oracle — agentic AI root-cause diagnosis for FCV Bot Gauntlet findings.

Takes the structured output of an fcv_gauntlet run (causal chains, top cost
leaks, detector counts) and uses the Claude API — with a scoped, read-only
`read_file` tool over the project's own source — to reason about the *actual*
root cause behind a diagnostic finding, instead of the hardcoded if/else
suggestion templates in reporting.py.

This spends real money from a personal Anthropic API budget. It never runs
without an explicit `--yes` on the CLI or `confirmed=True` in code — see
AGENT.md for the rule every agent must follow before setting that.

Budget model
------------
- A local spend ledger (`.spend_ledger.json`, gitignored) records every call's
  *actual* billed usage (from `response.usage`), not an estimate.
- DAILY_CAP_USD and MONTHLY_CAP_USD are hard stops, checked before any call.
- Per-run cost is bounded by MAX_OUTPUT_TOKENS and MAX_TOOL_CALLS, so a single
  run can't blow past the daily cap even in a worst case.
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Config — tune these, not the logic below.
# ---------------------------------------------------------------------------

MODEL = "claude-sonnet-5"

# $/1M tokens. Sonnet 5 intro pricing runs through 2026-08-31 — if you're
# reading this after that date, confirm current pricing (see the claude-api
# skill / platform.claude.com/docs/en/pricing) and update this table.
PRICING_PER_MTOK: dict[str, dict[str, float]] = {
    "claude-sonnet-5": {"input": 2.00, "output": 10.00},
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
    "claude-opus-5": {"input": 5.00, "output": 25.00},
}

DAILY_CAP_USD = 1.50
MONTHLY_CAP_USD = 23.00

MAX_OUTPUT_TOKENS = 4096
MAX_TOOL_CALLS = 6
MAX_FILE_READ_CHARS = 20_000
EFFORT = "medium"  # low|medium|high|xhigh|max — medium balances quality/cost

LEDGER_PATH = Path(__file__).resolve().parent / ".spend_ledger.json"
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent  # fcv_gauntlet/oracle/ -> repo root


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class OracleError(Exception):
    """Base class for oracle errors."""


class OracleConfirmationRequired(OracleError):
    """Raised when diagnose() is called without explicit user confirmation.

    This tool costs real money from a personal API budget. Every caller —
    human or agent — must get an explicit go-ahead from the account owner
    before setting confirmed=True. See AGENT.md.
    """


class OracleBudgetExceeded(OracleError):
    """Raised when the daily or monthly spend cap would be exceeded."""


# ---------------------------------------------------------------------------
# Spend ledger
# ---------------------------------------------------------------------------

@dataclass
class BudgetStatus:
    ok: bool
    reason: str
    today_usd: float
    month_usd: float
    daily_remaining_usd: float
    monthly_remaining_usd: float


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _load_ledger() -> dict[str, Any]:
    if not LEDGER_PATH.is_file():
        return {"entries": []}
    try:
        with open(LEDGER_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if "entries" not in data:
            data["entries"] = []
        return data
    except (json.JSONDecodeError, OSError):
        # Never let a corrupt ledger silently disable the spend cap.
        raise OracleError(
            f"Spend ledger at {LEDGER_PATH} is unreadable/corrupt. "
            "Fix or delete it before running oracle again — refusing to run "
            "with an untrustworthy budget record."
        )


def _save_ledger(ledger: dict[str, Any]) -> None:
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LEDGER_PATH, "w", encoding="utf-8") as f:
        json.dump(ledger, f, indent=2)


def spend_summary(ledger: dict[str, Any] | None = None) -> dict[str, float]:
    """Return {'today_usd': x, 'month_usd': y} from the ledger."""
    ledger = ledger if ledger is not None else _load_ledger()
    now = _now()
    today_str = now.strftime("%Y-%m-%d")
    month_str = now.strftime("%Y-%m")

    today_usd = 0.0
    month_usd = 0.0
    for e in ledger.get("entries", []):
        ts = e.get("date", "")
        cost = float(e.get("cost_usd", 0.0))
        if ts == today_str:
            today_usd += cost
        if ts.startswith(month_str):
            month_usd += cost
    return {"today_usd": round(today_usd, 4), "month_usd": round(month_usd, 4)}


def check_budget(ledger: dict[str, Any] | None = None) -> BudgetStatus:
    """Check current spend against the daily/monthly caps. Does not spend anything."""
    ledger = ledger if ledger is not None else _load_ledger()
    summary = spend_summary(ledger)
    today_usd = summary["today_usd"]
    month_usd = summary["month_usd"]
    daily_remaining = round(DAILY_CAP_USD - today_usd, 4)
    monthly_remaining = round(MONTHLY_CAP_USD - month_usd, 4)

    if month_usd >= MONTHLY_CAP_USD:
        return BudgetStatus(
            False,
            f"Monthly cap reached: ${month_usd:.2f} spent of ${MONTHLY_CAP_USD:.2f} budget.",
            today_usd, month_usd, daily_remaining, monthly_remaining,
        )
    if today_usd >= DAILY_CAP_USD:
        return BudgetStatus(
            False,
            f"Daily cap reached: ${today_usd:.2f} spent of ${DAILY_CAP_USD:.2f}/day.",
            today_usd, month_usd, daily_remaining, monthly_remaining,
        )
    return BudgetStatus(
        True, "ok", today_usd, month_usd, daily_remaining, monthly_remaining,
    )


def estimate_cost(input_tokens: int, output_tokens: int, model: str = MODEL) -> float:
    price = PRICING_PER_MTOK.get(model, PRICING_PER_MTOK[MODEL])
    return round(input_tokens / 1_000_000 * price["input"] + output_tokens / 1_000_000 * price["output"], 6)


def _record_usage(ledger: dict[str, Any], model: str, usage: Any, run_id: str) -> float:
    """Append one billed call to the ledger and persist it immediately."""
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    cache_write = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
    cache_read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
    price = PRICING_PER_MTOK.get(model, PRICING_PER_MTOK[MODEL])

    # Cache writes bill at ~1.25x input; cache reads at ~0.1x input.
    cost = (
        input_tokens / 1_000_000 * price["input"]
        + cache_write / 1_000_000 * price["input"] * 1.25
        + cache_read / 1_000_000 * price["input"] * 0.1
        + output_tokens / 1_000_000 * price["output"]
    )
    cost = round(cost, 6)

    ledger.setdefault("entries", []).append({
        "date": _now().strftime("%Y-%m-%d"),
        "timestamp": _now().isoformat(),
        "run_id": run_id,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_creation_input_tokens": cache_write,
        "cache_read_input_tokens": cache_read,
        "cost_usd": cost,
    })
    _save_ledger(ledger)
    return cost


def format_budget_report(status: BudgetStatus | None = None) -> str:
    status = status or check_budget()
    lines = [
        "=== Oracle API spend ===",
        f"  Today:      ${status.today_usd:.4f} / ${DAILY_CAP_USD:.2f}  (${status.daily_remaining_usd:.4f} remaining)",
        f"  This month: ${status.month_usd:.4f} / ${MONTHLY_CAP_USD:.2f}  (${status.monthly_remaining_usd:.4f} remaining)",
    ]
    if not status.ok:
        lines.append(f"  ⛔ {status.reason}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Scoped read-only file tool
# ---------------------------------------------------------------------------

def _safe_read_file(rel_path: str, project_root: Path) -> str:
    """Read a project-relative file, confined to project_root. Never raises
    outward — returns an error string the model can react to instead."""
    try:
        target = (project_root / rel_path).resolve()
        root = project_root.resolve()
        if not str(target).startswith(str(root) + "/") and target != root:
            return f"Error: path '{rel_path}' escapes the project root — refused."
        if any(part in (".venv", ".git", "__pycache__") or part.startswith(".env") for part in target.parts):
            return f"Error: path '{rel_path}' is inside a blocked directory."
        if not target.is_file():
            return f"Error: '{rel_path}' is not a file (or does not exist)."
        text = target.read_text(encoding="utf-8", errors="replace")
        if len(text) > MAX_FILE_READ_CHARS:
            text = text[:MAX_FILE_READ_CHARS] + f"\n... [truncated at {MAX_FILE_READ_CHARS} chars]"
        return text
    except Exception as exc:  # noqa: BLE001 — must never raise into the tool loop
        return f"Error reading '{rel_path}': {exc}"


READ_FILE_TOOL = {
    "name": "read_file",
    "description": (
        "Read a source file from this project (bot code, engine glue, config) to "
        "inspect the actual implementation behind a diagnostic finding, so your "
        "root-cause analysis cites real code instead of guessing. Path is "
        "project-relative, e.g. 'bots/baron/builder.py'. Returns up to "
        f"{MAX_FILE_READ_CHARS} characters; larger files are truncated."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Project-relative file path."},
        },
        "required": ["path"],
    },
}


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

def _build_prompt(report_data: dict[str, Any]) -> str:
    summary = report_data.get("summary", {})
    counts = summary.get("detector_counts", {})
    leaks = report_data.get("top_cost_leaks", [])[:10]
    chains = report_data.get("causal_chains", [])[:8]

    parts = [
        "You are diagnosing bugs in a competitive bot for a grid-based RTS game "
        "(FCV Bot Gauntlet). Below is structured diagnostic output from an "
        "automated replay analyzer: per-detector incident counts, the costliest "
        "individual events (estimated titanium lost), and causal chains linking "
        "related events together (e.g. a crash that orphaned a harvester).",
        "",
        f"Candidate bot: {report_data.get('candidate_bot')}  |  "
        f"Opponent: {report_data.get('opponent_bot')}",
        "",
        "## Detector incident counts",
        json.dumps(counts, indent=2),
        "",
        "## Top cost leaks (estimated titanium lost)",
        json.dumps(leaks, indent=2),
        "",
        "## Causal chains",
        json.dumps(chains, indent=2),
        "",
        "Use the read_file tool to inspect the actual bot source (paths are under "
        "bots/<bot_name>/...) for the modules implicated by the highest-cost "
        "leaks and chains — don't guess at file names, read the ones evidence "
        "points to. Then write a root-cause diagnosis: for each real issue you "
        "can substantiate from the code, give the file:line, what the code is "
        "actually doing wrong, and a concrete fix. Prioritize by estimated "
        "titanium loss. Be concise — this is a diagnostic report, not an essay. "
        "If the evidence doesn't clearly point to a fixable bug, say so rather "
        "than speculating.",
    ]
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Diagnosis result + main entry point
# ---------------------------------------------------------------------------

@dataclass
class DiagnosisResult:
    text: str
    cost_usd: float
    input_tokens: int
    output_tokens: int
    tool_calls_used: int
    stopped_reason: str = "end_turn"


def diagnose(
    report_data: dict[str, Any],
    *,
    confirmed: bool,
    project_root: Path | None = None,
    model: str = MODEL,
    max_tool_calls: int = MAX_TOOL_CALLS,
    effort: str = EFFORT,
    run_id: str | None = None,
) -> DiagnosisResult:
    """Run an agentic root-cause diagnosis over report_data. Costs real money.

    Raises OracleConfirmationRequired unless confirmed=True — see AGENT.md for
    the rule on when it's OK to pass that. Raises OracleBudgetExceeded if the
    daily/monthly spend cap is already hit.
    """
    if not confirmed:
        raise OracleConfirmationRequired(
            "diagnose() called with confirmed=False. This spends real API money "
            "from a personal budget — get explicit sign-off from the account "
            "owner on the estimated cost before calling this with confirmed=True. "
            "See fcv_gauntlet/oracle/AGENT.md."
        )

    try:
        import anthropic
    except ImportError as exc:
        raise OracleError(
            "The 'anthropic' package is not installed. Run: pip install anthropic"
        ) from exc

    project_root = project_root or PROJECT_ROOT
    ledger = _load_ledger()
    status = check_budget(ledger)
    if not status.ok:
        raise OracleBudgetExceeded(status.reason)

    run_id = run_id or f"oracle-{_now().strftime('%Y%m%dT%H%M%S')}"
    client = anthropic.Anthropic()

    messages: list[dict[str, Any]] = [{"role": "user", "content": _build_prompt(report_data)}]
    tools = [READ_FILE_TOOL]

    total_input = 0
    total_output = 0
    total_cost = 0.0
    tool_calls_used = 0
    final_text = ""
    stop_reason = "end_turn"

    for _ in range(max_tool_calls + 1):  # +1 so the model gets a final turn after its last tool call
        response = client.messages.create(
            model=model,
            max_tokens=MAX_OUTPUT_TOKENS,
            thinking={"type": "adaptive"},
            output_config={"effort": effort},
            tools=tools,
            messages=messages,
        )

        cost = _record_usage(ledger, model, response.usage, run_id)
        total_cost += cost
        total_input += int(getattr(response.usage, "input_tokens", 0) or 0)
        total_output += int(getattr(response.usage, "output_tokens", 0) or 0)
        stop_reason = response.stop_reason

        if stop_reason == "refusal":
            final_text = "(model declined to respond — see stop_details)"
            break

        text_blocks = [b.text for b in response.content if getattr(b, "type", None) == "text"]
        if text_blocks:
            final_text = "\n".join(text_blocks)

        if stop_reason != "tool_use":
            break

        tool_use_blocks = [b for b in response.content if getattr(b, "type", None) == "tool_use"]
        messages.append({"role": "assistant", "content": response.content})

        if tool_calls_used >= max_tool_calls:
            # Budget for tool calls exhausted — tell the model to wrap up without more reads.
            messages.append({
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": b.id,
                        "content": "Tool call budget exhausted for this run. Write your "
                                   "diagnosis now using only what you've already read.",
                        "is_error": True,
                    }
                    for b in tool_use_blocks
                ],
            })
            continue

        tool_results = []
        for b in tool_use_blocks:
            tool_calls_used += 1
            if b.name == "read_file":
                result = _safe_read_file(b.input.get("path", ""), project_root)
            else:
                result = f"Error: unknown tool '{b.name}'"
            tool_results.append({"type": "tool_result", "tool_use_id": b.id, "content": result})
        messages.append({"role": "user", "content": tool_results})

    return DiagnosisResult(
        text=final_text or "(no analysis produced)",
        cost_usd=round(total_cost, 6),
        input_tokens=total_input,
        output_tokens=total_output,
        tool_calls_used=tool_calls_used,
        stopped_reason=stop_reason,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli() -> int:
    parser = argparse.ArgumentParser(
        prog="oracle",
        description="Agentic AI root-cause diagnosis for an fcv_gauntlet JSON report. "
                     "Costs real API money — see AGENT.md before scripting this.",
    )
    parser.add_argument("--report", help="Path to a JSON report from write_json_report()")
    parser.add_argument("--status", action="store_true", help="Print spend ledger status and exit")
    parser.add_argument("--yes", action="store_true",
                         help="Confirm you have explicit human sign-off to spend money on this run. "
                              "Without this flag, oracle only prints the cost estimate and exits.")
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--max-tool-calls", type=int, default=MAX_TOOL_CALLS)
    args = parser.parse_args()

    status = check_budget()
    print(format_budget_report(status))

    if args.status:
        return 0

    if not args.report:
        parser.error("--report is required unless --status is given")

    report_path = Path(args.report)
    if not report_path.is_file():
        print(f"Error: report file not found: {report_path}", file=sys.stderr)
        return 1
    with open(report_path, "r", encoding="utf-8") as f:
        report_data = json.load(f)

    est_low = estimate_cost(3_000, 800, args.model)
    est_high = estimate_cost(30_000, 3_000, args.model)
    print()
    print(f"Estimated cost for this run: ${est_low:.3f}–${est_high:.3f} "
          f"(model={args.model}, up to {args.max_tool_calls} file reads)")

    if not args.yes:
        print()
        print("⚠️  DRY RUN — no API call made. This would spend real money from the")
        print("    account owner's personal Anthropic budget. Re-run with --yes only")
        print("    after the account owner has explicitly approved the estimate above.")
        return 0

    if not status.ok:
        print(f"\n⛔ Refusing to run: {status.reason}", file=sys.stderr)
        return 1

    print("\nRunning diagnosis...\n")
    try:
        result = diagnose(report_data, confirmed=True, model=args.model, max_tool_calls=args.max_tool_calls)
    except OracleError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(result.text)
    print()
    print(f"--- cost: ${result.cost_usd:.4f}  "
          f"(in={result.input_tokens} out={result.output_tokens} tool_calls={result.tool_calls_used}, "
          f"stopped: {result.stopped_reason}) ---")
    print(format_budget_report(check_budget()))
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
