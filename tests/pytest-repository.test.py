"""Exercise an installed wheel in disposable consumer checkouts against a private app stand-in."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
from threading import Thread
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
WHEEL = ROOT / "packages/tokensand_pytest_checks-0.1.2-py3-none-any.whl"
CAPTURE = ROOT / "plugins/tokensand-agent-pack/skills/tokensand-agent-pack/scripts/capture_pytest.py"
PROJECT, EPOCH = str(uuid.uuid4()), str(uuid.uuid4())
ENDPOINT = "/api/observability/v1/repository-checks"
BROKEN = "def test_first(): assert True\ndef test_repair(): assert False\n"
FIXED = BROKEN.replace("assert False", "assert True")


class Receiver(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, status, body):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def do_GET(self):
        assert self.headers["Authorization"] == "Bearer fixture-private"
        if self.path != ENDPOINT + "/connection/" + PROJECT:
            return self.reply(404, {})
        self.server.handshakes += 1
        self.reply(200, {"projectId": PROJECT, "connectionId": EPOCH})

    def do_POST(self):
        assert self.path == ENDPOINT
        assert self.headers["Authorization"] == "Bearer fixture-private"
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert set(payload) == {"schemaVersion", "projectId", "connectionId", "runId", "completedAtMs", "passed", "failed"}
        assert (payload["schemaVersion"], payload["projectId"], payload["connectionId"]) == (1, PROJECT, EPOCH)
        assert str(uuid.UUID(payload["runId"])) == payload["runId"]
        self.server.attempts.append(payload)
        saved = self.server.receipts.setdefault(payload["runId"], payload)
        assert saved == payload, "A retry must keep the original time, ID and scores"
        if self.server.bad_http_once:
            self.server.bad_http_once = False
            self.connection.sendall(b"invalid status line\r\n")
            self.connection.shutdown(socket.SHUT_RDWR)
            return
        if self.server.drop_once:
            self.server.drop_once = False
            self.connection.shutdown(socket.SHUT_RDWR)
            return
        self.reply(200, {"stored": True, "projectId": PROJECT, "runId": payload["runId"]})


class InstalledConsumer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.space = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.space.cleanup)
        venv = Path(cls.space.name) / "consumer-env"
        subprocess.run([sys.executable, "-m", "venv", "--system-site-packages", str(venv)], check=True)
        cls.python = str(venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))
        subprocess.run([cls.python, "-m", "pip", "--disable-pip-version-check", "install",
                        "--no-index", "--no-deps", "--force-reinstall", str(WHEEL)], check=True, capture_output=True)
        subprocess.run([cls.python, "-c", "import pytest"], check=True)

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.repo = Path(temp.name) / "consumer"
        self.tests = self.repo / "src" / "tests"
        self.tests.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        self.target = self.tests / "test_workflow.py"
        self.target.write_text(BROKEN)
        self.db = self.repo / ".git/tokensand-private/repository-checks.db"
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
        self.server.handshakes = 0
        self.server.attempts = []
        self.server.receipts = {}
        self.server.drop_once = False
        self.server.bad_http_once = False
        thread = Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.env = {key: os.environ[key] for key in ("PATH", "HOME", "SYSTEMROOT", "TMPDIR") if key in os.environ}
        self.env.update({"TOKENS_OBSERVE_INGEST_URL": f"http://127.0.0.1:{self.server.server_port}/api/observability/v1/events",
                         "TOKENS_OBSERVE_API_KEY": "fixture-private", "PYTHONDONTWRITEBYTECODE": "1"})

    def call(self, *args, code=0, cwd=None, env=None):
        p = subprocess.run([self.python, *map(str, args)], cwd=cwd or self.repo, env=env or self.env,
                           text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
        self.assertEqual(p.returncode, code, p.stdout)
        self.assertNotIn("fixture-private", p.stdout)
        return p.stdout

    def connect(self, *args):
        return self.call("-m", "tokensand_pytest", "connect", PROJECT, self.target.name, *args, cwd=self.tests)

    def run_tests(self, *args, **kwargs):
        return self.call("-m", "pytest", "-q", *args, **kwargs)

    def test_plain_pytest_scopes_failed_and_repaired_results_and_disconnects(self):
        self.run_tests(code=1)
        self.assertFalse(self.db.exists())
        self.assertEqual(self.server.handshakes, 0)
        self.connect()
        self.assertEqual(self.server.handshakes, 1)
        (self.repo / "test_other.py").write_text("def test_unrelated(): assert False\n")
        self.run_tests(code=1)
        self.target.write_text(FIXED)
        self.run_tests(cwd=self.tests)
        self.run_tests(code=1)
        scores = [(r["passed"], r["failed"]) for r in self.server.receipts.values()]
        self.assertEqual(scores, [(1, 1), (2, 0), (2, 0)])
        self.assertEqual(self.server.handshakes, 1)
        self.call("-m", "tokensand_pytest", "disconnect")
        self.run_tests(cwd=self.tests)
        self.assertEqual(len(self.server.attempts), 3)

    def test_local_review_and_selective_pytest_stay_local_in_connected_checkout(self):
        self.connect("--queue-offline")
        self.call(CAPTURE, self.repo / "private.json", self.target, code=1)
        self.assertEqual(json.loads((self.repo / "private.json").read_text())["issues"], [])
        self.assertEqual(self.server.attempts, [])
        (self.repo / "src/__init__.py").touch()
        (self.tests / "__init__.py").touch()
        for args, exitcode in [(("--collect-only",), 0), ((self.target, "-k", "first"), 0),
                               ((str(self.target) + "::test_first",), 0),
                               (("--pyargs", "src.tests.test_workflow::test_first"), 0),
                               (("--runxfail",), 1), (("-p", "no:tokensand_repository_checks"), 1),
                               ((self.target, self.target), 1)]:
            with self.subTest(args=args):
                self.run_tests(*args, code=exitcode)
                self.assertEqual(self.server.attempts, [])
        self.run_tests("--assert=plain", env={**self.env, "PYTHONOPTIMIZE": "1"})
        self.assertEqual(self.server.attempts, [])
        self.target.write_text("def test_early(): assert False\ndef test_later(): assert True\n")
        self.run_tests("-x", code=1)
        self.assertEqual(self.server.attempts, [])
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM pending").fetchone()[0], 0)

    def test_partial_run_offers_executable_full_file_command_for_each_selector(self):
        self.tests = self.repo / "src" / "-team's checks"
        self.tests.mkdir()
        self.target = self.tests / "test_release.py"
        self.target.write_text("import pytest\n@pytest.mark.parametrize('case', [1, 2], ids=['draft','repair'])\n"
                               "def test_release(case): assert case == 1\n")
        (self.tests.parent / "conftest.py").write_text(
            "def pytest_addoption(parser): parser.addoption('--case')\n"
            "def pytest_collection_modifyitems(config, items):\n"
            " case = config.getoption('--case')\n"
            " if case:\n"
            "  removed = [i for i in items if case not in i.nodeid]\n"
            "  config.hook.pytest_deselected(items=removed)\n"
            "  items[:] = [i for i in items if i not in removed]\n")
        for queued, selector in [(False, ["--case", "draft"]), (True, ["-k", "draft"]),
                                 (True, ["::test_release[draft]"])]:
            with self.subTest(queued=queued, selector=selector):
                self.connect(*(["--queue-offline"] if queued else []))
                cwd = self.tests.parent
                relative = "./" + self.target.relative_to(cwd).as_posix()
                args = [relative + selector[0]] if selector[0].startswith("::") else [relative, *selector]
                before = len(self.server.receipts)
                output = self.run_tests(*args, cwd=cwd)
                self.assertIn("partial run; no private counts sent", output)
                self.assertEqual(len(self.server.receipts), before)
                self.assertIn("keep the project’s test settings", output)
                command = next(line.strip() for line in output.splitlines() if line.startswith("  ") and " -m pytest" in line)
                self.assertNotIn("draft", command)
                shell = ["powershell", "-NoProfile", "-Command"] if os.name == "nt" else ["/bin/sh", "-c"]
                ran = subprocess.run([*shell, command], cwd=cwd, env=self.env, capture_output=True, text=True, timeout=30)
                self.assertEqual(ran.returncode, 1, ran.stdout + ran.stderr)
                self.assertIn("delivered (1 passed, 1 failed)", ran.stdout)
                self.assertEqual(len(self.server.receipts), before + 1)
                self.call("-m", "tokensand_pytest", "disconnect")

    def test_discovered_bracket_directory_offers_no_ambiguous_pytest_command(self):
        self.tests = self.repo / "src" / "commerce [US]"
        self.tests.mkdir()
        self.target = self.tests / "test_cart_price.py"
        self.target.write_text(BROKEN)
        self.connect()
        output = self.run_tests("-k", "first", cwd=self.tests.parent)
        self.assertIn("No portable command for this path", output)
        self.assertNotIn(" -m pytest -- ", output)
        self.assertEqual(len(self.server.attempts), 0)

    def test_full_file_hint_respects_persistent_offline_only_settings(self):
        (self.repo / "pytest.ini").write_text("[pytest]\naddopts = -m 'not live'\nmarkers = live: opt-in only\n")
        self.target.write_text("import pytest\ndef test_offline(): pass\n@pytest.mark.live\n"
                               "def test_live(): raise AssertionError('must stay excluded')\n")
        self.connect()
        for args in [(str(self.target) + "::test_offline",), (str(self.target),)]:
            output = self.run_tests(*args)
            self.assertIn("partial run; no private counts sent", output)
            self.assertIn("enroll an offline-only file", output)
            self.assertEqual(len(self.server.attempts), 0)

    def test_report_command_quotes_powershell_and_omits_unrenderable_paths(self):
        output = self.call("-c", """
