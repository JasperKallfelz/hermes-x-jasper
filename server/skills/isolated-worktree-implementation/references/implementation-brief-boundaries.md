# Implementation brief boundaries

Use this reference when a substantial feature must be implemented in an isolated worktree and the task touches multiple deployment surfaces.

## Pattern that worked well

- Verify the current live surfaces first, then split the work into two briefs if needed:
  - an **audit brief** for current prod/preview state
  - an **implementation brief** for code changes
- Freeze the implementation brief in the worktree before launching a worker.
- The brief should state, in plain terms:
  - exact worktree path
  - explicit in-scope and out-of-scope items
  - deployment boundary, e.g. preview-only or localhost-only
  - forbidden edits, especially production checkout, launchd/service config, secrets, schema/RLS, and public routing/firewall rules
  - required verification commands and artifact paths
- Keep the worker prompt self-contained so it can run without chat history.
- Require the worker to report changed files and verification commands, then verify them yourself from the worktree.

## Good brief content

- "Do not change production"
- "Bind only to 127.0.0.1"
- "No secrets in logs or responses"
- "Do not alter mobile rendering unless the manual action path is broken"
- "Do not broaden the scope to related features unless explicitly requested"

## Anti-patterns

- Letting the worker infer environment boundaries from repository defaults
- Mixing audit notes and implementation instructions in one vague prompt
- Omitting the exact verification commands
- Omitting the exact artifact or file paths to inspect after the worker finishes
