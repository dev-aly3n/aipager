"""Group attribution, confirm cards, /app, anonymous senders and an ended
target (roadmap 8.91 c, d, e, g, delivery 12a).

- (c) In a group, every result that changes a session says who did it
  (Stop, End, Restart, Delete, a mode or model switch, a settings change),
  and the End / Restart / Delete / mode-switch confirm cards (and the
  pickers that lead to them) answer only the person who asked for them.
- (d) `/app` in a group tells only a member with their own DM to DM the
  bot; the group menus no longer offer it.
- (e) A message from an anonymous admin or posted as a channel is told
  "Post as yourself to use the bot." once per chat per run and nothing
  else; its shared id is never recorded as pending, and the wizard
  refuses it.
- (g) A member whose own session has ended is asked which session, with a
  Resume button, instead of falling to the only live session (which may
  be another member's).
- An unstamped session's policy note carries its home chat's deny_tools.

Groups cannot be tested live, so these tests are the proof. Every DM case
pins the operator's own install byte for byte.

Group G: alice owner (also has her own DM scope), bob and carol users,
dave admin (group only). Live sessions x1 and x2 in G; d1 in alice's DM.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import Chat, Message, Update, User
from telegram.ext import ApplicationHandlerStop

from aipager import config, preferences, team
from aipager.bot import card_owner, group_intake, handlers, new_flow, session_parity
from aipager.bot import transport
from aipager.bot.session_ops import StopOutcome
from aipager.dtach import inject
from aipager.scope import Member, Scope
from aipager.state import SessionRegistry, Status, TrackedSession

G = -1001234
OTHER_G = -1009999
DM = 11
ALICE, BOB, CAROL, DAVE = 11, 22, 33, 44
X1, X2 = "claude-x1__g1001234", "claude-x2__g1001234"
D1 = "claude-d1"
DATE = datetime(2026, 10, 4, tzinfo=timezone.utc)
ANON, CHANNEL_BOT, SERVICE = 1087968824, 136817688, 777000


def _scopes(dm_deny=()):
    return [
        Scope(chat_id=G, kind="group", label="team", members=(
            Member(id=ALICE, label="alice", role="owner"),
            Member(id=BOB, label="bob", role="user"),
            Member(id=CAROL, label="carol", role="user"),
            Member(id=DAVE, label="dave", role="admin"),
        )),
        Scope(chat_id=DM, kind="dm", label="alice-dm", members=(
            Member(id=ALICE, label="alice", role="owner"),
        ), deny_tools=tuple(dm_deny)),
    ]


def _registry() -> SessionRegistry:
    r = SessionRegistry()
    for name, label, chat in ((X1, "x1", G), (X2, "x2", G), (D1, "d1", DM)):
        s = TrackedSession(name=name, label=label, status=Status.IDLE)
        s.scope_chat_id = chat
        s.scope_kind = "group" if chat < 0 else "dm"
        r._sessions[name] = s
    return r


@pytest.fixture
def gbot(mk_bot, monkeypatch):
    from aipager.policy import load_policy
    r = _registry()
    bot = mk_bot(r, scopes=_scopes())
    bot.policy = load_policy()
    monkeypatch.setattr(inject, "is_alive",
                        AsyncMock(side_effect=lambda name: name in r._sessions))
    bot._inject_prompt = AsyncMock(return_value=True)
    bot._card_for_injected = AsyncMock()
    bot._react = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    bot._update_bot_commands = AsyncMock()
    bot.refresh_pinned = AsyncMock()
    return bot


def _sess(bot, name):
    return bot.registry._sessions[name]


_SENT_ID = iter(range(7000, 99999))


def _msg(mk_update, text, user, *, chat=G, reply_to=None):
    """A command or message from *user*; its reply returns a message with
    a fresh id (the card a command sends)."""
    u = mk_update(text, user_id=user, chat_id=chat, message_id=next(_SENT_ID))
    u.effective_user.username = f"u{user}"
    u.effective_message = u.message
    u.effective_chat.type = "supergroup" if chat < 0 else "private"
    u.message.sender_chat = None
    u.message.from_user = u.effective_user
    u.message.forward_origin = None
    u.message.via_bot = None
    u.message.external_reply = None
    u.message.photo = None
    u.message.document = None
    u.message.entities = ()
    u.message.caption_entities = ()
    u.message.parse_entities = MagicMock(return_value={})
    u.message.parse_caption_entities = MagicMock(return_value={})
    u.message.reply_to_message = reply_to
    u.message.reply_text = AsyncMock(
        side_effect=lambda *a, **k: MagicMock(message_id=next(_SENT_ID)))
    return u


def _card(update):
    """(text, markup, message id) of the last reply *update* got."""
    call = update.message.reply_text.await_args
    return call.args[0], call.kwargs.get("reply_markup")


def _tap(bot, data, user, *, chat=G, message_id):
    query = MagicMock(data=data)
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.edit_message_reply_markup = AsyncMock()
    query.from_user = MagicMock(id=user, username=f"u{user}")
    query.message = MagicMock(message_id=message_id, text="")
    query.message.chat = MagicMock(id=chat, type="supergroup" if chat < 0 else "private")
    query.message.chat_id = chat
    update = MagicMock(callback_query=query, message=None, effective_user=query.from_user)
    update.effective_chat = MagicMock(id=chat, type="supergroup" if chat < 0 else "private")
    return update, query


def _run_tap(bot, run_async, data, user, *, chat=G, message_id):
    update, query = _tap(bot, data, user, chat=chat, message_id=message_id)
    run_async(bot._handle_callback(update, MagicMock()))
    return query


def _toasts(query):
    return [c.args[0] if c.args else None for c in query.answer.await_args_list]


def _edit_texts(query):
    return [c.args[0] if c.args else c.kwargs.get("text")
            for c in query.edit_message_text.await_args_list]


def _cb(bot, chat, name, verb):
    return session_parity.session_cb(bot, chat, bot.registry._sessions[name], verb)


# =============================================================================
# (c) who did it: each state-changing result names the actor in a group
# =============================================================================

@pytest.fixture
def stop_bot(gbot, monkeypatch):
    """The real Stop core, its PTY and card plumbing recorded."""
    monkeypatch.setattr(inject, "send_keys", AsyncMock(return_value=True))
    monkeypatch.setattr(inject, "discard_queued_input", AsyncMock(return_value=True))
    gbot._settle_card_text = AsyncMock()
    gbot._settle_queued_targets = AsyncMock()
    gbot._stop_animation = MagicMock()
    gbot._cancel_lazy_card = MagicMock()
    gbot._mark_not_delivered = AsyncMock()
    gbot._discard_queued_targets = MagicMock()
    for name in (X1, D1):
        _sess(gbot, name).status = Status.BUSY
    return gbot


def test_busy_card_stop_in_a_group_names_who_stopped_it(stop_bot, run_async, mk_update):
    sess = _sess(stop_bot, X1)
    update, query = _tap(stop_bot, "x", BOB, message_id=1)
    sess.busy_msg_id = 1
    outcome = run_async(stop_bot._stop_session(sess, update=update, query=query))
    assert outcome.ok and outcome.by == "@bob"
    stop_bot._settle_card_text.assert_awaited_once_with(
        sess, "⚠️ <b>x1</b> · Stopped by @bob")
    assert _edit_texts(query) == ["⚠️ <b>x1</b> · Stopped by @bob"]
    assert _toasts(query) == ["⏹ Stopped x1 by @bob"]


def test_busy_card_stop_in_a_dm_is_unchanged(stop_bot, run_async):
    sess = _sess(stop_bot, D1)
    update, query = _tap(stop_bot, "x", ALICE, chat=DM, message_id=1)
    sess.busy_msg_id = 1
    outcome = run_async(stop_bot._stop_session(sess, update=update, query=query))
    assert outcome.ok and outcome.by == ""
    stop_bot._settle_card_text.assert_awaited_once_with(sess, "⚠️ <b>d1</b> · Stopped")
    assert _edit_texts(query) == ["⚠️ <b>d1</b> · Stopped"]
    assert _toasts(query) == ["⏹ Stopped d1"]


def test_stop_command_in_a_group_names_the_sender(stop_bot, run_async, mk_update):
    u = _msg(mk_update, "/stop", CAROL)
    run_async(stop_bot._stop_session(_sess(stop_bot, X1), update=u))
    assert _card(u)[0] == "⏹ Stopped <b>x1</b> by @carol"
    stop_bot._settle_card_text.assert_awaited_once_with(
        _sess(stop_bot, X1), "⚠️ <b>x1</b> · Stopped by @carol")


def test_stop_command_in_a_dm_is_unchanged(stop_bot, run_async, mk_update):
    u = _msg(mk_update, "/stop", ALICE, chat=DM)
    run_async(stop_bot._stop_session(_sess(stop_bot, D1), update=u))
    assert _card(u)[0] == "⏹ Stopped <b>d1</b>"
    stop_bot._settle_card_text.assert_awaited_once_with(
        _sess(stop_bot, D1), "⚠️ <b>d1</b> · Stopped")


def test_stop_line_carries_the_dropped_count_after_the_name():
    from aipager.bot.callbacks import _stopped_line
    out = StopOutcome(ok=True, label="x1", dropped=2, by="@bob")
    assert _stopped_line(out) == "⏹ Stopped <b>x1</b> by @bob (2 queued messages discarded)"
    out = StopOutcome(ok=True, label="x1", dropped=2)
    assert _stopped_line(out) == "⏹ Stopped <b>x1</b> (2 queued messages discarded)"


@pytest.mark.parametrize("chat,name,by", [(G, X1, "@bob"), (DM, D1, "")])
def test_picker_and_status_stop_pass_the_tapper(gbot, run_async, chat, name, by):
    """/status's ⏹ and the /stop picker stop through the core with the
    tapper named in a group, nobody in a DM."""
    sess = _sess(gbot, name)
    sess.status = Status.BUSY
    user = BOB if chat == G else ALICE
    gbot._stop_session_core = AsyncMock(
        return_value=StopOutcome(ok=True, label=sess.label, by=by))
    update, query = _tap(gbot, "x", user, chat=chat, message_id=3)
    run_async(gbot._stop_the_turn_shown(update, query, name, sess.turn_key, again="x"))
    gbot._stop_session_core.assert_awaited_once_with(sess, by=by)
    assert _toasts(query) == [f"⏹ Stopped {sess.label}" + (f" by {by}" if by else "")]


def _end(gbot, run_async, chat, name, user, message_id=500):
    gbot._kill_session_core = AsyncMock(return_value=SimpleNamespace(result="killed"))
    gbot._render_status_list = MagicMock(return_value=("LIST", None))
    sess = _sess(gbot, name)
    return _run_tap(gbot, run_async, _cb(gbot, chat, name, f"endok{sess.turn_key}"),
                    user, chat=chat, message_id=message_id)


def test_end_in_a_group_names_who_ended_it(gbot, run_async):
    query = _end(gbot, run_async, G, X1, BOB)
    gbot._kill_session_core.assert_awaited_once()
    assert _edit_texts(query) == ["⏹ Ended <b>x1</b> by @bob\n\nLIST"]
    assert _toasts(query) == ["⏹ Ended x1 by @bob"]


def test_end_in_a_dm_is_unchanged(gbot, run_async):
    query = _end(gbot, run_async, DM, D1, ALICE)
    assert _edit_texts(query) == ["⏹ Ended <b>d1</b>\n\nLIST"]
    assert _toasts(query) == ["⏹ Ended d1"]


def _restart(gbot, run_async, chat, name, user, message_id=501):
    gbot._restart_session_core = AsyncMock(return_value=SimpleNamespace(
        ok=True, label=_sess(gbot, name).label, reason="", err=""))
    sess = _sess(gbot, name)
    return _run_tap(gbot, run_async, _cb(gbot, chat, name, f"restartok{sess.turn_key}"),
                    user, chat=chat, message_id=message_id)


def test_restart_in_a_group_names_who_restarted_it(gbot, run_async):
    query = _restart(gbot, run_async, G, X2, CAROL)
    assert _edit_texts(query) == ["🔄 [<b>x2</b>] restarted by @carol."]


def test_restart_in_a_dm_is_unchanged(gbot, run_async):
    query = _restart(gbot, run_async, DM, D1, ALICE)
    assert _edit_texts(query) == ["🔄 [<b>d1</b>] restarted."]


def _delete(gbot, run_async, chat, name, user, message_id=502):
    _sess(gbot, name).status = Status.GONE
    return _run_tap(gbot, run_async, _cb(gbot, chat, name, "delete-confirm"),
                    user, chat=chat, message_id=message_id)


def test_delete_in_a_group_names_who_deleted_it(gbot, run_async):
    query = _delete(gbot, run_async, G, X2, BOB)
    assert X2 not in gbot.registry._sessions
    assert _edit_texts(query) == ["🗑️ Deleted [<b>x2</b>] by @bob."]


def test_delete_in_a_dm_is_unchanged(gbot, run_async):
    query = _delete(gbot, run_async, DM, D1, ALICE)
    assert _edit_texts(query) == ["🗑️ Deleted [<b>d1</b>]."]


def _mode_confirm(gbot, run_async, chat, name, user, *, card=503):
    """Ask -> Auto from the confirm card, tapped by *user* (an admin)."""
    sess = _sess(gbot, name)
    gbot._perms_pending[name] = {"target_skip_perms": True, "msg_id": card,
                                 "label": sess.label, "turn": sess.turn_key}

    async def _switch(s, target):
        s.skip_perms = target
        return SimpleNamespace(ok=True, reason="", err="", label=s.label)
    gbot._perms_switch_core = AsyncMock(side_effect=_switch)
    return _run_tap(gbot, run_async, _cb(gbot, chat, name, "perms_confirm"),
                    user, chat=chat, message_id=card)


def test_mode_switch_in_a_group_names_who_switched_it(gbot, run_async):
    query = _mode_confirm(gbot, run_async, G, X1, ALICE)
    final = _edit_texts(query)[-1]
    assert final.startswith("⚙️ Switched to 🤖 Auto by @alice.\n\n")
    assert final.endswith("<b>x1</b> is 🤖 Auto.")


def test_mode_switch_in_a_dm_is_unchanged(gbot, run_async):
    query = _mode_confirm(gbot, run_async, DM, D1, ALICE)
    text, _kb = gbot._render_mode_card(DM, _sess(gbot, D1))
    assert _edit_texts(query)[-1] == text
    assert "Switched" not in text


@pytest.mark.parametrize("chat,name,user,note", [
    (G, X1, BOB, "⚙️ Switched to 💬 Ask by @bob.\n\n"), (DM, D1, ALICE, "")])
def test_typed_switch_to_ask_names_the_sender(gbot, run_async, mk_update, chat, name, user,
                                              note):
    sess = _sess(gbot, name)
    sess.skip_perms = True

    async def _switch(s, target):
        s.skip_perms = target
        return SimpleNamespace(ok=True, reason="", err="", label=s.label)
    gbot._perms_switch_core = AsyncMock(side_effect=_switch)
    u = _msg(mk_update, f"/mode {sess.label} ask", user, chat=chat)
    reply = MagicMock(message_id=777)
    reply.chat = MagicMock(id=chat)
    reply.edit_text = AsyncMock()
    u.message.reply_text = AsyncMock(return_value=reply)
    run_async(gbot._handle_mode_cmd(u, MagicMock()))
    assert u.message.reply_text.await_args.args[0] == (
        f"⚙️ Switching <b>{sess.label}</b> to 💬 Ask mode…")
    card, _kb = gbot._render_mode_card(chat, sess)
    assert reply.edit_text.await_args.args[0] == note + card


def _ready_tap(gbot, run_async, chat, name, user, verb):
    sess = _sess(gbot, name)
    gbot._switch_model_core = AsyncMock(return_value=SimpleNamespace(ok=True, detail=""))

    async def _switch(s, target):
        s.skip_perms = target
        return SimpleNamespace(ok=True, reason="", err="", label=s.label)
    gbot._perms_switch_core = AsyncMock(side_effect=_switch)
    gbot._app.bot.edit_message_text = AsyncMock()
    _run_tap(gbot, run_async, _cb(gbot, chat, name, verb), user, chat=chat,
             message_id=sess.busy_msg_id or 600)
    return gbot._app.bot.edit_message_text.await_args.kwargs["text"]


def test_ready_card_model_switch_names_the_tapper_in_a_group(gbot, run_async):
    from aipager.config import MODEL_CHOICES
    text = _ready_tap(gbot, run_async, G, X1, BOB, "rdy_m0")
    assert f"🔁 Model switched to {MODEL_CHOICES[0][0]} by @bob." in text


def test_ready_card_model_switch_in_a_dm_is_unchanged(gbot, run_async):
    text = _ready_tap(gbot, run_async, DM, D1, ALICE, "rdy_m0")
    assert "🔁" not in text and " by @" not in text


def test_ready_card_mode_switch_names_the_tapper_in_a_group(gbot, run_async):
    _sess(gbot, X1).skip_perms = True
    text = _ready_tap(gbot, run_async, G, X1, BOB, "rdy_ask")
    assert "🔁 Switched to 💬 Ask by @bob." in text
    _sess(gbot, D1).skip_perms = True
    text = _ready_tap(gbot, run_async, DM, D1, ALICE, "rdy_ask")
    assert "🔁" not in text


@pytest.fixture
def no_pref_writes(monkeypatch):
    monkeypatch.setattr(preferences, "set_preference", MagicMock())
    monkeypatch.setattr(preferences, "set_new_session_default", MagicMock())


def test_settings_change_in_a_group_names_who_changed_it(gbot, run_async, no_pref_writes):
    from aipager.bot.settings_menu import render_settings_section
    query = _run_tap(gbot, run_async, "_:set:layout:merged", ALICE, message_id=700)
    expected, _kb = render_settings_section(G, "layout")
    assert _edit_texts(query) == [expected + "\n\n<i>Changed by @alice.</i>"]


def test_settings_change_in_a_dm_is_unchanged(gbot, run_async, no_pref_writes):
    from aipager.bot.settings_menu import render_settings_section
    query = _run_tap(gbot, run_async, "_:set:layout:merged", ALICE, chat=DM, message_id=700)
    expected, _kb = render_settings_section(DM, "layout")
    assert _edit_texts(query) == [expected]


@pytest.mark.parametrize("chat,suffix", [(G, "\n\n<i>Changed by @alice.</i>"), (DM, "")])
def test_new_session_defaults_change_names_who_changed_it(gbot, run_async, no_pref_writes,
                                                         chat, suffix):
    gbot._app.bot.edit_message_text = AsyncMock()
    _run_tap(gbot, run_async, "_:set:ns:mode:ask", ALICE, chat=chat, message_id=701)
    text = gbot._app.bot.edit_message_text.await_args.kwargs["text"]
    expected, _kb = new_flow.render_new_session_defaults(gbot, chat, viewer=ALICE)
    assert text == expected + suffix


def test_actor_label_falls_back_to_the_telegram_handle(gbot):
    assert gbot._actor_label(BOB, G) == "@bob"
    assert gbot._actor_label(4040, G, SimpleNamespace(username="zed")) == "@zed"
    assert gbot._actor_label(4040, G, SimpleNamespace(username=None)) == "@unknown"
    assert gbot._actor_label(BOB, DM) == ""
    assert gbot._actor_label(ALICE, DM) == ""


# =============================================================================
# (c) confirm cards belong to whoever asked for them (D-D)
# =============================================================================

def _track_sent(update):
    """Make *update*'s replies return messages with known ids."""
    ids = []

    def _reply(*a, **k):
        mid = next(_SENT_ID)
        ids.append(mid)
        return MagicMock(message_id=mid)
    update.message.reply_text = AsyncMock(side_effect=_reply)
    return ids


