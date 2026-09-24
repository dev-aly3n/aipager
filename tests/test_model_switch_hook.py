"""PreModelSwitch: ``aipager-hook`` allows (skipping Claude Code's "Switch
model?" cache confirmation) ONLY a switch aipager itself typed — roadmap 8.35.

The channel is a marker file (``aipager.dtach.model_switch_marker``). The
autouse ``_isolate_model_switch_marker`` fixture in conftest keeps every
marker under ``tmp_path``; nothing here touches the real runtime dir.
"""

from __future__ import annotations

import io
import json
import os
import sys
import time

import pytest

from aipager.dtach import model_switch_marker as msm
from aipager.dtach import notify_hook

BASE = "/unused-because-marker_path-is-redirected"
SESSION = "claude-cat"
SID = "11111111-2222-3333-4444-555555555555"


def _payload(**over):
    p = {
        "hook_event_name": "PreModelSwitch",
        "session_id": SID,
        "source": "command",
        "from_model": "claude-sonnet-5",
        "to_model": "claude-opus-5-5",
        "requested_model": "claude-opus-5-5",
    }
    p.update(over)
    return p


def _mark(model="claude-opus-5-5", *, session=SESSION, sid=SID, ttl=30.0):
    assert msm.write_marker(BASE, session, model, sid, ttl=ttl)
    return msm.marker_path(BASE, session)


def _allowed(decision):
    return (decision is not None
            and decision["hookSpecificOutput"]["hookEventName"] == "PreModelSwitch"
            and decision["hookSpecificOutput"]["permissionDecision"] == "allow")


# ===== allow ================================================================

def test_the_switch_aipager_typed_is_allowed():
    _mark()
    assert _allowed(msm.decide(BASE, SESSION, _payload()))


def test_an_alias_matches_on_requested_model():
    """`/model opus`: Claude Code resolves to_model to an id, but
    requested_model is what was typed."""
    _mark("opus")
    assert _allowed(msm.decide(BASE, SESSION, _payload(
        requested_model="opus", to_model="claude-opus-5-5")))


def test_the_match_also_accepts_to_model_and_ignores_case():
    _mark("Claude-Opus-5-5[1M]")
    assert _allowed(msm.decide(BASE, SESSION, _payload(
        requested_model="something-normalised", to_model="claude-opus-5-5[1m]")))


def test_a_marker_with_no_claude_session_id_matches_nothing():
    """A nested `claude` started inside the session inherits its dtach
    name; only the Claude session id tells them apart, so it is required."""
    _mark(sid="")
    assert msm.decide(BASE, SESSION, _payload()) is None
    # ...even when the payload has no id either: two unknowns are not a match.
    assert msm.decide(BASE, SESSION, _payload(session_id="")) is None
    no_sid = _payload()
    del no_sid["session_id"]
    assert msm.decide(BASE, SESSION, no_sid) is None


# ===== no decision ==========================================================

def test_no_marker_no_decision():
    assert msm.decide(BASE, SESSION, _payload()) is None


def test_a_different_model_gets_no_decision_and_keeps_the_marker():
    path = _mark("claude-opus-5-5")
    assert msm.decide(BASE, SESSION, _payload(
        requested_model="sonnet", to_model="claude-sonnet-5")) is None
    assert path.exists(), "a non-matching switch must not consume the marker"


def test_an_expired_marker_gets_no_decision_and_is_removed():
    path = _mark(ttl=30.0)
    assert msm.decide(BASE, SESSION, _payload(), now=time.time() + 31) is None
    assert not path.exists()


def test_another_dtach_session_gets_no_decision():
    _mark(session="claude-other")
    assert msm.decide(BASE, SESSION, _payload()) is None


def test_another_claude_session_gets_no_decision():
    _mark()
    assert msm.decide(BASE, SESSION, _payload(session_id="someone-else")) is None


@pytest.mark.parametrize("source", ["picker", "sdk", "auto", "resume", "", None])
def test_a_switch_not_typed_as_a_command_gets_no_decision(source):
    """The terminal picker, the SDK and automatic switches are never ours."""
    _mark()
    assert msm.decide(BASE, SESSION, _payload(source=source)) is None


