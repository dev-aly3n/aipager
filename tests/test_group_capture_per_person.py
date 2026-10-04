"""The rename question and the /new Name card belong to one person, not to
the whole chat (roadmap 8.86, audit C3/C4).

In a group, bob's /new used to cancel alice's open Name card, bob's reply
to her card became a prompt, and a pending rename took the next message
from anyone (bob's "thanks" renamed alice's x1 to ``thanks``). Both are now
keyed by (chat, person). Groups cannot be tested live, so these tests are
the proof; the DM cases pin that a private chat behaves as before.

Group G: alice owner, bob user.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.bot import new_flow, session_parity
from aipager.scope import Member, Scope
from aipager.state import SessionRegistry, Status, TrackedSession

G = -1001234
DM = 11
ALICE, BOB = 11, 22
X1, X2 = "claude-x1__g1001234", "claude-x2__g1001234"
ANSWER = 5555          # a session's answer card in the chat


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


@pytest.fixture
def gbot(mk_bot, monkeypatch):
    from aipager.policy import load_policy
    r = SessionRegistry()
    for name, label, chat in ((X1, "x1", G), (X2, "x2", G), ("claude-d1", "d1", DM)):
        s = TrackedSession(name=name, label=label, status=Status.IDLE)
        s.scope_chat_id = chat
        r._sessions[name] = s
    r.last_active_session = X1
    bot = mk_bot(r, scopes=_scopes())
    bot.policy = load_policy()
    monkeypatch.setattr(
        "aipager.dtach.inject.is_alive",
        AsyncMock(side_effect=lambda name: name in r._sessions))
    bot._inject_prompt = AsyncMock(return_value=True)
    bot._card_for_injected = AsyncMock()
    bot._react = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    bot._app.bot.edit_message_text = AsyncMock()
    bot.create_session = AsyncMock(return_value=("", "not launched in tests"))
    bot._rename_session_core = AsyncMock(side_effect=lambda sess, new, **kw: SimpleNamespace(
        changed=True, previous_label=sess.label, new_label=new))
    return bot


def _msg(mk_update, text, user, *, chat=G, reply_to=None, message_id=900, sent_id=None):
    u = mk_update(text, user_id=user, chat_id=chat, message_id=message_id)
    u.effective_user.username = f"u{user}"
    u.effective_message = u.message
    u.effective_chat.type = "supergroup" if chat < 0 else "private"
    u.message.forward_origin = None
    u.message.via_bot = None
    u.message.external_reply = None
    u.message.photo = None
    u.message.document = None
    if reply_to is not None:
        rt = MagicMock()
        rt.message_id = reply_to
        rt.text = "answer"
        rt.caption = None
        rt.from_user = MagicMock(id=4242, is_bot=True)
        u.message.reply_to_message = rt
    else:
        u.message.reply_to_message = None
    if sent_id is not None:
        u.message.reply_text = AsyncMock(return_value=SimpleNamespace(message_id=sent_id))
    return u


def _tap(data, user, *, chat=G, message_id):
    query = MagicMock(data=data)
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.from_user = MagicMock(id=user)
    query.message = MagicMock(message_id=message_id)
    update = MagicMock(callback_query=query, message=None, effective_user=query.from_user)
    update.effective_chat = MagicMock(id=chat, type="supergroup" if chat < 0 else "private")
    return update, query


def _replies(update):
    return [c.args[0] for c in update.message.reply_text.await_args_list]


def _edits(bot):
    return [(c.kwargs.get("message_id"), c.kwargs.get("text"))
            for c in bot._app.bot.edit_message_text.await_args_list]


def _send(bot, run_async, update):
    """An inbound message as the application runs it: the group -1
    moved-on check, then the text router."""
    async def _go():
        await new_flow.close_if_moved_on(bot, update)
        await bot._handle_message(update, MagicMock())
        await asyncio.sleep(0)       # let a close's background edit land
    run_async(_go())


def _open_card(bot, run_async, mk_update, user, card_id, *, chat=G):
    u = _msg(mk_update, "/new", user, chat=chat, sent_id=card_id)
    run_async(new_flow.start_wizard(bot, u))
    return u


def _ask_rename(bot, run_async, session, user, card_id, *, chat=G):
    """⋮ → Rename on *session*'s menu message *card_id*, tapped by *user*."""
    update, query = _tap("_:sx:0:rename", user, chat=chat, message_id=card_id)

    async def _go():
        await new_flow.close_if_moved_on(bot, update)
        await session_parity.handle_callback(
            bot, update, query, session, "rename")
        await asyncio.sleep(0)
    run_async(_go())
    return query


