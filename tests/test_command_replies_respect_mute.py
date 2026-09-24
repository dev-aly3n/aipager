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

import logging
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

import aipager.bot as bot_pkg
from aipager.bot import flood, new_flow, session_parity
from tests.sweep_rules import (
    EXEMPT_FILES,
    GATED_FAMILIES,
    UNGATED_BY_CONSTRUCTION,
    URL_ALLOWLIST,
    bot_construction_offenders,
    gated_family_offenders,
    telegram_url_offenders,
    untolerated_send_offenders,
)
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
    """The pinned bar (8.31) stores the message id and records what it
    showed. During a mute nothing is sent, nothing is recorded, and the
    first refresh after the lift sends. Mutation: drop the send's ``is
    MUTED`` return and the sentinel raises ``AttributeError`` into the
    refresh's own catch-all (hence the "no exception logged" assertion);
    record a muted edit as shown and the refresh after the lift is
    skipped as "redundant"."""
    # dashboard.py keeps its own import-time copy of CHAT_ID, empty on a
    # runner with no .env; pin it like conftest pins config.CHAT_ID.
    monkeypatch.setattr("aipager.bot.dashboard.CHAT_ID", "256113222")
    bot = mk_bot()
    bot._app.bot.pin_chat_message = AsyncMock()
    bot._app.bot.edit_message_text = AsyncMock()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.BUSY,
                          scope_chat_id=256113222)
    bot.registry._sessions["claude-jim"] = sess
    MUTE.mute(256113222, BAN)  # the str CHAT_ID above lands on this int entry

    with caplog.at_level(logging.DEBUG):
        run_async(bot.refresh_pinned())
    bot._app.bot.send_message.assert_not_awaited()
    bot._app.bot.pin_chat_message.assert_not_awaited()
    assert not bot.registry.pinned_msg_ids
    assert bot._pinned[256113222].shown is None
    assert not any(r.exc_info for r in caplog.records), \
        "a skipped send is not an error: " + str(
            [r.getMessage() for r in caplog.records if r.exc_info])

    clock(BAN + 1)
    bot._app.bot.send_message.return_value = MagicMock(message_id=77)
    run_async(bot.refresh_pinned())
    bot._app.bot.send_message.assert_awaited_once()
    assert bot.registry.pinned_msg_ids == {256113222: 77}
    shown = bot._pinned[256113222].shown

    # And an edit of the existing pinned bar during a later mute records
    # nothing either, so the next refresh tries again.
    MUTE.mute(256113222, BAN)
    sess.status = Status.IDLE
    run_async(bot.refresh_pinned())
    bot._app.bot.edit_message_text.assert_not_awaited()
    assert bot._pinned[256113222].shown == shown


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
        update, query = _tap("claude-dev:allow", chat, message_id=4242)
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


# ── the routing contract, as four static sweeps (8.26 R2) ───────────────────
#
# The pure predicates live in `tests/sweep_rules.py` so they can be fed
# SYNTHETIC source — proving each one actually fires — without writing a
# file into `aipager/`. Here they are run over the real tree.
#
# What changed in 8.26, and why the old sweep could not have caught the
# 2026-09-15 incident: it walked `aipager/bot/*.py` only (never the Mini
# App, which holds 13 unguarded sends), knew nine method names (never
# `delete_message`, which had nine live sites, nor `send_chat_action`),
# and EXEMPTED `notify.py` and `animation.py` — the two files holding 23
# of the 66 outbound calls, including `send_busy`, the one that fired
# into the ban. The file's own comment admitted the sweep "therefore
# CANNOT protect" them.


def _package_files():
    """Every module the sweeps police: `aipager/bot/` AND `aipager/miniapp/`."""
    root = Path(bot_pkg.__file__).parent.parent
    return sorted(list((root / "bot").glob("*.py"))
                  + list((root / "miniapp").glob("*.py")))


def _sweep(fn, paths):
    offenders = []
    for path in paths:
        offenders += fn(str(path), path.read_text(encoding="utf-8"))
    return offenders


def test_the_exempt_set_is_exactly_the_seam_the_gate_and_the_observer():
    """D-8: `_EXEMPT` shrank from six files to three, and every file that
    left it is a file the sweep now protects — that is the point of moving
    enforcement to the chokepoint.

    Pinned as an EQUALITY, not a subset: re-exempting `notify.py` or
    `animation.py` to make a new offender go away is exactly the move that
    made the 2026-09-15 leak invisible, and it must fail here first.
    """
    assert EXEMPT_FILES == {"transport.py", "flood_budget.py", "observer.py"}
    assert "delete_message" in GATED_FAMILIES
    assert "send_chat_action" in GATED_FAMILIES


