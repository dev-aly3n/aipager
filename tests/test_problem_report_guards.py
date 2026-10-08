"""The report's guards, each with a test that fails when the guard goes
(roadmap 8.112, design section 7):

- the schema stays closed (no free-text field but the note, no pattern
  that admits a path, an address or a bare id);
- the report modules never read a message, a log record's text, locals,
  the environment or the hostname (an AST sweep);
- a frame is aipager's only inside the installed package (a pipx venv is
  itself named ``aipager``);
- the fingerprint names WHERE, never WHAT (stable across messages and line
  edits);
- the validator drops what the schema does not name.
"""

from __future__ import annotations

import ast
import errno
import json
import pathlib
import random
import re
from pathlib import Path

import pytest

from aipager.report import builder, fingerprint as fp
from aipager.report import schema as sc

REPORT_DIR = Path(fp.__file__).parent
HOSTILE = ["/home/x/y", "/Users/x", "a@b.c", "https://x.y", "a b", "a:b",
           "777000123456", "-1009876543210", "sk-ant-x", "x" * 300,
           "builtins.E7770001234", "aipager.x7770001234567", "1.2-7770001234567",
           "1.2+id7770001"]
#: Values of the wrong shape for every leaf: none may pass, none may raise.
ODD = [[], {}, ["a"], {"a": 1}, 1.5, b"x", (1,), object(),
       {"file": ["aipager/state.py"], "line": 1, "fn": "f"},
       {"file": {"aipager/state.py": 1}, "line": 1, "fn": "f"},
       {"file": "aipager/state.py", "line": True, "fn": "f"},
       {"file": "aipager/state.py", "line": 1, "fn": "cnry_7770001234"}]


# ---- the schema is closed --------------------------------------------------

def _leaves(schema, path="report"):
    if isinstance(schema, sc.Leaf):
        yield path, schema
    elif isinstance(schema, dict):
        for key, sub in schema.items():
            yield from _leaves(sub, f"{path}.{key}")
    else:
        kind = schema[0]
        if kind == "list":
            yield from _leaves(schema[1], f"{path}[]")
        elif kind == "set":
            yield from _leaves(schema[1], f"{path}[]")
        elif kind == "map":
            yield f"{path}<key>", schema[1]
            yield from _leaves(schema[2], f"{path}<value>")
        else:
            raise AssertionError(f"unknown schema form at {path}")


KNOWN_KINDS = {"enum", "int", "bool", "version", "day", "regex", "type_name",
               "fingerprint", "code_location", "site", "note"}


def test_every_leaf_has_a_known_shape_and_only_the_note_is_free_text():
    for path, leaf in _leaves(sc.SCHEMA):
        assert leaf.kind in KNOWN_KINDS, path
        if leaf.kind == "note":
            assert path == "report.note", f"free text outside the note: {path}"


def test_no_pattern_admits_a_path_an_address_or_a_bare_id():
    for path, leaf in _leaves(sc.SCHEMA):
        if leaf.kind in ("regex", "version", "day", "type_name", "fingerprint"):
            for value in HOSTILE:
                assert leaf.check(value) == sc.INVALID, f"{path} admits {value!r}"


def test_an_enum_never_holds_free_looking_values():
    for path, leaf in _leaves(sc.SCHEMA):
        if leaf.kind == "enum":
            for value in leaf.values - {sc.SCHEMA_ID}:
                assert len(value) <= 32 and "/" not in value and "@" not in value, path


# ---- the validator ----------------------------------------------------------

def test_no_leaf_admits_a_value_of_the_wrong_shape_and_none_raises():
    for path, leaf in _leaves(sc.SCHEMA):
        for value in ODD:
            assert leaf.check(value) == sc.INVALID, f"{path} admits {value!r}"


def _random_value(rng: random.Random, depth: int = 0):
    pick = rng.randrange(9 if depth < 3 else 6)
    if pick == 0:
        return None
    if pick == 1:
        return rng.choice([0, -1, 10**7, True, 1.5])
    if pick == 2:
        return rng.choice(["", "ok", "777000123456", "/home/x", "aipager/state.py"])
    if pick == 3:
        return rng.choice(HOSTILE)
    if pick == 4:
        return rng.choice(sorted(sc.SCHEMA))
    if pick == 5:
        return rng.choice(ODD)
    if pick == 6:
        return [_random_value(rng, depth + 1) for _ in range(rng.randrange(14))]
    keys = sorted(sc.SCHEMA) + ["chat_id", "cnry", "file", "line", "fn", "site", "n"]
    return {rng.choice(keys): _random_value(rng, depth + 1) for _ in range(rng.randrange(8))}


