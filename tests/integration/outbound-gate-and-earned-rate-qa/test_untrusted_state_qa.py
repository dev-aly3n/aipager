"""T2 — the durable file is UNTRUSTED INPUT.

"a truncated write during a crash must degrade to conservative defaults,
never to 'unlimited'" (``fix-brief-2.md`` T2).

The iteration-1 defect this directory reported (tester-iter1-004): a
``NaN`` rate survived ``restore()`` verbatim — ``json`` both writes and
reads the bare literal — and ``min(capacity, tokens + elapsed * NaN)``
then left the bucket permanently full. Every NaN comparison is false, so
``minimal_mode`` said "healthy", the status JSON became invalid, and the
chat's pacing was silently disabled: exactly the failure this ship exists
to end, arriving through the file the ship added.

Partitions covered per numeric field: non-finite (NaN, ±inf), negative,
zero, absurd, wrong type. Document-level: a NaN literal, an Infinity
literal, a truncation mid-object, a truncation mid-array, a non-object
document, a wrong version, and a missing file (which is a fresh install,
not a problem to report).
"""

from __future__ import annotations

import json
import logging
import math
import time
from pathlib import Path

import pytest

from aipager import config, status
from aipager.bot import flood_state
from aipager.bot.flood import MUTE

CHAT = 256113222
WINDOW = config.FLOOD_SUCCESS_WINDOW_SECONDS
CEILING = config.TELEGRAM_PRIVATE_MAX_RATE
FLOOR = config.FLOOD_MIN_RATE

NUMERIC_FIELDS = [
    "rate", "rate_earned_at", "backoff", "last_429_at",
    "muted_until", "mute_retry_after",
]
BAD_NUMBERS = [
    float("nan"), float("inf"), float("-inf"),
    -1.0, -1e9, 0.0, 1e9, 1e300,
]


async def _ok():
    return "sent"


def _state_path() -> Path:
    return Path(config.FLOOD_STATE_FILE)


def _entry(**overrides) -> dict:
    """A well-formed entry, earned NOW — a stamp two days in the past
    would let the chat climb to the ceiling before any row asserted
    anything, and every "clamped" row would then pass for free."""
    entry = {
        "chat_id": CHAT, "rate": 0.25, "rate_earned_at": time.time(),
        "backoff": 1.0, "last_429_at": None, "ban_stamps": [],
    }
    entry.update(overrides)
    return entry


def _write(document) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        document if isinstance(document, str) else json.dumps(document))


def _warnings(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.levelno >= logging.WARNING]


# ===== every numeric field, every bad class =============================

@pytest.mark.parametrize("bad", BAD_NUMBERS)
def test_a_bad_rate_never_becomes_a_non_finite_rate(limiter, qa_clock, bad):
    """The headline of T2: whatever the file says, the rate the daemon
    runs on is a real number."""
    limiter.restore([_entry(rate=bad)])
    assert math.isfinite(limiter.earned_rate(CHAT))


@pytest.mark.parametrize("bad", BAD_NUMBERS)
def test_a_bad_rate_never_becomes_an_unlimited_rate(limiter, qa_clock, bad):
    """"never to 'unlimited'". ``1e300`` and ``inf`` are the same failure
    as ``NaN`` with a different shape."""
    limiter.restore([_entry(rate=bad)])
    assert limiter.earned_rate(CHAT) <= CEILING


@pytest.mark.parametrize("bad", BAD_NUMBERS)
def test_a_bad_rate_never_drops_below_the_named_floor(limiter, qa_clock, bad):
    """The conservative default has a bottom too: a chat pinned at 0 or a
    negative rate would never send again, which is an outage."""
    limiter.restore([_entry(rate=bad)])
    assert limiter.earned_rate(CHAT) >= FLOOR


@pytest.mark.parametrize("field", NUMERIC_FIELDS)
def test_a_string_in_a_numeric_field_is_never_trusted(
    limiter, qa_clock, field,
):
    """Wrong-type partition, field by field: a hand-edited file (or half
    a write) puts a string where a float belongs."""
    limiter.restore([_entry(**{field: "not a number"})])
    rate = limiter.earned_rate(CHAT)
    assert math.isfinite(rate) and FLOOR <= rate <= CEILING


@pytest.mark.parametrize("field", NUMERIC_FIELDS)
def test_a_non_finite_value_in_any_numeric_field_raises_nothing(
    limiter, qa_clock, field,
):
    """``restore`` is called inside daemon startup: a raise there is a
    daemon that will not start because of a corrupt cache file."""
    limiter.restore([_entry(**{field: float("nan")})])
    assert math.isfinite(limiter.earned_rate(CHAT))


