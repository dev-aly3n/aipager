"""Black-box tests for TelegramBot._handle_callback answering Allow /
Allow-always / Deny taps via the PermissionRequest hook-decision channel,
per entrypoints.md.

``aipager.dtach.hook_reply.send_decision`` is never mocked here: a real
AF_UNIX SOCK_DGRAM socket stands in for "the hook is still parked
waiting" (a live listener) or "the hook already gave up / daemon can't
reach it" (an address nothing is listening on), so the callback's own
call into send_decision is exercised for real over a real socket. Only
``aipager.dtach.inject.send_keys``/``is_alive`` are mocked (the pty/dtach
boundary this suite always mocks) -- everything else is observed through
entrypoints.md's documented side effects: the isolated audit log's
``via`` field and the raw datagram (or absence of one) on the test's own
socket.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aipager import audit as audit_mod
from aipager.state import Status, TrackedSession


@pytest.fixture
def short_dir():
    d = tempfile.mkdtemp(prefix="phd-cb-")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def mk_query():
    """Build a mocked Telegram CallbackQuery (mirrors the mk_query()
    pattern in tests/test_bot_callbacks_perms.py)."""
    def _mk(callback_data, *, user_id=12345, message_id=42, text=""):
        query = MagicMock()
        query.data = callback_data
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        query.message = MagicMock()
        query.message.message_id = message_id
        query.message.text = text
        query.message.chat = MagicMock()
        query.message.chat.id = -100
        query.from_user = MagicMock()
        query.from_user.id = user_id
        update = MagicMock()
        update.callback_query = query
        update.effective_user = query.from_user
        update.effective_chat = MagicMock()
        update.effective_chat.id = -100
        return update, query
    return _mk


@pytest.fixture
def live_listener(short_dir):
    """A real socket standing in for a hook still parked waiting."""
    sock_path = os.path.join(short_dir, "aipager-reply-live.sock")
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    sock.bind(sock_path)
    sock.settimeout(2.0)
    yield sock, sock_path
    sock.close()


def _dead_hook_reply(short_dir, request_id="dead0000" * 4):
    """A hook_reply dict pointing at a path nobody listens on -- the
    'daemon can't reach the hook' / 'hook already gave up' case."""
    return {"addr": os.path.join(short_dir, "gone.sock"), "request_id": request_id}


def _read_last_audit_record():
    path = audit_mod.AUDIT_LOG_PATH
    lines = path.read_text().splitlines()
    assert lines, "audit.append was never called"
    return json.loads(lines[-1])


def _recv_or_none(sock, timeout=0.8):
    sock.settimeout(timeout)
    try:
        raw, _addr = sock.recvfrom(65536)
        return json.loads(raw.decode())
    except socket.timeout:
        return None


def _run_callback(bot, mk_query, run_async, action, pending, session_name="claude-dev"):
    sess = TrackedSession(name=session_name, label="dev", status=Status.INTERACTIVE)
    sess.pending_permission = pending
    bot.registry._sessions[session_name] = sess
    update, query = mk_query(f"{session_name}:{action}")

    key_calls = []

    async def mock_send_keys(sn, key):
        key_calls.append(key)
        return True

    async def mock_is_alive(name):
        return True

    with patch("aipager.dtach.inject.send_keys", side_effect=mock_send_keys), \
         patch("aipager.dtach.inject.is_alive", side_effect=mock_is_alive):
        run_async(bot._handle_callback(update, MagicMock()))
    return key_calls, sess, query


_SUGGESTION = {"type": "addRules",
              "rules": [{"toolName": "Bash", "ruleContent": "ls -la"}],
              "behavior": "allow", "destination": "localSettings"}


# ---- success path: real listener, zero keystrokes, via=hook_decision -----

