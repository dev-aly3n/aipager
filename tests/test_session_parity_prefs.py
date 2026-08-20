"""Tests for aipager.bot.session_parity — per-session preferences.

Covers entrypoints.md's ``render_session_preferences_root`` /
``render_session_preferences_field`` (the ONE renderer decision #3
asks for) and the ``_:spref...`` callback family (picker, index
navigation, set/clear, auth gating, stale-index fail-closed).

session_parity.py is not wired into handlers.py/callbacks.py in this
worktree (the integrator applies that wiring after all three streams
land — see implementation-parity.md), so every test here calls the
module's exported functions directly, exactly as entrypoints.md
documents them: ``handle_callback(bot, update, query, session_name,
action)`` mirrors the exact call shape the shared-file integration
snippet uses.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import preferences as prefs
from aipager.bot import session_parity
from aipager.state import TrackedSession
from aipager.team import Role, Team
from aipager.team import User as TeamUser


@pytest.fixture
def mk_query():
    def _mk(callback_data, *, user_id=12345, message_id=42, text=""):
        query = MagicMock()
        query.data = callback_data
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        query.message = MagicMock()
        query.message.message_id = message_id
        query.message.text = text
        query.message.reply_text = AsyncMock()
        query.message.reply_document = AsyncMock()
        query.from_user = MagicMock()
        query.from_user.id = user_id
        return query
    return _mk


def _mk_cb_update(chat_id, user_id):
    update = MagicMock()
    update.effective_chat = MagicMock()
    update.effective_chat.id = chat_id
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    return update


def _session(name="claude-dev", label="dev", **kwargs):
    return TrackedSession(name=name, label=label, **kwargs)


# ---- render_session_preferences_root / field (direct calls) -------------

def test_root_shows_effective_scope_values_with_no_overrides():
    prefs.set_preference(555, "layout", "merged")
    prefs.set_preference(555, "answer_length", "short")
    sess = _session(scope_chat_id=555)

    text, kb = session_parity.render_session_preferences_root(
        sess, 555, cb_prefix="_:spref:0",
    )

    flat = [btn.text for row in kb.inline_keyboard for btn in row]
    assert any("Merged into busy message" in t for t in flat)
    assert any("Short" in t for t in flat)
    assert not any("⭐" in t for t in flat)  # nothing overridden yet
    assert kb.inline_keyboard[0][0].callback_data == "_:spref:0:layout"
    assert "dev" in text


def test_root_marks_overridden_field():
    prefs.set_preference(555, "layout", "card")
    sess = _session(scope_chat_id=555, override_layout="replace")

    _text, kb = session_parity.render_session_preferences_root(
        sess, 555, cb_prefix="_:spref:0",
    )

    layout_row = kb.inline_keyboard[0][0].text
    assert "Replace with result" in layout_row
    assert "⭐" in layout_row


def test_root_identical_across_call_sites_differ_only_in_prefix():
    """design.md decision #3: /settings->Per-session and a session's own
    ⋮ menu->Preferences reach this via different cb_prefix values but
    must render the identical field list/labels/markers."""
    sess = _session(scope_chat_id=555, override_answer_length="short")

    text_a, kb_a = session_parity.render_session_preferences_root(
        sess, 555, cb_prefix="_:spref:3",
    )
    text_b, kb_b = session_parity.render_session_preferences_root(
        sess, 555, cb_prefix="_:spref:7",
    )

    labels_a = [btn.text for row in kb_a.inline_keyboard for btn in row]
    labels_b = [btn.text for row in kb_b.inline_keyboard for btn in row]
    assert labels_a == labels_b
    assert text_a == text_b

    cbs_a = [btn.callback_data for row in kb_a.inline_keyboard for btn in row]
    cbs_b = [btn.callback_data for row in kb_b.inline_keyboard for btn in row]
    assert cbs_a != cbs_b  # only the embedded index differs


def test_field_view_unknown_section_returns_none():
    sess = _session()
    assert session_parity.render_session_preferences_field(
        sess, 0, "bogus-section", cb_prefix="_:spref:0",
    ) is None


def test_field_view_formatting_tokens_marker_and_default_tag():
    prefs.set_preference(555, "simple_formatting", False)
    sess = _session(scope_chat_id=555, override_simple_formatting=True)

    _text, kb = session_parity.render_session_preferences_field(
        sess, 555, "formatting", cb_prefix="_:spref:0",
    )

    buttons = {b.callback_data: b.text for row in kb.inline_keyboard for b in row}
    assert "_:spref:0:formatting:off" in buttons
    assert "_:spref:0:formatting:on" in buttons
    assert "_:spref:0:formatting:default" in buttons
    assert "✅" in buttons["_:spref:0:formatting:on"]  # matches the override
    assert "(chat default)" in buttons["_:spref:0:formatting:off"]
    assert "« Back" in [b.text for row in kb.inline_keyboard for b in row]


def test_field_view_use_chat_default_row_marks_when_unset():
    sess = _session(scope_chat_id=0)  # no override at all
    _text, kb = session_parity.render_session_preferences_field(
        sess, 0, "length", cb_prefix="_:spref:0",
    )
    default_row = next(
        b for row in kb.inline_keyboard for b in row
        if b.callback_data == "_:spref:0:length:default"
    )
    assert "✅" in default_row.text


# ---- _pref_index_map / _register_pref_index isolation --------------------

def test_pref_index_lives_on_instance_not_module(mk_bot):
    """Non-negotiable: pending/index state must be a lazily-initialised
    TelegramBot instance attribute, never a module dict — a module dict
    would leak this exact registration into a second, unrelated bot."""
    bot1 = mk_bot()
    bot2 = mk_bot()

    session_parity._register_pref_index(bot1, 42, ["claude-a"])

    assert session_parity._pref_index_map(bot1)[42] == ["claude-a"]
    assert session_parity._pref_index_map(bot2).get(42) is None


# ---- _:spref callback family ----------------------------------------------

def test_spref_picker_lists_sessions_sorted_and_registers_index(mk_bot, run_async, mk_query):
    bot = mk_bot()
    bravo = _session(name="claude-bravo", label="bravo", scope_chat_id=555)
    alpha = _session(name="claude-alpha", label="alpha", scope_chat_id=555)
    bot.registry._sessions[bravo.name] = bravo
    bot.registry._sessions[alpha.name] = alpha

    query = mk_query("_:spref")
    update = _mk_cb_update(555, 12345)
    handled = run_async(session_parity.handle_callback(bot, update, query, "_", "spref"))

    assert handled is True
    query.edit_message_text.assert_awaited_once()
    assert bot._session_pref_index[555] == ["claude-alpha", "claude-bravo"]
    labels = [btn.text for row in query.edit_message_text.await_args.kwargs["reply_markup"]
              .inline_keyboard for btn in row]
    assert labels[0] == "alpha"
    assert labels[1] == "bravo"


def test_spref_index_opens_root_view(mk_bot, run_async, mk_query):
    bot = mk_bot()
    sess = _session(scope_chat_id=555)
    bot.registry._sessions[sess.name] = sess
    bot._session_pref_index = {555: [sess.name]}

    query = mk_query("_:spref:0")
    update = _mk_cb_update(555, 12345)
    handled = run_async(session_parity.handle_callback(bot, update, query, "_", "spref:0"))

    assert handled is True
    query.edit_message_text.assert_awaited_once()
    text = query.edit_message_text.await_args.args[0]
    assert "dev" in text


def test_spref_index_empty_list_fails_closed(mk_bot, run_async, mk_query):
    bot = mk_bot()
    bot._session_pref_index = {555: []}

    query = mk_query("_:spref:0")
    update = _mk_cb_update(555, 12345)
    handled = run_async(session_parity.handle_callback(bot, update, query, "_", "spref:0"))

    assert handled is True
    query.edit_message_text.assert_not_awaited()
    query.answer.assert_awaited_once()
    assert "no longer available" in query.answer.await_args.args[0]


def test_spref_stale_index_out_of_range_in_nonempty_list_fails_closed(mk_bot, run_async, mk_query):
    """Distinct from the empty-list case above: the index list is
    non-empty and its one entry resolves to a REAL, registered session —
    proving an out-of-range tap fails closed rather than wrapping around
    (e.g. via ``idx % len(names)``) onto that other, real session."""
    bot = mk_bot()
    only_one = _session(name="claude-only-one", label="only-one", scope_chat_id=555)
    bot.registry._sessions[only_one.name] = only_one
    bot._session_pref_index = {555: ["claude-only-one"]}

    query = mk_query("_:spref:5")
    update = _mk_cb_update(555, 12345)
    handled = run_async(session_parity.handle_callback(bot, update, query, "_", "spref:5"))

    assert handled is True
    query.edit_message_text.assert_not_awaited()
    assert "no longer available" in query.answer.await_args.args[0]


def test_spref_index_pointing_at_deleted_session_fails_closed(mk_bot, run_async, mk_query):
    """A session removed between the index being registered and the tap
    arriving must fail closed, never resolve to whatever else now sits
    at that internal name."""
    bot = mk_bot()
    sess = _session(scope_chat_id=555)
    bot.registry._sessions[sess.name] = sess
    bot._session_pref_index = {555: [sess.name]}
    bot.registry._sessions.pop(sess.name)  # simulate a delete mid-view

    query = mk_query("_:spref:0")
    update = _mk_cb_update(555, 12345)
    handled = run_async(session_parity.handle_callback(bot, update, query, "_", "spref:0"))

    assert handled is True
    query.edit_message_text.assert_not_awaited()
    assert "no longer available" in query.answer.await_args.args[0]


def test_spref_malformed_index_token_fails_closed(mk_bot, run_async, mk_query):
    bot = mk_bot()
    bot._session_pref_index = {555: ["claude-dev"]}

    query = mk_query("_:spref:not-a-number")
    update = _mk_cb_update(555, 12345)
    handled = run_async(
        session_parity.handle_callback(bot, update, query, "_", "spref:not-a-number"),
    )

    assert handled is True
    query.edit_message_text.assert_not_awaited()


def test_spref_field_navigation(mk_bot, run_async, mk_query):
    bot = mk_bot()
    sess = _session(scope_chat_id=555)
    bot.registry._sessions[sess.name] = sess
    bot._session_pref_index = {555: [sess.name]}

    query = mk_query("_:spref:0:layout")
    update = _mk_cb_update(555, 12345)
    handled = run_async(session_parity.handle_callback(bot, update, query, "_", "spref:0:layout"))

    assert handled is True
    query.edit_message_text.assert_awaited_once()
    cbs = [b.callback_data for row in query.edit_message_text.await_args.kwargs["reply_markup"]
           .inline_keyboard for b in row]
    assert "_:spref:0:layout:card" in cbs


def test_spref_field_navigation_invalid_section_toasts(mk_bot, run_async, mk_query):
    bot = mk_bot()
    sess = _session(scope_chat_id=555)
    bot.registry._sessions[sess.name] = sess
    bot._session_pref_index = {555: [sess.name]}

    query = mk_query("_:spref:0:bogus")
    update = _mk_cb_update(555, 12345)
    handled = run_async(session_parity.handle_callback(bot, update, query, "_", "spref:0:bogus"))

    assert handled is True
    query.edit_message_text.assert_not_awaited()
    query.answer.assert_awaited_once_with("Invalid callback")


def test_spref_set_value_mutates_and_marks_dirty(mk_bot, run_async, mk_query):
    bot = mk_bot()
    sess = _session(scope_chat_id=555)
    bot.registry._sessions[sess.name] = sess
    bot._session_pref_index = {555: [sess.name]}
    bot.registry._dirty = False

    query = mk_query("_:spref:0:length:short")
    update = _mk_cb_update(555, 12345)
    handled = run_async(
        session_parity.handle_callback(bot, update, query, "_", "spref:0:length:short"),
    )

    assert handled is True
    assert sess.override_answer_length == "short"
    assert bot.registry._dirty is True
    query.edit_message_text.assert_awaited_once()


def test_spref_clear_value_via_default_token(mk_bot, run_async, mk_query):
    bot = mk_bot()
    sess = _session(scope_chat_id=555, override_answer_length="short")
    bot.registry._sessions[sess.name] = sess
    bot._session_pref_index = {555: [sess.name]}

    query = mk_query("_:spref:0:length:default")
    update = _mk_cb_update(555, 12345)
    handled = run_async(
        session_parity.handle_callback(bot, update, query, "_", "spref:0:length:default"),
    )

    assert handled is True
    assert sess.override_answer_length is None


def test_spref_set_invalid_value_toasts_without_mutating(mk_bot, run_async, mk_query):
    bot = mk_bot()
    sess = _session(scope_chat_id=555)
    bot.registry._sessions[sess.name] = sess
    bot._session_pref_index = {555: [sess.name]}

    query = mk_query("_:spref:0:length:sideways")
    update = _mk_cb_update(555, 12345)
    handled = run_async(
        session_parity.handle_callback(bot, update, query, "_", "spref:0:length:sideways"),
    )

    assert handled is True
    assert sess.override_answer_length is None
    query.answer.assert_awaited_once_with("Invalid value")


def test_spref_stale_index_write_fails_closed_without_mutating(mk_bot, run_async, mk_query):
    bot = mk_bot()
    bot._session_pref_index = {555: []}

    query = mk_query("_:spref:0:length:short")
    update = _mk_cb_update(555, 12345)
    handled = run_async(
        session_parity.handle_callback(bot, update, query, "_", "spref:0:length:short"),
    )

    assert handled is True
    assert "no longer available" in query.answer.await_args.args[0]


def test_spref_write_denied_for_read_only_team_member(mk_bot, run_async, mk_query):
    team = Team(group_id=555, users={999: TeamUser(id=999, label="ro", role=Role.READ_ONLY)})
    bot = mk_bot(team=team)
    sess = _session(scope_chat_id=555)
    bot.registry._sessions[sess.name] = sess
    bot._session_pref_index = {555: [sess.name]}

    query = mk_query("_:spref:0:length:short", user_id=999)
    update = _mk_cb_update(555, 999)
    handled = run_async(
        session_parity.handle_callback(bot, update, query, "_", "spref:0:length:short"),
    )

    assert handled is True
    assert sess.override_answer_length is None
    query.answer.assert_awaited_once_with("You can't change this session's preferences.")


def test_spref_read_only_member_can_still_navigate(mk_bot, run_async, mk_query):
    team = Team(group_id=555, users={999: TeamUser(id=999, label="ro", role=Role.READ_ONLY)})
    bot = mk_bot(team=team)
    sess = _session(scope_chat_id=555)
    bot.registry._sessions[sess.name] = sess
    bot._session_pref_index = {555: [sess.name]}

    query = mk_query("_:spref:0", user_id=999)
    update = _mk_cb_update(555, 999)
    handled = run_async(session_parity.handle_callback(bot, update, query, "_", "spref:0"))

    assert handled is True
    query.edit_message_text.assert_awaited_once()


def test_spref_close(mk_bot, run_async, mk_query):
    bot = mk_bot()
    query = mk_query("_:spref:close")
    update = _mk_cb_update(555, 1)
    handled = run_async(session_parity.handle_callback(bot, update, query, "_", "spref:close"))

    assert handled is True
    query.edit_message_text.assert_awaited_once()
    assert query.edit_message_text.await_args.kwargs["reply_markup"] is None


# ---- dispatch boundaries ---------------------------------------------

def test_handle_callback_ignores_settings_menu_namespace(mk_bot, run_async, mk_query):
    """`_:set...` belongs to settings_menu.py's own callback family —
    session_parity must never intercept it."""
    bot = mk_bot()
    query = mk_query("_:set")
    update = _mk_cb_update(0, 1)
    handled = run_async(session_parity.handle_callback(bot, update, query, "_", "set"))
    assert handled is False
    query.edit_message_text.assert_not_awaited()


def test_handle_callback_ignores_new_flow_namespace(mk_bot, run_async, mk_query):
    bot = mk_bot()
    query = mk_query("_:nw:cancel")
    update = _mk_cb_update(0, 1)
    handled = run_async(session_parity.handle_callback(bot, update, query, "_", "nw:cancel"))
    assert handled is False
