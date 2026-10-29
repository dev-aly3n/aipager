"""The plain elapsed clock (design.md "Success criteria" 4; entrypoints.md
``aipager.clockfmt.elapsed`` and the "Elapsed clocks" list).

Equivalence classes: unit "s" (true seconds), unit "m"/"h" (30 s floor),
an unknown unit (treated as "s"), invalid input (0). Boundaries: 599/600,
3599/3600, 29/30, 59/60, the step edges 1409/1410, and an hour field past
99. Surfaces: the function, ``Tr.clock``, /status, the busy card status
line and the settled card.
"""

from __future__ import annotations

import math
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import clockfmt
from aipager import preferences as prefs
from aipager.state import SessionRegistry, Status, TrackedSession
from tests.footer_text import as_md


# ---- the function: unit "s" ------------------------------------------------------

@pytest.mark.parametrize("seconds,expected", [
    (0, "0:00"),
    (1, "0:01"),
    (45, "0:45"),
    (59, "0:59"),
    (60, "1:00"),
    (250, "4:10"),
    (599, "9:59"),
    (599.9, "9:59"),
    (600, "10:00"),
    (601, "10:01"),
    (1397, "23:17"),
    (3599, "59:59"),
    (3599.99, "59:59"),
    (3600, "1:00:00"),
    (3601, "1:00:01"),
    (5010, "1:23:30"),
    (5025, "1:23:45"),
    (7200, "2:00:00"),
    (35999, "9:59:59"),
    (36000, "10:00:00"),
    (360000, "100:00:00"),
])
def test_unit_s_shows_true_seconds(seconds, expected):
    assert clockfmt.elapsed(seconds, "s") == expected


@pytest.mark.parametrize("seconds,expected", [
    (45, "0:45"), (3599, "59:59"), (3600, "1:00:00"),
])
def test_the_default_unit_is_true_seconds(seconds, expected):
    assert clockfmt.elapsed(seconds) == expected


# ---- the function: units "m" and "h" floor to 30 s -------------------------------

@pytest.mark.parametrize("unit", ["m", "h"])
@pytest.mark.parametrize("seconds,expected", [
    (0, "0:00"),
    (29, "0:00"),
    (29.99, "0:00"),
    (30, "0:30"),
    (45, "0:30"),
    (59.9, "0:30"),
    (60, "1:00"),
    (599, "9:30"),
    (600, "10:00"),
    (1397, "23:00"),
    (1409, "23:00"),
    (1410, "23:30"),
    (1439, "23:30"),
    (1440, "24:00"),
    (3599, "59:30"),
    (3600, "1:00:00"),
    (3629, "1:00:00"),
    (3630, "1:00:30"),
    (4980, "1:23:00"),
    (5010, "1:23:30"),
    (360029, "100:00:00"),
])
def test_units_m_and_h_floor_to_30_seconds(unit, seconds, expected):
    assert clockfmt.elapsed(seconds, unit) == expected


def test_the_step_is_30_seconds():
    assert clockfmt.STEP_SECONDS == 30


@pytest.mark.parametrize("unit", ["x", "", "M", "seconds", None])
def test_an_unknown_unit_is_true_seconds(unit):
    assert clockfmt.elapsed(1425, unit) == "23:45"


def test_23_minutes_never_reads_as_0_23():
    """spec.md: never `0:23` for 23 minutes."""
    assert clockfmt.elapsed(23 * 60, "m") == "23:00"


# ---- the function: invalid input becomes 0 --------------------------------------

@pytest.mark.parametrize("value", [
    None, "abc", "", float("nan"), float("inf"), float("-inf"),
    -0.1, -4, -3600, [], {}, object(),
])
@pytest.mark.parametrize("unit", ["s", "m"])
def test_invalid_or_negative_input_is_zero(value, unit):
    assert clockfmt.elapsed(value, unit) == "0:00"


def test_a_numeric_string_is_read_as_a_number():
    """'coerce to float' (design.md 3)."""
    assert clockfmt.elapsed("45") == "0:45"


def test_the_result_is_always_a_plain_clock():
    for seconds in range(0, 7300, 7):
        for unit in ("s", "m", "h"):
            out = clockfmt.elapsed(seconds, unit)
            parts = out.split(":")
            assert len(parts) in (2, 3), out
            assert all(p.isdigit() for p in parts), out
            assert all(len(p) == 2 for p in parts[1:]), out


def test_the_30s_floor_never_rounds_up():
    for seconds in range(0, 7300):
        shown = clockfmt.elapsed(seconds, "m")
        parts = [int(p) for p in shown.split(":")]
        value = parts[0] * 60 + parts[1] if len(parts) == 2 else \
            parts[0] * 3600 + parts[1] * 60 + parts[2]
        assert value <= seconds < value + 30, (seconds, shown)
        assert value % 30 == 0, (seconds, shown)


# ---- the same function behind the i18n API --------------------------------------

def test_the_i18n_clock_is_the_same_function():
    from aipager.i18n import clock
    assert clock.elapsed is clockfmt.elapsed


@pytest.mark.parametrize("seconds,unit", [(45, "s"), (1425, "m"), (5025, "h"), (3600, "s")])
def test_english_tr_clock_equals_the_plain_clock(seconds, unit):
    from aipager import i18n
    assert i18n.get("en").clock(seconds, unit) == clockfmt.elapsed(seconds, unit)


def test_persian_clock_uses_persian_digits():
    from aipager import i18n
    assert i18n.get("fa").clock(45) == "۰:۴۵"


