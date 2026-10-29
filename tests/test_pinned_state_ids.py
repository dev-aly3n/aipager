"""The pinned bar and /status decide glyphs, counts and order from state
ids, never from the shown words (G69): in a translated chat the bar keeps
its glyphs and its counts."""

from __future__ import annotations

import pytest

from aipager import preferences
from aipager.bot import dashboard
from aipager.i18n import locales
from aipager.state import Status, TrackedSession

CHAT = 4242


@pytest.fixture
def bot(mk_bot, monkeypatch):
    monkeypatch.setattr(locales, "_TEST_ENABLED", frozenset({"qps"}))
    monkeypatch.setattr(dashboard, "CHAT_ID", str(CHAT))
    monkeypatch.setattr(dashboard, "get_rate_limiter", lambda: None)
    preferences.set_ui_language(CHAT, "qps")
    return mk_bot()


def _first(bot, sessions) -> str:
    """The bar's first line without the pseudo locale's opening bracket."""
    return bot._render_pinned(CHAT, sessions)[0].split("\n")[0].removeprefix("⟦")


def _sess(bot, name, status):
    s = TrackedSession(name=f"claude-{name}", label=name, status=status)
    s.scope_chat_id, s.scope_kind = CHAT, "dm"
    bot.registry._sessions[s.name] = s
    return s


def test_a_persian_bar_keeps_its_glyph(bot):
    """(The language here is the pseudo one: what matters is that the shown
    word is not the id.)"""
    busy = _sess(bot, "x1", Status.BUSY)
    first = _first(bot, [busy])
    assert first.startswith("⚙️ ") and "⟦" in first
    idle = _sess(bot, "x2", Status.IDLE)
    assert _first(bot, [idle]).startswith("💤 ")
    unknown = _sess(bot, "x3", Status.UNKNOWN)
    assert _first(bot, [idle, unknown]).startswith("🔄 ")


def test_counts_follow_the_ids_in_their_order(bot):
    sessions = [_sess(bot, "a", Status.IDLE), _sess(bot, "b", Status.BUSY),
                _sess(bot, "c", Status.BUSY), _sess(bot, "d", Status.UNKNOWN)]
    first = _first(bot, sessions)
    assert first.startswith("⚙️ ")
    parts = first[len("⚙️ "):].split(" · ")
    assert [p.split(" ")[0].lstrip("⟦") for p in parts] == ["2", "1", "1"]
    assert "⟦" in parts[0]


def test_the_status_glyph_follows_the_id(bot):
    sess = _sess(bot, "x1", Status.INTERACTIVE)
    assert dashboard._STATUS_GLYPHS[dashboard._pinned_word(sess)] == "⏳"
    # "tasks" has no word of its own: it names its counts (job_wait).
    assert (set(dashboard._STATUS_GLYPHS) | {"starting"}
            == set(dashboard._STATE_MSGS) | {"tasks"})
    assert dashboard._pinned_word(sess) == "needs_you"


def test_a_session_waiting_for_its_agents_keeps_its_glyph_and_its_bucket(bot):
    """Roadmap 8.121: alone it has 🔄 and its counts in the chat's words;
    with others it is counted with working (⚙️), whatever the language."""
    tasks = _sess(bot, "x1", Status.IDLE)
    tasks.active_subagents = {"a": {"type": "Explore"}, "b": {"type": "Plan"}}
    assert dashboard._pinned_word(tasks) == "tasks"
    first = _first(bot, [tasks])
    assert first.startswith("🔄 ") and "⟦" in first and "2" in first
    idle = _sess(bot, "x2", Status.IDLE)
    first = _first(bot, [tasks, idle])
    assert first.startswith("⚙️ ")
    parts = first[len("⚙️ "):].split(" · ")
    assert [p.split(" ")[0].lstrip("⟦") for p in parts] == ["1", "1"]
