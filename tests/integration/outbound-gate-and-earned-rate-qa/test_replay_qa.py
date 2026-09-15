"""§6 / row N — the 2026-09-15 replay, criteria 13 and 24.

Two sessions BUSY for ~45 simulated minutes in ONE private chat, prompts
arriving during bans, against a Telegram that behaves like it does toward
a bot WITH A HISTORY: a shrinking allowance and the real escalating ladder
``retry_after`` 1283 -> 312 -> 34212.

Three numbers, all from the incident:

* requests into an ACTIVE ban — 3, then 5, then 9 on 0.7.12, must be 0;
* answers lost — 5 on 0.7.12, must be 0;
* calls per rolling ``FLOOD_SUSTAINED_WINDOW`` — must stay under
  ``FLOOD_SUSTAINED_MAX`` for a PRIVATE chat, which had no such window at
  all before this ship.

The last test in this file is the one that makes the other three worth
reading: it runs the SAME replay with the gate neutralised and asserts the
harness then records requests into the ban. A row that cannot fail is not
evidence, and this row was reported vacuous three times before it bit.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from telegram.error import RetryAfter

import aipager.bot.rich_message as rm
from aipager import config
from aipager.bot import flood
from aipager.bot.flood import MUTE
from aipager.bot.held import HELD
from aipager.state import Status, TrackedSession

CHAT = 256113222        # the operator's private chat, as pinned by conftest
MINUTES_45 = 2700.0


def most_in_window(stamps, window: float) -> int:
    ordered = sorted(stamps)
    return max((sum(1 for t in ordered[i:] if t - start < window)
                for i, start in enumerate(ordered)), default=0)


class _ReplayBot:
    """The PTB side of the replay.

    Every method funnels through the real ``process_request`` and then into
    the penalised fake, which answers exactly as Telegram would — including
    raising ``RetryAfter`` so the limiter's own 429 and ban branches run.
    A double that merely recorded would take the two branches this replay
    exists to exercise out of the picture.
    """

    _ENDPOINTS = {
        "send_message": "sendMessage",
        "edit_message_text": "editMessageText",
        "delete_message": "deleteMessage",
        "send_document": "sendDocument",
        "send_chat_action": "sendChatAction",
        "set_message_reaction": "setMessageReaction",
        "pin_chat_message": "pinChatMessage",
        "edit_message_reply_markup": "editMessageReplyMarkup",
    }

    def __init__(self, limiter, fake):
        self._limiter = limiter
        self._fake = fake

    def __getattr__(self, name):
        if name.startswith("_") or name not in self._ENDPOINTS:
            raise AttributeError(name)
        endpoint = self._ENDPOINTS[name]

        async def _method(*args, rate_limit_args=None, **kwargs):
            chat_id = kwargs.get("chat_id")
            if chat_id is None and args:
                chat_id = args[0]

            async def _call():
                retry_after = self._fake.admit(endpoint, chat_id,
                                               kwargs.get("message_id"))
                if retry_after is not None:
                    raise RetryAfter(retry_after)
                return _Sent()

            return await self._limiter.process_request(
                callback=_call, args=(), kwargs={}, endpoint=endpoint,
                data={"chat_id": chat_id}, rate_limit_args=rate_limit_args)

        return _method


class _Sent:
    message_id = 10

    def __bool__(self):
        return True


def _busy(label: str, msg_id: int) -> TrackedSession:
    sess = TrackedSession(name=f"claude-{label}", label=label,
                          status=Status.BUSY)
    sess.busy_msg_id = msg_id
    sess.busy_started_at = time.monotonic()
    sess.stream_last_rendered = ""
    sess.scope_kind = "dm"
    sess.scope_chat_id = CHAT
    return sess


def _idle(label: str) -> TrackedSession:
    sess = TrackedSession(name=f"claude-{label}", label=label,
                          status=Status.IDLE)
    sess.scope_kind = "dm"
    sess.scope_chat_id = CHAT
    return sess


def _answers_on_the_wire(fake, payloads) -> set[str]:
    return {text for text in payloads}


class _Replay:
    """The result of one run, so each assertion can be its own test."""

    def __init__(self, fake, limiter, attempted, delivered, rate_after):
        self.fake = fake
        self.limiter = limiter
        self.attempted = attempted
        self.delivered = delivered
        #: The earned rate at the END of the 45 minutes — before the
        #: drain's quiet hours let it climb back, which they must.
        self.rate_after = rate_after


@pytest.fixture
def replay(vloop, penalised_telegram, replay_limiter, mk_bot, monkeypatch):
    """Run the 45-minute timeline once and hand the numbers over.

    The fake's allowance starts at a QUARTER of Telegram's published
    ceiling. That is the whole point of §6: the account this happened to
    was already penalised, and measuring a flood fix against a clean-bot
    regime is the mistake ``per-chat-flood-budget.md`` §12 made. At the
    published 1 call/s the daemon's own pacing never violates anything,
    no ban is ever issued, and "zero requests into a ban" becomes a
    statement about an empty set — which is exactly how this row was
    vacuous three times. Verified by running it both ways.
    """
    penalised_telegram.rate = config.TELEGRAM_PRIVATE_MAX_RATE / 4

    def _run(*, gate: bool = True, allowance: float | None = None):
        if allowance is not None:
            penalised_telegram.rate = allowance
        if not gate:
            # The control: neutralise the ONE thing under test, leaving
            # the fake, the ladder, the traffic and the assertions
            # identical.
            monkeypatch.setattr(flood.MUTE, "is_muted", lambda chat_id: False)

        bot = mk_bot()
        bot._app.bot = _ReplayBot(replay_limiter, penalised_telegram)
        bot._maybe_update_bot_name = _noop
        cards = [_busy("jim", 10), _busy("bob", 11)]
        # One session per prompt. The held buffer keeps ONE ENTRY PER
        # SESSION ("newest per turn wins"), so nine prompts to a single
        # session would collapse to one answer and the row would be
        # measuring that cap instead of the gate. The collapse itself is
        # pinned separately, at the bottom of this file.
        answer_sessions = [_idle(f"s{i}") for i in range(9)]
        attempted: list[str] = []
        measured: dict = {}
        delivered: list[str] = []

        original_post = rm._post

        async def _recording_post(method, payload, **kw):
            body = (payload.get("rich_message") or {}).get("markdown", "")
            result = await original_post(method, payload, **kw)
            for text in attempted:
                if text in body:
                    delivered.append(text)
            return result

        monkeypatch.setattr(rm, "_post", _recording_post)

        async def _scenario():
            tasks = [asyncio.ensure_future(bot._animate_busy(s)) for s in cards]
            try:
                for i, sess in enumerate(answer_sessions):
                    await asyncio.sleep(MINUTES_45 / 9)
                    text = f"ANSWER {i}"
                    attempted.append(text)
                    await bot.notify(sess, "idle_prompt", {"raw_md": text})
                    # The 2 s monitor tick, modelled: anything held goes
                    # out as soon as the mute has lifted.
                    for held in answer_sessions:
                        await bot.flush_held_answers(held)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

            # A ban outlasts the traffic that earned it, so the timeline
            # is followed to its end: "no answer lost" is a claim about
            # the whole incident, not about its first 45 minutes.
            measured["rate"] = replay_limiter.earned_rate(CHAT)

            # Long enough to outlast two overlapping 9.5-hour bans: the
            # claim is about the whole incident, not its first hour.
            for _ in range(4000):
                if not HELD.count() and not MUTE.is_muted(CHAT):
                    break
                await asyncio.sleep(60)
                for held in answer_sessions:
                    await bot.flush_held_answers(held)

        vloop.run_until_complete(asyncio.wait_for(_scenario(), timeout=1e9))
        return _Replay(penalised_telegram, replay_limiter, attempted,
                       delivered, measured.get("rate"))

    return _run


async def _noop(*a, **kw):
    return None


# ===== row N — the three numbers ========================================

def test_the_replay_actually_provoked_a_ban(replay):
    """The precondition every other row in this file rests on. A replay
    that never gets banned proves nothing about behaviour during a ban,
    and that is exactly how this row was vacuous three times."""
    result = replay()
    assert result.fake.answered, "the penalised regime never banned anything"


def test_zero_requests_are_made_into_an_active_ban(replay):
    """Criterion 24, the number the whole ship is judged on: 3, then 5,
    then 9 on 0.7.12."""
    result = replay()
    assert result.fake.into_ban == []


def test_no_answer_is_lost(replay):
    """Criterion 24's second number: five answers were dropped on
    2026-09-15 with 'no fallback'. Every prompt's answer must appear on
    the wire, on time or late."""
    result = replay()
    assert sorted(set(result.delivered)) == sorted(result.attempted)


def test_nothing_is_left_holding_at_the_end(replay):
    """The other half of 'no answer lost': an answer still in the buffer
    when the daemon stops is an answer lost, just later."""
    replay()
    assert HELD.count() == 0


def test_the_sustained_cap_holds_for_a_private_chat(replay):
    """Criterion 13 / 24's third number. A private chat had NO sustained
    window before this ship, which is how two BUSY sessions saturated one
    for 45 minutes and earned 9.5 hours."""
    result = replay()
    assert most_in_window(result.fake.metered_stamps_for(CHAT),
                          config.FLOOD_SUSTAINED_WINDOW) <= \
        config.FLOOD_SUSTAINED_MAX


def test_the_daemon_learns_a_slower_rate_from_the_regime(replay):
    """R4's whole thesis: the rate is LEARNED, not guessed. After a
    penalised regime has answered a ban, the chat's earned rate must be
    below the rate it started at — otherwise the daemon has learned
    nothing and will earn the same ban again tomorrow."""
    result = replay()
    assert result.rate_after < config.FLOOD_START_RATE


# ===== the control that makes the rows above evidence ==================

def test_the_same_replay_without_the_gate_does_fire_into_the_ban(replay):
    """Row N's honesty check.

    Everything is held constant except the gate: ``MUTE.is_muted`` is
    forced to ``False``, which is precisely the 0.7.12 world where the
    chokepoint did not know bans existed (``grep -c MUTE
    flood_budget.py`` = 0). If the assertions above still passed here,
    they would be measuring the harness rather than the fix.
    """
    result = replay(gate=False)
    assert result.fake.into_ban != []


# ===== criterion 13 — row E, at Telegram's PUBLISHED allowance =========

def test_two_sessions_streaming_for_45_minutes_earn_no_429_at_all(replay):
    """Row E / criterion 13. Two sessions streaming for 45 simulated
    minutes against a Telegram allowing its published 1 call/s must
    produce ZERO 429s — the 0.7.12 daemon ran two BUSY sessions for
    ~45 minutes continuously and earned 9.5 hours."""
    result = replay(allowance=config.TELEGRAM_PRIVATE_MAX_RATE)
    assert result.fake.violations == []


def test_that_run_also_stays_under_the_sustained_cap(replay):
    """The same 45 minutes, the other half of row E: no rolling
    ``FLOOD_SUSTAINED_WINDOW`` may hold more than
    ``FLOOD_SUSTAINED_MAX`` calls — for a PRIVATE chat."""
    result = replay(allowance=config.TELEGRAM_PRIVATE_MAX_RATE)
    assert most_in_window(result.fake.metered_stamps_for(CHAT),
                          config.FLOOD_SUSTAINED_WINDOW) <= \
        config.FLOOD_SUSTAINED_MAX


def test_no_answer_is_lost_at_the_published_allowance_either(replay):
    result = replay(allowance=config.TELEGRAM_PRIVATE_MAX_RATE)
    assert sorted(set(result.delivered)) == sorted(result.attempted)


# ===== the cap the replay had to route around ==========================

def test_two_answers_from_one_session_during_one_ban_both_arrive(
    mk_bot, run_async, rich_http, qa_clock,
):
    """§3.5's promise is "never lose an answer"; R6's is "nothing is ever
    SILENTLY dropped".

    A single session can finish two turns inside one 9.5-hour ban — the
    user sends a second prompt when the first goes quiet, which is
    precisely what they did on 2026-09-15. Both answers are the user's;
    the newer one is not a re-render of the older.
    """
    bot = mk_bot()
    bot._maybe_update_bot_name = _noop
    sess = _idle("jim")
    MUTE.mute(CHAT, 600.0)
    run_async(bot.notify(sess, "idle_prompt", {"raw_md": "FIRST answer"}))
    run_async(bot.notify(sess, "idle_prompt", {"raw_md": "SECOND answer"}))
    qa_clock.advance(601)
    run_async(bot.flush_held_answers(sess))
    bodies = " ".join((p.get("rich_message") or {}).get("markdown", "")
                      for _m, _c, p in rich_http.requests)
    assert "FIRST answer" in bodies and "SECOND answer" in bodies
