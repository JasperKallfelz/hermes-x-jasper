# Clean worktree smoke test

Session finding:
- A real headless Claude bridge test succeeded for both the initial turn and the follow-up turn, but the worker left an untracked `default.profraw` LLVM profile file in the isolated worktree.

Reliable fix:
- Set `LLVM_PROFILE_FILE=/dev/null` for the worker process when the bridge must keep the worktree clean.
- Keep the worker invocation pinned; do not add a configurable script override just to avoid the artifact.

Verification pattern:
1. Start a fresh repo or disposable fixture.
2. Launch the bridge and exercise a read-only initial task.
3. Send a follow-up in the same named session.
4. Check `git status --porcelain` in the worktree stays empty.
5. If the bridge supports close/forget, call it and confirm the session is closed.

Observed result after the fix:
- The smoke test returned the expected reply.
- The worktree remained clean (`0` lines from `git status --porcelain`).
- The session closed cleanly after the final step.
