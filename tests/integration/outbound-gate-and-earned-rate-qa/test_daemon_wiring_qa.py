"""rev-iter1-002 — the four production wirings, asserted BEHAVIOURALLY.

The reviewer deleted each of these single lines and the suite stayed
green, which under this project's rule ("every guard gets a test that
fails when the guard is removed") means they were not covered at all:

* ``flood_state.load()`` in ``lifecycle.start()`` — without it a restart
  during a 9.5-hour ban comes back believing the chat is healthy;
* ``flood_state.save_if_dirty(force=True)`` in ``lifecycle.stop()`` —
  without it the ban is never written down (its ORDER against
  ``MUTE.clear()`` is a belt on top of a brace, and measurably not
  observable — see that row's docstring);
* ``suppressed=`` on the session monitor's watchdog call — without it the
  busy-card watchdog fires through a mute (348 restarts in 54 minutes);
* the held-answer dispatch in the monitor's scan — without it an answer
  refused by a mute is held for ever and never delivered.

Every row here asserts what the DAEMON DID — a mute in force, a document
on disk, a send refused, an answer delivered — never that a function was
called. Each was verified by deleting its line and watching this row go
red.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import config
from aipager.bot import flood_state
from aipager.bot.flood import MUTE, FloodMuted
from aipager.bot.held import HELD, HELD_ANSWER_MAX_AGE_SECONDS
from aipager.session_monitor import CARD_STALE_SECONDS, SessionMonitor
from aipager.state import SessionRegistry, Status, TrackedSession

CHAT = 256113222
BAN = 34212.0


async def _noop(*args, **kwargs):
    return None


class _FakeApp:
    """What ``ApplicationBuilder.build()`` hands back, with nothing that
    can reach a network."""

    def __init__(self) -> None:
        self.bot = MagicMock()
        self.bot.set_my_commands = AsyncMock()
        self.bot.get_me = AsyncMock(
            return_value=MagicMock(username="bot", first_name="bot"))
        self.updater = MagicMock()
        self.updater.start_polling = AsyncMock()
        self.updater.stop = AsyncMock()
        self.initialize = AsyncMock()
        self.start = AsyncMock()
        self.stop = AsyncMock()
        self.shutdown = AsyncMock()
        self.add_handler = MagicMock()
        self.add_error_handler = MagicMock()
        self.bot_data: dict = {}


class _Builder:
    def __init__(self, app):
        self._app = app

    def __getattr__(self, name):
        def _chain(*args, **kwargs):
            return self
        return _chain

    def build(self):
        return self._app


def _startable(bot, monkeypatch):
    """``bot.start()`` with the real body and a fake Application.

    ``CHAT_ID`` is re-pinned on ``lifecycle`` itself, not on ``config``:
    the module does ``from aipager.config import CHAT_ID``, so the value
    is bound into its namespace at import and conftest's
    ``_pin_single_chat_config`` (which patches ``aipager.config``) never
    reaches it. Under CI parity (``CLAUDE_TG_CHAT_ID=``) the module
    constant is ``""`` and ``start()`` dies in ``int("")`` — an
    environment difference, not the behaviour these rows are about.
    Measured: without this the five ``start()`` rows pass on the
    developer's box and fail on a bare runner.
    """
    app = _FakeApp()
    monkeypatch.setattr("aipager.bot.lifecycle.CHAT_ID", config.CHAT_ID)
    monkeypatch.setattr(bot, "_make_builder", lambda: _Builder(app))
    return bot


def _stoppable(bot):
    app = _FakeApp()
    bot._app = app
    return bot


def _state_path() -> Path:
    return Path(config.FLOOD_STATE_FILE)


def _write_state(qa_clock, *, muted_for: float = BAN, rate: float | None = None):
    """A document a previous daemon would have written mid-ban."""
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "version": flood_state.SCHEMA_VERSION,
        "written_at": qa_clock.wall,
        "chats": [{
            "chat_id": CHAT,
            "rate": config.FLOOD_MIN_RATE if rate is None else rate,
            "rate_earned_at": qa_clock.wall,
            "backoff": 1.0,
            "last_429_at": None,
            "ban_stamps": [qa_clock.wall],
            "muted_until": qa_clock.wall + muted_for,
            "mute_retry_after": muted_for,
        }],
    }))


# ===== wiring 1 — ``flood_state.load()`` on start =======================

def test_a_daemon_that_starts_mid_ban_comes_back_muted(
    mk_bot, run_async, limiter, qa_clock, monkeypatch,
):
    """The behaviour, not the call: the process that comes back has only
    the file, and it must come back inside the ban."""
    _write_state(qa_clock)
    MUTE.clear()
    bot = _startable(mk_bot(), monkeypatch)
    run_async(bot.start())
    assert MUTE.is_muted(CHAT)


def test_a_daemon_that_starts_mid_ban_refuses_the_first_send(
    mk_bot, run_async, limiter, qa_clock, monkeypatch, gated_bot,
):
    """…and the refusal is real: the startup card edit is the very first
    thing the old code did after clearing the mute."""
    _write_state(qa_clock)
    MUTE.clear()
    bot = _startable(mk_bot(), monkeypatch)
    run_async(bot.start())
    with pytest.raises(FloodMuted):
        run_async(gated_bot.edit_message_text("text", CHAT, 42))
    assert gated_bot.calls == []


def test_a_daemon_that_starts_mid_ban_comes_back_slow(
    mk_bot, run_async, limiter, qa_clock, monkeypatch,
):
    """The rate half of the same line: the file also carries what the
    chat had EARNED, and a restart that forgets it sends at the start
    rate into a chat that just banned us."""
    _write_state(qa_clock)
    MUTE.clear()
    bot = _startable(mk_bot(), monkeypatch)
    run_async(bot.start())
    assert limiter.earned_rate(CHAT) == pytest.approx(config.FLOOD_MIN_RATE)


def test_a_daemon_with_no_state_file_starts_unmuted(
    mk_bot, run_async, limiter, qa_clock, monkeypatch,
):
    """The control: a fresh install starts healthy, so the rows above are
    about the file and not about a mute that was never cleared."""
    if _state_path().exists():
        _state_path().unlink()
    MUTE.clear()
    bot = _startable(mk_bot(), monkeypatch)
    run_async(bot.start())
    assert not MUTE.is_muted(CHAT)


# ===== wiring 2 — ``save_if_dirty(force=True)`` on stop =================

def test_the_daemon_writes_the_ban_down_before_it_exits(
    mk_bot, run_async, limiter, qa_clock,
):
    """A ban that is only in memory is a ban a ``systemctl restart``
    erases."""
    MUTE.mute(CHAT, BAN)
    if _state_path().exists():
        _state_path().unlink()
    run_async(_stoppable(mk_bot()).stop())
    assert _state_path().exists()


def test_what_the_daemon_wrote_on_the_way_out_still_carries_the_ban(
    mk_bot, run_async, limiter, qa_clock,
):
    """The file has to say what the ban WAS, not merely exist.

    Measured limitation, recorded rather than claimed: this row does NOT
    pin the save's ORDER against ``stop()``'s ``MUTE.clear()``. Moving the
    save below the clear leaves all sixteen rows here green, because the
    limiter keeps its own ``muted_until`` for the chat and that field
    survives the registry being emptied. The order is a belt on top of a
    brace; the brace is what is asserted.
    """
    MUTE.mute(CHAT, BAN)
    run_async(_stoppable(mk_bot()).stop())
    document = json.loads(_state_path().read_text())
    assert any(int(chat["chat_id"]) == CHAT and chat.get("muted_until")
               for chat in document["chats"])


def test_the_next_daemon_reads_that_ban_back(
    mk_bot, run_async, limiter, qa_clock, monkeypatch,
):
    """End to end across the restart, which is the only thing the
    operator cares about."""
    MUTE.mute(CHAT, BAN)
    run_async(_stoppable(mk_bot()).stop())
    assert not MUTE.is_muted(CHAT)          # the daemon really did exit
    bot = _startable(mk_bot(), monkeypatch)
    run_async(bot.start())
    assert MUTE.is_muted(CHAT)


# ===== wiring 3 — ``suppressed=`` on the watchdog ======================

def _busy_session(registry, *, stale: float = CARD_STALE_SECONDS + 10):
    now = time.monotonic()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    sess.busy_msg_id = 42
    sess.busy_started_at = now - 600
    sess.last_hook_at = now - 1
    sess.last_tool_edit_at = now - stale
    sess.animate_task = None
    sess.scope_kind = "dm"
    sess.scope_chat_id = CHAT
    registry._sessions["claude-jim"] = sess
    return sess


def _monitor(registry, events, monkeypatch):
    async def _notify(sess, kind, payload=None):
        events.append(kind)

    async def _sessions():
        return ["claude-jim"]

    monkeypatch.setattr("aipager.dtach.inject.list_sessions",
                        lambda: _sessions())
    return SessionMonitor(registry, _notify)


def test_the_watchdog_does_fire_on_a_stale_card(run_async, monkeypatch):
    """The positive control FIRST: without it "no watchdog event" passes
    for a scan that never reached the watchdog at all."""
    registry = SessionRegistry()
    _busy_session(registry)
    events: list[str] = []
    run_async(_monitor(registry, events, monkeypatch)._scan())
    assert "busy_card_watchdog" in events


def test_the_watchdog_takes_no_action_while_the_chat_is_muted(
    run_async, monkeypatch, limiter,
):
    """The wiring: the same stale card in a muted chat produces nothing.
    On 2026-09-15 it produced 348 restarts in 54 minutes."""
    registry = SessionRegistry()
    _busy_session(registry)
    MUTE.mute(CHAT, BAN)
    events: list[str] = []
    run_async(_monitor(registry, events, monkeypatch)._scan())
    assert "busy_card_watchdog" not in events


def test_the_watchdog_takes_no_action_in_minimal_mode(
    run_async, monkeypatch, limiter, qa_clock,
):
    """The other half of ``cards_suppressed``: a chat that is not muted
    but is pacing at the floor has no card to restart either."""
    registry = SessionRegistry()
    _busy_session(registry)
    limiter.restore([{
        "chat_id": CHAT, "rate": config.FLOOD_MIN_RATE,
        "rate_earned_at": qa_clock.wall, "backoff": 1.0,
        "last_429_at": None, "ban_stamps": [],
    }])
    events: list[str] = []
    run_async(_monitor(registry, events, monkeypatch)._scan())
    assert "busy_card_watchdog" not in events


# ===== wiring 4 — the held-answer dispatch =============================

def _held_session(registry):
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    sess.busy_started_at = time.monotonic()
    sess.scope_kind = "dm"
    sess.scope_chat_id = CHAT
    registry._sessions["claude-jim"] = sess
    return sess


def _answer_bot(mk_bot):
    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock(
        return_value=MagicMock(message_id=1))
    bot._app.bot.send_document = AsyncMock()
    bot._app.bot.delete_message = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    return bot


def _hold(text="the answer", session="claude-jim", at=None):
    return HELD.hold(chat_id=CHAT, session=session, label="jim",
                     rich_text=text, plain_text=text, reply_to=None, at=at)


def test_a_held_answer_is_delivered_by_the_monitor_tick(
    mk_bot, run_async, monkeypatch, rich_http, limiter, qa_clock,
):
    """The wiring, end to end: the user's answer arrives without anybody
    typing anything. ``within one 2 s tick`` is the documented promise."""
    registry = SessionRegistry()
    _held_session(registry)
    MUTE.mute(CHAT, 600.0)
    _hold()
    qa_clock.advance(601.0)
    bot = _answer_bot(mk_bot)

    async def _sessions():
        return ["claude-jim"]

    monkeypatch.setattr("aipager.dtach.inject.list_sessions",
                        lambda: _sessions())
    run_async(SessionMonitor(registry, bot.notify)._scan())
    assert HELD.count() == 0


def test_the_answer_the_monitor_delivered_actually_reached_telegram(
    mk_bot, run_async, monkeypatch, rich_http, limiter, qa_clock,
):
    """…and it reached the wire, not just the buffer's bin."""
    registry = SessionRegistry()
    _held_session(registry)
    MUTE.mute(CHAT, 600.0)
    _hold(text="the answer")
    qa_clock.advance(601.0)
    bot = _answer_bot(mk_bot)

    async def _sessions():
        return ["claude-jim"]

    monkeypatch.setattr("aipager.dtach.inject.list_sessions",
                        lambda: _sessions())
    run_async(SessionMonitor(registry, bot.notify)._scan())
    assert any("the answer" in json.dumps(payload)
               for _e, _c, payload in rich_http.requests)


