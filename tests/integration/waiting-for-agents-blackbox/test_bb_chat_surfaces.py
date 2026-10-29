"""design.md success criteria on the chat surfaces: the pinned bar, /status,
the /x switch line and the busy card show the waiting state with the
glyph 🔄 and the JOB counts, count it with working, and leave every other
state (and non-job background work on an idle session) as it was.

One fixture session carries noise on purpose: work outside the job (an
agent tracked session-wide only and a shell left out of the job), so a
surface that reads the session-wide tables shows the wrong count.
"""

from __future__ import annotations

import importlib
import re
import time

import pytest

from aipager import i18n
from aipager.bot import animation
from aipager.state import Status
from tests.footer_text import unfooter

kit = importlib.import_module("tests.integration.waiting-for-agents-blackbox.conftest")
EN = i18n.get("en")
L = kit.LABEL


def _waiting(bot, **kw):
    """The criterion's session: 2 job agents, 1 job shell, plus noise."""
    params = dict(agents=2, job_shells=1, other_agents=1, other_shells=1)
    params.update(kw)
    return kit.register(bot, kit.make_session(**params))


def _plain(html: str) -> str:
    """The HTML as read (the label is bold)."""
    return re.sub(r"</?b>", "", html)


def _bar(bot, *sessions) -> str:
    return _plain(bot._render_pinned(kit.CHAT, list(sessions) or None)[0])


def _head(bot, sess) -> str:
    return _plain(bot._status_block(sess, None, tr=EN).split("\n")[0])


def _line_of(text: str, label: str) -> str:
    return next(ln for ln in text.split("\n") if re.search(rf"\b{label}\b", ln))


# ---- the pinned bar: one session -------------------------------------------------

def test_bar_single_session_names_the_job_counts(chat_bot):
    sess = _waiting(chat_bot)
    assert _bar(chat_bot, sess).split("\n")[0] == f"🔄 {L} (waiting for 2 agents and 1 shell)"


@pytest.mark.parametrize("agents, shells, words", [
    (1, 0, "waiting for 1 agent"),
    (2, 0, "waiting for 2 agents"),
    (0, 1, "waiting for 1 shell"),
    (0, 2, "waiting for 2 shells"),
], ids=["1a", "2a", "1s", "2s"])
def test_bar_single_session_plural_forms(chat_bot, agents, shells, words):
    sess = _waiting(chat_bot, agents=agents, job_shells=shells)
    assert _bar(chat_bot, sess).split("\n")[0] == f"🔄 {L} ({words})"


def test_bar_finishing_up_while_grace_runs(chat_bot):
    sess = _waiting(chat_bot, agents=0, job_shells=0)
    sess.job_grace_until = time.monotonic() + 60
    assert _bar(chat_bot, sess).split("\n")[0] == f"🔄 {L} (finishing up)"


def test_bar_tasks_row_has_no_session_wide_running_note(chat_bot):
    sess = _waiting(chat_bot, other_agents=3, other_shells=2)
    assert "running" not in _bar(chat_bot, sess)


def test_bar_back_to_idle_after_grace_expiry(chat_bot):
    sess = _waiting(chat_bot, agents=0, job_shells=0, other_agents=0, other_shells=0)
    sess.job_grace_until = time.monotonic() - 0.01
    assert _bar(chat_bot, sess).split("\n")[0] == f"💤 {L} (idle)"


# ---- the pinned bar: other states unchanged --------------------------------------

def test_bar_idle_with_a_shell_outside_the_job_keeps_the_running_note(chat_bot):
    sess = kit.register(chat_bot, kit.make_session(other_shells=1))
    assert _bar(chat_bot, sess).split("\n")[0] == f"💤 {L} (idle, 1 shell running)"


def test_bar_idle_with_agents_outside_the_job_keeps_the_running_note(chat_bot):
    sess = kit.register(chat_bot, kit.make_session(other_agents=2))
    assert _bar(chat_bot, sess).split("\n")[0] == f"💤 {L} (idle, 2 agents running)"


def test_bar_busy_with_job_agents_stays_working(chat_bot):
    sess = _waiting(chat_bot, status=Status.BUSY)
    assert _bar(chat_bot, sess).split("\n")[0].startswith(f"⚙️ {L} (working")


