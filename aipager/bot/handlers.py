"""Telegram bot — python-telegram-bot v22 async Application.

Single owner of all Telegram communication. Handles:
- CallbackQuery (button taps) → dtach_inject.send_keys()
- Message replies → dtach_inject.send_text_and_enter()
- /status command → show all sessions
- /<label> <prompt> → direct send to session
"""

from __future__ import annotations

import asyncio
import inspect
import html as html_mod
import logging
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
    WebAppInfo,
)
from telegram.error import BadRequest, NetworkError
from telegram.ext import (
    ContextTypes,
)

from aipager.bot.mode_labels import mode_label
from aipager.dtach import inject

from aipager import statusline_file
from aipager import i18n, perm_modes
from aipager.i18n import Command, Msg, Plural, Safe, Span
from aipager.bot import (
    card_owner,
    group_intake,
    held_message,
    kbwords,
    language_menu,
    miniapp_link,
    mode_labels,
    mode_switch,
    new_flow,
    reactions,
    report_flow,
    session_parity,
)
from aipager.bot import session_ops as session_ops_mod
from aipager.bot.session_ops import (
    BG_BUSY_QUESTION,
    BG_HELD_NOTICE,
    BG_REFUSED_PARKED,
    bg_outcome_html,
    MODEL_DEFAULT_SAVED,
    MODEL_UNCONFIRMED,
    await_model_change,
    clear_model_switch_pending,
    mark_model_switch_sent,
    model_refusal_text,
    model_switch_refusal,
    settle_default,
)
from aipager.bot import callbacks as callbacks_mod
from aipager.bot.mode_switch import (
    MODE_BTN_TO_ASK,
    MODE_BTN_TO_AUTO,
    MODE_BTN_TO_FULL,
    TOAST_MODE_DIALOG_OPEN,
    TOAST_NEEDS_ADMIN_FULL,
)
from aipager.bot.settings_menu import render_settings_root
from aipager.config import (
    FILE_DOWNLOAD_DIR, KEYBOARD_PARENTS,
)
from aipager import uploads
from aipager.state import QUEUE_CAP, Status, TrackedSession

# Pure-function helpers and constants live in aipager.bot.transport
# now. Re-export the names this module uses internally so the
# TelegramBot class body below (and any external consumers like the
# tests) keeps working without changes.
from aipager.bot.transport import (  # noqa: F401
    PROMPT_REFUSED,
    MUTED,
    reply_text,
    edit_message,
    edit_text,
    edit_text_at,
    send_text,
    ACTION_VERBS,
    TELEGRAM_BOT_DOWNLOAD_LIMIT_BYTES,
    TELEGRAM_MAX_DOC_BYTES,
    TELEGRAM_MAX_TEXT_LEN,
    TruncationFailed,
    _build_diff_block,
    _detect_api_error,
    _DIFF_MAX_CHARS,
    _DIFF_MAX_LINES,
    _ERROR_PATTERNS,
    _extract_retry_after,
    _is_bot_blocked,
    _log_blocked_once,
    _MAX_TRUNCATIONS,
    _md_safe_boundaries,
    _PERSONAL_MODE_SENTINEL,
    _RETRY_AFTER_RE,
    _safe_truncate,
    _send_with_retry,
    _TRUNC_SUFFIX,
    _truncate_diff,
    calling_chat_id,
    calling_user_id,
    driver_id_from_update,
    mixed_sender_note_outstanding,
)

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)

# Seam: tests replace THIS, never ``asyncio.create_subprocess_exec``
# (``aipager.bot.handlers.asyncio`` IS the global module; CLAUDE.md).
_create_subprocess_exec = asyncio.create_subprocess_exec

#: What :meth:`_hold_for_open_dialog` returns when it had to hold a message
#: but the queue was full, so it was dropped (and the sender told). Truthy,
#: like "held", for every caller that only asks "stop here?"; the
#: "Which session?" resend (roadmap 8.94i) tells the two apart.
HOLD_DROPPED = object()

# /help (4.3): what you can do, grouped by task, in one screen. One key
# per line (multilingual design 5.4).
HELP_TITLE = Msg("help.title", "<b>How to use aipager</b>", note="/help's title")
HELP_START = Msg("help.start", "<b>Start:</b> /new name · /resume",
                 note="/help: how to start a session (name is what you type after /new) "
                      "or resume one")
#: In a private chat: a plain message reaches the bot.
HELP_TALK_DM = Msg("help.talk_dm",
                   "<b>Talk:</b> just type (it goes to the ✍️ session) · reply to a message"
                   " · /&lt;label&gt; message",
                   note="/help in a private chat: three ways to send to a session; "
                        "/&lt;label&gt; message is typed as a session's name after /")
#: In a group a plain message does not reach the bot: reply or mention.
#: That line is ``group_intake.group_help_talk_line``'s (one key each).
HELP_CONTROL = Msg("help.control", "<b>Control:</b> /stop · /now (send queued) · /clearqueue",
                   note="/help: stop a turn, send what is queued now, clear the queue")
HELP_MANAGE = Msg("help.manage",
                  "<b>Manage:</b> /status, then ⋮ (restart, rename, diff, end, delete)",
                  note="/help: the session menu ⋮ on /status and what it does")
HELP_SETTINGS = Msg("help.settings", "<b>Settings:</b> /settings · /mode (Ask, Auto or Full access)",
                    note="/help: settings and the permission mode; Auto and Ask are the "
                         "mode names")
HELP_ADMIN = Msg("help.admin", "<b>Admin:</b> /update (aipager and Claude Code)",
                 note="/help: what /update updates")
HELP_TIP = Msg("help.tip",
               "<i>Tip: tapping a command sends it at once. Long-press it (phone) or "
               "press Tab (desktop) to add text first.</i>",
               note="/help: a tapped /command is sent at once; how to add text to it first")


def help_text(tr, *, talk_line: str | None = None) -> str:
    """/help in *tr*'s language; *talk_line* (HTML) is the Talk line, the
    private chat's by default."""
    talk = talk_line if talk_line is not None else tr.html(HELP_TALK_DM)
    lines = [tr.html(m) for m in (HELP_CONTROL, HELP_MANAGE, HELP_SETTINGS, HELP_ADMIN)]
    return (f"{tr.html(HELP_TITLE)}\n\n{tr.html(HELP_START)}\n{talk}\n"
            + "\n".join(lines) + f"\n\n{tr.html(HELP_TIP)}")


#: /help in English, as a private chat reads it.
HELP_TEXT = help_text(i18n.get("en"))

# The home screen (/start, or a message that only mentions the bot).
HOME_TITLE = Msg("home.title", "👋 <b>aipager</b> - Claude Code from Telegram",
                 note="The home screen's first line")
HOME_DM_TARGET = Msg("home.dm_target", "✍️ Messages go to <b>{session}</b>.",
                     note="Home screen: where a plain message goes", eg={"session": "x1"})
HOME_TAP_SESSION = Msg("home.tap_session", "Tap a session on the keyboard to talk to it.",
                       note="Home screen: sessions are listed, none is the target yet")
HOME_START_ONE = Msg("home.start_one", "Start one with /new.",
                     note="Home screen: no session yet")
HOME_HELP_HINT = Msg("home.help_hint", "<i>/help shows everything you can do.</i>",
                     note="Home screen's last line")
HOME_BTN_NEW = Msg("home.btn.new", "🆕 New session", note="Home screen button: /new")
HOME_BTN_RESUME = Msg("home.btn.resume", "↩️ Resume",
                      note="Home screen button: the list of ended sessions to resume")
HOME_BTN_SETTINGS = Msg("home.btn.settings", "⚙️ Settings", note="Home screen button")

MODEL_SWITCH_OTHER_SENDER_REASON = Msg(
    "route.model_other_sender",
    "another member's message is still waiting to be picked up - switch the model after it.",
    note="Why /model was refused: someone else's message to the session is not taken yet")

# /new for a name in use: the conflict card (HTML; the chat's).
CONFLICT_RUNNING = Msg("newflow.conflict_running",
                       "⚠️ <b>{session}</b> is already running.\nWhat would you like to do?",
                       note="/new for a session that runs", eg={"session": "x1"})
CONFLICT_USED = Msg("newflow.conflict_used",
                    "♻️ <b>{session}</b> was previously used.\nWhat would you like to do?",
                    note="/new for a name an ended session had", eg={"session": "x1"})
CONFLICT_USED_PREVIEW = Msg("newflow.conflict_used_preview",
                            "♻️ <b>{session}</b> was previously used.\n<i>Last response:</i>\n"
                            "<blockquote>{preview}</blockquote>\nWhat would you like to do?",
                            note="As newflow.conflict_used, with the start of its last answer",
                            eg={"session": "x1", "preview": "All tests pass."})
CONFLICT_BTN_SWITCH = Msg("newflow.btn.switch_to", "↩️ Switch to it",
                          note="Button: talk to the running session instead")
CONFLICT_BTN_RESUME = Msg("newflow.btn.resume", "♻️ Resume",
                          note="Button: resume the ended session")
CONFLICT_BTN_REPLACE = Msg("newflow.btn.replace", "🆕 Replace (fresh)",
                           note="Button: end it and start a fresh session with the name")
CONFLICT_REPLACED = Msg("newflow.conflict_replaced",
                        "↩️ A newer /new for <b>{session}</b> replaced this card.",
                        note="An older conflict card for the same name", eg={"session": "x1"})

# Voice notes and files (plain text unless HTML is said).
MEDIA_BTN_INSTALL = Msg("media.btn.install", "📦 Install voice",
                        note="Button: install the optional voice extra")
MEDIA_BTN_CANCEL = Msg("media.btn.cancel", "Cancel", note="Button: do not install it")
MEDIA_NEEDS_EXTRA = Msg("media.needs_extra",
                        "⚠️ Voice messages need the optional voice extra (~{install} MB install "
                        "· ~{model} MB model on first use).",
                        note="A voice note while the voice extra is not installed; sizes in MB",
                        eg={"install": 200, "model": 74})
MEDIA_VOICE_TOO_BIG = Msg("media.voice_too_big",
                          "⚠️ Voice message is {size} MB; Telegram bots are capped at {limit} "
                          "MB.", note="A voice note too big for a bot to download",
                          eg={"size": "31.0", "limit": 20})
MEDIA_DOWNLOAD_FAILED = Msg("media.download_failed", "❌ Failed to download voice message.",
                            note="A voice note could not be downloaded")
MEDIA_TRANSCRIBING = Msg("media.transcribing", "🎙️ <i>Transcribing…</i>",
                         note="HTML. A voice note is being turned into text")
MEDIA_TRANSCRIBE_FAILED = Msg("media.transcribe_failed", "❌ Transcription failed: {error}",
                              note="Turning a voice note into text failed; {error} is why "
                                   "(English)", eg={"error": "out of memory"})
MEDIA_NO_SPEECH = Msg("media.no_speech", "⚠️ Couldn't make out any speech in that.",
                      note="A voice note with no words heard")
MEDIA_HEARD = Msg("media.heard", "🎙️ <i>Heard:</i> {text}",
                  note="HTML. What the voice note said, before it is sent to the session",
                  eg={"text": "fix the failing tests"})
MEDIA_INSTALL_NO_RECIPE = Msg("media.install_no_recipe",
                              "⚠️ This aipager build has no installer recipe for the\nvoice "
                              "extra. Update aipager and try again.",
                              note="This build cannot install the voice extra itself")
MEDIA_INSTALLING = Msg("media.installing", "📦 Installing voice extra…",
                       note="The voice extra is being installed")
MEDIA_INSTALL_CANT_START = Msg("media.install_cant_start", "❌ Couldn't start installer: {error}",
                               note="The installer did not start; {error} is why (English)",
                               eg={"error": "No such file"})
MEDIA_INSTALL_WORKING = Msg("media.install_working",
                            "📦 Installing voice extra - still working… ({seconds}s)",
                            note="Every few seconds while the install runs; {seconds} so far",
                            eg={"seconds": 35})
MEDIA_BREW_FOOTER = Msg("media.brew_footer",
                        "<i>Note: a future {command} may rebuild the formula venv and drop "
                        "faster-whisper. If voice stops working, tap Install again.</i>",
                        note="HTML. Under the Installed line when Homebrew installed aipager; "
                             "{command} is the brew command", eg={"command": "`brew upgrade "
                                                                             "aipager`"})
MEDIA_INSTALLED = Msg("media.installed",
                      "✅ Installed. Voice features need a daemon restart to load the new "
                      "module.", note="HTML. The voice extra is installed")
MEDIA_BTN_RESTART = Msg("media.btn.restart", "🔄 Restart daemon now",
                        note="Button: restart aipager so voice works")
MEDIA_INSTALL_FAILED = Msg("media.install_failed",
                           "❌ Install failed (exit {code}):\n<pre>{output}</pre>",
                           note="HTML. The install failed; {code} is its exit code, {output} "
                                "the end of what it printed", eg={"code": 1, "output": "error"})
MEDIA_RESTART_BUSY_UPDATE = Msg("media.restart_busy_update",
                                "⏳ Not restarting: an aipager update is running or about to "
                                "restart the daemon. Try again once it has finished.",
                                note="The restart button while aipager updates itself")
MEDIA_RESTART_LOCKED = Msg("media.restart_locked",
                           "⏳ Not restarting: an aipager update is running (the update lock "
                           "is held). Try again once it has finished.",
                           note="The restart button while an update elsewhere holds the lock")
MEDIA_RESTART_KILLMODE = Msg("media.restart_killmode",
                             "⚠️ Not restarting: the service KillMode would stop your "
                             "sessions.",
                             note="The service would stop every session with it; KillMode is "
                                  "systemd's setting's name")
MEDIA_RESTART_REFUSED = Msg("media.restart_refused", "⚠️ Not restarting: {reason}.",
                            note="Why aipager will not restart itself; {reason} is English",
                            eg={"reason": "the service is not running"})
MEDIA_RESTART_WOULD_KILL = Msg("media.restart_would_kill",
                               "Sessions a restart would kill: {sessions}",
                               note="Under media.restart_killmode: the sessions at risk",
                               eg={"sessions": "x1, x2"})
MEDIA_RESTART_CANT_SCHEDULE = Msg("media.restart_cant_schedule",
                                  "⚠️ Couldn't schedule the restart: {detail}\nPlease restart "
                                  "manually.", note="{detail} is why (English)",
                                  eg={"detail": "systemd-run failed"})
MEDIA_RESTARTING_IN = Msg("media.restarting_in",
                          "🔄 Restarting in {seconds} s - send your voice message again in a "
                          "few seconds.", note="The restart is on its way", eg={"seconds": 5})
MEDIA_RESTART_LAUNCHCTL = Msg("media.restart_launchctl", "🔄 Restarting via {command}…",
                              note="macOS: restarting through its service manager; {command} "
                                   "is the command", eg={"command": "launchctl kickstart"})
MEDIA_RESTART_CANT_OPEN = Msg("media.restart_cant_open",
                              "⚠️ Couldn't open {path}: {error}\nPlease restart manually.",
                              note="The restart's log file could not be opened; {error} is why "
                                   "(English)", eg={"path": "~/.local/state/aipager/restart.log",
                                                    "error": "Permission denied"})
MEDIA_RESTART_CANT_SPAWN = Msg("media.restart_cant_spawn",
                               "⚠️ Couldn't spawn replacement: {error}\nPlease restart "
                               "manually.", note="A new aipager could not be started; {error} "
                                                 "is why (English)",
                               eg={"error": "Permission denied"})
MEDIA_RESTART_FAILED_START = Msg("media.restart_failed_start",
                                 "⚠️ Replacement daemon failed to start. Please restart "
                                 "manually.", note="The new aipager stopped at once")
MEDIA_RESTART_RESEND_VOICE = Msg("media.restart_resend_voice",
                                 "🔄 Restarting - send your voice message again in a few "
                                 "seconds.", note="aipager restarts itself now")
MEDIA_FILE_TOO_BIG = Msg("media.file_too_big",
                         "⚠️ File is {size} MB. The Telegram bot API caps file downloads at "
                         "{limit} MB. Try splitting it, or paste the content as text.",
                         note="A file too big for a bot to download",
                         eg={"size": "31.0", "limit": 20})
MEDIA_FILE_DOWNLOAD_FAILED = Msg("media.file_download_failed",
                                 "❌ Failed to download file: {name}",
                                 note="A file could not be downloaded; {name} is its name",
                                 eg={"name": "report.pdf"})
# No count is shown, so only one file or several matters: a key each.
MEDIA_ALBUM_FAILED_ONE = Msg("media.album_failed_one", "❌ Failed to download file: {names}",
                             note="The one file of an album that could not be downloaded",
                             eg={"names": "a.jpg"})
MEDIA_ALBUM_FAILED_MANY = Msg("media.album_failed_many",
                              "❌ Failed to download files: {names}",
                              note="Files (two or more) of an album that could not be "
                                   "downloaded; {names} lists them",
                              eg={"names": "a.jpg, b.jpg"})
MEDIA_ALBUM_LEFT_OUT_ONE = Msg("media.album_left_out_one",
                               "❌ Failed to download file: {names} (left out of the album)",
                               note="As media.album_failed_one, and the rest of the album was "
                                    "sent without it", eg={"names": "a.jpg"})
MEDIA_ALBUM_LEFT_OUT_MANY = Msg("media.album_left_out_many",
                                "❌ Failed to download files: {names} (left out of the album)",
                                note="As media.album_failed_many, and the rest of the album "
                                     "was sent without them", eg={"names": "a.jpg, b.jpg"})

# What a message, command or tap to a session gets back when it could not
# go, and where it went (plain text unless HTML is said).
SENT_TO = Msg("route.sent_to", "{glyph} {what} sent to {session}",
              note="After a keyboard template or command: what was sent and to which "
                   "session; {what} is the button's text or the /command",
              eg={"glyph": "🧹", "what": "/clear", "session": "x1"})
