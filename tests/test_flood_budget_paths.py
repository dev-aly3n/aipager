"""Both HTTP paths under the 8.21 budget: rows E1, E2, F, K, L and O.

The daemon reaches Telegram two ways — python-telegram-bot's ``ExtBot``
and ``rich_message``'s own httpx POSTs — and 0.7.10 handled a 429
differently on each, in THREE places (PTB's retry loop,
``rich_message._handle_response`` and ``rich_message._handle_edit_response``).
8.21 gives all of them to the limiter. These rows prove it on the real
code paths, not on doubles.

The rich path runs through ``httpx.MockTransport`` on the module's own
client, reusing ``test_rich_message_flood.py``'s machinery. No test here
sleeps for real, and none patches ``asyncio.sleep`` through a module
path (``aipager.bot.transport.asyncio`` IS the global module; CLAUDE.md).
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import httpx
import pytest
from telegram.error import RetryAfter

import aipager.bot.rich_message as rm
from aipager import config
from aipager.bot.flood import MUTE
from aipager.bot.flood_budget import BudgetRateLimiter, FloodSkipped
from aipager.bot.rich_message import (
    RichMessageFloodBanned,
    edit_message_text_rich,
    get_rate_limiter,
    send_rich_message,
)
from aipager.bot.transport import _send_with_retry

# Captured at import, before conftest's _block_real_telegram_http replaces
# it: these rows must drive the REAL _post through a MockTransport.
_REAL_POST = rm._post

CHAT = 123456
BAN = 19289  # the 2026-09-11 incident's retry_after (5.4 h)


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

    The shared fixture abandons each loop it creates. Rows here leave
    pending limiter waiters and an httpx client behind, and the suite
    runs under a hard 1 GiB ``RLIMIT_AS`` (the first test file to run
    calls ``notify_hook.main()``, which clamps it on the pytest process
    itself and can never raise it back) — a leak here would fail an
    unrelated LATER test with "can't start new thread", not this file.
    Roadmap 8.19; worked around, not fixed.
    """
    loops: list[asyncio.AbstractEventLoop] = []

    def _run(coro):
        loop = asyncio.new_event_loop()
        loops.append(loop)
        return loop.run_until_complete(coro)

    yield _run

    for loop in loops:
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


@pytest.fixture(autouse=True)
def _fake_token(monkeypatch):
    monkeypatch.setattr("aipager.config.BOT_TOKEN", "TESTTOKEN")


@pytest.fixture(autouse=True)
def _reset_client(monkeypatch):
    """Give each row its own httpx client, and CLOSE it — loop included.

    ``test_rich_message_flood.py``'s version of this fixture abandons the
    event loop it builds for the teardown. That is one leaked loop (with
    its epoll fd and self-pipe) per test, and the suite runs under a hard
    1 GiB ``RLIMIT_AS`` that ``notify_hook.main()`` clamps on the pytest
    process itself and can never raise back: the cost does not show up
    here, it shows up as "can't start new thread" in an unrelated later
    test. Roadmap 8.19 — worked around, not fixed.
    """
    monkeypatch.setattr(rm, "_client", None)
    yield
    if rm._client is not None and not rm._client.is_closed:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(rm._client.aclose())
        finally:
            loop.close()
    monkeypatch.setattr(rm, "_client", None)


@pytest.fixture
def no_sleep(monkeypatch):
    """Record every back-off sleep the rich path asks for, without sleeping.

    Since 8.21 the contract is that NOTHING on a 429 path calls it: a row
    that asserts ``no_sleep == []`` is asserting the feature.
    """
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


def _ok(message_id=7):
    return {"ok": True, "result": {"message_id": message_id}}


def _429(retry_after):
    return {"ok": False, "error_code": 429, "description": "Too Many Requests",
            "parameters": {"retry_after": retry_after}}


def _scripted_http(monkeypatch, *responses):
    """Answer the scripted bodies in order, repeating the last; record the
    Bot API method of every request."""
    seen: list[str] = []
    script = list(responses)

    def _handler(request):
        seen.append(request.url.path.rsplit("/", 1)[-1])
        body = script.pop(0) if len(script) > 1 else script[0]
        return httpx.Response(200, json=body)

    _mock_http(monkeypatch, _handler)
    return seen


def _install(clock) -> BudgetRateLimiter:
    limiter = BudgetRateLimiter(clock=clock, sleep=clock.sleep)
    rm.set_rate_limiter(limiter)
    return limiter


def _call(method, **kw):
    if method == "sendRichMessage":
        return send_rich_message(CHAT, "hello", **kw)
    return edit_message_text_rich(CHAT, 10, "hello", **kw)


