"""Problem reports in Telegram (roadmap 8.112 step 3): one preview card.

Every way in leads to the same card in the OWNER's private chat with the
bot: the /help button, the Mini App's Settings row, the hook memory-cap
notice's "Report this" and the automatic offer's "Preview report". The
card shows the exact report (``builder.render_preview``: the bytes Send
sends), and offers Send, Add a note and Cancel.

- **One owner rule** (:func:`resolve_owner`), used by every surface and
  re-checked on every ``_:rp:`` tap: the tap gate lists ``rp:`` as VIEW
  only so that a member's tap reaches this module and is refused here.
- **Nothing is rebuilt at Send.** The report built when the card opened
  is kept with the card (:class:`KeptReport`, in daemon memory, at most
  :data:`MAX_KEPT_CARDS`, each for :data:`KEPT_CARD_TTL`); a note changes
  only its ``note``; Send sends that object, in a worker thread, once.
  After a restart the card has nothing kept and says so.
- **The note capture closes by default.** ``new_flow.close_if_moved_on``
  (the one group -1 pre-handler) calls :func:`on_update` first: anything
  the owner does other than typing the note, or tapping this card's own
  buttons, closes it.
- **The offer's answers** (``_:rp:op|on|od:<ts>``) are bound to the offer
  through its ``last_offer_ts``; a stale or foreign tap changes nothing.

Report content never reaches a log line here.
"""

from __future__ import annotations

import asyncio
import copy
import enum
import html
import importlib.util
import logging
import os
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from telegram import ExternalReplyInfo, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest

from aipager.bot.flood import MUTE
from aipager.bot.flood_budget import PRIORITY_INSTANT, rate_limit_args
from aipager.bot.transport import (
    MUTED,
    SKIPPED,
    TELEGRAM_MAX_TEXT_LEN,
    _message_chat_id,
    calling_chat_id,
    document_upload,
    edit_markup,
    edit_text,
    edit_text_at,
    send_text,
)
from aipager.report import wording

if TYPE_CHECKING:
    import httpx

    from aipager.bot.core import TelegramBot
    from aipager.report.builder import ReportContext

log = logging.getLogger("aipager.bot.report_flow")

#: The httpx transport Send uses: None in production (the real network),
#: an ``httpx.MockTransport`` in tests. Read at tap time.
SEND_TRANSPORT: "httpx.BaseTransport | None" = None

#: Preview cards kept at once (the oldest goes first) and for how long.
MAX_KEPT_CARDS = 5
KEPT_CARD_TTL = 24 * 3600
#: How long the note capture waits for the note.
NOTE_TTL = 600
REPORT_FILENAME = "report.json"

# ---- what the card and its toasts say (no em dashes, plain words) ---------

HEADING_HTML = "🐞 <b>Report a problem</b>"
DOCUMENT_LINE = "The exact report is in the report.json file that replies to this card."
SENDING_TEXT = "Sending..."
LOST_TEXT = ("This preview is no longer open, so nothing was sent. "
             "Open Report a problem again to see a fresh one.")
CANCELLED_TEXT = "Cancelled. Nothing was sent."
DOC_FAILED_TEXT = ("The report.json file could not be sent, so nothing was sent. "
                   "Open Report a problem again later.")
NOTE_PROMPT = ("✏️ Type your note as one message (at most 500 characters). "
               "It goes into the report exactly as shown before you send. "
               "Anything else you do closes this.")
NOTE_ADDED = "Note added."
NOTE_CUT = "Note added, cut to 500 characters."
NOTE_EMPTY = "That note was empty, so nothing was added."
NOTE_REFUSED = "That note could not be added. Try a shorter, plainer one."
NOTE_CLOSED = "Note not added: you did something else. Tap Add a note to try again."
NOT_OWNER_TEXT = "Only the owner of this aipager can report a problem."
NO_OWNER_TEXT = "I can't open a report preview: this install has no single owner chat."
IN_DM_TEXT = "The report preview is in your private chat with the bot."
NOT_OPENED_TEXT = "Could not open the report preview right now. Try again later."
SEND_FAILED_TEXT = ("This report could not be sent because of a problem inside aipager. "
                    "Nothing was sent.")
ALREADY_SENDING = "Already sending."
ALREADY_SENT = "Already sent."
INVALID_TEXT = "Invalid callback"
OFFER_STALE_TEXT = "This offer is no longer open."
OFFER_PREVIEW_TEXT = "Opened the report preview below."
OFFER_DECLINED_TEXT = "OK. aipager won't ask about this again on this version."
OFFER_OFF_TEXT = ("Automatic offers are now off. To turn them back on: "
                  "/settings, then Problem reports.")

SEND_BUTTON = "📤 Send"
NOTE_BUTTON = "✏️ Add a note"
CANCEL_BUTTON = "✖️ Cancel"
BACK_BUTTON_TEXT = "‹ Back"
REPORT_BUTTON = "🐞 Report a problem"
REPORT_THIS_BUTTON = "🐞 Report this"

