---
name: code-review
description: Review: evaluates code changes against project standards and initial specifications before commit.
---

# Code Review

A skill for conducting a **review** of diffs before committing. The review verifies standards compliance and functional correctness.

Every code change must satisfy the review criteria before being merged or committed.

## Review Axes

Evaluate changes across two distinct axes:

- **Spec Compliance**: Verify that the diff completely and faithfully implements the originating issue, specification, or PRD. Confirm that all edge cases defined in the requirements are handled.
- **Coding Standards**: Verify that the code conforms to project guidelines (e.g. file size limits, naming conventions). Identify potential code smells (e.g., duplicate logic, excessive nesting, tight coupling) and propose positive refactorings.

## Review Process

1. **Diff Generation**: Retrieve the complete git diff of all modifications.
2. **Analysis**: Inspect the diff line-by-line against both Spec and Standards axes.
3. **Report**: Present findings as a structured markdown report, listing clear, actionable recommendations.

## Completion Criterion

The review is complete only when:
1. The git diff has been fully evaluated against both Spec and Standards axes.
2. An actionable summary report of findings is presented to the user.