def test_allow_tap_with_live_hook_delivers_zero_keystrokes_and_via_hook_decision(
    mk_bot, mk_query, run_async, live_listener,
):
    sock, sock_path = live_listener
    hook_reply_dict = {"addr": sock_path, "request_id": "feedface" * 4}
    pending = {"tool_summary": "Bash: ls", "tool_info": {"name": "Bash"},
               "hook_reply": hook_reply_dict}

    keys, _sess, _query = _run_callback(mk_bot(), mk_query, run_async, "allow", pending)

    assert keys == []
    assert _read_last_audit_record()["via"] == "hook_decision"

    wire = _recv_or_none(sock)
    assert wire is not None, "send_decision never reached the live socket"
    assert wire["request_id"] == hook_reply_dict["request_id"]
    assert wire["decision"] == {"behavior": "allow"}


def test_deny_tap_with_live_hook_sends_permission_request_deny_shape(
    mk_bot, mk_query, run_async, live_listener,
):
    sock, sock_path = live_listener
    hook_reply_dict = {"addr": sock_path, "request_id": "abcdabcd" * 4}
    pending = {"tool_summary": "Bash: rm -rf x", "tool_info": {"name": "Bash"},
               "hook_reply": hook_reply_dict}

    keys, _sess, _query = _run_callback(mk_bot(), mk_query, run_async, "deny", pending)

    assert keys == []
    assert _read_last_audit_record()["via"] == "hook_decision"

    wire = _recv_or_none(sock)
    assert wire is not None
    decision = wire["decision"]
    assert decision["behavior"] == "deny"
    assert isinstance(decision["message"], str) and decision["message"]
    assert decision["interrupt"] is False
    # Never the PreToolUse shape (top-level reason / decision:"block").
    assert set(decision.keys()) == {"behavior", "message", "interrupt"}


def test_allow_always_tap_echoes_standing_rule_verbatim(
    mk_bot, mk_query, run_async, live_listener,
):
    sock, sock_path = live_listener
    hook_reply_dict = {"addr": sock_path, "request_id": "11112222" * 4}
    pending = {"tool_summary": "Bash: ls -la",
               "tool_info": {"name": "Bash", "always_available": True,
                             "standing_rule_suggestion": _SUGGESTION},
               "hook_reply": hook_reply_dict}

    keys, _sess, _query = _run_callback(mk_bot(), mk_query, run_async, "allow_always", pending)

    assert keys == []
    wire = _recv_or_none(sock)
    assert wire is not None
    assert wire["decision"] == {"behavior": "allow", "updatedPermissions": [_SUGGESTION]}
    assert _read_last_audit_record()["via"] == "hook_decision"


def test_allow_always_degraded_no_rule_still_tries_a_plain_hook_decision(
    mk_bot, mk_query, run_async, live_listener,
):
    """No standing rule to echo -- still tries the hook path first with
    a PLAIN allow (no updatedPermissions), needing no menu navigation."""
    sock, sock_path = live_listener
    hook_reply_dict = {"addr": sock_path, "request_id": "22223333" * 4}
    pending = {"tool_summary": "Bash: ls",
               "tool_info": {"name": "Bash", "always_available": False,
                             "standing_rule_suggestion": None},
               "hook_reply": hook_reply_dict}

    keys, _sess, _query = _run_callback(mk_bot(), mk_query, run_async, "allow_always", pending)

    assert keys == []
    wire = _recv_or_none(sock)
    assert wire is not None
    assert wire["decision"] == {"behavior": "allow"}
    assert _read_last_audit_record()["via"] == "hook_decision"


def test_allow_always_never_echoes_setmode_even_if_upstream_lets_it_through(
    mk_bot, mk_query, run_async, live_listener,
):
    """Defense-in-depth (design.md): even if a future bug let a non
    addRules/addDirectories suggestion reach tool_info, the callback
    layer must never echo it onto the wire."""
    bogus = {"type": "setMode", "mode": "acceptEdits", "destination": "session"}
    sock, sock_path = live_listener
    hook_reply_dict = {"addr": sock_path, "request_id": "33334444" * 4}
    pending = {"tool_summary": "Write: x",
               "tool_info": {"name": "Write", "always_available": True,
                             "standing_rule_suggestion": bogus},
               "hook_reply": hook_reply_dict}

    _run_callback(mk_bot(), mk_query, run_async, "allow_always", pending)

    wire = _recv_or_none(sock)
    assert wire is not None
    assert "updatedPermissions" not in wire["decision"]
    assert wire["decision"] == {"behavior": "allow"}


