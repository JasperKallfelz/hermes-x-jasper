#!/usr/bin/env bash
# Adversarial integration tests for the optional Claude Deep Chat boundary.
set -uo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
CLI="$ROOT_DIR/hermes-deep-chat"
WORKER="$ROOT_DIR/claude_worker.py"
RUNTIME="$ROOT_DIR/deep_chat_bridge.py"
SECURE_RUNTIME="$ROOT_DIR/secure_runtime.py"
PYTHON_BIN="${HERMES_DEEP_CHAT_PYTHON:-$(command -v python3.11 || true)}"
JQ_BIN="$(command -v jq || true)"

[[ -x "$PYTHON_BIN" ]] || { echo "FATAL: python3.11 is required" >&2; exit 1; }
[[ -x "$JQ_BIN" ]] || { echo "FATAL: jq is required" >&2; exit 1; }

PASS=0
FAIL=0
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/deep-chat-tests.XXXXXX")"
TEST_ROOT="$(cd -- "$TEST_ROOT" && pwd -P)"

cleanup() {
  [[ -n "${TEST_ROOT:-}" && -d "$TEST_ROOT" && "$TEST_ROOT" == */deep-chat-tests.* ]] || return
  rm -rf -- "$TEST_ROOT"
}
trap cleanup EXIT

ok() { PASS=$((PASS + 1)); printf 'ok   - %s\n' "$1"; }
bad() { FAIL=$((FAIL + 1)); printf 'FAIL - %s\n' "$1"; }
check() {
  local description="$1"
  shift
  if "$@" >/dev/null 2>&1; then ok "$description"; else bad "$description"; fi
}
expect_failure() {
  local description="$1"
  shift
  if "$@" >/dev/null 2>&1; then bad "$description (unexpected success)"; else ok "$description"; fi
}
jq_file() {
  local description="$1" path="$2"
  shift 2
  check "$description" "$JQ_BIN" -e "$@" "$path"
}
jq_text() {
  local description="$1" value="$2"
  shift 2
  if printf '%s' "$value" | "$JQ_BIN" -e "$@" >/dev/null 2>&1; then
    ok "$description"
  else
    bad "$description"
  fi
}
hash_file() { shasum -a 256 "$1" | awk '{print $1}'; }
mode_of() {
  case "$(uname -s)" in
    Darwin) stat -f '%Lp' "$1" ;;
    *) stat -c '%a' "$1" ;;
  esac
}
plain_git() {
  env -u GIT_DIR -u GIT_WORK_TREE -u GIT_INDEX_FILE -u GIT_OBJECT_DIRECTORY \
    -u GIT_ALTERNATE_OBJECT_DIRECTORIES -u GIT_CONFIG -u GIT_CONFIG_COUNT \
    -u GIT_CONFIG_KEY_0 -u GIT_CONFIG_VALUE_0 -u GIT_CONFIG_SYSTEM \
    -u GIT_CONFIG_GLOBAL git "$@"
}

write_wrapper_double() {
  cat > "$FAKE_CLAUDE" <<'PY'
#!/usr/bin/env python3
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

home = Path(os.environ["HOME"])
args = sys.argv[1:]

def read_mode(name, default):
    path = home / name
    return path.read_text(encoding="utf-8").strip() if path.exists() else default

def atomic_text(path, value):
    temporary = path.with_name(path.name + ".tmp.%d" % os.getpid())
    temporary.write_text(value, encoding="utf-8")
    os.replace(temporary, path)

def spawn_ignoring_child(label):
    source = (
        "import signal,time;"
        "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
        "signal.signal(signal.SIGINT,signal.SIG_IGN);"
        "signal.signal(signal.SIGHUP,signal.SIG_IGN);"
        "time.sleep(30)"
    )
    child = subprocess.Popen([sys.executable, "-c", source])
    atomic_text(home / (label + "-child-pid"), str(child.pid) + "\n")
    return child

if args[:2] == ["auth", "status"]:
    mode = read_mode("auth-mode", "ready")
    print("AUTH_STDOUT_SECRET_CANARY")
    print("AUTH_STDERR_SECRET_CANARY", file=sys.stderr)
    if mode == "fail":
        raise SystemExit(19)
    if mode == "leader-child":
        spawn_ignoring_child("auth")
    raise SystemExit(0)

counter_path = home / "model-count"
counter_lock = home / "model-count.lock"
with counter_lock.open("a+") as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    count = int(counter_path.read_text().strip() or "0") + 1 if counter_path.exists() else 1
    atomic_text(counter_path, str(count) + "\n")

record = {
    "argv": args,
    "cwd": os.getcwd(),
    "environment": dict(os.environ),
}
atomic_text(home / "model-last.json", json.dumps(record, sort_keys=True) + "\n")
atomic_text(home / "model-ready", str(count) + "\n")
mode = read_mode("wrapper-mode", "success")

if mode == "exit":
    print("MODEL_ERROR_SECRET_CANARY", file=sys.stderr)
    raise SystemExit(9)
if mode == "invalid":
    print("not-json")
    raise SystemExit(0)
if mode == "missing-session":
    print(json.dumps({"result": "MODEL_RESULT_SECRET_CANARY", "num_turns": count}))
    raise SystemExit(0)
if mode in ("hang", "leader-child"):
    spawn_ignoring_child("model")
    if mode == "hang":
        for caught in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(caught, signal.SIG_IGN)
        time.sleep(30)
if mode == "slow":
    activity = home / "model-activity.lock"
    with activity.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        active_path = home / "model-active"
        maximum_path = home / "model-max-active"
        active = int(active_path.read_text().strip() or "0") + 1 if active_path.exists() else 1
        maximum = int(maximum_path.read_text().strip() or "0") if maximum_path.exists() else 0
        atomic_text(active_path, str(active) + "\n")
        atomic_text(maximum_path, str(max(maximum, active)) + "\n")
    time.sleep(1.0)
    with activity.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        active = int((home / "model-active").read_text().strip()) - 1
        atomic_text(home / "model-active", str(active) + "\n")

print(json.dumps({
    "session_id": "session-%d" % count,
    "result": "MODEL_RESULT_SECRET_CANARY",
    "num_turns": count,
}))
PY
  chmod 0755 "$FAKE_CLAUDE"
}

