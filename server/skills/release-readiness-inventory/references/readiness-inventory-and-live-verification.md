# Readiness inventory and live verification

Notes from cross-session release/readiness work.

## Practical pattern

- Use `session_search` to recover the latest relevant session and the anchor that introduced the work.
- Scroll around that anchor to reconstruct: goal → what was already verified → what remains open.
- Cross-check the session narrative against the live repo/worktree/process state before declaring anything done.
- Keep canonical vs reference sources explicit. A detached worktree or sibling repo may be a source of truth for a specific slice, but it is not automatically the shipping path.

## Verification buckets

When inventorying unfinished work, separate:

- merged and live in canonical path
- verified in a side worktree but not merged
- in progress
- blocked
- intentionally deferred
- untracked release files or other supporting artifacts

## Live-state checks that mattered in practice

- `git status --short --branch` to distinguish branch, staged, unstaged, and untracked state.
- `git diff --stat` / `git diff --check` to confirm the shape of the actual change set.
- `ps` / `pgrep` / process handles to verify whether a background task is still running.
- A task list is only trustworthy after the underlying effect has been verified, not after the intent to do it.

## Reporting style

For readiness questions, lead with the gap:

- what is verified
- what is still missing
- what would prove completion
- whether the answer depends on a non-canonical reference worktree
