#!/usr/bin/env python3
"""Pinned installer and verifier for the public opt-in Hermes Pi runtime."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse


MODULE_DIR = Path(__file__).resolve().parent
STARTER_ROOT = MODULE_DIR.parents[1]
MANIFEST_PATH = MODULE_DIR / "manifest.json"
SHA1 = re.compile(r"^[0-9a-f]{40}$")
IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
FETCH_ATTEMPTS = 3
FETCH_TIMEOUT_SECONDS = 90
COMMAND_TIMEOUT_SECONDS = 3600
GAP_EXIT = 3


class ModuleError(RuntimeError):
    """A fail-closed module contract violation."""


class GapError(ModuleError):
    """A requested lane could not run in the available environment."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ModuleError(f"manifest {label} must be an object")
    return value


def validate_manifest(manifest: Mapping[str, Any], module_dir: Path) -> Path:
    if manifest.get("schema_version") != 1:
        raise ModuleError("unsupported Pi module manifest schema")
    if manifest.get("module") != "hermes-pi-runtime":
        raise ModuleError("unexpected Pi module identity")
    upstream = _require_mapping(manifest.get("upstream"), "upstream")
    for field in ("base_commit", "base_tree", "feature_commit", "feature_tree"):
        if not SHA1.fullmatch(str(upstream.get(field) or "")):
            raise ModuleError(f"manifest upstream.{field} is not an exact object ID")
    for field in ("repository", "feature_repository"):
        repository_identity(str(upstream.get(field) or ""), require_https=True)
    patch_info = _require_mapping(manifest.get("patch"), "patch")
    patch_name = str(patch_info.get("file") or "")
    if not patch_name or Path(patch_name).name != patch_name:
        raise ModuleError("manifest patch file must be a module-local basename")
    patch_path = module_dir / patch_name
    if patch_path.is_symlink() or not patch_path.is_file():
        raise ModuleError("manifest patch is missing or symlinked")
    expected_size = patch_info.get("bytes")
    if not isinstance(expected_size, int) or expected_size <= 0:
        raise ModuleError("manifest patch byte count is invalid")
    if patch_path.stat().st_size != expected_size:
        raise ModuleError("checked-in patch byte count does not match manifest")
    expected_hash = str(patch_info.get("sha256") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
        raise ModuleError("manifest patch SHA-256 is invalid")
    if sha256_file(patch_path) != expected_hash:
        raise ModuleError("checked-in patch SHA-256 does not match manifest")
    runtime = _require_mapping(manifest.get("runtime"), "runtime")
    if runtime.get("pi_version") != "0.84.3":
        raise ModuleError("manifest does not lock Pi 0.84.3")
    if not IMAGE_ID.fullmatch(str(runtime.get("immutable_image_id") or "")):
        raise ModuleError("manifest immutable image ID is invalid")
    if runtime.get("target_platform") != "linux/arm64":
        raise ModuleError("manifest target platform must be linux/arm64")
    evidence = _require_mapping(manifest.get("verified_evidence"), "verified_evidence")
    offline = _require_mapping(evidence.get("offline_release_suite"), "offline evidence")
    expected_offline = {
        "collected": 1419,
        "passed": 1419,
        "failures": 0,
        "skips": 0,
        "retries": 0,
    }
    if dict(offline) != expected_offline:
        raise ModuleError("manifest offline release evidence is not the reviewed 1,419-case gate")
    builds = _require_mapping(
        evidence.get("reproducible_linux_arm64_builds"), "build evidence"
    )
    if builds.get("no_cache_builds") != 2 or builds.get("matching_image_ids") != 2:
        raise ModuleError("manifest reproducibility evidence is incomplete")
    docker = _require_mapping(
        evidence.get("docker_containment_egress_e2e"), "Docker evidence"
    )
    if dict(docker) != {"cases": 5, "passed": 5, "failures": 0}:
        raise ModuleError("manifest Docker evidence is not the reviewed five-case gate")
    auth = _require_mapping(evidence.get("authenticated_model_e2e"), "auth evidence")
    if auth.get("current") is not False or auth.get("status") != "GAP":
        raise ModuleError("manifest must preserve the authenticated-model E2E gap")
    return patch_path


def load_manifest(path: Path = MANIFEST_PATH) -> tuple[dict[str, Any], Path]:
    if path.is_symlink() or not path.is_file():
        raise ModuleError("module manifest is missing or symlinked")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ModuleError("module manifest is unreadable or malformed") from exc
    if not isinstance(manifest, dict):
        raise ModuleError("module manifest must contain a JSON object")
    patch = validate_manifest(manifest, path.parent)
    return manifest, patch


def repository_identity(url: str, *, require_https: bool = False) -> str:
    """Return a canonical host/owner/repo identity without trusting URL spelling."""
    value = url.strip()
    if require_https:
        parsed = urlparse(value)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "github.com"
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ModuleError("manifest repositories must be credential-free GitHub HTTPS URLs")
        path = parsed.path
    elif value.startswith("git" + "@github.com:"):
        path = "/" + value.split(":", 1)[1]
    else:
        parsed = urlparse(value)
        if parsed.scheme not in {"https", "ssh"} or parsed.hostname != "github.com":
            raise ModuleError("Git object store origin is not the declared GitHub repository")
        path = parsed.path
    parts = [part for part in path.rstrip("/").removesuffix(".git").split("/") if part]
    if len(parts) != 2 or any(part in {".", ".."} for part in parts):
        raise ModuleError("repository URL does not identify one GitHub owner/repository")
    return "github.com/" + "/".join(part.lower() for part in parts)


def _clean_git_env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GCM_INTERACTIVE": "never",
            "GIT_ASKPASS": "/bin/false",
            "SSH_ASKPASS": "/bin/false",
            "LC_ALL": "C",
            "GIT_OPTIONAL_LOCKS": "0",
        }
    )
    if extra:
        env.update(extra)
    return env


