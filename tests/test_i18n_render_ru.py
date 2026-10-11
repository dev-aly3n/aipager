"""Every phase 2 surface rendered in Russian (the registry with ``ru``
stored for its chats): Cyrillic from the catalog, no invisible marks,
Latin digits, and the plural forms Russian needs (one, few, many) on
every counted surface (G77)."""

from __future__ import annotations

import json
import re

from aipager import i18n, preferences
from aipager.bot import agents_line, animation, card_rows, dashboard
from aipager.bot.notify import settled_agent_row
from aipager.i18n import core, plurals
from tests import i18n_surfaces

CYRILLIC = re.compile("[\u0400-\u04ff]")
MARKS = ("\u200f", "\u2068", "\u2069")
COUNTS = (1, 2, 5, 11, 12, 21, 22, 25)


def test_every_surface_reads_in_russian(mk_bot, run_async, monkeypatch):
    names = {n for n, _f in i18n_surfaces.SURFACES} - i18n_surfaces.PENDING
    renders = i18n_surfaces.render_all(mk_bot, run_async, monkeypatch, "ru", names=names)
    assert set(renders) == names
    for name, texts in renders.items():
        assert name in i18n_surfaces.NAMES_ONLY or any(CYRILLIC.search(t) for t in texts), name
        for text in texts:
            assert not any(m in text for m in MARKS), (name, text)
            assert not re.search("[\u06f0-\u06f9\u0660-\u0669]", text), (name, text)
            assert "\u2014" not in text, (name, text)


def _form(key: str, n: int, **params) -> str:
    """What the catalog says for *n*, filled in."""
    forms = core._catalog("ru")[key]
    text = forms[plurals.category("ru", n)].replace("{n}", str(n))
    for name, value in params.items():
        text = text.replace("{" + name + "}", value)
    return text


def _working_header(n: int, tr) -> str:
    """The first line of an answer's status block with *n* agents working."""
    view = agents_line.BlockView(agents=(agents_line.AgentRow("a", False),) * n)
    return agents_line.render_block(view, tr).split("\n\n")[0]


def test_plural_forms(mk_bot, run_async, monkeypatch):
    preferences.set_ui_language(i18n_surfaces.CHAT, "ru")
    ru = i18n.get("ru")
    assert [plurals.category("ru", n) for n in COUNTS] == [
        "one", "few", "many", "many", "many", "one", "few", "many"]
    for n in COUNTS:
        # The fold summary.
        assert animation._tool_fold_summary(n, ru) == _form("card.fold", n)
        # The settled agent row.
        row = card_rows.render_md(ru, settled_agent_row("Explore", n, 65))
        assert row == _form("card.row.agent_done", n, agent="`Explore`", clock="1:05")
        # The answer's status block.
        assert _working_header(n, ru) == _form("agents.head.working", n)
        # The full log's memory note.
        log = animation.build_full_log("x1", [("Read: /a", True)] * n, [], "", tr=ru)
        assert log.split("\n")[2] == _form("log.memory", n)


#: Every counted phase 3a key and a surface-free way to render it.
P3A_PLURALS = ("route.cleared", "route.queue_full", "sess.diff_files", "toast.clear_done",
               "notice.quiet_turn", "notice.compact_timeout", "held.late_marker")


