"""Callback toasts actually reach the user (8.31 follow-up).

Telegram answers a callback query ONCE: a second ``answerCallbackQuery``
for the same query is refused ("query is too old ... or query id is
invalid"). ``_handle_callback`` used to send an empty eager ack before the
handler ran, so every toast a handler sent afterwards ("Session not
found", "already answered", "this prompt has expired", ...) was refused
and never shown. The mocks recorded every ``answer`` call, so the tests
passed anyway.

:class:`_TelegramAnswers` behaves like Telegram: the FIRST answer is what
the user sees, and any later one raises ``BadRequest``. The dispatcher now
lets the handler answer first, with its text, and sends the empty ack only
when nothing has answered — after the handler, or after
``CALLBACK_ACK_BOUND`` seconds, whichever comes first, so the spinner never
hangs on a slow or failing handler. Exactly one answer per tap.
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.error import BadRequest

from aipager.bot import callbacks, session_parity
from aipager.state import Status, TrackedSession

CHAT = 256113222


class _TelegramAnswers:
    """``query.answer`` as Telegram treats it: once per query."""

    def __init__(self, clock=None) -> None:
        self.calls: list[tuple] = []
        self.shown: list = []          # at most one entry: what the user saw
        self._clock = clock
        #: seconds each answer spends on the wire
        self.latency = 0.0
        #: answers that came back (not cancelled mid-flight)
        self.completed = 0

    async def __call__(self, text=None, **kwargs):
        stamp = asyncio.get_running_loop().time()
        self.calls.append((text, kwargs, stamp))
        if self.latency:
            await asyncio.sleep(self.latency)       # the HTTP round trip
        if len(self.calls) > 1:
            raise BadRequest("Query is too old and response timeout expired "
                             "or query id is invalid")
        self.shown.append(text)
        self.completed += 1
        return True


def _update(data, *, message_id=42):
    answers = _TelegramAnswers()
    query = MagicMock()
    query.data = data
    query.answer = answers
    query.edit_message_text = AsyncMock()
    query.edit_message_reply_markup = AsyncMock()
    query.message = MagicMock()
    query.message.message_id = message_id
    query.message.text = ""
    query.message.chat = MagicMock()
    query.message.chat.id = CHAT
    query.from_user = MagicMock()
    query.from_user.id = CHAT
    update = MagicMock()
    update.callback_query = query
    update.effective_user = query.from_user
    update.effective_chat = MagicMock()
    update.effective_chat.id = CHAT
    update.effective_chat.type = "private"
    return update, answers


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(
                asyncio.gather(*pending, return_exceptions=True))
        loop.close()


def _session(bot, label="jim", status=Status.IDLE):
    sess = TrackedSession(name=f"claude-{label}", label=label, status=status,
                          scope_chat_id=CHAT)
    bot.registry._sessions[sess.name] = sess
    return sess


def _tap(bot, data, **kw):
    update, answers = _update(data, **kw)
    _run(bot._handle_callback(update, MagicMock()))
    # Nothing is left behind per tap (the map holds the query object).
    assert callbacks._ACKS == {}, callbacks._ACKS
    return answers


# ── existing toasts that were silently lost ─────────────────────────────────

@pytest.mark.parametrize("data, toast", [
    ("nocolon", "Invalid callback"),
    ("claude-jim:bogus_verb", "Unknown: bogus_verb"),
    ("claude-nobody:stop", "Session not found"),
    ("claude-nobody:kill", "Session not found"),
    ("claude-nobody:retry", "Session not found"),
    ("claude-nobody:pin_answer", "Session not found"),
    ("_:sx:99:allow", "That session is no longer available"),
])
def test_an_existing_toast_is_the_one_answer_the_user_sees(mk_bot, data, toast):
    """Before: the empty eager ack took the query's one answer, and this
    toast was refused. Mutation: restore the eager ack and every row sees
    ``None``."""
    bot = mk_bot()
    answers = _tap(bot, data)
    assert answers.shown == [toast], answers.calls
    assert len(answers.calls) == 1, answers.calls


def test_a_tap_with_no_toast_still_gets_exactly_one_empty_answer(mk_bot):
    """A handler that says nothing: the dispatcher's empty ack clears the
    spinner, once. Mutation: drop the final ack and the spinner hangs."""
    bot = mk_bot()
    answers = _tap(bot, "claude-x:kill-cancel")
    assert answers.shown == [None], answers.calls
    assert len(answers.calls) == 1


# ── the toasts 8.31 added ───────────────────────────────────────────────────

def test_pin_answer_already_answered_is_shown(mk_bot):
    bot = mk_bot()
    _session(bot, status=Status.BUSY)
    answers = _tap(bot, "claude-jim:pin_answer")
    assert answers.shown == ["already answered"], answers.calls


def test_pin_answer_nothing_to_resend_is_shown(mk_bot):
    bot = mk_bot()
    _session(bot, status=Status.INTERACTIVE)
    answers = _tap(bot, "claude-jim:pin_answer")
    assert answers.shown == [
        "The prompt can't be re-sent — answer it in the terminal"], answers.calls


def test_pin_answer_skipped_resend_is_shown(mk_bot):
    from aipager.bot.flood_budget import FloodSkipped
    bot = mk_bot()
    sess = _session(bot, status=Status.INTERACTIVE)
    sess.pending_prompt_msg = {"text": "🔐 jim", "keyboard": None,
                               "summary": "Bash: ls", "prompt_token": 7}
    bot._app.bot.send_message = AsyncMock(
        side_effect=FloodSkipped(CHAT, "sendMessage"))
    answers = _tap(bot, "claude-jim:pin_answer")
    assert answers.shown == ["Busy — try again in a moment"], answers.calls


def test_a_stale_prompt_surface_says_already_answered(mk_bot, monkeypatch):
    bot = mk_bot()
    sess = _session(bot, status=Status.BUSY)
    bot.register_prompt_surface(CHAT, 4000, sess, 7)
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=True))
    answers = _tap(bot, "claude-jim:allow", message_id=4000)
    assert answers.shown == ["already answered"], answers.calls


def test_an_expired_prompt_surface_says_so(mk_bot, monkeypatch):
    bot = mk_bot()
    sess = _session(bot, status=Status.INTERACTIVE)
    sess.pending_permission = {"tool_summary": "Bash: ls", "tool_info": None}
    sess.busy_msg_id = 71
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=True))
    answers = _tap(bot, "claude-jim:allow", message_id=4242)
    assert answers.shown == ["this prompt has expired"], answers.calls


# ── the bound: slow, raising, and late handlers ────────────────────────────

def _slow_parity(delay, *, then=None):
    async def _handle(bot, update, query, session_name, action):
        await asyncio.sleep(delay)
        if then is not None:
            await then(bot, query)
        return True
    return _handle


def test_a_slow_handler_is_acked_within_the_bound(mk_bot, monkeypatch):
    """The handler takes 0.5 s; the empty ack goes out at the bound (0.05 s
    here), while it is still running — once.

    Mutation: ack only after the handler and the spinner waits 0.5 s."""
    monkeypatch.setattr(callbacks, "CALLBACK_ACK_BOUND", 0.05)
    monkeypatch.setattr(session_parity, "handle_callback", _slow_parity(0.5))
    bot = mk_bot()
    update, answers = _update("claude-jim:anything")

    async def main():
        start = asyncio.get_running_loop().time()
        await bot._handle_callback(update, MagicMock())
        return start

    loop = asyncio.new_event_loop()
    try:
        start = loop.run_until_complete(main())
    finally:
        loop.close()
    assert len(answers.calls) == 1, answers.calls
    assert answers.shown == [None]
    assert answers.calls[0][2] - start < 0.4, answers.calls[0][2] - start


def test_a_raising_handler_is_still_acked_once(mk_bot, monkeypatch):
    async def _boom(*_a, **_k):
        raise RuntimeError("handler failed")

    monkeypatch.setattr(session_parity, "handle_callback", _boom)
    bot = mk_bot()
    update, answers = _update("claude-jim:anything")
    with pytest.raises(RuntimeError):
        _run(bot._handle_callback(update, MagicMock()))
    assert answers.shown == [None], answers.calls
    assert len(answers.calls) == 1


def test_a_toast_after_the_bound_is_dropped_quietly(
    mk_bot, monkeypatch, caplog,
):
    """The handler toasts after the bound already acked the query: the
    toast is not sent (it would be refused), it is logged at debug, and
    nothing raises.

    Mutation: send it anyway and the double sees a second answer."""
    monkeypatch.setattr(callbacks, "CALLBACK_ACK_BOUND", 0.05)

    async def _late(bot, query):
        await bot._safe_answer(query, "too late")

    monkeypatch.setattr(session_parity, "handle_callback",
                        _slow_parity(0.2, then=_late))
    bot = mk_bot()
    update, answers = _update("claude-jim:anything")
    with caplog.at_level(logging.DEBUG, logger="aipager.bot.callbacks"):
        _run(bot._handle_callback(update, MagicMock()))
    assert len(answers.calls) == 1, answers.calls
    assert answers.shown == [None]
    assert any("too late" in r.getMessage() for r in caplog.records)


def test_a_second_toast_from_one_handler_is_dropped(mk_bot, monkeypatch):
    """Two toasts in one handler: the first is shown, the second is not
    sent at all (one answer per tap)."""
    async def _two(bot, update, query, session_name, action):
        await bot._safe_answer(query, "first")
        await bot._safe_answer(query, "second")
        return True

    monkeypatch.setattr(session_parity, "handle_callback", _two)
    bot = mk_bot()
    answers = _tap(bot, "claude-jim:anything")
    assert answers.calls[0][0] == "first"
    assert len(answers.calls) == 1, answers.calls


# ── review iteration 4 ─────────────────────────────────────────────────────

def _run_past(coro, extra):
    """Run *coro*, then keep the loop going *extra* seconds (so a stray
    bound task would fire), then close it."""
    async def main():
        await coro
        await asyncio.sleep(extra)
    _run(main())


def test_the_bounds_answer_survives_the_handler_finishing_mid_flight(
    mk_bot, monkeypatch,
):
    """rev-iter4-001. The bound's empty ack is on the wire (0.1 s round
    trip) when the handler returns: it must complete, not be cancelled —
    otherwise the tap gets no answer at all.

    Mutation: drop the shield and the answer is cancelled (0 completed)."""
    monkeypatch.setattr(callbacks, "CALLBACK_ACK_BOUND", 0.05)
    monkeypatch.setattr(session_parity, "handle_callback", _slow_parity(0.08))
    bot = mk_bot()
    update, answers = _update("claude-jim:anything")
    answers.latency = 0.1
    _run_past(bot._handle_callback(update, MagicMock()), 0.3)
    assert len(answers.calls) == 1, answers.calls
    assert answers.completed == 1


def test_no_second_answer_after_the_handler_returns(mk_bot, monkeypatch):
    """The handler toasts and returns at once; the loop keeps running past
    the bound. The bound must be gone: no second answer a second later.

    Mutation: drop ``bound.cancel()`` and the bound answers again after the
    map entry is popped."""
    monkeypatch.setattr(callbacks, "CALLBACK_ACK_BOUND", 0.05)
    bot = mk_bot()
    update, answers = _update("claude-nobody:stop")
    _run_past(bot._handle_callback(update, MagicMock()), 0.3)
    assert answers.shown == ["Session not found"]
    assert len(answers.calls) == 1, answers.calls


def test_a_toast_racing_the_bounds_answer_is_not_sent(mk_bot, monkeypatch):
    """The bound's ack is on the wire when the handler toasts: the query is
    already marked answered, so the toast is dropped — one answer.

    Mutation: mark it answered only after the await and both are sent."""
    monkeypatch.setattr(callbacks, "CALLBACK_ACK_BOUND", 0.05)

    async def _toast(bot, query):
        await bot._safe_answer(query, "racing")

    monkeypatch.setattr(session_parity, "handle_callback",
                        _slow_parity(0.08, then=_toast))
    bot = mk_bot()
    update, answers = _update("claude-jim:anything")
    answers.latency = 0.1
    _run_past(bot._handle_callback(update, MagicMock()), 0.3)
    assert len(answers.calls) == 1, answers.calls


def test_a_cancelled_tap_leaves_no_entry_behind(mk_bot, monkeypatch):
    """rev-iter4-003. The tap's task is cancelled (shutdown) while its
    final answer is on the wire: the map entry still goes.

    Mutation: pop after the await instead of in a finally and it leaks."""
    monkeypatch.setattr(callbacks, "CALLBACK_ACK_BOUND", 5.0)
    bot = mk_bot()
    update, answers = _update("claude-x:kill-cancel")
    answers.latency = 1.0

    async def main():
        task = asyncio.ensure_future(bot._handle_callback(update, MagicMock()))
        await asyncio.sleep(0.05)       # the final answer is in flight
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    _run(main())
    assert callbacks._ACKS == {}, callbacks._ACKS


def test_the_bound_claims_the_query_in_the_step_it_wakes(mk_bot, monkeypatch):
    """rev-iter5-001. The bound wakes in the same step the handler toasts
    and returns (bound 0 s). The bound must see the toast's claim at once;
    a claim deferred into a new task runs after the handler dropped the
    map entry, and a missing entry means "send" — a second answer.

    Mutation: claim inside the shielded task (``_safe_answer``) and the
    double sees two answers."""
    monkeypatch.setattr(callbacks, "CALLBACK_ACK_BOUND", 0.0)

    async def _toast_then_yield(bot, update, query, session_name, action):
        await bot._safe_answer(query, "toast")
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return True

    monkeypatch.setattr(session_parity, "handle_callback", _toast_then_yield)
    bot = mk_bot()
    update, answers = _update("claude-jim:anything")
    _run_past(bot._handle_callback(update, MagicMock()), 0.1)
    assert [c[0] for c in answers.calls] == ["toast"], answers.calls


def test_a_bound_waking_while_a_toast_is_on_the_wire_sends_nothing(
    mk_bot, monkeypatch,
):
    """The handler's toast is still in flight (0.1 s round trip) when the
    bound wakes at 0.05 s: the toast claimed the query BEFORE its await, so
    the bound finds it answered — one answer.

    Mutation: mark the query answered only after ``query.answer`` returns
    and the bound sends a second one."""
    monkeypatch.setattr(callbacks, "CALLBACK_ACK_BOUND", 0.05)

    async def _toast_first(bot, update, query, session_name, action):
        await bot._safe_answer(query, "toast")
        return True

    monkeypatch.setattr(session_parity, "handle_callback", _toast_first)
    bot = mk_bot()
    update, answers = _update("claude-jim:anything")
    answers.latency = 0.1
    _run_past(bot._handle_callback(update, MagicMock()), 0.3)
    assert [c[0] for c in answers.calls] == ["toast"], answers.calls


def test_no_second_answer_after_the_handler_returns_even_uncancelled(
    mk_bot, monkeypatch,
):
    """The bound outliving its tap (not cancelled) finds no map entry and
    sends nothing. With ``bound.cancel()`` this is a second, independent
    guard for `test_no_second_answer_after_the_handler_returns`."""
    monkeypatch.setattr(callbacks, "CALLBACK_ACK_BOUND", 0.05)
    bot = mk_bot()
    update, answers = _update("claude-nobody:stop")
    query = update.callback_query

    async def main():
        await bot._handle_callback(update, MagicMock())
        await bot._ack_after_bound(query)       # a bound that was never cancelled

    _run(main())
    assert len(answers.calls) == 1, answers.calls
