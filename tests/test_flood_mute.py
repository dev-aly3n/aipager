"""The flood-ban mute (roadmap 8.17): the registry in bot/flood.py, the PTB
give-up branch that arms it, the startup notice that respects it, the
builder that hands ONE limiter to both send paths, and the CLI that shows
it.

Why: a 429 with an 8-hour ``retry_after`` used to be treated as a 30-second
one — clamp, sleep, retry, then a plain-text fallback — three fresh
violations per message, each extending the ban, while the bot looked dead
for most of a day. Every guard here is mutation-verified: remove it and the
named test fails.

No test here reaches Telegram: ``_send_with_retry`` gets a hand-rolled bot
double, the limiter is a real ``AIORateLimiter`` that is never driven, and
the signal file lives under ``tmp_path`` (conftest ``_isolate_flood_mute``).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import logging
import socket
import time
import types
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from telegram.error import RetryAfter

import aipager.bot.rich_message as rm
from aipager import config, status
from aipager.bot import flood
from aipager.bot.flood import MUTE, FloodMuted
from aipager.bot.transport import _send_with_retry
from aipager.scope import Member, Scope


# ── fixtures / doubles ───────────────────────────────────────────────────────

@pytest.fixture
def clock(monkeypatch):
    """Fake monotonic + wall clocks bound to flood.py's OWN ``time``
    reference — never the global module, which asyncio's loop reads."""
    state = {"mono": 10_000.0, "wall": 1_800_000_000.0}
    fake = types.SimpleNamespace(
        monotonic=lambda: state["mono"], time=lambda: state["wall"],
    )
    monkeypatch.setattr(flood, "time", fake)

    def advance(seconds: float) -> None:
        state["mono"] += seconds
        state["wall"] += seconds

    advance.state = state  # type: ignore[attr-defined]
    return advance


class _Bot:
    """``_send_with_retry`` double: scripted send outcomes, counted."""

    def __init__(self, outcomes):
        self._outcomes = list(outcomes)
        self.sends: list[int] = []
        self.reactions: list[str] = []

    async def send_message(self, chat_id, text, **kw):
        self.sends.append(chat_id)
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def set_message_reaction(self, chat_id, message_id, emoji):
        self.reactions.append(emoji)


def _scope(chat_id, label):
    return Scope(chat_id=chat_id, kind="dm" if chat_id > 0 else "group",
                 label=label,
                 members=(Member(id=abs(chat_id), label="u", role="user"),))


def _ns(**kw):
    return argparse.Namespace(**kw)


def _hhmm(wall: float) -> str:
    return _dt.datetime.fromtimestamp(wall).strftime("%H:%M")


def _signal_path() -> Path:
    return Path(config.FLOOD_MUTE_FILE)


# ── the registry ─────────────────────────────────────────────────────────────

def test_mute_holds_for_retry_after_then_lifts_on_the_clock(clock):
    MUTE.mute(-100, 28911, source="sendRichMessage")
    assert MUTE.is_muted(-100)
    assert MUTE.remaining(-100) == pytest.approx(28911)
    assert not MUTE.is_muted(-200), "another chat is never muted by proxy"
    clock(28910)
    assert MUTE.is_muted(-100)
    clock(2)
    assert not MUTE.is_muted(-100)
    assert MUTE.remaining(-100) == 0.0


def test_int_and_str_chat_ids_land_on_one_entry(clock):
    """The legacy path hands over ``config.CHAT_ID`` as a str, the rich path
    an int — both must hit the same mute."""
    MUTE.mute("256113222", 600)
    assert MUTE.is_muted(256113222)
    assert MUTE.is_muted("256113222")


def test_one_warning_when_a_mute_starts_one_info_when_it_lifts(clock, caplog):
    with caplog.at_level(logging.DEBUG, logger="aipager.bot.flood"):
        MUTE.mute(-100, 28911, source="editMessageText")
        for _ in range(5):
            MUTE.is_muted(-100)
        clock(30_000)
        for _ in range(3):
            MUTE.is_muted(-100)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    infos = [r for r in caplog.records if r.levelno == logging.INFO]
    assert len(warnings) == 1, [r.getMessage() for r in warnings]
    msg = warnings[0].getMessage()
    assert "-100" in msg and "28911" in msg and "editMessageText" in msg
    assert _hhmm(clock.state["wall"] - 30_000 + 28911) in msg, \
        "the warning must state the wall-clock time the mute lifts"
    assert len(infos) == 1, [r.getMessage() for r in infos]
    assert "lifted" in infos[0].getMessage() and "-100" in infos[0].getMessage()
    audible = [r for r in caplog.records if r.levelno >= logging.INFO]
    assert len(audible) == 2, ("never a line per skipped send",
                               [r.getMessage() for r in audible])


