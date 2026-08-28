# Reliability and quality gates

## Provider and lane contract

`hermes-coder` launches only Codex. `--primary auto` resolves to `codex`; `--primary codex` is explicit; any other value is an argument error before model launch.

The escalation chain has one fresh Codex attempt per lane:

```text
fast      gpt-5.6-luna / low
normal    gpt-5.6-terra / medium
complex   gpt-5.6-sol / high
frontier  gpt-5.6-sol / max
security  gpt-5.6-sol / max (no escalation)
```

A capability/quality failure may move to the next planned lane. Quota/auth failure opens the Codex circuit and stops same-provider escalation for that run. There is no second-provider fallback.

The optional Claude Deep Chat bridge is a separate explicit command under `tools/deep-chat`; the runner and flow never call it.

## Doctor

```console
bin/hermes-coder --doctor --requirement codex --doctor-timeout 5
```

Doctor resolves the configured `HERMES_CODER_CODEX` executable and runs `codex login status` without model inference. It emits one schema-versioned JSON document containing:

- `schema`, `kind`, `requirement: "codex"`, `ready`, and an overall stable `reason_id`;
- exactly one `vendors.codex` object with boolean `installed`, `authenticated`, and `ready`, plus a stable reason.

Exit `0` means ready, `75` means unavailable, and `130` means interrupted. Auth stdout/stderr and terminal stdin are discarded, so account identifiers, token text, credential paths, and raw CLI messages do not enter output or durable state.

Authentication is through the local subscription/OAuth CLI. The stack does not call an API directly or require API-billing credentials.

## Bounded child launch and cleanup

Model, gate, and Doctor stdout/stderr are drained concurrently while bounded 64 KiB diagnostic tails are retained. Children receive `/dev/null` as stdin. Signal exits are normalized, timeouts are bounded, and termination escalates only while the registered process owner remains verifiable.

On Linux, stable identity is the kernel start tick from `/proc/<pid>/stat`; on macOS it is the start time from `libproc`. POSIX capability is checked before launch and child identity immediately afterward. Failure returns a bounded harness reason and never substitutes a synthetic identity or signals a bare potentially recycled PGID.

Every POSIX model/gate target is launched behind the hardened persistent supervisor retained from the bounded-launch base. The supervisor:

1. becomes the new session and process-group leader;
2. sends a bounded readiness byte;
3. waits for a bounded parent acknowledgement before forking/execing the target;
4. reports exec failure or target completion over a bounded status protocol; and
5. remains leader until the parent authorizes fail-closed descendant cleanup.

Readiness, acknowledgement, protocol, identity, or cleanup failure returns exit `70`. A target cannot run before registration succeeds. Successful targets cannot leave same-group descendants silently behind.

During a flow, a private lifecycle socket carries bounded group start/stop frames with stable identities. Untrusted model/gate processes do not inherit the socket, token, journals, or state paths. Abrupt runner death triggers cleanup only for exact registered groups—never process-name search or broad process-table signalling.

## Final-answer isolation

`--final-output-only` is valid only for `inspect` and `review`. It asks Codex for native JSON events, accepts only the last completed `agent_message`, and enforces separate stream/answer byte ceilings. Invalid UTF-8/JSON, duplicate keys, native error events, malformed items, missing final messages, and oversized output fail closed.

Model stdout is suppressed in this mode until one attempt succeeds. Earlier or failed attempt answers and tool output cannot concatenate into the result. Native events and isolated text are not journaled.

## Budgets and circuit state

Defaults:

- `--max-attempts 8` (the normal full chain currently contains four attempts);
- `--attempt-timeout 3600`;
- `--wall-timeout 7200`;
- `--max-quality-failures 3`;
- `--circuit-cooldown 1800`.

The circuit defaults to `~/.hermes/state/hermes-coder-circuit.json`. `--circuit-state`/`HERMES_CODER_STATE` override it; `--no-circuit` or an empty environment value disables it. Legacy provider entries may remain in an existing schema-1 document, but only `codex` is read or updated.

State uses owner-only directories/files, no-follow/regular-file/ownership/size checks, and advisory locking. Malformed/unsafe state emits one warning and degrades to process-local behavior. Dry-run neither reads nor writes circuit/journal state.

## Gate schema

```json
{
  "version": 1,
  "gates": [
    {
      "name": "unit",
      "argv": ["python3.11", "-B", "-m", "unittest", "discover", "-s", "tests", "-q"],
      "timeout_seconds": 600
    }
  ]
}
```

The document must contain exactly `version` and `gates`. Gate names are unique printable strings; `argv` is a non-empty string array; optional timeout is finite and positive. Duplicate/unknown keys, invalid UTF-8, and documents above 1 MiB are rejected.

File gates run in order, then repeatable `--gate 'NAME=JSON_ARGV'` gates. Executables are resolved before model launch. The gate document, resolved executables, and directly invoked scripts are integrity-snapshotted around execution. A gate must leave Git-visible worktree state unchanged.

Gates:

- apply only to `implement`;
- run serially after model exit zero;
- use argv directly, never implicit shell evaluation;
- have independent timeouts bounded by the global wall clock;
- run in registered process groups; and
- receive sanitized Git/private state environment and `/dev/null` stdin.

A failed gate exposes only its name, status, and exit code to the next attempt. Raw output never enters prompts or journals. Missing executables/integrity/mutation failures return `70`; non-zero/timeout quality exhaustion returns `65`.

`--gates-only` is model-free, requires a configured gate, accepts no prompt, and does not touch circuit state. It returns `0`, `65`, `70`, or `124`.

The repository policy also runs the Deep Chat shell suite, Python compilation for every shipped Python file, Bash syntax for every shipped shell script, and a working-tree `git diff --check` for local edits. That local gate does not inspect already committed changes. GitHub CI fetches full history and runs a separate whitespace check over the event-derived committed range: PR merge-base through `HEAD`, push `before...HEAD`, or the empty tree through `HEAD` for an initial push.

## Git and recursion controls

`HERMES_CODER_ACTIVE` blocks recursive runner launches from model/gate hooks. Flow also recognizes this guard and holds a source-scoped kernel lock.

Git-control environment variables and `GIT_CONFIG_*` entries are stripped from children. Flow-owned Git commands additionally disable hooks/fsmonitor and use their own bounded readiness/acknowledgement launcher. Model subprocesses receive `LLVM_PROFILE_FILE=/dev/null`; gates retain the sanitized ambient profiling configuration.

## Journal and privacy

The JSONL journal defaults to `~/.hermes/logs/hermes-coder.jsonl`; `--journal`/`HERMES_CODER_LOG` override it and `--no-journal` disables it. Rotation defaults to 1 MiB and three backups.

Allowlisted records contain operational IDs, timestamps/durations, lane, compatibility `vendor` field (always `codex` for model attempts), model/effort/task class, exit/failure reasons, and bounded counts. They exclude prompts and hashes, commands, model/gate/auth output, workdir paths, arbitrary environment values, and secrets.

The terminal is a separate privacy boundary: ordinary execution and dry-run diagnostics display the model command, including its prompt. Do not redirect terminal diagnostics into a store with stronger privacy requirements.
