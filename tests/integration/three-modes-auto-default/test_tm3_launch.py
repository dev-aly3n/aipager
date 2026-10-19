"""Success criteria 1-4: launch flags per mode, never the --allow- flag,
refusal of an invalid mode before anything spawns, and the `aipager
session` CLI starting in Auto unless the user chose a mode."""

from __future__ import annotations

import re
import shlex
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aipager import perm_modes
from aipager.dtach import inject, launcher

from _tm3 import FLAGS, NEVER


def _proc():
    p = AsyncMock()
    p.returncode = 0
    p.communicate = AsyncMock(return_value=(b"", b""))
    return p


@pytest.fixture
def spawn(monkeypatch):
    """Capture what launch_session would exec; nothing is spawned."""
    captured: list[tuple] = []

    async def _fake_exec(*args, **kwargs):
        captured.append(args)
        return _proc()

    monkeypatch.setattr(inject, "_create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(inject, "_conversation_exists", lambda sid, projects=None: True)
    calls = {"n": 0}

    def _is_socket(self):
        calls["n"] += 1
        return calls["n"] > 1

    monkeypatch.setattr(inject.Path, "is_socket", _is_socket)
    return captured


def _tokens(captured) -> list[str]:
    cmd = captured[-1][-1]
    try:
        return shlex.split(cmd)
    except ValueError:
        return cmd.split()


def _mode_flags(tokens) -> list[str]:
    out = []
    for i, t in enumerate(tokens):
        if t == "--permission-mode":
            out.append(f"--permission-mode {tokens[i + 1] if i + 1 < len(tokens) else ''}")
        elif t.startswith("--permission-mode="):
            out.append("--permission-mode " + t.split("=", 1)[1])
        elif t == "--dangerously-skip-permissions":
            out.append(t)
    return out


MODES = ["ask", "auto", "full"]


@pytest.mark.parametrize("mode", MODES, ids=MODES)
def test_create_launch_carries_the_modes_flag(spawn, run_async, tmp_path, mode):
    ok, err = run_async(inject.launch_session("jim", mode=mode, cwd=str(tmp_path)))
    assert ok, err
    assert _mode_flags(_tokens(spawn)) == [FLAGS[mode]]


@pytest.mark.parametrize("mode", MODES, ids=MODES)
def test_resume_launch_carries_the_modes_flag(spawn, run_async, tmp_path, mode):
    ok, err = run_async(inject.launch_session(
        "jim", mode=mode, cwd=str(tmp_path),
        resume_id="e4f739a9-e19a-4d17-a8c2-12ba1b288907"))
    assert ok, err
    assert _mode_flags(_tokens(spawn)) == [FLAGS[mode]]


@pytest.mark.parametrize("mode", MODES, ids=MODES)
def test_relaunch_carries_the_modes_flag(spawn, run_async, tmp_path, mode):
    ok, err = run_async(inject.launch_session(
        "jim", mode=mode, cwd=str(tmp_path), is_relaunch=True,
        resume_id="e4f739a9-e19a-4d17-a8c2-12ba1b288907"))
    assert ok, err
    assert _mode_flags(_tokens(spawn)) == [FLAGS[mode]]


@pytest.mark.parametrize("mode", MODES, ids=MODES)
def test_no_launch_ever_passes_the_allow_flag(spawn, run_async, tmp_path, mode):
    run_async(inject.launch_session("jim", mode=mode, cwd=str(tmp_path)))
    assert NEVER not in spawn[-1][-1]


def test_the_allow_flag_is_not_in_the_launch_module_or_mode_module():
    """Design §1: the string must never appear in perm_modes or inject."""
    from pathlib import Path
    src = (Path(perm_modes.__file__).read_text() + Path(inject.__file__).read_text())
    assert NEVER not in src


@pytest.mark.parametrize("mode", MODES, ids=MODES)
def test_launch_flag_text_per_mode(mode):
    assert perm_modes.launch_flag(mode) == FLAGS[mode]


@pytest.mark.parametrize("bad", [True, False, "", "bypass", "Auto", None, "default"],
                         ids=["bool-true", "bool-false", "empty", "unknown",
                              "wrong-case", "none", "claude-id"])
def test_launch_flag_rejects_anything_else(bad):
    with pytest.raises(ValueError):
        perm_modes.launch_flag(bad)


@pytest.mark.parametrize("bad", [True, False, "", "bypass"],
                         ids=["bool-true", "bool-false", "empty", "unknown"])
def test_launch_session_refuses_an_invalid_mode(spawn, run_async, tmp_path, bad):
    result = run_async(inject.launch_session("jim", mode=bad, cwd=str(tmp_path)))
    assert result == (False, "Invalid permission mode")


@pytest.mark.parametrize("bad", [True, False, "", "bypass"],
                         ids=["bool-true", "bool-false", "empty", "unknown"])
def test_launch_session_spawns_nothing_for_an_invalid_mode(spawn, run_async, tmp_path, bad):
    run_async(inject.launch_session("jim", mode=bad, cwd=str(tmp_path)))
    assert spawn == []


def test_launch_session_mode_is_required(spawn, run_async, tmp_path):
    with pytest.raises(TypeError):
        run_async(inject.launch_session("jim", cwd=str(tmp_path)))
    assert spawn == []


# ---- the CLI: aipager session <name> [claude args] ------------------------

def _cli_argv(monkeypatch, claude_args):
    """Run launcher.launch up to the spawn and return the claude argv it
    would hand dtach (everything after the claude binary)."""
    runs: list[list[str]] = []
    monkeypatch.setattr(launcher, "_resolve_dtach", lambda: "/usr/bin/dtach-fake")
    monkeypatch.setattr(launcher, "_dtach_works", lambda p: (True, ""))
    monkeypatch.setattr(launcher, "_socket_alive", lambda s: False)
    monkeypatch.setattr(launcher.time, "sleep", lambda s: None)
    monkeypatch.setattr(launcher, "_claude_version_diag", lambda: "")
    monkeypatch.setattr(launcher.subprocess, "run",
                        lambda *a, **k: runs.append(list(a[0])) or MagicMock(
                            returncode=0, stderr="", stdout=""))
    from pathlib import Path
    with patch.object(Path, "exists", lambda self: False), \
         patch.object(Path, "is_socket", lambda self: False):
        launcher.launch("freshname", list(claude_args), claude_bin="/opt/fake/claude")
    spawn = [r for r in runs if "-n" in r]
    assert spawn, runs
    argv = spawn[0]
    joined = " ".join(argv)
    # The claude argv: everything after the claude binary, wherever env
    # wrappers sit in front of it.
    m = re.search(r"/opt/fake/claude", joined)
    assert m, argv
    idx = max(i for i, t in enumerate(argv) if "/opt/fake/claude" in t)
    tail = argv[idx + 1:]
    if len(tail) == 0 and " " in argv[idx]:  # a shell string
        tail = shlex.split(argv[idx].split("/opt/fake/claude", 1)[1])
    return tail


def test_cli_session_starts_in_auto(monkeypatch):
    tail = _cli_argv(monkeypatch, [])
    assert _mode_flags(tail) == ["--permission-mode auto"]


CHOSEN = [
    (["--permission-mode", "plan"], ["--permission-mode plan"]),
    (["--permission-mode=default"], ["--permission-mode default"]),
    (["--dangerously-skip-permissions"], ["--dangerously-skip-permissions"]),
    (["--model", "opus", "--permission-mode", "acceptEdits"],
     ["--permission-mode acceptEdits"]),
]


@pytest.mark.parametrize("args,expected", CHOSEN,
                         ids=["mode-plan", "mode-equals-default", "bypass",
                              "mode-after-other-args"])
def test_cli_session_adds_nothing_when_a_mode_was_chosen(monkeypatch, args, expected):
    tail = _cli_argv(monkeypatch, args)
    assert _mode_flags(tail) == expected


def test_cli_session_still_adds_auto_with_only_the_allow_flag(monkeypatch):
    tail = _cli_argv(monkeypatch, [NEVER])
    assert _mode_flags(tail) == ["--permission-mode auto"]


def test_cli_session_keeps_the_users_other_args(monkeypatch):
    tail = _cli_argv(monkeypatch, ["--model", "opus"])
    assert tail[-2:] == ["--model", "opus"]