def _within(schema, value) -> bool:
    """Every value is in its schema's shape: a dict only the schema's keys,
    a list at most its limit, a leaf what its check returns."""
    if isinstance(schema, sc.Leaf):
        return value == sc.INVALID or schema.check(value) == value
    if isinstance(schema, dict):
        return value == sc.INVALID or (isinstance(value, dict) and set(value) <= set(schema)
                                       and all(_within(schema[k], v) for k, v in value.items()))
    if schema[0] in ("list", "set"):
        limit = schema[2] if schema[0] == "list" else len(schema[1].values)
        return (isinstance(value, list) and len(value) <= limit
                and all(_within(schema[1], v) for v in value))
    return isinstance(value, dict) and all(
        schema[1].check(k) == k and _within(schema[2], v) for k, v in value.items())


@pytest.mark.parametrize("seed", range(100))
def test_the_validator_survives_any_structure_and_keeps_only_the_schema(seed):
    rng = random.Random(seed)
    report = {key: _random_value(rng) for key in rng.sample(sorted(sc.SCHEMA), 8)}
    report.update({"chat_id": 777000123456, "errors": [_random_value(rng) for _ in range(12)]})
    clean, problems = sc.validate(report)
    assert _within(sc.SCHEMA, clean)
    assert all("777000123456" not in p and "/home" not in p for p in problems)


def test_the_validator_drops_unknown_keys_and_marks_bad_leaves():
    clean, problems = sc.validate({
        "schema": sc.SCHEMA_ID, "trigger": "manual", "day": "2026-10-07",
        "chat_id": 777000123456, "python": {"version": "3.12.3", "impl": "CPython",
                                            "path": "/home/x"},
        "doctor": {"config": "ok", "token_valid": "fail"},
        "counters_24h": {"tg_5xx": 2, "cnry": 3},
        "os": {"distro": "/home/x"},
        "errors": [{"frames": [{"file": "/home/cnryuser/x.py", "line": 1, "fn": "f"},
                               {"file": "aipager/state.py", "line": 2, "fn": "g"}]}]})
    assert "chat_id" not in clean and "path" not in clean["python"]
    assert clean["doctor"] == {"config": "ok"}
    assert clean["counters_24h"] == {"tg_5xx": 2}
    assert clean["os"]["distro"] == sc.INVALID
    assert clean["errors"][0]["frames"] == [
        sc.INVALID, {"file": "aipager/state.py", "line": 2, "fn": "g"}]
    assert problems
    # A problem names the place, never the dropped key: a key can be data.
    assert not any("chat_id" in p or "cnry" in p for p in problems)


def test_a_type_name_from_a_module_not_on_the_list_is_invalid():
    assert sc.TYPE_NAME.check("customer_project.Billing") == sc.INVALID
    assert sc.TYPE_NAME.check("aipager.state.Outer.Inner") == "aipager.state.Outer.Inner"
    assert sc.TYPE_NAME.check("<other>") == "<other>"


def test_a_version_is_pep_440_shaped():
    for good in ("2.1.291", "22.0b1", "1.0rc1", "0.7.14.dev3+g1a2b", "1.0.post1", "0.0.0+unknown"):
        assert sc.VERSION.check(good) == good
    for bad in ("1.0cnryuser", "1.0+CNRY", "1.0-x", "1", "v1.0"):
        assert sc.VERSION.check(bad) == sc.INVALID


def test_a_site_line_is_ascii_digits():
    assert sc.SITE.check("aipager/state.py:12") == "aipager/state.py:12"
    assert sc.SITE.check("aipager/state.py:\u0661\u0662") == sc.INVALID