sandbox() {
  unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_OBJECT_DIRECTORY
  unset GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_CONFIG GIT_CONFIG_COUNT
  unset GIT_CONFIG_KEY_0 GIT_CONFIG_VALUE_0 GIT_CONFIG_SYSTEM GIT_CONFIG_GLOBAL
  unset CLAUDE_WORKER_PERMISSION_MODE HERMES_DEEP_CHAT_ALLOW_BYPASS
  SANDBOX="$(mktemp -d "$TEST_ROOT/sandbox.XXXXXX")"
  export HOME="$SANDBOX/home"
  export HERMES_HOME="$SANDBOX/hermes-root"
  mkdir -p "$HOME" "$HERMES_HOME"
  chmod 0700 "$HOME" "$HERMES_HOME"
  FAKE_CLAUDE="$SANDBOX/claude-double"
  export HERMES_DEEP_CHAT_CLAUDE="$FAKE_CLAUDE"
  export HERMES_DEEP_CHAT_WORKER="$WORKER"
  export HERMES_DEEP_CHAT_PYTHON="$PYTHON_BIN"
  export HERMES_PRIVATE_TOKEN="PRIVATE_HERMES_SECRET_CANARY"
  export HERMES_CHANNEL_TOKEN="PRIVATE_CHANNEL_SECRET_CANARY"
  export ANTHROPIC_API_KEY="PRIVATE_ANTHROPIC_SECRET_CANARY"
  export OPENAI_API_KEY="PRIVATE_OPENAI_SECRET_CANARY"
  export GITHUB_TOKEN="PRIVATE_GITHUB_SECRET_CANARY"
  export SSH_AUTH_SOCK="$SANDBOX/private-agent.sock"
  export CALLER_ONLY_STATE="PRIVATE_CALLER_STATE_CANARY"
  printf 'ready\n' > "$HOME/auth-mode"
  printf 'success\n' > "$HOME/wrapper-mode"
  write_wrapper_double
}

make_repo() {
  local repo="$1"
  mkdir -p "$repo"
  plain_git -C "$repo" init -q
  plain_git -C "$repo" -c user.name=Test -c user.email=test@example.invalid \
    commit -q --allow-empty -m init
}

model_count() {
  [[ -f "$HOME/model-count" ]] && tr -d '[:space:]' < "$HOME/model-count" || printf '0'
}

wait_for_file() {
  local path="$1" attempt
  for attempt in {1..200}; do
    [[ -f "$path" ]] && return 0
    sleep 0.02
  done
  return 1
}

pid_is_dead() {
  local path="$1" pid attempt
  [[ -f "$path" ]] || return 1
  pid="$(tr -d '[:space:]' < "$path")"
  for attempt in {1..100}; do
    if ! kill -0 "$pid" 2>/dev/null; then return 0; fi
    sleep 0.02
  done
  return 1
}

tamper_registry_field() {
  local expression="$1" output before_count before_state registry state
  registry="$HERMES_HOME/claude-sessions.json"
  state="$HERMES_HOME/state/deep-chat/bound.json"
  cp -p "$registry" "$SANDBOX/registry.backup"
  "$JQ_BIN" "$expression" "$registry" > "$SANDBOX/registry.changed"
  chmod 0600 "$SANDBOX/registry.changed"
  mv "$SANDBOX/registry.changed" "$registry"
  before_count="$(model_count)"
  before_state="$(hash_file "$state")"
  output="$($CLI send bound -- rejected-message 2>&1)"
  local rc=$?
  mv "$SANDBOX/registry.backup" "$registry"
  [[ $rc -ne 0 && "$output" == *registry_binding_mismatch* \
     && "$(model_count)" == "$before_count" && "$(hash_file "$state")" == "$before_state" ]]
}

