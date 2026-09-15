"""Plain command replies respect the flood mute (roadmap 8.17b).

8.17 stopped the daemon firing into a Telegram flood ban on its own; this
stops it firing once per human tap. Every ``update.message.reply_text``,
``query.edit_message_text``, ``bot.send_message``, ``reply_document`` and
``message.edit_text`` in the command/callback mixins now goes through the
seam in ``bot/transport.py`` (``reply_text``, ``edit_text``, ``edit_text_at``,
``send_text``, ``reply_document``, ``edit_message``), which returns
``MUTED`` — no exception, no log line, no Telegram call — while the target
chat is muted, and passes the call through untouched otherwise.

Every guard here is mutation-verified: remove it and the named test fails.
The mute is always armed through the registry's real API (``MUTE.mute``),
never by patching ``is_muted``; the clock is driven the way
``tests/test_flood_mute.py`` drives it. No test reaches Telegram: every
bot / message / query is an ``AsyncMock``-bearing double.
"""

from __future__ import annotations

import ast
import logging
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

import aipager.bot as bot_pkg
from aipager.bot import flood, new_flow, session_parity
from aipager.bot.flood import MUTE
from aipager.bot.transport import (
    MUTED,
    edit_markup,
    edit_message,
    edit_text,
    edit_text_at,
    reply_document,
    reply_text,
    send_text,
)
from aipager.state import Status, TrackedSession

MUTED_CHAT = -100
OTHER_CHAT = -200
BAN = 28911  # the retry_after of the real 2026-09-10 ban


# ── fixtures / doubles ───────────────────────────────────────────────────────

@pytest.fixture
def clock(monkeypatch):
    """Fake monotonic + wall clocks bound to flood.py's OWN ``time``
    reference — never the global module, which asyncio's loop reads."""
    state = {"mono": 10_000.0, "wall": 1_800_000_000.0}
    fake = types.SimpleNamespace(
        monotonic=lambda: state["mono"], time=lambda: state["wall"],
    )
    monkeypatch.setattr(flood, "time", fake)

    def advance(seconds: float) -> None:
        state["mono"] += seconds
        state["wall"] += seconds

    return advance


def _message(chat_id, message_id=42):
    """A Message double that knows its chat — the seam resolves the mute
    from ``message.chat.id``, so every double must carry a real one."""
    msg = MagicMock()
    msg.chat = MagicMock()
    msg.chat.id = chat_id
    msg.message_id = message_id
    msg.reply_text = AsyncMock(return_value=MagicMock(message_id=900))
    msg.reply_document = AsyncMock()
    msg.edit_text = AsyncMock()
    return msg


def _command(mk_update, text, chat_id, **kw):
    """``mk_update`` plus the message's own chat id (the fixture only
    stamps ``effective_chat``)."""
    update = mk_update(text, chat_id=chat_id, **kw)
    update.message.chat = MagicMock()
    update.message.chat.id = chat_id
    update.message.reply_text = AsyncMock(return_value=MagicMock(message_id=900))
    return update


def _tap(callback_data, chat_id, *, user_id=12345, message_id=42):
    query = MagicMock()
    query.data = callback_data
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.edit_message_reply_markup = AsyncMock()
    query.message = _message(chat_id, message_id)
    query.message.text = ""
    query.from_user = MagicMock()
    query.from_user.id = user_id
    update = MagicMock()
    update.callback_query = query
    update.effective_user = query.from_user
    update.effective_chat = MagicMock()
    update.effective_chat.id = chat_id
    return update, query


def _wizard_bot(mk_bot):
    bot = mk_bot()
    bot._app.bot.edit_message_text = AsyncMock()
    bot._app.bot.edit_message_reply_markup = AsyncMock()
    return bot


def _big_diff(monkeypatch):
    big_patch = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n" + "+line\n" * 2000
    files = [{"path": "a.py", "change_type": "modified", "binary": False,
              "truncated": False, "patch": big_patch}]
    monkeypatch.setattr(
        "aipager.miniapp.diff.collect_diff",
        AsyncMock(return_value={"available": True, "files": files,
                                "files_truncated": False}),
    )


# ── the seam itself ──────────────────────────────────────────────────────────

