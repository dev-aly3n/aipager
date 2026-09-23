"""Rows J, V, W, X (roadmap 8.30 Q6, §4.6): the rolling hour and the
warning regime survive a restart, are written while calls flow, and are
what ``aipager status`` / ``aipager doctor`` show.

The vm3 ban was diagnosable only from a journal. Now one command reads,
per chat: calls in the last hour against the chat's budget (and how old
that figure is), the warning regime's time left, bans in the last seven
days and the rate ceiling they imply.

Everything runs against the ISOLATED state file (``tests/conftest.py``'s
``_isolate_home_paths``), never the operator's ``~/.claude/``.
"""

from __future__ import annotations

import argparse
import json
import types
from pathlib import Path

import pytest

import aipager.bot.rich_message as rm
from aipager import config, doctor, status
from aipager.bot import flood_state
from aipager.bot.flood_budget import BudgetRateLimiter

CHAT = 256113222
MIN = 60.0


async def _ok():
    return "sent"


def _fill(limiter, run_async, n: int) -> None:
    async def _go():
        for _ in range(n):
            await limiter.process_request(
                callback=_ok, args=(), kwargs={}, endpoint="sendMessage",
                data={"chat_id": CHAT}, rate_limit_args=None)
    run_async(_go())


def _fresh(flood_clock) -> BudgetRateLimiter:
    lim = BudgetRateLimiter(clock=flood_clock, sleep=flood_clock.sleep,
                            signal_path=config.FLOOD_BACKOFF_FILE)
    rm.set_rate_limiter(lim)
    return lim


# ── J ────────────────────────────────────────────────────────────────────────

def test_j_a_restart_mid_regime_keeps_the_hour_the_warning_and_the_bans(
    flood_clock, run_async,
):
    """Row J (Q6). Serialise → a FRESH limiter → restore, with the wall
    clock ten minutes on: ``warned_until``, the ban stamps and the hour's
    buckets survive; ``hourly_used`` is the pre-restart figure minus the
    calls that aged out in those ten minutes; and the typing latch, set at
    850 and inside its hysteresis band (648–810) at 700, is still set.

    Mutation: leave the hour, the warning or the latch out of ``restore``
    and the new process hands the chat a fresh hour, forgets the warning,
    or re-lights the bubble at 700.
    """
    old_ban = flood_clock.wall - 8 * 86400.0          # history, outside the week
    first = _fresh(flood_clock)
    first.restore([{"chat_id": CHAT, "ban_stamps": [old_ban]}])
    _fill(first, run_async, 150)                      # t≈0–5 min: will age out
    flood_clock.advance(35 * MIN - (flood_clock.now - 1_000_000.0))
    _fill(first, run_async, 700)                      # t≈35–58 min: will not
    flood_clock.advance(60 * MIN - (flood_clock.now - 1_000_000.0))
    first.note_retry_after(CHAT, 5)                   # a 429: the regime
    before = first.hourly_usage(CHAT)
    assert before["used"] == 850 and before["typing_shed"] is True
    warned_left = first.warning_remaining(CHAT)
    assert flood_state.save_if_dirty(force=True)
    first.reset()

    flood_clock.advance(10 * MIN)
    second = _fresh(flood_clock)
    assert flood_state.load() is True
    after = second.hourly_usage(CHAT)
    assert after["used"] == 700, after                # 850 minus the 150 aged out
    assert after["typing_shed"] is True, "the latch was lost in the band"
    assert second.warning_remaining(CHAT) == pytest.approx(warned_left - 10 * MIN)
    snap = second.snapshot()["chats"][0]
    assert second.serialise()[0]["ban_stamps"] == [old_ban]
    assert snap["hourly_budget"] == config.FLOOD_HOURLY_MAX


def test_j_the_totals_beside_the_hour_are_not_restored(flood_clock, run_async):
    """``hourly_used`` / ``hourly_budget`` in the file are for the status
    reader; the window is rebuilt from the buckets alone, so a file whose
    total disagrees with its buckets cannot move the count."""
    lim = _fresh(flood_clock)
    lim.restore([{"chat_id": CHAT, "hourly_used": 999, "hourly_budget": 5,
                  "hourly": [[flood_clock.wall - 30.0, 0, 3]]}])
    assert lim.hourly_usage(CHAT)["used"] == 3
    assert lim.hourly_usage(CHAT)["budget"] == config.FLOOD_HOURLY_MAX


# ── X: the file is untrusted input ───────────────────────────────────────────

