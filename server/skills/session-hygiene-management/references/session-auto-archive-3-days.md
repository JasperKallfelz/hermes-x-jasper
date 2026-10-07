# Hermes stale-session auto-archive (3-day rule)

Session-specific note captured from a real cleanup run in this workspace.

## What was changed

- `sessions.auto_archive = true`
- `sessions.auto_archive_days = 3`
- `sessions.auto_prune = false`

## Behavior verified

- `maybe_auto_archive(idle_days=3, min_interval_hours=0, exclude_pinned=True)` archived **568** stale sessions.
- Messages were not deleted; only archive state changed.
- Database integrity check returned `ok` (`PRAGMA quick_check`).
- Remaining stale unpinned sessions after the sweep: `0`.

## Practical rule

- Treat this as a **soft-hide/archive** workflow, not a prune/delete workflow.
- Age sessions by **last activity** (latest message / last_activity_at / started_at fallback), not creation time.
- Keep `min_interval_hours` as the throttle so startup/tick hooks can call the helper opportunistically without repeated work.

## Verification pattern

1. Read config back from the live config store.
2. Count stale candidate sessions before the sweep.
3. Run the archive helper.
4. Recount stale candidates and confirm they drop to zero (or the expected remainder).
5. Run `PRAGMA quick_check` on the DB.