def test_muted_is_falsy_and_carries_no_message_id():
    assert not MUTED
    assert repr(MUTED) == "MUTED"
    assert not hasattr(MUTED, "message_id")


def test_reply_text_skips_a_muted_chat_and_passes_through_otherwise(run_async, clock):
    """Mutation: drop the ``is_muted`` check in ``reply_text`` and the
    muted message's ``reply_text`` is awaited."""
    MUTE.mute(MUTED_CHAT, BAN)
    muted, other = _message(MUTED_CHAT), _message(OTHER_CHAT)
    kb = object()
    assert run_async(reply_text(muted, "hi", parse_mode="HTML", reply_markup=kb)) is MUTED
    muted.reply_text.assert_not_awaited()
    sent = run_async(reply_text(other, "hi", parse_mode="HTML", reply_markup=kb))
    other.reply_text.assert_awaited_once_with("hi", parse_mode="HTML", reply_markup=kb)
    assert sent is other.reply_text.return_value, "the Telegram result is handed back"


def test_reply_document_skips_a_muted_chat_and_passes_through_otherwise(run_async, clock):
    MUTE.mute(MUTED_CHAT, BAN)
    muted, other = _message(MUTED_CHAT), _message(OTHER_CHAT)
    assert run_async(reply_document(muted, document=b"x", filename="a.diff")) is MUTED
    muted.reply_document.assert_not_awaited()
    run_async(reply_document(other, document=b"x", filename="a.diff"))
    other.reply_document.assert_awaited_once_with(document=b"x", filename="a.diff")


def test_edit_message_skips_a_muted_chat_and_passes_through_otherwise(run_async, clock):
    MUTE.mute(MUTED_CHAT, BAN)
    muted, other = _message(MUTED_CHAT), _message(OTHER_CHAT)
    assert run_async(edit_message(muted, "new", parse_mode="HTML")) is MUTED
    muted.edit_text.assert_not_awaited()
    run_async(edit_message(other, "new", parse_mode="HTML"))
    other.edit_text.assert_awaited_once_with("new", parse_mode="HTML")


def test_edit_message_on_a_reply_that_was_never_sent_returns_muted_without_raising(run_async):
    """The ``message`` a caller holds may itself be ``MUTED`` (its reply
    was skipped). Mutation: drop the ``message is MUTED`` arm and this
    raises ``AttributeError`` instead — the /new and voice flows edit
    their acknowledgement exactly like this."""
    assert run_async(edit_message(MUTED, "❌ failed")) is MUTED


def test_edit_text_skips_a_muted_chat_and_passes_through_otherwise(run_async, clock):
    MUTE.mute(MUTED_CHAT, BAN)
    _, muted = _tap("x:y", MUTED_CHAT)
    _, other = _tap("x:y", OTHER_CHAT)
    assert run_async(edit_text(muted, "t", reply_markup=None)) is MUTED
    muted.edit_message_text.assert_not_awaited()
    run_async(edit_text(other, "t", reply_markup=None))
    other.edit_message_text.assert_awaited_once_with("t", reply_markup=None)


def test_edit_text_at_resolves_the_chat_from_keyword_or_second_positional(run_async, clock):
    """Both call shapes in the tree: ``edit_message_text(chat_id=…,
    message_id=…, text=…)`` (new_flow) and ``edit_message_text(text, chat,
    msg_id)`` (the pinned dashboard). Mutation: read position 0 instead
    of 1 and the positional form sends into the mute."""
    MUTE.mute(MUTED_CHAT, BAN)
    tg = MagicMock()
    tg.edit_message_text = AsyncMock()
    assert run_async(edit_text_at(tg, chat_id=MUTED_CHAT, message_id=1, text="t")) is MUTED
    assert run_async(edit_text_at(tg, "t", MUTED_CHAT, 1, parse_mode="HTML")) is MUTED
    tg.edit_message_text.assert_not_awaited()
    run_async(edit_text_at(tg, chat_id=OTHER_CHAT, message_id=1, text="t"))
    tg.edit_message_text.assert_awaited_once_with(chat_id=OTHER_CHAT, message_id=1, text="t")
    tg.edit_message_text.reset_mock()
    run_async(edit_text_at(tg, "t", OTHER_CHAT, 1, parse_mode="HTML"))
    tg.edit_message_text.assert_awaited_once_with("t", OTHER_CHAT, 1, parse_mode="HTML")


