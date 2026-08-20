"""Priority 5 (task brief): callback_data <= 64 bytes for every button
any surface can emit, using the longest realistic session label.

Telegram's Bot API hard-caps `callback_data` at 64 bytes; a button that
exceeds it is rejected by Telegram's servers when the message is sent
(python-telegram-bot does not validate this client-side — confirmed
empirically: constructing an over-long InlineKeyboardButton raises
nothing locally). A session name that trips this is entirely
achievable in production, not a contrived corner case:

- `inject.launch_session`'s own cap allows an internal session `name`
  (registry key, WITHOUT the "claude-" prefix) up to 64 chars, and its
  own docstring says that cap is deliberately generous because "the
  internal name may carry a scope disambiguator suffix" — so the
  registry key `sess.name` (WITH the "claude-" prefix) can legitimately
  reach 64 + len("claude-") = 71 characters for a session created in a
  real (especially supergroup-sized) chat.
- Every session-scoped callback in this feature's entrypoints.md
  contract (`{name}:menu`, `{name}:restart`, `{name}:restart-confirm`,
  `{name}:rename-cancel`, `{name}:delete-confirm`, `{name}:diff`, ...)
  embeds that FULL `sess.name` verbatim, with no indirection — unlike
  the wizard's own new callbacks, which deliberately use short
  fixed-enum/index tokens for exactly this reason (entrypoints.md: "no
  indirection needed here because nothing user-supplied ... is ever
  placed in callback_data").

This suite constructs the longest session name `launch_session`'s own
64-char cap actually permits (so the scenario is "could really be
created", not merely "a long string"), scoped to a realistic large
supergroup chat_id, and checks every button any of this feature's
real, documented surfaces emits for that session.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from aipager.scope import disambiguated_name
from aipager.state import Status, TrackedSession


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


TELEGRAM_CALLBACK_DATA_LIMIT = 64

# A realistic large supergroup chat_id (Telegram's own id space for
# supergroups: -100 followed by ~10-13 digits).
BIG_GROUP_CHAT_ID = -1001234567890
CHAT = BIG_GROUP_CHAT_ID
USER = 2


def _max_creatable_label(chat_id, kind="group"):
    """The longest label for which `disambiguated_name(...)` (minus its
    "claude-" prefix) still sits AT OR UNDER `inject.launch_session`'s
    own 64-char cap — i.e. the longest label a real `/new` in this
    scope could actually succeed in creating."""
    from aipager.scope import scope_suffix
    suffix_len = len(scope_suffix(chat_id, kind))
    budget = 64 - 2 - suffix_len  # "__" + suffix
    assert budget > 0
    return "a" * budget


def _worst_case_session(chat_id=CHAT, kind="group"):
    label = _max_creatable_label(chat_id, kind)
    name = disambiguated_name(label, chat_id, kind)
    short_name = name.removeprefix("claude-")
    assert len(short_name) <= 64, (
        f"test setup bug: constructed short_name is {len(short_name)} "
        f"chars, over launch_session's own 64-char cap"
    )
    return TrackedSession(name=name, label=label, status=Status.IDLE,
                           scope_chat_id=chat_id)


def _oversize_report(cbs):
    over = [(cb, len(cb.encode("utf-8"))) for cb in cbs
            if len(cb.encode("utf-8")) > TELEGRAM_CALLBACK_DATA_LIMIT]
    return over


def _assert_all_within_budget(cbs, *, surface):
    over = _oversize_report(cbs)
    assert not over, (
        f"{surface}: {len(over)} of {len(cbs)} callback_data value(s) "
        f"exceed Telegram's {TELEGRAM_CALLBACK_DATA_LIMIT}-byte cap for "
        f"a realistic worst-case session name:\n" +
        "\n".join(f"  {n} bytes: {cb!r}" for cb, n in over)
    )


# --------------------------------------------------------------------------- #
# Sanity: the byte-counting methodology itself, on a SHORT label that DOES  #
# fit — proves this suite can pass, not just always fail.                   #
# --------------------------------------------------------------------------- #

def test_short_label_status_menu_button_fits_the_budget(mk_bot, helpers):
    bot = helpers.make_scoped_bot(
        mk_bot, chat_id=CHAT, members=[(USER, "bob", "user")])
    sess = TrackedSession(name="claude-jim__g1001234567890", label="jim",
                           status=Status.IDLE, scope_chat_id=CHAT)
    bot.registry._sessions[sess.name] = sess

    upd = helpers.make_message_update(
        "/status", chat_id=CHAT, chat_type="group", user_id=USER)
    _run(bot._handle_status(upd, MagicMock()))
    kb = upd.message.reply_text.await_args.kwargs.get("reply_markup")
    cbs = helpers.callback_data_in(kb)
    _assert_all_within_budget(cbs, surface="/status (short label)")


# --------------------------------------------------------------------------- #
# The realistic worst case.                                                 #
# --------------------------------------------------------------------------- #

def test_worst_case_label_is_actually_creatable(mk_bot, helpers):
    """Guards the scenario's own realism: `_max_creatable_label` must
    produce a `short_name` at or under `inject.launch_session`'s own
    64-char cap, using the REAL `_VALID_NAME`/length check — not a
    hand-picked number that happens to be convenient for this test."""
    from aipager.dtach import inject as _inject
    sess = _worst_case_session()
    short_name = sess.name.removeprefix("claude-")
    assert _inject._VALID_NAME.match(short_name)
    assert len(short_name) <= 64


def test_status_menu_button_exceeds_the_budget_for_worst_case_session(mk_bot, helpers):
    bot = helpers.make_scoped_bot(
        mk_bot, chat_id=CHAT, members=[(USER, "bob", "user")])
    sess = _worst_case_session()
    bot.registry._sessions[sess.name] = sess

    upd = helpers.make_message_update(
        "/status", chat_id=CHAT, chat_type="group", user_id=USER)
    _run(bot._handle_status(upd, MagicMock()))
    kb = upd.message.reply_text.await_args.kwargs.get("reply_markup")
    cbs = helpers.callback_data_in(kb)
    _assert_all_within_budget(cbs, surface="/status ⋮ menu button")


def test_menu_tap_rows_exceed_the_budget_for_worst_case_session(mk_bot, helpers):
    bot = helpers.make_scoped_bot(
        mk_bot, chat_id=CHAT, members=[(USER, "bob", "user")])
    sess = _worst_case_session()
    bot.registry._sessions[sess.name] = sess

    # The ⋮ menu tap itself, `{name}:menu`, would already have been
    # rejected by Telegram (see the /status test above) — but if it
    # somehow arrived, check what it would render too, since this is
    # the surface entrypoints.md says re-uses the SAME callback set
    # (`{name}:restart`, `{name}:rename`, `{name}:diff`, ...).
    cb_upd, q = helpers.make_callback_update(
        f"{sess.name}:menu", chat_id=CHAT, chat_type="group",
        user_id=USER, message_id=1,
    )
    _run(bot._handle_callback(cb_upd, MagicMock()))
    # The ⋮ menu edits the SAME message the tap came from (a single-turn
    # callback -> edit, unlike the wizard's cross-turn
    # bot._app.bot.edit_message_text convention), so it renders via
    # `query.edit_message_text` on this callback's own query object.
    q.edit_message_text.assert_awaited_once()
    markup = q.edit_message_text.await_args.kwargs.get("reply_markup")
    cbs = helpers.callback_data_in(markup)
    assert cbs, "expected the ⋮ menu to render at least one row"
    _assert_all_within_budget(cbs, surface="⋮ menu rows")


def test_restart_confirm_buttons_exceed_the_budget_for_worst_case_session(mk_bot, helpers):
    from aipager.bot import session_parity

    bot = helpers.make_scoped_bot(
        mk_bot, chat_id=CHAT, members=[(USER, "bob", "user")])
    sess = _worst_case_session()
    bot.registry._sessions[sess.name] = sess

    upd = helpers.make_message_update(
        f"/restart {sess.label}", chat_id=CHAT, chat_type="group",
        user_id=USER,
    )
    _run(session_parity.handle_restart_cmd(bot, upd, MagicMock()))
    kb = upd.message.reply_text.await_args.kwargs.get("reply_markup")
    cbs = helpers.callback_data_in(kb)
    _assert_all_within_budget(cbs, surface="/restart confirm buttons")


def test_delete_confirm_buttons_exceed_the_budget_for_worst_case_session(mk_bot, helpers):
    from aipager.bot import session_parity

    bot = helpers.make_scoped_bot(
        mk_bot, chat_id=CHAT, members=[(USER, "bob", "user")])
    label = _max_creatable_label(CHAT, "group")
    name = disambiguated_name(label, CHAT, "group")
    sess = TrackedSession(name=name, label=label, status=Status.GONE,
                           scope_chat_id=CHAT, claude_session_id="abc123")
    bot.registry._sessions[sess.name] = sess

    upd = helpers.make_message_update(
        f"/delete {sess.label}", chat_id=CHAT, chat_type="group",
        user_id=USER,
    )
    _run(session_parity.handle_delete_cmd(bot, upd, MagicMock()))
    kb = upd.message.reply_text.await_args.kwargs.get("reply_markup")
    cbs = helpers.callback_data_in(kb)
    _assert_all_within_budget(cbs, surface="/delete confirm buttons")