class _FakeBot:
    def __init__(self, side_effects):
        self._side = list(side_effects)
        self.calls: list[tuple] = []

    async def send_message(self, chat_id, text, **kwargs):
        self.calls.append((text, kwargs))
        s = self._side.pop(0)
        if isinstance(s, Exception):
            raise s
        return s

    async def set_message_reaction(self, chat_id, message_id, emoji):
        self.calls.append(("reaction", emoji))


# ── E2: the rich path's two 429 branches ─────────────────────────────────────

@pytest.mark.parametrize("method", ["sendRichMessage", "editMessageText"])
def test_a_rich_429_never_sleeps_privately(method, run_async, monkeypatch,
                                           no_sleep):
    """Row E2 (§11 D1). A JSON ``error_code: 429`` from EITHER rich branch
    is reported to the limiter and waited out by the limiter — not by a
    module-private ``min(retry_after, 30)`` sleep.

    ``editMessageText`` is the parameter that matters: it is the busy-card
    path, i.e. the 2026-09-11 incident itself, and it has its own
    independent clamp-sleep-retry that a fix to ``_handle_response`` alone
    would leave running.

    Mutation: restore the clamp-sleep in ``_handle_edit_response`` and the
    ``editMessageText`` parameter records ``no_sleep == [5]``.
    """
    clock = FakeClock()
    limiter = _install(clock)
    seen = _scripted_http(monkeypatch, _429(5), _ok(7))
    started = clock.now

    out = run_async(_call(method))

    assert out == {"message_id": 7}
    assert no_sleep == [], "the 429 path must not sleep privately any more"
    assert seen == [method, method], "exactly one POST before and one after"
    assert clock.now == pytest.approx(started + 5.0), "deferred, not slept"
    assert limiter.cadence_multiplier(CHAT) == 2.0
    assert not MUTE.is_muted(CHAT), "a small 429 is a rate limit, not a ban"


@pytest.mark.parametrize("method", ["sendRichMessage", "editMessageText"])
def test_a_rich_429_logs_one_warning_and_no_traceback(method, run_async,
                                                      monkeypatch, no_sleep,
                                                      caplog):
    """R5. Mutation: log per attempt, or with ``exc_info``, and the 2,021
    stack traces of the incident come back."""
    clock = FakeClock()
    _install(clock)
    _scripted_http(monkeypatch, _429(5), _ok(7))
    caplog.set_level("DEBUG")

    run_async(_call(method))

    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1, [r.getMessage() for r in warnings]
    assert warnings[0].name == "aipager.bot.flood_budget"
    assert not any(r.exc_info for r in caplog.records)


@pytest.mark.parametrize("method", ["sendRichMessage", "editMessageText"])
def test_a_rich_skip_caller_is_dropped_on_a_429_not_retried(method, run_async,
                                                            monkeypatch,
                                                            no_sleep):
    """A skippable card edit that meets a 429 is abandoned, not queued.

    Mutation: retry a skip caller and the card re-enters the window that
    just rejected it — one more violation per tick, which is the
    incident's shape.
    """
    clock = FakeClock()
    limiter = _install(clock)
    seen = _scripted_http(monkeypatch, _429(5), _ok(7))

    with pytest.raises(FloodSkipped):
        run_async(_call(method, kind="skip"))

    assert seen == [method], "no retry for a skip caller"
    assert no_sleep == []
    assert limiter.cadence_multiplier(CHAT) == 2.0


@pytest.mark.parametrize("method", ["sendRichMessage", "editMessageText"])
def test_a_rich_skip_is_refused_before_any_http_when_the_budget_is_short(
        method, run_async, monkeypatch, no_sleep):
    """The refusal happens in the limiter, so no POST is made at all.

    Mutation: let ``_post`` swallow ``FloodSkipped`` into ``None`` /
    ``RichMessageFallbackRequired`` and the card would log a warning at
    card cadence and degrade to a plain-text edit — a fresh violation.
    """
    clock = FakeClock()
    limiter = _install(clock)
    seen = _scripted_http(monkeypatch, _ok(7))
    limiter.note_retry_after(CHAT, 5)

    with pytest.raises(FloodSkipped):
        run_async(_call(method, kind="skip"))

    assert seen == []


# ── F: a ban still behaves exactly as 0.7.10 ─────────────────────────────────

@pytest.mark.parametrize("method", ["sendRichMessage", "editMessageText"])
def test_a_ban_on_the_rich_path_still_mutes_and_never_backs_off(method,
                                                                run_async,
                                                                monkeypatch,
                                                                no_sleep):
    """Row F/R6. A ``retry_after`` past the cap is a ban: the 8.17 mute
    owns it, exactly as in 0.7.10, and the limiter keeps its hands off.

    Mutation: let ``note_retry_after`` accept a value over the cap and the
    limiter starts competing with the mute for the one case the mute
    exists to own.
    """
    clock = FakeClock()
    limiter = _install(clock)
    seen = _scripted_http(monkeypatch, _429(BAN))

    with pytest.raises(RichMessageFloodBanned):
        run_async(_call(method))

    assert seen == [method], "one POST only — a retry extends the ban"
    assert no_sleep == []
    assert MUTE.is_muted(CHAT)
    assert limiter.cadence_multiplier(CHAT) == 1.0


