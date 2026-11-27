"""Offline starter. Replace plan_calls with your own local function returning call IDs."""
import argparse
import copy
from collections.abc import AsyncIterable
import hashlib
import inspect
import json
import os
import reprlib
import tempfile
from pathlib import Path
import runpy
import subprocess
import sys
import unittest


def plan_calls(calls):
    return [call["id"] for call in calls]  # Bug: a redelivered call gets scheduled twice.


first = {"id": "call-1", "tool": "reminder"}
second = {"id": "call-2", "tool": "reminder"}
cases = [
    {"id": "one_call", "input": {"calls": [first], "expected": ["call-1"]}},
    {"id": "retried_call", "input": {"calls": [first, first], "expected": ["call-1"]}},
    {"id": "distinct_calls_same_tool", "input": {"calls": [first, second], "expected": ["call-1", "call-2"]}},
]


def candidate(item):
    return plan_calls(item["calls"])


def baseline(item):
    return list(dict.fromkeys(call["id"] for call in item["calls"]))


def evaluate(item, output, variant):
    return {"planned_calls": output == item["expected"]}


# The SDK can run the same cases and callbacks with compare_local(**workflow).
workflow = dict(cases=cases, checks=[{"id": "planned_calls", "label": "Schedule each distinct call once"}],
                baseline=baseline, candidate=candidate, evaluate=evaluate)


class ValuePreview(reprlib.Repr):
    def repr_instance(self, value, level):
        if value is None or type(value) in (bool, float, complex):
            return repr(value)
        return f"<{type(value).__name__}>"


class GroupedTrialResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.passed_by_case = {}

    def addSuccess(self, test):
        super().addSuccess(test)
        key = (test.trial_position[0], test.case_id, test.trial_count)
        self.passed_by_case[key] = self.passed_by_case.get(key, 0) + 1

    def printErrorList(self, flavour, errors):
        # Scored issues appear in the case summary. Keep native tracebacks for
        # application and evaluator exceptions that ran outside a named check.
        super().printErrorList(flavour, [(test, detail) for test, detail in errors if not test.check_issues])

    def printErrors(self):
        super().printErrors()
        by_check = {}
        for test, _ in sorted(self.errors + self.failures, key=lambda pair: pair[0].trial_position):
            case_index, trial = test.trial_position
            for check_index, check_id, label, status in test.check_issues:
                key = (case_index, check_index, test.case_id, check_id, label, test.trial_count)
                by_check.setdefault(key, {}).setdefault(status, []).append(trial)
        for (_, _, case_id, check_id, label, total), statuses in sorted(by_check.items()):
            name = label if label == check_id else f"{label} [{check_id}]"
            one_trial = sum(map(len, statuses.values())) == 1
            counts = []
            for status, trials in statuses.items():
                detail = f"{status} {len(trials)}/{total}"
                if one_trial:
                    case_id = f"{case_id} (trial {trials[0]}/{total})"
                elif len(trials) < total:
                    detail += " (trials " + ", ".join(map(str, trials)) + ")"
                counts.append(detail)
            self.stream.writeln(f"{case_id} — {name}: {', '.join(counts)}")
        for (_, case_id, total), passed in self.passed_by_case.items():
            if passed == total:
                self.stream.writeln(f"{case_id} — passed {passed}/{total} trials")


def resolved_result(value, callback):
    if inspect.isawaitable(value) or isinstance(value, AsyncIterable):
        if inspect.iscoroutine(value) and inspect.getcoroutinestate(value) == inspect.CORO_CREATED:
            value.close()
        raise TypeError(
            f"{callback} returned an unfinished async result. "
            "Await the call or collect the stream inside your adapter before returning its result."
        )
    return value


