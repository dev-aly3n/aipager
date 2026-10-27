"""Tests for dtach_inject.launch_session — resume_id and cwd args."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

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

    monkeypatch.setattr(dtach_inject, "_create_subprocess_exec", _fake_exec)
    # The flag is only passed when a conversation actually exists to resume;
    # this test is about the pass-through, so say it does.
    monkeypatch.setattr(dtach_inject, "_conversation_exists", lambda sid, projects=None: True)
    # is_socket() is called twice: once for the "already exists" pre-check
    # (must return False) and again during the post-launch appearance wait
    # (must return True so the loop exits successfully).
    calls = {"n": 0}

    def _is_socket(self):
        calls["n"] += 1
        return calls["n"] > 1

    monkeypatch.setattr(dtach_inject.Path, "is_socket", _is_socket)

    ok, err = run_async(dtach_inject.launch_session(
        "jim", mode="ask",
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

    monkeypatch.setattr(dtach_inject, "_create_subprocess_exec", _fake_exec)
    calls = {"n": 0}

    def _is_socket(self):
        calls["n"] += 1
        return calls["n"] > 1

    monkeypatch.setattr(dtach_inject.Path, "is_socket", _is_socket)

    ok, _ = run_async(dtach_inject.launch_session("jim", mode="ask"))
    assert ok
    bash_cmd = captured["args"][-1]
    assert "--resume" not in bash_cmd


def test_launch_session_passes_explicit_env_with_daemon_credential(
        tmp_path, monkeypatch, run_async):
    """criterion 15: the subprocess call includes an explicit `env=`
    containing the token when the credential file provides one — never
    solely ambient inheritance (today's `create_subprocess_exec` passed
    no `env=` at all)."""
    captured = {}

    async def _fake_exec(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _make_proc(returncode=0)

    monkeypatch.setattr(dtach_inject, "_create_subprocess_exec", _fake_exec)
    calls = {"n": 0}

    def _is_socket(self):
        calls["n"] += 1
        return calls["n"] > 1

    monkeypatch.setattr(dtach_inject.Path, "is_socket", _is_socket)

    daemon_env = tmp_path / "daemon.env"
    daemon_env.write_text("CLAUDE_CODE_OAUTH_TOKEN=sk-from-daemon-env\n")
    monkeypatch.setattr(dtach_inject.daemon_secrets, "DAEMON_ENV_PATH", daemon_env)
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    # Ambient inheritance must NOT be what carries the token — clear it
    # from the calling process's own environ.
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)

    ok, _ = run_async(dtach_inject.launch_session("jim", mode="ask"))
    assert ok
    assert "env" in captured["kwargs"]
    assert captured["kwargs"]["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-from-daemon-env"


def test_launch_session_quotes_claude_bin_with_metacharacters(
        tmp_path, monkeypatch, run_async):
    """A claude binary path containing shell metacharacters must not
    execute injected content — inject.py:567's quoting fix.

    Every sibling value on the bash_cmd line (resume, model, sys_prompt)
    was already shlex.quote()d; the binary was the lone exception, safe
    only while it came verbatim from shutil.which(). Once it can come
    from config (claude_path) or AIPAGER_CLAUDE_BIN — i.e. whatever the
    resolver hands back — an unquoted value is a shell-injection sink.
    """
    captured = {}

    async def _fake_exec(*args, **kwargs):
        captured["args"] = args
        return _make_proc(returncode=0)

    monkeypatch.setattr(dtach_inject, "_create_subprocess_exec", _fake_exec)
    calls = {"n": 0}

    def _is_socket(self):
        calls["n"] += 1
        return calls["n"] > 1

    monkeypatch.setattr(dtach_inject.Path, "is_socket", _is_socket)
    # Simulate a resolved (or overridden) binary path an attacker-controlled
    # config value could plausibly contain: a shell metacharacter sequence
    # that would run a second command if concatenated unquoted.
    malicious = "/tmp/claude; touch /tmp/pwned"
    from aipager.claude_resolve import ClaudeInstall, ResolvedClaude
    fake_resolved = ResolvedClaude(
        chosen=ClaudeInstall(path=malicious, realpath=malicious, version="2.1.235"),
    )
    monkeypatch.setattr(dtach_inject.claude_resolve, "try_resolve_claude_binary",
                        lambda **kw: fake_resolved)

    ok, _ = run_async(dtach_inject.launch_session("jim", mode="ask"))
    assert ok
    bash_cmd = captured["args"][-1]
    # The whole malicious string must appear as a single shlex-quoted
    # token, so bash's own parser (not string interpolation) decides
    # where the token ends.
    import shlex as _shlex
    quoted = _shlex.quote(malicious)
    assert quoted in bash_cmd
    # If it were interpolated unquoted (the pre-fix bug), the raw path
    # would be immediately followed by a space (the separator before
    # `perms`); quoted, it is immediately followed by a closing quote
    # character instead.
    assert f"{malicious} " not in bash_cmd


def test_launch_session_rejects_when_cwd_missing(tmp_path, monkeypatch, run_async, caplog):
    """If the persisted cwd has been deleted, fail loudly before exec."""
    monkeypatch.setattr(dtach_inject.Path, "is_socket", lambda self: False)
    bogus = tmp_path / "nope"  # doesn't exist
    with caplog.at_level("WARNING", logger="aipager.dtach.inject"):
        ok, err = run_async(dtach_inject.launch_session(
            "jim", mode="ask",
            resume_id="abc",
            cwd=str(bogus),
        ))
    assert ok is False
    assert "original project dir is gone" in err
    # ...but NOT which directory. This string is rendered into a Telegram
    # message; in a group scope the absolute path would show every member
    # the operator's home directory and OS username.
    assert str(bogus) not in err
    # The operator still needs to know which one, so it goes to the journal.
    assert str(bogus) in caplog.text


def test_launch_session_strips_inherited_oauth_token(tmp_path, monkeypatch, run_async):
    """A CLAUDE_CODE_OAUTH_TOKEN in the daemon env pins every spawned
    claude to that token, overriding fresh creds from .credentials.json.
    When the credentials file is fresh, the launch strips the env token
    so each session reads from credentials."""
    captured = {}

    async def _fake_exec(*args, **kwargs):
        captured["args"] = args
        return _make_proc(returncode=0)

    monkeypatch.setattr(dtach_inject, "_create_subprocess_exec", _fake_exec)
    calls = {"n": 0}

    def _is_socket(self):
        calls["n"] += 1
        return calls["n"] > 1

    monkeypatch.setattr(dtach_inject.Path, "is_socket", _is_socket)
    monkeypatch.setattr(dtach_inject, "_credentials_file_is_fresh",
                        lambda path=None: True)

    ok, _ = run_async(dtach_inject.launch_session("jim", mode="ask"))
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

    monkeypatch.setattr(dtach_inject, "_create_subprocess_exec", _fake_exec)
    calls = {"n": 0}

    def _is_socket(self):
        calls["n"] += 1
        return calls["n"] > 1

    monkeypatch.setattr(dtach_inject.Path, "is_socket", _is_socket)
    monkeypatch.setattr(dtach_inject, "_credentials_file_is_fresh",
                        lambda path=None: False)

    ok, _ = run_async(dtach_inject.launch_session("jim", mode="ask"))
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
    """Claude Code's default config home at <tmp_path>/.claude, the folder
    :func:`_write_creds` writes into (aipager.claude_paths)."""
    from aipager import claude_paths
    monkeypatch.setattr(claude_paths, "DEFAULT_CONFIG_HOME", Path(tmp_path) / ".claude")


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

    monkeypatch.setattr(dtach_inject,
                        "_create_subprocess_exec", _fake_exec)
    calls = {"n": 0}

    def _is_socket(self):
        calls["n"] += 1
        return calls["n"] > 1

    monkeypatch.setattr(dtach_inject.Path, "is_socket", _is_socket)

    ok, _ = run_async(dtach_inject.launch_session(
        "jim", mode="ask", cwd=str(tmp_path),
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

def test_stash_no_op_when_only_refresh_token_populated(
        tmp_path, monkeypatch):
    """Refresh-token-only file: empty accessToken, non-empty
    refreshToken. Claude Code may successfully refresh via that token,
    so leave the file in place — this is NOT a dead-placeholder."""
    _patch_home(monkeypatch, tmp_path)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-token-1")
    _write_creds(str(tmp_path), {"claudeAiOauth": {
        "accessToken": "", "refreshToken": "rf-x", "expiresAt": 0,
    }})
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


# ---- _credentials_file_is_dead_placeholder ----------------------------

def test_dead_placeholder_true_when_both_tokens_empty(tmp_path, monkeypatch):
    """A real headless install's exact shape."""
    _patch_home(monkeypatch, tmp_path)
    _write_creds(str(tmp_path), {"claudeAiOauth": {
        "accessToken": "", "refreshToken": "", "expiresAt": 0,
    }})
    assert dtach_inject._credentials_file_is_dead_placeholder() is True


