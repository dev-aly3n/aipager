"""Priority 3 (task brief): Authorization at Confirm, not only at /new.

entrypoints.md: "/new" (no args) -- ``bot._authorize(update)`` at start;
**re-checked again at Confirm** via ``bot._can_prompt_user(user_id,
chat_id)`` (and, if Auto mode was chosen, ``bot._is_admin_user``)". A
caller demoted between choosing Auto and tapping Confirm must not get an
Auto session — and per design.md, a demotion caught at Confirm re-opens
the `mode` step rather than silently downgrading to Ask.

The demotion itself is modelled by mutating `bot.scopes` (a fresh
`Scope`/`Member` tuple with the same chat_id but a different role) —
the same mechanism a real config reload would produce — rather than by
patching `_is_admin_user`/`_can_prompt_user` string-replacement style,
so the test exercises the REAL scope-resolution code path
(`_member_in_scope`, `policy.get_role(...).bypass_safety`), not a stand-in
for it.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


CHAT = -100
USER = 2


def _scoped_bot_with_role(mk_bot, helpers, role):
    """``role`` is a BUILTIN policy role name (``aipager.safety.
    BUILTIN_ROLE_DEFAULTS``). Note "admin" is NOT the admin here:
    ``_is_admin_user`` gates on ``bypass_safety``, and only ``owner``
    carries it by default — a demotion test built on "admin" would
    never have had Auto to lose, and would pass for the wrong reason.
    """
    return helpers.make_scoped_bot(
        mk_bot, chat_id=CHAT, kind="group",
        members=[(USER, "bob", role)],
    )


def _advance_to_summary(helpers, bot, *, mode_cb, message_id=9001):
    upd = helpers.make_message_update(
        "/new", chat_id=CHAT, chat_type="group", user_id=USER)
    upd.message.reply_text.return_value.message_id = message_id
    _run(bot._handle_new_cmd(upd, MagicMock()))

    name_upd = helpers.make_message_update(
        "authwiz1", chat_id=CHAT, chat_type="group", user_id=USER)
    _run(bot._handle_message(name_upd, MagicMock()))

    cb_upd, q = helpers.make_callback_update(
        mode_cb, chat_id=CHAT, chat_type="group", user_id=USER,
        message_id=message_id,
    )
    _run(bot._handle_callback(cb_upd, MagicMock()))
    return message_id


async def _launch_ok(*a, **kw):
    return True, ""


def test_admin_demoted_before_confirm_does_not_get_auto_session(mk_bot, helpers):
    """The core security property: a demotion between choosing Auto and
    tapping Confirm must never result in a session with
    ``skip_perms=True`` for the now-non-admin caller — no privilege
    escalation, regardless of exactly how the wizard reacts to the
    demotion (see the next test for that UX-contract question)."""
    bot = _scoped_bot_with_role(mk_bot, helpers, "owner")
    message_id = _advance_to_summary(
        helpers, bot, mode_cb="_:nw:mode:auto")

    # Demote: replace the scope with the same member now on a
    # non-admin role, exactly as a live config reload would.
    bot.scopes = helpers.make_scopes(
        chat_id=CHAT, kind="group", members=[(USER, "bob", "user")])

    with patch("aipager.dtach.inject.launch_session", side_effect=_launch_ok):
        _run(bot._handle_callback(*helpers.make_callback_update(
            "_:nw:confirm", chat_id=CHAT, chat_type="group",
            user_id=USER, message_id=message_id,
        )))

    sess = bot.registry.find_by_label("authwiz1", CHAT)
    assert sess is None or sess.skip_perms is False, (
        "a caller demoted between choosing Auto and Confirm got an Auto "
        f"(skip_perms=True) session: {sess}"
    )


def test_admin_demoted_before_confirm_reopens_mode_step(mk_bot, helpers):
    """design.md: "a demotion between step 2 and Confirm re-opens the
    `mode` step rather than silently downgrading to Ask" — entrypoints.md
    documents the SAME contract explicitly. This is a UX/contract
    assertion distinct from the security property in the test above
    (which holds regardless): per the documented contract, Confirm must
    refuse and return to the mode step, not quietly create an Ask-mode
    session behind the caller's back."""
    bot = _scoped_bot_with_role(mk_bot, helpers, "owner")
    message_id = _advance_to_summary(
        helpers, bot, mode_cb="_:nw:mode:auto")

    bot.scopes = helpers.make_scopes(
        chat_id=CHAT, kind="group", members=[(USER, "bob", "user")])

    with patch("aipager.dtach.inject.launch_session", side_effect=_launch_ok):
        _run(bot._handle_callback(*helpers.make_callback_update(
            "_:nw:confirm", chat_id=CHAT, chat_type="group",
            user_id=USER, message_id=message_id,
        )))

    assert bot.registry.find_by_label("authwiz1", CHAT) is None, (
        "entrypoints.md: a demotion caught at Confirm must re-open the "
        "mode step, not silently create a (downgraded-to-Ask) session "
        "anyway"
    )
    _, markup, *_ = helpers.latest_edit(bot)
    cbs = helpers.callback_data_in(markup)
    assert "_:nw:mode:auto" in cbs or "_:nw:mode:ask" in cbs, (
        "expected Confirm to re-open the mode step after a demotion, "
        f"got callback_data={cbs}"
    )


