# Hermes Coder Stack

Hermes ships two deliberately separate model workflows:

- `bin/hermes-coder` and `bin/hermes-coder-flow` are Codex-only automation.
- `tools/deep-chat/` is an explicit, optional persistent Claude tool. It is never a coder or flow fallback.

Both workflows invoke local subscription/OAuth CLIs. They do not call model APIs directly, require API keys, or create direct API-billing traffic.

## Codex-only runner

`bin/hermes-coder` is a standard-library Python runner with bounded lane escalation, classified failures, privacy-safe journaling, persistent quota/auth cooldowns, deterministic argv gates, and fail-closed process supervision.

| Lane | Codex model | Reasoning effort |
|---|---|---|
| `fast` | `gpt-5.6-luna` | `low` |
| `normal` | `gpt-5.6-terra` | `medium` |
| `complex` | `gpt-5.6-sol` | `high` |
| `frontier` | `gpt-5.6-sol` | `max` |
| `security` | `gpt-5.6-sol` | `max` |

`--primary` accepts only `auto` and `codex`; `auto` resolves to Codex. The aliases `easy` → `fast` and `heavy` → `frontier`, task modes, `--tier`, `--no-escalate`, and dry-run remain supported. Read-only tasks use the Codex read-only sandbox.

```console
bin/hermes-coder --task implement --lane normal \
  --workdir /path/to/repository \
  "Implement the requested change and run its tests."

bin/hermes-coder --doctor --requirement codex --doctor-timeout 5
```

Doctor runs only `codex login status`, discards its output, and emits a bounded privacy-safe JSON result. If `CODEX_HOME` is unset, the runner may honor `~/.codex-active-home` when it points to an authenticated directory beneath the current home.

Attempts are finite: one fresh Codex process per planned lane, at most eight attempts by default, a one-hour attempt timeout, a two-hour wall clock, and at most three quality failures. Quota/auth failure opens the Codex circuit and does not route to another provider.

## Codex-only flow

`bin/hermes-coder-flow` performs, serially:

1. Git, gate, state-path, and Codex Doctor preflight;
2. optional read-only lane classification;
3. implementation in one new isolated worktree;
4. an independent read-only review in a fresh Codex process;
5. optionally one fresh repair process and one fresh re-review process; and
6. model-free final gates.

Using the same provider is intentional; process/session continuity is not reused between stages. The flow has one writer at a time and never commits, merges, rebases, pushes, opens a pull request, removes a worktree, or cleans working files. Branches and worktrees remain for manual inspection.

```console
bin/hermes-coder-flow --lane auto \
  "Implement the requested change and update its tests."

bin/hermes-deep-work /path/to/repository --lane complex --dry-run \
  "Plan a complex implementation."
```

`bin/hermes-deep-work` is only a portable convenience wrapper for the Codex flow. It finds `hermes-coder-flow` next to itself or through the explicit `HERMES_DEEP_WORK_FLOW` seam.

See [Hermes Coder Flow](docs/hermes-coder-flow.md) for the state machine and [Reliability and quality gates](docs/reliability-and-gates.md) for launch, gate, privacy, and exit contracts.

## Optional Claude Deep Chat

Deep Chat is opt-in and separate from automated coder/flow routing. It creates a named preserved worktree and a resumable Claude subscription session:

```console
tools/deep-chat/hermes-deep-chat start /path/to/repo my-chat \
  --schema-version 2 -- "Initial task"
tools/deep-chat/hermes-deep-chat send my-chat -- "Follow-up"
tools/deep-chat/hermes-deep-chat status my-chat
tools/deep-chat/hermes-deep-chat reconcile my-chat
tools/deep-chat/hermes-deep-chat close my-chat
```

The bridge and worker keep prompt/model output out of durable bridge state and the resumable-session registry. They never commit, push, merge, delete worktrees, forget workers automatically, or remove locks they did not acquire. Installation is a separate human action:

```console
tools/deep-chat/install-local.sh
```

See [Deep Chat](tools/deep-chat/README.md) for dependencies, portable overrides, schemas, and recovery rules.

## Gates and verification

The tracked [gate policy](.hermes-gates.json) runs the full Python suite, the Deep Chat shell suite, Python compilation, shell syntax checks, and a local working-tree `git diff --check`. Gate commands are argv arrays and never pass through a shell interpreter unless the gate explicitly invokes one. CI separately fetches full history and checks the committed event range: a PR merge-base through `HEAD`, or push `before...HEAD`, with an empty-tree fallback for an initial push.

```console
python3.11 -B -m unittest discover -s tests -q
bash tools/deep-chat/tests/test_deep_chat.sh
python3.11 -B -m py_compile bin/hermes-coder bin/hermes-coder-flow \
  tools/deep-chat/claude_worker.py tests/*.py
bash -n bin/hermes-deep-work tools/deep-chat/hermes-deep-chat \
  tools/deep-chat/install-local.sh tools/deep-chat/tests/test_deep_chat.sh
bin/hermes-coder --gates-only --gate-file .hermes-gates.json \
  --workdir . --no-journal
git diff --check
```

Tests use isolated homes, repositories, and executable doubles; they do not invoke a real model or network service.
