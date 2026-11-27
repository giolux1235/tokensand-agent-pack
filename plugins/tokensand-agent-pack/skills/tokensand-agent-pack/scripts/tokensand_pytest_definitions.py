"""Private native-review plugin. Bind each selected pytest case to the Python test it runs."""
from functools import partial
import hashlib
import json
import runpy
import sys
from pathlib import Path


def source_file(function):
    code = getattr(function, "__code__", None)
    if code is None or not Path(code.co_filename).is_absolute():
        return None
    path = Path(code.co_filename).resolve(strict=True)
    if not path.is_file():
        return None
    return path, code.co_firstlineno


def definition(item, root, digests):
    function = item.obj
    visited = set()
    files = []
    while id(function) not in visited:
        visited.add(id(function))
        location = source_file(function)
        if location and location[0].is_relative_to(root):
            path, line = location
            if path not in digests:
                digest = hashlib.sha256()
                with path.open("rb") as source:
                    for chunk in iter(lambda: source.read(65536), b""):
                        digest.update(chunk)
                digests[path] = digest.hexdigest()
            files.append([path.relative_to(root).as_posix(), line])
        if isinstance(function, partial):
            function = function.func
        elif hasattr(function, "__wrapped__"):
            function = function.__wrapped__
        else:
            # Pytest may unwrap a decorator when it reports a test's location.
            # A third-party wrapper is fine when the real test is checkout-local.
            reported = Path(item.reportinfo()[0]).resolve(strict=True)
            return files if location and location[0].is_relative_to(root) and any(
                root / row[0] == reported for row in files
            ) else []
    return []


def pytest_collection_finish(session):
    destination = Path(sys.argv[1]).with_suffix(".definitions.json")
    root = Path.cwd().resolve()
    definitions = {}
    digests = {}
    for item in session.items:
        try:
            definitions[item.nodeid] = definition(item, root, digests)
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            definitions[item.nodeid] = []
    with destination.open("x", encoding="utf-8") as output:
        hashes = {path.relative_to(root).as_posix(): value for path, value in digests.items()}
        json.dump({"cases": definitions, "sources": hashes}, output, separators=(",", ":"))

if __name__ == "__main__":
    # Attach this exact module to the original native runner. A checkout may
    # have a Python module with the same filename as either bundled script.
    capture = runpy.run_path(str(Path(__file__).with_name("capture_pytest.py")))
    sys.exit(capture["main"]([sys.argv[1], "-p", "__main__", *sys.argv[2:]]))