install_git_canaries() {
  local repo="$1"
  mkdir -p "$SANDBOX/hooks-local" "$SANDBOX/hooks-env"
  cat > "$SANDBOX/hooks-local/post-checkout" <<EOF
#!/usr/bin/env bash
touch "$SANDBOX/local-hook-canary"
EOF
  cat > "$SANDBOX/hooks-env/post-checkout" <<EOF
#!/usr/bin/env bash
touch "$SANDBOX/env-hook-canary"
EOF
  cat > "$SANDBOX/fsmonitor" <<EOF
#!/usr/bin/env bash
touch "$SANDBOX/fsmonitor-canary"
printf '2\\n'
EOF
  cat > "$SANDBOX/filter" <<EOF
#!/usr/bin/env bash
touch "$SANDBOX/filter-canary"
cat
EOF
  chmod 0755 "$SANDBOX/hooks-local/post-checkout" "$SANDBOX/hooks-env/post-checkout" \
    "$SANDBOX/fsmonitor" "$SANDBOX/filter"
  printf 'payload\n' > "$repo/payload.txt"
  printf 'payload.txt filter=evil\n' > "$repo/.gitattributes"
  plain_git -C "$repo" add payload.txt .gitattributes
  plain_git -C "$repo" -c user.name=Test -c user.email=test@example.invalid commit -q -m payload
  plain_git -C "$repo" config core.hooksPath "$SANDBOX/hooks-local"
  plain_git -C "$repo" config core.fsmonitor "$SANDBOX/fsmonitor"
  plain_git -C "$repo" config filter.evil.smudge "$SANDBOX/filter"
  plain_git -C "$repo" config filter.evil.clean "$SANDBOX/filter"
  plain_git -C "$repo" config filter.evil.required true
  export GIT_CONFIG_COUNT=1
  export GIT_CONFIG_KEY_0=core.hooksPath
  export GIT_CONFIG_VALUE_0="$SANDBOX/hooks-env"
}

cat > "$TEST_ROOT/secure-runtime-regression.py" <<'PY'
import os
from pathlib import Path
import signal
import sys
import time

runtime_path = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(runtime_path.parent))
import secure_runtime as runtime

mode = sys.argv[2]
root = Path(sys.argv[3])
root.mkdir(parents=True, exist_ok=True)
home = root / "home"
home.mkdir(mode=0o700, exist_ok=True)
environment = runtime.minimal_environment(home=home)

if mode == "hostile-cwd":
    workdir = root / "workdir"
    workdir.mkdir()
    marker = root / "select-imported"
    pid_file = root / "detached-pid"
    (workdir / "select.py").write_text(
        "import os, time\n"
        "from pathlib import Path\n"
        "Path({!r}).write_text('imported', encoding='utf-8')\n".format(str(marker))
        + "child = os.fork()\n"
        + "if child == 0:\n"
        + "    os.setsid()\n"
        + "    Path({!r}).write_text(str(os.getpid()), encoding='ascii')\n".format(str(pid_file))
        + "    time.sleep(30)\n"
        + "    os._exit(0)\n",
        encoding="utf-8",
    )
    result = None
    failure = None
    pid = None
    identity = None
    survived = False
    try:
        try:
            result = runtime.run_bounded(
                [sys.executable, "-I", "-c", "pass"],
                cwd=workdir,
                environment=environment,
                deadline=time.monotonic() + 5.0,
            )
        except Exception as exc:
            failure = exc
        wait_deadline = time.monotonic() + 1.0
        while marker.exists() and not pid_file.exists() and time.monotonic() < wait_deadline:
            time.sleep(0.01)
        if pid_file.exists():
            pid = int(pid_file.read_text(encoding="ascii"))
            identity = runtime.process_identity(pid)
            survived = identity is not None
    finally:
        if pid is not None and identity is not None and runtime.process_identity(pid) == identity:
            try:
                if os.getpgid(pid) == pid:
                    os.killpg(pid, signal.SIGKILL)
                else:
                    os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
    if (
        failure is not None
        or result is None
        or result.returncode != 0
        or not result.launched
        or not result.cleanup_verified
        or marker.exists()
        or pid_file.exists()
        or survived
    ):
        raise SystemExit("hostile cwd select.py reached the inline supervisor")
elif mode == "offset-clock":
    workdir = root / "workdir"
    workdir.mkdir()
    offset = 10000.0
    real_clock = runtime._clock
    runtime._clock = lambda: real_clock() + offset
    original = "def clock(): return time.clock_gettime(time.CLOCK_MONOTONIC)"
    translated = original + "+10000.0"
    if original not in runtime.SUPERVISOR_SOURCE:
        raise SystemExit("supervisor clock seam changed")
    runtime.SUPERVISOR_SOURCE = runtime.SUPERVISOR_SOURCE.replace(
        original, translated, 1
    )
    result = runtime.run_bounded(
        [sys.executable, "-I", "-c", "pass"],
        cwd=workdir,
        environment=environment,
        deadline=time.monotonic() + 5.0,
    )
    if (
        result.returncode != 0
        or not result.launched
        or result.timed_out
        or not result.cleanup_verified
    ):
        raise SystemExit("parent deadlines were not translated into supervisor clock time")
