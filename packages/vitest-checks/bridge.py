"""Local enrollment and count delivery through the published repository-check transport."""
import argparse
from contextlib import closing
from importlib.util import module_from_spec, spec_from_file_location
import json
import os
from pathlib import Path
import signal
import sqlite3
import stat
import subprocess
import sys
import time
from urllib.error import HTTPError
from http.client import HTTPException
import uuid

def load_transport():
    spec = spec_from_file_location("tokensand_repository_transport", Path(__file__).with_name("repository_transport.py"))
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

transport = load_transport()
transport._vitest_key = None
transport._vitest_git_env = {name: os.environ[name] for name in ("PATH", "HOME", "LANG", "TMPDIR") if name in os.environ}


def checkout():
    result = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True, env=transport._vitest_git_env)
    return Path(result.stdout.rstrip("\n")).resolve(strict=True)


def selection(db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='vitest_selection'").fetchone():
        return None
    return db.execute("""SELECT v.token,v.target,v.suite,v.keyfile,c.endpoint,c.offline FROM vitest_selection v JOIN connection c
                         ON v.endpoint=c.endpoint AND v.project=c.project AND v.epoch=c.epoch""").fetchone()


def read():
    db = transport._database()
    if db is None:
        return None
    with closing(db):
        return selection(db)


def credential(path, root):
    if not path.is_absolute() or path.resolve(strict=True) != path or path.is_relative_to(root):
        raise ValueError("choose an existing owner-only key file outside the checkout")
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW), "rb") as keyfile:
        info = os.fstat(keyfile.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("expected existing owner-only key file")
        data = keyfile.read(4097)
    key = data.decode("ascii").rstrip("\r\n")
    if len(data) > 4096 or not key or any(c.isspace() for c in key):
        raise ValueError("expected a single ingestion key")
    transport._vitest_key = key


def connect(args):
    root = checkout()
    target = Path(args.file).absolute()
    if target != target.resolve(strict=True) or not target.is_relative_to(root) or not target.is_file():
        raise ValueError("choose a real test file in the current checkout")
    relative = target.relative_to(root).as_posix()
    keyfile = Path(args.key_file).expanduser().absolute()
    credential(keyfile, root)
    def enroll(db, binding):
        db.execute("""CREATE TABLE IF NOT EXISTS vitest_selection(
            id INTEGER PRIMARY KEY CHECK(id=1),token TEXT NOT NULL,target TEXT NOT NULL,suite TEXT NOT NULL,
            keyfile TEXT NOT NULL,endpoint TEXT NOT NULL,project TEXT NOT NULL,epoch TEXT NOT NULL)""")
        db.execute("""CREATE TRIGGER IF NOT EXISTS erase_vitest_selection AFTER DELETE ON connection
                      BEGIN DELETE FROM vitest_selection; END""")
        db.execute("""CREATE TRIGGER IF NOT EXISTS revoke_changed_vitest_selection AFTER INSERT ON connection
                      BEGIN DELETE FROM vitest_selection WHERE (endpoint,project,epoch)!=(NEW.endpoint,NEW.project,NEW.epoch); END""")
        current = selection(db)
        requested = (relative, args.vitest_project, str(keyfile))
        if current is not None:
            if current[1:4] != requested:
                raise ValueError("disconnect before changing selection")
            return
        db.execute("INSERT INTO vitest_selection VALUES(1,?,?,?,?,?,?,?)", (str(uuid.uuid4()), *requested, *binding))
    return transport.connect(args.project, args.queue_offline, select=enroll)


def bound_opportunistic_http():
    """Limit the whole inline delivery batch. Delayed acknowledgments keep their existing queued IDs."""
    call = transport._call
    deadline = None

    def interrupt(signum, frame):
        raise TimeoutError("Private receipt acknowledgment remains pending")

    def bounded(url, payload=None):
        nonlocal deadline
        if deadline is None:
            deadline = time.monotonic() + 0.6
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("No foreground delivery time remains")
        previous = signal.signal(signal.SIGALRM, interrupt)
        signal.setitimer(signal.ITIMER_REAL, remaining)
        try:
            return call(url, payload)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)

    transport._call = bounded


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(required=True, dest="action")
    enroll = commands.add_parser("connect")
    enroll.add_argument("project")
    enroll.add_argument("file")
    enroll.add_argument("--key-file", required=True)
    enroll.add_argument("--vitest-project", default="")
    enroll.add_argument("--queue-offline", action="store_true")
    for cmd in ["selection", "sync", "disconnect", "record"]:
        commands.add_parser(cmd)
    args = parser.parse_args()
    if args.action == "connect":
        return connect(args)
    selected = read()
    if args.action == "selection":
        if selected is None:
            return "null"
        token, file, suite, _, _, _ = selected
        return json.dumps({"token":token, "file":str(checkout()/file), "suite":suite})
    if selected is None:
        return "disconnected"
    if args.action == "disconnect":
        return transport._work("disconnect")
    if args.action == "record":
        raw = sys.stdin.buffer.read(4097)
        if len(raw) > 4096:
            raise ValueError("invalid counts")
        info = json.loads(raw)
        if set(info) != {"token", "run", "passed", "failed"} or info["token"] != selected[0]:
            return "disconnected"
        passed, failed = info["passed"], info["failed"]
        if type(passed) is not int or type(failed) is not int or min(passed,failed)<0 or not 1<=passed+failed<=10000:
            return "unscored"
    keyfile, endpoint = selected[3:5]
    os.environ["TOKENS_OBSERVE_INGEST_URL"] = endpoint.removesuffix("repository-checks") + "events"
    try:
        credential(Path(keyfile), checkout())
    except (OSError, ValueError, UnicodeError):
        # Preserve the transport's opted-in outbox if the local credential is temporarily unavailable.
        transport._vitest_key = None
    same = lambda db, binding: selection(db) == selected
    if args.action == "sync":
        return transport._work("sync", grant=same)
    if selected[5]:
        bound_opportunistic_http()
    scores = {i: i < passed for i in range(passed + failed)}
    return transport.report_checks(scores, run_id=info["run"], grant=same)

if __name__ == "__main__":
    try:
        result = main()
        print(result)
        sys.exit(0 if result in ("connected", "nothing pending", "delivered", "disconnected", "null") or result.startswith("{") else 1)
    except (OSError, HTTPException, HTTPError, RuntimeError, sqlite3.Error, ValueError, KeyError, subprocess.SubprocessError):
        print("Tokens& could not access the selected checkout, private credential or app connection.", file=sys.stderr)
        sys.exit(1)
