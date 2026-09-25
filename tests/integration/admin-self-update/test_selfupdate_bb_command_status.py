"""SC-1: `/update` from the operator's DM shows running/latest aipager, the
install source, current/latest Claude Code, the restart mode and the
buttons; an unreachable network shows "couldn't check" and no traceback.
(design.md Success criteria #1, entrypoints.md "Telegram commands").

Roadmap 8.43 (operator decision 2026-09-25): `/update` now sends ONE
"Check for updates" button and looks nothing up; the versions appear once
it is tapped, with ONE Update button for only what has an update. `_show`
below therefore sends /update AND taps Check; the assertions that pinned
the old always-on buttons (Update Claude Code / Update aipager / Both,
"Checking versions", "unknown") now pin the new ones."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from aipager.bot import update_flow


def _cmd(mk_update, h, *, user_id, chat_id):
    upd = mk_update("/update", user_id=user_id, chat_id=chat_id)
    msg = h.status_message(chat_id, 900)
    upd.message.chat = MagicMock()
    upd.message.chat.id = chat_id
    upd.message.reply_text.return_value = msg
    upd.effective_message = upd.message
    return upd, msg


def _tap_check(msg, *, user_id, chat_id):
    query = MagicMock()
    query.data = "_:up:chk"
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.message = msg
    query.from_user = MagicMock()
    query.from_user.id = user_id
    upd = MagicMock()
    upd.callback_query = query
    upd.effective_user = query.from_user
    upd.effective_chat = MagicMock()
    upd.effective_chat.id = chat_id
    return upd


def _show(bot, mk_update, h, run_async, *, user_id=None, chat_id=None):
    user_id = user_id or h.OPERATOR
    chat_id = chat_id or h.DM
    upd, msg = _cmd(mk_update, h, user_id=user_id, chat_id=chat_id)

    async def go():
        await update_flow.handle_update_cmd(bot, upd, MagicMock())
        await bot._handle_callback(_tap_check(msg, user_id=user_id, chat_id=chat_id),
                                   MagicMock())
        await h.wait_for(lambda: msg.edit_text.await_count > 0, 5)
    run_async(go())
    return upd, msg


def _status_text(upd, msg, h, bot):
    return "\n".join(h.texts_of(msg, upd.message, bot._app.bot))


def test_update_first_replies_with_one_check_button(world, personal_bot, mk_update, h, run_async):
    """8.43: was "first replies Checking versions"; /update now offers one
    Check button and looks nothing up until it is tapped."""
    upd, _ = _cmd(mk_update, h, user_id=h.OPERATOR, chat_id=h.DM)
    run_async(update_flow.handle_update_cmd(personal_bot, upd, MagicMock()))
    first = upd.message.reply_text.await_args_list[0]
    markup = first.kwargs["reply_markup"]
    buttons = [(b.text, b.callback_data) for row in markup.inline_keyboard for b in row]
    assert buttons == [("🔄 Check for updates", "_:up:chk")]
    assert world.urls == [] and world.calls == []


def test_status_shows_running_aipager_version(world, personal_bot, mk_update, h, run_async):
    upd, msg = _show(personal_bot, mk_update, h, run_async)
    assert h.RUNNING in _status_text(upd, msg, h, personal_bot)


def test_status_shows_latest_aipager_version(world, personal_bot, mk_update, h, run_async):
    upd, msg = _show(personal_bot, mk_update, h, run_async)
    assert h.LATEST in _status_text(upd, msg, h, personal_bot)


def test_status_shows_install_source(world, personal_bot, mk_update, h, run_async):
    upd, msg = _show(personal_bot, mk_update, h, run_async)
    assert "pipx" in _status_text(upd, msg, h, personal_bot)


def test_status_shows_current_claude_version(world, personal_bot, mk_update, h, run_async):
    upd, msg = _show(personal_bot, mk_update, h, run_async)
    assert h.CLAUDE_OLD in _status_text(upd, msg, h, personal_bot)


def test_status_shows_latest_claude_version(world, personal_bot, mk_update, h, run_async):
    upd, msg = _show(personal_bot, mk_update, h, run_async)
    assert h.CLAUDE_NEW in _status_text(upd, msg, h, personal_bot)


def test_status_shows_automatic_systemd_restart_mode(world, personal_bot, mk_update, h, run_async):
    upd, msg = _show(personal_bot, mk_update, h, run_async)
    assert "Restart: automatic, once no turn is running." in _status_text(
        upd, msg, h, personal_bot)


def test_status_shows_manual_restart_mode_in_foreground(world, personal_bot, mk_update, h, run_async):
    world.set_under_unit(False)
    upd, msg = _show(personal_bot, mk_update, h, run_async)
    assert "manual" in _status_text(upd, msg, h, personal_bot)


def test_status_offers_one_update_button_and_cancel(world, personal_bot, mk_update, h, run_async):
    """8.43: was "offers all four buttons"; both products are newer here,
    so the one button is Update both."""
    upd, msg = _show(personal_bot, mk_update, h, run_async)
    labels = [t for t, _ in h.buttons_of(msg, upd.message)]
    assert labels == ["Update both", "Cancel"], labels


def test_status_buttons_carry_documented_callback_data(world, personal_bot, mk_update, h, run_async):
    upd, msg = _show(personal_bot, mk_update, h, run_async)
    data = [d for _, d in h.buttons_of(msg, upd.message)]
    assert data == ["_:up:go:both", "_:up:x"], data


def test_network_down_shows_couldnt_check(world, personal_bot, mk_update, h, run_async):
    world.latest_pypi = None
    world.claude_latest = None
    upd, msg = _show(personal_bot, mk_update, h, run_async)
    assert "couldn't check" in _status_text(upd, msg, h, personal_bot)


def test_network_down_shows_no_traceback(world, personal_bot, mk_update, h, run_async):
    world.latest_pypi = None
    world.claude_latest = None
    upd, msg = _show(personal_bot, mk_update, h, run_async)
    text = _status_text(upd, msg, h, personal_bot)
    assert "Traceback" not in text and "Error" not in text and "Exception" not in text


def test_garbage_pypi_body_shows_couldnt_check_not_the_body(world, personal_bot, mk_update, h, run_async, monkeypatch):
    from aipager import self_update
    monkeypatch.setattr(self_update, "_http_get",
                        lambda url, *, timeout, max_bytes: b"<html>502 Bad Gateway</html>")
    upd, msg = _show(personal_bot, mk_update, h, run_async)
    text = _status_text(upd, msg, h, personal_bot)
    assert "Bad Gateway" not in text and "couldn't check" in text


def test_fetch_that_raises_shows_couldnt_check_not_an_error(world, personal_bot, mk_update, h, run_async, monkeypatch):
    """Error guessing: a seam that violates its never-raise contract
    (e.g. a socket timeout leaking) must still render "couldn't check"."""
    from aipager import self_update

    def boom(url, *, timeout, max_bytes):
        raise TimeoutError("timed out reading pypi.org")
    monkeypatch.setattr(self_update, "_http_get", boom)
    upd, msg = _show(personal_bot, mk_update, h, run_async)
    text = _status_text(upd, msg, h, personal_bot)
    assert "timed out reading" not in text and "couldn't check" in text


def test_claude_not_found_hides_update_claude_button(world, personal_bot, mk_update, h, run_async):
    world.claude_path = None
    upd, msg = _show(personal_bot, mk_update, h, run_async)
    labels = [t for t, _ in h.buttons_of(msg, upd.message)]
    assert not any("Claude Code" in t for t in labels), labels


def test_claude_not_found_hides_both_button(world, personal_bot, mk_update, h, run_async):
    world.claude_path = None
    upd, msg = _show(personal_bot, mk_update, h, run_async)
    labels = [t for t, _ in h.buttons_of(msg, upd.message)]
    assert "Update both" not in labels, labels


def test_editable_install_hides_update_aipager_button(world, personal_bot, mk_update, h, run_async):
    world.origin = "editable"
    upd, msg = _show(personal_bot, mk_update, h, run_async)
    labels = [t for t, _ in h.buttons_of(msg, upd.message)]
    assert not any("Update aipager" in t for t in labels), labels


def test_editable_install_hides_both_button(world, personal_bot, mk_update, h, run_async):
    world.origin = "editable"
    upd, msg = _show(personal_bot, mk_update, h, run_async)
    labels = [t for t, _ in h.buttons_of(msg, upd.message)]
    assert "Update both" not in labels, labels


def test_local_path_source_shown_in_dm(world, personal_bot, mk_update, h, run_async):
    world.origin = "local"
    upd, msg = _show(personal_bot, mk_update, h, run_async)
    assert world.local_path in _status_text(upd, msg, h, personal_bot)


def test_status_check_runs_no_installer_and_no_restart(world, personal_bot, mk_update, h, run_async):
    _show(personal_bot, mk_update, h, run_async)
    assert not world.upgrade_calls() and not world.schedule_calls() \
        and not world.claude_update_calls()


def test_update_is_in_the_bot_command_menu():
    from aipager.bot.lifecycle import LifecycleMixin
    assert "update" in {c.command for c in LifecycleMixin._command_list(set())}


def test_update_is_in_help_text(world, personal_bot, mk_update, h, run_async):
    upd = mk_update("/help", user_id=h.OPERATOR, chat_id=h.DM)
    upd.effective_message = upd.message
    upd.effective_chat.type = "private"
    run_async(personal_bot._handle_start_cmd(upd, MagicMock()))
    assert "/update" in "\n".join(h.texts_of(upd.message, personal_bot._app.bot))
