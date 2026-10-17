"""An album (sendMediaGroup) is one call but N messages to Telegram: it
spends one token per item of the chat's budget, clamped to what each
limit can hold so a big album never waits for ever. Driven on the same
fake clock as tests/test_flood_budget.py: nothing sleeps for real."""

from __future__ import annotations

import asyncio

import pytest

from aipager.bot.flood_budget import BudgetRateLimiter, _request_cost


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
                        asyncio.gather(*pending, return_exceptions=True))
                loop.run_until_complete(loop.shutdown_default_executor())
            finally:
                loop.close()
    return _run


def _limiter(clock, tmp_path, **kw):
    return BudgetRateLimiter(clock=clock, sleep=clock.sleep,
                             signal_path=str(tmp_path / "signal"), **kw)


async def _send(limiter, endpoint, chat_id, items=0):
    data = {"chat_id": chat_id}
    if items:
        data["media"] = [object() for _ in range(items)]

    async def _call():
        return {"ok": True}
    return await limiter.process_request(
        callback=_call, args=(), kwargs={}, endpoint=endpoint, data=data,
        rate_limit_args=None)


def test_request_cost():
    assert _request_cost("sendMediaGroup", {"media": [1, 2, 3]}) == 3
    assert _request_cost("sendMediaGroup", {"media": "x"}) == 1
    assert _request_cost("sendMediaGroup", {}) == 1
    assert _request_cost("sendPhoto", {"media": [1, 2]}) == 1


def test_media_group_spends_one_token_per_item(tmp_path, run_async):
    clock = FakeClock()
    limiter = _limiter(clock, tmp_path, chat_burst=20, chat_max_rate=1.0)
    run_async(_send(limiter, "sendMessage", -1001))
    budget = limiter._budget_for(-1001)
    before_tokens, before_used = budget.chat.tokens(), budget.window.used()
    run_async(_send(limiter, "sendMediaGroup", -1001, items=6))
    assert budget.chat.tokens() == pytest.approx(before_tokens - 6, abs=1e-6)
    assert budget.window.used() == before_used + 6
    run_async(_send(limiter, "sendPhoto", -1001))
    assert budget.window.used() == before_used + 7


def test_album_never_waits_beyond_bucket_capacity(tmp_path, run_async):
    clock = FakeClock()
    limiter = _limiter(clock, tmp_path, chat_burst=3, chat_max_rate=1.0)

    async def go():
        await asyncio.wait_for(_send(limiter, "sendMediaGroup", 555, items=10),
                               timeout=5)
    run_async(go())
    budget = limiter._budget_for(555)
    assert budget.chat.tokens() < 1.0
