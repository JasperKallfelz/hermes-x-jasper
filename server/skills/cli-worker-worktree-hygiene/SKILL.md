---
name: cli-worker-worktree-hygiene
description: Use when headless CLI workers dirty Git worktrees.
version: 1.0.0
platforms: [macos, linux]
metadata:
  hermes:
    tags: [cli, workers, worktrees, hygiene, verification, automation]
    related_skills: [claude-cli, hybrid-coding-orchestration, agent-briefing]
---

# CLI Worker Worktree Hygiene

Use this skill when an external CLI worker or bridge runs inside an isolated Git worktree and the checkout must stay clean after the run.

## Core rule

Treat the worker process as a separate environment from the repository.
Anything that would leave instrumentation, cache, coverage, or log artifacts inside the worktree should be redirected elsewhere or disabled for the worker process.

## Start / run checklist

1. Start from a clean source checkout or known-good fixture.
2. Launch the worker in an isolated worktree or disposable repo.
3. Keep the worker invocation pinned and explicit; do not add configurable script overrides just to work around artifact leakage.
4. If the worker emits profile or coverage files, redirect them outside the worktree or to a null sink before changing repository code.
5. Prefer a real smoke test that exercises a start turn and at least one follow-up turn in the same session.
6. Verify the worker's own success result and then verify the worktree with `git status --porcelain`.

## Pitfalls

- Do not trust the worker report alone; confirm the checkout is still clean.
- Do not assume a transient artifact is harmless just because the task itself succeeded.
- Do not turn a temporary environment quirk into a broad claim that the whole CLI is broken.
- Do not skip the follow-up turn; the clean-worktree check matters most when state is carried across turns.

## Verification

For a smoke test, look for:

- expected worker reply on the initial turn,
- expected reply on the follow-up turn,
- empty `git status --porcelain` output in the worktree,
- optional close/forget step reporting the session as closed.

## Reference files

- `references/clean-worktree-smoke.md` — the profile-artifact workaround and a reproducible smoke-test recipe.
