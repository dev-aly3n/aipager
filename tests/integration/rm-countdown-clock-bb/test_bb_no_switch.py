"""aipager never sets Claude Code's switch that turns the risky-rm
countdown off (spec requirement 1; design.md success criterion 1;
entrypoints.md "Env vars")."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from aipager.dtach import inject

SWITCH = "CLAUDE_CODE_DISABLE_DANGEROUS_RM_TIMEOUT"
PACKAGE = Path(__file__).resolve().parents[3] / "aipager"


def _proc():
    proc = AsyncMock()
    proc.returncode = 0
    proc.communicate = AsyncMock(return_value=(b"", b""))
    return proc


@pytest.fixture
def launched(monkeypatch, run_async, tmp_path):
    """Launch through the daemon's builder; return every exec's argv."""
    calls: list[tuple] = []

    async def _fake_exec(*args, **kwargs):
        calls.append(args)
        return _proc()

    monkeypatch.setattr(inject, "_create_subprocess_exec", _fake_exec)
    n = {"k": 0}

    def _is_socket(self):
        n["k"] += 1
        return n["k"] > 1

    monkeypatch.setattr(inject.Path, "is_socket", _is_socket)

    def go(mode):
        calls.clear()
        n["k"] = 0
        ok, err = run_async(inject.launch_session("frontend", mode=mode, cwd=str(tmp_path)))
        assert ok, err
        return calls

    return go


@pytest.mark.parametrize("mode", ["ask", "auto", "full"])
def test_the_launch_command_has_no_switch(launched, mode):
    argv = launched(mode)
    assert argv and not any("DANGEROUS_RM_TIMEOUT" in str(a) for call in argv for a in call)


@pytest.mark.parametrize("mode", ["ask", "auto", "full"])
def test_the_launch_command_was_captured(launched, mode):
    """Control: the capture really sees the claude command line."""
    argv = launched(mode)
    assert any("claude" in str(a) for call in argv for a in call)


def test_no_file_in_the_package_names_the_switch():
    hits = [str(p.relative_to(PACKAGE)) for p in PACKAGE.rglob("*")
            if p.is_file() and p.suffix in {".py", ".sh", ".json", ".jsonl", ".yaml", ".toml",
                                             ".txt", ".md", ".html", ".js"}
            and SWITCH in p.read_text(encoding="utf-8", errors="replace")]
    assert hits == []
