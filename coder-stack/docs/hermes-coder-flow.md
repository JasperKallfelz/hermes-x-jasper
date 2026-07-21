# Hermes Coder Flow

`bin/hermes-coder-flow` is an inline, synchronous Phase C orchestrator. Its only model-execution primitive is the local `bin/hermes-coder` subscription CLI. It does not use direct model APIs, run parallel writers, commit changes, merge, rebase, push, open pull requests, or remove worktrees.

## State machine

1. The flow reads and bounds the prompt, verifies the source Git repository, refuses a dirty source by default, and resolves `--start-ref` to a commit. `--allow-dirty` is explicit and still branches from the resolved commit; source-checkout changes are never copied into the worktree. A source snapshot is retained so any additional classifier mutation is rejected even when the source began dirty. In addition to Git-visible and ignored worktree data, it fingerprints the common Git directory's `config`, `info/exclude`, and recursive `hooks/` entry names, types, modes, and bounded contents without following symlinks or special files.
2. It resolves and validates a tracked `.hermes-gates.json` at the start commit, or the document supplied by `--gate-file`, and freezes that document mode `0400` in private flow storage. Its digest is checked before and after every stage. Runner-side snapshots also protect configured gate executables and directly invoked interpreter scripts, and every gate must leave Git-visible worktree state unchanged. Gates are required unless `--no-gates` is passed.
3. For a real run, the flow invokes the trust-resolved `hermes-coder` with `--doctor both` before classification and before branch/worktree creation. The bounded JSON result is validated with an exact schema, allowlisted vendor reasons, vendor-state consistency, and exact exit/result consistency. Missing installation/auth, timeout, non-ready status, oversized output, malformed JSON, or inconsistent data fails closed and is recorded with a stable `preflight_*` reason. A valid interrupted Doctor result remains exit `130` with `preflight_status=aborted`; it is never remapped to vendor unavailability. No raw auth output is retained. Dry-run skips this call entirely.
4. An explicit `fast`, `normal`, `complex`, `frontier`, or `security` lane is used directly. In `auto`, deterministic security keywords select `security` and high-impact keywords select `complex` before any model call. Otherwise one read-only classifier attempt runs through `hermes-coder --final-output-only`. The runner parses the vendor's bounded native JSON protocol and returns only its final assistant answer, never tool or execution output. That isolated answer may contain the existing secret-tagged JSON marker or be an exact raw JSON document; either form must pass the same lane and reason-code validation. Raw JSON is never searched for in a mixed stream. Missing, invalid, quota-limited, or otherwise unavailable classification fails safe to `complex`; a user abort remains an abort. Output from a failed classifier attempt can never be combined with another attempt.
5. Only after lane selection, the flow creates a unique `hermes/flow/<run-id>` branch and an isolated worktree under `~/.hermes/worktrees` by default. It prints both targets and records them before Git starts so a partially completed `git worktree add` remains discoverable.
6. One implementation stage runs in that worktree through `hermes-coder`, with the selected lane and frozen gates. The runner's ordinary finite vendor fallback and forward lane escalation remain available, but attempts are sequential.
7. The trusted runner sends the actual successful vendor through a per-stage close-on-exec pipe. The attestation binds schema, flow/stage/run identity, exit code, and a random token; model and gate processes neither inherit the descriptor nor see its token. Writable journals are never trusted for vendor selection. The flow then launches exactly one read-only review attempt using the opposite vendor and `--final-output-only`. Review uses at least the `normal` lane even when implementation used `fast`; all stronger lanes are unchanged. Its JSON marker is derived for that stage with HMAC-SHA-256 from an independent per-flow cryptographic secret. The secret is never exported to runner, model, reviewer, or gate environments; the public recursion run ID cannot be used to forge a marker. The isolated final answer may use that marker block or an exact raw JSON document. In both cases the payload must contain `verdict`, `severity`, `summary`, and bounded structured `findings`; missing or inconsistent output fails closed. A passing verdict may contain low findings only, never medium/high/critical findings.
8. A failed review may trigger zero or one repair according to `--repair-passes`. Only the bounded structured review data and original task are given to the repair writer. High or critical findings escalate one capability lane; `security` remains `security` and `frontier` remains `frontier`. Gates run as part of the repair implementation stage.
9. After repair, a fresh reviewer opposite the actual successful repair vendor runs with the same `normal` review floor. A second failed review stops the flow.
10. Once review passes, the frozen gates run again through the public `hermes-coder --gates-only` mode. This final stage launches no model.
11. Every branch/worktree created or partially created is preserved for manual inspection.

Classifier and reviewer tasks use the runner's read-only modes: Claude plan permission plus safe mode, or the Codex read-only sandbox. Their native stdout is bounded and suppressed until the runner has isolated one successful final answer; stderr remains diagnostic. Git control variables are removed from all Git, runner, model, review, and gate subprocess environments. Flow-owned Git commands also override `core.hooksPath=/dev/null` and clear `core.fsmonitor`, including for `git worktree add`, so pre-existing repository config cannot execute a checkout hook or filesystem monitor. A source-scoped exclusive lock on the repository's common Git directory prevents nested or concurrent flows even when a child unsets recursion guards.

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

`--dry-run` performs prompt/repository/gate preflight and prints prospective branch, worktree, lane, and stages. It does not call the Doctor, a classifier, or another model, create a branch/worktree, freeze gates, or write flow state/journals.

