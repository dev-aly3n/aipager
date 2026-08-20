"""Priority 2 (task brief): the wizard must never wedge a chat.

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


# --------------------------------------------------------------------------- #
# Happy path, step by step — proves the state machine + callback contract   #
# from entrypoints.md actually holds through the real dispatchers.          #
# --------------------------------------------------------------------------- #

def test_wizard_start_sends_a_name_prompt(mk_bot, helpers):
    bot = helpers.make_personal_bot(mk_bot)
    upd = _start_wizard(helpers, bot)
    upd.message.reply_text.assert_awaited_once()


def test_wizard_name_step_advances_to_mode_buttons(mk_bot, helpers):
    bot = helpers.make_personal_bot(mk_bot)
    _start_wizard(helpers, bot)
    _send_text(helpers, bot, "wizname1")
    _, markup, *_ = helpers.latest_edit(bot)
    cbs = helpers.callback_data_in(markup)
    assert "_:nw:mode:auto" in cbs
    assert "_:nw:mode:ask" in cbs


def test_wizard_mode_step_has_cancel_button(mk_bot, helpers):
    bot = helpers.make_personal_bot(mk_bot)
    _start_wizard(helpers, bot)
    _send_text(helpers, bot, "wizname1")
    _, markup, *_ = helpers.latest_edit(bot)
    assert "_:nw:cancel" in helpers.callback_data_in(markup)


def test_wizard_mode_tap_ask_advances_to_summary(mk_bot, helpers):
    bot = helpers.make_personal_bot(mk_bot)
    _start_wizard(helpers, bot)
    _send_text(helpers, bot, "wizname1")
    _tap(helpers, bot, "_:nw:mode:ask")
    text, markup, *_ = helpers.latest_edit(bot)
    cbs = helpers.callback_data_in(markup)
    assert "_:nw:confirm" in cbs
    assert "_:nw:opt" in cbs


def test_wizard_summary_shows_the_chosen_name(mk_bot, helpers):
    """design.md: "The summary must show what will be created before
    Confirm — name, mode, and any options chosen"."""
    bot = helpers.make_personal_bot(mk_bot)
    _start_wizard(helpers, bot)
    _send_text(helpers, bot, "wizname1")
    _tap(helpers, bot, "_:nw:mode:ask")
    text, *_ = helpers.latest_edit(bot)
    assert "wizname1" in text


def test_wizard_summary_step_has_cancel_button(mk_bot, helpers):
    bot = helpers.make_personal_bot(mk_bot)
    _start_wizard(helpers, bot)
    _send_text(helpers, bot, "wizname1")
    _tap(helpers, bot, "_:nw:mode:ask")
    _, markup, *_ = helpers.latest_edit(bot)
    assert "_:nw:cancel" in helpers.callback_data_in(markup)


def test_wizard_optional_menu_has_model_and_path_and_back(mk_bot, helpers):
    bot = helpers.make_personal_bot(mk_bot)
    _start_wizard(helpers, bot)
    _send_text(helpers, bot, "wizname1")
    _tap(helpers, bot, "_:nw:mode:ask")
    _tap(helpers, bot, "_:nw:opt")
    _, markup, *_ = helpers.latest_edit(bot)
    cbs = helpers.callback_data_in(markup)
    assert "_:nw:opt:model" in cbs
    assert "_:nw:opt:path" in cbs
    assert "_:nw:summary" in cbs  # "<< Back" returns to summary


def test_wizard_back_from_optional_preserves_the_name(mk_bot, helpers):
    """design.md: "Editing after choosing. Returning from a sub-menu
    must preserve earlier choices; picking a model must not reset the
    name.""" ""
    bot = helpers.make_personal_bot(mk_bot)
    _start_wizard(helpers, bot)
    _send_text(helpers, bot, "wizname1")
    _tap(helpers, bot, "_:nw:mode:ask")
    _tap(helpers, bot, "_:nw:opt")
    _tap(helpers, bot, "_:nw:summary")
    text, *_ = helpers.latest_edit(bot)
    assert "wizname1" in text


def test_wizard_confirm_creates_a_session(mk_bot, helpers):
    bot = helpers.make_personal_bot(mk_bot)

    async def _launch_ok(*a, **kw):
        return True, ""

    _start_wizard(helpers, bot)
    _send_text(helpers, bot, "wizconfirm1")
    _tap(helpers, bot, "_:nw:mode:ask")
    with patch("aipager.dtach.inject.launch_session", side_effect=_launch_ok):
        _tap(helpers, bot, "_:nw:confirm")
    sess = bot.registry.find_by_label("wizconfirm1", CHAT)
    assert sess is not None


# --------------------------------------------------------------------------- #
# Wedge resistance — the core ask of priority 2.                            #
# --------------------------------------------------------------------------- #

def test_unrelated_text_at_mode_step_is_not_swallowed_by_the_wizard(mk_bot, helpers):
    """design.md Risks: "every non-text-capture wizard step (mode,
    summary, optional submenus) does NOT intercept text, so normal
    session routing is unaffected outside the two narrow text-capture
    windows." At the mode step (button-only), free text must fall
    through to ordinary routing rather than being read as a session
    name."""
    bot = helpers.make_personal_bot(mk_bot)
    _start_wizard(helpers, bot)
    _send_text(helpers, bot, "wizname1")  # now at mode step
    edits_before = bot._app.bot.edit_message_text.await_count

    stray = _send_text(helpers, bot, "hey what is going on here")

    # The wizard's own rendered message must NOT have been touched by
    # this stray text.
    assert bot._app.bot.edit_message_text.await_count == edits_before
    # And normal (no-active-session) routing must have handled it.
    stray.message.reply_text.assert_awaited_once()
    reply = stray.message.reply_text.await_args[0][0]
    assert "don't know which session" in reply.lower()


