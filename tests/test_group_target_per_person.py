"""In a group each person has their own message target (roadmap 8.90,
audit C7).

The target used to be one value per chat, and every outbound card or
answer moved it: alice's "continue" went to bob's x2 when x2 answered
last, and bob's keyboard Clear wiped alice's x1 with only a reaction.
Now a group keeps one target per (chat, person), moved only by that
person's own actions; session output moves only the chat-level target,
which is what a DM uses (redesign 4.10, unchanged). Groups cannot be
tested live, so these tests are the proof; the DM cases pin that a
private chat behaves as before, apart from keyboard commands and
templates now naming their session.

Group G: alice owner, bob user, live sessions x1 and x2.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.bot import session_parity
from aipager.dtach import inject
from aipager.scope import Member, Scope
from aipager.state import SessionRegistry, Status, TrackedSession

G = -1001234
DM = 11
ALICE, BOB = 11, 22
X1, X2 = "claude-x1__g1001234", "claude-x2__g1001234"
D1, D2 = "claude-d1", "claude-d2"
ANSWER = 5555          # x2's answer card in the group


def _scopes():
    return [
        Scope(chat_id=G, kind="group", label="team", members=(
            Member(id=ALICE, label="alice", role="owner"),
            Member(id=BOB, label="bob", role="user"),
        )),
        Scope(chat_id=DM, kind="dm", label="alice-dm", members=(
            Member(id=ALICE, label="alice", role="owner"),
        )),
    ]


def _registry() -> SessionRegistry:
    r = SessionRegistry()
    for name, label, chat in ((X1, "x1", G), (X2, "x2", G),
                              (D1, "d1", DM), (D2, "d2", DM)):
        s = TrackedSession(name=name, label=label, status=Status.IDLE)
        s.scope_chat_id = chat
        s.scope_kind = "group" if chat < 0 else "dm"
        r._sessions[name] = s
    return r


@pytest.fixture
def reg():
    return _registry()


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
    bot.refresh_pinned = AsyncMock()
    bot._command_map = {"Clear": "/clear", "Compact": "/compact"}
    bot._template_map = {"Continue": "Continue", "Run tests": "Run the tests"}
    return bot


def _msg(mk_update, text, user, *, chat=G, reply_to=None, message_id=900):
    u = mk_update(text, user_id=user, chat_id=chat, message_id=message_id)
    u.effective_user.username = f"u{user}"
    u.effective_message = u.message
    u.effective_chat.type = "supergroup" if chat < 0 else "private"
    u.message.forward_origin = None
    u.message.via_bot = None
    u.message.external_reply = None
    u.message.photo = None
    u.message.document = None
    u.message.entities = ()
    u.message.caption_entities = ()
    u.message.parse_entities = MagicMock(return_value={})
    u.message.parse_caption_entities = MagicMock(return_value={})
    if reply_to is not None:
        rt = MagicMock()
        rt.message_id = reply_to
        rt.text = "answer"
        rt.caption = None
        rt.from_user = MagicMock(id=4242, is_bot=True)
        u.message.reply_to_message = rt
    else:
        u.message.reply_to_message = None
    return u


def _send(bot, run_async, update):
    run_async(bot._handle_message(update, MagicMock()))


def _injected(bot):
    return [(c.args[0].label, c.args[1]) for c in bot._inject_prompt.await_args_list]


def _replies(update):
    return [c.args[0] for c in update.message.reply_text.await_args_list]


def _markup(update):
    return update.message.reply_text.await_args.kwargs.get("reply_markup")


def _talk_targets(bot, chat, markup):
    out = []
    for row in markup.inline_keyboard:
        for b in row:
            cb = b.callback_data
            if cb == "_:pick:cancel":
                out.append("cancel")
                continue
            _s, rest = cb.split(":", 1)
            _kind, idx, verb = rest.split(":", 2)
            out.append((session_parity._resolve_pref_index(bot, chat, idx).name, verb))
    return out


def _label(sess):
    return sess.label if sess is not None else None


def _name(sess):
    return sess.name if sess is not None else None


# ---- the registry --------------------------------------------------------------

def test_each_member_keeps_their_own_target(reg):
    reg.set_target(X1, G, ALICE)
    reg.set_target(X2, G, BOB)
    assert _label(reg.target_for(G, ALICE)) == "x1"
    assert _label(reg.target_for(G, BOB)) == "x2"


def test_session_output_never_moves_a_members_target(reg):
    reg.set_target(X1, G, ALICE)
    reg.track_message(101, X2, G)          # x2 answers in the group
    assert _label(reg.target_for(G, ALICE)) == "x1"
    # The chat-level target still follows output, as it always did.
    assert _label(reg.target_for(G)) == "x2"


def test_no_target_and_two_live_sessions_is_none(reg):
    reg.track_message(101, X2, G)
    assert reg.target_for(G, ALICE) is None


def test_no_target_and_one_live_session_is_that_session(reg):
    reg._sessions[X1].status = Status.GONE
    assert _label(reg.target_for(G, ALICE)) == "x2"


def test_an_ended_own_target_falls_back_to_the_only_live_session(reg):
    reg.set_target(X1, G, ALICE)
    reg._sessions[X1].status = Status.GONE
    assert _label(reg.target_for(G, ALICE)) == "x2"
    reg._sessions[X1].status = Status.IDLE
    reg._sessions[X1].label = "x1"
    reg.set_target(X1, G, ALICE)
    x3 = TrackedSession(name="claude-x3__g1001234", label="x3", status=Status.IDLE)
    x3.scope_chat_id = G
    reg._sessions[x3.name] = x3
    reg._sessions[X1].status = Status.GONE
    assert reg.target_for(G, ALICE) is None


def test_another_chats_session_is_never_a_members_target(reg):
    reg.set_target(D1, G, ALICE)          # d1 belongs to the DM
    assert ALICE not in {u for (_c, u) in reg._user_targets}
    reg._user_targets[(G, ALICE)] = (D1, 99)
    assert reg.target_for(G, ALICE) is None


def test_removing_a_session_forgets_it_as_a_members_target(reg):
    reg.set_target(X1, G, ALICE)
    reg.set_target(X2, G, BOB)
    reg.remove(X1)
    assert (G, ALICE) not in reg._user_targets
    assert _label(reg.target_for(G, BOB)) == "x2"
    # The only live session now: alice gets it without having picked it.
    assert _label(reg.target_for(G, ALICE)) == "x2"


def test_members_targets_survive_save_and_load(reg, tmp_state_file):
    reg.set_target(X1, G, ALICE)
    reg.set_target(X2, G, BOB)
    reg.track_message(101, X1, G)
    reg.save()
    data = json.loads(Path(tmp_state_file).read_text())
    assert "user_targets" in data
    assert data["user_targets"][f"{G}:{ALICE}"][0] == X1
    assert data["user_targets"][f"{G}:{BOB}"][0] == X2

    fresh = SessionRegistry()
    fresh.load()
    assert _label(fresh.target_for(G, ALICE)) == "x1"
    assert _label(fresh.target_for(G, BOB)) == "x2"
    # A new target after the load orders after the loaded ones.
    fresh.set_target(X2, G, ALICE)
    assert fresh._user_targets[(G, ALICE)][1] > fresh._user_targets[(G, BOB)][1]


def test_a_state_file_without_member_targets_loads(reg, tmp_state_file):
    reg.track_message(101, X1, G)
    reg.save()
    data = json.loads(Path(tmp_state_file).read_text())
    del data["user_targets"]
    Path(tmp_state_file).write_text(json.dumps(data))
    fresh = SessionRegistry()
    fresh.load()
    assert fresh._user_targets == {}
    assert fresh.target_for(G, ALICE) is None
    assert _label(fresh.target_for(G)) == "x1"


def test_bad_member_target_entries_are_dropped_on_load(reg, tmp_state_file):
    reg.save()
    data = json.loads(Path(tmp_state_file).read_text())
    data["user_targets"] = {
        "garbage": [X1, 1], f"{G}:{ALICE}": ["claude-unknown", 2],
        f"{G}:{BOB}": [X2, "x"], f"{DM}:{ALICE}": [D1, 3], f"{G}:33": [X1, 4],
    }
    Path(tmp_state_file).write_text(json.dumps(data))
    fresh = SessionRegistry()
    fresh.load()
    assert fresh._user_targets == {(G, 33): (X1, 4)}


def test_a_dm_ignores_the_person_and_follows_output(reg):
    """DM parity: the person is ignored, the target follows the latest
    conversation (redesign 4.10), and nothing per person is kept."""
    reg.set_target(D1, DM, ALICE)
    assert _label(reg.target_for(DM, ALICE)) == "d1"
    reg.track_message(101, D2, DM)
    assert _label(reg.target_for(DM, ALICE)) == "d2"
    assert reg.target_for(DM, ALICE) is reg.target_for(DM)
    assert reg._user_targets == {}


def test_a_person_id_that_is_not_a_real_id_reads_the_chat_target(reg):
    reg.set_target(X1, G, ALICE)
    reg.track_message(101, X2, G)
    assert _label(reg.target_for(G, True)) == "x2"
    assert _label(reg.target_for(G, "11")) == "x2"


def test_a_group_with_no_person_reads_the_chat_target(reg):
    reg.set_target(X1, G, ALICE)
    reg.track_message(101, X2, G)
    assert _label(reg.target_for(G)) == "x2"
    assert _label(reg.target_for(G, None)) == "x2"


def test_set_target_moves_the_chat_target_as_the_setter_did(reg):
    reg.set_target(X1, G, ALICE)
    assert reg.last_active_session == X1
    assert _label(reg.target_for(G)) == "x1"


# ---- the bot: messages, keyboard commands, replies, switches ----------------

def test_alices_message_stays_with_x1_when_bobs_x2_answers(gbot, run_async, mk_update):
    """Audit C7 probe 1: alice switched to x1, bob's x2 answered, and
    alice's "continue" goes to x1."""
    _send(gbot, run_async, _msg(mk_update, "/x1", ALICE, message_id=100))
    gbot.registry.track_message(101, X2, G)
    _send(gbot, run_async, _msg(mk_update, "continue", ALICE, message_id=102))
    assert _injected(gbot) == [("x1", "continue")]


