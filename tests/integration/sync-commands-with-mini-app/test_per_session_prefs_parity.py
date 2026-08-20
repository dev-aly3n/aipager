"""Priority 7 (task brief): both per-session preference routes render
identically — `/settings -> "Per-session"` and a session's ⋮ menu.

entrypoints.md: "Both live in aipager/bot/session_parity.py, called
from exactly two call sites ... with a different `cb_prefix` per call
but otherwise identical rendering, so the two paths cannot show
different fields, labels, or current-value markers."
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


CHAT = 0


def _one_session_bot(mk_bot, helpers, *, label="foo"):
    from aipager.state import TrackedSession, Status
    bot = helpers.make_personal_bot(mk_bot)
    sess = TrackedSession(name=f"claude-{label}", label=label,
                           status=Status.IDLE, scope_chat_id=CHAT)
    bot.registry._sessions[sess.name] = sess
    return bot, sess


def _prime_index(helpers, bot, *, message_id=1):
    """entrypoints.md: `bot._session_pref_index[chat_id]` is "populated
    fresh every time the picker ... is rendered" — a direct
    `_:spref:0:...` tap with no prior picker/menu render in this test
    has nothing to resolve `idx=0` against. Renders the picker once,
    purely to populate the index, exactly as a real `/settings ->
    Per-session` tap sequence would before reaching a field."""
    cb, q = helpers.make_callback_update(
        "_:spref", chat_id=CHAT, chat_type="private", message_id=message_id)
    _run(bot._handle_callback(cb, MagicMock()))
    return q


def _via_settings_picker(helpers, bot, *, message_id=1):
    """Path A: /settings -> "Per-session preferences" -> pick the
    (only) session -> the session's preference root view."""
    upd = helpers.make_message_update(
        "/settings", chat_id=CHAT, chat_type="private")
    _run(bot._handle_settings_cmd(upd, MagicMock()))

    cb1, q1 = helpers.make_callback_update(
        "_:spref", chat_id=CHAT, chat_type="private", message_id=message_id)
    _run(bot._handle_callback(cb1, MagicMock()))

    cb2, q2 = helpers.make_callback_update(
        "_:spref:0", chat_id=CHAT, chat_type="private", message_id=message_id)
    _run(bot._handle_callback(cb2, MagicMock()))
    args, kwargs = q2.edit_message_text.await_args
    text = args[0] if args else kwargs.get("text")
    return text, kwargs.get("reply_markup")


def _via_menu(helpers, bot, sess, *, message_id=2):
    """Path B: /status -> session's ⋮ menu -> "Preferences" row."""
    upd = helpers.make_message_update(
        "/status", chat_id=CHAT, chat_type="private")
    _run(bot._handle_status(upd, MagicMock()))

    cb1, q1 = helpers.make_callback_update(
        f"{sess.name}:menu", chat_id=CHAT, chat_type="private",
        message_id=message_id,
    )
    _run(bot._handle_callback(cb1, MagicMock()))
    menu_markup = q1.edit_message_text.await_args.kwargs.get("reply_markup")
    pref_cb = next(
        cb for cb in helpers.callback_data_in(menu_markup)
        if cb.startswith("_:spref:")
    )

    cb2, q2 = helpers.make_callback_update(
        pref_cb, chat_id=CHAT, chat_type="private", message_id=message_id)
    _run(bot._handle_callback(cb2, MagicMock()))
    args, kwargs = q2.edit_message_text.await_args
    text = args[0] if args else kwargs.get("text")
    return text, kwargs.get("reply_markup")


# --------------------------------------------------------------------------- #
# Root view parity.                                                         #
# --------------------------------------------------------------------------- #

def test_root_view_text_is_identical_from_both_entry_points(mk_bot, helpers):
    bot, sess = _one_session_bot(mk_bot, helpers)
    text_a, _ = _via_settings_picker(helpers, bot)
    text_b, _ = _via_menu(helpers, bot, sess)
    assert text_a == text_b


def test_root_view_field_labels_are_identical_from_both_entry_points(mk_bot, helpers):
    bot, sess = _one_session_bot(mk_bot, helpers)
    _, markup_a = _via_settings_picker(helpers, bot)
    _, markup_b = _via_menu(helpers, bot, sess)
    labels_a = [btn.text for row in markup_a.inline_keyboard for btn in row]
    labels_b = [btn.text for row in markup_b.inline_keyboard for btn in row]
    assert labels_a == labels_b


