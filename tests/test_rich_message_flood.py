"""The rich-message path under flood control (roadmap 8.17, R1–R3).

- R1: every ``sendRichMessage`` / ``editMessageText`` POST acquires through
  the SAME ``AIORateLimiter`` PTB uses — proven with a recording fake on a
  fake clock (no more than 20 acquisitions in any 60 s window, and every
  POST acquires), then once against the real class.
- R2: a 429 whose ``retry_after`` exceeds ``TELEGRAM_MAX_RETRY_AFTER`` is a
  ban — ONE POST, no sleep, no plain-text fallback, ``RichMessageFloodBanned``.
  A small ``retry_after`` is reported to the limiter and retried once, and
  since roadmap 8.21 it is the LIMITER that waits, never this module.
- R3: after a ban on chat A, sends and edits to A make zero HTTP calls and
  zero PTB calls, B still goes out, the animation loop stops, and when the
  mute lapses A goes through again with one "lifted" line.

The HTTP layer is ``httpx.MockTransport`` on the module's own client (the
real ``_post``), or ``_post`` replaced outright. Never a real bot token,
never a real sleep: ``rm._sleep`` is the module's attribute, patched here
instead of ``asyncio.sleep`` (the global module — CLAUDE.md).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import types
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from aipager import config
import aipager.bot.rich_message as rm
from aipager.bot import flood
from aipager.bot.flood import MUTE, FloodMuted
from aipager.bot.rich_message import (
    RichMessageFallbackRequired,
    RichMessageFloodBanned,
    edit_message_text_rich,
    send_rich_message,
)
from aipager.state import Status, TrackedSession

# Captured at import, before conftest's _block_real_telegram_http replaces it:
# the budget tests must drive the real _post through a MockTransport.
_REAL_POST = rm._post

BAN = 28911  # the 2026-09-10 incident's retry_after (~8 h)


# ── fixtures / doubles ───────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _fake_token(monkeypatch):
    monkeypatch.setattr("aipager.config.BOT_TOKEN", "TESTTOKEN")


@pytest.fixture(autouse=True)
def _reset_client(monkeypatch):
    monkeypatch.setattr(rm, "_client", None)
    yield
    if rm._client is not None and not rm._client.is_closed:
        asyncio.new_event_loop().run_until_complete(rm._client.aclose())
    monkeypatch.setattr(rm, "_client", None)


@pytest.fixture
def clock(monkeypatch):
    """Fake clocks bound to flood.py's OWN ``time`` reference only."""
    state = {"mono": 10_000.0, "wall": 1_800_000_000.0}
    fake = types.SimpleNamespace(
        monotonic=lambda: state["mono"], time=lambda: state["wall"],
    )
    monkeypatch.setattr(flood, "time", fake)

    def advance(seconds: float) -> None:
        state["mono"] += seconds
        state["wall"] += seconds

    return advance


@pytest.fixture
def no_sleep(monkeypatch):
    """Record every back-off sleep the rich path asks for, without sleeping."""
    slept: list[float] = []

    async def _fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(rm, "_sleep", _fake_sleep)
    return slept


