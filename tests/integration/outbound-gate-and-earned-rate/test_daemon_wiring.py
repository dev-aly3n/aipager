"""The four production wirings, each proved by the behaviour it produces.

Review rev-iter1-002 found these deletable with a green suite: replace
``flood_state.load()`` (`lifecycle.py`), ``flood_state.save_if_dirty(
force=True)`` (`lifecycle.py`), ``suppressed=suppressed`` and the
held-answer dispatch (`session_monitor.py`) with `pass`, and 6998 tests
still passed. A guard nothing fails for is not a guard — CLAUDE.md's rule,
and the reason the persistence work of R5, the watchdog silence of R7 and
the only delivery trigger of R6 were all unguarded at once.

Every row here drives the REAL entry point (``bot.start()``,
``bot.stop()``, ``SessionMonitor._scan()``) and asserts on what the daemon
DID — a mute still in force, a file on disk, a watchdog that took no
action, an event that reached the notifier — never on the fact that a
call happened. Each names the single line whose deletion fails it.
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import aipager.bot.rich_message as rm
from aipager import config
from aipager.bot import flood_state
from aipager.bot.flood import MUTE, FloodMuted
from aipager.bot.held import HELD
from aipager.session_monitor import SessionMonitor
from aipager.state import SessionRegistry, Status, TrackedSession

CHAT = 256113222
BAN = 34212.0


# ── lifecycle.start: `flood_state.load()` ───────────────────────────────────

def _previous_daemons_state(*, until: float, rate: float) -> None:
    """Write the file a daemon that died inside a ban would leave."""
    path = config.FLOOD_STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "version": flood_state.SCHEMA_VERSION,
        "written_at": until - BAN,
        "chats": [{
            "chat_id": CHAT, "rate": rate, "backoff": 1.0, "ban_stamps": [],
            "muted_until": until, "mute_retry_after": BAN,
        }],
    }), encoding="utf-8")


def _start_aborting_after_load(bot, limiter, run_async):
    """Run the real ``bot.start()`` and stop it the statement after
    ``flood_state.load()``.

    The abort is ``add_handler`` raising — the first thing start() does
    after the load — which is the same "pin it by aborting" pattern
    ``test_flood_mute.py::test_start_forgets_a_previous_daemons_mute_before_anything_else``
    uses for the statement before it. Nothing past the load runs, so a
    mute in force afterwards can only have come from the load.
    """
    app = MagicMock()
    app.add_handler.side_effect = RuntimeError("stop here")

    def _make_builder():
        rm.set_rate_limiter(limiter)
        return SimpleNamespace(build=lambda: app)

    bot._make_builder = _make_builder
    with pytest.raises(RuntimeError, match="stop here"):
        run_async(bot.start())


def test_the_daemon_starts_with_a_persisted_ban_still_in_force(
    mk_bot, limiter, gated_bot, run_async, flood_clock,
):
    """D-6 / criterion 15, at the LIFECYCLE level. On 2026-09-15 a daemon
    restarted inside a 9.5-hour ban came back knowing nothing and its own
    startup notice was the first request into that ban.

    Mutation: replace `flood_state.load()` in `lifecycle.start` with
    `pass` and this chat is unmuted at startup, so the send below goes
    out — into an active ban.
    """
    _previous_daemons_state(until=flood_clock.wall + BAN,
                            rate=config.FLOOD_MIN_RATE)
    bot = mk_bot()
    _start_aborting_after_load(bot, limiter, run_async)

    assert MUTE.is_muted(CHAT), "the previous daemon's ban was forgotten"
    with pytest.raises(FloodMuted):
        run_async(gated_bot.send_message(chat_id=CHAT, text="daemon started"))
    assert gated_bot.calls == []


def test_the_daemon_starts_with_the_rate_the_last_one_learned(
    mk_bot, limiter, run_async, flood_clock,
):
    """The other half of the same line: the earned rate is state too, and
    a restart at the ceiling is how the second ban was earned."""
    _previous_daemons_state(until=flood_clock.wall - 1.0,   # the ban lapsed
                            rate=config.FLOOD_MIN_RATE)
    bot = mk_bot()
    _start_aborting_after_load(bot, limiter, run_async)

    assert not MUTE.is_muted(CHAT)
    assert limiter.earned_rate(CHAT) == pytest.approx(config.FLOOD_MIN_RATE)


def test_a_corrupt_state_file_still_lets_the_daemon_start(
    mk_bot, limiter, run_async,
):
    """The `SessionRegistry.load` contract, at the same seam: whatever the
    file holds, start() gets as far as it would have."""
    config.FLOOD_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.FLOOD_STATE_FILE.write_text('{"version": 1, "chats": [{"rate"',
                                       encoding="utf-8")
    bot = mk_bot()
    _start_aborting_after_load(bot, limiter, run_async)   # reaches the abort

    assert not MUTE.is_muted(CHAT)


# ── lifecycle.stop: `flood_state.save_if_dirty(force=True)` ─────────────────

def _stoppable(bot):
    bot._app.updater.stop = AsyncMock()
    bot._app.stop = AsyncMock()
    bot._app.shutdown = AsyncMock()
    return bot


def test_the_daemon_writes_its_ban_to_disk_when_it_stops(
    mk_bot, limiter, run_async, flood_clock,
):
    """D-6's other half. `stop()` is the last chance: there is no next
    monitor tick to debounce onto, which is why it forces.

    Mutation: replace `flood_state.save_if_dirty(force=True)` with `pass`
    and a daemon stopped inside a ban leaves nothing behind — the next one
    starts at full speed into the same ban.
    """
    rm.set_rate_limiter(limiter)
    MUTE.mute(CHAT, BAN)
    _stoppable(mk_bot())
    bot = _stoppable(mk_bot())

    run_async(bot.stop())

    document = json.loads(config.FLOOD_STATE_FILE.read_text(encoding="utf-8"))
    entry = next(c for c in document["chats"] if str(c["chat_id"]) == str(CHAT))
    assert entry["muted_until"] > flood_clock.wall
    assert entry["rate"] == pytest.approx(config.FLOOD_MIN_RATE)


def test_the_state_is_written_before_the_mute_registry_is_cleared(
    mk_bot, limiter, run_async, flood_clock,
):
    """The ORDER inside stop(), which is its own guard: `MUTE.clear()`
    empties the registry `save_if_dirty` reads, so clearing first would
    write an empty document over a live ban — persisting nothing, loudly
    and correctly, which is the worst kind of bug to have.

    Mutation: move the save below `MUTE.clear()` and the document has no
    `muted_until` at all.
    """
    rm.set_rate_limiter(limiter)
    MUTE.mute(CHAT, BAN)
    bot = _stoppable(mk_bot())

    run_async(bot.stop())

    assert not MUTE.is_muted(CHAT), "stop() should still clear the registry"
    document = json.loads(config.FLOOD_STATE_FILE.read_text(encoding="utf-8"))
    assert any(c.get("muted_until") for c in document["chats"])


def test_a_ban_survives_a_real_stop_and_a_real_start(
    mk_bot, limiter, gated_bot, run_async, flood_clock,
):
    """The round trip, through both entry points and nothing else — the
    row D-6 asked for and rev-iter1-002 found missing. Either wiring
    deleted fails it."""
    rm.set_rate_limiter(limiter)
    MUTE.mute(CHAT, BAN)
    run_async(_stoppable(mk_bot()).stop())
    MUTE.clear()                       # a genuinely new process
    assert not MUTE.is_muted(CHAT)

    _start_aborting_after_load(mk_bot(), limiter, run_async)

    assert MUTE.is_muted(CHAT)
    with pytest.raises(FloodMuted):
        run_async(gated_bot.send_message(chat_id=CHAT, text="hello again"))


# ── session_monitor: `suppressed=suppressed` ────────────────────────────────

def _busy_session(now: float, chat=CHAT) -> TrackedSession:
    """A BUSY session with a live card and NO animate task — the shape the
    watchdog restarts."""
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.busy_msg_id = 42
    sess.busy_started_at = now - 120
    sess.last_hook_at = now - 1
    sess.last_tool_edit_at = now - 1
    sess.animate_task = None
    sess.scope_chat_id = chat
    return sess


def _scan(registry, monkeypatch, run_async):
    calls: list[tuple[str, dict]] = []

    async def _notify(sess, event, ctx):
        calls.append((event, dict(ctx)))

    monitor = SessionMonitor(registry, _notify)
    monkeypatch.setattr(
        "aipager.dtach.inject.list_sessions",
        lambda: _coro(list(registry._sessions)),
    )
    run_async(monitor._scan())
    return calls


async def _coro(value):
    return value


def test_the_live_watchdog_takes_no_action_on_a_muted_chat(
    mk_bot, limiter, monkeypatch, run_async, caplog,
):
    """R7 through the MONITOR, not through the pure helper. A muted chat's
    card cannot animate, so restarting its animation achieves nothing
    except two log lines every 20 s — 348 restarts in 54 minutes, measured
    2026-09-15, against six actual HTTP refusals all day.

    Mutation: drop `suppressed=suppressed` from the
    `busy_card_watchdog_action(...)` call and the restart storm is back,
    with this row failing on the notify call it produces.
    """
    import time as _time

    rm.set_rate_limiter(limiter)
    MUTE.mute(CHAT, BAN)
    registry = SessionRegistry()
    registry._sessions["claude-jim"] = _busy_session(_time.monotonic())

    with caplog.at_level(logging.INFO, logger="aipager.session_monitor"):
        calls = _scan(registry, monkeypatch, run_async)

    assert [event for event, _ctx in calls if event == "busy_card_watchdog"] == []
    assert any("suppressed" in r.getMessage() for r in caplog.records)


def test_the_live_watchdog_still_acts_on_a_healthy_chat(
    mk_bot, limiter, monkeypatch, run_async,
):
    """The control. Without it the row above passes for a watchdog that
    never does anything at all."""
    import time as _time

    rm.set_rate_limiter(limiter)
    registry = SessionRegistry()
    registry._sessions["claude-jim"] = _busy_session(_time.monotonic())

    calls = _scan(registry, monkeypatch, run_async)

    assert [ctx["action"] for event, ctx in calls
            if event == "busy_card_watchdog"] == ["restart"]


# ── session_monitor: the held-answer dispatch ───────────────────────────────

def test_the_monitor_dispatches_a_flush_for_a_session_with_a_held_answer(
    mk_bot, limiter, monkeypatch, run_async,
):
    """R6's ONLY delivery trigger. `flush_held_answers` is reachable from
    the `held_answer_flush` event and nothing else, and only `_scan`
    emits it — so with this line deleted a held answer is never delivered
    at all, which is the failure the buffer exists to prevent.

    Mutation: delete the `_has_held_answer(sess)` dispatch and no event
    reaches the notifier.
    """
    import time as _time

    rm.set_rate_limiter(limiter)
    registry = SessionRegistry()
    sess = _busy_session(_time.monotonic())
    sess.animate_task = MagicMock(done=lambda: False)     # no watchdog noise
    registry._sessions["claude-jim"] = sess
    HELD.hold(chat_id=CHAT, session="claude-jim", label="jim",
              rich_text="the answer", plain_text="the answer")

    calls = _scan(registry, monkeypatch, run_async)

    assert [event for event, _ctx in calls] == ["held_answer_flush"]


def test_the_monitor_dispatches_nothing_for_a_session_with_nothing_held(
    mk_bot, limiter, monkeypatch, run_async,
):
    """The control: the guard is cheap and false almost always."""
    import time as _time

    rm.set_rate_limiter(limiter)
    registry = SessionRegistry()
    sess = _busy_session(_time.monotonic())
    sess.animate_task = MagicMock(done=lambda: False)
    registry._sessions["claude-jim"] = sess

    assert _scan(registry, monkeypatch, run_async) == []


# ── session_monitor: the held-answer AGE cap sweep ──────────────────────────

def test_the_monitor_sweep_expires_an_answer_nothing_can_deliver(
    mk_bot, limiter, monkeypatch, run_async, caplog,
):
    """The age cap needs a clock, and the 2 s sweep is it (8.29 T4).

    Mutation: delete `held.HELD.expire()` from `_sweep_flood_backoff` and
    an answer from a ban that lifted a week ago sits in memory until the
    daemon restarts.
    """
    import time as _time

    from aipager.bot.held import HELD_ANSWER_MAX_AGE_SECONDS

    rm.set_rate_limiter(limiter)
    HELD.hold(chat_id=CHAT, session="claude-jim", label="jim",
              rich_text="ancient", plain_text="ancient",
              at=_time.time() - HELD_ANSWER_MAX_AGE_SECONDS - 60)
    registry = SessionRegistry()
    sess = _busy_session(_time.monotonic())
    sess.animate_task = MagicMock(done=lambda: False)
    registry._sessions["claude-jim"] = sess

    with caplog.at_level(logging.WARNING, logger="aipager.bot.held"):
        _scan(registry, monkeypatch, run_async)

    assert HELD.count() == 0
    assert any("expired" in r.getMessage() for r in caplog.records)
