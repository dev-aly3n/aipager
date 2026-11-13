"""The ⋮ session menu's 🧹 Clear conversation row and its confirm, in a
personal (DM) install: design.md success criteria 1, 2, 3 (DM wording),
4, 6 (personal-mode read_only), 9 (stale confirms) and 10 (Cancel).

Black-box: every test drives ``session_parity.handle_callback`` with the
verbs entrypoints.md lists (``menu``, ``clear``, ``clearok<turn_key>``,
``clear-cancel``) and observes only the tapped message, the toast and what
reached the PTY seam.
"""

from __future__ import annotations

import re
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.bot import session_parity
from aipager.state import Status
from aipager.team import Role, Team
from aipager.team import User as TeamUser

CHAT = 0
USER = 1
BUSY_TOAST = "⚠️ Can't clear while session is busy"
ENDED_TOAST = "That session has ended."


def _query(data="", *, user_id=USER, message_id=42):
    query = MagicMock()
    query.data = data
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.message = MagicMock()
    query.message.message_id = message_id
    query.message.text = ""
    query.message.reply_text = AsyncMock()
    query.from_user = MagicMock()
    query.from_user.id = user_id
    return query


def _cb_update(chat_id=CHAT, user_id=USER):
    update = MagicMock()
    update.effective_chat = MagicMock()
    update.effective_chat.id = chat_id
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    return update


def _tap(run_async, bot, verb, *, name="claude-dev", chat=CHAT, user=USER):
    query = _query(f"{name}:{verb}", user_id=user)
    handled = run_async(session_parity.handle_callback(
        bot, _cb_update(chat, user), query, name, verb))
    return query, handled


def _buttons(query):
    markup = query.edit_message_text.await_args.kwargs["reply_markup"]
    return [b for row in markup.inline_keyboard for b in row]


def _texts(query):
    return [b.text for b in _buttons(query)]


def _verb(button):
    return button.callback_data.rsplit(":", 1)[1]


def _edited_text(query):
    call = query.edit_message_text.await_args
    return call.args[0] if call.args else call.kwargs.get("text")


def _toasts(query):
    return [c.args[0] if c.args else c.kwargs.get("text")
            for c in query.answer.await_args_list]


def _plain(html):
    return re.sub(r"<[^>]+>", "", html)


def _draw_confirm(run_async, dm):
    """Tap 🧹 Clear conversation; return the drawn confirm's query."""
    query, _ = _tap(run_async, dm.bot, "clear")
    return query


def _confirm_verb(run_async, dm):
    query = _draw_confirm(run_async, dm)
    return next(_verb(b) for b in _buttons(query) if _verb(b).startswith("clearok"))


def _menu_has_clear_row(query):
    return any(b.callback_data.endswith(":clear") for b in _buttons(query))


# ---- criterion 1: the menu row ---------------------------------------------

def test_live_session_menu_lists_clear_after_restart_and_end(run_async, dm):
    query, _ = _tap(run_async, dm.bot, "menu")
    assert _texts(query) == ["🔄 Restart", "⏹ End session", "🧹 Clear conversation",
                             "✏️ Rename", "👤 Preferences", "📝 Diff", "✖️ Close"]


def test_live_session_menu_clear_row_carries_the_clear_verb(run_async, dm):
    query, _ = _tap(run_async, dm.bot, "menu")
    row = next(b for b in _buttons(query) if b.text == "🧹 Clear conversation")
    assert row.callback_data.endswith(":clear")


@pytest.mark.parametrize("status", [Status.BUSY, Status.INTERACTIVE],
                         ids=["busy", "interactive"])
def test_menu_of_a_working_but_live_session_still_offers_clear(run_async, dm, status):
    """Live means not GONE; the refusal comes at the tap, not by hiding."""
    dm.sess.status = status
    query, _ = _tap(run_async, dm.bot, "menu")
    assert _menu_has_clear_row(query)


def test_gone_session_menu_has_no_clear_row(run_async, dm):
    dm.sess.status = Status.GONE
    query, _ = _tap(run_async, dm.bot, "menu")
    assert not _menu_has_clear_row(query)


# ---- criterion 2: the confirm card ------------------------------------------

def test_clear_tap_is_handled(run_async, dm):
    _, handled = _tap(run_async, dm.bot, "clear")
    assert handled is True


def test_clear_tap_draws_the_confirm_text(run_async, dm):
    query = _draw_confirm(run_async, dm)
    assert _edited_text(query) == (
        "🧹 Clear the conversation in <b>dev</b>?\n"
        "Claude starts a new conversation and forgets this one.\n\n"
        "The old conversation stays saved on the computer, but it can't be "
        "resumed from here.")


