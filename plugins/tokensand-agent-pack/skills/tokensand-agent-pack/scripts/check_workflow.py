"""Offline starter. Replace plan_calls with your own local function returning call IDs."""
import argparse
import copy
from pathlib import Path
import runpy
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


def case_suite(definition, case_id=None):
    items = definition["cases"]
    if not items or not definition["checks"]:
        raise ValueError("Provide at least one case and one named check.")
    trials = 1
    if case_id is not None:
        items = [case for case in items if case["id"] == case_id]
        if not case_id or len(items) != 1:
            raise ValueError(f"Expected one case with exact ID {case_id!r}. Found {len(items)}.")
        trials = 3

    suite = unittest.TestSuite()
    for case in items:
        for trial in range(1, trials + 1):
            def check(current=case):
                output = definition["candidate"](copy.deepcopy(current["input"]))
                scores = definition["evaluate"](current["input"], output, "candidate")
                failed = [str(rule.get("label") or rule["id"]) for rule in definition["checks"]
                          if scores.get(rule["id"]) is not True]
                if failed:
                    raise AssertionError("Failed checks: " + ", ".join(failed))

            check.__name__ = str(case["id"])
            if case_id is not None:
                check.__name__ += f" (trial {trial}/{trials})"
            suite.addTest(unittest.FunctionTestCase(check))
    return suite


def load_tests(loader, tests, pattern):
    return case_suite(workflow)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Check every offline case once, or repeat one case three times.")
    parser.add_argument("--offline-workflow", type=Path, help="Trusted local Python file exporting a workflow dictionary.")
    parser.add_argument("--case", help="Exact ID of the case to repeat three times.")
    args = parser.parse_args()
    if args.offline_workflow:
        path = args.offline_workflow.resolve()
        sys.path.insert(0, str(path.parent))
        workflow = runpy.run_path(str(path))["workflow"]
    try:
        suite = case_suite(workflow, args.case)
    except ValueError as error:
        parser.error(str(error))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
