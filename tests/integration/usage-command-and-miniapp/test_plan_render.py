"""How plan readings are shown (design.md Success criteria 3-6, decisions.md
5-8; entrypoints.md /usage and /api/usage body).

Boundary-value analysis on the time left to a reset (24 h, just under,
1 h, under 1 h, 1 s, 0, past) and on the age of a reading (59 s, 60 s,
119 s, 1 h, 23 h 59 min 59 s, 1 day). Equivalence partitions over the
login kinds and the window subsets. Error guessing: a percentage over 100
(gateways can overshoot), a window order the payload does not follow, a
reading old enough to be pruned, more logins than the store keeps."""

from __future__ import annotations

import re
import time

import pytest

from aipager import usage_store
from aipager.bot import usage_cmd
from aipager.claude_bootstrap import SessionLogin
from aipager.statusline_file import Window

NOW = float(int(time.time()))
SAVED = SessionLogin("saved", "", "/home/alice/.claude")
GATEWAY = SessionLogin("gateway", "gw.example.com", "/home/alice/.claude")
ALL = usage_cmd.UsageAccess(None, "all", True)
GROUP_ACCESS = usage_cmd.UsageAccess(frozenset({-100777}), "chat", False)


def _hhmm(ts):
    return time.strftime("%H:%M", time.localtime(ts))


def _note(windows, *, login=SAVED, label="frontend", seen=NOW):
    usage_store.note_plan(label, windows, login, now=seen)


def _html(h, access=ALL, now=NOW):
    return usage_cmd.usage_html(usage_store.snapshot(None, now=now), access, h.en(), now)


def _view(h, access=ALL, now=NOW):
    return usage_cmd.usage_view(usage_store.snapshot(None, now=now), access, h.en(), now)


def _windows(h):
    (login,) = _view(h)["plan"]["logins"]
    return login["windows"]


def _reset_line(h, delta):
    _note({"seven_day": Window(42.0, int(NOW + delta))})
    (w,) = _windows(h)
    return w["reset"]


# ---- reset wording --------------------------------------------------------------

@pytest.mark.parametrize("delta, text", [
    (2 * 86400 + 5 * 3600 + 30 * 60, "Resets in 2 d 5 h"),
    (86400, "Resets in 1 d 0 h"),
    (6 * 86400 + 23 * 3600, "Resets in 6 d 23 h"),
], ids=["2d5h30m", "exactly-24h", "6d23h"])
def test_a_reset_a_day_or_more_away_says_days_and_hours(h, delta, text):
    assert _reset_line(h, delta) == text


@pytest.mark.parametrize("delta", [86400, 2 * 86400 + 5 * 3600, 6 * 86400],
                         ids=["24h", "2d5h", "6d"])
def test_a_reset_a_day_or_more_away_has_no_clock_time(h, delta):
    assert re.search(r"\d:\d\d", _reset_line(h, delta)) is None


@pytest.mark.parametrize("delta, text", [
    (86400 - 60, "Resets in 23 h 59 min, at {t}"),
    (3 * 3600 + 10 * 60, "Resets in 3 h 10 min, at {t}"),
    (3600, "Resets in 1 h 0 min, at {t}"),
], ids=["24h-1min", "3h10m", "exactly-1h"])
def test_a_reset_within_a_day_says_hours_minutes_and_the_clock(h, delta, text):
    assert _reset_line(h, delta) == text.format(t=_hhmm(NOW + delta))


@pytest.mark.parametrize("delta, text", [
    (25 * 60, "Resets in 25 min, at {t}"),
    (90, "Resets in 2 min, at {t}"),
    (60, "Resets in 1 min, at {t}"),
    (1, "Resets in 1 min, at {t}"),
], ids=["25min", "90s-rounds-up", "60s", "1s-is-at-least-1"])
def test_a_reset_within_the_hour_says_minutes_and_the_clock(h, delta, text):
    assert _reset_line(h, delta) == text.format(t=_hhmm(NOW + delta))


