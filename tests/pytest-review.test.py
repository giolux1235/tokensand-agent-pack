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

    def run_review(self, expected, *sources):
        sources = sources or ("tests/test_agent.py", "tests/conftest.py")
        result = subprocess.run([sys.executable, "-I", "-B", SCRIPT, self.root / "before.xml", self.root / "after.xml",
                                 "--before-root", self.root / "before", "--after-root", self.root / "after",
                                 *[arg for source in sources for arg in ("--test-source", source)]],
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

    def test_ancestor_controls_cover_both_trees_and_ignore_siblings(self):
        target = "tests/worker/check_delivery.py"
        for arm in ["before", "after"]:
            test = self.root / arm / target
            test.parent.mkdir()
            test.write_text("def test_redelivery(): pass\n")
        self.report("before", ("redelivery", "failure"))
        self.report("after", ("redelivery", None))
        sibling = self.root / "after/tests/other/conftest.py"
        sibling.parent.mkdir()
        sibling.write_text("# unrelated defaults")
        self.assertIn("(recovered)", self.run_review(0, target))
        for rel in ("conftest.py", "tests/worker/conftest.py", "tests/.pytest.toml", "pyproject.toml"):
            for arm in ("before", "after"):
                control = self.root / arm / rel
                with self.subTest(path=rel, arm=arm):
                    control.write_text("# fixture or config changed")
                    output = self.run_review(100, target)
                    self.assertIn(rel, output)
                    self.assertNotIn("(recovered)", output)
                    control.unlink()
        (self.root / "after/tests/conftest.py").write_text("# retry disabled")
        self.assertIn("tests/conftest.py", self.run_review(100, target))

    def test_implicit_fixture_may_not_escape_checkout(self):
        self.report("before", ("redelivery", "failure"))
        self.report("after", ("redelivery", None))
        (self.root / "after/conftest.py").symlink_to(self.root / "before/tests/conftest.py")
        self.assertIn("escapes its checkout", self.run_review(1, "tests/test_agent.py"))

    def test_shared_config_distinguishes_package_metadata_from_pytest_settings(self):
        self.report("before", ("delivery", "failure"))
        self.report("after", ("delivery", None))
        for name, metadata, settings, changed in [
            ("setup.cfg", "[metadata]\nversion = ", "[tool:pytest]\naddopts = -q\n", "--strict-markers"),
            ("tox.ini", "[tox]\nenvlist = ", "[pytest]\naddopts = -q\n", "--strict-markers"),
            ("pyproject.toml", "[project]\nversion = ", '[tool.pytest.ini_options]\naddopts = "-q"\n', "--strict-markers"),
        ]:
            with self.subTest(config=name):
                before = self.root / "before" / name
                after = self.root / "after" / name
                before.write_text(metadata + '"1.0"\n' + settings)
                after.write_text(metadata + '"1.1"\n' + settings)
                expected = 100 if name == "pyproject.toml" and sys.version_info < (3, 11) else 0
                self.run_review(expected, "tests/test_agent.py")
                self.run_review(100, "tests/test_agent.py", name)
                after.write_text(metadata + '"1.1"\n' + settings.replace("-q", changed))
                self.run_review(100, "tests/test_agent.py")
                before.unlink()
                after.unlink()

    def test_unparseable_shared_config_uses_full_file_hash(self):
        self.report("before", ("delivery", "failure"))
        self.report("after", ("delivery", None))
        for arm in ("before", "after"):
            (self.root / arm / "setup.cfg").write_text("[invalid " + arm)
        self.assertIn("setup.cfg", self.run_review(100, "tests/test_agent.py"))

    def test_changed_pytest_plugin_entry_points_hold_the_review(self):
        self.report("before", ("delivery", "failure"))
        self.report("after", ("delivery", None))
        for name, section in (("setup.cfg", "[options.entry_points]\npytest11 = "),
                              ("pyproject.toml", "[project.entry-points.pytest11]\nledger = ")):
            with self.subTest(config=name):
                for arm in ("before", "after"):
                    (self.root / arm / name).write_text(section + '"' + arm + '"')
                self.run_review(100, "tests/test_agent.py")
                for arm in ("before", "after"):
                    (self.root / arm / name).unlink()

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