def test_extending_an_active_mute_never_shortens_it_or_warns_again(clock, caplog):
    with caplog.at_level(logging.WARNING, logger="aipager.bot.flood"):
        MUTE.mute(-100, 100)
        MUTE.mute(-100, 200)   # a later, longer ban extends
        MUTE.mute(-100, 50)    # a shorter one is ignored
    assert MUTE.remaining(-100) == pytest.approx(200)
    assert sum(1 for r in caplog.records if r.levelno == logging.WARNING) == 1


def test_an_unresolvable_chat_id_is_never_muted_and_never_raises(clock):
    """An unscoped session on an unconfigured install resolves its chat to
    ``""`` (``config.CHAT_ID`` unset); the mute must stay out of the way —
    no crash, no spurious "muted", zero remaining — exactly as before."""
    MUTE.mute(-100, 28911)
    for unresolved in ("", None, "not-a-chat"):
        assert not MUTE.is_muted(unresolved)
        assert MUTE.remaining(unresolved) == 0.0
        MUTE.check(unresolved)  # returns, never raises
    assert MUTE.is_muted(-100), "the real mute is untouched by the probes"


def test_check_raises_flood_muted_carrying_the_remaining_seconds(clock):
    MUTE.mute(7, 1000)
    clock(400)
    with pytest.raises(FloodMuted) as ei:
        MUTE.check(7)
    assert ei.value.retry_after == pytest.approx(600)
    assert ei.value.chat_id == 7
    MUTE.check(8)  # not muted → returns


def test_clear_forgets_every_mute(clock):
    MUTE.mute(1, 100)
    MUTE.mute(2, 100)
    MUTE.clear()
    assert not MUTE.is_muted(1) and not MUTE.is_muted(2)
    assert MUTE.active() == []


# ── the status signal file ───────────────────────────────────────────────────

def test_mute_writes_the_signal_file_and_lift_removes_it(clock):
    path = _signal_path()
    assert not path.exists()
    MUTE.mute(-100, 28911)
    data = json.loads(path.read_text())
    assert data == {"muted": [{"chat_id": -100,
                               "until": clock.state["wall"] + 28911,
                               "retry_after": 28911.0}]}
    clock(30_000)
    MUTE.is_muted(-100)
    assert not path.exists(), "a lifted mute must not linger for `aipager status`"


def test_clear_unlinks_the_signal_file(clock):
    MUTE.mute(-100, 28911)
    assert _signal_path().exists()
    MUTE.clear()
    assert not _signal_path().exists()


def test_signal_file_write_failure_never_reaches_the_send_path(clock, monkeypatch, tmp_path):
    monkeypatch.setattr("aipager.config.FLOOD_MUTE_FILE",
                        str(tmp_path / "no-such-dir" / "aipager-flood-mute.json"))
    MUTE.mute(-100, 28911)  # must not raise
    assert MUTE.is_muted(-100)


# ── transport: the PTB give-up branch arms it, every later send skips ────────

def test_send_with_retry_give_up_arms_the_mute_so_the_next_send_makes_no_attempt(
    run_async,
):
    """Mutation: drop ``MUTE.mute(...)`` from the give-up branch and the
    second call attempts a send — two sends observed instead of one."""
    bot = _Bot([RetryAfter(28911), "MSG"])
    with pytest.raises(RetryAfter):
        run_async(_send_with_retry(bot, chat_id=7, text="first"))
    assert bot.sends == [7]
    assert MUTE.is_muted(7)
    with pytest.raises(FloodMuted):
        run_async(_send_with_retry(bot, chat_id=7, text="second"))
    assert bot.sends == [7], "the second message went into the ban"
    assert bot.reactions == [], "no reply target → no reaction (unchanged)"


def test_send_with_retry_skips_a_muted_chat_without_touching_the_bot(run_async):
    MUTE.mute(7, 1000)
    bot = _Bot(["MSG"])
    with pytest.raises(FloodMuted) as ei:
        run_async(_send_with_retry(bot, chat_id=7, text="hi",
                                   reply_to_message_id=3))
    assert bot.sends == [] and bot.reactions == []
    assert 0 < ei.value.retry_after <= 1000


def test_send_with_retry_muted_chat_does_not_block_another(run_async):
    MUTE.mute(7, 1000)
    bot = _Bot(["MSG"])
    assert run_async(_send_with_retry(bot, chat_id=8, text="hi")) == "MSG"
    assert bot.sends == [8]


