"""Disposable original-suite replay for explicitly trusted, offline Vitest checkouts."""
from dataclasses import dataclass
import hashlib
import os
import re
from pathlib import Path
import subprocess

from review_pytest import Invalid

MAX_FILES = 4096
MAX_BYTES = 64 * 1024 * 1024
CREDENTIAL_NAMES = {".npmrc", ".netrc", ".pypirc", ".git-credentials", "id_rsa", "id_ed25519",
                    "credentials.json", "service-account.json"}


def credential_path(path):
    name = path.name.lower()
    return (any(part.lower() in {".aws", ".ssh", ".kube", ".gnupg", ".azure"} for part in path.parts)
            or name in CREDENTIAL_NAMES or name == ".env" or name.startswith(".env.")
            or name.endswith((".key", ".p12", ".pfx", ".pem")))


@dataclass
class Snapshot:
    files: dict
    problem: str = ""


def git(root, *args):
    # The index is a list of tracked paths. Read actual worktree bytes, including edits.
    env = {key: os.environ[key] for key in ("PATH", "SystemRoot") if key in os.environ}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0")
    result = subprocess.run(["git", "--no-optional-locks", "-c", "core.fsmonitor=false", *args], cwd=root,
                            env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            timeout=20)
    if result.returncode:
        raise Invalid("original replay requires a Git worktree with tracked inputs")
    return result.stdout


def filesystem_data(root, observed):
    """A low-cost default guard for tracked data read outside Vite's import graph."""
    try:
        uses_fs = any(path.suffix.lower() in {".js", ".mjs", ".cjs", ".ts", ".mts", ".cts", ".jsx", ".tsx"}
                      and digest and re.search(rb"""['"](?:node:)?fs(?:/promises)?['"]""", (root / path).read_bytes())
                      for path, digest in observed.items())
        if not uses_fs:
            return None
        patterns = ["*.json", "*.jsonl", "*.ndjson", "*.csv", "*.tsv", "*.yaml", "*.yml", "*.xml", "*.snap", "*.txt"]
        names = git(root, "ls-files", "-z", "--", *patterns).split(b"\0")[:-1]
        if len(names) > MAX_FILES:
            return None
        files = {}
        size = 0
        for name in names:
            path = Path(os.fsdecode(name))
            value = root / path
            if path in observed or credential_path(path):
                continue
            if value.resolve() != value or (value.exists() and not value.is_file()):
                return None
            if not value.exists():
                files[path] = None
                continue
            size += value.stat().st_size
            if size > MAX_BYTES:
                return None
            files[path] = hashlib.sha256(value.read_bytes()).digest()
        return files
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, Invalid):
        return None