The gate document uses the schema described in [Reliability and quality gates](reliability-and-gates.md). `--no-gates` is the only way to run without gates and also skips the final gates-only stage.

## Budgets and exits

The defaults are a four-hour flow wall clock, two hours per stage, one hour per runner attempt, five seconds per Doctor vendor check, at most six model stages, and at most one repair. `--doctor-timeout` is capped at 30 seconds. Runner attempts and vendor fallback are bounded independently by `hermes-coder`. The remaining flow deadline also bounds preflight Git operations and worktree creation.

The flow continuously drains a private runner lifecycle socket, including during Doctor preflight. Each runner, model, gate, and auth-status process group is bound to a Linux procfs or macOS `libproc` start identity. On runner EOF or abnormal death, the flow completes the bounded frame stream and synchronously handles every remaining exact registration before declaring teardown complete. It refuses to signal an identity that changed or cannot be verified and returns exit `70` for a primary cleanup failure; no global process scan or process-name matching is used. `SIGINT`, `SIGTERM`, and `SIGHUP` handlers publish intent only, so repeated signals cannot asynchronously interrupt cleanup. An already-established abort or timeout reason remains primary while a cleanup problem is surfaced as a bounded secondary reason.

Containment remains process-group scoped. A descendant that deliberately calls `setsid()` can escape into a new session; no portable non-escapable macOS/Linux containment layer is claimed. Use an outer OS sandbox or service manager for commands that may daemonize adversarially.

| Exit | Meaning |
|---:|---|
| `0` | Review passed and final gates passed, or gates were explicitly disabled. |
| `2` | Invalid arguments, prompt, path, or gate configuration. |
| `64` | Source preflight failed or a read-only classifier changed the clean source. |
| `65` | Implementation, repair, or final deterministic gates failed. |
| `66` | Review failed with no repair available, or the post-repair review failed. |
| `67` | Review output was missing, invalid, inconsistent, or from the wrong vendor. |
| `69` | Recursive orchestration was refused. |
| `70` | Git, runner, storage, stage-budget, or other harness failure. |
| `75` | Required model vendors were unavailable, including quota/auth exhaustion. |
| `124` | A flow stage or wall-clock budget expired. |
| `130` | User/Doctor abort or graceful `SIGINT`/`SIGTERM`/`SIGHUP` termination. |

Quota, harness, timeout, and abort exits do not start an unbounded retry loop. Classifier unavailability alone uses the documented `complex` fallback; implementation and review terminal exits propagate.

## State, journals, and privacy

The flow journal defaults to `~/.hermes/logs/hermes-coder-flow.jsonl`. Per-run results, frozen gates, private transient prompt files, and run-local quota/auth circuit metadata default to `~/.hermes/state/flow`. Owner-only directories/files and bounded journal rotation are used. State, journal, source, gate, runner, and worktree paths are made absolute against the launch directory before validation and retained in that form across stage working directories.

Flow journal/result records contain controlled metadata only: run and stage identifiers, preflight readiness/reasons, stage start/end timestamps, requested lane/vendor, securely attested actual vendor, duration and terminal reason, Git HEAD before/after, privacy-safe SHA-256 digests of Git-visible worktree state before/after, bounded snapshot status/reason fields, gate/review status, the Git start commit, and branch/worktree paths. Every entered runner stage receives a unique monotonically increasing `stage_idx`, independent of the separate model-stage budget, and its start, end, and state records use the same value. Once a stage start is written, terminal runner, policy, cleanup, signal, and snapshot paths each attempt exactly one paired end/state record without replacing an earlier terminal reason. A pre-snapshot failure still produces stage start/end/state records without executing the stage. A post-snapshot failure preserves the completed model/gate outcome and records the snapshot failure separately. Digest inputs use domain-and-length framing for paths, metadata, file types, link targets, and contents. Regular untracked files are opened nonblocking and no-follow where supported, validated with descriptor/path identity checks before and after a size-bounded read, and special files are never opened. Records never retain the file paths themselves, prompt text or hashes, model/reviewer output, raw auth/gate output, gate commands, process command lines, arbitrary environment values, local process identities, or secrets. Reviewer summaries/findings are used transiently for a possible repair and are not stored in those records. Private prompt files are removed after each stage and by graceful `SIGINT`/`SIGTERM`/`SIGHUP` and process-exit cleanup. `SIGKILL` cannot run user-space cleanup, so an interrupted operator should inspect the owner-only stage directory after a forced kill.

The frozen gate document is an operational input artifact, not a journal record; by definition it contains the configured gate argv. Keep gate documents free of embedded secrets.

Overrides are:

- `HERMES_FLOW_CODER` for the runner executable;
- `HERMES_FLOW_LOG` for the flow JSONL journal (empty disables it);
- `HERMES_FLOW_STATE_DIR` for results and stage artifacts; and
- `HERMES_FLOW_WORKTREE_ROOT` for isolated worktrees.

`--runner`, `--journal`, `--state-dir`, and `--worktree-root` take precedence. Flow-owned output paths inside the source checkout are refused. Tests can point `HERMES_CODER_CLAUDE`, `HERMES_CODER_CODEX`, or `--runner` at executable stubs; the suite never needs a real model or network.

## Manual inspection

On every outcome, the terminal prints the branch, worktree, and result path when a worktree was attempted. Inspect it directly:

```console
git -C /printed/worktree/path status --short
git -C /printed/worktree/path diff
```

Cleanup, commits, merges, pushes, and any later integration are deliberately manual decisions.
