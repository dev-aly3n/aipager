"""Tests for dtach_inject.launch_session — resume_id and cwd args."""

from __future__ import annotations

import json
import time
from pathlib import Path
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
    When the credentials file is fresh, the launch strips the env token
    so each session reads from credentials."""
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
    monkeypatch.setattr(dtach_inject, "_credentials_file_is_fresh",
                        lambda: True)

    ok, _ = run_async(dtach_inject.launch_session("jim"))
    assert ok
    bash_cmd = captured["args"][-1]
    assert "unset CLAUDE_CODE_OAUTH_TOKEN" in bash_cmd
    assert "unset CLAUDECODE" in bash_cmd


def test_launch_session_keeps_oauth_token_when_no_credentials_file(
        tmp_path, monkeypatch, run_async):
    """Headless / setup-token deployments have no fresh credentials
    file; the env token IS the credential. Stripping it kills the only
    working auth, so the launch must keep it."""
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
    monkeypatch.setattr(dtach_inject, "_credentials_file_is_fresh",
                        lambda: False)

    ok, _ = run_async(dtach_inject.launch_session("jim"))
    assert ok
    bash_cmd = captured["args"][-1]
    assert "unset CLAUDE_CODE_OAUTH_TOKEN" not in bash_cmd
    assert "unset CLAUDECODE" in bash_cmd  # unrelated, always stripped


# ---- _credentials_file_is_fresh ---------------------------------------

def _write_creds(home: str, payload) -> None:
    """Write a payload to <home>/.claude/.credentials.json."""
    d = Path(home) / ".claude"
    d.mkdir(parents=True, exist_ok=True)
    (d / ".credentials.json").write_text(
        payload if isinstance(payload, str) else json.dumps(payload)
    )


def _patch_home(monkeypatch, tmp_path):
    monkeypatch.setattr(dtach_inject.Path, "home",
                        classmethod(lambda cls: tmp_path))


def test_credentials_file_is_fresh_missing_file_returns_false(
        tmp_path, monkeypatch):
    _patch_home(monkeypatch, tmp_path)
    assert dtach_inject._credentials_file_is_fresh() is False


def test_credentials_file_is_fresh_malformed_json_returns_false(
        tmp_path, monkeypatch):
    _patch_home(monkeypatch, tmp_path)
    _write_creds(str(tmp_path), "{not valid json")
    assert dtach_inject._credentials_file_is_fresh() is False


def test_credentials_file_is_fresh_missing_oauth_key_returns_false(
        tmp_path, monkeypatch):
    _patch_home(monkeypatch, tmp_path)
    _write_creds(str(tmp_path), {"otherKey": {"expiresAt": 999}})
    assert dtach_inject._credentials_file_is_fresh() is False


def test_credentials_file_is_fresh_missing_expires_at_returns_false(
        tmp_path, monkeypatch):
    _patch_home(monkeypatch, tmp_path)
    _write_creds(str(tmp_path), {"claudeAiOauth": {"accessToken": "x"}})
    assert dtach_inject._credentials_file_is_fresh() is False


def test_credentials_file_is_fresh_expired_returns_false(
        tmp_path, monkeypatch):
    _patch_home(monkeypatch, tmp_path)
    past_ms = int((time.time() - 3600) * 1000)
    _write_creds(str(tmp_path),
                 {"claudeAiOauth": {"accessToken": "sk-old-token",
                                    "expiresAt": past_ms}})
    assert dtach_inject._credentials_file_is_fresh() is False


def test_credentials_file_is_fresh_future_returns_true(
        tmp_path, monkeypatch):
    _patch_home(monkeypatch, tmp_path)
    future_ms = int((time.time() + 3600) * 1000)
    _write_creds(str(tmp_path),
                 {"claudeAiOauth": {"expiresAt": future_ms}})
    assert dtach_inject._credentials_file_is_fresh() is True


def test_credentials_file_is_fresh_wrong_type_returns_false(
        tmp_path, monkeypatch):
    _patch_home(monkeypatch, tmp_path)
    _write_creds(str(tmp_path),
                 {"claudeAiOauth": {"expiresAt": "not a number"}})
    assert dtach_inject._credentials_file_is_fresh() is False


# ---- _stash_expired_credentials_file -----------------------------------

def test_stash_no_op_when_env_token_missing(tmp_path, monkeypatch):
    """Env token unset → do NOT stash even an expired file. Env token
    is not our fallback in that case, so removing the file would leave
    claude with no auth at all."""
    _patch_home(monkeypatch, tmp_path)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    past_ms = int((time.time() - 3600) * 1000)
    _write_creds(str(tmp_path),
                 {"claudeAiOauth": {"accessToken": "sk-old-token",
                                    "expiresAt": past_ms}})
    creds = tmp_path / ".claude" / ".credentials.json"
    assert dtach_inject._stash_expired_credentials_file() is None
    assert creds.exists()  # untouched


def test_stash_no_op_when_creds_file_missing(tmp_path, monkeypatch):
    _patch_home(monkeypatch, tmp_path)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-token-1")
    assert dtach_inject._stash_expired_credentials_file() is None


def test_stash_no_op_when_creds_fresh(tmp_path, monkeypatch):
    """Fresh file wins — don't downgrade the user's auth to the env
    token unnecessarily. Existing strip logic handles this."""
    _patch_home(monkeypatch, tmp_path)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-token-1")
    future_ms = int((time.time() + 3600) * 1000)
    _write_creds(str(tmp_path),
                 {"claudeAiOauth": {"expiresAt": future_ms}})
    creds = tmp_path / ".claude" / ".credentials.json"
    assert dtach_inject._stash_expired_credentials_file() is None
    assert creds.exists()  # fresh file preserved


def test_stash_renames_expired_creds_when_env_token_set(tmp_path,
                                                        monkeypatch):
    _patch_home(monkeypatch, tmp_path)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-token-1")
    past_ms = int((time.time() - 3600) * 1000)
    _write_creds(str(tmp_path),
                 {"claudeAiOauth": {"accessToken": "sk-old-token",
                                    "expiresAt": past_ms}})
    creds = tmp_path / ".claude" / ".credentials.json"
    stash = creds.with_suffix(creds.suffix + ".stale")
    result = dtach_inject._stash_expired_credentials_file()
    assert result == stash
    assert not creds.exists()  # moved out
    assert stash.exists()      # into stash slot


def test_stash_overwrites_prior_stale_file(tmp_path, monkeypatch):
    """A pre-existing .stale from a prior stash must be replaced
    atomically, not error out."""
    _patch_home(monkeypatch, tmp_path)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-token-1")
    past_ms = int((time.time() - 3600) * 1000)
    _write_creds(str(tmp_path),
                 {"claudeAiOauth": {"accessToken": "sk-old-token",
                                    "expiresAt": past_ms}})
    creds = tmp_path / ".claude" / ".credentials.json"
    stash = creds.with_suffix(creds.suffix + ".stale")
    stash.write_text('{"older": "stash contents"}')
    result = dtach_inject._stash_expired_credentials_file()
    assert result == stash
    assert not creds.exists()
    # The stash file was replaced with the previously-current expired
    # creds file — not the older stash contents.
    assert "expiresAt" in stash.read_text()


def test_stash_returns_none_on_rename_error(tmp_path, monkeypatch):
    """A file-op failure (permission, EBUSY, disk full) must not raise
    — the launcher continues and the existing strip logic makes the
    best decision it can."""
    _patch_home(monkeypatch, tmp_path)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-token-1")
    past_ms = int((time.time() - 3600) * 1000)
    _write_creds(str(tmp_path),
                 {"claudeAiOauth": {"accessToken": "sk-old-token",
                                    "expiresAt": past_ms}})

    def _boom(self, target):
        raise OSError("simulated rename failure")

    monkeypatch.setattr(dtach_inject.Path, "replace", _boom)
    assert dtach_inject._stash_expired_credentials_file() is None


def test_launch_session_stashes_expired_creds_and_keeps_env_token(
        tmp_path, monkeypatch, run_async):
    """End-to-end: launch_session against a home with an expired creds
    file + env token stashes the file AND keeps the env token in the
    launcher (unset NOT injected)."""
    _patch_home(monkeypatch, tmp_path)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-token-1")
    past_ms = int((time.time() - 3600) * 1000)
    _write_creds(str(tmp_path),
                 {"claudeAiOauth": {"accessToken": "sk-old-token",
                                    "expiresAt": past_ms}})
    creds = tmp_path / ".claude" / ".credentials.json"
    stash = creds.with_suffix(creds.suffix + ".stale")

    captured = {}

    async def _fake_exec(*args, **kwargs):
        captured["args"] = args
        return _make_proc(returncode=0)

    monkeypatch.setattr(dtach_inject.asyncio,
                        "create_subprocess_exec", _fake_exec)
    calls = {"n": 0}

    def _is_socket(self):
        calls["n"] += 1
        return calls["n"] > 1

    monkeypatch.setattr(dtach_inject.Path, "is_socket", _is_socket)

    ok, _ = run_async(dtach_inject.launch_session(
        "jim", cwd=str(tmp_path),
    ))
    assert ok
    # File was stashed
    assert not creds.exists()
    assert stash.exists()
    # Env token was kept in the launcher (strip NOT injected)
    bash_cmd = captured["args"][-1]
    assert "unset CLAUDE_CODE_OAUTH_TOKEN" not in bash_cmd
    assert "unset CLAUDECODE" in bash_cmd


# ---- _credentials_file_has_token --------------------------------------

def test_credentials_file_has_token_returns_true_for_populated_file(
        tmp_path, monkeypatch):
    _patch_home(monkeypatch, tmp_path)
    _write_creds(str(tmp_path), {"claudeAiOauth": {"accessToken": "sk-x"}})
    assert dtach_inject._credentials_file_has_token() is True


def test_credentials_file_has_token_returns_false_for_missing_file(
        tmp_path, monkeypatch):
    _patch_home(monkeypatch, tmp_path)
    assert dtach_inject._credentials_file_has_token() is False


def test_credentials_file_has_token_returns_false_for_empty_string(
        tmp_path, monkeypatch):
    """Empty accessToken means the file is a cleared/placeholder — a
    Max-plan container that authenticates via a non-file path (device
    token / account UUID). Renaming it would break auth."""
    _patch_home(monkeypatch, tmp_path)
    _write_creds(str(tmp_path), {"claudeAiOauth": {"accessToken": ""}})
    assert dtach_inject._credentials_file_has_token() is False


def test_credentials_file_has_token_returns_false_for_malformed_json(
        tmp_path, monkeypatch):
    _patch_home(monkeypatch, tmp_path)
    _write_creds(str(tmp_path), "{not valid json")
    assert dtach_inject._credentials_file_has_token() is False


# ---- _stash_expired_credentials_file: empty-token guard ---------------

def test_stash_no_op_when_creds_file_has_empty_access_token(
        tmp_path, monkeypatch):
    """Reproduce Mohamad's shape: empty accessToken + expiresAt=0 +
    env token present. The new guard must prevent the stash so the
    placeholder file is preserved for whatever auth path uses it."""
    _patch_home(monkeypatch, tmp_path)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-token-1")
    _write_creds(str(tmp_path), {"claudeAiOauth": {"accessToken": "",
                                                   "expiresAt": 0}})
    creds = tmp_path / ".claude" / ".credentials.json"
    result = dtach_inject._stash_expired_credentials_file()
    assert result is None
    assert creds.exists()  # untouched


def test_stash_no_op_when_creds_file_missing_access_token_key(
        tmp_path, monkeypatch):
    """No accessToken key at all — fail-open: don't stash."""
    _patch_home(monkeypatch, tmp_path)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-token-1")
    past_ms = int((time.time() - 3600) * 1000)
    _write_creds(str(tmp_path), {"claudeAiOauth": {"expiresAt": past_ms}})
    creds = tmp_path / ".claude" / ".credentials.json"
    assert dtach_inject._stash_expired_credentials_file() is None
    assert creds.exists()


def test_stash_no_op_when_creds_file_access_token_wrong_type(
        tmp_path, monkeypatch):
    """accessToken is a non-string (int) — fail-open: don't stash."""
    _patch_home(monkeypatch, tmp_path)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-token-1")
    past_ms = int((time.time() - 3600) * 1000)
    _write_creds(str(tmp_path), {"claudeAiOauth": {"accessToken": 42,
                                                   "expiresAt": past_ms}})
    creds = tmp_path / ".claude" / ".credentials.json"
    assert dtach_inject._stash_expired_credentials_file() is None
    assert creds.exists()