def test_bobs_clear_goes_to_bobs_session_and_names_it(gbot, run_async, mk_update):
    """Audit C7 probe 2: bob talks to x2, alice's x1 answered last, bob
    taps Commands, Clear: x2 is cleared and the reply says so."""
    _send(gbot, run_async, _msg(mk_update, "/x2", BOB, message_id=100))
    gbot.registry.track_message(301, X1, G)
    up = _msg(mk_update, "Clear", BOB, message_id=302)
    _send(gbot, run_async, up)
    assert _injected(gbot) == [("x2", "/clear")]
    assert "🧹 /clear sent to x2" in _replies(up)


def test_a_member_with_no_target_is_asked_which_session(gbot, run_async, mk_update):
    gbot.registry.track_message(101, X2, G)
    up = _msg(mk_update, "hello", ALICE)
    _send(gbot, run_async, up)
    assert _injected(gbot) == []
    assert _replies(up) == [
        "Which session? Reply to one of its messages, or send /x1 your message."]
    assert _talk_targets(gbot, G, _markup(up)) == [
        (X1, "talk"), (X2, "talk"), "cancel"]


def test_a_keyboard_clear_with_no_target_asks_and_clears_nothing(gbot, run_async, mk_update):
    gbot.registry.track_message(101, X1, G)
    up = _msg(mk_update, "Clear", BOB)
    _send(gbot, run_async, up)
    assert _injected(gbot) == []
    assert _replies(up)[0].startswith("Which session?")


