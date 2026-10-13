"""Problem reports in Telegram (roadmap 8.112 step 3): one preview card.

Every way in from Telegram leads to the same card in the OWNER's private
chat with the bot: the /help button, the hook memory-cap notice's
"Report this" and the automatic offer's "Preview report" (the Mini App
has its own page, :mod:`aipager.bot.report_drafts`, built by the same
:func:`build_checked`). The
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
import re
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
    tapper_tr,
)
from aipager import i18n
from aipager.i18n import Msg, Plural
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

HEADING_HTML = Msg("rep.heading", "🐞 <b>Report a problem</b>",
                   note="The problem report card's title (the /help button has the same "
                        "words)")
DOCUMENT_LINE = Msg("rep.document_line",
                    "The exact report is in the report.json file that replies to this card.",
                    note="Report card: the report is too long for the card; keep report.json")
SENDING_TEXT = Msg("rep.status.sending", "Sending...", note="Report card status line")
LOST_TEXT = Msg("rep.lost",
                "This preview is no longer open, so nothing was sent. "
                "Open Report a problem again to see a fresh one.",
                note="Report card: aipager restarted or the card expired; 'Report a problem' "
                     "is the /help button")
CANCELLED_TEXT = Msg("rep.cancelled", "Cancelled. Nothing was sent.",
                     note="Report card after Cancel")
DOC_FAILED_TEXT = Msg("rep.doc_failed",
                      "The report.json file could not be sent, so nothing was sent. "
                      "Open Report a problem again later.",
                      note="Report card; keep report.json")
#: The note prompt and the cut note in English, as they read with
#: ``schema.NOTE_MAX`` = 500 (shown through the Plurals below).
NOTE_PROMPT = ("✏️ Type your note as one message (at most 500 characters). "
               "It goes into the report exactly as shown before you send. "
               "Anything else you do closes this.")
NOTE_ADDED = Msg("rep.status.note_added", "Note added.", note="Report card status line")
NOTE_CUT = "Note added, cut to 500 characters."
NOTE_EMPTY = Msg("rep.status.note_empty", "That note was empty, so nothing was added.",
                 note="Report card status line")
NOTE_REFUSED = Msg("rep.status.note_refused",
                   "That note could not be added. Try a shorter, plainer one.",
                   note="Report card status line")
NOTE_CLOSED = Msg("rep.status.note_closed",
                  "Note not added: you did something else. Tap Add a note to try again.",
                  note="Report card status line; 'Add a note' is the card's button")
NOT_OWNER_TEXT = Msg("toast.rep.not_owner",
                     "Only the owner of this aipager can report a problem.",
                     note="Pop-up: someone other than the owner tapped a report button")
NO_OWNER_TEXT = Msg("toast.rep.no_owner",
                    "I can't open a report preview: this install has no single owner chat.",
                    note="Pop-up")
IN_DM_TEXT = Msg("toast.rep.in_dm", "The report preview is in your private chat with the bot.",
                 note="Pop-up: the card opened in the owner's private chat")
NOT_OPENED_TEXT = Msg("toast.rep.not_opened",
                      "Could not open the report preview right now. Try again later.",
                      note="Pop-up")
SEND_FAILED_TEXT = Msg("rep.status.send_failed",
                       "This report could not be sent because of a problem inside aipager. "
                       "Nothing was sent.",
                       note="Report card and Mini App: sending failed inside aipager")
ALREADY_SENDING = Msg("toast.rep.already_sending", "Already sending.", note="Pop-up")
ALREADY_SENT = Msg("toast.rep.already_sent", "Already sent.", note="Pop-up")
INVALID_TEXT = "Invalid callback"
OFFER_STALE_TEXT = Msg("toast.rep.offer_stale", "This offer is no longer open.",
                       note="Pop-up: an old report offer's button was tapped")
OFFER_PREVIEW_TEXT = Msg("rep.offer.preview", "Opened the report preview below.",
                         note="The report offer after Preview report")
OFFER_NOT_OPENED_TEXT = Msg("rep.offer.not_opened",
                            "Could not open the report preview. "
                            "Report a problem in /help opens a fresh one.",
                            note="The report offer; 'Report a problem' is the /help button")
OFFER_DECLINED_TEXT = Msg("rep.offer.declined",
                          "OK. aipager won't ask about this again on this version.",
                          note="The report offer after Not now or Don't ask for this")
OFFER_OFF_TEXT = Msg("rep.offer.off",
                     "Automatic offers are now off. To turn them back on: "
                     "/settings, then Problem reports.",
                     note="Follows rep.offer.declined; 'Problem reports' is the /settings page")

SEND_BUTTON = Msg("btn.rep.send", "📤 Send", note="Report card button: send the report")
NOTE_BUTTON = Msg("btn.rep.note", "✏️ Add a note", note="Report card button")
CANCEL_BUTTON = Msg("btn.rep.cancel", "✖️ Cancel", note="Report card button")
BACK_BUTTON_TEXT = Msg("btn.rep.back", "‹ Back", note="Report card button: back to the report")
REPORT_BUTTON = Msg("btn.rep.report", "🐞 Report a problem",
                    note="/help button: open a problem report")
REPORT_THIS_BUTTON = Msg("btn.rep.report_this", "🐞 Report this",
                         note="Button under a notice about an internal problem")

_NOTE_PROMPT = Plural("rep.note.prompt",
                      one="✏️ Type your note as one message (at most {n} character). "
                          "It goes into the report exactly as shown before you send. "
                          "Anything else you do closes this.",
                      other="✏️ Type your note as one message (at most {n} characters). "
                            "It goes into the report exactly as shown before you send. "
                            "Anything else you do closes this.",
                      note="Report card: waiting for a note; {n} is 500")
_NOTE_CUT = Plural("rep.status.note_cut", one="Note added, cut to {n} character.",
                   other="Note added, cut to {n} characters.",
                   note="Report card status line; {n} is 500")
#: The bot's copy of the CLI's wording (``aipager.report.wording``), keyed:
#: its English equals the CLI's table (a test pins it).
EXPLANATION = Msg("rep.explanation",
                  "This is everything the report contains. Nothing has been sent yet.\n"
                  "It has no chats, prompts, session names, folders, tokens, chat ids or "
                  "usernames.\n"
                  "It goes to the aipager maintainer's error inbox (Sentry), anonymously.",
                  note="Report card: what a report holds. Keep the exact meaning: nothing "
                       "is sent yet; none of the listed things is in it; Sentry is a name")
_TRY_LATER = Msg("rep.outcome.try_later",
                 "Could not send right now. Nothing was lost; try again later.",
                 note="Report send outcome")
OUTCOME_MSGS: dict = {
    "sent": Msg("rep.outcome.sent",
                "Sent. Reference {reference} (quote it if you open a GitHub issue).",
                note="Report sent; {reference} is its id", eg={"reference": "ap1-x7k2"}),
    "rate_limited": _TRY_LATER,
    "rejected": _TRY_LATER,
    "offline": _TRY_LATER,
    "disabled": Msg("rep.outcome.disabled",
                    "Problem reports are switched off by the maintainer right now. "
                    "Nothing was sent.", note="Report send outcome"),
    "too_old": Msg("rep.outcome.too_old",
                   "This aipager version can no longer send reports. Update it ({command}) "
                   "and try again.", note="{command} is aipager update",
                   eg={"command": "aipager update"}),
    "daily_cap": Plural("rep.outcome.daily_cap",
                        one="Reports are limited to {n} a day from one machine. "
                            "Try again tomorrow.",
                        other="Reports are limited to {n} a day from one machine. "
                              "Try again tomorrow.",
                        note="Report send outcome; {n} is 5"),
    "invalid": Msg("rep.outcome.invalid",
                   "This report did not pass its own checks, so it was not sent.",
                   note="Report send outcome"),
}

# /settings (owner DM only)
SETTINGS_TITLE = Msg("set.text.reports_title", "🐞 <b>Problem reports</b>",
                     note="/settings > Problem reports: the page's title")
SETTINGS_INTRO = Msg(
    "set.text.reports_intro",
    "When aipager hits the same internal error more than once, it can offer "
    "to send a report. You always see the report first, and nothing is sent "
    "unless you tap Send.\n\n"
    "Ask me: offer it now and then (at most once in 3 days).\n"
    "Off: never offer. Report a problem in /help always works.",
    note="/settings > Problem reports. 'Send' is the report card's button; 'Ask me' "
         "and 'Off' are this page's two buttons; 'Report a problem' is a /help button: "
         "use the same words as those keys")
SETTINGS_AUTO_OFF = Msg("set.text.reports_auto_off",
                        "Automatic offers: off (you said no to two offers in a row).",
                        note="/settings > Problem reports, when offers stopped by themselves")
SETTINGS_ENV_OFF = Msg("set.text.reports_env_off",
                       "Offers are also off on this machine through AIPAGER_REPORT_PROMPTS=0.",
                       note="/settings > Problem reports: an environment variable turned "
                            "offers off; keep AIPAGER_REPORT_PROMPTS=0 as it is")
SETTINGS_ROW_LABELS = {
    "ask": Msg("set.opt.reports.ask", "Ask me",
               note="Problem reports option: aipager may offer to send a report"),
    "off": Msg("set.opt.reports.off", "Off", note="Problem reports option: never offer"),
    "auto_off": Msg("set.opt.reports.auto_off", "offers off",
                    note="Problem reports value: offers stopped after two refusals"),
}
TURN_ON_BUTTON = Msg("set.btn.reports_turn_on", "Turn offers back on",
                     note="Problem reports button: offer reports again")

#: Every status line a preview card can carry: the inline form keeps room
#: for the longest, so a status never pushes a card over Telegram's limit.
_STATUS_MSGS = (
    SENDING_TEXT, NOTE_ADDED, NOTE_CUT, NOTE_EMPTY, NOTE_REFUSED, NOTE_CLOSED,
    SEND_FAILED_TEXT,
)
_STATUS_LINES = (*_STATUS_MSGS, *wording.OUTCOME_LINES.values())
_STATUS_RESERVE = max(len(line) for line in _STATUS_LINES) + 80

_OFFER_ANSWERS = {"op": "send", "on": "not_now", "od": "dont_ask"}

_INSTANT = rate_limit_args(priority=PRIORITY_INSTANT)


def _mono() -> float:
    """The one monotonic clock this module reads (kept cards, the note
    capture): a seam, so a test can move it without touching ``time``."""
    return time.monotonic()


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
    created: float = 0.0          # _mono()
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
    now = _mono()
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


def _en():
    return i18n.get("en")


def _card_tr(kept: KeptReport):
    """A card's language: its chat's (the owner's private chat)."""
    return i18n.for_chat(kept.chat_id)


def shown(tr, line) -> str:
    """A status or final *line* (one of this module's constants) in *tr*'s
    language, plain text: the Msgs through their keys, the two English
    lines that hold the note's limit through their Plurals."""
    from aipager.report import schema as sc
    if line == NOTE_CUT:
        return tr.n(_NOTE_CUT, sc.NOTE_MAX, kind="text")
    if line == NOTE_PROMPT:
        return tr.n(_NOTE_PROMPT, sc.NOTE_MAX, kind="text")
    if isinstance(line, Msg):
        return tr.text(line)
    return line


def _explanation_html(tr=None) -> str:
    return _esc((tr or _en()).text(EXPLANATION))


def _visible_heading(tr) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", tr.html(HEADING_HTML)))


def _status_reserve(tr=None) -> int:
    """Room for the longest status or outcome line in *tr*'s language (the
    outcome's reference as long as the English reserve counts it), plus
    80: in English exactly :data:`_STATUS_RESERVE`."""
    tr = tr or _en()
    ref = "x" * len("{reference}")
    lines = [shown(tr, line) for line in _STATUS_MSGS]
    lines += [outcome_line(o, ref, tr=tr) for o in wording.OUTCOME_LINES]
    return max(len(line) for line in lines) + 80


def _fits_inline(preview: bytes, tr=None) -> bool:
    """Whether the card with the whole report (and the longest status
    line) stays inside one Telegram message, counted as Telegram counts
    it: UTF-16 units of the text after the HTML is parsed. Measured in
    *tr*'s language (the card's; English when None)."""
    tr = tr or _en()
    visible = "\n\n".join([_visible_heading(tr), tr.text(EXPLANATION),
                           preview.decode("utf-8")])
    return _utf16_len(visible) + _status_reserve(tr) <= TELEGRAM_MAX_TEXT_LEN


def card_text(kept: KeptReport, status: str | None = None, *, tr=None) -> str:
    """The card's HTML: heading, explanation, an optional status line (as
    shown, already in the card's language), then the exact report
    (inline) or the line naming its file. In *tr*'s language (English
    when None); the report itself is the fixed English JSON."""
    tr = tr or _en()
    parts = [tr.html(HEADING_HTML), _explanation_html(tr)]
    if status:
        parts.append(f"<b>{_esc(status)}</b>")
    if kept.mode == "inline":
        body = _esc(kept.preview.decode("utf-8"))
        parts.append(f"<blockquote expandable><pre>{body}</pre></blockquote>")
    else:
        parts.append(_esc(tr.text(DOCUMENT_LINE)))
    return "\n\n".join(parts)


def _capture_text(tr=None) -> str:
    tr = tr or _en()
    return f"{tr.html(HEADING_HTML)}\n\n{_esc(shown(tr, NOTE_PROMPT))}"


def _final_text(line: str, tr=None) -> str:
    """The card's last word: *line* as shown (already in *tr*'s language)."""
    return f"{(tr or _en()).html(HEADING_HTML)}\n\n{_esc(line)}"


def preview_keyboard(tr=None) -> InlineKeyboardMarkup:
    tr = tr or _en()
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(tr.text(SEND_BUTTON), callback_data="_:rp:send"),
         InlineKeyboardButton(tr.text(NOTE_BUTTON), callback_data="_:rp:note")],
        [InlineKeyboardButton(tr.text(CANCEL_BUTTON), callback_data="_:rp:cancel")],
    ])