def _kill_card(gbot, run_async, mk_update, user, text="/kill x1", chat=G):
    u = _msg(mk_update, text, user, chat=chat)
    ids = _track_sent(u)
    run_async(gbot._handle_kill_cmd(u, MagicMock()))
    return ids[-1]


def test_another_members_end_tap_on_a_kill_card_is_refused(gbot, run_async, mk_update):
    card = _kill_card(gbot, run_async, mk_update, ALICE)
    gbot._kill_session_core = AsyncMock(return_value=SimpleNamespace(result="killed"))
    gbot._render_status_list = MagicMock(return_value=("LIST", None))
    turn = _sess(gbot, X1).turn_key
    q = _run_tap(gbot, run_async, _cb(gbot, G, X1, f"endok{turn}"), BOB, message_id=card)
    assert _toasts(q) == ["This is @alice's card. Send /kill x1 for your own."]
    gbot._kill_session_core.assert_not_awaited()
    q.edit_message_text.assert_not_awaited()
    q = _run_tap(gbot, run_async, _cb(gbot, G, X1, "kill-cancel"), BOB, message_id=card)
    assert _toasts(q) == ["This is @alice's card. Send /kill x1 for your own."]
    q.edit_message_text.assert_not_awaited()
    # The requester confirms.
    q = _run_tap(gbot, run_async, _cb(gbot, G, X1, f"endok{turn}"), ALICE, message_id=card)
    gbot._kill_session_core.assert_awaited_once()
    assert _edit_texts(q) == ["⏹ Ended <b>x1</b> by @alice\n\nLIST"]


