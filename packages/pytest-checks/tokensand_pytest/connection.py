"""A local test selection tied to the repository reporter's authenticated connection."""
from contextlib import closing
from pathlib import Path
import subprocess
import uuid

from . import reporter


def root(checkout):
    result = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=checkout,
                            capture_output=True, text=True)
    if result.returncode:
        raise ValueError("Run this command inside the workflow's Git checkout.")
    return Path(result.stdout.rstrip("\n")).resolve()


def connect(project, test_file, queue_offline):
    checkout = root(Path.cwd())
    target = Path(test_file).resolve(strict=True)
    if not target.is_file() or target.suffix != ".py" or not target.is_relative_to(checkout):
        raise ValueError("Select a Python test file inside the current Git checkout.")
    relative = target.relative_to(checkout).as_posix()

    def select(db, binding):
        db.execute("""CREATE TABLE IF NOT EXISTS pytest_selection (
            id INTEGER PRIMARY KEY CHECK(id=1), token TEXT NOT NULL, target TEXT NOT NULL,
            endpoint TEXT NOT NULL, project TEXT NOT NULL, epoch TEXT NOT NULL)""")
        # The original standalone reporter's disconnect removes its connection. Forget our opt-in too.
        db.execute("""CREATE TRIGGER IF NOT EXISTS erase_pytest_selection AFTER DELETE ON connection
                      BEGIN DELETE FROM pytest_selection; END""")
        # The standalone reporter changes its binding through INSERT OR REPLACE.
        db.execute("""CREATE TRIGGER IF NOT EXISTS revoke_changed_pytest_selection AFTER INSERT ON connection
                      BEGIN DELETE FROM pytest_selection WHERE (endpoint,project,epoch) !=
                      (NEW.endpoint,NEW.project,NEW.epoch); END""")
        prior = selected(db)
        if prior is not None:
            if prior[1] != relative:
                raise ValueError("Disconnect before selecting a different workflow file.")
            return
        db.execute("INSERT OR REPLACE INTO pytest_selection VALUES(1,?,?,?,?,?)",
                   (str(uuid.uuid4()), relative, *binding))

    return reporter.connect(project, queue_offline, select=select)


def selected(db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pytest_selection'").fetchone():
        return None
    return db.execute("""SELECT p.token, p.target FROM pytest_selection p JOIN connection c
                         ON p.endpoint=c.endpoint AND p.project=c.project AND p.epoch=c.epoch""").fetchone()


def read(checkout):
    db = reporter._database(checkout=checkout)
    if db is None:
        return None
    with closing(db):
        return selected(db)


def report(checkout, selection, scores, run_id):
    # Recheck the same consent under the writer's lock. A test can be running during a disconnect.
    return reporter.report_checks(scores, run_id=run_id, checkout=checkout,
                                  grant=lambda db, binding: selected(db) == selection)
