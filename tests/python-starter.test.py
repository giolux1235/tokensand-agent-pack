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
    def test_declared_trials_expose_intermittent_failure(self):
        import runpy
        case_suite = runpy.run_path(str(SCRIPT))["case_suite"]
        for broken in (True, False):
            seen = []
            def candidate(item):
                seen.append(item)
                return not (broken and item == "late-index" and seen.count(item) == 2)

            workflow = {
                "cases": [{"id": item, "input": item} for item in ("steady-control", "late-index")],
                "repetitions": 3,
                "checks": [{"id": "found"}],
                "candidate": candidate,
                "evaluate": lambda _, output, variant: {"found": output},
            }
            result = unittest.TestResult()
            case_suite(workflow).run(result)
            self.assertEqual((result.testsRun, len(result.failures), len(result.errors)), (6, int(broken), 0))
            if broken:
                self.assertIn("late-index (trial 2/3)", str(result.failures[0][0]))

    def test_shipped_runner(self):
        with ZipFile(ROOT / "packages/tokensand_workflow_check-0.1.7-py3-none-any.whl") as wheel:
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

    def test_named_cases_catch_the_cursor_bug_in_either_order(self):
        for first, second in [("initial", "resume-tied-page"), ("resume-tied-page", "initial")]:
            with self.subTest(first=first), tempfile.TemporaryDirectory() as project:
                run = subprocess.run(
                    [sys.executable, "-I", "-B", SCRIPT, "--offline-workflow", ROOT / "examples/incremental-sync.py",
                     "--case", first, "--case", second],
                    cwd=project, capture_output=True, text=True, env={**os.environ, "FIX_CURSOR": "0", "ORACLE_DOWN": "0"},
                )
                self.assertEqual(run.returncode, 1, run.stderr)
                self.assertIn("Ran 6 tests", run.stderr)
                self.assertIn("failures=3", run.stderr)

    def test_repetitions_isolate_a_consumed_grader_input(self):
        import runpy
        result = unittest.TestResult()
        case_suite = runpy.run_path(str(SCRIPT))["case_suite"]
        source = {"required": [50]}
        definition = {
            "cases": [{"id": "threshold", "input": source}],
            "checks": [{"id": "boundary"}], "candidate": lambda _: 49,
            "evaluate": lambda item, output, _: {"boundary": not item["required"] or output == item["required"].pop()},
        }
        case_suite(definition, "threshold").run(result)
        self.assertEqual((result.testsRun, len(result.failures), len(result.errors)), (3, 3, 0))
        self.assertEqual(source, {"required": [50]})


if __name__ == "__main__":
    unittest.main()
