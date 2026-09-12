"""The busy card's cadence and its skip class (roadmap 8.21).

Rows A, B1–B4, C2, D, I and M of the design's case table. The rule is
``interval = max(BASE, N x floor) x 1.1 x backoff``, where N is the number
of BUSY sessions sharing the chat: the cards of a chat share the chat's
1/s budget instead of each assuming it owns one. Two sessions streaming
into one DM at 0.9 s apiece is what earned the 2026-09-11 ban.

Nothing here sleeps for real, and nothing patches ``asyncio.sleep``
through a module path (``aipager.bot.animation.asyncio`` IS the global
module; CLAUDE.md). Cadence is asserted on the pure rule and on the
interval the animator computes, never by waiting for it.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import config
from aipager.bot import animation
from aipager.bot import rich_message as rm
from aipager.bot.flood_budget import (
    BudgetRateLimiter,
    FloodSkipped,
    card_interval,
)
from aipager.bot.transport import MUTED, SKIPPED
from aipager.state import Status, TrackedSession

PRIVATE = 256113222   # what conftest's _pin_single_chat_config pins CHAT_ID to
GROUP = -1001


# ── harness ──────────────────────────────────────────────────────────────────

class FakeClock:
    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += max(seconds, 0.0)
        await asyncio.sleep(0)


@pytest.fixture
def run_async():
    """Override the shared ``run_async`` for this file, to CLOSE the loops.

    The shared fixture abandons each loop it creates. Rows here drive
    ``_animate_busy`` — a long-lived task — and leave limiter waiters
    pending, and the suite runs under a hard 1 GiB ``RLIMIT_AS`` (the
    first test file to run calls ``notify_hook.main()``, which clamps it
    on the pytest process itself and can never raise it back). A leak
    here would not fail THIS file; it would fail an unrelated LATER test
    with "can't start new thread". Roadmap 8.19, worked around here.
        Closed EAGERLY, as each coroutine finishes, rather than collected and
    closed at teardown: the suite runs with VmSize within a few tens of
    kilobytes of its ``RLIMIT_AS`` on this machine already, so holding
    even two loops at once is worth avoiding.
    """
    def _run(coro):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            try:
                pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True),
                    )
                loop.run_until_complete(loop.shutdown_default_executor())
            finally:
                loop.close()

    return _run


def _sess(label="jim", *, status=Status.BUSY, chat=0, streaming=False,
          msg_id=10) -> TrackedSession:
    sess = TrackedSession(name=f"claude-{label}", label=label, status=status)
    sess.busy_msg_id = msg_id
    sess.busy_started_at = time.monotonic() - 60
    sess.scope_kind = "dm"
    sess.scope_chat_id = chat
    sess.stream_dirty = streaming
    return sess


def _bot(mk_bot, *sessions):
    bot = mk_bot()
    for sess in sessions:
        bot.registry._sessions[sess.name] = sess
    bot._app.bot.send_chat_action = AsyncMock()
    bot._app.bot.edit_message_text = AsyncMock()
    return bot


async def _tick(bot, sess, verb="Working", *, waiting=False):
    """One tick, then one turn of the event loop.

    The turn of the loop is what makes "the card loop sends no chat
    action" honest (roadmap 8.24): the indicator lives on its own task, so
    a row asserting the TICK sends nothing has to give any task the tick
    might have started a chance to run before it looks.
    """
    out = await bot._animate_tick(sess, verb, waiting)
    await asyncio.sleep(0)
    return out


def _install(clock=None) -> BudgetRateLimiter:
    limiter = (BudgetRateLimiter(clock=clock, sleep=clock.sleep)
               if clock is not None else BudgetRateLimiter())
    rm.set_rate_limiter(limiter)
    return limiter


# ── A / B1–B4: the cadence rule ──────────────────────────────────────────────

def test_one_streaming_session_in_a_private_chat_gets_the_base_cadence(mk_bot):
    """Row A. Mutation: drop the ``x CARD_CADENCE_MARGIN`` and the cards
    claim the chat's whole budget, leaving no headroom for the answer."""
    sess = _sess(chat=PRIVATE, streaming=True)
    bot = _bot(mk_bot, sess)
    assert bot._card_interval(sess, streaming=True) == pytest.approx(
        card_interval(base=config.STREAM_EDIT_INTERVAL, busy_sessions=1,
                      is_group=False))
    assert bot._card_interval(sess, streaming=True) == pytest.approx(1.32)


