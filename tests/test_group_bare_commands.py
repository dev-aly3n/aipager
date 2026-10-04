"""Bare session commands in a group, and "Which session?" that sends
(roadmap 8.93 and 8.94i, delivery 18).

- 8.93: in a group (scope or team mode) a bare `/stop`, `/mode` (`/perms`),
  `/kill`, `/restart`, `/rename`, `/delete` or `/diff` acts only on the
  sender's own target, and only when it is a session the command makes
  sense for (through the command's usual confirmation). Otherwise it is
  always the picker, even with one candidate: that one may be another
  member's session. `/<cmd> <name>` is unchanged, and a private chat keeps
  P4's "the one session it makes sense for" byte for byte.
- 8.94i: a tap on a "Which session?" card's session button also sends the
  message it asked about there (text, voice transcript, file prompt), as
  the original sender, through every gate; the card says "Sent to x2.".
  Only the sender may use the card, and only for ten minutes.

Groups cannot be tested live, so these tests are the proof.

Group G: alice owner, bob and carol users, dave admin. Live sessions x1
and x2 in G; d1 in alice's DM.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import policy_snapshot as ps
from aipager.bot import card_owner, held_message, session_parity
from aipager.bot.transport import NEEDS_ADMIN_REPLY
from aipager.dtach import inject
from aipager.scope import Member, Scope
from aipager.state import SessionRegistry, Status, TrackedSession

G = -1001234
DM = 11
ALICE, BOB, CAROL, DAVE = 11, 22, 33, 44
X1, X2 = "claude-x1__g1001234", "claude-x2__g1001234"
D1, D2 = "claude-d1", "claude-d2"


def _scopes():
    return [
        Scope(chat_id=G, kind="group", label="team", members=(
            Member(id=ALICE, label="alice", role="owner"),
            Member(id=BOB, label="bob", role="user"),
            Member(id=CAROL, label="carol", role="user"),
            Member(id=DAVE, label="dave", role="admin"),
        )),
        Scope(chat_id=DM, kind="dm", label="alice-dm", members=(
            Member(id=ALICE, label="alice", role="owner"),
        )),
    ]


def _registry(extra=()) -> SessionRegistry:
    r = SessionRegistry()
    for name, label, chat in ((X1, "x1", G), (X2, "x2", G), (D1, "d1", DM), *extra):
        s = TrackedSession(name=name, label=label, status=Status.IDLE)
        s.scope_chat_id = chat
        s.scope_kind = "group" if chat < 0 else "dm"
        r._sessions[name] = s
    return r


def _wire(bot, r, monkeypatch):
    from aipager.policy import load_policy
    bot.policy = load_policy()
    monkeypatch.setattr(inject, "is_alive",
                        AsyncMock(side_effect=lambda name: name in r._sessions))
    bot._card_for_injected = AsyncMock()
    bot._react = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    bot._update_bot_commands = AsyncMock()
    bot._send_busy_and_animate = AsyncMock()
    bot.refresh_pinned = AsyncMock()
    bot._app.bot.id = 999
    bot._stop_session = AsyncMock(return_value=SimpleNamespace(ok=True))
    return bot


@pytest.fixture
def gbot(mk_bot, monkeypatch):
    """Scope mode, `_inject_prompt` recorded."""
    r = _registry()
    bot = _wire(mk_bot(r, scopes=_scopes()), r, monkeypatch)
    bot._inject_prompt = AsyncMock(return_value=True)
    return bot


@pytest.fixture
def typed(monkeypatch):
    out: list[tuple[str, str]] = []

    async def _send(name, text, *a, **kw):
        out.append((name, text))
        return True

    monkeypatch.setattr(inject, "send_text_and_enter", _send)
    return out


@pytest.fixture
def rbot(mk_bot, monkeypatch, typed):
    """Scope mode with the real `_inject_prompt`: the marker, the note and
    the gates are the ones a message sent there gets."""
    r = _registry()
    return _wire(mk_bot(r, scopes=_scopes()), r, monkeypatch)


def _sess(bot, name):
    return bot.registry._sessions[name]


_IDS = iter(range(20000, 999999))


def _msg(mk_update, text, user, *, chat=G):
    u = mk_update(text, user_id=user, chat_id=chat, message_id=next(_IDS))
    u.effective_user.username = f"u{user}"
    u.effective_user.first_name = "U"
    u.effective_user.last_name = ""
    u.effective_message = u.message
    u.effective_chat.type = "supergroup" if chat < 0 else "private"
    u.callback_query = None
    m = u.message
    m.chat = u.effective_chat
    m.caption = None
    m.media_group_id = None
    m.sender_chat = None
    m.from_user = u.effective_user
    m.photo = None
    m.document = None
    m.entities = ()
    m.caption_entities = ()
    m.parse_entities = MagicMock(return_value={})
    m.parse_caption_entities = MagicMock(return_value={})
    sent = []

    def _reply(*a, **k):
        mid = next(_IDS)
        sent.append(mid)
        return MagicMock(message_id=mid)
    m.reply_text = AsyncMock(side_effect=_reply)
    u.sent_ids = sent
    return u


def _replies(update):
    return [c.args[0] if c.args else c.kwargs.get("text")
            for c in update.message.reply_text.await_args_list]


def _markup(update):
    return update.message.reply_text.await_args.kwargs.get("reply_markup")


def _rows(bot, chat, markup):
    """(button text, session name or "cancel", verb) per row."""
    out = []
    for row in markup.inline_keyboard:
        for b in row:
            if b.callback_data == "_:pick:cancel":
                out.append((b.text, "cancel", ""))
                continue
            _s, rest = b.callback_data.split(":", 1)
            _kind, idx, verb = rest.split(":", 2)
            out.append((b.text, session_parity._resolve_pref_index(bot, chat, idx).name, verb))
    return out


def _tap(bot, run_async, data, user, *, chat=G, message_id, raised=None):
    """The tapped query. *raised*: a list that collects an exception the
    tap let out (instead of raising it), so the card can still be read."""
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
    try:
        run_async(bot._handle_callback(update, MagicMock()))
    except Exception as exc:
        if raised is None:
            raise
        raised.append(exc)
    return query


def _toasts(query):
    return [c.args[0] if c.args else None for c in query.answer.await_args_list]


def _edits(query):
    return [c.args[0] if c.args else c.kwargs.get("text")
            for c in query.edit_message_text.await_args_list]


def _cb(bot, chat, name, verb):
    return session_parity.session_cb(bot, chat, bot.registry._sessions[name], verb)


def _ctx():
    return MagicMock(bot=MagicMock(id=999))


# ---- running a bare command ------------------------------------------------

def _run(bot, run_async, update):
    """Dispatch *update* (a command) to its handler."""
    word = update.message.text.split()[0].lstrip("/")
    handler = {
        "stop": bot._handle_stop_cmd,
        "kill": bot._handle_kill_cmd,
        "mode": bot._handle_mode_cmd,
        "perms": bot._handle_perms_cmd,
        "restart": lambda u, c: session_parity.handle_restart_cmd(bot, u, c),
        "rename": lambda u, c: session_parity.handle_rename_cmd(bot, u, c),
        "delete": lambda u, c: session_parity.handle_delete_cmd(bot, u, c),
        "diff": lambda u, c: session_parity.handle_diff_cmd(bot, u, c),
    }[word]
    run_async(handler(update, MagicMock()))


@pytest.fixture
def diffs(monkeypatch):
    seen = []

    async def _fake(bot, sess, *, target_message):
        seen.append(sess.name)
    monkeypatch.setattr(session_parity, "_run_diff", _fake)
    return seen


# What each command's card for x1 starts with, when it acts on x1.
ACTED = {
    "kill": "⏹ End <b>x1</b>?",
    "mode": "<b>x1</b> is 💬 Ask.",
    "perms": "<b>x1</b> is 💬 Ask.",
    "restart": "🔄 Restart session [<b>x1</b>]?",
    "rename": "✏️ New name for [<b>x1</b>]?",
    "delete": "🗑️ Remove [<b>x1</b>] from the session list?",
}
QUESTION = {
    "stop": "Which one to stop?",
    "kill": "Which session to end?",
    "mode": "Which session?",
    "perms": "Which session?",
    "restart": "Which session to restart?",
    "rename": "Which session to rename?",
    "delete": "Which ended session to remove?",
    "diff": "Which session's diff?",
}
ALL = sorted(QUESTION)


def _setup(bot, command, *, own_target_x1: bool):
    """Make x1 (and x2) sessions *command* makes sense for; bob's own
    target is x1 when *own_target_x1*."""
    if own_target_x1:
        bot.registry.set_target(X1, G, BOB)
    if command == "stop":
        _sess(bot, X1).status = Status.BUSY
        _sess(bot, X2).status = Status.BUSY
    if command == "delete":
        _sess(bot, X1).status = Status.GONE
        _sess(bot, X2).status = Status.GONE


def _acted_on(bot, update, command, diffs, name):
    """True when the command acted on *name* without asking."""
    if command == "stop":
        return (bot._stop_session.await_count == 1
                and bot._stop_session.await_args.args[0].name == name)
    if command == "diff":
        return diffs == [name]
    label = bot.registry._sessions[name].label
    replies = _replies(update)
    return bool(replies) and replies[-1].startswith(ACTED[command].replace("x1", label))


def _nothing_acted(bot, update, command, diffs):
    return not any(_acted_on(bot, update, command, diffs, n)
                   for n in (X1, X2) if n in bot.registry._sessions)


# (a) the sender's own target qualifies: it is acted on, with its card

@pytest.mark.parametrize("command", ALL)
def test_bare_command_acts_on_the_senders_own_target(gbot, run_async, mk_update,
                                                     diffs, command):
    _setup(gbot, command, own_target_x1=True)
    # x2 is the chat's latest target, alice's: never the one bob means.
    gbot.registry.set_target(X2, G, ALICE)
    u = _msg(mk_update, f"/{command}", BOB)
    _run(gbot, run_async, u)
    assert _acted_on(gbot, u, command, diffs, X1)
    assert QUESTION[command] not in _replies(u)


def test_bare_kill_confirm_on_the_own_target_is_the_senders(gbot, run_async, mk_update):
    gbot.registry.set_target(X1, G, BOB)
    u = _msg(mk_update, "/kill", BOB)
    _run(gbot, run_async, u)
    assert card_owner.refusal(gbot, G, u.sent_ids[-1], CAROL, X1, "kill-cancel") == (
        "This is @bob's card. Send /kill x1 for your own.")


# (b) one candidate that is someone else's: the picker, never acted on

def _one_candidate(bot, command):
    """x1 is the one session *command* makes sense for; it is alice's."""
    bot.registry.set_target(X1, G, ALICE)
    if command == "stop":
        _sess(bot, X1).status = Status.BUSY
    elif command == "delete":
        _sess(bot, X1).status = Status.GONE
    elif command == "rename":
        del bot.registry._sessions[X2]
    else:
        _sess(bot, X2).status = Status.GONE


