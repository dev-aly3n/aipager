"""The pinned bar and /status show what a waiting session asks. For a
prompt with no details aipager stores an English summary ("Permission
needed", "Needs input"); a translated chat sees it in its own language,
drawn from the key at render time (review rev-iter1-006). The stored
value stays English, and an English chat reads it byte for byte. A real
tool summary is never translated. And /status's cut of a long step never
leaves a direction isolate open."""

from __future__ import annotations

import pytest

from aipager import i18n, preferences
from aipager.bot import dashboard
from aipager.bot.keyboards import PERM_NEEDED
from aipager.bot.notify import settled_agent_row
from aipager.state import Status, TrackedSession

CHAT = 4343
FSI, PDI = "\u2068", "\u2069"


@pytest.fixture
def bot(mk_bot, monkeypatch):
    monkeypatch.setattr(dashboard, "CHAT_ID", str(CHAT))
    monkeypatch.setattr(dashboard, "get_rate_limiter", lambda: None)
    b = mk_bot()
    b._read_status_file = lambda name: None
    return b


def _waiting(bot, *, perm_summary=None, prompt_summary=None):
    s = TrackedSession(name="claude-x1", label="x1", status=Status.INTERACTIVE)
    s.scope_chat_id, s.scope_kind = CHAT, "dm"
    if perm_summary is not None:
        s.pending_permission = {"tool_summary": perm_summary}
    if prompt_summary is not None:
        s.pending_prompt_msg = {"summary": prompt_summary}
    bot.registry._sessions[s.name] = s
    return s


def _first(bot, sess) -> str:
    return bot._render_pinned(CHAT, [sess])[0].split("\n")[0]


def _status(bot) -> str:
    return bot._render_status_list(CHAT)[0]


@pytest.mark.parametrize("code", ["fa", "ru"])
def test_a_stored_permission_needed_reads_in_the_chats_language(bot, code):
    preferences.set_ui_language(CHAT, code)
    sess = _waiting(bot, perm_summary="Permission needed")
    shown = i18n.get(code).text(PERM_NEEDED)
    for text in (_first(bot, sess), _status(bot)):
        assert shown in text and "Permission needed" not in text, text
    assert sess.pending_permission["tool_summary"] == "Permission needed"


@pytest.mark.parametrize("code", ["fa", "ru"])
def test_a_stored_needs_input_reads_in_the_chats_language(bot, code):
    preferences.set_ui_language(CHAT, code)
    sess = _waiting(bot, prompt_summary="Needs input")
    shown = i18n.get(code).text(dashboard.PIN_NEEDS_INPUT)
    for text in (_first(bot, sess), _status(bot)):
        assert shown in text and "Needs input" not in text, text
    assert sess.pending_prompt_msg["summary"] == "Needs input"


@pytest.mark.parametrize("code", ["fa", "ru"])
def test_a_real_tool_summary_is_never_translated(bot, code):
    preferences.set_ui_language(CHAT, code)
    sess = _waiting(bot, perm_summary="Bash: ls Permission needed")
    assert "Bash: ls Permission needed" in _first(bot, sess)


def test_english_reads_the_stored_summaries_as_they_are(bot):
    sess = _waiting(bot, perm_summary="Permission needed")
    assert _first(bot, sess) == "⏳ x1 needs you - Permission needed"
    sess.pending_permission = None
    sess.pending_prompt_msg = {"summary": "Needs input"}
    assert _first(bot, sess) == "⏳ x1 needs you - Needs input"


def test_a_cut_step_closes_its_isolate(bot):
    """A long agent name puts the 40-character cut inside the isolate the
    Persian row wraps it in; the cut closes it."""
    preferences.set_ui_language(CHAT, "fa")
    s = TrackedSession(name="claude-x1", label="x1", status=Status.BUSY)
    s.scope_chat_id, s.scope_kind = CHAT, "dm"
    s.tool_history = [(settled_agent_row("general-purpose-explorer-agent-number-two", 3, 65),
                       True)]
    bot.registry._sessions[s.name] = s
    text = _status(bot)
    step = next(line for line in text.split("\n") if "general-purpose" in line)
    assert "…" in step and FSI in step
    assert step.count(FSI) == step.count(PDI), repr(step)


def test_close_isolates_leaves_balanced_text_alone():
    assert dashboard._close_isolates("abc") == "abc"
    assert dashboard._close_isolates(f"a{FSI}b{PDI}") == f"a{FSI}b{PDI}"
    assert dashboard._close_isolates(f"a{FSI}b{FSI}c{PDI}") == f"a{FSI}b{FSI}c{PDI}{PDI}"