import sys
from pathlib import Path
from unittest.mock import patch
from tokensand_pytest.plugin import full_file_command
root = Path.cwd()
with patch.object(sys, "platform", "win32"), patch("tokensand_pytest.plugin.shutil.which", return_value=sys.executable):
    shell, command = full_file_command(root / "-team's files" / "test_flags.py", root)
    assert shell == "PowerShell" and command.startswith("& '")
    assert command.endswith(" -m pytest -- './-team''s files/test_flags.py'")
    assert full_file_command(root / "team‘quoted’" / "test_flags.py", root) is None
assert full_file_command(root / "file::ambiguous.py", root) is None
assert full_file_command(root / ("unprintable" + chr(27) + ".py"), root) is None
print("command boundaries passed")
""")
        self.assertIn("command boundaries passed", output)

    def test_skips_expected_failures_and_incomplete_runs_never_score(self):
        self.connect()
        for source, exitcode in [
                ("import pytest\n@pytest.mark.skip\ndef test_hidden(): pass", 0),
                ("import pytest\n@pytest.mark.xfail\ndef test_expected(): assert False", 0),
                ("import pytest\n@pytest.mark.xfail\ndef test_unexpected(): pass", 0),
                ("import pytest\n@pytest.mark.xfail(strict=True)\ndef test_unexpected(): pass", 1),
                ("import pytest\n@pytest.fixture\ndef broken(): raise ValueError()\ndef test_setup(broken): pass", 1),
                ("import pytest\n@pytest.fixture\ndef broken():\n yield\n raise ValueError()\ndef test_teardown(broken): pass", 1),
                ("import pytest\ndef test_interrupted(): raise KeyboardInterrupt()", 2),
                ("", 5)]:
            with self.subTest(source=source):
                self.target.write_text(source)
                self.run_tests(code=exitcode)
                self.assertEqual(self.server.attempts, [])
        self.target.write_text(FIXED)
        (self.repo / "test_other.py").write_text("this is invalid !")
        self.run_tests("--continue-on-collection-errors", code=1)
        (self.repo / "conftest.py").write_text("def pytest_collection_modifyitems(items): raise RuntimeError('broken hook')")
        self.run_tests(code=3)
        self.assertEqual(self.server.attempts, [])

    def test_offline_ack_loss_replays_identical_receipt_only_once(self):
        self.connect("--queue-offline")
        self.server.drop_once = True
        self.assertIn("queued", self.run_tests(code=1))
        self.assertEqual(len(self.server.receipts), 1)
        self.assertIn("delivered", self.call("-m", "tokensand_pytest", "sync", cwd=self.tests))
        self.assertIn("nothing pending", self.call("-m", "tokensand_pytest", "sync"))
        self.assertEqual(len(self.server.attempts), 2)
        self.assertEqual(self.server.attempts[0], self.server.attempts[1])
        self.assertEqual(len(self.server.receipts), 1)

    def test_midrun_disconnect_and_new_checkout_cannot_send(self):
        self.connect()
        self.target.write_text("import subprocess, sys\ndef test_stop():\n"
                               " subprocess.run([sys.executable,'-m','tokensand_pytest','disconnect'], check=True)\n")
        self.run_tests()
        self.assertEqual(self.server.attempts, [])
        other = self.repo.parent / "other"
        other.mkdir()
        subprocess.run(["git", "init", "-q", str(other)], check=True)
        (other / "test_other.py").write_text(FIXED)
        self.connect()
        self.run_tests(cwd=other)
        self.assertEqual(self.server.attempts, [])

    def test_connection_reuse_preserves_queue_permission_and_file_scope(self):
        self.connect("--queue-offline")
        with sqlite3.connect(self.db) as db:
            before = db.execute("SELECT * FROM connection").fetchone()
            grant = db.execute("SELECT token, target FROM pytest_selection").fetchone()
        self.assertEqual(grant[1], "src/tests/test_workflow.py")
        self.connect()
        another = self.tests / "test_other.py"
        another.write_text(FIXED)
        self.call("-m", "tokensand_pytest", "connect", PROJECT, another, code=1)
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute("SELECT * FROM connection").fetchone(), before)
            self.assertEqual(before[-1], 1)
            self.assertEqual(db.execute("SELECT token, target FROM pytest_selection").fetchone(), grant)
            # Exact legacy standalone reporter write when changing app, then reconnecting the original.
            db.execute("INSERT OR REPLACE INTO connection VALUES(1,?,?,?,?)",
                       (before[1], str(uuid.uuid4()), str(uuid.uuid4()), 1))
            db.execute("INSERT OR REPLACE INTO connection VALUES(1,?,?,?,?)", before[1:])
        self.run_tests(code=1)
        self.assertEqual(self.server.attempts, [])
        self.connect()
        self.run_tests(code=1)
        self.assertEqual(len(self.server.attempts), 1)

    def test_bad_http_does_not_change_native_exit_and_keeps_offline_receipt(self):
        self.connect("--queue-offline")
        self.target.write_text(FIXED)
        self.server.bad_http_once = True
        self.assertIn("queued", self.run_tests())
        self.assertIn("delivered", self.call("-m", "tokensand_pytest", "sync"))
        self.assertEqual(self.server.attempts[0], self.server.attempts[1])
        self.assertEqual(len(self.server.receipts), 1)

    def test_checkout_with_no_git_executable_and_redirected_file_stays_local(self):
        self.connect()
        self.target.write_text(FIXED)
        self.run_tests(env={**self.env, "PATH": str(self.repo / "missing-bin")})
        source = self.repo / "other"
        source.mkdir()
        (source / self.target.name).write_text(FIXED)
        self.target.unlink()
        self.tests.rmdir()
        self.tests.symlink_to(source, target_is_directory=True)
        self.run_tests(self.tests)
        self.assertEqual(self.server.attempts, [])

    def test_runtime_rerun_and_silent_deselection_stay_local(self):
        self.connect()
        self.target.write_text(FIXED)
        conftest = self.repo / "conftest.py"
        conftest.write_text("from _pytest.runner import runtestprotocol\n"
                            "def pytest_runtest_protocol(item, nextitem):\n"
                            " item.ihook.pytest_runtest_logstart(nodeid=item.nodeid, location=item.location)\n"
                            " for iteration in range(2): runtestprotocol(item, nextitem=nextitem)\n"
                            " item.ihook.pytest_runtest_logfinish(nodeid=item.nodeid, location=item.location)\n return True\n")
        self.run_tests()
        conftest.write_text("def pytest_collection_modifyitems(items): items.pop()")
        self.run_tests()
        self.assertEqual(self.server.attempts, [])

    def test_unusable_credentials_never_enable_reporting(self):
        env = self.env.copy()
        del env["TOKENS_OBSERVE_API_KEY"]
        self.call("-m", "tokensand_pytest", "connect", PROJECT, self.target, env=env, code=1)
        self.assertFalse(self.db.exists())
        self.run_tests(code=1)
        self.assertEqual(self.server.attempts, [])


if __name__ == "__main__":
    unittest.main()