def test_send_text_resolves_the_chat_from_keyword_or_first_positional(run_async, clock):
    MUTE.mute(MUTED_CHAT, BAN)
    tg = MagicMock()
    tg.send_message = AsyncMock()
    assert run_async(send_text(tg, MUTED_CHAT, "t", parse_mode="HTML")) is MUTED
    assert run_async(send_text(tg, chat_id=MUTED_CHAT, text="t")) is MUTED
    tg.send_message.assert_not_awaited()
    run_async(send_text(tg, OTHER_CHAT, "t", parse_mode="HTML"))
    tg.send_message.assert_awaited_once_with(OTHER_CHAT, "t", parse_mode="HTML")
    tg.send_message.reset_mock()
    run_async(send_text(tg, chat_id=OTHER_CHAT, text="t"))
    tg.send_message.assert_awaited_once_with(chat_id=OTHER_CHAT, text="t")


def test_the_legacy_str_chat_id_hits_the_same_mute_as_the_int(run_async, clock):
    """``config.CHAT_ID`` is a str; the rich path mutes by int. One entry."""
    MUTE.mute(256113222, BAN)
    tg = MagicMock()
    tg.send_message = AsyncMock()
    assert run_async(send_text(tg, "256113222", "t")) is MUTED
    tg.send_message.assert_not_awaited()


def test_an_unresolvable_target_is_sent_to_never_silently_blocked(run_async, clock):
    """A message double with no chat, a query with no message, a bot call
    with no chat id: the seam has nothing to look up, so it sends — a
    mute must never turn into a mystery drop elsewhere."""
    MUTE.mute(MUTED_CHAT, BAN)
    msg = MagicMock(spec=["reply_text"])
    msg.reply_text = AsyncMock()
    run_async(reply_text(msg, "t"))
    msg.reply_text.assert_awaited_once_with("t")
    query = MagicMock(spec=["edit_message_text"])
    query.edit_message_text = AsyncMock()
    run_async(edit_text(query, "t"))
    query.edit_message_text.assert_awaited_once_with("t")
    tg = MagicMock()
    tg.send_message = AsyncMock()
    run_async(send_text(tg, text="t"))
    tg.send_message.assert_awaited_once_with(text="t")


def test_a_skipped_send_logs_nothing(run_async, clock, caplog):
    """R1: nothing per call. The registry's one arming warning is the
    only audible line, however many replies the mute swallows."""
    with caplog.at_level(logging.DEBUG):
        MUTE.mute(MUTED_CHAT, BAN)
        msg = _message(MUTED_CHAT)
        for _ in range(25):
            run_async(reply_text(msg, "poke"))
            run_async(edit_message(msg, "poke"))
    ours = [r for r in caplog.records if r.name.startswith("aipager.bot")]
    assert [r.levelno for r in ours] == [logging.WARNING], \
        [(r.name, r.getMessage()) for r in ours]


# ── the four user actions the task names ─────────────────────────────────────

def test_status_makes_no_send_while_its_chat_is_muted_and_another_chat_still_gets_one(
    mk_bot, mk_update, run_async, clock, monkeypatch,
):
    """Mutation: bypass the seam in ``_handle_status`` (call
    ``update.message.reply_text`` directly) and the muted chat gets a send."""
    monkeypatch.setattr("aipager.dtach.inject.list_sessions", AsyncMock(return_value=[]))
    bot = mk_bot()
    MUTE.mute(MUTED_CHAT, BAN)

    muted = _command(mk_update, "/status", MUTED_CHAT)
    run_async(bot._handle_status(muted, MagicMock()))
    muted.message.reply_text.assert_not_awaited()
    bot._app.bot.send_message.assert_not_awaited()

    other = _command(mk_update, "/status", OTHER_CHAT)
    run_async(bot._handle_status(other, MagicMock()))
    other.message.reply_text.assert_awaited_once()
    assert "No sessions" in other.message.reply_text.await_args.args[0]


