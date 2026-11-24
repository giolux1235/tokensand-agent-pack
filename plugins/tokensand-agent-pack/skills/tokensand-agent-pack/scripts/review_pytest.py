#!/usr/bin/env python3
"""Review two local pytest JUnit reports against explicitly declared test sources."""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from xml.etree import ElementTree as ET


class Invalid(ValueError):
    pass


class ReportTree(ET.TreeBuilder):
    def doctype(self, name, pubid, system):
        raise Invalid("XML document type declarations are unsupported")


class Arguments(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(1, "INVALID: check report paths, checkout roots, and --test-source arguments.\n")


ATTACHMENTS = {"properties", "system-out", "system-err"}
OUTCOMES = {"failure": "failed", "error": "error", "skipped": "skipped"}
LIMIT = 16 * 1024 * 1024
SCOPE = ("User-supplied report/checkout pairing. Source hashes describe the files now. "
         "JUnit can omit non-strict XPASS; check the native pytest terminal outcome. "
         "This review does not authorize a release.")


def read_report(path):
    if not path.is_file():
        raise Invalid("report must be a regular file")
    with path.open("rb") as stream:
        data = stream.read(LIMIT + 1)
    if len(data) > LIMIT:
        raise Invalid("report exceeds 16 MiB")
    root = ET.fromstring(data, parser=ET.XMLParser(target=ReportTree()))
    if root.tag not in {"testsuite", "testsuites"}:
        raise Invalid("expected pytest JUnit testsuite or testsuites")
    suites = list(root) if root.tag == "testsuites" else [root]
    cases = {}
    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    for suite in suites:
        if suite.tag != "testsuite":
            raise Invalid("unsupported JUnit suite structure")
        rows = {}
        counts = {"failures": 0, "errors": 0, "skipped": 0}
        for case in suite:
            if case.tag in ATTACHMENTS:
                continue
            name = case.get("name")
            if case.tag != "testcase" or not name or not name.strip() or "classname" not in case.attrib:
                raise Invalid("testcase identity or suite structure is invalid")
            key = (case.get("classname"), name)
            tags = [child.tag for child in case if child.tag not in ATTACHMENTS]
            if any(tag not in OUTCOMES for tag in tags) or len(tags) != len(set(tags)):
                raise Invalid("unsupported or repeated testcase outcome")
            if "skipped" in tags and len(tags) > 1:
                raise Invalid("inconsistent skipped testcase outcome")
            for tag, attr in (("failure", "failures"), ("error", "errors"), ("skipped", "skipped")):
                counts[attr] += int(tag in tags)
            states = {OUTCOMES[tag] for tag in tags} or {"passed"}
            if key in rows:
                # Native pytest emits two rows for a failed call followed by a teardown error.
                if (rows[key], states) != ({"failed"}, {"error"}):
                    raise Invalid("ambiguous duplicate testcase identity")
                states.add("failed")
            rows[key] = states
        counts["tests"] = len(rows)
        for attr, count in counts.items():
            totals[attr] += count
            declared = suite.get(attr)
            if declared != str(count):
                raise Invalid("suite totals disagree with testcase evidence")
        if cases.keys() & rows.keys():
            raise Invalid("ambiguous duplicate testcase identity across suites")
        cases.update({key: "+".join(sorted(states)) for key, states in rows.items()})
    if root.tag == "testsuites" and any(root.get(k) not in (None, str(n)) for k, n in totals.items()):
        raise Invalid("report totals disagree with testcase evidence")
    if not cases:
        raise Invalid("report contains no testcases")
    return cases


def source_sha(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise Invalid("declared test source escapes its checkout")
    if not path.exists():
        return None
    if not path.is_file():
        raise Invalid("declared test source must be a regular file")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def case_label(key):
    group, name = key
    stem, param, _ = name.partition("[")
    fingerprint = hashlib.sha256(json.dumps(key, ensure_ascii=True).encode()).hexdigest()[:12]
    if not re.fullmatch(r"[A-Za-z0-9_.]*", group) or not re.fullmatch(r"[A-Za-z0-9_]+", stem):
        return "case#" + fingerprint
    return (group + "::" if group else "") + stem + ("[id#" + fingerprint + "]" if param else "")


def review(args):
    before, after = read_report(args.before), read_report(args.after)
    roots = [path.resolve(strict=True) for path in (args.before_root, args.after_root)]
    if not all(root.is_dir() for root in roots):
        raise Invalid("checkout roots must be directories")
    sources = []
    same_sources = True
    for relative in dict.fromkeys(args.test_source):
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise Invalid("use a checkout-relative test source path")
        old, new = (source_sha(root, relative) for root in roots)
        if old is None and new is None:
            raise Invalid("declared test source missing from both checkouts")
        same_sources = same_sources and old == new
        sources.append((str(relative), old, new))
    comparable = same_sources and before.keys() == after.keys()
    eligible = (comparable
                and all(state in {"passed", "failed"} for state in before.values())
                and all(state == "passed" for state in after.values()))
    print(("PASS" if eligible else "HOLD") + " — pytest JUnit report review")
    print(f"Baseline passed {list(before.values()).count('passed')}/{len(before)}; candidate passed {list(after.values()).count('passed')}/{len(after)}.")
    for path, old, new in sources:
        detail = old if old == new else f"{old or 'missing'} -> {new or 'missing'} (changed)"
        print(f"Declared source {json.dumps(path)}: {detail}")
    if not same_sources:
        print("Declared test sources changed. These reports cannot establish a repair.")
    if before.keys() != after.keys():
        print("Test coverage changed. Review added or missing cases.")
    for key in sorted(before.keys() | after.keys()):
        old, new = before.get(key, "absent"), after.get(key, "absent")
        if old == new == "passed":
            continue
        label = " (recovered)" if comparable and (old, new) == ("failed", "passed") else ""
        print(f"{case_label(key)}: {old} -> {new}{label}")
    print(SCOPE)
    return 0 if eligible else 100


def main():
    parser = Arguments(description=__doc__, epilog=SCOPE)
    parser.add_argument("before", type=Path, metavar="BEFORE_XML")
    parser.add_argument("after", type=Path, metavar="AFTER_XML")
    parser.add_argument("--before-root", required=True, type=Path)
    parser.add_argument("--after-root", required=True, type=Path)
    parser.add_argument("--test-source", required=True, type=Path, action="append", metavar="RELATIVE_PATH")
    args = parser.parse_args()
    try:
        return review(args)
    except Invalid as exc:
        print("INVALID: " + str(exc), file=sys.stderr)
    except (OSError, ET.ParseError, RuntimeError, LookupError, ValueError):
        print("INVALID: could not read the declared files or parse the JUnit XML.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