@pytest.mark.parametrize("command", ALL)
def test_one_candidate_of_another_member_is_a_picker(gbot, run_async, mk_update,
                                                     diffs, command):
    _one_candidate(gbot, command)
    u = _msg(mk_update, f"/{command}", BOB)
    _run(gbot, run_async, u)
    assert _nothing_acted(gbot, u, command, diffs)
    assert _replies(u) == [QUESTION[command]]
    rows = _rows(gbot, G, _markup(u))
    assert [r[1] for r in rows] == [X1, "cancel"]
    assert not rows[0][0].startswith("✍️")


@pytest.mark.parametrize("command", ["stop", "kill", "restart", "mode"])
def test_own_target_that_does_not_qualify_is_a_picker(gbot, run_async, mk_update,
                                                      diffs, command):
    """bob's own x2 is idle (/stop) or ended (the rest); the only candidate,
    x1, is alice's."""
    gbot.registry.set_target(X1, G, ALICE)
    gbot.registry.set_target(X2, G, BOB)
    if command == "stop":
        _sess(gbot, X1).status = Status.BUSY
    else:
        _sess(gbot, X2).status = Status.GONE
    u = _msg(mk_update, f"/{command}", BOB)
    _run(gbot, run_async, u)
    assert _nothing_acted(gbot, u, command, diffs)
    assert _replies(u) == [QUESTION[command]]
    assert [r[1] for r in _rows(gbot, G, _markup(u))] == [X1, "cancel"]


