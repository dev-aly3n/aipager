"""The elapsed clock: ``m:ss`` under an hour, ``h:mm:ss`` after; true
seconds at the card's unit "s", 30 s steps at "m"/"h" (a turn older than
10 minutes whose card refreshes every 30 s)."""

from __future__ import annotations

import math

import pytest

from aipager import clockfmt
from aipager.i18n import clock
from tests.footer_text import as_md

TABLE = [
    # seconds, unit, shown
    (0, "s", "0:00"), (1, "s", "0:01"), (45, "s", "0:45"), (59.99, "s", "0:59"),
    (60, "s", "1:00"), (250, "s", "4:10"), (599.9, "s", "9:59"), (600, "s", "10:00"),
    (1397, "s", "23:17"), (3599, "s", "59:59"), (3600, "s", "1:00:00"),
    (4980, "s", "1:23:00"), (7200, "s", "2:00:00"), (360000, "s", "100:00:00"),
    (45, "m", "0:30"), (29, "m", "0:00"), (30, "m", "0:30"), (1397, "m", "23:00"),
    (1410, "m", "23:30"), (1439.9, "m", "23:30"), (1440, "m", "24:00"),
    (3599, "m", "59:30"), (3600, "h", "1:00:00"), (5010, "h", "1:23:30"),
    (5039, "h", "1:23:30"), (4980, "h", "1:23:00"),
    (77, "x", "1:17"), (77, "", "1:17"), (77, None, "1:17"),
]


@pytest.mark.parametrize("seconds, unit, shown", TABLE)
def test_clock_table(seconds, unit, shown):
    assert clockfmt.elapsed(seconds, unit) == shown


@pytest.mark.parametrize("bad", [-4, -0.5, math.nan, math.inf, -math.inf, None, "x", [], {}])
def test_invalid_input_is_zero(bad):
    assert clockfmt.elapsed(bad) == "0:00"
    assert clockfmt.elapsed(bad, "h") == "0:00"


def test_numeric_strings_and_bools_coerce_like_float():
    assert clockfmt.elapsed("75") == "1:15"
    assert clockfmt.elapsed(True) == "0:01"


def test_minutes_never_read_as_seconds():
    """23 minutes is 23:00, never 0:23."""
    assert clockfmt.elapsed(23 * 60, "m") == "23:00"
    assert clockfmt.elapsed(23 * 60, "s") == "23:00"


def test_the_public_api_is_the_same_function():
    assert clock.elapsed is clockfmt.elapsed
    assert clock.STEP_SECONDS == clockfmt.STEP_SECONDS == 30


def test_clockfmt_is_stdlib_only():
    import ast
    from pathlib import Path
    tree = ast.parse(Path(clockfmt.__file__).read_text(encoding="utf-8"))
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imported |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert imported <= {"__future__", "math"}


def test_tr_clock_uses_native_digits_only_where_it_should():
    from aipager import i18n
    assert i18n.get("en").clock(5010, "h") == "1:23:30"
    assert i18n.get("fa").clock(5010, "h") == "۱:۲۳:۳۰"
    assert i18n.get("ar").clock(45) == "٠:٤٥"


def test_the_final_card_shows_true_seconds():
    """A settled card is not a live counter: whatever unit the turn
    reached, its clock reads true seconds."""
    import time

    from aipager.bot.animation import build_stream_card
    from aipager.state import Status, TrackedSession
    sess = TrackedSession(name="claude-dev", label="dev", status=Status.BUSY)
    sess.scope_kind, sess.scope_chat_id = "dm", 12345
    sess.busy_started_at = time.monotonic() - 1397.5
    sess.card_elapsed_unit = "m"
    live = as_md(build_stream_card(sess, "Working").rsplit("\n", 1)[-1])
    final = as_md(build_stream_card(sess, "Done", final=True).rsplit("\n", 1)[-1])
    assert live.endswith(" · 23:00"), live
    assert final.endswith(" · 23:17"), final
