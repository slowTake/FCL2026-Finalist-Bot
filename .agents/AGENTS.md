# General Repository Rules & Guidelines

These rules steer all development and communication. Reference the dedicated project skills under `.agents/skills/` to enforce these standards.

## 1. Code Design

- **Simplicity**: Solve the immediate problem directly. Follow the **minimal** design standards in the `simplicity` skill.
- **Modularity**: Design single-responsibility components with clear boundaries. Follow the **decoupled** standards in the `modular-code` skill.
- **Maintainability**: Prioritize readability and clear naming. Keep functions short and highly cohesive.

## 2. Development Workflow

- **Incremental Steps**: Build and verify code in small, incremental edits. Use the **red-green** loop in the `tdd` skill for all feature work.
- **Defensive Design**: Validate inputs and handle edge cases gracefully, surfacing errors explicitly.
- **Debugging**: When reproducing or fixing errors, follow the systematic scientific loop in the `diagnosing-bugs` skill.
- **Clarification**: Ask the user for clarification when requirements are ambiguous. Discuss design trade-offs and alternative approaches before coding.

## 3. Communication

- **Links**: Reference file paths and code symbols using standard clickable markdown links (e.g. `[filename](file:///path/to/file)`).