@pytest.mark.parametrize("delta", [0, -1, -3600, -6 * 86400], ids=["now", "1s-ago", "1h-ago", "6d-ago"])
def test_a_passed_reset_says_reset_since_the_last_reading(h, delta):
    _note({"seven_day": Window(83.0, int(NOW + delta))})
    assert "This week (all models): reset since the last reading" in _html(h)


@pytest.mark.parametrize("delta", [0, -60], ids=["now", "1min-ago"])
def test_a_passed_reset_never_shows_the_stale_percentage(h, delta):
    _note({"seven_day": Window(83.0, int(NOW + delta))})
    assert "83" not in _html(h).replace(_hhmm(NOW), "")


def test_a_passed_reset_is_state_reset_in_the_view(h):
    _note({"seven_day": Window(83.0, int(NOW - 1))})
    assert _windows(h)[0]["state"] == "reset"


def test_a_passed_reset_has_no_percentage_in_the_view(h):
    _note({"seven_day": Window(83.0, int(NOW - 1))})
    assert "pct" not in _windows(h)[0]


def test_a_passed_reset_has_an_empty_reset_line_in_the_view(h):
    _note({"seven_day": Window(83.0, int(NOW - 1))})
    assert _windows(h)[0]["reset"] == ""


def test_a_reset_one_second_away_still_shows_the_percentage(h):
    _note({"seven_day": Window(83.0, int(NOW + 1))})
    assert "This week (all models): 83% used" in _html(h)


def test_a_reading_goes_stale_as_time_passes(h):
    """The same stored reading, viewed after its reset time."""
    _note({"five_hour": Window(7.0, int(NOW + 600))})
    assert "5-hour window: reset since the last reading" in _html(h, now=NOW + 601)


@pytest.mark.parametrize("name, label", [
    ("five_hour", "5-hour window: reset since the last reading"),
    ("spend_limit", "Spend limit: reset since the last reading")])
def test_each_window_has_its_own_reset_line(h, name, label):
    _note({name: Window(50.0, int(NOW - 5))})
    assert label in _html(h)


def test_only_the_passed_window_is_shown_as_reset(h):
    _note({"five_hour": Window(7.0, int(NOW - 5)), "seven_day": Window(42.0, int(NOW + 86400))})
    assert "This week (all models): 42% used" in _html(h)


# ---- window lines ----------------------------------------------------------------------

@pytest.mark.parametrize("name, line", [
    ("five_hour", "5-hour window: 7% used"),
    ("seven_day", "This week (all models): 7% used"),
    ("spend_limit", "Spend limit: 7% used")])
def test_each_window_line(h, name, line):
    _note({name: Window(7.0, int(NOW + 3600 * 3))})
    assert line in _html(h)


def test_windows_are_shown_five_hour_then_week_then_spend_limit(h):
    _note({"spend_limit": Window(1.0, int(NOW + 9000)),
           "seven_day": Window(2.0, int(NOW + 90000)),
           "five_hour": Window(3.0, int(NOW + 900))})
    assert [w["id"] for w in _windows(h)] == ["five_hour", "seven_day", "spend_limit"]


def test_the_html_lists_the_windows_in_the_same_order(h):
    _note({"spend_limit": Window(1.0, int(NOW + 9000)),
           "seven_day": Window(2.0, int(NOW + 90000)),
           "five_hour": Window(3.0, int(NOW + 900))})
    html = _html(h)
    assert html.index("5-hour window") < html.index("This week") < html.index("Spend limit")


def test_a_percentage_is_rounded(h):
    _note({"seven_day": Window(82.6, int(NOW + 90000))})
    assert "83% used" in _html(h)


def test_the_view_percentage_is_an_integer(h):
    _note({"seven_day": Window(82.6, int(NOW + 90000))})
    assert _windows(h)[0]["pct"] == 83


def test_a_percentage_over_100_is_shown_as_reported(h):
    _note({"spend_limit": Window(105.0, int(NOW + 90000))})
    assert _windows(h)[0]["pct"] == 105


