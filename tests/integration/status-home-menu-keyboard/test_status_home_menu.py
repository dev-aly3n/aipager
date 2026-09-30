"""P3 of the command redesign (2026-09-30): /status is a list you can act
on, /start is a home screen, /help a short guide, the / menu puts the
sessions and the frequent commands first, and the main keyboard trades
`kill` for `new` (End moves to the ⋮ menu).
"""

from __future__ import annotations

import re
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.bot import new_flow, session_parity
from aipager.state import SessionRegistry, Status, TrackedSession

CHAT = 12345
OTHER_CHAT = 999


def _sess(bot, label, status=Status.IDLE, *, chat=CHAT, **fields):
    s = TrackedSession(name=f"claude-{label}", label=label, status=status)
    s.scope_chat_id = chat
    for k, v in fields.items():
        setattr(s, k, v)
    bot.registry._sessions[s.name] = s
    return s


@pytest.fixture
def bot(mk_bot, monkeypatch):
    b = mk_bot(SessionRegistry())
    b._read_status_file = MagicMock(return_value=None)
    b._maybe_update_bot_name = AsyncMock()
    b._update_bot_commands = AsyncMock()

    async def _alive(name):
        # A live session's socket is there; an ended one's is not (else
        # /status's reconcile would bring it back).
        sess = b.registry.get(name)
        return sess is not None and sess.status != Status.GONE
    monkeypatch.setattr("aipager.dtach.inject.is_alive", _alive)
    monkeypatch.setattr("aipager.dtach.inject.list_sessions", AsyncMock(return_value=[]))
    return b


def _status(bot, mk_update, run_async, *, chat=CHAT, private=False):
    update = mk_update("/status", chat_id=chat)
    update.effective_chat.type = "private" if private else "group"
    run_async(bot._handle_status(update, MagicMock()))
    call = update.message.reply_text.await_args
    return call.args[0], call.kwargs["reply_markup"]


def _rows(kb):
    return [[b.text for b in row] for row in kb.inline_keyboard]


def _resolve(bot, cb, chat=CHAT):
    return session_parity.resolve_short_cb(bot, chat, *cb.split(":", 1))


# ---- /status: the list -----------------------------------------------------------

def test_each_state_reads_in_words_with_the_target_marked(bot, mk_update, run_async):
    _sess(bot, "idle1")
    busy = _sess(bot, "work1", Status.BUSY, busy_started_at=time.monotonic() - 185,
                 tool_history=[("Bash: run the tests", False)])
    _sess(bot, "wait1", Status.INTERACTIVE,
          pending_permission={"tool_summary": "Bash: make deploy"})
    _sess(bot, "boot1", Status.UNKNOWN)
    bot.registry.last_active_session = busy.name

    text, _kb = _status(bot, mk_update, run_async)

    blocks = text.split("\n\n")
    assert blocks[0] == "📊 <b>Sessions (4)</b>"
    assert blocks[1:] == [
        "<b>boot1</b> · 🔄 starting",
        "<b>idle1</b> · 💤 idle",
        "<b>wait1</b> · ⏳ needs you · Bash: make deploy",
        "✍️ <b>work1</b> · ⚙️ working 3m · Bash: run the tests",
    ]
    assert "—" not in text


def test_the_details_line_says_model_context_cost_queue_and_agents(bot, mk_update, run_async):
    sess = _sess(bot, "x1", model_name="Opus 5.5", last_token_pct=42,
                 active_subagents={"a1": {}, "a2": {}}, bg_shells={"b1": {}})
    sess.queue_prompt("later", 1)
    bot._read_status_file = MagicMock(return_value={"model": "Opus 5.5", "ctx_pct": 42,
                                                     "cost": 3046.5})

    text, _kb = _status(bot, mk_update, run_async)

    assert text.split("\n")[-1] == (
        "   Opus 5.5 · ctx 42% · $3,046.50 · queue 1 (1 queued, 0 notes) · "
        "2 agents · 1 shell")


