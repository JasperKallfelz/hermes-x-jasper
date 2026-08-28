"""Release inventory, archive, traversal, patch-regeneration, and CI contracts."""
from __future__ import annotations

import gzip
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import build_release_artifact  # noqa: E402
import check_release_inputs  # noqa: E402


def git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=Fixture",
         "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false", *args],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
    )


def make_release_repo(root: Path) -> str:
    git(root, "init", "--quiet")
    (root / ".gitignore").write_text(
        ".hermes-task.md\n.hermes-result.md\n.hermes-repair-task.md\n"
        ".hermes-repair-result.md\n.hermes-pi-module-task.md\n"
        ".hermes-pi-module-result.md\nbuild/\ndist/\n*.zip\n*.tar\n*.tar.gz\n*.tgz\n",
        encoding="utf-8",
    )
    (root / "safe.txt").write_text("safe\n", encoding="utf-8")
    (root / "release").mkdir()
    inventory = [".gitignore", "release/tracked-files.txt", "safe.txt"]
    (root / "release/tracked-files.txt").write_text("\n".join(inventory) + "\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "--quiet", "-m", "candidate")
    return git(root, "rev-parse", "HEAD").stdout.strip()


def test_release_input_check_accepts_exact_clean_inventory_and_ignored_controls():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        commit = make_release_repo(root)
        assert check_release_inputs.check(
            root, commit, root / "release/tracked-files.txt"
        ) == []
        (root / ".hermes-task.md").write_text("private control\n", encoding="utf-8")
        assert check_release_inputs.check(
            root, commit, root / "release/tracked-files.txt"
        ) == []


def test_release_input_check_rejects_untracked_and_forbidden_tracked_paths():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        commit = make_release_repo(root)
        (root / "new.txt").write_text("untracked\n", encoding="utf-8")
        errors = check_release_inputs.check(root, commit, root / "release/tracked-files.txt")
        assert any("untracked" in error for error in errors)
        assert check_release_inputs.forbidden("dist/release.tar.gz")
        assert check_release_inputs.forbidden(".hermes-result.md")
        assert check_release_inputs.forbidden(".hermes-pi-module-result.md")
        assert check_release_inputs.forbidden("nested/.gitleaksignore")


def test_artifact_is_git_object_only_and_ignored_controls_never_enter():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        root = base / "repo"
        output = base / "output"
        root.mkdir()
        commit = make_release_repo(root)
        (root / ".hermes-task.md").write_text("private\n", encoding="utf-8")
        (root / "build").mkdir()
        (root / "build/local.txt").write_text("local\n", encoding="utf-8")
        archive = build_release_artifact.build(root, commit, output, "fixture-v1")
        assert archive.is_file()
        with tarfile.open(archive, "r:gz") as bundle:
            names = bundle.getnames()
        assert not any(".hermes-task.md" in name or "/build/" in name for name in names)
        run = (output / "release-run.json").read_text(encoding="utf-8")
        assert commit in run


def test_archive_traversal_and_link_members_are_rejected():
    inventory = ["safe.txt"]
    for kind in ("traversal", "symlink"):
        with tempfile.TemporaryDirectory() as td:
            archive = Path(td) / "bad.tar.gz"
            with tarfile.open(archive, "w:gz") as bundle:
                if kind == "traversal":
                    payload = b"bad\n"
                    member = tarfile.TarInfo("fixture/../escape.txt")
                    member.size = len(payload)
                    bundle.addfile(member, io.BytesIO(payload))
                else:
                    member = tarfile.TarInfo("fixture/safe.txt")
                    member.type = tarfile.SYMTYPE
                    member.linkname = "../../escape"
                    bundle.addfile(member)
            try:
                build_release_artifact.safe_unpack(
                    archive, Path(td) / "out", "fixture/", inventory
                )
            except ValueError:
                pass
            else:
                raise AssertionError(f"{kind} archive was accepted")


def test_patch_regenerator_stages_only_allowlist_and_cleans_index():
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        checkout = base / "checkout"
        starter = base / "starter"
        checkout.mkdir()
        (starter / "scripts").mkdir(parents=True)
        (starter / "patches").mkdir()
        git(checkout, "init", "--quiet")
        for name in ("owned-a.txt", "owned-b.txt", "unrelated.txt"):
            (checkout / name).write_text("base\n", encoding="utf-8")
        git(checkout, "add", "-A")
        git(checkout, "commit", "--quiet", "-m", "pin")
        pin = git(checkout, "rev-parse", "HEAD").stdout.strip()
        source = (REPO / "scripts/regenerate_patch.sh").read_text(encoding="utf-8")
        source = source.replace(
            "5fc308a70719a83cccdbba4c0e39c23f5a8239d5", pin
        )
        script = starter / "scripts/regenerate_patch.sh"
        script.write_text(source, encoding="utf-8")
        script.chmod(0o755)
        (starter / "patches/voice-and-desktop-features.paths").write_text(
            "owned-a.txt\nowned-b.txt\n", encoding="utf-8"
        )
        (checkout / "owned-a.txt").write_text("changed a\n", encoding="utf-8")
        (checkout / "owned-b.txt").write_text("changed b\n", encoding="utf-8")
        (checkout / "unrelated.txt").write_text("private unrelated\n", encoding="utf-8")
        output = starter / "patches/out.patch"
        result = subprocess.run(
            ["bash", str(script), str(checkout), str(output)],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        patch = output.read_text(encoding="utf-8")
        assert "owned-a.txt" in patch and "owned-b.txt" in patch
        assert "unrelated.txt" not in patch and "private unrelated" not in patch
        assert git(checkout, "diff", "--cached", "--name-only").stdout == ""


def test_ci_release_contract_is_full_history_gated_and_publish_last():
    ci = (REPO / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    release = (REPO / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "fetch-depth: 0" in ci and "fetch-depth: 0" in release
    assert "scripts/install_gitleaks.sh" in ci
    assert ci.index("scripts/install_gitleaks.sh") < ci.index("python -m pytest")
    assert "RELEASE_DIFF_BASE" in ci and "pull_request.base.sha" in ci
    assert "tags: [v0.3.0]" in release and "types: [created]" in release
    assert "needs: [pi-runtime, gates]" in release
    assert release.rfind("gh release create") > release.rfind("scripts/release_audit.sh")
    assert "contents: write" in release


def test_release_audit_always_names_every_required_gate():
    script = (REPO / "scripts/release_audit.sh").read_text(encoding="utf-8")
    for required in (
        "audit_public.py", "--history", "gitleaks_scan.sh", "check_release_inputs.py",
        "git diff --check", "prove_patch.sh", "build_release_artifact.py",
        "--ignore-gitleaks-allow", "release-run.json", "modules/pi-runtime/setup.sh",
    ):
        assert required in script


def test_tracked_inventory_lists_every_current_and_new_release_input():
    tracked = subprocess.check_output(
        ["git", "-C", str(REPO), "ls-files"], text=True
    ).splitlines()
    new = subprocess.check_output(
        ["git", "-C", str(REPO), "ls-files", "--others", "--exclude-standard"],
        text=True,
    ).splitlines()
    actual = (REPO / "release/tracked-files.txt").read_text(encoding="utf-8").splitlines()
    assert actual == sorted(set(tracked + new))
