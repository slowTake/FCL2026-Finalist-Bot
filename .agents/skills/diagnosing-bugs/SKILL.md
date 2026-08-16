---
name: diagnosing-bugs
description: Diagnose: reproduces, minimizes, and fixes hard bugs or performance regressions via a disciplined scientific loop.
---

# Diagnosing Bugs

A systematic discipline for identifying and fixing hard bugs and performance regressions. Progress through all phases sequentially, justifying any exceptions explicitly.

## Phase 1 — Build a Feedback Loop

A **tight** pass/fail feedback loop is the foundation of debugging. Prioritize establishing this feedback loop before conducting further code analysis.

### Repro Strategies
Select the most direct method to drive the bug code path:
1. **Failing test**: A unit, integration, or e2e test at the closest seam.
2. **HTTP script**: A curl command or script driving a running local server.
3. **CLI invocation**: A command executing the target code path with an input fixture.
4. **Headless script**: A browser script (Playwright/Puppeteer) asserting on DOM or console errors.
5. **Replayed trace**: Replaying a saved network request or event log in isolation.
6. **Throwaway harness**: A script running a minimal subset of the system to call the target function.
7. **Property loop**: Running random inputs to trigger a flake or boundary condition.
8. **Bisection harness**: Automating execution across git commits to run a `git bisect`.

### Loop Optimization
Refine the feedback loop to make it:
- **Fast**: Executing in seconds to enable rapid iteration.
- **Deterministic**: Pinning time, seeding RNGs, and freezing network states to eliminate flakiness.
- **Sharper**: Asserting on the exact symptom rather than a generic pass/fail.

### Handling Non-Reproduction
For non-deterministic bugs, focus on increasing the reproduction rate (e.g., parallelizing executions or adding timing stress). If a loop cannot be built, pause and request diagnostic assets (HAR files, logs, core dumps) or permission to add temporary instrumentation.

### Completion Criterion
Phase 1 is complete only when a **tight**, **red-capable** command is established. Paste the exact command and its failing output. The command must be:
- **Red-capable**: Drives the bug path and asserts the user's exact symptom.
- **Deterministic**: Returns the same verdict every run.
- **Fast**: Runs in seconds.
- **Agent-runnable**: Executes unattended.

---

## Phase 2 — Reproduce & Minimize

Run the feedback loop, verify the red signal, and isolate the bug.

### Verification
Confirm that:
- The failure matches the user's described symptom (wrong output, error code, delay).
- The reproduction is stable across multiple executions.

### Minimization
Shrink the scenario to the smallest set of inputs, configurations, or steps that still triggers the failure. Remove elements one at a time, running the feedback loop after each change.

### Completion Criterion
Phase 2 is complete only when removing any further element from the repro scenario causes the feedback loop to go green.

---

## Phase 3 — Hypothesize

Generate **3–5 ranked hypotheses** before testing any changes. This prevents anchoring on the first plausible idea.

### Structure
Phrase every hypothesis to be falsifiable:
> *"If <cause X> is true, then <changing Y> will make the bug disappear / Z will make it worse."*

Present the ranked list to the user for feedback before proceeding. If the user is unavailable, proceed with testing according to your ranking.

### Completion Criterion
Phase 3 is complete only when a list of falsifiable hypotheses is documented and presented.

---

## Phase 4 — Instrument & Test

Verify or falsify the hypotheses by changing one variable at a time.

### Instrumentation Rules
- **Probes**: Map every print, log, or breakpoint directly to a prediction from Phase 3.
- **Tools**: Use debuggers or REPL inspections where supported. 
- **Targeted Logs**: Place logs only at critical decision boundaries. Tag every debug log with a unique prefix (e.g., `[DEBUG-a4f2]`) to facilitate cleanup.
- **Performance Baselines**: For performance regressions, establish baseline measurements (`performance.now()`, query plans) before making modifications.

### Completion Criterion
Phase 4 is complete only when the root cause is isolated and verified against a specific hypothesis prediction.

---

## Phase 5 — Fix & Regression Test

Lock down the fix using tests at the correct seam.

### Process
1. **Seam Verification**: Ensure the test seam exercises the real bug pattern. If no correct seam exists, document this limitation.
2. **Failing Test**: Turn the minimized repro scenario into a failing regression test.
3. **Implement Fix**: Apply the fix and verify the regression test passes (goes green).
4. **Integration Check**: Re-run the Phase 1 loop against the original (un-minimized) scenario to confirm it is resolved.

### Completion Criterion
Phase 5 is complete only when the regression test passes and the original repro scenario runs successfully.

---

## Phase 6 — Cleanup & Post-Mortem

Prune all temporary files and log lines.

### Steps
- Remove all `[DEBUG-...]` logs from the codebase.
- Delete temporary test fixtures and scratch harnesses.
- Record the root cause and correct hypothesis in the commit message.
- Identify preventative measures (e.g., architectural decoupling, missing seams) and present recommendations to the user.

### Completion Criterion
Phase 6 is complete only when all debug instrumentation is removed and the codebase is verified clean.