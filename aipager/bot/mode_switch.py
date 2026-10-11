"""Confirming an in-place Ask <-> Auto switch from the next hook.

``SessionOpsMixin._mode_switch_core`` presses Shift+Tab in the session's
terminal and records a :class:`aipager.state.ModeSwitch`. No hook fires
on the switch itself, but every hook event carries the mode Claude Code
is in (``permission_mode``): the hook receiver records the main agent's
and, while a switch waits, dispatches ``mode_reported`` here.

- The reported mode is the target: confirmed.
- It is not, the first time: one more round of presses, counted from the
  mode Claude Code reported (never on a dialog event or while a dialog is
  open: the presses would answer it; the switch waits for the next hook).
- It is still not: the person is told the real mode. Asked for Auto and
  told Manual means Auto is not available for this session ("model or
  plan"); the session is then shown as Ask with that said.

Where the person saw "switch sent" (``attach_reply``) is edited with the
result; with no such message, or when the edit fails, a failure is sent
as a new message to the session's chat (a confirmation is not news then).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from aipager import i18n, perm_modes
from aipager.bot.mode_labels import shown_label
from aipager.bot.transport import (
    MUTED,
    edit_text_at,
    resolve_chat_id_int,
    send_text,
)
from aipager.dtach import inject
from aipager.i18n import Command, Msg, Safe

if TYPE_CHECKING:
    from aipager.state import ModeSwitch, TrackedSession

log = logging.getLogger(__name__)

#: Plain sentences (no markup): each is shown on the /mode card, in a chat
#: line, and on the Ready card's warning line alike.
SWITCH_SENT = Msg("modecard.switch_sent", "⚙️ Switching {session} to {mode} without a restart.",
                  note="An Ask/Auto switch was sent to the session's terminal; Claude Code "
                       "has not confirmed it yet. {mode} is the mode's label, which already "
                       "says mode",
                  eg={"session": "x1", "mode": "Auto mode"})
SWITCH_PENDING_LINE = Msg("modecard.switch_pending_line",
                          "⏳ Waiting for Claude Code to confirm the switch.",
                          note="On the mode card while a switch is not confirmed yet")
SWITCH_CONFIRMED = Msg("modecard.switch_confirmed", "✅ {session} is now in {mode}.",
                       note="Claude Code confirmed the mode switch; {mode} is the mode's "
                            "label, which already says mode",
                       eg={"session": "x1", "mode": "Auto mode"})
AUTO_UNAVAILABLE = Msg("modecard.auto_unavailable",
                       "Auto mode isn't available for {session} (model or plan), so it "
                       "stays in Ask mode.",
                       note="Claude Code would not switch to Auto mode for this session; "
                            "Auto mode and Ask mode are the mode names",
                       eg={"session": "x1"})
SWITCH_UNCONFIRMED = Msg("modecard.switch_unconfirmed",
                         "{session} didn't switch: it is in {mode} now.",
                         note="The mode switch did not land; {mode} is the mode the session "
                              "is really in (its label, which already says mode)",
                         eg={"session": "x1", "mode": "Ask mode"})

# Shared by the /mode card (handlers.py) and the Ready card (new_flow.py).
MODE_BTN_TO_ASK = Msg("modecard.btn.to_ask", "💬 Switch to Ask mode",
                      note="Button: switch to Ask mode (Claude asks before each action)")
MODE_BTN_TO_AUTO = Msg("modecard.btn.to_auto", "🤖 Switch to Auto mode",
                       note="Button: switch to Auto mode (a safety check runs safe actions and "
                            "stops risky ones)")
MODE_BTN_TO_FULL = Msg("modecard.btn.to_full", "🔓 Switch to Full access mode",
                       note="Button: switch to Full access mode (everything runs with no "
                            "checks; the session restarts)")
TOAST_NEEDS_ADMIN_FULL = Msg("toast.perms_needs_admin", "Full access mode needs an admin.",
                             note="Pop-up: switching to Full access mode is for admins")
TOAST_MODE_DIALOG_OPEN = Msg("toast.mode_dialog_open",
                             "{session} is waiting on a prompt. Answer it first, then switch "
                             "the mode.",
                             note="Pop-up: an Ask/Auto switch refused while the session shows "
                                  "a question or permission prompt", eg={"session": "x1"})

_TASKS: set = set()


def _spawn(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return task


def attach_reply(sess: TrackedSession, chat_id, message_id, kind: str, by: str = "") -> None:
    """Remember where the person saw "switch sent" (*kind* ``mode_card``,
    ``ready`` or ``mirror``), so the result edits it. Nothing when no
    switch waits (it was already confirmed) or nothing showed."""
    pending = sess.mode_switch
    if pending is None or chat_id is None or message_id is None:
        return
    pending.reply = (int(chat_id), int(message_id), kind)
    pending.by = by or ""


async def on_reported(bot, sess: TrackedSession, context: dict) -> None:
    """A main-agent hook reported the session's mode while a switch waits.
    Decides at once; presses and Telegram edits run in a task, so hook
    handling is never held up."""
    pending = sess.mode_switch
    if pending is None or not sess.reported_mode:
        return
    reported = sess.reported_mode
    mapped = perm_modes.from_claude(reported)
    if mapped == pending.target:
        sess.mode_switch = None
        _spawn(settle(bot, sess, pending, "confirmed"))
        return
    if not pending.retried:
        if context.get("dialog") or sess.dialog_is_open():
            # A Shift+Tab now would land in the dialog: the next hook
            # decides.
            return
        count = perm_modes.presses(reported, pending.target,
                                   auto_in_cycle=not sess.auto_unavailable)
        if count is None:
            sess.mode_switch = None
            _spawn(settle(bot, sess, pending, "unconfirmed"))
            return
        pending.retried = True
        pending.presses = count
        pending.sent_at = time.monotonic()
        sess.reported_mode = ""
        log.info("[%s] mode switch to %s: Claude Code reports %s, pressing %d more",
                 sess.label, pending.target, reported, count)
        _spawn(_retry(bot, sess, pending, count, reported))
        return
    sess.mode_switch = None
    if pending.target == perm_modes.AUTO and mapped == perm_modes.ASK:
        sess.auto_unavailable = True
        log.info("[%s] Auto mode not available: Claude Code stays in %s",
                 sess.label, reported)
        _spawn(settle(bot, sess, pending, "auto_unavailable"))
        return
    log.info("[%s] mode switch to %s not confirmed: Claude Code reports %s",
             sess.label, pending.target, reported)
    _spawn(settle(bot, sess, pending, "unconfirmed"))


async def _retry(bot, sess: TrackedSession, pending: ModeSwitch, count: int,
                 reported: str) -> None:
    if await inject.press_mode_cycle(sess.name, count):
        return
    if sess.mode_switch is pending:
        sess.mode_switch = None
        if not sess.reported_mode:
            sess.reported_mode = reported
        await settle(bot, sess, pending, "unconfirmed")


def _notes(tr, sess: TrackedSession, pending: ModeSwitch, outcome: str) -> tuple[str, str]:
    """``(html, plain)``: the result as an HTML line and as plain text."""
    if outcome == "confirmed":
        mode_html = Safe(shown_label(tr, sess))
        mode_text = Safe(shown_label(tr, sess, html=False))
        if pending.by:
            from aipager.bot.callbacks import MODE_SWITCHED_BY  # local: import cycle
            return (tr.html(MODE_SWITCHED_BY, mode=mode_html, person=Command(pending.by)),
                    tr.text(MODE_SWITCHED_BY, mode=mode_text, person=Command(pending.by)))
        return (tr.html(SWITCH_CONFIRMED, session=sess.label, mode=mode_html),
                tr.text(SWITCH_CONFIRMED, session=sess.label, mode=mode_text))
    from aipager.bot.new_flow import WARNING_LINE  # local: import cycle
    if outcome == "auto_unavailable":
        plain = tr.text(AUTO_UNAVAILABLE, session=sess.label)
        html = tr.html(AUTO_UNAVAILABLE, session=sess.label)
    else:
        plain = tr.text(SWITCH_UNCONFIRMED, session=sess.label,
                        mode=Safe(shown_label(tr, sess, html=False)))
        html = tr.html(SWITCH_UNCONFIRMED, session=sess.label,
                       mode=Safe(shown_label(tr, sess)))
    # One line, one mark at its start (the warning line's).
    return tr.html(WARNING_LINE, text=Safe(html.removeprefix(tr.mark))), plain


async def settle(bot, sess: TrackedSession, pending: ModeSwitch, outcome: str) -> None:
    """Show *outcome* (``confirmed``, ``auto_unavailable``,
    ``unconfirmed``) where the switch was shown as sent."""
    app = getattr(bot, "_app", None)
    if app is None:
        return
    reply = pending.reply
    chat_id = reply[0] if reply is not None else resolve_chat_id_int(sess)
    tr = i18n.for_chat(chat_id)
    try:
        note_html, note_plain = _notes(tr, sess, pending, outcome)
    except Exception:
        log.warning("[%s] mode switch result not rendered", sess.label, exc_info=True)
        return
    if reply is not None:
        r_chat, r_msg, kind = reply
        try:
            if kind == "mode_card":
                text, kb = bot._mode_card(r_chat, sess, note_html)
            elif kind == "ready":
                from aipager.bot import new_flow  # local: import cycle
                if outcome == "confirmed":
                    text, kb = new_flow.render_ready(
                        bot, None, sess, changed=note_plain if pending.by else "")
                else:
                    text, kb = new_flow.render_ready(bot, None, sess, note=note_plain)
            else:
                text, kb = note_html, None
            await edit_text_at(app.bot, chat_id=r_chat, message_id=r_msg, text=text,
                               parse_mode="HTML", reply_markup=kb)
            return
        except Exception:
            log.debug("[%s] mode switch result edit failed", sess.label, exc_info=True)
    if outcome == "confirmed" or not chat_id:
        return
    try:
        sent = await send_text(app.bot, chat_id=chat_id, text=note_html, parse_mode="HTML")
        if sent is MUTED:
            log.info("[%s] mode switch result not sent: chat muted", sess.label)
    except Exception:
        log.debug("[%s] mode switch result not sent", sess.label, exc_info=True)