def test_callback_edit_and_its_toast_are_both_skipped_while_muted(
    mk_bot, run_async, clock,
):
    """8.26 D-1: the toast is no longer the exception. ``answerCallbackQuery``
    is a request into the chat like any other, and a request into an ACTIVE
    ban is what escalated retry_after 1283 -> 312 -> 34212 on 2026-09-15 —
    the separate rate-limit bucket is about PACING, not about bans. It
    carries no ``chat_id``, so the limiter's gate cannot see it; the check
    lives in ``callbacks._safe_answer``, the one toast chokepoint.

    Mutation: delete that check and ``query.answer`` is awaited here.
    """
    bot = mk_bot()
    MUTE.mute(MUTED_CHAT, BAN)

    update, query = _tap("claude-jim:kill-cancel", MUTED_CHAT)
    run_async(bot._handle_callback(update, MagicMock()))
    query.edit_message_text.assert_not_awaited()
    query.answer.assert_not_awaited()

    update, query = _tap("claude-jim:kill-cancel", OTHER_CHAT)
    run_async(bot._handle_callback(update, MagicMock()))
    query.edit_message_text.assert_awaited_once()
    assert "Cancelled" in query.edit_message_text.await_args.args[0]


def test_a_toast_with_text_is_not_sent_into_a_muted_chat(mk_bot, run_async, clock):
    """8.26 D-1: even a toast that carries real information ("Session not
    found") is withheld while the chat is banned. The tap goes
    unacknowledged, exactly as it does while the daemon is busy — which is
    cheaper than the hours a fresh violation adds to the ban.

    Mutation: delete ``_safe_answer``'s mute check and a "not found" toast
    is awaited here.
    """
    bot = mk_bot()
    MUTE.mute(MUTED_CHAT, BAN)
    update, query = _tap("claude-nope:stop", MUTED_CHAT)
    run_async(bot._handle_callback(update, MagicMock()))
    query.answer.assert_not_awaited()
    query.edit_message_text.assert_not_awaited()
    bot._app.bot.send_message.assert_not_awaited()

    # ...and an unmuted chat still gets the very toast that was withheld.
    update, query = _tap("claude-nope:stop", OTHER_CHAT)
    run_async(bot._handle_callback(update, MagicMock()))
    toasts = [c.args[0] for c in query.answer.await_args_list if c.args]
    assert any("not found" in (t or "").lower() for t in toasts)


def test_new_wizard_start_makes_no_send_and_seeds_no_wizard_while_muted(
    mk_bot, mk_update, run_async, clock,
):
    """The return-value contract: ``start_wizard`` stores
    ``sent.message_id``. Mutation: drop its ``sent is MUTED`` return and
    this raises ``AttributeError`` on the sentinel — and had it survived,
    it would seed a wizard whose prompt the user never saw, swallowing
    their next message as a session name."""
    bot = _wizard_bot(mk_bot)
    MUTE.mute(MUTED_CHAT, BAN)

    muted = _command(mk_update, "/new", MUTED_CHAT, user_id=111)
    run_async(new_flow.start_wizard(bot, muted, MagicMock()))
    muted.message.reply_text.assert_not_awaited()
    assert MUTED_CHAT not in getattr(bot, "_new_wizard_pending", {})

    other = _command(mk_update, "/new", OTHER_CHAT, user_id=111)
    run_async(new_flow.start_wizard(bot, other, MagicMock()))
    other.message.reply_text.assert_awaited_once()
    assert bot._new_wizard_pending[OTHER_CHAT]["msg_id"] == 900


def test_new_wizard_name_step_makes_no_edit_while_muted_and_another_chat_still_does(
    mk_bot, mk_update, run_async, clock,
):
    """Mutation: bypass the seam in ``new_flow._edit_wizard`` and the
    muted chat's wizard message is edited into the ban."""
    bot = _wizard_bot(mk_bot)
    muted = _command(mk_update, "/new", MUTED_CHAT, user_id=111)
    other = _command(mk_update, "/new", OTHER_CHAT, user_id=222)
    run_async(new_flow.start_wizard(bot, muted, MagicMock()))
    run_async(new_flow.start_wizard(bot, other, MagicMock()))
    assert set(bot._new_wizard_pending) == {MUTED_CHAT, OTHER_CHAT}

    MUTE.mute(MUTED_CHAT, BAN)
    assert run_async(new_flow.maybe_handle_text(bot, muted, MagicMock(), "alpha")) is True
    bot._app.bot.edit_message_text.assert_not_awaited()

    assert run_async(new_flow.maybe_handle_text(bot, other, MagicMock(), "beta")) is True
    bot._app.bot.edit_message_text.assert_awaited_once()
    assert bot._app.bot.edit_message_text.await_args.kwargs["chat_id"] == OTHER_CHAT


