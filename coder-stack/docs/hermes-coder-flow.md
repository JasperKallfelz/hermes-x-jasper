# Hermes Coder Flow

`bin/hermes-coder-flow` is a synchronous Codex-only orchestrator layered on `bin/hermes-coder`. It uses local subscription/OAuth CLI execution only—no direct model API or direct API billing.

## State machine

1. Bound the prompt; verify the source Git repository, start ref, output paths, and source-scoped lock; refuse source dirt by default.
2. Resolve the tracked `.hermes-gates.json` or `--gate-file`, validate its exact schema, freeze it mode `0400` outside the future worktree, and fingerprint protected gate executables/scripts.
3. Invoke `hermes-coder --doctor --requirement codex`. The flow accepts only the bounded exact Doctor schema for one `codex` entry and verifies field/exit consistency. Missing auth/install, timeout, interruption, malformed output, and cleanup failure fail closed before classification or worktree creation. Dry-run skips Doctor.
4. Select an explicit lane, a deterministic `security`/`complex` override, or one read-only Codex classifier process. Invalid or unavailable classification fails safe to `complex`; an abort stays an abort.
5. Create a unique `hermes/flow/<run-id>` branch and isolated worktree beneath `~/.hermes/worktrees` by default.
6. Run implementation through a fresh `hermes-coder` process in that worktree. Quality gates run after a successful model exit.
7. Run exactly one independent read-only review through another fresh Codex process with `--final-output-only --no-escalate --max-attempts 1`. Review is floored at `normal`.
8. On a failed review, optionally run one fresh repair process. High/critical findings raise the repair lane once, except `security` and `frontier` remain fixed.
9. Run a fresh read-only Codex re-review process. A second failure stops.
10. Run the frozen gates again through model-free `hermes-coder --gates-only`.
11. Preserve every created or partially created branch/worktree for manual inspection.

The same provider across implementation and review is intentional. Each model stage launches a separate runner and separate `codex exec` process without resume/session reuse. Stages are serial, so there is never more than one writer.

## Review trust boundary

Classifier and reviewer stdout use Codex's bounded native JSON stream. Only the last completed agent message is accepted; tool output, earlier agent messages, malformed events, native errors, and failed attempts cannot supply a verdict.

Verdicts may be an exact raw JSON document or a secret-tagged block. Tags derive from an unexported per-flow HMAC secret. Review payloads have exact bounded fields: `verdict`, `severity`, `summary`, and structured `findings`. A pass cannot contain medium/high/critical findings. Only bounded structured findings—not raw reviewer text—enter a repair prompt.

The runner reports the successful provider over a private, authenticated, close-on-exec pipe. The schema retains the established `vendor` field name for compatibility, but the only accepted value is `codex`. Writable journals cannot determine attribution.

## Git and process hardening

Every flow-owned Git call strips inherited Git-control variables, disables hooks and fsmonitor, closes stdin, and uses the bounded POSIX launch handshake. The inline launcher must report readiness before the parent acknowledges execution; identity or acknowledgement failure prevents Git from executing. Deadlines cover preflight, snapshots, and worktree creation.

Runner, model, gate, Doctor, and Git processes are bound to stable Linux procfs or macOS `libproc` start identities before group signalling is authorized. The runner's persistent POSIX supervisor remains process-group leader after its target exits, performs fail-closed descendant cleanup, and uses a bounded readiness/acknowledgement protocol. The flow drains private lifecycle frames and cleans only exact registered groups. It never scans by process name or signals an unverifiable/recycled process ID.

Containment is process-group scoped. A deliberately daemonizing child that creates a new session can escape; use an outer OS sandbox for adversarial commands.

The source snapshot includes Git-visible state and bounded common-Git control surfaces. Classifier writes are rejected even with `--allow-dirty`. Stage snapshots use framed hashes, no-follow/nonblocking file reads, and fail closed on races or special files. Gate policy integrity is checked before and after every stage, and gates must leave Git-visible worktree state unchanged.

## Non-actions

The flow never commits, merges, rebases, pushes, opens a pull request, deploys, deletes a worktree, resets, or cleans files. `--repair-passes` allows only `0` or `1`; no parallel writers or unbounded loop exists.

## Usage

```console
bin/hermes-coder-flow --source /path/to/repo --lane auto \
  "Implement the feature, update tests, and preserve compatibility."

bin/hermes-coder-flow --source /path/to/repo --lane security \
  --gate-file /path/to/gates.json --repair-passes 0 \
  "Harden session authorization."

bin/hermes-coder-flow --source /path/to/repo --dry-run \
  "Plan an ordinary repository change."
```

Gates are required unless `--no-gates` is explicit. Dry-run validates prompt/repository/gate inputs and prints the prospective plan without Doctor/model execution, worktree creation, frozen policy, state, or journal writes.

## Budgets and exits

Defaults: four-hour flow wall clock, two hours per stage, one hour per runner attempt, five seconds for Codex Doctor auth status, at most six model stages, and one repair. Doctor timeout is capped at 30 seconds.

| Exit | Meaning |
|---:|---|
| `0` | Review and final gates passed, or gates were explicitly disabled. |
| `2` | Invalid arguments, prompt, path, or gate configuration. |
| `64` | Source/Git preflight failed or classifier changed the source. |
| `65` | Implementation, repair, or deterministic gates failed. |
| `66` | Review failed with no repair, or re-review failed. |
| `67` | Review output/provider was missing, wrong, or invalid. |
| `69` | Recursive/concurrent orchestration was refused. |
| `70` | Git, runner, storage, integrity, cleanup, or harness failure. |
| `75` | Codex was unavailable, including quota/auth failure. |
| `124` | Stage or global wall-clock budget expired. |
| `130` | User/Doctor abort or graceful termination. |

## State and privacy

The journal defaults to `~/.hermes/logs/hermes-coder-flow.jsonl`; state/frozen gates/transient prompts default to `~/.hermes/state/flow`. Owner-only storage, no-follow checks, bounded rotation, and atomic writes are used.

Doctor telemetry records `preflight_requirement: "codex"` and `codex_ready`; no Claude readiness field or requirement remains. Legacy circuit documents with unrelated provider entries remain readable, but only the Codex entry participates in routing.

Durable journal/state includes allowlisted operational metadata: IDs, lanes, the compatibility `vendor` fields, stage indices/times/statuses, privacy-safe digests, gate/review status, start commit, and preserved branch/worktree paths. It excludes prompt text/hashes, model/reviewer output, raw auth/gate output, commands, arbitrary environment values, and process identities. Transient prompt files are removed during normal and graceful-signal teardown; inspect owner-only state after `SIGKILL`.

Overrides:

- `HERMES_FLOW_CODER`, `HERMES_FLOW_LOG`, `HERMES_FLOW_STATE_DIR`, and `HERMES_FLOW_WORKTREE_ROOT`;
- `--runner`, `--journal`, `--state-dir`, and `--worktree-root` take precedence;
- `HERMES_CODER_CODEX` selects a test or installed Codex executable.

## Manual inspection

```console
git -C /printed/worktree/path status --short
git -C /printed/worktree/path diff
```

Integration and cleanup remain explicit human decisions. Optional Claude Deep Chat is documented separately in [tools/deep-chat/README.md](../tools/deep-chat/README.md) and is never called by this flow.
