#!/usr/bin/env python3
"""Run this checkout's pytest once and record its private native outcomes for review."""
from collections import Counter, defaultdict
from importlib.machinery import all_suffixes
import hashlib
import json
import os
from pathlib import Path
import sys
from types import ModuleType


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


IMPORT_SUFFIXES = tuple(all_suffixes())


class ProjectImports:
    """Verify observed imports that correspond to Python modules in either checkout."""

    def __init__(self):
        self.root = Path.cwd().resolve()
        peer = os.environ.get("TOKENSAND_PYTEST_PEER_ROOT")
        self.peer = Path(peer).resolve() if peer else None
        roots = [self.root] + ([self.peer] if self.peer else [])
        # An explicitly shared interpreter may supply third-party dependencies from
        # its installed libraries inside the other checkout.
        self.libraries = [Path(entry).resolve() for entry in sys.path
                          if isinstance(entry, str) and Path(entry).name in {"site-packages", "dist-packages"}]
        dirs = {".", "src", *json.loads(os.environ.get("TOKENSAND_PYTEST_SOURCE_DIRS", "[]"))}
        self.bases = [(root, root / name) for root in roots for name in dirs if (root / name).is_dir()]
        self.top_names = {entry.name if entry.is_dir() else entry.name.split(".")[0]
                          for _, base in self.bases for entry in base.iterdir()}
        self.checked = set()

    def matches_checkout(self, name, filename):
        origin = Path(filename).resolve() if filename and Path(filename).is_absolute() else None
        if self.peer and origin and origin.is_relative_to(self.peer):
            under_own_nested_root = self.root.is_relative_to(self.peer) and origin.is_relative_to(self.root)
            if not under_own_nested_root and not any(origin.is_relative_to(path) for path in self.libraries):
                return False
        if name.partition(".")[0] not in self.top_names:
            return True
        relative = Path(*name.split("."))
        project_module = False
        for root, base in self.bases:
            for stem in (base / relative, base / relative / "__init__"):
                for suffix in IMPORT_SUFFIXES:
                    candidate = Path(str(stem) + suffix)
                    if candidate.is_file():
                        project_module = True
                        if root == self.root and origin and origin.is_relative_to(root) and candidate.resolve() == origin:
                            return True
        # Namespace extensions and ordinary third-party modules are independent dependencies.
        return not project_module

    def conflicts(self):
        for name, module in sys.modules.copy().items():
            if module is None or not isinstance(name, str) or name == "__main__":
                continue
            # Reading module globals directly avoids executing custom __getattr__ hooks.
            attrs = ModuleType.__getattribute__(module, "__dict__") if isinstance(module, ModuleType) else {}
            spec = attrs.get("__spec__")
            filename = getattr(spec, "origin", None) or attrs.get("__file__")
            key = (name, filename if isinstance(filename, str) else None)
            if key not in self.checked:
                self.checked.add(key)
                try:
                    if not self.matches_checkout(*key):
                        return True
                except (OSError, RuntimeError):
                    return True
        return False


class NativeReports:
    def __init__(self):
        self.project_imports = ProjectImports()
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
        # Rewritten pytest modules retain asserts, but their ordinary imported helpers may not.
        if sys.flags.optimize:
            self.issues.add("disabled_assertions")
        if config.getoption("runxfail", default=False):
            self.issues.add("runxfail")
        if not config.pluginmanager.hasplugin("skipping"):
            self.issues.add("unsupported")
        if (config.getoption("numprocesses", default=None) or
                config.getoption("dist", default="no") != "no" or hasattr(config, "workerinput")):
            self.issues.add("distributed")

    def check_imports(self):
        if "foreign_import" not in self.issues and self.project_imports.conflicts():
            self.issues.add("foreign_import")

    def pytest_collection_finish(self, session):
        self.check_imports()
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
        if report.when == "call":
            # Fixture teardown can unload modules that were used by the test body.
            self.check_imports()
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
        self.check_imports()
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
