"""The waiting status line decides "finishing up" from the counts, not
from the words (the multilingual design, 5.4): a reworded or translated
phrase must not change which line the card shows. Since roadmap 8.121 the
line is ``job_wait.phrase``, the same words the bar, /status and /x use,
chosen from the job's counts (``TrackedSession.job_counts()``)."""

from __future__ import annotations

import time

from aipager.bot import job_wait
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
    monkeypatch.setattr(job_wait, "WAIT_FINISHING", Msg("jobwait.finishing", "wrapping up"))
    status = _status(_sess())
    assert status.startswith("🔄 **hiva** · wrapping up · ")


def test_a_count_phrase_that_reads_like_finishing_still_counts(monkeypatch):
    monkeypatch.setattr(job_wait, "WAIT_AGENTS", Plural(
        "jobwait.agents", one="finishing up {n}", other="finishing up {n}"))
    sess = _sess()
    sess.active_subagents["a1"] = {"type": "Explore"}
    assert "· finishing up 1 · " in _status(sess)


def test_the_real_phrases():
    sess = _sess()
    sess.job_grace_until = 10**12
    assert "🔄 **hiva** · finishing up · " in _status(sess)
    sess.active_subagents["a1"] = {"type": "Explore"}
    assert "🔄 **hiva** · waiting for 1 agent · " in _status(sess)
    sess.bg_shells["s1"] = {"label": "npm test", "in_job": True}
    assert "🔄 **hiva** · waiting for 1 agent and 1 shell · " in _status(sess)
    sess.bg_shells["s2"] = {"label": "npm run dev", "in_job": False}
    assert "🔄 **hiva** · waiting for 1 agent and 1 shell · " in _status(sess)
