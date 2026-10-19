"""`aipager resume <name>` brings a session back on the model it was started
on or switched to (``launch_model``), as the Telegram resume and restart do,
not on Claude Code's default."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from aipager.cli import resume as cli_resume


def _record(**fields) -> dict:
    return {
        "name": "claude-dev",
        "label": "dev",
        "claude_session_id": "some-uuid-1234",
        "cwd": "/home/alice/project",
        "gone_at": 1234567890.0,
        "perm_mode": "auto",
        **fields,
    }


def _launched_model(monkeypatch, record):
    monkeypatch.setattr(cli_resume, "_gone_history", lambda: [record])
    launched = {}

    async def mock_launch(label, **kw):
        launched.update(kw)
        return True, ""

    with patch("aipager.dtach.inject.launch_session", side_effect=mock_launch), \
            patch("pathlib.Path.is_socket", return_value=False), \
            patch("aipager.ui.ok"):
        assert cli_resume._resume_one("dev") == 0
    assert "model" in launched
    return launched["model"]


@pytest.mark.parametrize("saved, want", [
    ("openrouter/acme/model-y@v1:free", "openrouter/acme/model-y@v1:free"),
    ("opus", "opus"),
    ("  sonnet  ", "sonnet"),
    ("", None),
    ("   ", None),
    (None, None),
    (123, None),
    (["opus"], None),
], ids=["provider-name", "alias", "padded", "empty", "blank", "null", "number", "list"])
def test_resume_launches_the_saved_model(monkeypatch, saved, want):
    assert _launched_model(monkeypatch, _record(launch_model=saved)) == want


def test_an_older_record_without_a_model_resumes_on_the_default(monkeypatch):
    assert _launched_model(monkeypatch, _record()) is None


@pytest.mark.parametrize("preview", [
    "See [the docs](https://example.com/docs) first.",
    "Closed the tag [/x] by hand.",
    "[bold]not bold[/bold]",
    "Done :smile:",
    "Ends in a backslash \\",
], ids=["markdown-link", "stray-close", "style-tag", "emoji-code", "backslash"])
def test_the_last_response_is_printed_as_written(monkeypatch, capsys, preview):
    """Claude's last answer is its own text: printed as Rich markup, a
    Markdown link lost its text and a "[/x]" raised MarkupError after the
    session had already started, so no "Attach with" line followed."""
    monkeypatch.setattr(cli_resume, "_gone_history",
                        lambda: [_record(last_assistant_preview=preview)])

    async def mock_launch(label, **kw):
        return True, ""

    with patch("aipager.dtach.inject.launch_session", side_effect=mock_launch), \
            patch("pathlib.Path.is_socket", return_value=False), \
            patch("aipager.ui.ok"):
        assert cli_resume._resume_one("dev") == 0
    out = capsys.readouterr().out
    assert preview in out
    assert "Attach with: dtach -a" in out