def _renamed(bot):
    return [(c.args[0].name, c.args[1]) for c in bot._rename_session_core.await_args_list]


def _injected(bot):
    return [(c.args[0].label, c.args[1]) for c in bot._inject_prompt.await_args_list]


# ---- the Name card: one per person -------------------------------------------

def test_bobs_new_leaves_alices_card_open_and_her_reply_starts_x3(gbot, run_async, mk_update):
    """Audit C1c/C4: bob's /new turned alice's card into "Cancelled -
    started over." and her reply x3 then went to x1 as a prompt."""
    _open_card(gbot, run_async, mk_update, ALICE, 7000)
    _open_card(gbot, run_async, mk_update, BOB, 7001)

    assert all(text != "↩️ Cancelled - started over." for _id, text in _edits(gbot))
    assert set(new_flow._pending_store(gbot)) == {(G, ALICE), (G, BOB)}

    _send(gbot, run_async, _msg(mk_update, "x3", ALICE, reply_to=7000, message_id=12))

    gbot.create_session.assert_awaited_once()
    assert gbot.create_session.await_args.args[0] == "x3"
    assert gbot.create_session.await_args.kwargs["driver_user_id"] == ALICE
    gbot._inject_prompt.assert_not_awaited()
    assert new_flow._pending_store(gbot)[(G, BOB)]["msg_id"] == 7001


def test_a_second_new_replaces_only_the_same_persons_card(gbot, run_async, mk_update):
    _open_card(gbot, run_async, mk_update, ALICE, 7000)
    _open_card(gbot, run_async, mk_update, BOB, 7001)
    _open_card(gbot, run_async, mk_update, ALICE, 7002)

    assert (7000, "↩️ Cancelled - started over.") in _edits(gbot)
    assert all(mid != 7001 for mid, _t in _edits(gbot))
    store = new_flow._pending_store(gbot)
    assert (store[(G, ALICE)]["msg_id"], store[(G, BOB)]["msg_id"]) == (7002, 7001)


def test_bobs_reply_to_alices_card_is_told_whose_it_is_and_not_routed(
        gbot, run_async, mk_update):
    _open_card(gbot, run_async, mk_update, ALICE, 7000)
    before = dict(new_flow._pending_store(gbot)[(G, ALICE)])

    up = _msg(mk_update, "x3", BOB, reply_to=7000)
    _send(gbot, run_async, up)

    assert _replies(up) == ["This card is @alice's. Send /new for your own."]
    gbot._inject_prompt.assert_not_awaited()
    gbot.create_session.assert_not_awaited()
    assert new_flow._pending_store(gbot)[(G, ALICE)] == before   # not even touched


def test_bobs_reply_to_alices_card_is_refused_and_closes_his_own(
        gbot, run_async, mk_update):
    """His reply was not the answer to his own card either: it stops
    listening (moved on), so his next chat message starts nothing."""
    _open_card(gbot, run_async, mk_update, ALICE, 7000)
    _open_card(gbot, run_async, mk_update, BOB, 7001)

    up = _msg(mk_update, "x3", BOB, reply_to=7000)
    _send(gbot, run_async, up)

    assert _replies(up) == ["This card is @alice's. Send /new for your own."]
    gbot.create_session.assert_not_awaited()
    assert set(new_flow._pending_store(gbot)) == {(G, ALICE)}
    assert (7001, new_flow._CLOSED_TEXT) in _edits(gbot)

    _send(gbot, run_async, _msg(mk_update, "thanks", BOB))
    gbot.create_session.assert_not_awaited()


