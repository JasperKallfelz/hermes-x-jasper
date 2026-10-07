---
name: cron-reliability-recovery
description: Use when Hermes cron has failures or stale execution state.
version: 1.0.0
---

# Cron Reliability Recovery

## Scope and safety

Use for local Hermes cron diagnosis. Do not send a test delivery, alter credentials/providers/models, or restart a gateway unless the user explicitly directs it. Treat failed remote connections as external until a local defect is reproduced.

## Procedure

1. Check each profile separately with `hermes cron status`, `hermes cron list`, and `hermes --profile NAME cron status/runs`.
2. Inspect the affected `jobs.json`, durable `executions.db`, script existence, modes, and syntax without exposing secrets.
3. For mailbox DNS/connect failures, verify current DNS and TCP reachability for every failing host. If several independent hosts failed together but now resolve/connect and prior runs completed, classify it as transient; do not edit mail configuration or credentials and do not force-run a delivery-capable scanner.
4. For `RuntimeError: Connection error.` in agent jobs, inspect later durable runs before changing configuration. A subsequent completed execution means the fault was transient.
5. A `running` direct execution is stale only after confirming its recorded PID no longer exists. Hermes may automatically terminalize it as `unknown` after ownership loss. Preserve that durable uncertainty; never invent a successful outcome.
6. If a dead-PID direct run leaves `fire_claim` in the job record, make a timestamped backup of `jobs.json`, then clear only that verified stale `fire_claim`. Keep the historical `last_status`, `last_error`, and delivery error untouched until a real scheduled success overwrites them.
7. If a gateway traceback shows `cron.jobs._current_cron_store()` blocked in `Path.resolve()` while recording the ticker heartbeat, and multiple profile `cron status` probes time out together, treat this as a default-home hot-path stall—not malformed jobs. The safe source fix is to return the import-time cron store when `get_hermes_home()` already equals canonical `HERMES_DIR`, retaining `resolve()` for non-default, relative, or symlinked homes. Also inspect the import-time `HERMES_DIR = get_hermes_home().resolve()` assignment: it can block before `cron status` reaches `_current_cron_store()` for absolute profile homes. Keep the configured absolute non-symlink path without resolving at import; resolve lazily only for relative/symlinked homes. Verify with real `HERMES_HOME=<profile> hermes cron status` probes plus a regression assertion that unchanged absolute homes do not invoke `Path.resolve()`. Do not restart the gateway; the fix loads at its next watchdog-owned restart.
8. If `hermes cron status` exceeds its timeout while a raw system-wide `ps` process listing also stalls, do not treat healthy ticker records as a gateway failure. In `hermes_cli/cron.py`, make `cron_status()` read `gateway.status.get_running_pid()` for the current profile first and retain `hermes_cli.gateway.find_gateway_pids()` only as a fallback when no valid profile PID record exists. Back up the source, update the status test to mock `gateway.status.get_running_pid`, and verify status for every affected profile stays below the watchdog threshold.
9. Re-parse JSON and run cron status plus affected job history after any edit.

## Evidence standard

Report separately:
- repaired local state;
- still-historical or externally blocked errors;
- current successful later runs;
- actions intentionally not taken.

## Desktop-owned ticker false alarms

When `cron status` says no gateway but profile-local `ticker_heartbeat` and `ticker_last_success` advance, inspect the Desktop scheduler log before treating the profile as down. The primary Desktop backend ticks every local profile without a dedicated per-profile gateway PID. A stale `gateway_state.json` alone does not contradict a healthy Desktop ticker.

For the local reliability watchdog, reuse the durable-heartbeat fallback on a successful CLI invocation whose only liveness problem is the explicit no-gateway message, not only on CLI timeout. Require valid jobs JSON and both finite timestamps within the existing freshness/future-skew limits. Keep nonzero CLI exits, explicit stalled/failing ticks, missing markers, and malformed state unhealthy. Back up the watchdog first; verify offline negative cases and call its live `ticker_health` directly without invoking lifecycle actions. Re-run `hermes cron status` and report honestly if its gateway-only warning remains; watchdog health is not proof that the CLI warning was fixed. Do not install/restart a gateway merely to silence this false alarm.

## Pitfalls

- A failed Telegram delivery does not prove malformed cron configuration; first check whether a later run completed and cleared `last_delivery_error`.
- Do not mark an execution `completed` just because its PID vanished. Use the scheduler's `unknown` terminal state when side effects cannot be proven.
- Do not clear a claim owned by a live PID.
- For an interval LaunchAgent that deliberately exits after each check, distinguish a handled operational condition (for example, a rate-limited, user-visible auth alert) from a checker crash. If the condition is already logged and alert delivery is bounded, return exit 0 so launchd/infra audits do not misclassify normal completion as a daemon failure; verify with `launchctl kickstart -k` and `launchctl print`.
- A provider usage API may vary reset timestamps only in fractional seconds. If a watcher keys its fired state by the full ISO timestamp, it can launch duplicate waves; normalize both the calculated reset key and any existing corresponding fired-state value to whole-second precision, back up the state first, then verify the next scheduled watcher execution completes once.
- If a watcher de-duplicates provider reset windows using API timestamps, normalize the timestamp key to whole-second precision. Fractional-second jitter for the same physical reset can otherwise launch a duplicate downstream wave. Back up the script and state first; verify offline that two fractional representations do not launch again and that a genuinely new reset still does.
