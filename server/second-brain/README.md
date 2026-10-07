# Hermes Second Brain

Production-oriented local second brain scaffolding for a Hermes setup. The live OpenViking installation and Hermes configuration are intentionally outside this checkout; this project provides operational code, tests, templates, and safety rails.

## Architecture Boundaries

- Profile `USER.md` and `MEMORY.md`: keep only critical, curated always-on user facts/preferences and assistant operational lessons. Do not append nightly transcript summaries, TTL context, or staged intake here.
- OpenViking: the one on-demand searchable index/mirror for local context. Canonical ownership stays with the original files, Hermes/LCM state, or Context Inbox rows; OpenViking is not the master copy of source content.
- LCM: transcript preservation and summarization source. Keep raw/session artifacts canonical outside prompts; sync selected summaries into OpenViking instead of flooding MEMORY.md.
- Context Inbox: local canonical vault for passive message context from WhatsApp, Signal, Slack, and generic JSONL. Its separate TTL table owns only expiring context/episodes, and its intake tables are untrusted non-destination staging. Raw messages stay local in SQLite; only the pre-existing ranked, redacted summaries, reminder candidates, and habit hypotheses are exported for OpenViking review.
- Source files: markdown, text, PDF, and DOCX files remain canonical in their original roots. The sync pipeline records hashes and deterministic OpenViking resource URIs; it does not rewrite source files.
- Mail: only sanitized JSONL event feeds are accepted. Mailbox credentials, tokens, raw auth messages, and provider configuration are never consumed here.
- Dreaming: opt-in/background reflection has its own private SQLite state, reports, `DREAMS.md`, and generated OpenViking `dreams` namespace. It never writes `USER.md` or `MEMORY.md` and never performs calendar, mail, GitHub, Notion, payment, or other external actions.
- B+ control plane: intent, tool-job/work-order, temporary-memory, Dream-candidate, publication-outbox, and process-improvement states remain separate. Starting or completing one lifecycle never silently completes another or creates durable memory.

## Configuration

Create a manifest:

```bash
python -m hermes_second_brain init-manifest config/manifest.json
```

Edit `config/manifest.json` to list approved roots. Each source gets its own unique `namespace`; duplicate namespaces are rejected because production resource IDs are derived from `namespace` plus relative path. The scanner excludes VCS, virtualenvs, caches, build outputs, known binary extensions, and secret-looking paths by default, including token, password, API key, private key, OAuth, cookie, credential, secret, auth token, and access key filename variants.

The Context Inbox vault defaults to `~/.hermes/second-brain/context-inbox.sqlite3` and can be changed with `context_inbox_db` in the manifest or with `--db` on each `context-*` command.

### Ownership, temporary memory, preferences, and intake staging

The machine-validated ownership map is checked in at `src/hermes_second_brain/data_ownership.json`. Every category has exactly one master; mirrors, derived indexes, and typed intake staging are explicitly non-master. It also records the durable-fact, temporary-context, routine, episode, and assistant-self-lesson routing boundaries. Validation is read-only and never opens or migrates a database:

```bash
python -m hermes_second_brain ownership-check
python -m hermes_second_brain ownership-check --json
```

Temporary memory is deliberately limited to `context` and `episode` rows in the existing Context Inbox database. Expiry must be aware UTC, future, and no more than 365 days away. Active listing checks the supplied clock, so a row becomes invisible exactly at expiry even before tombstone cleanup. `expire` marks elapsed rows; `purge` permanently removes elapsed rows; both support a non-mutating preview. Temporary rows are not exported to OpenViking or promoted to profile memory, notes, tasks, reminders, or prompts:

```bash
python -m hermes_second_brain temporary-memory add --db /private/path/context.sqlite3 \
  --idempotency-key trip:berlin:context --kind context --text "Temporary trip context" \
  --expires-at 2030-01-02T12:00:00Z
python -m hermes_second_brain temporary-memory list --db /private/path/context.sqlite3 --json
python -m hermes_second_brain temporary-memory expire --db /private/path/context.sqlite3 --dry-run --json
python -m hermes_second_brain temporary-memory purge --db /private/path/context.sqlite3 --dry-run --json
```

Daily-brief preferences default to the private atomic file `~/.hermes/second-brain/briefing-preferences.json`; absent means the previous 20/10/10 section limits, 3900-character output cap, all sections, and quiet-when-empty behavior. `config/briefing-preferences.example.json` documents the strict schema. There is no free-form prompt field. Unknown keys, unsafe bounds, non-private active files, and symlinked paths are rejected. `HERMES_BRIEFING_PREFERENCES` or `--preferences` deliberately selects another path; explicit daily-brief limit flags override the file.

```bash
python -m hermes_second_brain briefing-preferences show --json
python -m hermes_second_brain briefing-preferences update --include-section events \
  --include-section reminders --event-limit 8 --reminder-limit 5 --max-output-characters 1800
python -m hermes_second_brain context-daily-brief --dry-run --json
python -m hermes_second_brain briefing-preferences reset
```

`intake-plan` accepts a model-classified version-1 JSON bundle from a file or stdin, validates bounded typed items, and returns one bundled confirmation. Supported destinations are `reminder`, `note`, `memory_fact`, `temporary_context`, `routine`, `personal_task`, and `hermes_work_order`; temporary context requires UTC expiry. The default is a validation-only dry run; `--commit` writes only untrusted staging rows. No item is executed or materialized at its destination, and sensitive, external, or approval-required items remain `pending_approval` for later use of Hermes-native tools and approvals.

```bash
python -m hermes_second_brain intake-plan --input classified-bundle.json --dry-run --json
python -m hermes_second_brain intake-plan --input - --commit --db /private/path/context.sqlite3 --json
```

### B+ closed-loop control plane

The B+ extension uses separate private SQLite stores and validated state machines:

- Intent: `captured → clarified → approved → planned → done`, with explicit cancellation paths.
- Tool job/work order: `queued → running → checkpointed → verifying → completed`, with `failed`, `stalled`, and `cancelled` alternatives. Completion is legal only from `verifying` and requires non-empty verification-evidence metadata. Every active-state mutation is fenced by both owner and a monotonically increasing lease generation and atomically checks freshness. Heartbeats extend bounded leases; the deterministic stale detector only marks an active leased job `stalled` after its recorded lease expires and never kills a process. Retry clears terminal-only fields before returning a job to `queued`.
- Memory: the existing temporary TTL rows and Dream candidates retain their own independent lifecycles. A job link is provenance, not an automatic intent or memory transition.
- Improvement proposal: `proposed → reviewed → approved → applied → canary → kept/rolled_back`, plus rejection and expiry. `applied` records an operator-confirmed action; the queue itself never edits a skill, configuration, test, workflow, or external system.

Mutations are transactional and creation/transition idempotency keys are replay-safe. Parent directories and SQLite files are private where owned (`0700`/`0600`), symlinked database paths are rejected, and the CLI projects results through metadata allowlists so summaries, checkpoints, verification prose, and proposal intervention text are not echoed:

```bash
PYTHONPATH=src python3.11 -m hermes_second_brain lifecycle intent-capture \
  --db .state/lifecycle.sqlite3 --idempotency-key example:intent \
  --summary "operator-provided private summary" --source manual --json
PYTHONPATH=src python3.11 -m hermes_second_brain lifecycle intent-advance \
  --db .state/lifecycle.sqlite3 --intent-id int_... --state clarified --json
PYTHONPATH=src python3.11 -m hermes_second_brain lifecycle status \
  --db .state/lifecycle.sqlite3 --json

PYTHONPATH=src python3.11 -m hermes_second_brain lifecycle job-enqueue \
  --db .state/lifecycle.sqlite3 --idempotency-key example:job \
  --job-kind dream_round --intent-id int_... --json
PYTHONPATH=src python3.11 -m hermes_second_brain lifecycle stale \
  --db .state/lifecycle.sqlite3 --dry-run --json

PYTHONPATH=src python3.11 -m hermes_second_brain improvement status \
  --db .state/improvements.sqlite3 --json
```