def test_a_template_with_no_target_asks(gbot, run_async, mk_update):
    up = _msg(mk_update, "Continue", BOB)
    _send(gbot, run_async, up)
    assert _injected(gbot) == []
    assert _replies(up)[0].startswith("Which session?")


def test_one_live_session_is_used_directly(gbot, run_async, mk_update):
    gbot.registry._sessions[X1].status = Status.GONE
    up = _msg(mk_update, "hello", ALICE)
    _send(gbot, run_async, up)
    assert _injected(gbot) == [("x2", "hello")]


def test_a_reply_to_x2_targets_x2_for_that_person_only(gbot, run_async, mk_update):
    gbot.registry.set_target(X1, G, ALICE)
    gbot.registry.set_target(X1, G, BOB)
    gbot.registry.track_message(ANSWER, X2, G)
    _send(gbot, run_async, _msg(mk_update, "on it", ALICE, reply_to=ANSWER))
    assert _injected(gbot) == [("x2", "on it")]
    assert _label(gbot.registry.target_for(G, ALICE)) == "x2"
    assert _label(gbot.registry.target_for(G, BOB)) == "x1"
    # Her next plain message follows her own choice.
    gbot.registry.track_message(ANSWER + 1, X1, G)
    _send(gbot, run_async, _msg(mk_update, "and this", ALICE, message_id=901))
    assert _injected(gbot)[-1] == ("x2", "and this")