else:
    raise SystemExit("unknown regression mode")
PY

check "Deep Chat supervisor ignores hostile cwd select.py and leaves no descendant" \
  "$PYTHON_BIN" -B "$TEST_ROOT/secure-runtime-regression.py" \
  "$SECURE_RUNTIME" hostile-cwd "$TEST_ROOT/hostile-cwd"
check "Deep Chat translates deadlines into an offset supervisor clock domain" \
  "$PYTHON_BIN" -B "$TEST_ROOT/secure-runtime-regression.py" \
  "$SECURE_RUNTIME" offset-clock "$TEST_ROOT/offset-clock"

# Deterministic parser and dry-run behavior (no auth or persistence).
sandbox
make_repo "$SANDBOX/repo"
expect_failure "no command is rejected" "$CLI"
expect_failure "malformed chat name is rejected" "$CLI" status Bad_Name
expect_failure "empty start message is rejected" "$CLI" start "$SANDBOX/repo" empty --dry-run --
expect_failure "empty send message is rejected" "$CLI" send empty --
plan="$($CLI start "$SANDBOX/repo" planned --schema-version 2 --dry-run -- task words)"
jq_text "dry-run reports schema v2" "$plan" '.dry_run and .schema_version == 2'
jq_text "dry-run preserves the complete task" "$plan" '.task == "task words"'
jq_text "dry-run resolves the source repository physically" "$plan" --arg repo "$SANDBOX/repo" '.source_repo == $repo'
check "dry-run creates no Hermes state" test ! -e "$HERMES_HOME/state"
check "dry-run creates no branch" bash -c '! env -u GIT_CONFIG_COUNT -u GIT_CONFIG_KEY_0 -u GIT_CONFIG_VALUE_0 git -C "$1" show-ref --verify -q refs/heads/hermes/deep-chat/planned' _ "$SANDBOX/repo"
plan="$($CLI start "$SANDBOX/repo" ordered --dry-run --role marathon --schema-version 1 -- task)"
jq_text "start options are accepted after the name in either order" "$plan" '.role == "marathon" and .schema_version == 1'

# Auth failure occurs before Hermes/Git mutation and never leaks wrapper output.
sandbox
make_repo "$SANDBOX/repo"
printf 'fail\n' > "$HOME/auth-mode"
auth_output="$($CLI start "$SANDBOX/repo" noauth -- task 2>&1)"
auth_rc=$?
check "auth failure returns unavailable exit" test "$auth_rc" -eq 75
check "auth failure has a stable reason" bash -c '[[ "$1" == *claude_auth_unavailable* ]]' _ "$auth_output"
check "auth diagnostics discard stdout secrets" bash -c '[[ "$1" != *AUTH_STDOUT_SECRET_CANARY* ]]' _ "$auth_output"
check "auth diagnostics discard stderr secrets" bash -c '[[ "$1" != *AUTH_STDERR_SECRET_CANARY* ]]' _ "$auth_output"
check "auth failure creates no state directory" test ! -e "$HERMES_HOME/state"
check "auth failure creates no branch" bash -c '! git -C "$1" show-ref --verify -q refs/heads/hermes/deep-chat/noauth' _ "$SANDBOX/repo"
check "auth failure never launches a model" test "$(model_count)" -eq 0

