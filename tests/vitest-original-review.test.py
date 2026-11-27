"""Exercise the public CLI against real local native tests and Git worktrees."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "plugins/tokensand-agent-pack/skills/tokensand-agent-pack/scripts/review_vitest.py"
MODULES = Path(os.environ.get("VITEST_TEST_MODULES", ROOT / "node_modules")).resolve()
TEST = """import {test, expect} from 'vitest';
import {route} from './route.mjs';
test('first item', () => expect(route([3])).toBe(3));
test('private-customer-example last item', () => expect(route([3, 8])).toBe(8));
"""
BAD = "export const route = a => a[0];\n"
GOOD = "export const route = a => a.at(-1);\n"


@unittest.skipUnless((MODULES / "vitest/vitest.mjs").is_file(), "install Vitest 4 or supply VITEST_TEST_MODULES")
class OriginalReview(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.base = self.root / "base"
        self.base.mkdir()
        self.git(self.base, "init", "-q")
        (self.base / "route.mjs").write_text(BAD)
        (self.base / "route.test.mjs").write_text(TEST)
        (self.base / ".gitignore").write_text("node_modules/\ncache/\n")
        self.git(self.base, "add", ".")
        self.git(self.base, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture")
        self.after = self.root / "proposed"
        self.peer = self.root / "other"
        for path in (self.after, self.peer):
            self.git(self.base, "worktree", "add", "-q", "--detach", str(path), "HEAD")
        for path in (self.base, self.after, self.peer):
            (path / "node_modules").symlink_to(MODULES, target_is_directory=True)
        (self.after / "route.test.mjs").write_text(TEST.replace("toBe(8)", "toBeGreaterThan(0)"))
        (self.peer / "route.mjs").write_text(GOOD)

    def git(self, root, *args):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, stdin=subprocess.DEVNULL)

    def review(self, status, *flags, replay=True, pair=False):
        cmd = [sys.executable, "-B", str(CLI), "--before-root", str(self.base), "--after-root", str(self.after)]
        if pair:
            cmd += ["--after-root", str(self.peer)]
        if replay:
            cmd += ["--replay-original-tests"]
        result = subprocess.run(cmd + list(flags) + ["route.test.mjs"], text=True, capture_output=True, timeout=45)
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, status, output)
        return output

    def test_one_command_replays_real_native_assertions_without_touching_the_candidates(self):
        def evidence():
            return {str(f):hashlib.sha256(f.read_bytes()).hexdigest() for arm in (self.base, self.after, self.peer)
                    for f in [arm / "route.mjs", arm / "route.test.mjs"]}
        original = evidence()
        result = self.review(100, pair=True)
        self.assertIn('Proposal 1 "proposed": HOLD', result)
        self.assertIn("Original tests (replayed): 1/2 passed, 1 failed.", result)
        self.assertIn('Proposal 2 "other": VERIFIED REPAIR', result)
        self.assertIn("Original tests (submitted run): 2/2 passed, 0 failed.", result)
        self.assertNotIn("private-customer-example", result)
        self.assertNotIn(str(self.root), result)
        self.assertEqual(original, evidence())
        (self.after / "route.mjs").write_text(GOOD)
        result = self.review(100, "--show-names")
        self.assertIn("HOLD", result)
        self.assertIn("Original tests (replayed): 2/2 passed, 0 failed.", result)
        self.assertNotIn("VERIFIED REPAIR", result)

    def test_hidden_tracked_oracle_bare_guard_and_explicit_original_fixture(self):
        text = """import {test,expect} from 'vitest';
import {readFileSync} from 'node:fs';
import {route} from './route.mjs';
const expected=JSON.parse(readFileSync(new URL('./expected.json',import.meta.url),'utf8'));
test('first item',()=>expect(route([3])).toBe(3));
test('last item',()=>expect(route([3,8])).toBe(expected));
"""
        for arm, value in [(self.base, 8), (self.after, 3)]:
            (arm / "route.test.mjs").write_text(text)
            (arm / "expected.json").write_text(json.dumps(value))
            self.git(arm, "add", "expected.json")
        (self.after / "route.mjs").write_text(BAD + "// same behavior\n")
        bare = self.review(100, replay=False)
        self.assertIn("unbound tracked data file changed", bare)
        self.assertNotIn("VERIFIED REPAIR", bare)
        strict = self.review(1)
        self.assertIn("HOLD", strict)
        self.assertIn("Original tests: unavailable", strict)
        self.assertNotIn("VERIFIED REPAIR", strict)
        named = self.review(100, "--test-source", "expected.json")
        self.assertIn("Original tests (replayed): 1/2 passed, 1 failed.", named)
        self.assertIn("HOLD", named)
        self.assertEqual((self.after / "expected.json").read_text(), "3")

    def test_conditional_tracked_data_and_irrelevant_readme(self):
        text = """import {test,expect} from 'vitest';
import {existsSync} from 'node:fs';
import {route} from './route.mjs';
test('last item',()=>expect(route([3,8])).toBe(existsSync(new URL('./use-last.txt',import.meta.url))?8:3));
"""
        for arm in (self.base, self.after):
            (arm / "route.test.mjs").write_text(text)
            (arm / "use-last.txt").write_text("same")
            self.git(arm, "add", "use-last.txt")
        (self.after / "route.test.mjs").write_text(text.replace("?8:3", "?3:3"))
        result = self.review(100)
        self.assertIn("Original tests (replayed): 0/1 passed, 1 failed.", result)
        for arm in (self.base, self.after):
            (arm / "route.test.mjs").write_text(text)
            (arm / "README.md").write_text(arm.name)
            self.git(arm, "add", "README.md")
        (self.after / "route.mjs").write_text(GOOD)
        self.assertIn("VERIFIED REPAIR", self.review(0, replay=False))
        strict = self.review(1)
        self.assertIn("HOLD", strict)
        self.assertIn("Original tests: unavailable", strict)
        self.assertNotIn("VERIFIED REPAIR", strict)

    def test_ignored_inputs_and_missing_controls_fail_closed_without_printing_private_data(self):
        hidden = self.after / "cache"
        hidden.mkdir()
        (hidden / "private-name").write_text("secret-not-for-stdout")
        output = self.review(1, pair=True)
        self.assertIn("untracked or ignored", output)
        self.assertNotIn("secret-not", output)
        self.assertNotIn("private-name", output)
        self.assertIn('Proposal 1 "proposed": ORIGINAL TESTS UNAVAILABLE', output)
        self.assertIn('Proposal 2 "other": VERIFIED REPAIR', output)
        self.assertIn("INVALID", self.review(1, "--test-source", "missing.txt"))
        self.assertIn("INVALID", self.review(1, "--test-source", "../missing.txt"))


if __name__ == "__main__":
    unittest.main()