def test_clear_confirm_does_not_promise_resume_brings_it_back(run_async, dm):
    text = _plain(_edited_text(_draw_confirm(run_async, dm))).lower()
    assert "can't be resumed" in text and "resume brings" not in text \
        and "resume can" not in text


def test_clear_confirm_buttons_are_clear_and_cancel(run_async, dm):
    query = _draw_confirm(run_async, dm)
    assert _texts(query) == ["🧹 Clear", "Cancel"]


def test_clear_confirm_ok_button_carries_the_current_turn_key(run_async, dm):
    query = _draw_confirm(run_async, dm)
    assert _verb(_buttons(query)[0]) == f"clearok{dm.sess.turn_key}"


def test_clear_confirm_cancel_button_carries_clear_cancel(run_async, dm):
    query = _draw_confirm(run_async, dm)
    assert _verb(_buttons(query)[1]) == "clear-cancel"


def test_drawing_the_confirm_types_nothing(run_async, dm):
    _draw_confirm(run_async, dm)
    dm.typed.assert_not_awaited()


# ---- criterion 3: confirm on an idle session ---------------------------------

def test_confirm_types_exactly_clear_once_into_that_session(run_async, dm):
    verb = _confirm_verb(run_async, dm)
    _tap(run_async, dm.bot, verb)
    assert [c.args[:2] for c in dm.typed.await_args_list] == [("claude-dev", "/clear")]


def test_confirm_turns_the_card_into_the_sent_line(run_async, dm):
    verb = _confirm_verb(run_async, dm)
    query, _ = _tap(run_async, dm.bot, verb)
    assert _plain(_edited_text(query)) == "🧹 /clear sent to dev"


def test_confirm_sent_card_has_no_keyboard(run_async, dm):
    verb = _confirm_verb(run_async, dm)
    query, _ = _tap(run_async, dm.bot, verb)
    assert query.edit_message_text.await_args.kwargs.get("reply_markup") is None


def test_confirm_success_answers_without_a_refusal_toast(run_async, dm):
    verb = _confirm_verb(run_async, dm)
    query, _ = _tap(run_async, dm.bot, verb)
    assert [t for t in _toasts(query) if t] == []


def test_confirm_does_not_type_into_another_session(run_async, dm):
    from aipager.state import TrackedSession
    other = TrackedSession(name="claude-frontend", label="frontend", status=Status.IDLE)
    dm.bot.registry._sessions[other.name] = other
    verb = _confirm_verb(run_async, dm)
    _tap(run_async, dm.bot, verb)
    assert {c.args[0] for c in dm.typed.await_args_list} == {"claude-dev"}


def test_double_tapped_confirm_types_clear_only_once(run_async, dm):
    """Error guessing: a person double-taps Clear. The first /clear moves the
    session through GONE (SessionEnd reason=clear), as production does."""
    verb = _confirm_verb(run_async, dm)
    _tap(run_async, dm.bot, verb)
    dm.bot.registry.transition(dm.sess.name, Status.GONE)
    dm.bot.registry.transition(dm.sess.name, Status.IDLE)
    _tap(run_async, dm.bot, verb)
    assert dm.typed.await_count == 1


# ---- criterion 4: busy -------------------------------------------------------

def test_clear_tap_on_busy_session_answers_busy(run_async, dm):
    dm.sess.status = Status.BUSY
    query, _ = _tap(run_async, dm.bot, "clear")
    assert _toasts(query) == [BUSY_TOAST]


def test_clear_tap_on_busy_session_draws_no_confirm(run_async, dm):
    dm.sess.status = Status.BUSY
    query, _ = _tap(run_async, dm.bot, "clear")
    query.edit_message_text.assert_not_awaited()


def test_confirm_on_busy_session_answers_busy(run_async, dm):
    verb = _confirm_verb(run_async, dm)
    dm.sess.status = Status.BUSY
    query, _ = _tap(run_async, dm.bot, verb)
    assert _toasts(query) == [BUSY_TOAST]


def test_confirm_on_busy_session_types_nothing(run_async, dm):
    verb = _confirm_verb(run_async, dm)
    dm.sess.status = Status.BUSY
    _tap(run_async, dm.bot, verb)
    dm.typed.assert_not_awaited()


def test_confirm_on_busy_session_redraws_the_menu(run_async, dm):
    verb = _confirm_verb(run_async, dm)
    dm.sess.status = Status.BUSY
    query, _ = _tap(run_async, dm.bot, verb)
    assert _menu_has_clear_row(query)


# ---- criterion 6: personal-mode read_only ------------------------------------

@pytest.fixture
def ro(dm):
    dm.bot.team = Team(group_id=0, users={999: TeamUser(id=999, label="ro",
                                                         role=Role.READ_ONLY)})
    return dm


