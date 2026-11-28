"""Transport for the public tokensand.com/agent-pack/tokensand_repo_checks.py contract."""
from http.client import HTTPException
import json
import os
from pathlib import Path
import sqlite3
import ssl
import subprocess
import sys
import time
from urllib import error, parse, request
import uuid

WEEK = 7 * 86400
MAX_PENDING = 256
_MACOS_CA_FILE = Path("/etc/ssl/cert.pem")
TLS_ERROR = "TLS certificate verification failed. Check the endpoint hostname and this Python environment's trusted CA certificates."


class CertificateTrustError(Exception):
    """A TLS certificate could not be verified using the configured trust store."""


def _macos_tls_context():
    if sys.platform != "darwin" or "SSL_CERT_FILE" in os.environ or "SSL_CERT_DIR" in os.environ:
        return None
    defaults = ssl.get_default_verify_paths()
    if defaults.cafile is not None or defaults.capath is not None or not _MACOS_CA_FILE.is_file():
        return None
    # Some standalone macOS Python builds have neither an OpenSSL store nor loaded roots.
    context = ssl.create_default_context()
    if context.cert_store_stats()["x509_ca"]:
        return None
    try:
        context.load_verify_locations(cafile=str(_MACOS_CA_FILE))
    except (OSError, ssl.SSLError):
        raise CertificateTrustError(TLS_ERROR) from None
    if ssl.HAS_ALPN:
        context.set_alpn_protocols(["http/1.1"])
    return context


def _uuid(value):
    return str(uuid.UUID(str(value)))


