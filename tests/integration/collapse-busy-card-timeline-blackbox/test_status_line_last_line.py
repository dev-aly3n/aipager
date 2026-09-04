"""Acceptance criterion (b) from the ship brief: the status line is the
LAST line of the card on busy, waiting AND final frames, with any number
of `<details>` folds preceding it (design.md Rule 8: "Status line last,
outside every block, on busy, waiting and final").

Each scenario below is driven with the SAME session shape across all
three frames (`build_stream_card_ex(sess, verb)`, `..., final=True`,
`..., waiting=True`) to isolate the frame dimension, crossed with the
fold-count dimension: zero folds, exactly one fold, and several
independent folds (commentary + an older run + a live-agent nested fold
+ a settled-agent nested fold, all in the same card).
"""

from __future__ import annotations

import time

from aipager.bot.animation import build_stream_card_ex
from aipager.state import Status, TrackedSession

MARK = "\U0001f916"  # 🤖
FRAMES = [
    ({}, "⏳"),  # busy: ⏳
    ({"final": True}, "✅"),  # final: ✅
    ({"waiting": True}, "\U0001f504"),  # waiting: 🔄
]


def _sess(label="jim"):
    sess = TrackedSession(name=f"claude-{label}", label=label, status=Status.BUSY)
    sess.busy_started_at = time.monotonic() - 45
    return sess


def _last_line(card):
    return card.rstrip("\n").splitlines()[-1]


# ---- zero folds -------------------------------------------------------------

def test_zero_folds_status_line_is_last_on_every_frame():
    sess = _sess()
    sess.tool_history = [("Bash: ls", True), ("Read: /a.py", True)]
    for kwargs, sigil in FRAMES:
        card, _truncated = build_stream_card_ex(sess, "Working", **kwargs)
        assert "<details" not in card
        assert _last_line(card).startswith(f"{sigil} **jim**")


# ---- exactly one fold --------------------------------------------------------

def _sess_one_fold(label="jim"):
    sess = _sess(label)
    rows = 12
    sess.tool_history = [(f"Bash: step-{i}", True) for i in range(rows)]
    # one commentary anchor splits the timeline into an older section
    # (rows 0..5, >= _FOLD_MIN_ROWS) and a newest section (never wrapped).
    sess.stream_commentary = [(0, "Starting the trace."), (6, "Now the fix.")]
    return sess


def test_exactly_one_fold_status_line_is_last_on_every_frame():
    for kwargs, sigil in FRAMES:
        sess = _sess_one_fold()
        card, _truncated = build_stream_card_ex(sess, "Working", **kwargs)
        assert card.count("<details") == 1, card
        assert _last_line(card).startswith(f"{sigil} **jim**")


def test_exactly_one_fold_status_line_text_contains_no_block_markup():
    sess = _sess_one_fold()
    card, _truncated = build_stream_card_ex(sess, "Working", final=True)
    last = _last_line(card)
    assert "<details" not in last and "</details>" not in last and "▸" not in last


# ---- several independent folds ----------------------------------------------

def _sess_several_folds(label="jim"):
    sess = _sess(label)
    rows = 20
    sess.tool_history = [(f"Bash: step-{i}", True) for i in range(rows)]
    # three commentary anchors -> (at least) two OLDER qualifying sections
    # plus the newest (never-wrapped) one.
    sess.stream_commentary = [
        (0, "Phase one."),
        (5, "Phase two."),
        (10, "Phase three."),
        (15, "Phase four."),
    ]
    live_idx = len(sess.tool_history)
    sess.tool_history.append((f"{MARK} explorer", False))
    sess.active_subagents["live-1"] = {
        "type": "explorer",
        "activity": "Bash: probing",
        "started_at": time.monotonic() - 12,
        "history_idx": live_idx,
        "tools": ["Grep: a", "Grep: b", "Grep: c"],
    }
    settled_idx = len(sess.tool_history)
    sess.tool_history.append((f"{MARK} archivist", True))
    sess.finished_subagents.append({
        "type": "archivist",
        "started_at": 0.0,
        "elapsed": 30.0,
        "tool_count": 4,
        "tools": ["Read: x", "Read: y", "Read: z", "Read: w"],
        "history_idx": settled_idx,
    })
    return sess


def test_several_folds_status_line_is_last_on_every_frame():
    for kwargs, sigil in FRAMES:
        sess = _sess_several_folds()
        card, _truncated = build_stream_card_ex(sess, "Working", **kwargs)
        n_blocks = card.count("<details")
        assert n_blocks >= 4, card  # 2 older runs + live nest + settled nest
        assert card.count("<details") == card.count("</details>")
        assert _last_line(card).startswith(f"{sigil} **jim**")


def test_several_folds_no_block_appears_after_the_status_line():
    sess = _sess_several_folds()
    card, _truncated = build_stream_card_ex(sess, "Working")
    last = _last_line(card)
    idx = card.rindex(last)
    tail = card[idx + len(last):]
    assert "<details" not in tail
    assert "</details>" not in tail


def test_several_folds_each_summary_matches_its_own_block_only():
    """Rule 2: several blocks per card is normal, one global block is
    explicitly rejected — each block's own `▸ N tool call(s)` count must
    match only what is physically inside THAT block, not an aggregate."""
    import re

    sess = _sess_several_folds()
    card, _truncated = build_stream_card_ex(sess, "Working")
    blocks = re.findall(
        r"<details><summary>▸ (\d+) tool calls?</summary>\n\n(.*?)\n\n</details>",
        card, re.DOTALL,
    )
    assert len(blocks) >= 4, card
    for claimed, body in blocks:
        rows = [r for r in body.split("\n\n") if r.strip()]
        assert len(rows) == int(claimed), (claimed, body)