def test_a_window_absent_from_the_reading_is_not_shown(h):
    """The setup-token login: only the week arrives."""
    _note({"seven_day": Window(83.0, int(NOW + 90000))})
    assert "5-hour" not in _html(h)


# ---- freshness ---------------------------------------------------------------------------

@pytest.mark.parametrize("age, words", [
    (0, "just now"), (59, "just now"),
    (60, "1 minute ago"), (119, "1 minute ago"), (120, "2 minutes ago"),
    (3599, "59 minutes ago"), (3600, "1 hour ago"), (7200, "2 hours ago"),
    (86399, "23 hours ago"), (86400, "1 day ago"), (2 * 86400 + 5, "2 days ago"),
], ids=["0s", "59s", "60s", "119s", "120s", "3599s", "1h", "2h", "1d-1s", "1d", "2d"])
def test_the_freshness_line(h, age, words):
    seen = NOW - age
    _note({"seven_day": Window(10.0, int(NOW + 7 * 86400))}, seen=seen)
    assert f"As of {_hhmm(seen)} ({words}), from session frontend." in _html(h)


def test_the_view_carries_the_freshness_line(h):
    _note({"seven_day": Window(10.0, int(NOW + 7 * 86400))}, seen=NOW - 240)
    (login,) = _view(h)["plan"]["logins"]
    assert login["fresh"] == f"As of {_hhmm(NOW - 240)} (4 minutes ago), from session frontend."


# ---- no reading -----------------------------------------------------------------------------

NONE_LINE = ("Plan limits show here after Claude answers in a session signed in with a "
             "Claude subscription. API keys, and gateways without a spend limit, do not "
             "report them.")


def test_no_reading_says_how_one_arrives(h):
    assert NONE_LINE in _html(h)


def test_no_reading_fills_the_view_empty_line(h):
    assert _view(h)["plan"]["empty"] == NONE_LINE


def test_no_reading_has_no_logins_in_the_view(h):
    assert _view(h)["plan"]["logins"] == []


def test_a_reading_clears_the_view_empty_line(h):
    _note({"seven_day": Window(10.0, int(NOW + 86400 * 3))})
    assert _view(h)["plan"]["empty"] is None


def test_no_reading_shows_no_percentage(h):
    assert "% used" not in _html(h)


# ---- logins ------------------------------------------------------------------------------------

@pytest.mark.parametrize("login, name", [
    (SessionLogin("token", "env", "/home/alice/.claude"), "Claude login token"),
    (SessionLogin("saved", "", "/home/alice/.claude"), "Claude sign-in saved on this computer"),
    (SessionLogin("gateway", "gw.example.com", "/home/alice/.claude"), "Gateway gw.example.com"),
    (SessionLogin("cloud", "", "/home/alice/.claude"), "Cloud provider"),
], ids=["token", "saved", "gateway", "cloud"])
def test_each_login_kind_is_named_when_two_logins_have_readings(h, login, name):
    other = SessionLogin("unknown", "", "")
    _note({"seven_day": Window(10.0, int(NOW + 86400 * 3))}, login=other, label="backend")
    _note({"seven_day": Window(20.0, int(NOW + 86400 * 3))}, login=login)
    assert name in [lg["name"] for lg in _view(h)["plan"]["logins"]]


@pytest.mark.parametrize("a, b", [
    (SessionLogin("token", "env", "/home/alice/.claude"),
     SessionLogin("saved", "", "/home/alice/.claude")),
    (SessionLogin("token", "project:/home/alice/a", "/home/alice/.claude"),
     SessionLogin("token", "project:/home/alice/b", "/home/alice/.claude")),
    (SessionLogin("saved", "", "/home/alice/.claude"),
     SessionLogin("saved", "", "/home/alice/.claude-work")),
    (SessionLogin("gateway", "gw1.example.com", "/home/alice/.claude"),
     SessionLogin("gateway", "gw2.example.com", "/home/alice/.claude")),
], ids=["kind", "source-layer", "config-home", "gateway-host"])
def test_logins_that_differ_keep_two_readings(a, b):
    _note({"seven_day": Window(10.0, int(NOW + 86400 * 3))}, login=a, label="frontend")
    _note({"seven_day": Window(20.0, int(NOW + 86400 * 3))}, login=b, label="backend")
    assert len(usage_store.snapshot(None, now=NOW).plan) == 2