def test_bobs_reply_to_alices_card_closes_his_rename(gbot, run_async, mk_update):
    _open_card(gbot, run_async, mk_update, ALICE, 7000)
    _ask_rename(gbot, run_async, X2, BOB, 8001)

    _send(gbot, run_async, _msg(mk_update, "beta", BOB, reply_to=7000))

    assert session_parity._rename_pending_map(gbot) == {}
    assert _renamed(gbot) == []


def test_bobs_plain_message_with_alices_card_open_routes_as_usual(
        gbot, run_async, mk_update):
    _open_card(gbot, run_async, mk_update, ALICE, 7000)

    up = _msg(mk_update, "hello", BOB, reply_to=ANSWER)
    _send(gbot, run_async, up)

    assert _injected(gbot) == [("x1", "hello")]
    assert (G, ALICE) in new_flow._pending_store(gbot)


def test_bobs_tap_on_alices_card_is_refused_even_with_his_own_open(
        gbot, run_async, mk_update):
    _open_card(gbot, run_async, mk_update, ALICE, 7000)
    _open_card(gbot, run_async, mk_update, BOB, 7001)
    store = new_flow._pending_store(gbot)
    before = {key: dict(card) for key, card in store.items()}
    assert store[(G, ALICE)]["skip_perms"] is True
    update, query = _tap("_:nw:mode:ask", BOB, message_id=7000)

    run_async(new_flow.handle_callback(gbot, update, query, "_", "nw:mode:ask"))

    assert query.answer.await_args.args[0] == "This isn't your new session."
    assert store == before


def test_each_persons_tap_drives_their_own_card(gbot, run_async, mk_update):
    _open_card(gbot, run_async, mk_update, ALICE, 7000)
    _open_card(gbot, run_async, mk_update, BOB, 7001)
    update, query = _tap("_:nw:cancel", BOB, message_id=7001)

    run_async(new_flow.handle_callback(gbot, update, query, "_", "nw:cancel"))

    assert set(new_flow._pending_store(gbot)) == {(G, ALICE)}
    assert (7001, "↩️ Cancelled.") in _edits(gbot)


def test_bobs_command_closes_only_his_own_card(gbot, run_async, mk_update):
    _open_card(gbot, run_async, mk_update, ALICE, 7000)
    _open_card(gbot, run_async, mk_update, BOB, 7001)

    run_async(new_flow.close_if_moved_on(gbot, _msg(mk_update, "/stop", BOB)))

    assert set(new_flow._pending_store(gbot)) == {(G, ALICE)}


def test_the_card_names_its_owner_in_a_group(gbot, run_async, mk_update):
    a = _open_card(gbot, run_async, mk_update, ALICE, 7000)
    b = _open_card(gbot, run_async, mk_update, BOB, 7001)

    assert _replies(a)[0].startswith("🆕 <b>New session</b> (@alice)\n\n")
    assert _replies(b)[0].startswith("🆕 <b>New session</b> (@bob)\n\n")
    # And keeps naming them when the card is redrawn (a picker, Back).
    update, query = _tap("_:nw:back", BOB, message_id=7001)
    run_async(new_flow.handle_callback(gbot, update, query, "_", "nw:back"))
    assert _edits(gbot)[-1][1].startswith("🆕 <b>New session</b> (@bob)\n\n")


def test_the_dm_card_is_unchanged(gbot, run_async, mk_update):
    a = _open_card(gbot, run_async, mk_update, ALICE, 7000, chat=DM)

    assert _replies(a)[0].startswith(
        "🆕 <b>New session</b>\n\nSend a name, and what to do first if you like:\n")
    assert "owner_label" not in new_flow._pending_store(gbot)[(DM, ALICE)]


# ---- the rename question: one per person ---------------------------------------

