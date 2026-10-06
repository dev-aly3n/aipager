"""No test patches the global ``asyncio`` module through a module path.

``monkeypatch.setattr("aipager.bot.session_ops.asyncio.sleep", fake)``
looks scoped to one module but is not: ``aipager.bot.session_ops.asyncio``
IS the one global ``asyncio`` module, so the fake replaces
``asyncio.sleep`` for every coroutine in the process (the event loop's
own helpers, PTB, aiohttp, every other task) for the test's duration. A
fake that returns at once turns any polling loop into a busy spin; that
hung this suite twice (CLAUDE.md, roadmap 8.70). Production modules
expose seams instead (``session_ops._sleep``, ``callbacks._sleep``,
``inject._create_subprocess_exec``, ...), defaulting to the real
functions, and tests patch those.

The scan is static (AST, one file at a time) and flags:

1. a string patch target through an ``aipager`` module path:
   ``"aipager.<...>.asyncio.<name>"`` (any attribute);
2. ``setattr`` / ``delattr`` / ``patch.object`` on an expression that
   ends in ``.asyncio`` (``inject.asyncio``, ``tm.asyncio``,
   ``aipager.bot.notify.asyncio``; any attribute);
3. an assignment to ``<x>.asyncio.<name>``;
4. a direct patch of the global module's pacing/scheduling attributes
   (``"asyncio.sleep"``, ``setattr(asyncio, "create_task", ...)``,
   ``asyncio.sleep = ...``): the same hazard without the module path.

Rule 4 deliberately covers only :data:`_PACING` and not
``create_subprocess_exec``: the "nothing may spawn" tripwires
(``test_miniapp_server``, ``miniapp-sessions-grid-diff-viewer``,
``manage-miniapp-tunnel``'s conftest) replace every spawn with a function
that RAISES, on purpose process-wide, through
``tests.conftest.forbid_every_spawn`` (the global function AND each
module's ``_create_subprocess_exec`` seam). A raising spawn cannot spin a
loop. Nothing is allow-listed by file.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent

_AIPAGER_ASYNCIO_TARGET = re.compile(r"^aipager(\.\w+)*\.asyncio\.\w+")
# A computed target (f-string, concatenation) with its runtime parts read
# as "?": anything ending ``.asyncio.<name>`` goes through some module.
_COMPUTED_ASYNCIO_TARGET = re.compile(r"\.asyncio\.\w+$")
_PACING = frozenset({
    "sleep", "create_task", "ensure_future", "gather", "wait", "wait_for",
    "shield", "timeout", "Event", "get_event_loop", "get_running_loop",
    "run", "new_event_loop",
})
_DIRECT_TARGET = re.compile(r"^asyncio(\.\w+)*\.(%s)$" % "|".join(sorted(_PACING)))
_SETTERS = frozenset({"setattr", "delattr", "object", "multiple",
                      "dict", "setitem", "delitem"})


def _call_name(func: ast.expr) -> str:
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _is_patch_call(name: str) -> bool:
    return "patch" in name or name in _SETTERS


def _string_target(node: ast.expr | None) -> tuple[str, bool] | None:
    """``(text, computed)`` for a str literal, f-string or ``+`` of them."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value, False
    if isinstance(node, ast.JoinedStr):
        return "".join(
            v.value if isinstance(v, ast.Constant) else "?" for v in node.values
        ), True
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _string_target(node.left), _string_target(node.right)
        if left is not None or right is not None:
            return (left or ("?", True))[0] + (right or ("?", True))[0], True
    return None


def _aliases(tree: ast.Module) -> tuple[set[str], set[str]]:
    """Names that ARE asyncio in this file: ``(global_names, module_names)``.

    ``import asyncio as aio`` / ``from <x> import asyncio`` give a global
    name; ``aio = inject.asyncio`` / ``getattr(inject, "asyncio")`` give a
    module-path name (any attribute through it is the global module).
    """
    global_names = {"asyncio"}
    module_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == "asyncio" and a.asname:
                    global_names.add(a.asname)
        elif isinstance(node, ast.ImportFrom) and node.module != "__future__":
            for a in node.names:
                if a.name == "asyncio":
                    (global_names if node.module is None or node.level
                     else module_names).add(a.asname or a.name)
        elif isinstance(node, ast.Assign):
            kind = _asyncio_kind(node.value, global_names, module_names)
            for t in node.targets:
                if kind and isinstance(t, ast.Name):
                    (module_names if kind == "module" else global_names).add(t.id)
    module_names -= {"asyncio"}
    return global_names, module_names


