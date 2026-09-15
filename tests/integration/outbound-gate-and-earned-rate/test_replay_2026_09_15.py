"""The 2026-09-15 timeline, replayed against a bot WITH A HISTORY (rows E, N).

What happened, measured on a friend's box running 0.7.11, read-only:

* two sessions streamed their busy cards into one private chat for ~45
  minutes continuously;
* three bans in one day, escalating ``retry_after`` **1283 -> 312 -> 34212**
  (the last is 9.5 hours);
* **3, then 5, then 9** requests fired INTO each active ban;
* **5 answers silently dropped**, each leaving one INFO line and nothing
  else.

The acceptance criteria are the inverse of those numbers: zero requests
into any active ban, zero answers lost, and sustained volume under the
cap for a PRIVATE chat — which had no volume ceiling at all before 8.27.

Why the fake is penalised rather than strict. `_StrictTelegram` in the
8.21 suite models a CLEAN bot: a fixed allowance and a friendly
``retry_after: 5``. Measuring a flood fix against that proves nothing
about the account the incident happened to — it is exactly the mistake
`per-chat-flood-budget.md` §12 made, and it is why 8.21 shipped a guessed
constant that earned three more bans. `_PenalisedTelegram` shrinks the
allowance on every violation and answers the real ladder.
"""

from __future__ import annotations

import asyncio

from aipager import config
from aipager.bot.flood import MUTE
from aipager.bot.held import HELD
from aipager.state import Status

PRIVATE = 256113222          # the chat `_pin_single_chat_config` pins


def _card(bot, label, msg_id, chat_id=PRIVATE):
    """A BUSY session with an open, streaming busy card."""
    sess = bot.registry.get_or_create(f"claude-{label}")
    sess.label = label
    sess.status = Status.BUSY
    sess.busy_msg_id = msg_id
    sess.stream_last_rendered = ""
    sess.stream_dirty = True
    sess.scope_kind = "dm" if (chat_id or 1) > 0 else "group"
    sess.scope_chat_id = chat_id
    sess.busy_started_at = 0.0
    sess.last_tool_edit_at = 0.0
    sess.trigger_msg_id = 3
    sess.record_tool(f"Bash: {label}", True)
    return sess


def _ext_bot(bot, telegram, limiter, chat_id=PRIVATE):
    """`bot._app.bot` with the one ExtBot behaviour that matters: every
    call goes through the limiter before it goes out."""
    def _method(endpoint, position=0):
        async def _call(*args, rate_limit_args=None, **kwargs):
            target = kwargs.get("chat_id")
            if target is None and len(args) > position:
                target = args[position]

            async def _do():
                telegram.admit(endpoint, target, kwargs.get("message_id"))
                return type("M", (), {"message_id": 1})()

            return await limiter.process_request(
                callback=_do, args=(), kwargs={}, endpoint=endpoint,
                data={"chat_id": target}, rate_limit_args=rate_limit_args)
        return _call

    bot._app.bot.send_chat_action = _method("sendChatAction")
    bot._app.bot.send_message = _method("sendMessage")
    bot._app.bot.edit_message_text = _method("editMessageText", 1)
    bot._app.bot.delete_message = _method("deleteMessage")
    bot._app.bot.send_document = _method("sendDocument")
    return bot


async def _stimulus(sessions, every=0.1):
    """Sessions that are actually working: a new tool row every 0.1 s, so
    the card always has something new to show. Without it the card text
    only changes when its elapsed counter does, and the observed edit rate
    would say more about the fixture than about the cadence."""
    n = 0
    while True:
        await asyncio.sleep(every)
        n += 1
        for sess in sessions:
            sess.record_tool(f"Bash: step {n}", True)
            sess.stream_dirty = True


def _run(vloop, bot, sessions, seconds: float, *, at=None):
    """Animate `sessions` for `seconds` of SIMULATED time."""
    async def main():
        tasks = [asyncio.ensure_future(bot._animate_busy(s)) for s in sessions]
        tasks += [asyncio.ensure_future(bot._animate_typing(s))
                  for s in sessions]
        tasks.append(asyncio.ensure_future(_stimulus(sessions)))
        for when, fn in (at or []):
            async def _later(when=when, fn=fn):
                await asyncio.sleep(when)
                out = fn()
                if asyncio.iscoroutine(out):
                    await out
            tasks.append(asyncio.ensure_future(_later()))
        await asyncio.sleep(seconds)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    vloop.run_until_complete(main())


