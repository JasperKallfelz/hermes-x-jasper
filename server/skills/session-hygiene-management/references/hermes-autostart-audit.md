# Hermes Autostart Audit Notes

Session-specific findings from an audit of Hermes service/autostart state on macOS.

## What to check
- `hermes gateway status`
- `hermes --profile <profile> gateway status`
- `~/Library/LaunchAgents/ai.hermes.*.plist`
- `launchctl list | grep -i 'ai\.hermes'`
- `ps -axo command= | grep 'hermes_cli.main'`

## Useful interpretation notes
- Hermes may have multiple profile-scoped gateway services alongside a desktop autostart.
- A removed LaunchAgent can still appear in `launchctl list` briefly while a process drains or exits.
- Verify again after a short delay before declaring the autostart gone.
- If several profiles are active, inspect each profile separately before mutating anything.
- A launchd job that receives SIGTERM every few seconds may be restarted by a child helper rather than crashing. Sample the live process tree fast enough to catch short-lived `launchctl kickstart -k ...` commands and print their parent/grandparent chain.
- Never schedule a one-shot cron script inside a gateway that restarts that same gateway. The script can kill its scheduler before `mark_job_run` persists completion, so the still-due one-shot fires again on every launch and creates a permanent SIGTERM/SIGKILL loop. Recovery order: remove the exact cron job, `launchctl bootout` the profile service, clear only the affected session's stale `resume_pending` marker while stopped, then `launchctl bootstrap` and verify one PID/runs count over several former restart intervals. Archive the helper rather than deleting it.

## Safe removal pattern
1. Stop/uninstall dedicated gateway services for each target profile explicitly.
2. Inspect every still-running multi-platform gateway as well: a `general` or default profile can carry Discord inside the same process as Telegram even when dedicated Discord profiles are already stopped. Check active platform startup lines and list Discord environment **key names only**—never print token values.
3. Before removing credentials, archive the affected `.env` and LaunchAgent definitions in a private `0700` directory with `0600` files and verify their hashes. Remove only the target platform keys (for Discord, active `DISCORD_*` assignments) from the live profile environment.
4. Disable and boot out the dedicated launchd labels, keep their plists outside the active LaunchAgents root, and restart only the still-required shared gateway so unrelated platforms remain available.
5. Re-check `launchctl list`, `launchctl print-disabled`, process listings, profile statuses, and the latest startup log after a drain delay. For a shared gateway, require an explicit `Gateway running with 1 platform(s)`/equivalent and no target-platform connection lines before calling it disabled.
6. Treat source code, profiles, sessions, worktrees, and skills as parked artifacts unless the user explicitly asks to delete them.