# /settings (owner DM only)
SETTINGS_TITLE = "🐞 <b>Problem reports</b>"
SETTINGS_INTRO = (
    "When aipager hits the same internal error more than once, it can offer "
    "to send a report. You always see the report first, and nothing is sent "
    "unless you tap Send.\n\n"
    "Ask me: offer it now and then (at most once in 3 days).\n"
    "Off: never offer. Report a problem in /help always works."
)
SETTINGS_AUTO_OFF = "Automatic offers: off (you said no to two offers in a row)."
SETTINGS_ENV_OFF = "Offers are also off on this machine through AIPAGER_REPORT_PROMPTS=0."
SETTINGS_ROW_LABELS = {"ask": "Ask me", "off": "Off", "auto_off": "offers off"}
TURN_ON_BUTTON = "Turn offers back on"

#: Every status line a preview card can carry: the inline form keeps room
#: for the longest, so a status never pushes a card over Telegram's limit.
_STATUS_LINES = (
    SENDING_TEXT, NOTE_ADDED, NOTE_CUT, NOTE_EMPTY, NOTE_REFUSED, NOTE_CLOSED,
    SEND_FAILED_TEXT,
    *wording.OUTCOME_LINES.values(),
)
_STATUS_RESERVE = max(len(line) for line in _STATUS_LINES) + 80

_OFFER_ANSWERS = {"op": "send", "on": "not_now", "od": "dont_ask"}

_INSTANT = rate_limit_args(priority=PRIORITY_INSTANT)


class OpenResult(enum.Enum):
    OPENED = "opened"
    NO_OWNER = "no_owner"
    NOT_SENT = "not_sent"


@dataclass
class KeptReport:
    """One open preview card and the exact report it shows."""
    report: dict | None
    preview: bytes
    chat_id: int
    msg_id: int = 0
    mode: str = "inline"          # "inline" | "document"
    doc_msg_id: int | None = None
    state: str = "open"           # "open" | "note" | "sending" | "done"
    created: float = 0.0          # time.monotonic()
    #: The background task running this card's Send (None until tapped).
    send_task: "asyncio.Task | None" = field(default=None, repr=False, compare=False)


# ---- the owner ------------------------------------------------------------

def resolve_owner(bot: "TelegramBot") -> int | None:
    """The owner's Telegram user id, which is also their private chat's
    id; None when there is no single owner (no positive ``CHAT_ID``, or
    no or several operator DMs in scope mode). Never raises.

    - scope mode: ``setup_cmd._operator_dm`` (the DM scope whose member is
      its own chat, preferring role ``owner``), as setup decides it;
    - personal and legacy team mode: ``CHAT_ID`` when it is a positive
      int (a DM's id is its person's id), read at call time.
    """
    try:
        scopes = getattr(bot, "scopes", None)
        if scopes is not None:
            from aipager.setup_cmd import _operator_dm
            scope, _ambiguous = _operator_dm(list(scopes))
            if scope is None:
                return None
            chat_id = scope.chat_id
        else:
            from aipager import config
            chat_id = int(config.CHAT_ID)
    except Exception:  # noqa: BLE001 - no owner is the safe answer
        return None
    if type(chat_id) is not int or chat_id <= 0:
        return None
    return chat_id


def is_owner(bot: "TelegramBot", user_id) -> bool:
    """Whether *user_id* (an int, never a bool) is the owner."""
    if type(user_id) is not int:
        return False
    owner = resolve_owner(bot)
    return owner is not None and user_id == owner


# ---- what a report says about the daemon ------------------------------------

def _configured_chats(bot: "TelegramBot") -> list[int]:
    scopes = getattr(bot, "scopes", None)
    if scopes is not None:
        return [s.chat_id for s in scopes if type(s.chat_id) is int]
    owner = resolve_owner(bot)
    return [owner] if owner is not None else []


def _features(bot: "TelegramBot") -> list[str]:
    from aipager import config, preferences

    out = []
    if config.MINIAPP_ENABLED:
        out.append("miniapp")
        out.append("tunnel_override" if config.MINIAPP_PUBLIC_URL else "tunnel_managed")
    if config.OBSERVER_BOTS:
        out.append("observers")
    try:
        # Whether faster-whisper is installed, without importing it (the
        # import alone takes seconds).
        if importlib.util.find_spec("faster_whisper") is not None:
            out.append("voice")
    except (ImportError, ValueError):
        pass
    try:
        if any(preferences.get_preferences(c).diff_preview for c in _configured_chats(bot)):
            out.append("diff_preview_any")
    except Exception:  # noqa: BLE001 - a feature left out is fine
        log.debug("report context: diff preview unknown", exc_info=True)
    if config.RICH_SUMMARIES:
        out.append("rich_summaries")
    return out


