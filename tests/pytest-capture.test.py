"""Exercise the native collector against genuine pytest calls in an independent checkout."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

SCRIPT = (Path(__file__).resolve().parents[1] / "plugins/tokensand-agent-pack/skills" /
          "tokensand-agent-pack/scripts/capture_pytest.py")


class Capture(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.checkout = self.root / "project with spaces"
        self.checkout.mkdir()

    def capture(self, source, *flags, conftest=None, selectors=None, addopts=None):
        (self.checkout / "test_project.py").write_text(source, encoding="utf-8")
        if conftest is not None:
            (self.checkout / "conftest.py").write_text(conftest, encoding="utf-8")
        env = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTHONDONTWRITEBYTECODE="1")
        env.pop("PYTEST_ADDOPTS", None)
        env.pop("PYTEST_PLUGINS", None)
        if addopts is not None:
            env["PYTEST_ADDOPTS"] = addopts
        output = self.root / "summary.json"
        run = subprocess.run([sys.executable, str(SCRIPT), str(output), *(selectors or ["test_project.py"]),
                              "-q", "-p", "no:cacheprovider", *flags], cwd=self.checkout, env=env,
                             capture_output=True, text=True, timeout=30)
        raw = output.read_bytes()
        result = json.loads(raw)
        self.assertEqual(set(result), {"version", "exit", "cases", "issues", "active_config", "active_config_sha256"})
        self.assertEqual(result["exit"], run.returncode)
        self.assertEqual(result["version"], 1)
        self.assertNotIn(b"PRIVATE-CANARY", raw)
        if os.name == "posix":
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        return result, {node.removeprefix("test_project.py::"): state for node, state in result["cases"]}

    def test_clean_case_and_checkout_import(self):
        (self.checkout / "myapp.py").write_text("value = 7\n", encoding="utf-8")
        result, cases = self.capture("from myapp import value\ndef test_clean(): assert value == 7\n")
        self.assertEqual((result["exit"], result["issues"], cases), (0, [], {"test_clean": "passed"}))

    def test_active_nonstandard_config_is_bound_to_its_exact_bytes(self):
        directory = self.checkout / "ci"
        directory.mkdir()
        settings = directory / "contract.ini"
        settings.write_bytes(b"[pytest]\n# private values may occur here\n")
        result, _ = self.capture("def test_ok(): pass\n", addopts="-c ci/contract.ini")
        self.assertEqual(result["active_config"], "ci/contract.ini")
        self.assertEqual(result["active_config_sha256"], hashlib.sha256(settings.read_bytes()).hexdigest())
        self.assertEqual(result["issues"], [])

    def test_external_config_never_reports_its_path_or_contents(self):
        settings = self.root / "PRIVATE-CANARY.ini"
        settings.write_text("[pytest]\n", encoding="utf-8")
        result, _ = self.capture("def test_ok(): pass\n", "-c", str(settings))
        self.assertIsNone(result["active_config"])
        self.assertIsNone(result["active_config_sha256"])
        self.assertIn("external_config", result["issues"])

    def test_no_config_and_explicit_node_selection_do_not_count_as_deselection(self):
        source = "def test_one(): pass\ndef test_two(): pass\n"
        result, cases = self.capture(source, selectors=["test_project.py::test_two"])
        self.assertEqual(cases, {"test_two":"passed"})
        self.assertEqual(result["issues"], [])
        self.assertIsNone(result["active_config"])
        self.assertIsNone(result["active_config_sha256"])

    def test_configured_keyword_filter_reports_deselection(self):
        result, cases = self.capture("def test_one(): pass\ndef test_two(): pass\n", addopts="-k test_one")
        self.assertEqual(cases, {"test_one":"passed"})
        self.assertEqual((result["exit"], result["issues"]), (0, ["deselected"]))

    def test_native_outcomes_and_strict_exceptions(self):
        result, cases = self.capture("""
import pytest
def test_clean(): pass
def test_failed(): pytest.fail("[XPASS(strict)] PRIVATE-CANARY")
@pytest.mark.skip(reason="PRIVATE-CANARY")
def test_skip(): pass
@pytest.mark.xfail(raises=ValueError, reason="PRIVATE-CANARY")
def test_expected(): raise ValueError("PRIVATE-CANARY")
@pytest.mark.xfail(raises=KeyError, reason="PRIVATE-CANARY")
def test_wrong_exception(): raise ValueError("PRIVATE-CANARY")
@pytest.mark.xfail(strict=False, reason="PRIVATE-CANARY")
def test_unexpected(): pass
@pytest.mark.xfail(strict=True, reason="PRIVATE-CANARY")
def test_strict(): pass
""", "-o", "xfail_strict=true")
        self.assertEqual(result["exit"], 1)
        self.assertEqual(result["issues"], [])
        self.assertEqual(cases, {"test_clean":"passed","test_failed":"failed","test_skip":"skipped",
                                 "test_expected":"xfailed","test_wrong_exception":"failed",
                                 "test_unexpected":"xpassed","test_strict":"xpassed-strict"})

    def test_a_non_strict_unexpected_pass_stays_exit_zero(self):
        result, cases = self.capture("import pytest\n@pytest.mark.xfail(strict=False)\ndef test_quota(): pass\n")
        self.assertEqual((result["exit"], result["issues"], cases), (0, [], {"test_quota": "xpassed"}))

    def test_run_false_does_not_execute_the_test(self):
        result, cases = self.capture("""
