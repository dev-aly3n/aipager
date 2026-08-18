"""Black-box integration test for design.md success criterion 8 -- "the
whole security point" per the task brief.

Each test here actually creates the SAME Telegram message id in TWO
DIFFERENT chats and drives a real ``TelegramBotCore._handle_message``
call for each, asserting resolution lands on the correct chat's session
in BOTH directions -- never the other chat's session, never a third,
uninvolved chat. Covers all three levels of the reply-resolution ladder
design.md describes:

  - Level 1: the ``_msg_map`` exact hit (``track_message``).
  - Level 2: the ``last_msg_id`` scan fallback (no ``track_message``
    call for the colliding id at all -- forces the ladder past level 1).
  - Level 3: the text-guess fallback (neither ``track_message`` nor a
    matching ``last_msg_id`` -- forces the ladder to the label-in-text
    guess, scoped by chat).

A fix that only scopes level 1 while leaving level 2 or 3 unscoped would
pass a level-1-only test trivially while still leaking -- hence all
three are exercised independently here, each verified via the actual
routing OUTCOME (which session went BUSY), not via any internal
resolver call.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from aipager.state import Status, TrackedSession

CHAT_A = 111
CHAT_B = 222
CHAT_C = 333  # uninvolved third chat
BOT_ID = 87654321
COLLIDING_MSG_ID = 500


def _sess(name, label, chat_id):
    s = TrackedSession(name=name, label=label, status=Status.IDLE)
    s.scope_chat_id = chat_id
    return s


def _ctx():
    c = MagicMock()
    c.bot.id = BOT_ID
    return c


def _wire_happy_dtach(monkeypatch, bot):
    monkeypatch.setattr("aipager.dtach.inject.is_alive", AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter",
                        AsyncMock(return_value=True))
    bot._send_busy_and_animate = AsyncMock()
    bot._react = AsyncMock()


def _reply_target(*, message_id, text="(colliding)"):
    m = MagicMock()
    m.message_id = message_id
    m.text = text
    m.caption = None
    m.from_user = None
    return m


# ===== Level 1: exact _msg_map hit, scoped by chat ==========================

def test_level1_msg_map_collision_never_crosses_chats(
    mk_bot, mk_update, run_async, monkeypatch,
):
    bot = mk_bot()
    sess_a = _sess("claude-a", "a", CHAT_A)
    sess_b = _sess("claude-b", "b", CHAT_B)
    bot.registry._sessions[sess_a.name] = sess_a
    bot.registry._sessions[sess_b.name] = sess_b
    bot.registry.track_message(COLLIDING_MSG_ID, sess_a.name, CHAT_A)
    bot.registry.track_message(COLLIDING_MSG_ID, sess_b.name, CHAT_B)
    _wire_happy_dtach(monkeypatch, bot)

    update_a = mk_update("reply in chat A", chat_id=CHAT_A, message_id=1001)
    update_a.message.reply_to_message = _reply_target(message_id=COLLIDING_MSG_ID)
    run_async(bot._handle_message(update_a, _ctx()))
    assert sess_a.status == Status.BUSY
    assert sess_b.status == Status.IDLE  # B must NEVER have been touched

    sess_a.status = Status.IDLE
    update_b = mk_update("reply in chat B", chat_id=CHAT_B, message_id=1002)
    update_b.message.reply_to_message = _reply_target(message_id=COLLIDING_MSG_ID)
    run_async(bot._handle_message(update_b, _ctx()))
    assert sess_b.status == Status.BUSY
    assert sess_a.status == Status.IDLE  # A must NEVER have been re-touched


def test_level1_uninvolved_third_chat_resolves_to_neither(
    mk_bot, mk_update, run_async, monkeypatch,
):
    """Isolates the reply-resolution ladder's own cross-chat behaviour
    from the separate, pre-existing "no session resolved -> fall back to
    last_active_session" path a plain message would also use (design.md's
    own unit test calls this "level 4 in the caller" -- explicitly
    outside `_resolve_reply_target`'s levels 1-3, and out of scope for
    this feature). ``last_active_session`` is cleared immediately before
    the chat-C call so the assertion below is unambiguously about the
    reply ladder, not about that separate fallback's own scoping."""
    bot = mk_bot()
    sess_a = _sess("claude-a", "a", CHAT_A)
    sess_b = _sess("claude-b", "b", CHAT_B)
    bot.registry._sessions[sess_a.name] = sess_a
    bot.registry._sessions[sess_b.name] = sess_b
    bot.registry.track_message(COLLIDING_MSG_ID, sess_a.name, CHAT_A)
    bot.registry.track_message(COLLIDING_MSG_ID, sess_b.name, CHAT_B)
    bot.registry.last_active_session = ""
    _wire_happy_dtach(monkeypatch, bot)

    update_c = mk_update("reply in chat C", chat_id=CHAT_C, message_id=1003)
    update_c.message.reply_to_message = _reply_target(message_id=COLLIDING_MSG_ID)
    run_async(bot._handle_message(update_c, _ctx()))

    assert sess_a.status == Status.IDLE
    assert sess_b.status == Status.IDLE
    text = update_c.message.reply_text.await_args.args[0]
    assert "don't know which session" in text


