"""design.md success criterion 15:

    "launch_session()'s subprocess call includes an explicit env=
    containing the token when the credential file provides one -- never
    ambient inheritance."

Deliberately asserts on the ``env=`` kwarg's *content*, not merely that
launch "succeeded" -- ambient inheritance (the pre-feature behaviour,
where no env= was passed at all and the daemon's own os.environ simply
happened to carry the token) would pass a weaker test that only checked
the call didn't raise. Here, the daemon's own os.environ deliberately
does NOT carry the token (monkeypatch.delenv), so the only way the
subprocess sees it is via the explicit env= table.
"""
from __future__ import annotations

import asyncio

from aipager.dtach.inject import launch_session


class _FakeDtachProc:
    def __init__(self):
        self.returncode = 1  # short-circuit launch_session's socket-poll loop

    async def communicate(self):
        return b"", b"fake dtach: not really run"


def test_session_subprocess_env_carries_token_from_daemon_env_file(
    tmp_path, monkeypatch, run_async,
):
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)

    import aipager.daemon_secrets as ds
    ds.DAEMON_ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    ds.DAEMON_ENV_PATH.write_text(
        "CLAUDE_CODE_OAUTH_TOKEN=sk-test-token-abc123\n", encoding="utf-8",
    )

    captured = {}

    async def _fake_create_subprocess_exec(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _FakeDtachProc()

    monkeypatch.setattr(
        asyncio, "create_subprocess_exec", _fake_create_subprocess_exec,
    )

    ok, err = run_async(launch_session("envinjsession"))

    assert "kwargs" in captured, "launch_session never reached create_subprocess_exec"
    env = captured["kwargs"].get("env")
    assert env is not None, (
        "launch_session must pass env= explicitly -- ambient inheritance "
        "(env=None / omitted) is exactly the bug this criterion guards "
        "against under LoadCredential="
    )
    assert env.get("CLAUDE_CODE_OAUTH_TOKEN") == "sk-test-token-abc123", (
        f"the daemon.env token did not reach the subprocess's env= table: "
        f"{env.get('CLAUDE_CODE_OAUTH_TOKEN')!r}"
    )


def test_session_subprocess_env_is_absent_the_token_when_no_credential_exists(
    tmp_path, monkeypatch, run_async,
):
    """Negative control: without this, a test asserting "token present"
    could pass merely because ambient inheritance leaked a real token
    from the process actually running the suite into env=, not because
    build_session_env() genuinely sourced it from daemon.env.
    """
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)

    import aipager.daemon_secrets as ds
    assert not ds.DAEMON_ENV_PATH.exists(), (
        "test isolation bug: DAEMON_ENV_PATH already exists before this "
        "test wrote anything to it"
    )

    captured = {}

    async def _fake_create_subprocess_exec(*args, **kwargs):
        captured["kwargs"] = kwargs
        return _FakeDtachProc()

    monkeypatch.setattr(
        asyncio, "create_subprocess_exec", _fake_create_subprocess_exec,
    )

    run_async(launch_session("envinjsession2"))

    env = captured["kwargs"].get("env") or {}
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env