def test_the_requester_cancels_their_own_kill_card(gbot, run_async, mk_update):
    card = _kill_card(gbot, run_async, mk_update, ALICE)
    q = _run_tap(gbot, run_async, _cb(gbot, G, X1, "kill-cancel"), ALICE, message_id=card)
    assert _edit_texts(q) == ["↩️ Cancelled. Nothing was ended."]
    assert not card_owner.refusal(gbot, G, card, BOB, X1, "kill-cancel")


def test_a_kill_card_in_a_dm_records_nobody(gbot, run_async, mk_update):
    card = _kill_card(gbot, run_async, mk_update, ALICE, text="/kill d1", chat=DM)
    assert not getattr(gbot, "_card_owners", None)
    gbot._kill_session_core = AsyncMock(return_value=SimpleNamespace(result="killed"))
    gbot._render_status_list = MagicMock(return_value=("LIST", None))
    turn = _sess(gbot, D1).turn_key
    q = _run_tap(gbot, run_async, _cb(gbot, DM, D1, f"endok{turn}"), ALICE, chat=DM,
                 message_id=card)
    assert _edit_texts(q) == ["⏹ Ended <b>d1</b>\n\nLIST"]


def test_the_menus_end_confirm_is_whoever_tapped_end(gbot, run_async):
    menu = 800
    _run_tap(gbot, run_async, _cb(gbot, G, X1, "end"), BOB, message_id=menu)
    gbot._kill_session_core = AsyncMock(return_value=SimpleNamespace(result="killed"))
    turn = _sess(gbot, X1).turn_key
    q = _run_tap(gbot, run_async, _cb(gbot, G, X1, f"endok{turn}"), ALICE, message_id=menu)
    assert _toasts(q) == ["This is @bob's card. Send /kill x1 for your own."]
    gbot._kill_session_core.assert_not_awaited()


def test_kill_picker_rows_and_cancel_answer_only_the_sender(gbot, run_async, mk_update):
    card = _kill_card(gbot, run_async, mk_update, ALICE, text="/kill")
    q = _run_tap(gbot, run_async, _cb(gbot, G, X2, "end"), BOB, message_id=card)
    assert _toasts(q) == ["This is @alice's card. Send /kill for your own."]
    q.edit_message_text.assert_not_awaited()
    q = _run_tap(gbot, run_async, "_:pick:cancel", BOB, message_id=card)
    assert _toasts(q) == ["This is @alice's card. Send /kill for your own."]
    q.edit_message_text.assert_not_awaited()
    # alice's own row opens the confirm, which is hers.
    q = _run_tap(gbot, run_async, _cb(gbot, G, X2, "end"), ALICE, message_id=card)
    assert _edit_texts(q)[0].startswith("⏹ End <b>x2</b>?")
    assert card_owner.refusal(gbot, G, card, BOB, X2, f"endok{_sess(gbot, X2).turn_key}") == (
        "This is @alice's card. Send /kill x2 for your own.")


