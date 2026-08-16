# oracle

Agentic AI root-cause diagnosis for `fcv_gauntlet` findings.

`fcv_gauntlet` already produces structured diagnostics — 12 detectors, causal
event chains, cost-weighted "top leaks" — but `reporting.py`'s "Automated
Actionable Fix Suggestions" section is a hardcoded `if counts.get(...) > N`
template pointing at canned file paths. `oracle` replaces that guesswork with
an actual model call: it hands the causal chains and cost leaks to Claude,
gives it a scoped, read-only `read_file` tool over the project's own bot
source, and gets back a diagnosis grounded in the real code.

> ⚠️ **This spends real money.** It calls the Anthropic API using a personal
> API key and budget. It is not free, it is not part of your Claude Code
> subscription, and it does not run automatically as part of a gauntlet —
> **see `AGENT.md` before wiring this into anything automated or invoking it
> from an agent.**

## Budget guardrails

- **Daily cap: $1.50.** **Monthly cap: $23.00.** Both are hard stops, checked
  before every run.
- Every call's *actual billed usage* (from `response.usage`, not an estimate)
  is logged to `.spend_ledger.json` next to this file (gitignored — it's a
  personal spend record, not project state).
- A single run is bounded: `max_tokens=4096`, at most 6 tool calls (file
  reads capped at 20,000 chars each). Typical cost per run is **$0.05–$0.25**
  on `claude-sonnet-5`.
- The CLI **never calls the API without `--yes`**. Without it, it prints the
  current spend, an estimated cost range for the run, and exits — a dry run.
- The library function `diagnose()` **never calls the API without
  `confirmed=True`** — it raises `OracleConfirmationRequired` otherwise.

Check spend anytime without running anything:

```sh
python -m fcv_gauntlet.oracle.oracle --status
```

## Setup

```sh
pip install anthropic
export ANTHROPIC_API_KEY=sk-ant-...   # your personal key
```

## CLI usage

```sh
# 1. Generate a JSON report from a gauntlet run or single-replay analysis
#    (see fcv_gauntlet/runner.py — json_output_path param)

# 2. Dry run — shows cost estimate, does NOT call the API
python -m fcv_gauntlet.oracle.oracle --report /tmp/report.json

# 3. Real run — only after a human has approved the estimate from step 2
python -m fcv_gauntlet.oracle.oracle --report /tmp/report.json --yes
```

## Library usage

```python
from fcv_gauntlet.oracle import diagnose, check_budget, OracleError

status = check_budget()
if not status.ok:
    print(status.reason)
else:
    # confirmed=True only after a human has explicitly approved the cost —
    # see AGENT.md
    result = diagnose(report_data, confirmed=True)
    print(result.text)
    print(f"cost: ${result.cost_usd:.4f}")
```

## Why not wire this straight into `reporting.py`?

On purpose. Every other part of `fcv_gauntlet` is free to run — it's local
compute. This is the one component that costs money on someone's personal
budget, so it stays an explicit, opt-in step rather than something that fires
silently on every gauntlet run. See `AGENT.md`.

## Files

- `oracle.py` — spend ledger, budget checks, the agentic diagnosis loop, CLI.
- `.spend_ledger.json` — gitignored, created on first run. One JSON line's
  worth of data per API call: date, model, token counts, actual cost.
- `AGENT.md` — rules for when and how an AI agent should invoke this tool.