def _capture_keyboard(tr=None) -> InlineKeyboardMarkup:
    tr = tr or _en()
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(tr.text(BACK_BUTTON_TEXT), callback_data="_:rp:back"),
        InlineKeyboardButton(tr.text(CANCEL_BUTTON), callback_data="_:rp:cancel"),
    ]])


def report_button_markup(label: str = REPORT_BUTTON, *, tr=None) -> InlineKeyboardMarkup:
    """The one-button keyboard that opens a manual preview (/help, the
    memory-cap notice). A keyed *label* is drawn in *tr*'s language
    (English when None); any other label as given."""
    if isinstance(label, Msg):
        label = (tr or _en()).text(label)
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
    tr = _card_tr(kept)
    if kept.mode == "inline":
        try:
            sent = await send_text(app.bot, kept.chat_id, card_text(kept, tr=tr),
                                   parse_mode="HTML", reply_markup=preview_keyboard(tr),
                                   rate_limit_args=_INSTANT)
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
        sent = await send_text(app.bot, kept.chat_id, card_text(kept, tr=tr),
                               parse_mode="HTML", reply_markup=preview_keyboard(tr),
                               rate_limit_args=_INSTANT)
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
        await _edit_card(bot, kept, _final_text(tr.text(DOC_FAILED_TEXT), tr), None)
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


