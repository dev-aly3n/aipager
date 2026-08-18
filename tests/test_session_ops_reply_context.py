"""Tests for design.md Part 0/2's reply-context building blocks in
``aipager.bot.session_ops``: ``SessionOpsMixin._build_reply_context``,
``_resolve_reply_target``, and the scoped ``_guess_session_from_text``.

Per entrypoints.md, ``_build_reply_context`` is internal — assert on its
*output* only (it's called directly here since this file IS the developer
unit-test layer for it; the Tester must not do this). ``msg``/``reply_to``
are constructed as ``MagicMock``s per the existing suite's convention
(see tests/test_bot_handlers_extra.py).
"""

from __future__ import annotations

import datetime
from unittest.mock import MagicMock

from aipager import policy_snapshot as ps
from aipager.state import Status, TrackedSession

BOT_ID = 999999


def _sess(**kw):
    s = TrackedSession(name="claude-jim", label="jim", status=kw.pop("status", Status.IDLE))
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def _reply_to(*, message_id=300, text="(old message)", caption=None,
             from_user=None, date=None):
    return MagicMock(
        message_id=message_id, text=text, caption=caption,
        from_user=from_user, date=date or datetime.datetime(2024, 1, 1, 21, 40),
    )


def _msg(*, reply_to_message=None, quote=None):
    return MagicMock(reply_to_message=reply_to_message, quote=quote)


def _user(uid):
    return MagicMock(id=uid)


# ---- criterion 11: no reply, no quote → no context -------------------------

def test_no_reply_and_no_quote_returns_empty(mk_bot):
    bot = mk_bot()
    sess = _sess()
    ctx = bot._build_reply_context(_msg(), sess, bot_id=BOT_ID, allow_file=True)
    assert ctx == ""


# ---- criterion 1: reply to the session's own latest message ----------------

def test_reply_to_latest_via_last_msg_id_returns_empty(mk_bot):
    bot = mk_bot()
    sess = _sess(last_msg_id=300)
    msg = _msg(reply_to_message=_reply_to(message_id=300))
    assert bot._build_reply_context(msg, sess, bot_id=BOT_ID, allow_file=True) == ""


def test_reply_to_latest_via_trigger_msg_id_returns_empty(mk_bot):
    bot = mk_bot()
    sess = _sess(trigger_msg_id=300)
    msg = _msg(reply_to_message=_reply_to(message_id=300))
    assert bot._build_reply_context(msg, sess, bot_id=BOT_ID, allow_file=True) == ""


def test_reply_to_latest_via_busy_msg_id_returns_empty(mk_bot):
    bot = mk_bot()
    sess = _sess()
    sess.busy_msg_id = 300
    msg = _msg(reply_to_message=_reply_to(message_id=300))
    assert bot._build_reply_context(msg, sess, bot_id=BOT_ID, allow_file=True) == ""


# ---- criterion 12: busy_msg_id sentinels never false-positive -------------

def test_busy_msg_id_sentinel_negative_one_never_false_positives_latest(mk_bot):
    bot = mk_bot()
    sess = _sess()
    sess.busy_msg_id = -1  # placeholder before a real id is known
    msg = _msg(reply_to_message=_reply_to(message_id=-1, text="whatever"))
    ctx = bot._build_reply_context(msg, sess, bot_id=BOT_ID, allow_file=False)
    assert ctx != ""  # must NOT be treated as "this is the latest message"


def test_busy_msg_id_sentinel_zero_never_false_positives_latest(mk_bot):
    bot = mk_bot()
    sess = _sess()
    sess.busy_msg_id = 0  # RichMessageGone sentinel
    msg = _msg(reply_to_message=_reply_to(message_id=0, text="whatever"))
    ctx = bot._build_reply_context(msg, sess, bot_id=BOT_ID, allow_file=False)
    assert ctx != ""


def test_busy_msg_id_none_never_false_positives_latest_for_a_zero_reply(mk_bot):
    bot = mk_bot()
    sess = _sess()  # last_msg_id/busy_msg_id/trigger_msg_id all default None
    msg = _msg(reply_to_message=_reply_to(message_id=0, text="whatever"))
    ctx = bot._build_reply_context(msg, sess, bot_id=BOT_ID, allow_file=False)
    assert ctx != ""


