"""OpenViking CLI adapter.

The adapter intentionally shells out to a user-installed command instead of
embedding credentials or service-specific state in this public starter.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
import tempfile

from .ids import slug
from .manifest import OpenVikingConfig
from .scanner import Resource


@dataclass(frozen=True)
class ExportResult:
    command: list[str]
    count: int
    dry_run: bool
    returncode: int = 0


def export_resources(config: OpenVikingConfig, resources: list[Resource], dry_run: bool) -> ExportResult:
    command = list(config.command)
    if dry_run or not resources:
        return ExportResult(command=command, count=len(resources), dry_run=True)

    # OpenViking ingests files rather than arbitrary JSONL records. Materialize
    # only the already-redacted summary in a private temporary directory; the
    # original approved-root file never leaves this machine through this tool.
    with tempfile.TemporaryDirectory(prefix="hermes-second-brain-") as tmp:
        temp_dir = Path(tmp)
        for resource in resources:
            target = (
                f"viking://resources/{slug(config.namespace_prefix)}/"
                f"{slug(resource.namespace)}/{resource.resource_id.rsplit(':', 1)[-1]}.md"
            )
            summary_file = temp_dir / f"{resource.resource_id.rsplit(':', 1)[-1]}.md"
            summary_file.write_text(f"# {resource.title}\n\n{resource.summary}\n", encoding="utf-8")

            # An existing exact resource is replaced; otherwise it is created.
            exists = subprocess.run(
                [*command, "stat", target, "-o", "json"],
                text=True,
                capture_output=True,
                check=False,
            ).returncode == 0
            if exists:
                invocation = [*command, "write", target, "--from-file", str(summary_file), "-o", "json"]
            else:
                invocation = [*command, "add-resource", str(summary_file), "--to", target, "--no-progress", "-o", "json"]
            proc = subprocess.run(invocation, text=True, capture_output=True, check=False)
            if proc.returncode != 0:
                # Do not echo a tool response: it can include local paths or
                # source-derived content. The user can rerun `ov` manually.
                raise RuntimeError("OpenViking export failed; inspect your local ov configuration and retry")
    return ExportResult(command=command, count=len(resources), dry_run=False, returncode=0)
