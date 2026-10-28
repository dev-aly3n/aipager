"""aipager never turns Claude Code's risky-removal countdown off (roadmap
8.128; design.md criterion 1). ``CLAUDE_CODE_DISABLE_DANGEROUS_RM_TIMEOUT``
made Auto skip asking (its classifier decided, and ran an rm with no
person), so the countdown stays on in every mode, with aipager's clock
and order guard. Neither launch builder sets it: the daemon's
``inject.launch_session`` command is read at its spawn seam, and no file
of aipager's own (``launcher.launch``'s argv builder included) names it."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

import aipager
from aipager.claude_resolve import ClaudeInstall, ResolvedClaude
from aipager.dtach import inject

SWITCH = "DANGEROUS_RM_TIMEOUT"
CLAUDE = "/opt/claude"


def _proc():
    p = AsyncMock()
    p.returncode = 0
    p.communicate = AsyncMock(return_value=(b"", b""))
    return p


@pytest.mark.parametrize("mode", ["ask", "auto", "full"])
def test_a_daemon_launch_never_sets_the_switch(monkeypatch, tmp_path, run_async, mode):
    captured = {}

    async def fake_exec(*args, **kwargs):
        captured["args"] = args
        captured["env"] = kwargs.get("env") or {}
        return _proc()

    calls = {"n": 0}

    def is_socket(self):
        calls["n"] += 1
        return calls["n"] > 1

    monkeypatch.setattr(inject, "_create_subprocess_exec", fake_exec)
    monkeypatch.setattr(inject.Path, "is_socket", is_socket)
    monkeypatch.setattr(inject.claude_resolve, "try_resolve_claude_binary",
                        lambda **k: ResolvedClaude(chosen=ClaudeInstall(
                            path=CLAUDE, realpath=CLAUDE, version="2.1.311")))
    ok, err = run_async(inject.launch_session("frontend", mode=mode, cwd=str(tmp_path)))
    assert ok, err
    args = captured["args"]
    assert args[3:6] == ("-Ez", "bash", "-c")
    assert CLAUDE in args[-1]
    assert all(SWITCH not in str(a) for a in args)
    assert all(SWITCH not in str(k) for k in captured["env"])


def test_no_aipager_file_names_the_switch():
    root = Path(aipager.__file__).parent
    named = [str(p.relative_to(root)) for p in root.rglob("*")
             if p.is_file() and p.suffix in (".py", ".sh", ".json", ".jsonl", ".yaml",
                                              ".toml", ".txt", ".md")
             and "__pycache__" not in p.parts
             and "CLAUDE_CODE_DISABLE_DANGEROUS_RM_TIMEOUT" in p.read_text(errors="replace")]
    assert named == []
