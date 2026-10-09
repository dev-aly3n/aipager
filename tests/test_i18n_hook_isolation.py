"""Check 13: the hook process never imports ``aipager.i18n``.

Claude Code starts ``aipager-hook`` on every hook event and waits for it,
so it must start fast, and nothing it loads may pull the translation
machinery in. Three tests:

(a) the dynamic closure: every one of the 16 hook events, plus the status
    line, runs in a fresh interpreter that dumps its ``aipager`` modules
    at exit. UserPromptSubmit runs with a real note on disk, which is the
    path that imports ``aipager.state``. Canaries prove the harness still
    reaches the deep modules, so it cannot pass by doing nothing;
(b) the static scan: every module of that closure, plus the AST closure
    of the two entry points, holds no import of ``aipager.i18n`` at any
    nesting level. A lazy import inside a function (which the dynamic run
    might never trigger) fails here;
(c) the scanner itself flags a function-level import.
"""

from __future__ import annotations

import ast
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from tests.test_hook_startup import EVENTS, SESSION, _env, _payload

REPO = Path(__file__).resolve().parents[1]
ENTRY_POINTS = ("aipager.dtach.notify_hook", "aipager.dtach.statusline_notify")
#: Modules the hook reaches only through its deeper paths (the note, the
#: background shell rows). Missing one means the harness stopped
#: exercising that path.
CANARIES = ("aipager.state", "aipager.policy_snapshot", "aipager.bg_shells",
            "aipager.config")

_DUMP = (
    "import atexit, json, sys\n"
    "def _dump():\n"
    "    mods = sorted(m for m in sys.modules if m == 'aipager' or m.startswith('aipager.'))\n"
    "    open({out!r}, 'w').write(json.dumps(mods))\n"
    "atexit.register(_dump)\n"
    "from aipager.dtach.{module} import main\n"
    "main()\n"
)

_WRITE_NOTE = (
    "from aipager.policy_snapshot import write_note\n"
    "print(write_note({session!r}, None, None, None, msg_id=1, chat_id=1, "
    "sender_key=(1, 1), body='[via Telegram]\\nhello', raw_text='hello', "
    "style_text='', scope_mode=False))\n"
)


@pytest.fixture(scope="module")
def closure():
    """The union of the ``aipager`` modules every hook run loaded."""
    root = Path(tempfile.mkdtemp(prefix="ahi-"))
    (root / "home").mkdir()
    loaded: set[str] = set()
    try:
        runs = [("notify_hook", _payload(e), e) for e in EVENTS]
        runs.append(("statusline_notify", {"session_id": "s", "model": {
            "display_name": "Opus"}, "context_window": {"used_percentage": 3},
            "cost": {"total_cost_usd": 0}}, "statusline"))
        for module, payload, name in runs:
            if name == "UserPromptSubmit":
                # Written by a separate interpreter, so the measured one
                # never imports the writer.
                helper = subprocess.run(
                    [sys.executable, "-c", _WRITE_NOTE.format(session=SESSION)],
                    capture_output=True, text=True, env=_env(root), cwd=str(root),
                    timeout=60)
                assert helper.returncode == 0 and helper.stdout.strip() != "None", (
                    helper.stderr)
                assert Path(helper.stdout.strip()).exists()
            out = root / f"mods-{name}.json"
            proc = subprocess.run(
                [sys.executable, "-c", _DUMP.format(out=str(out), module=module)],
                input=json.dumps(payload), capture_output=True, text=True,
                env=_env(root), cwd=str(root), timeout=60)
            assert out.exists(), f"{name}: no module dump\n{proc.stderr[-800:]}"
            loaded |= set(json.loads(out.read_text()))
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return loaded


def test_the_hook_never_loads_i18n(closure):
    assert [m for m in closure if m == "aipager.i18n" or m.startswith("aipager.i18n.")] == []
    missing = [c for c in CANARIES if c not in closure]
    assert not missing, f"the harness no longer reaches {missing}"


# ---- the static scan ----------------------------------------------------------------

def _path(module: str) -> Path | None:
    base = REPO.joinpath(*module.split("."))
    for p in (base.with_suffix(".py"), base / "__init__.py"):
        if p.is_file():
            return p
    return None


def _imported(tree: ast.AST, module: str, nodes) -> set[str]:
    out: set[str] = set()
    is_pkg = (_path(module) or Path()).name == "__init__.py"
    package = module if is_pkg else module.rpartition(".")[0]
    for node in nodes:
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = package.split(".")
                parts = parts[:len(parts) - node.level + 1]
                base = ".".join(parts + ([node.module] if node.module else []))
            out.add(base)
            out.update(f"{base}.{a.name}" for a in node.names)
    return {m for m in out if m == "aipager" or m.startswith("aipager.")}


def _module_level(tree: ast.Module):
    """Import nodes run when the module loads: not inside a function."""
    todo = list(tree.body)
    while todo:
        node = todo.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            yield node
        todo.extend(ast.iter_child_nodes(node))


def static_closure(entries=ENTRY_POINTS) -> set[str]:
    seen: set[str] = set()
    todo = list(entries)
    while todo:
        module = todo.pop()
        if module in seen or _path(module) is None:
            continue
        seen.add(module)
        parts = module.split(".")
        todo.extend(".".join(parts[:i]) for i in range(1, len(parts)))
        tree = ast.parse(_path(module).read_text(encoding="utf-8"))
        nodes = ast.walk(tree) if module in entries else _module_level(tree)
        todo.extend(_imported(tree, module, nodes))
    return seen


def i18n_imports(source: str) -> list[int]:
    """Line numbers of every import of ``aipager.i18n``, at any depth."""
    lines = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            if any(a.name == "aipager.i18n" or a.name.startswith("aipager.i18n.")
                   for a in node.names):
                lines.append(node.lineno)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if (mod == "aipager.i18n" or mod.startswith("aipager.i18n.")
                    or (mod == "aipager" and any(a.name == "i18n" for a in node.names))):
                lines.append(node.lineno)
    return lines


def test_no_closure_module_imports_i18n_anywhere(closure):
    modules = static_closure() | closure
    assert "aipager.dtach.notify_hook" in modules and "aipager.state" in modules
    bad = {}
    for module in sorted(modules):
        path = _path(module)
        if path is None:
            continue
        hits = i18n_imports(path.read_text(encoding="utf-8"))
        if hits:
            bad[module] = hits
    assert not bad, f"the hook's import closure reaches aipager.i18n: {bad}"


def test_the_static_scan_sees_a_lazy_import():
    source = (
        "import os\n"
        "def settled_row(x):\n"
        "    if x:\n"
        "        from aipager.i18n import clock\n"
        "    return x\n"
        "class C:\n"
        "    def f(self):\n"
        "        from aipager import i18n\n"
        "        import aipager.i18n.core\n"
    )
    assert i18n_imports(source) == [4, 8, 9]
    assert i18n_imports("from aipager import config\nimport aipager.i18nx\n") == []