# ---- fallback path: dead/absent hook_reply -> keystrokes ------------------

def test_allow_tap_without_hook_reply_falls_back_to_keystroke(mk_bot, mk_query, run_async):
    pending = {"tool_summary": "Bash: ls", "tool_info": {"name": "Bash"}}
    keys, _sess, _query = _run_callback(mk_bot(), mk_query, run_async, "allow", pending)
    assert keys == ["Enter"]
    assert _read_last_audit_record()["via"] == "keystroke_fallback"


def test_deny_tap_with_dead_hook_reply_falls_back_to_keystrokes(
    mk_bot, mk_query, run_async, short_dir,
):
    pending = {"tool_summary": "Bash: rm -rf x", "tool_info": {"name": "Bash"},
               "hook_reply": _dead_hook_reply(short_dir)}
    keys, _sess, _query = _run_callback(mk_bot(), mk_query, run_async, "deny", pending)
    assert keys, "deny fallback must still send some keys"
    assert keys[-1] == "Enter"
    assert _read_last_audit_record()["via"] == "keystroke_fallback"


def test_allow_always_with_dead_hook_reply_falls_back_to_existing_keystrokes(
    mk_bot, mk_query, run_async, short_dir,
):
    pending = {"tool_summary": "Bash: ls -la",
               "tool_info": {"name": "Bash", "always_available": True,
                             "standing_rule_suggestion": _SUGGESTION},
               "hook_reply": _dead_hook_reply(short_dir)}
    keys, _sess, _query = _run_callback(mk_bot(), mk_query, run_async, "allow_always", pending)
    assert keys == ["Down", "Enter"]
    assert _read_last_audit_record()["via"] == "keystroke_fallback"


# ---- AskUserQuestion guard: hook decision never attempted -----------------

@pytest.mark.parametrize("action", ["allow", "allow_always", "deny"])
def test_ask_user_question_never_attempts_hook_decision(
    mk_bot, mk_query, run_async, live_listener, action,
):
    """Even with a LIVE hook_reply present, AskUserQuestion must never
    reach send_decision -- proven black-box by the absence of any
    datagram on the socket that stands in for the reply channel."""
    sock, sock_path = live_listener
    hook_reply_dict = {"addr": sock_path, "request_id": "55556666" * 4}
    pending = {"tool_summary": "AskUserQuestion (loading...)",
               "tool_info": {"name": "AskUserQuestion",
                             "standing_rule_suggestion": _SUGGESTION},
               "hook_reply": hook_reply_dict}

    _run_callback(mk_bot(), mk_query, run_async, action, pending)

    wire = _recv_or_none(sock, timeout=0.5)
    assert wire is None, f"hook_reply.send_decision was invoked for AskUserQuestion: {wire!r}"
    assert _read_last_audit_record()["via"] == "keystroke_fallback"


# ---- boundary: distinct concurrent-looking request_ids never collide -----

def test_two_sessions_distinct_request_ids_each_only_answer_their_own(
    mk_bot, mk_query, run_async, short_dir,
):
    """Two different sessions' pending permissions, each with their own
    live socket/request_id -- answering one must never touch the other's
    socket."""
    sock_path_a = os.path.join(short_dir, "reply-a.sock")
    sock_path_b = os.path.join(short_dir, "reply-b.sock")
    sock_a = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    sock_a.bind(sock_path_a)
    sock_b = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    sock_b.bind(sock_path_b)
    try:
        bot = mk_bot()
        pending_a = {"tool_summary": "Bash: a", "tool_info": {"name": "Bash"},
                    "hook_reply": {"addr": sock_path_a, "request_id": "aaaa1111" * 4}}
        _run_callback(bot, mk_query, run_async, "allow", pending_a, session_name="claude-a")

        wire_a = _recv_or_none(sock_a)
        wire_b = _recv_or_none(sock_b, timeout=0.3)
        assert wire_a is not None
        assert wire_a["request_id"] == "aaaa1111" * 4
        assert wire_b is None, "the OTHER session's socket must never receive anything"
    finally:
        sock_a.close()
        sock_b.close()
