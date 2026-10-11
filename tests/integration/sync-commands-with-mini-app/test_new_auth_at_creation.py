"""Priority 3 (task brief): authorization at the moment a session is
created, not only when `/new` is sent.

Since the 2026-09-30 redesign the Name card asks one thing (the name) and
the name creates the session. So the checks run when the name arrives:
the message itself must be authorized (`_handle_message` →
`_authorize`), and Full access is re-checked against the person creating
(`_is_admin_user`). A caller who lost it since the card opened gets an
Auto session and the Ready card says so; nobody gets Full access they may
not use (2026-10-11; before, the same held for the old bypassing Auto).

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
    assert sess is not None and sess.perm_mode == "auto"


def _pick_full(helpers, bot):
    upd, _q = helpers.make_callback_update(
        "_:nw:mode:full", chat_id=CHAT, chat_type="group", user_id=USER,
        message_id=9001)
    _run(bot._handle_callback(upd, MagicMock()))


def test_an_owner_demoted_before_naming_does_not_get_full_access(mk_bot, helpers):
    """The core security property: the card chose Full access, the caller
    lost the right to it before sending the name. No Full access session:
    Auto, and the card says why."""
    bot = _scoped_bot_with_role(mk_bot, helpers, "owner")
    _open_card(helpers, bot)
    _pick_full(helpers, bot)
    # The owner's card starts in the daemon's folder: bob's root folder
    # here, or root folders would refuse the start for that reason.
    from aipager.dtach import inject
    bot.scopes = helpers.make_scopes(
        chat_id=CHAT, kind="group", members=[(USER, "bob", "user")],
        root=inject._PROJECT_DIR)

    _send_name(helpers, bot)

    sess = bot.registry.find_by_label("authwiz1", CHAT)
    assert sess is not None and sess.perm_mode == "auto"
    text, *_ = helpers.latest_edit(bot)
    assert "Full access mode needs an admin" in text, text


def test_an_owner_who_picked_full_access_gets_it(mk_bot, helpers):
    bot = _scoped_bot_with_role(mk_bot, helpers, "owner")
    _open_card(helpers, bot)
    _pick_full(helpers, bot)
    _send_name(helpers, bot)
    assert bot.registry.find_by_label("authwiz1", CHAT).perm_mode == "full"


def test_a_member_removed_before_naming_creates_nothing(mk_bot, helpers):
    bot = _scoped_bot_with_role(mk_bot, helpers, "user")
    _open_card(helpers, bot)
    bot.scopes = helpers.make_scopes(chat_id=CHAT, kind="group", members=[])

    _send_name(helpers, bot)

    assert bot.registry.find_by_label("authwiz1", CHAT) is None


def test_a_non_admin_naming_the_session_gets_auto(mk_bot, helpers):
    """Control: Auto needs no admin, so a "user" member creates a session,
    in Auto, with no complaint on the card."""
    bot = _scoped_bot_with_role(mk_bot, helpers, "user")
    _open_card(helpers, bot)
    _send_name(helpers, bot)

    sess = bot.registry.find_by_label("authwiz1", CHAT)
    assert sess is not None and sess.perm_mode == "auto"
    text, *_ = helpers.latest_edit(bot)
    assert "needs an admin" not in text


def test_a_non_admins_full_access_tap_is_refused_and_the_name_gets_auto(mk_bot, helpers):
    bot = _scoped_bot_with_role(mk_bot, helpers, "user")
    _open_card(helpers, bot)
    _pick_full(helpers, bot)

    _send_name(helpers, bot)

    sess = bot.registry.find_by_label("authwiz1", CHAT)
    assert sess is not None and sess.perm_mode == "auto"
