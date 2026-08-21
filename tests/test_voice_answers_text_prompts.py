"""A voice message must be able to answer the wizard and a rename prompt.

`_handle_message` runs free text through two capture hooks before any
session routing: `new_flow.maybe_handle_text` (the `/new` wizard's name,
custom-model and new-folder steps) and `session_parity.maybe_handle_text`
(a pending `/rename`). `_dispatch_voice_transcript` called neither, while
its docstring claimed it "mirrors the routing precedence of
`_handle_message` … so the user's voice behaves like their typed text
would".

The failure was not benign: the transcript went to the active session as
a prompt, so the wizard sat waiting while Claude was asked "my project
two" as a question.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from aipager.bot import new_flow
from aipager.state import Status


def _voice_update(mk_update, text=""):
    upd = mk_update(text, chat_id=555)
    upd.message.reply_text = AsyncMock()
    upd.message.reply_to_message = None
    return upd


def _pending_wizard_at_name_step(bot, chat_id=555):
    upd = MagicMock()
    upd.message = MagicMock()
    upd.message.reply_text = AsyncMock(
        return_value=MagicMock(message_id=900))
    upd.effective_chat = MagicMock(id=chat_id, type="private")
    upd.effective_user = MagicMock(id=12345)
    return upd


def test_a_voice_message_answers_the_new_wizard(mk_bot, mk_update, run_async):
    """The reported defect: say the session name instead of typing it."""
    bot = mk_bot()
    start = _pending_wizard_at_name_step(bot)
    run_async(new_flow.start_wizard(bot, start, MagicMock()))
    assert new_flow._pending_store(bot)[555]["step"] == "name"

    upd = _voice_update(mk_update)
    bot._app.bot.edit_message_text = AsyncMock()

    run_async(bot._dispatch_voice_transcript(upd, "voicenamed"))

    assert new_flow._pending_store(bot)[555].get("name") == "voicenamed", (
        "the wizard never saw the transcript")


def test_a_consumed_transcript_is_not_also_sent_to_a_session(
        mk_bot, mk_update, run_async, monkeypatch):
    """The early return must be real: a transcript the wizard took must
    not ALSO be injected as a prompt."""
    bot = mk_bot()
    sess = bot.registry.get_or_create("claude-active")
    sess.label = "active"
    bot.registry.transition("claude-active", Status.IDLE)
    bot.registry.last_active_session = "claude-active"

    start = _pending_wizard_at_name_step(bot)
    run_async(new_flow.start_wizard(bot, start, MagicMock()))

    # `is_alive` must be True, or the routing bails at "session not found"
    # long before injection and this test passes without proving anything —
    # it did exactly that on the first draft.
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=True))
    bot._inject_prompt = AsyncMock(return_value=True)
    bot._maybe_update_bot_name = AsyncMock()
    bot._react = AsyncMock()
    bot._send_busy_and_animate = AsyncMock()
    upd = _voice_update(mk_update)
    bot._app.bot.edit_message_text = AsyncMock()

    run_async(bot._dispatch_voice_transcript(upd, "voicenamed2"))

    bot._inject_prompt.assert_not_awaited()


def test_a_voice_message_answers_a_pending_rename(mk_bot, mk_update,
                                                  run_async):
    from aipager.bot import session_parity as sp

    bot = mk_bot()
    sess = bot.registry.get_or_create("claude-oldname")
    sess.label = "oldname"
    bot.registry.transition("claude-oldname", Status.IDLE)

    # Arm the rename capture the way the ⋮ menu does.
    # The pending record is a dict, not a bare name — `maybe_handle_text`
    # reads `pending["session_name"]`. Arm it through the module's own
    # accessor so the shape can't drift out from under this test.
    sp._rename_pending_map(bot)[555] = {"session_name": sess.name}

    upd = _voice_update(mk_update)
    bot._app.bot.edit_message_text = AsyncMock()
    bot._inject_prompt = AsyncMock(return_value=True)

    # Through the VOICE path, not by calling the hook directly — calling
    # `sp.maybe_handle_text` myself proved the hook works and said nothing
    # about whether `_dispatch_voice_transcript` reaches it, which is the
    # whole defect. Removing the hook left that version green.
    run_async(bot._dispatch_voice_transcript(upd, "newname"))

    assert sess.label == "newname", (
        f"the rename capture never saw the transcript (label={sess.label!r})")
    bot._inject_prompt.assert_not_awaited()


def test_ordinary_voice_still_reaches_the_session(mk_bot, mk_update,
                                                  run_async, monkeypatch):
    """No wizard, no rename: unchanged behaviour."""
    bot = mk_bot()
    sess = bot.registry.get_or_create("claude-active2")
    sess.label = "active2"
    bot.registry.transition("claude-active2", Status.IDLE)
    bot.registry.last_active_session = "claude-active2"

    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=True))
    bot._inject_prompt = AsyncMock(return_value=True)
    bot._maybe_update_bot_name = AsyncMock()
    bot._react = AsyncMock()
    bot._send_busy_and_animate = AsyncMock()

    upd = _voice_update(mk_update)
    run_async(bot._dispatch_voice_transcript(upd, "do the thing"))

    bot._inject_prompt.assert_awaited()


def test_the_voice_handler_passes_its_context_to_the_hooks(mk_bot, mk_update,
                                                           run_async,
                                                           monkeypatch, tmp_path):
    """`_handle_voice` has a real `ctx`; it must hand it on.

    Neither hook dereferences `ctx` today, so passing `None` was silent —
    and would have stayed silent right up until one of them started using
    it, at which point voice would crash where text does not.
    """
    bot = mk_bot()
    upd = mk_update("", chat_id=555)
    upd.message.voice = MagicMock(file_size=1000)
    fake_file = MagicMock()
    fake_file.download_to_drive = AsyncMock()
    upd.message.voice.get_file = AsyncMock(return_value=fake_file)
    ack = MagicMock()
    ack.edit_text = AsyncMock()
    upd.message.reply_text = AsyncMock(return_value=ack)
    monkeypatch.setattr("aipager.bot.voice.is_available", lambda: True)
    monkeypatch.setattr("aipager.bot.voice.transcribe",
                        AsyncMock(return_value="spoken words"))
    monkeypatch.setattr("aipager.bot.handlers.FILE_DOWNLOAD_DIR", tmp_path)
    bot._dispatch_voice_transcript = AsyncMock()

    sentinel = MagicMock(name="ctx")
    run_async(bot._handle_voice(upd, sentinel))

    args = bot._dispatch_voice_transcript.await_args.args
    assert sentinel in args, (
        f"_handle_voice dropped its ctx on the floor: {args!r}")
