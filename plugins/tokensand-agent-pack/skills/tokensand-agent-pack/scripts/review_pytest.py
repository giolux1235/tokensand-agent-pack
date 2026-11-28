#!/usr/bin/env python3
"""Review two local pytest JUnit reports against test sources and their pytest controls."""
import argparse
import configparser
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
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
        self.exit(1, "INVALID: check the requested paths, test selection and checkout roots.\n")


ATTACHMENTS = {"properties", "system-out", "system-err"}
OUTCOMES = {"failure": "failed", "error": "error", "skipped": "skipped"}
LIMIT = 16 * 1024 * 1024
PYTEST_CONTROLS = ("conftest.py", "pytest.toml", ".pytest.toml", "pytest.ini", ".pytest.ini",
                   "pyproject.toml", "tox.ini", "setup.cfg")
SHARED_CONFIGS = {"pyproject.toml", "tox.ini", "setup.cfg"}
SCOPE = ("User-supplied report/checkout pairing. Source hashes describe the files now. "
         "Shared configs are marked when only pytest settings were compared. Declare imported helpers and data separately. "
         "JUnit can omit non-strict XPASS; check the native pytest terminal outcome. "
         "This review does not authorize a release.")


def read_report(path, *, vitest_rows=False):
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
            if vitest_rows:
                # Native Vitest independently supplies identity and source location.
                # Keep every JUnit row only to cross-check its named outcome multiset.
                key += (len(rows),)
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


def pytest_settings(name, data):
    if name == "pyproject.toml":
        import tomllib  # Without the Python 3.11+ parser, the caller compares the full file.
        doc = tomllib.loads(data.decode())
        return {"pytest": doc.get("tool", {}).get("pytest"),
                "pytest11": doc.get("project", {}).get("entry-points", {}).get("pytest11")}
    config = configparser.RawConfigParser()
    config.optionxform = str
    config.read_string(data.decode())
    sections = {s: dict(config[s]) for s in ("pytest", "tool:pytest") if s in config}
    if name == "setup.cfg" and config.has_option("options.entry_points", "pytest11"):
        sections["pytest11"] = config.get("options.entry_points", "pytest11")
    return sections