async def build_checked(bot: "TelegramBot", trigger: str,
                        errors: list[dict] | None = None) -> tuple[dict, bytes] | None:
    """The one report build behind the preview card and the Mini App's
    report page: the daemon's facts, the report built in a worker thread,
    and its exact preview bytes (``send.checked_preview``). None when the
    build fails or the report does not pass its own checks."""
    from aipager.report import send

    ctx = report_context(bot)
    try:
        report = await asyncio.to_thread(_build, trigger, errors, ctx)
    except Exception as e:  # noqa: BLE001
        log.warning("problem report could not be built (%s)", type(e).__name__)
        return None
    preview = send.checked_preview(report)
    if preview is None:
        log.warning("problem report did not pass its own checks: no preview")
        return None
    return report, preview


async def open_preview(bot: "TelegramBot", *, trigger: str,
                       errors: list[dict] | None = None) -> OpenResult:
    """Build a report and post its preview card in the owner's private
    chat. *errors* None means the store's most recent ones (a manual
    report); the automatic offer passes the offered entries."""
    owner = resolve_owner(bot)
    if owner is None:
        return OpenResult.NO_OWNER
    built = await build_checked(bot, trigger, errors)
    if built is None:
        return OpenResult.NOT_SENT
    report, preview = built
    # Measured in the language the card is drawn in (ruling 7): a longer
    # translation goes out as report.json where the English would fit.
    kept = KeptReport(report=report, preview=preview, chat_id=owner,
                      mode="inline" if _fits_inline(preview, i18n.for_chat(owner))
                      else "document",
                      created=_mono())
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
    from aipager.bot import kbwords
    return kbwords.classify(bot, text) is not None


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
    if _mono() - pending["last_active"] > NOTE_TTL:
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
        tr = _card_tr(kept)
        _spawn(_edit_card(bot, kept, card_text(kept, tr.text(NOTE_CLOSED), tr=tr),
                          preview_keyboard(tr)))
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
    tr = i18n.for_chat(pending["chat_id"])
    kept = _lookup(bot, pending["chat_id"], pending["msg_id"])
    if kept is None or kept.state != "note" or kept.report is None:
        try:
            await send_text(bot._app.bot, pending["chat_id"], tr.text(LOST_TEXT),
                            rate_limit_args=_INSTANT)
        except Exception as e:  # noqa: BLE001
            log.debug("report lost-card reply failed (%s)", type(e).__name__)
        return True
    kept.state = "open"
    note = sc.normalize_note(text)
    if note is None:
        await _edit_card(bot, kept, card_text(kept, tr.text(NOTE_EMPTY), tr=tr),
                         preview_keyboard(tr))
        return True
    candidate = copy.deepcopy(kept.report)
    candidate["note"] = note
    clean, _problems = sc.validate(candidate)
    preview = send.checked_preview(clean) if clean == candidate else None
    if preview is None:
        await _edit_card(bot, kept, card_text(kept, tr.text(NOTE_REFUSED), tr=tr),
                         preview_keyboard(tr))
        return True
    kept.report, kept.preview = clean, preview
    status = shown(tr, NOTE_CUT if len(sc.clean_note(text).strip()) > sc.NOTE_MAX
                   else NOTE_ADDED)
    old_doc = None
    if kept.mode == "inline" and not _fits_inline(preview, tr):
        kept.mode = "document"
    if kept.mode == "document":
        old_doc = kept.doc_msg_id
        kept.doc_msg_id = await _send_document(bot, kept)
        if kept.doc_msg_id is None:
            kept.state = "done"
            kept.report = None
            await _edit_card(bot, kept, _final_text(tr.text(DOC_FAILED_TEXT), tr), None)
            return True
    await _edit_card(bot, kept, card_text(kept, status, tr=tr), preview_keyboard(tr))
    if old_doc is not None:
        await _delete_message(bot, kept.chat_id, old_doc)
    return True