def test_the_note_leaf_caps_the_length_and_refuses_hiding_characters():
    assert sc.NOTE.check("x" * sc.NOTE_MAX) == "x" * sc.NOTE_MAX
    assert sc.NOTE.check("x" * (sc.NOTE_MAX + 1)) == sc.INVALID
    assert sc.NOTE.check("") == sc.INVALID
    assert sc.NOTE.check("a\u202eb") == sc.INVALID
    assert sc.NOTE.check("a\x1b[2Jb") == sc.INVALID


def test_an_int_leaf_is_a_bounded_non_negative_int():
    assert sc.INT.check(10**6) == 10**6
    for value in (10**6 + 1, -1, True, 1.0):
        assert sc.INT.check(value) == sc.INVALID


def test_lists_are_cut_sets_deduplicated_and_map_keys_checked():
    entry = {"type": "builtins.ValueError"}
    clean, problems = sc.validate({
        "errors": [entry] * 11,
        "log_digest_24h": [{"site": "aipager/state.py:1", "level": "ERROR", "n": 1}] * 16,
        "config": {"features": ["miniapp", "voice", "miniapp"]},
        "deps": {"httpx": "0.28.1", "cnry-pkg": "1.0"}})
    assert len(clean["errors"]) == 10 and len(clean["log_digest_24h"]) == 15
    assert clean["config"]["features"] == ["miniapp", "voice"]
    assert clean["deps"] == {"httpx": "0.28.1"}


def test_a_built_report_is_valid_as_built():
    report = builder.build_report("manual")
    again, problems = sc.validate(json.loads(json.dumps(report)))
    assert again == report and problems == []


# ---- the report modules never read data -------------------------------------

FORBIDDEN_ATTRS = {
    # an exception's or a log record's text
    "getMessage", "msg", "args", "message", "__notes__", "filename", "filename2",
    "strerror", "__str__", "__repr__", "__format__", "__dict__", "format", "format_map",
    "format_exc", "format_exception", "format_exception_only", "format_tb",
    "format_stack", "extract_tb", "extract_stack", "print_exc", "print_exception",
    # frame contents and source
    "f_locals", "f_back", "co_consts", "getline", "getsource",
    # what a logging Formatter or a pickle hands back: the text and args
    "formatException", "formatStack", "formatMessage", "exc_text", "stack_info",
    "__reduce__", "__reduce_ex__", "__getstate__", "__getattribute__",
    # the machine and the person
    "gethostname", "getfqdn", "node", "uname", "platform", "environ", "environb",
    "getenv", "getcwd", "cwd", "home", "expanduser", "getuser", "getlogin",
    "getpwuid", "argv", "orig_argv", "executable", "attrgetter", "getenvb", "getcwdb",
    "abspath", "import_module", "FileIO"}
FORBIDDEN_CALLS = {"str", "repr", "ascii", "format", "vars", "locals", "globals",
                   "eval", "exec", "open", "__import__"}
FORBIDDEN_MODULES = {"traceback", "linecache", "inspect", "getpass", "pwd", "socket",
                     "subprocess", "operator", "pickle", "copyreg", "string", "reprlib",
                     "pprint"}
#: Keys whose value is text wherever they appear (asyncio's handler
#: context, a log record's dict): never subscripted or ``.get``-ed.
FORBIDDEN_KEYS = {"message", "msg", "args", "exc_text", "stack_info", "source_traceback",
                  "handle_traceback"}
#: The only names a file may be read through (``.read_text()``,
#: ``.read_bytes()``, ``.open()``): module-level path constants, each a
#: machine fact the builder takes one shaped value from.
FILE_READ_RE = r"^[A-Z][A-Z0-9_]*$"
#: Besides those, the files aipager itself writes and validates again on
#: every read, each by this one local name: ``store_path`` in
#: ``store._read`` (reports.json) and ``marker_path`` in ``markers._read``
#: (install.json, running.json).
FILE_READ_LOCALS = {"store_path", "marker_path"}
#: What an f-string in aipager/report may interpolate. A new entry must be
#: a typed value (a count, a version part, a checked name), never text.
#: It matches the expression's text, so it is a tripwire, not a proof: a
#: variable renamed to an allowed name passes. The validator is the proof.
ALLOWED_INTERPOLATIONS = {
    "v.major", "v.minor", "v.micro", "m.group(1)", "m.group(2)", "module",
    "qualname", "rel", "rel.as_posix()", "kind", "file", "fn",
    "frame.f_globals.get('__name__')", "path", "key", "limit", "i", "line"}