def test_bobs_message_while_alices_rename_waits_routes_normally(gbot, run_async, mk_update):
    """Audit C3: bob's "thanks" renamed alice's x1 to `thanks`, and a real
    prompt of his was swallowed as a bad name."""
    _ask_rename(gbot, run_async, X1, ALICE, 8000)

    _send(gbot, run_async, _msg(mk_update, "thanks", BOB, reply_to=ANSWER))
    _send(gbot, run_async, _msg(mk_update, "also fix the lint errors", BOB, reply_to=ANSWER))

    assert _renamed(gbot) == []
    assert _injected(gbot) == [("x1", "thanks"), ("x1", "also fix the lint errors")]
    assert gbot.registry.get(X1).label == "x1"
    # alice's question still waits for her, and her answer still renames.
    _send(gbot, run_async, _msg(mk_update, "api", ALICE, reply_to=8000))
    assert _renamed(gbot) == [(X1, "api")]


def test_two_renames_each_take_their_own_askers_answer(gbot, run_async, mk_update):
    """Audit C4: bob's rename replaced alice's, and her answer renamed x2."""
    _ask_rename(gbot, run_async, X1, ALICE, 8000)
    _ask_rename(gbot, run_async, X2, BOB, 8001)

    _send(gbot, run_async, _msg(mk_update, "alpha", ALICE, reply_to=8000))
    _send(gbot, run_async, _msg(mk_update, "beta", BOB, reply_to=8001))

    assert _renamed(gbot) == [(X1, "alpha"), (X2, "beta")]
    assert session_parity._rename_pending_map(gbot) == {}
    gbot._inject_prompt.assert_not_awaited()


def test_an_expired_rename_lets_the_message_through(gbot, run_async, mk_update):
    _ask_rename(gbot, run_async, X1, ALICE, 8000)
    session_parity._rename_pending_map(gbot)[(G, ALICE)]["last_active"] -= (
        new_flow._WIZARD_TTL_SECONDS + 1)

    _send(gbot, run_async, _msg(mk_update, "api", ALICE, reply_to=8000))

    assert _renamed(gbot) == []
    assert _injected(gbot) == [("x1", "api")]
    assert session_parity._rename_pending_map(gbot) == {}
    assert (8000, session_parity._RENAME_EXPIRED_TEXT) in _edits(gbot)


def test_a_rename_inside_its_ttl_still_takes_the_answer(gbot, run_async, mk_update):
    _ask_rename(gbot, run_async, X1, ALICE, 8000)
    session_parity._rename_pending_map(gbot)[(G, ALICE)]["last_active"] -= (
        new_flow._WIZARD_TTL_SECONDS - 5)

    _send(gbot, run_async, _msg(mk_update, "api", ALICE, reply_to=8000))

    assert _renamed(gbot) == [(X1, "api")]


@pytest.mark.parametrize("text", ["/stop", "/x2 hi", "/rename"])
def test_the_askers_command_closes_her_rename(gbot, run_async, mk_update, text):
    _ask_rename(gbot, run_async, X1, ALICE, 8000)
    _ask_rename(gbot, run_async, X2, BOB, 8001)

    async def _go():
        await new_flow.close_if_moved_on(gbot, _msg(mk_update, text, ALICE))
        await asyncio.sleep(0)
    run_async(_go())

    assert set(session_parity._rename_pending_map(gbot)) == {(G, BOB)}
    assert (8000, session_parity._RENAME_CLOSED_TEXT) in _edits(gbot)


def test_a_looking_command_keeps_the_rename(gbot, run_async, mk_update):
    _ask_rename(gbot, run_async, X1, ALICE, 8000)

    run_async(new_flow.close_if_moved_on(gbot, _msg(mk_update, "/status", ALICE)))

    assert (G, ALICE) in session_parity._rename_pending_map(gbot)


def test_the_askers_tap_elsewhere_closes_her_rename(gbot, run_async, mk_update):
    _ask_rename(gbot, run_async, X1, ALICE, 8000)
    update, _q = _tap("_:sx:0:stop", ALICE, message_id=ANSWER)

    run_async(new_flow.close_if_moved_on(gbot, update))

    assert session_parity._rename_pending_map(gbot) == {}


