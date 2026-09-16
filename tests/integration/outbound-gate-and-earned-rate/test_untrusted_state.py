"""The durable flood state is UNTRUSTED INPUT (8.29 T2).

``~/.claude/aipager-flood-state.json`` is written by a daemon that can be
SIGKILLed mid-write, read by the next one after a reboot, and editable by
anyone who can read the home directory. Every number in it has to be
validated on the way in, and the failure direction has to be
CONSERVATIVE: a corrupt file must degrade to "no state", never to
"unlimited".

The concrete hole these rows close: Python's ``json`` is not strict JSON.
``json.dumps`` writes the bare literal ``NaN`` and ``json.loads`` reads it
back as ``float('nan')``, which compares FALSE against every bound — so a
NaN rate sailed through the range clamp, ``min(capacity, tokens + elapsed
* NaN)`` left the bucket permanently full, ``minimal_mode`` answered
False for ever, and the chat's pacing was silently disabled. Negative and
absurd rates were already clamped; only the non-finite ones slipped
through, and only silently.
"""

from __future__ import annotations

import json
import logging
import math

import pytest

import aipager.bot.rich_message as rm
from aipager import config
from aipager.bot import flood_state
from aipager.bot.flood import MUTE

CHAT = 256113222


def _install(limiter):
    rm.set_rate_limiter(limiter)
    return limiter


def _state_path():
    return config.FLOOD_STATE_FILE


def _write(body: str) -> None:
    _state_path().parent.mkdir(parents=True, exist_ok=True)
    _state_path().write_text(body, encoding="utf-8")


def _document(**chat) -> str:
    entry = {"chat_id": CHAT}
    entry.update(chat)
    return json.dumps({"version": flood_state.SCHEMA_VERSION,
                       "written_at": 0.0, "chats": [entry]})


# ── the rate field, value by value ──────────────────────────────────────────

@pytest.mark.parametrize("rate", [
    float("nan"), float("inf"), float("-inf"),
])
def test_a_non_finite_rate_never_reaches_the_bucket(limiter, rate):
    """The whole point. Whatever the file says, the bucket's rate is a
    finite number the daemon chose.

    Mutation: drop `_finite` from `restore` and a NaN here disables this
    chat's pacing for the life of the process — silently, because every
    comparison against NaN is False and nothing ever reports it.
    """
    _install(limiter)
    limiter.restore([{"chat_id": CHAT, "rate": rate}])

    assert math.isfinite(limiter.earned_rate(CHAT))
    assert limiter.earned_rate(CHAT) == pytest.approx(config.FLOOD_START_RATE)


def test_set_rate_refuses_a_non_finite_value_and_keeps_the_old_one(
    limiter, flood_clock, caplog,
):
    """The guard at the OTHER end of the same hazard (tester-iter1-004
    asked for both). `restore` filters the file, but `set_rate` is the
    only writer of `TokenBucket.rate` and is reachable from `_earn`,
    `note_retry_after` and `note_ban` — all of which compute from values
    that could themselves be poisoned. Refusing here means a NaN can
    never become the bucket's rate by ANY route, and says so once.

    Mutation: drop the `math.isfinite` check in `ChatBudget.set_rate` and
    the chat is left with `rate = NaN`, where every comparison is false
    and the bucket never refuses anything again.
    """
    _install(limiter)
    budget = limiter._budget_for(CHAT)
    before = budget.rate

    with caplog.at_level(logging.WARNING, logger="aipager.bot.flood_budget"):
        budget.set_rate(float("nan"), flood_clock.now)

    assert budget.rate == pytest.approx(before)
    assert budget.chat.rate == pytest.approx(before)
    assert any("non-finite" in r.getMessage() for r in caplog.records)


def test_a_non_finite_rate_leaves_the_bucket_pacing_normally(
    limiter, flood_clock, run_async,
):
    """The behaviour behind the number: a bucket whose rate is NaN never
    refuses anything, because `tokens + elapsed * NaN` is NaN and NaN is
    never less than 1. Asserted through the bucket, not the accessor."""
    _install(limiter)
    limiter.restore([{"chat_id": CHAT, "rate": float("nan")}])
    budget = limiter._budget_for(CHAT)
    budget.chat.take(budget.chat.tokens())       # empty it

    assert budget.chat.time_until(1.0) > 0.0


@pytest.mark.parametrize("rate,expected", [
    (-5.0, config.FLOOD_MIN_RATE),
    (0.0, config.FLOOD_MIN_RATE),
    (999.0, config.TELEGRAM_PRIVATE_MAX_RATE),
])
def test_an_out_of_range_rate_is_clamped_into_the_documented_band(
    limiter, rate, expected,
):
    """Finite but absurd: clamped, not refused — a stored rate is still
    evidence about the chat, just not about a rate nobody publishes."""
    _install(limiter)
    limiter.restore([{"chat_id": CHAT, "rate": rate}])

    assert limiter.earned_rate(CHAT) == pytest.approx(expected)


@pytest.mark.parametrize("field", [
    "rate", "rate_earned_at", "backoff", "last_429_at", "ban_seen_until",
    "muted_until",
])
def test_a_string_in_any_numeric_field_is_defaulted_not_trusted(
    limiter, field, flood_clock,
):
    """A hand-edited file, or one from a future schema. Every numeric
    field defaults independently, and the chat still restores: throwing
    the whole entry away would also throw away `ban_stamps`, which is the
    one thing in the file we must never forget."""
    _install(limiter)
    limiter.restore([{
        "chat_id": CHAT, field: "quickly",
        "ban_stamps": [flood_clock.wall - 60.0],
    }])

    assert math.isfinite(limiter.earned_rate(CHAT))
    row = next(c for c in limiter.snapshot()["chats"] if c["chat_id"] == CHAT)
    assert row["bans_today"] == 1


