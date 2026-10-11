"""`/mode` takes the mode's word in any enabled language next to `ask` and
`auto` (G62); what is stored and sent stays the id, and a typed word that
is not a mode word is still read as a session name."""

from __future__ import annotations

import dataclasses
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.bot.mode_labels import mode_from_word
from aipager.i18n import core, locales
from aipager.state import Status, TrackedSession

CHAT = 4242


@pytest.fixture
def fa_ru(tmp_path, monkeypatch):
    """Persian and Russian enabled, with their mode labels and typed mode
    words in a catalog."""
    monkeypatch.setattr(core, "LOCALES_DIR", tmp_path)
    for code, ask, auto, full in (("fa", "پرسش", "خودکار", "کامل"),
                                  ("ru", "Спрашивать", "Авто", "Полный")):
        monkeypatch.setitem(locales.LOCALES, code,
                            dataclasses.replace(locales.LOCALES[code], enabled=True))
        mode = "مود" if code == "fa" else "Режим"
        lines = [json.dumps({"k": k, "v": v, "src": "0"}, ensure_ascii=False)
                 for k, v in (("mode.ask", f"💬 {mode} {ask}"), ("mode.auto", f"🤖 {mode} {auto}"),
                              ("mode.full", f"🔓 {mode} {full}"),
                              ("mode.word.ask", ask), ("mode.word.auto", auto),
                              ("mode.word.full", full))]
        (tmp_path / f"{code}.jsonl").write_text("".join(x + "\n" for x in lines),
                                               encoding="utf-8")
    core.reset_caches()


def test_the_word_resolves_in_every_enabled_language(fa_ru):
    assert mode_from_word("خودکار") == "auto"
    assert mode_from_word("\u200fخودکار") == "auto"
    assert mode_from_word("پرسش") == "ask"
    assert mode_from_word("авто") == "auto" and mode_from_word("АВТО") == "auto"
    assert mode_from_word("Спрашивать") == "ask"
    assert mode_from_word("AUTO") == "auto" and mode_from_word("Ask") == "ask"
    assert mode_from_word("کامل") == "full" and mode_from_word("ПОЛНЫЙ") == "full"
    assert mode_from_word("Full") == "full" and mode_from_word("🔓Full") == "full"
    for word in ("x1", "auto2", "", "خود", "💬"):
        assert mode_from_word(word) is None, word
    # The word for mode in a label is not a mode word of its own.
    for word in ("مود", "режим", "mode"):
        assert mode_from_word(word) is None, word


def _mode(bot, run_async, mk_update, text):
    update = mk_update(text, chat_id=CHAT)
    update.effective_chat.type = "private"
    update.message.reply_text = AsyncMock(return_value=MagicMock(message_id=1))
    run_async(bot._handle_mode_cmd(update, MagicMock()))
    return update


@pytest.fixture
def bot(mk_bot):
    b = mk_bot()
    s = TrackedSession(name="claude-x1", label="x1", status=Status.IDLE)
    s.scope_chat_id = CHAT
    b.registry._sessions[s.name] = s
    b.registry.last_active_session = s.name
    b._mode_flow = AsyncMock()
    return b


def test_mode_accepts_the_word_in_persian_and_russian(fa_ru, bot, run_async, mk_update):
    for text, target in (("/mode x1 خودکار", "auto"), ("/mode авто", "auto"),
                         ("/mode x1 پرسش", "ask"), ("/mode x1 auto", "auto"),
                         ("/mode x1 کامل", "full"), ("/mode полный", "full"),
                         ("/mode x1 full", "full")):
        bot._mode_flow.reset_mock()
        bot.registry._sessions["claude-x1"].perm_mode = "ask" if target != "ask" else "auto"
        _mode(bot, run_async, mk_update, text)
        sess, want = bot._mode_flow.await_args.args[:2]
        assert sess.name == "claude-x1" and want == target, text


def test_a_translated_word_is_not_taken_as_a_label(fa_ru, bot, run_async, mk_update):
    """`/mode خودکار x1`: the word is the mode, x1 the session; nothing is
    looked up as a session called خودکار."""
    update = _mode(bot, run_async, mk_update, "/mode خودکار x1")
    assert bot._mode_flow.await_count == 1
    assert bot._mode_flow.await_args.args[1] == "auto"
    replies = [c.args[0] for c in update.message.reply_text.await_args_list]
    assert not any("No live session named" in r for r in replies)