#### Metadata-only Process Observatory

`templates/hermes-process-observatory-plugin/` is a standalone passive Hermes plugin, not a core patch. It follows the current `post_tool_call`, `post_llm_call`, API-request, subagent, session-finalize, and Kanban lifecycle contracts. Hooks ignore tool args/results, prompts, response/model text, command strings, message bodies, raw error messages, paths, and credentials. The synchronous hook only projects bounded metadata and attempts a non-blocking put into a bounded memory queue. A daemon batch writer owns private atomic file writes and fsyncs and enforces hard queue, file-count, byte, and age quotas. Overflow or unload may drop telemetry, which is counted; user-facing work never waits for spool I/O. Explicit `flush()` and `stop()` helpers exist for tests and controlled finalization. Queue ancestry accepts only exact root-owned operating-system aliases such as `/var → /private/var`; all other symlinks are rejected.

The importer independently revalidates the exact allowlist, bounds each file, rejects unsafe queue ancestry, deduplicates by event ID, quarantines malformed input privately, and applies TTL retention. Reports contain counts, median/p90/p95, error/retry/stall/unverified-completion rates, and bounded anomalous tool/task classes. Small samples are labeled; latency percentiles are descriptive and never declare a job dead.

Repository-only validation and local test-store commands:

```bash
PYTHONPYCACHEPREFIX=/tmp/hermes-second-brain-pyc python3.11 -m py_compile \
  templates/hermes-process-observatory-plugin/__init__.py
PYTHONPATH=src python3.11 -m hermes_second_brain process-observatory import \
  --db .state/process-observatory.sqlite3 \
  --spool .state/process-observatory-spool.d \
  --quarantine .state/process-observatory-quarantine.d --json
PYTHONPATH=src python3.11 -m hermes_second_brain process-observatory report \
  --db .state/process-observatory.sqlite3 --json
```

For a later operator-controlled deployment, copy the complete template directory to `~/.hermes/plugins/process-observatory/`, verify it appears in `hermes plugins list`, enable it through the existing Hermes profile/plugin workflow, and restart only through the normal operator procedure. None of those live-profile steps is performed by this repository change.

#### Cross-vendor proposal review

`scripts/process_improvement_review.py` feeds only the bounded aggregate observatory report to two subscription-backed reviewers. The primary uses the configured Claude subscription wrapper. The independent second reviewer uses the Codex CLI directly through a mandatory isolation adapter: it copies only private auth into an ephemeral `CODEX_HOME`, positively requires `Logged in using ChatGPT`, removes all caller/provider environment variables, ignores user config and rules, disables and re-attests every advertised feature, and runs from an empty private directory under an OS sandbox that denies repository and private-file reads while allowing required runtime/auth/network access. The packet travels on stdin and no prompt file is created. A custom secondary argv cannot bypass the adapter. If subscription auth, no-tools attestation, or OS read isolation cannot be proven, review fails closed and persists nothing. Both reviewer paths use argv rather than shell strings and bound stdin/stdout/stderr, runtime, descendants, and output. Persistence occurs atomically only after both schemas validate and the second reviewer explicitly accepts or strictly lowers risk. It cannot upgrade risk or approve application. Empty data is a silent healthy no-op, and either reviewer failing leaves the existing proposal store unchanged and retryable.

```bash
# Write-free validation of config, safe argv, and bounds.
PYTHONPATH=src python3.11 scripts/process_improvement_review.py \
  --config config/manifest.example.json --validate-only
# Operator-run review: runs reviewers only when aggregate data exists and is
# still quiet on a no-data run.
PYTHONPATH=src python3.11 scripts/process_improvement_review.py \
  --config config/manifest.json --json
PYTHONPATH=src python3.11 -m hermes_second_brain improvement list \
  --db .state/improvements.sqlite3 --json
```

Native Hermes background self-improvement remains the immediate per-turn learning path. Native Curator remains responsible for skill-library hygiene; for an operator-run consolidation use a backup, `consolidate=true`, a stronger subscription-backed model, and a bounded staged review before accepting changes. Process Observatory and Dreaming may create reviewed proposals, but neither replaces those native facilities or applies changes automatically.

## Operations

### Background Dreaming

`dream` is a real managed Light → REM → Deep consolidation sweep, separate from the ordinary manifest sync. It reads only explicitly configured Hermes profile databases using SQLite `mode=ro`, prefers bounded LCM summaries, and falls back to bounded active user/assistant turns. Cron collectors, subagent sessions, inactive messages, system/tool/reasoning fields, prior Dream artifacts, and sessions containing Dream self-markers are excluded. All material is treated as untrusted data and redacted before it reaches the model, state, or reports.

The model adapter invokes the configured subscription wrapper as a subprocess argv with the prompt on stdin, strict per-phase JSON schema, Sonnet/high by default, safe mode, no session persistence, and no tools. It never needs a direct API key. Light stages exact-quote candidates; bounded stdlib HTTP JSON-body requests retrieve local OpenViking context without putting queries in process arguments; REM keeps connections, contradictions, blind spots, and hypotheses distinct; Deep receives bounded evidence text and must explicitly judge semantic support for every candidate and insight. Deterministic gates can only be confirmed or downgraded. Assistant-only claims cannot become durable conclusions without user or approved `brain`/`notes` canonical corroboration.

Commands:

```bash
# Metadata-only preview; does not create state or invoke the model
PYTHONPATH=src python3.11 -m hermes_second_brain dream --config config/manifest.json --dry-run --json

# One bounded normal/nightly sweep (quiet on healthy success)
PYTHONPATH=src python3.11 -m hermes_second_brain dream --config config/manifest.json

# Bounded historical/extended sweep; production defaults are 2 minutes,
# at most 64 rounds, and the nightly worker's 06:30 local catch-up deadline.
PROJECT_DIR="$(pwd)" scripts/dream_sweep.sh --full --until 06:30 --interval-minutes 2 --max-rounds 64

# Private metadata status; never includes source text or evidence snippets
PYTHONPATH=src python3.11 -m hermes_second_brain dream-status --config config/manifest.json --json

# Latest bounded German executive reflection
PYTHONPATH=src python3.11 -m hermes_second_brain dream-report --config config/manifest.json

# Morning once-only delivery; stdout is empty after the report was claimed
PYTHONPATH=src python3.11 -m hermes_second_brain dream-report --config config/manifest.json --claim
```

Extended full/backfill mode runs one bounded batch per independently committed
Dream run. Each successful batch is checkpointed and published before the next
interval sleep. If a later batch fails or reaches the hard deadline, only that
in-progress batch is rolled back; prior reports and source completions remain
durable, and the nightly worker can still run the ordinary manifest sync. The
loop stops immediately when no eligible sessions remain, without zero-work
sleeping or polling.

The explicit foreground/manual sweep `--until` accepts an aware/naive ISO datetime or local `HH:MM` in the configured timezone; its local time may resolve to tomorrow. The scheduled nightly worker does not inherit that rollover: it uses today’s absolute Europe/Berlin `03:00–06:30` window and exits successfully without work at or after `06:30` (or before `03:00`). Only `--manual-overnight` enables an explicit next-day cutoff. The wall-clock deadline is converted to a monotonic budget and caps every model call, retrieval request, retry, and interval sleep. Model processes run in their own process group; timeout or incremental stdout/stderr overflow terminates descendants. The foreground shell wrapper relies only on the owner/PID/generation-aware SQLite lease; generic `sync` remains a separate operation.

State and artifacts are private (`0700` directories, `0600` database/files where owned):

- `~/.hermes/second-brain/dream-state.sqlite3`: runs, rounds, phases, full-session change fingerprints, bounded evidence, insights, decisions, query hashes, leases, recoverable report claims, and the publication outbox.
- `~/.hermes/second-brain/dreams/reports/run_*.md`: bounded private German reports.
- `~/.hermes/second-brain/dreams/DREAMS.md`: historical managed diary entries with semantic-fingerprint dedupe.
- `~/.hermes/second-brain/import/dreams/run_*.md`: only durable `openviking_dream` candidates and deterministically grounded, Deep-supported insights for the normal manifest source `dream-conclusions`.

