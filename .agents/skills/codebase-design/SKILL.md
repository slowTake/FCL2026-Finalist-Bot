---
name: codebase-design
description: Design: guides codebase architecture using deep modules, clear seams, and minimal public interfaces.
---

# Codebase Design

A skill for designing software architecture. Codebase design focuses on placing clean **seams** and creating deep modules that encapsulate complexity.

## Design Principles

Apply these architectural principles when creating or refactoring modules:

- **Deep Modules**: Encapsulate extensive behavior and logic behind a small, simple public interface. Favor thick implementations with thin API surfaces.
- **Stable Seams**: Place public interfaces at natural, stable boundaries. Ensure changes to internal logic do not require changes to calling code.
- **Co-Located Complexity**: Group related data, operations, and error handlers within the same module rather than exposing internals or scattering logic across files.
- **Explicit Context**: Enforce boundaries by passing all dependencies and context explicitly through the interface, avoiding reliance on implicit state.

## Completion Criterion

The architectural design is complete only when:
1. The public interface of the module is verified to be minimal relative to its encapsulated capability.
2. The module boundary is explicitly defined, showing zero leakage of internal structures.