def test_diff_document_makes_no_send_while_muted_and_another_chat_still_gets_it(
    mk_bot, mk_update, run_async, clock, monkeypatch,
):
    """The one document path a tap can reach (``/diff`` with a large patch:
    a stat line, then ``reply_document``). Mutation: bypass the seam for
    ``reply_document`` in ``_run_diff`` and the muted chat gets the file."""
    _big_diff(monkeypatch)
    bot = mk_bot()
    sess = TrackedSession(name="claude-dev", label="dev", cwd="/nonexistent/proj")
    bot.registry._sessions[sess.name] = sess
    MUTE.mute(MUTED_CHAT, BAN)

    muted = _command(mk_update, "/diff dev", MUTED_CHAT)
    muted.message.reply_document = AsyncMock()
    run_async(session_parity.handle_diff_cmd(bot, muted, MagicMock()))
    muted.message.reply_text.assert_not_awaited()
    muted.message.reply_document.assert_not_awaited()

    other = _command(mk_update, "/diff dev", OTHER_CHAT)
    other.message.reply_document = AsyncMock()
    run_async(session_parity.handle_diff_cmd(bot, other, MagicMock()))
    other.message.reply_text.assert_awaited_once()
    other.message.reply_document.assert_awaited_once()
    assert other.message.reply_document.await_args.kwargs["filename"] == "dev.diff"


def test_after_the_mute_lapses_the_same_actions_send_again(
    mk_bot, mk_update, run_async, clock, monkeypatch,
):
    monkeypatch.setattr("aipager.dtach.inject.list_sessions", AsyncMock(return_value=[]))
    bot = mk_bot()
    MUTE.mute(MUTED_CHAT, BAN)

    update = _command(mk_update, "/status", MUTED_CHAT)
    run_async(bot._handle_status(update, MagicMock()))
    update.message.reply_text.assert_not_awaited()
    tap, query = _tap("claude-jim:kill-cancel", MUTED_CHAT)
    run_async(bot._handle_callback(tap, MagicMock()))
    query.edit_message_text.assert_not_awaited()

    clock(BAN + 1)

    run_async(bot._handle_status(update, MagicMock()))
    update.message.reply_text.assert_awaited_once()
    run_async(bot._handle_callback(tap, MagicMock()))
    query.edit_message_text.assert_awaited_once()


# ── the other return-value consumers ─────────────────────────────────────────

def test_perms_prompt_muted_leaves_nothing_pending_and_does_not_raise(
    mk_bot, mk_update, run_async, clock,
):
    """``/perms`` stores ``sent.message_id`` for the confirm tap. Mutation:
    drop the ``sent is MUTED`` return and this raises on the sentinel."""
    bot = mk_bot()
    sess = TrackedSession(name="claude-dev", label="dev", status=Status.IDLE)
    sess.skip_perms = False
    bot.registry._sessions["claude-dev"] = sess
    bot.registry.last_active_session = "claude-dev"
    bot._is_admin = MagicMock(return_value=True)
    MUTE.mute(MUTED_CHAT, BAN)

    update = _command(mk_update, "/perms", MUTED_CHAT)
    run_async(bot._handle_perms_cmd(update, MagicMock()))
    update.message.reply_text.assert_not_awaited()
    assert "claude-dev" not in bot._perms_pending

    update = _command(mk_update, "/perms", OTHER_CHAT)
    run_async(bot._handle_perms_cmd(update, MagicMock()))
    update.message.reply_text.assert_awaited_once()
    assert bot._perms_pending["claude-dev"]["msg_id"] == 900