@pytest.mark.parametrize("want,verb", [("ask", "modeask"), ("auto", "modeauto")])
def test_mode_with_a_mode_and_one_other_members_session_is_a_picker(
        gbot, run_async, mk_update, want, verb):
    _one_candidate(gbot, "mode")
    _sess(gbot, X1).skip_perms = want == "ask"
    gbot._perms_flow = AsyncMock()
    u = _msg(mk_update, f"/mode {want}", DAVE)
    _run(gbot, run_async, u)
    gbot._perms_flow.assert_not_awaited()
    rows = _rows(gbot, G, _markup(u))
    assert [r[1] for r in rows] == [X1, "cancel"]
    assert rows[0][2].startswith(verb)


def test_mode_with_a_mode_on_the_own_target_switches_it(gbot, run_async, mk_update):
    gbot.registry.set_target(X1, G, DAVE)
    _sess(gbot, X2).status = Status.GONE
    gbot._perms_flow = AsyncMock()
    u = _msg(mk_update, "/mode auto", DAVE)
    _run(gbot, run_async, u)
    assert gbot._perms_flow.await_count == 1
    assert gbot._perms_flow.await_args.args[0] is _sess(gbot, X1)


@pytest.mark.parametrize("command", ALL)
def test_one_candidate_and_no_target_at_all_is_a_picker(gbot, run_async, mk_update,
                                                        diffs, command):
    """carol has never picked a session: the chat's only one may be anyone's."""
    _one_candidate(gbot, command)
    u = _msg(mk_update, f"/{command}", CAROL)
    _run(gbot, run_async, u)
    assert _nothing_acted(gbot, u, command, diffs)
    assert _replies(u) == [QUESTION[command]]