def test_two_busy_sessions_in_one_private_chat_halve_each_others_cadence(mk_bot):
    """Row B1 — the incident. Two cards streaming into ONE DM must pace at
    ~2.2 s each, not 0.9 s each.

    Mutation: count N as 1 regardless (or drop the ``N x floor`` term) and
    each card goes back to its own private 1.32 s, i.e. ~1.5 edits/s into
    a chat Telegram allows 1/s.
    """
    a = _sess("a", chat=PRIVATE, streaming=True)
    b = _sess("b", chat=PRIVATE, streaming=True)
    bot = _bot(mk_bot, a, b)
    assert bot._card_interval(a, streaming=True) == pytest.approx(2.2)
    assert bot._card_interval(b, streaming=True) == pytest.approx(2.2)


def test_an_idle_session_does_not_slow_its_siblings_cards(mk_bot):
    """Row B2. N counts BUSY sessions only, and a flip is visible on the
    NEXT tick with nothing rescheduled — the interval is recomputed every
    time, never cached.

    Mutation: drop the ``status is Status.BUSY`` filter and a chat full of
    finished sessions would throttle the one that is still working.
    """
    a = _sess("a", chat=PRIVATE, streaming=True)
    b = _sess("b", chat=PRIVATE, streaming=True)
    c = _sess("c", chat=PRIVATE, streaming=True)
    bot = _bot(mk_bot, a, b, c)
    assert bot._card_interval(a, streaming=True) == pytest.approx(3.3)
    c.status = Status.IDLE
    assert bot._card_interval(a, streaming=True) == pytest.approx(2.2)


def test_a_group_card_paces_at_the_group_floor(mk_bot):
    """Row B3. A group allows 20 calls a minute, so one card's floor is
    3 s, not 1 s. Mutation: use the private floor for groups and a single
    session's card alone would spend 1/3 of the group's whole minute."""
    sess = _sess(chat=GROUP, streaming=True)
    bot = _bot(mk_bot, sess)
    assert bot._card_interval(sess, streaming=True) == pytest.approx(3.3)


def test_a_legacy_session_counts_in_its_resolved_chat_not_every_chat(mk_bot):
    """Row B4. A legacy session (``scope_chat_id == 0``) resolves to the
    configured CHAT_ID, so it and a stamped session in that same real chat
    are N = 2 in ONE chat — and no phantom chat ``0`` exists.

    Mutation: count N with ``registry.all_sessions(chat)``, which filters
    on the RAW ``scope_chat_id`` and treats 0 as matching EVERY scope: the
    legacy session would then be counted into every chat at once, slowing
    cards in chats it has nothing to do with.
    """
    legacy = _sess("legacy", chat=0, streaming=True)     # in memory: load()
    stamped = _sess("stamped", chat=PRIVATE, streaming=True)  # backfills this
    elsewhere = _sess("elsewhere", chat=GROUP, streaming=True)
    bot = _bot(mk_bot, legacy, stamped, elsewhere)

    assert bot._card_interval(stamped, streaming=True) == pytest.approx(2.2)
    assert bot._card_interval(legacy, streaming=True) == pytest.approx(2.2)
    # The group session is alone in ITS chat: the legacy one is not counted
    # there, and there is no chat 0 for it to live in.
    assert bot._card_interval(elsewhere, streaming=True) == pytest.approx(3.3)


