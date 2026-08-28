import json
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
DEEP_WORK = ROOT / "bin" / "hermes-deep-work"


FLOW_STUB = """\
#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

Path(os.environ["FLOW_WRAPPER_LOG"]).write_text(
    json.dumps(sys.argv[1:]), encoding="utf-8"
)
"""


class DeepWorkWrapperTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.repo = self.base / "repo"
        self.repo.mkdir()
        subprocess.run(
            ["git", "init", "-q"], cwd=str(self.repo), check=True
        )
        self.log = self.base / "flow-argv.json"
        self.env = os.environ.copy()
        self.env["FLOW_WRAPPER_LOG"] = str(self.log)
        self.env.pop("HERMES_CODER_ACTIVE", None)
        self.env.pop("HERMES_FLOW_ACTIVE", None)

    def tearDown(self):
        self.temp.cleanup()

    def executable(self, path, source):
        path.write_text(textwrap.dedent(source), encoding="utf-8")
        path.chmod(0o755)
        return path

    def assert_invocation(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(self.log.read_text(encoding="utf-8")),
            [
                "--source", str(self.repo.resolve()),
                "--lane", "complex",
                "--dry-run",
                "task words stay together",
            ],
        )

    def test_locates_flow_next_to_the_wrapper(self):
        install_bin = self.base / "installed-bin"
        install_bin.mkdir()
        wrapper = install_bin / "hermes-deep-work"
        wrapper.write_text(DEEP_WORK.read_text(encoding="utf-8"), encoding="utf-8")
        wrapper.chmod(0o755)
        self.executable(install_bin / "hermes-coder-flow", FLOW_STUB)
        result = subprocess.run(
            [str(wrapper), str(self.repo), "--lane", "complex", "--dry-run",
             "task", "words", "stay", "together"],
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assert_invocation(result)

    def test_explicit_absolute_flow_override_is_supported(self):
        flow = self.executable(self.base / "custom-flow", FLOW_STUB)
        env = self.env.copy()
        env["HERMES_DEEP_WORK_FLOW"] = str(flow)
        result = subprocess.run(
            [str(DEEP_WORK), str(self.repo), "--lane", "complex", "--dry-run",
             "task", "words", "stay", "together"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assert_invocation(result)


if __name__ == "__main__":
    unittest.main()