def test_member_removed_from_scope_entirely_before_confirm_is_refused(mk_bot, helpers):
    """A caller who can_prompt at the start but is removed from the
    scope entirely before Confirm must be refused, not silently
    create the session under their old identity."""
    bot = _scoped_bot_with_role(mk_bot, helpers, "user")
    message_id = _advance_to_summary(
        helpers, bot, mode_cb="_:nw:mode:ask")

    # Remove the member from the scope outright.
    bot.scopes = helpers.make_scopes(chat_id=CHAT, kind="group", members=[])

    with patch("aipager.dtach.inject.launch_session",
               side_effect=_launch_ok) as launch:
        _run(bot._handle_callback(*helpers.make_callback_update(
            "_:nw:confirm", chat_id=CHAT, chat_type="group",
            user_id=USER, message_id=message_id,
        )))

    launch.assert_not_awaited()
    assert bot.registry.find_by_label("authwiz1", CHAT) is None


def test_non_admin_confirming_ask_mode_still_succeeds(mk_bot, helpers):
    """Control case: Ask mode never required admin, so a "user"-role
    member (can_prompt=True, not admin) confirming Ask mode must still
    succeed — proving the refusal above is specifically about the
    Auto/admin re-check, not a blanket regression that blocks Confirm
    for every non-admin caller."""
    bot = _scoped_bot_with_role(mk_bot, helpers, "user")
    message_id = _advance_to_summary(
        helpers, bot, mode_cb="_:nw:mode:ask")

    with patch("aipager.dtach.inject.launch_session", side_effect=_launch_ok):
        _run(bot._handle_callback(*helpers.make_callback_update(
            "_:nw:confirm", chat_id=CHAT, chat_type="group",
            user_id=USER, message_id=message_id,
        )))

    assert bot.registry.find_by_label("authwiz1", CHAT) is not None


def test_confirm_without_a_chosen_mode_reopens_the_mode_step(mk_bot, helpers):
    """A non-admin's Auto tap is refused with an alert and the wizard
    stays on the mode step — but the Confirm callback is still live on
    any summary keyboard the chat is carrying (a superseded `/new`, a
    scrolled-back message). ``skip_perms`` is ``None`` until a mode is
    picked, and ``bool(None)`` is ``False``, so an unguarded Confirm
    would create an Ask session the caller never chose — the same
    silent-downgrade the demotion path is written to prevent, reached
    without any demotion at all.
    """
    bot = _scoped_bot_with_role(mk_bot, helpers, "user")
    # Auto is refused (role "user" has no bypass_safety): the wizard is
    # still on `mode`, and pending["skip_perms"] is still None.
    message_id = _advance_to_summary(helpers, bot, mode_cb="_:nw:mode:auto")

    with patch("aipager.dtach.inject.launch_session",
               side_effect=_launch_ok) as launch:
        _run(bot._handle_callback(*helpers.make_callback_update(
            "_:nw:confirm", chat_id=CHAT, chat_type="group",
            user_id=USER, message_id=message_id,
        )))

    assert bot.registry.find_by_label("authwiz1", CHAT) is None, (
        "Confirm created a session before any mode was chosen")
    launch.assert_not_awaited()
    _, markup, *_ = helpers.latest_edit(bot)
    cbs = helpers.callback_data_in(markup)
    assert "_:nw:mode:auto" in cbs and "_:nw:mode:ask" in cbs, (
        f"expected the mode step to re-open, got callback_data={cbs}")