# Every bridge-owned Git operation is sanitized, filter-neutralized, and supervised.
sandbox
make_repo "$SANDBOX/repo"
install_git_canaries "$SANDBOX/repo"
start_output="$($CLI start "$SANDBOX/repo" bound --timeout 15 -- initial-secret-message)"
start_rc=$?
state="$HERMES_HOME/state/deep-chat/bound.json"
registry="$HERMES_HOME/claude-sessions.json"
worktree="$HERMES_HOME/worktrees/deep-chat-bound"
check "secure start succeeds" test "$start_rc" -eq 0
jq_text "start returns an exact worker session" "$start_output" '.ok and .session_id == "session-1"'
jq_file "start persists schema-v2 active state" "$state" '.schema_version == 2 and .status == "active" and .start_phase == "complete"'
jq_file "start records a successful first turn" "$state" '.bridge_turn_counter == 1 and .turns[0].status == "succeeded" and .turns[0].worker_session_id == "session-1"'
jq_file "registry uses schema v2" "$registry" '.schema_version == 2 and .revision == 1'
jq_file "registry binds canonical cwd, role, model, and session" "$registry" --arg cwd "$worktree" '.workers["deep-chat-bound"] | .cwd == $cwd and .role == "deep" and .model == "claude-opus-5" and .session_id == "session-1"'
check "worktree exists at the canonical Hermes root" test -d "$worktree"
check "worktree branch is exact" bash -c '[[ "$(env -u GIT_CONFIG_COUNT -u GIT_CONFIG_KEY_0 -u GIT_CONFIG_VALUE_0 git -C "$1" symbolic-ref --short HEAD)" == hermes/deep-chat/bound ]]' _ "$worktree"
check "local post-checkout hook is disabled" test ! -e "$SANDBOX/local-hook-canary"
check "environment-injected hook is disabled" test ! -e "$SANDBOX/env-hook-canary"
check "fsmonitor command is disabled" test ! -e "$SANDBOX/fsmonitor-canary"
check "checkout filter is neutralized" test ! -e "$SANDBOX/filter-canary"
check "source status remains clean under hostile local config" bash -c '[[ -z "$(env -u GIT_CONFIG_COUNT -u GIT_CONFIG_KEY_0 -u GIT_CONFIG_VALUE_0 git -c core.fsmonitor= -C "$1" status --porcelain)" ]]' _ "$SANDBOX/repo"
check "state contains no user task or model result" bash -c '! grep -Eq "initial-secret-message|MODEL_RESULT_SECRET_CANARY" "$1"' _ "$state"
check "registry contains no user task or model result" bash -c '! grep -Eq "initial-secret-message|MODEL_RESULT_SECRET_CANARY" "$1"' _ "$registry"
check "state file is owner-only" test "$(mode_of "$state")" = 600
check "registry file is owner-only" test "$(mode_of "$registry")" = 600
check "state directory is owner-only" test "$(mode_of "$HERMES_HOME/state/deep-chat")" = 700
check "lock directory is owner-only" test "$(mode_of "$HERMES_HOME/locks/deep-chat")" = 700
check "registry lock directory is owner-only" test "$(mode_of "$HERMES_HOME/claude-sessions.json.locks")" = 700
jq_file "model default does not use permission bypass" "$HOME/model-last.json" '(.argv | index("default")) != null and (.argv | index("bypassPermissions")) == null'
jq_file "model cwd is the bound worktree" "$HOME/model-last.json" --arg cwd "$worktree" '.cwd == $cwd'
jq_file "model environment pins HOME" "$HOME/model-last.json" --arg home "$HOME" '.environment.HOME == $home'
jq_file "model environment pins LLVM profile suppression" "$HOME/model-last.json" '.environment.LLVM_PROFILE_FILE == "/dev/null"'
jq_file "model environment strips Hermes secrets" "$HOME/model-last.json" '(.environment | has("HERMES_PRIVATE_TOKEN") | not) and (.environment | has("HERMES_CHANNEL_TOKEN") | not)'
jq_file "model environment strips API credentials" "$HOME/model-last.json" '(.environment | has("ANTHROPIC_API_KEY") | not) and (.environment | has("OPENAI_API_KEY") | not) and (.environment | has("GITHUB_TOKEN") | not)'
jq_file "model environment strips sockets and caller state" "$HOME/model-last.json" '(.environment | has("SSH_AUTH_SOCK") | not) and (.environment | has("CALLER_ONLY_STATE") | not)'
jq_file "model environment strips every Git control variable" "$HOME/model-last.json" '[.environment | keys[] | select(startswith("GIT_"))] | length == 0'

# Read-only status and successful continuation preserve exact binding.
before_status="$(hash_file "$state")"
status_json="$($CLI status bound --timeout 10)"
jq_text "status observes a registered clean worktree" "$status_json" '.observed.worktree.exists and .observed.worktree.registered and .observed.worktree.clean'
jq_text "status observes an exact registry binding" "$status_json" '.observed.registry.entry_exists and .observed.registry.entry_matches'
check "status does not mutate persisted state" test "$before_status" = "$(hash_file "$state")"
send_output="$($CLI send bound --timeout 15 -- follow-up-secret-message)"
jq_text "successful send returns the next session" "$send_output" '.ok and .session_id == "session-2"'
jq_file "successful send advances the turn monotonically" "$state" '.bridge_turn_counter == 2 and (.turns|length) == 2 and .turns[1].status == "succeeded" and .turns[1].worker_session_id == "session-2"'
jq_file "successful send updates both state and registry session" "$registry" '.workers["deep-chat-bound"].session_id == "session-2" and .workers["deep-chat-bound"].revision == 2'
check "successful send persists no prompt or result" bash -c '! grep -Eq "follow-up-secret-message|MODEL_RESULT_SECRET_CANARY" "$1" "$2"' _ "$state" "$registry"

# All four registry identity fields are checked before model invocation/state mutation.
check "cwd mismatch is refused before invocation" tamper_registry_field '.workers["deep-chat-bound"].cwd = "/tmp/wrong-cwd"'
check "role mismatch is refused before invocation" tamper_registry_field '.workers["deep-chat-bound"].role = "marathon"'
check "model mismatch is refused before invocation" tamper_registry_field '.workers["deep-chat-bound"].model = "claude-fable-5"'
check "session mismatch is refused before invocation" tamper_registry_field '.workers["deep-chat-bound"].session_id = "wrong-session"'