def test_restart_card_answers_only_the_sender(gbot, run_async, mk_update):
    u = _msg(mk_update, "/restart x1", ALICE)
    ids = _track_sent(u)
    run_async(session_parity.handle_restart_cmd(gbot, u, MagicMock()))
    gbot._restart_session_core = AsyncMock(return_value=SimpleNamespace(
        ok=True, label="x1", reason="", err=""))
    turn = _sess(gbot, X1).turn_key
    for verb in (f"restartok{turn}", "restart-cancel"):
        q = _run_tap(gbot, run_async, _cb(gbot, G, X1, verb), BOB, message_id=ids[-1])
        assert _toasts(q) == ["This is @alice's card. Send /restart x1 for your own."]
        q.edit_message_text.assert_not_awaited()
    gbot._restart_session_core.assert_not_awaited()
    q = _run_tap(gbot, run_async, _cb(gbot, G, X1, f"restartok{turn}"), ALICE,
                 message_id=ids[-1])
    gbot._restart_session_core.assert_awaited_once()


def test_restart_picker_answers_only_the_sender(gbot, run_async, mk_update):
    u = _msg(mk_update, "/restart", ALICE)
    ids = _track_sent(u)
    run_async(session_parity.handle_restart_cmd(gbot, u, MagicMock()))
    q = _run_tap(gbot, run_async, _cb(gbot, G, X1, "restart"), BOB, message_id=ids[-1])
    assert _toasts(q) == ["This is @alice's card. Send /restart for your own."]
    q.edit_message_text.assert_not_awaited()


def test_menu_restart_confirm_is_whoever_tapped_restart(gbot, run_async):
    _run_tap(gbot, run_async, _cb(gbot, G, X1, "restart"), CAROL, message_id=801)
    q = _run_tap(gbot, run_async, _cb(gbot, G, X1, "restart-cancel"), BOB, message_id=801)
    assert _toasts(q) == ["This is @carol's card. Send /restart x1 for your own."]


def test_delete_card_answers_only_the_sender(gbot, run_async, mk_update):
    _sess(gbot, X2).status = Status.GONE
    u = _msg(mk_update, "/delete x2", ALICE)
    ids = _track_sent(u)
    run_async(session_parity.handle_delete_cmd(gbot, u, MagicMock()))
    for verb in ("delete-confirm", "delete-cancel"):
        q = _run_tap(gbot, run_async, _cb(gbot, G, X2, verb), BOB, message_id=ids[-1])
        assert _toasts(q) == ["This is @alice's card. Send /delete x2 for your own."]
        q.edit_message_text.assert_not_awaited()
    assert X2 in gbot.registry._sessions
    _run_tap(gbot, run_async, _cb(gbot, G, X2, "delete-confirm"), ALICE, message_id=ids[-1])
    assert X2 not in gbot.registry._sessions


def test_delete_picker_and_menu_delete_answer_only_their_requester(gbot, run_async,
                                                                   mk_update):
    _sess(gbot, X1).status = Status.GONE
    _sess(gbot, X2).status = Status.GONE
    u = _msg(mk_update, "/delete", ALICE)
    ids = _track_sent(u)
    run_async(session_parity.handle_delete_cmd(gbot, u, MagicMock()))
    q = _run_tap(gbot, run_async, _cb(gbot, G, X1, "delete"), BOB, message_id=ids[-1])
    assert _toasts(q) == ["This is @alice's card. Send /delete for your own."]
    _run_tap(gbot, run_async, _cb(gbot, G, X2, "delete"), BOB, message_id=802)
    q = _run_tap(gbot, run_async, _cb(gbot, G, X2, "delete-confirm"), ALICE, message_id=802)
    assert _toasts(q) == ["This is @bob's card. Send /delete x2 for your own."]
    assert X2 in gbot.registry._sessions


def _mode_auto_card(gbot, run_async, mk_update, user=ALICE):
    """alice sends /mode x1 auto: the confirm card is hers."""
    u = _msg(mk_update, "/mode x1 auto", user)
    ids = _track_sent(u)
    run_async(gbot._handle_mode_cmd(u, MagicMock()))
    assert gbot._perms_pending[X1]["msg_id"] == ids[-1]
    return ids[-1]


def test_mode_confirm_answers_only_the_sender(gbot, run_async, mk_update):
    card = _mode_auto_card(gbot, run_async, mk_update)
    gbot._perms_switch_core = AsyncMock()
    record = dict(gbot._perms_pending[X1])
    # dave is an admin: without the card's owner on record he could switch it.
    for verb in ("perms_confirm", "perms_cancel"):
        q = _run_tap(gbot, run_async, _cb(gbot, G, X1, verb), DAVE, message_id=card)
        assert _toasts(q) == ["This is @alice's card. Send /mode x1 for your own."]
        q.edit_message_text.assert_not_awaited()
    assert gbot._perms_pending[X1] == record
    gbot._perms_switch_core.assert_not_awaited()
    # alice cancels: the mode card again, and the card is nobody's now.
    _run_tap(gbot, run_async, _cb(gbot, G, X1, "perms_cancel"), ALICE, message_id=card)
    assert X1 not in gbot._perms_pending
    assert card_owner.refusal(gbot, G, card, BOB, X1, "perms_cancel") is None


def test_busy_mode_confirm_answers_only_the_sender(gbot, run_async, mk_update):
    _sess(gbot, X1).status = Status.BUSY
    card = _mode_auto_card(gbot, run_async, mk_update)
    for verb in ("perms_stop_switch", "perms_wait"):
        q = _run_tap(gbot, run_async, _cb(gbot, G, X1, verb), DAVE, message_id=card)
        assert _toasts(q) == ["This is @alice's card. Send /mode x1 for your own."]


def test_mode_picker_answers_only_the_sender_and_the_mode_card_is_everyones(
        gbot, run_async, mk_update):
    u = _msg(mk_update, "/mode", ALICE)
    ids = _track_sent(u)
    run_async(gbot._handle_mode_cmd(u, MagicMock()))
    q = _run_tap(gbot, run_async, _cb(gbot, G, X1, "mode_show"), BOB, message_id=ids[-1])
    assert _toasts(q) == ["This is @alice's card. Send /mode for your own."]
    q.edit_message_text.assert_not_awaited()
    _run_tap(gbot, run_async, _cb(gbot, G, X1, "mode_show"), ALICE, message_id=ids[-1])
    # The mode card it became is everyone's: dave's switch draws HIS confirm.
    turn = _sess(gbot, X1).turn_key
    q = _run_tap(gbot, run_async, _cb(gbot, G, X1, f"modeauto{turn}"), DAVE,
                 message_id=ids[-1])
    assert gbot._perms_pending.get(X1, {}).get("msg_id") == ids[-1]
    assert card_owner.refusal(gbot, G, ids[-1], ALICE, X1, "perms_confirm") == (
        "This is @dave's card. Send /mode x1 for your own.")


def test_a_mode_card_switch_draws_a_confirm_owned_by_the_tapper(gbot, run_async):
    turn = _sess(gbot, X1).turn_key
    _run_tap(gbot, run_async, _cb(gbot, G, X1, f"modeauto{turn}"), ALICE, message_id=900)
    assert gbot._perms_pending[X1]["msg_id"] == 900
    q = _run_tap(gbot, run_async, _cb(gbot, G, X1, "perms_confirm"), DAVE, message_id=900)
    assert _toasts(q) == ["This is @alice's card. Send /mode x1 for your own."]


def test_card_owner_records_nothing_in_a_dm_and_refuses_nothing_there(gbot):
    card_owner.claim(gbot, DM, 5, ALICE, kind="end", command="/kill d1")
    assert not getattr(gbot, "_card_owners", None)
    gbot._card_owners = {(DM, 5): {"user_id": ALICE, "label": "@alice", "kind": "end",
                                   "command": "/kill d1", "picker": False}}
    assert card_owner.refusal(gbot, DM, 5, 999, D1, "kill-cancel") is None


def test_card_owner_covers_only_its_cards_buttons(gbot):
    card_owner.claim(gbot, G, 5, ALICE, kind="end", command="/kill x1")
    assert card_owner.refusal(gbot, G, 5, BOB, X1, "endok3")
    assert card_owner.refusal(gbot, G, 5, BOB, X1, "kill-cancel")
    assert card_owner.refusal(gbot, G, 5, BOB, X1, "talk") is None
    assert card_owner.refusal(gbot, G, 5, BOB, X1, "menu") is None
    assert card_owner.refusal(gbot, G, 6, BOB, X1, "endok3") is None
    assert card_owner.refusal(gbot, G, 5, ALICE, X1, "endok3") is None
    card_owner.release(gbot, G, 5)
    assert card_owner.refusal(gbot, G, 5, BOB, X1, "endok3") is None