class _RecordingBot(_Bot):
    """``_Bot`` that records EVERY Bot API method reached, not just the two
    it knows about. Any attribute is a coroutine that appends its own name,
    so a call this test never thought of still shows up in ``.calls``."""

    def __init__(self, outcomes):
        super().__init__(outcomes)
        self.calls: list[str] = []

    async def send_message(self, chat_id, text, **kw):
        self.calls.append("sendMessage")
        return await super().send_message(chat_id, text, **kw)

    def __getattr__(self, name):
        async def _record(*a, **kw):
            self.calls.append(name)
        return _record


def test_send_with_retry_makes_no_call_of_any_kind_after_arming_the_mute(
    run_async,
):
    """8.26 D-1, replacing ``…_keys_the_flood_reaction_when_giving_up``.

    R1 is literally zero-exception for the mute. Until 0.7.12 this branch
    armed the mute and then fired a 🚨 ``setMessageReaction`` INTO the chat
    it had just banned, on the theory that reactions ride a separate bucket
    and "a dropped answer still gets its 🚨". Measured on 2026-09-15: a
    request into an ACTIVE ban is what escalated retry_after
    1283 -> 312 -> 34212 (9.5 h). The separate bucket governs PACING, not
    bans, and since 8.29 the answer is held and delivered late, so there is
    no drop left to signal.

    Mutation: re-add any call after ``MUTE.mute(...)`` — the 🚨 or anything
    else — and ``calls`` grows past the one send that earned the ban.
    """
    bot = _RecordingBot([RetryAfter(28911)])
    with pytest.raises(RetryAfter):
        run_async(_send_with_retry(bot, chat_id=7, text="hi",
                                   reply_to_message_id=3))
    assert bot.calls == ["sendMessage"], "something was sent into the ban"
    assert bot.reactions == [], "the 🚨 is gone (D-1)"
    assert MUTE.is_muted(7), "the mute itself must still be armed"


# ── lifecycle: startup notice, builder handoff, clear on start/stop ──────────

def test_startup_notice_skips_a_muted_chat_and_still_reaches_the_others(
    mk_bot, run_async, caplog,
):
    """R4: no startup send into a ban."""
    bot = mk_bot(scopes=[_scope(100, "ana"), _scope(-200, "team")])
    bot._app.bot.send_message = AsyncMock(return_value="MSG")
    MUTE.mute(-200, 5000)
    with caplog.at_level(logging.INFO, logger="aipager.bot.lifecycle"):
        run_async(bot.send_startup_notice("claude is not authenticated"))
    chats = [c.args[0] for c in bot._app.bot.send_message.await_args_list]
    assert chats == [100]
    assert any("skipped" in r.getMessage() and "-200" in r.getMessage()
               for r in caplog.records)
    assert not any(r.exc_info for r in caplog.records), "a mute is not an error"


def test_builder_hands_the_same_limiter_instance_to_rich_message(mk_bot, monkeypatch):
    """R1: one budget. Mutation: drop ``set_rate_limiter(limiter)`` and the
    rich path is left with None — unpaced, the exact bug."""
    from aipager.bot import lifecycle as lc
    from aipager.bot.flood_budget import BudgetRateLimiter

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
    assert rm._rate_limiter is seen["limiter"], \
        "rich_message must pace itself with PTB's limiter, not a second bucket"


def test_builder_limits_are_the_named_config_constants(mk_bot, monkeypatch):
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
    assert limiter._overall.rate == (config.TELEGRAM_OVERALL_MAX_RATE
                                     / config.TELEGRAM_OVERALL_TIME_PERIOD)
    assert limiter._chat_max_rate == config.TELEGRAM_PRIVATE_MAX_RATE
    assert limiter._chat_burst == config.TELEGRAM_CHAT_BURST
    assert limiter._group_max_calls == config.TELEGRAM_GROUP_MAX_CALLS
    assert limiter._group_window == config.TELEGRAM_GROUP_WINDOW
    # `max_retries` went with python-telegram-bot's retry loop (8.21): the
    # limiter defers a small 429 and retries it exactly once itself, and a
    # big one is a ban that surfaces to the sender untouched.


def test_start_forgets_a_previous_daemons_mute_before_anything_else(mk_bot, run_async):
    """R5: a restart clears the mute — including the signal file a dead
    daemon left for `aipager status`. Pinned by aborting start() right after
    its first statement."""
    bot = mk_bot()
    MUTE.mute(-100, 28911)
    assert _signal_path().exists()

    def _abort():
        raise RuntimeError("stop here — nothing past MUTE.clear() runs")

    bot._make_builder = _abort
    with pytest.raises(RuntimeError, match="stop here"):
        run_async(bot.start())
    assert not MUTE.is_muted(-100)
    assert not _signal_path().exists()