@pytest.mark.parametrize("hourly,expect", [
    ("not a list", 0),
    ([[1, 2]], 0),                                   # wrong shape
    ([["x", 1, 1]], 0),                              # non-numeric
    ([[float("nan"), 5, 5]], 0),                     # non-finite
    ([["WALL", float("inf"), 5]], 0),                # non-finite count
    ([["WALL", -5, 3]], 3),                          # negative -> 0
    ([["WALL", 1e12, 0]], 2 * config.FLOOD_HOURLY_MAX),  # absurd -> clamped
    ([["FUTURE", 4, 0]], 4),                         # future -> now, kept
    ([["OLD", 9, 9]], 0),                            # too old -> dropped
])
def test_x_a_malformed_hour_is_ignored_or_clamped(flood_clock, hourly, expect):
    """Row X. Every shape a hand-edit, a truncated write or a clock jump
    can leave in ``hourly`` is skipped or clamped: never raised on, never
    negative, never a count large enough to mean "unlimited", and a future
    bucket is kept (dropping it would forget calls — the unsafe way)."""
    lim = _fresh(flood_clock)
    marks = {"WALL": flood_clock.wall - 30.0, "FUTURE": flood_clock.wall + 1e9,
             "OLD": flood_clock.wall - 2 * 3600.0}
    if isinstance(hourly, list):
        hourly = [[marks.get(v, v) if i == 0 else v for i, v in enumerate(row)]
                  for row in hourly]
    assert lim.restore([{"chat_id": CHAT, "hourly": hourly}]) == 1
    assert lim.hourly_usage(CHAT)["used"] == expect


@pytest.mark.parametrize("value", [
    float("nan"), float("inf"), -5.0, "soon", None, True, [1],
])
def test_x_a_malformed_warned_until_is_ignored(flood_clock, value):
    lim = _fresh(flood_clock)
    assert lim.restore([{"chat_id": CHAT, "warned_until": value}]) == 1
    assert lim.warning_remaining(CHAT) == 0.0


def test_x_a_warned_until_years_ahead_is_clamped_to_one_regime(flood_clock):
    """A file written before a clock jump must not pin a chat warned for a
    year: clamped to one full regime from now. Mutation: trust it."""
    lim = _fresh(flood_clock)
    lim.restore([{"chat_id": CHAT, "warned_until": flood_clock.wall + 3e7}])
    assert lim.warning_remaining(CHAT) == pytest.approx(
        config.FLOOD_WARNING_HOURS * 3600.0)


@pytest.mark.parametrize("latch", ["yes", 1, None, [True]])
def test_x_a_latch_that_is_not_a_bool_is_ignored(flood_clock, latch):
    """``hourly_minimal`` / ``typing_shed`` restore only a real bool: an
    empty hour with a junk "on" latch must not put a healthy chat in
    minimal mode, nor a junk "off" keep a spent one out of it."""
    lim = _fresh(flood_clock)
    lim.restore([{"chat_id": CHAT, "hourly_minimal": latch, "typing_shed": latch}])
    usage = lim.hourly_usage(CHAT)
    assert (usage["minimal"], usage["typing_shed"]) == (False, False)


# ── W: written while calls flow ──────────────────────────────────────────────

@pytest.fixture
def state_clock(flood_clock, monkeypatch):
    """``flood_state``'s OWN ``time`` on the shared clock (its write floor
    is monotonic, its ``written_at`` wall)."""
    monkeypatch.setattr(flood_state, "time", types.SimpleNamespace(
        monotonic=lambda: flood_clock.now, time=lambda: flood_clock.wall))
    return flood_clock


def test_w_calls_flowing_rewrite_the_file_once_a_minute(
    state_clock, run_async,
):
    """Row W (Q6). One call every 2 s and the session monitor's 2 s tick,
    for ten minutes, with nothing material changing: the file is written
    at most once per ``FLOOD_STATE_VOLUME_REFRESH_SECONDS`` and at least
    once per 62 s. A quiet chat writes nothing; shutdown forces a write.

    Mutation: drop ``mark_volume`` from the call path (or the volume
    branch of ``save_if_dirty``) and the file is written once, at best.
    """
    lim = _fresh(state_clock)
    _fill(lim, run_async, 1)
    flood_state.save_if_dirty()
    writes: list[float] = []
    for _ in range(300):                             # 10 min of 2 s ticks
        state_clock.advance(2.0)
        _fill(lim, run_async, 1)
        if flood_state.save_if_dirty():
            writes.append(state_clock.now)
    gaps = [b - a for a, b in zip(writes, writes[1:])]
    assert len(writes) >= 9, writes
    assert min(gaps) >= config.FLOOD_STATE_VOLUME_REFRESH_SECONDS, gaps
    assert max(gaps) <= config.FLOOD_STATE_VOLUME_REFRESH_SECONDS + 2.0, gaps

    for _ in range(40):                              # quiet: nothing to write
        state_clock.advance(2.0)
        assert flood_state.save_if_dirty() is False
    assert flood_state.save_if_dirty(force=True) is True   # shutdown


# ── V: status and doctor ─────────────────────────────────────────────────────

