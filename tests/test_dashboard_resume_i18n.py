"""Multilingual phase 3b: the leftover dashboard text (the "(loading…)"
summary, the diff footer) and /resume (the picker and the session card it
opens) in the chat's language, English byte for byte as before."""

from __future__ import annotations

import re
from unittest.mock import AsyncMock, MagicMock

from aipager import i18n, preferences
from aipager.bot import dashboard
from aipager.state import Status, TrackedSession

CHAT = 256113222
MARKS = ("\u200f", "\u2068", "\u2069")
_CODE = re.compile(r"<code\b[^>]*>.*?</code>|<pre\b[^>]*>.*?</pre>", re.S)


def _busy(bot, monkeypatch, now=1_000_000.0):
    monkeypatch.setattr(dashboard, "time", MagicMock(monotonic=lambda: now, time=lambda: now))
    monkeypatch.setattr(dashboard, "queue_depth_parts", lambda sess: (2, 1))
    bot._read_status_file = lambda name: {"model": "Opus 4.7", "ctx_pct": 45, "cost": 1.5,
                                          "total_output": 12345}
    sess = TrackedSession(name="claude-x1", label="x1", status=Status.BUSY)
    sess.scope_chat_id, sess.scope_kind = CHAT, "dm"
    sess.busy_started_at = now - 250
    sess.last_hook_at = now - 30
    sess.last_lines_added, sess.last_lines_removed = 10, 5
    sess.active_subagents = {"a": {"type": "Explore"}, "b": {"type": "Explore"},
                             "c": {"type": "Plan"}}
    sess.tool_history = [("Read: /src/app.py", True), ("Bash: npm run build", "failed")]
    bot.registry._sessions[sess.name] = sess
    return sess


ENGLISH = ("⚙️ <b>[x1]</b> · busy 4:10\n\n<code>  Model   Opus 4.7\n"
           "  Ctx     45% ████░░░░░░\n  Cost    $1.50\n  Output  12.3k tokens\n"
           "  Lines   +10 -5\n  Agents  3 (Explore ×2, Plan)\n"
           "  Queue   3 pending (2 queued, 1 notes)\n  Active  30s ago</code>\n\n"
           "Recent:\n<code>  ✅ Read: /src/app.py\n  ❌ Bash: npm run build</code>")


def test_english_dashboard_unchanged(mk_bot, monkeypatch):
    bot = mk_bot()
    sess = _busy(bot, monkeypatch)
    assert bot._build_session_dashboard(sess) == ENGLISH
    assert bot._build_session_dashboard(sess, i18n.get("en")) == ENGLISH


def test_no_mark_inside_code(mk_bot, monkeypatch):
    preferences.set_ui_language(CHAT, "fa")
    bot = mk_bot()
    sess = _busy(bot, monkeypatch)
    text = bot._build_session_dashboard(sess)
    assert "مدل" in text
    for span in _CODE.findall(text):
        assert not any(m in span for m in MARKS), span
    assert "<code>Opus 4.7</code>" in text and "<code>Explore ×2, Plan</code>" in text
    assert "۴۵٪" in text and "$۱٫۵۰" in text and "۴:۱۰" in text
    # Every row outside code starts right to left.
    for line in text.split("\n"):
        if re.search("[\u0600-\u06ff]", _CODE.sub("", line)):
            assert line.startswith("\u200f"), line


def test_the_lines_row_keeps_its_signs_in_persian(mk_bot, monkeypatch):
    """"+10 -5" is one isolated run, so right to left never moves a sign
    away from its number."""
    preferences.set_ui_language(CHAT, "fa")
    bot = mk_bot()
    text = bot._build_session_dashboard(_busy(bot, monkeypatch))
    (row,) = [line for line in text.split("\n") if "+۱۰" in line]
    assert row.endswith("\u2068+۱۰ -۵\u2069"), row


def test_the_dashboard_in_russian(mk_bot, monkeypatch):
    preferences.set_ui_language(CHAT, "ru")
    bot = mk_bot()
    text = bot._build_session_dashboard(_busy(bot, monkeypatch))
    assert text.startswith("⚙️ <b>[x1]</b> · работает 4:10\n\nМодель: <code>Opus 4.7</code>")
    assert "Очередь: всего 3 (в очереди 2, заметок 1)" in text
    assert not any(m in text for m in MARKS)


