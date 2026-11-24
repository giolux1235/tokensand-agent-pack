"""Regression coverage for local review of existing pytest JUnit reports."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from xml.etree import ElementTree as ET

SCRIPT = Path(__file__).resolve().parents[1] / "plugins/tokensand-agent-pack/skills/tokensand-agent-pack/scripts/review_pytest.py"


class Review(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for dirname in ("before", "after"):
            path = self.root / dirname / "tests"
            path.mkdir(parents=True)
            (path / "test_agent.py").write_text("def test_pagination(): pass\n")
            (path / "conftest.py").write_text("# in-memory transport\n")

    def report(self, arm, *cases):
        suite = ET.Element("testsuite", tests=str(len(cases)), errors="0", failures="0", skipped="0")
        for name, status in cases:
            case = ET.SubElement(suite, "testcase", classname="tests.test_agent", name=name)
            if status:
                elem = ET.SubElement(case, status, message="PRIVATE_FIXTURE_VALUE")
                elem.text = "PRIVATE_TRACEBACK"
                key = {"failure": "failures", "skipped": "skipped", "error": "errors"}[status]
                suite.set(key, str(int(suite.get(key)) + 1))
            ET.SubElement(case, "system-out").text = "PRIVATE_APP_OUTPUT"
        ET.ElementTree(suite).write(self.root / (arm + ".xml"))

    def run_review(self, expected):
        result = subprocess.run([sys.executable, "-I", "-B", SCRIPT, self.root / "before.xml", self.root / "after.xml",
                                 "--before-root", self.root / "before", "--after-root", self.root / "after",
                                 "--test-source", "tests/test_agent.py", "--test-source", "tests/conftest.py"],
                                capture_output=True, text=True, cwd=self.root)
        message = result.stdout + result.stderr
        self.assertEqual(result.returncode, expected, message)
        self.assertNotIn("PRIVATE_", message)
        return message

    def test_names_matching_cases_and_recovers_only_the_failed_test(self):
        self.report("before", ("pagination", "failure"), ("healthy", None))
        self.report("after", ("healthy", None), ("pagination", None))
        self.assertIn("pagination", self.run_review(0))

    def test_changed_fixture_holds_apparent_repair(self):
        self.report("before", ("pagination", "failure"))
        self.report("after", ("pagination", None))
        (self.root / "after/tests/conftest.py").write_text("# transport no longer checks the cursor\n")
        self.assertIn("conftest.py", self.run_review(100))

    def test_missing_or_unscored_is_not_a_repair(self):
        self.report("before", ("pagination", "failure"), ("other", None))
        for status in ("error", "skipped"):
            with self.subTest(status=status):
                self.report("after", ("pagination", status), ("other", None))
                self.run_review(100)
        self.report("after", ("other", None))
        self.run_review(100)

    def test_changed_selection_holds_even_when_new_cases_pass(self):
        self.report("before", ("pagination", None))
        self.report("after", ("pagination", None), ("new", None))
        self.run_review(100)

    def test_indeterminate_duplicate_is_invalid(self):
        self.report("before", ("pagination", None))
        self.report("after", ("pagination", None), ("pagination", None))
        self.run_review(1)

    def test_native_suite_requires_accounting_totals(self):
        self.report("before", ("pagination", None))
        path = self.root / "before.xml"
        tree = ET.parse(path)
        tree.getroot().attrib.pop("tests")
        tree.write(path)
        self.report("after", ("pagination", None))
        self.run_review(1)

    def test_malformed_report_does_not_emit_private_data(self):
        self.report("before", ("pagination", None))
        (self.root / "after.xml").write_text("<testsuite><PRIVATE_FIXTURE_VALUE")
        self.run_review(1)


if __name__ == "__main__":
    unittest.main()
