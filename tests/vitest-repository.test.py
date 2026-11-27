"""Offline consumer installation and an actual Vitest 4.1 reporter against the existing local receipt contract."""
from http.server import ThreadingHTTPServer
from importlib.util import module_from_spec, spec_from_file_location
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from threading import Thread
import unittest

ROOT = Path(__file__).resolve().parents[1]
MODULES = Path(os.environ.get("VITEST_TEST_MODULES", ROOT / "node_modules")).resolve()
spec = spec_from_file_location("repository_fixture", ROOT / "tests/pytest-repository.test.py")
shared = module_from_spec(spec)
spec.loader.exec_module(shared)

TEST = """import {test} from 'vitest';
import assert from 'node:assert/strict';
import {total} from './orders.mjs';
test('counts every shipped item', () => assert.equal(total(), 4));
test('keeps private inputs off the worker', () => {
  assert.equal(process.env.TOKENS_OBSERVE_API_KEY, undefined);
  assert.equal(process.env.TOKENS_OBSERVE_INGEST_URL, undefined);
});
"""



class ControlledReceipt(shared.Receiver):
    def reply(self, status, body):
        if self.command == "POST" and self.server.ack_delay:
            time.sleep(self.server.ack_delay)
        try:
            super().reply(status, body)
        except (BrokenPipeError, ConnectionResetError):
            pass  # A queued native run can finish before the delayed response arrives.