def test_the_askers_reply_to_another_message_closes_and_routes(gbot, run_async, mk_update):
    _ask_rename(gbot, run_async, X1, ALICE, 8000)

    _send(gbot, run_async, _msg(mk_update, "fix the tests", ALICE, reply_to=ANSWER))

    assert _renamed(gbot) == []
    assert _injected(gbot) == [("x1", "fix the tests")]
    assert session_parity._rename_pending_map(gbot) == {}
    assert (8000, session_parity._RENAME_CLOSED_TEXT) in _edits(gbot)


def test_the_askers_keyboard_word_closes_or_keeps_like_the_card(gbot, run_async, mk_update):
    _ask_rename(gbot, run_async, X1, ALICE, 8000)
    gbot._handle_status = AsyncMock()
    _send(gbot, run_async, _msg(mk_update, "status", ALICE))
    assert (G, ALICE) in session_parity._rename_pending_map(gbot)
    assert _renamed(gbot) == []

    gbot._stop_session = AsyncMock()
    _send(gbot, run_async, _msg(mk_update, "stop", ALICE))
    assert session_parity._rename_pending_map(gbot) == {}
    assert _renamed(gbot) == []


def test_bobs_command_leaves_alices_rename(gbot, run_async, mk_update):
    _ask_rename(gbot, run_async, X1, ALICE, 8000)

    run_async(new_flow.close_if_moved_on(gbot, _msg(mk_update, "/stop", BOB)))

    assert (G, ALICE) in session_parity._rename_pending_map(gbot)


def test_her_cancel_tap_is_not_a_moved_on(gbot, run_async):
    """The rename's own Cancel: its handler says "Rename cancelled.", so
    the moved-on close must not edit the same message after it."""
    _ask_rename(gbot, run_async, X1, ALICE, 8000)
    update, query = _tap("_:sx:0:rename-cancel", ALICE, message_id=8000)

    async def _go():
        await new_flow.close_if_moved_on(gbot, update)
        await asyncio.sleep(0)
    run_async(_go())

    assert (G, ALICE) in session_parity._rename_pending_map(gbot)
    assert all(text != session_parity._RENAME_CLOSED_TEXT for _id, text in _edits(gbot))

    run_async(session_parity.handle_callback(gbot, update, query, X1, "rename-cancel"))
    assert session_parity._rename_pending_map(gbot) == {}
    query.edit_message_text.assert_awaited()
    assert query.edit_message_text.await_args.args[0] == "Rename cancelled."


def test_bobs_cancel_tap_on_alices_rename_closes_his_own(gbot, run_async):
    """Tapping someone else's Cancel is another action of his: his own
    rename stops waiting (moved on), hers stays."""
    _ask_rename(gbot, run_async, X1, ALICE, 8000)
    _ask_rename(gbot, run_async, X2, BOB, 8001)
    update, _query = _tap("_:sx:0:rename-cancel", BOB, message_id=8000)

    async def _go():
        await new_flow.close_if_moved_on(gbot, update)
        await asyncio.sleep(0)
    run_async(_go())

    assert set(session_parity._rename_pending_map(gbot)) == {(G, ALICE)}
    assert (8001, session_parity._RENAME_CLOSED_TEXT) in _edits(gbot)


def test_bobs_cancel_tap_on_alices_rename_is_refused(gbot, run_async):
    _ask_rename(gbot, run_async, X1, ALICE, 8000)
    update, query = _tap("_:sx:0:rename-cancel", BOB, message_id=8000)

    run_async(session_parity.handle_callback(gbot, update, query, X1, "rename-cancel"))

    assert [c.args[0] for c in query.answer.await_args_list] == ["This isn't your rename."]
    assert (G, ALICE) in session_parity._rename_pending_map(gbot)
    query.edit_message_text.assert_not_awaited()


