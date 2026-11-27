"""Private native-review plugin. Bind each selected pytest case to the Python test it runs."""
from functools import partial
import hashlib
import json
import runpy
import struct
import sys
from pathlib import Path


# Used only by the review subprocess; real pytest selection and outcome hooks keep native node IDs.
review_ids = {}
opaque_parameters = False


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


def parameter_digest(params):
    """Bind collected inputs without invoking repr, an encoder or iteration on application objects."""
    budget = 65536

    def encode(value, depth=0):
        nonlocal budget
        kind = type(value)
        budget -= 1 + (len(value) if kind is str or kind is bytes else 0)
        if kind is int:
            budget -= value.bit_length() // 8
        if budget < 0 or depth > 32:
            raise ValueError
        if value is None or kind is bool or kind is str or (kind is int and value.bit_length() <= 4096):
            return value
        if kind is float:
            return ["float", struct.pack(">d", value).hex()]
        if kind is bytes:
            return ["bytes", value.hex()]
        if kind is list or kind is tuple:
            return [kind.__name__, [encode(entry, depth + 1) for entry in value]]
        if kind is dict and len(value) <= budget and all(type(key) is str for key in value):
            return ["dict", [[encode(key, depth + 1), encode(value[key], depth + 1)] for key in value]]
        raise ValueError

    encoded = json.dumps(encode(params), ensure_ascii=True, separators=(",", ":")).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def pytest_collection_finish(session):
    global opaque_parameters
    destination = Path(sys.argv[1]).with_suffix(".definitions.json")
    root = Path.cwd().resolve()
    definitions = {}
    digests = {}
    for item in session.items:
        identity = item.nodeid
        if hasattr(item, "callspec"):
            try:
                identity += "[parameter-sha256=" + parameter_digest(item.callspec.params) + "]"
                review_ids[item.nodeid] = identity
            except (AttributeError, ValueError):
                opaque_parameters = True
        try:
            definitions[identity] = definition(item, root, digests)
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            definitions[identity] = []
    with destination.open("x", encoding="utf-8") as output:
        hashes = {path.relative_to(root).as_posix(): value for path, value in digests.items()}
        json.dump({"cases": definitions, "sources": hashes}, output, separators=(",", ":"))

if __name__ == "__main__":
    # Attach this exact module to the original native runner. A checkout may
    # have a Python module with the same filename as either bundled script.
    capture = runpy.run_path(str(Path(__file__).with_name("capture_pytest.py")))
    status = capture["main"]([sys.argv[1], "-p", "__main__", *sys.argv[2:]])
    destination = Path(sys.argv[1])
    if status in (0, 1) and (review_ids or opaque_parameters) and destination.stat().st_size <= 16 * 1024 * 1024:
        # capture exclusively creates this private file. Bind its completed outcomes
        # to the collected inputs; the standalone capture and reporter stay unchanged.
        with destination.open("r+", encoding="utf-8") as stream:
            record = json.load(stream)
            for row in record["cases"]:
                row[0] = review_ids.get(row[0], row[0])
            if opaque_parameters and "unsupported" not in record["issues"]:
                record["issues"].append("unsupported")
            stream.seek(0)
            json.dump(record, stream, separators=(",", ":"))
            stream.truncate()
    sys.exit(status)
