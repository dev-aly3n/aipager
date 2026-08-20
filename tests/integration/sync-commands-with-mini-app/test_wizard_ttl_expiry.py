"""design.md / entrypoints.md: "Idle for more than 10 minutes
(`_WIZARD_TTL_SECONDS = 600`, checked lazily on the next tap or text
message, no background timer) -> the wizard is treated as gone; the
next interaction with its stale message replies '⏱ This
session-creation wizard expired. Run /new again.' and clears its
keyboard."

This is the one success criterion iteration 1 flagged as untested,
specifically because it requires controlling elapsed time without
touching the global ``time``/``asyncio`` modules (the exact mistake
that OOM-killed this machine twice via ``asyncio.sleep`` patching).
The safe seam is ``aipager.bot.new_flow._now`` — a plain, synchronous
function with no bearing on the event loop's own scheduling — patched
directly rather than the global ``time`` module. 600 seconds is taken
verbatim from entrypoints.md's own contract text, never derived from
reading the constant out of the source module.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

from aipager.bot import new_flow

TTL_SECONDS = 600  # entrypoints.md's own documented number, not read from source


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


CHAT = 555  # private chat


def test_a_button_tap_on_an_expired_wizard_says_so_and_does_not_advance(
        mk_bot, helpers):
    bot = helpers.make_personal_bot(mk_bot)

    start = helpers.make_message_update("/new", chat_id=CHAT, chat_type="private")
    start.message.reply_text.return_value.message_id = 9001
    _run(bot._handle_new_cmd(start, MagicMock()))

    name_upd = helpers.make_message_update(
        "ttlwizname", chat_id=CHAT, chat_type="private")
    _run(bot._handle_message(name_upd, MagicMock()))  # now at `mode`

    baseline = new_flow._now()  # real clock read, taken AFTER the last touch
    with patch("aipager.bot.new_flow._now", lambda: baseline + TTL_SECONDS + 1):
        upd, q = helpers.make_callback_update(
            "_:nw:mode:ask", chat_id=CHAT, chat_type="private", message_id=9001)
        _run(bot._handle_callback(upd, MagicMock()))

    # Must not have silently advanced to `summary` (no Confirm button).
    if bot._app.bot.edit_message_text.await_args_list:
        text, markup, *_ = helpers.latest_edit(bot)
        cbs = helpers.callback_data_in(markup)
        assert "_:nw:confirm" not in cbs, (
            "an expired wizard must not silently advance past `mode`")
    assert bot.registry.find_by_label("ttlwizname", CHAT) is None


def test_a_button_tap_on_an_expired_wizard_shows_the_expiry_message(
        mk_bot, helpers):
    bot = helpers.make_personal_bot(mk_bot)

    start = helpers.make_message_update("/new", chat_id=CHAT, chat_type="private")
    start.message.reply_text.return_value.message_id = 9001
    _run(bot._handle_new_cmd(start, MagicMock()))
    name_upd = helpers.make_message_update(
        "ttlwizname2", chat_id=CHAT, chat_type="private")
    _run(bot._handle_message(name_upd, MagicMock()))

    baseline = new_flow._now()
    with patch("aipager.bot.new_flow._now", lambda: baseline + TTL_SECONDS + 1):
        upd, q = helpers.make_callback_update(
            "_:nw:mode:ask", chat_id=CHAT, chat_type="private", message_id=9001)
        _run(bot._handle_callback(upd, MagicMock()))

    surfaces = []
    if bot._app.bot.edit_message_text.await_args_list:
        surfaces.append(helpers.latest_edit(bot)[0] or "")
    for c in q.answer.await_args_list:
        if c.args:
            surfaces.append(c.args[0] or "")
    for c in q.edit_message_text.await_args_list:
        surfaces.append((c.kwargs.get("text") or (c.args[0] if c.args else "")) or "")

    assert any("expired" in s.lower() for s in surfaces), (
        f"expected an 'expired' message on the stale tap, got: {surfaces}")


def test_text_sent_to_an_expired_wizards_name_step_shows_the_expiry_message(
        mk_bot, helpers):
    """entrypoints.md: expiry is checked "on the next tap OR text
    message" — text-capture steps (like `name`) must be covered too,
    not only button taps. NOTE: merely asserting "no session was
    created" here would pass whether or not expiry fired at all — typing
    a name never creates a session by itself (only Confirm does); the
    real signal that the TTL path fired is the wizard's message being
    edited to say so.
    """
    bot = helpers.make_personal_bot(mk_bot)

    start = helpers.make_message_update("/new", chat_id=CHAT, chat_type="private")
    start.message.reply_text.return_value.message_id = 9101
    _run(bot._handle_new_cmd(start, MagicMock()))  # pending at `name` step

    baseline = new_flow._now()
    with patch("aipager.bot.new_flow._now", lambda: baseline + TTL_SECONDS + 1):
        stray = helpers.make_message_update(
            "somenamesenttoastalewiz", chat_id=CHAT, chat_type="private")
        _run(bot._handle_message(stray, MagicMock()))

    text, markup, *_ = helpers.latest_edit(bot)
    assert "expired" in (text or "").lower(), (
        f"expected the wizard's message to be edited with an expiry "
        f"notice, got: {text!r}"
    )
    assert markup is None or helpers.callback_data_in(markup) == [], (
        "an expired wizard's keyboard must be cleared"
    )
    assert bot.registry.find_by_label(
        "somenamesenttoastalewiz", CHAT) is None


def test_wizard_survives_expiry_and_a_new_new_starts_clean(mk_bot, helpers):
    """Expiry must be a soft reset, not a wedge: after an expired wizard
    is discovered, `/new` must still work normally."""
    bot = helpers.make_personal_bot(mk_bot)

    start = helpers.make_message_update("/new", chat_id=CHAT, chat_type="private")
    start.message.reply_text.return_value.message_id = 9201
    _run(bot._handle_new_cmd(start, MagicMock()))
    name_upd = helpers.make_message_update(
        "ttlsurvivewiz", chat_id=CHAT, chat_type="private")
    _run(bot._handle_message(name_upd, MagicMock()))

    baseline = new_flow._now()
    with patch("aipager.bot.new_flow._now", lambda: baseline + TTL_SECONDS + 1):
        upd, q = helpers.make_callback_update(
            "_:nw:mode:ask", chat_id=CHAT, chat_type="private", message_id=9201)
        _run(bot._handle_callback(upd, MagicMock()))

    # Confirm this run actually went through the expiry path (otherwise
    # "a new /new starts clean" below would hold trivially regardless of
    # whether the TTL guard did anything at all).
    expired_text, *_ = helpers.latest_edit(bot)
    assert "expired" in (expired_text or "").lower(), expired_text

    second = helpers.make_message_update("/new", chat_id=CHAT, chat_type="private")
    second.message.reply_text.return_value.message_id = 9202
    _run(bot._handle_new_cmd(second, MagicMock()))
    second.message.reply_text.assert_awaited_once()

    fresh_name = helpers.make_message_update(
        "freshafterexpiry", chat_id=CHAT, chat_type="private")
    _run(bot._handle_message(fresh_name, MagicMock()))
    text, markup, *_ = helpers.latest_edit(bot)
    assert "freshafterexpiry" in text