def test_the_buttons_are_what_each_state_can_do(bot, mk_update, run_async):
    idle = _sess(bot, "a1")
    busy = _sess(bot, "b1", Status.BUSY)
    wait = _sess(bot, "c1", Status.INTERACTIVE)

    _text, kb = _status(bot, mk_update, run_async)

    assert _rows(kb)[:3] == [["✍️ a1", "⋮"], ["✍️ b1", "⏹ Stop", "⋮"],
                             ["✍️ c1", "Answer", "⋮"]]
    resolved = [[_resolve(bot, b.callback_data) for b in row] for row in kb.inline_keyboard[:3]]
    assert resolved == [
        [(idle.name, "talk"), (idle.name, "menu")],
        [(busy.name, "talk"), (busy.name, f"ststop{busy.turn_key}"), (busy.name, "menu")],
        [(wait.name, "talk"), (wait.name, "pin_answer"), (wait.name, "menu")],
    ]


def test_the_footer_offers_new_ended_and_the_app(bot, mk_update, run_async):
    _sess(bot, "a1")
    _sess(bot, "gone1", Status.GONE, gone_at=time.time())
    _sess(bot, "gone2", Status.GONE, gone_at=time.time(), hidden_from_status=True)
    bot._miniapp_url = "https://example.invalid/app"

    _text, kb = _status(bot, mk_update, run_async, private=True)

    rows = kb.inline_keyboard
    assert [b.text for b in rows[-2]] == ["🆕 New", "⚫ Ended (1)"]
    assert [b.callback_data for b in rows[-2]] == ["_:nw:open", "_:st:ended"]
    assert rows[-1][0].web_app is not None


def test_an_empty_chat_offers_a_new_session(bot, mk_update, run_async):
    text, kb = _status(bot, mk_update, run_async)

    assert text == "No sessions yet."
    assert _rows(kb) == [["🆕 New session"]]


def test_only_ended_sessions_says_so(bot, mk_update, run_async):
    _sess(bot, "gone1", Status.GONE, gone_at=time.time())
    _sess(bot, "gone1b", Status.GONE, gone_at=time.time())

    text, kb = _status(bot, mk_update, run_async)

    assert text == "No live sessions."
    assert _rows(kb) == [["🆕 New session", "⚫ Ended (2)"]]


def test_another_chats_sessions_never_show(bot, mk_update, run_async):
    _sess(bot, "mine")
    _sess(bot, "theirs", chat=OTHER_CHAT)

    text, kb = _status(bot, mk_update, run_async)

    assert "mine" in text and "theirs" not in text


# ---- the Ended view --------------------------------------------------------------

def test_the_ended_view_resumes_deletes_clears_and_goes_back(bot):
    with_history = _sess(bot, "old1", Status.GONE, gone_at=time.time(),
                         claude_session_id="abc")
    no_history = _sess(bot, "old2", Status.GONE, gone_at=time.time() - 60)

    text, kb = bot._render_ended_view(CHAT)

    assert text.startswith("⚫ <b>Ended sessions (2)</b>")
    assert _rows(kb) == [["▶️ old1", "🗑"], ["🗑 old2"], ["🧹 Clear all", "« Back"]]
    assert [_resolve(bot, b.callback_data) for b in kb.inline_keyboard[0]] == [
        (with_history.name, "resume"), (with_history.name, "delete")]
    assert _resolve(bot, kb.inline_keyboard[1][0].callback_data) == (no_history.name, "delete")
    assert [b.callback_data for b in kb.inline_keyboard[-1]] == ["_:clear_gone", "_:st:list"]


def test_the_ended_view_lists_the_newest_ten(bot):
    now = time.time()
    for i in range(12):
        _sess(bot, f"old{i:02d}", Status.GONE, gone_at=now - i)

    text, kb = bot._render_ended_view(CHAT)

    assert len(kb.inline_keyboard) == 11
    assert kb.inline_keyboard[0][0].text == "🗑 old00"
    assert "The 10 newest are here; /resume lists all." in text


@pytest.fixture
def tap(bot, run_async):
    def _tap(data, *, user=1, chat=CHAT):
        query = MagicMock(data=data)
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        query.from_user = MagicMock(id=user)
        query.message = MagicMock(message_id=4242)
        update = MagicMock(callback_query=query, message=None, effective_user=query.from_user)
        update.effective_chat = MagicMock(id=chat, type="group")
        run_async(bot._handle_callback(update, MagicMock()))
        return query
    return _tap