Generated imports are built under the private staging directory, outside the watched import tree. One SQLite transaction fences the lease and records decisions, source checkpoints, report metadata, and an exact publication intent; only then are the report and `DREAMS.md` updated and the ready import atomically moved into the watched directory last. A crash between commit and move is reconciled from that exact outbox artifact without reprocessing or reinforcement inflation. Candidate classification, local publication, and verified OpenViking synchronization are separate fields: `openviking_dream` means classified, the atomic file move means `locally_published`, and only an exact normal-State-DB receipt binding deterministic Dream `source_id`, content hash, and a real `viking://resources/...` remote ID means `synced`. Report and publication retention are independent; an import without that receipt is never pruned, so sync outages remain retryable beyond the report window.

Canonical candidate identity is a bounded, deterministic subject/predicate/object/polarity/scope key. Conservative explicit aliases allow safe paraphrases to reinforce one candidate, while negation, reversed subject/object, another relation, contradictory polarity, or incompatible scope cannot merge. Exact claim variants and evidence provenance remain private; metadata status exposes only the identity hash/version, merge strategy, and variant count. Embedding similarity is never a merge rule. Actionable `context_inbox` classifications are written idempotently through a Dream staging outbox into the typed intent queue with a provenance hash and mandatory approval. They are not fabricated as passive message events and trigger no action. Hypotheses, deferrals, rejects, and contradictions remain private/report-only. Retrieval is read-only stdlib HTTP POST to the configured local OpenViking endpoint; plaintext queries are never stored or placed in process arguments.

Deployment architecture (documented here, not installed by this task): a Hermes no-agent cron at `03:00 Europe/Berlin` can call `python3.11 <project>/scripts/dream_launcher.py`. That launcher validates fixed project/config/worker/sync paths, uses private PID-and-heartbeat supervisor state plus a no-follow log, starts `dream_nightly.py` with `start_new_session=True`, and returns quickly and silently. The detached scheduled worker owns the long Light → REM → Deep lifecycle only inside today’s configured window, checkpoints every committed round, and stops as soon as the backlog is empty. After a complete/new/no-op-safe Dream outcome it invokes `scripts/deploy_check.sh` as the independent OpenViking concern. A lock skip has a distinct temporary-failure exit and cannot authorize acknowledgement. A failed or skipped sync leaves the local Dream run successful and `locally_published` rows retryable; only a successful sync followed by exact receipt validation runs the quiet idempotent acknowledgement. `already_running` never starts a concurrent sync. This repository does not claim that the Dream cron, launcher, worker, plugin, review runner, morning delivery, or live sync is deployed.

The morning operator report is separate from the private narrative Dream report. A claim atomically selects the newest trigger for the local day and supersedes older queued triggers; rendering is frozen as an immutable snapshot tied to that report/run and includes a stable `Liefer-ID` for sink idempotency. `scripts/dream_morning_report.py` emits stdout only while holding that claim and acknowledges only after a successful write and flush. Normal claim/ack operation emits once, but stdout cannot make crash-after-flush atomic: delivery is honestly at-least-once across crashes and a retry may repeat the same `Liefer-ID` and identical snapshot. It covers Dream health/backlog, publication and staging queues, failed/stalled jobs, approvals, expired TTL rows, and contradiction counts, never raw transcript/tool/message text. `templates/hermes-dream-morning-report.sh.example` is the deployment template.

Dry-run validation, with no report claim or acknowledgement:

```bash
PYTHONPATH=src python3.11 scripts/dream_morning_report.py \
  --config config/manifest.json --validate-only
bash -n templates/hermes-dream-morning-report.sh.example
```

Operator deployment steps, deliberately not executed here: render the two absolute placeholders in the shell template, copy the rendered script to `~/.hermes/scripts/dream-morning-report.sh` with mode `0700`, validate it with `bash -n`, then create a no-agent job in the gateway's local timezone, for example:

```bash
hermes cron create "30 7 * * *" --name "Dream Morning Report" \
  --script ~/.hermes/scripts/dream-morning-report.sh --no-agent \
  --deliver origin --workdir /absolute/path/to/hermes-second-brain
```

Dry run:

```bash
python -m hermes_second_brain sync --manifest config/manifest.json --dry-run
```

Sync:

```bash
python -m hermes_second_brain sync --manifest config/manifest.json
```

Local Memory Graph:

```bash
python -m hermes_second_brain graph --manifest config/manifest.json --host 127.0.0.1 --port 8765
python -m hermes_second_brain graph --manifest config/manifest.json --host 127.0.0.1 --port 8765 --no-open
```

The graph server is local, read-only, and dependency-free. It refuses non-loopback binds unless `--allow-remote` is passed, opens the State SQLite database and Context Inbox SQLite database in read-only mode, sends no-store and strict CSP headers, and serves only packaged local assets. The graph renders `root -> configured source namespace -> folders -> active resources`; local rows with `status = 'deleted'` are tombstones and are never rendered. Context Inbox data is shown only as private aggregate counts by platform, relevance tier, reminder status, and habit hypothesis status. Raw message bodies, raw JSON, sender or conversation names, reminder or habit text, absolute local paths, hashes, and secrets are not serialized to the browser.

Migration export without mutating originals:

```bash
python -m hermes_second_brain migrate \
  --memory-md /path/to/MEMORY.md \
  --user-md /path/to/USER.md \
  --holographic-db /path/to/memory_store.db \
  --output .state/migration.jsonl \
  --output-dir .state/migration-md
```

LCM summary export without raw messages:

```bash
python -m hermes_second_brain lcm-export \
  --lcm-db /path/to/lcm.db \
  --output-dir .state/lcm-md
```

Mail ingest from sanitized events:

```bash
python -m hermes_second_brain mail-ingest --input mail-events.jsonl --output .state/mail-resources.jsonl
```

Full-mail Context Maxing collector, observation only:

```bash
python -m hermes_second_brain mail-context-collect --json
python -m hermes_second_brain mail-context-bundle --json
python -m hermes_second_brain sync --manifest config/manifest.json
python -m hermes_second_brain mail-context-collect --full --account icloud --account gmail --account acme --json
```

The collector shells out to Himalaya using read-only preview calls. It lists folders with `himalaya folder list -a ACCOUNT -o json --quiet`, lists envelope pages with `himalaya envelope list -a ACCOUNT -f FOLDER -p N -s PAGE_SIZE -o json --quiet`, and reads unseen or changed messages with `himalaya message read -a ACCOUNT -f FOLDER --preview --no-headers -o json --quiet ID`. It never sends, replies, deletes, moves, or marks mail read. Tests use a fake Himalaya executable and never access live mail.

Defaults are explicit named accounts `icloud,gmail,acme`, local vault `~/.hermes/second-brain/mail-context.sqlite3`, private sanitized per-message markdown output under `~/.hermes/second-brain/import/mail`, 120 second subprocess timeout, 4 read workers, and 3 transient read attempts. Override with `--account`, `MAIL_CONTEXT_ACCOUNTS`, `--folder ACCOUNT:FOLDER`, `--db`, `--output-dir`, `--himalaya`, `--page-size`, `--max-pages`, `--max-messages`, `--timeout`, `--workers`, `--retries`, and `--retry-backoff` or their `MAIL_CONTEXT_*` environment counterparts. Gmail folder discovery avoids label duplication by selecting `[Gmail]/All Mail`, `[Gmail]/Spam`, `[Gmail]/Trash`, and `[Gmail]/Drafts`; explicit `--folder` overrides are honored as given.

