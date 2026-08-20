"""The `/new` wizard's pending state lives per CHAT, but its
authorization is about a USER — so every step has to ask who is acting
now, not who acted at `/new` time.

Found by review-2 and reproduced end-to-end there: in a scope chat, a
`read_only` member — one who cannot pass `_authorize` to run `/new` at
all — could tap Confirm on another member's open wizard and get a
session created, in Auto mode (`--dangerously-skip-permissions`) if
that is what the original caller had picked. The text steps were worse:
they reach `launch.create_directory`, so a stranger's ordinary chat
message could create a folder on the operator's disk.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

from aipager.bot import new_flow


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


CHAT = -100
OWNER = 1          # role "owner": can_prompt AND bypass_safety
STRANGER = 2       # role "read_only": neither


def _bot(mk_bot, helpers):
    return helpers.make_scoped_bot(
        mk_bot, chat_id=CHAT, kind="group",
        members=[(OWNER, "alice", "owner"), (STRANGER, "mallory", "read_only")],
    )


async def _launch_ok(*a, **kw):
    return True, ""


def _open_wizard(helpers, bot, *, mode_cb="_:nw:mode:auto", message_id=9100):
    """OWNER runs /new, names the session, and picks a mode."""
    upd = helpers.make_message_update(
        "/new", chat_id=CHAT, chat_type="group", user_id=OWNER)
    upd.message.reply_text.return_value.message_id = message_id
    _run(bot._handle_new_cmd(upd, MagicMock()))

    name_upd = helpers.make_message_update(
        "ownedbyalice", chat_id=CHAT, chat_type="group", user_id=OWNER)
    _run(bot._handle_message(name_upd, MagicMock()))

    if mode_cb:
        _run(bot._handle_callback(*helpers.make_callback_update(
            mode_cb, chat_id=CHAT, chat_type="group",
            user_id=OWNER, message_id=message_id)))
    return message_id


def test_a_read_only_member_cannot_confirm_someone_elses_wizard(mk_bot, helpers):
    """The escalation itself: tapping Confirm on another member's
    summary must not create a session — least of all an Auto one, which
    is what `/new`'s own authorization gate exists to protect."""
    bot = _bot(mk_bot, helpers)
    message_id = _open_wizard(helpers, bot)

    # Sanity: this member cannot run /new at all.
    probe = helpers.make_message_update(
        "/new", chat_id=CHAT, chat_type="group", user_id=STRANGER)
    assert _run(bot._authorize(probe)) is False, (
        "fixture drift: STRANGER is supposed to be unable to run /new")

    with patch("aipager.dtach.inject.launch_session",
               side_effect=_launch_ok) as launch:
        _run(bot._handle_callback(*helpers.make_callback_update(
            "_:nw:confirm", chat_id=CHAT, chat_type="group",
            user_id=STRANGER, message_id=message_id)))

    launch.assert_not_awaited()
    assert bot.registry.find_by_label("ownedbyalice", CHAT) is None, (
        "a read_only member created a session by tapping Confirm on "
        "another member's wizard")


def test_a_stranger_cannot_flip_someone_elses_wizard_to_auto(mk_bot, helpers):
    """Not just Confirm: no step may be driven by a bystander."""
    bot = _bot(mk_bot, helpers)
    message_id = _open_wizard(helpers, bot, mode_cb="_:nw:mode:ask")
    pending = new_flow._pending_store(bot)[CHAT]
    assert pending["skip_perms"] is False, "fixture drift: expected Ask mode"

    _run(bot._handle_callback(*helpers.make_callback_update(
        "_:nw:mode:auto", chat_id=CHAT, chat_type="group",
        user_id=STRANGER, message_id=message_id)))

    assert new_flow._pending_store(bot)[CHAT]["skip_perms"] is False, (
        "a bystander switched another member's wizard to Auto mode")


