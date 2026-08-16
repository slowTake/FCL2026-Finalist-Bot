---
name: handoff
description: Handoff: compiles the current conversation, logic, and pending steps into a stateful file for the next session.
disable-model-invocation: true
---

# Handoff

A skill for generating a **handoff** state file. The handoff ensures that context, progress, and next steps are preserved across agent sessions.

## Steps

Perform these steps in order when requested to generate a handoff:

1. **Verify State**: Gather the list of active requirements, completed tasks, and currently pending items.
   - *Completion Criterion*: A checklist of done vs. pending tasks is established.
2. **Draft the Document**: Create a `handoff.md` file in the root directory (or in the artifacts directory if specified). Populate it using the sections below.
   - *Completion Criterion*: The file contains all required sections with complete details.
3. **Present to User**: Highlight the next immediate steps in the chat and link to the file.
   - *Completion Criterion*: Clickable file link is provided to the user.

## Handoff Structure

Structure the handoff file with these headings:

- **Active Objective**: The primary goal of the session.
- **Current Status**: A summary of completed work and verified changes.
- **Next Immediate Steps**: A concrete list of tracer-bullet tasks for the next session.
- **Key Files & Symbols**: Clickable links to primary files, classes, or interfaces modified or created.
- **Design Decisions**: Rationale for design paths taken, including any resolved trade-offs.

## Completion Criterion

The handoff is complete only when:
1. A structured `handoff.md` file is written to the workspace and contains no placeholders.
2. The next immediate steps are clearly prioritized for the incoming session.