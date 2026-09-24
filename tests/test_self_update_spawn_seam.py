"""No spawn or fetch outside the self-update seams (roadmap 8.36).

The conftest guard replaces ``self_update._run_command`` and ``_http_get``
— which only protects anything if every process and every non-Telegram
fetch of the update path really goes through them. This sweep fails the
build on a ``subprocess.*`` / ``os.system`` / ``os.exec*`` / ``os.spawn*``
/ ``create_subprocess_*`` / ``urlopen`` call outside those two functions,
on any ``shell=True``, and on an ``aiohttp`` import, in the four modules
of the update path. The meta-tests below prove the guards themselves fire.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent / "aipager"
SWEPT = [
    ROOT / "install_source.py",
    ROOT / "self_update.py",
    ROOT / "bot" / "update_flow.py",
    ROOT / "updater.py",
]
SEAMS = {"_run_command", "_http_get"}
_OS_SPAWNERS = {"system", "popen", "fork", "forkpty"}


def spawn_offenders(filename: str, source: str) -> list[str]:
    tree = ast.parse(source, filename=filename)
    parents: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node

    def enclosing_function(node) -> str | None:
        cur = parents.get(id(node))
        while cur is not None:
            if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return cur.name
            cur = parents.get(id(cur))
        return None

    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names]
            mod = getattr(node, "module", None) or ""
            if mod.split(".")[0] == "aiohttp" or any(
                    n.split(".")[0] == "aiohttp" for n in names):
                offenders.append(f"{filename}:{node.lineno} imports aiohttp")
            if mod == "subprocess" and isinstance(node, ast.ImportFrom):
                offenders.append(f"{filename}:{node.lineno} from subprocess import …")
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if (kw.arg == "shell" and isinstance(kw.value, ast.Constant)
                    and kw.value.value is True):
                offenders.append(f"{filename}:{node.lineno} shell=True")
        func = node.func
        name = None
        if isinstance(func, ast.Attribute):
            base = ast.unparse(func.value)
            if base == "subprocess":
                name = f"subprocess.{func.attr}"
            elif base == "os" and (func.attr in _OS_SPAWNERS
                                   or func.attr.startswith(("exec", "spawn", "posix_spawn"))):
                name = f"os.{func.attr}"
            elif func.attr.startswith("create_subprocess_"):
                name = func.attr
            elif func.attr == "urlopen":
                name = "urlopen"
        elif isinstance(func, ast.Name) and (
                func.id == "urlopen" or func.id.startswith("create_subprocess_")):
            name = func.id
        if name is None:
            continue
        if enclosing_function(node) in SEAMS:
            continue
        offenders.append(f"{filename}:{node.lineno} {name}(")
    return offenders


def test_self_update_modules_spawn_only_through_seam():
    offenders = []
    for path in SWEPT:
        offenders += spawn_offenders(path.name, path.read_text())
    assert offenders == [], "\n".join(offenders)


@pytest.mark.parametrize("src", [
    "import subprocess\ndef f():\n    subprocess.run(['x'])\n",
    "import subprocess\ndef f():\n    subprocess.Popen(['x'])\n",
    "import os\ndef f():\n    os.system('x')\n",
    "import os\ndef f():\n    os.execv('/bin/x', ['x'])\n",
    "import asyncio\nasync def f():\n    await asyncio.create_subprocess_exec('x')\n",
    "import urllib.request\ndef f():\n    urllib.request.urlopen('https://x')\n",
    "def _run_command():\n    g(['x'], shell=True)\n",
    "import aiohttp\n",
    "from subprocess import run\n",
])
def test_sweep_catches_each_forbidden_shape(src):
    assert spawn_offenders("x.py", src), src


def test_sweep_allows_the_seams_themselves():
    src = ("import subprocess, urllib.request\n"
           "def _run_command():\n    subprocess.Popen(['x'])\n"
           "def _http_get():\n    urllib.request.urlopen('https://x')\n")
    assert spawn_offenders("x.py", src) == []


# ----- the conftest refusers actually refuse ----------------------------------

# Each meta-test below is written so that, were its guard REMOVED (as the
# mutation check does), the call would reach nothing real: a binary path
# that does not exist, and a URL on the local discard port. Removing a
# guard must turn these red, never run a real installer or fetch PyPI.

def test_conftest_refuses_real_update_spawn(_no_real_self_update_io):
    from aipager import self_update

    argv = ["/nonexistent/aipager-test/pipx", "upgrade", "aipager"]
    with pytest.raises(AssertionError, match="spawn a real process"):
        self_update.run_command(argv, timeout=1)
    assert _no_real_self_update_io.refused == [argv]
    _no_real_self_update_io.refused.clear()  # tripped on purpose


def test_conftest_blocks_release_fetch(_no_real_self_update_io, monkeypatch):
    from aipager import self_update

    monkeypatch.setattr(self_update, "PYPI_URL", "http://127.0.0.1:9/aipager/json")
    monkeypatch.setattr(self_update, "HTTP_TIMEOUT_SECONDS", 0.5)
    assert self_update.latest_aipager_version() is None
    assert _no_real_self_update_io.reached == ["http://127.0.0.1:9/aipager/json"]
    _no_real_self_update_io.reached.clear()  # tripped on purpose


def test_conftest_refuses_systemd_run_via_service_run():
    from aipager import service

    with pytest.raises(AssertionError, match="real service manager"):
        service._run(["/nonexistent/aipager-test/systemd-run", "--user", "true"])


def test_conftest_sees_no_real_cgroup():
    from aipager import self_update

    assert Path(self_update._PROC_SELF_CGROUP).read_text() == "0::/\n"
    assert self_update.restart_plan(None).mode == "foreground"