def test_bobs_cancel_on_alices_rename_is_refused_while_he_has_his_own(gbot, run_async):
    """Review 1 (a): the tapped question decides, not whether the tapper
    has a rename of their own."""
    _ask_rename(gbot, run_async, X1, ALICE, 8000)
    _ask_rename(gbot, run_async, X2, BOB, 8001)
    update, query = _tap("_:sx:0:rename-cancel", BOB, message_id=8000)

    run_async(session_parity.handle_callback(gbot, update, query, X1, "rename-cancel"))

    assert [c.args[0] for c in query.answer.await_args_list] == ["This isn't your rename."]
    query.edit_message_text.assert_not_awaited()
    assert set(session_parity._rename_pending_map(gbot)) == {(G, ALICE), (G, BOB)}


def test_her_cancel_on_bobs_rename_of_the_same_session_is_refused(gbot, run_async):
    """Review 1 (b): both rename x1; her tap on his question cancels
    neither his nor hers."""
    _ask_rename(gbot, run_async, X1, ALICE, 8000)
    _ask_rename(gbot, run_async, X1, BOB, 8001)
    update, query = _tap("_:sx:0:rename-cancel", ALICE, message_id=8001)

    run_async(session_parity.handle_callback(gbot, update, query, X1, "rename-cancel"))

    assert [c.args[0] for c in query.answer.await_args_list] == ["This isn't your rename."]
    query.edit_message_text.assert_not_awaited()
    assert set(session_parity._rename_pending_map(gbot)) == {(G, ALICE), (G, BOB)}


def test_the_cancel_handler_on_her_older_question_leaves_her_newer_one(gbot, run_async):
    """The handler alone (the moved-on pre-handler, which runs first in the
    app, closes the newer one anyway: any other action of hers)."""
    _ask_rename(gbot, run_async, X1, ALICE, 8000)
    _ask_rename(gbot, run_async, X2, ALICE, 8001)
    update, query = _tap("_:sx:0:rename-cancel", ALICE, message_id=8000)

    run_async(session_parity.handle_callback(gbot, update, query, X1, "rename-cancel"))

    assert session_parity._rename_pending_map(gbot).get((G, ALICE), {}).get("session_name") == X2


def test_her_new_card_ends_her_rename_but_not_bobs(gbot, run_async, mk_update):
    _ask_rename(gbot, run_async, X1, ALICE, 8000)
    _ask_rename(gbot, run_async, X2, BOB, 8001)

    async def _go():
        await new_flow.start_wizard(gbot, _msg(mk_update, "/new", ALICE, sent_id=7000))
        await asyncio.sleep(0)
    run_async(_go())

    assert set(session_parity._rename_pending_map(gbot)) == {(G, BOB)}
    assert (8000, session_parity._RENAME_CLOSED_TEXT) in _edits(gbot)


def test_a_new_card_that_never_went_out_keeps_her_rename(gbot, run_async, mk_update):
    from aipager.bot.transport import MUTED
    _ask_rename(gbot, run_async, X1, ALICE, 8000)
    up = _msg(mk_update, "/new", ALICE)
    up.message.reply_text = AsyncMock(return_value=MUTED)

    run_async(new_flow.start_wizard(gbot, up))

    assert (G, ALICE) in session_parity._rename_pending_map(gbot)


def test_her_rename_ends_her_card_but_not_bobs(gbot, run_async, mk_update):
    _open_card(gbot, run_async, mk_update, ALICE, 7000)
    _open_card(gbot, run_async, mk_update, BOB, 7001)

    async def _go():
        session_parity._start_rename_capture(gbot, G, gbot.registry.get(X1), ALICE, 8000)
        await asyncio.sleep(0)
    run_async(_go())

    assert set(new_flow._pending_store(gbot)) == {(G, BOB)}


def test_new_with_a_name_closes_her_rename(gbot, run_async, mk_update):
    _ask_rename(gbot, run_async, X1, ALICE, 8000)

    run_async(new_flow.create_from_text(gbot, _msg(mk_update, "/new x3", ALICE), "x3"))

    assert session_parity._rename_pending_map(gbot) == {}