@pytest.mark.parametrize("payload", [
    None, [], "PreModelSwitch", 42,
    {},
    {"hook_event_name": "PreToolUse", "source": "command", "session_id": SID,
     "requested_model": "claude-opus-5-5", "to_model": "claude-opus-5-5"},
    _payload(requested_model=None, to_model=None),
    _payload(requested_model=123, to_model=["claude-opus-5-5"]),
    _payload(session_id=None),
    _payload(session_id=""),
])
def test_malformed_payloads_never_raise(payload):
    _mark()
    assert msm.decide(BASE, SESSION, payload) is None


@pytest.mark.parametrize("content", [
    b"", b"not json", b"[]", b'"x"',
    json.dumps({"model": "claude-opus-5-5", "claude_session_id": SID}).encode(),
    json.dumps({"model": "claude-opus-5-5", "claude_session_id": SID,
                "expires_at": True}).encode(),
    json.dumps({"model": "claude-opus-5-5", "claude_session_id": SID,
                "expires_at": "later"}).encode(),
    json.dumps({"model": "", "claude_session_id": SID,
                "expires_at": time.time() + 30}).encode(),
    json.dumps({"model": 5, "claude_session_id": SID,
                "expires_at": time.time() + 30}).encode(),
    json.dumps({"model": "claude-opus-5-5", "claude_session_id": 7,
                "expires_at": time.time() + 30}).encode(),
])
def test_a_malformed_marker_gets_no_decision(content):
    path = msm.marker_path(BASE, SESSION)
    path.write_bytes(content)
    assert msm.decide(BASE, SESSION, _payload()) is None


def test_an_oversized_marker_gets_no_decision():
    path = msm.marker_path(BASE, SESSION)
    path.write_bytes(json.dumps({
        "model": "claude-opus-5-5", "expires_at": time.time() + 30,
        "claude_session_id": SID, "pad": "x" * 5000,
    }).encode())
    assert msm.decide(BASE, SESSION, _payload()) is None


def test_a_symlinked_marker_is_not_followed(tmp_path):
    real = tmp_path / "elsewhere.json"
    real.write_text(json.dumps({"model": "claude-opus-5-5", "claude_session_id": SID,
                                "expires_at": time.time() + 30}))
    os.symlink(real, msm.marker_path(BASE, SESSION))
    assert msm.decide(BASE, SESSION, _payload()) is None


def test_a_marker_owned_by_someone_else_is_ignored(monkeypatch):
    _mark()
    real_uid = os.getuid()
    monkeypatch.setattr(msm, "_getuid", lambda: real_uid + 1)
    assert msm.decide(BASE, SESSION, _payload()) is None


@pytest.mark.parametrize("session", ["", "../x", "a/b", "claude cat", None, "x" * 200])
def test_an_unsafe_session_name_is_refused(session):
    assert msm.write_marker(BASE, session, "opus") is False
    assert msm.decide(BASE, session, _payload()) is None


# ===== used exactly once ====================================================

def test_the_marker_is_consumed_once():
    path = _mark()
    assert _allowed(msm.decide(BASE, SESSION, _payload()))
    assert not path.exists()
    assert msm.decide(BASE, SESSION, _payload()) is None
    # no claim leftovers either
    assert list(path.parent.iterdir()) == []


def test_losing_the_claim_race_means_no_decision(monkeypatch):
    _mark()

    def _someone_else_got_it(src, dst):
        raise FileNotFoundError(src)
    monkeypatch.setattr(msm, "_rename", _someone_else_got_it)
    assert msm.decide(BASE, SESSION, _payload()) is None


def test_a_marker_replaced_between_check_and_claim_is_not_trusted(monkeypatch):
    """The daemon wrote a newer marker (another model) after the hook read
    the old one: the claimed bytes differ, so no decision."""
    path = _mark("claude-opus-5-5")
    real_rename = os.rename

    def _replaced_first(src, dst):
        msm.write_marker(BASE, SESSION, "claude-haiku-4-5", SID)
        real_rename(src, dst)
    monkeypatch.setattr(msm, "_rename", _replaced_first)
    assert msm.decide(BASE, SESSION, _payload()) is None
    assert not list(path.parent.glob("*.claimed.*"))


