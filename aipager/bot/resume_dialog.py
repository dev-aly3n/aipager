"""The chat side of dialogs Claude Code asks in its terminal with no hook
(roadmap 8.127; the rules are :mod:`aipager.term_dialog`'s).

One card per opening, in the session's own chat:

- after a ``--resume`` start (``term_dialog.RESUME_UNSURE``): which of the
  resume questions it is cannot be told (the summary question, the
  cold-resume question and the newer-model offer read the same in Claude
  Code's record), so the card offers only "Continue this conversation":
  Escape, each one's "dismiss" (2.1.311, live: the conversation goes on
  unchanged, same id, full history). Sent as soon as the opening has
  settled: aipager's own relaunch caused it;
- the cold-resume question Claude Code opens by itself on a long-idle
  session (``term_dialog.COLD_IDLE``): no card when it opens (it opens on
  every idle subscriber session about an hour after the last answer, and
  nothing is lost until a message is sent), but when a message meets it:
  "Resume" (Escape) and "Start a new conversation" (Escape, a wipe of the
  input box, then ``/clear``; never a row pick, whose keys depend on a
  flag aipager cannot see);
- any other dialog (``term_dialog.OTHER``): a note, no buttons, when a
  message meets it or when aipager's own typing opened it.

Never "Don't ask me again": it writes Claude Code's global config. Every
key goes through :func:`aipager.dtach.inject.press_key_in_dialog`, under
the session's write lock and only while the opening is still the one on
record. The card is shown closed (its buttons gone) when the dialog
closes, the session ends or is restarted; a note is deleted.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from aipager import i18n, term_dialog
from aipager.bot.transport import (
    calling_chat_id,
    edit_text_at,
    resolve_chat_id,
    resolve_chat_id_int,
    send_text,
)
from aipager.dtach import inject
from aipager.i18n import Command, Msg
from aipager.state import Status

if TYPE_CHECKING:
    from aipager.bot.core import TelegramBot

log = logging.getLogger(__name__)

# -- text -------------------------------------------------------------------

UNSURE_QUESTION = Msg(
    "rdlg.unsure_question",
    "❓ <b>{session}</b>: Claude Code is asking a question in the terminal "
    "before it goes on with this conversation.",
    note="HTML. Card in the session's chat right after the session was "
         "restarted or resumed: Claude Code shows a question in its own "
         "screen on the computer (about resuming an old, long conversation) "
         "and waits for an answer. aipager cannot tell which question it is",
    eg={"session": "x1"})
UNSURE_HINT = Msg(
    "rdlg.unsure_hint",
    "To start over or shorten it, tap Continue and then send {clear} or {compact}.",
    note="HTML. Second line of rdlg.unsure_question's card. Continue is the "
         "card's button rdlg.btn_continue; {clear} and {compact} are the "
         "commands /clear (start a new conversation) and /compact (shorten "
         "it to a summary)",
    eg={"clear": "/clear", "compact": "/compact"})
COLD_QUESTION = Msg(
    "rdlg.cold_question",
    "❓ <b>{session}</b>: Claude Code asks whether to resume this conversation. "
    "It was idle for a long time, and resuming it uses part of your 5-hour "
    "usage limit.",
    note="HTML. Card in the session's chat when a message meets Claude Code's "
         "question \"Resume this conversation?\" (shown in its own screen on "
         "the computer after the conversation sat unused). The 5-hour usage "
         "limit is the Claude plan's limit",
    eg={"session": "x1"})
BTN_CONTINUE = Msg("rdlg.btn_continue", "Continue this conversation",
                   note="Button on rdlg.unsure_question's card: answers Claude "
                        "Code's question so the conversation goes on as it is")
BTN_RESUME = Msg("rdlg.btn_resume", "Resume",
                 note="Button on rdlg.cold_question's card: resume the "
                      "conversation as it is (Claude Code's own option name)")
BTN_NEW = Msg("rdlg.btn_new", "Start a new conversation",
              note="Button on rdlg.cold_question's card: leave this "
                   "conversation and start a new, empty one (Claude Code's own "
                   "option name)")
OTHER_NOTE = Msg(
    "rdlg.other_note",
    "❓ <b>{session}</b> is waiting on a question in its terminal. Answer it "
    "there, and messages sent meanwhile go to Claude right after.",
    note="HTML. Note in the session's chat when Claude Code shows some other "
         "question or panel in its own screen on the computer that aipager "
         "cannot answer; messages are held until it is closed there. Deleted "
         "when it closes",
    eg={"session": "x1"})
CLOSED_CONTINUED = Msg(
    "rdlg.closed_continued", "✅ <b>{session}</b>: the conversation goes on.",
    note="HTML. The resume question's card once Continue or Resume answered it",
    eg={"session": "x1"})
CLOSED_NEW = Msg(
    "rdlg.closed_new", "🧹 <b>{session}</b>: a new conversation was started.",
    note="HTML. The resume question's card once Start a new conversation "
         "answered it",
    eg={"session": "x1"})
CLOSED_TERMINAL = Msg(
    "rdlg.closed_terminal",
    "<b>{session}</b>: the question was answered in the terminal.",
    note="HTML. The resume question's card once someone answered it on the "
         "computer (Claude Code's own screen) instead",
    eg={"session": "x1"})
CLOSED_ENDED = Msg(
    "rdlg.closed_ended",
    "<b>{session}</b>: the session ended before the question was answered.",
    note="HTML. The resume question's card when the session closed first",
    eg={"session": "x1"})
CLOSED_RESTARTED = Msg(
    "rdlg.closed_restarted",
    "<b>{session}</b>: the session was restarted before the question was answered.",
    note="HTML. The resume question's card when the session was restarted first",
    eg={"session": "x1"})
NEW_FAILED = Msg(
    "rdlg.new_failed",
    "<b>{session}</b>: the conversation goes on, but a new one could not be "
    "started. Send {clear} to start one.",
    note="HTML. The resume question's card when Start a new conversation "
         "answered the question but the command /clear could not be typed",
    eg={"session": "x1", "clear": "/clear"})
TOAST_CLOSED = Msg("toast.rdlg_closed", "This question is no longer open.",
                   note="Pop-up: a button on a resume question's card tapped after "
                        "the question was answered or closed")
TOAST_ANSWERING = Msg("toast.rdlg_answering", "This question is being answered already.",
                      note="Pop-up: a second tap while the first one's answer runs")
TOAST_FAILED = Msg("toast.rdlg_failed",
                   "Couldn't answer the question. Try again, or answer it in the terminal.",
                   note="Pop-up: the key could not be sent, or the question did not "
                        "close after it; the terminal is Claude Code's own screen")
TOAST_NOT_IDLE = Msg("toast.rdlg_not_idle",
                     "Claude is working right now. Try again when it is done.",
                     note="Pop-up: a button on a resume question's card tapped "
                          "while Claude works")

_OUTCOMES = {
    "continued": CLOSED_CONTINUED,
    "new": CLOSED_NEW,
    "terminal": CLOSED_TERMINAL,
    "ended": CLOSED_ENDED,
    "restarted": CLOSED_RESTARTED,
    "new_failed": NEW_FAILED,
}

# -- buttons ------------------------------------------------------------------

#: callback verb -> the answer it gives.
_VERBS = {"rdcont": "continue", "rdresume": "resume", "rdnew": "new"}
#: Which kinds' cards carry which verbs.
_KIND_VERBS = {
    term_dialog.RESUME_UNSURE: ("rdcont",),
    term_dialog.COLD_IDLE: ("rdresume", "rdnew"),
}


def parse_verb(action: str) -> tuple[str, int] | None:
    """``(verb, epoch)`` for a card button's action (``rdcont<N>``,
    ``rdresume<N>``, ``rdnew<N>``), or None when *action* is not one."""
    for verb in _VERBS:
        if action.startswith(verb):
            rest = action[len(verb):]
            if rest.isascii() and rest.isdigit() and len(rest) <= 12:
                return verb, int(rest)
            return None
    return None


def keyboard(bot: "TelegramBot", chat_id: int, sess, kind: str,
             tr=None) -> InlineKeyboardMarkup | None:
    """The card's buttons for the opening on record, keyed by its epoch;
    None for a kind that gets none (``other``)."""
    from aipager.bot.session_parity import session_cb  # local: import cycle
    cur = sess.term_dialog
    if cur is None:
        return None
    tr = tr or i18n.for_chat(chat_id)
    epoch = cur["epoch"]
    if kind == term_dialog.RESUME_UNSURE:
        return InlineKeyboardMarkup([[InlineKeyboardButton(
            tr.text(BTN_CONTINUE),
            callback_data=session_cb(bot, chat_id, sess, f"rdcont{epoch}"))]])
    if kind == term_dialog.COLD_IDLE:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton(
                tr.text(BTN_RESUME),
                callback_data=session_cb(bot, chat_id, sess, f"rdresume{epoch}"))],
            [InlineKeyboardButton(
                tr.text(BTN_NEW),
                callback_data=session_cb(bot, chat_id, sess, f"rdnew{epoch}"))],
        ])
    return None


def _card_text(tr, sess, kind: str) -> str:
    if kind == term_dialog.RESUME_UNSURE:
        return (tr.html(UNSURE_QUESTION, session=sess.label) + "\n\n"
                + tr.html(UNSURE_HINT, clear=Command("/clear"),
                          compact=Command("/compact")))
    if kind == term_dialog.COLD_IDLE:
        return tr.html(COLD_QUESTION, session=sess.label)
    return tr.html(OTHER_NOTE, session=sess.label)


# -- cards ----------------------------------------------------------------------

async def ensure_card(bot: "TelegramBot", sess, *, reply_to: int | None = None) -> None:
    """The open opening's card (or note) in the session's chat, once per
    opening (claimed before the send), as a reply to *reply_to* when
    given. Best-effort."""
    cur = sess.term_dialog
    if cur is None or cur.get("told") or bot._app is None:
        return
    cur["told"] = True  # claimed before the send's await
    kind = cur["kind"]
    chat = resolve_chat_id(sess)
    chat_int = resolve_chat_id_int(sess) or 0
    tr = i18n.for_sess(sess)
    kwargs = {"chat_id": chat, "text": _card_text(tr, sess, kind), "parse_mode": "HTML"}
    markup = keyboard(bot, chat_int, sess, kind, tr)
    if markup is not None:
        kwargs["reply_markup"] = markup
    sent = None
    for reply in ([reply_to, None] if reply_to else [None]):
        try:
            if reply:
                sent = await send_text(bot._app.bot, reply_to_message_id=reply, **kwargs)
            else:
                sent = await send_text(bot._app.bot, **kwargs)
            break
        except Exception:
            log.debug("[%s] terminal dialog card not sent (reply to %s)",
                      sess.label, reply, exc_info=True)
    message_id = getattr(sent, "message_id", None)
    if not isinstance(message_id, int):
        return
    card = (chat_int or chat, message_id)
    card_kind = "note" if kind == term_dialog.OTHER else "card"
    log.info("[%s] terminal dialog %s sent (kind=%s)", sess.label, card_kind, kind)
    if sess.term_dialog is cur:
        cur["card"] = card
        cur["card_kind"] = card_kind
        return
    # It closed while the card went out: queued for closing (the bot closes
    # the queue after every event; the monitor sends one while it is not
    # empty).
    sess.term_dialog_closing.append({
        "epoch": cur["epoch"], "card": card, "card_kind": card_kind,
        "outcome": "ended" if sess.status is Status.GONE else "terminal"})


async def on_opened(bot: "TelegramBot", sess) -> None:
    """The monitor saw an opening, or its card fall due
    (:func:`aipager.term_dialog.card_due`): the card goes out when it is
    due."""
    if term_dialog.card_due(sess, term_dialog._now_wall()):
        await ensure_card(bot, sess)


async def on_message_held(bot: "TelegramBot", update, sess) -> bool:
    """A message was held for the open terminal dialog: its card (or note)
    goes out now if it has not, as a reply to that message when it came
    from the session's chat. True when there is a card to put the
    per-person "answer it first" notice under; False for a note (it says
    all there is) or when none could be sent."""
    cur = sess.term_dialog
    if cur is None:
        return False
    reply_to = None
    message = getattr(update, "message", None)
    if calling_chat_id(update) == resolve_chat_id_int(sess) and message is not None:
        reply_to = getattr(message, "message_id", None)
    await ensure_card(bot, sess, reply_to=reply_to if isinstance(reply_to, int) else None)
    return cur.get("kind") != term_dialog.OTHER and bool(cur.get("card"))


async def close_cards(bot: "TelegramBot", sess) -> None:
    """Show the cards of closed openings closed (edited to their outcome,
    no buttons) and delete their notes. Best-effort."""
    records, sess.term_dialog_closing = list(sess.term_dialog_closing), []
    if not records or bot._app is None:
        return
    tr = i18n.for_sess(sess)
    for rec in records:
        chat_id, message_id = rec["card"]
        try:
            if rec.get("card_kind") == "note":
                await bot._app.bot.delete_message(chat_id=chat_id, message_id=message_id)
                continue
            msg = _OUTCOMES.get(rec.get("outcome") or "", CLOSED_TERMINAL)
            params = {"session": sess.label}
            if msg is NEW_FAILED:
                params["clear"] = Command("/clear")
            await edit_text_at(bot._app.bot, chat_id=chat_id, message_id=message_id,
                               text=tr.html(msg, **params), parse_mode="HTML",
                               reply_markup=None)
        except Exception:
            log.debug("[%s] could not close a terminal dialog card", sess.label,
                      exc_info=True)


# -- taps -------------------------------------------------------------------------

async def _still_open(sess, cur: dict) -> bool:
    """Whether *cur* is still the opening on record and Claude Code's
    record, read now, still shows it, with the session idle: asked under
    the write lock right before a key."""
    if sess.term_dialog is not cur or sess.status is not Status.IDLE:
        return False
    entry = await term_dialog.fresh_record(sess)
    return (sess.term_dialog is cur and sess.status is Status.IDLE
            and term_dialog.same_opening(sess, entry))


def _last_exchange(sess):
    from aipager.transcript import last_exchange_at
    return last_exchange_at(sess.transcript_path)


async def _wait_closed(sess, cur: dict) -> bool:
    """After a key: whether the opening closed within
    :data:`term_dialog.ANSWER_CLOSE_WAIT` (the record, read every
    :data:`term_dialog.ANSWER_CLOSE_POLL`, no longer shows it; taken into
    the session's state at once, so the probe lets the next typing
    through)."""
    from aipager.bot import session_ops  # local: import cycle; its sleep seam
    looks = max(1, int(term_dialog.ANSWER_CLOSE_WAIT / term_dialog.ANSWER_CLOSE_POLL))
    for i in range(looks):
        if i:
            await session_ops._sleep(term_dialog.ANSWER_CLOSE_POLL)
        if sess.term_dialog is not cur:
            return True
        entry = await term_dialog.fresh_record(sess)
        if sess.term_dialog is not cur:
            return True
        if entry is not None and not term_dialog.same_opening(sess, entry):
            term_dialog.observe(sess, entry, complete=True,
                                now_mono=term_dialog._now_mono(),
                                now_wall=term_dialog._now_wall(),
                                last_exchange_wall=lambda: _last_exchange(sess))
            return sess.term_dialog is not cur
    return False


def _set_outcome(sess, cur: dict, outcome: str) -> None:
    for rec in sess.term_dialog_closing:
        if rec.get("epoch") == cur.get("epoch"):
            rec["outcome"] = outcome
            return
    if cur.get("card"):
        sess.term_dialog_closing.append({
            "epoch": cur.get("epoch"), "card": cur["card"],
            "card_kind": cur.get("card_kind") or "card", "outcome": outcome})


async def handle_tap(bot: "TelegramBot", update, query, sess, verb: str, epoch: int,
                     chat_id, user_id, shared, person) -> None:
    """A tap on a terminal dialog card's button (the tap gate has passed
    it: APPROVE for Continue and Resume, PROMPT for Start a new
    conversation). Acts only on the opening the card was shown for, only
    once at a time, only while the session is idle; Start a new
    conversation also only for someone who may send ``/clear``."""
    from aipager.bot.session_parity import _drop_keyboard  # local: import cycle
    cur = sess.term_dialog
    if (cur is None or cur.get("epoch") != epoch
            or verb not in _KIND_VERBS.get(cur.get("kind"), ())):
        await bot._safe_answer(query, person.text(TOAST_CLOSED))
        await _drop_keyboard(query)
        return
    if cur.get("answering"):
        await bot._safe_answer(query, person.text(TOAST_ANSWERING))
        return
    if sess.status is not Status.IDLE:
        await bot._safe_answer(query, person.text(TOAST_NOT_IDLE))
        return
    if verb == "rdnew" and bot._command_needs_admin(
            "/clear", user_id, chat_id=bot._attribution_chat(sess, chat_id)):
        from aipager.bot.sibling_text import shown
        from aipager.bot.transport import NEEDS_ADMIN_REPLY
        await bot._safe_answer(query, shown(person, NEEDS_ADMIN_REPLY), show_alert=True)
        return
    cur["answering"] = True  # claimed with no await since the checks
    try:
        await _answer(bot, query, sess, cur, verb, user_id, person)
    finally:
        cur["answering"] = False


async def _answer(bot: "TelegramBot", query, sess, cur: dict, verb: str, user_id,
                  person) -> None:
    from aipager.bot.session_parity import _drop_keyboard  # local: import cycle
    answer = _VERBS[verb]
    if answer == "new":
        # A message sent while /clear runs waits for the new conversation.
        from aipager.bot.handlers import _claim_dialog_hold  # local: import cycle
        _claim_dialog_hold(sess)
    cur["answer"] = answer      # what its close says (term_dialog.close)

    async def still_open() -> bool:
        return await _still_open(sess, cur)

    pressed = await inject.press_key_in_dialog(sess.name, "Escape", still_open)
    if not pressed:
        cur["answer"] = ""
        if sess.term_dialog is cur and await _still_open(sess, cur):
            await bot._safe_answer(query, person.text(TOAST_FAILED))
            return
        if sess.term_dialog is cur:
            # Gone from the record: taken in, so the next typing goes through.
            entry = await term_dialog.fresh_record(sess)
            if entry is not None:
                term_dialog.observe(sess, entry, complete=True,
                                    now_mono=term_dialog._now_mono(),
                                    now_wall=term_dialog._now_wall(),
                                    last_exchange_wall=lambda: _last_exchange(sess))
        await bot._safe_answer(query, person.text(TOAST_CLOSED))
        await _drop_keyboard(query)
        await close_cards(bot, sess)
        return
    log.info("[%s] terminal dialog answered: %s", sess.label, answer)
    if not await _wait_closed(sess, cur):
        # Escape went in and the dialog is still up: whatever it was, its
        # dismiss continues the conversation, never a new one.
        if answer == "new":
            cur["answer"] = "continue"
        await bot._safe_answer(query, person.text(TOAST_FAILED))
        return
    if answer == "new":
        # In the idle variant Escape keeps a draft in the input box: typed
        # onto it, /clear would go to the old conversation as a prompt.
        inject.request_wipe(sess.name, term_dialog.WIPE_DRAFT_CHARS)
        ok = await bot._command_after_dialog(sess, "/clear", user_id)
        if not ok:
            log.warning("[%s] /clear after the resume question could not be typed",
                        sess.label)
            _set_outcome(sess, cur, "new_failed")
    await close_cards(bot, sess)