def test_a_direct_send_sets_the_senders_target(gbot, run_async, mk_update):
    _send(gbot, run_async, _msg(mk_update, "/x2 look", BOB))
    assert _injected(gbot) == [("x2", "look")]
    assert _label(gbot.registry.target_for(G, BOB)) == "x2"
    assert gbot.registry.target_for(G, ALICE) is None


def test_a_switch_sets_only_the_senders_target(gbot, run_async, mk_update):
    _send(gbot, run_async, _msg(mk_update, "/x2", BOB))
    assert _label(gbot.registry.target_for(G, BOB)) == "x2"
    assert gbot.registry.target_for(G, ALICE) is None


def test_a_talk_button_sets_the_tappers_target(gbot, run_async):
    sess = gbot.registry._sessions[X2]
    query = MagicMock()
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.from_user = MagicMock(id=ALICE)
    query.message = MagicMock(message_id=77)
    update = MagicMock(callback_query=query, message=None, effective_user=query.from_user)
    update.effective_chat = MagicMock(id=G, type="supergroup")
    gbot._safe_answer = AsyncMock()
    handled = run_async(session_parity.handle_callback(gbot, update, query, sess.name, "talk"))
    assert handled
    assert _label(gbot.registry.target_for(G, ALICE)) == "x2"
    assert gbot.registry.target_for(G, BOB) is None


def test_a_voice_transcript_goes_to_the_senders_target(gbot, run_async, mk_update):
    gbot.registry.set_target(X1, G, ALICE)
    gbot.registry.track_message(101, X2, G)
    run_async(gbot._dispatch_voice_transcript(
        _msg(mk_update, "", ALICE), "spoken words", MagicMock()))
    assert _injected(gbot) == [("x1", "spoken words")]


def test_a_voice_transcript_with_no_target_asks(gbot, run_async, mk_update):
    up = _msg(mk_update, "", ALICE)
    run_async(gbot._dispatch_voice_transcript(up, "spoken words", MagicMock()))
    assert _injected(gbot) == []
    assert _replies(up)[0].startswith("Which session?")


def test_a_file_goes_to_the_senders_target(gbot, run_async, mk_update, tmp_path):
    gbot.registry.set_target(X1, G, ALICE)
    gbot.registry.track_message(101, X2, G)
    path = tmp_path / "a.png"
    path.write_bytes(b"x")
    run_async(gbot._inject_file_prompt(
        _msg(mk_update, "", ALICE), MagicMock(), "look", [path],
        all_photos=True, log_name="a.png"))
    assert [label for label, _p in _injected(gbot)] == ["x1"]


def test_a_file_with_no_target_asks(gbot, run_async, mk_update, tmp_path):
    path = tmp_path / "a.png"
    path.write_bytes(b"x")
    up = _msg(mk_update, "", ALICE)
    run_async(gbot._inject_file_prompt(
        up, MagicMock(), "look", [path], all_photos=True, log_name="a.png"))
    assert _injected(gbot) == []
    assert _replies(up)[0].startswith("Which session?")


def test_a_template_goes_to_the_senders_target_and_names_it(gbot, run_async, mk_update):
    gbot.registry.set_target(X2, G, BOB)
    gbot.registry.track_message(101, X1, G)
    up = _msg(mk_update, "Continue", BOB)
    order = []
    up.message.reply_text = AsyncMock(side_effect=lambda *a, **k: order.append("reply"))
    gbot._card_for_injected = AsyncMock(side_effect=lambda *a, **k: order.append("card"))
    _send(gbot, run_async, up)
    assert _injected(gbot) == [("x2", "Continue")]
    assert "📝 Continue sent to x2" in _replies(up)
    # The line goes under the tap, before the Working card.
    assert order == ["reply", "card"]


def test_clearqueue_reads_the_senders_target(gbot, run_async, mk_update):
    gbot.registry.set_target(X2, G, BOB)
    gbot.registry.track_message(101, X1, G)
    gbot._authorize = AsyncMock(return_value=True)
    seen = []

    async def _core(sess, *a, **k):
        seen.append(sess.label)
        return SimpleNamespace(ok=False, dropped=0)
    gbot._clear_queue_core = _core
    run_async(gbot._handle_clearqueue_cmd(_msg(mk_update, "/clearqueue", BOB), MagicMock()))
    assert seen == ["x2"]