def test_card_owner_keeps_a_bounded_number_of_records(gbot, monkeypatch):
    monkeypatch.setattr(card_owner, "MAX_RECORDS", 3)
    for mid in range(5):
        card_owner.claim(gbot, G, mid, ALICE, kind="end", command="/kill x1")
    assert list(gbot._card_owners) == [(G, 2), (G, 3), (G, 4)]


# =============================================================================
# (d) /app in a group, and the menus
# =============================================================================

def test_app_in_a_group_for_a_member_with_a_dm(gbot, run_async, mk_update):
    u = _msg(mk_update, "/app", ALICE)
    run_async(gbot._handle_app_cmd(u, MagicMock()))
    assert _card(u)[0] == handlers.APP_IN_GROUP_TEXT
    assert handlers.APP_IN_GROUP_TEXT == ("📱 The Mini App only works in a private chat - "
                                          "DM the bot and send /app there.")


def test_app_in_a_group_for_a_member_without_a_dm(gbot, run_async, mk_update):
    u = _msg(mk_update, "/app", BOB)
    run_async(gbot._handle_app_cmd(u, MagicMock()))
    assert _card(u)[0] == ("The Mini App opens from your own chat with the bot. "
                           "Ask the operator to add you.")


def test_app_in_a_personal_mode_group_keeps_its_text(mk_bot, run_async, mk_update):
    bot = mk_bot()
    u = _msg(mk_update, "/app", BOB)
    run_async(bot._handle_app_cmd(u, MagicMock()))
    assert _card(u)[0] == handlers.APP_IN_GROUP_TEXT


def test_dm_scope_needs_the_member_in_their_own_dm(mk_bot):
    from aipager.policy import load_policy
    bot = mk_bot(scopes=_scopes() + [Scope(chat_id=CAROL, kind="dm", label="c", members=(
        Member(id=BOB, label="bob", role="owner"),))])
    bot.policy = load_policy()
    assert bot._has_dm_scope(ALICE)
    assert not bot._has_dm_scope(CAROL)     # a DM scope that does not list her
    assert not bot._has_dm_scope(BOB)
    assert not bot._has_dm_scope(G)
    assert not bot._has_dm_scope(None)


def test_group_menus_have_no_app_and_dm_menus_keep_it(gbot, run_async, monkeypatch):
    monkeypatch.setattr(config, "MINIAPP_ENABLED", True)
    gbot._app.bot.set_my_commands = AsyncMock()
    gbot._registered_scope_labels = {}
    run_async(type(gbot)._update_bot_commands_per_scope(gbot))
    menus = {c.kwargs["scope"].chat_id: [b.command for b in c.args[0]]
             for c in gbot._app.bot.set_my_commands.await_args_list}
    assert "app" not in menus[G]
    assert menus[DM] == ["d1"] + [c for c in menus[G] if c not in ("x1", "x2")] + ["app"]


@pytest.mark.parametrize("chat_id,has_app", [("-1001", False), ("11", True)])
def test_global_menu_follows_the_configured_chat(mk_bot, run_async, monkeypatch,
                                                 chat_id, has_app):
    monkeypatch.setattr(config, "MINIAPP_ENABLED", True)
    monkeypatch.setattr(config, "CHAT_ID", chat_id)
    bot = mk_bot()
    bot._app.bot.set_my_commands = AsyncMock()
    bot._registered_labels = None
    run_async(type(bot)._update_bot_commands_global(bot))
    commands = [b.command for b in bot._app.bot.set_my_commands.await_args.args[0]]
    assert ("app" in commands) is has_app


def test_command_list_drops_app_only_for_groups(monkeypatch):
    from aipager.bot.lifecycle import LifecycleMixin
    monkeypatch.setattr(config, "MINIAPP_ENABLED", True)
    dm = [c.command for c in LifecycleMixin._command_list({"x1"})]
    group = [c.command for c in LifecycleMixin._command_list({"x1"}, group=True)]
    assert dm == group + ["app"]


# =============================================================================
# (e) anonymous admins and channel posts
# =============================================================================

def _anon_msg(text="/status", *, chat_id=G, sender_chat_id=G, from_id=ANON, mid=1):
    chat = Chat(chat_id, "supergroup")
    sender_chat = Chat(sender_chat_id, "supergroup") if sender_chat_id is not None else None
    return Message(mid, DATE, chat, from_user=User(from_id, "Group", from_id == ANON),
                   sender_chat=sender_chat, text=text)


@pytest.fixture
def replies(monkeypatch):
    out = []

    async def _reply(msg, text, **kw):
        out.append((msg.chat_id, text))
        return MagicMock(message_id=1)
    monkeypatch.setattr(transport, "reply_text", _reply)
    return out


@pytest.fixture
def no_pending(monkeypatch, tmp_path):
    path = tmp_path / "pending.json"
    monkeypatch.setattr(team, "PENDING_USERS_PATH", path)
    return path


def _gate(bot, run_async, msg):
    try:
        run_async(group_intake.intake_gate(bot, Update(1, message=msg)))
    except ApplicationHandlerStop:
        return False
    return True


def test_anonymous_admin_is_told_once_per_chat_and_nothing_else(
        gbot, run_async, replies, no_pending):
    gbot.scopes = _scopes() + [Scope(chat_id=OTHER_G, kind="group", label="other",
                                     members=(Member(id=ALICE, label="alice", role="owner"),))]
    assert not _gate(gbot, run_async, _anon_msg())
    assert not _gate(gbot, run_async, _anon_msg("/x1 fix it", mid=2))
    assert not _gate(gbot, run_async, _anon_msg("/status", from_id=CHANNEL_BOT, mid=3))
    assert replies == [(G, "Post as yourself to use the bot.")]
    assert not _gate(gbot, run_async, _anon_msg(chat_id=OTHER_G, sender_chat_id=OTHER_G))
    assert replies[-1] == (OTHER_G, "Post as yourself to use the bot.")
    assert len(replies) == 2
    assert not no_pending.exists()
    gbot._inject_prompt.assert_not_awaited()


def test_a_linked_channel_forward_mentioning_nothing_is_silent(gbot, run_async, replies):
    assert not _gate(gbot, run_async, _anon_msg("new post", sender_chat_id=-100777,
                                                from_id=SERVICE))
    assert replies == []


def test_anonymous_sender_in_an_unconfigured_group_gets_no_reply(gbot, run_async, replies):
    assert not _gate(gbot, run_async, _anon_msg(chat_id=-100555, sender_chat_id=-100555))
    assert replies == []


def test_a_shared_id_without_sender_chat_is_anonymous_too(gbot, run_async, replies):
    assert not _gate(gbot, run_async, _anon_msg(sender_chat_id=None, from_id=SERVICE))
    assert replies == [(G, "Post as yourself to use the bot.")]


def test_a_member_in_a_group_still_passes(gbot, run_async, replies):
    msg = Message(1, DATE, Chat(G, "supergroup"), from_user=User(BOB, "bob", False),
                  text="/status")
    assert _gate(gbot, run_async, msg)
    assert replies == []


def test_team_mode_group_tells_the_anonymous_admin(mk_bot, run_async, replies,
                                                   monkeypatch):
    monkeypatch.setattr(config, "CHAT_ID", str(G))
    bot = mk_bot(team=MagicMock())
    assert not _gate(bot, run_async, _anon_msg())
    assert not _gate(bot, run_async, _anon_msg(chat_id=OTHER_G, sender_chat_id=OTHER_G))
    assert replies == [(G, "Post as yourself to use the bot.")]


def test_personal_mode_is_unchanged_for_an_anonymous_admin(mk_bot, run_async, replies,
                                                           monkeypatch):
    """Personal mode admits everyone its chat lets in as the operator; an
    anonymous admin there (often the operator) is handled as before."""
    monkeypatch.setattr(config, "CHAT_ID", str(G))
    bot = mk_bot()
    assert _gate(bot, run_async, _anon_msg())
    assert replies == []
    assert run_async(bot._authorize(_auth_update())) is True


