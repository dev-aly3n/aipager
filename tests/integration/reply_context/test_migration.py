"""Black-box integration test for design.md success criterion 9 -- the
state-file migration for the ``_msg_map`` re-key (bare ``message_id``
keys -> ``(chat_id, message_id)``).

The developer's own unit tests (``tests/test_state_reply_context.py``)
already exercise ``SessionRegistry.load()`` directly against hand-built
legacy state dicts. This file is independent: it drives the SAME public
entrypoint (``SessionRegistry.load()``/``save()``, a documented, public
``aipager.state`` surface, not one of entrypoints.md's "NOT exported"
internals) but additionally proves the migrated entry is actually WIRED
UP to real reply routing through a full ``TelegramBotCore._handle_message``
call -- not just to ``get_session_by_msg`` in isolation -- and constructs
its own drop-case fixtures independently rather than reusing the
developer's.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

from aipager.state import SessionRegistry, Status

CHAT_A = 4242
CHAT_B = 9999
BOT_ID = 87654321


def _legacy_state(msg_map, sessions):
    return {
        "version": 1,
        "last_active_session": "",
        "pinned_msg_id": 0,
        "msg_map": msg_map,
        "sessions": sessions,
    }


def _legacy_session(name, label, *, scope_chat_id):
    return {
        "name": name,
        "label": label,
        "last_msg_id": None,
        "transcript_path": "",
        "trigger_msg_id": None,
        "pending_queue": [],
        "last_prompt": "",
        "model_name": "",
        "busy_msg_id": None,
        "scope_chat_id": scope_chat_id,
        "scope_kind": "dm" if scope_chat_id > 0 else ("group" if scope_chat_id else ""),
    }


def _ctx():
    c = MagicMock()
    c.bot.id = BOT_ID
    return c


# ===== Both directions: correct chat resolves, wrong chat does not =========

def test_migrated_entry_resolves_for_its_own_chat_and_not_for_another(
    tmp_state_file,
):
    state = _legacy_state(
        {"600": "claude-alice"},
        {"claude-alice": _legacy_session("claude-alice", "alice", scope_chat_id=CHAT_A)},
    )
    tmp_state_file.write_text(json.dumps(state))

    r = SessionRegistry()
    r.load()

    resolved_own = r.get_session_by_msg(600, CHAT_A)
    assert resolved_own is not None
    assert resolved_own.name == "claude-alice"

    resolved_other = r.get_session_by_msg(600, CHAT_B)
    assert resolved_other is None


def test_migration_round_trips_through_a_real_reply_handler_call(
    mk_bot, mk_update, run_async, monkeypatch, tmp_state_file,
):
    """The developer's tests stop at ``get_session_by_msg``. This proves
    the migrated entry is wired all the way through a real
    ``_handle_message`` reply-routing call -- i.e. the migration doesn't
    just satisfy the registry primitive but the actual bot handler."""
    state = _legacy_state(
        {"700": "claude-alice"},
        {"claude-alice": _legacy_session("claude-alice", "alice", scope_chat_id=CHAT_A)},
    )
    tmp_state_file.write_text(json.dumps(state))

    registry = SessionRegistry()
    registry.load()
    bot = mk_bot(registry)
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter",
                        AsyncMock(return_value=True))
    bot._send_busy_and_animate = AsyncMock()
    bot._react = AsyncMock()

    reply_to = MagicMock()
    reply_to.message_id = 700
    reply_to.text = "the migrated old message"
    reply_to.caption = None
    reply_to.from_user = None

    # Correct chat: routes to the migrated session.
    update_own = mk_update("about that", chat_id=CHAT_A, message_id=800)
    update_own.message.reply_to_message = reply_to
    run_async(bot._handle_message(update_own, _ctx()))
    assert registry.get("claude-alice").status == Status.BUSY

    registry.get("claude-alice").status = Status.IDLE
    registry.last_active_session = ""

    # Wrong chat: must NOT route there.
    update_other = mk_update("about that", chat_id=CHAT_B, message_id=801)
    update_other.message.reply_to_message = reply_to
    run_async(bot._handle_message(update_other, _ctx()))
    assert registry.get("claude-alice").status == Status.IDLE
    text = update_other.message.reply_text.await_args.args[0]
    assert "don't know which session" in text


# ===== Drop cases ============================================================

def test_migration_drops_entry_for_a_session_that_no_longer_exists(tmp_state_file):
    state = _legacy_state(
        {"111": "claude-vanished"},  # no matching entry in "sessions"
        {},
    )
    tmp_state_file.write_text(json.dumps(state))

    r = SessionRegistry()
    r.load()  # must not raise

    assert r.get_session_by_msg(111, CHAT_A) is None
    assert r.get_session_by_msg(111, 0) is None


def test_migration_drops_entry_whose_session_has_scope_chat_id_zero(
    tmp_state_file, monkeypatch,
):
    from aipager import config
    monkeypatch.setattr(config, "SCOPES", None, raising=False)
    monkeypatch.setattr(config, "CHAT_ID", "")

    state = _legacy_state(
        {"222": "claude-unscoped"},
        {"claude-unscoped": _legacy_session("claude-unscoped", "u", scope_chat_id=0)},
    )
    tmp_state_file.write_text(json.dumps(state))

    r = SessionRegistry()
    r.load()  # must not raise

    assert r.get_session_by_msg(222, 0) is None
    assert r.get_session_by_msg(222, CHAT_A) is None
