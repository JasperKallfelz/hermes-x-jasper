# Reliability and quality gates

## Failure and exit contract

By default, child stdout and stderr are streamed independently while bounded 64 KiB tails are retained in memory. Model, gate, and auth-status children receive `/dev/null` as stdin. The runner starts each child in a separate process group, normalizes signal exits, forwards interruption to that group, and escalates termination for timed-out processes while the registered group owner remains verifiable.

On Linux, registrations use the kernel start ticks from `/proc/<pid>/stat`; on macOS they use the process start time returned by `libproc`. POSIX capability is checked before a model or gate `Popen`, and the exact child identity is captured immediately afterward. If either step fails, the runner returns bounded reason `process_identity_unavailable`; a just-launched child is stopped and reaped only through its still-live direct `Popen` handle. It never substitutes a synthetic POSIX identity or signals a bare possibly-recycled PGID. Non-POSIX direct runs retain their live-`Popen` fallback.

When a flow is active, the trusted runner reports model, gate, and auth-status group start/stop frames plus that identity over a private inherited socket. Untrusted children receive neither its environment variable nor its descriptor. The flow drains bounded frames continuously, freezes the registration set at runner EOF, and validates the recorded owner before every fallback signal. Abrupt runner death, timeout, and graceful signal teardown therefore act only on exact registered groups; no command-line matching, process-name search, broad process-table enumeration, or unrelated-process signalling is used. A changed/missing identity or group that cannot be stopped becomes an exit-`70` cleanup failure. If an abort or timeout was already the stronger terminal condition, it remains primary and the cleanup failure is reported as a bounded secondary reason.

Containment is process-group scoped, not a non-escapable sandbox. A hostile descendant can call `setsid()` and leave the registered group. Portable Python 3.9 on the supported macOS/Linux hosts provides no common cgroup/job-object containment primitive, so commands that may daemonize adversarially require an outer OS sandbox or service manager.

Classification happens only when a child exits non-zero. Specific subscription-CLI quota/auth signatures and wrapper exhaustion messages are recognized on either output stream. Bare/status-code `401` or `429`, generic authentication prose, and ordinary source/test text are deliberately not evidence. Exit-0 model output is always treated as successful model execution, even when it contains quota or failover chatter.

| Exit | Meaning |
|---:|---|
| `0` | A model succeeded and, for an implement task, every configured gate passed. |
| `2` | Invalid CLI invocation or invalid gate configuration. No model starts. |
| `65` | The planned chain was exhausted after a quality failure, or `--max-quality-failures` was reached. |
| `69` | Recursive model orchestration was refused before another model started. |
| `70` | Harness failure, such as a missing model or gate executable. No retry follows that failure. |
| `75` | The finite model chain is exhausted or all usable vendors are unavailable, including open quota/auth circuits. |
| `124` | The runner wall-clock budget expired. |
| `130` | User abort or SIGINT-normalized child abort. |

## Subscription Doctor

`hermes-coder --doctor any|both` is a model-free, scriptable readiness check. It resolves the configured `HERMES_CODER_CLAUDE` executable and runs it with `auth status`, then resolves `HERMES_CODER_CODEX` and runs it with `login status`. Each command runs in its own identity-bound process group with a short timeout (`5` seconds by default, configurable up to `30`) and bounded TERM/KILL cleanup. During Flow preflight these groups use the same private lifecycle handoff as model and gate groups.

The command emits exactly one schema-versioned JSON document. Each vendor has boolean `installed`, `authenticated`, and `ready` fields plus a stable `reason_id`; the document also contains the `any`/`both` requirement, overall readiness, and an overall reason. Exit `0` means the requested condition is satisfied, exit `75` means it is not, and exit `130` preserves an interrupted check. Raw auth stdout/stderr is discarded, auth commands cannot read terminal input, and the document contains no tokens, email addresses, credential-file paths, executable paths, or raw vendor messages. Doctor mode never launches model inference and never reads or writes journals, circuits, or gates.

Within one run, quota/auth failure blocks only that vendor. The other vendor can continue through already-planned stronger lanes after capability failures. Capability failures and attempt timeouts advance through the finite chain. Harness errors and user aborts stop immediately.

## Isolated final-answer mode

`--final-output-only` is narrowly valid for `--task inspect` and `--task
review`. Codex is launched with native `--json`; its JSONL stream is parsed
incrementally and only the last completed `item.completed` `agent_message`
text is retained. Tool, execution, reasoning, and user events are ignored.
Claude is launched with `--output-format json`; only a successful top-level
`result` event's string `result` is retained.

Native streams and isolated answers have separate hard byte ceilings. Invalid
UTF-8/JSON, duplicate keys, malformed events, native error results, oversized
streams/answers, and missing final messages fail closed as capability failures.
Model stdout is not streamed in this mode. The runner emits the isolated text
only after a model attempt succeeds and any secure flow attestation has been
sent, so failed and fallback attempts cannot concatenate answers. Diagnostics
remain on stderr. The ordinary output tail is not used to reconstruct the
answer, and neither the answer nor native event stream is added to journals.

## Budgets and cooldown state

Defaults are:

- `--max-attempts 8`
- `--attempt-timeout 3600`
- `--wall-timeout 7200`
- `--max-quality-failures 3`
- `--circuit-cooldown 1800`

