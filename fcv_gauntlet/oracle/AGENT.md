# AGENT.md — rules for invoking `oracle`

This file is for any AI agent (Claude Code or otherwise) working in this repo.
If you are about to run `oracle`, read this first.

## The one rule that matters

**`oracle` spends real money from the account owner's personal Anthropic API
budget. Never run it — CLI or library — without first telling the user what
it will cost and getting their explicit go-ahead in the conversation.**

This applies every time, not just the first time. A user approving one run
is not standing approval for future runs — ask again each time you're about
to invoke it, the same way you'd confirm before any other outward-facing,
costs-money action.

Concretely, before invoking `oracle`, you must:

1. **State the estimated cost out loud to the user**, e.g.:
   > Running oracle on this report will cost approximately $0.05–$0.25
   > (model: claude-sonnet-5, up to 6 file reads). Want me to proceed?
2. **Wait for an explicit yes** — "yes", "go ahead", "do it", a thumbs up in
   response to that specific question. Prior general permission to "build
   the tool" or "use the API" is not the same as approval to spend on *this*
   run. If the user's intent is ambiguous, ask.
3. **Only then** either:
   - run the CLI with `--yes`, or
   - call `diagnose(report_data, confirmed=True, ...)` in code.

Never pass `--yes` / `confirmed=True` speculatively "to see what it says," and
never wrap this tool in a script or hook that calls it without a human in the
loop for that specific invocation. If you're building automation that *might*
call `oracle`, the automation must itself pause and ask before doing so — the
confirmation requirement does not disappear just because the call is one
layer removed from a chat turn.

## Why this exists

`oracle` is the one part of `fcv_gauntlet` that costs money — everything else
(detectors, causal chains, replay analysis) is local compute. The user is
running this against a tight personal budget with a hard end-of-month
deadline, shared with other work. An agent quietly burning that budget
without asking is the failure mode this file exists to prevent — this keeps
teammates informed about API spend before it happens, not after.

## Checking spend without asking permission

You do **not** need to ask before *checking* the budget — only before
*spending against it*. These are always safe to run unprompted:

```sh
python -m fcv_gauntlet.oracle.oracle --status
```

```python
from fcv_gauntlet.oracle import check_budget
check_budget()  # read-only, costs nothing
```

Also safe: running the CLI **without** `--yes`. It prints the spend ledger
and a cost estimate and exits — no API call is made. Use this freely to show
the user what a run would cost before asking them to approve it.

## If the budget is already exhausted

`check_budget()` / the CLI banner will say so (`ok: False`, with a reason).
Tell the user plainly — "the daily/monthly oracle budget is used up" — don't
try to work around it (lower `max_tool_calls`, switch to a cheaper model,
etc.) without them explicitly asking you to attempt that.

## Summary checklist

- [ ] Told the user the estimated cost for *this specific run*
- [ ] Got an explicit yes for *this specific run*
- [ ] Only then set `--yes` / `confirmed=True`
- [ ] Never automated past this gate without a human still in the loop
