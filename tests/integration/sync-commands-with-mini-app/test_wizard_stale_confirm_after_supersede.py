"""design.md: starting a second `/new` (no args) while one is pending
"silently replaces" the first — its old message's keyboard is stripped.
entrypoints.md's pending state is keyed **per chat**
(`bot._new_wizard_pending: dict[int, dict]`), not per message. The
sentinel callback contract (`_:nw:confirm`, etc.) never embeds a
message id either — every callback in this flow is a fixed short
enum/sentinel (entrypoints.md's callback-data contract table).

That combination means a tap on the FIRST wizard's now-superseded
summary keyboard (stripped client-side, but a tap already in flight —
or a client that hasn't rendered the strip yet — can still deliver it)
resolves against whatever the CURRENT per-chat pending state is: the
SECOND wizard's, which may never have had a mode chosen yet
(`skip_perms is None`). This is exactly the "stale keyboard from a
superseded flow" case the Confirm guard exists for — a session must
never be created with a silently-defaulted (bool(None) == Ask) mode
the caller never chose, and the caller must land back on a live,
resumable mode step rather than a dead end.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


CHAT = 555  # private chat


async def _launch_ok(*a, **kw):
    return True, ""


def _start_wizard(helpers, bot, *, message_id):
    upd = helpers.make_message_update("/new", chat_id=CHAT, chat_type="private")
    upd.message.reply_text.return_value.message_id = message_id
    _run(bot._handle_new_cmd(upd, MagicMock()))
    return upd


def _send_text(helpers, bot, text):
    upd = helpers.make_message_update(text, chat_id=CHAT, chat_type="private")
    _run(bot._handle_message(upd, MagicMock()))
    return upd


def _tap(helpers, bot, callback_data, *, message_id):
    upd, q = helpers.make_callback_update(
        callback_data, chat_id=CHAT, chat_type="private", message_id=message_id,
    )
    _run(bot._handle_callback(upd, MagicMock()))
    return upd, q


def test_stale_confirm_from_a_superseded_wizard_does_not_create_a_session(
        mk_bot, helpers):
    bot = helpers.make_personal_bot(mk_bot)

    # First wizard: name -> mode(Ask) -> summary. A live Confirm button
    # (`_:nw:confirm`) now sits on message 9001.
    _start_wizard(helpers, bot, message_id=9001)
    _send_text(helpers, bot, "firstwizname")
    _tap(helpers, bot, "_:nw:mode:ask", message_id=9001)
    assert bot.registry.find_by_label("firstwizname", CHAT) is None  # sanity

    # Second `/new` supersedes it — per-chat pending resets to the name
    # step; skip_perms is None again (mode never chosen for THIS flow).
    _start_wizard(helpers, bot, message_id=9002)
    _send_text(helpers, bot, "secondwizname")  # advances 2nd wizard to `mode`

    # A tap on the FIRST (now-stale) message's Confirm button arrives —
    # same callback_data, resolved against the CURRENT (2nd) pending.
    with patch("aipager.dtach.inject.launch_session",
               side_effect=_launch_ok) as launch:
        _tap(helpers, bot, "_:nw:confirm", message_id=9001)

    launch.assert_not_awaited()
    assert bot.registry.find_by_label("firstwizname", CHAT) is None
    assert bot.registry.find_by_label("secondwizname", CHAT) is None, (
        "a stale Confirm tap must not create a session with a mode the "
        "caller never chose for the current flow"
    )


def test_stale_confirm_from_a_superseded_wizard_reopens_the_mode_step(
        mk_bot, helpers):
    bot = helpers.make_personal_bot(mk_bot)

    _start_wizard(helpers, bot, message_id=9001)
    _send_text(helpers, bot, "firstwizname")
    _tap(helpers, bot, "_:nw:mode:ask", message_id=9001)

    _start_wizard(helpers, bot, message_id=9002)
    _send_text(helpers, bot, "secondwizname")

    with patch("aipager.dtach.inject.launch_session", side_effect=_launch_ok):
        _tap(helpers, bot, "_:nw:confirm", message_id=9001)

    # The (2nd, current) wizard must still be alive and resumable at the
    # mode step — not silently dropped, not stuck.
    _, markup, *_ = helpers.latest_edit(bot)
    cbs = helpers.callback_data_in(markup)
    assert "_:nw:mode:auto" in cbs and "_:nw:mode:ask" in cbs, (
        f"expected the (still-pending) 2nd wizard's mode step, got {cbs}")


def test_wizard_survives_a_stale_confirm_and_can_still_be_completed(
        mk_bot, helpers):
    """The guard must be a soft refusal, not a wedge: after the stale
    tap, the CURRENT wizard can still be driven to completion."""
    bot = helpers.make_personal_bot(mk_bot)

    _start_wizard(helpers, bot, message_id=9001)
    _send_text(helpers, bot, "firstwizname")
    _tap(helpers, bot, "_:nw:mode:ask", message_id=9001)

    _start_wizard(helpers, bot, message_id=9002)
    _send_text(helpers, bot, "secondwizname")

    with patch("aipager.dtach.inject.launch_session", side_effect=_launch_ok):
        _tap(helpers, bot, "_:nw:confirm", message_id=9001)  # stale, refused
        _tap(helpers, bot, "_:nw:mode:ask", message_id=9002)  # now pick a mode
        _tap(helpers, bot, "_:nw:confirm", message_id=9002)   # for real

    sess = bot.registry.find_by_label("secondwizname", CHAT)
    assert sess is not None
    assert sess.skip_perms is False