def test_a_nan_rate_does_not_disable_pacing(limiter, qa_clock, run_async):
    """The BEHAVIOUR the NaN broke, not just its display.

    ``tokens + elapsed * NaN`` is NaN, ``min(capacity, NaN)`` is NaN, and
    every comparison against it is false — so the bucket never emptied and
    the chat sent as fast as the daemon could loop. The proof is that the
    call past the burst still has to WAIT.
    """
    limiter.restore([_entry(rate=float("nan"))])

    async def _drive():
        for _ in range(int(config.TELEGRAM_CHAT_BURST) + 1):
            await limiter.process_request(
                callback=_ok, args=(), kwargs={}, endpoint="sendMessage",
                data={"chat_id": CHAT}, rate_limit_args=None)

    run_async(_drive())
    assert qa_clock.sleeps


def test_a_healthy_file_is_still_trusted(limiter, qa_clock, run_async):
    """The control. Without it every row above passes for a ``restore``
    that throws the whole file away."""
    limiter.restore([_entry(rate=0.25)])
    assert limiter.earned_rate(CHAT) == pytest.approx(0.25)


# ===== the ban history inside an entry ==================================

def test_a_non_finite_ban_stamp_is_not_trusted(limiter, qa_clock):
    """``ban_stamps`` drive both the post-ban regime and ruling 5's
    half-ceiling cap; a NaN stamp there would make every comparison about
    "how long ago" false."""
    limiter.restore([_entry(ban_stamps=[float("nan"), float("inf")])])
    assert math.isfinite(limiter.earned_rate(CHAT))


def test_a_ban_stamp_in_the_future_never_freezes_the_chat_for_ever(
    limiter, qa_clock,
):
    """A clock step or a hand-edited file could stamp a ban in 2038; with
    the stamp trusted the chat would never climb again."""
    limiter.restore([_entry(rate=FLOOR, ban_stamps=[qa_clock.wall + 1e9])])
    qa_clock.advance(25 * 3600)
    assert limiter.earned_rate(CHAT) > FLOOR


def test_a_ban_stamp_that_is_not_a_number_raises_nothing(limiter, qa_clock):
    limiter.restore([_entry(ban_stamps=["yesterday", None, {}])])
    assert math.isfinite(limiter.earned_rate(CHAT))


def test_ban_stamps_that_are_not_a_list_raise_nothing(limiter, qa_clock):
    limiter.restore([_entry(ban_stamps="1789231234.0")])
    assert math.isfinite(limiter.earned_rate(CHAT))


def test_an_entry_that_is_not_a_dict_raises_nothing(limiter, qa_clock):
    limiter.restore(["chat", 17, None])
    assert limiter.earned_rate(CHAT) == pytest.approx(config.FLOOD_START_RATE)


def test_a_chats_array_that_is_not_an_array_raises_nothing(limiter):
    limiter.restore({"chat_id": CHAT})
    assert math.isfinite(limiter.earned_rate(CHAT))


# ===== the same untrusted numbers arriving from a 429 body ==============

@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1.0])
def test_a_non_finite_retry_after_never_becomes_the_rate(limiter, bad):
    """A ``retry_after`` is JSON too — it arrives in a Telegram error body
    and is no more trustworthy than the file."""
    limiter.note_retry_after(CHAT, bad)
    assert math.isfinite(limiter.earned_rate(CHAT))


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1.0])
def test_a_non_finite_ban_length_never_becomes_the_rate(limiter, bad):
    limiter.note_ban(CHAT, bad)
    rate = limiter.earned_rate(CHAT)
    assert math.isfinite(rate) and rate <= CEILING


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), 1e300])
def test_a_non_finite_mute_deadline_is_clamped_not_trusted(qa_clock, bad):
    """``FLOOD_MUTE_MAX_SECONDS`` is the documented sanity clamp; an
    ``inf`` deadline is a chat muted for ever."""
    MUTE.mute(CHAT, bad)
    assert MUTE.remaining(CHAT) <= config.FLOOD_MUTE_MAX_SECONDS


# ===== document-level corruption ========================================

CORRUPT_DOCUMENTS = {
    "nan_literal": '{"version": 1, "written_at": 1.0, "chats": '
                   '[{"chat_id": 256113222, "rate": NaN}]}',
    "infinity_literal": '{"version": 1, "written_at": 1.0, "chats": '
                        '[{"chat_id": 256113222, "rate": Infinity}]}',
    "truncated_mid_object": '{"version": 1, "written_at": 1.0, "chats": '
                            '[{"chat_id": 2561132',
    "truncated_mid_array": '{"version": 1, "written_at": 1.0, "chats": [',
    "empty": "",
    "not_an_object_list": "[1, 2, 3]",
    "not_an_object_string": '"chats"',
    "not_an_object_number": "17",
    "null": "null",
}


