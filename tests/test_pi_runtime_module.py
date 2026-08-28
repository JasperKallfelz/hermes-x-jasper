"""Public Pi module integrity, safety, documentation, and CI contracts."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest


REPO = Path(__file__).resolve().parents[1]
MODULE_DIR = REPO / "modules/pi-runtime"
SPEC = importlib.util.spec_from_file_location("pi_module", MODULE_DIR / "pi_module.py")
assert SPEC and SPEC.loader
pi_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pi_module)
sys.path.insert(0, str(REPO / "scripts"))
import classify_ci_changes  # noqa: E402

BASE = "306db2776c6b6f1acc85c31c4dabba3263f0e9fd"
FEATURE = "c1093d23837bab98013bc9929d0d2679416601e5"
PATCH_SHA256 = "d1c3f99a7ad5f0028ebd813cd0553a524c0c3db1bc4d39a7fe4aff13f03a3e75"
IMAGE_ID = "sha256:e89f45110e9277902bafbf49009e842bc9e38180e668fea8a6ff3dcdb2dd2cdf"
ORIGIN = "https://github.com/NousResearch/hermes-agent.git"


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Fixture",
         "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false", *args],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=check,
    )


def fixture_contract(tmp_path: Path) -> tuple[Path, dict, Path]:
    source = tmp_path / "objects"
    source.mkdir()
    git(source, "init", "--quiet")
    git(source, "remote", "add", "origin", ORIGIN)
    (source / "owned.txt").write_text("base\n", encoding="utf-8")
    git(source, "add", "owned.txt")
    git(source, "commit", "--quiet", "-m", "base")
    base = git(source, "rev-parse", "HEAD").stdout.strip()
    base_tree = git(source, "show", "-s", "--format=%T", base).stdout.strip()
    (source / "owned.txt").write_text("feature\n", encoding="utf-8")
    (source / "new.txt").write_text("new\n", encoding="utf-8")
    git(source, "add", "owned.txt", "new.txt")
    git(source, "commit", "--quiet", "-m", "feature")
    feature = git(source, "rev-parse", "HEAD").stdout.strip()
    feature_tree = git(source, "show", "-s", "--format=%T", feature).stdout.strip()
    patch = tmp_path / "fixture.patch"
    result = subprocess.run(
        ["git", "-C", str(source), "-c", "core.abbrev=40",
         "-c", "diff.mnemonicPrefix=false", "-c", "diff.noprefix=false",
         "-c", "diff.renames=false", "diff", "--no-color", "--binary",
         "--full-index", "--no-ext-diff", "--no-textconv", "--src-prefix=a/",
         "--dst-prefix=b/", base, feature, "--"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
    )
    patch.write_bytes(result.stdout)
    manifest = json.loads((MODULE_DIR / "manifest.json").read_text(encoding="utf-8"))
    manifest["upstream"].update(
        base_commit=base,
        base_tree=base_tree,
        feature_commit=feature,
        feature_tree=feature_tree,
    )
    manifest["patch"].update(
        file=patch.name,
        bytes=len(result.stdout),
        sha256=hashlib.sha256(result.stdout).hexdigest(),
    )
    return source, manifest, patch


def exact_base_checkout(source: Path, target: Path, manifest: dict) -> None:
    git(source, "clone", "--quiet", str(source), str(target))
    git(target, "remote", "set-url", "origin", ORIGIN)
    git(target, "checkout", "--quiet", "--detach", manifest["upstream"]["base_commit"])


def test_manifest_locks_exact_public_tuple_hash_and_evidence():
    manifest, patch = pi_module.load_manifest()
    assert manifest["schema_version"] == 1
    assert manifest["upstream"]["base_commit"] == BASE
    assert manifest["upstream"]["feature_commit"] == FEATURE
    assert manifest["patch"] == {
        "file": "hermes-pi.patch", "bytes": 775861, "sha256": PATCH_SHA256,
    }
    assert patch.stat().st_size == 775861
    assert hashlib.sha256(patch.read_bytes()).hexdigest() == PATCH_SHA256
    assert manifest["runtime"] == {
        "pi_version": "0.84.3",
        "immutable_image_id": IMAGE_ID,
        "target_platform": "linux/arm64",
    }
    assert manifest["verified_evidence"]["offline_release_suite"] == {
        "collected": 1419, "passed": 1419, "failures": 0, "skips": 0, "retries": 0,
    }
    assert manifest["verified_evidence"]["authenticated_model_e2e"]["status"] == "GAP"


def test_manifest_rejects_patch_tampering(tmp_path: Path):
    _, manifest, patch = fixture_contract(tmp_path)
    patch.write_bytes(patch.read_bytes() + b"tamper\n")
    with pytest.raises(pi_module.ModuleError, match="byte count"):
        pi_module.validate_manifest(manifest, tmp_path)


def test_plain_apply_fresh_exact_checkout_and_idempotent_rerun(tmp_path: Path):
    source, manifest, patch = fixture_contract(tmp_path)
    target = tmp_path / "install"
    state = pi_module.setup_module(
        str(target), dry_run=False, object_store_arg=str(source),
        manifest=manifest, patch_path=patch,
    )
    assert state == "installed"
    assert git(target, "rev-parse", "HEAD").stdout.strip() == manifest["upstream"]["base_commit"]
    assert pi_module.worktree_tree(target, manifest["upstream"]["base_commit"]) == manifest["upstream"]["feature_tree"]
    assert git(target, "diff", "--cached", "--quiet", check=False).returncode == 0
    before = git(target, "status", "--porcelain=v1", "--untracked-files=all").stdout
    assert pi_module.setup_module(
        str(target), dry_run=False, object_store_arg=str(source),
        manifest=manifest, patch_path=patch,
    ) == "idempotent"
    assert git(target, "status", "--porcelain=v1", "--untracked-files=all").stdout == before


@pytest.mark.parametrize("case", ["wrong-base", "wrong-tag", "wrong-origin", "dirty", "staged"])
def test_existing_checkout_rejects_wrong_identity_or_state(tmp_path: Path, case: str):
    source, manifest, patch = fixture_contract(tmp_path)
    target = tmp_path / "install"
    exact_base_checkout(source, target, manifest)
    if case == "wrong-base":
        git(target, "checkout", "--quiet", "--detach", manifest["upstream"]["feature_commit"])
    elif case == "wrong-tag":
        git(target, "tag", "wrong-release", manifest["upstream"]["feature_commit"])
        git(target, "checkout", "--quiet", "wrong-release")
    elif case == "wrong-origin":
        git(target, "remote", "set-url", "origin", "https://github.com/example/wrong.git")
    elif case == "dirty":
        (target / "owned.txt").write_text("unreviewed\n", encoding="utf-8")
    else:
        (target / "owned.txt").write_text("staged\n", encoding="utf-8")
        git(target, "add", "owned.txt")
    with pytest.raises(pi_module.ModuleError):
        pi_module.setup_module(
            str(target), dry_run=True, object_store_arg=None,
            manifest=manifest, patch_path=patch,
        )


def test_wrong_or_symlinked_object_store_and_install_paths_fail_closed(tmp_path: Path):
    source, manifest, patch = fixture_contract(tmp_path)
    git(source, "remote", "set-url", "origin", "https://github.com/example/wrong.git")
    with pytest.raises(pi_module.ModuleError, match="origin"):
        pi_module.validate_object_store(str(source), manifest)
    symlink = tmp_path / "linked"
    symlink.symlink_to(source, target_is_directory=True)
    with pytest.raises(pi_module.ModuleError, match="symlinked"):
        pi_module.validate_install_path(str(symlink))
    with pytest.raises(pi_module.ModuleError, match="separate"):
        pi_module.validate_install_path(str(MODULE_DIR / "nested-install"))


def test_dry_run_has_no_filesystem_network_global_or_auth_side_effects(tmp_path: Path):
    target = tmp_path / "install"
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    before = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))
    env = os.environ.copy()
    env["HOME"] = str(fake_home)
    result = subprocess.run(
        [sys.executable, str(MODULE_DIR / "pi_module.py"), "setup", "--dry-run", str(target)],
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "DRY-RUN" in result.stdout
    assert not target.exists()
    assert list(fake_home.iterdir()) == []
    assert sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*")) == before


def test_default_setup_executes_only_git_and_never_runtime_or_global_tools(tmp_path: Path, monkeypatch):
    source, manifest, patch = fixture_contract(tmp_path)
    target = tmp_path / "install"
    observed: list[str] = []
    original = pi_module.run

    def recording(args, **kwargs):
        observed.append(Path(str(args[0])).name)
        return original(args, **kwargs)

    monkeypatch.setattr(pi_module, "run", recording)
    pi_module.setup_module(
        str(target), dry_run=False, object_store_arg=str(source),
        manifest=manifest, patch_path=patch,
    )
    assert set(observed) == {"git"}
    source_text = (MODULE_DIR / "setup.sh").read_text(encoding="utf-8")
    for forbidden in ("docker", "npm install", "pip install", "claude", "codex", " pi auth"):
        assert forbidden not in source_text.lower()


@pytest.mark.parametrize("value", ["hermes-pi:0.84.3", "latest", "repo@sha256:abc", "sha256:" + "0" * 64])
def test_docker_mode_rejects_mutable_or_nonmanifest_images_before_docker(value: str, monkeypatch):
    manifest, _ = pi_module.load_manifest()
    called = False

    def forbidden(_image):
        nonlocal called
        called = True
        raise AssertionError("Docker must not be invoked")

    monkeypatch.setattr(pi_module, "_docker_json", forbidden)
    with pytest.raises(pi_module.ModuleError):
        pi_module.validate_requested_image(value, manifest)
    assert called is False


def test_path_classification_cannot_bypass_module_scripts_manifest_patch_tests_or_workflows():
    required = [
        "modules/pi-runtime/setup.sh",
        "modules/pi-runtime/verify.sh",
        "modules/pi-runtime/pi_module.py",
        "modules/pi-runtime/manifest.json",
        "modules/pi-runtime/hermes-pi.patch",
        "modules/pi-runtime/README.md",
        "tests/test_pi_runtime_module.py",
        ".github/workflows/ci.yml",
        ".github/workflows/pi-runtime.yml",
        ".github/workflows/release.yml",
        "scripts/classify_ci_changes.py",
    ]
    for path in required:
        assert classify_ci_changes.pi_runtime_required([path]), path
    assert not classify_ci_changes.pi_runtime_required(["modules/pi-runtime-typo/file"])
    for invalid in ("../modules/pi-runtime/setup.sh", "/modules/pi-runtime/setup.sh", "modules\\pi-runtime\\setup.sh"):
        with pytest.raises(ValueError):
            classify_ci_changes.pi_runtime_required([invalid])


def test_required_job_aggregation_rejects_failure_cancel_and_required_skip():
    success = {
        "changes": {"result": "success"},
        "release-gates": {"result": "success"},
        "pi-runtime": {"result": "success"},
    }
    assert classify_ci_changes.unexpected_required_results(success, pi_required=True) == []
    for result in ("failure", "cancelled", "skipped", ""):
        needs = copy.deepcopy(success)
        needs["pi-runtime"]["result"] = result
        assert "pi-runtime" in classify_ci_changes.unexpected_required_results(needs, pi_required=True)
    optional = copy.deepcopy(success)
    optional["pi-runtime"]["result"] = "skipped"
    assert classify_ci_changes.unexpected_required_results(optional, pi_required=False) == []
    optional["changes"]["result"] = "failure"
    assert "changes" in classify_ci_changes.unexpected_required_results(optional, pi_required=False)
    malformed = {"changes": "success", "release-gates": None, "pi-runtime": []}
    assert classify_ci_changes.unexpected_required_results(malformed, pi_required=True) == [
        "changes", "pi-runtime", "release-gates",
    ]


def test_ci_has_load_bearing_linux_arm64_windows_and_aggregate_contracts():
    ci = (REPO / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    pi = (REPO / ".github/workflows/pi-runtime.yml").read_text(encoding="utf-8")
    release = (REPO / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "needs: [changes, release-gates, pi-runtime]" in ci
    assert "if: always()" in ci
    assert "scripts/classify_ci_changes.py --check-needs" in ci
    assert "if: needs.changes.outputs.pi_runtime == 'true'" in ci
    assert "ubuntu-24.04-arm" in pi
    assert "windows-2025" in pi
    assert "test_drive_letter_colon_is_not_a_path_separator" in pi
    assert "--file-retries 0 --require-no-skips" in pi
    assert "--reproducibility --docker" in pi and IMAGE_ID in pi
    assert "1,419-case" in pi
    assert "needs: pi-runtime" in release and "needs: [pi-runtime, gates]" in release


def test_every_workflow_action_is_commit_pinned_and_tool_versions_are_exact():
    workflows = list((REPO / ".github/workflows").glob("*.yml"))
    action_pattern = re.compile(r"^\s*-?\s*uses:\s*([^\s#]+)", re.MULTILINE)
    for workflow in workflows:
        text = workflow.read_text(encoding="utf-8")
        assert "ubuntu-latest" not in text and "windows-latest" not in text
        for use in action_pattern.findall(text):
            if use.startswith("./"):
                continue
            assert re.fullmatch(r"[^@]+@[0-9a-f]{40}", use), (workflow.name, use)
    combined = "\n".join(path.read_text(encoding="utf-8") for path in workflows)
    for pin in (
        'python-version: "3.11.15"', 'version: "0.9.28"',
        'node-version: "22.23.2"', "npm@11.17.0", "pip==26.1.1",
        "PyYAML==6.0.3", "pytest==9.1.1", "28.0.4|28.0.4", "0\\.36\\.1",
    ):
        assert pin in combined


def test_public_docs_repeat_exact_evidence_boundaries_gap_and_rollback():
    module = (MODULE_DIR / "README.md").read_text(encoding="utf-8")
    roots = "\n".join(
        (REPO / path).read_text(encoding="utf-8")
        for path in (
            "README.md", "docs/FEATURES.md", "SECURITY.md", "CONTRIBUTING.md",
            "docs/RELEASING.md", "CHANGELOG.md",
        )
    )
    for value in (BASE, FEATURE, IMAGE_ID, "1,419", "5/5", "0.84.3"):
        assert value in module
        assert value in roots
    for phrase in (
        "experimental", "explicit opt-in", "Hermes remains the control plane",
        "No credentials", "authenticated", "expired", "separate installation",
    ):
        assert phrase.lower() in (module + roots).lower()
    assert "coder-stack/" in roots and "unchanged" in roots
