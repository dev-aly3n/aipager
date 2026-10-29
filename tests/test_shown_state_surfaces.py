"""Roadmap 8.121: every chat surface shows a session waiting for its own
background agents or shells the same way, from the same counts.

One idle session whose job runs 2 agents and 1 shell reads "waiting for 2
agents and 1 shell" with 🔄 on the pinned bar, in /status, in the /x reply
and on the busy card, and is counted with working when several sessions
share the bar. Work outside the job (a shell a later turn left out, an
agent restored without a job) keeps the 8.41 "(idle, N … running)" note.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from aipager.bot import job_wait
from aipager.bot.animation import build_stream_card
from aipager.miniapp import sessions as app_sessions
from aipager.state import Status, TrackedSession
from tests.footer_text import as_md

CHAT = 555


@pytest.fixture
def bar_chat(monkeypatch):
    monkeypatch.setattr("aipager.bot.dashboard.CHAT_ID", str(CHAT))


def _sess(bot, label="x1", status=Status.IDLE, *, agents=0, shells=0,
          outside_shells=0, outside_agents=0) -> TrackedSession:
    sess = bot.registry.get_or_create(f"claude-{label}")
    sess.label = label
    sess.status = status
    sess.scope_chat_id = CHAT
    sess.scope_kind = "dm"
    now = time.monotonic()
    sess.busy_started_at = now - 30
    for i in range(agents):
        sess.active_subagents[f"a{i}"] = {"type": "Explore", "started_at": now}
        sess.bg_agents[f"a{i}"] = {"type": "Explore", "started_at": now}
    for i in range(outside_agents):
        sess.bg_agents[f"o{i}"] = {"type": "Plan", "started_at": now}
    for i in range(shells):
        sess.bg_shells[f"s{i}"] = {"label": "npm test", "in_job": True, "started_at": now}
    for i in range(outside_shells):
        sess.bg_shells[f"d{i}"] = {"label": "npm run dev", "in_job": False,
                                   "started_at": now}
    return sess


def _bar(bot) -> str:
    return bot._render_pinned(CHAT)[0]


def _status_block(bot, sess) -> str:
    bot._read_status_file = MagicMock(return_value=None)
    return bot._status_block(sess, None)


def _card_line(sess) -> str:
    return as_md(build_stream_card(sess, "Working", waiting=True).splitlines()[-1])


# ---- one session, every surface, the same counts ----------------------------------------

def test_every_surface_names_the_same_job_counts(mk_bot, bar_chat):
    bot = mk_bot()
    sess = _sess(bot, agents=2, shells=1, outside_shells=1)
    phrase = "waiting for 2 agents and 1 shell"
    assert _bar(bot) == f"🔄 x1 ({phrase})"
    assert f"<b>x1</b> · 🔄 {phrase}" in _status_block(bot, sess)
    text, _kb = bot._render_switch_reply(CHAT, sess)
    assert f"· 🔄 {phrase} ·" in text.splitlines()[0]
    assert _card_line(sess).startswith(f"🔄 **x1** · {phrase} · ")
    row = app_sessions.session_summary(sess, time.monotonic())
    assert (row["status"], row["job_agents"], row["job_shells"]) == ("tasks", 2, 1)


@pytest.mark.parametrize("agents, shells, phrase", [
    (1, 0, "waiting for 1 agent"),
    (3, 0, "waiting for 3 agents"),
    (0, 1, "waiting for 1 shell"),
    (0, 2, "waiting for 2 shells"),
    (1, 2, "waiting for 1 agent and 2 shells"),
], ids=["agent", "agents", "shell", "shells", "both"])
def test_each_count_reads_as_its_own_sentence(mk_bot, bar_chat, agents, shells, phrase):
    bot = mk_bot()
    sess = _sess(bot, agents=agents, shells=shells)
    assert _bar(bot) == f"🔄 x1 ({phrase})"
    assert _card_line(sess).startswith(f"🔄 **x1** · {phrase} · ")


def test_the_grace_window_reads_finishing_up_everywhere(mk_bot, bar_chat):
    """Guard 3 on the surfaces: the job is open with nothing running."""
    bot = mk_bot()
    sess = _sess(bot)
    sess.job_grace_until = time.monotonic() + 60
    assert _bar(bot) == "🔄 x1 (finishing up)"
    assert "<b>x1</b> · 🔄 finishing up" in _status_block(bot, sess)
    assert _card_line(sess).startswith("🔄 **x1** · finishing up · ")
    row = app_sessions.session_summary(sess, time.monotonic())
    assert (row["status"], row["job_agents"], row["job_shells"]) == ("tasks", 0, 0)


def test_the_phrase_branches_on_the_counts_only():
    from aipager import i18n
    en = i18n.get("en")
    assert job_wait.phrase(en, 0, 0) == "finishing up"
    assert job_wait.phrase(en, 2, 0) == "waiting for 2 agents"
    assert job_wait.phrase(en, 0, 1) == "waiting for 1 shell"
    assert job_wait.phrase(en, 2, 1) == "waiting for 2 agents and 1 shell"


# ---- the pinned bar -------------------------------------------------------------------

def test_the_bar_counts_a_waiting_session_as_working(mk_bot, bar_chat):
    """Guard 4: one session waiting for its agents, one idle."""
    bot = mk_bot()
    _sess(bot, "x1", agents=2)
    _sess(bot, "x2")
    assert _bar(bot) == (
        "⚙️ 1 working · 1 idle\n"
        "• <b>x1</b> (waiting for 2 agents)\n"
        "• <b>x2</b> (idle)")


def test_a_waiting_row_names_only_the_job_counts(mk_bot, bar_chat):
    """Guard 5: agents and shells outside the job do not join the row."""
    bot = mk_bot()
    _sess(bot, agents=1, outside_agents=2, outside_shells=1)
    assert _bar(bot) == "🔄 x1 (waiting for 1 agent)"


def test_work_outside_the_job_keeps_the_running_note(mk_bot, bar_chat):
    bot = mk_bot()
    _sess(bot, outside_shells=1)
    assert _bar(bot) == "💤 x1 (idle, 1 shell running)"


def test_a_working_session_stays_working(mk_bot, bar_chat):
    bot = mk_bot()
    _sess(bot, status=Status.BUSY, agents=1)
    assert _bar(bot) == "⚙️ x1 (working, 1 agent running)"


def test_a_session_that_needs_you_wins(mk_bot, bar_chat):
    bot = mk_bot()
    sess = _sess(bot, status=Status.INTERACTIVE, agents=1)
    sess.pending_permission = {"tool_summary": "Bash: ls"}
    assert _bar(bot).startswith("⏳ x1 needs you")


# ---- /status ---------------------------------------------------------------------------

def test_status_names_the_job_counts_once(mk_bot):
    """Guard 11: the head says what the session waits for; the details
    line does not count agents or shells again (from another source)."""
    bot = mk_bot()
    sess = _sess(bot, agents=2, outside_shells=3)
    block = _status_block(bot, sess)
    head, *rest = block.split("\n")
    assert head.endswith("<b>x1</b> · 🔄 waiting for 2 agents")
    assert "agent" not in "".join(rest) and "shell" not in "".join(rest)


def test_status_offers_stop_while_it_waits(mk_bot):
    bot = mk_bot()
    sess = _sess(bot, agents=1)
    assert sess.can_be_stopped()
    _text, kb = bot._render_status_list(CHAT)
    callbacks = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert any("stop" in (cb or "") for cb in callbacks)


# ---- a translated chat ------------------------------------------------------------------

def test_a_translated_chat_keeps_the_glyph_and_the_counts(mk_bot, bar_chat):
    from aipager import i18n
    ru = i18n.get("ru")
    phrase = job_wait.phrase(ru, 2, 1)
    assert "2" in phrase and "1" in phrase and "waiting" not in phrase
    pseudo = i18n.get("qps")
    marked = job_wait.phrase(pseudo, 2, 1)
    assert "⟦" in marked and "2" in marked and "1" in marked