@pytest.mark.parametrize("command", ALL)
def test_a_picker_tap_reaches_the_same_card(gbot, run_async, mk_update, diffs, command):
    """The picker shown instead still leads to the session's own card."""
    _one_candidate(gbot, command)
    u = _msg(mk_update, f"/{command}", BOB)
    _run(gbot, run_async, u)
    cb = _markup(u).inline_keyboard[0][0].callback_data
    gbot._stop_session_core = AsyncMock(return_value=SimpleNamespace(
        ok=True, label="x1", discarded=0, dropped=0, by="@bob"))
    q = _tap(gbot, run_async, cb, BOB, message_id=u.sent_ids[-1])
    if command == "stop":
        assert gbot._stop_session_core.await_count == 1
        assert gbot._stop_session_core.await_args.args[0] is _sess(gbot, X1)
    elif command == "diff":
        assert diffs == [X1]
    elif command == "rename":
        assert _edits(q)[-1].startswith("✏️ New name for [<b>x1</b>]?")
    else:
        assert _edits(q)[-1].startswith(ACTED[command])


# (c) several and no target of their own: the picker; ✍️ marks their own

@pytest.mark.parametrize("command", ALL)
def test_several_and_no_own_target_is_a_picker_of_all(gbot, run_async, mk_update,
                                                      diffs, command):
    _setup(gbot, command, own_target_x1=False)
    gbot.registry.set_target(X2, G, ALICE)
    u = _msg(mk_update, f"/{command}", CAROL)
    _run(gbot, run_async, u)
    assert _nothing_acted(gbot, u, command, diffs)
    assert _replies(u) == [QUESTION[command]]
    rows = _rows(gbot, G, _markup(u))
    assert [r[1] for r in rows] == [X1, X2, "cancel"]
    assert not any(r[0].startswith("✍️") for r in rows)


def test_picker_marks_the_own_target_first_never_the_only_live_session(gbot):
    gbot.registry.set_target(X2, G, BOB)
    kb = session_parity.session_picker(
        gbot, G, [_sess(gbot, X1), _sess(gbot, X2)], "end", glyph="⏹ ", user_id=BOB)
    assert [r[:2] for r in _rows(gbot, G, kb)] == [
        ("✍️ x2", X2), ("⏹ x1", X1), ("✖️ Cancel", "cancel")]
    # carol has no target; x1 is the chat's only live session (and her
    # message would fall to it), but it is not hers to have chosen.
    _sess(gbot, X2).status = Status.GONE
    assert gbot.registry.target_for(G, CAROL) is _sess(gbot, X1)
    kb = session_parity.session_picker(gbot, G, [_sess(gbot, X1)], "end",
                                       glyph="⏹ ", user_id=CAROL)
    assert _rows(gbot, G, kb)[0][0] == "⏹ x1"


def test_an_ended_own_target_is_marked_in_the_delete_picker(gbot):
    gbot.registry.set_target(X2, G, BOB)
    for name in (X1, X2):
        _sess(gbot, name).status = Status.GONE
    kb = session_parity.session_picker(
        gbot, G, [_sess(gbot, X1), _sess(gbot, X2)], "delete", glyph="🗑️ ", user_id=BOB)
    assert _rows(gbot, G, kb)[0][:2] == ("✍️ x2", X2)


def test_own_target_is_per_person_and_group_only(gbot):
    gbot.registry.set_target(X1, G, BOB)
    assert gbot.registry.own_target(G, BOB) is _sess(gbot, X1)
    assert gbot.registry.own_target(G, CAROL) is None
    _sess(gbot, X1).status = Status.GONE
    assert gbot.registry.own_target(G, BOB) is _sess(gbot, X1)   # ended counts
    gbot.registry.set_target(D1, DM, ALICE)
    assert gbot.registry.own_target(DM, ALICE) is None
    gbot.registry._user_targets[(G, CAROL)] = (D1, 99)          # another chat's
    assert gbot.registry.own_target(G, CAROL) is None
    gbot.registry._user_targets[(DM, ALICE)] = (D1, 99)         # never for a DM
    assert gbot.registry.own_target(DM, ALICE) is None
    gbot.registry.set_target(X2, G, CAROL)
    _sess(gbot, X2).label = ""                                  # not a session yet
    assert gbot.registry.own_target(G, CAROL) is None


def test_diff_on_an_ended_own_target_diffs_it(gbot, run_async, mk_update, diffs):
    """As a DM's ended target is diffed: its folder is still there."""
    gbot.registry.set_target(X2, G, BOB)
    _sess(gbot, X2).status = Status.GONE
    _run(gbot, run_async, _msg(mk_update, "/diff", BOB))
    assert diffs == [X2]


# (d) `/<cmd> <name>` is unchanged

@pytest.mark.parametrize("command", ALL)
def test_a_named_command_acts_on_that_session(gbot, run_async, mk_update, diffs, command):
    _one_candidate(gbot, command)
    if command == "rename":
        # /rename names the session and its new name together.
        _run(gbot, run_async, _msg(mk_update, "/rename x1 y1", BOB))
        assert _sess(gbot, X1).label == "y1"
        return
    u = _msg(mk_update, f"/{command} x1", BOB)
    _run(gbot, run_async, u)
    assert _acted_on(gbot, u, command, diffs, X1)


# ---- private chats and personal mode: P4 byte for byte -----------------------