def _mock_http(monkeypatch, handler):
    """Route the REAL ``_post`` through an httpx MockTransport."""
    monkeypatch.setattr(rm, "_post", _REAL_POST)
    monkeypatch.setattr(
        rm, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


# Captured before conftest's autouse "no real Telegram" fixture replaces
# ``rm._post`` for the length of each test: this is the SEAM the doubles
# in this file stand in for, and the only honest thing to compare them to.
_REAL_POST = rm._post


def _ok(message_id=1):
    return {"ok": True, "result": {"message_id": message_id}}


def _429(retry_after):
    return {"ok": False, "error_code": 429, "description": "Too Many Requests",
            "parameters": {"retry_after": retry_after}}


def _scripted_post(*responses):
    """A ``_post`` double answering the scripted responses in order and
    repeating the last one; records every call and the kind it was made
    with.

    The ``kind`` and ``priority`` keywords mirror the real seam
    (``rich_message._post(method, payload, *, kind="blocking",
    priority="essential")`` — 8.21 and 8.26 respectively). A double
    missing either raises ``TypeError`` for the calls that pass it, which
    the rich path swallows into "unexpected error" and a ``None`` return —
    a silent no-op that reads to the card loop as a permanent failure,
    i.e. a vacuously green test.
    """
    calls: list[tuple[str, dict]] = []
    kinds: list[str] = []
    priorities: list[str] = []
    script = list(responses)

    async def _post(method, payload, *, kind: str = "blocking",
                    priority: str = "essential"):
        calls.append((method, payload))
        kinds.append(kind)
        priorities.append(priority)
        return script.pop(0) if len(script) > 1 else script[0]

    _post.calls = calls              # type: ignore[attr-defined]
    _post.kinds = kinds              # type: ignore[attr-defined]
    _post.priorities = priorities    # type: ignore[attr-defined]
    return _post


def test_the_scripted_post_double_matches_the_seam_it_replaces():
    """The double is only worth anything while its signature matches
    ``rich_message._post``. Compared against ``_REAL_POST``, captured at
    import time — ``rm._post`` itself is replaced by conftest's
    "no real Telegram" guard for the length of every test.

    Mutation: drop the ``kind`` or ``priority`` parameter from
    ``_scripted_post`` and the calls that pass it raise ``TypeError`` into
    the rich path's own ``except Exception``, which returns ``None`` —
    every assertion about such a call would then pass for the wrong
    reason.
    """
    import inspect

    real = inspect.signature(_REAL_POST).parameters
    double = inspect.signature(_scripted_post(_ok())).parameters
    assert list(double) == list(real)
    for name in ("kind", "priority"):
        assert double[name].kind is inspect.Parameter.KEYWORD_ONLY, name
        assert double[name].default == real[name].default, name


class _FakeLimiter:
    """20-per-60 s bucket on a fake clock; "waiting" advances the clock.

    Keyword-only ``process_request`` pins that ``_post`` calls PTB's seam
    with PTB's own parameter names.
    """

    def __init__(self, max_rate=20, period=60.0):
        self.now = 0.0
        self.stamps: list[float] = []
        self.calls: list[tuple[str, object]] = []
        self.inside = False
        self._max, self._period = max_rate, period
        self._window: list[float] = []

    async def process_request(self, *, callback, args, kwargs, endpoint, data,
                              rate_limit_args):
        self._window = [t for t in self._window if self.now - t < self._period]
        if len(self._window) >= self._max:
            self.now = self._window[0] + self._period
            self._window = [t for t in self._window if self.now - t < self._period]
        self._window.append(self.now)
        self.stamps.append(self.now)
        self.calls.append((endpoint, data.get("chat_id")))
        self.inside = True
        try:
            return await callback(*args, **kwargs)
        finally:
            self.inside = False


class _StepClock:
    """A monotonic clock a test steps by hand; ``sleep`` advances it."""

    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += max(seconds, 0.0)
        await asyncio.sleep(0)


def _sess(label="jim", status=Status.IDLE):
    s = TrackedSession(name=f"claude-{label}", label=label, status=status)
    s.busy_started_at = time.monotonic()
    s.scope_kind = "dm"
    s.scope_chat_id = 123456
    return s


# ── R1: one budget ───────────────────────────────────────────────────────────

def test_every_rich_post_acquires_the_shared_budget_and_is_paced(run_async, monkeypatch):
    """Mutation: bypass the limiter in ``_post`` → zero acquisitions
    recorded (and 45 sends land in one 60 s window)."""
    limiter = _FakeLimiter()
    rm.set_rate_limiter(limiter)
    seen_http = []

    def handler(request):
        assert limiter.inside, "the POST must run INSIDE the limiter's callback"
        seen_http.append(json.loads(request.content)["chat_id"])
        return httpx.Response(200, json=_ok(len(seen_http)))

    _mock_http(monkeypatch, handler)

    async def burst():
        for i in range(30):
            await send_rich_message(-100, f"reply {i}")
        for i in range(15):
            await edit_message_text_rich(-100, 1, f"card {i}")

    run_async(burst())

    assert len(seen_http) == 45
    assert len(limiter.stamps) == 45, "every rich POST must acquire the budget"
    assert {ep for ep, _ in limiter.calls} == {"sendRichMessage", "editMessageText"}
    assert all(chat == -100 for _, chat in limiter.calls), \
        "the group bucket is keyed on the payload's chat_id"
    for t in limiter.stamps:
        in_window = sum(1 for s in limiter.stamps if t <= s < t + 60)
        assert in_window <= 20, f"{in_window} acquisitions in the 60 s window at {t}"
    assert max(limiter.stamps) >= 120, "45 sends need three 60 s windows"


def test_the_real_limiter_accepts_the_seam_and_buckets_a_private_chat(
        run_async, monkeypatch):
    """The signature ``_post`` uses is PTB's real one (a keyword renamed in
    a PTB upgrade would fail here first), and EVERY chat gets a per-chat
    bucket — private ones included.

    Until 8.21 this asserted the opposite for a private chat, because
    ``AIORateLimiter`` keys its per-chat bucket on a negative id: the
    assertion documented the bug rather than the behaviour, and two cards
    streaming into one DM went out unpaced. Mutation: make ``_budget_for``
    return ``None`` for positive ids and chat 555 vanishes from the
    snapshot.
    """
    from aipager.bot.flood_budget import BudgetRateLimiter

    limiter = BudgetRateLimiter()
    rm.set_rate_limiter(limiter)
    _mock_http(monkeypatch, lambda request: httpx.Response(200, json=_ok(9)))

    async def few():
        for _ in range(3):
            assert (await send_rich_message(-100, "x"))["message_id"] == 9
        assert (await edit_message_text_rich(555, 1, "x"))["message_id"] == 9

    run_async(few())
    chats = {c["chat_id"]: c for c in limiter.snapshot()["chats"]}
    assert chats[-100]["kind"] == "group"
    # 8.27: the rolling window exists for EVERY chat kind now — a private
    # chat used to have none, so its bucket permitted 60 calls a minute
    # indefinitely. A group keeps the stricter of the two ceilings.
    assert chats[-100]["sustained_limit"] == int(config.TELEGRAM_GROUP_MAX_CALLS)
    assert chats[555]["kind"] == "private", "a private chat is budgeted too"
    assert chats[555]["sustained_limit"] == int(config.FLOOD_SUSTAINED_MAX), \
        "a private chat must have a volume ceiling of its own"
    assert chats[555]["calls"] == 1


# ── R2: a big retry_after ends the attempt ───────────────────────────────────

def test_send_ban_makes_one_post_no_sleep_and_is_not_a_fallback(run_async, monkeypatch, no_sleep):
    post = _scripted_post(_429(BAN))
    monkeypatch.setattr(rm, "_post", post)
    started = time.monotonic()
    with pytest.raises(RichMessageFloodBanned) as ei:
        run_async(send_rich_message(-100, "long reply"))
    assert time.monotonic() - started < 2.0
    assert [m for m, _ in post.calls] == ["sendRichMessage"]
    assert no_sleep == []
    assert ei.value.retry_after == BAN and ei.value.chat_id == -100
    assert not isinstance(ei.value, RichMessageFallbackRequired)
    assert isinstance(ei.value, FloodMuted)
    assert MUTE.is_muted(-100) and MUTE.remaining(-100) == pytest.approx(BAN, abs=1)


def test_edit_ban_makes_one_post_no_sleep_and_raises(run_async, monkeypatch, no_sleep):
    post = _scripted_post(_429(BAN))
    monkeypatch.setattr(rm, "_post", post)
    with pytest.raises(RichMessageFloodBanned) as ei:
        run_async(edit_message_text_rich(-100, 7, "card"))
    assert [m for m, _ in post.calls] == ["editMessageText"]
    assert no_sleep == []
    assert ei.value.retry_after == BAN
    assert MUTE.is_muted(-100)


@pytest.mark.parametrize("call", [
    lambda: send_rich_message(-100, "x"),
    lambda: edit_message_text_rich(-100, 7, "x"),
], ids=["send", "edit"])
def test_a_ban_on_the_retry_after_a_small_429_mutes_too(run_async, monkeypatch, no_sleep, call):
    """The ban-on-the-retry path is preserved exactly; only who waits for
    the first 429 changed (the limiter, not a private sleep here)."""
    post = _scripted_post(_429(5), _429(BAN))
    monkeypatch.setattr(rm, "_post", post)
    with pytest.raises(RichMessageFloodBanned):
        run_async(call())
    assert len(post.calls) == 2
    assert no_sleep == []
    assert MUTE.is_muted(-100)


@pytest.mark.parametrize("call", [
    lambda: send_rich_message(-100, "x"),
    lambda: edit_message_text_rich(-100, 7, "x"),
], ids=["send", "edit"])
def test_small_retry_after_defers_through_the_limiter_and_never_sleeps(
        run_async, monkeypatch, no_sleep, call):
    """429/5 → tell the limiter → retry once → result. Still exactly two
    POSTs, but this module no longer waits: the limiter bars the whole
    chat for the five seconds Telegram asked for, so the retry's own
    acquire is the wait, and every OTHER caller into that chat is deferred
    too. Sleeping here as well would double it.

    Mutation: restore the ``min(retry_after, 30)`` clamp-sleep in either
    response handler and ``no_sleep`` records a 5.
    """
    from aipager.bot.flood_budget import BudgetRateLimiter

    limiter = BudgetRateLimiter()
    rm.set_rate_limiter(limiter)
    post = _scripted_post(_429(5), _ok(42))
    monkeypatch.setattr(rm, "_post", post)
    assert run_async(call()) == {"message_id": 42}
    assert len(post.calls) == 2
    assert no_sleep == []
    assert limiter.cadence_multiplier(-100) == 2.0
    assert not MUTE.is_muted(-100)


def test_retry_after_exactly_at_the_cap_is_a_rate_limit_not_a_ban(run_async, monkeypatch, no_sleep):
    """Boundary: the cap is exclusive (``>``), so the cap value itself is
    still a rate limit — reported to the limiter, retried, not muted.

    The clamp to 30 s is gone with the private sleep (8.21); the boundary
    semantics this test is really named for are unchanged.
    """
    from aipager.bot.flood_budget import BudgetRateLimiter
    from aipager.config import TELEGRAM_MAX_RETRY_AFTER

    limiter = BudgetRateLimiter()
    rm.set_rate_limiter(limiter)
    post = _scripted_post(_429(int(TELEGRAM_MAX_RETRY_AFTER)), _ok(1))
    monkeypatch.setattr(rm, "_post", post)
    assert run_async(send_rich_message(-100, "x")) == {"message_id": 1}
    assert no_sleep == []
    assert limiter.cadence_multiplier(-100) == 2.0
    assert not MUTE.is_muted(-100)


def test_429_without_retry_after_keeps_the_30s_default(run_async, monkeypatch, no_sleep):
    """``_retry_after_of``'s default of 30 is what this test is named for
    and is unchanged; only who waits it out moved (8.21)."""
    from aipager.bot.flood_budget import BudgetRateLimiter

    clock = _StepClock()
    limiter = BudgetRateLimiter(clock=clock, sleep=clock.sleep)
    rm.set_rate_limiter(limiter)
    post = _scripted_post({"ok": False, "error_code": 429, "description": "x"}, _ok(1))
    monkeypatch.setattr(rm, "_post", post)
    started = clock.now
    assert run_async(edit_message_text_rich(-100, 7, "x")) == {"message_id": 1}
    assert no_sleep == []
    assert limiter.snapshot()["chats"][0]["retry_until_in"] == pytest.approx(
        30.0 - (clock.now - started))
    assert not MUTE.is_muted(-100)


# ── R3: the mute ─────────────────────────────────────────────────────────────

def test_after_a_ban_sends_and_edits_to_that_chat_make_zero_http_calls(run_async, monkeypatch, no_sleep):
    post = _scripted_post(_429(BAN))
    monkeypatch.setattr(rm, "_post", post)
    with pytest.raises(RichMessageFloodBanned):
        run_async(send_rich_message(-100, "the one attempt"))
    assert len(post.calls) == 1

    monkeypatch.setattr(rm, "_post", _scripted_post(_ok(5)))  # would succeed
    with pytest.raises(RichMessageFloodBanned):
        run_async(send_rich_message(-100, "next answer"))
    with pytest.raises(RichMessageFloodBanned):
        run_async(edit_message_text_rich(-100, 7, "next card edit"))
    assert rm._post.calls == [], "a muted chat costs zero attempts"
    assert no_sleep == []

    assert run_async(send_rich_message(-200, "other chat")) == {"message_id": 5}
    assert [p["chat_id"] for _, p in rm._post.calls] == [-200]


def test_mute_lifts_on_the_clock_and_logs_lifted_once(run_async, monkeypatch, no_sleep, clock, caplog):
    monkeypatch.setattr(rm, "_post", _scripted_post(_429(BAN)))
    with pytest.raises(RichMessageFloodBanned):
        run_async(send_rich_message(-100, "x"))
    monkeypatch.setattr(rm, "_post", _scripted_post(_ok(8)))
    clock(BAN - 1)
    with pytest.raises(RichMessageFloodBanned):
        run_async(send_rich_message(-100, "still muted"))
    clock(2)
    with caplog.at_level(logging.INFO, logger="aipager.bot.flood"):
        assert run_async(send_rich_message(-100, "goes through")) == {"message_id": 8}
        assert run_async(send_rich_message(-100, "and again")) == {"message_id": 8}
    lifted = [r for r in caplog.records if "lifted" in r.getMessage()]
    assert len(lifted) == 1
    assert len(rm._post.calls) == 2


# ── animation ────────────────────────────────────────────────────────────────

def _busy_sess():
    s = _sess(status=Status.BUSY)
    s.busy_msg_id = 10
    s.stream_last_rendered = ""
    return s


def test_edit_busy_rich_stops_the_animation_on_a_ban_without_degrading(mk_bot, run_async, monkeypatch):
    """Mutation: drop the ``RichMessageFloodBanned`` arm and the exception
    escapes ``_edit_busy_rich`` (it is deliberately not a Fallback)."""
    bot = mk_bot()
    sess = _busy_sess()
    monkeypatch.setattr(
        "aipager.bot.animation.edit_message_text_rich",
        AsyncMock(side_effect=RichMessageFloodBanned(BAN, 123456)),
    )
    bot._app.bot.edit_message_text = AsyncMock()
    assert run_async(bot._edit_busy_rich(sess, "Working")) is None
    bot._app.bot.edit_message_text.assert_not_awaited()


def test_edit_busy_rich_on_a_muted_chat_makes_no_http_call(mk_bot, run_async, monkeypatch):
    """Through the REAL edit_message_text_rich: the mute check runs before
    any POST."""
    bot = mk_bot()
    sess = _busy_sess()
    post = _scripted_post(_ok(10))
    monkeypatch.setattr(rm, "_post", post)
    MUTE.mute(123456, BAN)
    bot._app.bot.edit_message_text = AsyncMock()
    assert run_async(bot._edit_busy_rich(sess, "Working")) is None
    assert post.calls == []
    bot._app.bot.edit_message_text.assert_not_awaited()


def test_animate_tick_ends_the_loop_while_the_chat_is_muted(mk_bot, run_async, monkeypatch):
    """Mutation: drop the guard at the top of ``_animate_tick`` → the tick
    edits (mocked to succeed) and sends a typing indicator."""
    bot = mk_bot()
    sess = _busy_sess()
    monkeypatch.setattr("aipager.bot.animation.edit_message_text_rich",
                        AsyncMock(return_value={"message_id": 10}))
    bot._app.bot.send_chat_action = AsyncMock()
    MUTE.mute(123456, BAN)
    assert run_async(bot._animate_tick(sess, "Working", False)) is None
    bot._app.bot.send_chat_action.assert_not_awaited()
    from aipager.bot import animation
    animation.edit_message_text_rich.assert_not_awaited()


def test_animate_busy_loop_stops_for_a_session_in_a_muted_chat(mk_bot, run_async, monkeypatch):
    """The whole loop, real ``edit_message_text_rich``: one tick, then done.
    Without the mute handling it would tick every STREAM_EDIT_INTERVAL
    for as long as the session is BUSY and the 3 s wait would expire."""
    bot = mk_bot()
    sess = _busy_sess()
    post = _scripted_post(_ok(10))
    monkeypatch.setattr(rm, "_post", post)
    monkeypatch.setattr("aipager.bot.animation.FIRST_TICK_DELAY", 0.01)
    # The first-tick delay is max(FIRST_TICK_DELAY, the chat's floor)
    # since 8.21, so the floor has to come down with it or this waits a
    # full second. Behaviour under test is unchanged.
    monkeypatch.setattr("aipager.bot.animation.CARD_CADENCE_FLOOR_PRIVATE", 0.01)
    bot._app.bot.send_chat_action = AsyncMock()
    MUTE.mute(123456, BAN)
    run_async(asyncio.wait_for(bot._animate_busy(sess), timeout=3.0))
    assert post.calls == []
    bot._app.bot.send_chat_action.assert_not_awaited()


# ── notify: the finished answer ──────────────────────────────────────────────

def _idle_bot(mk_bot):
    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    bot._app.bot.send_document = AsyncMock()
    bot._app.bot.delete_message = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    return bot


def test_idle_answer_hit_by_a_ban_costs_exactly_one_attempt_and_no_ptb_fallback(
    mk_bot, run_async, monkeypatch, no_sleep,
):
    """On the pre-fix tree this path made THREE attempts per banned reply:
    the rich POST, a rich retry after a 30 s clamp, then the PTB plain-text
    fallback (measured 2026-09-10 — see the deliver summary)."""
    bot = _idle_bot(mk_bot)
    sess = _sess()
    post = _scripted_post(_429(BAN))
    monkeypatch.setattr(rm, "_post", post)
    run_async(bot.notify(sess, "idle_prompt", {"raw_md": "# Heading\n\nText"}))
    assert [m for m, _ in post.calls] == ["sendRichMessage"]
    assert no_sleep == []
    bot._app.bot.send_message.assert_not_awaited()
    bot._app.bot.send_document.assert_not_awaited()
    assert MUTE.is_muted(123456)


def test_idle_answer_into_a_muted_chat_costs_zero_attempts_header_body_and_attachment(
    mk_bot, run_async, monkeypatch, no_sleep,
):
    """An overflowing answer takes the standalone-header + attachment route:
    the header (PTB), the body (rich) and the document (PTB) are all sends,
    and all three must be skipped."""
    bot = _idle_bot(mk_bot)
    sess = _sess()
    post = _scripted_post(_ok(1))
    monkeypatch.setattr(rm, "_post", post)
    MUTE.mute(123456, BAN)
    run_async(bot.notify(sess, "idle_prompt", {"raw_md": "word " * 9000}))
    assert post.calls == []
    bot._app.bot.send_message.assert_not_awaited()
    bot._app.bot.send_document.assert_not_awaited()


def test_idle_answer_with_no_ban_is_untouched(mk_bot, run_async, monkeypatch):
    """R7 sanity: the happy path still sends the rich body, once."""
    bot = _idle_bot(mk_bot)
    sess = _sess()
    post = _scripted_post(_ok(1))
    monkeypatch.setattr(rm, "_post", post)
    run_async(bot.notify(sess, "idle_prompt", {"raw_md": "# Heading\n\nText"}))
    assert [m for m, _ in post.calls] == ["sendRichMessage"]


def test_job_buffer_flush_hit_by_a_ban_does_not_fall_back(mk_bot, run_async, monkeypatch, no_sleep):
    bot = _idle_bot(mk_bot)
    sess = _sess()
    sess.job_interim_buffer.append("interim answer")
    post = _scripted_post(_429(BAN))
    monkeypatch.setattr(rm, "_post", post)
    run_async(bot._flush_job_buffer(sess))
    assert [m for m, _ in post.calls] == ["sendRichMessage"]
    bot._app.bot.send_message.assert_not_awaited()


def test_merged_final_hit_by_a_ban_returns_false_and_the_replace_path_then_skips(
    mk_bot, run_async, monkeypatch, no_sleep,
):
    bot = _idle_bot(mk_bot)
    sess = _busy_sess()
    post = _scripted_post(_429(BAN))
    monkeypatch.setattr(rm, "_post", post)
    delivered = run_async(bot._send_merged_final(sess, "answer", send_as_new=True, reply_to=5))
    assert delivered is False
    assert [m for m, _ in post.calls] == ["sendRichMessage"]
    # The caller's replace-style fallback is itself a rich send: muted now.
    with pytest.raises(RichMessageFloodBanned):
        run_async(send_rich_message(123456, "replace-style body"))
    assert len(post.calls) == 1
    bot._app.bot.send_message.assert_not_awaited()


def test_merged_final_edit_hit_by_a_ban_returns_false(mk_bot, run_async, monkeypatch, no_sleep):
    bot = _idle_bot(mk_bot)
    sess = _busy_sess()
    post = _scripted_post(_429(BAN))
    monkeypatch.setattr(rm, "_post", post)
    assert run_async(bot._send_merged_final(sess, "answer")) is False
    assert [m for m, _ in post.calls] == ["editMessageText"]
    assert MUTE.is_muted(123456)


def test_merged_fallback_does_not_delete_the_stale_card_into_a_ban(
    mk_bot, run_async, monkeypatch, no_sleep,
):
    """``merged`` layout, whole idle path: the combined card+answer edit is
    the one attempt. Its ban makes the caller fall back to the replace
    style, which first deletes the stale card — a send too, skipped while
    muted — and then sends the body, skipped before any HTTP."""
    import dataclasses

    from aipager import preferences

    real = preferences.resolve_preferences

    def merged(scope_chat_id, overrides=None):
        return dataclasses.replace(real(scope_chat_id, overrides), layout="merged")

    monkeypatch.setattr(preferences, "resolve_preferences", merged)
    bot = _idle_bot(mk_bot)
    # The idle path is keyed on status == IDLE; the card is still live.
    sess = _sess()
    sess.busy_msg_id = 10
    sess.stream_last_rendered = ""
    post = _scripted_post(_429(BAN))
    monkeypatch.setattr(rm, "_post", post)
    run_async(bot.notify(sess, "idle_prompt", {"raw_md": "# Heading\n\nText"}))
    assert [m for m, _ in post.calls] == ["editMessageText"]
    bot._app.bot.delete_message.assert_not_awaited()
    bot._app.bot.send_message.assert_not_awaited()
    assert MUTE.is_muted(123456)
