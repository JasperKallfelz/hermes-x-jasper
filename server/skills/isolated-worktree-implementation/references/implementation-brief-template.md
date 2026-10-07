# Implementation brief template

Use this reference when a substantial feature is implemented in an isolated worktree.

## What to put in the frozen brief

- Exact worktree path
- In-scope items
- Out-of-scope items
- Forbidden edits
- Deployment / runtime boundary
- Acceptance criteria
- Required verification commands
- Exact artifact paths to inspect

## Pattern that worked

1. Create the worktree first.
2. Write `IMPLEMENTATION_BRIEF.md` inside that worktree.
3. Keep the brief self-contained so the worker does not need chat history.
4. Launch the worker only after the brief is frozen.
5. Verify real commands and inspect the resulting artifact or diff yourself.

## Notes

- For features that touch API, UI, tests, and data shape at once, the brief should explicitly freeze the boundary between overview/summary views and detailed views.
- Keep the main checkout untouched while the worktree is active.