@pytest.fixture
def dbot(mk_bot, monkeypatch):
    r = _registry(extra=((D2, "d2", DM),))
    bot = _wire(mk_bot(r, scopes=_scopes()), r, monkeypatch)
    bot._inject_prompt = AsyncMock(return_value=True)
    return bot


def _dm_one(bot, command):
    """d1 the DM's one session *command* makes sense for; no target."""
    del bot.registry._sessions[D2]
    if command == "stop":
        _sess(bot, D1).status = Status.BUSY
    if command == "delete":
        _sess(bot, D1).status = Status.GONE


@pytest.mark.parametrize("command", ALL)
def test_dm_one_session_is_acted_on(dbot, run_async, mk_update, diffs, command):
    _dm_one(dbot, command)
    u = _msg(mk_update, f"/{command}", ALICE, chat=DM)
    _run(dbot, run_async, u)
    if command == "stop":
        assert dbot._stop_session.await_count == 1
        assert dbot._stop_session.await_args.args[0] is _sess(dbot, D1)
    elif command == "diff":
        assert diffs == [D1]
    else:
        assert _replies(u)[-1].startswith(ACTED[command].replace("x1", "d1"))


@pytest.mark.parametrize("command", ALL)
def test_dm_several_is_a_picker_with_the_target_first(dbot, run_async, mk_update,
                                                      diffs, command):
    if command == "stop":
        for n in (D1, D2):
            _sess(dbot, n).status = Status.BUSY
    if command == "delete":
        for n in (D1, D2):
            _sess(dbot, n).status = Status.GONE
    dbot.registry.track_message(101, D2, DM)        # d2 is the DM's target
    u = _msg(mk_update, f"/{command}", ALICE, chat=DM)
    _run(dbot, run_async, u)
    if command in ("mode", "perms", "diff"):
        # These act on the target itself, as before.
        if command == "diff":
            assert diffs == [D2]
        else:
            assert _replies(u)[-1].startswith("✍️ <b>d2</b> is 💬 Ask.")
        return
    assert _replies(u) == [QUESTION[command]]
    rows = _rows(dbot, DM, _markup(u))
    assert rows[0][:2] == ("✍️ d2", D2)
    assert [r[1] for r in rows] == [D2, D1, "cancel"]


@pytest.mark.parametrize("command", ALL)
def test_personal_mode_group_keeps_the_one_session_shortcut(mk_bot, monkeypatch, run_async,
                                                            mk_update, diffs, command):
    """Personal mode: everyone the chat admits is the operator, so a group
    keeps P4's shortcut (as delivery 12a kept its cards unowned)."""
    r = _registry()
    bot = _wire(mk_bot(r), r, monkeypatch)
    _one_candidate(bot, command)
    u = _msg(mk_update, f"/{command}", BOB)
    _run(bot, run_async, u)
    assert _acted_on(bot, u, command, diffs, X1)


# ---- 8.94i: "Which session?" sends the message it asked about ----------------

def _ask(bot, run_async, mk_update, text="please fix the tests", user=BOB):
    """bob, no session of his own, two live: asked which, nothing sent."""
    u = _msg(mk_update, text, user)
    run_async(bot._handle_message(u, _ctx()))
    assert _replies(u)[-1].startswith("Which session?")
    return u, u.sent_ids[-1]


def test_a_talk_tap_sends_the_held_text(gbot, run_async, mk_update):
    u, card = _ask(gbot, run_async, mk_update)
    gbot._inject_prompt.assert_not_awaited()
    q = _tap(gbot, run_async, _cb(gbot, G, X2, "talk"), BOB, message_id=card)
    assert gbot._inject_prompt.await_count == 1
    call = gbot._inject_prompt.await_args
    assert call.args[0] is _sess(gbot, X2) and call.args[1] == "please fix the tests"
    assert call.kwargs == {"msg_id": u.message.message_id, "chat_id": G,
                           "driver_user_id": BOB}
    assert _edits(q) == ["Sent to x2."]
    assert card_owner.refusal(gbot, G, card, CAROL, X2, "talk") is None   # used
    assert gbot.registry.target_for(G, BOB) is _sess(gbot, X2)
    assert gbot.registry.get_session_by_msg(u.message.message_id, G) is _sess(gbot, X2)
    # Used once: a second (double) tap sends nothing and keeps the card.
    gbot._render_status_list = MagicMock(return_value=("LIST", None))
    q = _tap(gbot, run_async, _cb(gbot, G, X2, "talk"), BOB, message_id=card)
    assert gbot._inject_prompt.await_count == 1
    assert _edits(q) == []