def snapshot(root):
    try:
        if Path(os.fsdecode(git(root, "rev-parse", "--show-toplevel").rstrip(b"\n"))).resolve() != root:
            raise Invalid("select the Git worktree root and a repository-relative test file")
        extras = git(root, "ls-files", "--others", "--directory", "-z", "--", ".", ":(exclude,glob)**/node_modules",
                     ":(exclude,glob)**/node_modules/**")
        if extras:
            raise Invalid("untracked or ignored project files are present; use a separate checkout with its locked dependencies")
        entries = git(root, "ls-files", "--stage", "-z").split(b"\0")[:-1]
        if len(entries) > MAX_FILES:
            raise Invalid("original replay supports up to 4096 tracked files and 64 MiB of source")
        files = {}
        links = {}
        total = 0
        for entry in entries:
            metadata, name = entry.split(b"\t", 1)
            mode, _, stage = metadata.split()
            path = Path(os.fsdecode(name))
            if (stage != b"0" or mode not in (b"100644", b"100755", b"120000") or path.is_absolute()
                    or ".." in path.parts or "node_modules" in path.parts or
                    (root / path.parent).resolve() != root / path.parent):
                raise Invalid("submodules, conflicts or external inputs make original replay unavailable")
            if credential_path(path):
                raise Invalid("a tracked credential or environment-file layout is outside original replay scope")
            value = root / path
            if mode == b"120000":
                text = os.readlink(value)
                target = Path(os.path.normpath(value.parent / text))
                if (os.path.isabs(text) or not target.is_relative_to(root) or value.resolve(strict=True) != target
                        or target.resolve(strict=True) != target or target == root or target in value.parents):
                    raise Invalid("a tracked link escapes, loops or points through another link")
                links[path] = target.relative_to(root)
                files[path] = (hashlib.sha256(os.fsencode(text)).digest(), False, text)
                continue
            if value.resolve() != value or not value.is_file():
                raise Invalid("a tracked file is missing; original replay cannot reconstruct the worktree")
            stat = value.stat()
            total += stat.st_size
            if total > MAX_BYTES:
                raise Invalid("original replay supports up to 4096 tracked files and 64 MiB of source")
            files[path] = (hashlib.sha256(value.read_bytes()).digest(), bool(stat.st_mode & 0o111), None)
        directories = {parent for path in files for parent in path.parents}
        linked_directories = {parent for path in links for parent in path.parents}
        for target in links.values():
            if target not in files and (target not in directories or target in linked_directories):
                raise Invalid("a tracked link must point to a tracked file or a self-contained tracked directory")
        return Snapshot(files)
    except Invalid as error:
        return Snapshot({}, str(error))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        return Snapshot({}, "could not verify the tracked worktree inputs")


def stable(root, initial):
    if initial.problem:
        raise Invalid(initial.problem)
    if snapshot(root) != initial:
        raise Invalid("tracked worktree inputs changed during review")


