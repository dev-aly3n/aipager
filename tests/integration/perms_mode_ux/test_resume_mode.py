"""Integration tests: SC4, SC5 — resume passes the persisted mode.

SC4: /resume ben (an older file's skip_perms=True) → launch with Full access.
SC5: /resume !ben (persisted Ask) → launch with Full access (forced).
Also covers the Telegram resume mode picker (session tap → mode keyboard step).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

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
# SC4 — /resume ben with an older file's skip_perms=True → Full access      #
# --------------------------------------------------------------------------- #

def test_sc4_cli_resume_persisted_auto_passes_true(monkeypatch):
    """SC4: CLI /resume ben where ben's older record has skip_perms=True
    (the old bypassing Auto) must launch in Full access."""
    from aipager.cli import resume as cli_resume

    history = [{
        "name": "claude-ben",
        "label": "ben",
        "claude_session_id": "uuid-001",
        "cwd": "/home/alice",
        "gone_at": 1234567890.0,
        "last_assistant_preview": "Done.",
        "skip_perms": True,  # the old Auto, which bypassed: Full access now
    }]
    monkeypatch.setattr(cli_resume, "_gone_history", lambda: history)

    launched = {}

    async def mock_launch(label, *, mode, resume_id=None, cwd=None, **kw):
        launched.update({"label": label, "mode": mode})
        return True, ""

    with patch("aipager.dtach.inject.launch_session", side_effect=mock_launch), \
         patch("pathlib.Path.is_socket", return_value=False):
        rc = cli_resume._resume_one("ben", force_full=False)

    assert rc == 0
    assert launched.get("mode") == "full", (
        f"SC4: persisted skip_perms=True must launch in Full access; got {launched}"
    )


# --------------------------------------------------------------------------- #
# SC5 — /resume !ben forces Full access regardless of persisted value        #
# --------------------------------------------------------------------------- #

def test_sc5_cli_resume_bang_forces_auto_over_persisted_false(monkeypatch):
    """SC5: /resume !ben where ben has persisted Ask must still launch in
    Full access."""
    from aipager.cli import resume as cli_resume

    history = [{
        "name": "claude-ben",
        "label": "ben",
        "claude_session_id": "uuid-001",
        "cwd": "/home/alice",
        "gone_at": 1234567890.0,
        "last_assistant_preview": "Done.",
        "perm_mode": "ask",  # persisted Ask mode — must be overridden
    }]
    monkeypatch.setattr(cli_resume, "_gone_history", lambda: history)

    launched = {}

    async def mock_launch(label, *, mode, resume_id=None, cwd=None, **kw):
        launched.update({"label": label, "mode": mode})
        return True, ""

    with patch("aipager.dtach.inject.launch_session", side_effect=mock_launch), \
         patch("pathlib.Path.is_socket", return_value=False):
        rc = cli_resume._resume_one("ben", force_full=True)

    assert rc == 0
    assert launched.get("mode") == "full", (
        f"SC5: ! prefix must force Full access; got {launched}"
    )


# --------------------------------------------------------------------------- #
# Telegram /resume picker: session tap → mode picker keyboard shown           #
# --------------------------------------------------------------------------- #

def test_telegram_resume_picker_tap_shows_mode_picker():
    """After tapping a session in the /resume picker, the message must be
    edited to show a mode picker (Ask / Auto / Full access buttons), not
    immediately resume."""
    bot = _make_bot()
    sess = TrackedSession(name="claude-ben", label="ben", status=Status.GONE)
    sess.perm_mode = "ask"
    # A GONE session with no `claude_session_id` has nothing to resume FROM,
    # and is now refused before the mode picker rather than after it. This
    # test is about the picker appearing, so give it a resumable session.
    sess.claude_session_id = "uuid-mode-1"
    bot.registry._sessions["claude-ben"] = sess

    update, query = _make_query("claude-ben:resume")
    _run(bot._handle_callback(update, MagicMock()))

    # Message must be edited to mode picker
    query.edit_message_text.assert_awaited_once()
    edit_kwargs = query.edit_message_text.await_args[1] or {}

    assert "reply_markup" in edit_kwargs, (
        "Resume tap must show a mode picker keyboard"
    )
    kb = edit_kwargs["reply_markup"]
    all_cbs = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert any(cb.endswith("resume-ask") for cb in all_cbs), (
        f"Mode picker must offer Ask; got {all_cbs}"
    )
    assert any(cb.endswith("resume-auto") for cb in all_cbs), (
        f"Mode picker must offer Auto; got {all_cbs}"
    )
    # Personal mode: the operator is an admin, so Full access is offered.
    assert any(cb.endswith("resume-full") for cb in all_cbs), (
        f"Mode picker must offer Full access to an admin; got {all_cbs}"
    )


def test_telegram_resume_mode_picker_shows_default_label():
    """The mode picker must show (default) suffix on the button matching
    the session's persisted mode."""
    bot = _make_bot()
    # Session saved in Auto (Auto is the default for this session)
    sess = TrackedSession(name="claude-ben", label="ben", status=Status.GONE)
    sess.perm_mode = "auto"
    sess.claude_session_id = "uuid-mode-2"
    bot.registry._sessions["claude-ben"] = sess

    update, query = _make_query("claude-ben:resume")
    _run(bot._handle_callback(update, MagicMock()))

    kb = query.edit_message_text.await_args[1]["reply_markup"]
    all_labels = [btn.text for row in kb.inline_keyboard for btn in row]

    # Auto button should have (default)
    auto_labels = [lbl for lbl in all_labels if "Auto" in lbl]
    ask_labels = [lbl for lbl in all_labels if "Ask" in lbl]

    assert any("(default)" in lbl for lbl in auto_labels), (
        f"Auto button must have (default) when persisted=True; labels: {all_labels}"
    )
    assert not any("(default)" in lbl for lbl in ask_labels), (
        f"Ask button must NOT have (default) when persisted=True; labels: {all_labels}"
    )