def _edited(query):
    call = query.edit_message_text.await_args
    return (call.args[0] if call.args else call.kwargs["text"]), call.kwargs.get("reply_markup")


def test_ended_and_back_edit_the_status_in_place(bot, tap):
    _sess(bot, "a1")
    _sess(bot, "old1", Status.GONE, gone_at=time.time())

    text, _kb = _edited(tap("_:st:ended"))
    assert text.startswith("⚫ <b>Ended sessions (1)</b>")
    text, _kb = _edited(tap("_:st:list"))
    assert text.startswith("📊 <b>Sessions (1)</b>")


def test_clear_all_clears_only_this_chats_ended_sessions(bot, tap):
    mine = _sess(bot, "old1", Status.GONE, gone_at=time.time())
    theirs = _sess(bot, "old2", Status.GONE, gone_at=time.time(), chat=OTHER_CHAT)

    query = tap("_:clear_gone")

    assert mine.hidden_from_status is True
    assert theirs.hidden_from_status is False
    assert _edited(query)[0] == "No sessions yet."


# ---- ✍️ and 🆕 from /status ---------------------------------------------------

def test_talk_makes_it_this_chats_target_and_moves_the_marker(bot, tap, mk_update, run_async):
    a = _sess(bot, "a1")
    b = _sess(bot, "b1")
    bot.registry.last_active_session = a.name
    _text, kb = _status(bot, mk_update, run_async)
    talk_b = kb.inline_keyboard[1][0].callback_data

    query = tap(talk_b)

    assert bot.registry.target_for(CHAT) is b
    assert "✍️ Messages go to b1" in query.answer.await_args.args[0]
    assert "✍️ <b>b1</b>" in _edited(query)[0]
    bot._maybe_update_bot_name.assert_awaited()


def test_talk_is_refused_for_an_ended_session_or_someone_who_may_not_prompt(
        bot, tap, mk_update, run_async):
    a = _sess(bot, "a1")
    b = _sess(bot, "b1")
    bot.registry.last_active_session = a.name
    _text, kb = _status(bot, mk_update, run_async)
    talk_b = kb.inline_keyboard[1][0].callback_data

    bot._can_prompt_user = MagicMock(return_value=False)
    tap(talk_b)
    assert bot.registry.target_for(CHAT) is a

    bot._can_prompt_user = MagicMock(return_value=True)
    b.status = Status.GONE
    tap(talk_b)
    assert bot.registry.target_for(CHAT) is a


def test_new_opens_a_name_card_owned_by_the_tapper(bot, tap, monkeypatch):
    sent = AsyncMock(return_value=MagicMock(message_id=777))
    monkeypatch.setattr(new_flow, "send_text", sent)

    tap("_:nw:open", user=55)

    card = bot._new_wizard_pending[CHAT]
    assert (card["user_id"], card["msg_id"], card["step"]) == (55, 777, "name")
    assert "New session" in sent.await_args.args[2]


def test_new_is_refused_for_someone_who_may_not_prompt(bot, tap, monkeypatch):
    sent = AsyncMock(return_value=MagicMock(message_id=777))
    monkeypatch.setattr(new_flow, "send_text", sent)
    bot._can_prompt_user = MagicMock(return_value=False)

    query = tap("_:nw:open", user=55)

    sent.assert_not_awaited()
    assert CHAT not in getattr(bot, "_new_wizard_pending", {})
    assert "can't start" in query.answer.await_args.args[0]


# ---- /start and /help ------------------------------------------------------------

def _start(bot, mk_update, run_async, chat=CHAT, private=False):
    bot._app.bot.send_message = AsyncMock()
    bot._send_keyboard = AsyncMock()
    update = mk_update("/start", chat_id=chat)
    update.effective_chat.type = "private" if private else "group"
    run_async(bot._handle_start_cmd(update, MagicMock()))
    call = bot._app.bot.send_message.await_args
    return call.args[1], call.kwargs["reply_markup"]