def _asyncio_kind(node: ast.expr | None, global_names: set[str],
                  module_names: set[str]) -> str | None:
    """``"module"`` for asyncio reached through a module, ``"global"`` for
    the global name (or ``sys.modules["asyncio"]``), else None."""
    if isinstance(node, ast.Call) and _call_name(node.func) == "vars" and node.args:
        return _asyncio_kind(node.args[0], global_names, module_names)
    if isinstance(node, ast.Attribute):
        # ``inject.asyncio`` and anything below it (``inject.asyncio.tasks``,
        # ``inject.asyncio.__dict__``) is the global module or part of it.
        if node.attr == "asyncio":
            return "module"
        inner = _asyncio_kind(node.value, global_names, module_names)
        if inner == "module":
            return "module"
    if (isinstance(node, ast.Call) and _call_name(node.func) == "getattr"
            and len(node.args) >= 2 and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == "asyncio"):
        return "module"
    if (isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant)
            and node.slice.value == "asyncio"):
        return "global"
    if isinstance(node, ast.Name):
        if node.id in module_names:
            return "module"
        if node.id in global_names:
            return "global"
    return None


def _patch_args(call: ast.Call) -> tuple[ast.expr | None, list[str]]:
    """The patch target and the attribute names it replaces."""
    target = call.args[0] if call.args else None
    names: list[str] = []
    if len(call.args) > 1 and isinstance(call.args[1], ast.Constant):
        names.append(str(call.args[1].value))
    for kw in call.keywords:
        if kw.arg == "target":
            target = kw.value
        elif kw.arg in ("attribute", "name") and isinstance(kw.value, ast.Constant):
            names.append(str(kw.value.value))
        elif _call_name(call.func) == "multiple" and kw.arg:
            names.append(kw.arg)
    return target, names


def find_global_asyncio_patches(source: str) -> list[tuple[int, str]]:
    """Every line of *source* that patches the global asyncio module."""
    tree = ast.parse(source)
    global_names, module_names = _aliases(tree)
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_patch_call(_call_name(node.func)):
            target, names = _patch_args(node)
            text = _string_target(target)
            if text is not None:
                value, computed = text
                if (_AIPAGER_ASYNCIO_TARGET.match(value)
                        or (computed and _COMPUTED_ASYNCIO_TARGET.search(value))
                        or (not computed and _DIRECT_TARGET.match(value))):
                    hits.append((node.lineno, value))
                continue
            kind = _asyncio_kind(target, global_names, module_names)
            if kind == "module" or (kind == "global" and _PACING & set(names)):
                hits.append((node.lineno, f"{_call_name(node.func)}(<asyncio>, {names})"))
        elif isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if not isinstance(target, ast.Attribute):
                    continue
                kind = _asyncio_kind(target.value, global_names, module_names)
                if kind == "module" or (kind == "global" and target.attr in _PACING):
                    hits.append((node.lineno, f"<asyncio>.{target.attr} = ..."))
    return hits


def _test_files() -> list[Path]:
    return sorted(p for p in TESTS_DIR.rglob("*.py") if "__pycache__" not in p.parts)


def test_no_test_file_patches_the_global_asyncio_module():
    offenders: list[str] = []
    for path in _test_files():
        text = path.read_text(encoding="utf-8")
        if "asyncio" not in text:
            continue
        for lineno, what in find_global_asyncio_patches(text):
            offenders.append(f"{path.relative_to(TESTS_DIR.parent)}:{lineno}: {what}")
    assert not offenders, (
        "these patch the ONE global asyncio module (CLAUDE.md: it has hung the "
        "suite twice); patch the module's seam instead (e.g. "
        "session_ops._sleep, inject._create_subprocess_exec):\n  "
        + "\n  ".join(offenders))


def test_the_scan_actually_covers_the_test_tree():
    """Guard against a scan that silently reads nothing (a wrong root)."""
    files = _test_files()
    assert len(files) > 100
    assert Path(__file__).resolve() in files