def _busy_warned_chat(flood_clock, run_async) -> None:
    """A chat with 37 calls this hour, a 429 twenty minutes ago and one ban
    three days ago, written to the durable file."""
    lim = _fresh(flood_clock)
    lim.restore([{"chat_id": CHAT,
                  "ban_stamps": [flood_clock.wall - 3 * 86400.0]}])
    _fill(lim, run_async, 36)
    lim.note_retry_after(CHAT, 5)
    flood_clock.advance(20 * MIN)
    _fill(lim, run_async, 1)
    assert flood_state.save_if_dirty(force=True)


def test_v_status_reads_the_hour_the_regime_the_bans_and_the_ceiling(
    state_clock, run_async, monkeypatch,
):
    """Row V (§4.6): per chat, calls in the last hour / the hourly budget
    with the file's age, the warning regime's time left, bans in 7 days
    and the effective ceiling. On 0.7.13 none of these exist.

    ``status`` is another process with its own wall clock, so it is read
    at the same virtual instant the file was written.
    """
    _busy_warned_chat(state_clock, run_async)
    monkeypatch.setattr(status, "time", types.SimpleNamespace(
        time=lambda: state_clock.wall + 42.0))
    (row,) = status.read_flood_chats()
    assert row["hourly_used"] == 37
    assert row["hourly_budget"] == config.FLOOD_HOURLY_MAX // 2   # one ban
    assert row["hourly_age"] == pytest.approx(42.0)
    assert row["warning_remaining"] == pytest.approx(
        config.FLOOD_WARNING_HOURS * 3600.0 - 20 * MIN - 42.0)
    assert row["bans_7d"] == 1
    assert row["ceiling"] == pytest.approx(0.5)

    (line,) = status.flood_chat_lines([row])
    assert "(ceiling 0.50/s)" in line
    assert "37/600 calls in the last hour (as of 42s ago)" in line
    assert "warning regime, 5h 39m left" in line
    assert "(1 ban(s) in the last 7 days)" in line


def test_v_status_json_carries_the_new_fields(
    state_clock, run_async, monkeypatch, capsys,
):
    """``aipager status --json``: the same row keys, and strict JSON —
    nothing non-finite in it."""
    _busy_warned_chat(state_clock, run_async)
    monkeypatch.setattr(status, "BOT_TOKEN", "tok")
    monkeypatch.setattr(status, "CHAT_ID", "5")
    monkeypatch.setattr(status, "_daemon_alive", lambda: True)
    monkeypatch.setattr(status, "_gather_sessions", lambda: ([], set()))
    status.cmd_status(argparse.Namespace(as_json=True))
    raw = capsys.readouterr().out
    out = json.loads(raw, parse_constant=lambda c: pytest.fail(c))
    (row,) = out["flood_chats"]
    for key in ("hourly_used", "hourly_budget", "hourly_age",
                "warning_remaining", "bans_7d", "ceiling"):
        assert key in row, key


def test_v_doctor_shows_the_same_line(state_clock, run_async, monkeypatch,
                                      tmp_path):
    """``aipager doctor`` reuses the status reader and lines. The daemon
    probe is satisfied WITHOUT a socket: the path is a plain file and
    ``doctor``'s own ``socket`` reference is a stand-in whose datagram
    "arrives" — no socket is created anywhere."""
    _busy_warned_chat(state_clock, run_async)
    monkeypatch.setattr(status, "time", types.SimpleNamespace(
        time=lambda: state_clock.wall + 42.0))
    fake_sock_path = Path(tmp_path) / "aipager.sock"
    fake_sock_path.write_text("")
    monkeypatch.setattr("aipager.config.SOCKET_PATH", str(fake_sock_path))

    class _Sock:
        def settimeout(self, _t):
            pass

        def sendto(self, _data, _addr):
            return 0

        def close(self):
            pass

    monkeypatch.setattr(doctor, "socket", types.SimpleNamespace(
        socket=lambda *a, **k: _Sock(), AF_UNIX=1, SOCK_DGRAM=2))
    result = doctor.check_daemon()
    expected = status.flood_chat_lines(status.read_flood_chats())
    assert expected and all(line in result.detail for line in expected)
    assert any("calls in the last hour" in d for d in result.detail)


def test_v_status_sums_the_hour_against_its_own_clock(monkeypatch):
    """The file is up to a minute old; ``status`` counts the buckets that
    are inside the hour ending NOW, by its own clock — a bucket that has
    rolled out since the write is not counted. Mutation: sum every bucket
    in the file and the rolled-out one is reported as this hour's."""
    now = 1_900_000_000.0
    path = Path(config.FLOOD_STATE_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "written_at": now - 50.0, "chats": [
        {"chat_id": CHAT, "rate": 0.5,
         "hourly": [[now - 70 * MIN, 4, 3], [now - 30 * MIN, 2, 3],
                    ["junk", 1, 1], [now - 10.0, -4, 1]]},
    ]}))
    monkeypatch.setattr(status, "time", types.SimpleNamespace(time=lambda: now))
    (row,) = status.read_flood_chats()
    assert row["hourly_used"] == 6
    assert row["hourly_age"] == pytest.approx(50.0)
