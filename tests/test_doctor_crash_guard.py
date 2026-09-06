"""`aipager doctor` survives a check that throws (roadmap 8.8).

`run_all()` used to be a bare list comprehension over `CHECKS`, so one
check raising took the whole report down with a traceback — the one
tool meant to explain a broken environment hiding the other thirteen
answers. Now a crashing check becomes a single WARN row naming the
check and the error, the remaining checks still run in order, markup
in the exception text cannot corrupt the table, Ctrl-C still stops the
command, and the exit code keeps WARN's meaning (0).
"""

from __future__ import annotations

import argparse

import pytest

from aipager import doctor


def _ok(title: str):
    def check():
        return doctor.CheckResult(doctor.OK, title)
    check.__name__ = f"check_{title.replace(' ', '_')}"
    return check


def _raising(name: str, exc: BaseException):
    def check():
        raise exc
    check.__name__ = name
    return check


def test_a_crashing_check_becomes_one_warn_row_and_the_rest_still_run(monkeypatch):
    monkeypatch.setattr(doctor, "CHECKS", [
        _ok("first"),
        _raising("check_thing_two", RuntimeError("boom")),
        _ok("third"),
    ])

    rows = doctor.run_all()

    assert [r.title for r in rows] == ["first", "thing two", "third"]
    assert [r.status for r in rows] == [doctor.OK, doctor.WARN, doctor.OK]
    crashed = rows[1]
    assert crashed.detail == ["check crashed: RuntimeError: boom"]
    assert crashed.fix and "aipager doctor" in crashed.fix
    assert rows[0].detail == [] and rows[2].detail == []


def test_leading_underscore_check_name_is_titled_the_same_way(monkeypatch):
    monkeypatch.setattr(doctor, "CHECKS", [_raising("_check_odd_one", ValueError("x"))])
    assert doctor.run_all()[0].title == "odd one"


def test_markup_in_the_exception_text_is_escaped_and_printed_verbatim(monkeypatch, capsys):
    monkeypatch.setattr(doctor, "CHECKS", [
        _raising("check_markup", ValueError("[bold]x[/bold] failed")),
    ])

    rows = doctor.run_all()
    assert rows[0].detail == ["check crashed: ValueError: \\[bold]x\\[/bold] failed"]

    doctor._print_results(rows)
    out = capsys.readouterr().out
    assert "[bold]x[/bold] failed" in out   # rendered as text, not styling
    assert "markup" in out


def test_keyboard_interrupt_still_stops_the_command(monkeypatch):
    monkeypatch.setattr(doctor, "CHECKS", [_raising("check_slow", KeyboardInterrupt())])
    with pytest.raises(KeyboardInterrupt):
        doctor.run_all()


def test_a_crashed_check_is_a_warning_not_a_failure_for_the_exit_code(monkeypatch):
    monkeypatch.setattr(doctor, "CHECKS", [
        _ok("first"),
        _raising("check_thing_two", OSError(13, "Permission denied")),
    ])
    rc = doctor.cmd_doctor(argparse.Namespace())
    assert rc == 0


def test_crashed_check_logs_the_traceback_at_debug(monkeypatch, caplog):
    import logging
    monkeypatch.setattr(doctor, "CHECKS", [_raising("check_noisy", RuntimeError("boom"))])
    with caplog.at_level(logging.DEBUG, logger="aipager.doctor"):
        doctor.run_all()
    debug = [r for r in caplog.records if r.levelno == logging.DEBUG and "check_noisy" in r.getMessage()]
    assert debug and debug[0].exc_info is not None


def test_multi_line_exception_text_keeps_only_its_first_line(monkeypatch):
    monkeypatch.setattr(doctor, "CHECKS", [
        _raising("check_yaml", ValueError("line one\nline two\nline three")),
    ])
    detail = doctor.run_all()[0].detail[0]
    assert "\n" not in detail
    assert detail == "check crashed: ValueError: line one"


def test_long_exception_text_is_capped(monkeypatch):
    monkeypatch.setattr(doctor, "CHECKS", [_raising("check_big", RuntimeError("x" * 5000))])
    detail = doctor.run_all()[0].detail[0]
    assert len(detail) <= len("check crashed: RuntimeError: ") + doctor._CRASH_DETAIL_MAX
    assert detail.endswith("x")


def test_empty_exception_text_has_no_dangling_colon(monkeypatch):
    monkeypatch.setattr(doctor, "CHECKS", [_raising("check_bare", Exception())])
    assert doctor.run_all()[0].detail == ["check crashed: Exception"]


def test_nameless_callable_gets_the_fallback_title(monkeypatch):
    import functools

    def check(_flag):
        raise RuntimeError("boom")
    monkeypatch.setattr(doctor, "CHECKS", [functools.partial(check, True)])
    row = doctor.run_all()[0]
    assert row.title == "check" and row.status == doctor.WARN
