---
name: tdd
description: TDD: drives development and debugging via a strict test-first, red-green loop at verified seams.
---

# Test-Driven Development (TDD)

A skill for executing the **red-green** development loop. TDD ensures codebase predictability by verifying behavior through stable interfaces.

## Steps

Perform these steps in order for every vertical slice:

1. **Verify the Seam**: Identify the public interface boundary where the behavior will be observed. Present this seam to the user and obtain explicit confirmation before writing code.
   - *Completion Criterion*: The seam is confirmed by the user.
2. **Write a Failing Test (Red)**: Write a single test at the confirmed seam that verifies the new capability or reproduces the bug. Run the test suite to confirm it fails.
   - *Completion Criterion*: The test runs and fails with the expected assertion error.
3. **Pass the Test (Green)**: Write only the minimal implementation code required to make the failing test pass. Avoid adding speculative features or future-proofing.
   - *Completion Criterion*: The test suite passes successfully.

## Quality Standards

Consult these standards during every loop cycle. Disclosed details live in [tests.md](tests.md) and [mocking.md](mocking.md).

- **Decoupled**: Verify behavior exclusively through the public API. Decoupled tests ignore internal structure and survive refactoring.
- **Independent Assertions**: Verify results against known-good literals, worked examples, or specification values rather than re-computing the implementation's logic.
- **Vertical Slices**: Code one test and its matching implementation at a time. Each cycle acts as a **tracer bullet** to guide the next slice.
- **System-Boundary Mocking**: Mock exclusively at system boundaries (e.g., external APIs, time/randomness, network). Pass dependencies explicitly using injection.

## Completion Criterion

The TDD cycle for a feature or bug fix is complete only when:
1. All planned capabilities or bug fixes are verified by passing tests at the agreed seams.
2. The codebase contains no speculative or untested implementation code.