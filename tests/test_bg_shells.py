"""The background-shell text helpers (``aipager.bg_shells``) and the answer
line's shell wording (``aipager.bot.agents_line``). Pure functions."""

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
    ("completed", 0, "shell: t - done (6m)", True),
    ("failed", 1, "shell: t - failed (exit 1)", "failed"),
    ("failed", None, "shell: t - failed (6m)", "failed"),
    ("killed", None, "shell: t - stopped (6m)", "stopped"),
    ("stopped", None, "shell: t - stopped (6m)", "stopped"),
    ("lost", None, "shell: t - no end seen (6m)", "stopped"),
])
def test_settled_rows(outcome, exit_code, row, mark):
    assert bg_shells.settled_row("t", outcome, 380, exit_code) == (row, mark)


@pytest.mark.parametrize("seconds, text", [
    (-3, "0s"), (45, "45s"), (60, "1m"), (3599, "59m"), (3780, "1h 3m")])
def test_duration(seconds, text):
    assert bg_shells.duration(seconds) == text
    assert agents_line.duration(seconds) == text


# ── the answer line ─────────────────────────────────────────────────────────

def test_running_line_without_kinds_is_unchanged():
    assert agents_line.running_line(["a", "b"]) == (
        "⏳ 2 agents still running (a, b) - results will follow here")


@pytest.mark.parametrize("kinds, count", [
    (["shell"], "1 shell"),
    (["shell", "shell"], "2 shells"),
    (["agent", "shell"], "1 agent, 1 shell"),
    (["agent", "agent", "shell"], "2 agents, 1 shell"),
])
def test_running_line_counts_shells(kinds, count):
    labels = [f"l{i}" for i in range(len(kinds))]
    line = agents_line.running_line(labels, kinds=kinds)
    assert line.startswith(f"⏳ {count} still running (")
    assert line.endswith(") - results will follow here")


def test_done_line_for_shells():
    assert agents_line.done_line(["sleep 90"], 120, kinds=["shell"]) == \
        "✅ shell: sleep 90 - done (2m)"
    assert agents_line.done_line(["x"], 120, kinds=["agent"]) == \
        "✅ x done (2m)"
    assert agents_line.done_line(["a", "b"], 120,
                                 kinds=["agent", "shell"]) == \
        "✅ 1 agent, 1 shell done (2m)"
    assert agents_line.done_line(["a", "b"], 120) == "✅ 2 agents done (2m)"
    assert agents_line.done_line(["my_job"], 5, kinds=["shell"],
                                 markdown=True) == \
        "✅ shell: my\\_job - done (5s)"
