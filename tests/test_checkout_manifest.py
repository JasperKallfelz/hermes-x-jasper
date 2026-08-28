"""Filesystem-level exact-checkout manifest regressions."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "verify_upstream_checkout_under_test",
    ROOT / "scripts" / "verify_upstream_checkout.py",
)
assert SPEC and SPEC.loader
verify = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify)


def test_filesystem_walk_error_fails_closed(tmp_path: Path):
    def broken_walk(root, *, topdown, followlinks, onerror):
        assert topdown and not followlinks
        onerror(PermissionError(13, "denied", str(Path(root) / "sealed")))
        return iter(())

    with mock.patch.object(verify, "require_git", return_value=SimpleNamespace(stdout=b"")):
        with mock.patch.object(verify.os, "walk", side_effect=broken_walk):
            with pytest.raises(verify.CheckoutError, match="unreadable filesystem entry"):
                verify._paths(tmp_path)


def test_untracked_empty_directory_is_in_exact_manifest(tmp_path: Path):
    (tmp_path / "empty-extra").mkdir()
    with mock.patch.object(verify, "require_git", return_value=SimpleNamespace(stdout=b"")):
        assert verify._paths(tmp_path) == ["empty-extra"]
        assert verify.manifest(tmp_path)["empty-extra"] == ("directory",)