def test_dead_placeholder_false_when_accessToken_populated(tmp_path, monkeypatch):
    _patch_home(monkeypatch, tmp_path)
    _write_creds(str(tmp_path), {"claudeAiOauth": {
        "accessToken": "sk-x", "refreshToken": "",
    }})
    assert dtach_inject._credentials_file_is_dead_placeholder() is False


def test_dead_placeholder_false_when_only_refresh_populated(tmp_path,
                                                            monkeypatch):
    """Refresh-token-only: Claude Code may still refresh, don't touch."""
    _patch_home(monkeypatch, tmp_path)
    _write_creds(str(tmp_path), {"claudeAiOauth": {
        "accessToken": "", "refreshToken": "rf-x",
    }})
    assert dtach_inject._credentials_file_is_dead_placeholder() is False


def test_dead_placeholder_false_when_missing_refresh_key(tmp_path, monkeypatch):
    """No refreshToken key at all — fail-open, can't confirm both empty."""
    _patch_home(monkeypatch, tmp_path)
    _write_creds(str(tmp_path), {"claudeAiOauth": {"accessToken": ""}})
    assert dtach_inject._credentials_file_is_dead_placeholder() is False


def test_dead_placeholder_false_when_wrong_type(tmp_path, monkeypatch):
    """refreshToken is a non-string — fail-open."""
    _patch_home(monkeypatch, tmp_path)
    _write_creds(str(tmp_path), {"claudeAiOauth": {
        "accessToken": "", "refreshToken": 0,
    }})
    assert dtach_inject._credentials_file_is_dead_placeholder() is False