# ── E1: the PTB path, and transport's own retry ──────────────────────────────

def test_send_with_retry_never_sleeps_on_a_small_retry_after(run_async):
    """Row E1 / G14. Since 8.21 the limiter defers and retries a small 429
    itself, so anything that still reaches ``_send_with_retry`` has ALREADY
    been retried once — sleeping again would double the wait and put a
    second attempt into the same window.

    Mutation: re-add ``await asyncio.sleep(wait)`` + ``continue`` and this
    makes three attempts (and takes ten real seconds) instead of one.
    """
    bot = _FakeBot([RetryAfter(5), "MSG", "MSG"])

    with pytest.raises(RetryAfter):
        run_async(_send_with_retry(bot, chat_id=1, text="hi"))

    assert len(bot.calls) == 1, "one attempt; the limiter already retried"


def test_send_with_retry_still_mutes_and_reacts_on_a_ban(run_async):
    """R6: the give-up branch is untouched. Mutation: delete the
    ``MUTE.mute`` / 🚨 block with the small-value arm and a ban loses both
    its mute and the one signal the user gets."""
    bot = _FakeBot([RetryAfter(BAN)])

    with pytest.raises(RetryAfter):
        run_async(_send_with_retry(bot, chat_id=7, text="hi",
                                   reply_to_message_id=42))

    assert MUTE.is_muted(7)
    assert ("reaction", "🚨") in bot.calls


def test_a_small_429_on_the_ptb_path_defers_the_whole_chat(run_async):
    """Row E1. The limiter absorbs the ``RetryAfter`` PTB used to re-raise
    with a traceback: zero calls to that chat until it elapses, the
    request runs exactly once more and succeeds, cadence ×2, nothing
    muted.

    Mutation: drop the ``retry_until`` term from the blocking wait and the
    retry goes straight back into Telegram's window.
    """
    clock = FakeClock()
    limiter = BudgetRateLimiter(clock=clock, sleep=clock.sleep)
    attempts: list[float] = []
    started = clock.now

    async def _callback():
        attempts.append(clock.now)
        if len(attempts) == 1:
            raise RetryAfter(5)
        return {"ok": True}

    async def _drive():
        return await limiter.process_request(
            callback=_callback, args=(), kwargs={}, endpoint="sendMessage",
            data={"chat_id": CHAT}, rate_limit_args=None,
        )

    assert run_async(_drive()) == {"ok": True}
    assert attempts == [started, pytest.approx(started + 5.0)]
    assert limiter.cadence_multiplier(CHAT) == 2.0
    assert not MUTE.is_muted(CHAT)


# ── K: the limiter alone holds the rate ──────────────────────────────────────

def test_the_limiter_alone_holds_one_call_a_second_per_chat(run_async,
                                                            monkeypatch):
    """Row K — today's replay. Two "sessions" POST rich edits into ONE
    private chat as fast as they can, with the cadence rule out of the
    picture entirely: R1 must not depend on R4.

    The fake Telegram returns a 429 when a chat is called more than three
    times in a simulated second, so "no 429 came back" is a property of
    the code under test, not of the assertion.

    Mutation: make ``_budget_for`` return ``None`` for positive ids and
    the fake starts answering 429.
    """
    clock = FakeClock()
    _install(clock)
    stamps: list[float] = []
    flooded: list[float] = []

    def _handler(request):
        stamps.append(clock.now)
        recent = [t for t in stamps if clock.now - t < 1.0]
        if len(recent) > 3:
            flooded.append(clock.now)
            return httpx.Response(200, json=_429(5))
        return httpx.Response(200, json=_ok(7))

    _mock_http(monkeypatch, _handler)

    async def _drive():
        for _ in range(12):
            await edit_message_text_rich(CHAT, 10, "hello")

    run_async(_drive())
    assert flooded == [], f"the fake Telegram flood-limited us at {flooded}"
    assert len(stamps) == 12
    assert stamps[-1] - stamps[0] == pytest.approx(9.0)


# ── L: one limiter, both paths ───────────────────────────────────────────────

