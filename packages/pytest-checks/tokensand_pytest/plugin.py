"""Submit the selected file's aggregate only after a complete, scored native pytest run."""
import os
from pathlib import Path
import shlex
import shutil
import sqlite3
import sys
import uuid

import pytest

from . import connection
from .capture import NativeReports


def full_file_command(target, checkout):
    # pytest receives a path relative to its original invocation, including from a subdirectory.
    arg = "./" + Path(os.path.relpath(target, checkout)).as_posix()
    executable = sys.executable
    if not executable:
        return None
    name = Path(executable).name
    if shutil.which(name) == executable:
        executable = name
    # pytest reserves :: and [ for node IDs and parameter selections even in quoted paths.
    if not all(value.isprintable() for value in (executable, arg)) or "::" in arg or "[" in arg:
        return None
    if sys.platform == "win32":
        # PowerShell's single quotes take literal input; smart quotes are also string delimiters.
        if any(char in value for value in (executable, arg) for char in "‘’‚‛"):
            return None
        def literal(value):
            return "'" + value.replace("'", "''") + "'"
        return "PowerShell", f"& {literal(executable)} -m pytest -- {literal(arg)}"
    return "shell", shlex.join([executable, "-m", "pytest", "--", arg])


def pytest_configure(config):
    # The local reviewer supplies its own collector through pytest.main(plugins=[...]).
    # Never deliver a private rehearsal, or one receipt per distributed worker.
    if (getattr(pytest, "version_tuple", (0,))[0] < 7 or sys.flags.optimize
            or config.invocation_params.plugins or config.option.collectonly
            or config.getoption("numprocesses", default=None)
            or config.getoption("dist", default="no") != "no" or hasattr(config, "workerinput")
            or config.getoption("runxfail", default=False) or not config.pluginmanager.hasplugin("skipping")):
        return
    checkout = config.invocation_params.dir
    try:
        selection = connection.read(checkout)
        if selection is not None:
            root = connection.root(checkout)
            target = root / selection[1]
            if target.is_file() and target.resolve() == target:
                config.pluginmanager.register(ConnectedFile(checkout, selection, target), "tokensand-selected-file")
    except (OSError, RuntimeError, ValueError, sqlite3.Error):
        # A missing Git client or inaccessible checkout cannot change the developer's test result.
        return


class ConnectedFile:
    def __init__(self, checkout, selection, target):
        self.checkout = checkout
        self.selection = selection
        self.target = target
        self.reports = NativeReports()
        self.explicit_subset = False
        self.collected = set()

    def pytest_itemcollected(self, item):
        if item.path.resolve() == self.target:
            self.collected.add(item.nodeid)

    def pytest_deselected(self, items):
        if any(item.path.resolve() == self.target for item in items):
            self.explicit_subset = True

    def pytest_collection_finish(self, session):
        self.reports.selected.update(item.nodeid for item in session.items if item.path.resolve() == self.target)
        target_args = 0
        for arg in session.config.args:
            file, node, _ = str(arg).partition("::")
            is_target = (self.checkout / file).resolve() == self.target
            target_args += int(is_target)
            if node and (is_target or session.config.option.pyargs):
                self.explicit_subset = True
        if target_args > 1:
            self.explicit_subset = True

    def pytest_keyboard_interrupt(self, excinfo):
        self.reports.issues.add("interrupted")

    def pytest_internalerror(self, excrepr, excinfo):
        self.reports.issues.add("session_error")

    def pytest_collectreport(self, report):
        self.reports.pytest_collectreport(report)

    def pytest_runtest_logstart(self, nodeid):
        if nodeid in self.reports.selected:
            self.reports.pytest_runtest_logstart(nodeid)

    def pytest_runtest_logreport(self, report):
        if report.nodeid in self.reports.selected:
            self.reports.pytest_runtest_logreport(report)

    def pytest_runtest_logfinish(self, nodeid):
        if nodeid in self.reports.selected:
            self.reports.pytest_runtest_logfinish(nodeid)

    @pytest.hookimpl(hookwrapper=True, tryfirst=True)
    def pytest_sessionfinish(self, session, exitstatus):
        outcome = yield
        if outcome.excinfo or session.exitstatus not in (0, 1) or not self.reports.selected:
            return
        terminal = session.config.pluginmanager.getplugin("terminalreporter")
        if (self.explicit_subset or self.collected != set(self.reports.selected)
                or session.shouldfail or session.shouldstop
                or bool(session.testsfailed) != bool(session.exitstatus)):
            if terminal:
                terminal.write_line("Tokens&: partial run; no private counts sent.")
                if self.explicit_subset or self.collected != set(self.reports.selected):
                    hint = full_file_command(self.target, self.checkout)
                    if hint:
                        shell, command = hint
                        terminal.write_line(f"For private counts, run the enrolled file from this directory ({shell}):")
                        terminal.write_line("  " + command)
                        terminal.write_line("If it stays partial, enroll an offline-only file and keep the project’s test settings.")
                    else:
                        terminal.write_line("No portable command for this path. Use an existing full-file test command, "
                                            "or enroll an offline-only file with a plain path. Keep the project’s test settings.")
            return
        # The global pytest exit may include unrelated tests. Inspect only the selected file.
        selected_failed = any(stages.get("call") == "failed" for stages in self.reports.stages.values())
        result = self.reports.summary(int(selected_failed))
        cases = result["cases"]
        if result["issues"] or any(status not in ("passed", "failed") for _, status in cases):
            if terminal:
                terminal.write_line("Tokens&: incomplete or unscored workflow; no private counts sent.")
            return
        scores = {name: status == "passed" for name, status in cases}
        run_id = str(uuid.uuid4())
        status = connection.report(self.checkout, self.selection, scores, run_id)
        if terminal:
            terminal.write_line(f"Tokens& private check {run_id}: {status} ({sum(scores.values())} passed, "
                                f"{len(scores) - sum(scores.values())} failed).")