# ---- taps ---------------------------------------------------------------------

def outcome_line(outcome: str, reference: str | None = None, *, tr=None) -> str:
    """The card's line for one send outcome (the CLI's wording); an
    unknown one reads as try later. In English it is the CLI's own table,
    byte for byte; in any other language of *tr* the bot's keyed copy
    (whose English a test keeps equal to the table)."""
    if tr is None or tr.code == "en":
        return wording.OUTCOME_LINES.get(outcome, wording.TRY_LATER).format(
            reference=reference)
    msg = OUTCOME_MSGS.get(outcome, _TRY_LATER)
    if isinstance(msg, Plural):
        from aipager.report import send
        return tr.n(msg, send.SENDS_PER_DAY, kind="text")
    if msg.key == "rep.outcome.sent":
        return tr.text(msg, reference=str(reference))
    if msg.key == "rep.outcome.too_old":
        return tr.text(msg, command="`aipager update`")
    return tr.text(msg)


def _pop(query, pop):
    """The pop-up language: *pop* when the dispatcher passed it, else the
    tapper's."""
    return pop if pop is not None else tapper_tr(query)


def _query_chat_tr(query):
    return i18n.for_chat(_message_chat_id(getattr(query, "message", None)))


async def _tap_open(bot: "TelegramBot", query, chat_id, *, pop=None) -> None:
    pop = _pop(query, pop)
    owner = resolve_owner(bot)
    result = await open_preview(bot, trigger="manual")
    if result is OpenResult.OPENED:
        if chat_id != owner:
            await bot._safe_answer(query, pop.text(IN_DM_TEXT), show_alert=True)
    elif result is OpenResult.NO_OWNER:
        await bot._safe_answer(query, pop.text(NO_OWNER_TEXT), show_alert=True)
    else:
        await bot._safe_answer(query, pop.text(NOT_OPENED_TEXT), show_alert=True)