# ---- criterion 3: highlight produces context even on the latest message ---

def test_highlight_produces_context_even_when_reply_target_is_latest(mk_bot):
    bot = mk_bot()
    sess = _sess(last_msg_id=300)
    quote = MagicMock(text="the highlighted bit", is_manual=True)
    msg = _msg(reply_to_message=_reply_to(message_id=300), quote=quote)
    ctx = bot._build_reply_context(msg, sess, bot_id=BOT_ID, allow_file=True)
    assert ctx != ""
    assert "the highlighted bit" in ctx


def test_highlight_manual_true_says_highlighting_not_quoting(mk_bot):
    bot = mk_bot()
    sess = _sess()
    quote = MagicMock(text="a bit", is_manual=True)
    msg = _msg(reply_to_message=_reply_to(message_id=1, text="src"), quote=quote)
    ctx = bot._build_reply_context(msg, sess, bot_id=BOT_ID, allow_file=True)
    assert "highlighting" in ctx
    assert "quoting" not in ctx


def test_highlight_manual_false_never_uses_the_word_highlighted(mk_bot):
    """Criterion 5."""
    bot = mk_bot()
    sess = _sess()
    quote = MagicMock(text="a bit", is_manual=False)
    msg = _msg(reply_to_message=_reply_to(message_id=1, text="src"), quote=quote)
    ctx = bot._build_reply_context(msg, sess, bot_id=BOT_ID, allow_file=True)
    assert "highlighted" not in ctx
    assert "highlighting" not in ctx
    assert "quoting" in ctx
    assert "this quoted part of" in ctx


# ---- criterion 4: oversized highlight truncation + fallback file ----------

def test_highlight_over_1000_chars_truncated_with_marker(mk_bot):
    bot = mk_bot()
    sess = _sess()
    long_fragment = "y" * 1500
    quote = MagicMock(text=long_fragment, is_manual=True)
    msg = _msg(reply_to_message=_reply_to(message_id=1, text="src"), quote=quote)
    ctx = bot._build_reply_context(msg, sess, bot_id=BOT_ID, allow_file=False)
    assert "…(truncated)" in ctx
    assert "y" * 1001 not in ctx  # never leaks past the 1000-char cap inline


def test_highlight_over_1000_chars_writes_fallback_file_when_allow_file(
    mk_bot, tmp_path, monkeypatch,
):
    monkeypatch.setattr(ps, "reply_context_path", lambda n: tmp_path / f"{n}.txt")
    bot = mk_bot()
    sess = _sess()
    quote = MagicMock(text="z" * 1500, is_manual=True)
    msg = _msg(reply_to_message=_reply_to(message_id=1, text="src"), quote=quote)
    bot._build_reply_context(msg, sess, bot_id=BOT_ID, allow_file=True)
    assert (tmp_path / "claude-jim.txt").exists()


def test_highlight_over_1000_chars_no_fallback_file_when_queued(
    mk_bot, tmp_path, monkeypatch,
):
    """Part 4: allow_file=False (queued case) must never write the file,
    even for an oversized highlight."""
    monkeypatch.setattr(ps, "reply_context_path", lambda n: tmp_path / f"{n}.txt")
    bot = mk_bot()
    sess = _sess()
    quote = MagicMock(text="z" * 1500, is_manual=True)
    msg = _msg(reply_to_message=_reply_to(message_id=1, text="src"), quote=quote)
    bot._build_reply_context(msg, sess, bot_id=BOT_ID, allow_file=False)
    assert not (tmp_path / "claude-jim.txt").exists()


def test_highlight_under_1000_chars_never_writes_a_file(mk_bot, tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "reply_context_path", lambda n: tmp_path / f"{n}.txt")
    bot = mk_bot()
    sess = _sess()
    quote = MagicMock(text="short", is_manual=True)
    msg = _msg(reply_to_message=_reply_to(message_id=1, text="src"), quote=quote)
    bot._build_reply_context(msg, sess, bot_id=BOT_ID, allow_file=True)
    assert not (tmp_path / "claude-jim.txt").exists()


# ---- external quote (quote present, no visible reply_to_message) ----------

