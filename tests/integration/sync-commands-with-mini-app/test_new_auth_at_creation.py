"""Priority 3 (task brief): authorization at the moment a session is
created, not only when `/new` is sent.

Since the 2026-09-30 redesign the Name card asks one thing (the name) and
the name creates the session. So the checks run when the name arrives:
the message itself must be authorized (`_handle_message` →
`_authorize`), and Auto is re-checked against the person creating
(`_is_admin_user`). A caller who lost Auto since the card opened gets an
Ask session and the Ready card says so; nobody gets Auto they may not use.

The demotion is modelled by replacing `bot.scopes` (the same member, a
different role), the mechanism a real config reload produces, so the real
scope-resolution path runs.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


CHAT = -100
USER = 2


async def _launch_ok(*a, **kw):
    return True, ""


@pytest.fixture(autouse=True)
def _no_real_launch():
    with patch("aipager.dtach.inject.launch_session", side_effect=_launch_ok):
        yield


def _scoped_bot_with_role(mk_bot, helpers, role):
    """``role`` is a BUILTIN policy role name. "admin" is NOT the admin
    here: ``_is_admin_user`` gates on ``bypass_safety``, and only
    ``owner`` carries it by default."""
    return helpers.make_scoped_bot(
        mk_bot, chat_id=CHAT, kind="group",
        members=[(USER, "bob", role)],
    )


def _open_card(helpers, bot, message_id=9001):
    upd = helpers.make_message_update(
        "/new", chat_id=CHAT, chat_type="group", user_id=USER)
    upd.message.reply_text.return_value.message_id = message_id
    _run(bot._handle_new_cmd(upd, MagicMock()))
    return upd


def _send_name(helpers, bot, name="authwiz1"):
    upd = helpers.make_message_update(
        name, chat_id=CHAT, chat_type="group", user_id=USER)
    _run(bot._handle_message(upd, MagicMock()))
    return upd


def test_an_owner_gets_auto_by_default(mk_bot, helpers):
    bot = _scoped_bot_with_role(mk_bot, helpers, "owner")
    _open_card(helpers, bot)
    _send_name(helpers, bot)
    sess = bot.registry.find_by_label("authwiz1", CHAT)
    assert sess is not None and sess.skip_perms is True


def test_an_owner_demoted_before_naming_does_not_get_auto(mk_bot, helpers):
    """The core security property: the card was opened with Auto, the
    caller lost Auto before sending the name. No Auto session."""
    bot = _scoped_bot_with_role(mk_bot, helpers, "owner")
    _open_card(helpers, bot)
    bot.scopes = helpers.make_scopes(
        chat_id=CHAT, kind="group", members=[(USER, "bob", "user")])

    _send_name(helpers, bot)

    sess = bot.registry.find_by_label("authwiz1", CHAT)
    assert sess is not None and sess.skip_perms is False
    text, *_ = helpers.latest_edit(bot)
    assert "Auto mode needs an admin" in text, text


def test_a_member_removed_before_naming_creates_nothing(mk_bot, helpers):
    bot = _scoped_bot_with_role(mk_bot, helpers, "user")
    _open_card(helpers, bot)
    bot.scopes = helpers.make_scopes(chat_id=CHAT, kind="group", members=[])

    _send_name(helpers, bot)

    assert bot.registry.find_by_label("authwiz1", CHAT) is None


def test_a_non_admin_naming_the_session_gets_ask(mk_bot, helpers):
    """Control: Ask never needed an admin, so a "user" member creates a
    session, in Ask, with no complaint on the card."""
    bot = _scoped_bot_with_role(mk_bot, helpers, "user")
    _open_card(helpers, bot)
    _send_name(helpers, bot)

    sess = bot.registry.find_by_label("authwiz1", CHAT)
    assert sess is not None and sess.skip_perms is False
    text, *_ = helpers.latest_edit(bot)
    assert "Auto mode needs an admin" not in text


def test_a_non_admins_auto_tap_is_refused_and_the_name_still_asks(mk_bot, helpers):
    bot = _scoped_bot_with_role(mk_bot, helpers, "user")
    _open_card(helpers, bot)
    upd, q = helpers.make_callback_update(
        "_:nw:mode:auto", chat_id=CHAT, chat_type="group", user_id=USER,
        message_id=9001)
    _run(bot._handle_callback(upd, MagicMock()))

    _send_name(helpers, bot)

    sess = bot.registry.find_by_label("authwiz1", CHAT)
    assert sess is not None and sess.skip_perms is False