`mail-context-bundle` is the OpenViking-facing mail export step. It reads only the already-sanitized root-level `mail-<32 hex>.md` files, applies a second-stage chunk redaction that replaces full email addresses with `[EMAIL]`, shards them by the first digest hex digit, and writes up to 16 private aggregate files `mail-context-0.md` through `mail-context-f.md` under `~/.hermes/second-brain/import/mail-bundles`. The checked-in manifest source is `mail-context-bundles`, namespace `mail`, include `.md`, so OpenViking receives only these bounded aggregate resources. This replaces the unscalable per-message manifest source: a large mailbox can produce thousands of sanitized files, which would have created one OpenViking resource each at roughly 30 seconds apiece, and direct folder ingestion overloaded OpenViking.

Raw full bodies, account aliases, raw folder names, and stable message keys stay only in the private local SQLite vault. The per-message local export contains coarse mailbox/folder classes, display names, domains, category, tags, and redacted bounded text, then the bundle step preserves that sanitized content without exposing source filenames or stable message keys and additionally removes any remaining full address tokens. Full email addresses, credentials, one-time codes, and raw auth/security bodies are not exported; `security-auth` messages use a generic subject placeholder and omit body text entirely. Per-message read/materialization failures are stored locally with bounded non-secret metadata and retried on later runs. The commands are quiet on success unless `--json` is passed, and JSON summaries contain aggregate counts only.

Duplicate detection is intentionally folder scoped in this preview-only collector. Himalaya envelope previews do not provide a canonical provider Message-ID in the path used here, so moves or label changes may create a second local/exported record until a future provider identity is available. The collector does not attempt unsafe content dedupe.

The production hourly pattern is collect, then bundle, then regular manifest sync:

```bash
python -m hermes_second_brain mail-context-collect --json
python -m hermes_second_brain mail-context-bundle --json
python -m hermes_second_brain sync --manifest config/manifest.json
```

Deployment note: after switching an existing installation, mark old local state rows with `source_root_id = 'important-mail'` as `deleted` once in the local state database. Do not broad remote-delete mail resources from OpenViking; let the new `mail-context-bundles` source converge through normal exact-resource sync.

Context Inbox imports:

```bash
python -m hermes_second_brain context-import --manifest config/manifest.json --source jsonl --path context-events.jsonl
python -m hermes_second_brain context-import --manifest config/manifest.json --source jsonl --path context-events.jsonl.gz
python -m hermes_second_brain context-import --manifest config/manifest.json --source slack --path /path/to/slack-export
python -m hermes_second_brain context-import --manifest config/manifest.json --source slack --path ~/.hermes/second-brain/import/slack/slack-backfill-complete.jsonl.gz
python -m hermes_second_brain context-import --manifest config/manifest.json --source signal --path /path/to/signal.sqlite
python -m hermes_second_brain context-import --manifest config/manifest.json --source whatsapp --path /path/to/ChatStorage.sqlite
```

### Encrypted Signal Desktop History Collector (macOS)

`scripts/signal_desktop_collector.py` is the observation-only path for the real encrypted Signal Desktop database at `~/Library/Application Support/Signal/sql/db.sqlite`. It reads the operator's already logged-in local Desktop account; it does not link another device, contact Signal's service, send, reply, react, mark read, alter Signal, or register a model-callable action. The older `context-import --source signal` command remains available only for deliberately supplied plaintext SQLite exports.

Use a Python environment that has `cryptography` (the `signal-desktop` project extra declares it) and `/opt/homebrew/bin/sqlcipher`. The collector retrieves the `Signal Safe Storage` item with `/usr/bin/security`, decrypts the macOS Chromium `v10`-wrapped `encryptedKey` in memory, and sends the resulting SQLCipher key only over the child process's stdin. The key is never put in argv, an environment variable, a state file, Context Inbox, or logs. SQLCipher is started with `-readonly`, `-nofollow`, and `-noinit`; each bounded query also enables `query_only` and reads the live WAL rather than making a plaintext database copy.

Run the health check interactively once with the same Python executable and explicit login Keychain path that launchd will use:

```bash
HERMES_PYTHON="$HOME/.hermes/hermes-agent/venv/bin/python"
LOGIN_KEYCHAIN="$HOME/Library/Keychains/login.keychain-db"

"$HERMES_PYTHON" -c 'import cryptography'
"$HERMES_PYTHON" scripts/signal_desktop_collector.py \
  --check \
  --login-keychain "$LOGIN_KEYCHAIN" \
  --sqlcipher /opt/homebrew/bin/sqlcipher \
  --json
```

macOS may display a Keychain authorization prompt for the `Signal Safe Storage` password. Run this from the operator's unlocked GUI login session. Choosing **Always Allow** authorizes subsequent noninteractive runs; choosing **Allow** may prompt again, and a denied or locked login Keychain makes health/sync fail closed with a sanitized error. Granting **Always Allow** to `/usr/bin/security` is a same-UID trust tradeoff because other processes running as the same macOS user can invoke that binary; users who do not accept that tradeoff should choose one-time **Allow** and run the collector manually instead of scheduling it. Neither the password nor database key is printed by the health check. After it succeeds, perform the one-time initial backfill:

```bash
"$HERMES_PYTHON" scripts/signal_desktop_collector.py \
  --full \
  --login-keychain "$LOGIN_KEYCHAIN" \
  --sqlcipher /opt/homebrew/bin/sqlcipher \
  --json
```

The initial run scans all committed messages and then stores a private high-water state at `~/.hermes/second-brain/signal-desktop-history-state.json`. Later runs use keyset batches, re-read the newest 1,000 rows to catch recent edits, and do a full idempotent reconciliation every seven days for older in-place edits. `--lookback-rows` and `--full-rescan-days` tune those defaults; `--full` also safely recovers deliberately from invalid state. A nonblocking private lock prevents overlap. The existing Context Inbox watcher will rank/export the upserted events on its next cycle.

Current and older schemas are introspected. Message bodies are projected from named columns/selected JSON fields, never by returning the whole raw message JSON. A canonical message must have a stable direct or JSON message ID; the mutable SQLite `rowid` is used only for keyset traversal and provenance, never event identity. Current `message_attachments` rows and legacy JSON attachments are both supported. Stored metadata is restricted to type/order/content type/file name/size/dimensions/flags/caption and an existing regular, non-symlink local path contained by `attachments.noindex`; attachment `key`, `digest`, `iv`, CDN material, local encryption keys, and other cryptographic fields are never stored. Quote linkage and selected quote/edit history are retained in the private canonical event, while action target/account fields remain empty.

Attachment binaries remain local references. This collector does not copy, decrypt, open, OCR, transcribe, or export them; many Signal attachment files require key material that is intentionally not retained. Any later binary processing must be a separately approved local workflow.

For a 15-minute batch schedule, render `launchd/com.example.hermes-second-brain.signal-desktop-history.plist.template` after the interactive check:

```bash
PROJECT_DIR="$(pwd -P)"
HOME_DIR="$HOME"
HERMES_PYTHON="$HOME/.hermes/hermes-agent/venv/bin/python"
SQLCIPHER="/opt/homebrew/bin/sqlcipher"

mkdir -p "$HOME/.hermes/second-brain/logs" "$HOME/Library/LaunchAgents"
chmod 700 "$HOME/.hermes" "$HOME/.hermes/second-brain" "$HOME/.hermes/second-brain/logs"
umask 077
sed \
  -e "s#__PROJECT_DIR__#${PROJECT_DIR}#g" \
  -e "s#__HOME__#${HOME_DIR}#g" \
  -e "s#__HERMES_PYTHON__#${HERMES_PYTHON}#g" \
  -e "s#__SQLCIPHER__#${SQLCIPHER}#g" \
  launchd/com.example.hermes-second-brain.signal-desktop-history.plist.template \
  > "$HOME/Library/LaunchAgents/com.example.hermes-second-brain.signal-desktop-history.plist"
chmod 600 "$HOME/Library/LaunchAgents/com.example.hermes-second-brain.signal-desktop-history.plist"
plutil -lint "$HOME/Library/LaunchAgents/com.example.hermes-second-brain.signal-desktop-history.plist"
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.example.hermes-second-brain.signal-desktop-history.plist"
```