def test_the_monitor_delivers_nothing_while_the_ban_still_holds(
    mk_bot, run_async, monkeypatch, rich_http, limiter, qa_clock,
):
    """The control on the other side: the tick must not try to deliver
    INTO the ban — that is a request into an active ban, the number row N
    counts."""
    registry = SessionRegistry()
    _held_session(registry)
    MUTE.mute(CHAT, BAN)
    _hold()
    bot = _answer_bot(mk_bot)

    async def _sessions():
        return ["claude-jim"]

    monkeypatch.setattr("aipager.dtach.inject.list_sessions",
                        lambda: _sessions())
    run_async(SessionMonitor(registry, bot.notify)._scan())
    assert rich_http.requests == []


def test_an_answer_older_than_the_age_cap_is_not_delivered_as_news(
    mk_bot, run_async, monkeypatch, rich_http, limiter, qa_clock,
):
    """T4's age cap, driven from the tick that actually runs it.

    The assertion is that NOTHING went out, not that the buffer emptied:
    an unmuted chat empties the buffer by DELIVERING, so ``count == 0``
    passes with the sweep deleted (measured — that first draft survived
    the mutation). What the cap is for is the answer from a ban that
    lifted a week ago arriving as if it were news.
    """
    registry = SessionRegistry()
    _held_session(registry)
    _hold(at=time.time() - HELD_ANSWER_MAX_AGE_SECONDS - 60)
    bot = _answer_bot(mk_bot)

    async def _sessions():
        return ["claude-jim"]

    monkeypatch.setattr("aipager.dtach.inject.list_sessions",
                        lambda: _sessions())
    run_async(SessionMonitor(registry, bot.notify)._scan())
    assert rich_http.requests == []


