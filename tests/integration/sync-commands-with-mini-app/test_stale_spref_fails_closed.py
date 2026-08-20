"""Priority 8 (task brief): a stale `_:spref:<idx>` must fail closed,
never write to a different session than the user was looking at.

entrypoints.md: "``idx`` is an index into ``bot._session_pref_index[chat_id]``
(a list of internal session **names**), never the name itself ... a
stale index after a session is killed/renamed mid-view fails closed
('This session is no longer available — reopen /settings.') rather
than silently writing to the wrong session."
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


CHAT = 0
FAIL_CLOSED_TEXT = "no longer available"


def _two_session_bot(mk_bot, helpers):
    from aipager.state import TrackedSession, Status
    bot = helpers.make_personal_bot(mk_bot)
    sess_a = TrackedSession(name="claude-alpha", label="alpha",
                             status=Status.IDLE, scope_chat_id=CHAT)
    sess_b = TrackedSession(name="claude-beta", label="beta",
                             status=Status.IDLE, scope_chat_id=CHAT)
    bot.registry._sessions[sess_a.name] = sess_a
    bot.registry._sessions[sess_b.name] = sess_b
    return bot, sess_a, sess_b


def _render_picker(helpers, bot, *, message_id=1):
    """Populates bot._session_pref_index[CHAT] = ["claude-alpha",
    "claude-beta"] (registration order), exactly as a real /settings
    tap would."""
    cb, q = helpers.make_callback_update(
        "_:spref", chat_id=CHAT, chat_type="private", message_id=message_id)
    _run(bot._handle_callback(cb, MagicMock()))
    return q


def test_tap_on_a_session_removed_since_the_picker_was_rendered_fails_closed(
        mk_bot, helpers):
    bot, sess_a, sess_b = _two_session_bot(mk_bot, helpers)
    _render_picker(helpers, bot)

    del bot.registry._sessions[sess_a.name]  # simulate kill/delete mid-view

    cb, q = helpers.make_callback_update(
        "_:spref:0", chat_id=CHAT, chat_type="private", message_id=1)
    _run(bot._handle_callback(cb, MagicMock()))

    toasts = [c.args[0] for c in q.answer.await_args_list if c.args]
    assert any(FAIL_CLOSED_TEXT in (t or "") for t in toasts), toasts


def test_tap_on_a_removed_session_never_edits_the_message(mk_bot, helpers):
    """Fail closed means no render happens at all for the stale
    session — not a render of some fallback session."""
    bot, sess_a, sess_b = _two_session_bot(mk_bot, helpers)
    _render_picker(helpers, bot)
    del bot.registry._sessions[sess_a.name]

    cb, q = helpers.make_callback_update(
        "_:spref:0", chat_id=CHAT, chat_type="private", message_id=1)
    _run(bot._handle_callback(cb, MagicMock()))

    q.edit_message_text.assert_not_awaited()


def test_write_via_a_stale_index_never_touches_the_surviving_session(
        mk_bot, helpers):
    """The sharpest version of "never write to a different session":
    attempt an actual WRITE (`_:spref:0:layout:merged`) through the now-
    stale idx 0 and confirm session B's fields are completely
    untouched."""
    bot, sess_a, sess_b = _two_session_bot(mk_bot, helpers)
    _render_picker(helpers, bot)
    del bot.registry._sessions[sess_a.name]

    cb, q = helpers.make_callback_update(
        "_:spref:0:layout:merged", chat_id=CHAT, chat_type="private",
        message_id=1,
    )
    _run(bot._handle_callback(cb, MagicMock()))

    assert sess_b.override_layout is None, (
        "a stale idx=0 write landed on session B instead of failing closed"
    )


def test_out_of_range_index_fails_closed_without_crashing(mk_bot, helpers):
    bot, sess_a, sess_b = _two_session_bot(mk_bot, helpers)
    _render_picker(helpers, bot)  # index has exactly 2 entries: 0, 1

    cb, q = helpers.make_callback_update(
        "_:spref:99", chat_id=CHAT, chat_type="private", message_id=1)
    _run(bot._handle_callback(cb, MagicMock()))  # must not raise

    toasts = [c.args[0] for c in q.answer.await_args_list if c.args]
    assert any(FAIL_CLOSED_TEXT in (t or "") for t in toasts), toasts


def test_negative_index_fails_closed_without_crashing(mk_bot, helpers):
    """Error guessing: `-1` is a syntactically valid integer and a
    dangerous one for naive Python list indexing (`lst[-1]` is the LAST
    element, not an error) — must not silently resolve to some other
    session."""
    bot, sess_a, sess_b = _two_session_bot(mk_bot, helpers)
    _render_picker(helpers, bot)

    cb, q = helpers.make_callback_update(
        "_:spref:-1", chat_id=CHAT, chat_type="private", message_id=1)
    _run(bot._handle_callback(cb, MagicMock()))  # must not raise
    assert sess_b.override_layout is None
    assert sess_a.override_layout is None


def test_non_numeric_index_fails_closed_without_crashing(mk_bot, helpers):
    bot, sess_a, sess_b = _two_session_bot(mk_bot, helpers)
    _render_picker(helpers, bot)

    cb, q = helpers.make_callback_update(
        "_:spref:abc", chat_id=CHAT, chat_type="private", message_id=1)
    _run(bot._handle_callback(cb, MagicMock()))  # must not raise


def test_tap_with_no_index_ever_rendered_in_this_chat_fails_closed(mk_bot, helpers):
    """No `_render_picker` call at all — `bot._session_pref_index`
    has never been populated for this chat."""
    bot, sess_a, sess_b = _two_session_bot(mk_bot, helpers)

    cb, q = helpers.make_callback_update(
        "_:spref:0", chat_id=CHAT, chat_type="private", message_id=1)
    _run(bot._handle_callback(cb, MagicMock()))  # must not raise
    assert sess_a.override_layout is None
    assert sess_b.override_layout is None


def test_valid_index_for_the_surviving_session_still_works(mk_bot, helpers):
    """Control case: idx=1 (session B, never removed) must still work
    normally after idx=0's session was removed — the fail-closed
    behavior is scoped to the STALE entry, not a blanket breakage of
    the whole index."""
    bot, sess_a, sess_b = _two_session_bot(mk_bot, helpers)
    _render_picker(helpers, bot)
    del bot.registry._sessions[sess_a.name]

    cb, q = helpers.make_callback_update(
        "_:spref:1:layout:merged", chat_id=CHAT, chat_type="private",
        message_id=1,
    )
    _run(bot._handle_callback(cb, MagicMock()))

    assert sess_b.override_layout == "merged"


def test_stale_index_from_a_different_chat_does_not_leak_across_chats(
        mk_bot, helpers):
    """`bot._session_pref_index` is keyed per chat_id — an idx that is
    valid in chat A's index must not resolve against chat B's sessions
    just because both chats happen to use small integers."""
    from aipager.state import TrackedSession, Status
    bot = helpers.make_personal_bot(mk_bot)
    sess_other_chat = TrackedSession(
        name="claude-onlyinotherchat", label="onlyinotherchat",
        status=Status.IDLE, scope_chat_id=999,
    )
    bot.registry._sessions[sess_other_chat.name] = sess_other_chat
    # CHAT (0) has no sessions and its index was never rendered.

    cb, q = helpers.make_callback_update(
        "_:spref:0:layout:merged", chat_id=CHAT, chat_type="private",
        message_id=1,
    )
    _run(bot._handle_callback(cb, MagicMock()))

    assert sess_other_chat.override_layout is None