def test_a_non_finite_ban_stamp_is_dropped_and_the_real_ones_kept(
    limiter, flood_clock,
):
    """`bans_in_last_24h` sums a comparison, and every comparison against
    NaN is False — so a NaN stamp is not merely useless, it is a stamp
    that can never age out either."""
    _install(limiter)
    limiter.restore([{"chat_id": CHAT, "ban_stamps": [
        float("nan"), float("inf"), "yesterday", flood_clock.wall - 60.0]}])

    assert limiter._budget_for(CHAT).ban_stamps == [flood_clock.wall - 60.0]


def test_a_ban_stamp_from_the_future_is_clamped_not_dropped(
    limiter, flood_clock,
):
    """A clock that moved backwards (NTP, a VM snapshot). Dropping the
    stamp would FORGET a penalty, which is the unsafe direction; clamping
    it to now keeps the memory and lets it age out normally."""
    _install(limiter)
    limiter.restore([{"chat_id": CHAT,
                      "ban_stamps": [flood_clock.wall + 999_999.0]}])

    assert limiter._budget_for(CHAT).ban_stamps == [flood_clock.wall]


def test_a_restored_mute_deadline_past_the_cap_is_clamped(
    limiter, flood_clock,
):
    """D-7's clamp, applied to the two deadlines 8.29 added. A file
    written before a clock jump must not freeze a chat's climb for
    years."""
    _install(limiter)
    limiter.restore([{"chat_id": CHAT,
                      "muted_until": flood_clock.wall + 10 * 86400.0,
                      "ban_seen_until": flood_clock.wall + 10 * 86400.0}])
    budget = limiter._budget_for(CHAT)

    assert budget.muted_until <= flood_clock.wall + config.FLOOD_MUTE_MAX_SECONDS
    assert budget.ban_seen_until <= (flood_clock.wall
                                     + config.FLOOD_MUTE_MAX_SECONDS)


def test_a_non_finite_retry_after_never_arms_a_mute(flood_clock):
    """The same input arriving on the WIRE rather than from the file: a
    429 body is JSON too. `now + NaN` is a deadline `is_muted` reads as
    already lifted, so an unchecked one logs a mute LIFTING that never
    started."""
    MUTE.mute(CHAT, float("nan"))

    assert MUTE.is_muted(CHAT) is False
    assert MUTE.remaining(CHAT) == 0.0


def test_a_non_finite_ban_duration_is_not_recorded(limiter, flood_clock):
    """`note_ban(inf)` would set a deadline nothing ever passes, freezing
    this chat's climb for the life of the process."""
    _install(limiter)
    limiter.note_ban(CHAT, float("inf"))

    assert limiter._budget_for(CHAT).ban_seen_until == 0.0
    assert limiter.earned_rate(CHAT) == pytest.approx(config.FLOOD_START_RATE)


# ── the file as a whole ─────────────────────────────────────────────────────

@pytest.mark.parametrize("body", [
    '{"version": 1, "written_at": 0.0, "chats": [{"chat_id": 1, "rate": NaN}]}',
    '{"version": 1, "written_at": 0.0, "chats": [{"chat_id": 1, '
    '"rate": Infinity}]}',
    '{"version": 1, "written_at": 0.0, "chats": [{"chat_id": 1, "rate": 0.5',
    '{"version": 1, "written_at": 0.0, "chats"',
])
def test_a_file_with_a_non_finite_number_or_a_truncated_write_is_discarded(
    limiter, body, caplog,
):
    """T2's headline: a truncated write during a crash, or a number no
    writer of ours produces, is discarded WHOLE and reported ONCE.

    Mutation: leave `json.loads` on its default `parse_constant` and the
    bare `NaN` literal — which `json.dumps` itself writes — is parsed
    into a float that disables the chat's pacing.
    """
    _install(limiter)
    _write(body)
    with caplog.at_level(logging.WARNING, logger="aipager.bot.flood_state"):
        assert flood_state.read() == {}
        assert flood_state.load() is False
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 2          # one per read: `read()` then `load()`
    assert "corrupt" in warnings[0].message


def test_a_missing_file_says_nothing_at_all(limiter, caplog):
    """The control that keeps the WARNING meaningful: a fresh install has
    no state file, and that is not a problem to report."""
    _install(limiter)
    with caplog.at_level(logging.INFO, logger="aipager.bot.flood_state"):
        assert flood_state.read() == {}

    assert caplog.records == []


def test_a_valid_file_still_loads_after_all_that(limiter, flood_clock):
    """The other control: validation that rejects everything would also
    pass every row above."""
    _install(limiter)
    _write(_document(rate=0.25, rate_earned_at=flood_clock.wall,
                     backoff=1.0, ban_stamps=[]))

    assert flood_state.load() is True
    assert limiter.earned_rate(CHAT) == pytest.approx(0.25)


def test_the_writer_never_emits_a_non_finite_number(limiter, flood_clock):
    """The other side of the boundary: whatever happens in memory, the
    file this daemon writes stays strict JSON — `allow_nan=False` turns a
    would-be `NaN` into an unwritten file rather than one that poisons the
    next start."""
    _install(limiter)
    budget = limiter._budget_for(CHAT)
    budget.rate = float("nan")           # bypassing `set_rate`'s own guard
    flood_state.mark_dirty()

    assert flood_state.save_if_dirty(force=True) is False
    assert not _state_path().exists()