def source_sha(root, relative, compare_settings=False):
    entry = root / relative
    path = entry.resolve()
    if not path.is_relative_to(root):
        raise Invalid("declared test source escapes its checkout")
    if not path.exists():
        if entry.is_symlink():
            raise Invalid("test source has a broken symbolic link")
        return None
    if not path.is_file():
        raise Invalid("declared test source must be a regular file")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        if compare_settings and relative.name in SHARED_CONFIGS:
            data = stream.read(1024 * 1024 + 1)
            if len(data) <= 1024 * 1024:
                try:
                    settings = pytest_settings(relative.name, data)
                    payload = json.dumps(settings, sort_keys=True, allow_nan=False).encode()
                    return "pytest-settings/" + hashlib.sha256(payload).hexdigest()
                except (ImportError, ValueError, TypeError, AttributeError, RecursionError, configparser.Error):
                    pass  # Unreadable configuration must still be compared byte for byte.
            stream.seek(0)
        for block in iter(lambda: stream.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def case_label(key, *, native=False):
    group, name = key
    stem, param, _ = name.partition("[")
    fingerprint = hashlib.sha256(json.dumps(key, ensure_ascii=True).encode()).hexdigest()[:12]
    safe_group = re.fullmatch(r"[A-Za-z0-9_.]*", group)
    if native:
        safe_group = (not Path(group).is_absolute() and ".." not in Path(group).parts
                      and re.fullmatch(r"[A-Za-z0-9_./-]*(?:::[A-Za-z0-9_]+)*", group))
    if not safe_group or not re.fullmatch(r"[A-Za-z0-9_]+", stem):
        return "case#" + fingerprint
    return (group + "::" if group else "") + stem + ("[id#" + fingerprint + "]" if param else "")


def test_sources(roots, paths, active_configs=()):
    declared = dict.fromkeys(paths)
    controls = set(active_configs)
    for relative in declared:
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise Invalid("use a checkout-relative test source path")
        controls.update(parent / name for parent in relative.parents for name in PYTEST_CONTROLS)
    sources = []
    for relative in [*declared, *sorted(controls.difference(declared))]:
        old, new = (source_sha(root, relative, compare_settings=relative not in declared) for root in roots)
        if old is None and new is None:
            if relative in declared:
                raise Invalid("declared test source missing from both checkouts")
            continue
        sources.append((relative, old, new, relative in declared))
    return sources


def review_status(before, after, sources, issues):
    same_sources = all(old == new for _, old, new, _ in sources)
    comparable = same_sources and before.keys() == after.keys() and not issues
    eligible = (comparable
                and all(state in {"passed", "failed"} for state in before.values())
                and all(state == "passed" for state in after.values()))
    return same_sources, comparable, eligible


def verdict(before, after, sources, *, pytest_exits=None, issues=(), defining_sources=(), fixture_sources=()):
    native = pytest_exits is not None
    same_sources, comparable, eligible = review_status(before, after, sources, issues)
    mode = "native pytest review" if native else "pytest JUnit report review (XML outcomes only)"
    print(("PASS" if eligible else "HOLD") + " — " + mode)
    for issue in issues:
        print(issue)
    old_passes, new_passes = list(before.values()).count("passed"), list(after.values()).count("passed")
    if native:
        print(f"Ordinary passes were {old_passes}/{len(before)} before and {new_passes}/{len(after)} after. "
              f"Pytest exited {pytest_exits[0]} before and {pytest_exits[1]} after.")
    else:
        print(f"Baseline passed {old_passes}/{len(before)}; candidate passed {new_passes}/{len(after)}.")
    for path, old, new, is_declared in sources:
        detail = old if old == new else f"{old or 'missing'} -> {new or 'missing'} (changed)"
        label = "Declared source" if is_declared else ("Pytest control" if native else "Ancestor control")
        if path in defining_sources:
            label = "Defining test source"
        if path in fixture_sources and not is_declared:
            label = "Fixture source"
        print(f"{label} {json.dumps(str(path))}: {detail}")
    if not same_sources:
        print("Test sources or fixture/config files changed. Rerun both implementations with the same controls.")
    if before.keys() != after.keys():
        print("Test coverage changed. Review added or missing cases.")
    for key in sorted(before.keys() | after.keys()):
        old, new = before.get(key, "absent"), after.get(key, "absent")
        if old == new == "passed":
            continue
        label = " (recovered)" if comparable and (old, new) == ("failed", "passed") else ""
        print(f"{case_label(key, native=native)}: {old} -> {new}{label}")
    if native:
        outcomes = set(before.values()) | set(after.values())
        if "xfailed-before-call" in outcomes:
            print("An expected failure was reported during setup. Its test body did not run.")
        if "xpassed-strict" in outcomes:
            print("A strict unexpected pass fails the native pytest policy.")
        if outcomes & {"xpassed", "xpassed-strict"}:
            print("An expected failure passed its assertion. Review the expectation and rerun both revisions under the same policy.")
        print("Local runs compare the selected cases, defining tests and exercised checkout-local fixture files. Pytest checks imports from the other checkout "
              "and module origins in the root and selected src trees. Declare other assertion helpers and fixture data with --test-source.")
    else:
        print(SCOPE + " With both checkouts, use --run for native pytest outcomes.")
    return 0 if eligible else 100


def checkout_roots(args):
    roots = [path.resolve(strict=True) for path in (args.before_root, args.after_root)]
    if not all(root.is_dir() for root in roots):
        raise Invalid("checkout roots must be directories")
    return roots


def read_review(args):
    before, after = read_report(args.before), read_report(args.after)
    return verdict(before, after, test_sources(checkout_roots(args), args.test_source))


def native_interpreter(root, override):
    if override:
        path = Path(override)
        return path if path.is_absolute() else root / path
    venv = root / ".venv"
    if venv.is_dir():
        return venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    return Path(sys.executable)


def interpreter_environment(interpreter, root, peer, source_dirs):
    env = os.environ.copy()
    old_venv = env.pop("VIRTUAL_ENV", None)
    directories = os.get_exec_path(env)
    if old_venv:
        previous = Path(old_venv)
        directories = [directory for directory in directories
                       if Path(directory) not in (previous / "bin", previous / "Scripts")]
    env["PATH"] = os.pathsep.join([str(interpreter.parent), *directories])
    venv = interpreter.parent.parent
    if (venv / "pyvenv.cfg").is_file():
        env["VIRTUAL_ENV"] = str(venv)
        env.pop("PYTHONHOME", None)
    # Bind ordinary src-layout imports for this interpreter and its Python children.
    # A shared editable install or PYTHONPATH can otherwise load one revision twice.
    paths = env["PYTHONPATH"].split(os.pathsep) if "PYTHONPATH" in env else []
    peer_paths = {peer, peer / "src"}
    paths = [entry for entry in paths if (root / entry).resolve() not in peer_paths]
    source = root / "src"
    if source.is_dir() and not (source / "__init__.py").is_file():
        paths.insert(0, str(source))
    if paths:
        env["PYTHONPATH"] = os.pathsep.join(paths)
    else:
        env.pop("PYTHONPATH", None)
    env["TOKENSAND_PYTEST_PEER_ROOT"] = str(peer)
    env["TOKENSAND_PYTEST_SOURCE_DIRS"] = json.dumps(source_dirs)
    return env


NATIVE_ISSUES = {
    "runxfail": "--runxfail disabled expected-failure handling. Rerun with the project's normal marker policy.",
    "distributed": "Distributed coverage is unavailable. Use a single pytest worker for this review.",
    "disabled_assertions": "Python optimization can remove assertions in tests or imported helpers, even with pytest "
                           "assertion rewriting. Unset PYTHONOPTIMIZE and rerun both checkouts with ordinary Python.",
    "foreign_import": "A project module loaded from outside this checkout’s source. Remove shared project paths "
                      "from pytest configuration or install the project with python -m pip install -e . in each checkout’s "
                      "own .venv, then rerun.",
    "external_config": "The active pytest configuration is outside its checkout.",
    "deselected": "Pytest deselected cases. Check the selection and the project’s default options.",
    "collection_error": "Pytest could not collect the target. Run the target directly for its collection error.",
    "empty": "No selected cases were collected.",
    "interrupted": "Pytest was interrupted.",
    "session_error": "Pytest session policy and case outcomes did not agree.",
    "duplicate": "A test ran more than once. Review the individual attempts.",
    "incomplete": "A complete run was unavailable for every selected case.",
    "unsupported": "The environment produced an unsupported pytest outcome or configuration.",
    "multiple_outcomes": "More than one phase of a case reported an outcome. Inspect the native run.",
}


def native_run(root, peer, interpreter, targets, destination, arm, source_dirs, *, keep_failures=False):
    if not interpreter.is_file():
        raise Invalid(f"The {arm} interpreter is missing. Set --{arm}-python to this checkout’s Python with pytest installed.")
    helper = Path(__file__).with_name("tokensand_pytest_definitions.py").resolve(strict=True)
    # The child uses the project's own environment. Test output and marker reasons stay private.
    definitions_file = destination.with_suffix(".definitions.json")
    env = interpreter_environment(interpreter, root, peer, source_dirs)
    detail_args = ["--tokensand-private-failures"] if keep_failures else []
    result = subprocess.run([str(interpreter), str(helper), str(destination), *detail_args, *targets],
                            cwd=root, env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not destination.is_file():
        raise Invalid(f"The {arm} run produced no native result. Check --{arm}-python and run the selection locally with python -m pytest.")
    if destination.stat().st_size > LIMIT:
        raise Invalid(f"The {arm} native result exceeds 16 MiB.")
    record = json.loads(destination.read_text(encoding="utf-8"))
    if (not isinstance(record, dict) or record.get("version") != 1 or record.get("exit") != result.returncode
            or not isinstance(record.get("cases"), list) or not isinstance(record.get("issues"), list)):
        raise Invalid(f"The {arm} native result is incomplete.")
    issues = []
    for issue in record["issues"]:
        if not isinstance(issue, str) or issue not in NATIVE_ISSUES:
            raise Invalid(f"The {arm} run has an unknown session status.")
        issues.append(f"{arm}: {NATIVE_ISSUES[issue]}")
    if result.returncode not in (0, 1) or not record["cases"]:
        raise Invalid(f"The {arm} selection produced no usable decision (pytest exit {result.returncode}). "
                      + (" ".join(issues) or "Run the same target with python -m pytest in that checkout."))
    if "active_config" not in record or "active_config_sha256" not in record:
        raise Invalid(f"The {arm} run omitted its active configuration evidence.")
    config, digest = record["active_config"], record["active_config_sha256"]
    if config is not None:
        if (not isinstance(config, str) or not config or Path(config).is_absolute() or ".." in Path(config).parts
                or not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest)):
            raise Invalid(f"The {arm} run has invalid pytest configuration evidence.")
        config = Path(config)
    elif digest is not None:
        raise Invalid(f"The {arm} configuration digest has no corresponding file.")
    states = {"passed", "failed", "error", "skipped", "xfailed", "xfailed-before-call", "xpassed", "xpassed-strict"}
    cases = {}
    for row in record["cases"]:
        if not isinstance(row, list) or len(row) != 2 or not all(isinstance(v, str) for v in row):
            raise Invalid(f"The {arm} result contains an invalid case.")
        nodeid, state = row
        if not nodeid or state not in states:
            raise Invalid(f"The {arm} result contains an unsupported native outcome.")
        # Partition before the parameters: a parameter value may itself contain "::".
        stem, bracket, param = nodeid.partition("[")
        group, _, name = stem.rpartition("::")
        key = (group, name + bracket + param)
        if key in cases:
            raise Invalid(f"The {arm} result contains duplicate case identities.")
        cases[key] = state
    if not definitions_file.is_file() or definitions_file.stat().st_size > LIMIT:
        raise Invalid(f"The {arm} run omitted usable test definitions.")
    evidence = json.loads(definitions_file.read_text(encoding="utf-8"))
    if (not isinstance(evidence, dict) or not isinstance(evidence.get("cases"), dict)
            or not isinstance(evidence.get("sources"), dict)):
        raise Invalid(f"The {arm} run has invalid test definitions.")
    definitions = evidence["cases"]
    hashes = {}
    for name, checksum in evidence["sources"].items():
        path = Path(name)
        if (not path.parts or path.is_absolute() or ".." in path.parts or not isinstance(checksum, str)
                or not re.fullmatch("[0-9a-f]{64}", checksum)):
            raise Invalid(f"The {arm} run has invalid test definitions.")
        hashes[path] = checksum
    for nodeid, _ in record["cases"]:
        locations = definitions.get(nodeid)
        if not isinstance(locations, list) or not locations:
            issues.append(f"{arm}: Could not identify a checkout-local Python test definition for every case.")
        elif any(not isinstance(row, list) or len(row) != 2 or not isinstance(row[0], str)
                 or Path(row[0]) not in hashes or type(row[1]) is not int or row[1] < 1 for row in locations):
            raise Invalid(f"The {arm} run has invalid test definitions.")
    files, bindings = evidence.get("fixtureFiles"), evidence.get("caseFixtures")
    if (not isinstance(files, list) or any(not isinstance(name, str) or Path(name) not in hashes for name in files)
            or type(evidence.get("unresolvedFixture")) is not bool or not isinstance(bindings, dict)
            or any(not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest) for digest in bindings.values())):
        raise Invalid(f"The {arm} run omitted valid fixture evidence.")
    if bindings.keys() != definitions.keys():
        issues.append(f"{arm}: Fixture evidence is missing for a selected case.")
    if evidence["unresolvedFixture"]:
        issues.append(f"{arm}: A fixture definition could not be verified. Review its source before accepting the repair.")
    return cases, issues, result.returncode, config, digest, definitions, hashes, (set(map(Path, files)), bindings)