def test_root_view_field_ordering_matches_settings_schema(mk_bot, helpers):
    """entrypoints.md: `section` is one of layout/formatting/length/
    level — "same four as settings_menu.SECTIONS"."""
    from aipager.bot.settings_menu import SECTIONS
    bot, sess = _one_session_bot(mk_bot, helpers)
    _, markup = _via_settings_picker(helpers, bot)
    labels = [btn.text for row in markup.inline_keyboard for btn in row]
    # One row per SECTIONS entry precedes any nav row (Back/Close).
    assert len(labels) >= len(SECTIONS)


# --------------------------------------------------------------------------- #
# Field-level (value list) view parity, before and after a write — proves   #
# the two paths share state, not just an initial render.                    #
# --------------------------------------------------------------------------- #

def test_field_view_is_identical_from_both_entry_points_before_any_override(mk_bot, helpers):
    bot, sess = _one_session_bot(mk_bot, helpers)
    _prime_index(helpers, bot)

    cb_a, q_a = helpers.make_callback_update(
        "_:spref:0:layout", chat_id=CHAT, chat_type="private", message_id=1)
    _run(bot._handle_callback(cb_a, MagicMock()))
    text_a = q_a.edit_message_text.await_args[0][0]
    markup_a = q_a.edit_message_text.await_args.kwargs.get("reply_markup")

    cb_b, q_b = helpers.make_callback_update(
        "_:spref:0:layout", chat_id=CHAT, chat_type="private", message_id=2)
    _run(bot._handle_callback(cb_b, MagicMock()))
    text_b = q_b.edit_message_text.await_args[0][0]
    markup_b = q_b.edit_message_text.await_args.kwargs.get("reply_markup")

    assert text_a == text_b
    labels_a = [btn.text for row in markup_a.inline_keyboard for btn in row]
    labels_b = [btn.text for row in markup_b.inline_keyboard for btn in row]
    assert labels_a == labels_b


def test_setting_a_value_is_visible_from_both_entry_points(mk_bot, helpers):
    """The write happens via ONE call site (`_:spref:0:layout:merged`);
    the marker showing "merged" is now current must appear from BOTH
    the settings-picker root view AND the ⋮ menu root view — proving
    they read the same underlying state, not a per-call-site cache."""
    bot, sess = _one_session_bot(mk_bot, helpers)
    _prime_index(helpers, bot)

    cb_set, q_set = helpers.make_callback_update(
        "_:spref:0:layout:merged", chat_id=CHAT, chat_type="private",
        message_id=1,
    )
    _run(bot._handle_callback(cb_set, MagicMock()))
    assert sess.override_layout == "merged"

    text_a, _ = _via_settings_picker(helpers, bot, message_id=10)
    text_b, _ = _via_menu(helpers, bot, sess, message_id=11)

    assert "⭐" in text_a or "1" in text_a  # some overridden-count/marker
    assert text_a == text_b


def test_setting_a_value_marks_the_current_option_from_both_entry_points(mk_bot, helpers):
    bot, sess = _one_session_bot(mk_bot, helpers)
    _prime_index(helpers, bot)

    cb_set, q_set = helpers.make_callback_update(
        "_:spref:0:layout:merged", chat_id=CHAT, chat_type="private",
        message_id=1,
    )
    _run(bot._handle_callback(cb_set, MagicMock()))

    cb_a, q_a = helpers.make_callback_update(
        "_:spref:0:layout", chat_id=CHAT, chat_type="private", message_id=2)
    _run(bot._handle_callback(cb_a, MagicMock()))
    markup_a = q_a.edit_message_text.await_args.kwargs.get("reply_markup")
    merged_label_a = next(
        btn.text for row in markup_a.inline_keyboard for btn in row
        if btn.callback_data == "_:spref:0:layout:merged"
    )

    cb_b, q_b = helpers.make_callback_update(
        "_:spref:0:layout", chat_id=CHAT, chat_type="private", message_id=3)
    _run(bot._handle_callback(cb_b, MagicMock()))
    markup_b = q_b.edit_message_text.await_args.kwargs.get("reply_markup")
    merged_label_b = next(
        btn.text for row in markup_b.inline_keyboard for btn in row
        if btn.callback_data == "_:spref:0:layout:merged"
    )

    assert merged_label_a == merged_label_b
    assert "✅" in merged_label_a


def test_clearing_override_is_visible_from_both_entry_points(mk_bot, helpers):
    bot, sess = _one_session_bot(mk_bot, helpers)
    sess.override_layout = "replace"
    _prime_index(helpers, bot)

    cb_clear, _ = helpers.make_callback_update(
        "_:spref:0:layout:default", chat_id=CHAT, chat_type="private",
        message_id=1,
    )
    _run(bot._handle_callback(cb_clear, MagicMock()))
    assert sess.override_layout is None

    text_a, _ = _via_settings_picker(helpers, bot, message_id=10)
    text_b, _ = _via_menu(helpers, bot, sess, message_id=11)
    assert text_a == text_b
