"""design.md's own explicitly-requested guarantee (Algorithm section,
"Property test (this is the guarantee, not a nice-to-have)"): for a wide,
randomly-generated variety of card shapes, the REAL rendered string never
exceeds 8,800 characters (Python ``len()``, code points) or 32,768 UTF-8
bytes, and always ends with the status line as its last line — "a handful
of fixed hand-picked cases is not broad enough to serve as the
guarantee."

This is acceptance criterion (a) from the ship brief: the rendered card
is MEASURED, not estimated, against both ceilings across a variety of
shapes. A fixed-seed ``random``-driven generator sweeps prose length, row
counts, section count (via commentary anchors), live/settled agent
presence and tool-call counts (0 through double-digit), emoji/multi-byte
density, and busy/waiting/final frames — one pytest case per seed for
granular failure reporting, exactly as design.md's algorithm section
asks for ("a parametrized sweep of at least a few dozen varied shapes").

Fixture construction is grounded entirely in entrypoints.md's documented
``TrackedSession`` fields, empirically probed through
``build_stream_card_ex`` itself (the public entry point) to learn the
exact wiring a live/settled agent row needs to be recognised — no
internal source was read to derive this shape.
"""

from __future__ import annotations

import random
import re
import time

import pytest

from aipager.bot.animation import build_stream_card_ex
from aipager.state import Status, TrackedSession

MARK = "\U0001f916"  # 🤖
CARD_CHAR_BUDGET = 8_800
RICH_LIMIT_BYTES = 32_768
SIGIL = {"busy": "⏳", "waiting": "\U0001f504", "final": "✅"}  # ⏳ 🔄 ✅

N_SEEDS = 40


def _gen_session(seed):
    rng = random.Random(seed)
    label = "sw"
    sess = TrackedSession(name=f"claude-{label}", label=label, status=Status.BUSY)
    sess.busy_started_at = time.monotonic() - rng.uniform(1, 4000)

    n_rows = rng.choice(
        [0, 1, 2, 3, 4, 8, rng.randint(1, 60), rng.randint(60, 450)]
    )
    dense = rng.random() < 0.35
    rows = []
    for i in range(n_rows):
        if dense:
            filler = "\U0001f525" * rng.randint(0, 45)  # 4-byte-UTF-8 emoji
        else:
            filler = "z" * rng.randint(0, 260)
        text = f"Bash: step-{i} {filler}"
        done = rng.choice([True, True, False, "failed"])
        rows.append((text, done))
    sess.tool_history = rows

    n_comment = rng.choice([0, 0, 1, 2, 3, 4])
    if n_rows > 0 and n_comment:
        idxs = sorted(rng.sample(range(n_rows), min(n_comment, n_rows)))
        sess.stream_commentary = [
            (idx, f"Commentary at {idx}: " + ("prose " * rng.randint(1, 30)))
            for idx in idxs
        ]

    if rng.random() < 0.4:
        idx = len(sess.tool_history)
        n_tools = rng.choice([0, 1, 2, 3, 4, 9])
        sess.tool_history.append((f"{MARK} explorer", False))
        sess.active_subagents["live-1"] = {
            "type": "explorer",
            "activity": "Bash: probing",
            "started_at": time.monotonic() - rng.uniform(1, 900),
            "history_idx": idx,
            "tools": [f"tool-{t}" for t in range(n_tools)],
        }

    if rng.random() < 0.4:
        idx = len(sess.tool_history)
        n_tools = rng.choice([0, 1, 2, 3, 5, 12])
        sess.tool_history.append((f"{MARK} archivist", True))
        sess.finished_subagents.append({
            "type": "archivist",
            "started_at": 0.0,
            "elapsed": rng.uniform(1, 900),
            "tool_count": n_tools,
            "tools": [f"tool-{t}" for t in range(n_tools)],
            "history_idx": idx,
        })

    frame = rng.choice(["busy", "waiting", "final"])
    kwargs = {"final": frame == "final", "waiting": frame == "waiting"}
    return sess, kwargs, frame


@pytest.mark.parametrize("seed", range(N_SEEDS))
def test_generated_shape_never_exceeds_the_char_budget(seed):
    sess, kwargs, _frame = _gen_session(seed)
    card, _truncated = build_stream_card_ex(sess, "Working", **kwargs)
    assert len(card) <= CARD_CHAR_BUDGET


@pytest.mark.parametrize("seed", range(N_SEEDS))
def test_generated_shape_never_exceeds_the_byte_ceiling(seed):
    sess, kwargs, _frame = _gen_session(seed)
    card, _truncated = build_stream_card_ex(sess, "Working", **kwargs)
    assert len(card.encode("utf-8")) <= RICH_LIMIT_BYTES


@pytest.mark.parametrize("seed", range(N_SEEDS))
def test_generated_shape_ends_with_its_own_status_line(seed):
    sess, kwargs, frame = _gen_session(seed)
    card, _truncated = build_stream_card_ex(sess, "Working", **kwargs)
    last_line = card.rstrip("\n").splitlines()[-1]
    assert last_line.startswith(f"{SIGIL[frame]} **sw**")


@pytest.mark.parametrize("seed", range(N_SEEDS))
def test_generated_shape_status_line_is_outside_every_block(seed):
    """The last line must never itself be part of an open <details> span —
    i.e. every <details> opened in the card is also closed before the
    final line, and the final line contains no block markup."""
    sess, kwargs, _frame = _gen_session(seed)
    card, _truncated = build_stream_card_ex(sess, "Working", **kwargs)
    body = card.rstrip("\n")
    last_line = body.splitlines()[-1]
    before_status = body[: -len(last_line)]
    assert before_status.count("<details") == before_status.count("</details>")
    assert "<details" not in last_line
    assert "</details>" not in last_line
    assert "<summary" not in last_line


@pytest.mark.parametrize("seed", range(N_SEEDS))
def test_generated_shape_never_carries_an_open_attribute(seed):
    sess, kwargs, _frame = _gen_session(seed)
    card, _truncated = build_stream_card_ex(sess, "Working", **kwargs)
    assert not re.search(r"<details\s+open", card)
    assert "<details open>" not in card
    for tag in re.findall(r"<details[^>]*>", card):
        assert tag == "<details>", tag


@pytest.mark.parametrize("seed", range(N_SEEDS))
def test_generated_shape_truncated_flag_is_a_bool_consistent_with_dropping(seed):
    """error-guessing: `truncated` must be a real bool (not e.g. None or a
    truthy int) and, per design.md, can only be True when the card is at
    (or forced against) the ceiling — a tiny/empty shape must never report
    truncation."""
    sess, kwargs, _frame = _gen_session(seed)
    card, truncated = build_stream_card_ex(sess, "Working", **kwargs)
    assert truncated is True or truncated is False
    if len(sess.tool_history) <= 2 and not sess.stream_commentary:
        assert truncated is False