def _redacted_command(args: Sequence[str]) -> str:
    return " ".join(str(arg) for arg in args)


def run(
    args: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout: int = COMMAND_TIMEOUT_SECONDS,
    binary: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess[Any]:
    try:
        result = subprocess.run(
            [str(arg) for arg in args],
            cwd=cwd,
            env=dict(env) if env is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=not binary,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ModuleError(f"bounded command timed out: {_redacted_command(args[:2])}") from exc
    if check and result.returncode:
        stderr = result.stderr
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        detail = (stderr or "").strip().splitlines()
        suffix = f": {detail[-1]}" if detail else ""
        raise ModuleError(f"command failed: {_redacted_command(args[:3])}{suffix}")
    return result


def git(repo: Path, *args: str, binary: bool = False, check: bool = True,
        env: Mapping[str, str] | None = None) -> subprocess.CompletedProcess[Any]:
    return run(
        ["git", "-C", str(repo), *args],
        env=env or _clean_git_env(),
        binary=binary,
        check=check,
    )


def reject_symlink_components(path: Path) -> Path:
    absolute = Path(os.path.abspath(os.path.expanduser(str(path))))
    probe = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        probe = probe / part
        if probe.exists() or probe.is_symlink():
            if probe.is_symlink():
                raise ModuleError(f"symlinked path component is forbidden: {probe}")
    return absolute


def paths_overlap(first: Path, second: Path) -> bool:
    try:
        common = Path(os.path.commonpath((str(first), str(second))))
    except ValueError:
        return False
    return common == first or common == second


def validate_install_path(target_arg: str, object_store: Path | None = None) -> Path:
    if not target_arg.strip():
        raise ModuleError("an explicit non-empty install directory is required")
    target = reject_symlink_components(Path(target_arg))
    if target in {Path(target.anchor), Path.home()}:
        raise ModuleError("installing at the filesystem root or home directory is forbidden")
    starter = STARTER_ROOT.resolve()
    if paths_overlap(target, starter):
        raise ModuleError("install directory must be separate from this starter checkout")
    if object_store is not None and paths_overlap(target, object_store):
        raise ModuleError("install directory and object store must not overlap")
    parent = target.parent
    if not parent.is_dir() or parent.is_symlink():
        raise ModuleError("install directory parent must be an existing real directory")
    if target.exists() and not target.is_dir():
        raise ModuleError("install target exists and is not a directory")
    return target


def _git_output(repo: Path, *args: str) -> str:
    return str(git(repo, *args).stdout).strip()


def validate_object_store(path_arg: str, manifest: Mapping[str, Any]) -> Path:
    path = reject_symlink_components(Path(path_arg))
    if not path.is_dir():
        raise ModuleError("local object store must be an existing Git repository directory")
    probe = git(path, "rev-parse", "--git-dir", check=False)
    if probe.returncode:
        raise ModuleError("local object store is not a Git repository")
    urls = str(git(path, "remote", "get-url", "--all", "origin", check=False).stdout)
    origins = [line.strip() for line in urls.splitlines() if line.strip()]
    expected = repository_identity(str(manifest["upstream"]["repository"]), require_https=True)
    if len(origins) != 1 or repository_identity(origins[0]) != expected:
        raise ModuleError("local object store origin does not match the declared upstream")
    for field in ("base_commit", "feature_commit"):
        commit = str(manifest["upstream"][field])
        if git(path, "cat-file", "-e", f"{commit}^{{commit}}", check=False).returncode:
            raise ModuleError(f"local object store lacks exact {field}")
    verify_commit_relationship(path, manifest)
    return path


def verify_commit_relationship(repo: Path, manifest: Mapping[str, Any]) -> None:
    upstream = manifest["upstream"]
    base = str(upstream["base_commit"])
    feature = str(upstream["feature_commit"])
    if _git_output(repo, "show", "-s", "--format=%T", base) != upstream["base_tree"]:
        raise ModuleError("base commit tree does not match manifest")
    if _git_output(repo, "show", "-s", "--format=%T", feature) != upstream["feature_tree"]:
        raise ModuleError("feature commit tree does not match manifest")
    commit_body = _git_output(repo, "cat-file", "-p", feature).splitlines()
    parents = [line.split(" ", 1)[1] for line in commit_body if line.startswith("parent ")]
    if parents != [base]:
        raise ModuleError("feature commit is not the exact direct child of the declared base")


def verify_patch_matches_commits(
    repo: Path, manifest: Mapping[str, Any], patch_path: Path
) -> None:
    base = str(manifest["upstream"]["base_commit"])
    feature = str(manifest["upstream"]["feature_commit"])
    result = git(
        repo,
        "-c", "core.abbrev=40",
        "-c", "diff.mnemonicPrefix=false",
        "-c", "diff.noprefix=false",
        "-c", "diff.renames=false",
        "diff", "--no-color", "--binary", "--full-index", "--no-ext-diff",
        "--no-textconv", "--src-prefix=a/", "--dst-prefix=b/",
        base, feature, "--", binary=True,
    )
    actual = hashlib.sha256(result.stdout).hexdigest()
    if actual != manifest["patch"]["sha256"] or result.stdout != patch_path.read_bytes():
        raise ModuleError("checked-in patch is not the exact base-to-feature commit diff")


def _fetch_exact(repo: Path, source: str, commit: str, ref: str, depth: int) -> None:
    command = [
        "git", "-c", "protocol.file.allow=always", "-C", str(repo), "fetch",
        "--force", "--no-tags", f"--depth={depth}", source, f"{commit}:{ref}",
    ]
    last_error: ModuleError | None = None
    for attempt in range(1, FETCH_ATTEMPTS + 1):
        try:
            run(
                command,
                env=_clean_git_env(),
                timeout=FETCH_TIMEOUT_SECONDS,
            )
            if _git_output(repo, "rev-parse", f"{ref}^{{commit}}") != commit:
                raise ModuleError("fetch returned an object other than the requested commit")
            return
        except ModuleError as exc:
            last_error = exc
            if attempt < FETCH_ATTEMPTS:
                time.sleep(1)
    raise ModuleError(f"bounded exact-object fetch failed after {FETCH_ATTEMPTS} attempts") from last_error


def acquire_objects(
    repo: Path, manifest: Mapping[str, Any], object_store: Path | None
) -> None:
    upstream = manifest["upstream"]
    if object_store is not None:
        source_base = object_store.as_uri()
        source_feature = source_base
    else:
        source_base = str(upstream["repository"])
        source_feature = str(upstream["feature_repository"])
    _fetch_exact(
        repo, source_base, str(upstream["base_commit"]),
        "refs/pi-runtime/base", 1,
    )
    _fetch_exact(
        repo, source_feature, str(upstream["feature_commit"]),
        "refs/pi-runtime/feature", 2,
    )
    verify_commit_relationship(repo, manifest)


def ensure_objects(
    repo: Path, manifest: Mapping[str, Any], object_store: Path | None
) -> None:
    commits = (
        str(manifest["upstream"]["base_commit"]),
        str(manifest["upstream"]["feature_commit"]),
    )
    if all(
        git(repo, "cat-file", "-e", f"{commit}^{{commit}}", check=False).returncode == 0
        for commit in commits
    ):
        verify_commit_relationship(repo, manifest)
        return
    acquire_objects(repo, manifest, object_store)


def initialize_checkout(
    destination: Path,
    manifest: Mapping[str, Any],
    patch_path: Path,
    object_store: Path | None,
) -> None:
    run(["git", "init", "--quiet", str(destination)], env=_clean_git_env())
    git(destination, "remote", "add", "origin", str(manifest["upstream"]["repository"]))
    acquire_objects(destination, manifest, object_store)
    verify_patch_matches_commits(destination, manifest, patch_path)
    base = str(manifest["upstream"]["base_commit"])
    git(destination, "checkout", "--quiet", "--detach", base)
    if _git_output(destination, "rev-parse", "HEAD") != base:
        raise ModuleError("checkout did not land on the exact base commit")


def worktree_tree(repo: Path, base: str) -> str:
    fd, raw_index = tempfile.mkstemp(prefix="hermes-pi-index.")
    os.close(fd)
    index = Path(raw_index)
    index.unlink()
    try:
        env = _clean_git_env({"GIT_INDEX_FILE": str(index)})
        git(repo, "read-tree", base, env=env)
        git(repo, "add", "--all", "--force", "--", ".", env=env)
        return str(git(repo, "write-tree", env=env).stdout).strip()
    finally:
        index.unlink(missing_ok=True)


def validate_existing_checkout(repo: Path, manifest: Mapping[str, Any]) -> str:
    if (repo / ".git").is_symlink():
        raise ModuleError("symlinked Git metadata is forbidden")
    if git(repo, "rev-parse", "--is-inside-work-tree", check=False).stdout.strip() != "true":
        raise ModuleError("existing install directory is not a Git working tree")
    urls = str(git(repo, "remote", "get-url", "--all", "origin", check=False).stdout)
    origins = [line.strip() for line in urls.splitlines() if line.strip()]
    if origins != [manifest["upstream"]["repository"]]:
        raise ModuleError("existing install origin is not the exact declared upstream URL")
    base = str(manifest["upstream"]["base_commit"])
    if _git_output(repo, "rev-parse", "HEAD") != base:
        raise ModuleError("existing install is on the wrong base, branch, or tag")
    if git(repo, "diff", "--cached", "--quiet", base, "--", check=False).returncode:
        raise ModuleError("existing install has staged changes")
    tree = worktree_tree(repo, base)
    if tree == manifest["upstream"]["base_tree"]:
        return "base"
    if tree == manifest["upstream"]["feature_tree"]:
        return "feature"
    raise ModuleError("existing install is dirty or does not equal the reviewed feature tree")


def plain_apply(repo: Path, manifest: Mapping[str, Any], patch_path: Path) -> None:
    base = str(manifest["upstream"]["base_commit"])
    git(repo, "apply", "--check", "--whitespace=error-all", str(patch_path))
    git(repo, "apply", "--whitespace=error-all", str(patch_path))
    if worktree_tree(repo, base) != manifest["upstream"]["feature_tree"]:
        raise ModuleError("plain-applied worktree does not equal the feature commit tree")
    if git(repo, "diff", "--cached", "--quiet", base, "--", check=False).returncode:
        raise ModuleError("plain apply unexpectedly changed the Git index")


def setup_module(
    target_arg: str,
    *,
    dry_run: bool,
    object_store_arg: str | None,
    manifest: Mapping[str, Any],
    patch_path: Path,
) -> str:
    object_store = (
        validate_object_store(object_store_arg, manifest) if object_store_arg else None
    )
    target = validate_install_path(target_arg, object_store)
    if target.exists():
        state = validate_existing_checkout(target, manifest)
        if state == "feature":
            if not dry_run:
                ensure_objects(target, manifest, object_store)
                verify_patch_matches_commits(target, manifest, patch_path)
            print(f"Pi runtime already installed exactly at {target}")
            return "idempotent"
        if dry_run:
            print(f"DRY-RUN: would plain-apply the verified patch to exact base at {target}")
            return "dry-run"
        ensure_objects(target, manifest, object_store)
        verify_patch_matches_commits(target, manifest, patch_path)
        plain_apply(target, manifest, patch_path)
        print(f"Installed reviewed Pi runtime worktree at {target}")
        return "installed"
    if dry_run:
        source = "the validated local object store" if object_store else "bounded public exact-object fetches"
        print(f"DRY-RUN: would create {target} from {source}")
        print("DRY-RUN: would verify base, feature, patch digest, and plain-applied feature tree")
        return "dry-run"
    created = False
    try:
        target.mkdir(mode=0o755)
        created = True
        initialize_checkout(target, manifest, patch_path, object_store)
        plain_apply(target, manifest, patch_path)
    except Exception:
        if created and target.exists():
            shutil.rmtree(target)
        raise
    print(f"Installed reviewed Pi runtime worktree at {target}")
    return "installed"


def _runtime_env(*, offline: bool = False) -> dict[str, str]:
    env = os.environ.copy()
    sensitive = re.compile(
        r"(^|_)(TOKEN|SECRET|PASSWORD|API_KEY|AUTH|CREDENTIALS?)($|_)", re.IGNORECASE
    )
    for name in list(env):
        if sensitive.search(name) or name.startswith(("OPENAI_", "ANTHROPIC_", "PI_AUTH_")):
            env.pop(name, None)
    env.update({"CI": "1", "PYTHONDONTWRITEBYTECODE": "1"})
    if offline:
        env.update(
            {
                "UV_OFFLINE": "1",
                "HTTP_PROXY": "http://127.0.0.1:9",
                "HTTPS_PROXY": "http://127.0.0.1:9",
                "ALL_PROXY": "http://127.0.0.1:9",
                "NO_PROXY": "127.0.0.1,localhost,::1",
            }
        )
    return env


def _tool_version(command: Sequence[str], expected: str) -> None:
    executable = shutil.which(command[0])
    if executable is None:
        raise GapError(f"required tool is unavailable: {command[0]}")
    result = run([executable, *command[1:]], env=_runtime_env())
    actual = str(result.stdout).strip()
    if actual != expected:
        raise ModuleError(f"{command[0]} version is {actual!r}, expected {expected!r}")


def _assert_summary(output: str, expected_passed: int) -> None:
    matches = re.findall(
        r"Summary:.*?([0-9,]+) tests passed, ([0-9,]+) failed, ([0-9,]+) skipped",
        output,
    )
    if not matches:
        raise ModuleError("release suite did not emit its required no-skip summary")
    passed, failed, skipped = (int(value.replace(",", "")) for value in matches[-1])
    if (passed, failed, skipped) != (expected_passed, 0, 0):
        raise ModuleError(
            f"release suite summary was {passed} passed/{failed} failed/{skipped} skipped; "
            f"expected {expected_passed}/0/0"
        )


def run_offline_release_suite(repo: Path, manifest: Mapping[str, Any], offline: bool) -> None:
    toolchain = manifest["toolchain"]
    _tool_version(["uv", "--version"], f"uv {toolchain['uv']}")
    uv = shutil.which("uv") or "uv"
    env = _runtime_env(offline=offline)
    if not offline:
        run([uv, "python", "install", str(toolchain["python"])], cwd=repo, env=env)
    sync = [
        uv, "sync", "--locked", "--python", str(toolchain["python"]), "--extra", "dev"
    ]
    if offline:
        sync.append("--offline")
    try:
        run(sync, cwd=repo, env=env)
    except ModuleError as exc:
        if offline:
            raise GapError("offline uv cache/Python is incomplete; the release lane did not run") from exc
        raise
    command = [
        uv, "run", "--frozen", "--offline", "--python", str(toolchain["python"]),
        "scripts/run_pi_release_tests.sh",
    ]
    result = run(command, cwd=repo, env=_runtime_env(offline=True))
    output = str(result.stdout) + str(result.stderr)
    sys.stdout.write(output)
    expected = int(manifest["verified_evidence"]["offline_release_suite"]["passed"])
    _assert_summary(output, expected)


def validate_requested_image(image: str, manifest: Mapping[str, Any]) -> str:
    if not IMAGE_ID.fullmatch(image):
        raise ModuleError("Docker mode rejects mutable tags; pass an exact sha256 image ID")
    expected = str(manifest["runtime"]["immutable_image_id"])
    if image != expected:
        raise ModuleError("Docker mode requires the exact immutable image ID from manifest")
    return image


def _docker_json(image: str) -> Mapping[str, Any]:
    docker = shutil.which("docker")
    if docker is None:
        raise GapError("Docker is unavailable; the requested Docker lane did not run")
    result = run(
        [docker, "image", "inspect", "--format", "{{json .}}", image],
        env=_runtime_env(), check=False,
    )
    if result.returncode:
        raise GapError("the exact immutable image is not loaded; the Docker lane did not run")
    try:
        info = json.loads(str(result.stdout))
    except json.JSONDecodeError as exc:
        raise ModuleError("Docker returned malformed image inspection data") from exc
    if not isinstance(info, dict):
        raise ModuleError("Docker returned malformed image inspection data")
    return info


def run_docker_e2e(repo: Path, manifest: Mapping[str, Any], image_arg: str) -> None:
    image = validate_requested_image(image_arg, manifest)
    info = _docker_json(image)
    if info.get("Id") != image or info.get("Os") != "linux" or info.get("Architecture") != "arm64":
        raise ModuleError("loaded Docker image identity/platform does not match manifest")
    uv = shutil.which("uv")
    if uv is None:
        raise GapError("uv is unavailable; Docker E2E dependencies cannot be verified")
    toolchain = manifest["toolchain"]
    _tool_version(["uv", "--version"], f"uv {toolchain['uv']}")
    env = _runtime_env()
    run([uv, "python", "install", str(toolchain["python"])], cwd=repo, env=env)
    run(
        [uv, "sync", "--locked", "--python", str(toolchain["python"]), "--extra", "dev"],
        cwd=repo, env=env,
    )
    test_env = _runtime_env(offline=True)
    test_env["HERMES_TEST_PI_IMAGE"] = image
    result = run(
        [uv, "run", "--frozen", "--offline", "--python", str(toolchain["python"]),
         "scripts/run_pi_release_tests.sh", "--docker"],
        cwd=repo, env=test_env,
    )
    output = str(result.stdout) + str(result.stderr)
    sys.stdout.write(output)
    expected = int(manifest["verified_evidence"]["docker_containment_egress_e2e"]["passed"])
    _assert_summary(output, expected)


def _assert_docker_toolchain(manifest: Mapping[str, Any]) -> str:
    docker = shutil.which("docker")
    if docker is None:
        raise GapError("Docker is unavailable; reproducibility did not run")
    toolchain = manifest["toolchain"]
    version = str(run(
        [docker, "version", "--format", "{{.Client.Version}}|{{.Server.Version}}"],
        env=_runtime_env(),
    ).stdout).strip()
    expected_engine = str(toolchain["docker_engine_ci"])
    if version != f"{expected_engine}|{expected_engine}":
        raise ModuleError(f"Docker client/server must both be the reviewed {expected_engine}")
    buildx = str(run([docker, "buildx", "version"], env=_runtime_env()).stdout)
    expected_buildx = str(toolchain["docker_buildx_ci"])
    if not re.search(rf"\bv?{re.escape(expected_buildx)}\b", buildx):
        raise ModuleError(f"Docker Buildx must be the reviewed {expected_buildx}")
    platform = str(run(
        [docker, "info", "--format", "{{.OSType}}/{{.Architecture}}"],
        env=_runtime_env(),
    ).stdout).strip()
    if platform not in {"linux/arm64", "linux/aarch64"}:
        raise GapError("reproducibility requires a native linux/arm64 Docker host")
    return docker


def run_reproducibility(repo: Path, manifest: Mapping[str, Any]) -> None:
    docker = _assert_docker_toolchain(manifest)
    builder = repo / "agent/transports/pi_assets/build-image.sh"
    if not builder.is_file():
        raise ModuleError("patched feature checkout lacks the reviewed image builder")
    expected = str(manifest["runtime"]["immutable_image_id"])
    observed: list[str] = []
    for suffix in ("first", "second"):
        result = run(
            [str(builder), docker, f"hermes-pi-module-verify:{suffix}"],
            cwd=repo, env=_runtime_env(), timeout=COMMAND_TIMEOUT_SECONDS,
        )
        candidates = [line.strip() for line in str(result.stdout).splitlines() if IMAGE_ID.fullmatch(line.strip())]
        if len(candidates) != 1:
            raise ModuleError("image builder did not return exactly one immutable loaded ID")
        observed.append(candidates[0])
    if observed[0] != observed[1]:
        raise ModuleError("two clean no-cache builds produced different immutable image IDs")
    if observed[0] != expected:
        raise ModuleError("reproduced image ID does not equal the immutable manifest ID")


def verify_module(
    *,
    object_store_arg: str | None,
    fetch: bool,
    offline: bool,
    docker_image: str | None,
    reproducibility: bool,
    integrity_only: bool,
    manifest: Mapping[str, Any],
    patch_path: Path,
) -> None:
    print("GAP authenticated model E2E: expired OAuth; no current authenticated lane or auth material")
    if offline and not object_store_arg:
        raise ModuleError("--offline requires --object-store; network fallback is forbidden")
    if fetch and object_store_arg:
        raise ModuleError("--fetch and --object-store are mutually exclusive")
    if not fetch and not object_store_arg:
        raise ModuleError("verification requires --object-store or explicit bounded --fetch")
    if integrity_only and (docker_image is not None or reproducibility):
        raise ModuleError("--integrity-only cannot be combined with Docker lanes")
    object_store = (
        validate_object_store(object_store_arg, manifest) if object_store_arg else None
    )
    if docker_image is not None:
        validate_requested_image(docker_image, manifest)
    with tempfile.TemporaryDirectory(prefix="hermes-pi-verify.") as raw:
        checkout = Path(raw) / "hermes"
        initialize_checkout(checkout, manifest, patch_path, object_store)
        plain_apply(checkout, manifest, patch_path)
        print("PASS integrity: manifest, objects, patch diff, plain apply, and feature tree")
        if docker_image is None and not reproducibility and not integrity_only:
            run_offline_release_suite(checkout, manifest, offline)
            print("PASS offline release suite: 1,419 passed; zero failures/skips/retries")
        else:
            print("GAP offline release suite: not requested by this verification invocation")
        if reproducibility:
            run_reproducibility(checkout, manifest)
            print("PASS reproducibility: two no-cache linux/arm64 builds matched the manifest image ID")
        else:
            print("GAP reproducibility: not requested (use --reproducibility on native linux/arm64)")
        if docker_image is not None:
            run_docker_e2e(checkout, manifest, docker_image)
            print("PASS Docker containment/egress E2E: 5/5")
        else:
            print("GAP Docker containment/egress E2E: not requested (use --docker with the exact ID)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    setup = subparsers.add_parser("setup")
    setup.add_argument("--dry-run", action="store_true")
    setup.add_argument("--object-store")
    setup.add_argument("install_directory")
    verify = subparsers.add_parser("verify")
    source = verify.add_mutually_exclusive_group()
    source.add_argument("--object-store")
    source.add_argument("--fetch", action="store_true")
    verify.add_argument("--offline", action="store_true")
    verify.add_argument("--docker", metavar="SHA256_IMAGE_ID")
    verify.add_argument("--reproducibility", action="store_true")
    verify.add_argument("--integrity-only", action="store_true", help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    if sys.version_info < (3, 11):
        print("ERROR: Pi runtime module requires Python 3.11 or newer", file=sys.stderr)
        return 1
    args = build_parser().parse_args(argv)
    try:
        manifest, patch_path = load_manifest()
        if args.command == "setup":
            setup_module(
                args.install_directory,
                dry_run=args.dry_run,
                object_store_arg=args.object_store,
                manifest=manifest,
                patch_path=patch_path,
            )
        else:
            verify_module(
                object_store_arg=args.object_store,
                fetch=args.fetch,
                offline=args.offline,
                docker_image=args.docker,
                reproducibility=args.reproducibility,
                integrity_only=args.integrity_only,
                manifest=manifest,
                patch_path=patch_path,
            )
        return 0
    except GapError as exc:
        print(f"GAP: {exc}", file=sys.stderr)
        return GAP_EXIT
    except ModuleError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