def test_logins_that_differ_are_never_merged_in_the_view(h):
    _note({"seven_day": Window(10.0, int(NOW + 86400 * 3))}, login=SAVED, label="frontend")
    _note({"five_hour": Window(20.0, int(NOW + 3600))}, login=GATEWAY, label="backend")
    assert sorted(len(lg["windows"]) for lg in _view(h)["plan"]["logins"]) == [1, 1]


def test_the_same_login_keeps_one_reading():
    _note({"seven_day": Window(10.0, int(NOW + 86400 * 3))}, label="frontend", seen=NOW - 60)
    _note({"seven_day": Window(20.0, int(NOW + 86400 * 3))}, label="backend")
    assert len(usage_store.snapshot(None, now=NOW).plan) == 1


def test_the_same_login_shows_the_newest_reading(h):
    _note({"seven_day": Window(10.0, int(NOW + 86400 * 3))}, label="frontend", seen=NOW - 60)
    _note({"seven_day": Window(20.0, int(NOW + 86400 * 3))}, label="backend")
    assert "20% used" in _html(h)


def test_readings_are_newest_first(h):
    _note({"seven_day": Window(10.0, int(NOW + 86400 * 3))}, login=SAVED, label="frontend",
          seen=NOW - 600)
    _note({"seven_day": Window(20.0, int(NOW + 86400 * 3))}, login=GATEWAY, label="backend")
    html = _html(h)
    assert html.index("from session backend") < html.index("from session frontend")


def test_the_store_keeps_at_most_max_readings():
    for i in range(usage_store.MAX_READINGS + 1):
        _note({"seven_day": Window(10.0, int(NOW + 86400 * 3))},
              login=SessionLogin("saved", "", f"/home/alice/.claude-{i}"),
              label=f"s{i}", seen=NOW - 1000 + i)
    assert len(usage_store.snapshot(None, now=NOW).plan) == usage_store.MAX_READINGS


def test_the_least_recently_seen_reading_goes_first(h):
    for i in range(usage_store.MAX_READINGS + 1):
        _note({"seven_day": Window(10.0, int(NOW + 86400 * 3))},
              login=SessionLogin("saved", "", f"/home/alice/.claude-{i}"),
              label=f"s{i}", seen=NOW - 1000 + i)
    assert "from session s0." not in _html(h)


def test_a_reading_older_than_eight_days_is_gone(h):
    _note({"seven_day": Window(10.0, int(NOW - 86400))}, seen=NOW - 9 * 86400)
    assert len(usage_store.snapshot(None, now=NOW).plan) == 0


def test_note_plan_reports_a_change():
    assert _note_ret() is True


def _note_ret():
    return usage_store.note_plan("frontend", {"seven_day": Window(1.0, int(NOW + 9999))},
                                 SAVED, now=NOW)


# ---- groups and titles -----------------------------------------------------------------------

def test_the_html_title(h):
    assert "📊 <b>Usage</b>" in _html(h)


def test_the_plan_title(h):
    assert "Plan limits" in _html(h)


def test_without_plan_access_no_plan_title(h):
    _note({"seven_day": Window(10.0, int(NOW + 86400 * 3))})
    assert "Plan limits" not in _html(h, GROUP_ACCESS).replace(
        "Plan limits are not shown in a group", "")


def test_without_plan_access_no_percentage(h):
    _note({"seven_day": Window(10.0, int(NOW + 86400 * 3))})
    assert "used" not in _html(h, GROUP_ACCESS)


def test_without_plan_access_the_view_is_not_shown(h):
    _note({"seven_day": Window(10.0, int(NOW + 86400 * 3))})
    assert _view(h, GROUP_ACCESS)["plan"]["shown"] is False


def test_with_plan_access_the_view_is_shown(h):
    assert _view(h)["plan"]["shown"] is True