def test_dead_placeholder_false_when_missing_file(tmp_path, monkeypatch):
    _patch_home(monkeypatch, tmp_path)
    assert dtach_inject._credentials_file_is_dead_placeholder() is False


def test_dead_placeholder_false_when_malformed_json(tmp_path, monkeypatch):
    _patch_home(monkeypatch, tmp_path)
    _write_creds(str(tmp_path), "{not valid json")
    assert dtach_inject._credentials_file_is_dead_placeholder() is False


# ---- stash integration: dead placeholder + refresh-only ---------------

def test_stash_renames_dead_placeholder_when_env_token_set(
        tmp_path, monkeypatch):
    """The new fix path: dead placeholder + env token present → stash so
    claude falls back to env instead of shadowing with the dead file."""
    _patch_home(monkeypatch, tmp_path)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-token-1")
    _write_creds(str(tmp_path), {"claudeAiOauth": {
        "accessToken": "", "refreshToken": "",
        "expiresAt": 0, "refreshTokenExpiresAt": 0,
        "subscriptionType": "max",
    }})
    creds = tmp_path / ".claude" / ".credentials.json"
    stash = creds.with_suffix(creds.suffix + ".stale")
    result = dtach_inject._stash_expired_credentials_file()
    assert result == stash
    assert not creds.exists()
    assert stash.exists()


