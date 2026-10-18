"""Turning the tool on: the env var and the send note in every launch path
(design.md criterion "Every session aipager launches ..."; entrypoints.md
claude_launch, ``aipager session``, /new)."""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock

import pytest

from aipager import claude_launch
from aipager.bot.held import HELD
from aipager.dtach import inject, launcher

kit = sys.modules["_sftc_kit"]
NOTE = ("You can send files and images (screenshots, PDFs, videos, anything) to the "
        "person in Telegram with the SendUserFile tool; if it is not in your tool "
        "list, load it with ToolSearch. Never say you cannot send files.")
NAME_LINE = ('Your session name is "frontend". When users address you by this name, '
             'respond naturally -- it is your name in this session.')


def test_env_name():
    assert claude_launch.ATTACHMENTS_ENV == "CLAUDE_CODE_ATTACHMENTS_BY_PATH"


def test_send_note_text():
    assert claude_launch.SEND_FILES_NOTE == NOTE


def test_send_note_has_no_em_dash():
    assert "—" not in claude_launch.SEND_FILES_NOTE


@pytest.mark.parametrize("version", [None, "", "garbage", "2.1.301", "2.1.302",
                                     "2.1.1000", "2.2.0", "3.0.0", "2.1.301 (Claude Code)"],
                         ids=["none", "empty", "garbage", "at-301", "302", "1000",
                              "2.2", "3.0", "with-suffix"])
def test_note_is_added_for_unknown_or_new_claude(version):
    assert claude_launch.base_system_prompt("frontend", version) == \
        NAME_LINE + "\n\n" + NOTE


@pytest.mark.parametrize("version", ["2.1.300", "2.1.30", "2.0.999", "1.9.0"],
                         ids=["300", "30", "2.0", "1.x"])
def test_note_is_omitted_for_claude_before_2_1_301(version):
    assert claude_launch.base_system_prompt("frontend", version) == NAME_LINE


# ---- /new and friends: inject.launch_session -------------------------------------

def _proc():
    p = AsyncMock()
    p.returncode = 0
    p.communicate = AsyncMock(return_value=(b"", b""))
    return p


@pytest.fixture
def launched(monkeypatch, tmp_path, run_async):
    captured = {}

    async def fake_exec(*args, **kwargs):
        captured["args"] = args
        captured["env"] = kwargs.get("env")
        return _proc()
    monkeypatch.setattr(inject, "_create_subprocess_exec", fake_exec)
    calls = {"n": 0}

    def is_socket(self):
        calls["n"] += 1
        return calls["n"] > 1
    monkeypatch.setattr(inject.Path, "is_socket", is_socket)

    def _go(**kw):
        ok, err = run_async(inject.launch_session("frontend", mode="ask",
                                                  cwd=str(tmp_path), **kw))
        assert ok, err
        return captured
    return _go


def _sets_env(captured) -> bool:
    cmd = captured["args"][-1]
    env = captured.get("env") or {}
    return ("CLAUDE_CODE_ATTACHMENTS_BY_PATH=1" in cmd
            or env.get("CLAUDE_CODE_ATTACHMENTS_BY_PATH") == "1")


def test_launch_exports_attachments_by_path(launched):
    assert _sets_env(launched())


def test_launched_prompt_carries_the_send_note(launched):
    assert NOTE in launched()["args"][-1]


def test_launch_carries_the_note_once(launched):
    cmd = launched()["args"][-1]
    assert cmd.count("SendUserFile tool") == 1


# ---- aipager session: launcher.launch ------------------------------------------

@pytest.fixture
def terminal_launch(monkeypatch):
    calls = []

    class _Fail:
        returncode = 1
        stderr = "stop here\n"
        stdout = ""

    def run(argv, *a, **k):
        calls.append((list(argv) if isinstance(argv, (list, tuple)) else [argv], k))
        return _Fail()
    monkeypatch.setattr(launcher, "_resolve_dtach", lambda: "/fake/dtach")
    monkeypatch.setattr(launcher, "_dtach_works", lambda p: (True, ""))
    monkeypatch.setattr(launcher, "_socket_alive", lambda s: False)
    monkeypatch.setattr(launcher.subprocess, "run", run)
    launcher.launch("frontend")
    return calls


def _flat(calls):
    out = []
    for argv, kw in calls:
        out.extend(str(a) for a in argv)
        env = kw.get("env") or {}
        out.extend(f"{k}={v}" for k, v in env.items())
    return out


def test_terminal_session_sets_attachments_by_path(terminal_launch):
    flat = _flat(terminal_launch)
    assert any("CLAUDE_CODE_ATTACHMENTS_BY_PATH=1" in a for a in flat)


def test_terminal_prompt_carries_the_send_note(terminal_launch):
    assert any(NOTE in a for a in _flat(terminal_launch))


# ---- held answers: newest_key ----------------------------------------------------

def test_newest_key_is_none_without_held_answers():
    assert HELD.newest_key(-100777, kit.SESSION) is None
