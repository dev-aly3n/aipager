"""Phase D: identity/origin marker on injected prompts."""

from __future__ import annotations

from unittest.mock import AsyncMock

from aipager.dtach import inject
from aipager.policy import load_policy
from aipager.scope import Member, Scope
from aipager.state import Status, TrackedSession


def _bot(mk_bot, kind="group", chat=-100):
    scope = Scope(chat_id=chat, kind=kind, label="dev",
                  members=(Member(id=2, label="bob", role="user"),))
    bot = mk_bot(scopes=[scope])
    bot.policy = load_policy()
    return bot


def _sess(chat=-100, kind="group"):
    s = TrackedSession(name="claude-x__g100", label="x", status=Status.IDLE)
    s.scope_chat_id = chat
    s.scope_kind = kind
    s.last_driver_user_id = 2
    return s


def test_marker_group_includes_role(mk_bot):
    bot = _bot(mk_bot)
    assert bot._prompt_marker(_sess()) == "[via Telegram · @bob · role:user]"


def test_marker_dm_omits_role(mk_bot):
    bot = _bot(mk_bot, kind="dm", chat=555)
    assert bot._prompt_marker(_sess(chat=555, kind="dm")) == "[via Telegram · @bob]"


def test_marker_empty_when_legacy(mk_bot):
    bot = mk_bot()  # scopes=None
    assert bot._prompt_marker(_sess()) == ""


def test_inject_free_text_prefixes_marker(mk_bot, run_async, monkeypatch):
    bot = _bot(mk_bot)
    sent = {}

    async def _capture(name, text):
        sent["name"] = name
        sent["text"] = text
        return True

    monkeypatch.setattr(inject, "send_text_and_enter", _capture)
    sess = _sess()
    run_async(bot._inject_prompt(sess, "fix the bug"))
    assert sent["text"] == "[via Telegram · @bob · role:user]\nfix the bug"
    assert sess.last_prompt_origin == "telegram"


def test_inject_slash_command_no_marker(mk_bot, run_async, monkeypatch):
    bot = _bot(mk_bot)
    sent = {}

    async def _capture(name, text):
        sent["text"] = text
        return True

    monkeypatch.setattr(inject, "send_text_and_enter", _capture)
    sess = _sess()
    run_async(bot._inject_prompt(sess, "/compact"))
    assert sent["text"] == "/compact"          # marker would break the command
    assert sess.last_prompt_origin == "telegram"


def test_inject_legacy_no_marker_but_sets_origin(mk_bot, run_async, monkeypatch):
    bot = mk_bot()  # scopes=None → no marker
    monkeypatch.setattr(inject, "send_text_and_enter", AsyncMock(return_value=True))
    sess = _sess()
    run_async(bot._inject_prompt(sess, "hello"))
    assert sess.last_prompt_origin == "telegram"
