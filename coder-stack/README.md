# Hermes Coder Stack

`bin/hermes-coder` is a Python 3.9+, standard-library-only Claude/Codex runner for subscription-backed coding work. It provides adaptive capability lanes, cross-vendor fallback, classified failures, bounded execution, a privacy-safe run journal, persistent quota/auth cooldowns, and deterministic argv-based quality gates.

`bin/hermes-coder-flow` adds the Phase C inline flow: preflight, lane selection, one isolated branch/worktree, implementation, independent opposite-vendor review, at most one repair, a fresh review, and final gates. Every model stage is synchronous and goes through `bin/hermes-coder`; neither command calls a model API directly.

## Quick start

```console
bin/hermes-coder --task implement --lane normal \
  --workdir /path/to/repository \
  "Implement the requested change and run its tests."
```

The existing lanes (`fast`, `normal`, `complex`, `frontier`, and `security`), aliases (`easy` and `heavy`), task modes, automatic primary selection, `--tier`, `--no-escalate`, and dry-run command display remain supported. Read-only `inspect`, `plan`, and `review` tasks retain their original Claude safe-mode and Codex read-only sandbox behavior.

Execution is finite by default: at most eight planned model attempts, a one-hour timeout per model attempt, a two-hour run wall clock, and at most three quality failures. Override these with `--max-attempts`, `--attempt-timeout`, `--wall-timeout`, and `--max-quality-failures`.

Before an autonomous run, use the model-free subscription health check:

```console
bin/hermes-coder --doctor any
bin/hermes-coder --doctor both --doctor-timeout 5
```

The Doctor checks the configured Claude executable with `claude auth status` and the configured Codex executable with `codex login status`. It emits one privacy-safe JSON document with stable reason IDs and returns `0` only when the requested `any` or `both` readiness condition is met. Auth command output, account identifiers, tokens, and credential paths are discarded.

## Quality gates

Gates run only for `implement` tasks, sequentially after a model exits 0. A model attempt succeeds only after every gate passes. Gate failures advance to the next already-planned model; no new attempts are generated.

For trusted automation that needs a read-only model's answer without its mixed
tool stream, `--final-output-only` is available only with `--task inspect` or
`--task review`. It uses each vendor's native JSON output, validates a bounded
result, and emits only the successful attempt's isolated final answer. Ordinary
runs retain their existing live stdout/stderr behavior.

```console
bin/hermes-coder --gate-file .hermes-gates.json "Implement the change"

bin/hermes-coder \
  --gate 'unit=["python3","-m","unittest","discover","-s","tests"]' \
  --gate 'compile=["python3","-m","py_compile","bin/hermes-coder"]' \
  "Implement the change"
```

Gate commands are argv arrays and are never evaluated by a shell. See [Reliability and quality gates](docs/reliability-and-gates.md) for the versioned schema, exit codes, state paths, and safety details.

This repository tracks [`.hermes-gates.json`](.hermes-gates.json) with the complete unittest suite, off-worktree `py_compile` checks for both binaries and the relevant tests, and `git diff --check`. Every command is an argv array and is portable across macOS/Linux installations with `python3` and Git on `PATH`.

## Phase C flow

Run from a clean source repository containing a tracked `.hermes-gates.json`:

```console
bin/hermes-coder-flow --lane auto \
  "Implement the requested change and update its tests."
```

The source checkout is never used for implementation. Before classification or worktree creation, the flow invokes the resolved runner's Doctor with requirement `both`; an unavailable, timed-out, or malformed result fails closed. The flow then selects its lane, creates a unique `hermes/flow/...` branch and worktree under `~/.hermes/worktrees`, and always leaves both in place for manual inspection. Classifier and review stages use native final-answer isolation before validating either a secret-tagged verdict or an exact raw JSON document. The flow also uses a private runner-result pipe for actual-vendor attestation, source/common-Git-control fingerprints, source-scoped locking, immutable gate-policy checks, and fail-closed gate worktree snapshots. It never commits, merges, rebases, pushes, opens a pull request, or removes a worktree. Use `--gate-file PATH` for an external gate document or the explicit `--no-gates` escape hatch. `--dry-run` validates and prints the plan without a Doctor/classifier/model call or any worktree, state, or journal write.

See [Hermes Coder Flow](docs/hermes-coder-flow.md) for the state machine, review contract, environment overrides, privacy boundary, exit codes, and recovery procedure.

The original historical Phase A design note is retained at
[`docs/phase-a-plan.md`](docs/phase-a-plan.md). It is background, not the
current operating contract; the two documents above describe the implemented
behavior.

## Tests

The suite uses temporary executable stubs and isolated HOME directories; it never invokes a real model.

```console
PYTHONPYCACHEPREFIX=/tmp/hermes-coder-pycache \
  python3 -m unittest discover -s tests -v

PYTHONPYCACHEPREFIX=/tmp/hermes-coder-pycache \
  python3 -m py_compile bin/hermes-coder bin/hermes-coder-flow \
  tests/test_hermes_coder.py tests/test_hermes_coder_flow.py \
  tests/test_security_hardening.py

git diff --check
```

The parent starter also runs this suite and the compile check from its own
`make test` / `./verify.sh` and CI.

## License and provenance

This directory is a self-contained, public-safe snapshot: the runnable stack
(`bin/hermes-coder`, `bin/hermes-coder-flow`), its tracked gate policy
(`.hermes-gates.json`), the operator docs under `docs/`, and the standard-library
test suite under `tests/`. It carries no Git history, account identifiers, real
paths, journals, or runtime state.

The snapshot source is commit
`2a74f958cc1eb226584fdc51dfe72cebfc22ddab` from the separately maintained
Hermes Coder Stack repository. The runnable wrappers (`bin/hermes-coder`,
`bin/hermes-coder-flow`), the gate policy, and `tests/test_security_hardening.py`
are copied byte-for-byte. Two self-test files —
`tests/test_hermes_coder.py` and `tests/test_hermes_coder_flow.py` — carry a
single, documented public-only adaptation: privacy-marker fixtures of the form
`secret = "GATE_RAW_SECRET_…"` are renamed (`SECRET` → `MARKER`) so a public
full-history secret scan stays green without a blanket allowlist. The rename is
semantics-preserving and provably narrow — reverting it reproduces the source
bytes exactly, as enforced by `tests/test_coder_stack_snapshot.py`. Only public
integration/provenance text and portable path examples in the docs are otherwise
adapted here.

It is released under the MIT License — the same terms as the parent repository,
see [`../LICENSE`](../LICENSE). No provider credentials, tokens, or secrets are
bundled. The runner never calls a model API directly; it drives your own
subscription-backed Claude and Codex CLIs, which you install and authenticate
yourself (see the parent `README.md` and `../AGENTS.md`).
