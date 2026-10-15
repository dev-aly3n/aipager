"""The waiting status line decides "finishing up" from a state id, not
from the words (the multilingual design, 5.4): a reworded or translated
phrase must not change which line the card shows."""

from __future__ import annotations

import time

from aipager.bot import animation
from aipager.bot.animation import build_stream_card
from aipager.state import Status, TrackedSession
from tests.footer_text import as_md


def _sess():
    s = TrackedSession(name="claude-hiva", label="hiva", status=Status.BUSY)
    s.scope_kind, s.scope_chat_id = "dm", 12345
    s.busy_started_at = time.monotonic() - 10
    s.tool_history = [("Bash: ls", True)]
    return s


def _status(sess):
    return as_md(build_stream_card(sess, "Working", waiting=True).splitlines()[-1])


def test_finishing_state_survives_a_reworded_phrase(monkeypatch):
    monkeypatch.setattr(animation, "_agent_phrase",
                        lambda sess, tr=None: (animation._FINISHING, "wrapping up"))
    status = _status(_sess())
    assert status.startswith("🔄 **hiva** · wrapping up · ")
    assert "still working" not in status


def test_a_working_phrase_that_reads_like_the_old_sentinel_still_works(monkeypatch):
    monkeypatch.setattr(animation, "_agent_phrase",
                        lambda sess, tr=None: (animation._WORKING, "finishing up"))
    assert "finishing up still working" in _status(_sess())


def test_the_real_phrases_are_unchanged():
    sess = _sess()
    sess.job_grace_until = 10**12
    assert animation._agent_phrase(sess) == ("finishing", "finishing up")
    sess.active_subagents["a1"] = {"type": "Explore"}
    assert animation._agent_phrase(sess) == ("working", "1 agent (Explore)")
    assert "🔄 **hiva** · 1 agent (Explore) still working · " in _status(sess)