async def _lost(query) -> None:
    tr = _query_chat_tr(query)
    try:
        await edit_text(query, _final_text(tr.text(LOST_TEXT), tr), parse_mode="HTML",
                        reply_markup=None, rate_limit_args=_INSTANT)
    except Exception as e:  # noqa: BLE001
        log.debug("report lost-card edit failed (%s)", type(e).__name__)


def _drop_note_for(bot: "TelegramBot", kept: KeptReport) -> None:
    pending = _note_pending(bot)
    if pending and (pending["chat_id"], pending["msg_id"]) == (kept.chat_id, kept.msg_id):
        bot._report_note_pending = None


async def _tap_send(bot: "TelegramBot", query, kept: KeptReport, *, pop=None) -> None:
    pop = _pop(query, pop)
    if kept.state == "sending":
        await bot._safe_answer(query, pop.text(ALREADY_SENDING))
        return
    if kept.state == "done" or kept.report is None:
        await bot._safe_answer(query, pop.text(ALREADY_SENT))
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

    tr = _card_tr(kept)
    await _edit_card(bot, kept, card_text(kept, tr.text(SENDING_TEXT), tr=tr), None)
    try:
        result = await asyncio.to_thread(send.send, kept.report, transport=SEND_TRANSPORT)
    except Exception as e:  # noqa: BLE001 - send.send never raises; belt and braces
        log.warning("problem report send failed (%s)", type(e).__name__)
        result = None
    if result is None:
        # Something inside aipager broke: the same report would break the
        # same way, so this is final (no Send button to tap again).
        line = tr.text(SEND_FAILED_TEXT)
    else:
        line = outcome_line(result.outcome, result.reference, tr=tr)
        log.info("problem report send: %s", result.outcome)
        if result.outcome in wording.RETRYABLE:
            kept.state = "open"
            await _edit_card(bot, kept, card_text(kept, line, tr=tr), preview_keyboard(tr))
            return
    # Final: the report is let go; the entry stays so a late second tap
    # says "Already sent." instead of overwriting the outcome.
    kept.state = "done"
    kept.report = None
    await _edit_card(bot, kept, card_text(kept, line, tr=tr), None)


