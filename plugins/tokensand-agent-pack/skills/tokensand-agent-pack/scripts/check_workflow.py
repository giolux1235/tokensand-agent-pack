"""Offline starter. Replace plan_calls with your own local function returning call IDs."""
import argparse
import copy
from collections.abc import AsyncIterable
import inspect
import json
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


def case_suite(definition, case_id=None, inspect_values=False, *, score_issues=None):
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
        with tempfile.NamedTemporaryFile(prefix="tokensand-case-", suffix=".jsonl", delete=False) as handle:
            values_path = Path(handle.name)
        preview = ValuePreview()
        preview.maxlevel = 3
        preview.maxstring = 256
        print("Private case values: " + str(values_path), flush=True)

    suite = unittest.TestSuite()
    for case_index, case in enumerate(items):
        for trial in range(1, trials + 1):
            trial_issues = []
            def check(current=case, repetition=trial, issues=trial_issues):
                issues.clear()
                output = resolved_result(definition["candidate"](copy.deepcopy(current["input"])), "candidate")
                if values_path is not None:
                    row = {"trial": repetition, "actual": preview.repr(output)[:2048]}
                    if isinstance(current["input"], dict) and "expected" in current["input"]:
                        row["expected"] = preview.repr(current["input"]["expected"])[:2048]
                    with values_path.open("a", encoding="utf-8") as handle:
                        handle.write(json.dumps(row) + "\n")
                scores = resolved_result(definition["evaluate"](copy.deepcopy(current["input"]), output, "candidate"), "evaluate")
                observed = []
                timed_out = False
                for check_index, rule in enumerate(definition["checks"]):
                    label = str(rule.get("label") or rule["id"])
                    score = scores.get(rule["id"])
                    value = None
                    if not timed_out:
                        try:
                            value = score() if callable(score) else score
                        except Exception as error:
                            timed_out = isinstance(error, (TimeoutError, subprocess.TimeoutExpired))
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


def load_tests(loader, tests, pattern):
    return case_suite(workflow)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run each offline case for its configured repetitions (default: once).")
    parser.add_argument("--offline-workflow", type=Path, action="append", help="Trusted local Python file exporting a workflow dictionary.")
    parser.add_argument("--case", action="append", help="Exact case ID to run at least three times. Repeat --case to select more cases.")
    parser.add_argument("--inspect-values", action="store_true", help="Save local expected and actual value previews for one case.")
    parser.add_argument("-v", "--verbose", action="store_true", help="Show the full unittest report for each trial.")
    args = parser.parse_args()
    if args.inspect_values and len(args.case or []) != 1:
        parser.error("--inspect-values requires one --case CASE_ID.")
    if args.offline_workflow:
        if len(args.offline_workflow) != 1:
            parser.error("Supply one --offline-workflow path.")
        path = args.offline_workflow[0].resolve()
        sys.path.insert(0, str(path.parent))
        workflow = runpy.run_path(str(path))["workflow"]
    try:
        score_issues = []
        suite = case_suite(workflow, args.case, args.inspect_values, score_issues=score_issues)
    except ValueError as error:
        parser.error(str(error))
    group_trials = not args.verbose and not args.case and workflow.get("repetitions", 1) > 1
    runner = unittest.TextTestRunner(verbosity=0, resultclass=GroupedTrialResult) if group_trials else unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    if any(unscored for _, unscored in score_issues):
        print(f"Trials with failed or unscored checks: {len(score_issues)} of {result.testsRun}", file=sys.stderr)
        for description, groups in zip(("failed", "unscored"), zip(*score_issues)):
            count = sum(bool(labels) for labels in groups)
            names = ", ".join(dict.fromkeys(label for labels in groups for label in labels))
            detail = f" ({names})" if names else ""
            print(f"Trials with {description} checks: {count}{detail}", file=sys.stderr)
    sys.exit(0 if result.wasSuccessful() else 1)