def report_context(bot: "TelegramBot") -> "ReportContext":
    """The daemon's own facts for a report, from live objects: no
    subprocess, no network (the Claude auth source is the one the daemon
    found at startup, doctor checks are left out). Never raises: a fact
    that cannot be read is left at its default."""
    from aipager.report import builder, markers, store
    from aipager.report import schema as sc
    from aipager.state import Status

    ctx = builder.ReportContext()
    try:
        ctx.scopes = getattr(bot, "scopes", None)
        ctx.mode = ("scope" if ctx.scopes is not None
                    else "team" if getattr(bot, "team", None) is not None else "personal")
        roles = getattr(getattr(bot, "policy", None), "roles", None) or {}
        ctx.custom_role_names = [r for r in roles if isinstance(r, str)]
    except Exception:  # noqa: BLE001
        log.debug("report context: config facts unknown", exc_info=True)
    try:
        ctx.features = _features(bot)
    except Exception:  # noqa: BLE001
        log.debug("report context: features unknown", exc_info=True)
    started = markers.started_at()
    if started is not None:
        ctx.uptime_seconds = max(time.time() - started, 0.0)
    try:
        sessions = list(bot.registry.all_sessions().values())
        ctx.sessions_live = sum(1 for s in sessions
                                if s.status not in (Status.GONE, Status.UNKNOWN))
        ctx.sessions_busy = sum(1 for s in sessions if s.status == Status.BUSY)
    except Exception:  # noqa: BLE001
        log.debug("report context: sessions unknown", exc_info=True)
    try:
        ctx.unclean_exits_7d, ctx.last_exit = store.exit_facts()
    except Exception:  # noqa: BLE001
        log.debug("report context: exits unknown", exc_info=True)
    try:
        from aipager.status import read_flood_chats
        ctx.flood_rows = read_flood_chats()
    except Exception:  # noqa: BLE001 - a broken flood file still reports
        ctx.flood_rows = []
    ctx.doctor = []
    source = getattr(bot, "claude_auth_source", sc.UNKNOWN)
    allowed = sc.SCHEMA["claude_code"]["auth"].values
    ctx.claude_auth_source = source if source in allowed else sc.UNKNOWN
    return ctx


# ---- kept reports -----------------------------------------------------------

def _cards(bot: "TelegramBot") -> dict:
    cards = getattr(bot, "_report_cards", None)
    if cards is None:
        cards = {}
        bot._report_cards = cards
    return cards


def _prune(bot: "TelegramBot") -> None:
    cards = _cards(bot)
    now = time.monotonic()
    for key in [k for k, kept in cards.items() if now - kept.created > KEPT_CARD_TTL]:
        del cards[key]


def _keep(bot: "TelegramBot", kept: KeptReport) -> None:
    _prune(bot)
    cards = _cards(bot)
    cards[(kept.chat_id, kept.msg_id)] = kept
    while len(cards) > MAX_KEPT_CARDS:
        oldest = min(cards, key=lambda k: cards[k].created)
        del cards[oldest]


def _lookup(bot: "TelegramBot", chat_id, msg_id) -> KeptReport | None:
    _prune(bot)
    kept = _cards(bot).get((chat_id, msg_id))
    return kept


# ---- rendering ----------------------------------------------------------------

def _esc(text: str) -> str:
    """HTML-escape for Telegram: only ``&``, ``<`` and ``>`` matter."""
    return html.escape(text, quote=False)


def _utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _explanation_html() -> str:
    return "\n".join(_esc(line) for line in wording.EXPLANATION)


def _fits_inline(preview: bytes) -> bool:
    """Whether the card with the whole report (and the longest status
    line) stays inside one Telegram message, counted as Telegram counts
    it: UTF-16 units of the text after the HTML is parsed."""
    visible = "\n\n".join(["🐞 Report a problem", "\n".join(wording.EXPLANATION),
                           preview.decode("utf-8")])
    return _utf16_len(visible) + _STATUS_RESERVE <= TELEGRAM_MAX_TEXT_LEN


def card_text(kept: KeptReport, status: str | None = None) -> str:
    """The card's HTML: heading, explanation, an optional status line,
    then the exact report (inline) or the line naming its file."""
    parts = [HEADING_HTML, _explanation_html()]
    if status:
        parts.append(f"<b>{_esc(status)}</b>")
    if kept.mode == "inline":
        body = _esc(kept.preview.decode("utf-8"))
        parts.append(f"<blockquote expandable><pre>{body}</pre></blockquote>")
    else:
        parts.append(_esc(DOCUMENT_LINE))
    return "\n\n".join(parts)


def _capture_text() -> str:
    return f"{HEADING_HTML}\n\n{_esc(NOTE_PROMPT)}"


def _final_text(line: str) -> str:
    return f"{HEADING_HTML}\n\n{_esc(line)}"


def preview_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(SEND_BUTTON, callback_data="_:rp:send"),
         InlineKeyboardButton(NOTE_BUTTON, callback_data="_:rp:note")],
        [InlineKeyboardButton(CANCEL_BUTTON, callback_data="_:rp:cancel")],
    ])


def _capture_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(BACK_BUTTON_TEXT, callback_data="_:rp:back"),
        InlineKeyboardButton(CANCEL_BUTTON, callback_data="_:rp:cancel"),
    ]])


