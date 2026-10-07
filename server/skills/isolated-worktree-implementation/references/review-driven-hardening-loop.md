# Review-driven hardening loop

Use when a worktree implementation pass receives parallel review feedback and needs a bounded repair cycle.

## Pattern
1. Freeze the real findings into a brief in the same worktree.
   - accepted findings only
   - explicit non-goals
   - exact verification commands
   - release gates / failure-injection checks
2. Serialize fixes: one writer, one checkout. Never let two fixers touch the same files concurrently.
3. Treat untracked source/test/migration files as excluded from the diff unless they are intentionally part of the release; inventory them separately.
4. Re-run the project’s real tests for the repaired surface, then re-review once against the changed scope.
5. Stop after the second review round. If P1+ remains, report unresolved instead of looping.

## Guardrails
- Do not trust runner self-reports alone; verify file contents, exit codes, and `git status`.
- Source-only migrations are not “applied” until the target environment is verified separately.
- For release slices that touch multiple surfaces, keep the repair brief narrow so reviewers can validate boundaries cleanly.