import pytest
from pathlib import Path
@pytest.mark.xfail(run=False, reason="PRIVATE-CANARY")
def test_off(): Path("executed").touch()
""")
        self.assertEqual((result["exit"], result["issues"], cases), (0, [], {"test_off": "xfailed-before-call"}))
        self.assertFalse((self.checkout / "executed").exists())

    def test_setup_xfail_and_call_xfail_report_distinct_stages(self):
        result, cases = self.capture("""
import pytest
from pathlib import Path
@pytest.fixture
def xfailed_setup(): pytest.xfail("PRIVATE-CANARY")
def test_before_call(xfailed_setup): Path("ran-body").touch()
def test_inside_call(): pytest.xfail("PRIVATE-CANARY")
""")
        self.assertEqual((result["exit"], result["issues"], cases), (0, [], {
            "test_before_call":"xfailed-before-call", "test_inside_call":"xfailed",
        }))
        self.assertFalse((self.checkout / "ran-body").exists())

    def test_effective_runxfail_from_project_environment_is_limited(self):
        result, cases = self.capture("import pytest\n@pytest.mark.xfail(strict=True)\ndef test_marked(): pass\n",
                                      addopts="--runxfail")
        self.assertEqual((result["exit"], result["issues"], cases), (0, ["runxfail"], {"test_marked": "passed"}))

    def test_setup_and_teardown_errors(self):
        result, cases = self.capture("""
import pytest
@pytest.fixture
def bad_setup(): raise ValueError("PRIVATE-CANARY")
@pytest.fixture
def bad_teardown():
    yield
    raise ValueError("PRIVATE-CANARY")
def test_setup(bad_setup): pass
def test_teardown(bad_teardown): pass
""")
        self.assertEqual((result["exit"], result["issues"], cases), (1, [], {"test_setup":"error","test_teardown":"error"}))

    def test_a_failed_assertion_and_failed_teardown_are_held(self):
        result, cases = self.capture("""
import pytest
@pytest.fixture
def broken():
    yield
    raise ValueError("PRIVATE-CANARY")
def test_compound(broken): assert False
""")
        self.assertEqual((result["exit"], cases), (1, {"test_compound":"error"}))
        self.assertIn("multiple_outcomes", result["issues"])

    def test_no_tests_and_collection_exception(self):
        result, _ = self.capture("# no test functions\n")
        self.assertEqual(result["exit"], 5)
        self.assertIn("empty", result["issues"])
        (self.root / "summary.json").unlink()
        result, _ = self.capture("raise ValueError('PRIVATE-CANARY')\n")
        self.assertIn("collection_error", result["issues"])
        self.assertNotEqual(result["exit"], 0)

    def test_abort_loses_selected_case_and_cannot_be_clean(self):
        result, _ = self.capture("def test_first(): assert False\ndef test_next(): pass\n", "-x")
        self.assertEqual(result["exit"], 1)
        self.assertIn("incomplete", result["issues"])

    def test_interrupted_call_is_held(self):
        result, _ = self.capture("def test_abort(): raise KeyboardInterrupt\n")
        self.assertEqual(result["exit"], 2)
        self.assertIn("interrupted", result["issues"])
        self.assertIn("incomplete", result["issues"])

    def test_duplicate_selected_node_and_rerun_never_replace_original_evidence(self):
        result, _ = self.capture("def test_same(): pass\n", "--keep-duplicates", selectors=["test_project.py","test_project.py"])
        self.assertIn("duplicate", result["issues"])
        (self.root / "summary.json").unlink()
        result, _ = self.capture("def test_same(): pass\n", conftest="""
import pytest
@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    report = yield
    if report.when == "call": report.outcome = "rerun"
    return report
""")
        self.assertIn("unsupported", result["issues"])

    def test_collect_only_does_not_forge_execution(self):
        result, cases = self.capture("def test_collected(): assert False\n", "--collect-only")
        self.assertEqual((result["exit"],cases), (0, {}))
        self.assertIn("incomplete", result["issues"])

    def test_cannot_overwrite_a_previous_receipt(self):
        previous = self.root / "summary.json"
        previous.write_text("previous run", encoding="utf-8")
        run = subprocess.run([sys.executable, str(SCRIPT), str(previous), "test_project.py"], cwd=self.checkout,
                             capture_output=True, timeout=30)
        self.assertNotEqual(run.returncode, 0)
        self.assertEqual(previous.read_text(), "previous run")


if __name__ == "__main__":
    unittest.main()
