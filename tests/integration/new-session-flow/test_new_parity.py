"""Every spelling of `/new` ends the same way (operator, 2026-09-30: "if we
send /new x1 or we send /new and then send x1 so these are two variant but I
want same experience for both").

Each pair runs through the real handlers (`_handle_new_cmd`, and
`_handle_message` for the answer to the Name card and for the keyboard word
`new`), with only the dtach launch faked. The final Ready card (text and
buttons) and the created session's state must be identical.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import preferences
from aipager.state import SessionRegistry

CHAT = 555
OWNER = 111


async def _launch_ok(*a, **kw):
    return True, ""


@pytest.fixture
def run_flow(mk_bot, mk_update, run_async, monkeypatch):
    """Run a list of chat messages through the real handlers on a fresh
    bot; return (final card text, its button labels, session state)."""
    monkeypatch.setattr("aipager.dtach.inject.launch_session",
                        AsyncMock(side_effect=_launch_ok))

    def _run(*messages, admin=True):
        bot = mk_bot(SessionRegistry())
        bot._app.bot.edit_message_text = AsyncMock()
        bot._app.bot.set_my_commands = AsyncMock()
        bot._is_admin_user = MagicMock(return_value=admin)
        bot._is_admin = MagicMock(return_value=admin)
        for i, text in enumerate(messages):
            update = mk_update(text, message_id=10 + i, chat_id=CHAT, user_id=OWNER)
            update.effective_chat.type = "private"
            update.message.reply_text = AsyncMock(
                return_value=MagicMock(message_id=900 + i))
            if text.startswith("/new"):
                run_async(bot._handle_new_cmd(update, MagicMock()))
            else:
                run_async(bot._handle_message(update, MagicMock()))
        kw = bot._app.bot.edit_message_text.await_args.kwargs
        buttons = [b.text for row in kw["reply_markup"].inline_keyboard for b in row]
        sessions = [s for s in bot.registry.all_sessions().values() if s.label]
        assert len(sessions) == 1, sessions
        sess = sessions[0]
        state = {"label": sess.label, "skip_perms": sess.skip_perms,
                 "queued": [q[0] for q in sess.pending_queue],
                 "cwd": sess.cwd, "target": bot.registry.last_active_session == sess.name}
        return kw["text"], buttons, state
    return _run


@pytest.mark.parametrize("direct, answered", [
    (("/new x1",), ("/new", "x1")),
    (("/new x1 fix the tests",), ("/new", "x1 fix the tests")),
    (("/new !x1",), ("/new", "!x1")),
    (("/new x1",), ("new", "x1")),              # the keyboard word
])
def test_both_spellings_end_the_same(run_flow, direct, answered):
    assert run_flow(*direct) == run_flow(*answered)


def test_the_result_is_auto_ready_and_targeted(run_flow):
    text, buttons, state = run_flow("/new x1")
    assert state == {"label": "x1", "skip_perms": True, "queued": [],
                     "cwd": "", "target": True}
    assert "✅ <b>x1</b> is ready" in text and "🤖 Auto" in text
    assert "✍️ Just send a message, it goes to x1." in text
    assert buttons == ["💬 Switch to Ask", "🧠 Model"]


@pytest.mark.parametrize("name", ["x1", "!x1"])
def test_a_non_admin_gets_ask_on_both_spellings(run_flow, name):
    direct = run_flow(f"/new {name}", admin=False)
    answered = run_flow("/new", name, admin=False)
    assert direct[2]["skip_perms"] is False
    assert direct == answered
    # Asking for Auto (the `!`) is answered with why it asks instead.
    assert ("Auto mode needs an admin" in direct[0]) is name.startswith("!")


def test_the_chats_default_ask_applies_to_both_spellings(run_flow):
    preferences.set_new_session_default(CHAT, "mode", "ask")
    direct = run_flow("/new x1")
    answered = run_flow("/new", "x1")
    assert direct[2]["skip_perms"] is False
    assert direct == answered


def test_an_invalid_name_gives_the_same_reason_on_both_spellings(
        mk_bot, mk_update, run_async):
    reasons = []
    for messages in (("/new b@d",), ("/new", "b@d")):
        bot = mk_bot(SessionRegistry())
        bot._app.bot.edit_message_text = AsyncMock()
        last_reply = None
        for i, text in enumerate(messages):
            update = mk_update(text, message_id=10 + i, chat_id=CHAT, user_id=OWNER)
            update.message.reply_text = AsyncMock(
                return_value=MagicMock(message_id=900 + i))
            if text.startswith("/new"):
                run_async(bot._handle_new_cmd(update, MagicMock()))
            else:
                run_async(bot._handle_message(update, MagicMock()))
            last_reply = update.message.reply_text
        if bot._app.bot.edit_message_text.await_count:
            shown = bot._app.bot.edit_message_text.await_args.kwargs["text"]
        else:
            shown = last_reply.await_args.args[0]
        reasons.append([ln for ln in shown.split("\n") if ln.startswith("⚠️")])
    assert reasons[0] == reasons[1] and reasons[0]


# ---- a name already in use: the same card, and Replace keeps the choices ----

def _query(message_id, *, user=OWNER):
    query = MagicMock()
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.from_user = MagicMock(id=user)
    query.message = MagicMock(message_id=message_id)
    update = MagicMock()
    update.callback_query = query
    update.effective_user = query.from_user
    update.effective_chat = MagicMock(id=CHAT, type="private")
    return update, query


def _conflict_bot(mk_bot):
    from aipager.state import Status, TrackedSession

    bot = mk_bot(SessionRegistry())
    bot._app.bot.edit_message_text = AsyncMock()
    bot._app.bot.set_my_commands = AsyncMock()
    bot._is_admin_user = MagicMock(return_value=True)
    bot._is_admin = MagicMock(return_value=True)
    # Finished but resumable: the conflict card, and a Replace with no kill.
    bot.registry._sessions["claude-jim"] = TrackedSession(
        name="claude-jim", label="jim", status=Status.GONE,
        claude_session_id="abc", scope_chat_id=CHAT)
    return bot


def test_the_conflict_card_is_the_same_on_both_spellings(mk_bot, mk_update, run_async):
    def _card(*messages):
        bot = _conflict_bot(mk_bot)
        replies = []
        for i, text in enumerate(messages):
            update = mk_update(text, message_id=10 + i, chat_id=CHAT, user_id=OWNER)
            update.effective_chat.type = "private"
            update.message.reply_text = AsyncMock(
                return_value=MagicMock(message_id=900 + i))
            if text.startswith("/new"):
                run_async(bot._handle_new_cmd(update, MagicMock()))
            else:
                run_async(bot._handle_message(update, MagicMock()))
            replies += update.message.reply_text.await_args_list
        return bot, replies

    direct_bot, direct_replies = _card("/new jim do it")
    answered_bot, answered_replies = _card("/new", "jim do it")

    (direct,) = direct_replies                       # the conflict card, as a reply
    d_text, d_kb = direct.args[0], direct.kwargs["reply_markup"]
    # The bare path's one message: the Name card, edited into the conflict card.
    assert len(answered_replies) == 1
    edit = answered_bot._app.bot.edit_message_text.await_args.kwargs
    assert edit["message_id"] == 900
    assert edit["text"] == d_text
    labels = [[b.text for b in row] for row in d_kb.inline_keyboard]
    assert [[b.text for b in row] for row in edit["reply_markup"].inline_keyboard] == labels
    assert direct_bot._new_conflict_pending["claude-jim"]["prompt"] == "do it"
    assert answered_bot._new_conflict_pending["claude-jim"]["prompt"] == "do it"


def test_replace_keeps_the_model_folder_and_first_message_picked_on_the_card(
        mk_bot, mk_update, run_async, monkeypatch, tmp_path):
    from aipager.bot import new_flow
    from aipager.config import MODEL_CHOICES
    from aipager.miniapp import launch

    folder = str(tmp_path / "proj")
    monkeypatch.setattr(launch, "allowed_roots", lambda reg, chat, **_kw: [folder])
    launched = AsyncMock(side_effect=_launch_ok)
    monkeypatch.setattr("aipager.dtach.inject.launch_session", launched)
    bot = _conflict_bot(mk_bot)

    update = mk_update("/new", message_id=10, chat_id=CHAT, user_id=OWNER)
    update.effective_chat.type = "private"
    update.message.reply_text = AsyncMock(return_value=MagicMock(message_id=900))
    run_async(bot._handle_new_cmd(update, MagicMock()))
    for data in ("nw:opt:model", "nw:model:1", "nw:opt:path", "nw:path:0"):
        cb_update, cb_query = _query(900)
        run_async(new_flow.handle_callback(bot, cb_update, cb_query, "_", data))

    update = mk_update("jim do it", message_id=11, chat_id=CHAT, user_id=OWNER)
    update.effective_chat.type = "private"
    update.message.reply_text = AsyncMock()
    run_async(bot._handle_message(update, MagicMock()))

    card = bot._app.bot.edit_message_text.await_args.kwargs
    (replace,) = [b.callback_data for row in card["reply_markup"].inline_keyboard
                  for b in row if "Replace" in b.text]
    cb_update, cb_query = _query(900)
    cb_query.data = replace
    run_async(bot._handle_callback(cb_update, MagicMock()))

    wanted, _err = launch.validate_model(MODEL_CHOICES[1][0], MODEL_CHOICES)
    kw = launched.await_args.kwargs
    assert (kw["model"], kw["cwd"], kw["skip_perms"]) == (wanted, folder, True)
    fresh = bot.registry.get("claude-jim")
    assert [q[0] for q in fresh.pending_queue] == ["do it"]
    # And its Ready card names the model by its label.
    texts = [str(c.args[0]) if c.args else str(c.kwargs.get("text"))
             for c in cb_query.edit_message_text.await_args_list]
    assert any(f"🧠 {MODEL_CHOICES[1][0]} ·" in t for t in texts), texts