def test_a_talk_tap_keeps_the_reply_context(gbot, run_async, mk_update):
    u = _msg(mk_update, "and this one too", BOB)
    u.message.reply_to_message = MagicMock(message_id=4242, text="the old answer",
                                           caption=None, from_user=MagicMock(id=5))
    gbot._build_reply_context = MagicMock(return_value="CTX")
    run_async(gbot._handle_message(u, _ctx()))
    card = u.sent_ids[-1]
    _tap(gbot, run_async, _cb(gbot, G, X1, "talk"), BOB, message_id=card)
    assert gbot._build_reply_context.call_count == 1
    assert gbot._build_reply_context.call_args.args == (u.message, _sess(gbot, X1))
    assert gbot._inject_prompt.await_count == 1
    assert gbot._inject_prompt.await_args.args[2] == "CTX"


def test_the_sent_message_carries_the_senders_marker_and_role(rbot, typed, run_async,
                                                              mk_update):
    _u, card = _ask(rbot, run_async, mk_update, "run the linter")
    assert typed == []
    _tap(rbot, run_async, _cb(rbot, G, X2, "talk"), BOB, message_id=card)
    assert typed == [(X2, "[via Telegram · @bob · role:user]\nrun the linter")]
    notes = ps.list_outstanding_notes(X2)
    assert len(notes) == 1 and notes[0]["author_user_id"] == BOB
    assert notes[0]["bypass_safety"] is False


def test_the_slash_rule_still_applies_to_a_held_transcript(rbot, typed, run_async,
                                                           mk_update):
    u = _msg(mk_update, "", BOB)
    run_async(rbot._dispatch_voice_transcript(u, "/deliver ship it", _ctx()))
    card = u.sent_ids[-1]
    assert _replies(u)[-1].startswith("Which session?")
    q = _tap(rbot, run_async, _cb(rbot, G, X2, "talk"), BOB, message_id=card)
    assert typed == []
    assert NEEDS_ADMIN_REPLY in _replies(u)
    assert _edits(q) == ["Not sent to x2."]


def test_another_members_turn_still_holds_it(rbot, typed, run_async, mk_update):
    x2 = _sess(rbot, X2)
    x2.status = Status.BUSY
    x2.turn_sender_id = ALICE
    _u, card = _ask(rbot, run_async, mk_update, "also do this")
    q = _tap(rbot, run_async, _cb(rbot, G, X2, "talk"), BOB, message_id=card)
    assert typed == []
    assert [e[0] for e in x2.pending_queue] == ["also do this"]
    assert _edits(q) == ["Sent to x2."]


def test_a_talk_tap_sends_a_held_voice_transcript(gbot, run_async, mk_update):
    u = _msg(mk_update, "", BOB)
    run_async(gbot._dispatch_voice_transcript(u, "check the build", _ctx()))
    card = u.sent_ids[-1]
    q = _tap(gbot, run_async, _cb(gbot, G, X1, "talk"), BOB, message_id=card)
    assert gbot._inject_prompt.await_count == 1
    call = gbot._inject_prompt.await_args
    assert (call.args[0], call.args[1]) == (_sess(gbot, X1), "check the build")
    assert call.kwargs["driver_user_id"] == BOB
    assert _edits(q) == ["Sent to x1."]


def test_a_talk_tap_sends_a_held_file_prompt(gbot, run_async, mk_update, tmp_path):
    u = _msg(mk_update, "", BOB)
    path = tmp_path / "shot.png"
    run_async(gbot._inject_file_prompt(u, _ctx(), "what is wrong here", [path],
                                       all_photos=True, log_name="shot.png"))
    card = u.sent_ids[-1]
    gbot._inject_prompt.assert_not_awaited()
    q = _tap(gbot, run_async, _cb(gbot, G, X2, "talk"), BOB, message_id=card)
    assert gbot._inject_prompt.await_count == 1
    call = gbot._inject_prompt.await_args
    assert (call.args[0], call.args[1]) == (_sess(gbot, X2), f"what is wrong here {path}")
    assert _edits(q) == ["Sent to x2."]


def test_another_members_tap_is_refused_and_sends_nothing(gbot, run_async, mk_update):
    _u, card = _ask(gbot, run_async, mk_update)
    q = _tap(gbot, run_async, _cb(gbot, G, X2, "talk"), CAROL, message_id=card)
    assert _toasts(q) == ["This is @bob's card."]
    gbot._inject_prompt.assert_not_awaited()
    assert gbot.registry.own_target(G, CAROL) is None
    # Their Cancel too.
    q = _tap(gbot, run_async, "_:pick:cancel", CAROL, message_id=card)
    assert _toasts(q) == ["This is @bob's card."] and _edits(q) == []
    # The sender's own tap still works.
    _tap(gbot, run_async, _cb(gbot, G, X2, "talk"), BOB, message_id=card)
    assert gbot._inject_prompt.await_count == 1


def test_an_expired_message_only_sets_the_target(gbot, run_async, mk_update):
    _u, card = _ask(gbot, run_async, mk_update)
    gbot._held_messages[(G, card)].at -= held_message.MAX_AGE + 1
    q = _tap(gbot, run_async, _cb(gbot, G, X2, "talk"), BOB, message_id=card)
    gbot._inject_prompt.assert_not_awaited()
    assert _edits(q) == ["That message is too old to send; send it again."]
    assert gbot.registry.target_for(G, BOB) is _sess(gbot, X2)