def _auth_update(*, sender_chat_id=G, from_id=ANON, chat=G):
    u = MagicMock()
    u.effective_chat = MagicMock(id=chat, type="supergroup")
    u.effective_user = MagicMock(id=from_id, username="GroupAnonymousBot",
                                 first_name="Group", last_name="")
    u.effective_message = MagicMock()
    u.effective_message.sender_chat = (SimpleNamespace(id=sender_chat_id)
                                       if sender_chat_id is not None else None)
    u.effective_message.from_user = u.effective_user
    u.effective_message.reply_text = AsyncMock()
    u.message = u.effective_message
    return u


@pytest.mark.parametrize("mode", ["scopes", "team"])
@pytest.mark.parametrize("sender_chat_id,from_id", [(G, ANON), (None, SERVICE),
                                                    (-100777, 4242)])
def test_authorize_refuses_an_anonymous_sender_silently(
        mk_bot, run_async, no_pending, monkeypatch, mode, sender_chat_id, from_id):
    from aipager.bot import auth
    from aipager.policy import load_policy
    recorded = MagicMock()
    monkeypatch.setattr(auth, "record_pending_user", recorded)
    team_obj = None
    if mode == "team":
        team_obj = MagicMock()
        team_obj.get = MagicMock(return_value=None)
    bot = mk_bot(scopes=_scopes() if mode == "scopes" else None, team=team_obj)
    bot.policy = load_policy()
    u = _auth_update(sender_chat_id=sender_chat_id, from_id=from_id)
    assert run_async(bot._authorize(u)) is False
    recorded.assert_not_called()
    u.effective_message.reply_text.assert_not_awaited()


def test_authorize_still_records_a_real_non_member(mk_bot, run_async, no_pending, monkeypatch):
    from aipager.bot import auth
    from aipager.policy import load_policy
    team.reset_unauthorized_seen()
    recorded = MagicMock()
    monkeypatch.setattr(auth, "record_pending_user", recorded)
    bot = mk_bot(scopes=_scopes())
    bot.policy = load_policy()
    u = _auth_update(sender_chat_id=None, from_id=4242)
    assert run_async(bot._authorize(u)) is False
    recorded.assert_called_once()
    team.reset_unauthorized_seen()


@pytest.mark.parametrize("uid", [ANON, CHANNEL_BOT, SERVICE])
def test_record_pending_user_never_records_a_shared_id(no_pending, uid):
    team.record_pending_user(uid, username="GroupAnonymousBot", chat_id=G)
    assert not no_pending.exists()
    team.record_pending_user(4242, username="real", chat_id=G)
    assert [r["user_id"] for r in json.loads(no_pending.read_text())] == [4242]


def test_is_shared_sender_id():
    assert all(team.is_shared_sender_id(i) for i in (ANON, CHANNEL_BOT, SERVICE))
    assert not team.is_shared_sender_id(4242)
    assert not team.is_shared_sender_id(True)
    assert not team.is_shared_sender_id(str(ANON))


def test_wizard_auto_detect_skips_anonymous_and_channel_messages(monkeypatch):
    from aipager.wizard import telegram_api
    updates = [
        {"message": {"chat": {"id": G, "type": "supergroup"},
                     "from": {"id": ANON, "username": "GroupAnonymousBot"},
                     "sender_chat": {"id": G}}},
        {"message": {"chat": {"id": G, "type": "supergroup"},
                     "from": {"id": SERVICE, "first_name": "Telegram"}}},
        {"message": {"chat": {"id": G, "type": "supergroup"},
                     "from": {"id": 4242, "username": "bob"},
                     "sender_chat": {"id": -100777}}},
        {"message": {"chat": {"id": G, "type": "supergroup"},
                     "from": {"id": 5151, "username": "carol"}}},
    ]
    monkeypatch.setattr(telegram_api, "_http_json",
                        lambda url: ({"ok": True, "result": updates}, 200, ""))
    uid, who, _adv = telegram_api._fetch_id_from_updates("tok", want="user")
    assert (uid, who) == (5151, "carol")


def _stub_ask(monkeypatch, module, answers):
    queue = iter(answers)

    def _ask(prompt):
        try:
            return next(queue)
        except StopIteration:
            raise KeyboardInterrupt("ran out of canned answers")
    monkeypatch.setattr(module, "_ask", _ask)


@pytest.fixture
def wizard(monkeypatch):
    import contextlib
    from aipager.wizard import team_setup
    monkeypatch.setattr(team_setup, "_spin", lambda msg: contextlib.nullcontext())
    monkeypatch.setattr(team_setup, "_resolve_user", lambda t, q: None)
    return team_setup


@pytest.mark.parametrize("uid", [ANON, CHANNEL_BOT, SERVICE])
def test_wizard_rejects_a_typed_shared_id(wizard, monkeypatch, uid):
    warned = []
    monkeypatch.setattr(wizard, "friendly_warn", lambda *a: warned.append(a[0]))
    _stub_ask(monkeypatch, wizard, ["manual", str(uid), "5151", "carol"])
    out = wizard._capture_user_identity(1, existing_ids=set(), existing_labels=set(),
                                        token="tok")
    assert out == {"id": 5151, "label": "carol"}
    assert warned == [wizard.SHARED_SENDER_REFUSAL]


def test_wizard_rejects_an_auto_detected_shared_id(wizard, monkeypatch):
    # The third answer would be "is this the person?" if the shared id
    # were offered (it must not be), else the second "continue?".
    _stub_ask(monkeypatch, wizard, ["auto", True, True, True, ""])
    found = iter([(ANON, "GroupAnonymousBot", None), (5151, "carol", None)])
    monkeypatch.setattr(wizard, "_detect_id", lambda t, *, want, **k: next(found))
    printed = []
    monkeypatch.setattr(wizard.err_console, "print", lambda s, *a, **k: printed.append(s))
    out = wizard._capture_user_identity(1, existing_ids=set(), existing_labels=set(),
                                        token="tok")
    assert out == {"id": 5151, "label": "carol"}
    assert any(wizard.SHARED_SENDER_REFUSAL in p for p in printed)


# =============================================================================
# (g) a person whose own session has ended is asked, not routed
# =============================================================================

def _send(bot, run_async, update):
    run_async(bot._handle_message(update, MagicMock()))


def _buttons(bot, chat, markup):
    out = []
    for row in markup.inline_keyboard:
        for b in row:
            if b.callback_data == "_:pick:cancel":
                out.append((b.text, "cancel"))
                continue
            _s, rest = b.callback_data.split(":", 1)
            _kind, idx, verb = rest.split(":", 2)
            out.append((b.text, session_parity._resolve_pref_index(bot, chat, idx).name,
                        verb))
    return out


def test_own_session_ended_asks_which_with_resume_and_routes_nothing(
        gbot, run_async, mk_update):
    gbot.registry.set_target(X2, G, BOB)
    x2 = _sess(gbot, X2)
    x2.status = Status.GONE
    x2.claude_session_id = "UUID-2"
    assert gbot.registry.target_for(G, BOB) is None
    u = _msg(mk_update, "please continue", BOB)
    _send(gbot, run_async, u)
    gbot._inject_prompt.assert_not_awaited()
    text, markup = _card(u)
    assert text == "x2 has ended. Which session?"
    assert _buttons(gbot, G, markup) == [
        ("✍️ x1", X1, "talk"), ("▶️ Resume x2", X2, "resume"), ("✖️ Cancel", "cancel")]
    # The Resume button is the ⋮ menu's own verb: it opens the Ask/Auto step.
    cb = markup.inline_keyboard[1][0].callback_data
    q = _run_tap(gbot, run_async, cb, BOB, message_id=950)
    assert _edit_texts(q) == ["Resume <b>x2</b> as:"]


def test_own_session_ended_without_a_transcript_offers_no_resume(gbot, run_async, mk_update):
    gbot.registry.set_target(X2, G, BOB)
    _sess(gbot, X2).status = Status.GONE
    u = _msg(mk_update, "hi", BOB)
    _send(gbot, run_async, u)
    text, markup = _card(u)
    assert text == "x2 has ended. Which session?"
    assert _buttons(gbot, G, markup) == [("✍️ x1", X1, "talk"), ("✖️ Cancel", "cancel")]
    gbot._inject_prompt.assert_not_awaited()