def test_the_daemons_builder_installs_one_budget_rate_limiter(mk_bot,
                                                              monkeypatch):
    """Row L / R10. The instance ``lifecycle._make_builder`` hands to PTB
    IS the one ``rich_message`` paces itself with.

    Mutation: drop ``set_rate_limiter(limiter)`` and the rich path runs
    unpaced beside PTB's budget — the 8.17 bug, restored.
    """
    from aipager.bot import lifecycle as lc

    seen = {}

    class _RecordingBuilder:
        def __getattr__(self, name):
            def _chain(*args, **kwargs):
                if name == "rate_limiter":
                    seen["limiter"] = args[0] if args else None
                return self
            return _chain

    monkeypatch.setattr(lc, "ApplicationBuilder", _RecordingBuilder)
    mk_bot()._make_builder()

    assert isinstance(seen["limiter"], BudgetRateLimiter)
    assert get_rate_limiter() is seen["limiter"]


def test_the_builder_uses_the_named_per_chat_constants(mk_bot, monkeypatch):
    """The budget is built from ``config``'s named constants and nothing
    else. Mutation: hardcode a number here and the operator's config and
    the limiter's behaviour drift apart silently."""
    from aipager.bot import lifecycle as lc

    seen = {}

    class _RecordingBuilder:
        def __getattr__(self, name):
            def _chain(*args, **kwargs):
                if name == "rate_limiter":
                    seen["limiter"] = args[0]
                return self
            return _chain

    monkeypatch.setattr(lc, "ApplicationBuilder", _RecordingBuilder)
    mk_bot()._make_builder()
    limiter = seen["limiter"]
    assert limiter._overall.capacity == config.TELEGRAM_OVERALL_MAX_RATE
    assert limiter._overall.rate == pytest.approx(
        config.TELEGRAM_OVERALL_MAX_RATE / config.TELEGRAM_OVERALL_TIME_PERIOD)
    assert limiter._chat_max_rate == config.TELEGRAM_PRIVATE_MAX_RATE
    assert limiter._chat_burst == config.TELEGRAM_CHAT_BURST
    assert limiter._group_max_rate == config.TELEGRAM_GROUP_MAX_RATE
    assert limiter._group_time_period == config.TELEGRAM_GROUP_TIME_PERIOD


def test_get_rate_limiter_is_none_until_the_daemon_installs_one():
    """``None`` means "unpaced", the state every test starts in. Mutation:
    default it to a fresh limiter and a test that forgot to install one
    would pass a pacing assertion against a budget nothing else shares."""
    assert get_rate_limiter() is None


def test_start_and_stop_take_the_backoff_signal_down(mk_bot, run_async,
                                                     monkeypatch, tmp_path):
    """Rows J/P. Mutation: delete ``clear_backoff_signal()`` from
    ``lifecycle.start``/``stop`` and a restart inherits — or a dead daemon
    leaves behind — a multiplier `aipager status` will happily report."""
    from aipager.bot import lifecycle as lc

    path = Path(config.FLOOD_BACKOFF_FILE)
    bot = mk_bot()
    monkeypatch.setattr(bot, "_make_builder", lambda: (_ for _ in ()).throw(
        RuntimeError("stop here"),
    ))
    path.write_text('{"backoff": [{"chat_id": 1, "multiplier": 8.0}]}')
    with pytest.raises(RuntimeError):
        run_async(bot.start())
    assert not path.exists(), "start() must drop a previous daemon's backoff"

    path.write_text('{"backoff": [{"chat_id": 1, "multiplier": 8.0}]}')
    bot._app = None
    run_async(bot.stop())
    assert not path.exists(), "stop() must take the signal down with the daemon"
    assert lc is not None


# ── O: the Mini App is budgeted for free ─────────────────────────────────────

def test_no_mini_app_module_builds_its_own_telegram_bot():
    """Row O (§11 D12). Every Mini App send is an attribute call on
    ``bot._app.bot`` — the same ``ExtBot`` the limiter is installed on —
    so it is metered by the chat budget with no code of its own.

    Mutation: construct a ``telegram.Bot(token=...)`` anywhere under
    ``aipager/miniapp/`` (as ``bot/observer.py`` deliberately does, with
    its own token and its own budget) and this names the file.
    """
    from aipager import miniapp

    offenders = []
    for path in sorted(Path(miniapp.__file__).parent.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            func = getattr(node, "func", None)
            name = getattr(func, "attr", None) or getattr(func, "id", None)
            if isinstance(node, ast.Call) and name == "Bot":
                offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == [], offenders


def test_a_mini_app_send_is_counted_by_the_chat_budget(run_async):
    """The behavioural half of row O: a send made on the ExtBot the
    limiter paces lands in that chat's counters like any other."""
    clock = FakeClock()
    limiter = BudgetRateLimiter(clock=clock, sleep=clock.sleep)

    async def _callback():
        return {"ok": True}

    async def _drive():
        await limiter.process_request(
            callback=_callback, args=(), kwargs={}, endpoint="sendMessage",
            data={"chat_id": CHAT}, rate_limit_args=None,
        )

    run_async(_drive())
    chats = limiter.snapshot()["chats"]
    assert [c["chat_id"] for c in chats] == [CHAT]
    assert chats[0]["calls"] == 1