def test_perms_busy_prompt_muted_leaves_nothing_pending_and_does_not_raise(
    mk_bot, mk_update, run_async, clock,
):
    bot = mk_bot()
    sess = TrackedSession(name="claude-dev", label="dev", status=Status.BUSY)
    sess.skip_perms = False
    bot.registry._sessions["claude-dev"] = sess
    bot.registry.last_active_session = "claude-dev"
    bot._is_admin = MagicMock(return_value=True)
    MUTE.mute(MUTED_CHAT, BAN)

    update = _command(mk_update, "/perms", MUTED_CHAT)
    run_async(bot._handle_perms_cmd(update, MagicMock()))
    update.message.reply_text.assert_not_awaited()
    assert "claude-dev" not in bot._perms_pending


def test_pinned_dashboard_muted_remembers_nothing_and_retries_after_the_lift(
    mk_bot, run_async, clock, caplog, monkeypatch,
):
    """The pinned status line stores ``msg.message_id`` and records the
    text as shown. Mutation: drop the send's ``is MUTED`` return and the
    sentinel raises ``AttributeError`` into the method's own catch-all
    (hence the "no exception logged" assertion); drop the edit's and a
    text that never went out is recorded as shown, so the first refresh
    after the lift is skipped as "redundant"."""
    # dashboard.py keeps its own import-time copy of CHAT_ID, empty on a
    # runner with no .env; pin it like conftest pins config.CHAT_ID.
    monkeypatch.setattr("aipager.bot.dashboard.CHAT_ID", "256113222")
    bot = mk_bot()
    bot._app.bot.pin_chat_message = AsyncMock()
    bot._app.bot.edit_message_text = AsyncMock()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
    bot.registry._sessions["claude-jim"] = sess
    MUTE.mute(256113222, BAN)  # the str CHAT_ID above lands on this int entry

    with caplog.at_level(logging.DEBUG):
        run_async(bot._maybe_update_bot_name("claude-jim"))
    bot._app.bot.send_message.assert_not_awaited()
    bot._app.bot.pin_chat_message.assert_not_awaited()
    assert not bot.registry.pinned_msg_id
    assert bot._last_pinned_text != bot._build_pinned_text("claude-jim")
    assert not any(r.exc_info for r in caplog.records), \
        "a skipped send is not an error: " + str(
            [r.getMessage() for r in caplog.records if r.exc_info])

    clock(BAN + 1)
    bot._app.bot.send_message.return_value = MagicMock(message_id=77)
    run_async(bot._maybe_update_bot_name("claude-jim"))
    bot._app.bot.send_message.assert_awaited_once()
    assert bot.registry.pinned_msg_id == 77

    # And an edit of the existing pinned line during a later mute records
    # nothing either, so the next refresh tries again. (The line changes
    # with the model name; the active session's status is not shown.)
    MUTE.mute(256113222, BAN)
    sess.model_name = "Opus 4.7"
    run_async(bot._maybe_update_bot_name("claude-jim"))
    bot._app.bot.edit_message_text.assert_not_awaited()
    assert bot._last_pinned_text != bot._build_pinned_text("claude-jim")


# ── the seam's markup helper + the settings "Close" tap ──────────────────────

def test_edit_markup_skips_a_muted_chat_and_passes_through_otherwise(
    run_async, clock,
):
    """Mutation: drop ``edit_markup``'s check and the muted chat gets an
    editMessageReplyMarkup — a real send, metered like any other."""
    MUTE.mute(MUTED_CHAT, BAN)
    _u, muted = _tap("_:set:close", MUTED_CHAT)
    assert run_async(edit_markup(muted, reply_markup=None)) is MUTED
    muted.edit_message_reply_markup.assert_not_awaited()

    _u, other = _tap("_:set:close", OTHER_CHAT)
    run_async(edit_markup(other, reply_markup=None))
    other.edit_message_reply_markup.assert_awaited_once()


def test_settings_close_tap_makes_no_markup_edit_and_no_toast_while_muted(
    mk_bot, run_async, clock,
):
    """The settings menu's Close strips its keyboard with
    ``edit_message_reply_markup`` — the one send family the first pass of
    the static sweep could not see. Since 8.26 D-1 its toast is withheld
    too: both are requests into the banned chat."""
    bot = mk_bot()
    MUTE.mute(MUTED_CHAT, BAN)

    update, query = _tap("_:set:close", MUTED_CHAT)
    run_async(bot._handle_callback(update, MagicMock()))
    query.edit_message_reply_markup.assert_not_awaited()
    query.answer.assert_not_awaited()

    update, query = _tap("_:set:close", OTHER_CHAT)
    run_async(bot._handle_callback(update, MagicMock()))
    query.edit_message_reply_markup.assert_awaited_once()