@unittest.skipUnless(os.name == "posix" and (MODULES / "vitest/vitest.mjs").exists(), "provide Vitest 4.1 with VITEST_TEST_MODULES")
class PrivateVitestCheck(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.app = self.base / "orders"
        self.app.mkdir()
        self.env = {key: os.environ[key] for key in ["PATH", "LANG", "TMPDIR"] if key in os.environ}
        self.env.update(CI="1", HOME=str(self.base), NO_COLOR="1", npm_config_offline="true")
        self.package = self.base / "tokensand-vitest-checks-0.1.0.tgz"
        self.cmd(sys.executable, ROOT / "packages/vitest-checks/build.py", self.base)
        self.assertEqual(self.package.read_bytes(), (ROOT / "packages" / self.package.name).read_bytes())
        (self.app / "package.json").write_text(json.dumps({"name":"orders","private":True,"type":"module",
            "scripts":{"test":"node node_modules/vitest/vitest.mjs run orders.test.ts --no-cache"}}))
        self.cmd("git", "init", "-q")
        self.cmd("npm", "install", "--offline", "--ignore-scripts", "--legacy-peer-deps", "--no-audit", "--no-fund", "--save-dev", self.package)
        for name in ["vitest", "vite"]:
            (self.app / "node_modules" / name).symlink_to((MODULES / name).resolve(), target_is_directory=True)
        (self.app / "vitest.config.mjs").write_text("export default {test:{reporters:['default','@tokensand/vitest-checks','@tokensand/vitest-checks']}};")
        (self.app / "orders.test.ts").write_text(TEST)
        (self.app / "orders.mjs").write_text("export const total=()=>2;")
        self.key = self.base / "credential"
        self.key.write_text("fixture-private")
        self.key.chmod(0o600)
        self.receiver = ThreadingHTTPServer(("127.0.0.1", 0), ControlledReceipt)
        self.receiver.ack_delay = 0
        self.receiver.receipts = {}
        self.receiver.attempts = []
        self.receiver.handshakes = 0
        self.receiver.drop_once = self.receiver.bad_http_once = False
        worker = Thread(target=self.receiver.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(self.receiver.server_close)
        self.addCleanup(self.receiver.shutdown)
        self.url = f"http://127.0.0.1:{self.receiver.server_port}/api/observability/v1/events"

    def cmd(self, *args, code=0, env=None):
        result = subprocess.run(list(map(str,args)),cwd=self.app,env=self.env | (env or {}),text=True,capture_output=True,timeout=45)
        self.assertEqual(result.returncode,code,result.stdout+result.stderr)
        return result.stdout+result.stderr

    def connect(self):
        return self.cmd("node", "node_modules/@tokensand/vitest-checks/cli.mjs", "connect", shared.PROJECT,
                        "orders.test.ts", "--key-file", self.key, "--queue-offline", env={"TOKENS_OBSERVE_INGEST_URL": self.url})

    def action(self, name):
        return self.cmd("node", "node_modules/@tokensand/vitest-checks/cli.mjs", name)

    def test_native_failure_repair_retry_and_independent_disconnect(self):
        other = self.app / ".git/tokensand-private/repository-checks.db"
        other.parent.mkdir()
        with sqlite3.connect(other) as db:
            db.executescript("CREATE TABLE connection(id INTEGER); INSERT INTO connection VALUES(1); CREATE TABLE pending(run TEXT); INSERT INTO pending VALUES('separate-consent');")
        original = other.read_bytes()
        self.assertNotIn("Tokens& private check", self.cmd("npm","test",code=1))
        self.assertFalse((self.app/".git/tokensand-private-vitest").exists())
        self.assertEqual(self.receiver.handshakes,0)
        self.assertIn("connected",self.connect())
        self.assertNotIn("private check",self.cmd("npm","test","--","-t","counts every",code=1))
        self.assertFalse(self.receiver.attempts)
        self.assertIn("1 passed, 1 failed",self.cmd("npm","test",code=1))
        (self.app/"orders.mjs").write_text("export const total=()=>4;")
        self.receiver.drop_once=True
        self.assertIn("queued (2 passed, 0 failed)", self.cmd("npm","test"))
        self.assertIn("delivered",self.action("sync"))
        self.assertEqual(self.receiver.attempts[-2],self.receiver.attempts[-1])
        self.assertEqual(len(self.receiver.receipts),2)
        self.assertIn("nothing pending",self.action("sync"))
        self.assertEqual(len(self.receiver.attempts),3)
        self.receiver.drop_once = True
        self.assertIn("queued", self.cmd("npm", "test"))
        queue = self.app / ".git/tokensand-private-vitest/repository-checks.db"
        with sqlite3.connect(queue) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM pending").fetchone()[0], 1)
        self.assertIn("disconnected",self.action("disconnect"))
        with sqlite3.connect(queue) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM pending").fetchone()[0], 0)
        self.assertIn("disconnected", self.action("sync"))
        self.assertNotIn("private check",self.cmd("npm","test"))
        self.assertEqual(len(self.receiver.attempts),4)
        self.assertEqual(other.read_bytes(),original)

    def test_slow_acknowledgment_queues_before_full_sync(self):
        self.connect()
        self.receiver.ack_delay = 1.35
        # Healthy explicit sync waits long enough for a deliberate slow ACK.
        # The inline sender must return queued for that same receiver.
        self.assertIn("queued (1 passed, 1 failed)", self.cmd("npm", "test", code=1))
        self.assertIn("delivered", self.action("sync"))
        self.assertEqual(len(self.receiver.receipts), 1)
        self.assertEqual(self.receiver.attempts[0], self.receiver.attempts[1])

    def test_ongoing_run_cannot_reuse_future_consent(self):
        self.connect()
        (self.app/"orders.test.ts").write_text("""import {test,expect} from 'vitest';
import {existsSync,writeFileSync} from 'node:fs';
test('handles shipping', async () => {
  writeFileSync('running','');
  while (!existsSync('finish')) await new Promise(resolve=>setTimeout(resolve, 10));
  expect(4).toBe(4);
}, 12000);
""")
        with subprocess.Popen(["node", "node_modules/vitest/vitest.mjs", "run", "orders.test.ts"],
                              cwd=self.app,env=self.env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) as child:
            try:
                deadline=time.monotonic()+8
                while not (self.app/"running").exists() and child.poll() is None and time.monotonic()<deadline:
                    time.sleep(.03)
                self.assertTrue((self.app/"running").exists())
                self.assertIn("disconnected",self.action("disconnect"))
                self.assertIn("connected",self.connect())
                (self.app/"finish").touch()
                stdout,stderr=child.communicate(timeout=10)
                self.assertEqual(child.returncode,0,stdout+stderr)
                self.assertFalse(self.receiver.attempts)
            finally:
                if child.poll() is None: child.kill()

    def test_private_key_stays_out_of_git_and_tests(self):
        real_git = shutil.which("git")
        audit = self.base / "git-env.jsonl"
        wrappers = self.base / "bin"
        wrappers.mkdir()
        shim = wrappers / "git"
        shim.write_text(f"#!{sys.executable}\nimport json,os,sys\n"
                        + f"with open({str(audit)!r},'a') as log: log.write(json.dumps(sorted(os.environ))+'\\n')\n"
                        + f"os.execv({real_git!r}, [{real_git!r}, *sys.argv[1:]])\n")
        shim.chmod(0o755)
        self.env["PATH"] = str(wrappers) + os.pathsep + self.env["PATH"]
        self.env["TOKENS_OBSERVE_API_KEY"] = "unused-shell-fixture"
        self.env["UNRELATED_PRIVATE_VALUE"] = "other-shell-fixture"
        self.connect()
        self.env.pop("TOKENS_OBSERVE_API_KEY")
        self.env.pop("UNRELATED_PRIVATE_VALUE")
        self.cmd("npm","test",code=1)
        observed = [json.loads(line) for line in audit.read_text().splitlines()]
        self.assertGreaterEqual(len(observed), 4)
        for names in observed:
            self.assertNotIn("TOKENS_OBSERVE_API_KEY", names)
            self.assertNotIn("TOKENS_OBSERVE_INGEST_URL", names)
            self.assertNotIn("UNRELATED_PRIVATE_VALUE", names)
        self.assertEqual(len(self.receiver.receipts), 1)

    def test_hooked_case_and_late_teardown(self):
        self.connect()
        (self.app/"orders.test.ts").write_text("import {beforeEach} from 'vitest'; beforeEach(()=>{});\n"+TEST)
        self.assertNotIn("private check",self.cmd("npm","test",code=1))
        (self.app/"orders.test.ts").write_text(TEST)
        (self.app/"orders.mjs").write_text("export const total=()=>4;")
        (self.app/"vitest.config.mjs").write_text("export default {test:{reporters:['default','@tokensand/vitest-checks'],globalSetup:['./global.mjs']}};")
        (self.app/"global.mjs").write_text("export function setup(){};export function teardown(){throw Error('late teardown failure')}")
        result = self.cmd("npm","test")
        self.assertIn("late teardown failure",result)
        self.assertNotIn("private check",result)
        self.assertFalse(self.receiver.attempts)

    def test_nonordinary_cases_and_local_reviewer_stay_private(self):
        self.connect()
        for edge in ["test.skip('backorder',()=>{});","test.fails('expected failure',()=>assert.fail());",
                     "test('invalid input',()=>{throw Error('application did not start')});",
                     "test('flaky',{retry:1},({task})=>assert.equal(task.result?.retryCount,1));"]:
            (self.app/"orders.test.ts").write_text(TEST+edge)
            self.assertNotIn("private check",self.cmd("npm","test",code=1))
        (self.app/"orders.test.ts").write_text(TEST)
        self.assertNotIn("private check",self.cmd("npm","test",code=1,env={"TOKENSAND_VITEST_RECEIPT":"local-review"}))
        self.assertFalse(self.receiver.attempts)


if __name__ == "__main__":
    unittest.main()