def clean_revision(root, tree):
    """Only name HEAD when the index and the exact tested file bytes match it."""
    if tree.problem:
        return "Unavailable"
    try:
        revision = git(root, "rev-parse", "--verify", "HEAD^{commit}").decode("ascii").strip()
        if not re.fullmatch(r"(?:[a-f0-9]{40}|[a-f0-9]{64})", revision):
            return "Unavailable"
        if git(root, "diff", "--cached", "--no-ext-diff", "--no-renames", "--name-only", revision):
            return "Uncommitted"
        # Index shortcuts such as assume-unchanged can hide edited bytes from git status.
        entries = git(root, "ls-tree", "-r", "-z", revision).split(b"\0")[:-1]
        if len(entries) != len(tree.files):
            return "Uncommitted"
        algorithm = "sha1" if len(revision) == 40 else "sha256"
        for entry in entries:
            header, name = entry.split(b"\t", 1)
            mode, kind, oid = header.split()
            path = Path(os.fsdecode(name))
            if path not in tree.files or kind != b"blob":
                return "Uncommitted"
            digest, executable, link = tree.files[path]
            expected_mode = b"120000" if link is not None else (b"100755" if executable else b"100644")
            if mode != expected_mode:
                return "Uncommitted"
            if link is not None:
                contents = os.fsencode(link)
            else:
                with (root / path).open("rb") as handle:
                    size = os.fstat(handle.fileno()).st_size
                    if size > MAX_BYTES:
                        return "Unavailable"
                    contents = handle.read(size + 1)
            if len(contents) > MAX_BYTES or hashlib.sha256(contents).digest() != digest:
                return "Unavailable"
            blob = hashlib.new(algorithm, b"blob " + str(len(contents)).encode("ascii") + b"\0")
            blob.update(contents)
            if blob.hexdigest().encode("ascii") != oid:
                return "Uncommitted"
        return revision
    except (Invalid, OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        return "Unavailable"


def copy_input(root, path, expected, dest):
    source = root / path
    if expected[2] is not None:
        if os.readlink(source) != expected[2] or dest.exists() or dest.is_symlink():
            raise Invalid("a tracked link changed while constructing the disposable checkout")
        dest.symlink_to(expected[2], target_is_directory=source.is_dir())
        return
    if source.resolve() != source or source.stat().st_size > MAX_BYTES:
        raise Invalid("a source changed while constructing the disposable checkout")
    contents = source.read_bytes()
    if hashlib.sha256(contents).digest() != expected[0]:
        raise Invalid("a source changed while constructing the disposable checkout")
    dest.write_bytes(contents)
    dest.chmod(0o700 if expected[1] else 0o600)


def bind_modules(source, scratch, files):
    # Keep cache writes at the disposable node_modules root. Workspace links point
    # back to their disposable copy; ordinary installed packages remain in place.
    dirs = {parent for path in files for parent in path.parents}
    for parent in sorted(dirs):
        modules = source / parent / "node_modules"
        if not modules.is_dir():
            continue
        dest = scratch / parent / "node_modules"
        dest.mkdir()
        for entry in modules.iterdir():
            if entry.name.startswith(".") and entry.name != ".bin":
                continue
            if entry.name.startswith("@") and entry.is_dir():
                (dest / entry.name).mkdir()
                packages = list(entry.iterdir())
            else:
                packages = [entry]
            for package in packages:
                target = package.resolve(strict=True)
                if target.is_relative_to(source) and "node_modules" not in target.relative_to(source).parts:
                    target = scratch / target.relative_to(source)
                    if not target.is_dir():
                        raise Invalid("a workspace dependency could not be bound to the disposable checkout")
                elif "node_modules" not in target.parts:
                    raise Invalid("an external local dependency is outside the tracked checkout")
                (dest / package.relative_to(modules)).symlink_to(target, target_is_directory=target.is_dir())


def tracked_changes(before_tree, candidate_tree):
    return {path for path in before_tree.files.keys() | candidate_tree.files.keys()
            if before_tree.files.get(path) != candidate_tree.files.get(path)}


UNKNOWN_INPUT = ("A tracked input outside the identified service and selected tests changed. "
                 "For an expected fixture or config, name it with --test-source FILE.")


def original_result(args, baseline_root, root, before_tree, candidate_tree, early, later, work, capture, implementations):
    stable(baseline_root, before_tree)
    stable(root, candidate_tree)
    base_files, current_files = before_tree.files, candidate_tree.files
    original_inputs = set(args.files) | set(args.test_source)
    if original_inputs - base_files.keys() or original_inputs - current_files.keys():
        raise Invalid("selected tests and declared controls must be tracked in both worktrees")
    if any(base_files.get(p, (None, None, None))[2] or current_files.get(p, (None, None, None))[2]
           for p in original_inputs | implementations):
        raise Invalid("select regular tracked paths for the tests, service and declared controls")
    changed = tracked_changes(before_tree, candidate_tree)
    if changed - (original_inputs | implementations):
        raise Invalid(UNKNOWN_INPUT)
    overlay = changed & original_inputs
    checked = later
    mode = "submitted run"
    if overlay:
        scratch = (work / "original-checkout").resolve()
        scratch.mkdir(mode=0o700)
        for path, identity in current_files.items():
            dest = scratch / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            copy_input(root, path, identity, dest)
        bind_modules(root, scratch, current_files)
        parity = work / "submitted-in-copy"
        parity.mkdir(mode=0o700)
        if capture(scratch, args, parity) != later:
            raise Invalid("the disposable checkout did not reproduce the submitted suite and inputs")
        for path in overlay:
            copy_input(baseline_root, path, base_files[path], scratch / path)
        original = work / "original-results"
        original.mkdir(mode=0o700)
        checked = capture(scratch, args, original)
        stable(baseline_root, before_tree)
        stable(root, candidate_tree)
        mode = "replayed"
    before, old, _, first_issues, v1, _, _ = early
    after, new, _, last_issues, v2, _, _ = checked
    if (first_issues or last_issues or v1 != v2 or before.keys() != after.keys()
            or any(s not in ("passed", "failed") for s in [*before.values(), *after.values()])
            or implementations - (old.keys() & new.keys()) or old.keys() != new.keys() or any(old[p] != new[p] for p in old.keys() - implementations)):
        raise Invalid("original named cases, checked inputs, ordinary outcomes or runner settings do not match")
    return mode, after