def test_bar_interactive_with_job_agents_is_not_waiting_for(chat_bot):
    sess = _waiting(chat_bot, status=Status.INTERACTIVE)
    sess.pending_permission = {"tool_summary": "Bash: ls"}
    assert "waiting for" not in _bar(chat_bot, sess)


# ---- the pinned bar: several sessions --------------------------------------------

def _two(bot, other_status, **other):
    a = _waiting(bot)
    b = kit.register(bot, kit.make_session(status=other_status, name="claude-x2",
                                          label="x2", **other))
    return a, b


def test_bar_counts_tasks_with_working_next_to_an_idle_session(chat_bot):
    a, b = _two(chat_bot, Status.IDLE)
    assert _bar(chat_bot, a, b).split("\n")[0] == "⚙️ 1 working · 1 idle"


def test_bar_counts_tasks_with_working_next_to_a_busy_session(chat_bot):
    a, b = _two(chat_bot, Status.BUSY)
    assert _bar(chat_bot, a, b).split("\n")[0] == "⚙️ 2 working"


def test_bar_several_sessions_line_names_the_job_counts(chat_bot):
    a, b = _two(chat_bot, Status.IDLE)
    assert _line_of(_bar(chat_bot, a, b), L) == f"• {L} (waiting for 2 agents and 1 shell)"


def test_bar_several_sessions_idle_neighbour_with_a_server_keeps_its_note(chat_bot):
    a, b = _two(chat_bot, Status.IDLE, other_shells=1)
    assert _line_of(_bar(chat_bot, a, b), "x2") == "• x2 (idle, 1 shell running)"


# ---- /status -----------------------------------------------------------------------

def test_status_head_names_the_job_counts(chat_bot):
    sess = _waiting(chat_bot)
    head = _head(chat_bot, sess)
    assert head.endswith(f"{L} · 🔄 waiting for 2 agents and 1 shell")


def test_status_block_names_agents_only_once(chat_bot):
    """design.md guard 11: no second, mixed-source count on the details line."""
    sess = _waiting(chat_bot, other_agents=4, other_shells=3)
    block = chat_bot._status_block(sess, None, tr=EN)
    assert len(re.findall(r"\bagents?\b", block)) == 1, block


def test_status_block_names_shells_only_once(chat_bot):
    sess = _waiting(chat_bot, other_agents=4, other_shells=3)
    block = chat_bot._status_block(sess, None, tr=EN)
    assert len(re.findall(r"\bshells?\b", block)) == 1, block


def test_status_finishing_up_head(chat_bot):
    sess = _waiting(chat_bot, agents=0, job_shells=0)
    sess.job_continuation_active = True
    head = _head(chat_bot, sess)
    assert head.endswith(f"{L} · 🔄 finishing up")


def test_status_list_shows_the_waiting_head(chat_bot):
    _waiting(chat_bot)
    text = _plain(chat_bot._render_status_list(kit.CHAT)[0])
    assert f"{L} · 🔄 waiting for 2 agents and 1 shell" in text


def _verbs(kb) -> set[str]:
    """Each button's verb, its one-time stamp dropped."""
    return {re.sub(r"\d+$", "", b.callback_data.rsplit(":", 1)[-1])
            for row in (kb.inline_keyboard if kb else ()) for b in row
            if isinstance(b.callback_data, str)}


def _status_verbs(mk_bot, monkeypatch, sess) -> set[str]:
    from aipager.bot import dashboard
    monkeypatch.setattr(dashboard, "CHAT_ID", str(kit.CHAT))
    monkeypatch.setattr(dashboard, "get_rate_limiter", lambda: None)
    bot = mk_bot()
    bot._read_status_file = lambda name: None
    kit.register(bot, sess)
    return _verbs(bot._render_status_list(kit.CHAT)[1])


@pytest.fixture
def stop_verbs(mk_bot, monkeypatch):
    """What /status adds for a working session over a plain idle one: its
    Stop (read from the surface, not assumed)."""
    busy = _status_verbs(mk_bot, monkeypatch, kit.make_session(status=Status.BUSY))
    idle = _status_verbs(mk_bot, monkeypatch, kit.make_session())
    extra = busy - idle
    assert extra, (busy, idle)
    return extra


