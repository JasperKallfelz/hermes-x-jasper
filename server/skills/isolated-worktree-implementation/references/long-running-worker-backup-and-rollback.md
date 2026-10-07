# Long-running worker backup and rollback notes

Session pattern for broad autonomous coding runs:
- Create an explicit restore point or backup before the worker starts.
- Keep the implementation isolated in a dedicated worktree.
- Freeze the brief in a file inside that worktree so the worker does not depend on chat history.
- Record the rollback path and the artifact/report location in the brief.
- Launch the worker with an explicit workdir and save logs to a stable file path.
- Verify liveness and completion separately when the worker runs in the background.
- After exit, inspect the actual log/report and the checkout diff before reporting success.

Observed in the Pi runtime orchestration session:
- The brief lived in `.hermes/plans/PI_RUNTIME_EXECUTION_BRIEF.md` inside the worktree.
- A pre-run snapshot and a Git bundle were created before the long coding pass.
- The worker was launched in the background, and the final report was expected from a dedicated output file rather than the transient terminal stream.