def test_stop_takes_the_mute_and_its_signal_file_down_with_the_daemon(mk_bot, run_async):
    bot = mk_bot()
    bot._app.updater.stop = AsyncMock()
    bot._app.stop = AsyncMock()
    bot._app.shutdown = AsyncMock()
    MUTE.mute(-100, 28911)
    run_async(bot.stop())
    assert not MUTE.is_muted(-100)
    assert not _signal_path().exists()


# ── `aipager status` / `aipager doctor` ──────────────────────────────────────

def _write_signal(until: float, chat_id=-100) -> None:
    _signal_path().write_text(json.dumps(
        {"muted": [{"chat_id": chat_id, "until": until, "retry_after": 1}]}))


def test_read_flood_mutes_returns_active_entries_only():
    assert status.read_flood_mutes() == []           # no file
    _write_signal(time.time() - 5)
    assert status.read_flood_mutes() == []           # lapsed
    _signal_path().write_text("{not json")
    assert status.read_flood_mutes() == []           # garbage
    _signal_path().write_text(json.dumps({"muted": [{"chat_id": 1}]}))
    assert status.read_flood_mutes() == []           # no until
    until = time.time() + 3600
    _write_signal(until)
    assert status.read_flood_mutes() == [{"chat_id": -100, "until": until}]


def test_daemon_mute_is_what_status_reads_back():
    """Writer and reader agree on the file: arm a mute the way the daemon
    does, read it the way the CLI does."""
    MUTE.mute(-100, 3600)
    mutes = status.read_flood_mutes()
    assert [m["chat_id"] for m in mutes] == [-100]
    line = status.flood_mute_lines(mutes)[0]
    assert line == f"Telegram flood-muted until {_hhmm(mutes[0]['until'])} (chat -100)"


def test_status_plain_render_shows_the_mute_line_and_omits_it_when_none(capsys):
    until = time.time() + 3600
    status._render_plain(True, [], 0.0, [{"chat_id": -100, "until": until}])
    out = capsys.readouterr().out
    assert f"Telegram flood-muted until {_hhmm(until)} (chat -100)" in out
    status._render_plain(True, [], 0.0, [])
    assert "flood-muted" not in capsys.readouterr().out
    status._render_plain(True, [], 0.0)  # the old 3-arg call still works
    assert "flood-muted" not in capsys.readouterr().out


def test_status_rich_render_shows_the_mute_line(capsys):
    until = time.time() + 3600
    status._render_rich(True, [], 0.0, [{"chat_id": -100, "until": until}])
    out = capsys.readouterr().out
    assert "flood-muted until" in out and _hhmm(until) in out and "-100" in out
    status._render_rich(True, [], 0.0)
    assert "flood-muted" not in capsys.readouterr().out


def test_cmd_status_json_carries_flood_muted(monkeypatch, capsys):
    monkeypatch.setattr(status, "BOT_TOKEN", "tok")
    monkeypatch.setattr(status, "CHAT_ID", "5")
    monkeypatch.setattr(status, "_daemon_alive", lambda: True)
    monkeypatch.setattr(status, "_gather_sessions", lambda: ([], set()))
    until = time.time() + 3600
    _write_signal(until)
    assert status.cmd_status(_ns(as_json=True)) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["flood_muted"] == [{"chat_id": -100, "until": until}]
    _signal_path().unlink()
    status.cmd_status(_ns(as_json=True))
    assert json.loads(capsys.readouterr().out)["flood_muted"] == []


def test_cmd_status_text_output_shows_the_mute(monkeypatch, capsys):
    monkeypatch.setattr(status, "BOT_TOKEN", "tok")
    monkeypatch.setattr(status, "CHAT_ID", "5")
    monkeypatch.setattr(status, "_daemon_alive", lambda: True)
    monkeypatch.setattr(status, "_gather_sessions", lambda: ([], set()))
    until = time.time() + 3600
    _write_signal(until)
    status.cmd_status(_ns(as_json=False))
    assert f"flood-muted until {_hhmm(until)}" in capsys.readouterr().out


def test_doctor_daemon_row_warns_while_a_chat_is_muted(monkeypatch, tmp_path):
    from aipager import doctor

    sock_path = tmp_path / "aipager.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    server.bind(str(sock_path))
    try:
        monkeypatch.setattr("aipager.config.SOCKET_PATH", str(sock_path))
        assert doctor.check_daemon().status == doctor.OK
        until = time.time() + 3600
        _write_signal(until)
        r = doctor.check_daemon()
        assert r.status == doctor.WARN
        assert any(f"flood-muted until {_hhmm(until)}" in d for d in r.detail)
        assert any("self-clears" in d for d in r.detail)
    finally:
        server.close()
