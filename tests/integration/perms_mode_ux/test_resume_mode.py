"""Integration tests: SC4, SC5 — resume passes persisted skip_perms.

SC4: /resume ben (persisted skip_perms=True) → launch called with skip_perms=True.
SC5: /resume !ben (persisted skip_perms=False) → launch called with skip_perms=True (forced).
Also covers the Telegram resume mode picker (session tap → mode keyboard step).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aipager.state import SessionRegistry, Status, TrackedSession


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _make_bot(*, registry=None):
    from aipager.bot import TelegramBot
    if registry is None:
        registry = SessionRegistry()
    bot = TelegramBot(registry)
    bot._app = MagicMock()
    bot._app.bot = MagicMock()
    bot._app.bot.send_message = AsyncMock()
    bot.team = None
    bot.scopes = None
    return bot


def _make_query(callback_data, *, user_id=12345, message_id=42):
    query = MagicMock()
    query.data = callback_data
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.message = MagicMock()
    query.message.message_id = message_id
    query.message.text = ""
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


# --------------------------------------------------------------------------- #
# SC4 — /resume ben with persisted skip_perms=True → launch with True         #
# --------------------------------------------------------------------------- #

def test_sc4_cli_resume_persisted_auto_passes_true(monkeypatch):
    """SC4: CLI /resume ben where ben has skip_perms=True must pass
    skip_perms=True to launch_session."""
    from aipager.cli import resume as cli_resume

    history = [{
        "name": "claude-ben",
        "label": "ben",
        "claude_session_id": "uuid-001",
        "cwd": "/home/aly",
        "gone_at": 1234567890.0,
        "last_assistant_preview": "Done.",
        "skip_perms": True,  # persisted Auto mode
    }]
    monkeypatch.setattr(cli_resume, "_gone_history", lambda: history)

    launched = {}

    async def mock_launch(label, *, resume_id=None, cwd=None, skip_perms=False, **kw):
        launched.update({"label": label, "skip_perms": skip_perms})
        return True, ""

    with patch("aipager.dtach.inject.launch_session", side_effect=mock_launch), \
         patch("pathlib.Path.is_socket", return_value=False):
        rc = cli_resume._resume_one("ben", force_auto=False)

    assert rc == 0
    assert launched.get("skip_perms") is True, (
        f"SC4: persisted skip_perms=True must be passed to launch; got {launched}"
    )


# --------------------------------------------------------------------------- #
# SC5 — /resume !ben forces Auto regardless of persisted value                #
# --------------------------------------------------------------------------- #

def test_sc5_cli_resume_bang_forces_auto_over_persisted_false(monkeypatch):
    """SC5: /resume !ben where ben has persisted skip_perms=False must still
    launch with skip_perms=True."""
    from aipager.cli import resume as cli_resume

    history = [{
        "name": "claude-ben",
        "label": "ben",
        "claude_session_id": "uuid-001",
        "cwd": "/home/aly",
        "gone_at": 1234567890.0,
        "last_assistant_preview": "Done.",
        "skip_perms": False,  # persisted Ask mode — must be overridden
    }]
    monkeypatch.setattr(cli_resume, "_gone_history", lambda: history)

    launched = {}

    async def mock_launch(label, *, resume_id=None, cwd=None, skip_perms=False, **kw):
        launched.update({"label": label, "skip_perms": skip_perms})
        return True, ""

    with patch("aipager.dtach.inject.launch_session", side_effect=mock_launch), \
         patch("pathlib.Path.is_socket", return_value=False):
        rc = cli_resume._resume_one("ben", force_auto=True)

    assert rc == 0
    assert launched.get("skip_perms") is True, (
        f"SC5: ! prefix must force skip_perms=True; got {launched}"
    )


# --------------------------------------------------------------------------- #
# Telegram /resume picker: session tap → mode picker keyboard shown           #
# --------------------------------------------------------------------------- #

def test_telegram_resume_picker_tap_shows_mode_picker():
    """After tapping a session in the /resume picker, the message must be
    edited to show a mode picker (Ask / Auto buttons), not immediately resume."""
    bot = _make_bot()
    sess = TrackedSession(name="claude-ben", label="ben", status=Status.GONE)
    sess.skip_perms = False
    bot.registry._sessions["claude-ben"] = sess

    update, query = _make_query("claude-ben:resume")
    _run(bot._handle_callback(update, MagicMock()))

    # Message must be edited to mode picker
    query.edit_message_text.assert_awaited_once()
    edit_kwargs = query.edit_message_text.await_args[1] or {}
    edit_text = query.edit_message_text.await_args[0][0] if query.edit_message_text.await_args[0] else None

    assert "reply_markup" in edit_kwargs, (
        "Resume tap must show a mode picker keyboard"
    )
    kb = edit_kwargs["reply_markup"]
    all_cbs = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert any("resume_mode_ask" in cb for cb in all_cbs), (
        f"Mode picker must have resume_mode_ask; got {all_cbs}"
    )
    assert any("resume_mode_auto" in cb for cb in all_cbs), (
        f"Mode picker must have resume_mode_auto; got {all_cbs}"
    )


def test_telegram_resume_mode_picker_shows_default_label():
    """The mode picker must show (default) suffix on the button matching
    the session's persisted skip_perms."""
    bot = _make_bot()
    # Session with skip_perms=True (Auto is the default for this session)
    sess = TrackedSession(name="claude-ben", label="ben", status=Status.GONE)
    sess.skip_perms = True
    bot.registry._sessions["claude-ben"] = sess

    update, query = _make_query("claude-ben:resume")
    _run(bot._handle_callback(update, MagicMock()))

    kb = query.edit_message_text.await_args[1]["reply_markup"]
    all_labels = [btn.text for row in kb.inline_keyboard for btn in row]

    # Auto button should have (default)
    auto_labels = [l for l in all_labels if "Auto" in l]
    ask_labels = [l for l in all_labels if "Ask" in l]

    assert any("(default)" in l for l in auto_labels), (
        f"Auto button must have (default) when persisted=True; labels: {all_labels}"
    )
    assert not any("(default)" in l for l in ask_labels), (
        f"Ask button must NOT have (default) when persisted=True; labels: {all_labels}"
    )