def test_phase_3a_plural_forms(mk_bot, run_async, monkeypatch):
    """Each counted phase 3a line, through the code that sends it, reads
    the catalog's form for n's category (one, few, many)."""
    from aipager.bot import callbacks, handlers, held_message, notify, session_parity
    ru = i18n.get("ru")
    msgs = {m.key: m for m in (handlers.CLEARED, handlers.QUEUE_FULL,
                               session_parity.DIFF_FILES, notify.QUIET_TURN,
                               notify.COMPACT_TIMEOUT, held_message.LATE_MARKER,
                               callbacks.TOAST_CLEAR_DONE)}
    assert set(msgs) == set(P3A_PLURALS)
    for key in P3A_PLURALS:
        assert set(core._catalog("ru")[key]) == {"one", "few", "many", "other"}, key
    for n in COUNTS:
        assert held_message.late_marker_text(ru, n * 60) == _form("held.late_marker", n)
        for key, params in (("route.cleared", {"session": "x1"}),
                            ("route.queue_full", {"session": "x1"}),
                            ("sess.diff_files", {"session": "x1", "added": "3",
                                                 "removed": "1"})):
            text = ru.n(msgs[key], n, kind="text", **params)
            assert text == _form(key, n, **params), (key, n)
    renders = i18n_surfaces.render_all(
        mk_bot, run_async, monkeypatch, "ru",
        names={"route.clearqueue", "notice.quiet_turn", "notice.compact_timeout",
               "taps.clear_gone", "route.queue_full", "sess.diff"})
    flat = "\n".join(t for texts in renders.values() for t in texts)
    for key, counts in (("route.cleared", (1, 2, 5)), ("notice.quiet_turn", (1, 2, 5, 21)),
                        ("notice.compact_timeout", (1, 2, 5, 21)),
                        ("toast.clear_done", (1, 2, 5))):
        for n in counts:
            assert _form(key, n, session="x1") in flat, (key, n)


def test_plural_forms_on_status_and_the_bar(mk_bot, run_async, monkeypatch):
    from aipager.state import Status
    monkeypatch.setattr(dashboard, "CHAT_ID", str(i18n_surfaces.CHAT))
    monkeypatch.setattr(dashboard, "get_rate_limiter", lambda: None)
    preferences.set_ui_language(i18n_surfaces.CHAT, "ru")
    for n in (1, 2, 5, 21, 22):
        bot = mk_bot()
        bot._read_status_file = lambda name: None
        sessions = [i18n_surfaces._sess(name=f"claude-s{i}", label=f"s{i}",
                                        status=Status.IDLE) for i in range(n)]
        i18n_surfaces._registered(bot, *sessions)
        text, _kb = bot._render_status_list(i18n_surfaces.CHAT)
        assert text.startswith(_form("status.sessions", n))
        if n > 1:
            first = bot._render_pinned(i18n_surfaces.CHAT, sessions)[0].split("\n")[0]
            assert first == "💤 " + _form("pin.count.idle", n)


def test_the_catalog_carries_every_russian_form():
    for key, value in core._catalog("ru").items():
        if isinstance(value, dict):
            assert set(value) == {"one", "few", "many", "other"}, key
    assert json.dumps(core._catalog("ru"), ensure_ascii=False).count("{n}") > 20


def _waiting_line(n: int, tr) -> str:
    """The busy card's waiting line with *n* background agents of one type."""
    sess = i18n_surfaces._sess()
    sess.active_subagents = {f"a{i}": {"type": "Explore", "started_at": i18n_surfaces.NOW}
                             for i in range(n)}
    return animation._status_line(sess, "Working", waiting=True, tr=tr)


def test_the_words_after_a_count_agree_with_one_and_twenty_one(mk_bot, run_async, monkeypatch):
    """The predicate after a counted phrase sits outside its Plural, so it
    must read right for every n: "1 агент ещё работают" (a plural verb
    after a singular subject) was the review's rev-iter1-001."""
    preferences.set_ui_language(i18n_surfaces.CHAT, "ru")
    ru = i18n.get("ru")
    for n in (1, 2, 5, 21):
        agents = _form("card.agents_typed", n, types="Explore")
        assert _waiting_line(n, ru) == f"🔄 **x1** · {agents} ещё в работе", n
    for n in (1, 21):
        assert "работают" not in _working_header(n, ru)
        assert "работают" not in _waiting_line(n, ru)


def test_the_waiting_line_reads_in_the_sessions_language(mk_bot, run_async, monkeypatch):
    """With no ``tr`` given, the whole waiting line reads in the session
    chat's language, the agents phrase included: ``_agent_phrase`` resolves
    the language itself (the phase 1 stand-ins take one argument)."""
    preferences.set_ui_language(i18n_surfaces.CHAT, "ru")
    sess = i18n_surfaces._sess()
    sess.active_subagents = {"a0": {"type": "Explore", "started_at": i18n_surfaces.NOW}}
    line = animation._status_line(sess, "Working", waiting=True)
    assert line == f"🔄 **x1** · {_form('card.agents_typed', 1, types='Explore')} ещё в работе"
