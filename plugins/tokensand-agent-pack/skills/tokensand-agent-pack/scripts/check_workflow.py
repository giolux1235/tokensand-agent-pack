"""Offline starter. Replace plan_calls with your own local function returning call IDs."""
import argparse
import copy
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


def case_suite(definition, case_id=None, inspect_values=False):
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
    for case in items:
        for trial in range(1, trials + 1):
            def check(current=case, repetition=trial):
                output = definition["candidate"](copy.deepcopy(current["input"]))
                if values_path is not None:
                    row = {"trial": repetition, "actual": preview.repr(output)[:2048]}
                    if isinstance(current["input"], dict) and "expected" in current["input"]:
                        row["expected"] = preview.repr(current["input"]["expected"])[:2048]
                    with values_path.open("a", encoding="utf-8") as handle:
                        handle.write(json.dumps(row) + "\n")
                scores = definition["evaluate"](copy.deepcopy(current["input"]), output, "candidate")
                failed, unscored = [], []
                timed_out = False
                for rule in definition["checks"]:
                    label = str(rule.get("label") or rule["id"])
                    score = scores.get(rule["id"])
                    if timed_out:
                        unscored.append(label)
                        continue
                    try:
                        value = score() if callable(score) else score
                    except Exception as error:
                        timed_out = isinstance(error, (TimeoutError, subprocess.TimeoutExpired))
                        unscored.append(label)
                        continue
                    if value is False:
                        failed.append(label)
                    elif value is not True:
                        if inspect.iscoroutine(value):
                            value.close()
                        unscored.append(label)
                reasons = []
                if failed:
                    reasons.append("Failed checks: " + ", ".join(failed))
                if unscored:
                    reasons.append("Unscored checks: " + ", ".join(unscored))
                    raise RuntimeError("\n".join(reasons))
                if failed:
                    raise AssertionError("\n".join(reasons))

            check.__name__ = str(case["id"])
            if trials > 1:
                check.__name__ += f" (trial {trial}/{trials})"
            suite.addTest(unittest.FunctionTestCase(check))
    return suite


def load_tests(loader, tests, pattern):
    return case_suite(workflow)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run each offline case for its configured repetitions (default: once).")
    parser.add_argument("--offline-workflow", type=Path, action="append", help="Trusted local Python file exporting a workflow dictionary.")
    parser.add_argument("--case", action="append", help="Exact case ID to run at least three times. Repeat --case to select more cases.")
    parser.add_argument("--inspect-values", action="store_true", help="Save local expected and actual value previews for one case.")
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
        suite = case_suite(workflow, args.case, args.inspect_values)
    except ValueError as error:
        parser.error(str(error))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