def test_decide_never_raises_even_if_the_filesystem_does(monkeypatch):
    _mark()

    def _boom(*_a, **_k):
        raise RuntimeError("disk on fire")
    monkeypatch.setattr(msm, "_read_own_file", _boom)
    assert msm.decide(BASE, SESSION, _payload()) is None


# ===== write / clear ========================================================

def test_write_is_atomic_and_private():
    path = _mark()
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert list(path.parent.glob("*.tmp.*")) == []
    body = json.loads(path.read_text())
    assert body["model"] == "claude-opus-5-5" and body["claude_session_id"] == SID


def test_clear_removes_the_marker_and_tolerates_absence():
    path = _mark()
    msm.clear_marker(BASE, SESSION)
    assert not path.exists()
    msm.clear_marker(BASE, SESSION)          # second time: no error


# ===== aipager-hook end to end (in process, no socket) ======================

def _run_hook(monkeypatch, tmp_path, stdin_text, session=SESSION):
    monkeypatch.setattr(notify_hook, "SOCKET_PATH", str(tmp_path / "nope.sock"))
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin_text))
    sent = []
    monkeypatch.setattr(notify_hook.socket, "socket",
                        lambda *a, **k: sent.append(a) or (_ for _ in ()).throw(
                            AssertionError("PreModelSwitch must not touch a socket")))
    notify_hook._run(session, [b""])
    return sent


def test_hook_prints_allow_for_the_initiated_switch(monkeypatch, tmp_path, capsys):
    _mark()
    started = time.monotonic()
    _run_hook(monkeypatch, tmp_path, json.dumps(_payload()))
    assert time.monotonic() - started < 1.0
    out = json.loads(capsys.readouterr().out)
    assert out == {"hookSpecificOutput": {
        "hookEventName": "PreModelSwitch", "permissionDecision": "allow",
        "permissionDecisionReason": msm.ALLOW_REASON}}


def test_hook_prints_nothing_for_someone_elses_switch(monkeypatch, tmp_path, capsys):
    _run_hook(monkeypatch, tmp_path, json.dumps(_payload()))
    assert capsys.readouterr().out == ""


def test_hook_prints_nothing_and_survives_a_decide_crash(monkeypatch, tmp_path, capsys):
    _mark()

    def _boom(*_a, **_k):
        raise RuntimeError("boom")
    monkeypatch.setattr(msm, "decide", _boom)
    _run_hook(monkeypatch, tmp_path, json.dumps(_payload()))
    assert capsys.readouterr().out == ""


def test_hook_does_not_forward_premodelswitch_to_the_daemon(monkeypatch, tmp_path, capsys):
    """Covered by the socket stub raising: a forward would fail the test."""
    _mark()
    sent = _run_hook(monkeypatch, tmp_path, json.dumps(_payload()))
    assert sent == []


def test_hook_uses_the_session_from_its_environment_not_the_payload(
    monkeypatch, tmp_path, capsys,
):
    _mark(session="claude-other")
    _run_hook(monkeypatch, tmp_path, json.dumps(_payload()), session=SESSION)
    assert capsys.readouterr().out == ""


# ===== daemon side ==========================================================

def test_a_mini_app_style_switch_writes_the_marker_and_a_failed_send_clears_it():
    from aipager.bot.session_ops import (
        clear_model_switch_pending, mark_model_switch_sent,
    )
    from aipager.state import Status, TrackedSession

    sess = TrackedSession(name=SESSION, label="cat", status=Status.IDLE,
                          model_name="Sonnet 5", claude_session_id=SID)
    mark_model_switch_sent(sess, "claude-opus-5-5")
    path = msm.marker_path(BASE, SESSION)
    body = json.loads(path.read_text())
    assert body["model"] == "claude-opus-5-5"
    assert body["claude_session_id"] == SID
    assert _allowed(msm.decide(BASE, SESSION, _payload()))

    mark_model_switch_sent(sess, "claude-opus-5-5")
    clear_model_switch_pending(sess)
    assert not path.exists()


