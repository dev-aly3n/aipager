"""design.md success criteria:
- "Tapping Stop while a session is in the waiting-on-background-work
  state succeeds (today it is refused with 'is not busy')."
- The waiting-card header contract, pinned directly against the pure
  ``animation.build_stream_card(..., waiting=True)`` function
  entrypoints.md specifies exactly.
"""

from __future__ import annotations

import re
import time
from unittest.mock import AsyncMock, MagicMock

from aipager.bot import animation
from aipager.state import Status, TrackedSession


def _waiting_sess(*, n_agents=1, types=("Explore",), elapsed_s=260):
    sess = TrackedSession(name="claude-hiva", label="hiva", status=Status.IDLE)
    sess.busy_started_at = time.monotonic() - elapsed_s
    agents = {}
    for i in range(n_agents):
        t = types[i % len(types)] if types else ""
        agents[f"agent-{i}"] = {"type": t, "started_at": sess.busy_started_at,
                                "history_idx": None}
    sess.active_subagents = agents
    return sess


# ---- build_stream_card(waiting=True): header contract ---------------------

def test_waiting_header_singular_agent_count():
    sess = _waiting_sess(n_agents=1, types=("Explore",), elapsed_s=260)
    out = animation.build_stream_card(sess, "Verb", waiting=True)
    assert "1 agent" in out
    assert "1 agents" not in out


def test_waiting_header_plural_agent_count():
    sess = _waiting_sess(n_agents=2, types=("Explore", "Plan"), elapsed_s=260)
    out = animation.build_stream_card(sess, "Verb", waiting=True)
    assert "2 agents" in out


def test_waiting_header_never_says_finished():
    sess = _waiting_sess()
    out = animation.build_stream_card(sess, "Verb", waiting=True)
    assert "Finished" not in out
    assert "waiting on background work" in out


def test_waiting_header_shows_types_for_one_to_three_distinct():
    sess = _waiting_sess(n_agents=2, types=("Explore", "Plan"), elapsed_s=10)
    out = animation.build_stream_card(sess, "Verb", waiting=True)
    assert "Explore" in out
    assert "Plan" in out


def test_waiting_header_omits_types_beyond_three_distinct():
    sess = _waiting_sess(n_agents=4, types=("A", "B", "C", "D"), elapsed_s=10)
    out = animation.build_stream_card(sess, "Verb", waiting=True)
    # The parenthetical type list must be absent once distinct types > 3 —
    # none of the fabricated single-letter type names may appear bracketed.
    assert not re.search(r"\(\s*[ABCD](,\s*[ABCD]){1,3}\s*\)", out)


def test_waiting_header_elapsed_under_60s_uses_seconds_form():
    sess = _waiting_sess(elapsed_s=45)
    out = animation.build_stream_card(sess, "Verb", waiting=True)
    assert re.search(r"\b4[0-9]s\b", out), out


def test_waiting_header_elapsed_at_60s_boundary_uses_minutes_form():
    sess = _waiting_sess(elapsed_s=60)
    out = animation.build_stream_card(sess, "Verb", waiting=True)
    assert re.search(r"\b1m\b", out), out


def test_waiting_header_elapsed_just_under_60s_boundary_stays_seconds_form():
    sess = _waiting_sess(elapsed_s=59)
    out = animation.build_stream_card(sess, "Verb", waiting=True)
    assert "1m" not in out
    assert re.search(r"\b59s\b", out), out


def test_waiting_header_omits_elapsed_when_busy_started_at_falsy():
    sess = _waiting_sess()
    sess.busy_started_at = 0.0
    out = animation.build_stream_card(sess, "Verb", waiting=True)
    assert "waiting on background work" in out


def test_non_waiting_render_has_no_waiting_text():
    """Equivalence control: the default (waiting=False) render of the
    exact same session must not carry the waiting-card language at all —
    proves the two frames are genuinely distinct, not always-on text."""
    sess = _waiting_sess()
    out = animation.build_stream_card(sess, "Verb", waiting=False)
    assert "waiting on background work" not in out


# ---- Stop tapped while the session sits in the waiting state -------------

def _wire_stop(bot):
    bot._stop_animation = MagicMock()
    bot._edit_busy_raw = AsyncMock()
    return bot


def test_stop_succeeds_while_waiting_on_background_work(mk_bot, run_async, monkeypatch):
    bot = _wire_stop(mk_bot())
    sess = TrackedSession(name="claude-hiva", label="hiva", status=Status.IDLE)
    sess.active_subagents = {"a1": {"type": "Explore", "started_at": 0.0,
                                    "history_idx": None}}
    bot.registry._sessions["claude-hiva"] = sess
    assert sess.job_background_open() is True
    monkeypatch.setattr("aipager.dtach.inject.send_keys",
                        AsyncMock(return_value=True))
    async def _no_sleep(_): pass
    monkeypatch.setattr("aipager.bot.session_ops.asyncio.sleep", _no_sleep)

    outcome = run_async(bot._stop_session_core(sess))

    assert outcome.ok is True, (
        f"Stop was refused while the session was in the waiting-on-"
        f"background-work state: {outcome!r}")


def test_stop_clears_active_subagents_so_the_job_is_genuinely_over(
        mk_bot, run_async, monkeypatch):
    """The other half of the same criterion: after Stop succeeds, the
    predicate must go False too — Stop must genuinely end the job, not
    merely be ALLOWED to run while a phantom job-open predicate lingers."""
    bot = _wire_stop(mk_bot())
    sess = TrackedSession(name="claude-hiva", label="hiva", status=Status.IDLE)
    sess.active_subagents = {"a1": {"type": "Explore", "started_at": 0.0,
                                    "history_idx": None}}
    bot.registry._sessions["claude-hiva"] = sess
    monkeypatch.setattr("aipager.dtach.inject.send_keys",
                        AsyncMock(return_value=True))
    async def _no_sleep(_): pass
    monkeypatch.setattr("aipager.bot.session_ops.asyncio.sleep", _no_sleep)

    run_async(bot._stop_session_core(sess))

    assert sess.active_subagents == {}
    assert sess.job_background_open() is False


def test_stop_still_refused_for_a_genuinely_idle_session_with_no_agents(
        mk_bot, run_async, monkeypatch):
    """Regression guard on the widened gate: it must widen EXACTLY to
    job_background_open(), not loosen into 'always allow Stop from
    IDLE' — a plain idle session with no open agents must still be
    refused, matching today's behaviour."""
    bot = _wire_stop(mk_bot())
    sess = TrackedSession(name="claude-hiva", label="hiva", status=Status.IDLE)
    sess.active_subagents = {}
    bot.registry._sessions["claude-hiva"] = sess

    async def _boom(*a, **kw):
        raise AssertionError("inject.send_keys must not run for a non-busy, "
                             "non-job-open session")
    monkeypatch.setattr("aipager.dtach.inject.send_keys", _boom)

    outcome = run_async(bot._stop_session_core(sess))

    assert outcome.ok is False