async def _tap_note(bot: "TelegramBot", query, kept: KeptReport, user_id: int,
                    *, pop=None) -> None:
    from aipager.bot import new_flow, session_parity

    if kept.state in ("sending", "done") or kept.report is None:
        pop = _pop(query, pop)
        await bot._safe_answer(query, pop.text(ALREADY_SENDING if kept.state == "sending"
                                               else ALREADY_SENT))
        return
    tr = _card_tr(kept)
    if not await _edit_card(bot, kept, _capture_text(tr), _capture_keyboard(tr)):
        return  # never shown: nothing may take the next message as the note
    kept.state = "note"
    # Their newest question wins: their Name card and rename question close.
    new_flow.close_open_card(bot, kept.chat_id, user_id)
    session_parity.close_rename(bot, kept.chat_id, user_id)
    previous = _note_pending(bot)
    if previous and (previous["chat_id"], previous["msg_id"]) != (kept.chat_id, kept.msg_id):
        close_note(bot)
    bot._report_note_pending = {"chat_id": kept.chat_id, "user_id": user_id,
                                "msg_id": kept.msg_id, "last_active": _mono()}


async def _tap_back(bot: "TelegramBot", query, kept: KeptReport, *, pop=None) -> None:
    _drop_note_for(bot, kept)
    if kept.state == "note":
        kept.state = "open"
    if kept.state != "open":
        pop = _pop(query, pop)
        await bot._safe_answer(query, pop.text(ALREADY_SENDING if kept.state == "sending"
                                               else ALREADY_SENT))
        return
    tr = _card_tr(kept)
    await _edit_card(bot, kept, card_text(kept, tr=tr), preview_keyboard(tr))