def report_button_markup(label: str = REPORT_BUTTON) -> InlineKeyboardMarkup:
    """The one-button keyboard that opens a manual preview (/help, the
    memory-cap notice)."""
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data="_:rp:open")]])


# ---- Telegram calls (all mute-aware, all INSTANT: a tap's answer) -----------

def _sent_id(sent) -> int | None:
    if sent is None or sent is MUTED or sent is SKIPPED:
        return None
    msg_id = getattr(sent, "message_id", None)
    return msg_id if type(msg_id) is int else None


async def _edit_card(bot: "TelegramBot", kept: KeptReport, text: str,
                     markup: InlineKeyboardMarkup | None) -> bool:
    """Edit *kept*'s card; whether the edit went out. Never raises."""
    try:
        res = await edit_text_at(bot._app.bot, text=text, chat_id=kept.chat_id,
                                 message_id=kept.msg_id, parse_mode="HTML",
                                 reply_markup=markup, rate_limit_args=_INSTANT)
    except BadRequest as e:
        if "not modified" in str(e).lower():
            return True
        log.debug("report card edit refused (%s)", type(e).__name__)
        return False
    except Exception as e:  # noqa: BLE001 - FloodMuted, network: the card stays
        log.debug("report card edit failed (%s)", type(e).__name__)
        return False
    return res is not MUTED and res is not SKIPPED and res is not None


async def _send_document(bot: "TelegramBot", kept: KeptReport) -> int | None:
    """``report.json`` (the exact preview bytes) as a reply to the card."""
    if MUTE.is_muted(kept.chat_id):
        return None
    app = bot._app
    try:
        sent = await app.bot.send_document(
            kept.chat_id, document=document_upload(kept.preview, REPORT_FILENAME),
            filename=REPORT_FILENAME, reply_to_message_id=kept.msg_id,
            rate_limit_args=_INSTANT)
    except Exception as e:  # noqa: BLE001 - FloodMuted, refusal, network
        log.debug("report.json send failed (%s)", type(e).__name__)
        return None
    return _sent_id(sent)


async def _delete_message(bot: "TelegramBot", chat_id: int, msg_id: int | None) -> None:
    if msg_id is None or MUTE.is_muted(chat_id):
        return
    app = bot._app
    try:
        await app.bot.delete_message(chat_id, msg_id, rate_limit_args=_INSTANT)
    except Exception as e:  # noqa: BLE001 - best effort
        log.debug("old report.json delete failed (%s)", type(e).__name__)


async def _post_card(bot: "TelegramBot", kept: KeptReport) -> bool:
    """Send *kept*'s card as a new message in its chat (inline when the
    report fits, else with ``report.json`` replying to it). Sets
    ``msg_id`` (and ``doc_msg_id``); whether the card and its report went
    out."""
    app = bot._app
    if kept.mode == "inline":
        try:
            sent = await send_text(app.bot, kept.chat_id, card_text(kept), parse_mode="HTML",
                                   reply_markup=preview_keyboard(), rate_limit_args=_INSTANT)
        except BadRequest as e:
            # The inline form was refused (a parse or length problem): the
            # document form never loses a byte.
            log.info("report preview inline form refused (%s): sending report.json",
                     type(e).__name__)
            kept.mode = "document"
        except Exception as e:  # noqa: BLE001
            log.debug("report preview send failed (%s)", type(e).__name__)
            return False
        else:
            msg_id = _sent_id(sent)
            if msg_id is None:
                return False
            kept.msg_id = msg_id
            return True
    try:
        sent = await send_text(app.bot, kept.chat_id, card_text(kept), parse_mode="HTML",
                               reply_markup=preview_keyboard(), rate_limit_args=_INSTANT)
    except Exception as e:  # noqa: BLE001
        log.debug("report preview send failed (%s)", type(e).__name__)
        return False
    msg_id = _sent_id(sent)
    if msg_id is None:
        return False
    kept.msg_id = msg_id
    kept.doc_msg_id = await _send_document(bot, kept)
    if kept.doc_msg_id is None:
        # Never offer to send what the owner could not see.
        await _edit_card(bot, kept, _final_text(DOC_FAILED_TEXT), None)
        return False
    return True


# ---- opening a preview --------------------------------------------------------

def _build(trigger: str, errors: list[dict] | None, ctx: "ReportContext") -> dict:
    """The report (reads files: runs in a worker thread)."""
    from aipager.report import builder, store

    if errors is None:
        errors = store.errors()
    return builder.build_report(trigger, errors=errors, counters=store.counters_24h(),
                                log_digest=store.digest_24h(), context=ctx)


