"""Tests for dtach_inject.launch_session — resume_id and cwd args."""

from __future__ import annotations

from unittest.mock import AsyncMock

from aipager.dtach import inject as dtach_inject


def _make_proc(returncode: int = 0, stderr: bytes = b""):
    """Return an awaitable mock that yields (stdout, stderr) once."""
    proc = AsyncMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(b"", stderr))
    return proc


def test_launch_session_includes_resume_flag(tmp_path, monkeypatch, run_async):
    """resume_id is passed through to claude --resume."""
    # Stub the subprocess call and the socket-existence check
    captured = {}

    async def _fake_exec(*args, **kwargs):
        captured["args"] = args
        captured["cwd"] = kwargs.get("cwd")
        return _make_proc(returncode=0)

    monkeypatch.setattr(dtach_inject.asyncio, "create_subprocess_exec", _fake_exec)
    # is_socket() is called twice: once for the "already exists" pre-check
    # (must return False) and again during the post-launch appearance wait
    # (must return True so the loop exits successfully).
    calls = {"n": 0}

    def _is_socket(self):
        calls["n"] += 1
        return calls["n"] > 1

    monkeypatch.setattr(dtach_inject.Path, "is_socket", _is_socket)

    ok, err = run_async(dtach_inject.launch_session(
        "jim",
        resume_id="e4f739a9-e19a-4d17-a8c2-12ba1b288907",
        cwd=str(tmp_path),  # real existing dir
    ))
    assert ok, err

    # bash_cmd is the last positional argument; -c flag precedes it
    bash_cmd = captured["args"][-1]
    assert "--resume" in bash_cmd
    assert "e4f739a9-e19a-4d17-a8c2-12ba1b288907" in bash_cmd
    assert captured["cwd"] == str(tmp_path)


def test_launch_session_no_resume_flag_when_id_missing(tmp_path, monkeypatch, run_async):
    """Without resume_id, claude --resume is NOT injected."""
    captured = {}

    async def _fake_exec(*args, **kwargs):
        captured["args"] = args
        return _make_proc(returncode=0)

    monkeypatch.setattr(dtach_inject.asyncio, "create_subprocess_exec", _fake_exec)
    calls = {"n": 0}

    def _is_socket(self):
        calls["n"] += 1
        return calls["n"] > 1

    monkeypatch.setattr(dtach_inject.Path, "is_socket", _is_socket)

    ok, _ = run_async(dtach_inject.launch_session("jim"))
    assert ok
    bash_cmd = captured["args"][-1]
    assert "--resume" not in bash_cmd


def test_launch_session_rejects_when_cwd_missing(tmp_path, monkeypatch, run_async):
    """If the persisted cwd has been deleted, fail loudly before exec."""
    monkeypatch.setattr(dtach_inject.Path, "is_socket", lambda self: False)
    bogus = tmp_path / "nope"  # doesn't exist
    ok, err = run_async(dtach_inject.launch_session(
        "jim",
        resume_id="abc",
        cwd=str(bogus),
    ))
    assert ok is False
    assert "original project dir is gone" in err
    assert str(bogus) in err


def test_launch_session_strips_inherited_oauth_token(tmp_path, monkeypatch, run_async):
    """A CLAUDE_CODE_OAUTH_TOKEN in the daemon env pins every spawned
    claude to that token, overriding fresh creds from .credentials.json.
    The launch must unset it so each session reads from credentials."""
    captured = {}

    async def _fake_exec(*args, **kwargs):
        captured["args"] = args
        return _make_proc(returncode=0)

    monkeypatch.setattr(dtach_inject.asyncio, "create_subprocess_exec", _fake_exec)
    calls = {"n": 0}

    def _is_socket(self):
        calls["n"] += 1
        return calls["n"] > 1

    monkeypatch.setattr(dtach_inject.Path, "is_socket", _is_socket)

    ok, _ = run_async(dtach_inject.launch_session("jim"))
    assert ok
    bash_cmd = captured["args"][-1]
    assert "unset CLAUDE_CODE_OAUTH_TOKEN" in bash_cmd
    assert "unset CLAUDECODE" in bash_cmd