@pytest.mark.parametrize("snippet", [
    'monkeypatch.setattr("aipager.bot.session_ops.asyncio.sleep", f)',
    'monkeypatch.setattr("aipager.session_monitor.asyncio.create_task", f)',
    'monkeypatch.setattr(\n    "aipager.cli.daemon.asyncio.Event", f)',
    'mock.patch("aipager.bot.notify.asyncio.sleep", new=f)',
    'with patch("aipager.dtach.inject.asyncio.create_subprocess_exec"): pass',
    'monkeypatch.setattr(inject.asyncio, "create_subprocess_exec", f)',
    'monkeypatch.setattr(aipager.bot.notify.asyncio, "sleep", f)',
    'patch.object(session_ops.asyncio, "sleep", f)',
    'mock.patch.object(target=tm.asyncio, attribute="sleep", new=f)',
    'monkeypatch.delattr(inject.asyncio, "sleep")',
    'inject.asyncio.sleep = f',
    'monkeypatch.setattr("asyncio.sleep", f)',
    'patch("asyncio.create_task", f)',
    'monkeypatch.setattr(asyncio, "sleep", f)',
    'patch.object(asyncio, "ensure_future", f)',
    'asyncio.sleep = f',
    'patch.multiple(inject.asyncio, sleep=f)',
    'patch.multiple(asyncio, create_task=f)',
    'aio = inject.asyncio\nmonkeypatch.setattr(aio, "create_subprocess_exec", f)',
    'import asyncio as aio\nmonkeypatch.setattr(aio, "sleep", f)',
    'from aipager.bot.notify import asyncio as aio\nmonkeypatch.setattr(aio, "spawn", f)',
    'monkeypatch.setattr(getattr(inject, "asyncio"), "sleep", f)',
    'monkeypatch.setattr(sys.modules["asyncio"], "sleep", f)',
    'monkeypatch.setattr(f"{MOD}.asyncio.sleep", f)',
    'monkeypatch.setattr(MOD + ".asyncio.create_task", f)',
    'mock.patch(target="aipager.bot.auth.asyncio.sleep", new=f)',
    'monkeypatch.setattr(inject.asyncio.subprocess, "PIPE", x)',
    'patch.dict(inject.asyncio.__dict__, {"sleep": f})',
    'monkeypatch.setitem(vars(inject.asyncio), "sleep", f)',
    'patch("asyncio.tasks.sleep", f)',
])
def test_the_scan_flags_a_global_patch(snippet):
    assert find_global_asyncio_patches(snippet), snippet


@pytest.mark.parametrize("snippet", [
    'monkeypatch.setattr("aipager.bot.session_ops._sleep", f)',
    'monkeypatch.setattr(session_ops, "_spawn", f)',
    'monkeypatch.setattr(inject, "_create_subprocess_exec", f)',
    'monkeypatch.setattr("aipager.bot.notify.COMPACT_DONE_PAUSE_SECONDS", 0)',
    'monkeypatch.setattr("asyncio.create_subprocess_exec", boom)',
    'await asyncio.sleep(0)',
    'x = "aipager.bot.notify.asyncio IS the global module"',
    'log.info("aipager.bot.notify.asyncio.sleep")',
    'monkeypatch.setattr(asyncio, "create_subprocess_exec", boom)',
    'patch.multiple(session_ops, _sleep=f)',
    'monkeypatch.setattr(f"{MOD}._sleep", f)',
])
def test_the_scan_leaves_scoped_seams_alone(snippet):
    assert find_global_asyncio_patches(snippet) == [], snippet


def test_every_seam_defaults_to_the_real_asyncio_function():
    """Production must behave byte-for-byte as before: each seam IS the
    asyncio function it stands in for, not a wrapper."""
    import asyncio

    from aipager.bot import animation, auth, callbacks, handlers, session_ops
    from aipager.cli import daemon
    from aipager.dtach import inject
    from aipager.miniapp import tunnel_manager
    from aipager import session_monitor

    assert session_ops._sleep is asyncio.sleep
    assert session_ops._spawn is asyncio.create_task
    assert callbacks._sleep is asyncio.sleep
    assert auth._sleep is asyncio.sleep
    assert animation._compact_sleep is asyncio.sleep
    assert session_monitor._loop_sleep is asyncio.sleep
    assert inject._key_gap_sleep is asyncio.sleep
    assert inject._create_subprocess_exec is asyncio.create_subprocess_exec
    assert handlers._create_subprocess_exec is asyncio.create_subprocess_exec
    assert tunnel_manager._create_subprocess_exec is asyncio.create_subprocess_exec
    assert daemon._new_stop_event is asyncio.Event


def test_every_spawn_seam_is_covered_by_the_tripwire_helper():
    """A module-level spawn seam (``<name> = asyncio.create_subprocess_exec``)
    is bound at import, so a global patch never reaches it.
    ``forbid_every_spawn`` lists every one; a new seam left out of it would
    silently disarm the "nothing may spawn" tripwires for that module (as
    tunnel_manager's briefly did)."""
    from tests.conftest import SPAWN_SEAMS

    pkg = TESTS_DIR.parent / "aipager"
    found = set()
    for path in pkg.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        mod = ".".join(path.relative_to(pkg.parent).with_suffix("").parts)
        for name in re.findall(
                r"^(\w+) = asyncio\.create_subprocess_(?:exec|shell)\s*$",
                text, re.MULTILINE):
            found.add(f"{mod}.{name}")
    assert found, "no spawn seam found: the scan is looking in the wrong place"
    assert found == set(SPAWN_SEAMS)


def test_forbid_every_spawn_reaches_each_seam(monkeypatch):
    import asyncio
    import importlib

    from tests.conftest import SPAWN_SEAMS, forbid_every_spawn

    async def _boom(*_a, **_k):
        raise AssertionError("spawn")

    forbid_every_spawn(monkeypatch, _boom)
    assert asyncio.create_subprocess_exec is _boom
    for target in SPAWN_SEAMS:
        mod, attr = target.rsplit(".", 1)
        assert getattr(importlib.import_module(mod), attr) is _boom, target