# ── the busy card's two tap-driven primitives (animation.py) ─────────────────

def test_safe_edit_callback_skips_a_muted_chat_and_passes_through_otherwise(
    mk_bot, run_async, clock,
):
    """``_safe_edit_callback`` drives the ``__voice__:install`` progress
    edits (handlers.py 305-471). Mutation: drop its check and the muted
    chat gets an edit."""
    bot = mk_bot()
    MUTE.mute(MUTED_CHAT, BAN)

    _u, muted = _tap("__voice__:install", MUTED_CHAT)
    run_async(bot._safe_edit_callback(muted, "installing…"))
    muted.edit_message_text.assert_not_awaited()

    _u, other = _tap("__voice__:install", OTHER_CHAT)
    run_async(bot._safe_edit_callback(other, "installing…"))
    other.edit_message_text.assert_awaited_once()


def test_edit_busy_raw_skips_a_muted_chat_and_returns_false_never_none(
    mk_bot, run_async, clock,
):
    """False, not None. Every caller that inspects the result treats
    ``None`` as "message gone" and clears ``busy_msg_id``
    (``animation.py:1668``, ``notify.py:1407,1466,2302``) — returning it
    for a transient ban would throw the card away for good."""
    bot = mk_bot()
    bot._app.bot.edit_message_text = AsyncMock()
    MUTE.mute(MUTED_CHAT, BAN)

    assert run_async(bot._edit_busy_raw(42, "t", chat_id=MUTED_CHAT)) is False
    bot._app.bot.edit_message_text.assert_not_awaited()

    assert run_async(bot._edit_busy_raw(42, "t", chat_id=OTHER_CHAT)) is True
    bot._app.bot.edit_message_text.assert_awaited_once()


def test_edit_busy_raw_resolves_the_global_chat_id_when_the_caller_passes_none(
    mk_bot, run_async, clock, monkeypatch,
):
    """Every ``_edit_busy_raw`` in callbacks.py passes no ``chat_id``, so
    the mute has to resolve through animation.py's own import-time copy
    of CHAT_ID (empty on a runner with no .env — pin it, as the dashboard
    test does)."""
    monkeypatch.setattr("aipager.bot.animation.CHAT_ID", str(MUTED_CHAT))
    bot = mk_bot()
    bot._app.bot.edit_message_text = AsyncMock()
    MUTE.mute(MUTED_CHAT, BAN)

    assert run_async(bot._edit_busy_raw(42, "t")) is False
    bot._app.bot.edit_message_text.assert_not_awaited()


def test_permission_answer_tap_makes_no_card_edit_while_muted_and_another_chat_does(
    mk_bot, run_async, clock, monkeypatch,
):
    """The most common tap on this product. Mutation: drop
    ``_edit_busy_raw``'s check and the muted chat gets one
    editMessageText per permission answer."""
    monkeypatch.setattr("aipager.dtach.inject.send_keys", AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    MUTE.mute(MUTED_CHAT, BAN)

    for chat, expected in ((MUTED_CHAT, 0), (OTHER_CHAT, 1)):
        monkeypatch.setattr("aipager.bot.animation.CHAT_ID", str(chat))
        bot = mk_bot()
        bot._app.bot.edit_message_text = AsyncMock()
        bot._start_animation = MagicMock()
        sess = TrackedSession(name="claude-dev", label="dev",
                              status=Status.INTERACTIVE)
        sess.busy_msg_id = 4242
        sess.pending_permission = {"tool_summary": "Bash: x",
                                   "tool_info": {"name": "Bash"}}
        bot.registry._sessions["claude-dev"] = sess
        update, query = _tap("claude-dev:allow", chat)
        run_async(bot._handle_callback(update, MagicMock()))
        assert bot._app.bot.edit_message_text.await_count == expected, chat
        # 8.26 D-1: the toast follows the edit. It used to fire either
        # way ("a separate bucket"); a request into an active ban extends
        # it whatever endpoint it names. The unmuted chat gets more than
        # one (the eager ack, then the result), so this asserts presence
        # rather than a count.
        if expected:
            assert query.answer.await_count >= 1, chat
        else:
            query.answer.assert_not_awaited()


def test_stop_makes_no_card_edit_while_muted_and_another_chat_still_does(
    mk_bot, run_async, clock, monkeypatch,
):
    """``/stop`` and the Stop button both land on ``_stop_session``'s
    "· Stopped" edit."""
    monkeypatch.setattr("aipager.dtach.inject.send_keys", AsyncMock(return_value=True))
    MUTE.mute(MUTED_CHAT, BAN)

    for chat, expected in ((MUTED_CHAT, 0), (OTHER_CHAT, 1)):
        monkeypatch.setattr("aipager.bot.animation.CHAT_ID", str(chat))
        bot = mk_bot()
        bot._app.bot.edit_message_text = AsyncMock()
        bot._stop_animation = MagicMock()
        sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY)
        sess.busy_msg_id = 4242
        bot.registry._sessions["claude-jim"] = sess
        run_async(bot._stop_session(sess))
        assert bot._app.bot.edit_message_text.await_count == expected, chat