def test_a_message_just_inside_ten_minutes_is_sent(gbot, run_async, mk_update):
    _u, card = _ask(gbot, run_async, mk_update)
    gbot._held_messages[(G, card)].at -= held_message.MAX_AGE - 5
    _tap(gbot, run_async, _cb(gbot, G, X2, "talk"), BOB, message_id=card)
    assert gbot._inject_prompt.await_count == 1
    assert held_message.MAX_AGE == 600


def test_a_cancelled_card_forgets_its_message(gbot, run_async, mk_update):
    _u, card = _ask(gbot, run_async, mk_update)
    q = _tap(gbot, run_async, "_:pick:cancel", BOB, message_id=card)
    assert _edits(q) == ["Cancelled."]
    assert (G, card) not in gbot._held_messages


def test_the_ended_card_sends_on_a_talk_tap_and_resume_is_unchanged(
        gbot, run_async, mk_update):
    gbot.registry.set_target(X2, G, BOB)
    x2 = _sess(gbot, X2)
    x2.status = Status.GONE
    x2.claude_session_id = "UUID-2"
    u = _msg(mk_update, "keep going", BOB)
    run_async(gbot._handle_message(u, _ctx()))
    card = u.sent_ids[-1]
    assert _replies(u)[-1] == ("x2 has ended. Which session?\n"
                               "After ▶️ Resume, send your message again.")
    rows = _rows(gbot, G, _markup(u))
    assert [r[2] for r in rows] == ["talk", "resume", ""]
    # Resume: the ⋮ menu's own step, nothing sent.
    q = _tap(gbot, run_async, _cb(gbot, G, X2, "resume"), BOB, message_id=card)
    assert _edits(q) == ["Resume <b>x2</b> as:"]
    gbot._inject_prompt.assert_not_awaited()
    # Another ask, answered with x1: sent there.
    u = _msg(mk_update, "keep going", BOB)
    run_async(gbot._handle_message(u, _ctx()))
    q = _tap(gbot, run_async, _cb(gbot, G, X1, "talk"), BOB, message_id=u.sent_ids[-1])
    assert gbot._inject_prompt.await_count == 1
    assert gbot._inject_prompt.await_args.args[:2] == (_sess(gbot, X1), "keep going")
    assert _edits(q) == ["Sent to x1."]


def test_another_member_cannot_resume_from_the_ended_card(gbot, run_async, mk_update):
    gbot.registry.set_target(X2, G, BOB)
    x2 = _sess(gbot, X2)
    x2.status = Status.GONE
    x2.claude_session_id = "UUID-2"
    u = _msg(mk_update, "keep going", BOB)
    run_async(gbot._handle_message(u, _ctx()))
    q = _tap(gbot, run_async, _cb(gbot, G, X2, "resume"), CAROL, message_id=u.sent_ids[-1])
    assert _toasts(q) == ["This is @bob's card."] and _edits(q) == []


def test_a_question_with_nothing_held_keeps_the_status_list(gbot, run_async, mk_update):
    """A template's question holds nothing: the tap sets the target and
    shows the list, as before."""
    u = _msg(mk_update, "x", BOB)
    run_async(gbot._send_template(u, "summarise", button="Summary"))
    card = u.sent_ids[-1]
    gbot._render_status_list = MagicMock(return_value=("LIST", None))
    q = _tap(gbot, run_async, _cb(gbot, G, X1, "talk"), BOB, message_id=card)
    assert _edits(q) == ["LIST"]
    gbot._inject_prompt.assert_not_awaited()


def test_a_status_list_talk_tap_is_unchanged(gbot, run_async):
    gbot._render_status_list = MagicMock(return_value=("LIST", None))
    q = _tap(gbot, run_async, _cb(gbot, G, X1, "talk"), BOB, message_id=31337)
    assert _toasts(q) == ["✍️ Messages go to x1"] and _edits(q) == ["LIST"]
    gbot._inject_prompt.assert_not_awaited()


def test_a_dm_never_asks_and_sends_as_before(gbot, run_async, mk_update):
    gbot.registry.track_message(101, D1, DM)
    u = _msg(mk_update, "hello", ALICE, chat=DM)
    run_async(gbot._handle_message(u, _ctx()))
    assert gbot._inject_prompt.await_count == 1
    assert gbot._inject_prompt.await_args.args[:2] == (_sess(gbot, D1), "hello")
    assert not getattr(gbot, "_held_messages", None)


def test_held_messages_are_bounded(gbot):
    async def _r(sess):
        return True
    for i in range(held_message.MAX_RECORDS + 5):
        held_message.hold(gbot, G, i, BOB, _r)
    assert len(gbot._held_messages) == held_message.MAX_RECORDS
    assert (G, 0) not in gbot._held_messages


