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


def test_inject_writes_policy_snapshot(mk_bot, run_async, monkeypatch):
    from aipager import policy_snapshot
    bot = _bot(mk_bot)
    monkeypatch.setattr(inject, "send_text_and_enter", AsyncMock(return_value=True))
    captured = {}
    monkeypatch.setattr(
        policy_snapshot, "write_snapshot",
        lambda name, role, scope, member, style_text="": captured.update(
            name=name, role=role, member=member, style_text=style_text))
    run_async(bot._inject_prompt(_sess(), "do the thing"))
    assert captured["name"] == "claude-x__g100"
    assert captured["member"].label == "bob"        # driver resolved
    assert captured["role"].name == "user"


def test_inject_prompt_style_text_reflects_session_override(mk_bot, run_async, monkeypatch):
    """THE load-bearing regression test for design.md's critical path
    (session_ops.py's _inject_prompt): style_text must be built from
    resolve_preferences(scope, sess.preference_overrides()), never
    get_preferences(scope) alone. A caller that regresses to the latter
    passes every OTHER test in this feature while the per-session
    settings feature does nothing in production — this is the one test
    standing between that regression and green CI (design.md Risks)."""
    from aipager import policy_snapshot, preferences as prefs_mod
    bot = _bot(mk_bot)
    monkeypatch.setattr(inject, "send_text_and_enter", AsyncMock(return_value=True))
    captured = {}
    monkeypatch.setattr(
        policy_snapshot, "write_snapshot",
        lambda name, role, scope, member, style_text="": captured.update(
            style_text=style_text))

    sess = _sess()
    # Scope default: no style guidance at all.
    prefs_mod.set_preference(sess.scope_chat_id, "answer_length", "none")
    run_async(bot._inject_prompt(sess, "hello"))
    assert captured["style_text"] == ""  # nothing to inject at the scope default

    # This session overrides answer_length; the scope itself is untouched.
    sess.override_answer_length = "xshort"
    run_async(bot._inject_prompt(sess, "hello again"))
    assert "one or two sentences" in captured["style_text"]
    assert prefs_mod.get_preferences(sess.scope_chat_id).answer_length == "none"


def test_inject_legacy_no_marker_but_sets_origin(mk_bot, run_async, monkeypatch):
    bot = mk_bot()  # scopes=None → no marker
    monkeypatch.setattr(inject, "send_text_and_enter", AsyncMock(return_value=True))
    sess = _sess()
    run_async(bot._inject_prompt(sess, "hello"))
    assert sess.last_prompt_origin == "telegram"