def test_clearqueue_with_no_target_asks(gbot, run_async, mk_update):
    gbot._authorize = AsyncMock(return_value=True)
    gbot._clear_queue_core = AsyncMock()
    up = _msg(mk_update, "/clearqueue", BOB)
    run_async(gbot._handle_clearqueue_cmd(up, MagicMock()))
    gbot._clear_queue_core.assert_not_awaited()
    assert _replies(up)[0].startswith("Which session?")


def test_now_reads_the_senders_target(gbot, run_async, mk_update):
    gbot.registry.set_target(X2, G, BOB)
    gbot.registry.track_message(101, X1, G)
    gbot._authorize = AsyncMock(return_value=True)
    seen = []

    async def _core(sess, *a, **k):
        seen.append(sess.label)
        return SimpleNamespace(result="nothing")
    gbot._send_now_core = _core
    run_async(gbot._handle_now_cmd(_msg(mk_update, "/now", BOB), MagicMock()))
    assert seen == ["x2"]


def test_now_with_no_target_asks(gbot, run_async, mk_update):
    gbot._authorize = AsyncMock(return_value=True)
    gbot._send_now_core = AsyncMock()
    up = _msg(mk_update, "/now", BOB)
    run_async(gbot._handle_now_cmd(up, MagicMock()))
    gbot._send_now_core.assert_not_awaited()
    assert _replies(up)[0].startswith("Which session?")


def test_mode_reads_the_senders_target(gbot, run_async, mk_update):
    gbot.registry.set_target(X2, G, BOB)
    gbot.registry.track_message(101, X1, G)
    gbot._authorize = AsyncMock(return_value=True)
    up = _msg(mk_update, "/mode", BOB)
    run_async(gbot._handle_mode_cmd(up, MagicMock()))
    # No ✍️ in a group: the card is everyone's, the target is per person.
    assert _replies(up) == ["<b>x2</b> is 💬 Ask."]


def test_diff_reads_the_senders_target(gbot, run_async, mk_update, monkeypatch):
    gbot.registry.set_target(X2, G, BOB)
    gbot.registry.track_message(101, X1, G)
    gbot._authorize = AsyncMock(return_value=True)
    seen = []

    async def _diff(bot, sess, **k):
        seen.append(sess.label)
    monkeypatch.setattr(session_parity, "_run_diff", _diff)
    up = _msg(mk_update, "/diff", BOB)
    run_async(session_parity.handle_diff_cmd(gbot, up, MagicMock()))
    assert seen == ["x2"]


def test_pickers_mark_the_viewers_target_not_the_chats(gbot):
    gbot.registry.set_target(X2, G, BOB)
    gbot.registry.track_message(101, X1, G)
    live = [gbot.registry._sessions[X1], gbot.registry._sessions[X2]]
    kb = session_parity.session_picker(gbot, G, live, "restart", user_id=BOB)
    assert kb.inline_keyboard[0][0].text == "✍️ x2"
    kb = session_parity.session_picker(gbot, G, live, "restart")
    assert [r[0].text for r in kb.inline_keyboard[:2]] == ["x1", "x2"]


def test_a_new_session_is_its_creators_target(gbot, run_async, monkeypatch):
    gbot._update_bot_commands = AsyncMock()
    monkeypatch.setattr(inject, "launch_session", AsyncMock(return_value=(True, "")))
    gbot.registry.set_target(X1, G, BOB)
    name, err = run_async(gbot.create_session(
        "x3", scope_chat_id=G, driver_user_id=ALICE))
    assert name, err
    assert _name(gbot.registry.target_for(G, ALICE)) == name
    assert _label(gbot.registry.target_for(G, BOB)) == "x1"


def test_the_pinned_bar_has_no_target_line_in_a_group(gbot):
    gbot.registry.set_target(X1, G, ALICE)
    live = [gbot.registry._sessions[X1], gbot.registry._sessions[X2]]
    text, _kb = gbot._render_pinned(G, live)
    assert "Messages go to" not in text


def test_the_pinned_bar_keeps_its_target_line_in_a_dm(gbot):
    gbot.registry.track_message(101, D2, DM)
    live = [gbot.registry._sessions[D1], gbot.registry._sessions[D2]]
    text, _kb = gbot._render_pinned(DM, live)
    assert "✍️ Messages go to <b>d2</b>" in text