def case_suite(definition, case_id=None, inspect_values=False, *, score_issues=None, variant=None, score_rows=None):
    explicit_variant = variant is not None
    variant = variant or "candidate"
    items = definition["cases"]
    if not items or not definition["checks"]:
        raise ValueError("Provide at least one case and one named check.")
    trials = definition.get("repetitions", 1)
    if type(trials) is not int or not 1 <= trials <= 10:
        raise ValueError("repetitions must be an integer from 1 to 10.")
    if case_id is not None:
        requested = [case_id] if isinstance(case_id, str) else case_id
        if not requested or len(set(requested)) != len(requested):
            raise ValueError("Choose each case ID once.")
        selected = []
        for name in requested:
            matches = [case for case in items if case["id"] == name]
            if not name or len(matches) != 1:
                raise ValueError(f"Expected one case with exact ID {name!r}. Found {len(matches)}.")
            selected.append(matches[0])
        items = selected
        trials = max(3, trials)

    values_path = None
    if inspect_values:
        if case_id is None or len(items) != 1:
            raise ValueError("--inspect-values requires one --case CASE_ID.")
        if not callable(definition.get(variant)):
            raise ValueError(f"The workflow must define a callable {variant}.")
        with tempfile.NamedTemporaryFile(prefix="tokensand-case-", suffix=".jsonl", delete=False) as handle:
            values_path = Path(handle.name)
        preview = ValuePreview()
        preview.maxlevel = 3
        preview.maxstring = 256
        print(f"Replay variant: {variant} (fresh local run)", flush=True)
        print("Private case values: " + str(values_path), flush=True)

    suite = unittest.TestSuite()
    for case_index, case in enumerate(items):
        if score_rows is not None:
            encoded = json.dumps(case["input"], sort_keys=True, separators=(",", ":"), allow_nan=False)
            if json.loads(encoded) != case["input"]:
                raise ValueError("Paired checks require JSON fixture inputs.")
            fingerprint = hashlib.sha256(encoded.encode()).hexdigest()
        for trial in range(1, trials + 1):
            trial_issues = []
            outcomes = None
            if score_rows is not None:
                outcomes = [None] * len(definition["checks"])
                score_rows.append([case["id"], trial, fingerprint, outcomes])

            def check(current=case, repetition=trial, issues=trial_issues, recorded=outcomes):
                issues.clear()
                output = resolved_result(definition[variant](copy.deepcopy(current["input"])), variant)
                if values_path is not None:
                    row = {"trial": repetition, "actual": preview.repr(output)[:2048]}
                    if explicit_variant:
                        row["variant"] = variant
                    if isinstance(current["input"], dict) and "expected" in current["input"]:
                        row["expected"] = preview.repr(current["input"]["expected"])[:2048]
                    with values_path.open("a", encoding="utf-8") as handle:
                        handle.write(json.dumps(row) + "\n")
                scores = resolved_result(definition["evaluate"](copy.deepcopy(current["input"]), output, variant), "evaluate")
                observed = []
                for check_index, rule in enumerate(definition["checks"]):
                    label = str(rule.get("label") or rule["id"])
                    score = scores.get(rule["id"])
                    try:
                        value = score() if callable(score) else score
                    except Exception:
                        # This synchronous scorer returned control. Other named checks are independent.
                        value = None
                    if recorded is not None and type(value) is bool:
                        recorded[check_index] = value
                    if value is True:
                        continue
                    if inspect.iscoroutine(value):
                        value.close()
                    check_status = "failed" if value is False else "unscored"
                    observed.append((check_index, str(rule["id"]), label, check_status))
                failed = [label for _, _, label, status in observed if status == "failed"]
                unscored = [label for _, _, label, status in observed if status == "unscored"]
                if score_issues is not None and (failed or unscored):
                    score_issues.append((failed, unscored))
                reasons = []
                if failed:
                    reasons.append("Failed checks: " + ", ".join(failed))
                if unscored:
                    reasons.append("Unscored checks: " + ", ".join(unscored))
                    issues.extend(observed)
                    raise RuntimeError("\n".join(reasons))
                if failed:
                    issues.extend(observed)
                    raise AssertionError("\n".join(reasons))

            check.__name__ = str(case["id"])
            if trials > 1:
                check.__name__ += f" (trial {trial}/{trials})"
            test = unittest.FunctionTestCase(check)
            test.case_id = str(case["id"])
            test.trial_position = (case_index, trial)
            test.trial_count = trials
            test.check_issues = trial_issues
            suite.addTest(test)
    return suite


def load_workflow(path):
    path = Path(path).resolve()
    sys.path.insert(0, str(path.parent))
    return runpy.run_path(str(path))["workflow"]


def variant_scores(variant, workflow_path, *selected):
    original = load_workflow(workflow_path)
    # Capture the declared test matrix before application callbacks can mutate it.
    definition = dict(original, cases=copy.deepcopy(list(original["cases"])),
                      checks=copy.deepcopy(list(original["checks"])))
    if not callable(definition.get("evaluate")):
        raise ValueError("The workflow must define a callable evaluator.")
    if not callable(definition.get(variant)):
        raise ValueError(f"The workflow must define a callable {variant}.")
    for field in ("cases", "checks"):
        ids = [entry["id"] for entry in definition[field]]
        if not all(isinstance(key, str) and key for key in ids) or len(ids) != len(set(ids)):
            raise ValueError(f"Each entry in {field} needs a unique string id.")
    rows = []
    suite = case_suite(definition, selected or None, variant=variant, score_rows=rows)
    result = suite.run(unittest.TestResult())
    if not rows or result.testsRun != len(rows):
        raise ValueError("No complete test matrix was executed.")
    return {"checks": [c["id"] for c in definition["checks"]], "rows": rows}


