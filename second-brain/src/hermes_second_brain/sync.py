"""Second Brain sync orchestration."""

from __future__ import annotations

from dataclasses import dataclass

from .manifest import Manifest
from .ov_adapter import ExportResult, export_resources
from .scanner import Resource, scan_roots
from .state import State


@dataclass(frozen=True)
class SyncPlan:
    scanned: int
    changed: list[Resource]
    export: ExportResult


def sync_manifest(manifest: Manifest, dry_run: bool = True) -> SyncPlan:
    resources = scan_roots(manifest.roots)
    with State(manifest.state_path) as state:
        changed = state.changed(resources)
        export = export_resources(manifest.openviking, changed, dry_run=dry_run)
        state.mark_seen(resources, exported=not dry_run and export.returncode == 0)
    return SyncPlan(scanned=len(resources), changed=changed, export=export)