def test_a_not_streaming_card_uses_the_slower_base(mk_bot):
    """Mutation: always use STREAM_EDIT_INTERVAL and a quiet card edits
    nearly three times as often as it needs to."""
    sess = _sess(chat=PRIVATE)
    bot = _bot(mk_bot, sess)
    assert bot._card_interval(sess, streaming=False) == pytest.approx(3.3)
    assert bot._card_interval(sess, streaming=True) == pytest.approx(1.32)


def test_a_backing_off_chat_multiplies_every_card_interval(mk_bot):
    """Row E4 through the animator. Mutation: ignore the limiter's
    ``cadence_multiplier`` and a 429 changes nothing about how fast the
    cards go back into the window that produced it."""
    sess = _sess(chat=PRIVATE, streaming=True)
    bot = _bot(mk_bot, sess)
    limiter = _install()
    assert bot._card_interval(sess, streaming=True) == pytest.approx(1.32)
    limiter.note_retry_after(PRIVATE, 5)
    limiter.note_retry_after(PRIVATE, 5)
    assert bot._card_interval(sess, streaming=True) == pytest.approx(1.32 * 4)


def test_the_first_tick_delay_never_dips_below_the_chat_floor(mk_bot):
    """§11 U3. Mutation: return FIRST_TICK_DELAY unconditionally and a
    fresh group card spends a token 1.5 s in that it needs at 3 s."""
    private = _sess("p", chat=PRIVATE)
    group = _sess("g", chat=GROUP)
    bot = _bot(mk_bot, private, group)
    assert bot._first_tick_delay(private) == pytest.approx(animation.FIRST_TICK_DELAY)
    assert bot._first_tick_delay(group) == pytest.approx(
        config.CARD_CADENCE_FLOOR_GROUP)


def test_the_cadence_is_one_pure_rule_read_by_the_animator(mk_bot):
    """R4/D4. The animator must not re-derive the rule: the loop sleep and
    the tick's debounce both read the same function, so they cannot drift
    apart (which is what left the typing indicator at 0.9 s while the
    edits slowed down).

    Mutation: revert ``_animate_busy``'s sleep to a bare
    ``STREAM_EDIT_INTERVAL`` and the loop wakes faster than the card can
    be edited, for no benefit.
    """
    a = _sess("a", chat=PRIVATE, streaming=True)
    b = _sess("b", chat=PRIVATE, streaming=True)
    bot = _bot(mk_bot, a, b)
    assert bot._card_interval(a, streaming=True) == pytest.approx(
        card_interval(base=config.STREAM_EDIT_INTERVAL, busy_sessions=2,
                      is_group=False))
    assert bot._card_interval(a, streaming=True) != config.STREAM_EDIT_INTERVAL


def test_the_loop_wake_and_the_debounce_read_the_same_interval():
    """G15/D4: ``_animate_busy``'s sleep is the cadence FUNCTION, not a
    module constant — otherwise the loop keeps waking at the old 0.9 s
    while the edits slow down, which is what left the typing indicator
    running at full rate through the whole incident.

    Asserted statically, on the AST: the alternative is patching
    ``asyncio.sleep`` through ``aipager.bot.animation.asyncio``, which IS
    the global module and has hung this suite twice (CLAUDE.md). The
    debounce is covered behaviourally by the rows above.

    Mutation: revert the loop sleep to a bare ``STREAM_EDIT_INTERVAL`` and
    this names it.
    """
    import ast
    import textwrap

    # Only the one method, streamed out of the file a line at a time:
    # neither a full-module AST nor ``inspect.getsource`` (which pulls the
    # whole 2,000-line module into ``linecache``) — the suite runs within
    # ~1 MB of its RLIMIT_AS and that peak is never returned (8.19).
    snippet: list[str] = []
    with open(animation.__file__, encoding="utf-8") as fh:
        for line in fh:
            if snippet and (line.startswith("    def ")
                            or line.startswith("    async def ")):
                break
            if snippet or line.startswith("    async def _animate_busy"):
                snippet.append(line)
    assert snippet, "_animate_busy not found in animation.py"
    loop = ast.parse(textwrap.dedent("".join(snippet)))
    sleeps = [
        node for node in ast.walk(loop)
        if (isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "sleep")
    ]
    assert len(sleeps) == 1, "the animate loop should have exactly one sleep"
    argument = ast.dump(sleeps[0].args[0])
    assert "_card_interval" in argument, argument
    assert "_first_tick_delay" in argument, argument
    assert "STREAM_EDIT_INTERVAL" not in argument, argument
    assert "BUSY_EDIT_INTERVAL" not in argument, argument