def test_own_session_ended_and_nothing_live_still_asks(gbot, run_async, mk_update):
    gbot.registry.set_target(X2, G, BOB)
    for name in (X1, X2):
        _sess(gbot, name).status = Status.GONE
    _sess(gbot, X2).claude_session_id = "UUID-2"
    u = _msg(mk_update, "hi", BOB)
    _send(gbot, run_async, u)
    text, markup = _card(u)
    assert text == "x2 has ended. Which session?"
    assert _buttons(gbot, G, markup) == [("▶️ Resume x2", X2, "resume"),
                                         ("✖️ Cancel", "cancel")]


def test_never_picked_person_still_gets_the_only_live_session(gbot, run_async, mk_update):
    _sess(gbot, X2).status = Status.GONE
    u = _msg(mk_update, "hello", CAROL)
    _send(gbot, run_async, u)
    assert [c.args[0].label for c in gbot._inject_prompt.await_args_list] == ["x1"]


def test_ended_target_is_per_person_and_group_only(gbot):
    gbot.registry.set_target(X2, G, BOB)
    _sess(gbot, X2).status = Status.GONE
    assert gbot.registry.ended_target_for(G, BOB) is _sess(gbot, X2)
    assert gbot.registry.ended_target_for(G, CAROL) is None
    assert gbot.registry.target_for(G, CAROL) is _sess(gbot, X1)
    # Picking another session ends the question.
    gbot.registry.set_target(X1, G, BOB)
    assert gbot.registry.ended_target_for(G, BOB) is None
    assert gbot.registry.target_for(G, BOB) is _sess(gbot, X1)
    # A DM never has one, whatever its target is.
    gbot.registry.set_target(D1, DM, ALICE)
    _sess(gbot, D1).status = Status.GONE
    assert gbot.registry.ended_target_for(DM, ALICE) is None
    gbot.registry._user_targets[(DM, ALICE)] = (D1, 99)     # never written for a DM
    assert gbot.registry.ended_target_for(DM, ALICE) is None


def test_dm_with_an_ended_target_is_unchanged(gbot, run_async, mk_update):
    """A private chat never asks "has ended": its target rules are 4.10's."""
    gbot.registry.track_message(101, D1, DM)
    _sess(gbot, D1).status = Status.GONE
    u = _msg(mk_update, "hello", ALICE, chat=DM)
    _send(gbot, run_async, u)
    assert all("has ended. Which session?" not in str(c.args)
               for c in u.message.reply_text.await_args_list)


def test_a_talk_tap_after_the_question_routes_the_next_message(gbot, run_async, mk_update):
    gbot.registry.set_target(X2, G, BOB)
    _sess(gbot, X2).status = Status.GONE
    _run_tap(gbot, run_async, _cb(gbot, G, X1, "talk"), BOB, message_id=951)
    gbot._render_status_list = MagicMock(return_value=("LIST", None))
    u = _msg(mk_update, "go on", BOB)
    _send(gbot, run_async, u)
    assert [c.args[0].label for c in gbot._inject_prompt.await_args_list] == ["x1"]


# =============================================================================
# follow-up: an unstamped session's note carries its home chat's deny_tools
# =============================================================================

def test_unstamped_sessions_note_carries_the_home_chats_deny_tools(
        mk_bot, run_async, monkeypatch):
    from aipager import policy_snapshot
    from aipager.policy import load_policy
    bot = mk_bot(scopes=_scopes(dm_deny=("WebFetch",)))
    bot.policy = load_policy()
    monkeypatch.setattr(inject, "send_text_and_enter", AsyncMock(return_value=True))
    monkeypatch.setattr(inject, "is_alive", AsyncMock(return_value=True))
    seen = []
    monkeypatch.setattr(policy_snapshot, "write_note",
                        lambda name, role, scope, member, **kw: seen.append(scope))
    sess = TrackedSession(name="claude-old", label="old", status=Status.IDLE)
    bot.registry._sessions["claude-old"] = sess
    assert sess.scope_chat_id == 0
    run_async(bot._inject_prompt(sess, "hello", driver_user_id=ALICE))
    assert [getattr(s, "chat_id", None) for s in seen] == [DM]
    assert seen[0].deny_tools == ("WebFetch",)


def test_stamped_sessions_note_uses_its_own_chat(mk_bot, run_async, monkeypatch):
    from aipager import policy_snapshot
    from aipager.policy import load_policy
    bot = mk_bot(scopes=_scopes(dm_deny=("WebFetch",)))
    bot.policy = load_policy()
    monkeypatch.setattr(inject, "send_text_and_enter", AsyncMock(return_value=True))
    monkeypatch.setattr(inject, "is_alive", AsyncMock(return_value=True))
    seen = []
    monkeypatch.setattr(policy_snapshot, "write_note",
                        lambda name, role, scope, member, **kw: seen.append(scope))
    sess = TrackedSession(name=X1, label="x1", status=Status.IDLE)
    sess.scope_chat_id = G
    bot.registry._sessions[X1] = sess
    run_async(bot._inject_prompt(sess, "hello", driver_user_id=BOB))
    assert [s.chat_id for s in seen] == [G]


# =============================================================================
# a used or cancelled card is nobody's any more
# =============================================================================

def test_a_used_end_restart_or_delete_card_is_released(gbot, run_async, mk_update):
    gbot._kill_session_core = AsyncMock(return_value=SimpleNamespace(result="killed"))
    gbot._render_status_list = MagicMock(return_value=("LIST", None))
    card = _kill_card(gbot, run_async, mk_update, ALICE)
    turn = _sess(gbot, X1).turn_key
    _run_tap(gbot, run_async, _cb(gbot, G, X1, f"endok{turn}"), ALICE, message_id=card)
    assert card_owner.refusal(gbot, G, card, BOB, X1, "kill-cancel") is None

    gbot._restart_session_core = AsyncMock(return_value=SimpleNamespace(
        ok=True, label="x2", reason="", err=""))
    _run_tap(gbot, run_async, _cb(gbot, G, X2, "restart"), ALICE, message_id=810)
    turn = _sess(gbot, X2).turn_key
    _run_tap(gbot, run_async, _cb(gbot, G, X2, f"restartok{turn}"), ALICE, message_id=810)
    assert card_owner.refusal(gbot, G, 810, BOB, X2, "restart-cancel") is None

    _run_tap(gbot, run_async, _cb(gbot, G, X2, "restart"), ALICE, message_id=811)
    _run_tap(gbot, run_async, _cb(gbot, G, X2, "restart-cancel"), ALICE, message_id=811)
    assert card_owner.refusal(gbot, G, 811, BOB, X2, "restart-cancel") is None

    _sess(gbot, X2).status = Status.GONE
    _run_tap(gbot, run_async, _cb(gbot, G, X2, "delete"), ALICE, message_id=812)
    _run_tap(gbot, run_async, _cb(gbot, G, X2, "delete-cancel"), ALICE, message_id=812)
    assert card_owner.refusal(gbot, G, 812, BOB, X2, "delete-cancel") is None
    _run_tap(gbot, run_async, _cb(gbot, G, X2, "delete"), ALICE, message_id=813)
    _run_tap(gbot, run_async, _cb(gbot, G, X2, "delete-confirm"), ALICE, message_id=813)
    assert card_owner.refusal(gbot, G, 813, BOB, X2, "delete-cancel") is None


def test_a_cancelled_picker_is_released(gbot, run_async, mk_update):
    card = _kill_card(gbot, run_async, mk_update, ALICE, text="/kill")
    q = _run_tap(gbot, run_async, "_:pick:cancel", ALICE, message_id=card)
    assert _edit_texts(q) == ["Cancelled."]
    assert card_owner.refusal(gbot, G, card, BOB, "_", "pick:cancel") is None


def test_a_confirmed_mode_card_is_released(gbot, run_async, mk_update):
    card = _mode_auto_card(gbot, run_async, mk_update)

    async def _switch(s, target):
        s.skip_perms = target
        return SimpleNamespace(ok=True, reason="", err="", label=s.label)
    gbot._perms_switch_core = AsyncMock(side_effect=_switch)
    _run_tap(gbot, run_async, _cb(gbot, G, X1, "perms_confirm"), ALICE, message_id=card)
    gbot._perms_switch_core.assert_awaited_once()
    assert card_owner.refusal(gbot, G, card, DAVE, X1, "perms_confirm") is None