# ---- DM parity --------------------------------------------------------------------

def test_dm_output_still_moves_the_target(gbot, run_async, mk_update):
    """One person in a DM: the target follows the latest conversation."""
    _send(gbot, run_async, _msg(mk_update, "/d1", ALICE, chat=DM, message_id=100))
    gbot.registry.track_message(101, D2, DM)
    _send(gbot, run_async, _msg(mk_update, "continue", ALICE, chat=DM, message_id=102))
    assert _injected(gbot) == [("d2", "continue")]


def test_dm_keyboard_clear_names_its_session(gbot, run_async, mk_update):
    gbot.registry.track_message(101, D1, DM)
    up = _msg(mk_update, "Clear", ALICE, chat=DM)
    _send(gbot, run_async, up)
    assert _injected(gbot) == [("d1", "/clear")]
    assert _replies(up) == ["🧹 /clear sent to d1"]


def test_dm_other_command_and_template_name_their_session(gbot, run_async, mk_update):
    gbot.registry.track_message(101, D1, DM)
    up = _msg(mk_update, "Compact", ALICE, chat=DM)
    _send(gbot, run_async, up)
    assert _replies(up) == ["↪️ /compact sent to d1"]
    up = _msg(mk_update, "Run tests", ALICE, chat=DM, message_id=903)
    _send(gbot, run_async, up)
    assert _injected(gbot)[-1] == ("d1", "Run the tests")
    assert _replies(up) == ["📝 Run tests sent to d1"]


def test_dm_with_no_target_never_asks_which_session(gbot, run_async, mk_update):
    """A DM keeps its old reply when nothing is targeted: no picker."""
    gbot.registry.last_active_session = ""
    up = _msg(mk_update, "hello", ALICE, chat=DM)
    _send(gbot, run_async, up)
    assert _injected(gbot) == []
    assert not any(r.startswith("Which session?") for r in _replies(up))


# ---- more writers ------------------------------------------------------------------

def test_a_held_direct_send_still_sets_the_senders_target(gbot, run_async, mk_update):
    """The target is the sender's choice even when the message itself is
    held behind an open dialog."""
    gbot._hold_for_open_dialog = AsyncMock(return_value=True)
    _send(gbot, run_async, _msg(mk_update, "/x2 look", BOB))
    assert _injected(gbot) == []
    assert _label(gbot.registry.target_for(G, BOB)) == "x2"


def _discoverable(gbot, monkeypatch, name):
    alive = set(gbot.registry._sessions) | {name}
    monkeypatch.setattr(inject, "is_alive", AsyncMock(side_effect=lambda n: n in alive))
    gbot._update_bot_commands = AsyncMock()
    gbot._send_busy_and_animate = AsyncMock()


def test_a_direct_send_to_a_discovered_session_sets_the_senders_target(
        gbot, run_async, mk_update, monkeypatch):
    _discoverable(gbot, monkeypatch, "claude-x9__g1001234")
    _send(gbot, run_async, _msg(mk_update, "/x9__g1001234 look", BOB))
    assert [c.args[0].name for c in gbot._inject_prompt.await_args_list] == [
        "claude-x9__g1001234"]
    assert _name(gbot.registry.target_for(G, BOB)) == "claude-x9__g1001234"
    assert gbot.registry.target_for(G, ALICE) is None


def test_a_held_direct_send_to_a_discovered_session_sets_the_target(
        gbot, run_async, mk_update, monkeypatch):
    _discoverable(gbot, monkeypatch, "claude-x9__g1001234")
    gbot._hold_for_open_dialog = AsyncMock(return_value=True)
    _send(gbot, run_async, _msg(mk_update, "/x9__g1001234 look", BOB))
    assert _injected(gbot) == []
    assert _name(gbot.registry.target_for(G, BOB)) == "claude-x9__g1001234"


def test_a_switch_to_a_discovered_session_sets_the_senders_target(
        gbot, run_async, mk_update, monkeypatch):
    _discoverable(gbot, monkeypatch, "claude-x9__g1001234")
    run_async(gbot._switch_session(_msg(mk_update, "/x9__g1001234", BOB), "x9__g1001234"))
    assert _name(gbot.registry.target_for(G, BOB)) == "claude-x9__g1001234"
    assert gbot.registry.target_for(G, ALICE) is None