def test_start_is_this_chats_home_screen(bot, mk_update, run_async):
    mine = _sess(bot, "mine")
    _sess(bot, "theirs", chat=OTHER_CHAT)
    bot.registry.last_active_session = mine.name

    text, kb = _start(bot, mk_update, run_async)

    assert text.startswith("👋 <b>aipager</b> - Claude Code from Telegram\n\n📊")
    assert "mine" in text and "theirs" not in text
    assert "✍️ Messages go to <b>mine</b>." in text
    assert [(b.text, b.callback_data) for b in kb.inline_keyboard[0]] == [
        ("🆕 New session", "_:nw:open"), ("⚙️ Settings", "_:set:back")]
    bot._send_keyboard.assert_awaited_once()


@pytest.mark.parametrize("setup, line", [
    ("none", "Start one with /new."),
    ("no-target", "Tap a session on the keyboard to talk to it."),
])
def test_start_says_what_to_do_next(bot, mk_update, run_async, setup, line):
    if setup == "no-target":
        _sess(bot, "x1")

    text, _kb = _start(bot, mk_update, run_async)

    assert line in text


def test_help_is_the_short_guide(bot, mk_update, run_async):
    from aipager.bot.handlers import HELP_TEXT

    update = mk_update("/help", chat_id=CHAT)
    run_async(bot._handle_help_cmd(update, MagicMock()))

    assert update.message.reply_text.await_args.args[0] == HELP_TEXT
    assert "—" not in HELP_TEXT
    assert "Long-press" in HELP_TEXT and "Tab" in HELP_TEXT


def _dispatched_commands(mk_bot, run_async):
    from telegram.ext import CommandHandler

    b = mk_bot()
    registered = []
    stub = MagicMock()
    stub.add_handler = MagicMock(side_effect=lambda h, *a, **k: registered.append(h))
    stub.bot = MagicMock()
    stub.bot.set_my_commands = AsyncMock()
    stub.initialize = AsyncMock()
    stub.start = AsyncMock()
    stub.updater = MagicMock()
    stub.updater.start_polling = AsyncMock()
    b._make_builder = MagicMock(return_value=MagicMock(build=MagicMock(return_value=stub)))
    b._update_bot_commands = AsyncMock()
    try:
        run_async(b.start())
    except Exception:
        pass
    return {c for h in registered if isinstance(h, CommandHandler) for c in h.commands}


def test_help_names_only_commands_that_answer(mk_bot, run_async):
    from aipager.bot.handlers import HELP_TEXT

    dispatched = _dispatched_commands(mk_bot, run_async)
    plain = re.sub(r"<[^>]+>", "", HELP_TEXT)
    named = set(re.findall(r"(?<![\w/])/([a-z]+)", plain))
    assert named and named <= dispatched, sorted(named - dispatched)


def test_help_is_its_own_handler(mk_bot, run_async):
    from telegram.ext import CommandHandler

    b = mk_bot()
    registered = []
    stub = MagicMock()
    stub.add_handler = MagicMock(side_effect=lambda h, *a, **k: registered.append(h))
    stub.bot = MagicMock()
    stub.bot.set_my_commands = AsyncMock()
    stub.initialize = AsyncMock()
    stub.start = AsyncMock()
    stub.updater = MagicMock()
    stub.updater.start_polling = AsyncMock()
    b._make_builder = MagicMock(return_value=MagicMock(build=MagicMock(return_value=stub)))
    b._update_bot_commands = AsyncMock()
    try:
        run_async(b.start())
    except Exception:
        pass
    (help_handler,) = [h for h in registered
                       if isinstance(h, CommandHandler) and "help" in h.commands]
    assert help_handler.callback == b._handle_help_cmd


# ---- the / menu and the keyboard -------------------------------------------------

def test_the_menu_puts_sessions_then_the_frequent_commands_first(mk_bot, monkeypatch):
    from aipager.bot.lifecycle import LifecycleMixin

    monkeypatch.setattr("aipager.config.MINIAPP_ENABLED", False)
    commands = LifecycleMixin._command_list({"x1", "dev"})

    assert [(c.command, c.description) for c in commands[:2]] == [
        ("dev", "Talk to dev"), ("x1", "Talk to x1")]
    assert [c.command for c in commands[2:]] == [
        "new", "status", "stop", "now", "resume", "mode", "settings", "help"]


