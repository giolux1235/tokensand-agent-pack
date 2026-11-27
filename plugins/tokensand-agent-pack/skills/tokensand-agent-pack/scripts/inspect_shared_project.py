#!/usr/bin/env python3
"""Locate the source a publisher linked on a public Tokens& project. Never run it."""
import argparse
from html.parser import HTMLParser
import http.client
import json
import os
from pathlib import Path
import re
import ssl
import sys
import time
from urllib.parse import quote, unquote, urlsplit

ORIGIN = "https://tokensand.com"
PAGE = re.compile(r"https://tokensand\.com(/p/[a-z0-9][a-z0-9-]{0,127})/?(?:[?#][^\s]*)?")
REPOSITORY = r"https://github\.com/([A-Za-z0-9][A-Za-z0-9-]{0,38})/([A-Za-z0-9_.-]+)"
PINNED_FILE = re.compile(REPOSITORY + r"/blob/([0-9a-fA-F]{40})/([^?#]+)(?:#L[0-9]+(?:-L[0-9]+)?)?")
LIMIT = 2 * 1024 * 1024


def project_url(value):
    match = PAGE.fullmatch(value)
    if not match:
        raise ValueError("Use the public https://tokensand.com/p/… URL returned by the publisher.")
    return ORIGIN + match[1]


def tls_context():
    context = ssl.create_default_context()
    # Some python.org macOS interpreters have neither an OpenSSL store nor loaded roots.
    if sys.platform == "darwin" and not any(k in os.environ for k in ("SSL_CERT_FILE", "SSL_CERT_DIR")):
        defaults = ssl.get_default_verify_paths()
        system_ca = Path("/etc/ssl/cert.pem")
        if not defaults.cafile and not defaults.capath and not context.cert_store_stats()["x509_ca"] and system_ca.is_file():
            context.load_verify_locations(cafile=str(system_ca))
    return context


def fetch_page(url):
    path = project_url(url)[len(ORIGIN):]
    connection = http.client.HTTPSConnection("tokensand.com", context=tls_context(), timeout=10)
    deadline = time.monotonic() + 15
    try:
        connection.request("GET", path, headers={"Accept": "text/html", "Accept-Encoding": "identity"})
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError(f"Public project returned HTTP {response.status}. Use the exact public link.")
        if response.headers.get_content_type() != "text/html":
            raise ValueError("The project did not return an HTML page.")
        body = bytearray()
        while len(body) <= LIMIT and time.monotonic() < deadline:
            block = response.read1(min(65536, LIMIT + 1 - len(body)))
            if not block:
                return body.decode("utf-8")
            body.extend(block)
        raise ValueError("The public page exceeded the size or time limit.")
    finally:
        connection.close()


def unique_fields(items):
    fields = dict(items)
    if len(items) != len(fields):
        raise ValueError("The public project metadata contains duplicate fields.")
    return fields


class ProjectPage(HTMLParser):
    def __init__(self):
        super().__init__()
        self.canonicals = []
        self.records = []
        self.script = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "link" and "canonical" in (attrs.get("rel") or "").lower().split():
            self.canonicals.append(attrs.get("href"))
        elif tag == "script" and attrs.get("type") == "application/ld+json":
            self.script = []

    def handle_data(self, data):
        if self.script is not None:
            self.script.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.script is not None:
            data = json.loads("".join(self.script), object_pairs_hook=unique_fields)
            self.script = None
            if isinstance(data, dict) and data.get("@type") == "SoftwareSourceCode":
                self.records.append(data)


def https_link(value):
    if not isinstance(value, str) or not value.isascii() or not value.isprintable() or " " in value or "\\" in value:
        return False
    url = urlsplit(value)
    return (url.scheme == "https" and bool(url.hostname) and url.username is None and url.password is None
            and not url.query and not url.fragment)


def github_repo(owner, repo):
    return f"https://github.com/{owner}/{repo}"


def pinned_file(value):
    if not isinstance(value, str):
        return None
    match = PINNED_FILE.fullmatch(value)
    if not match:
        return None
    owner, repo, revision, filename = match.groups()
    try:
        parts = [unquote(part, errors="strict") for part in filename.split("/")]
    except UnicodeError:
        return None
    if (repo in (".", "..") or any(part in ("", ".", "..") or not part.isprintable()
                                  or "/" in part or "\\" in part for part in parts)):
        return None
    return github_repo(owner, repo), revision.lower(), "/".join(parts)


def inspect(url, html):
    parser = ProjectPage()
    parser.feed(html)
    parser.close()
    if parser.canonicals != [url] or len(parser.records) != 1 or parser.records[0].get("url") != url:
        raise ValueError("The page does not uniquely identify this public project.")
    record = parser.records[0]
    if record.get("@context") not in ("https://schema.org", "https://schema.org/"):
        raise ValueError("Unrecognized public project metadata.")
    repo = record.get("codeRepository")
    if repo is not None and not https_link(repo):
        raise ValueError("The published repository field is malformed.")
    links = record.get("sameAs", [])
    if not isinstance(links, list) or not all(isinstance(link, str) for link in links):
        raise ValueError("The public source links are malformed.")
    sources = {source for link in links if (source := pinned_file(link))}
    lines = [f"Project: {url}"]
    if repo:
        lines.append(f"Published repository: {repo}")
    if len(sources) == 1:
        source_repo, revision, filename = sources.pop()
        # A conflicting GitHub repository link cannot establish which source was published.
        linked_repos = {github_repo(match[1], match[2].removesuffix(".git")).casefold() for link in links
                        if (match := re.fullmatch(REPOSITORY + r"(?:/?|/(?:blob|tree)/.*)", link))}
        # Do not reconcile links across owners or repositories.
        normalized = repo.rstrip("/").removesuffix(".git") if repo else source_repo
        if normalized.casefold() == source_repo.casefold() and linked_repos == {source_repo.casefold()}:
            if not repo:
                lines.append(f"Repository from linked file: {source_repo}")
            lines.extend([f"Linked revision: {revision}", f"Linked file: {filename}",
                          f"Source: {source_repo}/blob/{revision}/{quote(filename)}",
                          "Inspect this edition's source and instructions before running a local check."])
            return 0, lines
        lines.append("The linked file belongs to a different repository. Confirm which source to use with the publisher.")
    elif sources:
        lines.append("Several pinned files are linked. Confirm which file the publisher intended.")
    elif repo:
        lines.append("No unique pinned file. Inspect the published repository and record the revision you test.")
    else:
        lines.append("The project provides no supported pinned source. Ask the publisher for the repository or an exact file link.")
    return 2, lines


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="public Tokens& project URL")
    args = parser.parse_args()
    try:
        url = project_url(args.url)
        status, lines = inspect(url, fetch_page(url))
    except (ValueError, UnicodeError, RecursionError, OSError, http.client.HTTPException) as exc:
        if isinstance(exc, ssl.SSLCertVerificationError):
            print("Python could not verify tokensand.com. Check the certificates trusted by this interpreter.", file=sys.stderr)
        elif isinstance(exc, ValueError) and not isinstance(exc, (json.JSONDecodeError, UnicodeError)):
            print(str(exc), file=sys.stderr)
        else:
            print("Could not read valid public project metadata from tokensand.com.", file=sys.stderr)
        return 1
    print("\n".join(lines))
    return status


if __name__ == "__main__":
    sys.exit(main())
