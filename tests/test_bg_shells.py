"""The background-shell text helpers (``aipager.bg_shells``) and the answer
block's shell wording (``aipager.bot.agents_line``). Pure functions."""

from __future__ import annotations

import pytest

from aipager import bg_shells
from aipager.bot import agents_line


def _block(task_id, status=None, summary="", result=None):
    parts = ["<task-notification>", f"<task-id>{task_id}</task-id>"]
    if status is not None:
        parts.append(f"<status>{status}</status>")
    if summary:
        parts.append(f"<summary>{summary}</summary>")
    if result is not None:
        parts.append(f"<result>{result}</result>")
    parts.append("</task-notification>")
    return "\n".join(parts)


# ── shell_label ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("tool_input, label", [
    ({"description": "run the tests", "command": "pytest"}, "run the tests"),
    ({"description": "  spaced   out  ", "command": "x"}, "spaced out"),
    ({"description": "", "command": "pytest -q\necho two"}, "pytest -q"),
    ({"command": "  \n  make   all \n"}, "make all"),
    ({}, "shell"),
    ({"description": None, "command": None}, "shell"),
    ("not a dict", "shell"),
])
def test_shell_label(tool_input, label):
    assert bg_shells.shell_label(tool_input) == label


def test_shell_label_is_cut_with_an_ellipsis():
    label = bg_shells.shell_label({"description": "x" * 100})
    assert len(label) == bg_shells.LABEL_MAX
    assert label.endswith("…")
    assert bg_shells.shell_label({"description": "y" * 60}) == "y" * 60


# ── parse_shell_ends ────────────────────────────────────────────────────────

def test_parse_each_status_and_exit_code():
    text = (_block("b1", "completed",
                   'Background command "a" completed (exit code 0)')
            + _block("b2", "failed",
                     'Background command "b" failed with exit code 144')
            + _block("b3", "killed", 'Background command "c" was stopped'))
    assert bg_shells.parse_shell_ends(text) == [
        ("b1", "completed", 0), ("b2", "failed", 144), ("b3", "killed", None)]


def test_parse_ignores_blocks_without_an_ending_status():
    text = (_block("b1", None, 'Monitor "m" event: tick')
            + _block("b2", "running")
            + _block("b3", "completed"))
    assert bg_shells.parse_shell_ends(text) == [("b3", "completed", None)]


def test_parse_reads_nothing_inside_a_result():
    quoted = _block("b9", "completed")
    text = _block("a1", "completed", 'Agent "x" completed',
                  result=f"it said {quoted}")
    assert bg_shells.parse_shell_ends(text) == [("a1", "completed", None)]
    open_result = ("<task-notification><task-id>b9</task-id>"
                   "<result>tail <status>completed</status>")
    assert bg_shells.parse_shell_ends(open_result) == []


@pytest.mark.parametrize("text", ["", "plain prompt",
                                  "<task-id>b1</task-id><status>completed"
                                  "</status>"])
def test_parse_without_a_notification(text):
    assert bg_shells.parse_shell_ends(text) == []


# ── rows ────────────────────────────────────────────────────────────────────

def test_live_rows():
    assert bg_shells.live_row("sleep 90", "45s") == "shell: sleep 90 (45s)"
    assert bg_shells.final_live_row("sleep 90") == \
        "shell: sleep 90 (still running)"


@pytest.mark.parametrize("outcome, exit_code, row, mark", [
    ("completed", 0, "shell: t - done (6:20)", True),
    ("failed", 1, "shell: t - failed (exit 1)", "failed"),
    ("failed", None, "shell: t - failed (6:20)", "failed"),
    ("killed", None, "shell: t - stopped (6:20)", "stopped"),
    ("stopped", None, "shell: t - stopped (6:20)", "stopped"),
    ("lost", None, "shell: t - no end seen (6:20)", "stopped"),
])
def test_settled_rows(outcome, exit_code, row, mark):
    assert bg_shells.settled_row("t", outcome, 380, exit_code) == (row, mark)


@pytest.mark.parametrize("seconds, text", [
    (-3, "0:00"), (45, "0:45"), (60, "1:00"), (3599, "59:59"), (3780, "1:03:00")])
def test_duration(seconds, text):
    assert bg_shells.duration(seconds) == text
    assert agents_line.duration(seconds) == text


# ── the answer's status block (shells) ─────────────────────────────────────

@pytest.mark.parametrize("view, text", [
    (agents_line.BlockView(shells_running=1), "🖥 1 shell running"),
    (agents_line.BlockView(shells_running=2), "🖥 2 shells running"),
    (agents_line.BlockView(shells_finished=1, shells_finished_seconds=120),
     "1 shell finished (2:00)"),
    (agents_line.BlockView(shells_stopped=1, shells_stopped_seconds=5),
     "1 shell stopped (0:05)"),
    (agents_line.BlockView(shells_finished=1, shells_finished_seconds=120,
                           shells_stopped=2, shells_stopped_seconds=5),
     "1 shell finished (2:00) · 2 shells stopped (0:05)"),
    (agents_line.BlockView(shells_running=1, shells_finished=3),
     "🖥 1 shell running"),
], ids=["one-running", "two-running", "finished", "stopped",
        "finished-and-stopped", "running-wins"])
def test_the_block_counts_shells(view, text):
    assert agents_line.render_block(view) == text