# ── the deferred-keyboard hold ───────────────────────────────────────────────

def test_a_muted_keyboard_send_keeps_the_hold_and_sends_once_after_the_lift(
    mk_bot, run_async, clock,
):
    """The hold is cleared before the send on purpose — a *failed*
    attempt still counts. A muted send is not an attempt, so the flags
    must survive; otherwise a main keyboard suppressed for the length of
    a ban is never re-sent, which is the exact "buttonless until some
    unrelated later event" bug the hold exists to prevent."""
    bot = mk_bot()
    bot._keyboard_level = "templates"
    bot._keyboard_deferred = True
    MUTE.mute(MUTED_CHAT, BAN)

    run_async(bot._send_keyboard(level="main", chat_id=MUTED_CHAT))
    bot._app.bot.send_message.assert_not_awaited()
    assert bot._keyboard_deferred is True
    assert bot._keyboard_level == "templates"

    clock(BAN + 1)
    run_async(bot._send_keyboard(level="main", chat_id=MUTED_CHAT))
    bot._app.bot.send_message.assert_awaited_once()
    assert bot._keyboard_deferred is False
    assert bot._keyboard_level == "main"


# ── the routing contract, as a static sweep ──────────────────────────────────

_GATED_FAMILIES = {
    "reply_text", "reply_document", "reply_photo", "edit_message_text",
    "send_message", "send_document", "send_photo", "edit_text",
    # Added after review-1: the settings "Close" tap stripped its keyboard
    # with this one, a real send the first pass of this sweep could not
    # see because the family list did not name it.
    "edit_message_reply_markup",
}
# Files whose direct calls are gated elsewhere or exempt by design (R3):
_EXEMPT = {
    "transport.py",     # the seam itself and `_send_with_retry` (8.17)
    "notify.py",        # 8.17: per-site mute checks on the delivery paths
    "animation.py",     # 8.17: `_animate_tick` / `_edit_busy_rich`, plus
                        # 8.17b's `_edit_busy_raw` / `_safe_edit_callback`
                        # guards — which this sweep therefore CANNOT
                        # protect; the N1/N3 behavioural tests above are
                        # their only guard.
    "rich_message.py",  # 8.17: `_raise_if_muted` in the rich HTTP path
    "observer.py",      # observer bots: their own tokens, their own budgets
    "lifecycle.py",     # startup: runs right after `MUTE.clear()`, and its
                        # startup notice goes through `_send_with_retry`
}


def test_no_plain_reply_in_the_bot_package_bypasses_the_seam():
    """Walk every ``ast.Call`` in the bot mixins: a direct
    ``<anything>.reply_text(...)`` (or any other gated family) outside
    the exempt files is a send that ignores the mute. Mutation: turn any
    routed site back into its direct call and this names it."""
    offenders = []
    for path in sorted(Path(bot_pkg.__file__).parent.glob("*.py")):
        if path.name in _EXEMPT:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in _GATED_FAMILIES):
                offenders.append(f"{path.name}:{node.lineno} .{node.func.attr}(")
    assert offenders == [], offenders
