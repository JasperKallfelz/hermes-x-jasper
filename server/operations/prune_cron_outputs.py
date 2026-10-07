#!/usr/bin/env python3
"""Prune Hermes cron output files older than the configured retention period."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

RETENTION_DAYS = int(os.environ.get("HERMES_CRON_OUTPUT_RETENTION_DAYS", "14"))
# Output directories for jobs no longer present in jobs.json ("orphans") keep
# accumulating after a job is deleted. Once their last output has aged past this
# threshold, drop the empty/stale directory too so output/ mirrors jobs.json.
ORPHAN_RETENTION_DAYS = int(os.environ.get("HERMES_CRON_OUTPUT_ORPHAN_RETENTION_DAYS", "14"))
JOBS_JSON = Path.home() / ".hermes" / "cron" / "jobs.json"
# Silent no-agent runs ("Status: silent (empty output)") are pure bookkeeping;
# prune them much sooner so 1-minute watchdog jobs don't accumulate thousands of files.
SILENT_RETENTION_DAYS = int(os.environ.get("HERMES_CRON_OUTPUT_SILENT_RETENTION_DAYS", "1"))
SILENT_MARKER = b"**Status:** silent (empty output)"
SILENT_SNIFF_BYTES = 4096
OUTPUT_ROOT = Path.home() / ".hermes" / "cron" / "output"
DRY_RUN = os.environ.get("HERMES_CRON_PRUNE_DRY_RUN") == "1"


def _is_silent_output(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            return SILENT_MARKER in fh.read(SILENT_SNIFF_BYTES)
    except OSError:
        return False


def _known_job_ids() -> set[str] | None:
    """Return job IDs present in jobs.json, or None if it can't be read.

    Returning None makes orphan pruning a no-op so a missing/corrupt jobs.json
    never causes us to delete directories for jobs that actually still exist.
    """
    try:
        raw = json.loads(JOBS_JSON.read_text())
    except (OSError, ValueError):
        return None
    if isinstance(raw, dict):
        jobs = raw.get("jobs", list(raw.values()))
    else:
        jobs = raw
    ids: set[str] = set()
    for job in jobs:
        if isinstance(job, dict):
            jid = job.get("id") or job.get("job_id")
            if jid:
                ids.add(str(jid))
    return ids


def main() -> int:
    if RETENTION_DAYS < 1:
        raise ValueError("HERMES_CRON_OUTPUT_RETENTION_DAYS must be at least 1")
    if not OUTPUT_ROOT.is_dir():
        return 0

    cutoff = time.time() - RETENTION_DAYS * 86400
    silent_cutoff = time.time() - SILENT_RETENTION_DAYS * 86400
    orphan_cutoff = time.time() - ORPHAN_RETENTION_DAYS * 86400
    known_ids = _known_job_ids()
    removed = 0
    freed = 0
    orphan_dirs_removed = 0

    for job_dir in OUTPUT_ROOT.iterdir():
        if not job_dir.is_dir() or job_dir.is_symlink():
            continue
        for path in job_dir.iterdir():
            if not path.is_file() or path.is_symlink() or path.suffix != ".md":
                continue
            mtime = path.stat().st_mtime
            expired = mtime < cutoff or (mtime < silent_cutoff and _is_silent_output(path))
            if not expired:
                continue
            size = path.stat().st_size
            if not DRY_RUN:
                path.unlink()
            removed += 1
            freed += size

        # Reap output dirs whose job no longer exists in jobs.json, once their
        # newest remaining file (if any) has aged past the orphan threshold.
        # known_ids is None => jobs.json unreadable, so skip orphan reaping.
        if known_ids is not None and job_dir.name not in known_ids:
            remaining = [p for p in job_dir.iterdir() if not p.name.startswith(".")]
            newest = max((p.stat().st_mtime for p in remaining), default=0.0)
            if newest < orphan_cutoff:
                if not DRY_RUN:
                    for p in remaining:
                        if p.is_file() and not p.is_symlink():
                            p.unlink()
                    try:
                        job_dir.rmdir()
                    except OSError:
                        pass
                    else:
                        orphan_dirs_removed += 1
                else:
                    orphan_dirs_removed += 1

    # Empty stdout keeps successful routine runs silent. Emit only when work was done.
    if removed or orphan_dirs_removed:
        action = "Would remove" if DRY_RUN else "Removed"
        parts = []
        if removed:
            parts.append(f"{removed} cron output files ({freed} bytes)")
        if orphan_dirs_removed:
            parts.append(f"{orphan_dirs_removed} orphaned job dir(s)")
        print(f"{action} {'; '.join(parts)}; retention={RETENTION_DAYS}d orphan={ORPHAN_RETENTION_DAYS}d")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
