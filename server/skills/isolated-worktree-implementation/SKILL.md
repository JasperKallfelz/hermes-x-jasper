---
name: isolated-worktree-implementation
description: Use when implementing big repo changes in a worktree.
version: 1.0.1
platforms: [macos, linux, windows]
metadata:
  hermes:
    tags: [Coding, Worktrees, Verification, Checkpoints, Artifacts]
---

# Isolated Worktree Implementation

Use this for substantial code changes, multi-file refactors, feature spikes that must be verified, or any implementation where you want a clean, reproducible run and a real artifact at the end.

## Core idea

Keep the main checkout untouched, isolate the implementation in a dedicated worktree, freeze the task in a prompt file, and verify the resulting code and artifacts directly. See `references/artifact-cleanup.md` for the cleanup pattern when verification generates tracked or untracked build outputs, and `references/implementation-brief-template.md` for a frozen brief starter.

For Swift/Xcode work, see `references/ios-xcode-verification.md` for the strict TDD + simulator-destination verification pattern.

## Workflow

1. **Create an isolated worktree**
   - Use a fresh Git worktree or branch for the task.
   - Do not run competing writers in the same checkout.
   - Keep the main checkout as the reference state.

2. **Checkpoint before the big change**
   - Save a checkpoint or equivalent restore point before major edits.
   - Use it when the task is broad enough that rollback is valuable.

3. **Write a self-contained prompt file**
   - Put the acceptance criteria, constraints, and verification commands into a file inside the worktree.
   - Include exact artifact paths and explicit non-goals.
   - Prefer this over a long inline shell string for big jobs.
   - For tasks that touch more than one surface, freeze the boundary in the brief before coding. See `references/implementation-brief-boundaries.md` and `references/implementation-brief-template.md`.
   - Keep the brief in the worktree itself so the worker does not depend on chat history.
   - For long autonomous runs, add the rollback path and any pre-run backup/checkpoint location to the brief before starting.

4. **Run the implementation in the isolated worktree**
   - Launch the coding runner from that worktree.
   - Pass the prompt file and the worktree path explicitly.
   - Keep the task bounded and deterministic.
   - If a review pass finds P1/P0 issues, freeze a repair brief in the same worktree before editing and keep fixes serial. See `references/review-driven-hardening-loop.md` for the compact repair/re-review loop.

5. **Verify with real evidence**
   - Run the repo's actual tests in the worktree.
   - Inspect the produced files, reports, or media directly.
   - Confirm the artifact exists and is decodable/usable, not just that a command returned success.
   - For visual/video artifacts, independently decode the output and sample at least one frame (for example with `ffprobe`/`ffmpeg`) before claiming the demo is good.
   - If a runner or subagent says "done", still verify the exact artifact path yourself before relaying success.
   - For cloud preview Supabase work, probe the live target read-only first, reconcile with a forward-only migration, then repair the remote migration ledger only after the schema is independently verified.

6. **Report exact results**
   - State the worktree path, checkpoint tag, test command, and artifact paths.
   - Summarize what changed without implying success that wasn't verified.

## When this pattern is especially useful

- Large feature work with multiple files
- Debugging paths where you want a clean before/after
- Experimental implementation with a real demo artifact
- Tasks where a runner or agent needs a very explicit contract

## Pitfalls

- Do not trust a runner self-summary alone.
- Do not forget to re-run the repository's real tests.
- Do not overwrite the main checkout during experiments.
- Do not omit the artifact path from the prompt; it makes verification harder.
- Do not let multiple writers edit the same worktree concurrently.
- If the verifier runs in a read-only container, keep caches and bytecode off the workspace (tmpfs or disabled), and keep trusted tool imports separate from workspace-import plumbing.
- If verification rewrites tracked build artifacts or creates untracked outputs, restore or quarantine them before handing the worktree back so the remaining diff reflects only intended source changes.

## Reference

- See `references/quality-first-worktree-foundations.md` for a compact recipe and a concrete command pattern.
- See `references/artifact-cleanup.md` for the cleanup pattern when builds leave tracked or untracked outputs behind.
- See `references/cloud-preview-reconciliation.md` for read-only probe → forward-only migration → ledger-repair workflows on cloud preview Supabase targets.
- See `references/demo-artifact-verification.md` for a short checklist when the deliverable is a video/image/audio artifact.
- See `references/long-running-worker-backup-and-rollback.md` for the pre-run snapshot, rollback-path, and background-worker verification pattern.
- See `references/read-only-verifier-sandboxes.md` for cache/import handling notes from read-only Docker verification runs.