def _sweep(source: str, name: str) -> list[str]:
    tree = ast.parse(source)
    parent = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    found = []
    for node in ast.walk(tree):
        where = f"{name}:{getattr(node, 'lineno', '?')}"
        if isinstance(node, ast.Attribute):
            if node.attr in FORBIDDEN_ATTRS:
                found.append(f"{where} .{node.attr}")
            call = parent.get(node)
            if (node.attr == "get" and isinstance(call, ast.Call) and call.args
                    and isinstance(call.args[0], ast.Constant)
                    and call.args[0].value in FORBIDDEN_KEYS):
                found.append(f"{where} .get({call.args[0].value!r})")
            if node.attr in ("read_text", "read_bytes", "open") and not (
                    isinstance(node.value, ast.Name)
                    and (re.match(FILE_READ_RE, node.value.id)
                         or node.value.id in FILE_READ_LOCALS)):
                found.append(f"{where} a file read through {ast.unparse(node.value)}")
            if node.attr == "f_globals":
                # Only ever f_globals.get("__name__"): the module's name.
                up = parent.get(node)
                call = parent.get(up)
                if not (isinstance(up, ast.Attribute) and up.attr == "get"
                        and isinstance(call, ast.Call) and call.args
                        and isinstance(call.args[0], ast.Constant)
                        and call.args[0].value == "__name__"):
                    found.append(f"{where} f_globals beyond __name__")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in FORBIDDEN_CALLS:
                found.append(f"{where} {node.func.id}()")
            if (node.func.id in ("getattr", "hasattr", "setattr", "delattr")
                    and len(node.args) > 1 and isinstance(node.args[1], ast.Constant)
                    and node.args[1].value in FORBIDDEN_ATTRS):
                found.append(f"{where} {node.func.id}(.., {node.args[1].value!r})")
            if node.func.id in ("getattr", "hasattr") and len(node.args) > 1 and not (
                    isinstance(node.args[1], ast.Constant)):
                found.append(f"{where} {node.func.id} with a computed name")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in FORBIDDEN_MODULES:
                    found.append(f"{where} import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] in FORBIDDEN_MODULES:
                found.append(f"{where} from {node.module}")
            for alias in node.names:
                if alias.name in FORBIDDEN_ATTRS or alias.name in FORBIDDEN_CALLS:
                    found.append(f"{where} import of {alias.name}")
        elif (isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant)
              and node.slice.value in FORBIDDEN_KEYS):
            found.append(f"{where} [{node.slice.value!r}]")
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod) and (
                isinstance(node.left, ast.Constant) and isinstance(node.left.value, str)):
            found.append(f"{where} %-formatting")
        elif isinstance(node, ast.FormattedValue):
            text = ast.unparse(node.value)
            if text not in ALLOWED_INTERPOLATIONS:
                found.append(f"{where} f-string of {text}")
    return found


def test_the_report_modules_never_read_a_message_locals_env_or_host():
    found = []
    for path in sorted(REPORT_DIR.glob("*.py")):
        found += _sweep(path.read_text(), path.name)
    assert found == [], "forbidden reads in aipager/report: " + "; ".join(found)


@pytest.mark.parametrize("line", [
    "str(exc)", "repr(exc)", "exc.args", "exc.__str__()", "exc.__dict__",
    "'{}'.format(exc)", "'%s' % (exc,)", "f'{exc}'", "f'{err!r}'",
    "record.getMessage()", "record.msg", "getattr(exc, 'args')", "getattr(exc, name)",
    "frame.f_locals", "frame.f_globals['token']", "frame.f_globals.get('TOKEN')",
    "from traceback import format_exception as fe", "import traceback",
    "import linecache", "from os import environ", "os.environ.get('HOME')",
    "os.getenv('HOME')", "from socket import gethostname, getfqdn",
    "platform.node()", "platform.platform()", "getpass.getuser()", "Path.home()",
    "Path('~').expanduser()", "sys.argv", "open('/etc/hostname')",
    "code.co_consts", "from operator import attrgetter",
    "context['message']", "context.get('message')", "record.__dict__['msg']",
    "self.formatter.formatException(record.exc_info)", "self.formatMessage(record)",
    "record.exc_text", "record.stack_info", "exc.__reduce__()", "exc.__reduce_ex__(2)",
    "import pickle", "from copyreg import dispatch_table", "from string import Formatter",
    "import string", "Path('/etc/hostname').read_text()",
    "Path('/proc/self/environ').read_bytes()", "target.open()", "os.getenvb(b'HOME')",
    "os.getcwdb()", "os.path.abspath('.')", "importlib.import_module('socket')",
    "io.FileIO('/etc/hostname')", "import reprlib", "from pprint import pformat"])
