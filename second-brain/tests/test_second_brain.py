from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hermes_second_brain.cli import main
from hermes_second_brain.ids import namespace_resource_id
from hermes_second_brain.manifest import load_manifest
from hermes_second_brain.scanner import records_jsonl, scan_roots
from hermes_second_brain.sync import sync_manifest


def write_manifest(tmp_path: Path, command: list[str] | None = None) -> Path:
    notes = tmp_path / "notes"
    notes.mkdir()
    home_hint = "/" + "Users" + "/you"
    (notes / "public.md").write_text(f"# Public Note\n\nContact user@example.com from {home_hint} only.\n", encoding="utf-8")
    (notes / ".env").write_text("TOKEN=not-exported\n", encoding="utf-8")
    (notes / "private-token.txt").write_text("secret note\n", encoding="utf-8")
    manifest = tmp_path / "second-brain.toml"
    command_expr = json.dumps(command or ["ov"])
    manifest.write_text(
        f"""
state_path = ".second-brain/state.sqlite3"

[openviking]
command = {command_expr}
namespace_prefix = "starter"

[[approved_roots]]
name = "notes"
path = "./notes"
namespace = "team-notes"
include = ["**/*.md", "**/*.txt"]
summary_max_chars = 200
""",
        encoding="utf-8",
    )
    return manifest


def test_manifest_scan_skips_secret_looking_paths(tmp_path: Path):
    manifest = load_manifest(write_manifest(tmp_path))
    resources = scan_roots(manifest.roots)
    assert [r.relative_path for r in resources] == ["public.md"]
    assert resources[0].title == "Public Note"
    assert "<home>" in resources[0].summary


def test_manifest_scan_skips_symlinks_outside_approved_root(tmp_path: Path):
    manifest = load_manifest(write_manifest(tmp_path))
    outside = tmp_path / "outside.md"
    outside.write_text("# Outside\nDo not export.\n", encoding="utf-8")
    (manifest.roots[0].path / "linked.md").symlink_to(outside)
    assert [r.relative_path for r in scan_roots(manifest.roots)] == ["public.md"]


def test_ids_are_deterministic_and_do_not_expose_paths(tmp_path: Path):
    manifest = load_manifest(write_manifest(tmp_path))
    root = manifest.roots[0]
    path = root.path / "public.md"
    first = namespace_resource_id(root.namespace, root.path, path)
    second = namespace_resource_id(root.namespace, root.path, path)
    assert first == second
    assert "public" not in first
    assert first.startswith("urn:hermes-second-brain:team-notes:")


def test_dry_run_creates_local_sqlite_state_without_export(tmp_path: Path):
    manifest = load_manifest(write_manifest(tmp_path))
    plan = sync_manifest(manifest, dry_run=True)
    assert plan.scanned == 1
    assert len(plan.changed) == 1
    assert manifest.state_path.exists()
    with sqlite3.connect(manifest.state_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM resources").fetchone()[0] == 1
        assert conn.execute("SELECT exported_at FROM resources").fetchone()[0] is None


def test_real_sync_invokes_openviking_style_cli(tmp_path: Path):
    sink = tmp_path / "sink.md"
    fake = tmp_path / "fake_ov.py"
    fake.write_text(
        "import pathlib, sys\n"
        "sink = pathlib.Path(sys.argv[1])\n"
        "args = sys.argv[2:]\n"
        "if args[0] == 'stat': raise SystemExit(1)\n"
        "source = pathlib.Path(args[1] if args[0] == 'add-resource' else args[3])\n"
        "sink.write_text(source.read_text(encoding='utf-8'), encoding='utf-8')\n",
        encoding="utf-8",
    )
    manifest = load_manifest(write_manifest(tmp_path, [sys.executable, str(fake), str(sink)]))
    plan = sync_manifest(manifest, dry_run=False)
    assert plan.export.count == 1
    exported = sink.read_text(encoding="utf-8")
    assert "# Public Note" in exported
    assert "<home>" in exported


def test_real_sync_after_dry_run_exports_pending_summary(tmp_path: Path):
    sink = tmp_path / "sink.md"
    fake = tmp_path / "fake_ov.py"
    fake.write_text(
        "import pathlib, sys\n"
        "sink = pathlib.Path(sys.argv[1])\n"
        "args = sys.argv[2:]\n"
        "if args[0] == 'stat': raise SystemExit(1)\n"
        "source = pathlib.Path(args[1] if args[0] == 'add-resource' else args[3])\n"
        "sink.write_text(source.read_text(encoding='utf-8'), encoding='utf-8')\n",
        encoding="utf-8",
    )
    manifest = load_manifest(write_manifest(tmp_path, [sys.executable, str(fake), str(sink)]))
    assert sync_manifest(manifest, dry_run=True).changed
    plan = sync_manifest(manifest, dry_run=False)
    assert len(plan.changed) == 1
    assert sink.exists()


def test_jsonl_contains_approved_summary_records(tmp_path: Path):
    manifest = load_manifest(write_manifest(tmp_path))
    resources = scan_roots(manifest.roots)
    line = records_jsonl(resources, "starter").strip()
    record = json.loads(line)
    assert set(record) == {"id", "metadata", "namespace", "summary", "title"}
    assert record["metadata"]["root"] == "notes"


def test_cli_init_scan_and_dry_run(tmp_path: Path, monkeypatch):
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        assert main(["init", "--manifest", "second-brain.toml"]) == 0
        assert main(["--manifest", "second-brain.toml", "scan"]) == 0
        assert main(["--manifest", "second-brain.toml", "sync", "--dry-run"]) == 0
    finally:
        os.chdir(cwd)
