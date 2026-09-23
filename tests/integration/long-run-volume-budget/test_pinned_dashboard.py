"""The pinned dashboard's debounce (tester-iter2-001, 8.30 iteration 3).

`notify` asks for a dashboard refresh on EVERY hook, headed by the session
that sent it. With two sessions streaming at once the header flipped
("📌 s0" / "📌 s1") on nearly every hook: 1,618 of a two-hour QA run's
2,164 calls, which tripped the hourly shed and minimal mode. A refresh
now goes out at once only on a change of STATE (a session's status, a
session added or gone). Anything else waits PINNED_REFRESH_INTERVAL, or
PINNED_REFRESH_BUSY_INTERVAL while a session is busy, and yields while
the chat's hour is past the bubble's resume mark.

The pinned dashboard exists only on a legacy single-chat install
(``scopes is None`` and a ``CHAT_ID``). Every daemon start writes
``aipager.yaml`` since v2, and that turns it off (bec4a6e), so these rows
set both explicitly.
"""

from __future__ import annotations

import asyncio

import pytest

from aipager import config
from aipager.state import Status

CHAT = 256113222
PINNED = 9999


@pytest.fixture
def dash(mk_bot, vbot, vloop, monkeypatch):
    monkeypatch.setattr("aipager.bot.dashboard.CHAT_ID", str(CHAT))
    bot = mk_bot()
    bot._app.bot = vbot
    bot.registry.pinned_msg_id = PINNED
    for label in ("s0", "s1"):
        sess = bot.registry.get_or_create(f"claude-{label}")
        sess.label = label
        sess.status = Status.IDLE
        sess.scope_chat_id = CHAT
        sess.scope_kind = "dm"
    return bot


def _refreshes(vbot) -> list[float]:
    return vbot.stamps("editMessageText")


def _run(vloop, steps):
    """Run ``steps``, a list of ``(delay, coroutine factory)``, on the
    virtual loop."""
    async def main():
        for delay, step in steps:
            await asyncio.sleep(delay)
            await step()
    vloop.run_until_complete(main())


def test_a_header_flip_alone_waits_for_the_interval(dash, vbot, vloop):
    """Two idle sessions notify in turn. The first refresh goes out. The
    header flips a second later, and that waits until
    PINNED_REFRESH_INTERVAL has passed since the last one shown.

    Mutation: drop the interval check and the flip goes out at once."""
    flip = [lambda: dash._maybe_update_bot_name("claude-s0"),
            lambda: dash._maybe_update_bot_name("claude-s1")]
    _run(vloop, [(0.0, flip[0]), (1.0, flip[1]), (1.0, flip[0]),
                 (config.PINNED_REFRESH_INTERVAL - 1.0, flip[1])])
    stamps = _refreshes(vbot)
    assert len(stamps) == 2, stamps
    assert stamps[1] - stamps[0] >= config.PINNED_REFRESH_INTERVAL - 1e-6


def test_a_status_change_goes_out_at_once(dash, vbot, vloop):
    """A session's status changes a second after a refresh. That is state,
    and it goes out at once.

    Mutation: leave the status out of ``_pinned_state`` and it waits the
    interval."""
    s1 = dash.registry.get("claude-s1")

    async def _busy():
        s1.status = Status.INTERACTIVE
        await dash._maybe_update_bot_name("claude-s0")

    _run(vloop, [(0.0, lambda: dash._maybe_update_bot_name("claude-s0")),
                 (1.0, _busy)])
    stamps = _refreshes(vbot)
    assert len(stamps) == 2, stamps
    assert stamps[1] - stamps[0] < 2.0


def test_a_session_arriving_goes_out_at_once(dash, vbot, vloop):
    """A new session is state too. Mutation: reduce the state to the SET
    of statuses in play and a third idle session waits the interval."""
    async def _arrive():
        sess = dash.registry.get_or_create("claude-s2")
        sess.label = "s2"
        sess.status = Status.IDLE
        await dash._maybe_update_bot_name("claude-s0")

    _run(vloop, [(0.0, lambda: dash._maybe_update_bot_name("claude-s0")),
                 (1.0, _arrive)])
    assert len(_refreshes(vbot)) == 2


def test_while_a_session_is_busy_a_flip_waits_the_busy_interval(
    dash, vbot, vloop,
):
    """With a session BUSY (the bubble is live), a refresh that is not a
    status change waits PINNED_REFRESH_BUSY_INTERVAL, not the idle
    interval.

    Mutation: use PINNED_REFRESH_INTERVAL whatever the status and the
    flip goes out after a minute."""
    dash.registry.get("claude-s0").status = Status.BUSY
    flip = [lambda: dash._maybe_update_bot_name("claude-s0"),
            lambda: dash._maybe_update_bot_name("claude-s1")]
    gap = config.PINNED_REFRESH_INTERVAL + 1.0
    _run(vloop, [(0.0, flip[0]), (gap, flip[1]),
                 (config.PINNED_REFRESH_BUSY_INTERVAL - gap, flip[1])])
    stamps = _refreshes(vbot)
    assert len(stamps) == 2, stamps
    assert stamps[1] - stamps[0] >= config.PINNED_REFRESH_BUSY_INTERVAL - 1e-6


def test_a_routine_refresh_yields_once_the_hour_nears_the_shed(
    dash, vbot, vloop, vlimiter,
):
    """The chat's rolling hour is at the bubble's resume mark. A refresh
    with no status change is withheld even after its interval, because it
    would spend the bubble's share. A status change still goes out.

    Mutation: drop the hour check and the routine refresh goes out."""
    share = vlimiter.hourly_usage(CHAT)["ornament_budget"]
    used = int(config.FLOOD_HOURLY_TYPING_RESUME_BELOW * share) + 1

    out: dict = {}

    async def _fill():
        vlimiter.restore([{"chat_id": CHAT,
                           "hourly": [[vloop.wall() - 60.0, used, 0]]}])

    async def _routine():
        # The bucket has refilled since the restore: only the hour can
        # refuse this refresh.
        assert vlimiter.hourly_usage(CHAT)["ornament_used"] >= used
        assert vlimiter.hourly_usage(CHAT)["typing_shed"] is False
        await dash._maybe_update_bot_name("claude-s1")
        out["routine"] = len(_refreshes(vbot))

    async def _status():
        dash.registry.get("claude-s1").status = Status.INTERACTIVE
        await dash._maybe_update_bot_name("claude-s1")

    _run(vloop, [(0.0, lambda: dash._maybe_update_bot_name("claude-s0")),
                 (config.PINNED_REFRESH_INTERVAL + 1.0, _fill),
                 (10.0, _routine),
                 (1.0, _status)])
    assert out["routine"] == 1, "the routine refresh spent the bubble's share"
    stamps = _refreshes(vbot)
    assert len(stamps) == 2, stamps
    assert stamps[1] - stamps[0] > config.PINNED_REFRESH_INTERVAL + 10.0
