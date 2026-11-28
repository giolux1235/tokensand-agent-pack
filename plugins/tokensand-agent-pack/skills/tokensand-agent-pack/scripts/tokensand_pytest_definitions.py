"""Private native-review plugin. Bind each selected pytest case to the Python test it runs."""
from functools import partial
from weakref import WeakKeyDictionary
import hashlib
import json
import runpy
import struct
import sys
import sysconfig

import pytest
from pathlib import Path


# Used only by the review subprocess; real pytest selection and outcome hooks keep native node IDs.
review_ids = {}
opaque_parameters = False
checkout = Path.cwd().resolve()
library_paths = {Path(sysconfig.get_path(name)).resolve() for name in ("purelib", "platlib")}
case_definitions = {}
source_digests = {}
fixture_locations = {}
case_fixtures = {}
unresolved_fixture = False

# A FixtureDef is reused across parametrized/scope cache lifetimes. Its current
# instance changes only when pytest actually calls the setup hook.
current_instances = {}
request_instances = WeakKeyDictionary()
case_roots = set()
case_lookups = set()


class FixtureInstance:
    def __init__(self, name, locations):
        self.name = name
        self.locations = sorted(locations)
        self.dependencies = set()
        self.setting_up = True

    def signature(self, memo):
        if self not in memo:
            children = sorted(d for child in self.dependencies if (d := child.signature(memo)))
            # Third-party fixtures are outside this check unless they lead to a local fixture.
            if self.locations or children:
                payload = json.dumps([self.name, self.locations, children], separators=(",", ":"))
                memo[self] = hashlib.sha256(payload.encode("ascii")).hexdigest()
            else:
                memo[self] = None
        return memo[self]


def source_file(function):
    code = getattr(function, "__code__", None)
    if code is None or not Path(code.co_filename).is_absolute():
        return None
    path = Path(code.co_filename).resolve(strict=True)
    if not path.is_file():
        return None
    return path, code.co_firstlineno


def function_chain(function):
    seen = set()
    while id(function) not in seen:
        seen.add(id(function))
        yield function
        if isinstance(function, partial):
            function = function.func
        elif hasattr(function, "__wrapped__"):
            function = function.__wrapped__
        else:
            return


def source_digest(path):
    if path not in source_digests:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(65536), b""):
                digest.update(chunk)
        source_digests[path] = digest.hexdigest()


def definition(item):
    files = []
    for function in function_chain(item.obj):
        location = source_file(function)
        if location and location[0].is_relative_to(checkout):
            path, line = location
            source_digest(path)
            files.append([path.relative_to(checkout).as_posix(), line])
        if not isinstance(function, partial) and not hasattr(function, "__wrapped__"):
            reported = Path(item.reportinfo()[0]).resolve(strict=True)
            return files if location and location[0].is_relative_to(checkout) and any(
                checkout / row[0] == reported for row in files
            ) else []
    return []


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_fixture_setup(fixturedef, request):
    # This also observes autouse, transitive and dynamically requested fixtures. Never inspect fixture values.
    global unresolved_fixture
    try:
        for function in function_chain(fixturedef.func):
            location = source_file(function)
            if location is None:
                module = sys.modules.get(getattr(function, "__module__", ""))
                origin = getattr(module, "__file__", None)
                if origin and Path(origin).resolve().is_relative_to(checkout):
                    unresolved_fixture = True
                continue
            path, line = location
            if path.is_relative_to(checkout) and not any(path.is_relative_to(lib) for lib in library_paths):
                source_digest(path)
                fixture_locations.setdefault(fixturedef, set()).add((path.relative_to(checkout).as_posix(), line))
        instance = FixtureInstance(fixturedef.argname, fixture_locations.get(fixturedef, ()))
        current_instances[fixturedef] = instance
        request_instances[request] = instance
        # All supported pytest versions resolve declared dependencies before setup.
        # pytest 7 injects them without calling the public getfixturevalue method.
        for name in fixturedef.argnames:
            if name != "request":
                instance.dependencies.add(current_instances[request._fixture_defs[name]])
    except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError):
        unresolved_fixture = True
    try:
        yield
    finally:
        if request in request_instances:
            request_instances[request].setting_up = False


def track_fixture_getter(original):
    def getfixturevalue(request, argname):
        global unresolved_fixture
        value = original(request, argname)
        if argname != "request":
            try:
                child = current_instances[request._fixture_defs[argname]]
                if request.fixturename is None:
                    case_roots.add(child)
                else:
                    parent = request_instances[request]
                    if parent.setting_up:
                        parent.dependencies.add(child)
                    else:
                        # A fixture can return a callable that looks up a dependency
                        # during a test. Keep that edge in the current case only.
                        case_lookups.add((parent, child))
            except (AttributeError, KeyError, TypeError):
                unresolved_fixture = True
        return value
    return getfixturevalue


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_protocol(item):
    case_roots.clear()
    case_lookups.clear()
    yield


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    yield
    if call.when != "teardown":
        return
    global unresolved_fixture
    try:
        memo = {}
        roots = sorted(d for instance in case_roots if (d := instance.signature(memo)))
        lookups = sorted((parent.signature(memo) or "", d) for parent, child in case_lookups
                         if (d := child.signature(memo)))
        encoded = json.dumps([roots, lookups], separators=(",", ":")).encode("ascii")
        case_fixtures[review_ids.get(item.nodeid, item.nodeid)] = hashlib.sha256(encoded).hexdigest()
    except (AttributeError, RecursionError, TypeError, ValueError):
        unresolved_fixture = True


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
    for item in session.items:
        identity = item.nodeid
        if hasattr(item, "callspec"):
            try:
                identity += "[parameter-sha256=" + parameter_digest(item.callspec.params) + "]"
                review_ids[item.nodeid] = identity
            except (AttributeError, ValueError):
                opaque_parameters = True
        try:
            case_definitions[identity] = definition(item)
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            case_definitions[identity] = []


if __name__ == "__main__":
    # Attach this exact module to the original native runner. A checkout may
    # have a Python module with the same filename as either bundled script.
    capture = runpy.run_path(str(Path(__file__).with_name("capture_pytest.py")))
    fixture_request = getattr(pytest, "FixtureRequest", None)
    getter = getattr(fixture_request, "getfixturevalue", None)
    tracked_getter = track_fixture_getter(getter)
    if callable(getter):
        fixture_request.getfixturevalue = tracked_getter
    else:
        unresolved_fixture = True
    try:
        status = capture["main"]([sys.argv[1], "-p", "__main__", *sys.argv[2:]])
    finally:
        if callable(getter):
            if fixture_request.getfixturevalue is not tracked_getter:
                unresolved_fixture = True
            fixture_request.getfixturevalue = getter
    destination = Path(sys.argv[1])
    hashes = {path.relative_to(checkout).as_posix(): value for path, value in source_digests.items()}
    with destination.with_suffix(".definitions.json").open("x", encoding="utf-8") as output:
        fixture_files = sorted({name for locations in fixture_locations.values() for name, _ in locations})
        json.dump({"cases": case_definitions, "sources": hashes, "fixtureFiles": fixture_files, "caseFixtures": case_fixtures,
                   "unresolvedFixture": unresolved_fixture}, output, separators=(",", ":"))
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