Scheduled success is quiet. Errors and `context_import_status` contain only categorical/aggregate details, never bodies, sender/target identifiers, Keychain diagnostics, or SQLCipher stderr. Only committed WAL state is visible, so a message mid-write appears on a later run. Conversation/sender names are best-effort on schema variants; arbitrary old edits become visible at the weekly reconciliation; rows deleted from Signal after prior ingestion are not automatically deleted from Context Inbox.

Context ranking, brief, export, and stats:

```bash
python -m hermes_second_brain context-rank --manifest config/manifest.json
python -m hermes_second_brain context-brief --manifest config/manifest.json --json
python -m hermes_second_brain context-export-openviking --manifest config/manifest.json
python -m hermes_second_brain context-alerts --manifest config/manifest.json --initialize
python -m hermes_second_brain context-alerts --manifest config/manifest.json
python -m hermes_second_brain context-daily-brief --manifest config/manifest.json
python -m hermes_second_brain context-feedback --manifest config/manifest.json --reminder rem_... --status done --json
python -m hermes_second_brain context-stats --manifest config/manifest.json --json
```

`context-export-openviking` defaults to `~/.hermes/second-brain/import/context/context-inbox.txt`. The production and example manifests include this directory as source `context-inbox`, namespace `context`, with `.txt` only, so OpenViking receives only the narrow redacted context export. JSONL content in this text file is intentional; raw vault rows, attachment binaries, private Slack URLs, raw JSON, and credentials are not exported.

For Telegram/no-agent scheduling, run `context-alerts --initialize` once immediately after the initial backfill. It marks currently eligible immediate inbound reminders/events as already seen without printing private bodies. After that, a cron or launchd job may run `context-alerts` and send exactly stdout to Telegram; stdout is empty when there are no new alerts. Alert delivery is persisted in SQLite, so reruns are idempotent and the same source event is not emitted once as an event and again as a reminder. `context-feedback --reminder <id> --status done|later|dismissed|candidate --json` is the feedback hook for future Hermes actions.

`context-daily-brief` prints a German daily briefing from high/medium events in the last 24 hours, open reminder candidates, and habit hypotheses. File preferences control included sections, per-section limits, total character cap, quiet-when-empty behavior, and excluded platforms; explicit command-line limits win. Omitted counts are included in JSON and the plain German output says that more items remain searchable. Daily delivery is persisted by local date, so rerunning the scheduled wrapper emits empty stdout after the first claim. `context-daily-brief --dry-run` avoids notification claim and emission, but may refresh local derived ranking, reminder, and habit rows in the Context Inbox database; it does not write the preference file, and a subsequent normal run may still claim once. `--force` remains the backward-compatible bypass and `--json` reports both `dry_run` and `claimed`. If a later outbound message exists in the same platform/account/conversation, reminder follow-up is marked `possibly_addressed` instead of nagging as an open candidate.

`scripts/context_cycle.sh` is an operational template for a manual snapshot/import/rank/export cycle. It imports staged canonical JSONL, staged `context-inbox-spool.jsonl`, the plugin default queue `~/.hermes/second-brain/context-inbox-spool.jsonl.d/*.jsonl`, legacy single-file spool leftovers, Slack complete archive, optional Slack live spool `slack/slack-live.jsonl.gz` with legacy `slack-live.jsonl` fallback, Signal, and WhatsApp. Malformed/non-object queue files are atomically moved without loss to a private `quarantine/` directory with a sanitized JSON sidecar, later valid queue files still import, and the script exits nonzero after rank/export if any quarantine happened. Set `WHATSAPP_IMPORT_PATH` to override the macOS default `~/Library/Group Containers/group.net.whatsapp.WhatsApp.shared/ChatStorage.sqlite`; if absent it falls back to staged `$CONTEXT_IMPORT_DIR/ChatStorage.sqlite`. WhatsApp is opened read-only and copied to a temporary SQLite snapshot before reading.

`scripts/context_watch.py` is the robust no-agent five-minute watcher intended for copying to `~/.hermes/scripts/context_watch.py`. It uses fixed safe defaults or env overrides, invokes `python -m hermes_second_brain` via subprocess argv with no shell, imports the gateway queue and legacy spool leftovers plus WhatsApp and Slack archives/spools, ranks, exports context, then runs the one-time alert claim and prints only the exact alert text. Queue files are prevalidated before DB import; malformed/non-object files are quarantined under the private queue directory without logging message bodies, syntactically valid import/SQLite failures remain retryable, and the watcher exits nonzero only after healthy rank/export/alert work completes when quarantine occurred. For live Slack, it imports `$CONTEXT_IMPORT_DIR/slack/slack-live.jsonl.gz` when present and otherwise falls back to legacy `$CONTEXT_IMPORT_DIR/slack/slack-live.jsonl`; it does not import both in the same cycle. Errors go to stderr and return nonzero so cron can alert on failure. It deliberately avoids a full OpenViking sync; the broader second-brain sync should run on its own cadence. `scripts/context_daily_brief.py` similarly prints only the once-only daily briefing text, or empty stdout after the daily/content claim already exists.

Recall evaluation:

```bash
python -m hermes_second_brain eval --spec tests/fixtures/eval_spec.json --output .state/eval-report.json --threshold 0.75
```

## State And Deletion

SQLite state is maintained with WAL and explicit transactions. New or changed resources become `pending`; workers atomically claim rows as `in_progress` with bounded leases so concurrent sync processes cannot enqueue the same row. Successful OpenViking writes become `synced` through a compare-and-set on `source_id`, `sha256`, and `status`; failed rows return to `pending` for retry. Missing source files or missing configured roots are marked `deleted` locally only; no broad remote deletion is performed.

The Context Inbox SQLite vault also uses WAL, busy timeouts, and private local permissions. When safely owned, the vault directory is kept `0700`; the SQLite database, WAL/SHM sidecars, queue entries, state files, exports, and generated context files are kept `0600`. Message rows are upserted idempotently by `platform/account/source_message_id` when source IDs exist, otherwise by a deterministic content identity over platform, account, conversation, sender, timestamp, and body. The canonical event schema stores platform/account/workspace, conversation metadata, sender metadata, direction, body, timestamps, source IDs, thread/permalink, attachment metadata JSON, raw JSON, source path, flags, relevance score/tier/reasons, and processing state. Reminder candidates and habit hypotheses are stored in separate provenance tables.

The legacy `context-import --source signal` function intentionally still does not read `config.json`, Keychain, or encryption material. It accepts only a deliberately supplied plaintext SQLite export and records `blocked_decryption_needed` for the real encrypted database. The separately documented `signal-desktop-history` collector is the narrowly authorized encrypted local path. Live `signal-cli` linking remains a separate, optional collector.

### Local read-only context sources

The multi-source scanner is an observation-only layer in front of the Context Inbox. Its private source vault defaults to `~/.hermes/second-brain/context-sources.sqlite3`; it stores bounded/minimized source items, redacted derivatives, opaque cursors, aggregate run metrics, health, and retention timestamps. Cursors advance in the same transaction as source items and derivatives. Only the redacted derivatives are batch-upserted into the existing Context Inbox. Completed reconciliation generations propagate source tombstones into the inbox without deleting event provenance or open reminders; a capped/partial generation never tombstones unseen rows. Both vaults use WAL, private directory/file modes, busy timeouts, and SQLite/symlink defenses. `--dry-run` does not create either vault.

Health, Sleep, Workouts, and Screen Time continuously watch source-specific folders under the private local ingress root `~/.hermes/second-brain/context-ingress` (override with `CONTEXT_INGRESS_DIR` or `context-scan --ingress-dir`). A real scan creates the root and the four source folders as `0700`; a dry run creates nothing. An iPhone Shortcut or app should finish a bounded `.json`, `.jsonl`, `.csv`, or `.zip` export under a temporary name and atomically rename it into `health/`, `sleep/`, `workouts/`, or `screen-time/`. The scanner accepts at most 256 finalized files, 20 MiB per file, and 100 MiB total per source; it never edits, moves, deletes, or opens private iOS databases. Immutable files are deduplicated by SHA-256 content, while ZIP member and record positions make capped scans resumable.