def test_an_instant_switch_from_a_mode_picker_leaves_the_card_open(gbot, run_async, mk_update):
    """/mode ask with two sessions: alice's picker row switches x1 to Ask
    at once, and the mode card it becomes is everyone's again."""
    for name in (X1, X2):
        _sess(gbot, name).skip_perms = True

    async def _switch(s, target):
        s.skip_perms = target
        return SimpleNamespace(ok=True, reason="", err="", label=s.label)
    gbot._perms_switch_core = AsyncMock(side_effect=_switch)
    u = _msg(mk_update, "/mode ask", ALICE)
    ids = _track_sent(u)
    run_async(gbot._handle_mode_cmd(u, MagicMock()))
    turn = _sess(gbot, X1).turn_key
    _run_tap(gbot, run_async, _cb(gbot, G, X1, f"modeask{turn}"), ALICE, message_id=ids[-1])
    assert _sess(gbot, X1).skip_perms is False
    turn = _sess(gbot, X1).turn_key
    assert card_owner.refusal(gbot, G, ids[-1], DAVE, X1, f"modeauto{turn}") is None


def test_ended_target_in_another_chat_is_not_this_chats(gbot):
    gbot.registry._user_targets[(G, BOB)] = (D1, 99)
    _sess(gbot, D1).status = Status.GONE
    assert gbot.registry.ended_target_for(G, BOB) is None


def test_personal_mode_group_is_unchanged(mk_bot, run_async, mk_update):
    """Personal mode: everyone the chat admits is the operator. No card is
    anyone's, and no result line names anyone."""
    r = _registry()
    bot = mk_bot(r)
    u = _msg(mk_update, "/kill x1", BOB)
    _track_sent(u)
    run_async(bot._handle_kill_cmd(u, MagicMock()))
    assert not getattr(bot, "_card_owners", None)
    assert bot._actor_label(BOB, G, SimpleNamespace(username="bob")) == ""


def test_a_legacy_team_member_is_named_by_their_label(mk_bot, mk_update, run_async):
    member = SimpleNamespace(id=BOB, label="bobby", role="user")
    team_obj = MagicMock()
    team_obj.get = MagicMock(side_effect=lambda uid: member if uid == BOB else None)
    bot = mk_bot(_registry(), team=team_obj)
    assert bot._actor_label(BOB, G) == "@bobby"
    u = _msg(mk_update, "/kill x1", BOB)
    ids = _track_sent(u)
    run_async(bot._handle_kill_cmd(u, MagicMock()))
    assert card_owner.refusal(bot, G, ids[-1], CAROL, X1, "kill-cancel") == (
        "This is @bobby's card. Send /kill x1 for your own.")


def test_bare_restart_with_one_session_is_the_senders_card(gbot, run_async, mk_update):
    _sess(gbot, X2).status = Status.GONE
    u = _msg(mk_update, "/restart", ALICE)
    ids = _track_sent(u)
    run_async(session_parity.handle_restart_cmd(gbot, u, MagicMock()))
    assert card_owner.refusal(gbot, G, ids[-1], BOB, X1, "restart-cancel") == (
        "This is @alice's card. Send /restart x1 for your own.")


def test_a_requester_unknown_in_the_chat_is_named_by_handle(gbot):
    card_owner.claim(gbot, G, 77, 4040, kind="end", command="/kill x1",
                     who=SimpleNamespace(username="zed"))
    assert card_owner.refusal(gbot, G, 77, BOB, X1, "kill-cancel") == (
        "This is @zed's card. Send /kill x1 for your own.")


# ---- review iter 1 -----------------------------------------------------------

def test_a_restart_in_progress_keeps_its_card_the_requesters(gbot, run_async, mk_update):
    """rev-iter1-001: the card keeps its buttons for the whole restart."""
    u = _msg(mk_update, "/restart x1", ALICE)
    ids = _track_sent(u)
    run_async(session_parity.handle_restart_cmd(gbot, u, MagicMock()))
    card = ids[-1]
    during = []

    async def _restart(sess):
        update, query = _tap(gbot, _cb(gbot, G, X1, "restart-cancel"), BOB, message_id=card)
        await gbot._handle_callback(update, MagicMock())
        during.append((_toasts(query), query.edit_message_text.await_count))
        return SimpleNamespace(ok=True, label="x1", reason="", err="")
    gbot._restart_session_core = AsyncMock(side_effect=_restart)
    turn = _sess(gbot, X1).turn_key
    q = _run_tap(gbot, run_async, _cb(gbot, G, X1, f"restartok{turn}"), ALICE, message_id=card)
    assert during == [(["This is @alice's card. Send /restart x1 for your own."], 0)]
    assert _edit_texts(q) == ["🔄 [<b>x1</b>] restarted by @alice."]
    assert card_owner.refusal(gbot, G, card, BOB, X1, "restart-cancel") is None


def test_a_mode_switch_in_progress_keeps_its_card_the_requesters(gbot, run_async, mk_update):
    card = _mode_auto_card(gbot, run_async, mk_update)
    during = []

    async def _switch(s, target):
        update, query = _tap(gbot, _cb(gbot, G, X1, "perms_cancel"), DAVE, message_id=card)
        await gbot._handle_callback(update, MagicMock())
        during.append(_toasts(query))
        s.skip_perms = target
        return SimpleNamespace(ok=True, reason="", err="", label=s.label)
    gbot._perms_switch_core = AsyncMock(side_effect=_switch)
    _run_tap(gbot, run_async, _cb(gbot, G, X1, "perms_confirm"), ALICE, message_id=card)
    assert during == [["This is @alice's card. Send /mode x1 for your own."]]
    assert card_owner.refusal(gbot, G, card, DAVE, X1, "perms_cancel") is None


def test_a_kill_picker_row_for_an_ended_session_leaves_an_open_menu(
        gbot, run_async, mk_update):
    card = _kill_card(gbot, run_async, mk_update, ALICE, text="/kill")
    _sess(gbot, X2).status = Status.GONE
    q = _run_tap(gbot, run_async, _cb(gbot, G, X2, "end"), ALICE, message_id=card)
    assert _toasts(q) == ["That session has already ended."]
    assert card_owner.refusal(gbot, G, card, BOB, X1, "end") is None


def test_a_session_being_resumed_is_not_called_ended(gbot):
    import time
    gbot.registry.set_target(X2, G, BOB)
    x2 = _sess(gbot, X2)
    x2.status = Status.GONE
    x2.resuming_until = time.monotonic() + 60
    assert x2.is_resuming()
    assert gbot.registry.ended_target_for(G, BOB) is None


def test_old_pending_records_of_shared_ids_are_hidden(no_pending):
    no_pending.write_text(json.dumps([
        {"user_id": ANON, "username": "GroupAnonymousBot"},
        {"user_id": 4242, "username": "real"}]))
    assert [r["user_id"] for r in team.list_pending_users()] == [4242]


def test_wizard_auto_detect_says_why_when_only_anonymous_messages_were_seen(monkeypatch):
    from aipager.wizard import telegram_api
    updates = [{"message": {"chat": {"id": G, "type": "supergroup"},
                            "from": {"id": ANON}, "sender_chat": {"id": G}}}]
    monkeypatch.setattr(telegram_api, "_http_json",
                        lambda url: ({"ok": True, "result": updates}, 200, ""))
    assert telegram_api._fetch_id_from_updates("tok", want="user") == (
        None, None, telegram_api.ANONYMOUS_SENDER_ADVISORY)


def test_wizard_prints_the_advisory(wizard, monkeypatch):
    from aipager.wizard import telegram_api
    _stub_ask(monkeypatch, wizard, ["auto", True, "cancel"])
    monkeypatch.setattr(wizard, "_detect_id",
                        lambda t, *, want, **k: (None, None, telegram_api.ANONYMOUS_SENDER_ADVISORY))
    printed = []
    monkeypatch.setattr(wizard.err_console, "print", lambda s, *a, **k: printed.append(s))
    assert wizard._capture_user_identity(1, existing_ids=set(), existing_labels=set(),
                                         token="tok") is None
    assert printed == [f"  [err]{telegram_api.ANONYMOUS_SENDER_ADVISORY}[/err]"]