def test_status_list_offers_stop_while_waiting(chat_bot, stop_verbs):
    _waiting(chat_bot)
    assert stop_verbs <= _verbs(chat_bot._render_status_list(kit.CHAT)[1])


def test_status_list_offers_no_stop_for_plain_idle(chat_bot, stop_verbs):
    """Control for the test above: the Stop is the waiting state's."""
    kit.register(chat_bot, kit.make_session(other_shells=1))
    assert not stop_verbs & _verbs(chat_bot._render_status_list(kit.CHAT)[1])


def test_status_interactive_with_agents_is_not_waiting_for(chat_bot):
    sess = _waiting(chat_bot, status=Status.INTERACTIVE)
    sess.pending_permission = {"tool_summary": "Bash: ls"}
    assert "waiting for" not in chat_bot._status_block(sess, None, tr=EN)


def test_status_busy_with_agents_is_not_waiting_for(chat_bot):
    sess = _waiting(chat_bot, status=Status.BUSY)
    assert "waiting for" not in chat_bot._status_block(sess, None, tr=EN).split("\n")[0]


# ---- /x switch line ----------------------------------------------------------------

def test_switch_reply_names_the_job_counts(chat_bot):
    sess = _waiting(chat_bot)
    text = chat_bot._render_switch_reply(kit.CHAT, sess)[0]
    assert " · 🔄 waiting for 2 agents and 1 shell · " in text


def test_switch_reply_one_shell(chat_bot):
    sess = _waiting(chat_bot, agents=0, job_shells=1)
    text = chat_bot._render_switch_reply(kit.CHAT, sess)[0]
    assert " · 🔄 waiting for 1 shell · " in text


def test_switch_reply_plain_idle_is_not_waiting_for(chat_bot):
    sess = kit.register(chat_bot, kit.make_session(other_shells=1, other_agents=1))
    assert "waiting for" not in chat_bot._render_switch_reply(kit.CHAT, sess)[0]


# ---- the busy card's last line -----------------------------------------------------

def _card_last(sess) -> str:
    sess.busy_started_at = time.monotonic() - 192
    out = unfooter(animation.build_stream_card(sess, "Working", waiting=True))
    return out.splitlines()[-1]


def test_card_last_line_names_the_job_counts():
    sess = kit.make_session(agents=2, job_shells=1, other_agents=1, other_shells=1)
    assert re.fullmatch(r"🔄 \*\*x1\*\* · waiting for 2 agents and 1 shell · 3:1\d",
                        _card_last(sess)), _card_last(sess)


@pytest.mark.parametrize("agents, shells, words", [
    (1, 0, "waiting for 1 agent"), (0, 1, "waiting for 1 shell"),
    (2, 0, "waiting for 2 agents"), (0, 2, "waiting for 2 shells"),
], ids=["1a", "1s", "2a", "2s"])
def test_card_last_line_plural_forms(agents, shells, words):
    sess = kit.make_session(agents=agents, job_shells=shells)
    assert re.fullmatch(rf"🔄 \*\*x1\*\* · {words} · 3:1\d", _card_last(sess)), _card_last(sess)


def test_card_last_line_finishing_up_at_zero_counts():
    sess = kit.make_session()
    sess.job_grace_until = time.monotonic() + 60
    assert re.fullmatch(r"🔄 \*\*x1\*\* · finishing up · 3:1\d", _card_last(sess)), _card_last(sess)


# ---- every chat surface agrees ------------------------------------------------------

def test_every_chat_surface_names_the_same_counts(chat_bot):
    """One fixture, one phrase on the bar, /status, /x and the card."""
    sess = _waiting(chat_bot)
    phrase = "waiting for 2 agents and 1 shell"
    texts = {
        "bar": _bar(chat_bot, sess),
        "status": chat_bot._status_block(sess, None, tr=EN),
        "switch": chat_bot._render_switch_reply(kit.CHAT, sess)[0],
        "card": _card_last(sess),
    }
    assert {k: phrase in v for k, v in texts.items()} == dict.fromkeys(texts, True)
