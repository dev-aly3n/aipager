"""SC-1: `/update` from the operator's DM shows running/latest aipager, the
install source, current/latest Claude Code, the restart mode and the
buttons; an unreachable network shows "unknown" and no traceback.
(design.md Success criteria #1, entrypoints.md "Telegram commands")."""

from __future__ import annotations

from unittest.mock import MagicMock

from aipager.bot import update_flow


def _cmd(mk_update, h, *, user_id, chat_id):
    upd = mk_update("/update", user_id=user_id, chat_id=chat_id)
    msg = h.status_message(chat_id, 900)
    upd.message.chat = MagicMock()
    upd.message.chat.id = chat_id
    upd.message.reply_text.return_value = msg
    upd.effective_message = upd.message
    return upd, msg


def _show(bot, mk_update, h, run_async, *, user_id=None, chat_id=None):
    user_id = user_id or h.OPERATOR
    chat_id = chat_id or h.DM
    upd, msg = _cmd(mk_update, h, user_id=user_id, chat_id=chat_id)

    async def go():
        await update_flow.handle_update_cmd(bot, upd, MagicMock())
        await h.wait_for(lambda: msg.edit_text.await_count > 0, 5)
    run_async(go())
    return upd, msg


def _status_text(upd, msg, h, bot):
    return "\n".join(h.texts_of(msg, upd.message, bot._app.bot))


def test_update_first_replies_checking_versions(world, personal_bot, mk_update, h, run_async):
    upd, _ = _show(personal_bot, mk_update, h, run_async)
    first = upd.message.reply_text.await_args_list[0]
    text = first.args[0] if first.args else first.kwargs.get("text", "")
    assert "Checking versions" in text


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
    assert "automatic (systemd)" in _status_text(upd, msg, h, personal_bot)


def test_status_shows_manual_restart_mode_in_foreground(world, personal_bot, mk_update, h, run_async):
    world.set_under_unit(False)
    upd, msg = _show(personal_bot, mk_update, h, run_async)
    assert "manual" in _status_text(upd, msg, h, personal_bot)


def test_status_offers_all_four_buttons(world, personal_bot, mk_update, h, run_async):
    upd, msg = _show(personal_bot, mk_update, h, run_async)
    labels = " | ".join(t for t, _ in h.buttons_of(msg, upd.message))
    assert all(x in labels for x in ("Update Claude Code", "Update aipager", "Both", "Cancel")), labels


def test_status_buttons_carry_documented_callback_data(world, personal_bot, mk_update, h, run_async):
    upd, msg = _show(personal_bot, mk_update, h, run_async)
    data = {d for _, d in h.buttons_of(msg, upd.message)}
    assert {"_:up:cc", "_:up:ap", "_:up:both", "_:up:x"} <= data, data


def test_network_down_shows_unknown(world, personal_bot, mk_update, h, run_async):
    world.latest_pypi = None
    world.claude_latest = None
    upd, msg = _show(personal_bot, mk_update, h, run_async)
    assert "unknown" in _status_text(upd, msg, h, personal_bot)


def test_network_down_shows_no_traceback(world, personal_bot, mk_update, h, run_async):
    world.latest_pypi = None
    world.claude_latest = None
    upd, msg = _show(personal_bot, mk_update, h, run_async)
    text = _status_text(upd, msg, h, personal_bot)
    assert "Traceback" not in text and "Error" not in text and "Exception" not in text


def test_garbage_pypi_body_shows_unknown_not_the_body(world, personal_bot, mk_update, h, run_async, monkeypatch):
    from aipager import self_update
    monkeypatch.setattr(self_update, "_http_get",
                        lambda url, *, timeout, max_bytes: b"<html>502 Bad Gateway</html>")
    upd, msg = _show(personal_bot, mk_update, h, run_async)
    text = _status_text(upd, msg, h, personal_bot)
    assert "Bad Gateway" not in text and "unknown" in text


def test_fetch_that_raises_shows_unknown_not_an_error(world, personal_bot, mk_update, h, run_async, monkeypatch):
    """Error guessing: a seam that violates its never-raise contract
    (e.g. a socket timeout leaking) must still render "unknown"."""
    from aipager import self_update

    def boom(url, *, timeout, max_bytes):
        raise TimeoutError("timed out reading pypi.org")
    monkeypatch.setattr(self_update, "_http_get", boom)
    upd, msg = _show(personal_bot, mk_update, h, run_async)
    text = _status_text(upd, msg, h, personal_bot)
    assert "timed out reading" not in text and "unknown" in text


def test_claude_not_found_hides_update_claude_button(world, personal_bot, mk_update, h, run_async):
    world.claude_path = None
    upd, msg = _show(personal_bot, mk_update, h, run_async)
    labels = [t for t, _ in h.buttons_of(msg, upd.message)]
    assert not any("Claude Code" in t for t in labels), labels


def test_claude_not_found_hides_both_button(world, personal_bot, mk_update, h, run_async):
    world.claude_path = None
    upd, msg = _show(personal_bot, mk_update, h, run_async)
    labels = [t for t, _ in h.buttons_of(msg, upd.message)]
    assert "Both" not in labels, labels


def test_editable_install_hides_update_aipager_button(world, personal_bot, mk_update, h, run_async):
    world.origin = "editable"
    upd, msg = _show(personal_bot, mk_update, h, run_async)
    labels = [t for t, _ in h.buttons_of(msg, upd.message)]
    assert not any("Update aipager" in t for t in labels), labels


def test_editable_install_hides_both_button(world, personal_bot, mk_update, h, run_async):
    world.origin = "editable"
    upd, msg = _show(personal_bot, mk_update, h, run_async)
    labels = [t for t, _ in h.buttons_of(msg, upd.message)]
    assert "Both" not in labels, labels


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