def test_loading_summary_is_translated():
    fa, ru = i18n.get("fa"), i18n.get("ru")
    stored = "AskUserQuestion (loading…)"
    assert dashboard._stored_summary_text(i18n.get("en"), stored) == stored
    assert dashboard._stored_summary_text(ru, stored) == "AskUserQuestion (загрузка…)"
    shown = dashboard._stored_summary_text(fa, stored)
    assert "AskUserQuestion" in shown and "loading" not in shown


def test_the_diff_footer_in_the_sessions_language(mk_bot, run_async):
    preferences.set_ui_language(CHAT, "ru")
    bot = mk_bot()
    sess = TrackedSession(name="claude-x1", label="x1", status=Status.BUSY)
    sess.scope_chat_id, sess.scope_kind = CHAT, "dm"
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=3))
    content = "\n".join(f"line {i}" for i in range(34))
    run_async(bot._send_diff_preview(sess, "Write", {"file_path": "/a.py", "content": content}))
    text = bot._app.bot.send_message.await_args.args[1]
    assert text.endswith("</code></pre>\n<i>…и ещё 7 строк</i>")


def _gone(bot, name, gone_at, resumable=True):
    sess = TrackedSession(name=f"claude-{name}", label=name, status=Status.GONE)
    sess.scope_chat_id, sess.scope_kind = CHAT, "dm"
    sess.gone_at = gone_at
    sess.claude_session_id = "abc" if resumable else None
    sess.transcript_path = None
    bot.registry._sessions[sess.name] = sess


def test_the_picker_in_persian_and_russian(mk_bot, monkeypatch):
    monkeypatch.setattr(dashboard, "time", MagicMock(time=lambda: 10_000.0))
    bot = mk_bot()
    for i in range(12):
        _gone(bot, f"p{i}", 10_000.0 - 60 * (i + 1))
    _gone(bot, "h1", 9_000.0, resumable=False)
    preferences.set_ui_language(CHAT, "ru")
    text, kb = bot._render_resume_picker(0, CHAT)
    assert text.startswith("📚 <b>Прошлые сессии</b> (всего 12)")
    assert text.endswith("<i>Ещё 1 сессия без сохранённой истории, её не возобновить.</i>")
    labels = [b.text for row in kb.inline_keyboard for b in row]
    assert labels[0] == "p0 (1 мин назад)" and labels[-2:] == ["Стр. 1/2", "Далее »"]
    preferences.set_ui_language(CHAT, "fa")
    text, kb = bot._render_resume_picker(page=1, scope_chat_id=CHAT)
    labels = [b.text for row in kb.inline_keyboard for b in row]
    assert labels[-2:] == ["\u200f« قبلی", "\u200fصفحهٔ ۲/۲"]


def test_the_unscoped_picker_speaks_the_one_pinned_chats_language(mk_bot, monkeypatch):
    """No scope on a legacy personal install: the picker takes the language
    of the one chat `_pinned_chats` names (stored Persian here, so it differs
    from the English default), without reading CHAT_ID itself."""
    monkeypatch.setattr(dashboard, "time", MagicMock(time=lambda: 10_000.0))
    monkeypatch.setattr(dashboard, "CHAT_ID", str(CHAT))
    preferences.set_ui_language(CHAT, "fa")
    bot = mk_bot()
    assert bot._pinned_chats() == [CHAT]
    _gone(bot, "p0", 10_000.0 - 60)
    text, kb = bot._render_resume_picker(page=0, scope_chat_id=None)
    assert re.search("[\u0600-\u06ff]", text), text
    assert "Previous sessions" not in text
    assert kb.inline_keyboard[0][0].text != "p0 (1m ago)"
    # With two chats there is no single one to follow: the default language.
    bot.scopes = [MagicMock(chat_id=CHAT), MagicMock(chat_id=CHAT + 1)]
    text, kb = bot._render_resume_picker(page=0, scope_chat_id=None)
    assert "Previous sessions" in text
    assert kb.inline_keyboard[0][0].text == "p0 (1m ago)"


def test_fmt_gone_ago_english_unchanged(monkeypatch):
    monkeypatch.setattr(dashboard, "time", MagicMock(time=lambda: 200_000.0))
    fmt = dashboard.DashboardMixin._fmt_gone_ago
    assert [fmt(200_000.0 - d) for d in (0, 59, 61, 3600, 172800)] == [
        "0s ago", "59s ago", "1m ago", "1h ago", "2d ago"]
    assert fmt(None) == "earlier"
    assert fmt(200_000.0 - 61, i18n.get("ru")) == "1 мин назад"