def _most_in_window(stamps: list[float], window: float) -> int:
    return max((sum(1 for t in stamps if s <= t < s + window) for s in stamps),
               default=0)


# ── row E: 45 minutes of two streaming sessions ──────────────────────────────

def test_two_sessions_stream_for_45_minutes_with_no_429_and_under_the_cap(
    mk_bot, vloop, penalised_telegram, replay_limiter,
):
    """Row E, and the shape of the incident itself.

    Two sessions, one PRIVATE chat, 45 simulated minutes of continuous
    work against a fake that shrinks its allowance on every violation.
    The assertions are the ones 0.7.12 could not meet:

    * zero 429s — the cards pace themselves under the allowance instead of
      discovering it by being refused;
    * never more than `FLOOD_SUSTAINED_MAX` metered calls in ANY rolling
      `FLOOD_SUSTAINED_WINDOW`, for a PRIVATE chat, which had no such
      window at all before 8.27 (its 1/s bucket permitted 60 a minute
      indefinitely — which is precisely what ran for 45 minutes here);
    * nothing muted, because nothing was ever banned.

    Mutation: build the sustained window `if is_group` again and
    `_most_in_window` goes past the cap; drop `sustained_min_gap` from
    `card_interval` and the cards fight the cap instead of respecting it.
    """
    bot = _ext_bot(mk_bot(), penalised_telegram, replay_limiter)
    cards = [_card(bot, "a", 10), _card(bot, "b", 11)]

    _run(vloop, bot, cards, 2700.0)

    assert penalised_telegram.violations == [], \
        f"{len(penalised_telegram.violations)} 429(s) in 45 minutes"
    assert penalised_telegram.answered == []
    assert penalised_telegram.into_ban == []
    assert not MUTE.is_muted(PRIVATE)

    metered = penalised_telegram.metered_stamps_for(PRIVATE)
    worst = _most_in_window(metered, config.FLOOD_SUSTAINED_WINDOW)
    assert worst <= config.FLOOD_SUSTAINED_MAX, \
        f"{worst} metered calls in one rolling window"
    assert metered, "the cards never edited at all — the row proves nothing"


def test_the_penalised_fake_really_does_ban_an_unpaced_sender(
    vloop, penalised_telegram,
):
    """The harness's own control, and the most important test in this
    file: a fake that never bans makes every other row here vacuous.

    Drives the fake DIRECTLY, with no limiter in the way, exactly as an
    unpaced 0.7.12 busy card would — and gets the real ladder back.
    """
    answers = [penalised_telegram.admit("editMessageText", PRIVATE, 10)
               for _ in range(50)]
    assert any(a is not None for a in answers), "the fake never refused"
    assert penalised_telegram.answered[0] == \
        penalised_telegram.LADDER[0] == 1283.0
    assert penalised_telegram.banned(PRIVATE)
    # And every further request lands INSIDE the ban, which is the count
    # the whole ship is judged on.
    assert penalised_telegram.into_ban, "requests into the ban were not recorded"


def test_the_allowance_shrinks_with_each_ban(vloop, penalised_telegram):
    """The property that makes a GUESSED constant unworkable and a
    LEARNED rate necessary: what was safe yesterday is not safe today.

    Mutation: make `_allowance` constant and the fake degenerates into
    `_StrictTelegram`, which is the clean-bot assumption 8.21 shipped.
    """
    before = penalised_telegram._allowance(PRIVATE)
    for _ in range(50):
        penalised_telegram.admit("editMessageText", PRIVATE, 10)
    assert penalised_telegram._allowance(PRIVATE) < before


# ── row N: the full replay, bans and all ─────────────────────────────────────