# ── M: no typing indicator while a card is live ──────────────────────────────

def test_a_debounced_tick_makes_no_telegram_call_at_all(mk_bot, run_async):
    """Row M / G18. Until 8.21 a debounced tick still fired a
    ``sendChatAction``, once per loop wake per BUSY session — the debounce
    never suppressed it, so at 0.9 s wakes it roughly DOUBLED the daemon's
    real call rate into the chat and was the most frequent chat-scoped
    call in the incident.

    Still true after 8.24 brought the indicator back, and for a stronger
    reason than "the debounce suppresses it": the bubble is not on this
    code path at all any more. It has its own task and its own clock
    (``_animate_typing``), so no branch of the card loop can send it,
    pace it, or be paced by it.

    Mutation: call ``send_chat_action`` (or ``_animate_typing``'s helpers)
    from the debounced branch and this records a call for a tick that sent
    nothing.
    """
    sess = _sess(chat=PRIVATE, streaming=True)
    bot = _bot(mk_bot, sess)
    bot._edit_busy_rich = AsyncMock(return_value=True)
    sess.last_tool_edit_at = time.monotonic()   # inside the interval

    assert run_async(_tick(bot, sess)) is False

    bot._edit_busy_rich.assert_not_awaited()
    bot._app.bot.send_chat_action.assert_not_awaited()


def test_a_ticking_card_sends_no_typing_indicator_in_any_chat_kind(
        mk_bot, run_async):
    """Case M, twice amended. 8.21 removed the indicator from the card loop
    because, CHARGED TO THE CHAT'S SEND BUDGET, it cost 57 calls a minute
    into one DM with 49% of the card edits refused and gaps of 2.2–6.6 s.
    8.24 brought the bubble back — live probes caught ``sendChatAction``
    answering 200 on all eleven calls made during a real
    ``retry_after=10`` window that refused every edit into the same chat,
    so Telegram does not meter chat actions with messages — but brought it
    back on **its own task**, never on the tick.

    So this row still holds, in both chat kinds, and now guards the
    boundary rather than the absence: the card loop is not the indicator's
    owner. What the bubble costs and when it fires is asserted in
    ``tests/test_typing_indicator.py`` and, over simulated minutes, in
    ``tests/integration/per-chat-flood-budget/``.

    Mutation: offer the indicator from the tick again — with or without a
    chat-kind guard — and one of these two rows records a call.
    """
    for chat in (PRIVATE, GROUP):
        sess = _sess(chat=chat, streaming=True)
        bot = _bot(mk_bot, sess)
        bot._edit_busy_rich = AsyncMock(return_value=True)
        sess.last_tool_edit_at = 0.0             # well outside the interval

        assert run_async(_tick(bot, sess)) is True

        bot._edit_busy_rich.assert_awaited_once()   # the edit still goes out
        bot._app.bot.send_chat_action.assert_not_awaited()