QUEUE_FULL = Plural("route.queue_full",
                    one="⚠️ Queue is full ({n} pending) for [{session}]. Answer the prompt or "
                        "clear the queue.",
                    other="⚠️ Queue is full ({n} pending) for [{session}]. Answer the prompt "
                          "or clear the queue.",
                    note="HTML. A message could not be held: the queue already holds {n}",
                    eg={"session": "x1"})
# The one notice a message held behind an open dialog gets (the first one
# per person per dialog), as a reply to the dialog's card when it can be.
HELD_QUESTION = Msg(
    "route.held_question",
    "❓ Claude is still waiting for your answer to this question. Answer it "
    "first, and your new message goes to Claude right after.",
    note="Reply to the open question's card when someone sends a message while "
         "Claude waits for the answer: the message is held, not sent, until the "
         "question is answered. Deleted once it is sent")
HELD_PERMISSION = Msg(
    "route.held_permission",
    "❓ Claude is still waiting for your answer to this permission request. "
    "Answer it first, and your new message goes to Claude right after.",
    note="As route.held_question, for an open permission request (Allow / Deny)")
HELD_TERMINAL = Msg(
    "route.held_terminal",
    "❓ Claude is waiting for an answer in the terminal. Answer it there or tap "
    "Stop, and your new message goes to Claude right after.",
    note="As route.held_question, for a question or permission request that can "
         "only be answered in the terminal (Claude Code's own screen on the "
         "computer); Stop is the busy card's Stop button")
NO_SESSION_HERE = Msg("route.no_session_here", "⚠️ No session named <b>{session}</b> here.",
                      note="HTML. A typed name matches no session in this chat",
                      eg={"session": "x1"})
NO_LIVE_HERE = Msg("route.no_live_here", "⚠️ No live session named <b>{session}</b> here.",
                   note="HTML. A typed name matches no running session in this chat",
                   eg={"session": "x1"})
NOT_WORKING = Msg("route.not_working", "<b>{session}</b> is not working.",
                  note="HTML. Stop asked of a session that is idle", eg={"session": "x1"})
NOTHING_RUNNING = Msg("route.nothing_running", "Nothing is running.",
                      note="/stop with no session working")
WHICH_STOP = Msg("route.which_stop", "Which one to stop?",
                 note="/stop with several sessions working: above the list")
LABEL_COMMAND = "/<label>"
CLEAR_NO_ACTIVE = Msg("route.clear_no_active",
                      "No active session - switch to one with {command} first.",
                      note="/clearqueue with no session; {command} is /<label>, a session's "
                           "name typed after /", eg={"command": "/<label>"})
CLEAR_NOTHING = Msg("route.clear_nothing", "Nothing to clear in [{session}].",
                    note="HTML. /clearqueue found nothing queued", eg={"session": "x1"})
CLEARED = Plural("route.cleared",
                 one="🗑️ Cleared {n} queued message for [<b>{session}</b>].",
                 other="🗑️ Cleared {n} queued messages for [<b>{session}</b>].",
                 note="HTML. /clearqueue dropped {n} waiting messages", eg={"session": "x1"})
KILL_NONE = Msg("route.kill_none", "No sessions to end.", note="/kill with no session")
KILL_WHICH = Msg("route.kill_which", "Which session to end?",
                 note="/kill with several sessions: above the list")
ENDED_WHICH = Msg("route.ended_which", "{session} has ended. Which session?",
                  note="In a group: the person's own session has ended; above the list of "
                       "others and its Resume", eg={"session": "x1"})
RESUME_THEN_RESEND = Msg("route.resume_then_resend",
                         "After ▶️ Resume, send your message again.",
                         note="Under route.ended_which: Resume does not send the message")
WHICH_REPLY = Msg("route.which_reply",
                  "Which session? Reply to one of its messages, or send {command} your "
                  "message.",
                  note="In a group, a member with no session of their own; {command} is a "
                       "session's /name", eg={"command": "/x1"})
BTN_RESUME_LABEL = Msg("route.btn.resume_label", "▶️ Resume {session}",
                       note="Button: resume the person's ended session", eg={"session": "x1"})
MENTION_ALONE = Msg("route.mention_alone",
                    "Send {command} on its own, without mentioning the bot.",
                    note="A command sent together with the bot's @mention in a group",
                    eg={"command": "/stop"})
NO_SESSION = Msg("route.no_session",
                 "⚠️ I don't know which session this is for. Pick one with {command} or the "
                 "keyboard.",
                 note="A message with no session to go to; {command} is /<label>",
                 eg={"command": "/<label>"})
FILE_NO_SESSION = Msg("route.file_no_session",
                      "⚠️ I don't know which session this file is for. Pick one with "
                      "{command} or the keyboard.",
                      note="A file with no session to go to; {command} is /<label>",
                      eg={"command": "/<label>"})
VOICE_NO_SESSION = Msg("route.voice_no_session",
                       "⚠️ Voice transcribed but no active session to send it to. Pick one "
                       "with {command} first.",
                       note="A voice note was heard but has no session to go to; {command} "
                            "is /<label>", eg={"command": "/<label>"})
SESSION_MISSING = Msg("route.session_missing", "⚠️ Session '{name}' not found",
                      note="The session's process is gone; {name} is its internal name",
                      eg={"name": "claude-x1"})
BTN_RESUME = Msg("route.btn.resume", "🔁 Resume", note="Button: resume the session")
SEND_FAILED = Msg("route.send_failed", "❌ Failed to send to [{session}]",
                  note="A message could not be typed into the session", eg={"session": "x1"})
INJECT_FAILED = Msg("route.inject_failed", "❌ Failed to inject transcript into [{session}]",
                    note="A voice note's text could not be typed into the session",
                    eg={"session": "x1"})
NO_ACTIVE = Msg("route.no_active", "⚠️ No active session",
                note="A keyboard template or command with no session to go to")
CANT_CLEAR_BUSY = Msg("route.cant_clear_busy", "⚠️ Can't clear while session is busy",
                      note="/clear refused while a turn runs")
REFUSAL_FRAME = Msg("route.refusal_frame", "⚠️ [{session}] {reason}",
                    note="A refusal for one session; {reason} is why",
                    eg={"session": "x1", "reason": "Session isn't running."})
MODEL_SWITCHED = Msg("route.model_switched", "🔄 <b>{session}</b> → {model}",
                     note="HTML. /model sent: the session and the model; RTL catalogs use ←",
                     eg={"session": "x1", "model": "opus"})
MODEL_NOW = Msg("route.model_now", "🧠 now {model}",
                note="HTML. Under the /model reply: the model the session now reports",
                eg={"model": "Opus 4.7"})
WARNING_LINE = Msg("route.warning", "⚠️ {text}",
                   note="A warning line; {text} is the warning",
                   eg={"text": "not confirmed - check the session."})
NOT_ALIVE = Msg("route.not_alive", "⚠️ [{session}] session not alive",
                note="A typed /name whose session's process is gone", eg={"session": "x1"})
UNKNOWN_SESSION = Msg("route.unknown_session", "⚠️ Unknown session: {session}",
                      note="A typed /name that is no session here", eg={"session": "x1"})

# Strong references to the /model feedback follow-ups, so a pending one
# is not garbage-collected mid-wait (asyncio keeps only weak ones).
_MODEL_CONFIRM_TASKS: set[asyncio.Task] = set()


# ---- File uploads: download policy ---------------------------------------
# A photo/document download is a getFile call plus a GET of the bytes; on
# a slow or lossy link either can time out. Bounded retries, on the
# transient network classes only (see _download_with_retry).
#: /app in a group (roadmap 8.91d): to someone whose own chat with the bot
#: is configured, and to anyone else (that DM would refuse them).
APP_IN_GROUP_TEXT = Msg("appcmd.in_group",
                        "📱 The Mini App only works in a private chat - DM the bot and send "
                        "/app there.",
                        note="/app in a group, to someone with their own chat with the bot")
APP_NO_DM_TEXT = Msg("appcmd.no_dm",
                     "The Mini App opens from your own chat with the bot. Ask the operator "
                     "to add you.",
                     note="/app in a group, to someone the bot does not talk to privately")
APP_NOT_ENABLED = Msg("appcmd.not_enabled",
                      "The Mini App server isn't enabled on this machine.\nAsk the operator to "
                      "run {command} and restart the daemon.",
                      note="HTML. /app while the Mini App is off; {command} is the command "
                           "that turns it on", eg={"command": "aipager miniapp enable"})
APP_CANT_START = Msg("appcmd.cant_start",
                     "📱 The Mini App can't start on this machine - its install looks "
                     "incomplete.\n\nEverything else keeps working; ask whoever set this up "
                     "to reinstall aipager.",
                     note="/app when the Mini App's parts are missing")
APP_SETTING_UP = Msg("appcmd.setting_up",
                     "📱 The Mini App link is still being set up - try /app again in a few "
                     "seconds.",
                     note="/app while its link is not ready yet")
APP_STRANGER = Msg("appcmd.stranger",
                   "🚫 This bot isn't configured to talk to you. Ask the operator to add your "
                   "Telegram user ID ({user_id}) via {command}.",
                   note="/app from someone not set up; {user_id} is their Telegram id, "
                        "{command} the setup command",
                   eg={"user_id": "123456789", "command": "`aipager config`"})
APP_BTN_OPEN = Msg("appcmd.btn.open", "📱 Open Mini App", note="Button that opens the Mini App")
APP_TAP_TO_OPEN = Msg("appcmd.tap_to_open", "Tap to open the aipager dashboard:",
                      note="Above the Open Mini App button")

# /mode and its card (4.4).
MODE_USAGE = Msg("modecard.usage",
                 "⚠️ /mode takes a session name and ask, auto or full, for example {example} "
                 "or {example_ask}.",
                 note="/mode typed with a word it does not take; ask, auto and full are typed "
                      "as they are; {example} and {example_ask} are commands to copy",
                 eg={"example": "/mode x1", "example_ask": "/mode x1 ask"})
MODE_WHICH = Msg("modecard.which", "Which session?", note="/mode: above the list of sessions")
MODE_WHICH_TO = Msg("modecard.which_to", "Which session to switch to {mode}?",
                    note="/mode ask, /mode auto or /mode full: above the list; {mode} is the "
                         "mode's name with its emoji", eg={"mode": "Auto mode"})
MODE_NONE_LIVE = Msg("modecard.none_live", "No live sessions here. Start one with /new.",
                     note="/mode with no session running")
MODE_ALREADY = Msg("modecard.already", "<b>{session}</b> is already in {mode}.",
                   note="HTML. /mode asked for the mode the session is in; {mode} is its name",
                   eg={"session": "x1", "mode": "Auto mode"})
MODE_IS = Msg("modecard.is", "<b>{session}</b> is in {mode}.",
              note="HTML. The mode card: the session's mode; {mode} is the mode's "
                   "label, which already says mode",
              eg={"session": "x1", "mode": "Auto mode"})
MODE_IS_TARGET = Msg("modecard.is_target", "✍️ <b>{session}</b> is in {mode}.",
                     note="HTML. The mode card for the session messages go to; {mode} is "
                          "the mode's label, which already says mode",
                     eg={"session": "x1", "mode": "Auto mode"})
NEEDS_ADMIN_FULL = Msg("modecard.needs_admin", "Full access mode needs an admin.",
                       note="Switching to Full access mode is for admins")
TOAST_PERMS_STARTING = Msg("toast.perms_starting",
                           "{session} is still starting - try again in a moment",
                           note="Pop-up: the session's state is not known yet",
                           eg={"session": "x1"})
MODE_INITIALIZING = Msg("modecard.initializing",
                        "⚠️ <b>{session}</b>'s status is still initializing. Try /mode again "
                        "in a moment.",
                        note="HTML. /mode while the session's state is not known yet",
                        eg={"session": "x1"})
MODE_BUSY_TO_ASK = Msg("modecard.busy_to_ask", "⚙️ <b>{session}</b> is busy.\nSwitch to Ask "
                       "mode?", note="HTML. Above Stop & switch / Not now", eg={"session": "x1"})
MODE_BUSY_TO_AUTO = Msg("modecard.busy_to_auto", "⚙️ <b>{session}</b> is busy.\nSwitch to "
                        "Auto mode?", note="HTML. Above Stop & switch / Not now",
                        eg={"session": "x1"})
MODE_BUSY_TO_FULL = Msg("modecard.busy_to_full", "⚙️ <b>{session}</b> is busy.\nSwitch to "
                        "Full access mode? The session restarts.",
                        note="HTML. Above Stop & switch / Not now: switching to Full access "
                             "mode restarts the session", eg={"session": "x1"})
MODE_CONFIRM_FULL = Msg("modecard.confirm_full",
                        "⚙️ Switch <b>{session}</b> to {mode}?\n<i>Everything runs with no "
                        "checks ({flag}). Switching restarts the session; the conversation "
                        "is kept.</i>",
                        note="HTML. The Full access confirm; {mode} is the Full access mode "
                             "label (it already says mode), {flag} Claude Code's own option "
                             "name",
                        eg={"session": "x1", "mode": "Full access mode",
                            "flag": "--dangerously-skip-permissions"})
MODE_SWITCHING = Msg("modecard.switching",
                     "⚙️ Switching <b>{session}</b> to {mode} (the session restarts)…",
                     note="HTML. A mode switch that restarts the session is under way; {mode} "
                          "is the mode's label, which already says mode",
                     eg={"session": "x1", "mode": "Ask mode"})
MODE_DIALOG_OPEN = Msg("modecard.dialog_open",
                       "⚠️ <b>{session}</b> is waiting on a question or permission prompt. "
                       "Answer it first, then switch the mode.",
                       note="HTML. An Ask/Auto switch refused: the session shows a prompt the "
                            "key presses would answer", eg={"session": "x1"})
MODE_PRESS_FAILED = Msg("modecard.press_failed",
                        "❌ Couldn't reach <b>{session}</b>'s terminal - mode not changed.",
                        note="HTML. The key presses for an Ask/Auto switch could not be sent",
                        eg={"session": "x1"})

# /whoami: config keys and role names are names, written as they are.
WHOAMI_NOT_MEMBER = Msg("whoami.not_member", "🪪 You're not a member of this scope.",
                        note="/whoami from someone not in this chat's configuration")
WHOAMI_HEAD = Msg("whoami.head", "🪪 <b>{member}</b> in <b>{scope}</b>",
                  note="HTML. /whoami: who and which configured chat (scope)",
                  eg={"member": "@aly", "scope": "team"})
WHOAMI_TEAM_HEAD = Msg("whoami.team_head", "🪪 <b>{member}</b>",
                       note="HTML. /whoami in team mode: who", eg={"member": "@aly"})
WHOAMI_ROLE = Msg("whoami.role", "  role: <b>{role}</b>",
                  note="HTML. /whoami: the person's role, named as configured",
                  eg={"role": "user"})
WHOAMI_BYPASS_SAFETY = Msg("whoami.bypass_safety", "  bypass_safety: {value}",
                           note="/whoami: a setting's name (as it is) and yes or no",
                           eg={"value": "no"})
WHOAMI_BYPASS_ROLE_DENIES = Msg("whoami.bypass_role_denies", "  bypass_role_denies: {value}",
                                note="/whoami: a setting's name (as it is) and yes or no",
                                eg={"value": "no"})
WHOAMI_CAN_MANAGE = Msg("whoami.can_manage", "  can_manage: {value}",
                        note="/whoami: a setting's name (as it is) and yes or no",
                        eg={"value": "no"})
WHOAMI_EFFECTIVE_DENY = Msg("whoami.effective_deny", "  effective deny_tools: {tools}",
                            note="/whoami: the tools refused in the end (deny_tools is a "
                                 "setting's name)", eg={"tools": "Bash, WebFetch"})
WHOAMI_EFFECTIVE_ALLOW = Msg("whoami.effective_allow", "  effective allow_tools: {tools}",
                             note="/whoami: the only tools allowed in the end (allow_tools is "
                                  "a setting's name)", eg={"tools": "Read, Grep"})
WHOAMI_TEAM_DENY = Msg("whoami.team_deny", "  deny_tools: {tools}",
                       note="/whoami in team mode: the tools refused (deny_tools is a "
                            "setting's name)", eg={"tools": "Bash"})
WHOAMI_YES = Msg("whoami.yes", "yes", note="/whoami: a setting is on")
WHOAMI_NO = Msg("whoami.no", "no", note="/whoami: a setting is off")
WHOAMI_NONE = Msg("whoami.none", "(none)", note="/whoami: no tool on the list")
WHOAMI_ANY = Msg("whoami.any", "(any)", note="/whoami: every tool allowed")
WHOAMI_NOT_ALLOWED = Msg("whoami.not_allowed", "🪪 You're not on this bot's allow-list.",
                         note="/whoami in team mode from someone not on the team")
WHOAMI_PERSONAL = Msg("whoami.personal",
                      "🪪 Personal mode - full control of this machine from this DM.",
                      note="/whoami with one person and no team configured")

_DOWNLOAD_ATTEMPTS = 3
# Seconds slept after the n-th failed attempt (index n-1) before the next
# one. With three attempts only the first two entries are ever slept; the
# third is headroom should _DOWNLOAD_ATTEMPTS grow. Tests set this to
# (0, 0, 0) — never patch asyncio.sleep to shortcut it: the module-level
# ``asyncio`` IS the global module, and patching it wedges the loop.
_DOWNLOAD_BACKOFF_SECONDS: tuple[float, ...] = (1.0, 2.0, 4.0)
# Per-call read timeout for the download. The Application default
# (lifecycle: read_timeout(20)) is sized for API calls; a multi-MB file on
# a slow link legitimately needs longer and should not need a retry at all.
_DOWNLOAD_READ_TIMEOUT = 60.0

# ---- File uploads: album (media group) coalescing -------------------------
# Telegram delivers an album as one Update per item sharing a
# media_group_id, the caption riding on only one of them. Items are
# collected per (chat_id, media_group_id) and injected as ONE prompt once
# no further item has arrived for this long.
_ALBUM_SETTLE_SECONDS = 1.5
# A group that has seen no arrival or completion for this long is dropped,
# so a lost Update can never leak an entry for the life of the daemon.
_ALBUM_MAX_AGE_SECONDS = 60.0