def test_stash_no_op_for_refresh_only_placeholder(tmp_path, monkeypatch):
    """Regression guard: refresh-token-only files must not be stashed."""
    _patch_home(monkeypatch, tmp_path)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-token-1")
    _write_creds(str(tmp_path), {"claudeAiOauth": {
        "accessToken": "", "refreshToken": "rf-x", "expiresAt": 0,
    }})
    creds = tmp_path / ".claude" / ".credentials.json"
    result = dtach_inject._stash_expired_credentials_file()
    assert result is None
    assert creds.exists()



# ---- the permission mode's flag (three modes) ----------------------------

_MODE_FLAGS = ("--permission-mode default", "--permission-mode auto",
               "--dangerously-skip-permissions")


def _launch_cmd(monkeypatch, run_async, tmp_path, mode):
    captured = {}

    async def _fake_exec(*args, **kwargs):
        captured["args"] = args
        return _make_proc(returncode=0)

    monkeypatch.setattr(dtach_inject, "_create_subprocess_exec", _fake_exec)
    calls = {"n": 0}

    def _is_socket(self):
        calls["n"] += 1
        return calls["n"] > 1

    monkeypatch.setattr(dtach_inject.Path, "is_socket", _is_socket)
    ok, err = run_async(dtach_inject.launch_session("jim", mode=mode, cwd=str(tmp_path)))
    return ok, err, (captured["args"][-1] if "args" in captured else None)




@pytest.mark.parametrize("mode, flag", [
    ("ask", "--permission-mode default"),
    ("auto", "--permission-mode auto"),
    ("full", "--dangerously-skip-permissions"),
], ids=["ask", "auto", "full"])
def test_launch_passes_exactly_one_mode_flag(tmp_path, monkeypatch, run_async, mode, flag):
    ok, err, cmd = _launch_cmd(monkeypatch, run_async, tmp_path, mode)
    assert ok, err
    assert flag in cmd
    assert [f for f in _MODE_FLAGS if f in cmd] == [flag]
    assert cmd.count("--permission-mode") == (0 if mode == "full" else 1)
    assert "--allow-dangerously" not in cmd


@pytest.mark.parametrize("bad", [True, False, "", "bypass"],
                         ids=["bool-true", "bool-false", "empty", "unknown"])
def test_launch_refuses_anything_but_a_mode_id(tmp_path, monkeypatch, run_async, bad):
    ok, err, cmd = _launch_cmd(monkeypatch, run_async, tmp_path, bad)
    assert not ok
    assert err == "Invalid permission mode"
    assert cmd is None  # nothing was spawned


# ---- files Claude sends: the switch and the note ----------------------------

def _send_launch_cmd(tmp_path, monkeypatch, run_async, *, version="2.1.302", extra=None):
    from aipager.claude_resolve import ClaudeInstall, ResolvedClaude
    captured = {}

    async def _fake_exec(*args, **kwargs):
        captured["args"] = args
        return _make_proc(returncode=0)
    monkeypatch.setattr(dtach_inject, "_create_subprocess_exec", _fake_exec)
    calls = {"n": 0}

    def _is_socket(self):
        calls["n"] += 1
        return calls["n"] > 1
    monkeypatch.setattr(dtach_inject.Path, "is_socket", _is_socket)
    resolved = ResolvedClaude(chosen=ClaudeInstall(path="/opt/claude",
                                                   realpath="/opt/claude", version=version))
    monkeypatch.setattr(dtach_inject.claude_resolve, "try_resolve_claude_binary",
                        lambda **kw: resolved)
    ok, err = run_async(dtach_inject.launch_session(
        "jim", mode="ask", cwd=str(tmp_path), system_prompt_extra=extra))
    assert ok, err
    return captured["args"][-1]


