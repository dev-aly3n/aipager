"""Tests for aipager.errors — friendly error rendering and excepthook."""

import errno
import sys

import pytest

from aipager import errors


def test_friendly_error_prints_title_and_lines(capsys):
    errors.friendly_error("First line", "  second")
    out = capsys.readouterr()
    assert out.out == ""
    assert "✗ First line" in out.err
    assert "  second" in out.err


def test_friendly_error_with_bug_appends_issue_url(capsys):
    errors.friendly_error("Crashed", bug=True)
    err = capsys.readouterr().err
    assert errors.ISSUE_URL in err
    assert "aipager doctor" in err


def test_friendly_error_without_bug_omits_issue_url(capsys):
    errors.friendly_error("misconfigured")
    err = capsys.readouterr().err
    assert errors.ISSUE_URL not in err


def test_friendly_warn_uses_warn_marker(capsys):
    errors.friendly_warn("Heads up")
    err = capsys.readouterr().err
    assert err.startswith("⚠ Heads up")


def test_install_excepthook_replaces_sys_excepthook(monkeypatch):
    original = sys.excepthook
    try:
        errors.install_excepthook()
        assert sys.excepthook is not original
    finally:
        sys.excepthook = original


def test_excepthook_keyboard_interrupt_delegates_to_default(monkeypatch):
    # KeyboardInterrupt should still go to the real default excepthook
    # so Ctrl-C produces the normal traceback (not the bug-report banner).
    original = sys.excepthook
    seen: list[type] = []
    monkeypatch.setattr(sys, "__excepthook__",
                        lambda t, e, tb: seen.append(t))
    try:
        errors.install_excepthook()
        sys.excepthook(KeyboardInterrupt, KeyboardInterrupt(), None)
        assert seen == [KeyboardInterrupt]
    finally:
        sys.excepthook = original


def test_excepthook_other_exception_prints_friendly_block(monkeypatch, capsys):
    original = sys.excepthook
    try:
        errors.install_excepthook()
        sys.excepthook(RuntimeError, RuntimeError("boom"), None)
        err = capsys.readouterr().err
        assert "aipager hit an unexpected error" in err
        assert "RuntimeError" in err
        assert "boom" in err
        assert errors.ISSUE_URL in err
    finally:
        sys.excepthook = original


def test_with_friendly_errors_translates_permission_error(capsys):
    @errors.with_friendly_errors
    def fn():
        raise PermissionError(errno.EACCES, "denied", "/no/such/file")

    with pytest.raises(SystemExit) as exc:
        fn()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "permission denied" in err
    assert "/no/such/file" in err


def test_with_friendly_errors_translates_disk_full(capsys):
    @errors.with_friendly_errors
    def fn():
        raise OSError(errno.ENOSPC, "no space left on device")

    with pytest.raises(SystemExit) as exc:
        fn()
    assert exc.value.code == 1
    assert "disk full" in capsys.readouterr().err


def test_with_friendly_errors_keyboard_interrupt_exits_130(capsys):
    @errors.with_friendly_errors
    def fn():
        raise KeyboardInterrupt

    with pytest.raises(SystemExit) as exc:
        fn()
    assert exc.value.code == 130
    assert "Cancelled" in capsys.readouterr().err


def test_with_friendly_errors_passes_systemexit_through():
    @errors.with_friendly_errors
    def fn():
        raise SystemExit(2)

    with pytest.raises(SystemExit) as exc:
        fn()
    assert exc.value.code == 2


def test_with_friendly_errors_generic_oserror_is_bug(capsys):
    @errors.with_friendly_errors
    def fn():
        raise OSError("weird")

    with pytest.raises(SystemExit):
        fn()
    err = capsys.readouterr().err
    assert errors.ISSUE_URL in err
