"""The daemon must not narrate the operator's machine into a Telegram chat.

`format_provenance`'s lines — absolute binary paths, versions, every
install found — are journal material. Telegram is not the journal: those
messages sit on Telegram's servers, get forwarded and screenshotted, and
in a team scope reach every member of every configured group. The daemon
used to send them on every start, healthy or not, so the overwhelmingly
common case was broadcasting the home directory (hence the OS username)
and the machine's layout when nothing was wrong at all.

These tests pin the three rules that replaced that:
  1. a healthy start says nothing;
  2. an *undetermined* auth state says nothing (no crying wolf);
  3. a genuinely logged-out state says one thing, and that thing names
     no path, no home directory, no filename and no version.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from unittest.mock import AsyncMock
from unittest.mock import MagicMock as _M

import pytest

from aipager import claude_bootstrap, claude_resolve, daemon_secrets
from aipager.claude_bootstrap import ProvenanceInfo
from aipager.claude_resolve import AuthStatus, format_auth_notice
from aipager.cli import daemon

from tests.test_cli_daemon_run import _patch_components

DETAILED = "claude: /home/aly/.local/bin/claude (2.1.235) · auth: oauth_token (env)"


def _run_daemon_with(monkeypatch, provenance):
    monkeypatch.setattr("aipager.config.BOT_TOKEN", "tok")
    monkeypatch.setattr("aipager.config.CHAT_ID", "12345")
    monkeypatch.setattr("aipager.config.OBSERVER_BOTS", [])
    bot, _hook, _monitor, _registry, _, _ = _patch_components(monkeypatch)
    bot.send_startup_notice = AsyncMock()
    monkeypatch.setattr(
        "aipager.claude_bootstrap.bootstrap_claude_settings", lambda: provenance)
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(daemon._run_daemon("bot_username"))
        # The auth check is a fire-and-forget task that hands the slow
        # sweep to a worker thread, so a single sleep(0) does NOT drain
        # it — draining that way would have made "sends nothing" pass for
        # the wrong reason, by simply never letting the send happen.
        pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
        if pending:
            loop.run_until_complete(asyncio.gather(*pending,
                                                   return_exceptions=True))
    finally:
        loop.close()
    return bot


# ---- what reaches Telegram at all --------------------------------------

def test_healthy_start_sends_no_telegram_message(monkeypatch):
    """The regression this whole change exists for."""
    monkeypatch.setattr(claude_bootstrap, "recover_auth_or_notice",
                        lambda p: None)
    bot = _run_daemon_with(monkeypatch, ProvenanceInfo(
        lines=[DETAILED], auth_ok=True,
        pending=claude_bootstrap.PendingAuthCheck("/x/claude", "2.1.235", {})))
    bot.send_startup_notice.assert_not_awaited()


def test_a_valid_credential_is_checked_for_real_and_stays_silent(monkeypatch):
    """A healthy-LOOKING start must still be validated.

    This inverts the previous version of this test, deliberately.
    `claude auth status` reports a credential's presence, not its
    validity — a revoked token answers ``loggedIn: true`` — so skipping
    the real check on a "healthy" start is exactly how an expired token
    stayed invisible while every session hung BUSY forever.
    """
    calls = []

    def _check(pending):
        calls.append(pending)
        return None                      # valid -> nothing to say

    monkeypatch.setattr(claude_bootstrap, "recover_auth_or_notice", _check)
    bot = _run_daemon_with(monkeypatch, ProvenanceInfo(
        lines=[DETAILED], auth_ok=True,
        pending=claude_bootstrap.PendingAuthCheck("/x/claude", "2.1.235", {})))
    assert len(calls) == 1, "a healthy-looking start skipped real validation"
    bot.send_startup_notice.assert_not_awaited()


def test_start_with_no_provenance_at_all_sends_nothing(monkeypatch):
    bot = _run_daemon_with(monkeypatch, None)
    bot.send_startup_notice.assert_not_awaited()


def test_logged_out_start_sends_exactly_one_message(monkeypatch):
    monkeypatch.setattr(claude_bootstrap, "recover_auth_or_notice",
                        lambda p: format_auth_notice(["setup token"]))
    bot = _run_daemon_with(monkeypatch, ProvenanceInfo(
        lines=[DETAILED], auth_ok=False,
        pending=claude_bootstrap.PendingAuthCheck("/x/claude", "2.1.235", {})))
    assert bot.send_startup_notice.await_count == 1


def test_the_detailed_provenance_line_is_never_what_gets_sent(monkeypatch):
    """Belt and braces: even when we DO speak, the journal text is not
    the text that goes out."""
    monkeypatch.setattr(claude_bootstrap, "recover_auth_or_notice",
                        lambda p: format_auth_notice([]))
    bot = _run_daemon_with(monkeypatch, ProvenanceInfo(
        lines=[DETAILED], auth_ok=False,
        pending=claude_bootstrap.PendingAuthCheck("/x/claude", "2.1.235", {})))
    sent = bot.send_startup_notice.await_args.args[0]
    assert sent != DETAILED
    assert "/home/" not in sent


# ---- the message itself leaks nothing ----------------------------------

@pytest.mark.parametrize("kinds", [
    [], ["setup token"], ["API key"], ["stored login"],
    ["setup token", "API key"], ["setup token", "API key", "stored login"],
])
def test_notice_never_contains_machine_detail(kinds):
    text = format_auth_notice(kinds)
    home = str(Path.home())
    forbidden = {
        "/home/": "an absolute home path",
        home: "this machine's home directory",
        "/usr/": "an absolute system path",
        ".credentials.json": "a credential filename",
        "daemon.env": "a credential filename",
        "claude_oauth": "a credential filename",
        "CREDENTIALS_DIRECTORY": "an internal env var name",
        ".local/bin": "an install location",
    }
    for needle, why in forbidden.items():
        assert needle not in text, f"notice leaked {why}: {text!r}"
    assert not re.search(r"\d+\.\d+\.\d+", text), (
        f"notice leaked a version number: {text!r}")
    # The operator still has to be told what to do about it.
    assert "claude auth login" in text


def test_notice_names_the_kind_it_found():
    assert "setup token" in format_auth_notice(["setup token"])
    assert "API key" in format_auth_notice(["API key"])
    assert "couldn't find a credential" in format_auth_notice([])


def test_notice_articles_are_grammatical():
    assert "an API key" in format_auth_notice(["API key"])
    assert "a setup token" in format_auth_notice(["setup token"])


# ---- when the daemon decides to speak ----------------------------------

def _bootstrap_with_auth(monkeypatch, tmp_path, auth, *, recover=None):
    """Drive bootstrap_claude_settings() with everything real stubbed out
    except the auth decision under test."""
    monkeypatch.setattr(claude_bootstrap, "_ensure_bypass_accepted", lambda: False)
    monkeypatch.setattr(claude_bootstrap, "_ensure_workdir_trusted", lambda w: False)
    monkeypatch.setattr(claude_bootstrap, "_ensure_hooks_and_statusline", lambda: False)

    chosen = claude_resolve.ClaudeInstall(
        path=str(tmp_path / "claude"), realpath=str(tmp_path / "claude"),
        version="2.1.235")
    resolved = claude_resolve.ResolvedClaude(chosen=chosen, others=())
    monkeypatch.setattr(claude_resolve, "resolve_claude_binary", lambda: resolved)
    monkeypatch.setattr(daemon_secrets, "build_session_env", lambda: {})
    monkeypatch.setattr(claude_resolve, "detect_auth",
                        lambda *a, **k: auth)
    monkeypatch.setattr(
        claude_bootstrap, "_recover_auth",
        recover or (lambda *a, **k: (False, [])))
    return claude_bootstrap.bootstrap_claude_settings(workdir=str(tmp_path))


def test_healthy_auth_still_schedules_a_real_validation(monkeypatch, tmp_path):
    info = _bootstrap_with_auth(monkeypatch, tmp_path,
                                AuthStatus(True, "oauth_token", "env"))
    assert info.auth_ok is True
    assert info.pending is not None, (
        "auth status cannot see an expired token, so its 'logged in' "
        "verdict must never be the last word")


@pytest.mark.parametrize("source", ["probe-failed", "version-gated"])
def test_undetermined_auth_stays_quiet(monkeypatch, tmp_path, source):
    """A cheap-check timeout or an old binary tells us nothing, so it
    must not itself produce a warning — but it must not suppress the
    real validation either."""
    info = _bootstrap_with_auth(
        monkeypatch, tmp_path,
        AuthStatus(False, "unknown", source, error="whatever"))
    assert info.pending is not None, (
        "an undetermined cheap check is all the more reason to check for real")


def test_definitely_logged_out_defers_the_sweep_off_the_startup_path(
        monkeypatch, tmp_path):
    """bootstrap runs BEFORE bot.start(), and the sweep can burn ~20s in
    blocking subprocesses. It must hand back work to do, not do it."""
    info = _bootstrap_with_auth(monkeypatch, tmp_path,
                                AuthStatus(False, "none", "unknown"))
    assert info.auth_ok is False
    assert info.pending is not None, "the sweep was not deferred"
    assert info.pending is not None
    assert info.pending.claude_path.endswith("claude")


def test_successful_recovery_stays_quiet(monkeypatch, tmp_path):
    """The operator only hears from us if we could not fix it ourselves."""
    monkeypatch.setattr(claude_resolve, "validate_credential",
                        lambda *a, **k: claude_resolve.CredentialCheck("absent"))
    monkeypatch.setattr(claude_bootstrap, "_recover_auth",
                        lambda *a, **k: (True, ["setup token"]))
    pending = claude_bootstrap.PendingAuthCheck("/x/claude", "2.1.235", {})
    assert claude_bootstrap.recover_auth_or_notice(pending) is None


def test_failed_recovery_reports_what_it_found(monkeypatch, tmp_path):
    monkeypatch.setattr(claude_resolve, "validate_credential",
                        lambda *a, **k: claude_resolve.CredentialCheck("absent"))
    monkeypatch.setattr(claude_bootstrap, "_recover_auth",
                        lambda *a, **k: (False, ["setup token"]))
    pending = claude_bootstrap.PendingAuthCheck("/x/claude", "2.1.235", {})
    text = claude_bootstrap.recover_auth_or_notice(pending)
    assert "setup token" in text
    assert "/home/" not in text


# ---- the deferred check, as the daemon actually runs it ----------------

def test_finish_auth_check_runs_the_sweep_and_sends_only_when_needed(
        monkeypatch):
    from unittest.mock import AsyncMock as _AM

    bot = _M()
    bot.send_startup_notice = _AM()
    monkeypatch.setattr(claude_bootstrap, "recover_auth_or_notice",
                        lambda pending: "\u26a0\ufe0f needs login")
    info = ProvenanceInfo(
        lines=[DETAILED], auth_ok=False,
        pending=claude_bootstrap.PendingAuthCheck("/x/claude", "2.1.235", {}))

    asyncio.run(daemon._finish_auth_check(bot, info))

    bot.send_startup_notice.assert_awaited_once_with("\u26a0\ufe0f needs login")


def test_finish_auth_check_stays_silent_when_the_sweep_recovers(monkeypatch):
    from unittest.mock import AsyncMock as _AM

    bot = _M()
    bot.send_startup_notice = _AM()
    monkeypatch.setattr(claude_bootstrap, "recover_auth_or_notice",
                        lambda pending: None)
    info = ProvenanceInfo(
        lines=[DETAILED], auth_ok=False,
        pending=claude_bootstrap.PendingAuthCheck("/x/claude", "2.1.235", {}))

    asyncio.run(daemon._finish_auth_check(bot, info))

    bot.send_startup_notice.assert_not_awaited()


def test_finish_auth_check_never_raises(monkeypatch):
    """It is a fire-and-forget task; an escape would surface only as a
    'Task exception was never retrieved' warning."""
    from unittest.mock import AsyncMock as _AM

    bot = _M()
    bot.send_startup_notice = _AM()

    def _boom(pending):
        raise RuntimeError("sweep exploded")

    monkeypatch.setattr(claude_bootstrap, "recover_auth_or_notice", _boom)
    info = ProvenanceInfo(
        lines=[DETAILED], auth_ok=False,
        pending=claude_bootstrap.PendingAuthCheck("/x/claude", "2.1.235", {}))

    asyncio.run(daemon._finish_auth_check(bot, info))  # must not raise
    bot.send_startup_notice.assert_not_awaited()


def test_provenance_info_refuses_a_state_that_would_skip_validation():
    """``auth_ok`` has no default, and a resolved binary must always
    carry deferred work."""
    with pytest.raises(TypeError):
        ProvenanceInfo(lines=["x"])          # type: ignore[call-arg]
    # A resolved binary with nothing pending would silently skip
    # validation — the very hole this change closes.
    with pytest.raises(ValueError):
        ProvenanceInfo(lines=["x"], auth_ok=True, pending=None)


# ---- the recovery sweep -------------------------------------------------

def _sweep_env(monkeypatch, tmp_path, *, daemon_env=None, shell_token=None,
               works=(), creds_file=False):
    """Arrange the sweep's world. ``works`` is the set of token values
    `claude auth status` will accept."""
    env_path = tmp_path / "daemon.env"
    if daemon_env is not None:
        env_path.write_text(daemon_env)
    monkeypatch.setattr(daemon_secrets, "DAEMON_ENV_PATH", env_path)

    fake_home = tmp_path / "home"
    (fake_home / ".claude").mkdir(parents=True)
    if creds_file:
        (fake_home / ".claude" / ".credentials.json").write_text("{}")
    monkeypatch.setattr("pathlib.Path.home", staticmethod(lambda: fake_home))

    monkeypatch.setattr(
        "aipager.service._discover_token_via_login_shell", lambda: shell_token)

    def _validate(path, env, **k):
        tok = env.get("CLAUDE_CODE_OAUTH_TOKEN")
        state = "valid" if tok in works else "rejected"
        return claude_resolve.CredentialCheck(state)

    # The sweep validates candidates for real now; stubbing detect_auth
    # would leave the actual probe unmocked (and conftest would refuse it).
    monkeypatch.setattr(claude_resolve, "validate_credential", _validate)
    monkeypatch.setattr(daemon_secrets, "_recovered", {})
    return env_path


def test_sweep_recovers_a_working_login_shell_token(monkeypatch, tmp_path):
    """The exact shape this feature exists for: a token exported from
    ~/.bashrc, invisible to a systemd unit."""
    _sweep_env(monkeypatch, tmp_path, shell_token="good", works={"good"})
    recovered, found = claude_bootstrap._recover_auth("/x/claude", "2.1.235", {})
    assert recovered is True
    assert "setup token" in found
    # and real session launches must now get it
    assert daemon_secrets.build_session_env({})["CLAUDE_CODE_OAUTH_TOKEN"] == "good"


def test_sweep_overrides_a_credential_that_was_proven_broken(monkeypatch, tmp_path):
    """The file source was probed and rejected; the recovered one was
    probed and accepted. Preferring the broken one would strand every
    session on the login screen."""
    _sweep_env(monkeypatch, tmp_path, shell_token="good", works={"good"})
    recovered, _ = claude_bootstrap._recover_auth(
        "/x/claude", "2.1.235", {"CLAUDE_CODE_OAUTH_TOKEN": "stale"})
    assert recovered is True
    env = daemon_secrets.build_session_env({"CLAUDE_CODE_OAUTH_TOKEN": "stale"})
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "good"


def test_sweep_reports_kinds_but_recovers_nothing_when_all_are_rejected(
        monkeypatch, tmp_path):
    _sweep_env(monkeypatch, tmp_path,
               daemon_env="ANTHROPIC_API_KEY=nope\n",
               shell_token="also-nope", works=set(), creds_file=True)
    recovered, found = claude_bootstrap._recover_auth("/x/claude", "2.1.235", {})
    assert recovered is False
    assert set(found) == {"API key", "stored login", "setup token"}


def test_sweep_never_writes_to_the_credential_file(monkeypatch, tmp_path):
    """Strictly read-only — it must never write, repair, or rename a
    user's credential file (see _stash_expired_credentials_file, which
    does rename one and is deliberately not reachable from here)."""
    env_path = _sweep_env(monkeypatch, tmp_path,
                          daemon_env="CLAUDE_CODE_OAUTH_TOKEN=filetok\n",
                          shell_token="good", works={"good"})
    before = env_path.read_bytes()
    before_files = sorted(p.name for p in tmp_path.rglob("*"))
    claude_bootstrap._recover_auth("/x/claude", "2.1.235", {})
    assert env_path.read_bytes() == before, "the sweep rewrote daemon.env"
    assert sorted(p.name for p in tmp_path.rglob("*")) == before_files, (
        "the sweep created or renamed a file")


def test_sweep_survives_a_login_shell_that_explodes(monkeypatch, tmp_path):
    _sweep_env(monkeypatch, tmp_path, works=set())

    def _boom():
        raise OSError("no shell")

    monkeypatch.setattr(
        "aipager.service._discover_token_via_login_shell", _boom)
    recovered, found = claude_bootstrap._recover_auth("/x/claude", "2.1.235", {})
    assert recovered is False
    assert found == []


def test_recovered_credential_is_in_process_only(monkeypatch, tmp_path):
    """Nothing is persisted, so a credential the operator later fixes
    properly is picked up normally instead of being shadowed by a memo.

    The first version of this test called ``_sweep_env`` without
    ``daemon_env=``, so the file it asserted about was never created and
    ``not env_path.exists()`` short-circuited the whole assertion — it
    passed even if the sweep had written the token, or done nothing at
    all. Seed the file, then scan every file under tmp_path for the
    recovered value, so there is no path by which "not persisted" can be
    true for the wrong reason.
    """
    env_path = _sweep_env(monkeypatch, tmp_path,
                          daemon_env="CLAUDE_CODE_OAUTH_TOKEN=stale\n",
                          shell_token="good", works={"good"})
    assert env_path.exists(), "fixture precondition: the file must exist"

    recovered, _ = claude_bootstrap._recover_auth("/x/claude", "2.1.235", {})
    assert recovered is True, "precondition: the sweep must have succeeded"

    # In memory, yes...
    assert daemon_secrets.build_session_env({})["CLAUDE_CODE_OAUTH_TOKEN"] == "good"
    # ...on disk, nowhere.
    assert "good" not in env_path.read_text()
    leaked = [
        f for f in tmp_path.rglob("*")
        if f.is_file() and "good" in f.read_text(errors="ignore")
    ]
    assert leaked == [], f"the recovered token was persisted to {leaked}"