# A post-launch failure is acceptance-uncertain and blocks retry and close.
printf 'exit\n' > "$HOME/wrapper-mode"
uncertain_output="$($CLI send bound --timeout 10 -- uncertain-secret-message 2>&1)"
uncertain_rc=$?
check "nonzero model response is surfaced as failure" test "$uncertain_rc" -ne 0
check "model stderr is not leaked" bash -c '[[ "$1" != *MODEL_ERROR_SECRET_CANARY* ]]' _ "$uncertain_output"
jq_file "nonzero response persists acceptance uncertainty" "$state" '.status == "reconciliation_required" and .reconciliation_required and .acceptance_unknown and .turns[-1].status == "acceptance_unknown"'
count_after_unknown="$(model_count)"
expect_failure "uncertain chat refuses an overlapping retry" "$CLI" send bound -- retry
check "refused retry does not invoke the model" test "$(model_count)" = "$count_after_unknown"
expect_failure "uncertain chat refuses close" "$CLI" close bound
reconcile_json="$($CLI reconcile bound)"
jq_text "read-only reconciliation identifies acceptance uncertainty" "$reconcile_json" '.classification == "acceptance_unknown" and .requires_human_instruction'
resolve_json="$($CLI reconcile bound --resolve-unknown not-accepted)"
jq_text "explicit not-accepted reconciliation reactivates the bound session" "$resolve_json" '.ok and .status == "active"'
jq_file "reconciliation is durable and explicit" "$state" '.status == "active" and (.reconciliation_required|not) and .turns[-1].status == "reconciled_not_accepted"'

# Invalid output is also uncertain; it is never treated as retryable.
printf 'invalid\n' > "$HOME/wrapper-mode"
expect_failure "invalid model JSON fails closed" "$CLI" send bound -- invalid-output
jq_file "invalid model JSON requires reconciliation" "$state" '.reconciliation_required and .uncertainty_reason_id == "claude_output_invalid"'
expect_failure "invalid-response chat blocks close" "$CLI" close bound
$CLI reconcile bound --resolve-unknown not-accepted >/dev/null

# One deadline includes timeout, TERM->KILL, nested supervisor cleanup, and lock release.
printf 'hang\n' > "$HOME/wrapper-mode"
rm -f "$HOME/model-ready" "$HOME/model-child-pid"
started="$($PYTHON_BIN -c 'import time; print(time.monotonic())')"
timeout_output="$($CLI send bound --timeout 2 -- timed-out-message 2>&1)"
timeout_rc=$?
finished="$($PYTHON_BIN -c 'import time; print(time.monotonic())')"
elapsed="$($PYTHON_BIN -c 'import sys; print(float(sys.argv[2])-float(sys.argv[1]))' "$started" "$finished")"
check "timed-out model exits with timeout code" test "$timeout_rc" -eq 124
check "timeout remains inside one bounded contract" awk -v value="$elapsed" 'BEGIN { exit !(value < 4.0) }'
check "TERM-ignoring model descendant is dead" pid_is_dead "$HOME/model-child-pid"
jq_file "timeout is persisted as acceptance-unknown" "$state" '.reconciliation_required and .acceptance_unknown and .uncertainty_reason_id == "worker_timeout"'
expect_failure "timeout blocks retry" "$CLI" send bound -- no-overlap
expect_failure "timeout blocks close" "$CLI" close bound
$CLI reconcile bound --resolve-unknown not-accepted >/dev/null

# External interruption settles the durable turn as uncertain and reaps descendants.
rm -f "$HOME/model-ready" "$HOME/model-child-pid"
$CLI send bound --timeout 10 -- interrupted-message >/dev/null 2>&1 &
bridge_pid=$!
wait_for_file "$HOME/model-ready"
kill -TERM "$bridge_pid" 2>/dev/null || true
wait "$bridge_pid" 2>/dev/null
interrupt_rc=$?
check "interrupted send exits nonzero" test "$interrupt_rc" -ne 0
check "interrupted send reaps TERM-ignoring descendant" pid_is_dead "$HOME/model-child-pid"
jq_file "interrupted turn is no longer left attempting" "$state" '.reconciliation_required and .acceptance_unknown and .turns[-1].status == "acceptance_unknown" and ([.turns[].status] | index("attempting") | not)'
expect_failure "interrupted turn blocks retry" "$CLI" send bound -- retry-after-interrupt
$CLI reconcile bound --resolve-unknown not-accepted >/dev/null