def send_variant_scores(variant, workflow_path, *selected):
    # Keep the control pipe private before importing the trusted app. Direct fd
    # writes and child processes see the same null stdout/stderr as in 0.1.12.
    with os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8") as receipt:
        os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
        scores = variant_scores(variant, workflow_path, *selected)
        json.dump(scores, receipt, separators=(",", ":"), allow_nan=False)
    return 0


def valid_variant_scores(record):
    if not isinstance(record, dict) or set(record) != {"checks", "rows"}:
        return False
    checks, rows = record["checks"], record["rows"]
    if (not isinstance(checks, list) or not checks or not all(isinstance(c, str) and c for c in checks)
            or len(checks) != len(set(checks)) or not isinstance(rows, list) or not rows):
        return False
    seen = set()
    for row in rows:
        if not isinstance(row, list) or len(row) != 4:
            return False
        case, trial, fingerprint, values = row
        if (not isinstance(case, str) or not case or type(trial) is not int or not 1 <= trial <= 10
                or (case, trial) in seen or not isinstance(fingerprint, str) or len(fingerprint) != 64
                or any(char not in "0123456789abcdef" for char in fingerprint)
                or not isinstance(values, list) or len(values) != len(checks)
                or not all(value is None or type(value) is bool for value in values)):
            return False
        seen.add((case, trial))
    return True


def read_variant_scores(command, timeout=None):
    # communicate() drains the pipe while the worker runs, including matrices
    # larger than the OS pipe buffer. The CLI keeps the caller's time budget.
    result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, timeout=timeout)
    if result.returncode:
        raise ValueError("Check the adapter defines both workflows, an evaluator, unique case/check IDs "
                         "and JSON inputs. Check any --case ID or run the ordinary check for setup details.")
    try:
        record = json.loads(result.stdout)
    except (ValueError, RecursionError):
        record = None
    if not valid_variant_scores(record):
        raise ValueError("The worker returned an invalid score receipt. Run the ordinary check for details.")
    return record


def compare_baseline(workflow_path, selected):
    # Separate interpreters preserve within-variant state across repetitions without
    # letting one variant warm the other's cache or advance its application state.
    initial_hash = hashlib.sha256(workflow_path.read_bytes()).digest()
    records = []
    for variant in ("baseline", "candidate"):
        command = [sys.executable, "-B", __file__, "--_variant-scores", variant, str(workflow_path), *selected]
        try:
            records.append(read_variant_scores(command))
        except OSError:
            print(f"Could not launch or read the {variant} worker. Check local Python subprocess access.", file=sys.stderr)
            return 2
        except ValueError as error:
            print(f"Could not score the {variant}. {error}", file=sys.stderr)
            return 2
        if not workflow_path.is_file() or hashlib.sha256(workflow_path.read_bytes()).digest() != initial_hash:
            print("The workflow adapter changed during the check. Save it and rerun.", file=sys.stderr)
            return 2

    before, after = records
    if (not before["checks"] or not before["rows"] or before["checks"] != after["checks"]
            or [r[:3] for r in before["rows"]] != [r[:3] for r in after["rows"]]):
        print("Cases, inputs, repetitions or checks changed between variants. Freeze the fixtures and rerun.", file=sys.stderr)
        return 2
    labels = ("New failure", "Unscored", "Existing failure", "Recovered", "Passed both")
    transitions = {(True, False): labels[0], (False, False): labels[2],
                   (False, True): labels[3], (True, True): labels[4]}
    grouped = {label: {} for label in labels}
    partial_pairs = {}
    for old, new in zip(before["rows"], after["rows"]):
        case_id, trial, _, values = new
        for check_id, earlier, current in zip(after["checks"], old[3], values):
            label = transitions.get((earlier, current), "Unscored")
            key = (case_id, check_id)
            grouped[label].setdefault(key, []).append(trial)
            if label == "Unscored":
                partial_pairs.setdefault(key, {}).setdefault((earlier, current), []).append(trial)
    print("Baseline and candidate on the same cases and checks", file=sys.stderr)
    score_text = {True: "passed", False: "failed", None: "unscored"}
    for label, pairs in grouped.items():
        if label == "New failure" and not pairs:
            print("New failures: undetermined (unscored checks)" if grouped["Unscored"] else "New failures: 0", file=sys.stderr)
        for (case_id, check_id), trials in pairs.items():
            print(f"{label}: {json.dumps(case_id)} / {json.dumps(check_id)} · trials "
                  + ", ".join(map(str, trials)), file=sys.stderr)
            if label == "Unscored":
                for (earlier, current), partial_trials in partial_pairs[(case_id, check_id)].items():
                    print(f"  Baseline {score_text[earlier]}, candidate {score_text[current]} · trials "
                          + ", ".join(map(str, partial_trials)), file=sys.stderr)
    return int(bool(grouped["New failure"] or grouped["Existing failure"] or grouped["Unscored"]))