def test_read_only_clear_tap_is_refused(run_async, ro):
    query, _ = _tap(run_async, ro.bot, "clear", user=999)
    assert _toasts(query) == ["You can't clear this session's conversation."]


def test_read_only_clear_tap_draws_nothing(run_async, ro):
    query, _ = _tap(run_async, ro.bot, "clear", user=999)
    query.edit_message_text.assert_not_awaited()


def test_read_only_confirm_is_refused(run_async, ro):
    query, _ = _tap(run_async, ro.bot, f"clearok{ro.sess.turn_key}", user=999)
    assert _toasts(query) == ["You can't clear this session's conversation."]


def test_read_only_confirm_types_nothing(run_async, ro):
    _tap(run_async, ro.bot, f"clearok{ro.sess.turn_key}", user=999)
    ro.typed.assert_not_awaited()


def test_read_only_confirm_leaves_the_card(run_async, ro):
    query, _ = _tap(run_async, ro.bot, f"clearok{ro.sess.turn_key}", user=999)
    query.edit_message_text.assert_not_awaited()


# ---- criterion 9: stale confirms ---------------------------------------------

def test_confirm_after_session_ended_answers_ended(run_async, dm):
    verb = _confirm_verb(run_async, dm)
    dm.bot.registry.transition(dm.sess.name, Status.GONE)
    query, _ = _tap(run_async, dm.bot, verb)
    assert _toasts(query) == [ENDED_TOAST]


def test_confirm_after_session_ended_types_nothing(run_async, dm):
    verb = _confirm_verb(run_async, dm)
    dm.bot.registry.transition(dm.sess.name, Status.GONE)
    _tap(run_async, dm.bot, verb)
    dm.typed.assert_not_awaited()


def test_confirm_after_session_ended_redraws_the_menu(run_async, dm):
    verb = _confirm_verb(run_async, dm)
    dm.bot.registry.transition(dm.sess.name, Status.GONE)
    query, _ = _tap(run_async, dm.bot, verb)
    assert query.edit_message_text.await_args.kwargs.get("reply_markup") is not None


def test_clear_tap_on_ended_session_answers_ended(run_async, dm):
    dm.sess.status = Status.GONE
    query, _ = _tap(run_async, dm.bot, "clear")
    assert _toasts(query) == [ENDED_TOAST]


def test_clear_tap_on_ended_session_draws_no_confirm(run_async, dm):
    dm.sess.status = Status.GONE
    query, _ = _tap(run_async, dm.bot, "clear")
    assert not any(b.text == "🧹 Clear" for b in _buttons(query))


def test_confirm_during_resume_answers_resuming(run_async, dm):
    verb = _confirm_verb(run_async, dm)
    dm.sess.resuming_until = time.monotonic() + 60
    query, _ = _tap(run_async, dm.bot, verb)
    assert _toasts(query) == ["dev is being resumed - try again in a moment"]


def test_confirm_during_resume_types_nothing(run_async, dm):
    verb = _confirm_verb(run_async, dm)
    dm.sess.resuming_until = time.monotonic() + 60
    _tap(run_async, dm.bot, verb)
    dm.typed.assert_not_awaited()


MOVED_ON = ("dev changed since this was shown - tap 🧹 Clear conversation again "
            "to clear it.")


def _already_cleared(dm):
    dm.bot.registry.transition(dm.sess.name, Status.GONE)
    dm.bot.registry.transition(dm.sess.name, Status.IDLE)


def _new_turn(dm):
    dm.bot.registry.transition(dm.sess.name, Status.BUSY)
    dm.bot.registry.transition(dm.sess.name, Status.IDLE)


@pytest.mark.parametrize("advance", [_already_cleared, _new_turn],
                         ids=["already-cleared", "new-turn"])
def test_stale_turn_confirm_answers_changed(run_async, dm, advance):
    verb = _confirm_verb(run_async, dm)
    advance(dm)
    query, _ = _tap(run_async, dm.bot, verb)
    assert _toasts(query) == [MOVED_ON]


@pytest.mark.parametrize("advance", [_already_cleared, _new_turn],
                         ids=["already-cleared", "new-turn"])
def test_stale_turn_confirm_types_nothing(run_async, dm, advance):
    verb = _confirm_verb(run_async, dm)
    advance(dm)
    _tap(run_async, dm.bot, verb)
    dm.typed.assert_not_awaited()


@pytest.mark.parametrize("advance", [_already_cleared, _new_turn],
                         ids=["already-cleared", "new-turn"])
def test_stale_turn_confirm_redraws_the_menu(run_async, dm, advance):
    verb = _confirm_verb(run_async, dm)
    advance(dm)
    query, _ = _tap(run_async, dm.bot, verb)
    assert _menu_has_clear_row(query)


