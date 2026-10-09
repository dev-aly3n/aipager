"""A separate permission prompt restored after a restart (G74): the marks
aipager itself put in its right-to-left text (U+200F, U+2068, U+2069) are
dropped instead of being shown as markers, and every other direction
control in the saved text is still revealed."""

from __future__ import annotations

import time

from aipager import bidi_guard
from aipager.state import Status, TrackedSession

RLM, FSI, PDI, RLO, LRI = "\u200f", "\u2068", "\u2069", "\u202e", "\u2066"


def _record(text, summary):
    perm = {"tool_name": "Bash", "tool_summary": summary, "detail": "",
            "always_available": False, "standing_rule_suggestion": None,
            "hook_reply": None, "question": None}
    return {"kind": "separate", "card_msg_id": 77, "msg_id": 88, "chat_id": -100,
            "text": text, "summary": summary, "tool_use_id": "toolu_1",
            "shown_wall": time.time() - 5, "perm": perm}


def _restore(mk_bot, record):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.INTERACTIVE)
    sess.busy_msg_id = 42
    bot._rebuild_open_prompt(sess, record, time.monotonic())
    return sess.pending_prompt_msg


def test_own_marks_do_not_become_markers(mk_bot):
    text = (RLM + "🔐 " + FSI + "<b>jim</b>" + PDI + " مجوز لازم است\n"
            "<code>Bash: ls</code>")
    msg = _restore(mk_bot, _record(text, "Bash: ls"))
    assert "⟨U+" not in msg["text"]
    assert msg["text"] == "🔐 <b>jim</b> مجوز لازم است\n<code>Bash: ls</code>"


def test_a_foreign_override_is_still_revealed(mk_bot):
    text = RLM + "🔐 <b>jim</b>\n<code>Bash: tidy " + RLO + "up " + LRI + "x</code>"
    msg = _restore(mk_bot, _record(text, f"Bash: tidy {RLO}up"))
    assert RLO not in msg["text"] and "⟨U+202E⟩" in msg["text"]
    assert "⟨U+2066⟩" in msg["text"] and "⟨U+200F⟩" not in msg["text"]
    assert msg["summary"] == "Bash: tidy ⟨U+202E⟩up"


def test_strip_own_marks_touches_only_its_three():
    assert bidi_guard.strip_own_marks(RLM + FSI + "a" + PDI + RLO + "\u200e\u061c") == (
        "a" + RLO + "\u200e\u061c")
    assert bidi_guard.strip_own_marks(None) is None
    assert bidi_guard.strip_own_marks("می‌خواهم") == "می‌خواهم"