def read_failure_details(path, cases, arm):
    invalid = Invalid(f"The {arm} private failure details are incomplete or invalid.")
    if path.is_symlink() or not path.is_file() or path.stat().st_size > LIMIT:
        raise invalid
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) != {"failures", "omitted"}:
        raise invalid
    rows, omitted = data["failures"], data["omitted"]
    if (not isinstance(rows, list) or len(rows) > 100 or type(omitted) is not int or omitted < 0
            or (omitted and len(rows) != 100)):
        raise invalid
    expected = {}
    for (group, name), state in cases.items():
        if state in {"failed", "error", "xpassed-strict"}:
            node = f"{group}::{name}" if group else name
            expected[hashlib.sha256(node.encode()).hexdigest()] = (node, state)
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"caseHash", "nodeid", "phase", "message", "truncated"}:
            raise invalid
        fingerprint, phase = row.pop("caseHash"), row["phase"]
        if (not isinstance(fingerprint, str) or fingerprint not in expected or phase not in ("call", "setup", "teardown")
                or (fingerprint, phase) in seen or not isinstance(row["message"], str) or len(row["message"]) > 8192
                or not isinstance(row["nodeid"], str) or not 0 < len(row["nodeid"]) <= 4096
                or type(row["truncated"]) is not bool):
            raise invalid
        node, state = expected[fingerprint]
        raw_node = re.sub(r"\[parameter-sha256=[0-9a-f]{64}\]$", "", node)
        if row["nodeid"] not in (node[:4096], raw_node[:4096]) or (phase != "call" and state != "error"):
            raise invalid
        seen.add((fingerprint, phase))
    if len(expected.keys() - {key for key, _ in seen}) > omitted or len(rows) + omitted > 3 * len(expected):
        raise invalid
    return data


