import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "plugins/tokensand-agent-pack/skills/tokensand-agent-pack/scripts/check_workflow.py"


class PythonRelease(unittest.TestCase):
    def test_shipped_runner(self):
        with ZipFile(ROOT / "packages/tokensand_workflow_check-0.1.3-py3-none-any.whl") as wheel:
            self.assertEqual(wheel.read("tokensand_check_starter.py"), SCRIPT.read_bytes())
        for repair, outage, code, message in [
            ("0", "0", 1, "Failed checks: Return every remaining record"),
            ("1", "0", 0, "OK"),
            ("1", "1", 1, "Unscored checks: Return every remaining record"),
        ]:
            with self.subTest(repair=repair, outage=outage), tempfile.TemporaryDirectory() as project:
                run = subprocess.run(
                    [sys.executable, "-I", "-B", SCRIPT, "--offline-workflow", ROOT / "examples/incremental-sync.py"],
                    cwd=project, capture_output=True, text=True,
                    env={**os.environ, "FIX_CURSOR": repair, "ORACLE_DOWN": outage},
                )
                self.assertEqual(run.returncode, code, run.stderr)
                self.assertIn(message, run.stderr)
                self.assertNotIn("private fixture detail", run.stderr)


if __name__ == "__main__":
    unittest.main()