def _prompt_of(bash_cmd: str) -> str:
    import shlex as _shlex
    words = _shlex.split(bash_cmd)
    return words[words.index("--append-system-prompt") + 1]


def test_launch_exports_attachments_by_path(tmp_path, monkeypatch, run_async):
    bash_cmd = _send_launch_cmd(tmp_path, monkeypatch, run_async)
    assert "export CLAUDE_CODE_ATTACHMENTS_BY_PATH=1; " in bash_cmd
    assert bash_cmd.index("CLAUDE_CODE_ATTACHMENTS_BY_PATH") < bash_cmd.index("/opt/claude")


def test_personal_mode_prompt_carries_the_telegram_note(tmp_path, monkeypatch, run_async):
    from aipager.claude_launch import TELEGRAM_NOTE
    prompt = _prompt_of(_send_launch_cmd(tmp_path, monkeypatch, run_async))
    assert prompt == ('Your session name is "jim". When users address you by this name, '
                      'respond naturally -- it is your name in this session.\n\n'
                      + TELEGRAM_NOTE)


def test_scoped_prompt_has_the_note_once_before_session_md(tmp_path, monkeypatch,
                                                           run_async):
    from aipager.claude_launch import TELEGRAM_NOTE
    prompt = _prompt_of(_send_launch_cmd(tmp_path, monkeypatch, run_async,
                                    extra="# SESSION.md\nrules"))
    assert prompt.count(TELEGRAM_NOTE) == 1
    assert prompt.index(TELEGRAM_NOTE) < prompt.index("# SESSION.md")


def test_an_old_claude_gets_the_no_send_note(tmp_path, monkeypatch, run_async):
    from aipager.claude_launch import TELEGRAM_NOTE, TELEGRAM_NOTE_NO_SEND
    bash_cmd = _send_launch_cmd(tmp_path, monkeypatch, run_async, version="2.1.299")
    assert TELEGRAM_NOTE not in _prompt_of(bash_cmd)
    assert _prompt_of(bash_cmd).endswith("\n\n" + TELEGRAM_NOTE_NO_SEND)
    # The switch is set whatever the version: it does nothing on an older one.
    assert "export CLAUDE_CODE_ATTACHMENTS_BY_PATH=1; " in bash_cmd


# ---- the recorded system prompt is not replayed (8.125) ---------------------

def _claude_install(tmp_path, version):
    """A claude link in tmp_path pointing at a file for *version*, as the
    native installer lays it out: (link path, real path)."""
    versions = tmp_path / "versions"
    versions.mkdir(exist_ok=True)
    target = versions / version
    target.write_text("")
    link = tmp_path / "claude"
    if link.is_symlink():
        link.unlink()
    link.symlink_to(target)
    return str(link), str(target)