async def open_preview(bot: "TelegramBot", *, trigger: str,
                       errors: list[dict] | None = None) -> OpenResult:
    """Build a report and post its preview card in the owner's private
    chat. *errors* None means the store's most recent ones (a manual
    report); the automatic offer passes the offered entries."""
    from aipager.report import send

    owner = resolve_owner(bot)
    if owner is None:
        return OpenResult.NO_OWNER
    ctx = report_context(bot)
    try:
        report = await asyncio.to_thread(_build, trigger, errors, ctx)
    except Exception as e:  # noqa: BLE001
        log.warning("problem report could not be built (%s)", type(e).__name__)
        return OpenResult.NOT_SENT
    preview = send.checked_preview(report)
    if preview is None:
        log.warning("problem report did not pass its own checks: no preview")
        return OpenResult.NOT_SENT
    kept = KeptReport(report=report, preview=preview, chat_id=owner,
                      mode="inline" if _fits_inline(preview) else "document",
                      created=time.monotonic())
    if not await _post_card(bot, kept):
        return OpenResult.NOT_SENT
    _keep(bot, kept)
    return OpenResult.OPENED


# ---- the note capture -----------------------------------------------------------

def _note_pending(bot: "TelegramBot") -> dict | None:
    return getattr(bot, "_report_note_pending", None)


def _keyboard_word(bot: "TelegramBot", text: str) -> bool:
    """A main-keyboard word (the sets the /new Name card tells apart):
    a tap on the keyboard, never a note."""
    from aipager.bot import new_flow
    from aipager.config import (
        APP_BUTTON,
        BACK_BUTTON,
        COMMANDS_BUTTON,
        MODELS_BUTTON,
        TEMPLATES_BUTTON,
    )

    word = text.strip().lower()
    if word in new_flow._LOOKING_WORDS or word in new_flow._ACTING_WORDS:
        return True
    if text in (TEMPLATES_BUTTON, COMMANDS_BUTTON, MODELS_BUTTON, BACK_BUTTON, APP_BUTTON):
        return True
    return any(text in (getattr(bot, attr, None) or {})
               for attr in ("_template_map", "_command_map", "_model_map"))


def _is_note_answer(bot: "TelegramBot", update, pending: dict) -> bool:
    """Whether *update* is the note the capture waits for: a plain text
    message (not a command, a keyboard word, a forward, or a reply to
    another message) from its person, in its chat, in time."""
    if getattr(update, "callback_query", None) is not None:
        return False
    msg = getattr(update, "message", None)
    if msg is None:
        return False
    if calling_chat_id(update) != pending["chat_id"]:
        return False
    if getattr(getattr(update, "effective_user", None), "id", None) != pending["user_id"]:
        return False
    if time.monotonic() - pending["last_active"] > NOTE_TTL:
        return False
    text = getattr(msg, "text", None)
    if not isinstance(text, str) or text.strip().startswith("/"):
        return False
    if (getattr(msg, "forward_origin", None) is not None
            or getattr(msg, "via_bot", None) is not None):
        return False
    reply_to = getattr(msg, "reply_to_message", None)
    if reply_to is not None and getattr(reply_to, "message_id", None) != pending["msg_id"]:
        return False
    if isinstance(getattr(msg, "external_reply", None), ExternalReplyInfo):
        return False
    return not _keyboard_word(bot, text.strip())


def _spawn(coro) -> "asyncio.Task":
    from aipager.bot.notify import _BACKGROUND_TASKS  # local: import cycle
    task = asyncio.create_task(coro)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    return task


def close_note(bot: "TelegramBot", user_id=None) -> None:
    """The owner did something else: the capture stops waiting, and its
    card goes back to the preview saying the note was not added. With a
    *user_id*, only that person's capture closes. Never raises."""
    try:
        pending = _note_pending(bot)
        if not pending or (user_id is not None and pending["user_id"] != user_id):
            return
        bot._report_note_pending = None
        kept = _lookup(bot, pending["chat_id"], pending["msg_id"])
        if kept is None or kept.state != "note":
            return
        kept.state = "open"
        _spawn(_edit_card(bot, kept, card_text(kept, NOTE_CLOSED), preview_keyboard()))
    except Exception:  # noqa: BLE001
        log.debug("report note close failed", exc_info=True)


def _stamp_owner_activity(bot: "TelegramBot", update) -> None:
    owner = resolve_owner(bot)
    if owner is None:
        return
    user_id = getattr(getattr(update, "effective_user", None), "id", None)
    if user_id == owner and type(user_id) is int and calling_chat_id(update) == owner:
        bot._report_owner_seen_at = time.time()


def _own_card_tap(update, pending: dict) -> bool:
    query = getattr(update, "callback_query", None)
    if query is None:
        return False
    data = getattr(query, "data", None)
    message = getattr(query, "message", None)
    return (isinstance(data, str) and data.startswith("_:rp:")
            and _message_chat_id(message) == pending["chat_id"]
            and getattr(message, "message_id", None) == pending["msg_id"])