def load_tests(loader, tests, pattern):
    return case_suite(workflow)


def main(argv):
    if argv[:1] == ["--_variant-scores"]:
        return send_variant_scores(*argv[1:])
    parser = argparse.ArgumentParser(description="Run each offline case for its configured repetitions (default: once).")
    parser.add_argument("--offline-workflow", type=Path, action="append", help="Trusted local Python file exporting a workflow dictionary.")
    parser.add_argument("--case", action="append", help="Exact case ID to run at least three times. Repeat --case to select more cases.")
    parser.add_argument("--inspect-values", action="store_true", help="Save local expected and actual value previews for one case.")
    parser.add_argument("--variant", choices=("baseline", "candidate"), action="append",
                        help="Choose the arm for a fresh --inspect-values replay of one case (default: candidate).")
    parser.add_argument("-v", "--verbose", action="store_true", help="Show the full unittest report for each trial.")
    parser.add_argument("--compare-baseline", action="store_true",
                        help="Score baseline and candidate separately on matched offline fixtures and print changed checks.")
    args = parser.parse_args(argv)
    if args.variant:
        if args.compare_baseline:
            parser.error("--compare-baseline scores both arms. For private values, use --case CASE_ID --inspect-values --variant baseline or candidate separately.")
        if len(args.variant) != 1:
            parser.error("Choose one --variant baseline or candidate.")
        if not args.inspect_values:
            parser.error("--variant selects one arm for --inspect-values with one --case CASE_ID.")
    if args.compare_baseline:
        if len(args.offline_workflow or []) != 1:
            parser.error("--compare-baseline requires --offline-workflow PATH to your project adapter.")
        if args.inspect_values or args.verbose:
            parser.error("--compare-baseline reports changed checks. To inspect one arm, omit --compare-baseline and use --case CASE_ID --inspect-values --variant baseline or candidate. Use --verbose separately for detailed candidate output.")
    if args.inspect_values and len(args.case or []) != 1:
        parser.error("--inspect-values requires one --case CASE_ID.")
    if args.offline_workflow:
        if len(args.offline_workflow) != 1:
            parser.error("Supply one --offline-workflow path.")
        path = args.offline_workflow[0]
        if not path.is_file():
            print("No cases ran. Supply a Python workflow file from your project directory.", file=sys.stderr)
            for name in ("workflow.py", "my_workflow.py"):
                if Path(name).is_file():
                    print(f"Found ./{name}. If this is your adapter, rerun with --offline-workflow ./{name}.", file=sys.stderr)
                    break
            else:
                print("Pass its path with --offline-workflow, or create an adapter from this example.\n"
                      "https://github.com/giolux1235/tokensand-agent-pack/blob/python-check-0.1.14/examples/incremental-sync.py", file=sys.stderr)
            return 2
        if args.compare_baseline:
            return compare_baseline(path, args.case or [])
        definition = load_workflow(path)
    else:
        definition = workflow
    try:
        score_issues = []
        suite = case_suite(definition, args.case, args.inspect_values, score_issues=score_issues,
                           variant=args.variant[0] if args.variant else None)
    except ValueError as error:
        parser.error(str(error))
    group_trials = not args.verbose and not args.case and definition.get("repetitions", 1) > 1
    runner = unittest.TextTestRunner(verbosity=0, resultclass=GroupedTrialResult) if group_trials else unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    if any(unscored for _, unscored in score_issues):
        print(f"Trials with failed or unscored checks: {len(score_issues)} of {result.testsRun}", file=sys.stderr)
        for description, groups in zip(("failed", "unscored"), zip(*score_issues)):
            count = sum(bool(labels) for labels in groups)
            names = ", ".join(dict.fromkeys(label for labels in groups for label in labels))
            detail = f" ({names})" if names else ""
            print(f"Trials with {description} checks: {count}{detail}", file=sys.stderr)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
