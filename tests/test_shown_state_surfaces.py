"""Roadmap 8.121: every chat surface shows a session waiting for its own
background agents the same way, from the same count.

One idle session whose job runs 2 agents reads "waiting for 2 agents"
with 🔄 on the pinned bar, in /status, in the /x reply and on the busy
card, and is counted with working when several sessions share the bar.
Agents only (design-amend-5 A): a job shell never joins the phrase, and a
session whose job holds only shells, or nothing left running, shows idle
everywhere but on the busy card, which follows the job and keeps main's
words ("1 shell still working", "finishing up"). Work outside the job
keeps the 8.41 "(idle, N … running)" note.
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


# ---- one session, every surface, the same count ----------------------------------------

def test_every_surface_names_the_same_agent_count(mk_bot, bar_chat):
    bot = mk_bot()
    sess = _sess(bot, agents=2, shells=1, outside_shells=1)
    phrase = "waiting for 2 agents"
    assert _bar(bot) == f"🔄 x1 ({phrase})"
    assert f"<b>x1</b> · 🔄 {phrase}" in _status_block(bot, sess)
    text, _kb = bot._render_switch_reply(CHAT, sess)
    assert f"· 🔄 {phrase} ·" in text.splitlines()[0]
    assert _card_line(sess).startswith(f"🔄 **x1** · {phrase} · ")
    row = app_sessions.session_summary(sess, time.monotonic())
    assert (row["status"], row["job_agents"]) == ("tasks", 2)
    assert "job_shells" not in row


@pytest.mark.parametrize("agents, shells, phrase", [
    (1, 0, "waiting for 1 agent"),
    (3, 0, "waiting for 3 agents"),
    (1, 2, "waiting for 1 agent"),
], ids=["agent", "agents", "agent-and-shells"])
def test_each_count_reads_as_its_own_sentence(mk_bot, bar_chat, agents, shells, phrase):
    bot = mk_bot()
    sess = _sess(bot, agents=agents, shells=shells)
    assert _bar(bot) == f"🔄 x1 ({phrase})"
    assert _card_line(sess).startswith(f"🔄 **x1** · {phrase} · ")


@pytest.mark.parametrize("shells, card", [
    (1, "🔄 **x1** · 1 shell still working · "),
    (2, "🔄 **x1** · 2 shells still working · "),
], ids=["shell", "shells"])
def test_a_job_with_only_shells_is_idle_but_its_card_still_works(
        mk_bot, bar_chat, shells, card):
    """design-amend-5 A: a job shell alone is idle on every derived
    surface, with main's running note; the card keeps main's line."""
    bot = mk_bot()
    sess = _sess(bot, shells=shells)
    noun = "shell" if shells == 1 else "shells"
    assert _bar(bot) == f"💤 x1 (idle, {shells} {noun} running)"
    assert "<b>x1</b> · 💤 idle" in _status_block(bot, sess)
    text, _kb = bot._render_switch_reply(CHAT, sess)
    assert "· 💤 idle ·" in text.splitlines()[0]
    row = app_sessions.session_summary(sess, time.monotonic())
    assert (row["status"], row["job_agents"]) == ("idle", 0)
    assert _card_line(sess).startswith(card)


def test_the_grace_window_is_idle_but_its_card_is_finishing_up(mk_bot, bar_chat):
    """Guard 3 on the surfaces: the job is open with nothing running."""
    bot = mk_bot()
    sess = _sess(bot)
    sess.job_grace_until = time.monotonic() + 60
    assert _bar(bot) == "💤 x1 (idle)"
    assert "<b>x1</b> · 💤 idle" in _status_block(bot, sess)
    assert _card_line(sess).startswith("🔄 **x1** · finishing up · ")
    row = app_sessions.session_summary(sess, time.monotonic())
    assert (row["status"], row["job_agents"]) == ("idle", 0)


def test_the_phrase_names_the_agents():
    from aipager import i18n
    en = i18n.get("en")
    assert job_wait.phrase(en, 1) == "waiting for 1 agent"
    assert job_wait.phrase(en, 2) == "waiting for 2 agents"


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


def test_a_waiting_row_names_only_the_job_agents(mk_bot, bar_chat):
    """Guard 5: agents and shells outside the job, and the job's own
    shell, do not join the row."""
    bot = mk_bot()
    _sess(bot, agents=1, shells=1, outside_agents=2, outside_shells=1)
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

def test_status_names_the_agent_count_once(mk_bot):
    """Guard 11: the head says how many agents the session waits for; the
    details line does not count the agents again (from another source),
    and still counts the shells running, as it does for an idle row."""
    bot = mk_bot()
    sess = _sess(bot, agents=2, outside_shells=3)
    block = _status_block(bot, sess)
    head, *rest = block.split("\n")
    assert head.endswith("<b>x1</b> · 🔄 waiting for 2 agents")
    assert "agent" not in "".join(rest)
    assert "3 shells" in "".join(rest)


def test_status_offers_stop_while_it_waits(mk_bot):
    bot = mk_bot()
    sess = _sess(bot, agents=1)
    assert sess.can_be_stopped()
    _text, kb = bot._render_status_list(CHAT)
    callbacks = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert any("stop" in (cb or "") for cb in callbacks)


# ---- a translated chat ------------------------------------------------------------------

def test_a_translated_chat_keeps_the_glyph_and_the_count(mk_bot, bar_chat):
    from aipager import i18n
    ru = i18n.get("ru")
    phrase = job_wait.phrase(ru, 2)
    assert "2" in phrase and "waiting" not in phrase
    pseudo = i18n.get("qps")
    marked = job_wait.phrase(pseudo, 2)
    assert "⟦" in marked and "2" in marked
