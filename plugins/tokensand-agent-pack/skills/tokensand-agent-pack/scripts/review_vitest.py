#!/usr/bin/env python3
"""Review proposed repairs against a fresh baseline from the same trusted Vitest 4 tests."""
import argparse
from collections import Counter
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from xml.etree.ElementTree import ParseError

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
    if args.replay_original_tests:
        home = directory / "home"
        home.mkdir(mode=0o700)
        env.update(HOME=str(home), USERPROFILE=str(home), TMPDIR=str(home), TEMP=str(home))
    env.update(CI="1", NO_COLOR="1", TOKENSAND_VITEST_RECEIPT=str(receipt))
    command = ["node", str(package / "vitest.mjs"), "run", "--no-cache", "--no-update", "--allowOnly=false", "--includeTaskLocation",
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
    if data["unbound"]:
        issues.append("An import or file snapshot could not be bound to this checkout.")
    if data["nonordinary"]:
        issues.append("Vitest reported an expected failure, exclusive or retried case, or a custom snapshot handler.")
    for key, digest in data["controls"].items():
        path = relative_path(key)
        if path in controls and controls[path] != digest:
            issues.append("An evaluation input changed during collection.")
        controls[path] = digest
    if any(source_sha(root, path) != digest for path, digest in controls.items()):
        issues.append("An evaluation input changed during execution.")
    protected = {*declared, *map(Path, LOCKS), *map(relative_path, data["protectedFiles"])}
    return cases, controls, protected, issues, version, run.returncode


def implementation_files(args, loaded, protected):
    implementation = set(args.implementation)
    if not implementation:
        # The adjacent module with the same name as a .test or .spec file is
        # the common Vitest layout. All other changed local imports need a role.
        for target in args.files:
            name = re.sub(r"\.(test|spec)\.[^.]+$", "", target.name)
            adjacent = {path for path in loaded - protected
                        if path.parent == target.parent and path.stem == name}
            if len(adjacent) == 1:
                implementation.update(adjacent)
    if implementation & protected or implementation - loaded:
        raise Invalid("implementation files must be reached local modules, separate from tests and controls")
    return implementation


def case_name(key, show_names):
    fingerprint = hashlib.sha256(json.dumps(key, ensure_ascii=True).encode()).hexdigest()[:12]
    detail = [key[0] + (f":{key[3]}:{key[4]}" if key[3] else ""), key[1]]
    return json.dumps(detail, ensure_ascii=True)[:500] if show_names else "case#" + fingerprint


def compare(args, earlier, later, extra_issues=()):
    before, old, baseline_controls, first, v1, e1 = earlier
    after, new, candidate_controls, last, v2, e2 = later
    issues = list(dict.fromkeys([*first, *last, *extra_issues]))
    if v1 != v2:
        issues.append("The installed Vitest versions differ.")
    protected = baseline_controls | candidate_controls
    loaded = old.keys() | new.keys()
    implementation = implementation_files(args, loaded, protected)
    one_sided = implementation & (old.keys() ^ new.keys())
    changed_impl = {path for path in implementation - one_sided if old[path] != new[path]}
    if not implementation:
        issues.append("No implementation was identified. Use --implementation FILE for the intended service module.")
    elif one_sided:
        for path in sorted(one_sided):
            checkout = "baseline" if path in old else "candidate"
            issues.append(f"Implementation reached only in the {checkout}: " + json.dumps(path.as_posix(), ensure_ascii=True) +
                          ". Verify both test runs exercise the same service module.")
    elif not changed_impl:
        known = ", ".join(json.dumps(path.as_posix(), ensure_ascii=True) for path in sorted(implementation))
        issues.append("Implementation unchanged: " + known + ".")
    if (old.keys() ^ new.keys()) - implementation:
        issues.append("The runs loaded different tests, controls or undeclared local imports.")
    sources = [(path, old.get(path), new.get(path), True) for path in sorted(loaded - implementation)]
    _, comparable, eligible = review_status(before, after, sources, issues)
    recovered = {key for key in before.keys() & after.keys() if before[key] == "failed" and after[key] == "passed"}
    verified = eligible and bool(recovered)
    print(("VERIFIED REPAIR" if verified else "HOLD") + " — selected local Vitest cases")
    print(f"Vitest-reported passes: {list(before.values()).count('passed')}/{len(before)} before, "
          f"{list(after.values()).count('passed')}/{len(after)} after. Vitest exits: {e1}, {e2}.")
    for path in sorted(changed_impl):
        print("Changed implementation: " + json.dumps(path.as_posix(), ensure_ascii=True))
    for issue in issues:
        print(issue)
    for path, lhs, rhs, _ in sources:
        if lhs != rhs:
            print("Changed test, control or local import: " + json.dumps(path.as_posix(), ensure_ascii=True))
    if implementation and not (changed_impl or one_sided) and any(old.get(path) != new.get(path) for path in args.files):
        print("The selected test changed while its identified implementation stayed identical. Keep the original assertions and fix the implementation.")
    if before.keys() != after.keys():
        print("The named test roster changed.")
    if not recovered:
        print("No previously failing named case was observed passing.")
    for key in sorted(before.keys() | after.keys()):
        was, now = before.get(key, "absent"), after.get(key, "absent")
        if was == now == "passed":
            continue
        label = case_name(key, args.show_names)
        transition = ""
        if key in recovered:
            transition = " (recovered)" if comparable else " (recovery unverified)"
        print(f"{label}: {was} -> {now}" + transition)
    return 0 if verified else 100


def review(args):
    before = args.before_root.resolve(strict=True)
    proposals = args.after_root or [Path.cwd()]
    if len(set(args.files)) != len(args.files):
        raise Invalid("select each test file once")
    if len(proposals) == 1 and proposals[0].resolve(strict=True) == before:
        raise Invalid("use two distinct checkouts")
    with tempfile.TemporaryDirectory(prefix="tokensand-vitest-") as work:
        base = Path(work) / "before"
        base.mkdir(mode=0o700)
        if args.replay_original_tests:
            from review_vitest_replay import UNKNOWN_INPUT, original_result, snapshot, tracked_changes
            before_tree = snapshot(before)
        earlier = capture(before, args, base)
        if not args.replay_original_tests:
            from review_vitest_replay import filesystem_data
            before_data = filesystem_data(before, earlier[1])
        unavailable = set()
        seen = {before}
        decisions = []
        for index, proposal in enumerate(proposals, 1):
            label = f"Proposal {index} " + json.dumps(proposal.name or ".", ensure_ascii=True)
            if len(proposals) > 1:
                print(label, flush=True)

            def review_proposal(_):
                root = proposal.resolve(strict=True)
                if root in seen:
                    raise Invalid("select a distinct candidate checkout once; the baseline cannot be a candidate")
                seen.add(root)
                directory = Path(work) / str(index)
                directory.mkdir(mode=0o700)
                if args.replay_original_tests:
                    candidate_tree = snapshot(root)
                later = capture(root, args, directory)
                if not args.replay_original_tests:
                    extra = []
                    if before_data is not None:
                        after_data = filesystem_data(root, later[1])
                        if after_data is not None:
                            data_drift = sorted(p for p in before_data.keys() | after_data.keys()
                                                if before_data.get(p) != after_data.get(p))
                            if data_drift:
                                name = json.dumps(data_drift[0].as_posix(), ensure_ascii=True)
                                extra.append("The suite uses filesystem access and an unbound tracked data file changed: " +
                                             name + ". Review its role; declare expected data with --test-source FILE.")
                    return compare(args, earlier, later, extra)
                implementation = implementation_files(args, earlier[1].keys() | later[1].keys(), earlier[2] | later[2])
                extra = []
                if not before_tree.problem and not candidate_tree.problem:
                    known = implementation | set(args.files) | set(args.test_source)
                    if tracked_changes(before_tree, candidate_tree) - known:
                        extra.append(UNKNOWN_INPUT)
                print("Submitted tests")
                status = compare(args, earlier, later, extra)
                try:
                    with tempfile.TemporaryDirectory(prefix="original-", dir=directory) as scratch:
                        mode, after = original_result(args, before, root, before_tree, candidate_tree,
                                                      earlier, later, Path(scratch), capture, implementation)
                    failed = [key for key, state in after.items() if state == "failed"]
                    print(f"Original tests ({mode}): {len(after)-len(failed)}/{len(after)} passed, {len(failed)} failed.")
                    for key in sorted(failed):
                        print("  Original failure: " + case_name(key, args.show_names))
                except Invalid as error:
                    print("Original tests: unavailable — " + str(error))
                    unavailable.add(index)
                except (OSError, ValueError, LookupError, RuntimeError, ParseError, subprocess.SubprocessError):
                    print("Original tests: unavailable — could not verify the disposable checkout.")
                    unavailable.add(index)
                return status

            status = execute(review_proposal, args)
            decisions.append((label, status))
        if len(decisions) > 1:
            print("Release decision for the selected cases:")
            for label, status in decisions:
                print(f"  {label}: " + {0: "VERIFIED REPAIR", 100: "HOLD", 1: "INVALID"}[status])
        statuses = [status for _, status in decisions]
        if any(status != 1 for status in statuses):
            if not args.show_names:
                print("Use --show-names locally to display the test file and full case name.")
            print("Scope: selected tests, reached local modules, active settings and bound snapshots. Declare files read outside the module graph with --test-source.")
            if not args.replay_original_tests:
                print("For original-suite scores, add --replay-original-tests to this command.")
        return 1 if unavailable or 1 in statuses else max(statuses)


if __name__ == "__main__":
    parser = Arguments(description=__doc__)
    parser.add_argument("--before-root", required=True, type=Path)
    parser.add_argument("--after-root", type=Path, action="append", metavar="CHECKOUT",
                        help="candidate checkout (default: current directory); repeat to review multiple proposals")
    parser.add_argument("--replay-original-tests", action="store_true",
                        help="also check the baseline tests and declared controls on each trusted candidate")
    parser.add_argument("--implementation", type=relative_path, action="append", default=[], metavar="FILE",
                        help="changed service module; the adjacent same-named module is recognized automatically")
    parser.add_argument("--config", type=relative_path, help="explicit Vitest config relative to each checkout")
    parser.add_argument("--test-source", type=relative_path, action="append", default=[], metavar="FILE")
    parser.add_argument("--show-names", action="store_true", help="print local testcase names; these can contain private fixture values")
    parser.add_argument("files", nargs="+", type=relative_path, metavar="TEST_FILE")
    sys.exit(execute(review, parser.parse_args()))