def test_the_sweep_flags_each_way_of_reading_data(line):
    assert _sweep(line, "probe.py"), f"the sweep misses {line!r}"


# ---- frames: only inside the installed package -------------------------------

def _raise_from(filename: str, module: str):
    namespace = {"__name__": module}
    exec(compile("def boom():\n    raise ValueError('x')\n", filename, "exec"), namespace)
    try:
        namespace["boom"]()
    except ValueError as exc:
        return exc
    raise AssertionError


@pytest.fixture
def fake_install(tmp_path, monkeypatch):
    """A pipx-style install: the venv itself is named ``aipager``."""
    pkg = tmp_path / "venvs" / "aipager" / "lib" / "python3.12" / "site-packages" / "aipager"
    (pkg / "bot").mkdir(parents=True)
    for rel in ("__init__.py", "bot/__init__.py", "bot/animation.py"):
        (pkg / rel).write_text("")
    monkeypatch.setattr(fp, "PACKAGE_DIR", pkg)
    fp._shipped.cache_clear()
    fp._relative_in.cache_clear()
    yield pkg
    fp._shipped.cache_clear()
    fp._relative_in.cache_clear()


def test_a_frame_in_the_installed_package_is_named_from_the_package_root(fake_install):
    exc = _raise_from(str(fake_install / "bot" / "animation.py"), "aipager.bot.animation")
    frames = fp.aipager_frames(exc)
    assert frames == [{"file": "aipager/bot/animation.py", "line": 2, "fn": "boom"}]


def test_a_look_alike_module_or_a_file_outside_the_package_is_not_ours(fake_install):
    evil = _raise_from(str(fake_install / "bot" / "animation.py"), "aipager_evil")
    outside = _raise_from(str(fake_install.parent / "aipager_x.py"), "aipager.bot.animation")
    venv = _raise_from(str(fake_install.parents[3] / "bin" / "x.py"), "aipager.cli")
    # Inside the package directory but not a shipped file (written later,
    # or a name made up by exec): not ours either.
    ghost = _raise_from(str(fake_install / "bot" / "cnryuser.py"), "aipager.bot.cnryuser")
    for exc in (evil, outside, venv, ghost):
        assert fp.aipager_frames(exc) == []
        # Not part of the fingerprint either: the same as no aipager frame.
        assert fp.fingerprint(exc) == fp._digest("builtins.ValueError|")


# ---- the fingerprint names where, not what ------------------------------------

def _raise_in_aipager(message: str, line_padding: int = 0, exc_type=ValueError):
    src = "\n" * line_padding + "def boom(message):\n    raise TYPE(message)\n"
    namespace = {"__name__": "aipager.state", "TYPE": exc_type}
    exec(compile(src, str(fp.PACKAGE_DIR / "state.py"), "exec"), namespace)
    try:
        namespace["boom"](message)
    except Exception as exc:  # noqa: BLE001
        return exc
    raise AssertionError


def test_the_fingerprint_ignores_the_message_and_line_moves():
    a = fp.fingerprint(_raise_in_aipager("chat 777000123456"))
    b = fp.fingerprint(_raise_in_aipager("something else entirely"))
    c = fp.fingerprint(_raise_in_aipager("x", line_padding=40))
    assert a == b == c
    assert fp.FINGERPRINT_RE.match(a)


def test_the_fingerprint_tells_types_apart():
    assert fp.fingerprint(_raise_in_aipager("x")) != fp.fingerprint(
        _raise_in_aipager("x", exc_type=KeyError))