def _snapshot_launch_words(tmp_path, monkeypatch, run_async, *, version, resume_id=None,
                           resolve=True, installed=None, refreshed="same"):
    """The words of claude's command line for a launch whose resolver
    remembers *version*; *installed* (a version) moves the link to that
    build first, and *refreshed* is what probing it again returns ("same":
    the install as it is now, None: it does not verify, "other": another
    file answers for 2.1.311, "raise": the probe fails with an error)."""
    import shlex as _shlex

    from aipager.claude_resolve import ClaudeInstall, ResolvedClaude
    captured, refreshes = {}, []

    async def _fake_exec(*args, **kwargs):
        captured["args"] = args
        return _make_proc(returncode=0)
    monkeypatch.setattr(dtach_inject, "_create_subprocess_exec", _fake_exec)
    calls = {"n": 0}

    def _is_socket(self):
        calls["n"] += 1
        return calls["n"] > 1
    monkeypatch.setattr(dtach_inject.Path, "is_socket", _is_socket)
    monkeypatch.setattr(dtach_inject, "_conversation_exists",
                        lambda sid, projects=None: True)
    path, real = _claude_install(tmp_path, version)
    resolved = ResolvedClaude(chosen=ClaudeInstall(path=path, realpath=real, version=version))
    if installed is not None:
        path, real = _claude_install(tmp_path, installed)

    def _reprobe():
        import threading
        refreshes.append(threading.get_ident())
        if refreshed == "raise":
            raise ValueError("embedded null byte")
        if refreshed is None:
            return None
        if refreshed == "other":
            other = str(tmp_path / "elsewhere-claude")
            return ClaudeInstall(path=other, realpath=other, version="2.1.311")
        return ClaudeInstall(path=path, realpath=real, version=installed or version)
    monkeypatch.setattr(dtach_inject.claude_resolve, "try_resolve_claude_binary",
                        lambda **kw: resolved if resolve else None)
    monkeypatch.setattr(dtach_inject.claude_resolve, "reprobe_chosen", _reprobe)
    ok, err = run_async(dtach_inject.launch_session(
        "jim", mode="ask", cwd=str(tmp_path), resume_id=resume_id,
        system_prompt_extra="# SESSION.md\nroles"))
    assert ok, err
    words = _shlex.split(captured["args"][-1])
    return words[:words.index("--append-system-prompt") + 2], path, refreshes


@pytest.mark.parametrize("resume_id", [None, "e4f739a9-e19a-4d17-a8c2-12ba1b288907"],
                         ids=["fresh", "resume"])
def test_launch_turns_the_prompt_snapshot_off(tmp_path, monkeypatch, run_async, resume_id):
    words, path, refreshes = _snapshot_launch_words(
        tmp_path, monkeypatch, run_async, version="2.1.310", resume_id=resume_id)
    at = words.index("--system-prompt-snapshot")
    assert words[at + 1] == "off"
    assert words.count("--system-prompt-snapshot") == 1
    # On claude's own command line, with the prompt it would otherwise not send.
    assert words.index(path) < at
    assert words[-1].endswith("# SESSION.md\nroles")
    if resume_id:
        assert words[words.index("--resume") + 1] == resume_id
    assert refreshes == []        # the binary did not move: no second probe


def test_launch_after_claude_updated_itself_keeps_the_flag(tmp_path, monkeypatch, run_async):
    """Claude Code's installer points the link at each new version's file:
    the daemon reads the version again (in a worker thread: ``--version``
    may take seconds, never on the event loop) and passes the flag."""
    import threading
    words, path, refreshes = _snapshot_launch_words(
        tmp_path, monkeypatch, run_async, version="2.1.310", installed="2.1.311",
        resume_id="e4f739a9-e19a-4d17-a8c2-12ba1b288907")
    assert len(refreshes) == 1 and refreshes[0] != threading.get_ident()
    assert words[words.index("--system-prompt-snapshot") + 1] == "off"
    assert path in words


@pytest.mark.parametrize("version, resolve, installed, refreshed", [
    ("2.1.258", True, None, "same"),
    ("2.1.310", False, None, "same"),
    # The link now leads to an older build than the flag.
    ("2.1.310", True, "2.1.250", "same"),
    # It moved and reading it again fails: not known.
    ("2.1.310", True, "2.1.311", None),
    # It moved to an older build, and another file answers the probe for a
    # newer one: that version is not the launched binary's.
    ("2.1.310", True, "2.1.250", "other"),
    # The probe itself fails with an error: the session still starts.
    ("2.1.310", True, "2.1.311", "raise"),
], ids=["older-claude", "unknown-claude", "moved-to-older", "moved-unreadable",
        "moved-other-answer", "moved-probe-raises"])
