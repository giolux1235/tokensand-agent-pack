"""Exercise the review CLI against real local pytest runs in two trusted checkouts."""
from importlib.util import find_spec
import json
import os
from pathlib import Path
import subprocess
import sys
import sysconfig
import tempfile
import unittest
import venv


SCRIPT = (Path(__file__).resolve().parents[1]
          / "plugins/tokensand-agent-pack/skills/tokensand-agent-pack/scripts/review_pytest.py")
TARGET = "test_worker.py::test_retry"
TEST = '''import pytest
from worker import deliveries, experimental

@pytest.mark.xfail(experimental, reason="PRIVATE_expected_reason", strict=False)
@pytest.mark.parametrize("event", ["PRIVATE_event_value"], ids=["PRIVATE_tenant_id"])
def test_retry(event):
    print("PRIVATE_worker_output")
    assert deliveries([event, event]) == [event], "PRIVATE_assertion_details"
'''


@unittest.skipUnless(find_spec("pytest"), "native CLI checks require pytest in the test interpreter")
class NativeReview(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.before = self.root / "before"
        self.after = self.root / "after"
        for root in (self.before, self.after):
            root.mkdir()
            (root / "test_worker.py").write_text(TEST)
        self.worker(self.before, fixed=False)
        self.worker(self.after, fixed=True)

    def worker(self, root, *, fixed, experimental=False):
        body = "list(dict.fromkeys(events))" if fixed else "list(events)"
        (root / "worker.py").write_text(
            f"experimental = {experimental}\n\ndef deliveries(events):\n    return {body}\n"
        )

    def review(self, expected, *arguments, caller_env=None):
        env = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTHONDONTWRITEBYTECODE="1")
        for name in ("PYTHONPATH", "PYTEST_ADDOPTS", "PYTEST_PLUGINS"):
            env.pop(name, None)
        env.update(caller_env or {})
        result = subprocess.run(
            [sys.executable, "-I", "-B", SCRIPT, "--run", "--before-root", self.before, *arguments],
            cwd=self.after, env=env, text=True, capture_output=True, timeout=30,
        )
        message = result.stdout + result.stderr
        self.assertEqual(result.returncode, expected, message)
        self.assertNotIn("PRIVATE_", message)
        return message.lower()

    def test_non_strict_xpass_is_held_with_case_status(self):
        self.worker(self.after, fixed=True, experimental=True)
        result = self.review(100, "--after-root", self.after, TARGET)
        self.assertIn("hold", result)
        self.assertIn("xpassed", result)
        self.assertNotIn("(recovered)", result)

    def test_same_parametrized_assertion_recovers_without_exposing_input(self):
        result = self.review(0, "--before-python", sys.executable, "--after-python", sys.executable,
                             TARGET + "[PRIVATE_tenant_id]")
        self.assertIn("pass", result)
        self.assertIn("test_retry", result)

    def test_weakened_assertion_holds_an_apparent_repair(self):
        self.worker(self.after, fixed=False)
        (self.after / "test_worker.py").write_text(TEST.replace("== [event]", "!= []"))
        result = self.review(100, "--after-root", self.after, "--test-source", "test_worker.py", TARGET)
        self.assertIn("hold", result)
        self.assertIn("test_worker.py", result)
        self.assertNotIn("(recovered)", result)

    def test_empty_and_missing_selections_are_setup_errors(self):
        for root in (self.before, self.after):
            (root / "test_empty.py").write_text("# no tests in this module\n")
        for targets in ((), ("test_empty.py",), ("missing.py",), (TARGET + "[PRIVATE_unknown_id]",)):
            with self.subTest(targets=targets):
                self.review(1, *targets)

    def test_missing_interpreter_or_missing_pytest_is_a_setup_error(self):
        empty_env = self.root / "empty-env"
        venv.EnvBuilder(with_pip=False).create(empty_env)
        empty_python = empty_env / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        for interpreter in (self.root / "absent-python", empty_python):
            with self.subTest(interpreter=interpreter):
                self.review(1, "--after-root", self.after, "--after-python", interpreter, TARGET)

    def test_changed_active_config_outside_test_ancestors_holds(self):
        self.worker(self.before, fixed=True)
        for root in (self.before, self.after):
            (root / "ci").mkdir()
            (root / "ci/pytest.ini").write_text("[pytest]\nxfail_strict = false\n")
        caller = {"PYTEST_ADDOPTS": "--rootdir=. -c ci/pytest.ini"}
        self.review(0, TARGET, caller_env=caller)
        (self.after / "ci/pytest.ini").write_text("[pytest]\nxfail_strict = true\n")
        self.assertIn("hold", self.review(100, TARGET, caller_env=caller))

    def test_activated_candidate_does_not_supply_baseline_console_script(self):
        check = '''import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

def test_console_retry():
    tool = shutil.which("worker-retry-count")
    count = subprocess.check_output([tool], text=True).strip()
    evidence = [sys.prefix, os.getenv("VIRTUAL_ENV"), os.get_exec_path()[0], tool, count]
    Path("observed.json").write_text(json.dumps(evidence))
    assert count == "1"
'''
        script_dir = "Scripts" if os.name == "nt" else "bin"
        command = "worker-retry-count.cmd" if os.name == "nt" else "worker-retry-count"
        for root, count in ((self.before, "2"), (self.after, "1")):
            env_root = root / ".venv"
            venv.EnvBuilder(with_pip=False, system_site_packages=True).create(env_root)
            scripts = env_root / script_dir
            if sys.prefix != sys.base_prefix:
                python = scripts / ("python.exe" if os.name == "nt" else "python")
                lib = subprocess.check_output([python, "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
                                              text=True).strip()
                (Path(lib) / "test-dependencies.pth").write_text(sysconfig.get_path("purelib") + "\n")
            body = "@echo off\necho " if os.name == "nt" else "#!/bin/sh\necho "
            tool = scripts / command
            tool.write_text(body + count + "\n")
            tool.chmod(0o755)
            (root / "test_console.py").write_text(check)

        active = self.after / ".venv"
        caller = {"VIRTUAL_ENV": str(active), "PATH": str(active / script_dir) + os.pathsep + os.environ["PATH"]}
        result = self.review(0, "test_console.py", caller_env=caller)
        for root, count in ((self.before, "2"), (self.after, "1")):
            env_root = root / ".venv"
            scripts = env_root / script_dir
            prefix, activated, first_path, tool, output = json.loads((root / "observed.json").read_text())
            self.assertEqual([Path(v).resolve() for v in (prefix, activated, first_path, tool)],
                             [p.resolve() for p in (env_root, env_root, scripts, scripts / command)])
            self.assertEqual(output, count)
        self.assertIn("(recovered)", result)

    def test_option_looking_filename_is_rejected_as_a_positional_target(self):
        filename = "--junitxml=unexpected.py"
        for root in (self.before, self.after):
            (root / filename).write_text("def test_example(): pass\n")
        self.review(1, "--", filename)
        for root in (self.before, self.after):
            self.assertFalse((root / "unexpected.py").exists())


if __name__ == "__main__":
    unittest.main()
