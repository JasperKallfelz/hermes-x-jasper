"""Parent and vendored test-package integration."""
from pathlib import Path


_VENDORED_TESTS = Path(__file__).resolve().parents[1] / "coder-stack" / "tests"
if _VENDORED_TESTS.is_dir():
    __path__.append(str(_VENDORED_TESTS))