def _database(create=False, checkout=None, *, readonly=False):
    git = subprocess.run(["git", "rev-parse", "--absolute-git-dir"], capture_output=True, text=True, cwd=checkout)
    if git.returncode:
        if create:
            raise ValueError("Run connect inside your Git checkout.")
        return None
    directory = Path(git.stdout.strip()) / "tokensand-private"
    dbfile = directory / "repository-checks.db"
    if directory.is_symlink() or dbfile.is_symlink():
        raise ValueError("Use the checkout’s own local database.")
    if not create and not dbfile.exists():
        return None
    if readonly:
        return sqlite3.connect(dbfile.as_uri() + "?mode=ro", uri=True, timeout=5)
    directory.mkdir(mode=0o700, exist_ok=True)
    if not dbfile.exists():
        with os.fdopen(os.open(dbfile, os.O_CREAT | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), 0o600), "wb"):
            pass
    db = sqlite3.connect(dbfile, timeout=5)
    if create:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS connection (id INTEGER PRIMARY KEY CHECK (id=1),
                endpoint TEXT NOT NULL, project TEXT NOT NULL, epoch TEXT NOT NULL, offline INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS pending (run TEXT PRIMARY KEY, completed INTEGER NOT NULL,
                passed INTEGER NOT NULL, failed INTEGER NOT NULL);
        """)
    return db


def _results_url(binding):
    if not binding or binding[0] != "https://tokensand.com/api/observability/v1/repository-checks":
        return None
    project = binding[1]
    try:
        identifier = uuid.UUID(str(project))
        if str(identifier) != project or identifier.version not in range(1, 9):
            return None
    except ValueError:
        return None
    return f"https://tokensand.com/dashboard/agents?mode=developer&setup=repo&observeProjectId={project}#repository-checks"


def results_url():
    db = _database(readonly=True)
    if db is None:
        return None
    try:
        return _results_url(db.execute("SELECT endpoint,project FROM connection WHERE id=1").fetchone())
    finally:
        db.close()


def interactive_results(is_terminal):
    return is_terminal and not any(key in os.environ for key in ("CI", "GITHUB_ACTIONS", "BUILD_NUMBER", "TF_BUILD", "TEAMCITY_VERSION"))


def _endpoint():
    raw = os.environ.get("TOKENS_OBSERVE_INGEST_URL", "")
    url = parse.urlsplit(raw)
    hosted = url.scheme == "https" and url.netloc == "tokensand.com" and url.path == "/api/observability/v1/events"
    local = url.scheme == "http" and url.hostname in ("127.0.0.1", "localhost", "::1") and url.path in (
        "/v1/events", "/api/observability/v1/events")
    if not (hosted or local) or url.username or url.password or url.query or url.fragment:
        raise ValueError("Set TOKENS_OBSERVE_INGEST_URL to the Connect app endpoint.")
    return raw[:-len("events")] + "repository-checks"


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def _call(url, payload=None):
    key = os.environ.get("TOKENS_OBSERVE_API_KEY")
    if not key:
        raise ValueError("Set the ingestion key for this private app.")
    data = None if payload is None else json.dumps(payload).encode()
    req = request.Request(url, data=data, headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    handlers = [_NoRedirect()]
    context = _macos_tls_context() if req.type == "https" else None
    if context is not None:
        handlers.append(request.HTTPSHandler(context=context))
    try:
        response = request.build_opener(*handlers).open(req, timeout=3)
    except error.URLError as exc:
        if isinstance(exc.reason, ssl.SSLCertVerificationError):
            raise CertificateTrustError(TLS_ERROR) from None
        raise
    with response:
        raw = response.read(4097)
        if len(raw) > 4096 or response.headers.get_content_type() != "application/json":
            raise ValueError("Invalid response from private app.")
        decoded = json.loads(raw)
        if not isinstance(decoded, dict):
            raise ValueError("Invalid response from private app.")
        return decoded


def connect(project, offline=False, *, select=None):
    project = _uuid(project)
    endpoint = _endpoint()
    reply = _call(endpoint + "/connection/" + project)
    if reply.get("projectId") != project:
        raise ValueError("The key does not match this app.")
    epoch = _uuid(reply["connectionId"])
    db = _database(create=True)
    try:
        with db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT endpoint, project, epoch FROM connection").fetchone()
            if select is not None and old:
                if old != (endpoint, project, epoch):
                    raise ValueError("Disconnect the existing checkout before selecting a different app or connection.")
                offline = offline or bool(db.execute("SELECT offline FROM connection").fetchone()[0])
            if old != (endpoint, project, epoch) or not offline:
                db.execute("DELETE FROM pending")
            db.execute("INSERT OR REPLACE INTO connection VALUES(1,?,?,?,?)", (endpoint, project, epoch, int(offline)))
            if select is not None:
                select(db, (endpoint, project, epoch))
    finally:
        db.close()
    return "connected"


def _deliver(db, binding, row):
    endpoint, project, epoch, _ = binding
    if _endpoint() != endpoint:
        return "wrong endpoint"
    run, completed, passed, failed = row
    if (_uuid(run) != run or _uuid(project) != project or _uuid(epoch) != epoch
            or any(type(n) is not int for n in (completed, passed, failed))
            or min(passed, failed) < 0 or not 1 <= passed + failed <= 10000):
        raise ValueError("Invalid queued check.")
    reply = _call(endpoint, {"schemaVersion": 1, "projectId": project, "connectionId": epoch,
                             "runId": run, "completedAtMs": completed, "passed": passed, "failed": failed})
    if reply.get("stored") is not True or reply.get("runId") != run or reply.get("projectId") != project:
        raise ValueError("Unconfirmed check delivery.")
    db.execute("DELETE FROM pending WHERE run=?", (run,))
    return "delivered"


def _send_pending(db, binding, rows, requested_run):
    confirmed = False
    try:
        for row in rows:
            outcome = _deliver(db, binding, row)
            if outcome != "delivered":
                return outcome, confirmed
            if requested_run is None or row[0] == requested_run:
                confirmed = True
    except CertificateTrustError:
        return ("queued; " if binding[3] else "") + TLS_ERROR, confirmed
    except error.HTTPError as exc:
        if exc.code in (401, 403, 404):
            return "check app connection", confirmed
        if exc.code in (400, 409, 410):
            db.execute("DELETE FROM pending WHERE run=?", (row[0],))
            return "receipt rejected", confirmed
        return ("queued" if binding[3] else "delivery unavailable"), confirmed
    except (OSError, HTTPException, ValueError, KeyError):
        return ("queued" if binding[3] else "delivery unavailable"), confirmed
    if db.execute("SELECT 1 FROM pending LIMIT 1").fetchone():
        return "queued", confirmed
    return ("delivered" if rows else "nothing pending"), confirmed


def _work(action, scores=None, run_id=None, *, checkout=None, grant=None, on_delivered=None):
    db = _database(checkout=checkout)
    if db is None:
        return "disconnected"
    try:
        with db:
            # Serialize local connect/disconnect with sends, including concurrent test processes.
            db.execute("BEGIN IMMEDIATE")
            binding = db.execute("SELECT endpoint,project,epoch,offline FROM connection").fetchone()
            if action == "disconnect":
                db.execute("DELETE FROM pending")
                db.execute("DELETE FROM connection")
                return "disconnected"
            if not binding or (grant is not None and not grant(db, binding)):
                return "disconnected"
            now = time.time_ns() // 1000000
            db.execute("DELETE FROM pending WHERE completed<=?", (now - WEEK * 1000,))
            fresh = None
            if action == "report":
                if not scores or len(scores) > 10000 or any(type(score) is not bool for score in scores.values()):
                    return "unscored"
                run = _uuid(run_id) if run_id is not None else str(uuid.uuid4())
                passed = sum(scores.values())
                fresh = (run, now, passed, len(scores) - passed)
                if binding[3]:
                    prior = db.execute("SELECT passed,failed FROM pending WHERE run=?", (run,)).fetchone()
                    if prior and prior != fresh[2:]:
                        return "conflicting result"
                    if not prior and db.execute("SELECT count(*) FROM pending").fetchone()[0] >= MAX_PENDING:
                        oldest = db.execute("SELECT run,completed,passed,failed FROM pending ORDER BY completed LIMIT 1").fetchone()
                        try:
                            if _deliver(db, binding, oldest) != "delivered":
                                return "queue full"
                        except CertificateTrustError:
                            return "queue full; " + TLS_ERROR
                        except (OSError, HTTPException, ValueError):
                            return "queue full"
                    db.execute("INSERT OR IGNORE INTO pending VALUES(?,?,?,?)", fresh)
            if binding[3]:
                # Queue before the first network request so process death cannot lose a retry.
                db.commit()
                db.execute("BEGIN IMMEDIATE")
                if db.execute("SELECT endpoint,project,epoch,offline FROM connection").fetchone() != binding:
                    return "disconnected"
                rows = db.execute("SELECT run,completed,passed,failed FROM pending ORDER BY completed LIMIT 8").fetchall()
            else:
                rows = [fresh] if fresh else []
            status, confirmed = _send_pending(db, binding, rows, fresh[0] if fresh else None)
        # This binding produced a confirmed receipt and the local transaction committed.
        if confirmed and on_delivered is not None:
            url = _results_url(binding)
            if url:
                on_delivered(url)
        return status
    finally:
        db.close()


def report_checks(scores, *, run_id=None, checkout=None, grant=None, on_delivered=None):
    """Pass one bool per expected assertion or None if a predicate never returned."""
    try:
        return _work("report", scores, run_id, checkout=checkout, grant=grant, on_delivered=on_delivered)
    except (OSError, HTTPException, sqlite3.Error, TypeError, ValueError, AttributeError):
        return "report unavailable"
