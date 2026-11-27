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

    def test_markdown_reports_clean_revisions_and_original_counts_without_private_labels(self):
        revisions = []
        for root in (self.after, self.peer):
            self.git(root, "add", "-u")
            self.git(root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                     "commit", "-qm", "private message")
            revisions.append(subprocess.check_output(["git", "-C", root, "rev-parse", "HEAD"], text=True).strip())
        result = self.review(100, "--markdown", pair=True, replay=False)
        self.assertIn(f"| 1 | {revisions[0]} | 2/2 | 1/2 (replayed) | Hold |", result)
        self.assertIn(f"| 2 | {revisions[1]} | 2/2 | 2/2 (same suite) | Verified repair |", result)
        self.assertIn("checked implementation stayed the same", result)
        self.assertIn("Baseline passed: 1/2", result)
        for private in ("private message", "private-customer-example", "route.mjs", "route.test.mjs",
                        str(self.root), "proposed", "other", "case#", "--before-root"):
            self.assertNotIn(private, result)

    def test_markdown_dirty_worktree_and_missing_proposal_keep_their_own_results(self):
        self.git(self.after, "update-index", "--assume-unchanged", "route.test.mjs")
        result = self.review(1, "--markdown", "--after-root", str(self.root / "private-missing"),
                             pair=True, replay=False)
        self.assertIn("| 1 | Uncommitted | 2/2 | 1/2 (replayed) | Unavailable |", result)
        self.assertIn("| 2 | Uncommitted | 2/2 | 2/2 (same suite) | Unavailable |", result)
        self.assertIn("| 3 | Unavailable | Unavailable | Unavailable | Unavailable |", result)
        self.assertNotIn("private-missing", result)
        self.assertNotIn("private-customer-example", self.review(1, "--show-names", "--markdown", replay=False))

    def test_markdown_refuses_staged_repair_and_a_changed_clean_head_after_real_capture(self):
        from argparse import Namespace
        from contextlib import redirect_stdout
        from io import StringIO
        from unittest.mock import patch
        sys.path.insert(0, str(CLI.parent))
        self.addCleanup(sys.path.remove, str(CLI.parent))
        import review_vitest as runner
        self.git(self.peer, "add", "route.mjs")
        args = Namespace(before_root=self.base, after_root=[self.peer], files=[Path("route.test.mjs")],
                         implementation=[], test_source=[], config=None, show_names=False)
        stream = StringIO()
        with redirect_stdout(stream):
            self.assertEqual(runner.markdown(args), 1)
        self.assertIn("Uncommitted | 2/2 | 2/2 (same suite) | Unavailable", stream.getvalue())
        for message in ("fixed", "same tree"):
            self.git(self.peer, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                     "commit", "--allow-empty", "-qm", message)
        new = subprocess.check_output(["git", "-C", self.peer, "rev-parse", "HEAD"], text=True).strip()
        self.git(self.peer, "reset", "--hard", "HEAD^")
        capture = runner.capture

        def move_after_capture(root, selected, directory):
            measured = capture(root, selected, directory)
            if root == self.peer:
                self.git(root, "update-ref", "HEAD", new)
            return measured

        stream = StringIO()
        with patch.object(runner, "capture", move_after_capture), redirect_stdout(stream):
            self.assertEqual(runner.markdown(args), 1)
        self.assertIn("Unavailable | 2/2 | 2/2 (same suite) | Unavailable", stream.getvalue())
        self.assertNotIn(new, stream.getvalue())

    def test_markdown_keeps_unavailable_original_separate_from_submitted_score(self):
        (self.after / "private-untracked").write_text("secret-marker")
        result = self.review(1, "--markdown", replay=False)
        self.assertIn("| 1 | Unavailable | 2/2 | Unavailable | Unavailable |", result)
        self.assertNotIn("private-untracked", result)
        self.assertNotIn("secret-marker", result)

    def test_recipient_rechecks_mixed_verdict_and_rejects_forged_note(self):
        for root in (self.after, self.peer):
            self.git(root, "add", "-u")
            self.git(root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                     "commit", "-qm", "proposal")
        note = self.root / "private-note.md"
        authored = self.review(100, "--markdown", pair=True, replay=False)
        self.assertRegex(authored, r"Scope and outcomes \(SHA-256\): [0-9a-f]{64}\n")
        note.write_text(authored)
        self.assertIn("Review reproduced", self.review(100, "--verify-markdown", str(note), pair=True, replay=False))
        note.write_text(authored.replace("1/2 (replayed)", "2/2 (replayed)"))
        self.assertIn("Review mismatch", self.review(1, "--verify-markdown", str(note), pair=True, replay=False))
        note.write_text("private content\n" * 20000)
        error = self.review(1, "--verify-markdown", str(note), pair=True, replay=False)
        self.assertNotIn("private", error)

    def test_recipient_rejects_the_same_counts_from_a_different_selection(self):
        for root in (self.base, self.peer):
            (root / "second.test.mjs").write_text(TEST)
            self.git(root, "add", ".")
            self.git(root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                     "commit", "-qm", "second suite")
        common = [sys.executable, "-B", str(CLI), "--before-root", str(self.base), "--after-root", str(self.peer),
                  "--implementation", "route.mjs"]
        file = self.root / "note.md"
        first = subprocess.run(common + ["--markdown", "route.test.mjs"], text=True, capture_output=True, check=True)
        second = subprocess.run(common + ["--markdown", "second.test.mjs"], text=True, capture_output=True, check=True)
        self.assertEqual(first.stdout.split("Scope and outcomes")[0], second.stdout.split("Scope and outcomes")[0])
        self.assertNotEqual(first.stdout, second.stdout)
        file.write_text(first.stdout)
        for selection, expected in (("route.test.mjs", 0), ("second.test.mjs", 1)):
            rerun = subprocess.run(common + ["--verify-markdown", str(file), selection], text=True, capture_output=True)
            self.assertEqual(rerun.returncode, expected, rerun.stderr + rerun.stdout)
        # Case-to-outcome association and runner version are independently bound.
        sys.path.insert(0, str(CLI.parent))
        self.addCleanup(sys.path.remove, str(CLI.parent))
        from review_vitest import measured_cases
        a, b = ("suite", "a", "id1", 2, 1), ("suite", "b", "id2", 3, 1)
        original = measured_cases({a: "failed", b: "passed"}, "4.1.11")
        self.assertNotEqual(original, measured_cases({a: "passed", b: "failed"}, "4.1.11"))
        self.assertNotEqual(original, measured_cases({a: "failed", b: "passed"}, "4.1.12"))

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
        # A removed tracked data file can also change a conditional oracle.
        (self.after / "route.test.mjs").write_text(text)
        (self.after / "route.mjs").write_text(BAD + "// equivalent\n")
        (self.after / "use-last.txt").unlink()
        self.assertIn("unbound tracked data file changed", self.review(100, replay=False))
        (self.after / "use-last.txt").write_text("same")
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

    def test_contained_doc_links_and_unscoped_outer_file(self):
        for arm in (self.base, self.after):
            (arm / "docs").mkdir()
            (arm / "docs/guide.md").write_text("local documentation")
            (arm / "docs/other.md").write_text("other document")
            (arm / "guide.md").symlink_to("docs/guide.md")
            (arm / "guides").symlink_to("docs", target_is_directory=True)
            self.git(arm, "add", "docs", "guide.md", "guides")
        self.assertIn("Original tests (replayed): 1/2 passed", self.review(100))
        (self.after / "guide.md").unlink()
        (self.after / "guide.md").symlink_to("docs/other.md")
        self.assertIn("Original tests: unavailable", self.review(1))
        outer = self.root / "outside-private-fixture.txt"
        outer.write_text("never include this file")
        (self.after / "guide.md").unlink()
        (self.after / "guide.md").symlink_to("../outside-private-fixture.txt")
        output = self.review(1)
        self.assertIn("Original tests: unavailable", output)
        self.assertNotIn(outer.read_text(), output)
        self.assertNotIn("Original tests (replayed)", output)

    def test_nested_package_with_parent_fixture_is_not_silently_reconstructed(self):
        for arm in (self.base, self.after):
            sub = arm / "service"
            sub.mkdir()
            (sub / "package.json").write_text('{"private":true,"type":"module"}')
            (sub / "node_modules").symlink_to(MODULES, target_is_directory=True)
            (sub / "route.mjs").write_text(BAD)
            (arm / "use-original.txt").write_text("original rule")
            case = ("import {test,expect} from 'vitest'; import {existsSync} from 'node:fs'; "
                    "import {route} from './route.mjs'; test('last',()=>expect(route([3,8]))"
                    ".toBe(existsSync(new URL('../use-original.txt',import.meta.url))?8:3));")
            (sub / "route.test.mjs").write_text(case if arm == self.base else case.replace("?8:3", "?3:3"))
            self.git(arm, "add", "use-original.txt", "service/package.json", "service/route.test.mjs", "service/route.mjs")
        self.base = self.base / "service"
        self.after = self.after / "service"
        output = self.review(1)
        self.assertIn("select the Git worktree root", output)
        self.assertNotIn("Original tests (replayed)", output)

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