def test_a_rename_question_that_never_went_out_waits_for_nothing(
        gbot, run_async, mk_update):
    from aipager.bot.transport import MUTED
    gbot.registry._sessions.pop(X2)
    up = _msg(mk_update, "/rename", ALICE)
    up.message.reply_text = AsyncMock(return_value=MUTED)

    run_async(session_parity.handle_rename_cmd(gbot, up, MagicMock()))

    assert session_parity._rename_pending_map(gbot) == {}


def test_rename_cmd_remembers_its_question(gbot, run_async, mk_update):
    gbot.registry._sessions.pop(X2)
    up = _msg(mk_update, "/rename", ALICE, sent_id=8123)

    run_async(session_parity.handle_rename_cmd(gbot, up, MagicMock()))

    assert session_parity._rename_pending_map(gbot)[(G, ALICE)]["msg_id"] == 8123
    assert _replies(up)[0].endswith("Reply to this message with the new name.")


# ---- DM parity --------------------------------------------------------------------

def test_dm_rename_takes_a_plain_message(gbot, run_async, mk_update):
    _ask_rename(gbot, run_async, "claude-d1", ALICE, 8000, chat=DM)

    _send(gbot, run_async, _msg(mk_update, "api", ALICE, chat=DM))

    assert _renamed(gbot) == [("claude-d1", "api")]
    gbot._inject_prompt.assert_not_awaited()


def test_dm_rename_expires_like_the_card(gbot, run_async, mk_update):
    gbot.registry.last_active_session = "claude-d1"
    _ask_rename(gbot, run_async, "claude-d1", ALICE, 8000, chat=DM)
    session_parity._rename_pending_map(gbot)[(DM, ALICE)]["last_active"] -= (
        new_flow._WIZARD_TTL_SECONDS + 1)

    _send(gbot, run_async, _msg(mk_update, "api", ALICE, chat=DM))

    assert _renamed(gbot) == []
    assert _injected(gbot) == [("d1", "api")]


def test_dm_rename_closes_on_a_command(gbot, run_async, mk_update):
    _ask_rename(gbot, run_async, "claude-d1", ALICE, 8000, chat=DM)

    async def _go():
        await new_flow.close_if_moved_on(gbot, _msg(mk_update, "/stop", ALICE, chat=DM))
        await asyncio.sleep(0)
    run_async(_go())

    assert session_parity._rename_pending_map(gbot) == {}
    assert (8000, session_parity._RENAME_CLOSED_TEXT) in _edits(gbot)


@pytest.mark.parametrize("text,reply_to", [("stop", None), ("fix it", ANSWER)])
def test_dm_rename_closes_on_a_message_that_is_not_the_answer(
        gbot, run_async, mk_update, text, reply_to):
    gbot.registry.last_active_session = "claude-d1"
    gbot._stop_session = AsyncMock()
    _ask_rename(gbot, run_async, "claude-d1", ALICE, 8000, chat=DM)

    _send(gbot, run_async, _msg(mk_update, text, ALICE, chat=DM, reply_to=reply_to))

    assert _renamed(gbot) == []
    assert session_parity._rename_pending_map(gbot) == {}
    assert (8000, session_parity._RENAME_CLOSED_TEXT) in _edits(gbot)


def test_dm_second_new_still_replaces_the_first(gbot, run_async, mk_update):
    _open_card(gbot, run_async, mk_update, ALICE, 7000, chat=DM)
    _open_card(gbot, run_async, mk_update, ALICE, 7001, chat=DM)

    assert (7000, "↩️ Cancelled - started over.") in _edits(gbot)
    assert list(new_flow._pending_store(gbot)) == [(DM, ALICE)]


def test_dm_card_answer_and_cancel(gbot, run_async, mk_update):
    _open_card(gbot, run_async, mk_update, ALICE, 7000, chat=DM)
    _send(gbot, run_async, _msg(mk_update, "x9", ALICE, chat=DM))
    assert gbot.create_session.await_args.args[0] == "x9"

    _open_card(gbot, run_async, mk_update, ALICE, 7002, chat=DM)
    update, query = _tap("_:nw:cancel", ALICE, chat=DM, message_id=7002)
    run_async(new_flow.handle_callback(gbot, update, query, "_", "nw:cancel"))
    assert new_flow._pending_store(gbot) == {}
    assert (7002, "↩️ Cancelled.") in _edits(gbot)


