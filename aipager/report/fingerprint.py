"""Name an error by WHERE it happened in aipager, never by what it said.

Standard library only and cheap to import: the PreToolUse hook imports it
(a later step) under a 1 GiB address-space cap and a 5 ms budget. Nothing
runs at import; the one walk of the package directory happens on the
first error described, and each frame's file is resolved once.

An exception is reduced to:

- its type, ``module.QualName``, the module from an allow-list (anything
  else is ``<other>``: a third-party or user module name is not ours to
  report);
- aipager's own frames, taken from the traceback OBJECTS (never formatted
  text), each as ``{"file": "aipager/...", "line": N, "fn": "Qual.name"}``.

A frame is aipager's only when its module is ``aipager`` or ``aipager.*``
AND its file resolves inside the installed package directory. A substring
test on ``"aipager/"`` would be wrong: a pipx venv is itself named
``aipager`` (``.../venvs/aipager/lib/.../site-packages/aipager/...``).

Never read: the exception's message or ``args``, ``__notes__``, frame
locals, source lines (so no ``traceback`` module: the traceback objects
are walked by hand). The fingerprint never hashes data either: a hashed
chat id is not anonymous (a 10-digit id space is brute-forced in seconds).
"""

from __future__ import annotations

import errno as _errno
import functools
import hashlib
import os
import re
from pathlib import Path

#: The installed package directory (``.../aipager``). Module-level so a
#: test can point it at a fake install.
PACKAGE_DIR = Path(__file__).resolve().parent.parent

FINGERPRINT_PREFIX = "ap1-"
FINGERPRINT_RE = re.compile(r"^ap1-[0-9a-f]{12}$")
FN_RE = re.compile(r"^[A-Za-z_<][A-Za-z0-9_.<>]{0,80}$")
TYPE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,120}$")

#: How many of the innermost aipager frames name an error; more frames
#: travel in a report (``MAX_FRAMES``) but do not split one error in two.
KEY_FRAMES = 8
MAX_FRAMES = 12
MAX_CAUSES = 3

#: Modules whose exception types may be named. Anything else is ``<other>``.
TYPE_MODULES = ("builtins", "aipager", "telegram", "httpx", "httpcore",
                "aiohttp", "asyncio", "json", "yaml", "ssl", "socket",
                "concurrent.futures", "subprocess")

#: Top-level packages whose frames may be named (by package only) as the
#: outside code an aipager frame called into. Anything else is ``<other>``.
EXTERNAL_PACKAGES = frozenset({
    "telegram", "httpx", "httpcore", "aiohttp", "asyncio", "json", "yaml",
    "ssl", "socket", "concurrent", "subprocess", "selectors", "anyio",
    "aiolimiter", "rich", "questionary", "multidict", "yarl", "h11", "h2",
    "pathlib", "os", "re", "logging", "threading", "importlib",
})


def _module_allowed(module) -> bool:
    if not isinstance(module, str):
        return False
    return any(module == m or module.startswith(m + ".") for m in TYPE_MODULES)


def qualified_type(exc_type: type) -> str:
    """``module.QualName`` of an exception type, or ``<other>``."""
    module = getattr(exc_type, "__module__", "") or ""
    qualname = getattr(exc_type, "__qualname__", "") or ""
    name = f"{module}.{qualname}"
    if not _module_allowed(module) or not TYPE_NAME_RE.match(name):
        return "<other>"
    return name


@functools.lru_cache(maxsize=4)
def _shipped(package_dir: str) -> frozenset[str]:
    """Every ``aipager/...py`` file in the installed package."""
    root = Path(package_dir)
    out = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in filenames:
            if name.endswith(".py"):
                rel = (Path(dirpath) / name).relative_to(root).as_posix()
                out.add(f"aipager/{rel}")
    return frozenset(out)


def shipped_files() -> frozenset[str]:
    return _shipped(os.fspath(PACKAGE_DIR))


@functools.lru_cache(maxsize=512)
def _relative_in(package_dir: str, filename: str) -> str | None:
    try:
        rel = Path(filename).resolve().relative_to(package_dir)
    except (OSError, ValueError, RuntimeError):
        return None
    name = f"aipager/{rel.as_posix()}"
    return name if name in _shipped(package_dir) else None


def _relative_file(filename) -> str | None:
    """``aipager/...py`` for a file inside the installed package, else None
    (resolved once per file: a deep recursion has one file a thousand
    times)."""
    if not isinstance(filename, str) or not os.path.isabs(filename):
        return None  # a relative name resolves against the cwd: not cacheable
    return _relative_in(os.fspath(PACKAGE_DIR), filename)


