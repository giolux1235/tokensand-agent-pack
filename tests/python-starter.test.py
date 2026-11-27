import os
import shlex
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "plugins/tokensand-agent-pack/skills/tokensand-agent-pack/scripts/check_workflow.py"


class PythonRelease(unittest.TestCase):
    def test_required_coverage_keeps_the_suite_and_rejects_missing_or_unselected_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "ran"
            adapter = root / "workflow.py"
            adapter.write_text("""
from pathlib import Path
def task(item):
    with open(Path(__file__).with_name('ran'), 'a') as witness:
        witness.write(item + '\\n')
    return item
workflow = dict(cases=[{'id':i,'input':i} for i in ('one', 'two')],
                checks=[{'id':i} for i in ('keep', 'other')],
                repetitions=2, baseline=task, candidate=task,
                evaluate=lambda item, output, variant: {'keep': True, 'other': item == 'one'})
""")
            def check(flags):
                marker.unlink(missing_ok=True)
                return subprocess.run([sys.executable, "-B", str(SCRIPT), "--offline-workflow", str(adapter), *flags],
                                      capture_output=True, text=True, timeout=10)
            for extra, code, text, callback_count in [
                (["--require-case=one", "--require-check=keep"], 1, 'Existing failure: "two" / "other" · trials 1, 2', 8),
                (["--case=one", "--require-case=one", "--require-check=keep"], 0, 'Passed both: "one" / "keep" · trials 1, 2, 3', 6),
                (["--require-check=gone", "--require-case=absent"], 2, 'Missing required cases: "absent"\nMissing required checks: "gone"', 0),
                (["--case=one", "--require-case=two"], 2, 'Required case "two" is outside --case selection', 0),
                (["--require-check=keep", "--require-check=sk-example-private"], 2, 'Missing required checks: #2', 0),
                (["--case=unknown"], 2, 'Missing required cases: "unknown"', 0),
            ]:
                with self.subTest(extra=extra):
                    result = check(["--compare-baseline", *extra])
                    self.assertEqual(result.returncode, code, result.stderr)
                    self.assertIn(text, result.stderr)
                    self.assertNotIn("sk-example-private", result.stderr)
                    self.assertEqual(len(marker.read_text().splitlines()) if marker.exists() else 0, callback_count)
            for flags in (["--require-case=one"], ["--compare-baseline", "--require-check="],
                          ["--compare-baseline", "--require-case=one", "--require-case=one"],
                          ["--compare-baseline", "--require-check=keep", "--require-check=keep"],
                          ["--compare-baseline", "--require-check=secret\nvalue"]):
                result = check(flags)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertFalse(marker.exists())
                self.assertNotIn("secret\nvalue", result.stderr)

    def test_missing_coverage_preempts_empty_matrices_and_known_false_remains_failed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            adapter = root / "workflow.py"
            run = lambda: subprocess.run([sys.executable, "-B", str(SCRIPT), "--offline-workflow", str(adapter),
                "--compare-baseline", "--require-case=event", "--require-check=ok"],
                capture_output=True, text=True, timeout=10)
            adapter.write_text("workflow=dict(cases=[],checks=[],baseline=lambda x:x,candidate=lambda x:x,evaluate=lambda *_:{})")
            result = run()
            self.assertEqual(result.returncode, 2)
            self.assertIn('Missing required cases: "event"\nMissing required checks: "ok"', result.stderr)
            adapter.write_text("workflow=dict(cases=[{'id':'event','input':1}], checks=[{'id':'ok'},{'id':'sk-private-name'}],"
                               "baseline=lambda x:x,candidate=lambda x:x,evaluate=lambda *_:{'ok':False})")
            result = run()
            self.assertEqual(result.returncode, 1)
            self.assertIn('Existing failure: "event" / "ok"', result.stderr)
            self.assertIn('Unscored: "event" / #2', result.stderr)
            self.assertNotIn("sk-private-name", result.stderr)

    def test_missing_adapter_points_to_an_existing_file_or_the_template_without_running_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wrong = root / "my_workflow.py"
            for mode in ([], ["--compare-baseline"], ["--case", "initial", "--inspect-values"]):
                for available in (False, True):
                    with self.subTest(mode=mode, available=available):
                        known = root / "workflow.py"
                        if available:
                            known.write_text("raise RuntimeError('unselected adapter executed')\n")
                            wrong.mkdir()  # A directory is not a workflow even when its name ends in .py.
                        run = subprocess.run([sys.executable, "-I", "-B", SCRIPT, "--offline-workflow", wrong, *mode],
                                             cwd=root, capture_output=True, text=True)
                        self.assertEqual(run.returncode, 2)
                        self.assertEqual(run.stdout, "")
                        self.assertIn("No cases ran", run.stderr)
                        self.assertIn("--offline-workflow ./workflow.py" if available else "examples/incremental-sync.py", run.stderr)
                        self.assertNotIn("Traceback", run.stderr)
                        self.assertNotIn(directory, run.stderr)
                        self.assertNotIn("unselected adapter", run.stderr)
                        if available:
                            known.unlink()
                            wrong.rmdir()

    def test_existing_adapter_keeps_its_native_debug_details_and_private_compare_behavior(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            file = root / "workflow.py"
            for body, error, detail in [
                ("import tokensand_test_missing_package", "ModuleNotFoundError", "tokensand_test_missing_package"),
                ("open('test-private-invoice.json')", "FileNotFoundError", "test-private-invoice.json"),
            ]:
                file.write_text(body)
                for mode in ([], ["--case", "initial", "--inspect-values"], ["--compare-baseline"]):
                    with self.subTest(error=error, mode=mode):
                        run = subprocess.run([sys.executable, "-I", "-B", SCRIPT, "--offline-workflow", file, *mode],
                                             cwd=root, capture_output=True, text=True)
                        self.assertEqual(run.stdout, "")
                        self.assertNotIn("No cases ran", run.stderr)
                        if mode == ["--compare-baseline"]:
                            self.assertEqual(run.returncode, 2)
                            self.assertIn("Could not score the baseline", run.stderr)
                            self.assertNotIn(detail, run.stderr)
                        else:
                            self.assertEqual(run.returncode, 1)
                            self.assertIn(error, run.stderr)
                            self.assertIn(detail, run.stderr)

    def test_paired_check_runs_with_all_file_writes_denied(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            # Install a write guard in the CLI and every Python subprocess on all platforms.
            (root / "sitecustomize.py").write_text("""
import os, sys
def read_only(event, args):
    if event == "open":
        path, _, flags = args
        if isinstance(path, (str, bytes)) and os.fsdecode(path) != os.devnull:
            if flags & (os.O_CREAT | os.O_TRUNC | os.O_RDWR | os.O_WRONLY):
                raise PermissionError("Read-only test checkout")
    if event in ("os.mkdir", "tempfile.mkstemp", "tempfile.mkdtemp"):
        raise PermissionError("Read-only test checkout")
sys.addaudithook(read_only)
""")
            with ZipFile(ROOT / "packages/tokensand_workflow_check-0.1.16-py3-none-any.whl") as wheel:
                wheel.extractall(root / "installed")
            for command in ([sys.executable, str(SCRIPT)], [sys.executable, "-m", "tokensand_check"]):
                with self.subTest(command=command[-1]):
                    run = subprocess.run(
                        [*command, "--compare-baseline", "--offline-workflow", str(ROOT / "examples/incremental-sync.py")],
                        cwd=root, capture_output=True, text=True, timeout=10,
                        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "FIX_CURSOR": "1",
                             "PYTHONPATH": os.pathsep.join((str(root), str(root / "installed")))},
                    )
                    self.assertEqual(run.returncode, 0, run.stderr)
                    self.assertIn('Passed both: "resume-tied-page" / "complete"', run.stderr)

    def test_installed_and_standalone_runners_match_the_same_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with ZipFile(ROOT / "packages/tokensand_workflow_check-0.1.16-py3-none-any.whl") as wheel:
                self.assertEqual(wheel.read("tokensand_check_starter.py"), SCRIPT.read_bytes())
                wheel.extractall(root / "package")
            adapter = ROOT / "examples/incremental-sync.py"
            for command in ([sys.executable, str(SCRIPT)], [sys.executable, "-m", "tokensand_check"]):
                for repaired in ("0", "1"):
                    with self.subTest(command=command[-1], repaired=repaired):
                        run = subprocess.run([*command, "--compare-baseline", "--offline-workflow", str(adapter)],
                            cwd=root, capture_output=True, text=True, timeout=10,
                            env={"PATH": os.defpath, "PYTHONPATH": str(root / "package"), "FIX_CURSOR": repaired})
                        self.assertEqual(run.returncode, 0 if repaired == "1" else 1, run.stderr)
                        self.assertIn('Passed both:', run.stderr)
                        self.assertIn("New failures: 0", run.stderr)
                        expected = "Passed both" if repaired == "1" else "Existing failure"
                        self.assertIn(f'{expected}: "resume-tied-page" / "complete"', run.stderr)

    def test_independent_timeout_keeps_the_refund_routing_failure_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter = Path(directory) / "workflow.py"
            adapter.write_text("""
def evaluate(item, output, variant):
    def lookup_policy():
        if variant == "candidate":
            raise TimeoutError("private evaluator endpoint")
        return True
    return {"policy": lookup_policy, "route": lambda: output == "billing"}

workflow = {
    "cases": [{"id": "refund", "input": "refund"}],
    "checks": [{"id": "policy"}, {"id": "route"}],
    "baseline": lambda item: "billing",
    "candidate": lambda item: "general",
    "evaluate": evaluate,
}
""")
            run = subprocess.run(
                [sys.executable, str(SCRIPT), "--offline-workflow", str(adapter), "--compare-baseline"],
                capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(run.returncode, 1, run.stderr)
            self.assertIn('New failure: "refund" / "route"', run.stderr)
            self.assertIn('Unscored: "refund" / "policy"', run.stderr)
            self.assertNotIn("private evaluator endpoint", run.stderr)

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

    def test_upgrade_uses_edited_cases_and_the_installed_runner(self):
        target = "./test_tokensand_first.py"
        documented = [
            (ROOT / "README.md", f"python3 -m tokensand_check --offline-workflow {target}"),
            (SCRIPT.parents[1] / "SKILL.md", f'python3 "$SKILL_DIR/scripts/check_workflow.py" --offline-workflow {target}'),
        ]
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            edited = project / "test_tokensand_first.py"
            with ZipFile(ROOT / "packages/tokensand_workflow_check-0.1.8-py3-none-any.whl") as old:
                source = old.read("tokensand_check_starter.py").decode()
            marker = 'if __name__ == "__main__":'
            adapter = ("async def fetch(item): return item\n"
                       "workflow.update(candidate=fetch, evaluate=lambda *args: {'planned_calls': True})\n")
            self.assertEqual(source.count(marker), 1)
            edited.write_text(source.replace(marker, adapter + marker))
            original = edited.read_bytes()
            with ZipFile(ROOT / "packages/tokensand_workflow_check-0.1.16-py3-none-any.whl") as current:
                current.extractall(project / "wheel")
            for guide, command in documented:
                with self.subTest(guide=guide.name):
                    self.assertIn(command, guide.read_text())
                    args = [arg.replace("$SKILL_DIR", str(SCRIPT.parents[1])) for arg in shlex.split(command)[1:]]
                    run = subprocess.run([sys.executable, *args], cwd=project, capture_output=True, text=True,
                                         env={**os.environ, "PYTHONPATH": str(project / "wheel")})
                    self.assertEqual(run.returncode, 1, run.stderr)
                    self.assertEqual(run.stderr.count("TypeError: candidate returned an unfinished async result"), 3)
                    self.assertEqual(edited.read_bytes(), original)

    def test_documented_variant_inspection_in_wheel_and_agent_pack(self):
        import json
        prefix = "--offline-workflow ./my_workflow.py --case CASE_ID --inspect-values --variant baseline"
        self.assertIn("python3 -m tokensand_check " + prefix, (ROOT / "README.md").read_text())
        self.assertIn('python3 "$SKILL_DIR/scripts/check_workflow.py" ' + prefix,
                      (SCRIPT.parents[1] / "SKILL.md").read_text())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            adapter = root / "my_workflow.py"
            adapter.write_text("""
workflow = {
    "cases": [{"id": "counter", "input": {"expected": "private_repaired"}}],
    "checks": [{"id": "counter_correct"}],
    "baseline": lambda item: "private_broken",
    "candidate": lambda item: item["expected"],
    "evaluate": lambda item, output, variant: {"counter_correct": output == item["expected"]},
}
""")
            with ZipFile(ROOT / "packages/tokensand_workflow_check-0.1.16-py3-none-any.whl") as wheel:
                wheel.extractall(root / "installed")
            for entry in [[str(SCRIPT)], ["-m", "tokensand_check"]]:
                for arm, extra, code in [("candidate", [], 0), ("baseline", ["--variant", "baseline"], 1)]:
                    with self.subTest(entry=entry, arm=arm):
                        run = subprocess.run(
                            [sys.executable, *entry, "--offline-workflow", str(adapter), "--case", "counter", "--inspect-values", *extra],
                            cwd=root, env={**os.environ, "TMPDIR": str(root), "PYTHONPATH": str(root / "installed")},
                            text=True, capture_output=True, timeout=10,
                        )
                        self.assertEqual(run.returncode, code, run.stderr)
                        output = run.stdout + run.stderr
                        self.assertIn(f"Replay variant: {arm} (fresh local run)", output)
                        self.assertNotIn("private_broken", output)
                        self.assertNotIn("private_repaired", output)
                        file = Path(next(line.removeprefix("Private case values: ") for line in run.stdout.splitlines()
                                         if line.startswith("Private case values: ")))
                        self.assertEqual(file.parent.resolve(), root.resolve())
                        self.assertEqual(file.stat().st_mode & 0o077, 0)
                        values = [json.loads(line) for line in file.read_text().splitlines()]
                        self.assertEqual(len(values), 3)
                        for value in values:
                            self.assertEqual(set(value), {"trial", "expected", "actual", *(["variant"] if extra else [])})
                            self.assertEqual(value["actual"], repr("private_broken" if extra else "private_repaired"))
                            if extra:
                                self.assertEqual(value["variant"], "baseline")
                        file.unlink()

    def test_shipped_runner(self):
        with ZipFile(ROOT / "packages/tokensand_workflow_check-0.1.16-py3-none-any.whl") as wheel:
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

    def test_repeated_scores_group_by_case_and_check_without_hiding_runtime_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = Path(tmp) / "mixed.py"
            adapter.write_text("""
from collections import Counter
calls = Counter()
def target(case):
    calls[case] += 1
    if case == "mixed" and calls[case] == 3:
        raise ValueError("application error")
    return "PRIVATE_OUTPUT"
def unavailable():
    raise ValueError("PRIVATE_SCORER_REASON")
def evaluate(case, output, variant):
    if case == "mixed" and calls[case] == 2:
        return {"left": unavailable, "right": True}
    return {"left": False, "right": None}
workflow = dict(repetitions=3, cases=[{"id": c, "input": c} for c in ("mixed", "steady")],
                candidate=target, evaluate=evaluate,
                checks=[{"id": c, "label": "Span"} for c in ("left", "right")])
""")
            command = [sys.executable, "-I", "-B", SCRIPT, "--offline-workflow", adapter]
            grouped = subprocess.run(command, capture_output=True, text=True)
            verbose = subprocess.run([*command, "--verbose"], capture_output=True, text=True)
            for run in (grouped, verbose):
                self.assertEqual(run.returncode, 1)
                self.assertEqual(run.stdout, "")
                self.assertIn("ValueError: application error", run.stderr)
                self.assertNotIn("PRIVATE_", run.stderr)
                self.assertIn("Trials with failed or unscored checks: 5 of 6", run.stderr)
            expected = [
                "mixed — Span [left]: failed 1/3 (trials 1), unscored 1/3 (trials 2)",
                "mixed (trial 1/3) — Span [right]: unscored 1/3",
                "steady — Span [left]: failed 3/3",
                "steady — Span [right]: unscored 3/3",
            ]
            self.assertEqual([line for line in grouped.stderr.splitlines() if " — " in line], expected)
            self.assertEqual(grouped.stderr.count("Traceback "), 1)
            self.assertIn("mixed (trial 3/3)", grouped.stderr)
            self.assertEqual(verbose.stderr.count("Traceback "), 6)
            self.assertNotIn(" — ", verbose.stderr)

    def test_grouped_runs_identify_all_fully_passing_cases_and_respect_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = Path(tmp) / "passing.py"
            adapter.write_text("""
workflow = dict(repetitions=4, cases=[{"id": c, "input": c} for c in ("one", "two", "three")],
                candidate=lambda item: item, evaluate=lambda *args: {"shape": True},
                checks=[{"id": "shape"}])
""")
            command = [sys.executable, "-I", "-B", SCRIPT, "--offline-workflow", adapter]
            for selection in ([], ["two", "three"]):
                args = [arg for case in selection for arg in ("--case", case)]
                run = subprocess.run([*command, *args], capture_output=True, text=True)
                self.assertEqual(run.returncode, 0, run.stderr)
                if selection:
                    self.assertNotIn("one (trial", run.stderr)
                    for case in selection:
                        self.assertIn(f"{case} (trial 4/4)", run.stderr)
                else:
                    printed = [line for line in run.stderr.splitlines() if " — " in line]
                    self.assertEqual(printed, [f"{case} — passed 4/4 trials" for case in ["one", "two", "three"]])

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