def test_external_quote_when_no_reply_to_message(mk_bot):
    bot = mk_bot()
    sess = _sess()
    quote = MagicMock(text="fragment from elsewhere", is_manual=True)
    msg = _msg(reply_to_message=None, quote=quote)
    ctx = bot._build_reply_context(msg, sess, bot_id=BOT_ID, allow_file=True)
    assert "fragment from elsewhere" in ctx
    assert "can't otherwise see" in ctx


# ---- whole-message reply to an older message -------------------------------

def test_whole_message_excerpt_capped_at_80_chars_with_ellipsis(mk_bot):
    bot = mk_bot()
    sess = _sess(last_msg_id=999)  # reply target (1) is NOT the latest
    long_text = "a" * 150
    msg = _msg(reply_to_message=_reply_to(message_id=1, text=long_text))
    ctx = bot._build_reply_context(msg, sess, bot_id=BOT_ID, allow_file=False)
    assert ("a" * 80 + "…") in ctx
    assert ("a" * 81) not in ctx


def test_whole_message_writes_full_text_file_when_allow_file(mk_bot, tmp_path, monkeypatch):
    """Criterion 2."""
    monkeypatch.setattr(ps, "reply_context_path", lambda n: tmp_path / f"{n}.txt")
    bot = mk_bot()
    sess = _sess(last_msg_id=999)
    msg = _msg(reply_to_message=_reply_to(message_id=1, text="the full original text"))
    ctx = bot._build_reply_context(msg, sess, bot_id=BOT_ID, allow_file=True)
    p = tmp_path / "claude-jim.txt"
    assert p.exists()
    assert oct(p.stat().st_mode)[-3:] == "600"
    assert "the full original text" in p.read_text()
    assert str(p) in ctx or "claude-jim.txt" in ctx


def test_whole_message_queued_no_file_and_says_not_retained(mk_bot, tmp_path, monkeypatch):
    """Part 4: allow_file=False during BUSY — no file, and the wording
    says so instead of pointing at a path."""
    monkeypatch.setattr(ps, "reply_context_path", lambda n: tmp_path / f"{n}.txt")
    bot = mk_bot()
    sess = _sess(last_msg_id=999)
    msg = _msg(reply_to_message=_reply_to(message_id=1, text="the full original text"))
    ctx = bot._build_reply_context(msg, sess, bot_id=BOT_ID, allow_file=False)
    assert not (tmp_path / "claude-jim.txt").exists()
    assert "not retained" in ctx


def test_nontext_reply_with_no_text_or_caption(mk_bot):
    bot = mk_bot()
    sess = _sess(last_msg_id=999)
    msg = _msg(reply_to_message=_reply_to(message_id=1, text=None, caption=None))
    ctx = bot._build_reply_context(msg, sess, bot_id=BOT_ID, allow_file=True)
    assert "non-text message" in ctx
    assert ctx != ""


def test_nontext_reply_uses_caption_fallback_is_text_if_present(mk_bot):
    """caption alone (no .text) is real content, not the non-text branch."""
    bot = mk_bot()
    sess = _sess(last_msg_id=999)
    msg = _msg(reply_to_message=_reply_to(message_id=1, text=None, caption="a photo caption"))
    ctx = bot._build_reply_context(msg, sess, bot_id=BOT_ID, allow_file=False)
    assert "a photo caption" in ctx


# ---- author attribution ----------------------------------------------------

def test_author_you_when_from_user_matches_bot_id(mk_bot):
    bot = mk_bot()
    sess = _sess(last_msg_id=999)
    msg = _msg(reply_to_message=_reply_to(
        message_id=1, text="hi", from_user=_user(BOT_ID)))
    ctx = bot._build_reply_context(msg, sess, bot_id=BOT_ID, allow_file=False)
    assert "by you" in ctx


def test_author_telegram_user_uses_numeric_id_not_a_label(mk_bot):
    bot = mk_bot()
    sess = _sess(last_msg_id=999)
    other = _user(256113222)
    other.label = "should-never-appear"
    msg = _msg(reply_to_message=_reply_to(message_id=1, text="hi", from_user=other))
    ctx = bot._build_reply_context(msg, sess, bot_id=BOT_ID, allow_file=False)
    assert "Telegram user 256113222" in ctx
    assert "should-never-appear" not in ctx
    assert "from Telegram user 256113222" in ctx  # "from", not "by"