def test_the_rare_commands_leave_the_menu_but_still_answer(mk_bot, run_async):
    from aipager.bot.lifecycle import LifecycleMixin

    menu = {c.command for c in LifecycleMixin._command_list(set())}
    # /perms is /mode's old name since P4 (typed only).
    rare = {"kill", "restart", "rename", "delete", "diff", "clearqueue", "whoami", "update",
            "perms"}
    assert not (menu & rare)
    assert rare <= _dispatched_commands(mk_bot, run_async)


def test_the_keyboard_has_new_where_kill_was(mk_bot, run_async):
    b = mk_bot()
    b._app.bot.send_message = AsyncMock()
    run_async(b._send_keyboard(level="main", chat_id=CHAT))

    kb = b._app.bot.send_message.await_args.kwargs["reply_markup"]
    rows = [[btn.text for btn in row] for row in kb.keyboard]
    assert ["status", "stop", "new"] in rows
    assert not any("kill" in row for row in rows)


# ---- ⋮ → End session ----------------------------------------------------------------

def test_a_live_sessions_menu_offers_end_with_a_confirm(bot, tap):
    sess = _sess(bot, "x1")
    text, kb = session_parity._render_session_menu(bot, CHAT, sess)
    end_cbs = [b.callback_data for row in kb.inline_keyboard for b in row
               if b.text == "⏹ End session"]
    assert len(end_cbs) == 1, "a live session's ⋮ menu offers End"
    end_cb = end_cbs[0]

    query = tap(end_cb)

    text, kb = _edited(query)
    assert text == "⏹ End <b>x1</b>? Claude stops and the session closes."
    assert [(b.text, _resolve(bot, b.callback_data)) for b in kb.inline_keyboard[0]] == [
        ("⏹ End", (sess.name, f"endok{sess.turn_key}")), ("Cancel", (sess.name, "kill-cancel"))]


def test_an_ended_session_has_no_end_and_refuses_one(bot, tap):
    sess = _sess(bot, "x1", Status.GONE)
    _text, kb = session_parity._render_session_menu(bot, CHAT, sess)
    assert "⏹ End session" not in [b.text for row in kb.inline_keyboard for b in row]

    end_cb = session_parity.session_cb(bot, CHAT, sess, "end")
    query = tap(end_cb)
    toasts = [c.args[0] for c in query.answer.await_args_list if c.args and c.args[0]]
    assert any("already ended" in t for t in toasts), toasts


def test_end_is_refused_for_someone_who_may_not_prompt(bot, tap):
    sess = _sess(bot, "x1")
    bot._can_prompt_user = MagicMock(return_value=False)

    query = tap(session_parity.session_cb(bot, CHAT, sess, "end"))

    query.edit_message_text.assert_not_awaited()
    assert "can't end" in query.answer.await_args.args[0]


# ---- review-1 fixes -------------------------------------------------------------------

def _stop_cb(bot, sess):
    return session_parity.session_cb(bot, CHAT, sess, f"ststop{sess.turn_key}")


def test_stop_from_status_stops_and_redraws_the_list(bot, tap):
    """Review-1 (001): it used to overwrite the whole list with one line."""
    busy = _sess(bot, "b1", Status.BUSY)
    _sess(bot, "a1")
    bot._stop_session_core = AsyncMock(return_value=MagicMock(ok=True, dropped=0, label="b1"))

    query = tap(_stop_cb(bot, busy))

    bot._stop_session_core.assert_awaited_once_with(busy)
    assert "⏹ Stopped b1" in query.answer.await_args.args[0]
    assert _edited(query)[0].startswith("📊 <b>Sessions (2)</b>")


def test_stop_from_status_refuses_a_turn_that_moved_on(bot, tap):
    busy = _sess(bot, "b1", Status.BUSY)
    cb = _stop_cb(bot, busy)
    bot.registry.transition(busy.name, Status.IDLE)
    bot.registry.transition(busy.name, Status.BUSY)   # a new turn since the list was drawn
    bot._stop_session_core = AsyncMock(return_value=MagicMock(ok=True, dropped=0, label="b1"))

    query = tap(cb)

    bot._stop_session_core.assert_not_awaited()
    assert "moved on" in query.answer.await_args.args[0]
    assert _edited(query)[0].startswith("📊")