def test_a_voice_reply_to_x2_sets_the_senders_target(gbot, run_async, mk_update):
    gbot.registry.set_target(X1, G, ALICE)
    gbot.registry.track_message(ANSWER, X2, G)
    run_async(gbot._dispatch_voice_transcript(
        _msg(mk_update, "", ALICE, reply_to=ANSWER), "spoken", MagicMock()))
    assert _injected(gbot) == [("x2", "spoken")]
    assert _label(gbot.registry.target_for(G, ALICE)) == "x2"


def test_a_file_reply_to_x2_sets_the_senders_target(gbot, run_async, mk_update, tmp_path):
    gbot.registry.set_target(X1, G, ALICE)
    gbot.registry.track_message(ANSWER, X2, G)
    path = tmp_path / "a.png"
    path.write_bytes(b"x")
    run_async(gbot._inject_file_prompt(
        _msg(mk_update, "", ALICE, reply_to=ANSWER), MagicMock(), "look", [path],
        all_photos=True, log_name="a.png"))
    assert [label for label, _p in _injected(gbot)] == ["x2"]
    assert _label(gbot.registry.target_for(G, ALICE)) == "x2"


def test_resuming_a_session_makes_it_the_resumers_target(gbot, run_async, monkeypatch):
    sess = gbot.registry._sessions[X2]
    sess.status = Status.GONE
    sess.claude_session_id = "UUID-1"
    monkeypatch.setattr(inject, "launch_session", AsyncMock(return_value=(True, "")))
    gbot._update_bot_commands = AsyncMock()
    outcome = run_async(gbot._do_resume_core(sess, driver_user_id=BOB))
    assert outcome.ok
    assert _label(gbot.registry.target_for(G, BOB)) == "x2"
    assert gbot.registry.target_for(G, ALICE) is None


def test_switching_from_the_new_conflict_card_sets_the_tappers_target(gbot, run_async):
    gbot._new_conflict_pending[X2] = {"prompt": "", "skip_perms": False,
                                      "user_id": ALICE, "msg_id": 5}
    query = MagicMock()
    query.data = f"{X2}:new_resume"
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.message = MagicMock(message_id=42, text="")
    query.message.chat = MagicMock(id=G, type="supergroup")
    query.from_user = MagicMock(id=ALICE)
    update = MagicMock(callback_query=query, effective_user=query.from_user)
    update.effective_chat = MagicMock(id=G, type="supergroup")
    run_async(gbot._handle_callback(update, MagicMock()))
    assert _label(gbot.registry.target_for(G, ALICE)) == "x2"
    assert gbot.registry.target_for(G, BOB) is None


# ---- more readers -------------------------------------------------------------------

def test_the_home_screen_reads_the_senders_target(gbot, run_async, mk_update, monkeypatch):
    """/start in a group: with no target of their own, a member is told to
    tap a session, even when the chat's target (x1 answered) is set."""
    from aipager.bot import handlers
    sent = AsyncMock()
    monkeypatch.setattr(handlers, "send_text", sent)
    gbot._send_keyboard = AsyncMock()
    gbot.registry.track_message(101, X1, G)
    run_async(gbot._send_home(_msg(mk_update, "/start", BOB)))
    assert "Tap a session on the keyboard to talk to it." in sent.await_args.args[2]
    gbot.registry.set_target(X2, G, BOB)
    run_async(gbot._send_home(_msg(mk_update, "/start", BOB)))
    assert "Reply to a message from <b>x2</b>" in sent.await_args.args[2]


def test_status_in_a_group_marks_no_target(gbot, mk_update):
    """The /status list is everyone's in a group (a tap re-renders it for
    the whole chat), and each member has their own target: no ✍️."""
    gbot.registry.set_target(X2, G, BOB)
    gbot.registry.track_message(101, X1, G)
    text, _kb = gbot._render_status_list(G, _msg(mk_update, "/status", BOB))
    assert "<b>x1</b>" in text and "<b>x2</b>" in text
    assert "✍️" not in text.split("\n\n", 1)[1]


def test_status_in_a_dm_marks_the_chats_target(gbot, mk_update):
    gbot.registry.track_message(101, D2, DM)
    text, _kb = gbot._render_status_list(DM, _msg(mk_update, "/status", ALICE, chat=DM))
    assert "✍️ <b>d2</b>" in text


