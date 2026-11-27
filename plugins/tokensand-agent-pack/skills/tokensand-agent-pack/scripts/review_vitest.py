#!/usr/bin/env python3
"""Run selected, trusted Vitest 4 test files in two local checkouts."""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from review_pytest import Arguments, Invalid, execute, read_report, review_status, source_sha

REPORTER = Path(__file__).resolve().with_name("review_vitest_reporter.mjs")
LOCKS = ("package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lock", "bun.lockb")


def relative_path(value):
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0].startswith("-"):
        raise argparse.ArgumentTypeError("provide a checkout-relative file")
    return path


def capture(root, args, directory):
    package = root / "node_modules/vitest"
    if not (package / "vitest.mjs").is_file():
        raise Invalid("install this checkout’s locked development dependencies; local Vitest 4 is required")
    version = json.loads((package / "package.json").read_text())["version"]
    if not version.startswith("4.") or not shutil.which("node"):
        raise Invalid("this command supports a locally installed Vitest 4 with Node on PATH")
    declared = [*args.files, *args.test_source]
    if args.config:
        declared.append(args.config)
    controls = {path: source_sha(root, path) for path in [*declared, *map(Path, LOCKS)]}
    if any(controls[path] is None for path in declared):
        raise Invalid("a selected test, config or declared fixture is missing; use paths in both checkouts")

    xml, receipt = directory / "results.xml", directory / "scope.json"
    env = {key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR", "TEMP", "SystemRoot", "USERPROFILE", "LANG")
           if key in os.environ}
    env.update(CI="1", NO_COLOR="1", TOKENSAND_VITEST_RECEIPT=str(receipt))
    command = ["node", str(package / "vitest.mjs"), "run", "--no-update", "--allowOnly=false", "--includeTaskLocation",
               "--reporter=junit", "--reporter=" + str(REPORTER), "--outputFile.junit=" + str(xml)]
    if args.config:
        command += ["--config", str(args.config)]
    command.extend(str(path) for path in args.files)
    run = subprocess.run(command, cwd=root, env=env, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not xml.is_file() or not receipt.is_file():
        raise Invalid("Vitest did not produce a complete review; run the selected files with the checkout’s normal Vitest command")
    if receipt.stat().st_size > 1024 * 1024:
        raise Invalid("Vitest control receipt is too large")
    data = json.loads(receipt.read_text())
    wanted = sorted(str(path) for path in args.files)
    if sorted(data["modules"]) != wanted:
        raise Invalid("Vitest collected a different set of files; select exact test files in one project")
    if data["unscored"]:
        raise Invalid("Vitest reported a suite or worker error; inspect the checkout’s native run")
    rows = read_report(xml, vitest_rows=True)
    cases = {}
    for row in data["cases"]:
        file, title, task_id, line, column, outcome = row
        key = (file, title, task_id, line, column)
        if not task_id or key in cases:
            raise Invalid("ambiguous native Vitest identity")
        cases[key] = outcome
    if (Counter((group, title, state) for (group, title, _), state in rows.items()) !=
            Counter((key[0], key[1], state) for key, state in cases.items())):
        raise Invalid("Vitest native cases disagree with its JUnit output; inspect the local suite")
    scored_files = {key[0] for key in cases}
    if scored_files - set(wanted):
        raise Invalid("Vitest reported named cases outside the selected files")
    any_failed = any(state in ("failed", "error") for state in cases.values())
    issues = []
    if set(wanted) - scored_files:
        issues.append("A selected file contributed no named cases.")
    if run.returncode != int(any_failed):
        issues.append("Vitest exited inconsistently with the named cases or reported a suite or worker error.")
    if data["nonordinary"]:
        issues.append("Vitest reported an expected failure, exclusive or retried case, or a custom snapshot handler.")
    for key, digest in data["controls"].items():
        path = relative_path(key)
        if path in controls and controls[path] != digest:
            issues.append("An evaluation input changed during collection.")
        controls[path] = digest
    if any(source_sha(root, path) != digest for path, digest in controls.items()):
        issues.append("An evaluation input changed during execution.")
    return cases, controls, issues, version, run.returncode


def review(args):
    roots = [path.resolve(strict=True) for path in (args.before_root, args.after_root)]
    if roots[0] == roots[1] or len(set(args.files)) != len(args.files):
        raise Invalid("use two distinct checkouts and each selected test file once")
    with tempfile.TemporaryDirectory(prefix="tokensand-vitest-") as work:
        runs = []
        for name, root in zip(("before", "after"), roots):
            directory = Path(work) / name
            directory.mkdir(mode=0o700)
            runs.append(capture(root, args, directory))
    (before, old, first, v1, e1), (after, new, last, v2, e2) = runs
    issues = list(dict.fromkeys(first + last))
    if v1 != v2:
        issues.append("The installed Vitest versions differ.")
    if old.keys() != new.keys():
        issues.append("The two runs loaded different control files.")
    sources = [(path, old.get(path), new.get(path), True) for path in sorted(old.keys() | new.keys())]
    _, comparable, eligible = review_status(before, after, sources, issues)
    recovered = {key for key in before.keys() & after.keys() if before[key] == "failed" and after[key] == "passed"}
    verified = eligible and bool(recovered)
    print(("VERIFIED REPAIR" if verified else "HOLD") + " — selected local Vitest cases")
    print(f"Ordinary passes: {list(before.values()).count('passed')}/{len(before)} before, "
          f"{list(after.values()).count('passed')}/{len(after)} after. Vitest exits: {e1}, {e2}.")
    for issue in issues:
        print(issue)
    for path, lhs, rhs, _ in sources:
        if lhs != rhs:
            print("Changed test or control: " + json.dumps(path.as_posix(), ensure_ascii=True))
    if before.keys() != after.keys():
        print("The named test roster changed.")
    if not recovered:
        print("No previously failing named case was observed passing.")
    for key in sorted(before.keys() | after.keys()):
        was, now = before.get(key, "absent"), after.get(key, "absent")
        if was == now == "passed":
            continue
        fingerprint = hashlib.sha256(json.dumps(key, ensure_ascii=True).encode()).hexdigest()[:12]
        detail = [key[0] + (f":{key[3]}:{key[4]}" if key[3] else ""), key[1]]
        label = json.dumps(detail, ensure_ascii=True)[:500] if args.show_names else "case#" + fingerprint
        print(f"{label}: {was} -> {now}" + (" (recovered)" if comparable and key in recovered else ""))
    if not args.show_names:
        print("Use --show-names locally to display the test file and full case name.")
    print("Scope: selected tests, active config/setup and standard snapshots. Declare imported helpers and data with --test-source.")
    return 0 if verified else 100


if __name__ == "__main__":
    parser = Arguments(description=__doc__)
    parser.add_argument("--before-root", required=True, type=Path)
    parser.add_argument("--after-root", default=Path.cwd(), type=Path)
    parser.add_argument("--config", type=relative_path, help="explicit Vitest config relative to each checkout")
    parser.add_argument("--test-source", type=relative_path, action="append", default=[], metavar="FILE")
    parser.add_argument("--show-names", action="store_true", help="print local testcase names; these can contain private fixture values")
    parser.add_argument("files", nargs="+", type=relative_path, metavar="TEST_FILE")
    sys.exit(execute(review, parser.parse_args()))
