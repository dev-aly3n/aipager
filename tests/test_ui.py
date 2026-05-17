"""Tests for aipager.ui — color/TTY handling and glyph output."""

from __future__ import annotations

import sys



def _reload_ui():
    """Re-import ui after env mutation so _resolve_color_kwargs re-runs."""
    sys.modules.pop("aipager.ui", None)
    import aipager.ui as ui
    return ui


def test_no_color_env_disables_color(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    ui = _reload_ui()
    assert ui.console.no_color is True


def test_clicolor_zero_disables_color(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.setenv("CLICOLOR", "0")
    ui = _reload_ui()
    assert ui.console.no_color is True


def test_clicolor_force_forces_terminal(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.delenv("CLICOLOR", raising=False)
    monkeypatch.setenv("CLICOLOR_FORCE", "1")
    ui = _reload_ui()
    # force_terminal becomes True on the console.
    assert ui.console.is_terminal is True


def test_ok_writes_to_stdout(capsys):
    # Reset env to a clean state for this test.
    ui = _reload_ui()
    ui.ok("first", "  detail")
    out, err = capsys.readouterr()
    assert ui.GLYPH_OK in out
    assert "first" in out
    assert "detail" in out
    assert err == ""


def test_err_writes_to_stderr(capsys):
    ui = _reload_ui()
    ui.err("oops")
    out, err = capsys.readouterr()
    assert ui.GLYPH_ERR in err
    assert "oops" in err
    assert out == ""


def test_warn_writes_to_stderr(capsys):
    ui = _reload_ui()
    ui.warn("careful")
    out, err = capsys.readouterr()
    assert ui.GLYPH_WARN in err
    assert "careful" in err


def test_err_block_includes_issue_url_when_bug(capsys):
    ui = _reload_ui()
    ui.err_block("crash", ["something broke"],
                 bug=True, issue_url="https://example.test/issues")
    err = capsys.readouterr().err
    assert "crash" in err
    assert "something broke" in err
    assert "https://example.test/issues" in err
    assert "aipager doctor" in err


def test_err_block_no_url_when_not_bug(capsys):
    ui = _reload_ui()
    ui.err_block("nope", ["fix it"], bug=False, issue_url="https://x/issues")
    err = capsys.readouterr().err
    assert "https://x/issues" not in err


def test_is_tty_returns_bool():
    ui = _reload_ui()
    assert isinstance(ui.is_tty(), bool)


def test_glyphs_are_single_chars():
    ui = _reload_ui()
    # Single grapheme each (multi-byte but one column).
    assert len(ui.GLYPH_OK) == 1
    assert len(ui.GLYPH_ERR) == 1
    assert len(ui.GLYPH_WARN) == 1