def test_the_replay_makes_zero_requests_into_any_active_ban(
    mk_bot, vloop, penalised_telegram, replay_limiter,
):
    """Row N, the acceptance criterion.

    A ban is armed part-way through a live run — as one really was on
    2026-09-15, mid-turn, with two cards ticking and a typing bubble
    refreshing — and the run continues for another twenty simulated
    minutes. On 0.7.12 that produced 3, then 5, then 9 requests into the
    active ban, because the mute lived in 22 per-site checks and
    `animation.send_busy` was not one of them.

    The count must now be ZERO. Not "small": a request into an active ban
    is what escalates the ladder, so one is a regression.

    PROMPTS ARRIVE DURING THE BAN, and that is not decoration. The
    already-running cards are refused by `_animate_tick`'s own pre-check,
    which predates this ship — so a replay of those alone would pass on
    0.7.12 and prove nothing. The path that actually failed on 2026-09-15
    was a NEW prompt: the traceback at 18:10:21 runs user prompt ->
    `animation.send_busy` -> `self._app.bot.send_message` ->
    `process_request` -> HTTP -> `RetryAfter: Retry in 33147 seconds`.
    `send_busy` had no mute guard of any kind (`grep MUTE` inside that
    function = 0), so ONLY the gate can refuse it. Same for the
    re-anchor's `delete_message` and the superseded card's document.

    Mutation: delete the gate from `process_request` and `into_ban` fills
    with the busy cards of every prompt that arrived during the ban.
    """
    from aipager.miniapp.server import MiniAppServer

    bot = _ext_bot(mk_bot(), penalised_telegram, replay_limiter)
    cards = [_card(bot, "a", 10), _card(bot, "b", 11)]
    server = MiniAppServer.__new__(MiniAppServer)
    server.bot = bot

    def _telegram_bans_us():
        # Telegram's side and ours, together — both are needed or the row
        # is vacuous. `penalised_telegram.ban(...)` is what makes the fake
        # count later requests as `into_ban`; the two daemon calls are
        # exactly what `transport._send_with_retry` and
        # `rich_message._ban_if_excessive` do on a ban-sized retry_after:
        # arm the mute, record the rate, and make no further call.
        penalised_telegram.ban(PRIVATE, 34212.0)
        MUTE.mute(PRIVATE, 34212.0, source="sendMessage")
        replay_limiter.note_ban(PRIVATE, 34212.0)

    outcomes: list = []

    async def _bounded(name, coro):
        """Run one ungated call site and record HOW it ended.

        Bounded by `wait_for` ON THE VIRTUAL CLOCK, and that bound is
        load-bearing: a ban also drops the chat's earned rate to
        `FLOOD_MIN_RATE`, so a call that is NOT refused would instead
        QUEUE on the blocking acquire for twenty-odd simulated seconds
        apiece. Without the timeout, "no request reached Telegram" would
        be satisfied by the limiter merely being slow.

        Each step is independent: an exception in one must not skip the
        rest, or a row can silently stop exercising the very call site it
        was added for. (It did — found by deleting the gate and watching
        this row still pass.)
        """
        try:
            outcomes.append((name, await asyncio.wait_for(coro, timeout=60.0)))
        except asyncio.TimeoutError:
            outcomes.append((name, "QUEUED — not refused"))
        except Exception as exc:                     # noqa: BLE001
            outcomes.append((name, f"raised {type(exc).__name__}"))

    async def _prompt_arrives(n):
        """A user acts while the chat is banned — the incident path.

        The Mini App mirror is first and is the one that DISCRIMINATES
        THE GATE: it is ESSENTIAL (so minimal mode does not touch it) and
        it has no per-site mute check of any kind — one of the 13
        `self.bot._app.bot.send_message` calls in `miniapp/server.py`,
        none of which ever had a guard and none of which the pre-8.26
        sweep could even see, because it walked `aipager/bot/*.py` only.
        Delete the gate and this one goes into the ban.

        The busy card is the literal 18:10:21 traceback (`send_busy` ->
        `send_message` -> `process_request` -> HTTP -> `RetryAfter: Retry
        in 33147 seconds`). It is refused by minimal mode as well as by
        the gate, so on its own it is evidence rather than proof — which
        is exactly why the mirror is here too.
        """
        fresh = _card(bot, f"late{n}", 20 + n)
        fresh.busy_msg_id = 0
        await _bounded("mirror",
                       server._mirror_session_killed(PRIVATE, f"late{n}"))
        await _bounded("send_busy", bot.send_busy(fresh))
        # `_reanchor_busy_card` and `_close_superseded_card` are
        # DELIBERATELY not driven here. Both take `sess.animate_lock`,
        # which the live animator holds across its own awaits, so calling
        # them from outside the animation loop deadlocks the replay rather
        # than testing anything. They are ORNAMENT sites and are covered
        # directly by `test_gate.py` and `test_priority_classes.py`.

    _run(vloop, bot, cards, 1200.0, at=[
        (120.0, _telegram_bans_us),
        (180.0, lambda: _prompt_arrives(1)),
        (400.0, lambda: _prompt_arrives(2)),
        (900.0, lambda: _prompt_arrives(3)),
    ])

    assert MUTE.is_muted(PRIVATE), "precondition: the ban is still running"
    assert penalised_telegram.into_ban == [], (
        f"{len(penalised_telegram.into_ban)} request(s) went into an active "
        f"ban: {penalised_telegram.into_ban[:5]}")
    # Every ungated call site must have been REACHED and REFUSED —
    # never queued, never skipped by an earlier step's exception.
    assert len(outcomes) == 6, f"a call site was skipped: {outcomes}"
    assert all(result is None for _name, result in outcomes), (
        f"a call during the ban was not refused: {outcomes}")
    assert {name for name, _ in outcomes} == {"mirror", "send_busy"}
    # And the run really did exercise those paths before the ban, so
    # "zero" is not zero-because-nothing-ran.
    assert penalised_telegram.calls, "the replay made no requests at all"