def test_the_one_ungated_method_is_recorded_with_its_reason():
    """`query.answer` is a DELIBERATE KEEP, not an oversight (ruling 6).

    `answerCallbackQuery` carries no `chat_id`, so the gate's
    `chat_id is None` short-circuit is what handles it — the same branch
    `getMe` and `getFile` take — and Telegram does not meter it against a
    chat. It is also the only feedback an unauthorized tap can get
    (`auth.py`'s two allow-list toasts, answering a user who is not in the
    conversation). Out of the gate's scope BY CONSTRUCTION rather than as
    an exception to R1.

    This row exists so the next reader of "literally zero-exception" finds
    the decision instead of assuming a miss — and so adding `answer` to
    the gated family, which would make `auth.py` an offender, fails here
    with the reason attached.
    """
    assert set(UNGATED_BY_CONSTRUCTION) == {"answer"}
    assert not set(UNGATED_BY_CONSTRUCTION) & GATED_FAMILIES
    assert "no chat_id" in UNGATED_BY_CONSTRUCTION["answer"]


def test_no_send_in_bot_or_miniapp_runs_on_an_ungated_receiver():
    """Sweep 1 over the real tree.

    A call is an offender unless its receiver IS the daemon's one
    limiter-bound `ExtBot`. `self._app.bot.send_message(...)` is fine now —
    the gate underneath it cannot be bypassed. `message.reply_text(...)`
    and `query.edit_message_text(...)` are NOT: a PTB update object is
    gated by nothing, which is the 8.17b sentinel contract these 27 tests
    depend on.

    Mutation: turn any routed site back into its direct call — e.g.
    `animation._safe_edit_callback`'s `query.edit_message_text` — and this
    names it.
    """
    offenders = _sweep(gated_family_offenders, _package_files())
    assert offenders == [], offenders


def test_the_bot_packages_name_no_telegram_api_url():
    """Sweep 2 (R2). The one in-daemon URL builder is
    `rich_message._api_url`; every other `api.telegram.org` string in the
    daemon is a path around the limiter. The allowlist is out-of-process
    or diagnostic callers only (`doctor`, `cli/daemon`, the wizard,
    observer bots) and is deliberately tiny — each entry is a path R1 does
    not cover.

    Docstrings are skipped, which is what keeps `errors.py`'s explanatory
    prose from tripping it; a real URL in `errors.py` still fails.
    """
    root = Path(bot_pkg.__file__).parent.parent
    offenders = _sweep(telegram_url_offenders, sorted(root.rglob("*.py")))
    assert offenders == [], offenders


def test_no_second_bot_is_constructed_in_the_daemon_packages():
    """Sweep 3, and the reason sweep 1 may allow the bare receiver name
    `bot`: if no second `Bot`/`ApplicationBuilder` can be built here, every
    `bot` in reach is the app's limiter-bound one. The two sweeps are a
    PAIR — weakening this one silently weakens that one.

    `lifecycle.py` builds the app's; `observer.py` builds the observers'
    (own tokens, own budgets, 8.21 §11 D12).
    """
    offenders = _sweep(bot_construction_offenders, _package_files())
    assert offenders == [], offenders


def test_every_send_is_inside_a_try_that_tolerates_the_gate():
    """Sweep 4, and load-bearing (P-3).

    The gate raises `FloodMuted` from inside `process_request`, so a send
    that is not lexically inside a tolerant `try` turns a muted chat into
    an exception that propagates out of its caller. In
    `NotifyMixin.notify` — ONE 1726-line method with ~20 direct sends —
    that would abort the rest of the turn and lose the answer the gate
    exists to protect: the fix becoming a fresh instance of the bug it
    ends.

    Mutation: unwrap any send (e.g. the permission-prompt fallback in
    `notify.notify`) and this names it.
    """
    offenders = _sweep(untolerated_send_offenders, _package_files())
    assert offenders == [], offenders


# ── the same four predicates, proved to actually fire (row D) ───────────────
#
# A sweep that returns [] over the real tree proves nothing unless it also
# returns something over source that SHOULD offend. Synthetic source, so
# nothing is written into `aipager/`.