def test_confirm_with_a_turn_key_from_the_future_types_nothing(run_async, dm):
    """Boundary: a key that is not the current one, in either direction."""
    _tap(run_async, dm.bot, f"clearok{dm.sess.turn_key + 1}")
    dm.typed.assert_not_awaited()


def _open_dialog(dm):
    dm.sess.status = Status.INTERACTIVE
    dm.sess.pending_permission = {"tool_summary": "Bash: ls"}


def _background_agent(dm):
    dm.sess.add_subagent("a1", {"type": "explore", "started_at": time.time()})


def _parked(dm):
    dm.sess.parked_job = "x"


STAYS = [
    (_open_dialog, "Answer the open prompt first, then clear the conversation."),
    (_background_agent,
     "Claude is still finishing its work - clear the conversation when it's done."),
    (_parked, "Bring dev back from Claude Code's background first."),
]
STAYS_IDS = ["dialog-open", "background-agents", "parked"]


@pytest.mark.parametrize("arrange, toast", STAYS, ids=STAYS_IDS)
def test_confirm_with_work_pending_answers_why(run_async, dm, arrange, toast):
    verb = _confirm_verb(run_async, dm)
    arrange(dm)
    query, _ = _tap(run_async, dm.bot, verb)
    assert _toasts(query) == [toast]


@pytest.mark.parametrize("arrange, toast", STAYS, ids=STAYS_IDS)
def test_confirm_with_work_pending_types_nothing(run_async, dm, arrange, toast):
    verb = _confirm_verb(run_async, dm)
    arrange(dm)
    _tap(run_async, dm.bot, verb)
    dm.typed.assert_not_awaited()


@pytest.mark.parametrize("arrange, toast", STAYS, ids=STAYS_IDS)
def test_confirm_with_work_pending_keeps_the_card(run_async, dm, arrange, toast):
    verb = _confirm_verb(run_async, dm)
    arrange(dm)
    query, _ = _tap(run_async, dm.bot, verb)
    query.edit_message_text.assert_not_awaited()


def test_confirm_with_socket_gone_answers_not_found(run_async, dm):
    verb = _confirm_verb(run_async, dm)
    dm.alive.return_value = False
    query, _ = _tap(run_async, dm.bot, verb)
    assert _toasts(query) == ["dev was not found"]


def test_confirm_with_socket_gone_types_nothing(run_async, dm):
    verb = _confirm_verb(run_async, dm)
    dm.alive.return_value = False
    _tap(run_async, dm.bot, verb)
    dm.typed.assert_not_awaited()


def test_confirm_with_socket_gone_redraws_the_menu(run_async, dm):
    verb = _confirm_verb(run_async, dm)
    dm.alive.return_value = False
    query, _ = _tap(run_async, dm.bot, verb)
    assert query.edit_message_text.await_args.kwargs.get("reply_markup") is not None


def test_confirm_when_typing_fails_answers_try_again(run_async, dm):
    verb = _confirm_verb(run_async, dm)
    dm.bot._inject_prompt = AsyncMock(return_value=False)
    query, _ = _tap(run_async, dm.bot, verb)
    assert _toasts(query) == ["Couldn't clear dev - try again."]


def test_confirm_when_typing_fails_keeps_the_card(run_async, dm):
    verb = _confirm_verb(run_async, dm)
    dm.bot._inject_prompt = AsyncMock(return_value=False)
    query, _ = _tap(run_async, dm.bot, verb)
    query.edit_message_text.assert_not_awaited()


def test_clearok_without_digits_types_nothing(run_async, dm):
    _tap(run_async, dm.bot, "clearokX")
    dm.typed.assert_not_awaited()


def test_clearok_without_digits_is_not_handled(run_async, dm):
    _, handled = _tap(run_async, dm.bot, "clearokX")
    assert handled is False


# ---- criterion 10: Cancel ----------------------------------------------------

def test_cancel_says_clear_cancelled(run_async, dm):
    _draw_confirm(run_async, dm)
    query, _ = _tap(run_async, dm.bot, "clear-cancel")
    assert _edited_text(query) == "Clear cancelled for [<b>dev</b>]."


def test_cancel_removes_the_keyboard(run_async, dm):
    _draw_confirm(run_async, dm)
    query, _ = _tap(run_async, dm.bot, "clear-cancel")
    assert query.edit_message_text.await_args.kwargs.get("reply_markup") is None


def test_cancel_types_nothing(run_async, dm):
    _draw_confirm(run_async, dm)
    _tap(run_async, dm.bot, "clear-cancel")
    dm.typed.assert_not_awaited()
