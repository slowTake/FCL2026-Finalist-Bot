---
name: modular-code
description: Modular design: decouples business logic, state, and interfaces into single-responsibility components and clear boundaries.
---

# Modular Code

A skill for enforcing **decoupled** architecture. Modularity ensures that parts of the codebase can be modified, tested, or deleted in isolation.

Every file created or refactored under this skill must satisfy the **decoupling** criteria before completion.

## Bounded Components

Define clear boundaries for every unit of code.

- **Single Responsibility**: Restrict each module to a single logical concern. Co-locate related logic, definitions, and errors within the same module, rather than scattering them.
- **Explicit Interfaces**: Specify exact input and output interfaces for every component. Pass configuration and dependencies explicitly via parameters or constructors to isolate component state.
- **Directional Dependencies**: Draw a clear, one-way dependency graph. Keep business logic independent of user interface or infrastructure details. Write pure modules with explicit input/output flows.

## Structural Limits

Enforce physical constraints to keep code readable and maintainable.

- **File Limits**: Limit files to a maximum of 300 lines. When a file exceeds this or handles multiple duties, split it into smaller, well-named files.
- **Single Source of Truth**: Keep each logic branch in a single source of truth. Reuse helper functions and utilities across modules rather than duplicating behavior.

## Completion Criterion

The work is complete only when:
1. Every new or modified file conforms to the structural and responsibility boundaries.
2. The dependency graph is strictly unidirectional, showing a clear hierarchy from entry points to core logic.