def test_a_fresh_card_starts_the_bubble_without_sending_it_itself(mk_bot,
                                                                 run_async):
    """Card CREATION, in both chat kinds (8.24). A fresh card does light
    the bubble — that is the moment nothing else is moving, and the chat
    list is where the bubble is visible without opening the chat — but
    ``send_busy`` does not make the call: ``_start_animation`` starts the
    typing task the moment it returns.

    That split is not cosmetic. ``send_busy`` is also
    ``_reanchor_busy_card``'s send path, which runs inside a tick holding
    ``animate_lock``, so an awaited chat action there would land in front
    of the re-anchor's own edit (review rev-iter1-001).

    Mutation: send the action from ``send_busy`` and the card path pays for
    it here; drop ``_start_typing`` from ``_start_animation`` and no card
    ever lights a bubble at all.
    """
    for chat in (PRIVATE, GROUP):
        sess = _sess(chat=chat)
        sess.busy_msg_id = None
        bot = _bot(mk_bot, sess)
        bot._app.bot.send_message = AsyncMock(
            return_value=MagicMock(message_id=42))

        async def _drive(sess=sess, bot=bot):
            await bot._send_busy_and_animate(sess)
            assert sess.typing_task is not None
            sent_by_the_card_path = list(
                bot._app.bot.send_chat_action.await_args_list)
            await asyncio.sleep(0)          # now let the typing task run
            await asyncio.sleep(0)
            bot._stop_animation(sess)
            return sent_by_the_card_path

        by_card_path = run_async(_drive())

        assert sess.busy_msg_id == 42
        assert by_card_path == [], "the card path made the chat action itself"
        bot._app.bot.send_chat_action.assert_awaited_once_with(
            chat_id=chat, action="typing")


def test_the_animation_module_sends_the_chat_action_from_one_place_only():
    """The rule as a property of the code rather than of one scenario: the
    module may reach ``send_chat_action`` from exactly ONE place, the
    ``_send_typing`` helper that every gate feeds into. A row driving one
    tick can only prove the branches it happens to take; this covers the
    ones it does not — an ad-hoc call from a new branch would bypass the
    interval gate, the BUSY check and the mute check in one go.

    (Until 8.24 this row asserted the module held NO reference at all,
    which is what 8.21 removed; the claim it protects is the same one —
    nothing sends this action except through the gate.)

    Mutation: add a second ``send_chat_action(`` call site anywhere in
    ``aipager/bot/animation.py`` and this names the line.
    """
    source = Path(animation.__file__).read_text(encoding="utf-8").splitlines()
    sites = [
        i + 1 for i, line in enumerate(source)
        if "send_chat_action(" in line and not line.lstrip().startswith("#")
    ]
    assert len(sites) == 1, sites
    # …and it is inside `_send_typing`, not merely somewhere in the file.
    owner = [line for line in source[:sites[0]]
             if line.startswith("    async def ") or line.startswith("    def ")]
    assert owner[-1].strip().startswith("async def _send_typing("), owner[-1]


def test_a_waiting_tick_still_edits_the_card_and_sends_no_indicator(
        mk_bot, run_async):
    """A session sitting on an open background job is not generating
    anything, but its card still has an elapsed counter to advance — so
    the edit goes out, and no chat action does. Two independent reasons
    since 8.24, and both are wanted: the tick never sends the indicator at
    all, and a waiting frame means ``status != BUSY``, which is what
    ``_typing_chat`` gates on — so the chat list never shows "typing…" for
    a session that is not working (R5, asserted directly in
    ``tests/test_typing_indicator.py``).

    Mutation: skip the edit while ``waiting`` and a background job's card
    freezes with a stopped clock.
    """
    sess = _sess(chat=PRIVATE, status=Status.IDLE)
    bot = _bot(mk_bot, sess)
    bot._edit_busy_rich = AsyncMock(return_value=True)
    sess.last_tool_edit_at = 0.0

    assert run_async(_tick(bot, sess, waiting=True)) is True
    bot._edit_busy_rich.assert_awaited_once()
    bot._app.bot.send_chat_action.assert_not_awaited()


# ── C2: skip, and the starvation guard ───────────────────────────────────────

def test_an_ordinary_card_tick_is_a_skip_caller(mk_bot, run_async):
    """Mutation: make the ordinary tick blocking and every card edit
    queues ahead of the next answer instead of standing aside."""
    sess = _sess(chat=PRIVATE, streaming=True)
    bot = _bot(mk_bot, sess)
    bot._edit_busy_rich = AsyncMock(return_value=True)
    sess.last_tool_edit_at = 0.0

    run_async(bot._animate_tick(sess, "Working", False))
    assert bot._edit_busy_rich.await_args.kwargs["kind"] == "skip"