def test_the_family_sweep_names_a_call_on_a_ptb_update_object():
    src = "async def f(message):\n    await message.reply_text('hi')\n"
    assert gated_family_offenders("aipager/bot/x.py", src) == [
        "x.py:2 message.reply_text("]


def test_the_family_sweep_names_a_send_in_the_miniapp_package():
    """The Mini App was never walked at all before 8.26 — its 13 mirrors
    were invisible to CI by construction."""
    src = "async def f(client):\n    await client.send_message(1, 'hi')\n"
    assert gated_family_offenders("aipager/miniapp/x.py", src) == [
        "x.py:2 client.send_message("]


@pytest.mark.parametrize("method", ["delete_message", "send_chat_action"])
def test_the_family_sweep_names_the_two_families_d8_added(method):
    src = f"async def f(q):\n    await q.{method}(1)\n"
    assert gated_family_offenders("aipager/bot/x.py", src) == [
        f"x.py:2 q.{method}("]


def test_the_family_sweep_allows_the_one_gated_receiver():
    """The whole point of the chokepoint: this is NOT a leak any more."""
    src = "async def f(self):\n    await self._app.bot.send_message(1, 'hi')\n"
    assert gated_family_offenders("aipager/bot/x.py", src) == []


def test_the_family_sweep_skips_the_three_exempt_files():
    src = "async def f(message):\n    await message.reply_text('hi')\n"
    for name in sorted(EXEMPT_FILES):
        assert gated_family_offenders(f"aipager/bot/{name}", src) == [], name


def test_the_url_sweep_names_an_f_string_outside_the_allowlist():
    src = "def f(t):\n    return f'https://api.telegram.org/bot{t}/getMe'\n"
    assert telegram_url_offenders("aipager/bot/x.py", src) == [
        "x.py:2 api.telegram.org"]


def test_the_url_sweep_skips_docstrings_but_not_real_urls():
    """`errors.py` explains the API in prose; that must not fail the
    sweep, and a real URL in the same file still must."""
    doc = '"""Talk to https://api.telegram.org for this."""\n'
    assert telegram_url_offenders("aipager/errors.py", doc) == []
    real = doc + "URL = 'https://api.telegram.org/bot'\n"
    assert telegram_url_offenders("aipager/errors.py", real) == [
        "errors.py:2 api.telegram.org"]


def test_the_url_sweep_allows_the_builder_and_the_out_of_process_callers():
    src = "URL = 'https://api.telegram.org/bot'\n"
    assert telegram_url_offenders("aipager/bot/rich_message.py", src) == []
    for name in sorted(URL_ALLOWLIST):
        assert telegram_url_offenders(f"aipager/{name}", src) == [], name


@pytest.mark.parametrize("ctor", ["Bot", "ApplicationBuilder"])
def test_the_constructor_sweep_names_a_second_bot(ctor):
    src = f"def f(t):\n    return {ctor}(token=t)\n"
    assert bot_construction_offenders("aipager/bot/x.py", src) == [
        f"x.py:2 {ctor}("]
    assert bot_construction_offenders("aipager/bot/lifecycle.py", src) == []
    assert bot_construction_offenders("aipager/bot/observer.py", src) == []


def test_the_tolerance_sweep_names_a_send_outside_a_try():
    src = "async def f(self):\n    await self._app.bot.send_message(1, 'x')\n"
    assert untolerated_send_offenders("aipager/bot/x.py", src) == [
        "x.py:2 .send_message( not inside a tolerant try"]


def test_the_tolerance_sweep_names_a_send_in_an_intolerant_try():
    """`except BadRequest` does not catch `FloodMuted`. A try that does
    not tolerate the gate is no better than no try at all."""
    src = ("async def f(self):\n"
           "    try:\n"
           "        await self._app.bot.send_message(1, 'x')\n"
           "    except BadRequest:\n"
           "        pass\n")
    assert untolerated_send_offenders("aipager/bot/x.py", src) == [
        "x.py:3 .send_message( not inside a tolerant try"]


@pytest.mark.parametrize("arm", ["FloodMuted", "Exception", "BaseException"])
def test_the_tolerance_sweep_accepts_a_handler_that_catches_the_gate(arm):
    src = ("async def f(self):\n"
           "    try:\n"
           "        await self._app.bot.send_message(1, 'x')\n"
           f"    except {arm}:\n"
           "        pass\n")
    assert untolerated_send_offenders("aipager/bot/x.py", src) == []