def test_that_sweep_is_never_silent(
    mk_bot, run_async, monkeypatch, rich_http, limiter, qa_clock, caplog,
):
    """"nothing leaves the buffer silently" — the age cap is a LOSS of a
    user's work and has to be reported at WARNING."""
    registry = SessionRegistry()
    _held_session(registry)
    _hold(at=time.time() - HELD_ANSWER_MAX_AGE_SECONDS - 60)
    bot = _answer_bot(mk_bot)

    async def _sessions():
        return ["claude-jim"]

    monkeypatch.setattr("aipager.dtach.inject.list_sessions",
                        lambda: _sessions())
    with caplog.at_level(logging.DEBUG, logger="aipager.bot.held"):
        run_async(SessionMonitor(registry, bot.notify)._scan())
    assert [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_an_answer_inside_the_age_cap_is_not_swept(
    mk_bot, run_async, monkeypatch, rich_http, limiter, qa_clock,
):
    """The just-inside boundary, and the control for the two rows above:
    the cap is never shorter than the longest mute that can exist."""
    registry = SessionRegistry()
    _held_session(registry)
    MUTE.mute(CHAT, BAN)
    _hold(at=time.time() - HELD_ANSWER_MAX_AGE_SECONDS + 60)
    bot = _answer_bot(mk_bot)

    async def _sessions():
        return ["claude-jim"]

    monkeypatch.setattr("aipager.dtach.inject.list_sessions",
                        lambda: _sessions())
    run_async(SessionMonitor(registry, bot.notify)._scan())
    assert HELD.count() == 1