def test_a_skipped_edit_leaves_the_cards_stamps_untouched(mk_bot, run_async,
                                                          monkeypatch):
    """G20. A skip is not a render: nothing was sent, so nothing may be
    recorded as sent. ``stream_last_rendered`` especially — recording it
    would make the dedupe swallow the NEXT tick's attempt too, and the
    card would sit frozen with its content one edit behind.

    Mutation: stamp ``last_tool_edit_at`` / ``stream_last_rendered`` on the
    skip path and the card stops re-rendering. Mutation: return ``None``
    instead of ``False`` and every caller reads "message gone", drops
    ``busy_msg_id`` and loses the card for good.
    """
    sess = _sess(chat=PRIVATE, streaming=True)
    sess.last_tool_edit_at = 0.0
    sess.stream_last_rendered = "old"
    bot = _bot(mk_bot, sess)

    async def _refused(*a, **kw):
        raise FloodSkipped(PRIVATE, "editMessageText")

    monkeypatch.setattr(animation, "edit_message_text_rich", _refused)

    result = run_async(bot._edit_busy_rich(sess, "Working", kind="skip"))

    assert result is False, "transient, never None (which means 'message gone')"
    assert sess.last_tool_edit_at == 0.0
    assert sess.stream_last_rendered == "old"
    assert sess.stream_dirty is True
    assert sess.card_skipped_since > 0.0

    # A second refusal marks the START of the run, not the latest one.
    first = sess.card_skipped_since
    run_async(bot._edit_busy_rich(sess, "Working", kind="skip"))
    assert sess.card_skipped_since == first


def test_a_card_refused_for_two_intervals_blocks_once_and_lands(mk_bot,
                                                                run_async):
    """Row C2 / G19 — the starvation guard. A card may be slow, never
    frozen: once a run of refusals is two intervals old the next tick
    makes ONE blocking attempt, and a landed edit clears the run.

    Mutation: never set ``card_skipped_since`` (or never read it) and a
    chat that stays busy enough never lets its card through at all.
    """
    sess = _sess(chat=PRIVATE, streaming=True)
    bot = _bot(mk_bot, sess)
    bot._edit_busy_rich = AsyncMock(return_value=True)
    sess.last_tool_edit_at = 0.0
    interval = bot._card_interval(sess, streaming=True)

    # A fresh run of refusals: still a skip caller.
    sess.card_skipped_since = time.monotonic()
    run_async(bot._animate_tick(sess, "Working", False))
    assert bot._edit_busy_rich.await_args.kwargs["kind"] == "skip"

    # Two intervals of refusals: one blocking attempt.
    sess.card_skipped_since = time.monotonic() - 2 * interval - 0.1
    run_async(bot._animate_tick(sess, "Working", False))
    assert bot._edit_busy_rich.await_args.kwargs["kind"] == "blocking"


def test_a_landed_edit_ends_the_run_of_refusals(mk_bot, run_async, monkeypatch):
    """The other half of C2: ``card_skipped_since`` returns to 0.0 as soon
    as an edit lands, so the guard measures THIS run of refusals and not
    the life of the session.

    Mutation: never clear it and every later tick blocks, which reinstates
    exactly the queue-behind-the-cards behaviour 8.21 removes.
    """
    sess = _sess(chat=PRIVATE, streaming=True)
    sess.card_skipped_since = time.monotonic() - 100
    bot = _bot(mk_bot, sess)
    monkeypatch.setattr(animation, "edit_message_text_rich",
                        AsyncMock(return_value={"message_id": 10}))

    assert run_async(bot._edit_busy_rich(sess, "Working", kind="skip")) is True
    assert sess.card_skipped_since == 0.0


