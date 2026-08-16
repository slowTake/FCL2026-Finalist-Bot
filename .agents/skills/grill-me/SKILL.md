---
name: grill-me
description: Grill: interviews the user relentlessly to stress-test and sharpen a plan or design before execution.
---

# Grill Me

A skill for **grilling** the user to resolve design decisions, uncover edge cases, and establish a shared plan.

Every grilling session must be conducted step-by-step and satisfy the completion criteria before execution begins.

## Interactive Interview

Structure the interview to be focused and manageable.

- **Single Questions**: Ask questions one at a time. Wait for feedback on each response before presenting the next question.
- **Recommendations**: For every question asked, present a clear, recommended answer based on best practices.
- **Fact Finding**: Explore the codebase to retrieve concrete facts rather than asking the user. Limit questions to design decisions, trade-offs, and project goals.
- **Constructive Challenge**: Challenge assumptions and ask for clarification on ambiguous plans.

## Completion Criterion

The grilling session is complete only when:
1. Every branch of the design tree has been explored, and all dependencies between decisions are resolved.
2. The user has explicitly confirmed that a shared understanding has been reached.
3. The final agreed-upon plan is summarized clearly before execution.