@pytest.mark.parametrize("name", sorted(CORRUPT_DOCUMENTS))
def test_a_corrupt_document_is_read_as_no_state(tmp_path, name):
    """"a corrupt or partially-written file discarded … and treated as
    'no state'"."""
    _write(CORRUPT_DOCUMENTS[name])
    assert flood_state.read() == {}


@pytest.mark.parametrize("name", sorted(CORRUPT_DOCUMENTS))
def test_a_corrupt_document_never_raises_on_load(tmp_path, name):
    """``load()`` runs inside ``lifecycle.start()``."""
    _write(CORRUPT_DOCUMENTS[name])
    flood_state.load()


@pytest.mark.parametrize("name", sorted(CORRUPT_DOCUMENTS))
def test_a_corrupt_document_is_reported_exactly_once(tmp_path, caplog, name):
    """"discarded with **one** WARNING" — not none (a silent reset of a
    ban history is how a banned chat comes back at full speed) and not one
    per chat (that is the log spam this ship deletes)."""
    _write(CORRUPT_DOCUMENTS[name])
    with caplog.at_level(logging.DEBUG, logger="aipager.bot.flood_state"):
        flood_state.read()
    assert len(_warnings(caplog)) == 1


@pytest.mark.parametrize("name", sorted(CORRUPT_DOCUMENTS))
def test_a_corrupt_document_leaves_no_chat_unlimited(
    limiter, tmp_path, name, run_async,
):
    """The safety direction: after the discard the chat is paced by the
    conservative default rather than by nothing at all."""
    _write(CORRUPT_DOCUMENTS[name])
    flood_state.load()
    rate = limiter.earned_rate(CHAT)
    assert math.isfinite(rate) and rate <= config.FLOOD_START_RATE


def test_a_missing_file_says_nothing_at_all(tmp_path, caplog):
    """A fresh install is not a problem to report: a WARNING on every
    first start is how operators learn to ignore WARNINGs."""
    if _state_path().exists():
        _state_path().unlink()
    with caplog.at_level(logging.DEBUG, logger="aipager.bot.flood_state"):
        assert flood_state.read() == {}
    assert _warnings(caplog) == []


def test_a_valid_document_is_not_reported_as_corrupt(tmp_path, caplog):
    """The control for the WARNING rows: a good file logs nothing."""
    _write({"version": flood_state.SCHEMA_VERSION, "written_at": 1.0,
            "chats": [_entry()]})
    with caplog.at_level(logging.DEBUG, logger="aipager.bot.flood_state"):
        assert flood_state.read() != {}
    assert _warnings(caplog) == []


def test_a_wrong_version_is_not_half_read(tmp_path):
    """Criterion 16's wording: a wrong ``version`` starts fresh. Reading
    half a schema is worse than reading none."""
    _write({"version": flood_state.SCHEMA_VERSION + 99, "written_at": 1.0,
            "chats": [_entry(rate=CEILING)]})
    flood_state.load()
    assert status.read_flood_chats() == []


# ===== what the writer puts back ========================================

def test_the_writer_never_emits_a_bare_nan_literal(limiter, qa_clock):
    """The other end of the same defect: ``json.dumps`` writes the bare
    ``NaN`` literal happily, which is how an invalid document gets created
    in the first place — and ``aipager status --json`` would then emit
    JSON no other parser accepts."""
    limiter.restore([_entry(rate=float("nan"))])
    flood_state.mark_dirty()
    flood_state.save_if_dirty(force=True)
    body = _state_path().read_text()
    assert "NaN" not in body and "Infinity" not in body


def test_a_document_written_after_a_corrupt_one_parses_strictly(
    limiter, qa_clock, tmp_path,
):
    """Round trip: whatever the daemon writes must be readable by a strict
    parser, or the next start discards it and forgets the ban."""
    _write(CORRUPT_DOCUMENTS["nan_literal"])
    flood_state.load()
    MUTE.mute(CHAT, 600.0)
    flood_state.mark_dirty()
    flood_state.save_if_dirty(force=True)
    json.loads(_state_path().read_text(), parse_constant=_reject)


def _reject(literal):  # noqa: D401 — a strict parser refuses NaN/Infinity
    raise ValueError(literal)