def test_the_replay_loses_no_answer(
    mk_bot, vloop, penalised_telegram, replay_limiter, monkeypatch,
):
    """Row N's other half. Five answers were dropped on 2026-09-15; the
    replay must lose none.

    Each of the two sessions finishes its turn DURING the ban, which is
    the exact circumstance that lost them: the answer went to
    `send_rich_message`, came back `RichMessageFloodBanned`, and the
    `except` arm logged one line while the text went out of scope.

    Mutation: restore that arm's `log.info(..., "no fallback")` in place
    of the hold and `HELD.count()` is 0 here — both answers gone.
    """
    bot = _ext_bot(mk_bot(), penalised_telegram, replay_limiter)
    cards = [_card(bot, "a", 10), _card(bot, "b", 11)]
    bot._maybe_update_bot_name = _noop

    penalised_telegram.ban(PRIVATE, 34212.0)
    MUTE.mute(PRIVATE, 34212.0, source="sendMessage")
    replay_limiter.note_ban(PRIVATE, 34212.0)

    async def _finish():
        for sess in cards:
            sess.status = Status.IDLE
            await bot.notify(sess, "idle_prompt",
                             {"summary": f"the answer from {sess.label}"})

    _run(vloop, bot, cards, 600.0, at=[(60.0, _finish)])

    assert penalised_telegram.into_ban == [], "a send went into the ban"
    assert HELD.count(PRIVATE) == 2, (
        f"{HELD.count(PRIVATE)} of 2 answers were held; the rest are lost")
    held_text = " ".join(e.rich_text for e in HELD.pending(PRIVATE))
    assert "the answer from a" in held_text
    assert "the answer from b" in held_text


def test_the_held_answers_go_out_once_the_ban_lifts(
    mk_bot, vloop, penalised_telegram, replay_limiter, run_async, monkeypatch,
):
    """And the end of the story: the ban lapses and both answers are
    delivered, once each, with the late marker.

    This is the difference between "no answer is lost" as a slogan and as
    a property: the answers exist, and the daemon delivers them without
    anyone asking again.
    """
    import aipager.bot.rich_message as rm
    from aipager.bot import flood

    bot = _ext_bot(mk_bot(), penalised_telegram, replay_limiter)
    sess = _card(bot, "a", 10)
    bot._maybe_update_bot_name = _noop

    MUTE.mute(PRIVATE, 600.0, source="sendMessage")
    bot._hold_answer(sess, "the answer", "the answer", 3)
    assert HELD.count(PRIVATE) == 1

    sent: list[str] = []

    async def _send(chat_id, markdown, **kw):
        sent.append(markdown)
        return {"message_id": 99}

    monkeypatch.setattr(rm, "_rate_limiter", replay_limiter)
    monkeypatch.setattr("aipager.bot.notify.send_rich_message", _send)
    # Move flood.py's OWN wall clock past the deadline — the module's own
    # reference, never the global `time` module.
    import types as _types
    real_time = flood.time
    monkeypatch.setattr(flood, "time", _types.SimpleNamespace(
        monotonic=real_time.monotonic,
        time=lambda: real_time.time() + 601.0))

    assert run_async(bot.flush_held_answers(sess)) == 1
    assert len(sent) == 1
    assert sent[0].startswith("⏳ delivered late (held ")
    assert "the answer" in sent[0]
    assert HELD.count(PRIVATE) == 0


async def _noop(*a, **kw):
    return None