def test_arabic_clock_uses_arabic_digits():
    from aipager import i18n
    assert i18n.get("ar").clock(5010, "m") == "١:٢٣:٣٠"


@pytest.mark.parametrize("code", ["es", "de", "fr", "zh-Hans", "xx-unknown"])
def test_other_languages_keep_latin_digits(code):
    from aipager import i18n
    assert i18n.get(code).clock(250) == "4:10"


# ---- /status ---------------------------------------------------------------------

CHAT = 12345


@pytest.fixture
def status_bot(mk_bot, monkeypatch):
    bot = mk_bot(SessionRegistry())
    bot._read_status_file = MagicMock(return_value=None)
    bot._maybe_update_bot_name = AsyncMock()
    bot._update_bot_commands = AsyncMock()

    async def _alive(name):
        sess = bot.registry.get(name)
        return sess is not None and sess.status != Status.GONE
    monkeypatch.setattr("aipager.dtach.inject.is_alive", _alive)
    monkeypatch.setattr("aipager.dtach.inject.list_sessions",
                        AsyncMock(return_value=[]))
    return bot


def _status_line(bot, mk_update, run_async, age):
    s = TrackedSession(name="claude-x1", label="x1", status=Status.BUSY)
    s.scope_chat_id = CHAT
    s.busy_started_at = time.monotonic() - age
    bot.registry._sessions[s.name] = s
    update = mk_update("/status", chat_id=CHAT)
    update.effective_chat.type = "group"
    run_async(bot._handle_status(update, MagicMock()))
    text = update.message.reply_text.await_args.args[0]
    (line,) = [ln for ln in text.split("\n") if "<b>x1</b>" in ln]
    return line


@pytest.mark.parametrize("age,clock,next_clock", [
    (192.3, "3:12", "3:13"), (599.3, "9:59", "10:00"),
    (600.3, "10:00", "10:01"), (1425.3, "23:45", "23:46"),
    (3599.3, "59:59", "1:00:00"), (3600.3, "1:00:00", "1:00:01"),
])
def test_status_says_working_with_a_true_seconds_clock(
        status_bot, mk_update, run_async, age, clock, next_clock):
    started = time.monotonic()
    line = _status_line(status_bot, mk_update, run_async, age)
    # The clock is real, and each age leaves 0.7 s before the next second.
    # A loaded CI runner took longer than that once (2026-10-18), so only a
    # call that slow may show the next second.
    crossed = time.monotonic() - started >= 0.7
    assert (f"⚙️ working {clock}" in line
            or (crossed and f"⚙️ working {next_clock}" in line))


# ---- the busy card status line and the settled card -------------------------------

def _card_sess(age, unit, chat=222333444):
    s = TrackedSession(name="claude-x1", label="x1", status=Status.BUSY)
    s.scope_kind = "dm"
    s.scope_chat_id = chat
    s.busy_started_at = time.monotonic() - age
    s.card_elapsed_unit = unit
    return s


def _status_of(card):
    """The card's status line (a footer block) read back as Markdown."""
    return as_md(card.rpartition("\n\n")[2])


@pytest.mark.parametrize("age,clock", [
    (0.3, "0:00"), (45.3, "0:45"), (250.3, "4:10"), (599.3, "9:59"),
])
def test_the_live_card_counts_true_seconds_under_10_minutes(age, clock):
    from aipager.bot.animation import build_stream_card
    status = _status_of(build_stream_card(_card_sess(age, "s"), "Working"))
    assert status.startswith(f"⏳ **x1** · Working · {clock}")


@pytest.mark.parametrize("age,clock", [
    (1397.3, "23:00"), (1409.3, "23:00"), (1410.3, "23:30"),
    (4980.3, "1:23:00"), (5025.3, "1:23:30"),
])
def test_the_live_card_moves_in_30s_steps_after_10_minutes(age, clock):
    from aipager.bot.animation import build_stream_card
    status = _status_of(build_stream_card(_card_sess(age, "m"), "Working"))
    assert status.startswith(f"⏳ **x1** · Working · {clock}")


def test_decay_off_keeps_true_seconds_on_a_long_turn():
    from aipager.bot.animation import build_stream_card
    prefs.set_preference(222333444, "card_age_decay", False)
    status = _status_of(build_stream_card(_card_sess(1425.3, "s"), "Working"))
    assert status.startswith("⏳ **x1** · Working · 23:45")


def test_the_settled_card_shows_true_seconds_after_a_long_turn():
    """Settled values are true seconds (design.md success criterion 4)."""
    from aipager.bot.animation import build_stream_card
    status = _status_of(build_stream_card(_card_sess(1397.3, "m"), "Done", final=True))
    assert " · Done · 23:17" in status


def test_the_live_card_never_shows_the_old_word_counter():
    from aipager.bot.animation import build_stream_card
    status = _status_of(build_stream_card(_card_sess(4980.3, "m"), "Working"))
    assert "1h 23m" not in status


def test_the_clock_has_no_timer_prefix():
    """Orchestrator ruling: no ⏱ prefix is added; only the number changes."""
    from aipager.bot.animation import build_stream_card
    status = _status_of(build_stream_card(_card_sess(250.3, "s"), "Working"))
    assert "⏱" not in status


def test_nan_safe_reading_of_the_spec_examples():
    """entrypoints.md example 599.9 -> 9:59 is truncation, not rounding."""
    assert clockfmt.elapsed(math.nextafter(600, 0)) == "9:59"