async def _tap_cancel(bot: "TelegramBot", query, kept: KeptReport | None,
                      *, pop=None) -> None:
    if kept is not None:
        if kept.state == "sending":
            await bot._safe_answer(query, _pop(query, pop).text(ALREADY_SENDING))
            return
        _drop_note_for(bot, kept)
        _cards(bot).pop((kept.chat_id, kept.msg_id), None)
    tr = _query_chat_tr(query)
    try:
        await edit_text(query, _final_text(tr.text(CANCELLED_TEXT), tr), parse_mode="HTML",
                        reply_markup=None, rate_limit_args=_INSTANT)
    except Exception as e:  # noqa: BLE001
        log.debug("report cancel edit failed (%s)", type(e).__name__)


# ---- the automatic offer's answers ----------------------------------------------

async def _strip_keyboard(query) -> None:
    try:
        await edit_markup(query, reply_markup=None, rate_limit_args=_INSTANT)
    except Exception as e:  # noqa: BLE001
        log.debug("offer keyboard strip failed (%s)", type(e).__name__)


async def _answer_offer(bot: "TelegramBot", query, verb: str, ts_text: str,
                        *, pop=None) -> None:
    from aipager.report import policy, store

    pop = _pop(query, pop)
    # The offer lives in the owner's private chat: its edits read that
    # chat's language, the pop-ups the tapper's.
    tr = _query_chat_tr(query)
    now = int(time.time())
    state = store.policy_state()
    settled = policy.settle(state, now)
    if settled != state:
        store.save_policy(settled)
    offer_ts = (int(ts_text) if ts_text.isascii() and ts_text.isdigit() and len(ts_text) <= 12
                else None)
    if offer_ts is None or not settled.pending or settled.last_offer_ts != offer_ts:
        await bot._safe_answer(query, pop.text(OFFER_STALE_TEXT))
        await _strip_keyboard(query)
        return
    offered = settled.pending
    answered = policy.note_answer(settled, _OFFER_ANSWERS[verb])
    store.save_policy(answered)
    if verb == "op":
        entries = {r["entry"]["fingerprint"]: r["entry"] for r in store.records()}
        errors = [entries[fp] for fp in offered if fp in entries]
        result = await open_preview(bot, trigger="auto", errors=errors)
        opened = result is OpenResult.OPENED
        if not opened:
            await bot._safe_answer(query, pop.text(NOT_OPENED_TEXT), show_alert=True)
        # Said only once the outcome is known.
        try:
            await edit_text(query, tr.text(OFFER_PREVIEW_TEXT if opened
                                           else OFFER_NOT_OPENED_TEXT),
                            reply_markup=None, rate_limit_args=_INSTANT)
        except Exception as e:  # noqa: BLE001
            log.debug("offer notice edit failed (%s)", type(e).__name__)
        return
    # Two whole sentences, side by side.
    text = tr.text(OFFER_DECLINED_TEXT)
    if answered.auto_off and not settled.auto_off:
        text += " " + tr.text(OFFER_OFF_TEXT)
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


