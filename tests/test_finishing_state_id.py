"""The busy card's waiting line decides "finishing up" from the counts,
not from the words (the multilingual design, 5.4): a reworded or
translated phrase must not change which line the card shows.

The card follows the job (roadmap 8.121, design-amend-5): with the job's
agents running it says ``waiting for N agents`` (``job_wait.phrase``, the
words the bar, /status and /x use); with only the job's shells it keeps
``N shells still working``; with nothing left running, ``finishing up``.
"""

from __future__ import annotations

import time

from aipager.bot import animation, job_wait
from aipager.bot.animation import build_stream_card
from aipager.i18n import Msg, Plural
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


def test_finishing_survives_a_reworded_phrase(monkeypatch):
    monkeypatch.setattr(animation, "FINISHING", Msg("card.finishing", "wrapping up"))
    status = _status(_sess())
    assert status.startswith("🔄 **hiva** · wrapping up · ")
    assert "still working" not in status


def test_a_shell_count_that_reads_like_finishing_still_counts(monkeypatch):
    monkeypatch.setattr(animation, "SHELLS", Plural(
        "card.shells", one="finishing up {n}", other="finishing up {n}"))
    sess = _sess()
    sess.bg_shells["s1"] = {"label": "npm test", "in_job": True}
    assert "· finishing up 1 still working · " in _status(sess)


def test_an_agent_count_that_reads_like_finishing_still_counts(monkeypatch):
    monkeypatch.setattr(job_wait, "WAIT_AGENTS", Plural(
        "jobwait.agents", one="finishing up {n}", other="finishing up {n}"))
    sess = _sess()
    sess.active_subagents["a1"] = {"type": "Explore"}
    assert "· finishing up 1 · " in _status(sess)


def test_the_real_phrases():
    sess = _sess()
    sess.job_grace_until = 10**12
    assert "🔄 **hiva** · finishing up · " in _status(sess)
    sess.bg_shells["s2"] = {"label": "npm run dev", "in_job": False}
    assert "🔄 **hiva** · finishing up · " in _status(sess), "a shell outside the job"
    sess.bg_shells["s1"] = {"label": "npm test", "in_job": True}
    assert "🔄 **hiva** · 1 shell still working · " in _status(sess)
    sess.active_subagents["a1"] = {"type": "Explore"}
    assert "🔄 **hiva** · waiting for 1 agent · " in _status(sess)
    del sess.bg_shells["s1"]
    assert "🔄 **hiva** · waiting for 1 agent · " in _status(sess)


def test_the_card_frame_follows_the_job_not_the_shown_state():
    """The card's frame (what may bypass the age decay) stays "waiting"
    while the job holds only a shell, or only its grace window: the card
    still shows those, though every other surface reads idle."""
    from aipager.bot.animation import AnimationMixin
    sess = _sess()
    sess.status = Status.IDLE
    sess.bg_shells["s1"] = {"label": "npm test", "in_job": True}
    assert sess.shown_state().state == "idle"
    assert AnimationMixin._card_frame_state(sess)[1] is True
    del sess.bg_shells["s1"]
    sess.job_grace_until = time.monotonic() + 60
    assert AnimationMixin._card_frame_state(sess)[1] is True
    sess.job_grace_until = 0.0
    assert AnimationMixin._card_frame_state(sess)[1] is False