# ===== Level 2: last_msg_id scan fallback, scoped by chat ==================

def test_level2_last_msg_id_scan_collision_never_crosses_chats(
    mk_bot, mk_update, run_async, monkeypatch,
):
    """Neither session's colliding id ever went through track_message --
    forces the ladder past level 1 and into the last_msg_id scan."""
    bot = mk_bot()
    sess_a = _sess("claude-a", "a", CHAT_A)
    sess_b = _sess("claude-b", "b", CHAT_B)
    sess_a.last_msg_id = COLLIDING_MSG_ID
    sess_b.last_msg_id = COLLIDING_MSG_ID
    bot.registry._sessions[sess_a.name] = sess_a
    bot.registry._sessions[sess_b.name] = sess_b
    _wire_happy_dtach(monkeypatch, bot)

    update_a = mk_update("reply in chat A", chat_id=CHAT_A, message_id=1001)
    update_a.message.reply_to_message = _reply_target(message_id=COLLIDING_MSG_ID)
    run_async(bot._handle_message(update_a, _ctx()))
    assert sess_a.status == Status.BUSY
    assert sess_b.status == Status.IDLE

    sess_a.status = Status.IDLE
    update_b = mk_update("reply in chat B", chat_id=CHAT_B, message_id=1002)
    update_b.message.reply_to_message = _reply_target(message_id=COLLIDING_MSG_ID)
    run_async(bot._handle_message(update_b, _ctx()))
    assert sess_b.status == Status.BUSY
    assert sess_a.status == Status.IDLE


# ===== Level 3: text-guess fallback, scoped by chat =========================

def test_level3_text_guess_collision_never_crosses_chats(
    mk_bot, mk_update, run_async, monkeypatch,
):
    """Two sessions share the SAME human label across two chats. Neither
    the colliding id nor any last_msg_id matches -- forces the ladder to
    the label-in-text guess. Without per-chat scoping this is genuinely
    ambiguous (two sessions named "jim"); scoped to one chat it must be
    unambiguous and resolve correctly."""
    bot = mk_bot()
    sess_a = _sess("claude-jim__d111", "jim", CHAT_A)
    sess_b = _sess("claude-jim__d222", "jim", CHAT_B)
    bot.registry._sessions[sess_a.name] = sess_a
    bot.registry._sessions[sess_b.name] = sess_b
    _wire_happy_dtach(monkeypatch, bot)

    guess_text = "⚙️ jim · Working…"

    update_a = mk_update("reply in chat A", chat_id=CHAT_A, message_id=1001)
    update_a.message.reply_to_message = _reply_target(message_id=88888, text=guess_text)
    run_async(bot._handle_message(update_a, _ctx()))
    assert sess_a.status == Status.BUSY
    assert sess_b.status == Status.IDLE

    sess_a.status = Status.IDLE
    update_b = mk_update("reply in chat B", chat_id=CHAT_B, message_id=1002)
    update_b.message.reply_to_message = _reply_target(message_id=88888, text=guess_text)
    run_async(bot._handle_message(update_b, _ctx()))
    assert sess_b.status == Status.BUSY
    assert sess_a.status == Status.IDLE
