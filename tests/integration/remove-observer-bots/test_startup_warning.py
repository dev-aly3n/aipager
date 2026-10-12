"""A still-set ``OBSERVER_BOTS`` gives one English start-up warning and
nothing else; unset, empty or whitespace gives silence. The daemon starts
and stops normally either way, and the value (bot tokens) never shows.

design.md success criteria 1 and 2; entrypoints.md ``_run_daemon``.
"""

from __future__ import annotations

import logging

import pytest

REMOVED_WARNING = (
    "OBSERVER_BOTS is set but observer bots were removed; "
    "to let someone watch, add them to a group with the read_only role"
)

SECRET = "123456789:AAsecretTOKENcanary"

#: Non-blank values: every one counts as "set", whatever its format.
SET_VALUES = {
    "token_shaped": f"{SECRET}:42",
    "multi_entry": "111111:AAoneTOKEN:1,222222:AAtwoTOKEN:-1002",
    "malformed": "garbage",
    "single_char": "x",
    "padded": "   1:a:2   ",
    "inner_newline": "1:a:2\n3:b:4",
    "percent_format": "%s %d %(x)s {0}",
    "non_ascii": "توکن:مخفی",
    "very_long": "9" * 10_000,
}

#: Values that mean "not set".
BLANK_VALUES = {
    "unset": None,
    "empty": "",
    "one_space": " ",
    "spaces": "    ",
    "tabs_and_newlines": "\t\n \r\n\t",
}


def _hits(caplog):
    return [r for r in caplog.records if r.getMessage() == REMOVED_WARNING]


# ---- set: exactly one warning ------------------------------------------------

@pytest.mark.parametrize("value", list(SET_VALUES.values()), ids=list(SET_VALUES))
def test_a_non_blank_value_gives_exactly_one_warning(run_daemon, caplog, value):
    run_daemon(value)
    assert len(_hits(caplog)) == 1


@pytest.mark.parametrize("value", list(SET_VALUES.values()), ids=list(SET_VALUES))
def test_a_non_blank_value_never_stops_the_daemon(run_daemon, value):
    stubs = run_daemon(value)
    assert stubs.error is None


@pytest.mark.parametrize("value", list(SET_VALUES.values()), ids=list(SET_VALUES))
def test_a_non_blank_value_still_starts_every_component(run_daemon, value):
    stubs = run_daemon(value)
    assert (stubs.bot.start.await_count, stubs.hook.start.await_count,
            stubs.monitor.start.await_count) == (1, 1, 1)


@pytest.mark.parametrize("value", list(SET_VALUES.values()), ids=list(SET_VALUES))
def test_a_non_blank_value_still_stops_every_component(run_daemon, value):
    stubs = run_daemon(value)
    assert (stubs.bot.stop.await_count, stubs.hook.stop.call_count,
            stubs.monitor.stop.call_count) == (1, 1, 1)


def test_the_warning_is_at_warning_level(run_daemon, caplog):
    run_daemon(SET_VALUES["token_shaped"])
    assert [r.levelno for r in _hits(caplog)] == [logging.WARNING]


def test_the_warning_is_on_the_aipager_logger(run_daemon, caplog):
    run_daemon(SET_VALUES["token_shaped"])
    assert [r.name for r in _hits(caplog)] == ["aipager"]


def test_the_warning_carries_no_exception(run_daemon, caplog):
    run_daemon(SET_VALUES["token_shaped"])
    assert [r.exc_info for r in _hits(caplog)] == [None]


def test_no_other_record_names_the_variable(run_daemon, caplog):
    """The single warning is the only record that mentions it."""
    run_daemon(SET_VALUES["multi_entry"])
    mentions = [r for r in caplog.records if "OBSERVER_BOTS" in r.getMessage()]
    assert len(mentions) == 1


def test_no_record_at_error_level_or_above(run_daemon, caplog):
    run_daemon(SET_VALUES["malformed"])
    assert [r.getMessage() for r in caplog.records
            if r.levelno >= logging.ERROR] == []


# ---- the value never shows ---------------------------------------------------

def _leaks(caplog, capsys, needles):
    captured = capsys.readouterr()
    found = []
    for r in caplog.records:
        parts = [r.getMessage(), str(r.msg), repr(r.args), r.exc_text or ""]
        found += [n for n in needles for p in parts if n in p]
    found += [n for n in needles if n in captured.out or n in captured.err]
    return found


def test_a_token_value_never_reaches_a_log_record_or_the_console(
        run_daemon, caplog, capsys):
    run_daemon(SET_VALUES["token_shaped"])
    # A plain ``assert not leaks`` would print the secret on failure.
    if _leaks(caplog, capsys, [SECRET, "AAsecretTOKENcanary"]):
        pytest.fail("the OBSERVER_BOTS value leaked", pytrace=False)


def test_no_entry_of_a_multi_entry_value_leaks(run_daemon, caplog, capsys):
    run_daemon(SET_VALUES["multi_entry"])
    if _leaks(caplog, capsys, ["AAoneTOKEN", "AAtwoTOKEN", "111111:", "222222:"]):
        pytest.fail("an OBSERVER_BOTS entry leaked", pytrace=False)


def test_a_malformed_value_never_leaks(run_daemon, caplog, capsys):
    run_daemon("zz-canary-garbage-zz")
    if _leaks(caplog, capsys, ["zz-canary-garbage-zz"]):
        pytest.fail("the malformed OBSERVER_BOTS value leaked", pytrace=False)


def test_format_characters_in_the_value_leave_the_message_exact(run_daemon, caplog):
    """A value full of %-format and {}-format markers neither crashes the
    log call nor changes the text."""
    run_daemon(SET_VALUES["percent_format"])
    assert [r.getMessage() for r in _hits(caplog)] == [REMOVED_WARNING]


# ---- blank or unset: silence -------------------------------------------------

@pytest.mark.parametrize("value", list(BLANK_VALUES.values()), ids=list(BLANK_VALUES))
def test_a_blank_or_unset_value_gives_no_warning(run_daemon, caplog, value):
    run_daemon(value)
    assert _hits(caplog) == []


@pytest.mark.parametrize("value", list(BLANK_VALUES.values()), ids=list(BLANK_VALUES))
def test_a_blank_or_unset_value_is_never_mentioned(run_daemon, caplog, value):
    run_daemon(value)
    assert [r.getMessage() for r in caplog.records
            if "OBSERVER_BOTS" in r.getMessage()] == []


@pytest.mark.parametrize("value", list(BLANK_VALUES.values()), ids=list(BLANK_VALUES))
def test_a_blank_or_unset_value_starts_the_daemon(run_daemon, value):
    stubs = run_daemon(value)
    assert (stubs.error, stubs.bot.start.await_count,
            stubs.hook.start.await_count, stubs.monitor.start.await_count) \
        == (None, 1, 1, 1)


# ---- read at call time, once per start ---------------------------------------

def test_the_variable_is_read_at_each_start_not_at_import(run_daemon, caplog):
    """Set for the first start, then removed: the second start is silent."""
    run_daemon(SET_VALUES["token_shaped"])
    caplog.clear()
    run_daemon(None)
    assert _hits(caplog) == []


def test_a_value_set_after_a_silent_start_warns_on_the_next(run_daemon, caplog):
    run_daemon(None)
    caplog.clear()
    run_daemon(SET_VALUES["malformed"])
    assert len(_hits(caplog)) == 1


def test_each_start_warns_once(run_daemon, caplog):
    run_daemon(SET_VALUES["token_shaped"])
    run_daemon(SET_VALUES["token_shaped"])
    assert len(_hits(caplog)) == 2