def test_a_card_whose_owner_is_unknown_names_no_one(gbot, run_async, mk_update):
    """No member to name (a personal-mode bot in a group): no "@unknown"."""
    gbot.scopes = None
    gbot.team = None
    a = _open_card(gbot, run_async, mk_update, ALICE, 7000)
    assert _replies(a)[0].startswith("🆕 <b>New session</b>\n\n")

    up = _msg(mk_update, "x3", BOB, reply_to=7000)
    run_async(new_flow.maybe_handle_text(gbot, up, MagicMock(), "x3"))
    assert _replies(up) == ["This card is someone else's. Send /new for your own."]


def test_the_owner_is_named_only_in_a_group():
    pending = {"owner_label": "@alice", "skip_perms": True, "can_auto": True}
    group_text, _kb = new_flow._render_name_card(pending, chat_id=G)
    dm_text, _kb = new_flow._render_name_card(pending, chat_id=DM)
    assert group_text.startswith("🆕 <b>New session</b> (@alice)\n\n")
    assert dm_text.startswith("🆕 <b>New session</b>\n\n")


def test_bobs_unreplied_message_never_answers_alices_card(gbot, run_async, mk_update):
    """Even if it reaches the router (the intake gate stops most of them)."""
    _open_card(gbot, run_async, mk_update, ALICE, 7000)

    handled = run_async(new_flow.maybe_handle_text(
        gbot, _msg(mk_update, "x5", BOB), MagicMock(), "x5"))

    assert handled is False
    gbot.create_session.assert_not_awaited()


def test_a_dm_reply_to_the_card_by_someone_else_is_not_refused(gbot, run_async, mk_update):
    """The "whose card" reply is group wording; a private chat keeps its
    old behaviour (a stranger's text is simply not the card's)."""
    _open_card(gbot, run_async, mk_update, ALICE, 7000, chat=DM)

    up = _msg(mk_update, "x3", BOB, chat=DM, reply_to=7000)
    handled = run_async(new_flow.maybe_handle_text(gbot, up, MagicMock(), "x3"))

    assert handled is False
    assert _replies(up) == []


def test_a_late_tap_on_his_own_closed_card_says_expired(gbot, run_async, mk_update):
    """Review 1: with alice's card open, bob's in-flight tap on his own
    card that just closed is not "someone else's"."""
    _open_card(gbot, run_async, mk_update, ALICE, 7000)
    _open_card(gbot, run_async, mk_update, BOB, 7001)
    update, query = _tap("_:nw:cancel", BOB, message_id=7001)
    run_async(new_flow.handle_callback(gbot, update, query, "_", "nw:cancel"))

    update, query = _tap("_:nw:mode:ask", BOB, message_id=7001)
    run_async(new_flow.handle_callback(gbot, update, query, "_", "nw:mode:ask"))

    assert query.answer.await_args.args[0] == "This card expired."
    assert (7001, new_flow._EXPIRED_TEXT) in _edits(gbot)
    assert (G, ALICE) in new_flow._pending_store(gbot)


def test_a_tap_with_no_message_id_on_someone_elses_card_is_refused(gbot, run_async, mk_update):
    """No tapped message to tell by: a person with no card of their own
    is refused while someone else's is open, as before."""
    _open_card(gbot, run_async, mk_update, ALICE, 7000)
    update, query = _tap("_:nw:mode:ask", BOB, message_id=7000)
    query.message = None

    run_async(new_flow.handle_callback(gbot, update, query, "_", "nw:mode:ask"))

    assert query.answer.await_args.args[0] == "This isn't your new session."
    assert new_flow._pending_store(gbot)[(G, ALICE)]["skip_perms"] is True
