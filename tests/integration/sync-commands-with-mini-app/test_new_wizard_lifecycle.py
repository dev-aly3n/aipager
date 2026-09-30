"""Priority 2 (task brief): the `/new` Name card must never wedge a chat.

Since the 2026-09-30 redesign the card asks one thing, the name: the next
message from the person who opened it starts the session and the card
becomes the Ready card (no mode step, no summary, no Confirm).

Exercises the wizard exclusively through the real dispatch seams —
`bot._handle_new_cmd` (entry), `bot._handle_message` (the two narrow
text-capture windows), and `bot._handle_callback` (every button tap) —
never `new_flow.start_wizard`/`maybe_handle_text`/`handle_callback`
directly. All assertions read the wizard's rendered state off
`bot._app.bot.edit_message_text`, the same "one message, edited in
place across turns" channel already used by `dashboard.py`/
`animation.py`/`notify.py` — not off internal `new_flow`/
`bot._new_wizard_pending` state.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


CHAT = 555  # private chat — positive id


def _start_wizard(helpers, bot, *, first_message_id=9001):
    upd = helpers.make_message_update("/new", chat_id=CHAT, chat_type="private")
    upd.message.reply_text.return_value.message_id = first_message_id
    _run(bot._handle_new_cmd(upd, MagicMock()))
    return upd


def _send_text(helpers, bot, text):
    upd = helpers.make_message_update(text, chat_id=CHAT, chat_type="private")
    _run(bot._handle_message(upd, MagicMock()))
    return upd


def _tap(helpers, bot, callback_data, *, message_id=9001):
    upd, q = helpers.make_callback_update(
        callback_data, chat_id=CHAT, chat_type="private",
        message_id=message_id,
    )
    _run(bot._handle_callback(upd, MagicMock()))
    return upd, q


async def _launch_ok(*a, **kw):
    return True, ""


@pytest.fixture(autouse=True)
def _no_real_launch():
    with patch("aipager.dtach.inject.launch_session", side_effect=_launch_ok):
        yield


# --------------------------------------------------------------------------- #
# Happy path through the real dispatchers                                     #
# --------------------------------------------------------------------------- #

def test_wizard_start_sends_a_name_prompt(mk_bot, helpers):
    bot = helpers.make_personal_bot(mk_bot)
    upd = _start_wizard(helpers, bot)
    upd.message.reply_text.assert_awaited_once()
    assert "Send a name" in upd.message.reply_text.await_args[0][0]


def test_the_name_starts_the_session_and_the_card_becomes_ready(mk_bot, helpers):
    bot = helpers.make_personal_bot(mk_bot)
    _start_wizard(helpers, bot)
    _send_text(helpers, bot, "wizname1")
    assert bot.registry.find_by_label("wizname1", CHAT) is not None
    text, markup, *_ = helpers.latest_edit(bot)
    assert "wizname1</b> is ready" in text
    assert "_:nw:cancel" not in helpers.callback_data_in(markup)


def test_the_name_card_has_the_mode_toggle_and_cancel(mk_bot, helpers):
    bot = helpers.make_personal_bot(mk_bot)
    upd = _start_wizard(helpers, bot)
    markup = upd.message.reply_text.await_args.kwargs["reply_markup"]
    cbs = helpers.callback_data_in(markup)
    assert "_:nw:mode:ask" in cbs and "_:nw:cancel" in cbs


def test_the_mode_toggle_keeps_the_card_waiting_for_a_name(mk_bot, helpers):
    bot = helpers.make_personal_bot(mk_bot)
    _start_wizard(helpers, bot)
    _tap(helpers, bot, "_:nw:mode:ask")
    text, markup, *_ = helpers.latest_edit(bot)
    assert "💬 Ask" in text and "Send a name" in text
    assert "_:nw:mode:auto" in helpers.callback_data_in(markup)
    _send_text(helpers, bot, "askmode1")
    sess = bot.registry.find_by_label("askmode1", CHAT)
    assert sess is not None and sess.skip_perms is False


def test_the_model_picker_goes_back_to_the_name_card(mk_bot, helpers):
    bot = helpers.make_personal_bot(mk_bot)
    _start_wizard(helpers, bot)
    _tap(helpers, bot, "_:nw:opt:model")
    _, markup, *_ = helpers.latest_edit(bot)
    assert "_:nw:back" in helpers.callback_data_in(markup)
    _tap(helpers, bot, "_:nw:back")
    text, *_ = helpers.latest_edit(bot)
    assert "Send a name" in text


# --------------------------------------------------------------------------- #
# Wedge resistance                                                            #
# --------------------------------------------------------------------------- #

def test_second_new_replaces_a_pending_wizard_with_a_fresh_one(mk_bot, helpers):
    """A second bare `/new` replaces the open card: the old message's
    keyboard is stripped with a "Cancelled - started over." edit."""
    bot = helpers.make_personal_bot(mk_bot)
    _start_wizard(helpers, bot, first_message_id=9001)

    second = helpers.make_message_update("/new", chat_id=CHAT, chat_type="private")
    second.message.reply_text.return_value.message_id = 9002
    _run(bot._handle_new_cmd(second, MagicMock()))

    matches = [
        (text, mid) for text, _, _, mid in helpers.all_edits(bot)
        if mid == 9001
    ]
    assert matches, "expected an edit targeting the first card's message_id"
    assert any("started over" in (t or "").lower() for t, _ in matches), matches
    second.message.reply_text.assert_awaited_once()


def test_second_new_wizard_creates_only_its_own_session(mk_bot, helpers):
    bot = helpers.make_personal_bot(mk_bot)
    _start_wizard(helpers, bot, first_message_id=9001)
    second = helpers.make_message_update("/new", chat_id=CHAT, chat_type="private")
    second.message.reply_text.return_value.message_id = 9002
    _run(bot._handle_new_cmd(second, MagicMock()))
    _send_text(helpers, bot, "secondname")

    assert bot.registry.find_by_label("secondname", CHAT) is not None
    ready = [mid for text, _, _, mid in helpers.all_edits(bot)
             if "is ready" in (text or "")]
    assert ready == [9002]


def test_cancel_clears_the_wizard(mk_bot, helpers):
    bot = helpers.make_personal_bot(mk_bot)
    _start_wizard(helpers, bot)
    _tap(helpers, bot, "_:nw:cancel")
    text, markup, *_ = helpers.latest_edit(bot)
    assert markup is None or helpers.callback_data_in(markup) == []


def test_text_after_cancel_is_not_captured_as_a_name(mk_bot, helpers):
    bot = helpers.make_personal_bot(mk_bot)
    _start_wizard(helpers, bot)
    _tap(helpers, bot, "_:nw:cancel")

    upd = _send_text(helpers, bot, "shouldnotbecapturedasaname")
    upd.message.reply_text.assert_awaited_once()
    reply = upd.message.reply_text.await_args[0][0]
    assert "don't know which session" in reply.lower()
    assert bot.registry.find_by_label("shouldnotbecapturedasaname", CHAT) is None


def test_tap_belonging_to_a_wizard_that_no_longer_exists_does_not_crash(mk_bot, helpers):
    """A stale tap after Cancel has already cleared the card must fail
    gracefully, never raise."""
    bot = helpers.make_personal_bot(mk_bot)
    _start_wizard(helpers, bot)
    _tap(helpers, bot, "_:nw:cancel")
    _tap(helpers, bot, "_:nw:mode:auto")


def test_tap_for_a_wizard_that_was_never_started_does_not_crash(mk_bot, helpers):
    """A completely orphaned callback (no /new in this chat, or a button
    from the old four-step wizard) fails closed, never raises."""
    bot = helpers.make_personal_bot(mk_bot)
    _tap(helpers, bot, "_:nw:confirm", message_id=1234)


def test_wizard_state_is_per_chat_not_global(mk_bot, helpers):
    """Two chats each running /new never see each other's card."""
    bot = helpers.make_personal_bot(mk_bot)
    upd_a = helpers.make_message_update("/new", chat_id=111, chat_type="private")
    upd_a.message.reply_text.return_value.message_id = 5001
    _run(bot._handle_new_cmd(upd_a, MagicMock()))

    upd_b = helpers.make_message_update("/new", chat_id=222, chat_type="private")
    upd_b.message.reply_text.return_value.message_id = 5002
    _run(bot._handle_new_cmd(upd_b, MagicMock()))

    name_a = helpers.make_message_update("nameforchata", chat_id=111, chat_type="private")
    _run(bot._handle_message(name_a, MagicMock()))
    text_a, *_ = helpers.latest_edit(bot)
    assert "nameforchata" in text_a

    name_b = helpers.make_message_update("nameforchatb", chat_id=222, chat_type="private")
    _run(bot._handle_message(name_b, MagicMock()))
    text_b, *_ = helpers.latest_edit(bot)
    assert "nameforchatb" in text_b
    assert "nameforchata" not in text_b