def test_stop_from_status_survives_a_card_move_in_the_same_turn(bot, tap):
    """Review-1 (002): the busy card moving mid-turn made a message-id check
    call the tap stale; the turn is what counts."""
    busy = _sess(bot, "b1", Status.BUSY, busy_msg_id=99999)
    bot._stop_session_core = AsyncMock(return_value=MagicMock(ok=True, dropped=0, label="b1"))

    tap(_stop_cb(bot, busy))

    bot._stop_session_core.assert_awaited_once()


def test_stop_from_status_needs_the_right_to_prompt(bot, tap):
    busy = _sess(bot, "b1", Status.BUSY)
    bot._stop_session_core = AsyncMock(return_value=MagicMock(ok=True, dropped=0, label="b1"))
    bot._can_prompt_user = MagicMock(return_value=False)

    tap(_stop_cb(bot, busy))

    bot._stop_session_core.assert_not_awaited()


def _end_ok_cb(bot, sess, turn=None):
    return session_parity.session_cb(
        bot, CHAT, sess, f"endok{sess.turn_key if turn is None else turn}")


def test_end_ends_the_session_and_redraws_the_list(bot, tap):
    sess = _sess(bot, "x1", busy_msg_id=99999)   # the card moved: no matter
    bot._kill_session_core = AsyncMock(return_value=MagicMock(result="killed"))

    query = tap(_end_ok_cb(bot, sess))

    bot._kill_session_core.assert_awaited_once_with(sess.name, "x1")
    assert "⏹ Ended x1" in query.answer.await_args.args[0]
    # P4: what happened stays above what remains.
    assert _edited(query)[0].startswith("⏹ Ended <b>x1</b>\n\n📊")


def test_end_refuses_work_that_started_after_it_was_tapped(bot, tap):
    """Review-1 (006): the stale refusal, now by turn."""
    sess = _sess(bot, "x1", Status.BUSY)
    cb = _end_ok_cb(bot, sess)
    bot.registry.transition(sess.name, Status.IDLE)
    bot.registry.transition(sess.name, Status.BUSY)   # new work since End was tapped
    bot._kill_session_core = AsyncMock(return_value=MagicMock(result="killed"))

    query = tap(cb)

    bot._kill_session_core.assert_not_awaited()
    assert "moved on" in query.answer.await_args.args[0]


def test_end_needs_the_right_to_prompt(bot, tap):
    sess = _sess(bot, "x1")
    bot._kill_session_core = AsyncMock(return_value=MagicMock(result="killed"))
    bot._can_prompt_user = MagicMock(return_value=False)

    tap(_end_ok_cb(bot, sess))

    bot._kill_session_core.assert_not_awaited()


@pytest.mark.parametrize("verb", ["kill", "kill-confirm"])
@pytest.mark.parametrize("may_prompt", [True, False])
def test_the_old_kill_buttons_end_nothing(bot, tap, verb, may_prompt):
    """Review-1 (003) gated these on the right to prompt; P4 retired them
    (review-1 of P4, 003): nothing renders them any more, and nothing can
    check which chat or turn one still in a chat was shown for."""
    sess = _sess(bot, "x1")
    bot._kill_session_core = AsyncMock(return_value=MagicMock(result="killed"))
    bot._can_prompt_user = MagicMock(return_value=may_prompt)

    query = tap(session_parity.session_cb(bot, CHAT, sess, verb))

    bot._kill_session_core.assert_not_awaited()
    assert query.answer.await_args.args[0] == "This button is out of date - send /kill again"


def test_talk_refuses_another_chats_session(bot, tap):
    """Review-1 (006): the other-chat check had no test."""
    mine = _sess(bot, "a1")
    theirs = _sess(bot, "b1", chat=OTHER_CHAT)
    bot.registry.last_active_session = mine.name

    query = tap(f"{theirs.name}:talk")

    assert bot.registry.target_for(CHAT) is mine
    assert "isn't running here" in query.answer.await_args.args[0]