def test_author_none_when_from_user_absent_drops_attribution(mk_bot):
    bot = mk_bot()
    sess = _sess(last_msg_id=999)
    msg = _msg(reply_to_message=_reply_to(message_id=1, text="hi", from_user=None))
    ctx = bot._build_reply_context(msg, sess, bot_id=BOT_ID, allow_file=False)
    assert ", by " not in ctx
    assert ", from " not in ctx


# ---- _resolve_reply_target (design.md Part 0, levels 1-3) ------------------

def test_resolve_reply_target_returns_none_when_reply_to_is_none(mk_bot):
    bot = mk_bot()
    assert bot._resolve_reply_target(None, 4242) is None


def test_resolve_reply_target_level1_exact_msg_map_hit(mk_bot):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    bot.registry._sessions["claude-jim"] = sess
    bot.registry.track_message(300, "claude-jim", 4242)
    reply_to = _reply_to(message_id=300)
    resolved = bot._resolve_reply_target(reply_to, 4242)
    assert resolved is sess


def test_resolve_reply_target_level2_last_msg_id_scan(mk_bot):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    sess.scope_chat_id = 4242
    sess.last_msg_id = 555  # never went through track_message
    bot.registry._sessions["claude-jim"] = sess
    resolved = bot._resolve_reply_target(_reply_to(message_id=555), 4242)
    assert resolved is sess


def test_resolve_reply_target_level3_text_guess(mk_bot):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    sess.scope_chat_id = 4242
    bot.registry._sessions["claude-jim"] = sess
    reply_to = _reply_to(message_id=99999, text="⚙️ jim · Working…")
    resolved = bot._resolve_reply_target(reply_to, 4242)
    assert resolved is sess


def test_resolve_reply_target_returns_none_when_nothing_matches(mk_bot):
    bot = mk_bot()
    reply_to = _reply_to(message_id=99999, text="no label here at all")
    assert bot._resolve_reply_target(reply_to, 4242) is None


def test_resolve_reply_target_cross_chat_collision_never_resolves_wrong_chat(mk_bot):
    """Criterion 8, exercised through the actual production routing
    function (not just the registry primitive)."""
    bot = mk_bot()
    a = TrackedSession(name="claude-a", label="a", status=Status.IDLE)
    a.scope_chat_id = 111
    b = TrackedSession(name="claude-b", label="b", status=Status.IDLE)
    b.scope_chat_id = 222
    bot.registry._sessions["claude-a"] = a
    bot.registry._sessions["claude-b"] = b
    bot.registry.track_message(500, "claude-a", 111)
    bot.registry.track_message(500, "claude-b", 222)

    reply_to = _reply_to(message_id=500)
    resolved_a = bot._resolve_reply_target(reply_to, 111)
    resolved_b = bot._resolve_reply_target(reply_to, 222)
    assert resolved_a is a
    assert resolved_b is b
    # A third, uninvolved chat must resolve to neither (falls through to
    # level 4 in the caller).
    assert bot._resolve_reply_target(reply_to, 333) is None


def test_guess_session_from_text_scoped_excludes_other_chat_same_label(mk_bot):
    """Level 3, scoped: two sessions share a label across two chats —
    without scoping this would be an ambiguous 2-match (returns None);
    scoped to one chat it's an unambiguous 1-match."""
    bot = mk_bot()
    a = TrackedSession(name="claude-jim__d111", label="jim", status=Status.IDLE)
    a.scope_chat_id = 111
    b = TrackedSession(name="claude-jim__d222", label="jim", status=Status.IDLE)
    b.scope_chat_id = 222
    bot.registry._sessions["claude-jim__d111"] = a
    bot.registry._sessions["claude-jim__d222"] = b

    text = "⚙️ jim · Working…"
    assert bot._guess_session_from_text(text, 111) is a
    assert bot._guess_session_from_text(text, 222) is b
    # Unscoped (legacy default) sees both labels and can't disambiguate.
    assert bot._guess_session_from_text(text) is None
