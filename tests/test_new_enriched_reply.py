"""Tests for /new enriched reply — mode icon, mode label, cwd, model, /perms nudge."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aipager.state import Status, TrackedSession


@pytest.fixture
def mk_launch_ok():
    """Patch inject.launch_session to succeed."""
    async def _launch(*a, **kw):
        return True, ""
    return _launch


def test_new_ask_mode_reply_contains_ask_and_perms_nudge(
        mk_bot, mk_update, run_async, mk_launch_ok):
    """Reply for /new dev (Ask mode) must contain 💬, 'Ask', and /perms."""
    bot = mk_bot()
    update = mk_update("/new dev")

    with patch("aipager.dtach.inject.launch_session", side_effect=mk_launch_ok):
        run_async(bot._handle_new_cmd(update, MagicMock()))

    # status_msg.edit_text was called
    status_msg = update.message.reply_text.return_value
    status_msg.edit_text.assert_awaited_once()
    text = status_msg.edit_text.await_args[0][0]

    assert "💬" in text or "Ask" in text, f"Expected Ask mode text, got: {text}"
    assert "/perms" in text, f"Expected /perms nudge, got: {text}"
    # Must NOT contain "Auto" as the mode (it's Ask mode)
    assert "🤖" not in text, f"Should not have Auto icon in Ask mode: {text}"


def test_new_auto_mode_reply_contains_auto_no_perms_nudge(
        mk_bot, mk_update, run_async, mk_launch_ok):
    """Reply for /new !dev (Auto mode) must contain 🤖, 'Auto', no /perms nudge."""
    bot = mk_bot()
    bot._is_admin = MagicMock(return_value=True)
    update = mk_update("/new !dev")

    with patch("aipager.dtach.inject.launch_session", side_effect=mk_launch_ok):
        run_async(bot._handle_new_cmd(update, MagicMock()))

    status_msg = update.message.reply_text.return_value
    status_msg.edit_text.assert_awaited_once()
    text = status_msg.edit_text.await_args[0][0]

    assert "🤖" in text or "Auto" in text, f"Expected Auto mode text, got: {text}"
    # Auto mode should NOT have the /perms nudge
    assert "/perms" not in text, f"Should not have /perms nudge in Auto mode: {text}"


def test_new_reply_omits_model_when_unknown(mk_bot, mk_update, run_async, mk_launch_ok):
    """When model_name is empty, the model clause should be omitted (no 'None')."""
    bot = mk_bot()
    update = mk_update("/new dev")

    with patch("aipager.dtach.inject.launch_session", side_effect=mk_launch_ok):
        run_async(bot._handle_new_cmd(update, MagicMock()))

    status_msg = update.message.reply_text.return_value
    text = status_msg.edit_text.await_args[0][0]

    # model_name is "" by default — should NOT appear as "None" or empty placeholder
    assert "None" not in text, f"'None' should not appear in reply: {text}"
    # No model icon if unknown
    assert "🧠" not in text or text.count("🧠") == 0, (
        f"Model icon should not appear when unknown: {text}"
    )


def test_new_reply_includes_model_when_known(mk_bot, mk_update, run_async, mk_launch_ok):
    """When model_name is pre-populated on the session, it appears in the reply.

    model_name is normally populated by the first statusLine event (async,
    after launch).  Here we pre-seed the registry with the session so that
    get_or_create returns the existing entry, which already has model_name set.
    """
    from aipager.scope import disambiguated_name
    from aipager.state import Status, TrackedSession

    bot = mk_bot()
    # With chat_id=0 and scope_kind="dm", the session name is "claude-dev__d0".
    # Pre-seed as GONE with no claude_session_id so the conflict check is skipped,
    # but model_name is preserved for the enriched reply check.
    session_name = disambiguated_name("dev", 0, "dm")
    pre = TrackedSession(name=session_name, label="dev", status=Status.GONE)
    pre.model_name = "Sonnet 4.5"
    bot.registry._sessions[session_name] = pre

    update = mk_update("/new dev", chat_id=0)

    with patch("aipager.dtach.inject.launch_session", side_effect=mk_launch_ok):
        run_async(bot._handle_new_cmd(update, MagicMock()))

    status_msg = update.message.reply_text.return_value
    text = status_msg.edit_text.await_args[0][0]
    assert "Sonnet 4.5" in text, f"Expected model name in reply: {text}"


def test_new_auto_requires_admin(mk_bot, mk_update, run_async):
    """Non-admin user sending /new !dev gets an error."""
    bot = mk_bot()
    bot._is_admin = MagicMock(return_value=False)
    update = mk_update("/new !dev")

    run_async(bot._handle_new_cmd(update, MagicMock()))

    update.message.reply_text.assert_awaited_once()
    msg = update.message.reply_text.await_args[0][0]
    assert "requires admin role" in msg


def _find_dev_session(bot):
    """Find the 'dev'-labeled session regardless of internal name."""
    for sess in bot.registry.all_sessions().values():
        if sess.label == "dev":
            return sess
    return None


def test_new_sets_skip_perms_on_session(mk_bot, mk_update, run_async, mk_launch_ok):
    """After /new dev, the session's skip_perms should be False (Ask mode)."""
    bot = mk_bot()
    update = mk_update("/new dev")

    with patch("aipager.dtach.inject.launch_session", side_effect=mk_launch_ok):
        run_async(bot._handle_new_cmd(update, MagicMock()))

    sess = _find_dev_session(bot)
    assert sess is not None
    assert sess.skip_perms is False


def test_new_auto_sets_skip_perms_true(mk_bot, mk_update, run_async, mk_launch_ok):
    """After /new !dev, the session's skip_perms should be True (Auto mode)."""
    bot = mk_bot()
    bot._is_admin = MagicMock(return_value=True)
    update = mk_update("/new !dev")

    with patch("aipager.dtach.inject.launch_session", side_effect=mk_launch_ok):
        run_async(bot._handle_new_cmd(update, MagicMock()))

    sess = _find_dev_session(bot)
    assert sess is not None
    assert sess.skip_perms is True