def test_telegram_resume_mode_ask_calls_do_resume_with_false():
    """resume_mode_ask callback must call _do_resume with mode_override="ask"."""
    bot = _make_bot()
    bot._resume_mode_pending["claude-ben"] = "ben"
    bot._do_resume = AsyncMock()

    update, query = _make_query("claude-ben:resume_mode_ask")
    _run(bot._handle_callback(update, MagicMock()))

    bot._do_resume.assert_awaited_once()
    kw = bot._do_resume.await_args[1]
    assert kw.get("mode_override") == "ask", (
        f"resume_mode_ask must use mode_override='ask'; got {kw}"
    )


def test_telegram_resume_mode_auto_calls_do_resume_with_true():
    """resume_mode_auto (a picker from before Full access) must call
    _do_resume with mode_override="auto": Auto now, never wider."""
    bot = _make_bot()
    bot._resume_mode_pending["claude-ben"] = "ben"
    bot._do_resume = AsyncMock()

    update, query = _make_query("claude-ben:resume_mode_auto")
    _run(bot._handle_callback(update, MagicMock()))

    bot._do_resume.assert_awaited_once()
    kw = bot._do_resume.await_args[1]
    assert kw.get("mode_override") == "auto", (
        f"resume_mode_auto must use mode_override='auto'; got {kw}"
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
# Boundary: a maximum-length session name — callback_data must stay          #
# within 64 bytes. `_make_cb` (the old assert-on-overflow builder) is gone   #
# entirely (design.md) — nothing can overflow after the switch to           #
# `session_cb`'s indexed form, so there is no overflow case left to assert. #
# --------------------------------------------------------------------------- #

def test_make_cb_no_longer_exists():
    """entrypoints.md: `_make_cb` is deleted by this ship. A
    missing-attribute test is the contract — not an `AssertionError`
    from calling it."""
    bot = _make_bot()
    assert not hasattr(bot, "_make_cb")


def test_perms_stop_switch_stays_within_64_bytes_for_a_maximum_length_name():
    """`perms_stop_switch` is the tightest verb in the codebase — it used
    to break at a 28-character label (spec.md's SECOND CORRECTION). A
    64-byte internal name (`inject.launch_session`'s own cap) must still
    produce a callback_data that fits, however large the per-chat index
    gets."""
    bot = _make_bot()
    max_name = "claude-" + "x" * 45 + "__d256113222"  # 64 bytes, per spec.md
    assert len(max_name.encode()) == 64
    sess = TrackedSession(name=max_name, label="x" * 45, status=Status.BUSY)
    bot.registry._sessions[sess.name] = sess

    kb = bot._build_perms_busy_keyboard(sess)
    cbs = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert any(cb.endswith(":perms_stop_switch") for cb in cbs), cbs
    for cb in cbs:
        assert len(cb.encode()) <= 64, cb