@dataclass
class _PendingAlbum:
    """One in-flight Telegram media group — see ``_handle_file``."""

    key: tuple[int, str]  # (chat_id, media_group_id)
    update: Update  # first item's Update: reply-context, origin and 👀 target
    ctx: ContextTypes.DEFAULT_TYPE
    touched: float  # time.monotonic() of the latest arrival or completion
    paths: list[Path] = field(default_factory=list)  # arrival order
    failed: list[str] = field(default_factory=list)  # display names
    caption: str = ""
    all_photos: bool = True
    pending: int = 0  # registered items whose download is still running
    settle_task: asyncio.Task | None = None


def open_dialog_surface(sess) -> tuple[str, int | None, int | None]:
    """``(kind, chat_id, message_id)`` of the dialog *sess* waits on, as
    the chat shows it: ``kind`` is ``"question"`` (AskUserQuestion),
    ``"permission"`` (a tool permission) or ``"terminal"`` (no answerable
    message of this wait in the chat: a dialog left to the terminal, or a
    prompt whose send did not land). The message is the busy card that
    carries the prompt inline, or the prompt's own message; ``None`` for
    ``"terminal"``."""
    from aipager.bot.transport import resolve_chat_id_int

    def kind_of(perm: dict) -> str:
        info = perm.get("tool_info")
        name = info.get("name") if isinstance(info, dict) else None
        return ("question" if perm.get("ask_question") or name == "AskUserQuestion"
                else "permission")

    card = sess.busy_msg_id
    perm = sess.pending_permission
    if isinstance(perm, dict) and isinstance(card, int) and card > 0:
        return kind_of(perm), resolve_chat_id_int(sess), card
    sent = sess.pending_prompt_msg
    if isinstance(sent, dict):
        sent_perm = sent.get("perm")
        msg_id = sent.get("msg_id")
        if (isinstance(sent_perm, dict) and isinstance(msg_id, int) and msg_id > 0
                and (sent_perm.get("wait_started_at") or 0)
                >= sess.interactive_entered_at):
            chat = sent.get("chat_id")
            return (kind_of(sent_perm),
                    chat if isinstance(chat, int) and chat else resolve_chat_id_int(sess),
                    msg_id)
    return "terminal", None, None


def _claim_dialog_hold(sess) -> dict | None:
    """The hold (``TrackedSession.dialog_hold``) a message held for a
    dialog belongs to, created for this dialog when there is none or the
    one there is an earlier dialog's (its notices are carried along, to be
    deleted at the release). None when no dialog is open and none is
    being released. Synchronous: no await between the check and the
    claim."""
    hold = sess.dialog_hold
    if not sess.dialog_is_open():
        return hold
    entered = sess.interactive_entered_at
    if hold is None or hold.get("entered_at") != entered:
        hold = {"entered_at": entered, "told": set(),
                "notices": list(hold.get("notices") or []) if hold else []}
        sess.dialog_hold = hold
    return hold


def _upload_chat(update) -> int:
    """The chat an upload came from, whose folder it goes to. Refuses
    (``UnsafeUploadFolder``) when there is none, rather than guess."""
    chat = calling_chat_id(update)
    if chat is None:
        raise uploads.UnsafeUploadFolder("no-chat", None)
    return chat


def _sent_to_line(glyph: str, what: str, label: str, tr=None) -> str:
    """``🧹 /clear sent to x1``: the reply to a keyboard command or
    template, naming the session it went to (roadmap 8.90). Plain text, in
    *tr*'s language (English when None). *what* is shown as it is: the
    tapped button's own text, or the command."""
    tr = tr or i18n.get("en")
    shown = Command(what) if what.startswith("/") else what
    return tr.text(SENT_TO, glyph=Safe(glyph), what=shown, session=label)


def _file_prompt(caption: str, paths: list[Path], *, all_photos: bool) -> str:
    """The prompt handed to claude for one or more uploaded files —
    shared by the lone-file and album paths so an album reads exactly
    like a single upload, pluralised.

    Without a caption the prompt only POINTS at the file(s) — ``check
    this: <path>`` / ``check these: <paths>`` — and never presumes the
    task: the old "Describe this image" / "Read and analyze this file"
    defaults sent claude off describing a screenshot the operator had
    only meant to hand over (operator request, 2026-09-05). Photos and
    documents get the same neutral form; ``all_photos`` is kept for the
    callers' sake and no longer changes the wording."""
    joined = " ".join(str(p) for p in paths)
    if caption:
        return f"{caption} {joined}"
    return f"check {'this' if len(paths) == 1 else 'these'}: {joined}"


def _split_caption_target(caption: str, own_username: str = "") -> tuple[str | None, str]:
    """``(label, rest)`` for a caption whose FIRST token is ``/<label>``,
    ``(None, caption)`` otherwise.

    Mirrors the text path, where ``/<label> <prompt>`` is a direct send
    to that session and a bare ``/<label>`` names it too. Only the first
    token routes: ``check /ann`` is a caption, not a target. The slash is
    never left in the prompt — ``session_ops._inject_prompt`` sends a
    ``/``-prefixed text raw, and Claude Code then reads it as a slash
    command and rejects it without firing a hook (observed 2026-09-05,
    roadmap 8.10). A bare slash (``/`` or ``/ text``) has no label and
    is returned as the label ``/`` so the caller refuses it as an
    unknown session rather than sending it. A trailing ``@<own_username>``
    on the label (group clients add it, roadmap 8.85) is dropped."""
    stripped = caption.strip()
    if not stripped.startswith("/"):
        return None, caption
    parts = stripped.split(None, 1)
    head = parts[0]
    rest = parts[1] if len(parts) > 1 else ""
    return group_intake.drop_own_suffix(head[1:], own_username) or head, rest


async def _download_with_retry(media, save_path: Path, *, display_name: str) -> None:
    """``get_file()`` + ``download_to_drive()`` with bounded retries.

    Retries the transient network classes only — ``NetworkError`` and
    its ``TimedOut`` subclass. In python-telegram-bot 22 ``BadRequest``
    ALSO subclasses ``NetworkError`` but is a 4xx (a bad file_id will not
    come right on the next try), so it is excluded explicitly;
    ``Forbidden`` and everything else never retry. Re-raises the last
    error once every attempt has failed.
    """
    for attempt in range(1, _DOWNLOAD_ATTEMPTS + 1):
        try:
            tg_file = await media.get_file(read_timeout=_DOWNLOAD_READ_TIMEOUT)
            await tg_file.download_to_drive(
                custom_path=str(save_path), read_timeout=_DOWNLOAD_READ_TIMEOUT,
            )
            return
        except NetworkError as exc:
            if isinstance(exc, BadRequest) or attempt >= _DOWNLOAD_ATTEMPTS:
                raise
            delay = _DOWNLOAD_BACKOFF_SECONDS[
                min(attempt - 1, len(_DOWNLOAD_BACKOFF_SECONDS) - 1)
            ]
            log.info(
                "Download of %s: attempt %d/%d failed (%s), retrying in %.0fs",
                display_name, attempt, _DOWNLOAD_ATTEMPTS,
                type(exc).__name__, delay,
            )
            await asyncio.sleep(delay)