Quota/auth failures open a per-vendor cooldown in `~/.hermes/state/hermes-coder-circuit.json`. Override it with `--circuit-state PATH` or `HERMES_CODER_STATE`; use `--no-circuit` or an empty environment value to disable persistence. A vendor is skipped until its stored cooldown expires. The implementation makes no half-open probe claim: after expiry, the vendor is simply eligible for an ordinary planned attempt again.

The state directory and file are owner-only (`0700` and `0600`). Symlinks, non-regular files, unowned files, oversized state, and malformed data are rejected. Advisory locking is used where the platform supports it. A state error produces one warning and safely degrades to process-local behavior for that run.

Dry-run reads and validates gate configuration so its displayed plan is accurate, but it does not read or mutate circuit state and does not create a journal.

## Gate schema

The gate file is UTF-8 JSON with a required version and no additional top-level fields:

```json
{
  "version": 1,
  "gates": [
    {
      "name": "unit",
      "argv": ["python3", "-m", "unittest", "discover", "-s", "tests"],
      "timeout_seconds": 300
    },
    {
      "name": "compile",
      "argv": ["python3", "-m", "py_compile", "bin/hermes-coder"]
    }
  ]
}
```

The tracked repository policy in `/.hermes-gates.json` runs the full unittest suite with bytecode writes disabled, compiles both binaries plus the relevant test modules with `PYTHONPYCACHEPREFIX` directed under `/tmp`, and runs `git diff --check`. It therefore makes no Git-visible worktree change on macOS or Linux.

`name` must be unique and printable. `argv` must be a non-empty array of non-empty strings. `timeout_seconds` is optional and defaults to `--gate-timeout` (300 seconds). Unknown or duplicate JSON keys are rejected. The file is limited to 1 MiB.

Repeatable CLI gates use `--gate 'NAME=JSON_ARGV'`. File gates run first in file order, followed by CLI gates in flag order. Executables are resolved and the complete immutable gate list is validated before the first model starts. The gate document, resolved executables, and directly invoked interpreter scripts are integrity-snapshotted before model execution and checked before and after every gate. Ordinary file arguments remain worktree targets, so compile/test gates can still judge files changed by the task. A writer modification to protected gate code is a fail-closed harness error rather than a weaker policy.

Gates:

- apply only to `implement` tasks;
- run sequentially after model exit 0;
- inherit the model work directory and current environment;
- stream both output channels live;
- use no shell;
- have independent timeouts bounded by the overall wall clock; and
- run in separately terminable process groups registered with the flow's private shutdown handoff when one is active.

Git-visible status/diff state, including untracked file contents, is compared before and after each gate. A gate that changes the judged worktree fails closed with exit `70`, even if it exits zero. Gate-generated ignored build artifacts are outside this Git-visible comparison.

On gate failure, the next model receives only the gate name, status, and exit code. Raw gate output is never inserted into a model prompt or journal. A missing executable is detected before model launch and returns `70`. A non-zero or timed-out gate is a quality failure; exhausted repair attempts return `65`.

`--gates-only` is the public model-free verification mode used by Phase C for its final check. It accepts no prompt, requires at least one configured gate, does not read or mutate vendor circuit state, and returns `0`, `65`, `70`, or `124` for pass, quality failure, gate harness failure, or wall timeout. `--gates-only --dry-run` validates and displays the resolved gate plan without running it or creating a journal.

While a model or gate child is active, `HERMES_CODER_ACTIVE` prevents an accidental hook in that process tree from recursively launching another runner. `hermes-coder-flow` recognizes the same guard and additionally holds a source-scoped kernel lock that remains effective if a child removes recursion environment variables.

Model, review, and gate children do not inherit runner journal/circuit/result-channel paths or Git repository-control variables such as `GIT_DIR`, `GIT_WORK_TREE`, alternate object directories, or index overrides. Journal and circuit paths are retained as launch-directory absolute paths before use.

Model CLI children additionally receive `LLVM_PROFILE_FILE=/dev/null`, preventing instrumented vendor wrappers from creating profiling artifacts in the target worktree. Gate children retain the sanitized ambient environment without this override, so explicitly configured gate profiling remains under user control.

## Journal and privacy boundary

The best-effort JSONL journal defaults to `~/.hermes/logs/hermes-coder.jsonl`. Override it with `--journal PATH` or `HERMES_CODER_LOG`; use `--no-journal` or an empty environment value to disable it. Rotation defaults to 1 MiB plus three backups and is configurable with `--journal-max-bytes` (minimum 1024) and `--journal-backups`.

Records contain only controlled operational fields: schema/event identifiers, a random run ID, timestamps and durations, planned lane/vendor/model/effort/task metadata, exit codes, failure classes, stable reason IDs, and bounded attempt counts. They never contain prompts, prompt hashes, command argv, model or gate output, regex-matched text, work-directory paths, environment values, or secret values.

The journal directory, journal, and lock file are owner-only. Append and rotation use no-follow/regular-file/ownership checks and advisory locking where supported. At most one warning is emitted if journaling fails, and a journal problem never changes run routing or its final exit.

The terminal is a different privacy boundary: for compatibility, the existing `+ COMMAND` diagnostics and `--dry-run` output still display the full model command, including its prompt. Do not redirect those diagnostics into a log that has stronger privacy requirements than the terminal session.
