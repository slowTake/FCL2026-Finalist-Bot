---
name: git
description: Git: manages branches, staging, committing, pushing, and creating pull requests predictably following repo standards.
---

# Git Management

A skill for executing **git** operations cleanly. Safe version control ensures codebase history remains clear and traceable.

## Steps

Perform these steps in order for every code commit:

1. **Verify the Branch**: Ensure the current local branch matches the target feature or issue. Create new feature branches using hyphenated, lowercase names prefixed with the task type (e.g., `feature/login-page`, `bugfix/cart-error`).
   - *Completion Criterion*: The active branch is confirmed correct.
2. **Stage Specific Changes**: Review the status of modified files. Stage only the files that are directly related to the active requirement or ticket. Keep unrelated or scratch files unstaged.
   - *Completion Criterion*: `git status` shows only target files in the staging area.
3. **Commit with Context**: Write commit messages that start with a short, imperative summary (e.g., `feat: add user authentication`). Follow with a body detailing the *what* and the *why* of the changes, referencing ticket numbers.
   - *Completion Criterion*: The commit is recorded locally.
4. **Synchronize and Create Pull Request**: Fetch recent remote changes, rebase the local branch if modifications occurred, push the branch to the remote repository, and create a pull request (e.g., using `gh pr create` or the hosting service's interface). Never merge or submit changes directly to the main or default branch.
   - *Completion Criterion*: The branch is pushed and a pull request is successfully opened.

## Quality Standards

- **Atomic Commits**: Group logically related edits into a single commit. Keep commits small and focused on a single change.
- **Clean Staging**: Inspect diffs before staging to ensure only production-ready code is included. Verify that the staging area contains zero temporary log lines, debug instrumentation, or scratch scripts.
- **Rebase over Merge**: Keep branch history linear by utilizing `git rebase` when updating feature branches with main changes.
- **Pull Requests over Direct Push**: All changes must be submitted via a pull request. Never submit or push directly to the target/production branch.

## Completion Criterion

The git operation is complete only when:
1. All target modifications are committed on the correct feature branch.
2. The local workspace has no uncommitted changes in tracked files.
3. The branch is successfully pushed to the remote repository and a corresponding pull request has been created.