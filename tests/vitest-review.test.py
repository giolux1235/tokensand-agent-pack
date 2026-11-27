"""Real Vitest 4 native review. Supply a local node_modules with VITEST_TEST_MODULES."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "plugins/tokensand-agent-pack/skills/tokensand-agent-pack/scripts/review_vitest.py"
MODULES = Path(os.environ.get("VITEST_TEST_MODULES", ROOT / "node_modules")).resolve()
TEST = '''import {test,expect} from 'vitest';
import {units} from './service.mjs';
test('handles a single unit', () => expect(units([1])).toBe(1));
test('combines both private-ticket-example units', () => expect(units([1, 3])).toBe(4));
'''
CONFIG = "export default {test:{setupFiles:['./setup.mjs']}}"
BEFORE = "export const units = a => a[0];"
AFTER = "export const units = a => a.reduce((x,y) => x+y, 0);"


@unittest.skipUnless((MODULES / "vitest/vitest.mjs").is_file(), "install local Vitest 4 or set VITEST_TEST_MODULES")
class NativeReview(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for arm, implementation in (("before", BEFORE), ("after", AFTER)):
            d = self.root / arm
            d.mkdir()
            (d / "node_modules").symlink_to(MODULES, target_is_directory=True)
            (d / "units.test.mjs").write_text(TEST)
            (d / "service.mjs").write_text(implementation)
            (d / "vitest.config.mjs").write_text(CONFIG)
            (d / "setup.mjs").write_text("export {};")

    def review(self, code, *extra, label="units.test.mjs"):
        result = subprocess.run([sys.executable, str(CLI), "--before-root", str(self.root / "before"),
                                 "--after-root", str(self.root / "after"), *extra, label],
                                capture_output=True, text=True, timeout=45)
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, code, output)
        return output

    def set(self, arm, file, source):
        (self.root / arm / file).write_text(source)

    def test_fresh_result_and_private_names(self):
        self.assertNotIn("private-ticket", self.review(0))
        output = self.review(0, "--show-names")
        self.assertIn("combines both private-ticket-example units", output)
        self.assertIn("1/2 before, 2/2 after", output)
        self.set("after", "service.mjs", BEFORE)
        self.set("after", "results.xml", '<testsuite tests="2"/>')
        self.assertIn("1/2 after", self.review(100))

    def test_scope_weakening_and_ordinary_native_skips(self):
        self.set("after", "service.mjs", BEFORE)
        for source in [TEST.replace("toBe(4)", "toBe(1)"), TEST.replace("test('combines", "test.skip('combines"),
                       TEST.split("test('combines")[0]]:
            with self.subTest(source=source):
                self.set("after", "units.test.mjs", source)
                self.assertIn("HOLD", self.review(100))
        self.set("after", "service.mjs", AFTER)
        self.set("after", "units.test.mjs", TEST.replace("combines both", "new name for"))
        self.assertIn("roster changed", self.review(100))

    def test_duplicate_titles_keep_each_registration_and_location(self):
        same = TEST.replace("handles a single unit", "checks quantities").replace("combines both private-ticket-example units", "checks quantities")
        for arm in ("before", "after"):
            self.set(arm, "units.test.mjs", same)
        outcome = self.review(0, "--show-names")
        self.assertIn("1/2 before, 2/2 after", outcome)
        self.assertIn("units.test.mjs:4:", outcome)
        self.set("after", "service.mjs", BEFORE)
        self.set("after", "units.test.mjs", same.replace("toBe(4)", "toBe(1)"))
        self.assertIn("Changed test or control", self.review(100))

    def test_inputs_and_declared_data(self):
        for source in ("setup.mjs", "vitest.config.mjs"):
            prior = (self.root / "after" / source).read_text()
            self.set("after", source, prior + "\n// changed\n")
            self.assertIn(source, self.review(100))
            self.set("after", source, prior)
        for arm, data in [("before", "4"), ("after", "1")]:
            self.set(arm, "expected.json", data)
        self.assertIn("expected.json", self.review(100, "--test-source", "expected.json"))

    def test_snapshot_selection_and_empty_scope(self):
        for arm in ("before", "after"):
            d = self.root / arm / "__snapshots__"
            d.mkdir()
            (d / "units.test.mjs.snap").write_text("// " + arm)
        self.assertIn("units.test.mjs.snap", self.review(100))
        self.set("after", "units.test.mjs", "")
        self.assertIn("INVALID", self.review(1))

    def test_inverted_expected_failure_and_runner_errors(self):
        conditional = TEST + "test.fails('inverted', () => expect(1).toBe(2));"
        for arm in ("before", "after"):
            self.set(arm, "units.test.mjs", conditional)
        self.assertIn("expected failure", self.review(100))
        self.set("after", "setup.mjs", "throw Error('private stack marker');")
        result = self.review(1)
        self.assertNotIn("private stack marker", result)

    def test_wrong_file_selection_and_already_green(self):
        self.assertIn("INVALID", self.review(1, label="../outside.test.mjs"))
        self.set("before", "service.mjs", AFTER)
        self.assertIn("No previously failing", self.review(100))


if __name__ == "__main__":
    unittest.main()