def test_the_starvation_guards_blocking_attempt_is_bounded(mk_bot, run_async,
                                                           monkeypatch):
    """A blocking acquire runs inside ``sess._stream_edit_lock``, and the
    stale-card watchdog RESTARTS a task that holds that lock for 20 s. The
    guard's one blocking edit is therefore bounded well under that; on
    timeout it degrades to a skip and leaves the run of refusals open, so
    the next tick tries again rather than giving up.

    Mutation: drop the ``asyncio.wait_for`` and a chat deferred for a long
    retry_after wedges the animation task into the watchdog's restart.
    """
    sess = _sess(chat=PRIVATE, streaming=True)
    bot = _bot(mk_bot, sess)
    sess.last_tool_edit_at = 0.0
    started = time.monotonic()
    sess.card_skipped_since = started - 100

    async def _never(*a, **kw):
        await asyncio.sleep(3600)

    bot._edit_busy_rich = AsyncMock(side_effect=_never)
    monkeypatch.setattr(animation, "CARD_STARVATION_BLOCK_TIMEOUT", 0.01)

    result = run_async(bot._animate_tick(sess, "Working", False))

    assert result is True, "a timed-out blocking attempt is a skip, not a stop"
    assert sess.card_skipped_since == started - 100, "the run stays open"


def test_the_watchdogs_forced_refresh_is_a_skip_caller(mk_bot, run_async):
    """Design §8. A card that is stale BECAUSE its chat is over budget
    must not be "fixed" by spending the budget. A skipped refresh leaves
    ``last_tool_edit_at`` alone, so the monitor simply re-arms in 20 s.

    Mutation: make it blocking and the watchdog becomes a second, slower
    animator competing for the same tokens.
    """
    sess = _sess(chat=PRIVATE)
    sess.animate_task = MagicMock()
    sess.animate_task.done.return_value = False
    bot = _bot(mk_bot, sess)
    bot._edit_busy_rich = AsyncMock(return_value=False)

    run_async(bot._watchdog_busy_card(sess, "refresh", 25.0))

    bot._edit_busy_rich.assert_awaited_once()
    assert bot._edit_busy_rich.await_args.kwargs["kind"] == "skip"


# ── D: an answer keeps the reserve ───────────────────────────────────────────

def test_an_answer_runs_while_two_cards_are_being_refused(run_async):
    """Row D. Two cards stream into one chat; a real answer arrives. The
    answer is a BLOCKING caller and must go out within a token's wait,
    while the cards' skip acquires stand aside.

    Mutation: drop the reserve (``< 2.0`` -> ``< 1.0``) and the cards
    spend the last token, so the answer waits a full second behind them.
    """
    clock = FakeClock()
    limiter = BudgetRateLimiter(clock=clock, sleep=clock.sleep)
    ran: list[tuple[str, float]] = []

    async def _card():
        ran.append(("card", clock.now))
        return True

    async def _answer():
        ran.append(("answer", clock.now))
        return True

    async def _drive():
        # Two cards spend the burst down to the reserve.
        for _ in range(2):
            await limiter.process_request(
                callback=_card, args=(), kwargs={}, endpoint="editMessageText",
                data={"chat_id": PRIVATE}, rate_limit_args={"kind": "skip"})
        started = clock.now
        # Both cards are now refused...
        for _ in range(2):
            with pytest.raises(FloodSkipped):
                await limiter.process_request(
                    callback=_card, args=(), kwargs={},
                    endpoint="editMessageText", data={"chat_id": PRIVATE},
                    rate_limit_args={"kind": "skip"})
        # ...but the answer runs immediately, on the reserved token.
        await limiter.process_request(
            callback=_answer, args=(), kwargs={}, endpoint="sendMessage",
            data={"chat_id": PRIVATE}, rate_limit_args=None)
        assert clock.now == started

    run_async(_drive())
    assert [kind for kind, _ in ran] == ["card", "card", "answer"]


# ── I: the pinned dashboard ──────────────────────────────────────────────────