def test_start_has_no_inline_app_row_and_resume_only_with_something_to_resume(
        bot, mk_update, run_async):
    """Review-1 (005, 007): the keyboard sent right after carries 📱 App;
    Resume with nothing to resume swapped the screen for one line."""
    bot._miniapp_url = "https://example.invalid/app"
    _text, kb = _start(bot, mk_update, run_async, private=True)
    assert _rows(kb) == [["🆕 New session", "⚙️ Settings"]]

    _sess(bot, "old1", Status.GONE, gone_at=time.time(), claude_session_id="abc")
    _text, kb = _start(bot, mk_update, run_async)
    assert _rows(kb) == [["🆕 New session", "↩️ Resume", "⚙️ Settings"]]


# ---- review-2 fixes -------------------------------------------------------------------

def test_an_old_stop_never_stops_a_recreated_session_of_the_same_name(bot, tap):
    """Review-2 (001): a new entry's turn counter starts again at 0, so an
    old /status Stop could carry the new session's first turn. The key a
    button carries is new for every session and every turn."""
    old = _sess(bot, "x1", Status.BUSY)
    cb = _stop_cb(bot, old)
    del bot.registry._sessions[old.name]            # ended, then /new x1
    new = _sess(bot, "x1", Status.BUSY)
    bot._stop_session_core = AsyncMock(return_value=MagicMock(ok=True, dropped=0, label="b1"))

    query = tap(cb)

    bot._stop_session_core.assert_not_awaited()
    assert "moved on" in query.answer.await_args.args[0]
    assert new.turn_key != old.turn_key


def test_an_old_end_never_ends_a_recreated_session_of_the_same_name(bot, tap):
    old = _sess(bot, "x1")
    cb = _end_ok_cb(bot, old)
    del bot.registry._sessions[old.name]
    _sess(bot, "x1")
    bot._kill_session_core = AsyncMock(return_value=MagicMock(result="killed"))

    tap(cb)

    bot._kill_session_core.assert_not_awaited()


def test_turn_keys_do_not_repeat_across_a_restart(tmp_state_file):
    """Keys are seeded from the clock when the daemon starts, so a
    restarted daemon (a new process, a fresh counter) starts above every
    key the old one handed out. Within one process: new for every turn."""
    from aipager import state

    assert next(state._TURN_KEYS) > time.time_ns() // 1000 - 10**6 * 86400 * 365
    reg = SessionRegistry()
    reg.transition("claude-x1", Status.BUSY)
    before = reg.get("claude-x1").turn_key
    reg.transition("claude-x1", Status.IDLE)
    reg.transition("claude-x1", Status.BUSY)
    assert reg.get("claude-x1").turn_key > before


def test_stop_from_status_says_how_many_queued_messages_went(bot, tap):
    """Review-2 (003): the count the /stop wrapper reports stays visible."""
    busy = _sess(bot, "b1", Status.BUSY)
    bot._stop_session_core = AsyncMock(return_value=MagicMock(ok=True, dropped=2, label="b1"))

    query = tap(_stop_cb(bot, busy))

    assert query.answer.await_args.args[0] == "⏹ Stopped b1 (2 queued messages discarded)"


@pytest.mark.parametrize("result, toast", [
    ("resuming", "x1 is being resumed - try again in a moment"),
    ("still_running", "x1 did not stop - try again"),
    ("not_found", "x1 was not found"),
])
def test_a_failed_end_says_why_in_words(bot, tap, result, toast):
    sess = _sess(bot, "x1")
    bot._kill_session_core = AsyncMock(return_value=MagicMock(result=result))

    query = tap(_end_ok_cb(bot, sess))

    assert query.answer.await_args.args[0] == toast


@pytest.mark.parametrize("verb", ["ststop", "endok"])
def test_stop_and_end_refuse_another_chats_session(bot, tap, verb):
    theirs = _sess(bot, "b1", Status.BUSY, chat=OTHER_CHAT)
    bot._stop_session_core = AsyncMock(return_value=MagicMock(ok=True, dropped=0, label="b1"))
    bot._kill_session_core = AsyncMock(return_value=MagicMock(result="killed"))

    query = tap(f"{theirs.name}:{verb}{theirs.turn_key}")

    bot._stop_session_core.assert_not_awaited()
    bot._kill_session_core.assert_not_awaited()
    assert "isn't running here" in query.answer.await_args.args[0]
