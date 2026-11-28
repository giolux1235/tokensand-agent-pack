"""Strict TLS checks against a local TLS server using the actual public wheel transport."""
from contextlib import ExitStack, redirect_stderr
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
import os
from pathlib import Path
import shutil
import sqlite3
import ssl
import subprocess
import sys
import tempfile
from threading import Thread
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import patch
from urllib.error import HTTPError

WHEEL = Path(__file__).resolve().parents[1] / "packages/tokensand_pytest_checks-0.1.3-py3-none-any.whl"
sys.path.insert(0, str(WHEEL))
from tokensand_pytest import __main__ as cli, reporter

EMPTY = SimpleNamespace(cafile=None, capath=None)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        self.server.seen.append((self.path, self.headers.get("Authorization")))
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/after-redirect")
            self.end_headers()
        else:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"stored":true}')


class CertificateTrust(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temp.cleanup)
        base = Path(temp.name)
        openssl = shutil.which("openssl")
        if not openssl:
            raise RuntimeError("The TLS fixture requires openssl to generate temporary self-signed localhost certificates.")
        config = base / "openssl.cnf"
        config.write_text("[req]\ndistinguished_name=dn\nx509_extensions=extensions\nprompt=no\n"
                          "[dn]\nCN=localhost\n[extensions]\nsubjectAltName=DNS:localhost\n"
                          "basicConstraints=critical,CA:TRUE\nkeyUsage=critical,digitalSignature,keyEncipherment,keyCertSign\n"
                          "subjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid:always\n")
        cls.bundle = base / "localhost.pem"
        cls.unrelated = base / "other.pem"
        for cert in (cls.bundle, cls.unrelated):
            subprocess.run([openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                            "-config", str(config), "-keyout", str(cert.with_suffix(".key")), "-out", str(cert)],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.server.seen = []
        server_tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_tls.load_cert_chain(cls.bundle, cls.bundle.with_suffix(".key"))
        cls.server.socket = server_tls.wrap_socket(cls.server.socket, server_side=True)
        cls.addClassCleanup(cls.server.server_close)
        Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.addClassCleanup(cls.server.shutdown)
        cls.url = f"https://localhost:{cls.server.server_port}"

    def setUp(self):
        self.server.seen.clear()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ, {"TOKENS_OBSERVE_API_KEY": "local-secret", "no_proxy": "*"}, clear=True))
        self.stack.enter_context(patch.object(reporter.sys, "platform", "darwin"))
        self.paths = self.stack.enter_context(patch.object(reporter.ssl, "get_default_verify_paths", return_value=EMPTY))
        # Model a standalone Python with zero roots regardless of the interpreter executing this test.
        self.context = self.stack.enter_context(patch.object(reporter.ssl, "create_default_context",
                    side_effect=lambda: ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)))
        self.stack.enter_context(patch.object(reporter, "_MACOS_CA_FILE", self.bundle))

    def test_empty_darwin_stores_verify_hostname_and_chain_without_touching_environment(self):
        prior = dict(os.environ)
        context = reporter._macos_tls_context()
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertEqual(context.cert_store_stats()["x509_ca"], 1)
        self.assertEqual(reporter._call(self.url + "/ok"), {"stored": True})
        self.assertEqual(self.server.seen, [("/ok", "Bearer local-secret")])
        with self.assertRaisesRegex(reporter.CertificateTrustError, "TLS certificate verification failed"):
            reporter._call(self.url.replace("localhost", "127.0.0.1") + "/wrong-host")
        with patch.object(reporter, "_MACOS_CA_FILE", self.unrelated):
            with self.assertRaisesRegex(reporter.CertificateTrustError, "TLS certificate verification failed"):
                reporter._call(self.url + "/unknown-signer")
        self.assertEqual(self.server.seen, [("/ok", "Bearer local-secret")])
        self.assertEqual(dict(os.environ), prior)

    def test_explicit_or_available_trust_sources_are_preserved(self):
        for name in ("SSL_CERT_FILE", "SSL_CERT_DIR"):
            for value in ("", "/missing/my-corporate-trust"):
                with self.subTest(name=name, value=value), patch.dict(os.environ, {name: value}):
                    self.assertIsNone(reporter._macos_tls_context())
        self.paths.assert_not_called()
        for cafile, capath in [("/configured/cert.pem", None), (None, "/configured/certdir")]:
            self.paths.return_value = SimpleNamespace(cafile=cafile, capath=capath)
            self.assertIsNone(reporter._macos_tls_context())
        self.paths.return_value = EMPTY
        with patch.object(reporter.sys, "platform", "linux"):
            self.assertIsNone(reporter._macos_tls_context())
        with patch.object(reporter, "_MACOS_CA_FILE", self.bundle.with_name("missing.pem")):
            self.assertIsNone(reporter._macos_tls_context())
        self.context.assert_not_called()
        populated = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        populated.load_verify_locations(cafile=self.unrelated)
        self.context.side_effect = lambda: populated
        self.assertIsNone(reporter._macos_tls_context())
        self.assertEqual(populated.cert_store_stats()["x509_ca"], 1)

    def test_verified_origin_cannot_redirect_the_ingestion_credential(self):
        with self.assertRaises(HTTPError) as exc:
            reporter._call(self.url + "/redirect")
        self.assertEqual(exc.exception.code, 302)
        self.assertEqual(self.server.seen, [("/redirect", "Bearer local-secret")])

    def test_tls_failure_preserves_only_opted_in_offline_receipts(self):
        original_call = reporter._call
        def wrong_host(*_):
            return original_call(self.url.replace("localhost", "127.0.0.1") + "/report")
        with tempfile.TemporaryDirectory() as temp:
            for offline in (False, True):
                dbfile = Path(temp) / f"{offline}.db"
                with sqlite3.connect(dbfile) as db:
                    db.execute("CREATE TABLE connection(id,endpoint,project,epoch,offline)")
                    db.execute("CREATE TABLE pending(run PRIMARY KEY,completed,passed,failed)")
                    db.execute("INSERT INTO connection VALUES (1,?,?,?,?)",
                               ("https://tokensand.com/api/observability/v1/repository-checks",
                                str(uuid.uuid4()), str(uuid.uuid4()), int(offline)))
                env = {"TOKENS_OBSERVE_INGEST_URL": "https://tokensand.com/api/observability/v1/events"}
                with patch.dict(os.environ, env), patch.object(reporter, "_call", side_effect=wrong_host):
                    with patch.object(reporter, "_database", side_effect=lambda **_: sqlite3.connect(dbfile)):
                        status = reporter.report_checks({"one": False}, run_id=str(uuid.uuid4()))
                        self.assertEqual(status, ("queued; " if offline else "") + reporter.TLS_ERROR)
                        if offline:
                            self.assertEqual(reporter._work("sync"), status)
                with sqlite3.connect(dbfile) as db:
                    self.assertEqual(db.execute("SELECT count(*) FROM pending").fetchone()[0], int(offline))
        self.assertEqual(self.server.seen, [])

    def test_certificate_diagnostic_is_specific_and_does_not_disclose_credentials(self):
        def wrong_host(*_):
            return reporter._call(self.url.replace("localhost", "127.0.0.1") + "/connect")
        message = StringIO()
        with patch.object(cli, "version", return_value="9.0.3"), patch.object(cli.connection, "connect", side_effect=wrong_host):
            with redirect_stderr(message):
                self.assertEqual(cli.main(["connect", "app", "file.py"]), 1)
        self.assertEqual(message.getvalue().strip(), reporter.TLS_ERROR)
        self.assertNotIn("local-secret", message.getvalue())
        self.assertEqual(self.server.seen, [])


if __name__ == "__main__":
    unittest.main()