def render_settings_section(tr=None) -> tuple[str, InlineKeyboardMarkup]:
    """The Problem reports page of /settings, in *tr*'s language (the
    owner's private chat; English when None)."""
    from aipager import i18n, preferences
    from aipager.bot import language_menu
    from aipager.report import store

    tr = tr or i18n.get("en")
    current = preferences.get_problem_reports()
    auto_off = store.policy_state().auto_off
    lines = [tr.html(SETTINGS_TITLE), tr.html(SETTINGS_INTRO)]
    if auto_off:
        lines.append(tr.html(SETTINGS_AUTO_OFF))
    if _env_off():
        lines.append(tr.html(SETTINGS_ENV_OFF))
    def _label(value: str) -> str:
        return tr.text(SETTINGS_ROW_LABELS[value]) + (" ✅" if value == current else "")

    rows = [[InlineKeyboardButton(_label("ask"), callback_data="_:rp:set:ask")],
            [InlineKeyboardButton(_label("off"), callback_data="_:rp:set:off")]]
    if auto_off:
        rows.append([InlineKeyboardButton(tr.text(TURN_ON_BUTTON),
                                          callback_data="_:rp:set:on")])
    rows.append([InlineKeyboardButton(tr.text(language_menu.BACK),
                                      callback_data="_:set:back")])
    return "\n\n".join(lines), InlineKeyboardMarkup(rows)


async def _tap_settings(bot: "TelegramBot", query, parts: list[str], *, pop=None) -> None:
    from aipager import preferences
    from aipager.bot.language_menu import INVALID_CALLBACK
    from aipager.report import policy, store

    if len(parts) == 2:
        choice = parts[1]
        if choice in ("ask", "off"):
            preferences.set_problem_reports(choice)
        elif choice == "on":
            store.save_policy(policy.reenable(store.policy_state()))
        else:
            await bot._safe_answer(query, _pop(query, pop).text(INVALID_CALLBACK))
            return
    text, kb = render_settings_section(
        i18n.for_chat(_message_chat_id(getattr(query, "message", None))))
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
    from aipager.bot.language_menu import INVALID_CALLBACK
    # Every pop-up below is the tapper's; the card's edits its chat's.
    pop = tapper_tr(query)
    parts = action.split(":")[1:]
    verb = parts[0] if parts else ""
    user_id = getattr(getattr(query, "from_user", None), "id", None)
    message = getattr(query, "message", None)
    chat_id = _message_chat_id(message)
    if chat_id is None:
        chat_id = calling_chat_id(update)
    owner = resolve_owner(bot)
    if owner is None:
        await bot._safe_answer(query, pop.text(NO_OWNER_TEXT), show_alert=True)
        return True
    if not is_owner(bot, user_id):
        log.info("report tap refused: not the owner (action %s)", verb)
        await bot._safe_answer(query, pop.text(NOT_OWNER_TEXT), show_alert=True)
        return True
    if verb == "open" and len(parts) == 1:
        await _tap_open(bot, query, chat_id, pop=pop)
        return True
    if chat_id != owner:
        # Cards, offers and the settings page live in the owner's DM only.
        await bot._safe_answer(query, pop.text(NOT_OWNER_TEXT), show_alert=True)
        return True
    if verb in _OFFER_ANSWERS and len(parts) == 2:
        await _answer_offer(bot, query, verb, parts[1], pop=pop)
        return True
    if verb == "set" and len(parts) <= 2:
        await _tap_settings(bot, query, parts, pop=pop)
        return True
    if verb not in ("send", "note", "back", "cancel") or len(parts) != 1:
        await bot._safe_answer(query, pop.text(INVALID_CALLBACK))
        return True
    kept = _lookup(bot, chat_id, getattr(message, "message_id", None))
    if verb == "cancel":
        await _tap_cancel(bot, query, kept, pop=pop)
        return True
    if kept is None:
        await _lost(query)
        return True
    if verb == "send":
        await _tap_send(bot, query, kept, pop=pop)
    elif verb == "note":
        await _tap_note(bot, query, kept, user_id, pop=pop)
    else:
        await _tap_back(bot, query, kept, pop=pop)
    return True