# Direct leaders may exit zero while a same-group child lives; supervisors own the group.
printf 'leader-child\n' > "$HOME/auth-mode"
printf 'success\n' > "$HOME/wrapper-mode"
rm -f "$HOME/auth-child-pid"
make_repo "$SANDBOX/repo-auth-child"
$CLI start "$SANDBOX/repo-auth-child" authchild --timeout 15 -- task >/dev/null
check "auth leader-exit descendant is dead" pid_is_dead "$HOME/auth-child-pid"
printf 'ready\n' > "$HOME/auth-mode"
printf 'leader-child\n' > "$HOME/wrapper-mode"
rm -f "$HOME/model-child-pid"
$CLI send authchild --timeout 10 -- leader-exit-child >/dev/null
check "model leader-exit descendant is dead" pid_is_dead "$HOME/model-child-pid"
jq_file "leader-exit model still records its certain response" "$HERMES_HOME/state/deep-chat/authchild.json" '.status == "active" and .turns[-1].status == "succeeded"'

# A hard bridge crash after worktree creation retains inspectable phased state.
sandbox
make_repo "$SANDBOX/repo"
printf 'hang\n' > "$HOME/wrapper-mode"
$CLI start "$SANDBOX/repo" crashed --timeout 20 -- crash-window >/dev/null 2>&1 &
crash_pid=$!
wait_for_file "$HOME/model-ready"
crash_state="$HERMES_HOME/state/deep-chat/crashed.json"
check "provisional state exists before forced bridge crash" test -f "$crash_state"
kill -KILL "$crash_pid" 2>/dev/null || true
wait "$crash_pid" 2>/dev/null
crash_rc=$?
check "forced bridge crash is observable" test "$crash_rc" -ne 0
jq_file "crash state records completed branch/worktree phases" "$crash_state" '.status == "starting" and .branch_phase == "created" and .worktree_phase == "created" and .start_phase == "worker_launch_pending"'
check "crash preserves the created worktree" test -d "$HERMES_HOME/worktrees/deep-chat-crashed"
crash_reconcile="$($CLI reconcile crashed)"
jq_text "crash reconciliation is preservation-first" "$crash_reconcile" '.classification == "worker_registry_mismatch" and .requires_human_instruction'
check "crashed model descendant is eventually dead" pid_is_dead "$HOME/model-child-pid"

# Persistence paths, modes, symlinks, umask, HOME, and cwd are fail-closed.
sandbox
make_repo "$SANDBOX/repo"
old_umask="$(umask)"
umask 000
$CLI start "$SANDBOX/repo" private --timeout 15 -- mode-test >/dev/null
umask "$old_umask"
private_state="$HERMES_HOME/state/deep-chat/private.json"
private_registry="$HERMES_HOME/claude-sessions.json"
check "permissive umask cannot loosen state mode" test "$(mode_of "$private_state")" = 600
check "permissive umask cannot loosen registry mode" test "$(mode_of "$private_registry")" = 600
check "permissive umask cannot loosen persistence directories" bash -c '[[ "$1" == 700 && "$2" == 700 && "$3" == 700 ]]' _ "$(mode_of "$HERMES_HOME/state/deep-chat")" "$(mode_of "$HERMES_HOME/locks/deep-chat")" "$(mode_of "$HERMES_HOME/claude-sessions.json.locks")"
chmod 0644 "$private_state"
expect_failure "world-readable state is rejected" "$CLI" status private
chmod 0600 "$private_state"
chmod 0644 "$private_registry"
expect_failure "world-readable registry is rejected before continuation" "$CLI" send private -- rejected
chmod 0600 "$private_registry"
mv "$private_state" "$SANDBOX/real-state"
ln -s "$SANDBOX/real-state" "$private_state"
expect_failure "symlinked state is rejected" "$CLI" status private
rm "$private_state"
mv "$SANDBOX/real-state" "$private_state"
registry_backup="$SANDBOX/registry-real"
mv "$private_registry" "$registry_backup"
ln -s "$registry_backup" "$private_registry"
expect_failure "symlinked registry is rejected" "$PYTHON_BIN" -B "$WORKER" --registry "$private_registry" --wrapper "$FAKE_CLAUDE" list
rm "$private_registry"
mv "$registry_backup" "$private_registry"
lock_file="$HERMES_HOME/locks/deep-chat/private.lock"
mv "$lock_file" "$SANDBOX/lock-real"
ln -s "$SANDBOX/lock-real" "$lock_file"
expect_failure "symlinked state lock is rejected" "$CLI" send private -- rejected
rm "$lock_file"
mv "$SANDBOX/lock-real" "$lock_file"
check "relative registry override is rejected" bash -c 'HERMES_DEEP_CHAT_REGISTRY=relative "$1" list >/dev/null 2>&1; [[ $? -eq 64 ]]' _ "$CLI"
check "relative HERMES_HOME is rejected" bash -c 'HERMES_HOME=relative "$1" list >/dev/null 2>&1; [[ $? -eq 64 ]]' _ "$CLI"
mkdir -p "$SANDBOX/home-link-parent"
ln -s "$HOME" "$SANDBOX/home-link"
ln -s "$HERMES_HOME" "$SANDBOX/hermes-link"
symlink_status="$(cd / && HOME="$SANDBOX/home-link" HERMES_HOME="$SANDBOX/hermes-link" "$CLI" status private)"
jq_text "symlinked HOME and HERMES_HOME resolve once to physical roots" "$symlink_status" '.status == "active" and .observed.registry.entry_matches'
alternate_status="$(cd / && "$CLI" status private)"
jq_text "alternate caller cwd cannot influence persisted paths" "$alternate_status" '.status == "active" and .observed.worktree.registered'