def test_the_mode_card_marks_the_target_in_a_dm_only(gbot):
    gbot.registry.track_message(101, D2, DM)
    text, _kb = gbot._render_mode_card(DM, gbot.registry._sessions[D2])
    assert text.startswith("✍️ <b>d2</b>")
    gbot.registry.track_message(102, X1, G)
    text, _kb = gbot._render_mode_card(G, gbot.registry._sessions[X1])
    assert text.startswith("<b>x1</b>")


def _first_button(update):
    return _markup(update).inline_keyboard[0][0].text


@pytest.mark.parametrize("command", ["/restart", "/rename", "/kill", "/stop"])
def test_session_pickers_put_the_senders_target_first(gbot, run_async, mk_update, command):
    from aipager.bot import session_parity as sp
    for name in (X1, X2):
        gbot.registry._sessions[name].status = Status.BUSY
    gbot.registry.set_target(X2, G, BOB)
    gbot.registry.track_message(101, X1, G)
    gbot._authorize = AsyncMock(return_value=True)
    up = _msg(mk_update, command, BOB)
    handler = {
        "/restart": lambda: sp.handle_restart_cmd(gbot, up, MagicMock()),
        "/rename": lambda: sp.handle_rename_cmd(gbot, up, MagicMock()),
        "/kill": lambda: gbot._handle_kill_cmd(up, MagicMock()),
        "/stop": lambda: gbot._handle_stop_cmd(up, MagicMock()),
    }[command]
    run_async(handler())
    assert _first_button(up).endswith("x2") and _first_button(up).startswith("✍️")


def test_a_group_with_no_live_session_never_asks_which(gbot, run_async, mk_update):
    for name in (X1, X2):
        gbot.registry._sessions[name].status = Status.GONE
    up = _msg(mk_update, "hello", ALICE)
    _send(gbot, run_async, up)
    assert _injected(gbot) == []
    assert not any(r.startswith("Which session?") for r in _replies(up))


def test_an_unlabelled_session_is_not_counted_as_live(reg):
    reg._sessions[X1].label = ""
    assert _label(reg.target_for(G, ALICE)) == "x2"


def test_clearing_the_target_clears_the_members_targets_too(reg):
    reg.set_target(X1, G, ALICE)
    reg.last_active_session = ""
    assert reg._user_targets == {}


def test_set_target_with_no_person_records_no_member_target(reg):
    reg.set_target(X1, G, None)
    reg.set_target(X1, G, True)
    assert reg._user_targets == {}
    assert reg.last_active_session == X1


def test_set_target_marks_the_registry_dirty(reg):
    reg._dirty = False
    reg.set_target(X1, G, ALICE)
    assert reg._dirty


def test_a_loaded_registry_orders_new_targets_after_loaded_ones(reg, tmp_state_file):
    reg.set_target(X1, G, ALICE)
    reg.set_target(X2, G, BOB)
    reg.save()
    data = json.loads(Path(tmp_state_file).read_text())
    data["targets"] = {}
    Path(tmp_state_file).write_text(json.dumps(data))
    fresh = SessionRegistry()
    fresh.load()
    fresh.set_target(X1, G, 33)
    assert fresh._user_targets[(G, 33)][1] > fresh._user_targets[(G, BOB)][1]


def test_an_infinite_order_in_a_hand_edited_file_is_dropped(reg, tmp_state_file):
    reg.save()
    raw = Path(tmp_state_file).read_text()
    data = json.loads(raw)
    data["user_targets"] = {f"{G}:{ALICE}": [X1, 1]}
    Path(tmp_state_file).write_text(
        json.dumps(data).replace(f'["{X1}", 1]', f'["{X1}", Infinity]'))
    fresh = SessionRegistry()
    try:
        fresh.load()
        crashed = False
    except OverflowError:
        crashed = True
    assert not crashed
    assert fresh._user_targets == {}
    assert len(fresh._sessions) == len(reg._sessions)


def test_calling_user_id_takes_only_a_real_id():
    from aipager.bot.transport import calling_user_id
    assert calling_user_id(SimpleNamespace(effective_user=SimpleNamespace(id=7))) == 7
    assert calling_user_id(SimpleNamespace(from_user=SimpleNamespace(id=8))) == 8
    assert calling_user_id(MagicMock()) is None
    assert calling_user_id(SimpleNamespace(effective_user=SimpleNamespace(id=True))) is None
    assert calling_user_id(SimpleNamespace()) is None