```bash
python -m hermes_second_brain context-scan --all --json
python -m hermes_second_brain context-scan --source safari --source reminders --full --json
python -m hermes_second_brain context-source-health --json
python -m hermes_second_brain context-retention --dry-run --json
python -m hermes_second_brain context-import-export --source health --path health-summary.json --format json --dry-run --json
```

Source states are `healthy`, `degraded`, `pending_permission`, `unsupported`, `error`, and `disabled`. A missing permission or user export is expected and does not stop other sources, ranking, exports, or alerts. Public command output contains source names, state/reason codes, and counts only—never source text, URLs, contact data, paths, remote responses, or credentials.

Apple Calendar, Messages and Notes are strict read-only sources. Calendar invokes only `cal --json list YYYY-MM-DD YYYY-MM-DD`, retains a bounded 30-day lookback plus 90-day horizon, and never reads descriptions, URLs or credentials. Messages takes a WAL-aware read-only snapshot of `~/Library/Messages/chat.db`, reads no attachments/blobs or raw contact identities, keeps derivatives out of OpenViking by default, and runs a bounded weekly reconciliation. Notes invokes only `memo notes --no-cache`, retaining metadata titles only after redaction and secret-pattern exclusion; it never reads note bodies or attachments.

| Source | Read boundary | Stored data / status | Default raw retention |
| --- | --- | --- | --- |
| Calendar | configured `cal --json list` argv allowlist | Redacted summary/timing/provider and hashed calendar/location IDs; no description, URL or credentials | 30 days |
| Reminders | `remindctl show all --json --no-input` argv allowlist | Redacted title, due/completed state, hashed list | Open/current cache, 30 days |
| Safari | read-only SQLite snapshot and bounded plist | URL without query/fragment, title, timestamp/bookmark; only bookmarks publish to Context Inbox | 30 days |
| Messages | read-only WAL-aware Messages SQLite snapshot | Redacted text and hashed conversation/handle IDs; no attachments/blobs/identities; not exported to OpenViking by default | 30 days |
| Notes | `memo notes --no-cache` metadata list only | Redacted title and hashed folder/title; suspected secrets, bodies and attachments excluded | 30 days |
| Photos | read-only positive schema/column allowlist | Hashed asset ID, time, media type, favorite, duration; optional coarse region; only favorites publish | 30 days |
| Contacts | Contacts.framework boundary only | Hashes and email domains; no notes, images, addresses, or private SQLite | 30 days |
| Screen Time | continuous private ingress of user-export daily aggregates only | Content-hashed, schema-validated aggregate files; no private Screen Time database | 180 days |
| WhatsApp | existing read-only SQLite snapshot importer | Main-file and WAL-aware change detection; existing stable message identities | 30 days source cache |
| Notion / Slack | configurable injected credential-free transport and fixed Search/List/Fetch allowlists | `pending_permission` only without/with an unavailable transport; no credential persistence | 90 days |
| Health / Sleep / Workouts | continuous private ingress of JSON/JSONL/CSV/ZIP exports | Content-hashed schema-validated summaries; value- and key-level GPS/location/diagnosis data rejected | 90 days |
| Banking / Purchases | JSON/JSONL/CSV/ZIP safe import only | Amount, currency, date, category, hashed merchant; account/card identifiers rejected | 90 days |

Notion and Slack transports are deliberately dependency-injected through `default_registry(transports={...})` or their adapter constructors. The scanner contains no remote login flow, token fields, or mutation tools; tests use local fixtures and never call remote APIs. Contacts uses an optional narrow public Contacts.framework provider when PyObjC is installed and access is already authorized; it never requests permission automatically and never reads Apple's private Contacts SQLite. HealthKit databases, private Screen Time databases, bank logins, PSD2 connections, screen scraping, payments, image content, OCR, faces, and embeddings are outside this scanner.

### Passive Signal Live Collector

`scripts/signal_context_collector.py` is an observation-only live collector for `signal-cli` 0.14.6 running as a manually linked HTTP daemon. It never sends, replies, reacts, marks messages read, changes Hermes Signal allowlists, or invokes the Hermes Signal gateway. It reads only `GET /api/v1/check` for health and `GET /api/v1/events?account=<E164>` as `text/event-stream`, then writes canonical Context Inbox JSONL records into the existing observer queue directory `~/.hermes/second-brain/context-inbox-spool.jsonl.d/`.

Manual setup after QR linking:

```bash
signal-cli --account <YOUR_E164> daemon --http 127.0.0.1:8080
SIGNAL_ACCOUNT=<YOUR_E164> python3.11 scripts/signal_context_collector.py --health-check
SIGNAL_ACCOUNT=<YOUR_E164> python3.11 scripts/signal_context_collector.py
```

For launchd, install the `signal-cli` daemon and collector as separate LaunchAgents after the linked-device QR pairing succeeds. Copy `launchd/com.example.hermes-second-brain.signal-cli-daemon.plist.template` and `launchd/com.example.hermes-second-brain.signal-context-collector.plist.template` to `~/Library/LaunchAgents/`, replace placeholders, ensure `~/.hermes/second-brain/logs` exists with private permissions, then validate and load the daemon first:

```bash
mkdir -p ~/.hermes/second-brain/logs
chmod 700 ~/.hermes ~/.hermes/second-brain ~/.hermes/second-brain/logs
umask 077

SIGNAL_CLI="$(command -v signal-cli)"
PYTHON_3_11="$(command -v python3.11)"
PROJECT_DIR="$(pwd)"
HOME_DIR="$HOME"
SIGNAL_E164_ACCOUNT="<YOUR_E164>"

sed \
  -e "s#__SIGNAL_CLI__#${SIGNAL_CLI}#g" \
  -e "s#__SIGNAL_E164_ACCOUNT__#${SIGNAL_E164_ACCOUNT}#g" \
  -e "s#__HOME__#${HOME_DIR}#g" \
  launchd/com.example.hermes-second-brain.signal-cli-daemon.plist.template \
  > ~/Library/LaunchAgents/com.example.hermes-second-brain.signal-cli-daemon.plist
chmod 600 ~/Library/LaunchAgents/com.example.hermes-second-brain.signal-cli-daemon.plist

sed \
  -e "s#__PROJECT_DIR__#${PROJECT_DIR}#g" \
  -e "s#__PYTHON_3_11__#${PYTHON_3_11}#g" \
  -e "s#__HOME__#${HOME_DIR}#g" \
  -e "s#__SIGNAL_E164_ACCOUNT__#${SIGNAL_E164_ACCOUNT}#g" \
  launchd/com.example.hermes-second-brain.signal-context-collector.plist.template \
  > ~/Library/LaunchAgents/com.example.hermes-second-brain.signal-context-collector.plist
chmod 600 ~/Library/LaunchAgents/com.example.hermes-second-brain.signal-context-collector.plist

plutil -lint ~/Library/LaunchAgents/com.example.hermes-second-brain.signal-cli-daemon.plist
plutil -lint ~/Library/LaunchAgents/com.example.hermes-second-brain.signal-context-collector.plist

launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.example.hermes-second-brain.signal-cli-daemon.plist
SIGNAL_ACCOUNT="${SIGNAL_E164_ACCOUNT}" python3.11 scripts/signal_context_collector.py --health-check
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.example.hermes-second-brain.signal-context-collector.plist
```