def test_a_strangers_message_is_not_swallowed_as_wizard_input(mk_bot, helpers):
    """The name/model/new-folder steps capture free text. A bystander's
    ordinary message must reach normal routing untouched — both so it is
    not stolen from them, and so it cannot name (or create a directory
    for) somebody else's session."""
    bot = _bot(mk_bot, helpers)
    upd = helpers.make_message_update(
        "/new", chat_id=CHAT, chat_type="group", user_id=OWNER)
    upd.message.reply_text.return_value.message_id = 9101
    _run(bot._handle_new_cmd(upd, MagicMock()))
    assert new_flow._pending_store(bot)[CHAT]["step"] == "name"

    stranger_msg = helpers.make_message_update(
        "hijacked", chat_id=CHAT, chat_type="group", user_id=STRANGER)
    claimed = _run(new_flow.maybe_handle_text(
        bot, stranger_msg, MagicMock(), "hijacked"))

    assert claimed is False, (
        "the wizard consumed a bystander's message — it never reaches the "
        "session they meant it for")
    assert not new_flow._pending_store(bot)[CHAT].get("name"), (
        "a bystander named another member's session")


def test_the_caller_who_opened_the_wizard_can_still_finish_it(mk_bot, helpers):
    """The guard must refuse a stranger without also breaking the owner —
    an authorization check that fails closed for everybody is just an
    outage."""
    bot = _bot(mk_bot, helpers)
    message_id = _open_wizard(helpers, bot)

    with patch("aipager.dtach.inject.launch_session", side_effect=_launch_ok):
        _run(bot._handle_callback(*helpers.make_callback_update(
            "_:nw:confirm", chat_id=CHAT, chat_type="group",
            user_id=OWNER, message_id=message_id)))

    sess = bot.registry.find_by_label("ownedbyalice", CHAT)
    assert sess is not None, "the wizard's own caller could not finish it"
    assert sess.skip_perms is True, "the Auto mode the owner picked was lost"


# The two tests above are also caught by `_confirm`/`mode:auto`
# authorizing the acting user, because a read_only member fails a
# capability check on any path. The two below isolate the dispatcher's
# ownership guard: neither step has a capability check of its own, so
# only "this isn't your wizard" can refuse them.

def test_a_bystander_cannot_cancel_someone_elses_wizard(mk_bot, helpers):
    """Cancel takes no privilege at all — it just destroys the pending
    state. Without an ownership check, anyone in a group can wipe out
    another member's half-finished setup."""
    bot = _bot(mk_bot, helpers)
    message_id = _open_wizard(helpers, bot)

    _run(bot._handle_callback(*helpers.make_callback_update(
        "_:nw:cancel", chat_id=CHAT, chat_type="group",
        user_id=STRANGER, message_id=message_id)))

    assert CHAT in new_flow._pending_store(bot), (
        "a bystander cancelled another member's wizard")


def test_an_authorized_member_still_cannot_finish_anothers_wizard(
        mk_bot, helpers):
    """The sharper case: a member who CAN create sessions taps Confirm on
    someone else's Ask-mode wizard. Every capability check passes — they
    really are allowed to make sessions — so only the ownership guard
    stands between them and creating a session out of another member's
    half-finished choices, under a name that member picked."""
    bot = helpers.make_scoped_bot(
        mk_bot, chat_id=CHAT, kind="group",
        members=[(OWNER, "alice", "owner"), (3, "bob", "user")],
    )
    message_id = _open_wizard(helpers, bot, mode_cb="_:nw:mode:ask")

    with patch("aipager.dtach.inject.launch_session",
               side_effect=_launch_ok) as launch:
        _run(bot._handle_callback(*helpers.make_callback_update(
            "_:nw:confirm", chat_id=CHAT, chat_type="group",
            user_id=3, message_id=message_id)))

    launch.assert_not_awaited()
    assert bot.registry.find_by_label("ownedbyalice", CHAT) is None, (
        "an authorized bystander created a session from another member's "
        "wizard")