def on_update(bot: "TelegramBot", update) -> None:
    """Every update, before any handler (``new_flow.close_if_moved_on``):
    note when the owner last acted in their private chat, and close the
    note capture on anything its person does other than typing the note
    or tapping the card's own buttons. Never raises."""
    try:
        _stamp_owner_activity(bot, update)
    except Exception:  # noqa: BLE001
        log.debug("report owner activity stamp failed", exc_info=True)
    try:
        pending = _note_pending(bot)
        if not pending:
            return
        user_id = getattr(getattr(update, "effective_user", None), "id", None)
        if user_id != pending["user_id"]:
            return
        if _is_note_answer(bot, update, pending) or _own_card_tap(update, pending):
            return
        close_note(bot, user_id)
    except Exception:  # noqa: BLE001
        log.debug("report note pre-check failed", exc_info=True)


async def maybe_handle_text(bot: "TelegramBot", update, ctx, text: str) -> bool:
    """Take *text* as the note when the capture waits for it (True), else
    leave it to the usual routing (False)."""
    from aipager.report import schema as sc
    from aipager.report import send

    pending = _note_pending(bot)
    if not pending or not _is_note_answer(bot, update, pending):
        return False
    bot._report_note_pending = None
    kept = _lookup(bot, pending["chat_id"], pending["msg_id"])
    if kept is None or kept.state != "note" or kept.report is None:
        try:
            await send_text(bot._app.bot, pending["chat_id"], LOST_TEXT,
                            rate_limit_args=_INSTANT)
        except Exception as e:  # noqa: BLE001
            log.debug("report lost-card reply failed (%s)", type(e).__name__)
        return True
    kept.state = "open"
    note = sc.normalize_note(text)
    if note is None:
        await _edit_card(bot, kept, card_text(kept, NOTE_EMPTY), preview_keyboard())
        return True
    candidate = copy.deepcopy(kept.report)
    candidate["note"] = note
    clean, _problems = sc.validate(candidate)
    preview = send.checked_preview(clean) if clean == candidate else None
    if preview is None:
        await _edit_card(bot, kept, card_text(kept, NOTE_REFUSED), preview_keyboard())
        return True
    kept.report, kept.preview = clean, preview
    status = NOTE_CUT if len(sc.clean_note(text).strip()) > sc.NOTE_MAX else NOTE_ADDED
    old_doc = None
    if kept.mode == "inline" and not _fits_inline(preview):
        kept.mode = "document"
    if kept.mode == "document":
        old_doc = kept.doc_msg_id
        kept.doc_msg_id = await _send_document(bot, kept)
        if kept.doc_msg_id is None:
            kept.state = "done"
            kept.report = None
            await _edit_card(bot, kept, _final_text(DOC_FAILED_TEXT), None)
            return True
    await _edit_card(bot, kept, card_text(kept, status), preview_keyboard())
    if old_doc is not None:
        await _delete_message(bot, kept.chat_id, old_doc)
    return True


# ---- taps ---------------------------------------------------------------------

def outcome_line(outcome: str, reference: str | None = None) -> str:
    """The card's line for one send outcome (the CLI's wording); an
    unknown one reads as try later."""
    return wording.OUTCOME_LINES.get(outcome, wording.TRY_LATER).format(reference=reference)


async def _tap_open(bot: "TelegramBot", query, chat_id) -> None:
    owner = resolve_owner(bot)
    result = await open_preview(bot, trigger="manual")
    if result is OpenResult.OPENED:
        if chat_id != owner:
            await bot._safe_answer(query, IN_DM_TEXT, show_alert=True)
    elif result is OpenResult.NO_OWNER:
        await bot._safe_answer(query, NO_OWNER_TEXT, show_alert=True)
    else:
        await bot._safe_answer(query, NOT_OPENED_TEXT, show_alert=True)


async def _lost(query) -> None:
    try:
        await edit_text(query, _final_text(LOST_TEXT), parse_mode="HTML",
                        reply_markup=None, rate_limit_args=_INSTANT)
    except Exception as e:  # noqa: BLE001
        log.debug("report lost-card edit failed (%s)", type(e).__name__)


def _drop_note_for(bot: "TelegramBot", kept: KeptReport) -> None:
    pending = _note_pending(bot)
    if pending and (pending["chat_id"], pending["msg_id"]) == (kept.chat_id, kept.msg_id):
        bot._report_note_pending = None


async def _tap_send(bot: "TelegramBot", query, kept: KeptReport) -> None:
    if kept.state == "sending":
        await bot._safe_answer(query, ALREADY_SENDING)
        return
    if kept.state == "done" or kept.report is None:
        await bot._safe_answer(query, ALREADY_SENT)
        return
    # Claimed before anything awaits: a second tap sees it.
    kept.state = "sending"
    _drop_note_for(bot, kept)
    # The network call (up to ~20 s) runs in its own task: PTB handles
    # updates one at a time, so awaiting it here would hold every chat's
    # messages and taps (Stop included) behind it.
    kept.send_task = _spawn(_run_send(bot, kept))


