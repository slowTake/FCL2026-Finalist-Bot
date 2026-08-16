---
name: simplicity
description: Simple design: implements the minimal direct solution for the immediate requirement, prioritizing readability and deletion.
---

# Simplicity

A skill for enforcing **minimal** implementations. Simplicity ensures that code is easy to read, easy to understand, and easy to delete.

Every implementation or refactoring under this skill must satisfy the **minimal** design criteria before completion.

## Concrete Implementation

Implement only the active requirements.

- **Immediate Utility**: Solve the immediate problem directly. Focus strictly on active requirements, utilizing the most direct, standard patterns.
- **Native Capabilities**: Prefer built-in language capabilities, native functions, and standard libraries to external packages.
- **Explicit Control Flow**: Write flat, linear, and explicit control flows. Favor readability and transparency over complex nesting, indirection, or clever one-liners.

## Code Pruning

Actively reduce code volume and complexity.

- **Dead Code Removal**: Prune dead code, unused functions, obsolete helpers, and unused imports immediately.
- **Minimal Footprint**: Keep files, functions, and variables as small and direct as possible. Write code that is easy to delete when requirements change.

## Completion Criterion

The work is complete only when:
1. The immediate problem is solved using the minimum necessary logic and code footprint.
2. All temporary code, unused imports, and dead code have been pruned from the workspace.