Rollback:

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.example.hermes-second-brain.signal-context-collector.plist
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.example.hermes-second-brain.signal-cli-daemon.plist
```

The collector captures inbound and outbound Signal daemon envelopes, including Note-to-Self, sync-sent outbound DMs/groups, group messages, direct messages, edits, quote metadata, and attachment metadata references only. It does not copy attachment binaries. The configured E.164 account is required locally for the daemon URL but is stored in canonical records only as a stable hash and is never printed in full by collector logs.

WhatsApp macOS `ChatStorage.sqlite` import opens the source read-only and uses SQLite backup into a temporary snapshot so active WAL content is included without mutating the source. It reads the Core Data `ZWAMESSAGE`, `ZWACHATSESSION`, and `ZWAMEDIAITEM` tables, converts timestamps from the 2001-01-01 UTC epoch, and stores media paths/metadata as references only. It does not copy attachment binaries or store media keys.

Slack import supports standard export directories, API/Composio-style JSON response files, plain canonical JSONL, and gzip JSONL/JSONL.GZ files. The staged production backfill path is `~/.hermes/second-brain/import/slack/slack-backfill-complete.jsonl.gz`; the scheduled live collector stages `~/.hermes/second-brain/import/slack/slack-live.jsonl.gz`, with `slack-live.jsonl` kept as a legacy fallback for the watcher. Rows may wrap messages with workspace, self-user, and channel metadata. Direction is inferred only when `self_user_id` or `SLACK_SELF_USER_ID` is explicit; otherwise standard export direction is stored as `unknown`. Channel type comes from Slack channel flags, and local attachment references are preserved when sibling `attachments/<file_id>.<ext>` files exist. Slack private/signed URLs are not exported to OpenViking. Malformed records/files are skipped with summary errors instead of aborting the whole backfill.

Ranking is deterministic and transparent: immediate is score `>=85`, briefing is `60-84`, archive is `<60`. English and German direct asks, explicit date/deadline language, family/close-contact hints, mentions, urgency, inbound DMs, and commitments boost scores; bot, marketing, newsletter, promo, Werbung, Rabatt, abmelden, and outbound-only chatter are suppressed. Reminder extraction keeps source event provenance and due hints only when explicit. Habit hypotheses support English and German recurrence phrases and require repeated independent observations, currently at least three events across at least two days, and are never promoted from one message.

The OpenViking context export is deliberately narrow and redacted. It emits redacted event summaries, reminder candidates, and habit hypotheses; it does not export raw JSON, full local vault contents, attachment private URLs, signed URLs, credentials in URLs, secrets, passwords, tokens, API keys, or one-time codes. Local raw vault rows remain raw and local; protect the SQLite file accordingly.

Current Context Inbox limits: ranking and reminder extraction are deterministic heuristics, not semantic task completion. WhatsApp local DB snapshots are continuous only when scheduled repeatedly. Slack complete backfill is supported, but ongoing all-channel polling still requires an external connector/feed. Encrypted Signal Desktop history collection is macOS-only and depends on an unlocked authorized login Keychain, SQLCipher, and a compatible introspected local schema; other Signal sources still require a deliberately supplied plaintext export or signal-cli-style event feed. Attachments are metadata/local references; they are not all copied, OCRed, transcribed, or indexed. The production scope is therefore all accessible canonical message rows plus available attachment metadata/references, not a binary archive of every platform attachment. `possibly_addressed` is conservative and only uses later outbound messages in the same platform/account/conversation. Alert delivery is once-only per source event; edits to already delivered source messages do not resend unless represented as a new source event. The scripts do not install account integrations, mutate live chat stores, copy attachment binaries, send Telegram messages directly, or trigger a full OpenViking sync.

## Personal Messaging Controller

`personal-message` prepares local mutation intents, searches the local Context Inbox,
renders Slack plans, and executes the local WhatsApp/Signal adapters.

### Trust and approval boundary

Hermes' native tool boundary **MUST obtain allow-once user approval for every external mutation
before invoking `execute-action`**. `execute-action` must not be registered as a model-callable
tool. Its public `user-request-acknowledged` literal is only an accidental-invocation guard. It is
not a secret, signature, capability, cryptographic authorization, or proof of consent. The local
SQLite database, HMAC key, staging files, and acknowledgement cannot protect against a malicious
process running as the same OS user.

`prepare-action` is offline: it validates provenance and stores an expiring intent. After native
approval, `execute-action` atomically claims that intent before local dispatch. This provides
local **at-most-once dispatch**, not an end-to-end delivery uniqueness guarantee. No ambiguous or
stale dispatch is replayed automatically; a stronger guarantee would require a provider-supported
idempotency key.

### WhatsApp Python convenience wrapper

`prepare_whatsapp` is an offline Python helper: it resolves one destination and creates a
redacted pending intent without contacting the bridge. `send_whatsapp` performs that same
preparation and exactly one execution attempt. A caller may invoke `send_whatsapp` **only after
it has enforced native allow-once approval** for the exact message; its internal acknowledgement
literal is not authorization.

```python
from hermes_second_brain.personal_messaging import prepare_whatsapp, send_whatsapp

# Offline: creates a pending intent and returns redacted metadata.
prepared = prepare_whatsapp(group="DEMO_PROJECT", text="Draft update")

# Only after native allow-once approval for this exact mutation.
result = send_whatsapp(group="DEMO_PROJECT", text="Approved update")
```

### Reading

```bash
# Passive local status. It does not create/chmod the actions DB or probe sockets.
personal-message status

# Connectivity is a separate, explicit read-only network probe.
personal-message probe

# Bounded search (limit capped at 200). LIKE wildcards in the query are escaped.
personal-message search --query "voucher" --platform whatsapp --since 2026-07-01T00:00:00Z

# Conversations with latest-message metadata and counts.
personal-message conversations --platform signal

# One event by id, including its full local body (this is a local, user-invoked lookup).
# raw_json is withheld unless --include-raw is passed.
personal-message show --event-id ev-wa-1
```

### Writing (two phases)

```bash
# Prefer stdin or a no-follow UTF-8 file so message bodies do not enter argv.
personal-message prepare-action \
  --platform whatsapp --action reply --event-id ev-wa-1 --message-stdin
# -> {"status":"prepared","intent":{"intent_id":"pmi_…","payload_hash":"…","expires_ts":"…"}}

# Phase 2 — invoke only after Hermes has granted native allow-once approval.
personal-message execute-action \
  --intent-id pmi_… --acknowledgement user-request-acknowledged

# Validate and render the request without claiming the intent or touching the network.
personal-message execute-action --intent-id pmi_… \
  --acknowledgement user-request-acknowledged --dry-run
