"""Clear conversation in a group (scope mode), through the full callback
dispatch (tap gate + card owner): design.md success criteria 3 (group
wording), 6 (tap gate), 7 (a role that may not send /clear), 8 (card
ownership) and 9 (another chat).

Group G: alice owner, bob user, dave admin, erin read_only; live sessions
x1 and x2 in G, d1 in alice's DM.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.bot import session_parity
from aipager.dtach import inject
from aipager.state import Status

# The ids the conftest's ``gbot`` fixture is built from (a hyphenated test
# directory is not an importable package, so they are repeated here).
G = -1001234
DM = 11
ALICE, BOB, DAVE, ERIN = 11, 22, 44, 55
X1 = "claude-x1__g1001234"


def make_tap(data, user, *, chat=G, message_id=900):
    query = MagicMock(data=data)
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.edit_message_reply_markup = AsyncMock()
    query.from_user = MagicMock(id=user, username=f"u{user}")
    query.message = MagicMock(message_id=message_id, text="")
    query.message.chat = MagicMock(id=chat, type="supergroup" if chat < 0 else "private")
    query.message.chat_id = chat
    query.message.reply_text = AsyncMock()
    update = MagicMock(callback_query=query, message=None, effective_user=query.from_user)
    update.effective_chat = MagicMock(id=chat, type="supergroup" if chat < 0 else "private")
    return update, query

CONFIRM_HEAD = "🧹 Clear the conversation in <b>x1</b>?"
NEEDS_ADMIN = "That command needs an admin."


def _cb(bot, verb, name=X1, chat=G):
    return session_parity.session_cb(bot, chat, bot.registry._sessions[name], verb)


def _run(bot, run_async, data, user, *, chat=G, message_id=900):
    update, query = make_tap(data, user, chat=chat, message_id=message_id)
    run_async(bot._handle_callback(update, MagicMock()))
    return query


def _toasts(query):
    return [c.args[0] if c.args else c.kwargs.get("text")
            for c in query.answer.await_args_list]


def _edits(query):
    return [c.args[0] if c.args else c.kwargs.get("text")
            for c in query.edit_message_text.await_args_list]


def _ok_data(query):
    markup = query.edit_message_text.await_args.kwargs["reply_markup"]
    return next(b.callback_data for row in markup.inline_keyboard for b in row
                if b.callback_data.rsplit(":", 1)[1].startswith("clearok"))


def _draw(gbot, run_async, user=ALICE, message_id=900):
    query = _run(gbot, run_async, _cb(gbot, "clear"), user, message_id=message_id)
    return query


def _typed_clear(gbot):
    return [c for c in gbot._inject_prompt.await_args_list if c.args[1:2] == ("/clear",)]


# ---- criterion 3 (group): who cleared it -------------------------------------

def test_group_clear_tap_draws_the_confirm(gbot, run_async):
    query = _draw(gbot, run_async)
    assert _edits(query)[0].startswith(CONFIRM_HEAD)


def test_group_confirm_types_clear_once_into_x1(gbot, run_async):
    ok = _ok_data(_draw(gbot, run_async))
    _run(gbot, run_async, ok, ALICE)
    assert [(c.args[0].name, c.args[1]) for c in gbot._inject_prompt.await_args_list] == [
        (X1, "/clear")]


def test_group_confirm_names_who_cleared_it(gbot, run_async):
    ok = _ok_data(_draw(gbot, run_async))
    query = _run(gbot, run_async, ok, ALICE)
    assert _edits(query) == ["🧹 /clear sent to x1 by @alice"]


def test_group_confirm_sent_card_has_no_keyboard(gbot, run_async):
    ok = _ok_data(_draw(gbot, run_async))
    query = _run(gbot, run_async, ok, ALICE)
    assert query.edit_message_text.await_args.kwargs.get("reply_markup") is None


def test_group_clear_tap_records_the_card_as_the_tappers(gbot, run_async):
    _draw(gbot, run_async, user=BOB, message_id=905)
    rec = gbot.registry.card_owners[(G, 905)]
    assert rec["kind"] == "clear"


# ---- criterion 6: the tap gate refuses read_only first -----------------------

def test_read_only_member_clear_tap_is_refused_by_the_gate(gbot, run_async):
    query = _run(gbot, run_async, _cb(gbot, "clear"), ERIN)
    assert _toasts(query) == ["Your role can't do that here."]


def test_read_only_member_clear_tap_draws_nothing(gbot, run_async):
    query = _run(gbot, run_async, _cb(gbot, "clear"), ERIN)
    query.edit_message_text.assert_not_awaited()


def test_read_only_member_confirm_is_refused_by_the_gate(gbot, run_async):
    turn = gbot.registry._sessions[X1].turn_key
    query = _run(gbot, run_async, _cb(gbot, f"clearok{turn}"), ERIN)
    assert _toasts(query) == ["Your role can't do that here."]


def test_read_only_member_confirm_types_nothing(gbot, run_async):
    turn = gbot.registry._sessions[X1].turn_key
    _run(gbot, run_async, _cb(gbot, f"clearok{turn}"), ERIN)
    gbot._inject_prompt.assert_not_awaited()


def test_read_only_member_cancel_is_refused_by_the_gate(gbot, run_async):
    query = _run(gbot, run_async, _cb(gbot, "clear-cancel"), ERIN)
    assert _toasts(query) == ["Your role can't do that here."]


@pytest.mark.parametrize("verb", ["clear", "clearok7", "clear-cancel"])
def test_clear_verbs_need_the_prompt_capability(verb):
    from aipager.bot import tap_gate
    assert tap_gate.required_capability(X1, verb) == tap_gate.Cap.PROMPT


def test_read_only_member_direct_clear_tap_says_cant_clear(gbot, run_async):
    """Behind the gate, the handler re-checks (a demotion between taps)."""
    update, query = make_tap("x", ERIN)
    run_async(session_parity.handle_callback(gbot, update, query, X1, "clear"))
    assert _toasts(query) == ["You can't clear this session's conversation."]


def test_read_only_member_direct_confirm_types_nothing(gbot, run_async):
    turn = gbot.registry._sessions[X1].turn_key
    update, query = make_tap("x", ERIN)
    run_async(session_parity.handle_callback(gbot, update, query, X1, f"clearok{turn}"))
    gbot._inject_prompt.assert_not_awaited()


# ---- criterion 7: Clear removed from the Commands keyboard -------------------

def test_user_role_clear_tap_without_clear_command_gets_needs_admin_alert(gbot, run_async):
    gbot._command_map = {}
    query = _run(gbot, run_async, _cb(gbot, "clear"), BOB)
    call = query.answer.await_args
    assert (call.args[0], call.kwargs.get("show_alert")) == (NEEDS_ADMIN, True)


def test_user_role_clear_tap_without_clear_command_draws_nothing(gbot, run_async):
    gbot._command_map = {}
    query = _run(gbot, run_async, _cb(gbot, "clear"), BOB)
    query.edit_message_text.assert_not_awaited()


def test_user_role_clear_tap_without_clear_command_types_nothing(gbot, run_async):
    gbot._command_map = {}
    _run(gbot, run_async, _cb(gbot, "clear"), BOB)
    gbot._inject_prompt.assert_not_awaited()


@pytest.mark.parametrize("who", [ALICE, DAVE], ids=["owner", "admin"])
def test_owner_or_admin_may_clear_without_clear_command(gbot, run_async, who):
    gbot._command_map = {}
    ok = _ok_data(_draw(gbot, run_async, user=who))
    _run(gbot, run_async, ok, who)
    assert len(_typed_clear(gbot)) == 1


def test_user_role_with_clear_command_may_clear(gbot, run_async):
    ok = _ok_data(_draw(gbot, run_async, user=BOB))
    _run(gbot, run_async, ok, BOB)
    assert len(_typed_clear(gbot)) == 1


@pytest.fixture
def real_typing(gbot, monkeypatch):
    """The real prompt path, so its own role rule runs; the PTY is a seam."""
    del gbot._inject_prompt
    typed = AsyncMock(return_value=True)
    monkeypatch.setattr(inject, "send_text_and_enter", typed)
    return typed


def test_confirm_after_clear_left_the_keyboard_alerts_needs_admin(gbot, run_async,
                                                                  real_typing):
    ok = _ok_data(_draw(gbot, run_async, user=BOB))
    gbot._command_map = {}
    query = _run(gbot, run_async, ok, BOB)
    call = query.answer.await_args
    assert (call.args[0], call.kwargs.get("show_alert")) == (NEEDS_ADMIN, True)


def test_confirm_after_clear_left_the_keyboard_types_nothing(gbot, run_async, real_typing):
    ok = _ok_data(_draw(gbot, run_async, user=BOB))
    gbot._command_map = {}
    _run(gbot, run_async, ok, BOB)
    real_typing.assert_not_awaited()


def test_confirm_through_the_real_prompt_path_types_clear(gbot, run_async, real_typing):
    ok = _ok_data(_draw(gbot, run_async, user=BOB))
    _run(gbot, run_async, ok, BOB)
    assert [c.args[:2] for c in real_typing.await_args_list] == [(X1, "/clear")]


# ---- criterion 8: someone else's card ----------------------------------------

@pytest.mark.parametrize("verb", ["ok", "clear-cancel"], ids=["confirm", "cancel"])
def test_other_members_tap_on_the_clear_card_gets_owner_toast(gbot, run_async, verb):
    ok = _ok_data(_draw(gbot, run_async, user=ALICE, message_id=910))
    data = ok if verb == "ok" else _cb(gbot, "clear-cancel")
    query = _run(gbot, run_async, data, BOB, message_id=910)
    assert _toasts(query) == ["This is @alice's card."]


@pytest.mark.parametrize("verb", ["ok", "clear-cancel"], ids=["confirm", "cancel"])
def test_other_members_tap_on_the_clear_card_types_nothing(gbot, run_async, verb):
    ok = _ok_data(_draw(gbot, run_async, user=ALICE, message_id=910))
    data = ok if verb == "ok" else _cb(gbot, "clear-cancel")
    _run(gbot, run_async, data, BOB, message_id=910)
    gbot._inject_prompt.assert_not_awaited()


@pytest.mark.parametrize("verb", ["ok", "clear-cancel"], ids=["confirm", "cancel"])
def test_other_members_tap_on_the_clear_card_edits_nothing(gbot, run_async, verb):
    ok = _ok_data(_draw(gbot, run_async, user=ALICE, message_id=910))
    data = ok if verb == "ok" else _cb(gbot, "clear-cancel")
    query = _run(gbot, run_async, data, BOB, message_id=910)
    query.edit_message_text.assert_not_awaited()


def test_an_admin_is_also_refused_on_someone_elses_clear_card(gbot, run_async):
    ok = _ok_data(_draw(gbot, run_async, user=BOB, message_id=911))
    query = _run(gbot, run_async, ok, DAVE, message_id=911)
    assert _toasts(query) == ["This is @bob's card."]


def test_the_card_owner_can_still_confirm_after_a_refused_tap(gbot, run_async):
    ok = _ok_data(_draw(gbot, run_async, user=ALICE, message_id=912))
    _run(gbot, run_async, ok, BOB, message_id=912)
    _run(gbot, run_async, ok, ALICE, message_id=912)
    assert len(_typed_clear(gbot)) == 1


def test_the_card_owner_can_cancel_their_clear_card(gbot, run_async):
    _draw(gbot, run_async, user=ALICE, message_id=913)
    query = _run(gbot, run_async, _cb(gbot, "clear-cancel"), ALICE, message_id=913)
    assert _edits(query) == ["Clear cancelled for [<b>x1</b>]."]


# ---- criterion 9: from another chat ------------------------------------------

def test_clear_tap_from_another_chat_says_not_running_here(gbot, run_async):
    update, query = make_tap("x", ALICE, chat=DM)
    run_async(session_parity.handle_callback(gbot, update, query, X1, "clear"))
    assert _toasts(query) == ["That session isn't running here."]


def test_clear_tap_from_another_chat_draws_nothing(gbot, run_async):
    update, query = make_tap("x", ALICE, chat=DM)
    run_async(session_parity.handle_callback(gbot, update, query, X1, "clear"))
    query.edit_message_text.assert_not_awaited()


def test_confirm_from_another_chat_says_not_running_here(gbot, run_async):
    turn = gbot.registry._sessions[X1].turn_key
    update, query = make_tap("x", ALICE, chat=DM)
    run_async(session_parity.handle_callback(gbot, update, query, X1, f"clearok{turn}"))
    assert _toasts(query) == ["That session isn't running here."]


def test_confirm_from_another_chat_types_nothing(gbot, run_async):
    turn = gbot.registry._sessions[X1].turn_key
    update, query = make_tap("x", ALICE, chat=DM)
    run_async(session_parity.handle_callback(gbot, update, query, X1, f"clearok{turn}"))
    gbot._inject_prompt.assert_not_awaited()


def test_group_confirm_on_busy_session_types_nothing(gbot, run_async):
    ok = _ok_data(_draw(gbot, run_async))
    gbot.registry._sessions[X1].status = Status.BUSY
    _run(gbot, run_async, ok, ALICE)
    gbot._inject_prompt.assert_not_awaited()