def test_launch_never_passes_the_flag_to_a_claude_that_may_refuse_it(
        tmp_path, monkeypatch, run_async, version, resolve, installed, refreshed):
    words, _path, probes = _snapshot_launch_words(
        tmp_path, monkeypatch, run_async, version=version,
        resume_id="e4f739a9-e19a-4d17-a8c2-12ba1b288907", resolve=resolve,
        installed=installed, refreshed=refreshed)
    assert len(probes) == (1 if installed else 0)
    assert "--system-prompt-snapshot" not in words
    assert "--resume" in words
    if resolve:
        assert _path in words                # still the binary it was


# ---- why the session ends: claude's exit is recorded (step 4, T4.1) ---------

def _exit_launch_cmd(tmp_path, monkeypatch, run_async, claude_path="/opt/claude"):
    from aipager.claude_resolve import ClaudeInstall, ResolvedClaude
    captured = {}

    async def _fake_exec(*args, **kwargs):
        captured["args"] = args
        return _make_proc(returncode=0)
    monkeypatch.setattr(dtach_inject, "_create_subprocess_exec", _fake_exec)
    calls = {"n": 0}

    def _is_socket(self):
        calls["n"] += 1
        return calls["n"] > 1
    monkeypatch.setattr(dtach_inject.Path, "is_socket", _is_socket)
    resolved = ResolvedClaude(chosen=ClaudeInstall(path=claude_path,
                                                   realpath=claude_path,
                                                   version="2.1.302"))
    monkeypatch.setattr(dtach_inject.claude_resolve, "try_resolve_claude_binary",
                        lambda **kw: resolved)
    ok, err = run_async(dtach_inject.launch_session("jim", mode="ask", cwd=str(tmp_path)))
    assert ok, err
    assert captured["args"][4:6] == ("bash", "-c")
    return captured["args"][-1]


def test_the_exit_record_is_removed_before_claude_and_written_after(
        tmp_path, monkeypatch, run_async):
    import shlex as _shlex

    from aipager.dtach import bg_jobs
    cmd = _exit_launch_cmd(tmp_path, monkeypatch, run_async)
    quoted = _shlex.quote(str(bg_jobs.exit_path("claude-jim")))
    claude_at = cmd.index("/opt/claude ")
    assert cmd.index(f"p={quoted}; ") < cmd.index('rm -f -- "$p";') < claude_at
    after = cmd[cmd.index("--append-system-prompt"):]
    assert after.index("rc=$?;") < after.index("printf") < after.index(
        'mv -f -- "$p.tmp" "$p";')
    # claude is not the last command: bash cannot exec it away.
    assert cmd.rsplit(";", 1)[-1].strip() == "exit $rc"


def test_the_launch_shell_records_a_stand_in_claudes_exit(tmp_path, monkeypatch, run_async):
    """The captured ``bash -c`` command, run for real on a stand-in claude
    (no claude, no dtach): the earlier launch's record is gone while the
    stand-in runs, and its exit code is recorded after it."""
    import os
    import subprocess

    from aipager.dtach import bg_jobs
    stand_in = tmp_path / "stand-in"
    stand_in.write_text('#!/bin/sh\n[ -e "$REC" ] && exit 9\nexit 5\n')
    stand_in.chmod(0o755)
    record = bg_jobs.exit_path("claude-jim")
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text('{"code": 0, "start": 1, "end": 1}')
    cmd = _exit_launch_cmd(tmp_path, monkeypatch, run_async, claude_path=str(stand_in))
    before = int(time.time())
    done = subprocess.run(["bash", "-c", cmd], cwd=tmp_path, capture_output=True,
                          timeout=30, env={"PATH": os.environ.get("PATH", ""),
                                           "REC": str(record)})
    assert done.returncode == 5, done.stderr
    data = json.loads(record.read_text())
    assert data["code"] == 5
    assert before <= data["start"] <= data["end"] <= int(time.time())