def test_an_unknown_module_type_is_named_other():
    cls = type("Secret", (Exception,), {"__module__": "customer_project.billing"})
    assert fp.qualified_type(cls) == "<other>"
    assert fp.qualified_type(ValueError) == "builtins.ValueError"
    odd = type("Odd", (Exception,), {})
    odd.__module__ = 5
    assert fp.qualified_type(odd) == "<other>"


def test_odd_frame_names_and_errnos_never_raise():
    namespace = {"__name__": 5}
    exec(compile("def boom():\n    raise ValueError('x')\n", str(fp.PACKAGE_DIR / "state.py"),
                 "exec"), namespace)
    try:
        namespace["boom"]()
    except ValueError as exc:
        assert fp.describe_exception(exc)["frames"] == []
    odd = OSError()
    odd.errno = [errno.ENOSPC]
    assert fp.errno_name(odd) is None
    assert fp.errno_name(OSError(errno.ENOSPC, "x")) == "ENOSPC"


def test_a_relative_file_name_is_never_ours(monkeypatch):
    """It resolves against the cwd (``python -c``, a ``''`` path entry), so
    the same name would mean different files over a process's life."""
    monkeypatch.chdir(fp.PACKAGE_DIR.parent)
    assert fp._relative_file(str(fp.PACKAGE_DIR / "state.py")) == "aipager/state.py"
    assert fp._relative_file("aipager/state.py") is None


def test_a_frame_line_must_be_a_non_negative_int():
    exc = _raise_in_aipager("x")
    frame = exc.__traceback__.tb_next.tb_frame
    assert fp.code_location(frame, 3) == {"file": "aipager/state.py", "line": 3, "fn": "boom"}
    for line in (-1, None, "3"):
        assert fp.code_location(frame, line) is None


def test_at_most_eight_outside_packages_are_named():
    tops = ["telegram", "httpx", "httpcore", "aiohttp", "asyncio", "json", "yaml", "ssl",
            "socket", "concurrent"]
    inner = None
    for top in reversed(tops):
        namespace = {"__name__": f"{top}.mod", "nxt": inner}
        exec(compile("def f():\n    return nxt() if nxt else 1 / 0\n", f"/srv/{top}.py", "exec"),
             namespace)
        inner = namespace["f"]
    try:
        inner()
    except ZeroDivisionError as exc:
        assert fp.external_packages(exc) == tops[::-1][:8]


def test_chained_types_stop_at_a_cycle_and_at_three():
    chain = [ValueError(str(i)) for i in range(6)]
    for outer, inner in zip(chain, chain[1:]):
        outer.__cause__ = inner
    assert fp.cause_types(chain[0]) == ["builtins.ValueError"] * fp.MAX_CAUSES
    a, b = KeyError("a"), TypeError("b")
    a.__context__, b.__context__ = b, a
    assert fp.cause_types(a) == ["builtins.TypeError"]


def test_a_deep_recursion_resolves_each_file_once(fake_install, monkeypatch):
    calls = []
    real = pathlib.Path.resolve
    monkeypatch.setattr(pathlib.Path, "resolve",
                        lambda self, *a, **k: calls.append(1) or real(self, *a, **k))
    namespace = {"__name__": "aipager.bot.animation"}
    exec(compile("def down(n):\n    return down(n - 1) if n else 1 / 0\n",
                 str(fake_install / "bot" / "animation.py"), "exec"), namespace)
    try:
        namespace["down"](300)
    except ZeroDivisionError as exc:
        facts = fp.describe_exception(exc)
    assert len(facts["frames"]) == fp.MAX_FRAMES
    assert len(calls) <= 2, f"{len(calls)} resolves for one file"


def test_outside_code_is_named_by_allow_listed_package_only():
    """A crash that passes through the user's own code names it ``<other>``."""
    inner = {"__name__": "acme_billing_secret.invoices"}
    exec(compile("def pay():\n    raise ValueError('x')\n", "/srv/acme/invoices.py", "exec"), inner)
    outer = {"__name__": "aipager.state", "pay": inner["pay"]}
    exec(compile("def run():\n    pay()\n", str(fp.PACKAGE_DIR / "state.py"), "exec"), outer)
    try:
        outer["run"]()
    except ValueError as exc:
        assert fp.external_packages(exc) == ["<other>"]
        assert [f["fn"] for f in fp.aipager_frames(exc)] == ["run"]
        return
    raise AssertionError
