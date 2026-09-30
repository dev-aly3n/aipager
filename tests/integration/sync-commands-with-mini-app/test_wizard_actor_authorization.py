"""The `/new` Name card's pending state lives per CHAT, but its
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

import pytest

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


@pytest.fixture(autouse=True)
def _no_real_launch():
    with patch("aipager.dtach.inject.launch_session", side_effect=_launch_ok):
        yield


def _open_wizard(helpers, bot, *, mode_cb=None, message_id=9100):
    """OWNER runs /new (the Name card, Auto by default for an owner) and
    optionally taps a mode. Nobody has named the session yet: since the
    2026-09-30 redesign the name itself creates it."""
    upd = helpers.make_message_update(
        "/new", chat_id=CHAT, chat_type="group", user_id=OWNER)
    upd.message.reply_text.return_value.message_id = message_id
    _run(bot._handle_new_cmd(upd, MagicMock()))
    if mode_cb:
        _run(bot._handle_callback(*helpers.make_callback_update(
            mode_cb, chat_id=CHAT, chat_type="group",
            user_id=OWNER, message_id=message_id)))
    return message_id


def _name_by(helpers, bot, user_id, name="ownedbyalice"):
    upd = helpers.make_message_update(
        name, chat_id=CHAT, chat_type="group", user_id=user_id)
    _run(bot._handle_message(upd, MagicMock()))
    return upd


def test_a_read_only_members_message_cannot_name_someone_elses_session(
        mk_bot, helpers):
    """The escalation itself: a read_only member's text must not create a
    session from another member's open card, least of all an Auto one."""
    bot = _bot(mk_bot, helpers)
    _open_wizard(helpers, bot)

    probe = helpers.make_message_update(
        "/new", chat_id=CHAT, chat_type="group", user_id=STRANGER)
    assert _run(bot._authorize(probe)) is False, (
        "fixture drift: STRANGER is supposed to be unable to run /new")

    _name_by(helpers, bot, STRANGER)

    assert bot.registry.find_by_label("ownedbyalice", CHAT) is None
    assert CHAT in new_flow._pending_store(bot), "the owner's card was lost"


def test_a_stranger_cannot_flip_someone_elses_wizard_to_auto(mk_bot, helpers):
    """No step may be driven by a bystander."""
    bot = _bot(mk_bot, helpers)
    message_id = _open_wizard(helpers, bot, mode_cb="_:nw:mode:ask")
    assert new_flow._pending_store(bot)[CHAT]["skip_perms"] is False, (
        "fixture drift: expected Ask mode")

    _run(bot._handle_callback(*helpers.make_callback_update(
        "_:nw:mode:auto", chat_id=CHAT, chat_type="group",
        user_id=STRANGER, message_id=message_id)))

    assert new_flow._pending_store(bot)[CHAT]["skip_perms"] is False, (
        "a bystander switched another member's card to Auto mode")


def test_a_strangers_message_is_not_swallowed_as_wizard_input(mk_bot, helpers):
    """The card takes free text. A bystander's ordinary message must
    reach normal routing untouched: it is not stolen from them, and it
    cannot name (or create a folder for) somebody else's session."""
    bot = _bot(mk_bot, helpers)
    _open_wizard(helpers, bot)
    assert new_flow._pending_store(bot)[CHAT]["step"] == "name"

    stranger_msg = helpers.make_message_update(
        "hijacked", chat_id=CHAT, chat_type="group", user_id=STRANGER)
    claimed = _run(new_flow.maybe_handle_text(
        bot, stranger_msg, MagicMock(), "hijacked"))

    assert claimed is False
    assert bot.registry.find_by_label("hijacked", CHAT) is None


def test_the_caller_who_opened_the_wizard_can_still_finish_it(mk_bot, helpers):
    """The guard must refuse a stranger without also breaking the owner."""
    bot = _bot(mk_bot, helpers)
    _open_wizard(helpers, bot)

    _name_by(helpers, bot, OWNER)

    sess = bot.registry.find_by_label("ownedbyalice", CHAT)
    assert sess is not None, "the card's own caller could not finish it"
    assert sess.skip_perms is True, "the owner's default Auto was lost"


# The rows below isolate the ownership guard: neither step has a
# capability check of its own, so only "this isn't your card" can refuse
# them.

def test_a_bystander_cannot_cancel_someone_elses_wizard(mk_bot, helpers):
    """Cancel takes no privilege at all; without an ownership check,
    anyone in a group could wipe out another member's open card."""
    bot = _bot(mk_bot, helpers)
    message_id = _open_wizard(helpers, bot)

    _run(bot._handle_callback(*helpers.make_callback_update(
        "_:nw:cancel", chat_id=CHAT, chat_type="group",
        user_id=STRANGER, message_id=message_id)))

    assert CHAT in new_flow._pending_store(bot), (
        "a bystander cancelled another member's card")


def test_an_authorized_member_still_cannot_finish_anothers_wizard(
        mk_bot, helpers):
    """The sharper case: a member who CAN create sessions sends a name
    while someone else's card is open. Their message is theirs: it does
    not complete the other member's card (with its choices, under their
    name)."""
    bot = helpers.make_scoped_bot(
        mk_bot, chat_id=CHAT, kind="group",
        members=[(OWNER, "alice", "owner"), (3, "bob", "user")],
    )
    _open_wizard(helpers, bot, mode_cb="_:nw:mode:ask")

    claimed = _run(new_flow.maybe_handle_text(
        bot, helpers.make_message_update(
            "ownedbyalice", chat_id=CHAT, chat_type="group", user_id=3),
        MagicMock(), "ownedbyalice"))

    assert claimed is False
    assert bot.registry.find_by_label("ownedbyalice", CHAT) is None
    assert CHAT in new_flow._pending_store(bot)
