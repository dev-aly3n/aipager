"""Black-box: a Persian private chat and a Russian one talking to their
sessions (help, home, routing replies, /new end to end, the session
commands), the language chosen by a tap in each chat's own menu."""

from __future__ import annotations

import re

from tests import i18n_ml2_bb as bb
from tests.i18n_ml2_bb import BIDI_CONTROLS, CYRILLIC, PERSIAN

LATIN_WORDS = re.compile(r"\b(the|session|is|not|to|No|Which)\b")


def _prose(texts):
    """What a person reads: tags and isolated values removed."""
    out = []
    for text in texts:
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub("\u2068.*?\u2069", "", text, flags=re.S)
        out.append(text)
    return out


def test_a_persian_chat_reads_help_home_and_its_replies(world):
    world.choose(bb.DM_A, "fa")
    world.send("_handle_help_cmd", "/help")
    world.bot._read_status_file = lambda name: None
    world.send("_handle_start_cmd", "/start")
    world.send("_handle_stop_cmd", "/stop")
    world.send("_handle_clearqueue_cmd", "/clearqueue")
    assert len(world.tg.out) >= 4
    for text in _prose(world.tg.out):
        if text.startswith("/") or not text.strip():
            continue
        assert PERSIAN.search(text) and not LATIN_WORDS.search(text), text
    assert "/new" in "".join(world.tg.out)


def test_a_russian_chat_runs_its_session_commands(world):
    world.choose(bb.DM_A, "ru")
    world.send("_handle_kill_cmd", "/kill")
    world.send("_handle_resume_cmd", "/resume zz")
    world.session("x1")
    world.send("_handle_stop_cmd", "/stop zz")
    world.send("_handle_kill_cmd", "/kill x1")
    texts = world.tg.out
    assert len(texts) >= 4
    for text in texts:
        assert not BIDI_CONTROLS.search(text), text
    assert all(CYRILLIC.search(t) for t in texts if not t.startswith(("/", "⏹ x1")))
    # The confirm's buttons are Russian too.
    assert any(CYRILLIC.search(t) and t.startswith("⏹") for t in texts)


def test_new_end_to_end_in_persian(world):
    """/new x1: the Starting card, then the Ready card with its buttons,
    all Persian; the session name stays as typed."""
    from aipager.state import Status
    world.choose(bb.DM_A, "fa")

    async def _create(label, *, scope_chat_id, **kw):
        sess = world.session(label, chat=scope_chat_id, status=Status.IDLE)
        return sess.name, ""
    world.bot.create_session = _create
    world.send("_handle_new_cmd", "/new x1")
    texts = world.tg.out
    assert texts and all(PERSIAN.search(t) for t in texts if len(t) > 3)
    assert any("x1" in t for t in texts)
    assert not any(re.search("x[۰-۹]", t) for t in texts)


def test_a_chat_back_in_english_reads_english(world):
    world.choose(bb.DM_A, "fa")
    world.choose(bb.DM_A, "en")
    world.send("_handle_stop_cmd", "/stop")
    assert world.tg.out == ["Nothing is running."]