def test_a_telegram_switch_writes_the_marker_for_the_typed_model(
    mk_bot, mk_update, run_async, monkeypatch,
):
    from unittest.mock import AsyncMock

    from aipager.state import Status, TrackedSession

    bot = mk_bot()
    sess = TrackedSession(name=SESSION, label="cat", status=Status.IDLE,
                          model_name="Sonnet 5", claude_session_id=SID)
    bot.registry._sessions[SESSION] = sess
    bot.registry.last_active_session = SESSION
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter",
                        AsyncMock(return_value=True))
    bot._react = AsyncMock()
    bot._confirm_model_feedback = AsyncMock()
    run_async(bot._send_command(mk_update("Opus 5.5 1M"), "/model claude-opus-5-5[1m]"))
    body = json.loads(msm.marker_path(BASE, SESSION).read_text())
    assert body["model"] == "claude-opus-5-5[1m]"


def test_a_confirmed_switch_tidies_a_marker_nobody_claimed(run_async):
    """Older Claude Code (no PreModelSwitch) never claims it; the daemon
    removes it once the statusline shows the switch landed."""
    import asyncio

    from aipager.bot.session_ops import await_model_change, mark_model_switch_sent
    from aipager.state import Status, TrackedSession

    sess = TrackedSession(name=SESSION, label="cat", status=Status.IDLE,
                          model_name="Sonnet 5")
    mark_model_switch_sent(sess, "opus")

    async def _run():
        asyncio.get_running_loop().call_later(0.05, setattr, sess, "model_name", "Opus 5.5")
        return await await_model_change(sess, "Sonnet 5", timeout=2.0, interval=0.01)
    assert run_async(_run()) == "Opus 5.5"
    assert not msm.marker_path(BASE, SESSION).exists()


# ===== settings.json rollout ================================================

def test_the_wizard_adds_premodelswitch_idempotently_with_its_timeout(monkeypatch):
    from aipager.wizard import settings_patch

    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    settings = {"hooks": {"Stop": [{"hooks": [
        {"type": "command", "command": "/usr/bin/aipager-hook"}]}]}}
    settings_patch._merge_hooks(settings)
    first = json.dumps(settings, sort_keys=True)
    assert settings["hooks"]["PreModelSwitch"] == [{"hooks": [{
        "type": "command", "command": "/usr/bin/aipager-hook", "timeout": 5}]}]
    settings_patch._merge_hooks(settings)
    assert json.dumps(settings, sort_keys=True) == first


def test_the_wizard_backfills_the_timeout_on_an_existing_entry(monkeypatch):
    from aipager.wizard import settings_patch

    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    settings = {"hooks": {"PreModelSwitch": [{"hooks": [
        {"type": "command", "command": "/usr/bin/aipager-hook"}]}]}}
    settings_patch._merge_hooks(settings)
    assert settings["hooks"]["PreModelSwitch"][0]["hooks"] == [
        {"type": "command", "command": "/usr/bin/aipager-hook", "timeout": 5}]


def test_daemon_start_adds_premodelswitch_to_an_existing_install_idempotently(
    tmp_path, monkeypatch,
):
    """How every earlier hook addition reached existing installs: the
    daemon's own bootstrap wires any missing event on start."""
    from aipager import claude_bootstrap

    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [
        {"type": "command", "command": "/usr/bin/aipager-hook"}]}]},
        "statusLine": {"type": "command", "command": "/usr/bin/aipager-statusline"}}))
    monkeypatch.setattr(claude_bootstrap, "_SETTINGS", settings)
    monkeypatch.setattr(
        claude_bootstrap.shutil, "which",
        lambda cmd: f"/usr/bin/{cmd}"
        if cmd in {"aipager-hook", "aipager-statusline"} else None)
    assert claude_bootstrap._ensure_hooks_and_statusline() is True
    data = json.loads(settings.read_text())
    assert data["hooks"]["PreModelSwitch"] == [{"hooks": [{
        "type": "command", "command": "/usr/bin/aipager-hook", "timeout": 5}]}]
    # only PreModelSwitch carries a timeout
    for event, blocks in data["hooks"].items():
        if event != "PreModelSwitch":
            for b in blocks:
                for h in b["hooks"]:
                    assert "timeout" not in h, event
    before = settings.read_text()
    claude_bootstrap._ensure_hooks_and_statusline()
    assert settings.read_text() == before


