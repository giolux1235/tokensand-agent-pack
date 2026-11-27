#!/usr/bin/env python3
"""Run this checkout's pytest once and record its private native outcomes for review."""
from collections import Counter, defaultdict
import hashlib
import json
import os
from pathlib import Path
import sys


def stage_outcome(report):
    if report.when not in ("setup", "call", "teardown"):
        return None
    if report.outcome == "skipped":
        if not hasattr(report, "wasxfail"):
            return "skipped"
        return "xfailed-before-call" if report.when == "setup" else "xfailed"
    if report.outcome == "passed":
        return "xpassed" if hasattr(report, "wasxfail") else "passed"
    if report.outcome == "failed":
        if report.when != "call":
            return "error"
        # pytest uses a string here for an assertion that passed under strict xfail.
        # An actual failing assertion, even with similar text, carries a traceback.
        if isinstance(report.longrepr, str) and report.longrepr.startswith("[XPASS(strict)]"):
            return "xpassed-strict"
        return "failed"
    return None


class NativeReports:
    def __init__(self):
        self.selected = Counter()
        self.started = Counter()
        self.finished = Counter()
        self.stages = defaultdict(dict)
        self.issues = set()
        self.active_config = None
        self.active_config_sha256 = None

    def pytest_configure(self, config):
        if config.inipath is not None:
            root = config.invocation_params.dir.resolve()
            try:
                path = config.inipath.resolve(strict=True)
                if path.is_relative_to(root) and path.is_file():
                    digest = hashlib.sha256()
                    with path.open("rb") as source:
                        for chunk in iter(lambda: source.read(64 * 1024), b""):
                            digest.update(chunk)
                    self.active_config = path.relative_to(root).as_posix()
                    self.active_config_sha256 = digest.hexdigest()
                elif not path.is_relative_to(root):
                    self.issues.add("external_config")
                else:
                    self.issues.add("incomplete")
            except (OSError, RuntimeError):
                self.issues.add("incomplete")
        if config.getoption("runxfail", default=False):
            self.issues.add("runxfail")
        if not config.pluginmanager.hasplugin("skipping"):
            self.issues.add("unsupported")
        if (config.getoption("numprocesses", default=None) or
                config.getoption("dist", default="no") != "no" or hasattr(config, "workerinput")):
            self.issues.add("distributed")

    def pytest_collection_finish(self, session):
        self.selected.update(item.nodeid for item in session.items)

    def pytest_deselected(self, items):
        if items:
            self.issues.add("deselected")

    def pytest_collectreport(self, report):
        if report.failed:
            self.issues.add("collection_error")

    def pytest_runtest_logstart(self, nodeid):
        self.started[nodeid] += 1

    def pytest_runtest_logreport(self, report):
        if self.started[report.nodeid] != 1 or self.finished[report.nodeid]:
            self.issues.add("incomplete")
        if hasattr(report, "node") or hasattr(report, "worker_id"):
            self.issues.add("distributed")
        stages = self.stages[report.nodeid]
        if report.when in stages:
            self.issues.add("duplicate")
        state = stage_outcome(report)
        if state is None:
            self.issues.add("unsupported")
        stages[report.when] = state

    def pytest_runtest_logfinish(self, nodeid):
        if "teardown" not in self.stages.get(nodeid, {}):
            self.issues.add("incomplete")
        self.finished[nodeid] += 1

    def summary(self, exit_code):
        cases = []
        selected = set(self.selected)
        if not selected:
            self.issues.add("empty")
        if any(selected != set(items) for items in (self.started, self.stages, self.finished)):
            self.issues.add("incomplete")
        if any(count > 1 for items in (self.selected, self.started, self.finished) for count in items.values()):
            self.issues.add("duplicate")
        for nodeid in sorted(selected):
            stages = self.stages.get(nodeid, {})
            expected = ["setup", "call", "teardown"] if stages.get("setup") == "passed" else ["setup", "teardown"]
            if list(stages) != expected or any(value is None for value in stages.values()):
                self.issues.add("incomplete")
                continue
            outcomes = [value for value in stages.values() if value != "passed"]
            if len(outcomes) > 1:
                self.issues.add("multiple_outcomes")
            cases.append([nodeid, "error" if "error" in outcomes else next(iter(outcomes), "passed")])
        if exit_code == 2 and "collection_error" not in self.issues:
            self.issues.add("interrupted")
        elif exit_code not in (0, 1, 2, 5) or (exit_code == 5 and selected):
            self.issues.add("session_error")
        observed_failure = any(state in ("failed", "error", "xpassed-strict") for _, state in cases)
        if exit_code in (0, 1) and not self.issues and observed_failure != (exit_code == 1):
            self.issues.add("session_error")
        return {"version": 1, "exit": exit_code, "cases": cases, "issues": sorted(self.issues),
                "active_config": self.active_config, "active_config_sha256": self.active_config_sha256}


def main(args):
    if len(args) < 2 or not any(not value.startswith("-") for value in args[1:]):
        print("Usage: capture_pytest.py NEW_PRIVATE_RESULT_JSON FILE_OR_NODEID [...]", file=sys.stderr)
        return 4
    # Match python -m pytest, which puts the checkout before site packages.
    sys.path.insert(0, os.getcwd())
    try:
        import pytest
    except ImportError:
        print("Use the checkout's Python interpreter with pytest installed.", file=sys.stderr)
        return 4
    try:
        # Never reuse stale evidence. An executing or interrupted run has no readable JSON.
        fd = os.open(Path(args[0]), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            reports = NativeReports()
            exit_code = int(pytest.main(args[1:], plugins=[reports]))
            json.dump(reports.summary(exit_code), output, ensure_ascii=True, separators=(",", ":"))
            output.write("\n")
        return exit_code
    except OSError:
        print("Could not save the result. Choose a new file in a private directory.", file=sys.stderr)
        return 4


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