def test_personal_mode_group_sends_only_for_the_sender(mk_bot, monkeypatch, run_async,
                                                      mk_update):
    """No card has an owner in personal mode, so the held message itself
    checks who tapped: anyone else's tap only sets their own target."""
    r = _registry()
    bot = _wire(mk_bot(r), r, monkeypatch)
    bot._inject_prompt = AsyncMock(return_value=True)
    bot._render_status_list = MagicMock(return_value=("LIST", None))
    _u, card = _ask(bot, run_async, mk_update)
    q = _tap(bot, run_async, _cb(bot, G, X2, "talk"), CAROL, message_id=card)
    bot._inject_prompt.assert_not_awaited()
    assert _edits(q) == ["LIST"]
    q = _tap(bot, run_async, _cb(bot, G, X2, "talk"), BOB, message_id=card)
    assert bot._inject_prompt.await_count == 1
    assert bot._inject_prompt.await_args.args[:2] == (_sess(bot, X2), "please fix the tests")
    assert _edits(q) == ["Sent to x2."]


def test_an_ended_card_with_nothing_held_has_no_resend_line(gbot, run_async, mk_update):
    gbot.registry.set_target(X2, G, BOB)
    x2 = _sess(gbot, X2)
    x2.status = Status.GONE
    x2.claude_session_id = "UUID-2"
    u = _msg(mk_update, "x", BOB)
    run_async(gbot._send_template(u, "summarise", button="Summary"))
    assert _replies(u)[-1] == "x2 has ended. Which session?"


@pytest.mark.parametrize("kind", ["text", "voice", "file"])
def test_a_full_queue_says_not_sent(rbot, typed, run_async, mk_update, tmp_path, kind):
    from aipager.state import QUEUE_CAP
    x2 = _sess(rbot, X2)
    x2.status = Status.BUSY
    x2.turn_sender_id = ALICE
    for i in range(QUEUE_CAP):
        assert x2.queue_prompt(f"m{i}", 100 + i, "", ALICE)
    if kind == "text":
        u, card = _ask(rbot, run_async, mk_update, "one more")
    else:
        u = _msg(mk_update, "", BOB)
        if kind == "voice":
            run_async(rbot._dispatch_voice_transcript(u, "one more", _ctx()))
        else:
            run_async(rbot._inject_file_prompt(u, _ctx(), "one more", [tmp_path / "a.png"],
                                               all_photos=True, log_name="a.png"))
        card = u.sent_ids[-1]
        assert _replies(u)[-1].startswith("Which session?")
    q = _tap(rbot, run_async, _cb(rbot, G, X2, "talk"), BOB, message_id=card)
    assert typed == []
    assert any("Queue is full" in r for r in _replies(u))
    assert _edits(q) == ["Not sent to x2."]


def test_a_card_that_held_nothing_becomes_everyones_list(gbot, run_async, mk_update):
    """rev-iter1-001: the status list a tap turns it into is shared."""
    u = _msg(mk_update, "x", BOB)
    run_async(gbot._send_template(u, "summarise", button="Summary"))
    card = u.sent_ids[-1]
    assert card_owner.refusal(gbot, G, card, CAROL, X1, "talk") == "This is @bob's card."
    gbot._render_status_list = MagicMock(return_value=("LIST", None))
    _tap(gbot, run_async, _cb(gbot, G, X1, "talk"), BOB, message_id=card)
    assert card_owner.refusal(gbot, G, card, CAROL, X1, "talk") is None


@pytest.mark.parametrize("command", ["kill", "stop", "mode"])
def test_team_mode_group_uses_the_group_rule(mk_bot, monkeypatch, run_async, mk_update,
                                             diffs, command):
    """Legacy team mode is a shared group too: no one-session shortcut."""
    member = SimpleNamespace(id=BOB, label="bob", role="admin")
    team_obj = MagicMock()
    team_obj.get = MagicMock(side_effect=lambda uid: member if uid == BOB else None)
    r = _registry()
    bot = _wire(mk_bot(r, team=team_obj), r, monkeypatch)
    bot._authorize = AsyncMock(return_value=True)
    assert session_parity.shared_group(bot, G)
    _one_candidate(bot, command)
    u = _msg(mk_update, f"/{command}", BOB)
    _run(bot, run_async, u)
    assert _nothing_acted(bot, u, command, diffs)
    assert _replies(u) == [QUESTION[command]]


def test_a_send_that_raises_says_not_sent(gbot, run_async, mk_update):
    """The message is used up either way: the card must not look usable."""
    _u, card = _ask(gbot, run_async, mk_update)
    gbot._inject_prompt = AsyncMock(side_effect=RuntimeError("boom"))
    raised = []
    q = _tap(gbot, run_async, _cb(gbot, G, X2, "talk"), BOB, message_id=card, raised=raised)
    assert _edits(q) == ["Not sent to x2."]
    assert raised == []