def test_unrelated_text_at_mode_step_does_not_wedge_the_wizard(mk_bot, helpers):
    """Continuation of the above: after the stray message, the wizard
    must still be alive and respond correctly to the mode button tap —
    proving the interruption didn't corrupt or clear its state."""
    bot = helpers.make_personal_bot(mk_bot)
    _start_wizard(helpers, bot)
    _send_text(helpers, bot, "wizname1")
    _send_text(helpers, bot, "hey what is going on here")

    _tap(helpers, bot, "_:nw:mode:auto")
    text, markup, *_ = helpers.latest_edit(bot)
    assert "_:nw:confirm" in helpers.callback_data_in(markup)


def test_unrelated_text_at_summary_step_is_not_swallowed(mk_bot, helpers):
    bot = helpers.make_personal_bot(mk_bot)
    _start_wizard(helpers, bot)
    _send_text(helpers, bot, "wizname1")
    _tap(helpers, bot, "_:nw:mode:ask")  # now at summary step
    edits_before = bot._app.bot.edit_message_text.await_count

    stray = _send_text(helpers, bot, "some completely unrelated line of text")

    assert bot._app.bot.edit_message_text.await_count == edits_before
    stray.message.reply_text.assert_awaited_once()


def test_second_new_replaces_a_pending_wizard_with_a_fresh_one(mk_bot, helpers):
    """design.md: starting a second `/new` (no args) while one is
    pending silently replaces it — the old message's keyboard is
    stripped with a "Cancelled — started over." edit."""
    bot = helpers.make_personal_bot(mk_bot)
    _start_wizard(helpers, bot, first_message_id=9001)
    _send_text(helpers, bot, "firstname")  # first wizard now at mode step

    second = helpers.make_message_update("/new", chat_id=CHAT, chat_type="private")
    second.message.reply_text.return_value.message_id = 9002
    _run(bot._handle_new_cmd(second, MagicMock()))

    # The FIRST wizard's message (9001) was edited to say it was
    # cancelled/started over.
    matches = [
        (text, mid) for text, _, _, mid in helpers.all_edits(bot)
        if mid == 9001
    ]
    assert matches, "expected an edit targeting the first wizard's message_id"
    assert any("started over" in (t or "").lower() for t, _ in matches), matches
    # A fresh name prompt was sent for the second wizard.
    second.message.reply_text.assert_awaited_once()


def test_second_new_wizard_creates_only_its_own_session(mk_bot, helpers):
    """The abandoned first wizard's name must never be created — only
    completing the SECOND wizard creates a session, and it's the
    second one's name."""
    bot = helpers.make_personal_bot(mk_bot)

    async def _launch_ok(*a, **kw):
        return True, ""

    _start_wizard(helpers, bot, first_message_id=9001)
    _send_text(helpers, bot, "abandonedname")

    second = helpers.make_message_update("/new", chat_id=CHAT, chat_type="private")
    second.message.reply_text.return_value.message_id = 9002
    _run(bot._handle_new_cmd(second, MagicMock()))
    _send_text(helpers, bot, "secondname")
    _tap(helpers, bot, "_:nw:mode:ask", message_id=9002)
    with patch("aipager.dtach.inject.launch_session", side_effect=_launch_ok):
        _tap(helpers, bot, "_:nw:confirm", message_id=9002)

    assert bot.registry.find_by_label("secondname", CHAT) is not None
    assert bot.registry.find_by_label("abandonedname", CHAT) is None


def test_cancel_clears_the_wizard(mk_bot, helpers):
    bot = helpers.make_personal_bot(mk_bot)
    _start_wizard(helpers, bot)
    _send_text(helpers, bot, "wizname1")
    _tap(helpers, bot, "_:nw:cancel")
    text, markup, *_ = helpers.latest_edit(bot)
    assert markup is None or helpers.callback_data_in(markup) == []


def test_text_after_cancel_is_not_captured_as_a_name(mk_bot, helpers):
    bot = helpers.make_personal_bot(mk_bot)
    _start_wizard(helpers, bot)
    _send_text(helpers, bot, "wizname1")
    _tap(helpers, bot, "_:nw:cancel")

    upd = _send_text(helpers, bot, "shouldnotbecapturedasaname")
    upd.message.reply_text.assert_awaited_once()
    reply = upd.message.reply_text.await_args[0][0]
    assert "don't know which session" in reply.lower()


def test_tap_belonging_to_a_wizard_that_no_longer_exists_does_not_crash(mk_bot, helpers):
    """A stale tap after Cancel has already cleared the pending wizard
    for this chat — must fail gracefully, never raise."""
    bot = helpers.make_personal_bot(mk_bot)
    _start_wizard(helpers, bot)
    _send_text(helpers, bot, "wizname1")
    _tap(helpers, bot, "_:nw:cancel")

    # No exception should propagate from this tap.
    _tap(helpers, bot, "_:nw:mode:auto")


def test_tap_for_a_wizard_that_was_never_started_does_not_crash(mk_bot, helpers):
    """A completely orphaned callback_data (no /new was ever run in
    this chat) must also fail closed, not raise."""
    bot = helpers.make_personal_bot(mk_bot)
    _tap(helpers, bot, "_:nw:confirm", message_id=1234)


def test_wizard_state_is_per_chat_not_global(mk_bot, helpers):
    """Two different chats each running /new must not see each
    other's in-progress name/mode."""
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