def test_a_skipped_pinned_refresh_is_retried_on_the_next_change(mk_bot,
                                                                run_async,
                                                                monkeypatch):
    """Row I / G21. The dashboard is a summary that is always
    re-derivable, which is what makes it skippable — but a skipped refresh
    must NOT be recorded as shown, or the next state change compares equal
    to text the user never saw and skips the edit as redundant.

    Mutation: keep ``if edited is MUTED:`` alone and the pinned card
    silently freezes at whatever it last really showed.
    """
    bot = mk_bot()
    bot.registry.pinned_msg_id = 77
    monkeypatch.setattr("aipager.bot.dashboard.CHAT_ID", str(PRIVATE))
    texts = iter(["first", "second"])
    monkeypatch.setattr(bot, "_build_pinned_text", lambda name: next(texts))

    calls: list[dict] = []

    async def _edit(*args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise FloodSkipped(PRIVATE, "editMessageText")
        return MagicMock()

    bot._app.bot.edit_message_text = AsyncMock(side_effect=_edit)

    run_async(bot._maybe_update_bot_name("claude-jim"))
    assert bot._last_pinned_text != "first", "a skipped refresh was never shown"
    assert calls[0]["rate_limit_args"] == {"kind": "skip"}

    run_async(bot._maybe_update_bot_name("claude-jim"))
    assert bot._last_pinned_text == "second"
    assert len(calls) == 2


def test_the_seam_turns_a_refused_skip_into_the_skipped_sentinel(run_async):
    """The transport seam TRANSLATES the limiter's decision; it never takes
    it. Mutation: let ``FloodSkipped`` escape ``edit_text_at`` and a
    fire-and-forget dashboard refresh raises into an unhandled task."""
    from aipager.bot.transport import edit_text_at, send_text

    bot = MagicMock()

    async def _raise(*a, **kw):
        raise FloodSkipped(PRIVATE, "editMessageText")

    bot.edit_message_text = AsyncMock(side_effect=_raise)
    bot.send_message = AsyncMock(side_effect=_raise)

    assert run_async(edit_text_at(bot, "x", PRIVATE, 1)) is SKIPPED
    assert run_async(send_text(bot, PRIVATE, "x")) is SKIPPED


def test_skipped_is_falsy_and_distinct_from_muted():
    """Mutation: return MUTED for a skip and the operator is told the chat
    is banned when it is merely busy — and `aipager status` would be asked
    to show a mute that does not exist."""
    assert bool(SKIPPED) is False
    assert repr(SKIPPED) == "SKIPPED"
    assert SKIPPED is not MUTED
    assert not hasattr(SKIPPED, "message_id")


# ── R8: none of this survives a restart ──────────────────────────────────────

def test_no_budget_state_survives_save_and_load():
    """Row J / G28. ``card_skipped_since`` is transient by being absent
    from ``_PERSIST_FIELDS`` — a restart has no budget state to be starved
    by, and a card that thinks it has been refused for an hour would make
    its first tick blocking for no reason.

    Mutation: add it to ``_PERSIST_FIELDS`` and the stamp comes back from
    disk, measured against a monotonic clock that reset with the process.
    """
    import aipager.state as state_mod
    from aipager.state import SessionRegistry

    # conftest's autouse `_isolate_home_paths` already points this at a
    # tmp dir; read it rather than re-pointing it, so this test cannot
    # accidentally write to the operator's real ~/.claude.
    state_file = Path(state_mod.SESSION_STATE_FILE)
    assert state_file.parent != Path.home() / ".claude"

    registry = SessionRegistry()
    sess = _sess(chat=PRIVATE)
    sess.card_skipped_since = 12345.6
    registry._sessions[sess.name] = sess
    registry.mark_dirty()
    registry.save()

    assert "card_skipped_since" not in state_file.read_text()
    assert "card_skipped_since" not in SessionRegistry._PERSIST_FIELDS

    reloaded = SessionRegistry()
    reloaded.load()
    for restored in reloaded.all_sessions().values():
        assert restored.card_skipped_since == 0.0