async def _run_send(bot: "TelegramBot", kept: KeptReport) -> None:
    """Send *kept*'s report once, in a worker thread, and put the outcome
    on its card. Never raises."""
    from aipager.report import send

    await _edit_card(bot, kept, card_text(kept, SENDING_TEXT), None)
    try:
        result = await asyncio.to_thread(send.send, kept.report, transport=SEND_TRANSPORT)
    except Exception as e:  # noqa: BLE001 - send.send never raises; belt and braces
        log.warning("problem report send failed (%s)", type(e).__name__)
        result = None
    if result is None:
        # Something inside aipager broke: the same report would break the
        # same way, so this is final (no Send button to tap again).
        line = SEND_FAILED_TEXT
    else:
        line = outcome_line(result.outcome, result.reference)
        log.info("problem report send: %s", result.outcome)
        if result.outcome in wording.RETRYABLE:
            kept.state = "open"
            await _edit_card(bot, kept, card_text(kept, line), preview_keyboard())
            return
    # Final: the report is let go; the entry stays so a late second tap
    # says "Already sent." instead of overwriting the outcome.
    kept.state = "done"
    kept.report = None
    await _edit_card(bot, kept, card_text(kept, line), None)


async def _tap_note(bot: "TelegramBot", query, kept: KeptReport, user_id: int) -> None:
    from aipager.bot import new_flow, session_parity

    if kept.state in ("sending", "done") or kept.report is None:
        await bot._safe_answer(query, ALREADY_SENDING if kept.state == "sending"
                               else ALREADY_SENT)
        return
    if not await _edit_card(bot, kept, _capture_text(), _capture_keyboard()):
        return  # never shown: nothing may take the next message as the note
    kept.state = "note"
    # Their newest question wins: their Name card and rename question close.
    new_flow.close_open_card(bot, kept.chat_id, user_id)
    session_parity.close_rename(bot, kept.chat_id, user_id)
    previous = _note_pending(bot)
    if previous and (previous["chat_id"], previous["msg_id"]) != (kept.chat_id, kept.msg_id):
        close_note(bot)
    bot._report_note_pending = {"chat_id": kept.chat_id, "user_id": user_id,
                                "msg_id": kept.msg_id, "last_active": time.monotonic()}


async def _tap_back(bot: "TelegramBot", query, kept: KeptReport) -> None:
    _drop_note_for(bot, kept)
    if kept.state == "note":
        kept.state = "open"
    if kept.state != "open":
        await bot._safe_answer(query, ALREADY_SENDING if kept.state == "sending"
                               else ALREADY_SENT)
        return
    await _edit_card(bot, kept, card_text(kept), preview_keyboard())


async def _tap_cancel(bot: "TelegramBot", query, kept: KeptReport | None) -> None:
    if kept is not None:
        if kept.state == "sending":
            await bot._safe_answer(query, ALREADY_SENDING)
            return
        _drop_note_for(bot, kept)
        _cards(bot).pop((kept.chat_id, kept.msg_id), None)
    try:
        await edit_text(query, _final_text(CANCELLED_TEXT), parse_mode="HTML",
                        reply_markup=None, rate_limit_args=_INSTANT)
    except Exception as e:  # noqa: BLE001
        log.debug("report cancel edit failed (%s)", type(e).__name__)


# ---- the automatic offer's answers ----------------------------------------------

async def _strip_keyboard(query) -> None:
    try:
        await edit_markup(query, reply_markup=None, rate_limit_args=_INSTANT)
    except Exception as e:  # noqa: BLE001
        log.debug("offer keyboard strip failed (%s)", type(e).__name__)


async def _answer_offer(bot: "TelegramBot", query, verb: str, ts_text: str) -> None:
    from aipager.report import policy, store

    now = int(time.time())
    state = store.policy_state()
    settled = policy.settle(state, now)
    if settled != state:
        store.save_policy(settled)
    offer_ts = int(ts_text) if ts_text.isdigit() and len(ts_text) <= 12 else None
    if offer_ts is None or not settled.pending or settled.last_offer_ts != offer_ts:
        await bot._safe_answer(query, OFFER_STALE_TEXT)
        await _strip_keyboard(query)
        return
    offered = settled.pending
    answered = policy.note_answer(settled, _OFFER_ANSWERS[verb])
    store.save_policy(answered)
    if verb == "op":
        try:
            await edit_text(query, OFFER_PREVIEW_TEXT, reply_markup=None,
                            rate_limit_args=_INSTANT)
        except Exception as e:  # noqa: BLE001
            log.debug("offer notice edit failed (%s)", type(e).__name__)
        entries = {r["entry"]["fingerprint"]: r["entry"] for r in store.records()}
        errors = [entries[fp] for fp in offered if fp in entries]
        result = await open_preview(bot, trigger="auto", errors=errors)
        if result is not OpenResult.OPENED:
            await bot._safe_answer(query, NOT_OPENED_TEXT, show_alert=True)
        return
    text = OFFER_DECLINED_TEXT
    if answered.auto_off and not settled.auto_off:
        text += " " + OFFER_OFF_TEXT
    try:
        await edit_text(query, text, reply_markup=None, rate_limit_args=_INSTANT)
    except Exception as e:  # noqa: BLE001
        log.debug("offer notice edit failed (%s)", type(e).__name__)