def write_failure_report(path, reports):
    # Publish a complete pair exclusively, even if two reviews chose the same path.
    with tempfile.TemporaryDirectory(prefix=".tokensand-report-", dir=path.parent) as staging:
        staged = Path(staging, "report.json")
        with os.fdopen(os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as stream:
            json.dump({"schema": "tokensand-pytest-failures-v1", **reports}, stream, indent=2)
            stream.write("\n")
        try:
            os.link(staged, path)
        except FileExistsError as exc:
            raise Invalid("Choose a new private failure report path. The selected path already exists.") from exc


def run_review(args):
    roots = checkout_roots(args)
    report_path = args.failure_report
    if report_path and (os.path.lexists(report_path) or not report_path.parent.is_dir()):
        raise Invalid("Choose a new private failure report path in an existing directory.")
    if roots[0] == roots[1]:
        raise Invalid("Choose separate earlier and later checkouts.")
    files = []
    for target in args.targets:
        name = target.split("::", 1)[0]
        path = Path(name)
        if not name or name.startswith(("-", "@")) or path.is_absolute() or ".." in path.parts:
            raise Invalid("Select a checkout-relative test file or pytest node ID.")
        if any(not (root / path).is_file() or not (root / path).resolve().is_relative_to(root) for root in roots):
            raise Invalid("A selected file is missing or outside its checkout. Select a file present in both revisions.")
        files.append(path)
    declared = files + args.test_source
    sources = test_sources(roots, declared)
    # Selected tests and declared helpers identify service source trees in a monorepo.
    source_dirs = sorted({str(parent / "src") for path in declared for parent in path.parents
                          if any((root / parent / "src").is_dir() for root in roots)})
    with tempfile.TemporaryDirectory(prefix="tokensand-pytest-") as temp:
        before, first_issues, before_exit, before_config, before_digest, first_definitions, first_hashes, (first_fixture_files, first_bindings) = native_run(
            roots[0], roots[1], native_interpreter(roots[0], args.before_python),
            args.targets, Path(temp, "before.json"), "before", source_dirs, keep_failures=bool(report_path))
        after, last_issues, after_exit, after_config, after_digest, last_definitions, last_hashes, (last_fixture_files, last_bindings) = native_run(
            roots[1], roots[0], native_interpreter(roots[1], args.after_python),
            args.targets, Path(temp, "after.json"), "after", source_dirs, keep_failures=bool(report_path))
        if report_path:
            reports = {arm: read_failure_details(Path(temp, arm + ".failures.json"), cases, arm)
                       for arm, cases in (("before", before), ("after", after))}
    issues = list(dict.fromkeys(first_issues + last_issues))
    if any(first_bindings[case] != last_bindings[case] for case in first_bindings.keys() & last_bindings.keys()):
        issues.append("A case used different checkout-local fixture definitions. Verify both revisions with the same fixtures.")
    for nodeid in first_definitions.keys() & last_definitions.keys():
        if first_definitions[nodeid] != last_definitions[nodeid]:
            issues.append("A selected test resolved to a different definition. Rerun both implementations with the same test definition.")
            break
    if sources != test_sources(roots, files + args.test_source):
        issues.append("A declared test source changed during execution. Review the changes and rerun.")
    if before_config != after_config:
        issues.append("The checkouts used different active pytest configurations.")
    configs = [path for path in (before_config, after_config) if path is not None]
    for root, config, digest in zip(roots, (before_config, after_config), (before_digest, after_digest)):
        if config is not None and source_sha(root, config) != digest:
            issues.append("The active pytest configuration changed during execution.")
    sources = test_sources(roots, declared, configs)
    for root, checksums in zip(roots, (first_hashes, last_hashes)):
        if any(source_sha(root, path) != digest for path, digest in checksums.items()):
            issues.append("A defining test source changed during execution. Review the change and rerun.")
    other_definitions = (first_hashes.keys() | last_hashes.keys()) - {path for path, *_ in sources}
    for path in sorted(other_definitions):
        sources.append((path, first_hashes.get(path), last_hashes.get(path), False))
    if report_path:
        write_failure_report(report_path, reports)
    return verdict(before, after, sources, pytest_exits=(before_exit, after_exit), issues=issues,
                   defining_sources=other_definitions, fixture_sources=first_fixture_files | last_fixture_files)


def main():
    if sys.argv[1:2] == ["--run"]:
        parser = Arguments(description="Run the same existing pytest cases in two trusted checkouts.")
        parser.add_argument("--before-root", required=True, type=Path)
        parser.add_argument("--after-root", default=Path.cwd(), type=Path)
        parser.add_argument("--before-python", metavar="INTERPRETER", help="absolute path or path relative to the earlier checkout")
        parser.add_argument("--after-python", metavar="INTERPRETER", help="absolute path or path relative to the later checkout")
        parser.add_argument("--test-source", action="append", type=Path, default=[], metavar="RELATIVE_PATH")
        parser.add_argument("--failure-report", type=Path, metavar="NEW_FILE",
                            help="save original pytest failure messages and case IDs locally; can contain private test data")
        parser.add_argument("targets", nargs="+", metavar="FILE[::NODE]")
        return execute(run_review, parser.parse_args(sys.argv[2:]))
    parser = Arguments(description=__doc__, epilog="Use --run --help to review native pytest outcomes from two trusted checkouts.")
    parser.add_argument("before", type=Path, metavar="BEFORE_XML")
    parser.add_argument("after", type=Path, metavar="AFTER_XML")
    parser.add_argument("--before-root", required=True, type=Path)
    parser.add_argument("--after-root", required=True, type=Path)
    parser.add_argument("--test-source", required=True, type=Path, action="append", metavar="RELATIVE_PATH")
    return execute(read_review, parser.parse_args())


def execute(action, args):
    try:
        return action(args)
    except Invalid as exc:
        print("INVALID: " + str(exc), file=sys.stderr)
    except (OSError, ET.ParseError, RuntimeError, LookupError, ValueError):
        print("INVALID: could not read the selected checkouts, interpreters or test evidence.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