```

The prepare result and ordinary intent listings omit raw targets and source quote bodies. Dry-run
and Slack-plan output is redacted by default. `--reveal-local-plan` is intended only inside the
native approved connector boundary.

### Delivery outcomes

- **succeeded** — a strict, platform-specific positive acknowledgement was validated.
- **failed** — validation or a proven local/pre-connect failure occurred before dispatch.
- **uncertain** — delivery may have happened (including timeout, reset, broken pipe, malformed or
  empty response, HTTP error after POST, or stale in-flight state). Verify in the provider app.

`reconcile --age-seconds …` moves stale `in_flight`/`external_pending` rows to `uncertain`; it
never retries them.

### Bridges and Slack

- **WhatsApp** and **Signal** use pinned literal loopback HTTP endpoints by default. Credentials,
  queries, fragments, redirects, proxies, deceptive hostnames, and non-HTTP schemes are refused.
  Endpoint and account identity are bound into the prepared intent and cannot be substituted at
  execution. `HERMES_PM_ALLOW_NON_LOOPBACK=1` is an explicitly unsafe development override.
- **Slack** is read locally from the Context Inbox, but this CLI holds **no Slack credentials and
  performs no Slack writes.** It renders an explicitly `unexecuted_plan` for send/reply, react,
  unreact, edit, delete, or mark-read using the bound Composio connection. Typing is unsupported.
  Slack file sending is unsupported until a staged local file can be converted safely into a
  valid Composio `FileUploadable`; local paths are never emitted in a Slack plan. After the native
  approved connector runs the plan, it records one categorical result:

  ```bash
  personal-message record-external-result \
    --intent-id pmi_… --plan-hash … --result success \
    --acknowledgement user-request-acknowledged
  ```

  Concurrent result calls allow only one terminal transition.

### Storage, audit, and safety

Intents live under `~/.hermes/second-brain/` with private permissions. Target summaries use a keyed
HMAC handle from a private random key rather than an unsalted phone-number hash. Audit details are
categorical and omit bodies, emoji, paths, raw targets, and remote error text.

Attachments are opened with no-follow semantics, bounded, hashed, copied atomically to private
0700/0600 staging, and reverified immediately before dispatch. Only the staged copy is sent; it is
purged after success, failure, uncertainty, or safe expiry. `purge --retention-hours 24` is the
documented default for deleting old terminal intent payloads and any eligible staged artifacts;
audit metadata remains.

## Hermes Observer Template

`templates/hermes-context-inbox-plugin/` contains a passive Hermes gateway observer. It registers `pre_gateway_dispatch` for the installed Hermes API, where Hermes invokes synchronous hooks as `invoke_hook("pre_gateway_dispatch", event=MessageEvent, gateway=..., session_store=...)`. The hook accepts `event` as a keyword plus `**kwargs`, converts real `MessageEvent`/`source` objects and dict test doubles into canonical JSONL, preserves media metadata, writes one atomic `0600` event file under `HERMES_CONTEXT_INBOX_SPOOL.d` or `~/.hermes/second-brain/context-inbox-spool.jsonl.d` with a `0700` directory, and avoids serializing full arbitrary raw objects.

When the Second Brain package and host tool API are available, the template also registers only `second_brain_intake`. Its schema requires the already-classified typed bundle and explicitly tells the model to use Hermes-native destination tools and approvals after staging. It returns one confirmation, never invokes destination actions, and is omitted cleanly when the package/tool API is unavailable. This addition does not alter the observer hook's fail-open behavior.

By default the observer writes the passive spool record and returns `None` (Hermes' normal-allow path) for every platform, including Slack, Signal, and WhatsApp, so existing Hermes behavior is not altered or suppressed. Set `HERMES_CONTEXT_INBOX_SKIP_PLATFORMS=slack,signal,whatsapp` only when you explicitly want the observer to skip those platforms after spooling. Observer errors fail open with `None`. This collector/plugin does not send, reply, react, or mark messages read.

Account pairing and scheduler creation are intentionally not automatic side effects of the project scripts. In the current default-profile deployment, the observer plugin is enabled, WhatsApp is read through the local read-only watcher, Slack is supplied by an external scheduled connector, and Hermes-native cron jobs deliver watcher alerts and the daily brief. The optional live `signal-cli` code remains inactive until the user completes the linked-device QR flow. The separate Signal Desktop history LaunchAgent also remains inactive until the interactive Keychain health check and explicit template installation described above.

OpenViking 0.4.10 operations are intentionally narrow:

- New resources use `ov add-resource <path> --to <viking-uri> --no-progress -o json`.
- Existing resources are checked with `ov stat <viking-uri> -o json`. If OpenViking reports the exact URI as a directory/container, updates remove only that exact container with `ov rm <viking-uri> --recursive --wait --timeout <seconds> -o json`, then ingest the source again with `ov add-resource <path> --to <same-viking-uri> --no-progress -o json`. This applies to `.md`, `.txt`, `.pdf`, and `.docx` and avoids stale generated/chunked children. Recursive removal is used only after the deterministic exact URI passes safety validation; namespace roots, trailing slash paths, and wildcard paths are not valid remove targets.
- Existing `.md` and `.txt` resources that are actual files, not containers, use `ov write <viking-uri> --from-file <path> -o json`.
- Existing `.pdf` and `.docx` resources are always replaced by removing only the exact stored URI, recursively only when `ov stat` reports a directory/container, then adding back to the same URI.
- Tags are replaced with `source_id=<safe deterministic id>`, `sha256=<hash>`, and `namespace=<safe namespace>` using `ov set-tags <viking-uri> --tags ... --mode replace -o json`.
- A sync batch performs one global `ov wait --timeout <seconds> -o json` after successful asynchronous add/write enqueues.
- Recall evaluation uses `ov find <query> -u viking://resources/<namespace> --limit <k> -L 0,1,2 -o json`.

Target URIs are deterministic under `viking://resources/<sanitized-namespace>/` and are derived from the scanner `source_id` plus the original file suffix. URI/path traversal patterns are rejected before any OpenViking write or remove command is run. If an initial add reaches OpenViking but returns a transient conflict before local state is marked synced, the next run checks the exact deterministic target URI with `ov stat`, tags it, and returns without deleting or reingesting. Changed existing resources use the file/container update rules above.

## Deployment Shape

The example manifest is production-shaped: it points the state database, Context Inbox vault, and dreaming/B+ stores under `~/.hermes/second-brain/`, resolves the OpenViking `ov` binary from `PATH` (override with `ov_binary` or `OV_BINARY`), and configures source namespaces `brain`, `notes`, `sessions`, `legacy-memory`, `mail`, `context`, and `dreams`. The mail namespace comes from source `mail-context-bundles`, not a per-message source. When adapting an existing deployment, preserve your namespaces and the current `source_id` algorithm to avoid changing IDs for already indexed resources. The `dreaming`/`b_plus` keys describe intended paths and policy; their presence does not assert that the Process Observatory plugin, review runner, Dream jobs, morning job, or new databases have been installed or activated.

A typical deployment also defines these Hermes-native scheduled jobs outside the repository checkout:

- `Mail Context Sync`: hourly, external Hermes-native execution of `scripts/mail_context_sync.sh`; runs mail collection, mail bundling, then the regular manifest sync.
- `Slack Context Live Collector`: every 15 minutes, local delivery only.
- `Context Inbox Watch`: every 15 minutes, no-agent execution of `~/.hermes/scripts/context_watch.py`, with non-empty stdout delivered to Telegram.
- `Context Daily Brief`: daily at 08:00 local time, no-agent execution of `~/.hermes/scripts/context_daily_brief.py`, with empty stdout suppressed.

These jobs are operational profile state, not launchd templates. A fresh deployment must create equivalent Hermes scheduler jobs explicitly after installing the scripts; loading only the sync and health LaunchAgents does not provide mail collection, Telegram alert, or daily-brief delivery.

## Launchd

Templates live in `launchd/`. The sync and health templates cover OpenViking synchronization and health checks only; the Signal Desktop history template is a separate read-only 15-minute collector, and the Hermes-native mail/alert/daily jobs described above are separate. The generic deploy check syncs already-materialized manifest sources only, including existing mail bundles; it does not collect mail, rebuild mail bundles, collect Signal Desktop history, or replace the `Mail Context Sync` hourly job. Do not wire mail or Signal secrets into generic launchd sync/deploy checks. Logs are written under `~/.hermes/second-brain/logs/`, and the templates set launchd `Umask` to decimal `63` (octal `077`). Replace placeholders with absolute paths, create the log directory privately, write generated plists with mode `0600`, then load:

```bash
mkdir -p ~/.hermes/second-brain/logs
chmod 700 ~/.hermes ~/.hermes/second-brain ~/.hermes/second-brain/logs
umask 077
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.example.hermes-second-brain.sync.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.example.hermes-second-brain.health.plist
```

Rollback:

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.example.hermes-second-brain.sync.plist
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.example.hermes-second-brain.health.plist
```

The check script writes nothing to stdout on healthy scheduled runs and uses stderr for actionable failures.

## Verification

The tracked `.hermes-gates.json` is the repository policy used by future `hermes-coder-flow` runs. Its real behavioral gate is exactly:

```bash
PYTHONPATH=src python3.11 -m unittest discover -s tests -v
```

It also compiles the B+ scripts and passive plugin with bytecode redirected outside the worktree, then runs `git diff --check`. All implementation remains Python 3.11+ and standard-library only.