# ---- /settings (owner DM only) ----------------------------------------------------

def _env_off() -> bool:
    return os.environ.get("AIPAGER_REPORT_PROMPTS") == "0"


def settings_row_state(bot: "TelegramBot", chat_id, user_id) -> str | None:
    """The /settings root row's value ("Ask me", "Off", "offers off"),
    or None when the row does not belong: only the owner, in their own
    private chat."""
    owner = resolve_owner(bot)
    if owner is None or chat_id != owner or not is_owner(bot, user_id):
        return None
    from aipager import preferences
    from aipager.report import store

    if preferences.get_problem_reports() == "off":
        return SETTINGS_ROW_LABELS["off"]
    try:
        if store.policy_state().auto_off:
            return SETTINGS_ROW_LABELS["auto_off"]
    except Exception:  # noqa: BLE001
        log.debug("report policy state unreadable", exc_info=True)
    return SETTINGS_ROW_LABELS["ask"]


def render_settings_section() -> tuple[str, InlineKeyboardMarkup]:
    """The Problem reports page of /settings."""
    from aipager import preferences
    from aipager.report import store

    current = preferences.get_problem_reports()
    auto_off = store.policy_state().auto_off
    lines = [SETTINGS_TITLE, _esc(SETTINGS_INTRO)]
    if auto_off:
        lines.append(_esc(SETTINGS_AUTO_OFF))
    if _env_off():
        lines.append(_esc(SETTINGS_ENV_OFF))
    def _label(value: str) -> str:
        return SETTINGS_ROW_LABELS[value] + (" ✅" if value == current else "")

    rows = [[InlineKeyboardButton(_label("ask"), callback_data="_:rp:set:ask")],
            [InlineKeyboardButton(_label("off"), callback_data="_:rp:set:off")]]
    if auto_off:
        rows.append([InlineKeyboardButton(TURN_ON_BUTTON, callback_data="_:rp:set:on")])
    rows.append([InlineKeyboardButton("« Back", callback_data="_:set:back")])
    return "\n\n".join(lines), InlineKeyboardMarkup(rows)


async def _tap_settings(bot: "TelegramBot", query, parts: list[str]) -> None:
    from aipager import preferences
    from aipager.report import policy, store

    if len(parts) == 2:
        choice = parts[1]
        if choice in ("ask", "off"):
            preferences.set_problem_reports(choice)
        elif choice == "on":
            store.save_policy(policy.reenable(store.policy_state()))
        else:
            await bot._safe_answer(query, INVALID_TEXT)
            return
    text, kb = render_settings_section()
    try:
        await edit_text(query, text, parse_mode="HTML", reply_markup=kb,
                        rate_limit_args=_INSTANT)
    except Exception as e:  # noqa: BLE001
        log.debug("report settings edit failed (%s)", type(e).__name__)


# ---- the dispatcher ---------------------------------------------------------------

async def handle_callback(bot: "TelegramBot", update, query, session_name: str,
                          action: str) -> bool:
    """True iff this is an ``_:rp:`` callback (handled here). Every tap is
    re-checked: only the owner, and everything but ``open`` only in the
    owner's private chat."""
    if session_name != "_" or not action.startswith("rp:"):
        return False
    parts = action.split(":")[1:]
    verb = parts[0] if parts else ""
    user_id = getattr(getattr(query, "from_user", None), "id", None)
    message = getattr(query, "message", None)
    chat_id = _message_chat_id(message)
    if chat_id is None:
        chat_id = calling_chat_id(update)
    owner = resolve_owner(bot)
    if owner is None:
        await bot._safe_answer(query, NO_OWNER_TEXT, show_alert=True)
        return True
    if not is_owner(bot, user_id):
        log.info("report tap refused: not the owner (action %s)", verb)
        await bot._safe_answer(query, NOT_OWNER_TEXT, show_alert=True)
        return True
    if verb == "open" and len(parts) == 1:
        await _tap_open(bot, query, chat_id)
        return True
    if chat_id != owner:
        # Cards, offers and the settings page live in the owner's DM only.
        await bot._safe_answer(query, NOT_OWNER_TEXT, show_alert=True)
        return True
    if verb in _OFFER_ANSWERS and len(parts) == 2:
        await _answer_offer(bot, query, verb, parts[1])
        return True
    if verb == "set" and len(parts) <= 2:
        await _tap_settings(bot, query, parts)
        return True
    if verb not in ("send", "note", "back", "cancel") or len(parts) != 1:
        await bot._safe_answer(query, INVALID_TEXT)
        return True
    kept = _lookup(bot, chat_id, getattr(message, "message_id", None))
    if verb == "cancel":
        await _tap_cancel(bot, query, kept)
        return True
    if kept is None:
        await _lost(query)
        return True
    if verb == "send":
        await _tap_send(bot, query, kept)
    elif verb == "note":
        await _tap_note(bot, query, kept, user_id)
    else:
        await _tap_back(bot, query, kept)
    return True