fake_python="$SANDBOX/python39-double"
cat > "$fake_python" <<'EOF'
#!/usr/bin/env bash
exit 1
EOF
chmod 0755 "$fake_python"
python39_output="$(HERMES_DEEP_CHAT_PYTHON="$fake_python" "$CLI" list 2>&1)"
python39_rc=$?
check "Python 3.9 double is rejected before runtime" test "$python39_rc" -eq 75
check "Python version rejection is explicit" bash -c '[[ "$1" == *"Python 3.11 or newer is required"* ]]' _ "$python39_output"

# Distinct workers overlap model execution and merge registry updates without loss.
sandbox
make_repo "$SANDBOX/repo"
printf 'slow\n' > "$HOME/wrapper-mode"
$CLI start "$SANDBOX/repo" parallel-a --timeout 15 -- first >/dev/null 2>&1 &
parallel_a=$!
$CLI start "$SANDBOX/repo" parallel-b --timeout 15 -- second >/dev/null 2>&1 &
parallel_b=$!
wait "$parallel_a"; parallel_a_rc=$?
wait "$parallel_b"; parallel_b_rc=$?
check "first distinct worker succeeds" test "$parallel_a_rc" -eq 0
check "second distinct worker succeeds" test "$parallel_b_rc" -eq 0
check "distinct workers execute concurrently" test "$(tr -d '[:space:]' < "$HOME/model-max-active")" -ge 2
jq_file "concurrent registry update loses no worker" "$HERMES_HOME/claude-sessions.json" '.revision == 2 and (.workers|length) == 2 and .workers["deep-chat-parallel-a"] and .workers["deep-chat-parallel-b"]'
jq_file "first concurrent state is exactly bound" "$HERMES_HOME/state/deep-chat/parallel-a.json" '.status == "active" and .worker_session_id != null'
jq_file "second concurrent state is exactly bound" "$HERMES_HOME/state/deep-chat/parallel-b.json" '.status == "active" and .worker_session_id != null'

# Bypass permissions is a named opt-in only.
sandbox
make_repo "$SANDBOX/repo"
export CLAUDE_WORKER_PERMISSION_MODE=bypassPermissions
unacked_output="$($CLI start "$SANDBOX/repo" unacked --dry-run -- task 2>&1)"
unacked_rc=$?
check "permission bypass without acknowledgement is rejected" test "$unacked_rc" -eq 64
check "unacknowledged bypass mutates nothing" test ! -e "$HERMES_HOME/state"
export HERMES_DEEP_CHAT_ALLOW_BYPASS=1
$CLI start "$SANDBOX/repo" bypassed --timeout 15 -- task >/dev/null
jq_file "named bypass opt-in reaches only the model argument" "$HOME/model-last.json" '(.argv | index("bypassPermissions")) != null'

# Production source hygiene and documented advisory filesystem boundary.
check "bridge and worker compile under Python 3.11" "$PYTHON_BIN" -B -m py_compile "$RUNTIME" "$WORKER" "$SECURE_RUNTIME"
check "bridge exposes absolute worker, wrapper, registry, and Python seams" bash -c 'grep -q HERMES_DEEP_CHAT_WORKER "$1" && grep -q HERMES_DEEP_CHAT_CLAUDE "$1" && grep -q HERMES_DEEP_CHAT_REGISTRY "$1" && grep -q HERMES_DEEP_CHAT_PYTHON "$2"' _ "$RUNTIME" "$CLI"
check "default source contains no bypassPermissions default" bash -c '! grep -Eq "get\([^)]*,[[:space:]]*[\"'\'' ]bypassPermissions" "$1" "$2"' _ "$RUNTIME" "$WORKER"
check "prompts identify the filesystem boundary as policy-only" grep -q 'policy boundary, not an OS sandbox' "$RUNTIME"
check "installer deploys all maintained runtime files from canonical roots" bash -c 'grep -q deep_chat_bridge.py "$1" && grep -q secure_runtime.py "$1" && grep -q HERMES_HOME "$1" && ! grep -q "/Users/" "$1"' _ "$ROOT_DIR/install-local.sh"
check "no Deep Chat source hardcodes a personal home" bash -c '! grep -R "/Users/" "$1" --include="*.py" --include="*.sh" --exclude=test_deep_chat.sh' _ "$ROOT_DIR"

printf '\npassed: %d  failed: %d\n' "$PASS" "$FAIL"
if [[ $PASS -lt 68 ]]; then
  echo "FAIL - suite must retain at least 68 explicit contracts" >&2
  exit 1
fi
[[ $FAIL -eq 0 ]]