def _function_name(code) -> str | None:
    name = getattr(code, "co_qualname", None) or code.co_name
    return name if FN_RE.match(name) else None


def is_aipager_frame(frame) -> bool:
    module = frame.f_globals.get("__name__", "")
    if not isinstance(module, str):
        return False
    if module != "aipager" and not module.startswith("aipager."):
        return False
    return _relative_file(frame.f_code.co_filename) is not None


def code_location(frame, lineno: int) -> dict | None:
    """``{"file", "line", "fn"}`` for an aipager frame, else None."""
    if not is_aipager_frame(frame):
        return None
    file = _relative_file(frame.f_code.co_filename)
    fn = _function_name(frame.f_code)
    if file is None or fn is None or not isinstance(lineno, int) or lineno < 0:
        return None
    return {"file": file, "line": lineno, "fn": fn}


def _walk(tb) -> list[tuple[object, int]]:
    """``(frame, lineno)`` from the traceback objects, innermost first."""
    out = []
    while tb is not None:
        out.append((tb.tb_frame, tb.tb_lineno))
        tb = tb.tb_next
    out.reverse()
    return out


def aipager_frames(exc: BaseException, limit: int = MAX_FRAMES) -> list[dict]:
    """aipager's own frames of *exc*, innermost first."""
    out = []
    for frame, lineno in _walk(exc.__traceback__):
        loc = code_location(frame, lineno)
        if loc is not None:
            out.append(loc)
            if len(out) >= limit:
                break
    return out


def external_packages(exc: BaseException) -> list[str]:
    """The outside packages *exc* passed through, innermost first, each
    once: allow-listed top-level names, else ``<other>``."""
    out: list[str] = []
    for frame, _lineno in _walk(exc.__traceback__):
        if is_aipager_frame(frame):
            continue
        module = frame.f_globals.get("__name__", "")
        top = module.split(".", 1)[0] if isinstance(module, str) else ""
        name = top if top in EXTERNAL_PACKAGES else "<other>"
        if name not in out:
            out.append(name)
    return out[:8]


def cause_types(exc: BaseException) -> list[str]:
    """The types of the exceptions chained under *exc* (``__cause__``,
    else ``__context__``), outermost first, at most ``MAX_CAUSES``."""
    out = []
    seen = {id(exc)}
    cur = exc.__cause__ or exc.__context__
    while cur is not None and id(cur) not in seen and len(out) < MAX_CAUSES:
        seen.add(id(cur))
        out.append(qualified_type(type(cur)))
        cur = cur.__cause__ or cur.__context__
    return out


def errno_name(exc: BaseException) -> str | None:
    """The symbolic errno of an OSError (``ENOSPC``), never its text."""
    if not isinstance(exc, OSError):
        return None
    number = exc.errno
    if not isinstance(number, int):
        return None
    return _errno.errorcode.get(number)


def _digest(text: str) -> str:
    return FINGERPRINT_PREFIX + hashlib.sha256(text.encode()).hexdigest()[:12]


def fingerprint(exc: BaseException) -> str:
    """``ap1-<12 hex>`` from the exception type and the innermost
    ``KEY_FRAMES`` aipager frames (module and function, no line numbers
    and no version, so it survives edits and releases)."""
    parts = []
    for frame, _lineno in _walk(exc.__traceback__):
        if not is_aipager_frame(frame):
            continue
        fn = _function_name(frame.f_code) or "<?>"
        parts.append(f"{frame.f_globals.get('__name__')}:{fn}")
        if len(parts) >= KEY_FRAMES:
            break
    return _digest(qualified_type(type(exc)) + "|" + ";".join(parts))


def site_fingerprint(kind: str, file: str, fn: str) -> str:
    """For an event that is no exception (a counter at a call site):
    from its kind and the call site only."""
    return _digest(f"site|{kind}|{file}|{fn}")


def describe_exception(exc: BaseException) -> dict:
    """The allow-listed facts of *exc*: its fingerprint, type, chained
    types, errno name, aipager frames and outside packages."""
    return {
        "fingerprint": fingerprint(exc),
        "type": qualified_type(type(exc)),
        "cause_types": cause_types(exc),
        "errno": errno_name(exc),
        "frames": aipager_frames(exc),
        "external": external_packages(exc),
    }
