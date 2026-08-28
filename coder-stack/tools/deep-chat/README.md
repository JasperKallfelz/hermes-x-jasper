# Deep Chat

This directory is the canonical source for the local Deep Chat bridge and its
Claude worker. The bridge creates one named Git branch/worktree and maintains a
small state document; the worker owns the resumable Claude session registry.
Neither program commits, merges, rebases, pushes, deploys, deletes worktrees, or
forgets workers automatically.

Deep Chat is an explicit opt-in tool. It is never invoked as a fallback by the
Codex-only `hermes-coder` or `hermes-coder-flow` commands. It uses the local
Claude subscription/OAuth wrapper, not a direct API key or direct API billing.

## Requirements and commands

- Python 3.11+ on `PATH` (or `HERMES_DEEP_CHAT_PYTHON`)
- `jq`, Git, and the authenticated `~/.local/bin/claude-subscription` wrapper
- the maintained worker beside the bridge, or at `~/.hermes/bin/claude_worker.py`

```console
hermes-deep-chat start /path/to/repo my-chat -- "Initial task"
hermes-deep-chat start /path/to/repo future-chat --schema-version 2 -- "Initial task"
hermes-deep-chat send my-chat -- "Follow-up"
hermes-deep-chat status my-chat
hermes-deep-chat reconcile my-chat
hermes-deep-chat list
hermes-deep-chat close my-chat
```

A real `start` first runs the non-inference subscription-wrapper command
`auth status`, discards all of its output, and fails before any lock, branch,
worktree, or state mutation when auth is unavailable. It never logs in. Dry-run
is planning-only and does not require auth.

Schema 1 remains the default and stays readable without migration. Schema 2 is
opt-in at creation and records only monotonic local bridge turn IDs, operation,
attempt/success timestamps, outcome, and the worker session ID returned after a
successful turn. Task and model-result text are never stored in bridge state or
the worker registry. Unknown schemas are observable but fail closed for writes.

`status` preserves persisted fields at the top level and adds a fresh,
nonpersisted `observed` object. It reads local Git and registry state only; it
does not contact Claude or change locks/state. `reconcile` is also read-only and
returns a classification plus human recovery options. It never repairs,
continues, creates, forgets, resets, cleans, or removes anything. In particular,
a failed chat with a missing worker and dirty worktree is classified
`failed_worker_missing_dirty_worktree` and requires human instruction.

## Test and deploy

Tests create isolated repositories and HOME directories. They select a worker
test double only in a disposable bridge copy and install local doubles inside
the sandbox. Production callers can use the explicit `HERMES_DEEP_CHAT_WORKER`,
`HERMES_DEEP_CHAT_PYTHON`, `HERMES_DEEP_CHAT_CLAUDE`, and
`HERMES_DEEP_CHAT_REGISTRY` seams; worker paths must be absolute.

```console
make -C tools/deep-chat test
make -C tools/deep-chat syntax
make -C tools/deep-chat shellcheck
git diff --check
```

Nothing deploys automatically. After reviewing the diff and test output, a
human may explicitly install the bridge under `~/.local/bin` and the worker
under `~/.hermes/bin` (or set explicit absolute destination variables):

```console
tools/deep-chat/install-local.sh
```

Installer overrides are `HERMES_DEEP_CHAT_BRIDGE_DEST` and
`HERMES_DEEP_CHAT_WORKER_DEST`; both destinations must be absolute.

Do not use reconciliation as authorization to modify an existing chat. Inspect
dirty worktrees and registry discrepancies first, and obtain human direction.