def test_a_fifo_at_the_marker_path_cannot_hang_the_hook():
    """Anything that can create the file could make it a FIFO; opening one
    for reading blocks until a writer appears — the hook must not."""
    import threading

    path = msm.marker_path(BASE, SESSION)
    os.mkfifo(path)
    result = []
    t = threading.Thread(
        target=lambda: result.append(msm.decide(BASE, SESSION, _payload())),
        daemon=True)
    t.start()
    t.join(2.0)
    try:
        assert not t.is_alive(), "decide() blocked on a FIFO"
        assert result == [None]
    finally:
        if t.is_alive():   # unblock the reader so the thread can finish
            fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
            os.close(fd)
            t.join(2.0)


def test_an_empty_model_on_both_sides_is_not_a_match():
    """A marker with no model must not match a payload that names none."""
    path = msm.marker_path(BASE, SESSION)
    path.write_text(json.dumps({"model": "  ", "claude_session_id": SID,
                                "expires_at": time.time() + 30}))
    assert msm.decide(BASE, SESSION, _payload(requested_model="", to_model="")) is None


def test_an_unsafe_session_name_never_reaches_the_filesystem():
    """A name with a slash would point the marker path somewhere else; even
    with a valid marker planted exactly there, no decision."""
    planted = msm.marker_path(BASE, "a/b")
    planted.parent.mkdir(parents=True, exist_ok=True)
    planted.write_text(json.dumps({"model": "claude-opus-5-5", "claude_session_id": SID,
                                   "expires_at": time.time() + 30}))
    assert msm.decide(BASE, "a/b", _payload()) is None
    assert planted.exists()


def test_clearing_an_unsafe_session_name_touches_nothing():
    planted = msm.marker_path(BASE, "a/b")
    planted.parent.mkdir(parents=True, exist_ok=True)
    planted.write_text("{}")
    msm.clear_marker(BASE, "a/b")
    assert planted.exists()


def test_the_default_marker_lifetime_is_short_and_inside_the_pending_window():
    """A marker must never outlive the refusal that stops a second /model
    from being typed into an open "Switch model?" question."""
    from aipager.bot.session_ops import MODEL_SWITCH_PENDING_SECONDS

    assert msm.MARKER_TTL_SECONDS <= MODEL_SWITCH_PENDING_SECONDS
    assert msm.write_marker(BASE, SESSION, "claude-opus-5-5", SID)   # default TTL
    later = time.time() + msm.MARKER_TTL_SECONDS + 1
    assert msm.decide(BASE, SESSION, _payload(), now=later) is None
    assert msm.MARKER_TTL_SECONDS <= 30


def test_daemon_and_hook_meet_in_the_same_directory(
    monkeypatch, tmp_path, capsys, real_marker_path,
):
    """End to end through the real filesystem layout: the daemon writes
    the marker where ITS socket lives, the hook looks where ITS socket
    lives, and with the same socket path (the session env carries the
    daemon's) the hook finds it. Uses the real marker_path, not the
    conftest redirect, still entirely inside tmp_path."""
    from aipager.bot.session_ops import mark_model_switch_sent
    from aipager.state import Status, TrackedSession

    monkeypatch.setattr(msm, "marker_path", real_marker_path)
    sock = tmp_path / "rt" / "aipager.sock"
    sock.parent.mkdir()
    monkeypatch.setattr("aipager.config.SOCKET_PATH", str(sock))

    sess = TrackedSession(name=SESSION, label="cat", status=Status.IDLE,
                          model_name="Sonnet 5", claude_session_id=SID)
    mark_model_switch_sent(sess, "claude-opus-5-5")
    assert (sock.parent / f"aipager-modelswitch-{SESSION}.json").exists()

    monkeypatch.setattr(notify_hook, "SOCKET_PATH", str(sock))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(_payload())))
    notify_hook._run(SESSION, [b""])
    assert _allowed(json.loads(capsys.readouterr().out))
