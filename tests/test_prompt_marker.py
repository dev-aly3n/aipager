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
    """``_inject_prompt`` writes a per-message note now (design.md "queue
    handoff"), not the one-slot snapshot directly — same resolved role/
    scope/member/style_text inputs, just carried on ``write_note``.

    Passes ``driver_user_id`` explicitly (as every real live-``Update``
    caller does) — permission attribution requires it since review
    rev-iter1-001; see
    ``test_inject_prompt_role_resolution_requires_an_explicit_driver_user_id``
    for the fail-closed case."""
    from aipager import policy_snapshot
    bot = _bot(mk_bot)
    monkeypatch.setattr(inject, "send_text_and_enter", AsyncMock(return_value=True))
    captured = {}

    def _fake_write_note(name, role, scope, member, *, msg_id=None,
                         chat_id=None, sender_key=None, body="",
                         raw_text="", style_text="", reply_context=""):
        captured.update(name=name, role=role, member=member,
                        style_text=style_text)
        return None
    monkeypatch.setattr(policy_snapshot, "write_note", _fake_write_note)
    run_async(bot._inject_prompt(_sess(), "do the thing", driver_user_id=2))
    assert captured["name"] == "claude-x__g100"
    assert captured["member"].label == "bob"        # driver resolved
    assert captured["role"].name == "user"


def test_inject_prompt_role_resolution_requires_an_explicit_driver_user_id(
    mk_bot, run_async, monkeypatch,
):
    """review rev-iter1-001: permission attribution (member/role) must
    come from the id the CALLER explicitly supplied, never from
    ``sess.last_driver_user_id`` alone. A caller with no live identity
    for this specific message (Retry, a literal /compact, the
    queue-drain site) gets ``member=None``/``role=None`` — floor
    permissions — even though ``sess.last_driver_user_id`` is set to a
    real, resolvable member. Fail closed rather than silently inherit
    whoever the mutable session field happens to name."""
    from aipager import policy_snapshot
    bot = _bot(mk_bot)
    monkeypatch.setattr(inject, "send_text_and_enter", AsyncMock(return_value=True))
    captured = {}

    def _fake_write_note(name, role, scope, member, *, msg_id=None,
                         chat_id=None, sender_key=None, body="",
                         raw_text="", style_text="", reply_context=""):
        captured.update(role=role, member=member)
        return None
    monkeypatch.setattr(policy_snapshot, "write_note", _fake_write_note)

    sess = _sess()
    assert sess.last_driver_user_id == 2  # a resolvable member is set

    run_async(bot._inject_prompt(sess, "do the thing"))  # no driver_user_id

    assert captured["member"] is None
    assert captured["role"] is None


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

    def _fake_write_note(name, role, scope, member, *, msg_id=None,
                         chat_id=None, sender_key=None, body="",
                         raw_text="", style_text="", reply_context=""):
        captured.update(style_text=style_text)
        return None
    monkeypatch.setattr(policy_snapshot, "write_note", _fake_write_note)

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


# ---- queue handoff: the note itself (design.md) ----------------------------

def test_inject_prompt_writes_a_note_with_msg_id_chat_id_and_body(mk_bot, run_async, monkeypatch):
    from aipager import policy_snapshot as ps
    bot = _bot(mk_bot)
    monkeypatch.setattr(inject, "send_text_and_enter", AsyncMock(return_value=True))
    sess = _sess()

    run_async(bot._inject_prompt(sess, "fix the bug", msg_id=555, chat_id=-100))

    notes = ps.list_outstanding_notes(sess.name)
    assert len(notes) == 1
    note = notes[0]
    assert note["msg_id"] == 555
    assert note["chat_id"] == -100
    assert note["body"] == "[via Telegram · @bob · role:user]\nfix the bug"
    assert note["raw_text"] == "fix the bug"  # never the marker-prefixed body


def test_inject_prompt_sender_key_uses_the_explicit_driver_user_id(mk_bot, run_async, monkeypatch):
    """The bug found in review: sender_key must come from the SAME
    source the mixed-sender hold-check reads (the live Update's sender),
    not from sess.last_driver_user_id — which stays stale/unset for
    exactly the callers (personal mode; any call site before
    _mark_driver runs) where this matters most."""
    from aipager import policy_snapshot as ps
    bot = _bot(mk_bot)
    monkeypatch.setattr(inject, "send_text_and_enter", AsyncMock(return_value=True))
    sess = _sess()
    sess.last_driver_user_id = None  # deliberately stale/unset

    run_async(bot._inject_prompt(sess, "hi", driver_user_id=99999))

    note = ps.list_outstanding_notes(sess.name)[0]
    assert note["sender_key"] == [sess.scope_chat_id, 99999]


def test_inject_prompt_sender_key_falls_back_to_last_driver_user_id_when_omitted(
    mk_bot, run_async, monkeypatch,
):
    """Callers with no live Update (the queue-drain site, Retry, a
    literal /compact) fall back to sess.last_driver_user_id — the best
    available approximation."""
    from aipager import policy_snapshot as ps
    bot = _bot(mk_bot)
    monkeypatch.setattr(inject, "send_text_and_enter", AsyncMock(return_value=True))
    sess = _sess()
    sess.last_driver_user_id = 42

    run_async(bot._inject_prompt(sess, "hi"))  # no driver_user_id passed

    note = ps.list_outstanding_notes(sess.name)[0]
    assert note["sender_key"] == [sess.scope_chat_id, 42]


def test_inject_prompt_two_calls_with_the_same_driver_id_are_not_mixed_sender(
    mk_bot, run_async, monkeypatch,
):
    """Regression for the review-found bug directly: the SAME human
    sending two messages in a row must never be flagged as a mixed
    sender because the note-writer and the hold-check disagreed about
    where "who sent this" comes from."""
    from aipager.bot import transport

    bot = _bot(mk_bot)
    monkeypatch.setattr(inject, "send_text_and_enter", AsyncMock(return_value=True))
    sess = _sess()

    run_async(bot._inject_prompt(sess, "first", driver_user_id=12345))

    class _User:
        id = 12345

    class _Update:
        effective_user = _User()

    assert transport.mixed_sender_note_outstanding(sess, _Update()) is False
