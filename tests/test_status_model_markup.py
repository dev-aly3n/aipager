"""`aipager status` prints a session's model as written.

The model is Claude Code's display name, and a model name is any text
someone typed or set (a provider's id, a name with brackets). Read as Rich
markup, ``[bold]`` would restyle the line and a stray ``[/x]`` would raise
MarkupError and stop the command."""

from __future__ import annotations

import pytest

from aipager import status

MODEL = "acme[/x] [bold]model[1m]"


def _row(**fields) -> dict:
    row = {"name": "claude-x", "label": "x", "status": "IDLE", "model": MODEL,
           "context_pct": None, "cost_usd": None, "queue_depth": 0,
           "waiting_kind": None, "waiting_summary": None}
    row.update(fields)
    return row


@pytest.mark.parametrize("render", ["rich", "plain"])
def test_both_renderers_print_the_model_as_written(capsys, render):
    renderer = (status.render_sessions_rich if render == "rich"
                else status.render_sessions_plain)
    renderer([_row()])
    out = capsys.readouterr().out
    assert MODEL in out


@pytest.mark.parametrize("render", ["rich", "plain"])
def test_a_model_next_to_a_waiting_text_keeps_both(capsys, render):
    renderer = (status.render_sessions_rich if render == "rich"
                else status.render_sessions_plain)
    renderer([_row(status="WAITING", waiting_kind="permission",
                   waiting_summary="Bash: ls")])
    out = capsys.readouterr().out
    assert "permission: Bash: ls" in out and MODEL in out