def test_telegram_resume_mode_ask_calls_do_resume_with_false():
    """resume_mode_ask callback must call _do_resume with skip_perms_override=False."""
    bot = _make_bot()
    bot._resume_mode_pending["claude-ben"] = "ben"
    bot._do_resume = AsyncMock()

    update, query = _make_query("claude-ben:resume_mode_ask")
    _run(bot._handle_callback(update, MagicMock()))

    bot._do_resume.assert_awaited_once()
    kw = bot._do_resume.await_args[1]
    assert kw.get("skip_perms_override") is False, (
        f"resume_mode_ask must use skip_perms_override=False; got {kw}"
    )


def test_telegram_resume_mode_auto_calls_do_resume_with_true():
    """resume_mode_auto callback must call _do_resume with skip_perms_override=True."""
    bot = _make_bot()
    bot._resume_mode_pending["claude-ben"] = "ben"
    bot._do_resume = AsyncMock()

    update, query = _make_query("claude-ben:resume_mode_auto")
    _run(bot._handle_callback(update, MagicMock()))

    bot._do_resume.assert_awaited_once()
    kw = bot._do_resume.await_args[1]
    assert kw.get("skip_perms_override") is True, (
        f"resume_mode_auto must use skip_perms_override=True; got {kw}"
    )


def test_telegram_resume_mode_cancel_edits_to_cancelled():
    """resume_mode_cancel must edit message to Cancelled and clear pending."""
    bot = _make_bot()
    bot._resume_mode_pending["claude-ben"] = "ben"

    update, query = _make_query("claude-ben:resume_mode_cancel")
    _run(bot._handle_callback(update, MagicMock()))

    query.edit_message_text.assert_awaited_once()
    text = query.edit_message_text.await_args[0][0]
    assert "Cancelled" in text or "cancel" in text.lower(), (
        f"resume_mode_cancel must edit to Cancelled; got: {text}"
    )
    assert "claude-ben" not in bot._resume_mode_pending


# --------------------------------------------------------------------------- #
# Boundary: 30-char session label — callback_data must stay within 64 bytes  #
# --------------------------------------------------------------------------- #

def test_callback_data_within_64_bytes_for_long_label():
    """Callback data for a session with a 20-char label must stay ≤ 64 bytes."""
    bot = _make_bot()
    # 20-char label is the stated maximum
    twenty_char_name = "claude-" + "x" * 13  # "claude-" (7) + 13 = 20 chars label portion
    cb = bot._make_cb(twenty_char_name, "perms_stop_switch")
    assert len(cb.encode("utf-8")) <= 64, (
        f"Callback data must be ≤ 64 bytes; got {len(cb.encode())} bytes: {cb!r}"
    )


def test_callback_data_within_64_bytes_for_very_long_name():
    """Callback data for a session name exceeding the limit must be truncated
    to fit within 64 bytes."""
    bot = _make_bot()
    very_long = "claude-" + "x" * 60
    cb = bot._make_cb(very_long, "perms_stop_switch")
    assert len(cb.encode("utf-8")) <= 64, (
        f"_make_cb must truncate to ≤ 64 bytes; got {len(cb.encode())} bytes"
    )
    assert cb.endswith(":perms_stop_switch"), (
        "Truncated callback must still end with the action suffix"
    )