class CommandHandlersMixin:
    """Mixin for TelegramBot — see :mod:`aipager.bot` overview."""

    #: ``SessionMonitor.check_parked`` (wired by cli/daemon.py): the gate
    #: looks for a Claude Code background-job park again before each
    #: message, so one noticed between two ticks still holds it.
    parked_check = None

    async def _hold_for_open_dialog(self, update: Update, sess, text: str,
                                    reply_context: str = "") -> object:
        """Queue `text` instead of injecting it while a dialog is open,
        while a different Telegram user's note is still outstanding, OR
        while a different person's turn is running (roadmap 8.77,
        :meth:`_turn_sender_differs`).

        The single gate every inbound prompt path consults. Returns True
        when the message was held, in which case the caller must return
        without injecting (:data:`HOLD_DROPPED`, also truthy, when it had
        to be held but the queue was full: it was dropped, the sender told). Same name, same 8-call-site seam as before
        (design.md "queue handoff") — a second hold condition was added
        to the existing gate rather than threading a new one through
        every call site.

        Held messages go into ``pending_queue`` rather than a structure of
        their own: an unanswered permission prompt can sit indefinitely, so
        the hold needs the cap, the 24h expiry, the persistence and the
        ``/clearqueue`` lever that queue already has. No new release path is
        needed either. Ten sites clear ``pending_permission``; every one of
        them either transitions to BUSY (the answer handlers, the auto-deny
        path, the INTERACTIVE watchdog), is itself the turn-end handler that
        drains the queue, or belongs to stop/kill — which clear the queue
        deliberately, as they already did for busy-queued messages.

        The mixed-sender condition reads ``update.effective_user.id``
        directly rather than ``sess.last_driver_user_id`` — at the point
        every one of these call sites runs, ``_mark_driver`` (which sets
        that field) has not necessarily fired yet for THIS message, so
        the session-level field can still describe the PREVIOUS sender.

        The reaction is the same 👀 a busy-queued message gets, deliberately:
        the state really is the same one (handed off, not yet taken). It
        turns 👍 when Claude takes the message after its release, or 🤷
        if it is discarded first (:mod:`aipager.bot.reactions`).
        """
        held_reason = ""
        # Parked on a Claude Code background job, looked for again now
        # (the park can be newer than the monitor's last tick): ahead of
        # every other reason, since typed into the parked client the text
        # can start a stray background job. Held until it is brought back.
        check = self.parked_check
        if check is not None:
            try:
                result = check(sess)
                if inspect.isawaitable(result):
                    await result        # the daemon's: its reads off the loop
            except Exception:
                log.warning("[%s] parked check failed", sess.label, exc_info=True)
        for_park = bool(sess.parked_job)
        for_dialog = (not for_park
                      and (sess.dialog_is_open() or sess.dialog_release_pending()))
        if for_park:
            held_reason = "parked"
        elif for_dialog:
            # A dialog is open (or was just answered and the messages held
            # for it are still going out, ahead of this one). Keyed to the
            # dialog, never to a time limit: typed now, the text and its
            # Enter would answer the dialog (2026-10-12).
            held_reason = "prompt open"
        elif mixed_sender_note_outstanding(sess, update):
            held_reason = "different sender outstanding"
        elif self._turn_sender_differs(sess, driver_id_from_update(update),
                                       chat_id=calling_chat_id(update)):
            # Roadmap 8.77 (D-H): someone else's turn is running. Joined to
            # it, this message would run under that turn's rules; held, it
            # drains at the turn's end and runs as its sender.
            held_reason = "another sender's turn running"
        # A `/model` was just typed and Claude Code may still be checking
        # the model: text typed now would be lost. Held, it goes out once
        # the statusline reports the switch or the check's time is up
        # (`_settle_model_hold`).
        # Also while messages held for it wait to go out: this one goes
        # after them (`model_hold_active`).
        for_model = not held_reason and sess.model_hold_active()

        if not held_reason and not for_model:
            return False
        if not sess.queue_prompt(text, update.message.message_id, reply_context,
                                 driver_id_from_update(update)):
            await reply_text(update.message,
                i18n.for_update(update).n(QUEUE_FULL, QUEUE_CAP, session=sess.label),
                parse_mode="HTML",
            )
            return HOLD_DROPPED
        self.registry.mark_dirty()
        if for_dialog:
            # Recorded before any await: a dialog answered meanwhile then
            # marks the hold answered, and this message goes out with it.
            _claim_dialog_hold(sess)
        if for_model:
            sess.model_hold = True
        await self._react(update, reactions.HANDED_OFF)
        if for_model:
            log.info("[%s] Held (model check running): %s", sess.label, text[:80])
        else:
            log.info("[%s] Held (%s): %s", sess.label, held_reason, text[:80])
        if for_dialog:
            await self._tell_held_for_dialog(update, sess)
        if for_park:
            await self._after_park_hold(update, sess)
        return True

    async def _after_park_hold(self, update: Update, sess) -> None:
        """A message was held for a Claude Code background-job park (P5).
        From someone who may Restart the session, it brings the session
        back by itself (a spawned, unforced bring-back: the message goes
        out after it, ``bg_release``); while one is already running it
        only waits. Anyone else is told once per park why it waits."""
        person = calling_user_id(update)
        if self._bring_back_refusal(sess, person, calling_chat_id(update)) is not None:
            await self._tell_held_for_park(update, sess)
            return
        if sess.relaunch_in_flight:
            log.info("[%s] held for a bring-back already running", sess.label)
            return
        session_ops_mod._spawn(self._auto_bring_back(update, sess))

    async def _auto_bring_back(self, update: Update, sess) -> None:
        """The gate's bring-back (:meth:`_after_park_hold`), and what it
        tells the sender: the busy question once per person per park (its
        buttons stop the job or leave it), or why it failed. Never raises."""
        try:
            outcome = await self._bring_back_core(sess)
            if outcome.reason == "bg_busy" and outcome.retry_after > 0:
                # Parked a moment ago, and that is the only reason: a job
                # forked a moment ago can read idle just before it goes on
                # with a moved turn. The message is held anyway: wait the
                # moment out and decide again, rather than ask.
                log.info("[%s] bring-back waits %.1fs for the park to settle",
                         sess.label, outcome.retry_after)
                await session_ops_mod._sleep(outcome.retry_after)
                outcome = await self._bring_back_core(sess, settled=True)
        except Exception:
            log.exception("[%s] automatic bring-back failed", sess.label)
            return
        if outcome.ok or outcome.reason == "already_restarting":
            return
        if outcome.reason == "not_parked":
            # The park ended meanwhile. Another bring-back that finished
            # between this spawn and its run leaves its release pending
            # (its relaunched claude may not be up yet), or has released a
            # held message already and that turn runs: the release and the
            # turn's end drain the queue, in order. Only a park that ended
            # with no bring-back (an idle session nobody releases) drains
            # here.
            if not sess.bg_release_pending and sess.status == Status.IDLE:
                await self._drain_next_queued(sess)
            return
        chat_id = calling_chat_id(update)
        if chat_id is None or not self._app:
            return
        tr = i18n.for_update(update)
        kb = None
        if outcome.reason == "bg_busy":
            marker = ("busy", calling_user_id(update) or 0)
            if marker in sess.park_told:
                return
            sess.park_told.add(marker)  # claimed before the send's await
            text = tr.html(BG_BUSY_QUESTION, session=sess.label)
            kb = session_parity.bg_busy_keyboard(self, chat_id, sess, tr)
        else:
            text = (bg_outcome_html(tr, outcome, sess.label)
                    or session_parity._restart_outcome_text(outcome, tr=tr))
        try:
            await send_text(self._app.bot, chat_id=chat_id, text=text,
                            parse_mode="HTML", reply_markup=kb,
                            reply_to_message_id=update.message.message_id)
        except Exception:
            log.debug("[%s] bring-back reply failed", sess.label, exc_info=True)

    async def _tell_held_for_park(self, update: Update, sess) -> None:
        """Tell the sender of a message held for a Claude Code background
        job why it waits: once per person per park (``park_told``; later
        messages only get the 👀), as a reply to the held message.
        Best-effort."""
        person = driver_id_from_update(update) or 0
        if person in sess.park_told:
            return
        sess.park_told.add(person)  # claimed before the send's await
        chat_id = calling_chat_id(update)
        if chat_id is None:
            return
        try:
            await send_text(self._app.bot, chat_id=chat_id,
                            text=i18n.for_update(update).html(
                                BG_HELD_NOTICE, session=sess.label),
                            parse_mode="HTML",
                            reply_to_message_id=update.message.message_id)
        except Exception:
            log.debug("[%s] parked-hold notice failed", sess.label, exc_info=True)

    async def _tell_held_for_dialog(self, update: Update, sess) -> None:
        """Tell the sender of a message held behind an open dialog why it
        waits: once per person per dialog (later messages only get the 👀),
        as a reply to the dialog's card when it is in this chat, else to
        the held message. Records the hold (``dialog_hold``) whatever the
        send does, so the dialog's close releases the message and deletes
        the notice. Best-effort: a failed send leaves the message held."""
        hold = sess.dialog_hold
        if hold is None or not sess.dialog_is_open():
            return  # answered (or settled) meanwhile: nothing to wait for
        hold = _claim_dialog_hold(sess)
        person = driver_id_from_update(update) or 0
        if person in hold["told"]:
            return
        hold["told"].add(person)  # claimed before the send's await
        chat_id = calling_chat_id(update)
        kind, surface_chat, surface_msg = open_dialog_surface(sess)
        text = i18n.for_update(update).text(
            {"question": HELD_QUESTION, "permission": HELD_PERMISSION}.get(
                kind, HELD_TERMINAL))
        held_id = update.message.message_id
        targets = []
        if surface_msg and surface_chat == chat_id:
            targets.append(surface_msg)
        targets.append(held_id)
        sent = None
        for reply_to in targets:
            try:
                sent = await send_text(self._app.bot, chat_id=chat_id, text=text,
                                       reply_to_message_id=reply_to)
                break
            except Exception:
                # The card is gone (deleted, too old): under the message.
                log.debug("[%s] held-message notice under %s failed",
                          sess.label, reply_to, exc_info=True)
        message_id = getattr(sent, "message_id", None)
        if not isinstance(message_id, int) or chat_id is None:
            return
        if sess.dialog_hold is hold:
            hold["notices"].append((chat_id, message_id))
            return
        # The dialog closed and its hold was settled while this went out:
        # the notice is already out of date.
        try:
            await self._app.bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception:
            log.debug("[%s] could not delete the late held-message notice",
                      sess.label, exc_info=True)

    async def _react(self, update: Update, emoji: str) -> None:
        """React to the user's message — one lifecycle edge
        (:mod:`aipager.bot.reactions`: allowed emoji only, never repeated,
        never backwards, SIGNAL class, mute-gated)."""
        try:
            chat_id = update.effective_chat.id
            msg_id = update.message.message_id
        except Exception:
            return  # no message to react to in this context
        await reactions.set_reaction(self, chat_id, msg_id, emoji)

    async def _install_voice_extra(self, query) -> None:
        """Run the install subprocess and edit the prompt message with
        progress, ending in success or failure.

        Triggered by the `__voice__:install` inline-keyboard tap when
        the user sends a voice message without the voice extra. Lets
        users on the road install the extra without SSH access.
        """
        from aipager import updater

        # The tapped message is the chat's (resolved here: a stand-in may
        # take exactly ``(query)``).
        tr = i18n.for_update(SimpleNamespace(callback_query=query))
        installer = updater._detect_installer()
        cmd = updater.install_extra_cmd(installer, "voice")
        if cmd is None:
            # Should be unreachable for "voice" today — install_extra_cmd
            # only returns None for an extra it doesn't know about. Keep
            # a defensive fallback so a future extra can't strand the user.
            await self._safe_edit_callback(query, tr.text(MEDIA_INSTALL_NO_RECIPE))
            return

        await self._safe_edit_callback(query, tr.text(MEDIA_INSTALLING))

        # Launch the install. Capture stdout/stderr so a failure message
        # can show the user what went wrong.
        try:
            proc = await _create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except (OSError, subprocess.SubprocessError) as e:
            # Escaped as ever, though the edit is plain text.
            await self._safe_edit_callback(
                query, tr.html(MEDIA_INSTALL_CANT_START, error=str(e)))
            return

        # Edit the message every few seconds so the user has a
        # heartbeat that the install is alive.
        started = time.monotonic()
        last_edit_at = started
        while True:
            try:
                # Wait briefly for the proc to finish; if it doesn't,
                # edit the message and loop.
                await asyncio.wait_for(proc.wait(), timeout=5.0)
                break
            except asyncio.TimeoutError:
                elapsed = int(time.monotonic() - started)
                if time.monotonic() - last_edit_at >= 5.0:
                    await self._safe_edit_callback(
                        query, tr.text(MEDIA_INSTALL_WORKING, seconds=elapsed))
                    last_edit_at = time.monotonic()

        stdout = (await proc.stdout.read()).decode("utf-8", errors="replace")
        if proc.returncode == 0:
            # Only brew genuinely wipes the extra on upgrade (its
            # formula venv is rebuilt from scratch). Editable /
            # pip-user / venv installs keep faster-whisper indefinitely
            # — the warning would just be noise there.
            footer = ""
            if installer == "brew":
                footer = "\n\n" + tr.html(MEDIA_BREW_FOOTER,
                                           command=Span("`brew upgrade aipager`"))

            # Always offer the restart button — service units use
            # systemctl / launchctl; other installs spawn a detached
            # replacement and SIGTERM ourselves. Either way the user
            # doesn't need terminal access.
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    tr.text(MEDIA_BTN_RESTART),
                    callback_data="__voice__:restart",
                ),
            ]])
            await self._safe_edit_callback(
                query,
                tr.html(MEDIA_INSTALLED) + footer,
                parse_mode="HTML",
                reply_markup=kb,
            )
        else:
            tail = stdout.strip()[-500:]
            await self._safe_edit_callback(
                query, tr.html(MEDIA_INSTALL_FAILED, code=proc.returncode, output=tail),
                parse_mode="HTML",
            )

    async def _restart_daemon(self, query) -> None:
        """Trigger a clean restart.

        A daemon that IS the systemd-user unit's main process schedules a
        detached, delayed ``systemctl --user restart`` through
        :func:`aipager.self_update.schedule_restart` (a transient unit
        outside our cgroup, so it survives our exit) — never a blocking
        ``systemctl restart`` from inside our own cgroup on the event loop.
        It refuses when the unit's KillMode would take every
        daemon-launched session down with it. launchd goes through
        ``launchctl kickstart`` in a thread. Everything else — including a
        foreground daemon on a machine that also has a unit file installed,
        which the old ``LINUX_UNIT_PATH.exists()`` check sent to systemctl
        and so started a second daemon — spawns a detached replacement that
        waits for us to die (:func:`aipager.self_restart.spawn_waiter`, the
        original launch argv), then stops this daemon cleanly.
        """
        import platform
        from aipager import self_update
        from aipager.service import MACOS_LABEL, MACOS_PLIST_PATH, _run

        # The tapped message is the chat's (a stand-in may take ``(query)``).
        tr = i18n.for_update(SimpleNamespace(callback_query=query))

        # A restart mid-update would import a half-written venv (or drop a
        # pending update restart): refuse while this daemon runs an update
        # job or awaits its restart, or while anyone (the CLI) holds the
        # update lock.
        updates = getattr(self, "updates", None)
        if updates is not None and updates.busy:
            await self._safe_edit_callback(query, tr.text(MEDIA_RESTART_BUSY_UPDATE))
            return
        probe = self_update.UpdateLock()
        if not probe.try_acquire():
            await self._safe_edit_callback(query, tr.text(MEDIA_RESTART_LOCKED))
            return
        probe.release()

        sysname = platform.system().lower()
        if sysname == "linux":
            plan = await asyncio.to_thread(self_update.restart_plan, self.registry)
            if plan.mode == "systemd":
                if not plan.automatic:
                    # The plan's reason is self_update's (English).
                    text = (tr.text(MEDIA_RESTART_REFUSED, reason=plan.reason) if plan.reason
                            else tr.text(MEDIA_RESTART_KILLMODE))
                    if plan.at_risk:
                        text += "\n" + tr.text(MEDIA_RESTART_WOULD_KILL,
                                               sessions=tr.join(plan.at_risk))
                    await self._safe_edit_callback(query, text)
                    return
                ok, detail = await asyncio.to_thread(self_update.schedule_restart, plan)
                if not ok:
                    await self._safe_edit_callback(
                        query, tr.text(MEDIA_RESTART_CANT_SCHEDULE, detail=detail))
                    return
                await self._safe_edit_callback(
                    query, tr.text(MEDIA_RESTARTING_IN,
                                   seconds=self_update.RESTART_DELAY_SECONDS))
                return
        if sysname == "darwin" and MACOS_PLIST_PATH.exists():
            await self._safe_edit_callback(
                query, tr.text(MEDIA_RESTART_LAUNCHCTL, command=Span("launchctl kickstart")))
            await asyncio.to_thread(
                _run, ["launchctl", "kickstart", "-k",
                       f"gui/{os.getuid()}/{MACOS_LABEL}"])
            return

        # No service unit: a detached replacement waits for us to die,
        # then runs the original launch argv (aipager.self_restart); we
        # then stop cleanly.
        from aipager import instance, self_restart
        target = self_restart.exec_target()
        if target is None:
            await self._safe_edit_callback(
                query, tr.html(MEDIA_RESTART_CANT_SPAWN, error="no Python interpreter to run"))
            return
        log_path = instance.self_restart_log_path()
        res = await asyncio.to_thread(self_restart.spawn_waiter, target, log_path)
        if not res.ok:
            if res.stage == "open":
                # Escaped as ever, though the edit is plain text.
                text = tr.html(MEDIA_RESTART_CANT_OPEN, path=str(log_path),
                               error=str(res.error))
            elif res.stage == "spawn":
                text = tr.html(MEDIA_RESTART_CANT_SPAWN, error=str(res.error))
            else:
                text = tr.text(MEDIA_RESTART_FAILED_START)
            await self._safe_edit_callback(query, text)
            return

        # Tell the user before we stop. The HTTP edit needs a beat to
        # flush before the shutdown tears the bot down.
        await self._safe_edit_callback(query, tr.text(MEDIA_RESTART_RESEND_VOICE))
        await asyncio.sleep(0.5)
        self_restart.stop_daemon()

    async def _handle_start_cmd(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/start: the home screen (4.3). This chat's sessions (never another
        chat's), where a plain message goes, and the three things to do
        next. It used to be a 25-line manual listing every chat's
        sessions; the manual is /help now."""
        if not await self._authorize(update, allow_read_only=True):
            return
        await self._send_home(update)

    async def _send_home(self, update: Update) -> None:
        """The home screen: /start's reply, and the answer to a message that
        was only a mention of the bot."""
        chat_id = calling_chat_id(update)
        tr = i18n.for_update(update)
        listing, _kb = self._render_status_list(chat_id, update, with_buttons=False)
        target = self._target(update)
        if target is not None and target.status != Status.GONE:
            label = f"<b>{html_mod.escape(target.label)}</b>"
            next_line = group_intake.talk_hint(
                self, chat_id, label, dm=tr.html(HOME_DM_TARGET, session=target.label),
                tr=tr, raw_label=Span(label))
        elif self._status_sessions(chat_id)[0]:
            # Live sessions listed (decided from the sessions, never from
            # the listing's words, which follow the chat's language).
            next_line = tr.html(HOME_TAP_SESSION)
        else:
            next_line = tr.html(HOME_START_ONE)
        text = (f"{tr.html(HOME_TITLE)}\n\n{listing}\n\n{next_line}\n\n"
                f"{tr.html(HOME_HELP_HINT)}")
        # No inline App row: the menu button beside the message box
        # opens the Mini App (see _app_button_row). Resume only with
        # something to resume, or the tap would swap this screen for one
        # line and no buttons.
        row = [InlineKeyboardButton(tr.text(HOME_BTN_NEW), callback_data="_:nw:open")]
        if any(s.claude_session_id for s in self._gone_sessions_sorted(chat_id)):
            row.append(InlineKeyboardButton(tr.text(HOME_BTN_RESUME),
                                            callback_data="_:resume_page:0"))
        row.append(InlineKeyboardButton(tr.text(HOME_BTN_SETTINGS), callback_data="_:set:back"))
        keyboard = InlineKeyboardMarkup([row])
        try:
            await send_text(self._app.bot,
                update.effective_chat.id, text, parse_mode="HTML",
                reply_markup=keyboard,
            )
        except Exception:
            log.warning("Failed to send /start welcome", exc_info=True)
        # Make sure the persistent keyboard is showing.
        await self._send_keyboard(level="main", chat_id=chat_id)

    async def _handle_help_cmd(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/help: a short guide grouped by task (4.3)."""
        if not await self._authorize(update, allow_read_only=True):
            return
        tr = i18n.for_update(update)
        talk = None
        if group_intake.is_group_chat(calling_chat_id(update)):
            talk = group_intake.group_help_talk_line(self, tr=tr)
        text = help_text(tr, talk_line=talk)
        # Problem reports (8.112): the owner's button only, in a DM or a
        # group (the preview itself always opens in the owner's DM), in
        # the chat's language like the /help it sits under.
        extra = ({"reply_markup": report_flow.report_button_markup(tr=tr)}
                 if report_flow.is_owner(self, calling_user_id(update)) else {})
        await reply_text(update.message, text, parse_mode="HTML", **extra)

    def _app_button_row(self, update: Update) -> list:
        """One inline row linking to the Mini App, or [] when it doesn't belong.

        Returned as a list so callers can splice it into an existing
        keyboard with ``+`` and get nothing when the Mini App isn't
        reachable.

        Private chats only: Telegram rejects an ENTIRE keyboard that
        carries a ``web_app`` button in a group, so one misplaced button
        costs every other button in the message.

        Not used on ``/start``: the menu button beside the message box is
        that screen's way into the Mini App. The reply keyboard has no App
        button since 2026-10-12 (Telegram gives a Mini App opened from a
        keyboard button no init data, so it cannot sign in).
        """
        chat = update.effective_chat
        if chat is None or chat.type != "private":
            return []
        url = getattr(self, "_miniapp_url", "")
        if not url:
            return []
        chat_tr = i18n.for_chat(getattr(chat, "id", None))
        from aipager.bot import appearance
        return [[InlineKeyboardButton(kbwords.KB_APP, web_app=WebAppInfo(
            url=miniapp_link.with_lang(url, chat_tr.code)),
            style=appearance.app_style(getattr(chat, "id", None)))]]

    async def _handle_settings_cmd(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /settings — open the root preferences menu.

        Gated like /status (``allow_read_only=True``): viewing current
        values is never restricted. Changing a value is admin-gated in
        groups inside the callback dispatcher, not here (see
        ``callbacks._dispatch_settings_action``).
        """
        if not await self._authorize(update, allow_read_only=True):
            return
        chat_id = calling_chat_id(update)
        text, kb = render_settings_root(
            chat_id or 0, problem_reports=report_flow.settings_row_state(
                self, chat_id, calling_user_id(update)),
            language=i18n.for_update(update).code)
        app_row = self._app_button_row(update)
        if app_row:
            kb = InlineKeyboardMarkup(list(kb.inline_keyboard) + app_row)
        await reply_text(update.message, text, parse_mode="HTML", reply_markup=kb)

    async def _handle_language_cmd(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /language: the list of languages aipager speaks, the same
        one the /settings Language row opens (multilingual design, 7.2).
        Anyone allowed to look may (``allow_read_only=True``); changing a
        group's language is admin-gated in the callback."""
        if not await self._authorize(update, allow_read_only=True):
            return
        from aipager.bot import appearance
        chat_id = calling_chat_id(update)
        private = isinstance(chat_id, int) and chat_id > 0
        text, kb = language_menu.render_language_menu(
            i18n.for_update(update), private=private,
            current=language_menu.current_choice(chat_id, private=private),
            style=appearance.choice_style(chat_id))
        await reply_text(update.message, text, parse_mode="HTML", reply_markup=kb)

    async def _handle_app_cmd(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /app — send the Mini App launcher button.

        Gated like /status (``allow_read_only=True`` — viewing the
        dashboard is never restricted). Mini Apps are a private-chat-only
        Bot API feature (both button types), so groups get a plain-text
        explanation instead, never a button (see design.md / spec.md).
        """
        if not await self._authorize(update, allow_read_only=True):
            return

        tr = i18n.for_update(update)
        chat = update.effective_chat
        if chat is None or chat.id <= 0:
            await reply_text(update.message, tr.text(
                APP_IN_GROUP_TEXT if self._has_dm_scope(calling_user_id(update))
                else APP_NO_DM_TEXT))
            return

        from aipager.config import MINIAPP_ENABLED
        if not MINIAPP_ENABLED:
            await reply_text(update.message, tr.html(
                APP_NOT_ENABLED, command=Span("<code>aipager miniapp enable</code>")),
                parse_mode="HTML")
            return

        # One resolver for every surface that hands out this URL (/app,
        # the chat menu button) — it also keeps the
        # blocking tailscale probe off the event loop.
        from aipager.miniapp.tunnel import resolve_public_url
        url = await resolve_public_url()
        if not url:
            # This used to print Tailscale install instructions, including
            # `curl | sh` and two `sudo` lines. That predates the managed
            # Cloudflare tunnel and is now both a contract violation — the
            # user installs aipager and runs nothing else — and simply
            # false: on a healthy install the only reason there is no URL
            # yet is that the tunnel has not finished coming up
            # (cloudflared needs ~10-30s, and the first DNS probe often
            # fails and succeeds on the retry).
            from aipager.miniapp.server import miniapp_extra_available
            if not miniapp_extra_available():
                await reply_text(update.message, tr.text(APP_CANT_START))
            else:
                await reply_text(update.message, tr.text(APP_SETTING_UP))
            return

        # Personal mode only (scope/team mode already gated above via
        # _authorize's own membership checks): from here on we're about
        # to disclose the tunnel URL itself via the button below, so
        # require the caller to actually be the operator — same guard,
        # same reasoning as MiniAppServer._single_chat (see
        # AuthMixin._is_personal_mode_operator).
        if self.scopes is None and self.team is None:
            tg_user = update.effective_user
            if not self._is_personal_mode_operator(
                tg_user.id if tg_user is not None else None,
            ):
                # The id is copied as it is: Latin digits in every language.
                await reply_text(update.message, tr.text(
                    APP_STRANGER, user_id=str(tg_user.id if tg_user is not None else "?"),
                    command=Span("`aipager config`")))
                return

        from aipager.bot import appearance
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(tr.text(APP_BTN_OPEN), web_app=WebAppInfo(
                url=miniapp_link.with_lang(url, tr.code)),
                style=appearance.app_style(getattr(update.effective_chat, "id", None))),
        ]])
        await reply_text(update.message, tr.text(APP_TAP_TO_OPEN), reply_markup=keyboard)

    async def _handle_status(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /status: this chat's sessions as a list you can act on."""
        if not await self._authorize(update, allow_read_only=True):
            return
        chat_id = calling_chat_id(update)
        sessions = self.registry.all_sessions(chat_id)
        if not sessions and self.scopes is None:
            # Discovery (adopt raw dtach sockets) is a single-scope
            # recovery aid only. In multi-scope the monitor's socket scan
            # already adopts (and stamps) every socket, so an empty scope
            # just says so (below).
            for name in await inject.list_sessions():
                self.registry.get_or_create(name)
            sessions = self.registry.all_sessions(chat_id)

        for name, sess in sessions.items():
            alive = await inject.is_alive(name)
            # Reconcile: socket alive but status GONE → recover to IDLE
            if alive and sess.status == Status.GONE:
                self.registry.transition(name, Status.IDLE)
            # Hidden flag is set by "Clear all" in the Ended view. If a
            # hidden session comes back alive (e.g. via /resume), unhide so
            # it reappears in /status. /resume always shows it regardless.
            if alive and sess.hidden_from_status:
                sess.hidden_from_status = False
                self.registry.mark_dirty()

        # 4.2 (2026-09-30): a list you can act on, instead of one stats
        # table per session. The stats that matter are on each session's
        # second line.
        text, keyboard = self._render_status_list(chat_id, update)
        await reply_text(update.message, text, parse_mode="HTML", reply_markup=keyboard)
        if sessions:
            # Only with sessions to list, as before: an empty /status must
            # not broadcast a keyboard (it would go out even while this
            # chat is muted).
            asyncio.create_task(self._update_bot_commands())

    @staticmethod
    def _read_status_file(session_name: str) -> dict | None:
        """Read cost and context from the statusLine JSON file."""
        sl = statusline_file.read_status_line(session_name)
        if sl is None:
            return None
        return {
            "ctx_pct": sl.context_pct,
            "cost": sl.cost_usd,
            "model": sl.model,
            "total_output": sl.total_output,
        }

    async def _handle_stop_cmd(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/stop [name] (4.4): stop what a session of this chat is doing.
        Bare: the one working session (even when it is not the target), a
        picker when several are, and "Nothing is running." when none is."""
        if not await self._authorize(update):
            return
        chat_id = calling_chat_id(update)
        tr = i18n.for_update(update)
        parts = self._own_text(update).split(maxsplit=1)
        if len(parts) > 1:
            label = parts[1].strip().lstrip("/")
            sess = self.registry.find_by_label(label, chat_id)
            if sess is None:
                await reply_text(update.message, tr.html(NO_SESSION_HERE, session=label),
                                 parse_mode="HTML")
                return
            if not sess.can_be_stopped():
                await reply_text(update.message, tr.html(NOT_WORKING, session=sess.label),
                                 parse_mode="HTML")
                return
            working = [sess]
        else:
            # Working includes an idle session whose job still runs a
            # background agent or shell: its card still says working.
            working = [s for s in self.registry.all_sessions(chat_id).values()
                       if s.label and s.can_be_stopped()]
        if not working:
            await reply_text(update.message, tr.text(NOTHING_RUNNING))
            return
        # A typed name is one candidate, acted on; bare, the P4 rule, and
        # in a group only the person's own target (8.93).
        sess = (working[0] if len(parts) > 1 else
                session_parity.bare_pick(self, chat_id, calling_user_id(update), working))
        if sess is None:
            sent = await reply_text(update.message, tr.text(WHICH_STOP),
                                    reply_markup=session_parity.session_picker(
                                        self, chat_id, working, "pstop", glyph="⏹ ",
                                        keyed=True, user_id=calling_user_id(update)))
            # In a group, the asker's alone (8.94j), like /kill's.
            card_owner.claim_sent(self, chat_id, sent, calling_user_id(update),
                                  kind="stop", command="/stop", picker=True,
                                  who=update.effective_user)
            return
        outcome = await self._stop_session(sess, update=update)
        if not outcome.ok:
            await reply_text(update.message, tr.html(NOT_WORKING, session=sess.label),
                             parse_mode="HTML")

    async def _handle_clearqueue_cmd(
        self, update: Update, ctx: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle /clearqueue — drop pending queued prompts for the last
        active session, AND anything Claude itself is holding but hasn't
        confirmed picking up yet, without interrupting the running task.

        `/stop` already drains both, but at the cost of interrupting the
        current work. `/clearqueue` is the "just stop QUEUING for the
        next thing" surgical tool: the current prompt keeps running, the
        queue and Claude's own not-yet-picked-up notes both empty.

        Thin wrapper around :meth:`_clear_queue_core` (design.md "Stop
        and /clearqueue on the same primitive") — the Mini App's own
        clearqueue route calls the same core, so the two surfaces report
        the same combined count and can never drift.
        """
        if not await self._authorize(update):
            return
        sess = self._target(update)
        tr = i18n.for_update(update)
        if not sess:
            if await self._ask_which_session(update):
                return
            await reply_text(update.message,
                tr.text(CLEAR_NO_ACTIVE, command=Command(LABEL_COMMAND)))
            return
        outcome = await self._clear_queue_core(sess)
        if not outcome.ok:
            await reply_text(update.message, tr.html(CLEAR_NOTHING, session=sess.label),
                             parse_mode="HTML")
            return
        await reply_text(update.message,
                         tr.n(CLEARED, outcome.dropped, session=sess.label),
                         parse_mode="HTML")
        log.info("[%s] /clearqueue cleared %d entries", sess.label, outcome.dropped)

    async def _handle_kill_cmd(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/kill [name] (4.4): end a session of this chat, always through
        a confirm. Bare: the one live session's confirm, or a picker of
        this chat's live sessions (it used to list every chat's)."""
        if not await self._authorize(update):
            return
        chat_id = calling_chat_id(update)
        tr = i18n.for_update(update)
        parts = self._own_text(update).split(maxsplit=1)
        if len(parts) < 2:
            alive = [s for s in self.registry.all_sessions(chat_id).values()
                     if s.status != Status.GONE and s.label]
            if not alive:
                await reply_text(update.message, tr.text(KILL_NONE))
                return
            # The P4 rule; in a group only the person's own target (8.93).
            sess = session_parity.bare_pick(self, chat_id, calling_user_id(update), alive)
            if sess is None:
                sent = await reply_text(update.message, tr.text(KILL_WHICH),
                                        reply_markup=session_parity.session_picker(
                                            self, chat_id, alive, "end", glyph="⏹ ",
                                            user_id=calling_user_id(update)))
                card_owner.claim_sent(self, chat_id, sent, calling_user_id(update),
                                      kind="end", command="/kill", picker=True,
                                      who=update.effective_user)
                return
        else:
            label = parts[1].strip().lstrip("/")
            sess = self.registry.find_by_label(label, chat_id)
            if sess is None:
                await reply_text(update.message, tr.html(NO_LIVE_HERE, session=label),
                                 parse_mode="HTML")
                return
        text, kb = session_parity.render_end_confirm(self, chat_id, sess)
        sent = await reply_text(update.message, text, reply_markup=kb, parse_mode="HTML")
        card_owner.claim_sent(self, chat_id, sent, calling_user_id(update),
                              kind="end", command=f"/kill {sess.label}",
                              who=update.effective_user)

    async def _handle_new_cmd(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /new [name [first message...]]: start a Claude Code session.

        One flow for every spelling (``new_flow``): with an argument the
        session starts at once with the chat's defaults (Auto for an
        admin unless the chat's default says Ask); bare, the Name card
        asks for the name and the next message is that same argument.
        Both end in the same Ready card. A leading ``!`` on the name is
        the legacy Auto shorthand."""
        if not await self._authorize(update):
            return
        parts = self._own_text(update).split(maxsplit=1)  # /new <rest>
        if len(parts) < 2:
            await new_flow.start_wizard(self, update, ctx)
            return
        await new_flow.create_from_text(self, update, parts[1])

    # ---- /new name-conflict prompt --------------------------------------

    async def _send_new_conflict_prompt(self, *, update: Update,
                                          existing: TrackedSession,
                                          prompt: str,
                                          mode: str,
                                          model: str | None = None,
                                          cwd: str | None = None,
                                          edit_msg_id: int | None = None) -> None:
        """Render the Resume / Replace / Cancel buttons when /new hits a known name.

        ``edit_msg_id``: the Name card the name answered, which becomes
        this card (one message per flow); else it is a reply.

        ``existing.status`` tells us whether the conflict is with a live
        session (`!= GONE`) or a history entry (`GONE` with a stashed
        claude_session_id). Both flows share the same buttons; the
        callback distinguishes via the session's current status.
        """
        label = existing.label
        alive = existing.status != Status.GONE

        # One card per name: a newer /new for it (anyone's) replaces the
        # older card, which says so instead of keeping live buttons its
        # author can no longer use.
        older = self._new_conflict_pending.get(existing.name)
        entry = self._new_conflict_pending[existing.name] = {
            "prompt": prompt,
            "mode": mode,
            # What a Replace starts the fresh session with: the same
            # choices the Name card or the chat's defaults gave /new.
            "model": model,
            "cwd": cwd,
            # Replace refuses to kill work the card never showed: a turn
            # that starts after it, on a session idle when it was shown.
            "was_working": existing.status in (Status.BUSY, Status.INTERACTIVE),
            "user_id": (update.effective_user.id
                        if update.effective_user else 0),
            "msg_id": update.message.message_id,
        }

        conflict_chat_id = calling_chat_id(update) or 0
        # A shared card: the chat's language (signature kept for stand-ins).
        tr = i18n.for_chat(conflict_chat_id)
        if alive:
            header = tr.html(CONFLICT_RUNNING, session=label)
            resume_label = tr.text(CONFLICT_BTN_SWITCH)
        else:
            preview = existing.last_assistant_preview or ""
            header = (tr.html(CONFLICT_USED_PREVIEW, session=label, preview=preview) if preview
                      else tr.html(CONFLICT_USED, session=label))
            resume_label = tr.text(CONFLICT_BTN_RESUME)

        from aipager.bot import keyboards  # the shared ↩️ Cancel
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    resume_label,
                    callback_data=session_parity.session_cb(
                        self, conflict_chat_id, existing, "new_resume")),
                InlineKeyboardButton(
                    tr.text(CONFLICT_BTN_REPLACE),
                    callback_data=session_parity.session_cb(
                        self, conflict_chat_id, existing, "new_replace")),
            ],
            [
                InlineKeyboardButton(
                    tr.text(keyboards.CANCEL),
                    callback_data=session_parity.session_cb(
                        self, conflict_chat_id, existing, "new_cancel")),
            ],
        ])
        card_id = None
        if edit_msg_id is not None:
            try:
                await edit_text_at(self._app.bot,
                    chat_id=conflict_chat_id, message_id=edit_msg_id,
                    text=header, parse_mode="HTML", reply_markup=keyboard,
                )
                card_id = edit_msg_id
            except Exception:
                log.debug("conflict card: editing the Name card failed, replying",
                          exc_info=True)
        if card_id is None:
            sent = await reply_text(update.message,
                header, parse_mode="HTML", reply_markup=keyboard,
            )
            card_id = getattr(sent, "message_id", None)
        # Into THIS card's own entry: another /new for the name may have
        # replaced it while the card went out.
        entry["card"] = (conflict_chat_id, card_id)
        if self._new_conflict_pending.get(existing.name) is not entry:
            stale = entry["card"]     # it did: this card is the older one now
        else:
            stale = (older or {}).get("card")
            if stale == entry["card"]:
                stale = None          # the same message, edited again
        if stale and stale[1]:
            try:
                await edit_text_at(self._app.bot,
                    chat_id=stale[0], message_id=stale[1],
                    text=i18n.for_chat(stale[0]).html(CONFLICT_REPLACED, session=label),
                    parse_mode="HTML",
                )
            except Exception:
                log.debug("conflict card: retiring the older card failed",
                          exc_info=True)

    async def _handle_resume_cmd(self, update: Update,
                                  ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /resume [<name>] — resume a previous session, or open picker."""
        if not await self._authorize(update):
            return
        parts = (update.message.text or "").split(maxsplit=1)
        if len(parts) < 2:
            text, kb = self._render_resume_picker(
                page=0, scope_chat_id=calling_chat_id(update),
            )
            await reply_text(update.message,
                text, parse_mode="HTML", reply_markup=kb,
            )
            return
        name = parts[1].strip().lstrip("@/").lower()
        # Reuse direct-resume helper so the /resume command and the picker
        # button take exactly the same code path (and produce the same
        # errors / dashboard reply).
        await self._do_resume(
            label=name,
            reply_fn=update.message.reply_text,
            update=update,
        )

    async def _handle_mode_cmd(self, update: Update,
                               ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/mode [name] [ask|auto|full] (4.4); /perms is the same command.

        Shows the session's mode with a one-tap switch to each other one
        (it used to flip it blind), or switches straight to the mode named:
        `/mode ask`, `/mode x1 auto`. Only going to Full access needs an
        admin."""
        if not await self._authorize(update):
            return
        chat_id = calling_chat_id(update)
        tr = i18n.for_update(update)
        # `ask` / `auto` / `full`, or the mode's word in any enabled language; a
        # translated word that is also the exact name of a live session
        # here names that session (7b). Stored and sent: the id.
        want, label, ok = mode_labels.parse_mode_args(
            update.message.text.split()[1:],
            lambda name: self.registry.find_by_label(name, chat_id) is not None)
        if not ok:
            # A word it would otherwise drop: say what it takes rather
            # than act on half of what was typed.
            await reply_text(update.message, tr.text(
                MODE_USAGE, example=Command("/mode x1"),
                example_ask=Command("/mode x1 ask")))
            return
        if label is not None:
            sess = self.registry.find_by_label(label, chat_id)
            if sess is None:
                await reply_text(update.message, tr.html(NO_LIVE_HERE, session=label),
                                 parse_mode="HTML")
                return
        else:
            group = session_parity.shared_group(self, chat_id)
            # In a group only the person's own target, never the chat's
            # only live session, which may be another member's (8.93).
            sess = (self.registry.own_target(chat_id, calling_user_id(update)) if group
                    else self._target(update))
            if sess is None or sess.status == Status.GONE:
                live = [s for s in self.registry.all_sessions(chat_id).values()
                        if s.label and s.status != Status.GONE]
                if len(live) > 1 or (group and live):
                    # A mode named still switches in one tap: the picker's
                    # buttons are then the card's own switch (turn-keyed).
                    # A group's picker marks the person's own target.
                    picker_for = calling_user_id(update) if group else None
                    if want is None:
                        question = tr.text(MODE_WHICH)
                        kb = session_parity.session_picker(
                            self, chat_id, live, "mode_show", user_id=picker_for)
                    else:
                        question = tr.text(MODE_WHICH_TO,
                                           mode=Safe(tr.text(mode_label(want))))
                        if want == perm_modes.ASK:
                            kb = session_parity.session_picker(
                                self, chat_id, live, "modeask", keyed=True,
                                user_id=picker_for)
                        elif want == perm_modes.AUTO:
                            kb = session_parity.session_picker(
                                self, chat_id, live, "modeauto", keyed=True,
                                user_id=picker_for)
                        else:
                            kb = session_parity.session_picker(
                                self, chat_id, live, "modefull", keyed=True,
                                user_id=picker_for)
                    sent = await reply_text(update.message, question, reply_markup=kb)
                    card_owner.claim_sent(self, chat_id, sent, calling_user_id(update),
                                          kind="mode", command="/mode", picker=True,
                                          who=update.effective_user)
                    return
                sess = live[0] if live else None
        if sess is None or sess.status == Status.GONE:
            await reply_text(update.message, tr.text(MODE_NONE_LIVE))
            return
        if want is None:
            text, kb = self._render_mode_card(chat_id, sess)
            await reply_text(update.message, text, parse_mode="HTML", reply_markup=kb)
            return
        if self._already_in_mode(sess, want):
            await reply_text(update.message, tr.html(
                MODE_ALREADY, session=sess.label, mode=Safe(mode_labels.shown_label(tr, sess))),
                parse_mode="HTML")
            return
        await self._mode_flow(sess, want, update.message,
                              may_full=self._may_use_full_access(calling_user_id(update), chat_id),
                              chat_id=chat_id,
                              requester=calling_user_id(update))

    # The old name: the same command since 2026-09-30.
    _handle_perms_cmd = _handle_mode_cmd

    def _render_mode_card(self, chat_id, sess: TrackedSession, tr=None):
        """`x1 is in 🤖 Auto mode.` and a switch to each other mode. ✍️ marks
        the chat's target, never in a group: there each member has their
        own (roadmap 8.90), and the card is everyone's (so it is in the
        chat's language when *tr* is None). The Full access button shows
        for everyone; the tap is refused for someone not an admin."""
        tr = tr or i18n.for_chat(chat_id)
        target = (None if group_intake.is_group_chat(chat_id)
                  else self.registry.target_for(chat_id))
        text = tr.html(MODE_IS_TARGET if sess is target else MODE_IS, session=sess.label,
                       mode=Safe(mode_labels.shown_label(tr, sess)))
        if sess.mode_switch is not None:
            text += "\n" + tr.html(mode_switch.SWITCH_PENDING_LINE)
        # A switch to or from Full access relaunches the session, so each
        # button carries the turn it was shown for (a new turn since
        # refuses a restart, as Stop and End do).
        current = sess.current_mode()
        rows = []
        if current != perm_modes.ASK:
            rows.append([InlineKeyboardButton(
                tr.text(MODE_BTN_TO_ASK), callback_data=session_parity.session_cb(
                    self, chat_id or 0, sess, f"modeask{sess.turn_key}"))])
        if current != perm_modes.AUTO:
            rows.append([InlineKeyboardButton(
                tr.text(MODE_BTN_TO_AUTO), callback_data=session_parity.session_cb(
                    self, chat_id or 0, sess, f"modeauto{sess.turn_key}"))])
        if current != perm_modes.FULL:
            rows.append([InlineKeyboardButton(
                tr.text(MODE_BTN_TO_FULL), callback_data=session_parity.session_cb(
                    self, chat_id or 0, sess, f"modefull{sess.turn_key}"))])
        return text, InlineKeyboardMarkup(rows)

    def _mode_card(self, chat_id, sess: TrackedSession, note: str = ""):
        """The /mode card, under *note* (HTML) when there is one: where every
        switch ends, whether it happened, failed or was refused."""
        text, kb = self._render_mode_card(chat_id, sess)
        return (f"{note}\n\n{text}" if note else text), kb

    async def _mode_flow(self, sess: TrackedSession, target_mode: str,
                         message, *, may_full: bool, chat_id=None,
                         query=None, requester: int | None = None) -> None:
        """Switch *sess* to *target_mode* (``ask``, ``auto``, ``full``).

        Ask <-> Auto is in place (Shift+Tab, no restart): fine while a turn
        runs, refused while a question or permission prompt is open, and
        the card says the switch was sent until Claude Code confirms it
        (aipager/bot/mode_switch.py). Into Full access asks to confirm;
        out of it switches at once; both restart the session, and a working
        session offers Stop & switch first. Every way ends in the /mode
        card.

        ``query``: the tap on a /mode card or picker. That message is edited
        through every step, one message that changes in place (operator,
        2026-10-01: a second message left the card behind with a button
        pointing the wrong way); a refusal is a toast and the card stays.
        Without it (the typed command) the flow replies once to *message*
        and edits that reply. ``chat_id``: the chat the card is in, for its
        buttons. ``requester``: who asked (the tapper or the sender); in a
        group the confirm is theirs alone and the result names them
        (roadmap 8.91c)."""
        # The card and the reply are the chat's; a pop-up is the tapper's.
        card_chat = (chat_id if chat_id is not None
                     else getattr(getattr(message, "chat", None), "id", None))
        tr = i18n.for_chat(card_chat)
        person = (i18n.for_person(SimpleNamespace(callback_query=query))
                  if query is not None else tr)
        shown: list = []        # the typed path's one reply, once sent

        async def show(text, kb=None):
            """Put a step on screen; the id of the message it is on, or None
            when nothing showed (the chat is muted)."""
            if query is not None and not shown:
                try:
                    if await edit_text(query, text, parse_mode="HTML",
                                       reply_markup=kb) is MUTED:
                        return None
                    return getattr(query.message, "message_id", None)
                except Exception as exc:
                    if "not modified" in str(exc).lower():
                        # The card already shows this step (a second tap on
                        # the same button): it is on screen, not a failure.
                        return getattr(query.message, "message_id", None)
                    log.debug("[%s] mode card edit failed, replying", sess.label,
                              exc_info=True)
                    target = query.message
            else:
                # The typed command, or a card whose edit failed once: every
                # later step goes to the one reply, never back to the card.
                target = query.message if query is not None else message
            if shown and shown[0] is not MUTED:
                try:
                    await edit_message(shown[0], text, parse_mode="HTML", reply_markup=kb)
                    return shown[0].message_id
                except Exception:
                    log.debug("[%s] mode reply edit failed, replying", sess.label,
                              exc_info=True)
            sent = await reply_text(target, text, parse_mode="HTML", reply_markup=kb)
            shown[:] = [sent]
            return None if sent is MUTED else getattr(sent, "message_id", None)

        async def refuse(toast: str, reply: str) -> None:
            if query is not None:
                await self._safe_answer(query, toast)       # the card stays
            else:
                await reply_text(message, reply, parse_mode="HTML")

        # Admin gate: only switching TO Full access needs one (leaving it
        # narrows, like Ask and Auto, which are everyone's).
        if target_mode == perm_modes.FULL and not may_full:
            await refuse(person.text(TOAST_NEEDS_ADMIN_FULL), tr.html(NEEDS_ADMIN_FULL))
            return
        # A switch relaunches the session: a role without can_manage only
        # for one inside its members' root folder.
        from aipager import member_root
        from aipager.bot.sibling_text import shown as shown_text
        refusal = member_root.relaunch_refusal(self, requester, chat_id, sess.cwd)
        if refusal:
            await refuse(shown_text(person, refusal), member_root.as_html(tr, refusal))
            return

        if sess.status == Status.UNKNOWN:
            await refuse(person.text(TOAST_PERMS_STARTING, session=sess.label),
                         tr.html(MODE_INITIALIZING, session=sess.label))
            return

        in_place = not sess.mode_switch_restarts(target_mode)
        if in_place and sess.dialog_is_open():
            # A Shift+Tab now would be read by the prompt on screen.
            await refuse(person.text(TOAST_MODE_DIALOG_OPEN, session=sess.label),
                         tr.html(MODE_DIALOG_OPEN, session=sess.label))
            return

        if query is not None:
            await self._safe_answer(query)
            # The tapped message (a /mode picker's row, or the card's own
            # switch) becomes this flow's: no earlier owner holds it.
            card_owner.release(self, chat_id,
                               getattr(getattr(query, "message", None), "message_id", None))
        tg_user = (getattr(query, "from_user", None) if query is not None
                   else getattr(message, "from_user", None))
        by = self._actor_label(requester, chat_id, tg_user)

        if in_place:
            outcome = await self._mode_switch_core(sess, target_mode)
            mode = Safe(tr.html(mode_label(target_mode)))
            if outcome.reason == "sent":
                note = tr.html(mode_switch.SWITCH_SENT, session=sess.label, mode=mode)
            elif outcome.reason == "already":
                note = tr.html(MODE_ALREADY, session=sess.label,
                               mode=Safe(mode_labels.shown_label(tr, sess)))
            elif outcome.reason == "dialog_open":
                note = tr.html(MODE_DIALOG_OPEN, session=sess.label)
            elif outcome.reason == "send_failed":
                note = tr.html(MODE_PRESS_FAILED, session=sess.label)
            elif outcome.ok:
                note = (tr.html(callbacks_mod.MODE_SWITCHED_BY, mode=mode, person=Command(by))
                        if by else "")
            elif outcome.reason == "still_stopping":
                note = tr.html(callbacks_mod.MODE_STILL_STOPPING, session=sess.label)
            elif outcome.reason == "launch_failed":
                note = tr.html(callbacks_mod.MODE_SWITCH_FAILED, error=outcome.err)
            elif outcome.reason == "starting":
                note = tr.html(MODE_INITIALIZING, session=sess.label)
            else:
                note = callbacks_mod._switch_refused(outcome, tr)
            text, kb = callbacks_mod._switch_card(self, chat_id, sess, outcome, note, tr)
            card_id = await show(text, kb)
            if outcome.reason == "sent":
                mode_switch.attach_reply(sess, card_chat, card_id, "mode_card", by)
            return

        def own(card_id) -> None:
            """The confirm on *card_id* is the requester's (8.91c)."""
            card_owner.claim(self, chat_id, card_id, requester,
                             kind="mode", command=f"/mode {sess.label}", who=tg_user)

        busy_text = {perm_modes.ASK: MODE_BUSY_TO_ASK, perm_modes.AUTO: MODE_BUSY_TO_AUTO,
                     perm_modes.FULL: MODE_BUSY_TO_FULL}[target_mode]
        if sess.status == Status.BUSY:
            # BUSY flow: show Stop & switch / Not now keyboard.
            # The whole sentence for each mode: never a shown word sliced.
            card_id = await show(tr.html(busy_text, session=sess.label),
                                 self._build_perms_busy_keyboard(sess))
            if card_id is None:
                # Muted: no keyboard reached the user, so there is nothing
                # pending for a tap to confirm.
                return
            self._perms_pending[sess.name] = {
                "target_mode": target_mode,
                "msg_id": card_id,
                "label": sess.label,
                # The card may be an older message edited in place, so its
                # id says nothing about the turn: the turn it was shown for
                # decides (callbacks perms_stop_switch).
                "turn": sess.turn_key,
            }
            own(card_id)
            return

        # IDLE flow.
        if target_mode == perm_modes.FULL:
            # Into Full access: require confirmation.
            card_id = await show(
                tr.html(MODE_CONFIRM_FULL, session=sess.label,
                        mode=Safe(tr.html(mode_label(perm_modes.FULL))),
                        flag=Span(f"<code>{perm_modes.launch_flag(perm_modes.FULL)}</code>")),
                self._build_perms_confirm_keyboard(sess))
            if card_id is None:
                return  # as above: no prompt went out, nothing to confirm
            self._perms_pending[sess.name] = {
                "target_mode": target_mode,
                "msg_id": card_id,
                "label": sess.label,
                "turn": sess.turn_key,
            }
            own(card_id)
        else:
            # Out of Full access (it narrows): at once, no confirmation.
            await show(tr.html(MODE_SWITCHING, session=sess.label,
                               mode=Safe(tr.html(mode_label(target_mode)))))

            async def _edit(text, **kw):
                await show(text, kw.get("reply_markup"))

            await self._do_perms_switch_via_fn(
                sess, target_mode, _edit, chat_id=chat_id, announce=False, by=by)

    async def _handle_whoami(self, update: Update,
                             ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /whoami — show the caller their resolved identity +
        effective policy (role + merged deny/allow list). Replies only
        to the calling chat; never reveals other scopes' data."""
        if not await self._authorize(update, allow_read_only=True):
            return

        tr = i18n.for_update(update)

        def yes_no(value: bool) -> Safe:
            return Safe(tr.html(WHOAMI_YES if value else WHOAMI_NO))

        def tools(names, empty: Msg) -> str:
            # Tool names as they are; only the empty list is words.
            return tr.join(names) if names else Safe(tr.html(empty))

        if self.scopes is not None:
            from aipager.policy_snapshot import resolve_snapshot
            chat = update.effective_chat
            tg_user = update.effective_user
            scope = self._scope_for(chat.id if chat else None)
            member = self._member_in_scope(scope, tg_user.id if tg_user else None)
            if member is None:
                await reply_text(update.message, tr.text(WHOAMI_NOT_MEMBER))
                return
            role = self.policy.get_role(member.role)
            snap = resolve_snapshot(role, scope, member)
            lines = [
                tr.html(WHOAMI_HEAD, member=Command(f"@{member.label}"), scope=scope.label),
                tr.html(WHOAMI_ROLE, role=member.role),
                tr.html(WHOAMI_BYPASS_SAFETY, value=yes_no(snap["bypass_safety"])),
                tr.html(WHOAMI_BYPASS_ROLE_DENIES,
                        value=yes_no(bool(role and role.bypass_role_denies))),
                tr.html(WHOAMI_CAN_MANAGE,
                        value=yes_no(getattr(role, "can_manage", False) is True)),
                tr.html(WHOAMI_EFFECTIVE_DENY, tools=tools(snap["deny_tools"], WHOAMI_NONE)),
                tr.html(WHOAMI_EFFECTIVE_ALLOW, tools=tools(snap["allow_tools"], WHOAMI_ANY)),
            ]
            await reply_text(update.message,
                "\n".join(lines), parse_mode="HTML")
            return

        if self.team is not None:
            tg_user = update.effective_user
            member = self.team.get(tg_user.id) if tg_user else None
            if member is None:
                await reply_text(update.message, tr.text(WHOAMI_NOT_ALLOWED))
                return
            lines = [
                tr.html(WHOAMI_TEAM_HEAD, member=Command(f"@{member.label}")),
                tr.html(WHOAMI_ROLE, role=member.role.value),
                tr.html(WHOAMI_TEAM_DENY,
                        tools=tools(self.team.rules.deny_tools, WHOAMI_NONE)),
            ]
            await reply_text(update.message, "\n".join(lines), parse_mode="HTML")
            return

        await reply_text(update.message, tr.text(WHOAMI_PERSONAL))

    def _target(self, update) -> TrackedSession | None:
        """Where an unaddressed message from whoever sent *update* goes.
        In a group each person has their own (roadmap 8.90): a message
        never lands in the session another member last talked to. A DM
        is the chat's target, as before."""
        return self.registry.target_for(
            calling_chat_id(update), calling_user_id(update))

    def _set_target(self, name: str, update) -> None:
        """*update*'s sender made *name* their target by their own action
        (a send, a switch, a reply). The chat's target moves as before."""
        self.registry.set_target(
            name, calling_chat_id(update), calling_user_id(update))

    async def _ask_which_session(self, update, *, resend=None) -> bool:
        """A group member with no target of their own while the chat has
        several live sessions is asked which one, and nothing is sent
        (roadmap 8.90). True when it asked.

        ``resend(sess)``: sends the message that was asked about to the
        session the person picks (roadmap 8.94i). A tap on one of the
        card's session buttons then sends it there as well as making it
        their target; the card is theirs alone (``card_owner``)."""
        chat_id = calling_chat_id(update)
        user_id = calling_user_id(update)
        if user_id is None or not group_intake.is_group_chat(chat_id):
            return False
        live = sorted(
            (s for s in self.registry.all_sessions(chat_id).values()
             if s.label and s.status != Status.GONE),
            key=lambda s: (s.label.lower(), s.name))
        tr = i18n.for_update(update)
        ended = self.registry.ended_target_for(chat_id, user_id)
        if ended is not None:
            # Roadmap 8.91g: their own session has ended. The only live
            # session left may be another member's, so nothing is sent:
            # they pick one, or resume theirs.
            question = tr.text(ENDED_WHICH, session=ended.label)
            if resend is not None and ended.claude_session_id:
                # Resume brings the session back but sends nothing.
                question += "\n" + tr.text(RESUME_THEN_RESEND)
            sent = await reply_text(
                update.message, question,
                reply_markup=self._ended_target_keyboard(chat_id, live, ended))
        elif len(live) < 2:
            return False
        else:
            sent = await reply_text(
                update.message,
                tr.text(WHICH_REPLY, command=Command(f"/{live[0].label}")),
                reply_markup=session_parity.session_picker(
                    self, chat_id, live, "talk", glyph="✍️ ", user_id=user_id))
        card_owner.claim_sent(self, chat_id, sent, user_id, kind="ask", command="",
                              picker=True, who=update.effective_user)
        if resend is not None:
            held_message.hold(self, chat_id, getattr(sent, "message_id", None),
                              user_id, resend)
        return True

    def _ended_target_keyboard(self, chat_id, live, ended) -> InlineKeyboardMarkup:
        """The talk buttons for the chat's live sessions, then ▶️ Resume
        for the person's ended one when it can be resumed (the ⋮ menu's
        own `resume` verb and its gates), then Cancel."""
        rows = [[InlineKeyboardButton(
            f"✍️ {s.label}",
            callback_data=session_parity.session_cb(self, chat_id or 0, s, "talk"))]
            for s in live]
        tr = i18n.for_chat(chat_id)
        if ended.claude_session_id:
            rows.append([InlineKeyboardButton(
                tr.text(BTN_RESUME_LABEL, session=ended.label),
                callback_data=session_parity.session_cb(
                    self, chat_id or 0, ended, "resume"))])
        rows.append([InlineKeyboardButton(tr.text(session_parity.PICK_CANCEL),
                                          callback_data="_:pick:cancel")])
        return InlineKeyboardMarkup(rows)

    def _own_text(self, update: Update) -> str:
        """The message's text without this bot's own mentions, stripped:
        what a command or the text router reads (roadmap 8.84). In a group
        a keyboard tap's marker goes too (8.91f): the router hands a
        tapped `▫️ stop` or `▫️ new` to the same handlers as `/stop` and
        `/new`, which read their argument from here."""
        text = group_intake.text_without_own_mentions(self, update.message)[0].strip()
        if group_intake.is_group_chat(calling_chat_id(update)):
            text = group_intake.keyboard_tap(text)[0]
        return text

    def _own_caption(self, msg) -> str:
        """A file's caption without this bot's own mentions (stripped only
        when one was removed, so a private chat's caption is unchanged)."""
        caption, mentioned = group_intake.text_without_own_mentions(self, msg, caption=True)
        return caption.strip() if mentioned else (caption or "")

    async def _handle_message(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle text messages — replies to notifications or /<label> commands."""
        if not await self._authorize(update):
            return
        # This bot's own mention is how a group addresses it, never part of
        # the message: "@bot fix the tests" is "fix the tests" (to Claude
        # Code `@word` is a file), "@bot status" is `status` (roadmap 8.84).
        raw, mentioned = group_intake.text_without_own_mentions(self, update.message)
        text = raw.strip()
        # Roadmap 8.91f: in a group a keyboard button's label carries a
        # marker, and only a marked label is a tap; the marker goes before
        # anything reads the text. A typed word is a keyboard word only in
        # a message addressed to the bot (a command, a reply to it, a
        # mention: "@bot stop" stops). A private chat is unchanged.
        keyboard_words = True
        if group_intake.is_group_chat(calling_chat_id(update)):
            text, tapped = group_intake.keyboard_tap(text)
            keyboard_words = tapped or group_intake.explicitly_addressed(
                self, update.message)
        if not text:
            if mentioned:
                await self._send_home(update)
            return
        own_name = group_intake.own_username(self)
        if mentioned and text.startswith("/") and len(text) > 1:
            # "@bot /stop": a command once the mention is gone, but no
            # command handler saw it. Never type it into a session.
            word = group_intake.drop_own_suffix(text[1:].split(maxsplit=1)[0], own_name)
            if word.lower() in group_intake.OWN_COMMANDS:
                await reply_text(update.message, i18n.for_update(update).text(
                    MENTION_ALONE, command=Command(f"/{word}")))
                return
        # Multi-step flows get first refusal on free text. Each returns
        # False unless it is genuinely mid-capture for THIS chat, so
        # ordinary session routing below is untouched outside those
        # narrow windows. The problem report note goes first: it is the
        # owner's newest question when it is open (report_flow).
        if await report_flow.maybe_handle_text(self, update, ctx, text):
            return
        if await new_flow.maybe_handle_text(self, update, ctx, text):
            return
        if await session_parity.maybe_handle_text(self, update, ctx, text):
            return

        # Keyboard navigation + button matching — MUST stay above /<label> handlers
        # because some buttons (e.g. "/compact") start with slash.
        kb_chat = calling_chat_id(update)
        # `keyboard_words` is False only for a group message that is neither
        # a marked tap nor addressed to the bot (8.91f). The intake gate
        # stops such a message first; this keeps a path around the gate
        # from turning a typed "stop" or "Clear" into a button.
        # One resolver for every keyboard text (bot/kbwords.py): it knows
        # every enabled language's labels.
        nav = kbwords.nav_action(text) if keyboard_words else None
        if nav == "nav:templates":
            await self._send_keyboard(level="templates", chat_id=kb_chat, tap=update)
            return
        if nav == "nav:commands":
            await self._send_keyboard(level="commands", chat_id=kb_chat, tap=update)
            return
        if nav == "nav:models":
            await self._send_keyboard(level="models", chat_id=kb_chat, tap=update)
            return
        if nav == "nav:app":
            # The label typed, or sent by a client too old to know a
            # `web_app` button (an older aipager's keyboard had one until
            # 2026-10-12). Answer with /app's inline button rather than
            # letting the label through as a prompt to Claude.
            await self._handle_app_cmd(update, ctx)
            return
        if nav == "nav:back":
            # Context-aware: go to parent of current level (models→commands, etc.)
            # The level is this chat's, and in a group this member's (8.91b).
            parent = KEYBOARD_PARENTS.get(
                self._keyboard_level_for(kb_chat, calling_user_id(update)), "main")
            await self._send_keyboard(level=parent, chat_id=kb_chat, tap=update)
            return

        # Quick template buttons inject their prompt into the active
        # session; Claude Code slash commands and model choices are sent
        # instantly, with no BUSY transition.
        # A default label in English or this chat's language only (the
        # language its keyboard is drawn in): a translated "Clear" runs
        # /clear, so another language's word stays a message for Claude.
        custom = (kbwords.custom_action(self, text, i18n.for_chat(kb_chat).code)
                  if keyboard_words else None)
        if custom is not None:
            kind, payload = custom
            if kind == "tpl":
                await self._send_template(update, payload, button=text)
            else:
                await self._send_command(update, payload)
            return

        # /<label> <prompt> — direct send
        if text.startswith("/") and " " in text:
            parts = text.split(" ", 1)
            # `/x1@thisbot fix it`: group clients add the bot's name (8.85).
            target_label = group_intake.drop_own_suffix(parts[0][1:], own_name)
            prompt_text = parts[1].strip()
            if target_label and prompt_text:
                if prompt_text.lower() == "stop":
                    await self._stop_by_label(update, target_label)
                    return
                await self._direct_send(update, target_label, prompt_text)
                return

        # Bare /<label> — switch active session (e.g. keyboard tap)
        if text.startswith("/") and " " not in text:
            target_label = group_intake.drop_own_suffix(text[1:], own_name)
            if target_label and target_label not in ("status", "stop", "kill"):
                await self._switch_session(update, target_label)
                return

        # Bare text matching keyboard buttons (no slash)
        if keyboard_words and " " not in text and not text.startswith("/"):
            word = kbwords.bare_word(text)
            if word == "status":
                await self._handle_status(update, ctx)
                return
            if word == "stop":
                await self._handle_stop_cmd(update, ctx)
                return
            if word == "kill":
                await self._handle_kill_cmd(update, ctx)
                return
            if word == "new":
                await self._handle_new_cmd(update, ctx)
                return
            # Check if it matches a known session label (scoped to caller)
            if self.registry.find_by_label(text, calling_chat_id(update)):
                await self._switch_session(update, text)
                return

        # Routing precedence:
        #   1-3. _resolve_reply_target: exact msg_map hit, a last_msg_id
        #        scan, then a text-guess — all scoped to the calling
        #        chat (design.md Part 0) so a message id colliding
        #        across two chats can never resolve into the wrong
        #        chat's session.
        #   4. last_active_session (the session the user most recently addressed)
        #   5. Error: nothing to route to → tell the user instead of silently dropping
        reply_to = update.message.reply_to_message
        chat_id = calling_chat_id(update)
        sess = self._resolve_reply_target(reply_to, chat_id)
        fallback_reason = ""
        if reply_to and not sess:
            fallback_reason = (
                f"reply target msg {reply_to.message_id} unknown — "
                "routed by last_active fallback"
            )
        if not sess:
            # This chat's own target: never another chat's session (F11),
            # and in a group the sender's own (8.90).
            sess = self._target(update)

        if not sess:
            bot_id = ctx.bot.id

            async def resend(chosen) -> bool:
                return await self._send_text_to(update, chosen, text, bot_id=bot_id)
            if await self._ask_which_session(update, resend=resend):
                return
            log.warning("Dropped text %r — no session to route to", text[:80])
            await reply_text(update.message, i18n.for_update(update).text(
                NO_SESSION, command=Command(LABEL_COMMAND)))
            return

        if fallback_reason:
            log.info("[%s] %s", sess.label, fallback_reason)
        await self._send_text_to(update, sess, text, bot_id=ctx.bot.id)

    async def _reply_session_missing(self, update: Update, message, sess) -> None:
        """The session's process is gone: say so (its internal name, as
        ever) under *message*, with a Resume button."""
        tr = i18n.for_update(update)
        await reply_text(message,
            tr.text(SESSION_MISSING, name=sess.name),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(
                tr.text(BTN_RESUME),
                callback_data=session_parity.session_cb(
                    self, calling_chat_id(update) or 0, sess, "resume"))]]),
        )

    async def _send_text_to(self, update: Update, sess: TrackedSession, text: str,
                            *, bot_id) -> bool:
        """Send a text message to *sess*, the session its routing chose (or
        the one its sender picked on "Which session?", roadmap 8.94i): its
        reply context, the sender's identity and rights, and every gate.
        True when it went there (sent, or held for later)."""
        chat_id = calling_chat_id(update)
        self._set_target(sess.name, update)  # user is talking to this session now
        asyncio.create_task(self._maybe_update_bot_name(sess.name))

        if not await inject.is_alive(sess.name):
            await self._reply_session_missing(update, update.message, sess)
            return False

        reply_context = self._build_reply_context(
            update.message, sess, bot_id=bot_id,
            allow_file=sess.status != Status.BUSY,
        )

        if await self._refuse_admin_command(update, text):
            return False
        held = await self._hold_for_open_dialog(update, sess, text, reply_context)
        if held:
            return held is not HOLD_DROPPED

        # Every inbound message injects immediately now, regardless of
        # Status.BUSY (design.md "queue handoff") — Claude keeps its own
        # not-yet-picked-up queue, matched by the UserPromptSubmit hook
        # at pick-up. `_direct_send`/`_send_command` have injected into a
        # BUSY session's pty all along; this is that same behaviour.
        # R1 (design.md "turn anchor follows consumption"): the reply
        # target itself moves only when this message starts a turn from
        # IDLE — `_adopt_trigger` no-ops the target (but always sets
        # `last_prompt` when not-BUSY) while BUSY.
        self._adopt_trigger(sess, update.message.message_id, text,
            driver_user_id=driver_id_from_update(update))
        self._mark_driver(sess, update)
        self.registry.track_message(update.message.message_id, sess.name, chat_id or 0)
        self.registry.mark_dirty()
        was_busy = sess.status is Status.BUSY
        ok = await self._inject_prompt(
            sess, text, reply_context,
            msg_id=update.message.message_id, chat_id=chat_id,
            driver_user_id=driver_id_from_update(update),
        )
        if ok:
            await self._react(update, reactions.HANDED_OFF)
            self.registry.transition(sess.name, Status.BUSY)
            await self._card_for_injected(sess, was_busy=was_busy)
            log.info("[%s] Sent text: %s", sess.label, text[:80])
            return True
        if ok is PROMPT_REFUSED:
            await self._reply_needs_admin(update)
        else:
            await reply_text(update.message, i18n.for_update(update).text(
                SEND_FAILED, session=sess.label))
        return False

    async def _handle_voice(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle voice messages — transcribe via local Whisper, inject as prompt.

        Requires the optional ``aipager[voice]`` extra. When unavailable
        we tell the user once and bail out. On success we send back the
        transcript (so the user can verify what we heard) and inject it
        into the active session like any other text prompt.
        """
        if not await self._authorize(update):
            return
        msg = update.message
        if not msg.voice:
            return
        tr = i18n.for_update(update)

        # Voice extra installed?
        from aipager.bot import voice
        if not voice.is_available():
            # Offer to install it remotely — user might be away from
            # their terminal. The callback handler does the actual work.
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    tr.text(MEDIA_BTN_INSTALL), callback_data="__voice__:install"),
                InlineKeyboardButton(
                    tr.text(MEDIA_BTN_CANCEL), callback_data="__voice__:cancel"),
            ]])
            await reply_text(msg, tr.text(MEDIA_NEEDS_EXTRA, install=200, model=74),
                             reply_markup=keyboard)
            return

        # Pre-size check (Telegram bot file-download limit, ~20 MB).
        size = msg.voice.file_size or 0
        if size > TELEGRAM_BOT_DOWNLOAD_LIMIT_BYTES:
            mb = size / (1024 * 1024)
            limit_mb = TELEGRAM_BOT_DOWNLOAD_LIMIT_BYTES // (1024 * 1024)
            await reply_text(msg, tr.text(MEDIA_VOICE_TOO_BIG, size=tr.num(f"{mb:.1f}"),
                                          limit=limit_mb))
            return

        # Download the .ogg file
        save_path = None
        try:
            # This chat's own private folder (aipager/uploads.py).
            folder = uploads.ensure_chat_folder(
                FILE_DOWNLOAD_DIR, _upload_chat(update))
            save_path = uploads.reserve_file(folder, f"{int(time.time())}_voice.ogg")
            tg_file = await msg.voice.get_file()
            await tg_file.download_to_drive(custom_path=str(save_path))
            uploads.seal_file(save_path)
        except Exception:
            log.warning("Voice download failed", exc_info=True)
            if save_path is not None:
                save_path.unlink(missing_ok=True)  # the empty file it reserved
            await reply_text(msg, tr.text(MEDIA_DOWNLOAD_FAILED))
            return

        # Acknowledge so the user knows we're working
        ack_msg = await reply_text(msg, tr.html(MEDIA_TRANSCRIBING), parse_mode="HTML")

        try:
            text = await voice.transcribe(str(save_path))
        except voice.VoiceUnavailable as e:
            # The missing extra by its id, in the chat's language; anything
            # else as it was said (English).
            why = (Safe(voice.not_installed_text(tr))
                   if getattr(e, "reason_id", "") == "not_installed" else str(e))
            await edit_message(ack_msg, tr.text(WARNING_LINE, text=why))
            return
        except Exception as e:
            log.warning("transcription failed", exc_info=True)
            await edit_message(ack_msg, tr.text(MEDIA_TRANSCRIBE_FAILED, error=str(e)))
            return
        finally:
            # Clean up the audio file once we've transcribed it
            save_path.unlink(missing_ok=True)

        if not text:
            await edit_message(ack_msg, tr.text(MEDIA_NO_SPEECH))
            return

        # Show the transcript to the user (as heard: never translated).
        await edit_message(ack_msg, tr.html(MEDIA_HEARD, text=text), parse_mode="HTML")

        # Inject the transcript as if it were a regular text reply.
        # Route to the same session that bare text would (last_active or
        # reply target), reusing _handle_message's logic. Build a
        # lightweight shim Update so we don't duplicate routing code.
        # Simpler: write the text into the existing message and dispatch.
        # Cleanest: invoke the same logic inline.
        await self._dispatch_voice_transcript(update, text, ctx)

    async def _dispatch_voice_transcript(
        self, update: Update, transcript: str,
        ctx: ContextTypes.DEFAULT_TYPE | None = None,
    ) -> None:
        """Inject a voice-transcript into the active session as a prompt.

        Mirrors the routing precedence of ``_handle_message`` (reply
        target → last_active_session) so the user's voice behaves like
        their typed text would.
        """
        # The same two capture hooks `_handle_message` runs before any
        # session routing, in the same order. Without them a spoken answer
        # to "what should this session be called?" did not merely fail to
        # reach the wizard — it was injected into the active session as a
        # prompt, so Claude got asked "my project two" while the wizard sat
        # waiting. Both hooks take the text as an argument (neither reads
        # `update.message.text`), so a transcript serves them unchanged.
        if await new_flow.maybe_handle_text(self, update, ctx, transcript):
            return
        if await session_parity.maybe_handle_text(self, update, ctx, transcript):
            return

        reply_to = update.message.reply_to_message
        chat_id = calling_chat_id(update)
        sess = self._resolve_reply_target(reply_to, chat_id)
        if not sess:
            sess = self._target(update)
        if not sess:
            async def resend(chosen) -> bool:
                return await self._send_transcript_to(update, chosen, transcript)
            if await self._ask_which_session(update, resend=resend):
                return
            await reply_text(update.message, i18n.for_update(update).text(
                VOICE_NO_SESSION, command=Command(LABEL_COMMAND)))
            return
        await self._send_transcript_to(update, sess, transcript)

    async def _send_transcript_to(self, update: Update, sess: TrackedSession,
                                  transcript: str) -> bool:
        """:meth:`_send_text_to` for a voice message's transcript. True when
        it went there (sent, or held for later)."""
        chat_id = calling_chat_id(update)
        self._set_target(sess.name, update)
        asyncio.create_task(self._maybe_update_bot_name(sess.name))

        if not await inject.is_alive(sess.name):
            await self._reply_session_missing(update, update.message, sess)
            return False

        reply_context = self._build_reply_context(
            update.message, sess, bot_id=self._app.bot.id,
            allow_file=sess.status != Status.BUSY,
        )

        if await self._refuse_admin_command(update, transcript):
            return False
        held = await self._hold_for_open_dialog(update, sess, transcript, reply_context)
        if held:
            return held is not HOLD_DROPPED

        # Injects immediately regardless of Status.BUSY — see the same
        # comment in _handle_message (design.md "queue handoff"). R1:
        # the reply target itself only moves via _adopt_trigger.
        self._adopt_trigger(sess, update.message.message_id, transcript,
            driver_user_id=driver_id_from_update(update))
        self._mark_driver(sess, update)
        self.registry.track_message(update.message.message_id, sess.name, chat_id or 0)
        self.registry.mark_dirty()
        was_busy = sess.status is Status.BUSY
        ok = await self._inject_prompt(
            sess, transcript, reply_context,
            msg_id=update.message.message_id, chat_id=chat_id,
            driver_user_id=driver_id_from_update(update),
        )
        if ok:
            # The same lifecycle as typed text: 👀 now, 👍 when Claude takes
            # it. The "🎙️ Transcribing…" reply already covers the stage
            # before this; a reaction for it would cost a fourth call.
            await self._react(update, reactions.HANDED_OFF)
            self.registry.transition(sess.name, Status.BUSY)
            await self._card_for_injected(sess, was_busy=was_busy)
            log.info("[%s] Voice injected: %r", sess.label, transcript[:80])
            return True
        if ok is PROMPT_REFUSED:
            await self._reply_needs_admin(update)
        else:
            await reply_text(update.message, i18n.for_update(update).text(
                INJECT_FAILED, session=sess.label))
        return False

    async def _handle_file(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle photo/document messages — download file and inject prompt.

        A lone file is injected right here. An album item (``media_group_id``
        set) is parked in ``self._albums`` instead, and the whole group goes
        out as ONE prompt from ``_flush_album`` once no further item has
        arrived for ``_ALBUM_SETTLE_SECONDS``.
        """
        if not await self._authorize(update):
            return
        msg = update.message

        # Up-front size check — Telegram's bot API tops out at 20 MB on
        # download, so we'd rather reject with a clear message than try
        # and fail with a vague "Failed to download" later.
        file_size = None
        if msg.document:
            file_size = msg.document.file_size
        elif msg.photo:
            # Largest size is last; treat its file_size as the cap
            file_size = msg.photo[-1].file_size if msg.photo[-1] else None
        if file_size and file_size > TELEGRAM_BOT_DOWNLOAD_LIMIT_BYTES:
            mb = file_size / (1024 * 1024)
            limit_mb = TELEGRAM_BOT_DOWNLOAD_LIMIT_BYTES // (1024 * 1024)
            tr = i18n.for_update(update)
            await reply_text(msg, tr.text(MEDIA_FILE_TOO_BIG, size=tr.num(f"{mb:.1f}"),
                                          limit=limit_mb))
            return

        if msg.photo:
            media = msg.photo[-1]  # largest size is last
            display_name = "photo.jpg"
        elif msg.document:
            media = msg.document
            raw_name = msg.document.file_name or "document"
            display_name = Path(raw_name).name or "document"
        else:
            return

        # An album item is registered BEFORE its download so the settle
        # timer armed by the previous item cannot fire while this one is
        # still coming down. Its outcome is recorded in the ``finally`` so
        # the group's in-flight counter balances even on cancellation.
        album = self._album_register(update, ctx) if msg.media_group_id else None

        save_path: Path | None = None
        try:
            # This chat's own private folder (aipager/uploads.py): 0700,
            # the file 0600, and another chat's restricted turns are kept
            # off it by their policy snapshot.
            folder = uploads.ensure_chat_folder(
                FILE_DOWNLOAD_DIR, _upload_chat(update))
            target = uploads.reserve_file(
                folder, f"{int(time.time())}_{display_name}")
            try:
                await _download_with_retry(media, target, display_name=display_name)
                uploads.seal_file(target)
            except BaseException:
                target.unlink(missing_ok=True)  # the empty file it reserved
                raise
            save_path = target
        except Exception:
            log.warning("File download failed: %s", display_name, exc_info=True)
        finally:
            if album is not None:
                self._album_record(album, msg, save_path, display_name)

        if album is not None:
            return  # _flush_album injects the whole group once it settles
        if save_path is None:
            await reply_text(msg, i18n.for_update(update).text(
                MEDIA_FILE_DOWNLOAD_FAILED, name=display_name))
            return

        await self._inject_file_prompt(
            update, ctx, self._own_caption(msg), [save_path],
            all_photos=bool(msg.photo), log_name=save_path.name,
        )

    async def _inject_file_prompt(
        self, update: Update, ctx: ContextTypes.DEFAULT_TYPE, caption: str,
        paths: list[Path], *, all_photos: bool, log_name: str,
    ) -> None:
        """Route an upload to a session and inject its prompt — the shared
        tail of the lone-file and album paths.

        A caption whose first token is ``/<label>`` picks the session the
        way ``/<label> <prompt>`` does for text and is stripped from the
        prompt; the wording itself is ``_file_prompt``'s alone. A label
        nothing answers to is refused here (the text path's reply) and
        nothing is injected — an upload never hands claude a prompt that
        begins with ``/``."""
        msg = update.message
        chat_id = calling_chat_id(update)

        label, caption = _split_caption_target(caption, group_intake.own_username(self))
        if label is not None:
            sess = await self._session_for_typed_label(update, label)
            if sess is None:
                return
        else:
            # Same routing precedence as _handle_message: the reply
            # pointer, then the last active session.
            sess = self._resolve_reply_target(msg.reply_to_message, chat_id)
            if not sess:
                sess = self._target(update)
        prompt = _file_prompt(caption, paths, all_photos=all_photos)

        if not sess:
            bot_id = ctx.bot.id

            async def resend(chosen) -> bool:
                return await self._send_file_prompt_to(
                    update, chosen, prompt, bot_id=bot_id, log_name=log_name)
            if await self._ask_which_session(update, resend=resend):
                return
            await reply_text(msg, i18n.for_update(update).text(
                FILE_NO_SESSION, command=Command(LABEL_COMMAND)))
            return
        await self._send_file_prompt_to(update, sess, prompt, bot_id=ctx.bot.id,
                                        log_name=log_name)

    async def _send_file_prompt_to(self, update: Update, sess: TrackedSession,
                                   prompt: str, *, bot_id, log_name: str) -> bool:
        """:meth:`_send_text_to` for an upload's prompt (the files are
        already saved). True when it went there (sent, or held for later)."""
        msg = update.message
        chat_id = calling_chat_id(update)
        self._set_target(sess.name, update)
        asyncio.create_task(self._maybe_update_bot_name(sess.name))

        if not await inject.is_alive(sess.name):
            await self._reply_session_missing(update, msg, sess)
            return False

        reply_context = self._build_reply_context(
            msg, sess, bot_id=bot_id,
            allow_file=sess.status != Status.BUSY,
        )

        if await self._refuse_admin_command(update, prompt):
            return False
        held = await self._hold_for_open_dialog(update, sess, prompt, reply_context)
        if held:
            return held is not HOLD_DROPPED

        # Injects immediately regardless of Status.BUSY — see the same
        # comment in _handle_message (design.md "queue handoff"). R1:
        # the reply target itself only moves via _adopt_trigger.
        self._adopt_trigger(sess, msg.message_id, prompt,
            driver_user_id=driver_id_from_update(update))
        self._mark_driver(sess, update)
        self.registry.track_message(msg.message_id, sess.name, chat_id or 0)
        self.registry.mark_dirty()
        was_busy = sess.status is Status.BUSY
        ok = await self._inject_prompt(
            sess, prompt, reply_context,
            msg_id=msg.message_id, chat_id=chat_id,
            driver_user_id=driver_id_from_update(update),
        )
        if ok:
            await self._react(update, reactions.HANDED_OFF)
            self.registry.transition(sess.name, Status.BUSY)
            await self._card_for_injected(sess, was_busy=was_busy)
            log.info("[%s] File sent: %s", sess.label, log_name)
            return True
        if ok is PROMPT_REFUSED:
            await self._reply_needs_admin(update)
        else:
            await reply_text(msg, i18n.for_update(update).text(SEND_FAILED, session=sess.label))
        return False

    # ---- Albums (media groups) --------------------------------------------

    def _album_register(
        self, update: Update, ctx: ContextTypes.DEFAULT_TYPE,
    ) -> _PendingAlbum:
        """Park an arriving album item, creating its group on the first
        one. Cancels the group's settle timer; ``_album_record`` re-arms
        it once this item's download has finished."""
        now = time.monotonic()
        self._sweep_albums(now)
        key = (calling_chat_id(update) or 0, update.message.media_group_id)
        album = self._albums.get(key)
        if album is None:
            album = _PendingAlbum(key=key, update=update, ctx=ctx, touched=now)
            self._albums[key] = album
        album.touched = now
        album.pending += 1
        if album.settle_task is not None:
            album.settle_task.cancel()
            album.settle_task = None
        return album

    def _album_record(
        self, album: _PendingAlbum, msg, save_path: Path | None, display_name: str,
    ) -> None:
        """Note one item's outcome and arm the settle timer once no
        download of the group is still running."""
        album.touched = time.monotonic()
        album.pending -= 1
        if save_path is not None:
            album.paths.append(save_path)
        else:
            album.failed.append(display_name)
        caption = self._own_caption(msg)
        if caption and not album.caption:
            album.caption = caption
        if not msg.photo:
            album.all_photos = False
        if album.pending == 0:
            album.settle_task = asyncio.create_task(self._settle_album(album))

    async def _settle_album(self, album: _PendingAlbum) -> None:
        """Wait out the settle window, then flush the group. Every later
        arrival cancels this task and ``_album_record`` starts a new one,
        so reaching the flush means the group has gone quiet."""
        await asyncio.sleep(_ALBUM_SETTLE_SECONDS)
        if album.pending:  # defensive: never flush a half-downloaded group
            return
        if self._albums.get(album.key) is album:
            del self._albums[album.key]
        try:
            await self._flush_album(album)
        except Exception:
            log.exception("Album %s: flush failed", album.key[1])

    async def _flush_album(self, album: _PendingAlbum) -> None:
        """Inject the whole group as ONE prompt, with ONE reply for any
        items that failed every download attempt — never one per item."""
        if album.paths:
            await self._inject_file_prompt(
                album.update, album.ctx, album.caption, album.paths,
                all_photos=album.all_photos,
                log_name=f"album of {len(album.paths)}",
            )
        if album.failed:
            tr = i18n.for_update(album.update)
            one = len(album.failed) == 1
            if album.paths:
                key = MEDIA_ALBUM_LEFT_OUT_ONE if one else MEDIA_ALBUM_LEFT_OUT_MANY
            else:
                key = MEDIA_ALBUM_FAILED_ONE if one else MEDIA_ALBUM_FAILED_MANY
            await reply_text(album.update.message, tr.text(key, names=tr.join(album.failed)))

    def _sweep_albums(self, now: float) -> None:
        """Drop groups idle for longer than ``_ALBUM_MAX_AGE_SECONDS``."""
        for key, album in list(self._albums.items()):
            if now - album.touched > _ALBUM_MAX_AGE_SECONDS:
                if album.settle_task is not None:
                    album.settle_task.cancel()
                del self._albums[key]
                log.warning(
                    "Album %s: dropped after %.0fs idle (%d files never sent)",
                    key[1], now - album.touched, len(album.paths),
                )

    async def _send_template(
        self, update: Update, prompt_text: str, *, button: str = "",
    ) -> None:
        """Inject a quick-template prompt into this chat's target session
        (the sender's own in a group), and say which session got it."""
        sess = self._target(update)
        tr = i18n.for_update(update)

        if not sess or sess.status == Status.GONE:
            if await self._ask_which_session(update):
                return
            await reply_text(update.message, tr.text(NO_ACTIVE))
            return

        if not await inject.is_alive(sess.name):
            await reply_text(update.message, tr.text(SESSION_MISSING, name=sess.name))
            return

        if await self._refuse_admin_command(update, prompt_text):
            return
        if await self._hold_for_open_dialog(update, sess, prompt_text):
            return

        # Injects immediately regardless of Status.BUSY — see the same
        # comment in _handle_message (design.md "queue handoff"). R1:
        # the reply target itself only moves via _adopt_trigger.
        template_chat_id = calling_chat_id(update)
        self._adopt_trigger(sess, update.message.message_id, prompt_text,
            driver_user_id=driver_id_from_update(update))
        self.registry.track_message(update.message.message_id, sess.name,
                                    template_chat_id or 0)
        self.registry.mark_dirty()
        was_busy = sess.status is Status.BUSY
        ok = await self._inject_prompt(
            sess, prompt_text,
            msg_id=update.message.message_id, chat_id=template_chat_id,
            driver_user_id=driver_id_from_update(update),
        )
        if ok:
            await self._react(update, reactions.HANDED_OFF)
            # Before the card, so the line sits under the tap, not under
            # the Working card or the answer it becomes.
            await reply_text(update.message, _sent_to_line(
                "📝", button or prompt_text, sess.label, tr))
            self.registry.transition(sess.name, Status.BUSY)
            await self._card_for_injected(sess, was_busy=was_busy)
            log.info("[%s] Template sent: %s", sess.label, prompt_text[:80])
        elif ok is PROMPT_REFUSED:
            await self._reply_needs_admin(update)
        else:
            await reply_text(update.message, tr.text(SEND_FAILED, session=sess.label))

    async def _send_command(self, update: Update, command_text: str) -> None:
        """Inject an instant Claude Code slash command (no BUSY transition).

        Unlike _send_template(), this does NOT transition to BUSY or start
        animation. Slash commands like /model, /cost, /context complete
        instantly and produce no Claude response.

        It names the session it went to (roadmap 8.90): a Clear tapped in a
        group used to wipe whichever session answered last, unannounced.
        """
        sess = self._target(update)
        tr = i18n.for_update(update)

        if not sess or sess.status == Status.GONE:
            if await self._ask_which_session(update):
                return
            await reply_text(update.message, tr.text(NO_ACTIVE))
            return

        if not await inject.is_alive(sess.name):
            await reply_text(update.message, tr.text(SESSION_MISSING, name=sess.name))
            return

        # /clear is destructive — refuse while session is actively working
        if command_text == "/clear" and sess.status == Status.BUSY:
            await reply_text(update.message, tr.text(CANT_CLEAR_BUSY))
            return

        # /model follows the Mini App picker's rule exactly (roadmap 8.35
        # R2): refused while a turn runs or a prompt is open, not held.
        # Claude Code runs /model mid-turn rather than after it.
        is_model_switch = command_text.startswith("/model ")
        if is_model_switch and sess.parked_job:
            # Never held for a parked session either: it would be typed
            # after the bring-back with none of the checks below.
            await reply_text(update.message,
                             tr.html(BG_REFUSED_PARKED, session=sess.label),
                             parse_mode="HTML")
            return
        if is_model_switch:
            refusal = model_switch_refusal(sess)
            if refusal is not None:
                # By its code, in the chat's language (the Mini App keeps
                # the English detail).
                await reply_text(update.message, tr.text(
                    REFUSAL_FRAME, session=sess.label,
                    reason=Safe(model_refusal_text(tr, *refusal))))
                return

        # A /model is never HELD for later: queued, it would be typed at
        # drain time with none of the checks above. The hold's other
        # condition — another member's message still outstanding (team
        # mode) — therefore refuses it too.
        if is_model_switch and mixed_sender_note_outstanding(sess, update):
            await reply_text(update.message, tr.text(
                REFUSAL_FRAME, session=sess.label,
                reason=Safe(tr.text(MODEL_SWITCH_OTHER_SENDER_REASON))))
            return

        if await self._hold_for_open_dialog(update, sess, command_text):
            return

        was_busy = sess.status == Status.BUSY
        previous_model = sess.model_name or ""
        if is_model_switch:
            # No re-check needed here, unlike _switch_model_core: the
            # refusal above already ran after the liveness probe, and the
            # hold check does not yield unless it holds.
            previous_model = mark_model_switch_sent(
                sess, command_text.split(" ", 1)[1].strip(),
            )
        ok = await self._inject_prompt(
            sess, command_text,
            msg_id=update.message.message_id, chat_id=calling_chat_id(update),
            driver_user_id=driver_id_from_update(update),
        )
        if ok:
            # Enter on Claude Code's idle prompt runs the command there and
            # then, and a local one (`/model`) fires no hook that could
            # ever say so: the injection is where it is done — 👌, one
            # call. While a turn runs Claude Code queues it instead: 👀,
            # then 👌 when that turn ends (`_mark_ran_commands`), or 👍 /
            # 🤷 if its pick-up named it. (✅ was never an allowed bot
            # reaction — it silently failed, 8.33.)
            await self._react(update, reactions.HANDED_OFF if was_busy
                              else reactions.ACK)
            if is_model_switch:
                # A later relaunch (/perms, restart, resume) keeps it.
                sess.launch_model = command_text.split(" ", 1)[1].strip()
                self.registry.mark_dirty()
            if not was_busy:
                # Ran, so its note is no queued message: let it expire
                # (roadmap 8.37 - a lingering one made /clearqueue type
                # Escape into a later turn). Queued, it waits for 👌.
                from aipager.policy_snapshot import mark_command_notes_ran
                mark_command_notes_ran(
                    sess.name, msg_id=update.message.message_id,
                    chat_id=calling_chat_id(update),
                )
            # Explicit feedback for model changes
            if is_model_switch:
                model_arg = command_text.split(" ", 1)[1]
                header = tr.html(MODEL_SWITCHED, session=sess.label, model=model_arg)
                sent = await reply_text(update.message, header, parse_mode="HTML")
                # The same message then shows the model the session
                # actually reports, once its statusline says so.
                task = asyncio.create_task(
                    self._confirm_model_feedback(
                        sent, sess, previous_model, header,
                        user_id=driver_id_from_update(update),
                        chat_id=calling_chat_id(update)),
                )
                _MODEL_CONFIRM_TASKS.add(task)
                task.add_done_callback(_MODEL_CONFIRM_TASKS.discard)
            else:
                # The model line above names it already.
                await reply_text(update.message, _sent_to_line(
                    "🧹" if command_text == "/clear" else "↪️",
                    command_text, sess.label, tr))
            log.info("[%s] Command sent: %s", sess.label, command_text)
        else:
            if is_model_switch:
                clear_model_switch_pending(sess)
            if ok is PROMPT_REFUSED:
                await self._reply_needs_admin(update)
            else:
                await reply_text(update.message, tr.text(SEND_FAILED, session=sess.label))

    async def _confirm_model_feedback(
        self, message, sess, previous_model: str, header: str,
        *, timeout: float | None = None, user_id: int | None = None,
        chat_id: int | None = None,
    ) -> None:
        """Edit a ``/model`` feedback message once the switch is known.

        Appends the model the statusline now reports, or — when nothing
        changed within the wait — says it was not confirmed. One edit,
        through the same flood-aware helper every other edit uses; a
        muted chat or a reply that was never sent simply skips it. Either
        way a member's switch first gets Claude Code's default put back
        (``session_ops.settle_default``).
        """
        new_model = await await_model_change(sess, previous_model, timeout=timeout)
        # Whatever happens to the reply: a member's switch must not stay the
        # owner's default (session_ops.settle_default).
        saved = await settle_default(self, sess, user_id, chat_id, confirmed=bool(new_model))
        if message is None or message is MUTED:
            return
        # The session's chat, where the reply is (one argument fewer than a
        # stand-in may take: resolved here).
        tr = i18n.for_sess(sess)
        if new_model:
            text = f"{header}\n{tr.html(MODEL_NOW, model=new_model)}"
            if saved:
                # As in the terminal: Claude Code made it the default too
                # (a member's switch puts the old default back instead).
                text += f"\n{tr.html(MODEL_DEFAULT_SAVED)}"
        else:
            text = f"{header}\n{tr.html(WARNING_LINE, text=tr.text(MODEL_UNCONFIRMED))}"
        try:
            await edit_message(message, text, parse_mode="HTML")
        except Exception:
            log.debug("[%s] model feedback edit failed", sess.label, exc_info=True)

    async def _session_for_typed_label(
        self, update: Update, label: str,
    ) -> TrackedSession | None:
        """The session an operator-typed ``/<label>`` names, resolved the
        way ``_direct_send`` resolves it: the registry (scoped to the
        calling chat, finished twins included), then discovery of a live
        ``claude-<label>`` adopted under the typed label. Replies with
        the reason and returns ``None`` when nothing usable answers to
        it, so callers only have to bail out."""
        tr = i18n.for_update(update)
        sess = self.registry.find_by_label(
            label, calling_chat_id(update), include_gone=True)
        if sess is not None:
            if not await inject.is_alive(sess.name):
                await reply_text(update.message, tr.text(NOT_ALIVE, session=label))
                return None
            return sess
        session_name = f"claude-{label}"
        if await inject.is_alive(session_name):
            sess = self._adopt_by_typed_name(
                session_name, label, calling_chat_id(update))
            if sess is not None:
                return sess
        await reply_text(update.message, tr.text(UNKNOWN_SESSION, session=label))
        return None

    async def _direct_send(self, update: Update, target_label: str, prompt_text: str) -> None:
        """Send prompt directly to a session by label."""
        tr = i18n.for_update(update)
        sess = self.registry.find_by_label(
            target_label, calling_chat_id(update), include_gone=True)
        if sess is not None:
            name = sess.name
            if not await inject.is_alive(name):
                await reply_text(update.message, tr.text(NOT_ALIVE, session=target_label))
                return
            if await self._refuse_admin_command(update, prompt_text):
                return
            # Targeting is the operator's explicit choice and applies even
            # when the message itself is held, so it is recorded first.
            self._set_target(name, update)
            if await self._hold_for_open_dialog(update, sess, prompt_text):
                return
            # R1 (design.md "turn anchor follows consumption"): the
            # reply target itself only moves via _adopt_trigger.
            self._adopt_trigger(sess, update.message.message_id, prompt_text,
                driver_user_id=driver_id_from_update(update))
            self.registry.track_message(update.message.message_id, name,
                                        calling_chat_id(update) or 0)
            self.registry.mark_dirty()
            self.registry.last_active_session = name  # user explicitly targeted this session
            asyncio.create_task(self._maybe_update_bot_name(name))
            was_busy = sess.status is Status.BUSY
            ok = await self._inject_prompt(
                sess, prompt_text,
                msg_id=update.message.message_id, chat_id=calling_chat_id(update),
                driver_user_id=driver_id_from_update(update),
            )
            if ok:
                await self._react(update, reactions.HANDED_OFF)
                self.registry.transition(name, Status.BUSY)
                await self._card_for_injected(sess, was_busy=was_busy)
                log.info("[%s] Direct send: %s", target_label, prompt_text[:80])
            elif ok is PROMPT_REFUSED:
                await self._reply_needs_admin(update)
            else:
                await reply_text(update.message, tr.text(SEND_FAILED, session=target_label))
            return

        # Not found in registry — try session discovery
        session_name = f"claude-{target_label}"
        # Another chat's session is unknown here (roadmap 8.76), checked
        # before anything else so the reply does not even confirm it runs.
        if (await inject.is_alive(session_name)
                and not self._typed_name_foreign(
                    session_name, calling_chat_id(update))):
            if await self._refuse_admin_command(update, prompt_text):
                return
            new_sess = self._adopt_by_typed_name(
                session_name, target_label, calling_chat_id(update))
            if new_sess is None:
                await reply_text(update.message,
                                 tr.text(UNKNOWN_SESSION, session=target_label))
                return
            self._set_target(session_name, update)
            if await self._hold_for_open_dialog(update, new_sess, prompt_text):
                return
            # R1 (design.md "turn anchor follows consumption"): the
            # reply target itself only moves via _adopt_trigger.
            self._adopt_trigger(new_sess, update.message.message_id, prompt_text,
                driver_user_id=driver_id_from_update(update))
            self.registry.track_message(update.message.message_id, session_name,
                                        calling_chat_id(update) or 0)
            self.registry.mark_dirty()
            self.registry.last_active_session = session_name
            asyncio.create_task(self._maybe_update_bot_name(session_name))
            ok = await self._inject_prompt(
                new_sess, prompt_text,
                msg_id=update.message.message_id, chat_id=calling_chat_id(update),
                driver_user_id=driver_id_from_update(update),
            )
            if ok:
                await self._react(update, reactions.HANDED_OFF)
                self.registry.transition(session_name, Status.BUSY)
                await self._send_busy_and_animate(new_sess)
            elif ok is PROMPT_REFUSED:
                await self._reply_needs_admin(update)
            else:
                await reply_text(update.message, tr.text(SEND_FAILED, session=target_label))
        else:
            await reply_text(update.message, tr.text(UNKNOWN_SESSION, session=target_label))
