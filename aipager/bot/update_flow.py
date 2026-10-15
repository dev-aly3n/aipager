"""Admin self-update from Telegram and the Mini App (roadmap 8.36).

``/update`` offers one "Check for updates" button (roadmap 8.43). Tapping
it looks up both products and shows one line each, then ONE button that
updates only what has an update (Update aipager / Update Claude Code /
Update both). The Mini App's Updates block drives the SAME
:class:`UpdateManager` methods, and both surfaces take the offer from the
one function :func:`update_offer`, so the two cannot drift. A start always
goes through :meth:`UpdateManager.start_offer`, which re-derives the offer
from the last check rather than trusting the button. All real work
(spawning, fetching, the lock, the restart gate, the restart plan, the
marker) lives in :mod:`aipager.self_update`; this module is orchestration
and text.

Flood discipline: one status message per job, edited in place. Outcome
edits are ESSENTIAL; heartbeat and blocker edits are skippable ORNAMENTs,
sent only when their content changed and at most once per
``PROGRESS_EDIT_MIN_SECONDS``. Everything goes through the transport
seams, so a flood-muted chat receives nothing.

Callback data (short-callback family ``_``): ``_:up:chk``,
``_:up:go:<cc|ap|both>``, ``_:up:x``, ``_:up:rs`` (Restart now: run the
installed aipager), ``_:up:now:<job>``, ``_:up:wait:<job>``,
``_:up:stop:<job>``. The pre-8.43 ``_:up:cc``, ``_:up:ap`` and
``_:up:both`` only answer that the menu is out of date.

Restart now (a ``kind="restart"`` job) finishes an update without a
service unit: it waits for idle sessions like the systemd restart, then
asks :mod:`aipager.self_restart` to stop the daemon cleanly and exec the
installed aipager in place (a systemd daemon schedules its restart as
before).
"""

from __future__ import annotations

import asyncio
import html as html_mod
import logging
import os
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest

from aipager import _version, i18n, install_source, self_restart, self_update
from aipager.bot.flood import FloodMuted
from aipager.bot.flood_budget import PRIORITY_ORNAMENT, rate_limit_args
from aipager.bot.transport import (
    MUTED,
    SKIPPED,
    _message_chat_id,
    _send_with_retry,
    calling_chat_id,
    edit_message,
    edit_text,
    edit_text_at,
    reply_text,
    send_text,
    tapper_tr,
)
from aipager.i18n import Msg, Plural, Safe, Span

if TYPE_CHECKING:
    from aipager.bot import TelegramBot

log = logging.getLogger("aipager.bot.update_flow")

KINDS = ("claude", "aipager", "both")
CONTROLS = ("restart-now", "wait-more", "cancel")
TERMINAL_PHASES = frozenset({"done", "failed", "cancelled", "restart_scheduled"})
# Phases in which "Cancel" is still offered: nothing is being installed.
_CANCELLABLE = frozenset({"starting", "waiting_for_idle", "gate_timeout"})
# Phases in which an installer (or ``claude update``) may be writing to disk.
_INSTALLING = frozenset({"claude_updating", "upgrading"})
# A bound on each status lookup, beyond the per-request HTTP timeout.
STATUS_STEP_TIMEOUT_SECONDS = 20.0
# If we are still alive this long after scheduling a restart, it did not
# happen: release the lock and say so.
RESTART_WATCHDOG_SECONDS = 120.0
# At daemon shutdown, how long a running installer gets to finish before its
# process group is killed (systemd's TimeoutStopSec is 15 s in total).
SHUTDOWN_GRACE_SECONDS = 3.0
# The whole of UpdateManager.shutdown() — grace, kill, the job's last word,
# the helpers — fits in this, well under TimeoutStopSec=15, so the daemon's
# own registry save and bot stop still run before systemd's SIGKILL.
SHUTDOWN_DEADLINE_SECONDS = 8.0

DENIED_TEXT = Msg("toast.upd.denied", "🚫 Only an admin can update aipager.",
                  note="Reply and pop-up: someone who is not an admin tried /update or its "
                       "buttons")
STALE_TEXT = Msg("toast.upd.stale", "That update already finished",
                 note="Pop-up: a button of an update that has already ended was tapped")
# The pre-8.43 per-product buttons (``_:up:cc`` / ``ap`` / ``both``) and
# Mini App routes: they start nothing any more.
MENU_OUT_OF_DATE_TEXT = Msg("toast.upd.menu_old", "This menu is out of date, send /update again",
                            note="Pop-up: a button from an old /update menu was tapped; keep "
                                 "/update as it is")
CHECK_PROMPT_TEXT = Msg("upd.prompt",
                        "⬆️ <b>Updates</b>\n\nCheck whether aipager or Claude Code "
                        "has a newer version.",
                        note="/update: the reply above the Check for updates button")
CHECK_BUTTON_TEXT = Msg("btn.upd.check", "🔄 Check for updates",
                        note="/update button: look up the newest versions")
CHECKING_TEXT = Msg("toast.upd.checking", "Checking…",
                    note="Pop-up and message while /update looks up the newest versions")
UP_TO_DATE_TEXT = Msg("upd.summary.up_to_date", "Everything is up to date.",
                      note="/update check: nothing newer to install")
CHECK_FAILED_TEXT = Msg("upd.summary.check_failed",
                        "Nothing newer found, but a check failed. Try again later.",
                        note="/update check: one of the lookups failed")
# Something newer exists, but each such product can't be updated from here
# (its line says why); never "Everything is up to date." under that line.
CANT_UPDATE_HERE_TEXT = Msg("upd.summary.cant_here",
                            "An update is out, but it can't be installed from here.",
                            note="/update check: a newer version exists that this install "
                                 "cannot apply (the line above says why)")
OFFER_STALE_TEXT = Msg("upd.offer_stale",
                       "⚠️ This check is out of date. Check again before updating.",
                       note="/update: the Update button is older than the last check")
# How long an Update button (or the Mini App's) stays good after its check.
# Past this, or once any job has run, the tap asks for a fresh check.
OFFER_MAX_AGE_SECONDS = 600.0
# Callback verb <-> job kind for the one Update button.
_GO_CODES = {"cc": "claude", "ap": "aipager", "both": "both"}
_GO_VERBS = {kind: code for code, kind in _GO_CODES.items()}

# ---- what /update says (each line one whole sentence; versions, install
# kinds, reasons from the install probe and phase ids go in as data) ------

_AP_UNCHECKED = Msg("upd.line.ap_unchecked", "aipager {running} (couldn't check)",
                    note="/update check line: the newest aipager version could not be "
                         "looked up", eg={"running": "1.0.1"})
_AP_NEWER = Msg("upd.line.ap_newer", "aipager {running} → {latest}",
                note="/update check line: the running and the newer aipager version", eg={"running": "1.0.1", "latest": "1.0.2"})
_AP_NEWER_BLOCKED = Msg("upd.line.ap_newer_blocked",
                        "aipager {running} → {latest} (can't update from here: {reason})",
                        note="/update check line: newer aipager, but this install cannot be "
                             "upgraded; {reason} is the install's own explanation (English)",
                        eg={"running": "1.0.1", "latest": "1.0.2", "reason": "editable install"})
_AP_CURRENT = Msg("upd.line.ap_current", "aipager {running} (up to date)",
                  note="/update check line", eg={"running": "1.0.1"})
_AP_INSTALLED = Msg("upd.note.ap_installed",
                    "aipager {installed} is installed but not running yet.",
                    note="/update check: a newer aipager is on disk, the old one still runs",
                    eg={"installed": "1.0.2"})
_CC_UNKNOWN = Msg("upd.line.cc_unknown", "Claude Code (couldn't check)",
                  note="/update check line: the installed Claude Code version is unknown")
_CC_UNCHECKED = Msg("upd.line.cc_unchecked", "Claude Code {current} (couldn't check)",
                    note="/update check line", eg={"current": "2.1.300"})
_CC_NEWER = Msg("upd.line.cc_newer", "Claude Code {current} → {latest}",
                note="/update check line", eg={"current": "2.1.300", "latest": "2.1.310"})
_CC_NEWER_BLOCKED = Msg("upd.line.cc_newer_blocked",
                        "Claude Code {current} → {latest} (can't update from here: {reason})",
                        note="/update check line; {reason} is English text from the install",
                        eg={"current": "2.1.300", "latest": "2.1.310",
                            "reason": "npm prefix not writable"})
_CC_CURRENT = Msg("upd.line.cc_current", "Claude Code {current} (up to date)",
                  note="/update check line", eg={"current": "2.1.300"})
_LABEL_BOTH = Msg("btn.upd.both", "Update both",
                  note="/update button: update aipager and Claude Code")
_LABEL_AP = Msg("btn.upd.aipager", "Update aipager", note="/update button")
_LABEL_CC = Msg("btn.upd.claude", "Update Claude Code", note="/update button")
_RESTART_AUTO = Msg("upd.restart.auto", "Restart: automatic, once no turn is running.",
                    note="/update check: how aipager restarts after the update")
_RESTART_MANUAL = Msg("upd.restart.manual", "Restart: manual ({reason})",
                      note="/update check: aipager cannot restart itself; {reason} says why",
                      eg={"reason": "no service unit"})
_UNKNOWN_INSTALL = Msg("upd.reason.unknown_install", "unknown install",
                       note="Stands in for {reason} when the install gave no reason")
_YOURSELF = Msg("upd.reason.yourself", "restart it yourself",
                note="Stands in for {reason} in 'Restart: manual ({reason})' and "
                     "'Restart needed: {reason}'")
_HEADING = Msg("upd.heading", "⬆️ <b>Updates</b>", note="/update check: the title")
_CHECK_AGAIN = Msg("btn.upd.check_again", "Check again", note="/update button")
_CANCEL = Msg("btn.upd.cancel", "Cancel", note="/update button: do not update")
_RESTART_NOW = Msg("btn.upd.restart_now", "Restart now",
                   note="/update button: restart aipager now, even if sessions are busy")
_WAIT_MORE = Msg("btn.upd.wait", "Wait {mins} more min",
                 note="/update button: wait longer for sessions to go idle", eg={"mins": 10})
#: The "Wait N more min" button's N, as it has always read.
_WAIT_MORE_MINUTES = 10
_STILL_RUNNING = Msg("upd.still_running", "Still running:",
                     note="/update: the list of what a restart would interrupt follows")
_STARTING = Msg("upd.starting", "⏳ Starting the update…", note="/update job: first message")
_OUTPUT_IN_LOG = Msg("upd.output_in_log", "(output in the daemon log)",
                     note="/update in a group: the installer's output is not shown here")
_EL_S = Msg("upd.elapsed.s", "{s}s", note="Update heartbeat: seconds so far", eg={"s": 30})
_EL_M = Msg("upd.elapsed.m", "{m} min", note="Update heartbeat: minutes so far", eg={"m": 2})
_EL_MS = Msg("upd.elapsed.ms", "{m} min {s}s", note="Update heartbeat: minutes and seconds",
             eg={"m": 2, "s": 30})
_STEP_STARTED = Msg("upd.step.started", "⏳ {step}…",
                    note="/update job: a step started; {step} is upd.step.*",
                    eg={"step": "Updating Claude Code"})
_STEP_STILL = Msg("upd.step.still_working", "⏳ {step} - still working{elapsed}",
                  note="/update job heartbeat; {elapsed} is ' (2 min 30s)' or empty",
                  eg={"step": "Updating Claude Code", "elapsed": " (2 min 30s)"})
_STEP_CLAUDE = Msg("upd.step.claude", "Updating Claude Code", note="/update job step name")
_STEP_AIPAGER = Msg("upd.step.aipager", "Upgrading aipager via {kind}",
                    note="/update job step name; {kind} is the installer (pipx, uv, pip...)",
                    eg={"kind": "pipx"})
_UNEXPECTED = Msg("upd.unexpected_error",
                  "❌ The update stopped on an unexpected error (see the daemon log).",
                  note="/update job: an internal error stopped it")
_SN_NONE_RESTARTED = Msg("upd.sessions.none_restarted",
                         "Running sessions keep the old version until they are restarted - "
                         "none was restarted.",
                         note="After a Claude Code update: running sessions were left alone")
_SN_OLD = Msg("upd.sessions.old", "Still on the old version: {labels}",
              note="After a Claude Code update: this chat's sessions (names)",
              eg={"labels": "x1, x2"})
_SN_OTHERS = Plural("upd.sessions.other_chats", one="and {n} in other chats",
                    other="and {n} in other chats",
                    note="Continues the line above it: sessions of other chats on the old "
                         "version")
_SN_NONE = Msg("upd.sessions.none", "No sessions are running right now.",
               note="After a Claude Code update")
_SN_NEW = Msg("upd.sessions.new_version", "New sessions use {version}.",
              note="After a Claude Code update", eg={"version": "2.1.310"})
_CC_ERROR = Msg("upd.cc.error", "❌ <b>Claude Code</b>: {error}",
                note="Claude Code update failed before it started; {error} is English",
                eg={"error": "claude not found"})
_CC_TIMEOUT = Msg("upd.cc.timeout", "⏱ <b>Claude Code</b> update timed out after {seconds}s",
                  note="Claude Code update", eg={"seconds": 300})
_CC_FAILED = Msg("upd.cc.failed", "❌ <b>Claude Code</b> update failed (exit {code})",
                 note="Claude Code update; {code} is the exit code or ?", eg={"code": "1"})
_CC_DONE = Msg("upd.cc.done", "✅ <b>Claude Code</b> {before} → {after}",
               note="Claude Code updated from one version to another",
               eg={"before": "2.1.300", "after": "2.1.310"})
_CC_DONE_SHUTDOWN = Msg("upd.cc.done_shutdown",
                        "✅ <b>Claude Code</b> update finished; aipager is shutting down, so "
                        "/update shows the new version once it is back",
                        note="Claude Code updated while aipager stops; keep /update as it is")
_CC_CURRENT_DONE = Msg("upd.cc.current", "✅ <b>Claude Code</b> is already up to date ({version})",
                       note="Claude Code update: nothing newer", eg={"version": "2.1.310"})
_MANUAL_NEEDED = Msg("upd.manual.needed", "Restart needed: {reason}",
                     note="aipager was updated but cannot restart itself", 
                     eg={"reason": "no service unit"})
_WOULD_KILL = Msg("upd.manual.would_kill", "Sessions a restart would kill: {labels}",
                  note="Session names", eg={"labels": "x1, x2"})
_AP_BLOCKED = Msg("upd.ap.blocked", "🚫 <b>aipager</b> can't be updated from here: {reason}",
                  note="{reason} is English text from the install",
                  eg={"reason": "editable install"})
_AP_NOT_FOUND = Msg("upd.ap.not_found",
                    "❌ <b>aipager</b>: couldn't find <code>{kind}</code> (searched {path})",
                    note="The installer program is missing", eg={"kind": "pipx", "path": "/usr/bin"})
_AP_UP_TO_DATE = Msg("upd.ap.current", "✅ <b>aipager</b> is already up to date ({version}).",
                     note="aipager update: nothing newer", eg={"version": "1.0.1"})
_AP_CANCELLED = Msg("upd.ap.cancelled",
                    "↩️ <b>aipager</b> update cancelled - nothing was installed.",
                    note="aipager update cancelled while waiting for idle sessions")
_AP_TIMEOUT = Msg("upd.ap.timeout",
                  "⏱ <b>aipager</b> upgrade timed out after {seconds}s and was stopped, "
                  "nothing restarted, still running {running}. The install on disk may be "
                  "partial: reinstall with <code>{hint}</code> before the next restart.",
                  note="{hint} is a command", eg={"seconds": 600, "running": "1.0.1",
                                                  "hint": "pipx install --force aipager"})
_AP_FAILED = Msg("upd.ap.failed",
                 "❌ <b>aipager</b> upgrade failed (exit {code}), nothing restarted.",
                 note="{code} is the installer's exit code or ?", eg={"code": "2"})
_AP_IMPORT_FAILED = Msg("upd.ap.import_failed",
                        "❌ The new version failed to import, still running {running}, "
                        "nothing restarted. Reinstall with <code>{hint}</code>",
                        note="{hint} is a command",
                        eg={"running": "1.0.1", "hint": "pipx install --force aipager"})
_AP_SAME_LOCAL_AT = Msg("upd.ap.current_local_at",
                        "✅ <b>aipager</b> is already at {running} - this {kind} install "
                        "upgrades from the local path {where}, which still has the same "
                        "version. Update that checkout first.",
                        note="{kind} is the installer, {where} a folder",
                        eg={"running": "1.0.1", "kind": "pipx", "where": "/src/aipager"})
_AP_SAME_LOCAL = Msg("upd.ap.current_local",
                     "✅ <b>aipager</b> is already at {running} - this {kind} install "
                     "upgrades from the local path, which still has the same version. "
                     "Update that checkout first.",
                     note="{kind} is the installer", eg={"running": "1.0.1", "kind": "pipx"})
_AP_SAME_GIT = Msg("upd.ap.current_git",
                   "✅ <b>aipager</b> is already at {running} - this {kind} install upgrades "
                   "from its git source, which has nothing newer.",
                   note="{kind} is the installer", eg={"running": "1.0.1", "kind": "pipx"})
_AP_SAME_INDEX = Msg("upd.ap.current_index",
                     "✅ <b>aipager</b> is already at {running} - the package index has "
                     "nothing newer.", note="The package index is PyPI",
                     eg={"running": "1.0.1"})
_AP_SAME = Msg("upd.ap.current_plain", "✅ <b>aipager</b> is already at {running}.",
               note="aipager update: nothing newer", eg={"running": "1.0.1"})
_AP_INSTALLED_OK = Msg("upd.ap.installed", "✅ <b>aipager</b> {frm} → {to} installed.",
                       note="aipager updated; how to restart follows", eg={"frm": "1.0.1", "to": "1.0.2"})
_AP_WAITING = Msg("upd.ap.waiting",
                  "⏳ <b>aipager</b> {frm} → {to} installed, restarting when the current "
                  "turn ends", note="aipager updated, the restart waits", eg={"frm": "1.0.1", "to": "1.0.2"})
_AP_RESTART_CANCELLED = Msg("upd.ap.restart_cancelled",
                            "✅ <b>aipager</b> {frm} → {to} installed, but the restart was "
                            "cancelled. Restart it yourself: <code>{cmd}</code>",
                            note="{cmd} is a command",
                            eg={"frm": "1.0.1", "to": "1.0.2",
                                "cmd": "systemctl --user restart aipager"})
_AP_PREPARE_FAILED = Msg("upd.ap.prepare_failed",
                         "✅ <b>aipager</b> {frm} → {to} installed, but the restart could not "
                         "be prepared ({error}). Restart it yourself: <code>{cmd}</code>",
                         note="{error} is English", eg={"frm": "1.0.1", "to": "1.0.2", "error": "disk full",
                             "cmd": "aipager start"})
_AP_SCHEDULE_FAILED = Msg("upd.ap.schedule_failed",
                          "✅ <b>aipager</b> {frm} → {to} installed, but scheduling the "
                          "restart failed: {detail}. Restart it yourself: <code>{cmd}</code>",
                          note="{detail} is English", eg={"frm": "1.0.1", "to": "1.0.2", "detail": "no systemd-run",
                              "cmd": "aipager start"})
_AP_RESTARTING = Msg("upd.ap.restarting", "⏳ <b>aipager</b> {frm} → {to} installed, restarting…",
                     note="aipager updated and restarts now", eg={"frm": "1.0.1", "to": "1.0.2"})
_AP_FINISHED_SHUTDOWN = Msg("upd.ap.finished_shutdown",
                            "✅ <b>aipager</b> upgrade finished; the daemon is shutting down, so "
                            "nothing was restarted - the next start runs what is installed.",
                            note="aipager stopped right after the upgrade")
_AP_INSTALLED_SHUTDOWN = Msg("upd.ap.installed_shutdown",
                             "✅ <b>aipager</b> {frm} → {to} installed; the daemon is shutting "
                             "down, so nothing was restarted - the next start runs {to}.",
                             note="aipager stopped right after the upgrade", eg={"frm": "1.0.1", "to": "1.0.2"})
_FIX_CLAUDE = Msg("upd.fix.claude", "run {command} again",
                  note="Ends 'If anything misbehaves, ...'; {command} is claude update",
                  eg={"command": "claude update"})
_FIX_REINSTALL = Msg("upd.fix.reinstall", "reinstall with {command}",
                     note="Ends 'If anything misbehaves, ...'; {command} is a command",
                     eg={"command": "pipx install --force aipager"})
_SHUTDOWN_PARTIAL = Msg("upd.shutdown.partial",
                        "⚠️ The daemon shut down while the update was installing; the installer "
                        "was stopped, so the install may be partial. If anything misbehaves, "
                        "{fix}.", note="{fix} is upd.fix.claude or upd.fix.reinstall",
                        eg={"fix": "run claude update again"})
_SHUTDOWN_EARLY = Msg("upd.shutdown.early",
                      "⚠️ The daemon shut down before the update finished.",
                      note="/update job")
_GATE_BUSY = Plural("upd.gate.busy",
                    one="⏰ Sessions are still busy after {n} min. Wait {wait} more min, "
                        "restart now, or cancel? Restart now interrupts everything listed "
                        "below.",
                    other="⏰ Sessions are still busy after {n} min. Wait {wait} more min, "
                          "restart now, or cancel? Restart now interrupts everything listed "
                          "below.",
                    note="/update: the restart waited {n} minutes for idle sessions; the "
                         "buttons are Wait {wait} more min, Restart now, Cancel",
                    eg={"wait": 10})
_GATE_BUSY_HELD = Plural("upd.gate.busy_held",
                         one="⏰ Sessions are still busy after {n} min. Wait {wait} more min, "
                             "restart now, or cancel? Restart now interrupts everything listed "
                             "below, including the undelivered held answers.",
                         other="⏰ Sessions are still busy after {n} min. Wait {wait} more "
                               "min, restart now, or cancel? Restart now interrupts everything "
                               "listed below, including the undelivered held answers.",
                         note="As upd.gate.busy, when answers are held back for a chat",
                         eg={"wait": 10})
_GATE_WAITING = Msg("upd.gate.waiting",
                    "⏳ <b>aipager</b>: waiting for every session to go idle before the "
                    "restart.", note="/update job")
_WATCHDOG = Msg("upd.watchdog",
                "⚠️ The scheduled restart did not happen - the new version is installed but "
                "the old one is still running. Restart it yourself: {command}",
                note="{command} is a command",
                eg={"command": "systemctl --user restart aipager.service"})
_BUSY_RESTARTING = Msg("upd.busy.restarting",
                       "⏳ aipager is about to restart for an update. Try /update again once "
                       "it is back.", note="/update while a restart is pending")
_BUSY_RUNNING = Msg("upd.busy.running",
                    "⏳ An update is already running ({phase}). Its status message shows the "
                    "progress.", note="{phase} is an internal step name, as it is",
                    eg={"phase": "waiting_for_idle"})
_BUSY_SHORT = Msg("upd.busy.running_short", "⏳ An update is already running ({phase}).",
                  note="{phase} is an internal step name", eg={"phase": "upgrading"})
_CHECK_FAILED_NOW = Msg("upd.check_failed_now",
                        "⚠️ Couldn't check for updates right now. Try again.",
                        note="/update: the lookup failed")
_CANCELLED = Msg("upd.cancelled", "↩️ Update cancelled - nothing changed.",
                 note="/update: Cancel was tapped")
_SHUTTING_DOWN = Msg("upd.shutting_down", "⏳ aipager is shutting down. Try again once it is back.",
                     note="/update while aipager stops")
_AP_BLOCKED_HERE = Msg("upd.ap.blocked_here", "🚫 aipager can't be updated from here: {reason}",
                       note="{reason} is English text from the install",
                       eg={"reason": "editable install"})
_MARKER_INTERRUPTED = Msg("upd.marker.interrupted",
                          "⚠️ An update was interrupted when aipager shut down; the install "
                          "may be partial. Now running {running}. If anything misbehaves, "
                          "{fix}.", note="After a restart; {fix} is upd.fix.*",
                          eg={"running": "1.0.1", "fix": "run claude update again"})
_MARKER_WRONG = Msg("upd.marker.wrong_version",
                    "⚠️ aipager restarted but is running {running}, not {to}.",
                    note="After an update restart", eg={"running": "1.0.1", "to": "1.0.2"})
_MARKER_DONE = Plural("upd.marker.done",
                      one="✅ aipager updated {frm} → {to}, {n} session re-adopted",
                      other="✅ aipager updated {frm} → {to}, {n} sessions re-adopted",
                      note="After an update restart: the sessions aipager picked up again",
                      eg={"frm": "1.0.1", "to": "1.0.2"})
_NOT_BACK = Msg("upd.marker.not_back", "⚠️ Not back: {labels}",
                note="Sessions that did not come back after the restart",
                eg={"labels": "x9"})

# ---- Restart now: finish an update without a service unit ----------------------
_BTN_RESTART = Msg("btn.upd.restart", "🔄 Restart now",
                   note="Button: restart aipager so it runs the version that is already "
                        "installed (it waits until no turn is running)")
_AP_WAITING_LINE = Msg("upd.line.ap_waiting",
                       "aipager {installed} is installed and waiting for a restart "
                       "(running {running}).",
                       note="/update check line: a newer aipager is on disk; only a restart "
                            "of aipager is missing", eg={"installed": "1.1.0", "running": "1.0.1"})
_SUMMARY_WAITING = Msg("upd.summary.waiting", "Restart aipager to finish the update.",
                       note="/update check: the update is installed; Restart now finishes it")
_RESTART_BUTTON = Msg("upd.restart.button", "Restart: tap Restart now once it is installed.",
                      note="/update check: after this update, aipager restarts when the "
                           "Restart now button is tapped")
_AP_BROKEN = Msg("upd.note.ap_broken",
                 "aipager {installed} is installed but does not load. Reinstall it with "
                 "{command}.", note="/update check: the newer aipager on disk fails to "
                                    "import; {command} is a command",
                 eg={"installed": "1.1.0", "command": "pipx install --force aipager"})
_AP_RESTART_OFFER = Msg("upd.ap.restart_offer",
                        "Tap Restart now to run it (aipager waits until no turn is running).",
                        note="After aipager was installed: the Restart now button below "
                             "starts the new version")
_RS_CHECKING = Msg("upd.rs.checking", "⏳ Checking the installed aipager…",
                   note="Restart now: first step, the installed version is checked")
_RS_NOTHING = Msg("upd.rs.nothing",
                  "✅ aipager {running} is already the installed version, so there is "
                  "nothing to restart.", note="Restart now: nothing newer is installed",
                  eg={"running": "1.1.0"})
_RS_BROKEN = Msg("upd.rs.broken",
                 "❌ The installed aipager does not load, so aipager was not restarted and "
                 "still runs {running}. Reinstall with <code>{hint}</code>",
                 note="Restart now: the installed version fails to import; {hint} is a "
                      "command", eg={"running": "1.0.1", "hint": "pipx install --force aipager"})
_RS_CANCELLED = Msg("upd.rs.cancelled", "↩️ Restart cancelled. aipager still runs {running}.",
                    note="Restart now was cancelled while it waited for idle sessions",
                    eg={"running": "1.0.1"})
_RS_NOT_POSSIBLE = Msg("upd.rs.not_possible", "⚠️ aipager could not restart itself here.",
                       note="Restart now: the restart could not be started; the steps to do "
                            "it by hand follow")
_STEPS_LEAD = Msg("upd.steps.lead", "To run {to}, restart aipager yourself:",
                  note="Above the steps to restart aipager by hand; {to} is a version",
                  eg={"to": "1.1.0"})
_STEPS_TERMINAL = Msg("upd.steps.terminal",
                      "In the terminal or tmux window where aipager runs, press {keys}, then "
                      "run {command}.",
                      note="Restart by hand; {keys} is a key name (Ctrl+C), {command} a "
                           "command; tmux is a program's name",
                      eg={"keys": "Ctrl+C", "command": "aipager start"})
_STEPS_LAUNCHD = Msg("upd.steps.launchd", "On the Mac, run {command}.",
                     note="Restart by hand on macOS; {command} is a command",
                     eg={"command": "launchctl kickstart -k gui/501/com.aipager.daemon"})
_STEPS_DOCKER = Msg("upd.steps.docker", "On the Docker host, run {command}.",
                    note="Restart by hand: aipager runs in a Docker container; {command} is a "
                         "command", eg={"command": "docker restart aipager"})
_STEPS_DOCKER_IMAGE = Msg("upd.steps.docker_image",
                          "Recreating the container starts it from its image again, which "
                          "brings back the aipager version in that image.",
                          note="Docker: a warning under the restart steps")
_HOOKS_NEWER = Msg("upd.hooks.newer",
                   "⚠️ The aipager update is only half done: Claude Code's hooks already run "
                   "{hook}, but aipager itself still runs {running}. Restart aipager to "
                   "finish it.", note="Sent once to the owner: a newer aipager is installed "
                                     "but the running one is older; the Restart now button "
                                     "follows", eg={"hook": "1.1.0", "running": "1.0.1"})
_RESTART_LOCKED = Msg("toast.upd.restart_locked",
                      "An update is running elsewhere. Try again once it has finished.",
                      note="Pop-up: Restart now while an update elsewhere holds the lock")

#: One owner notice per newer hook version: a failed send is retried no
#: sooner than this...
HOOK_NOTICE_RETRY_SECONDS = 600.0
#: ...and at most this many times. Held back (the owner's chat not clear,
#: an update job running) costs no attempt and is retried on the session
#: monitor's tick; the outbound gate skipping it costs none either and is
#: retried after this.
HOOK_NOTICE_MAX_ATTEMPTS = 3
HOOK_NOTICE_SKIP_RETRY_SECONDS = 30.0
# Hook versions remembered per run (a spoofed datagram can add at most one
# notice per version, and only this many versions).
_HOOK_SEEN_MAX = 16
#: Key names and commands of the manual steps (data, never translated).
_CTRL_C = "Ctrl+C"


def _esc(value) -> str:
    return html_mod.escape(str(value))


def _esc_text(value) -> str:
    """Escape for HTML message TEXT (not attributes): keeps "couldn't"."""
    return html_mod.escape(str(value), quote=False)


# ---------------------------------------------------------------------------
# Results and the job
# ---------------------------------------------------------------------------

@dataclass
class StartResult:
    ok: bool
    error: str | None = None
    job: dict | None = None
    # Why, beyond ``error``: ``"lock_held"`` when another process (the CLI)
    # holds the update lock.
    detail: str | None = None


@dataclass
class ControlResult:
    ok: bool
    error: str | None = None
    job: dict | None = None


@dataclass
class _Job:
    id: int
    kind: str
    chat_id: int
    user_id: int | None
    origin: str
    started_at: float
    phase: str = "starting"
    sections: list[str] = field(default_factory=list)
    live: str | None = None
    blockers: list[str] = field(default_factory=list)
    summary: str = ""
    restart_now: bool = False
    wait_more: bool = False
    cancel_requested: bool = False
    # The transient systemd unit carrying the scheduled restart.
    restart_unit: str | None = None
    # The daemon shut down mid-install and killed the installer.
    interrupted: bool = False
    # ``(from, to)`` once the new aipager is on disk and proven, so a
    # shutdown after that point still leaves the normal A→B marker.
    installed: tuple[str, str] | None = None
    # The "installed, restarting when the current turn ends" section while
    # the post-install gate holds the restart (dropped before any marker is
    # written: the restart replaces it).
    waiting_section: str | None = None
    # Finished ``done`` with a Restart now button (the new aipager is
    # installed and this daemon can restart in place).
    can_restart: bool = False
    event: asyncio.Event | None = None
    reporter: "_Reporter | None" = None

    @property
    def terminal(self) -> bool:
        return self.phase in TERMINAL_PHASES

    def snapshot(self) -> dict:
        snap = {
            "id": self.id, "kind": self.kind, "phase": self.phase,
            "blockers": list(self.blockers), "summary": self.summary,
            "started_at": self.started_at,
        }
        if self.installed is not None:
            # The Mini App's restart watch waits for exactly this version.
            snap["installed"] = {"from": self.installed[0], "to": self.installed[1]}
        if self.can_restart:
            snap["can_restart"] = True
        return snap

    def wake(self) -> None:
        if self.event is not None:
            self.event.set()


@dataclass(frozen=True)
class _InPlace:
    """A restart job's last step: ask the daemon to stop and exec this."""

    target: "self_restart.ExecTarget"
    frm: str
    to: str
    steps: str
    # The plan's ``to_dict()``: the manual steps if the exec cannot start.
    plan: dict = field(default_factory=dict)


def _daemon_code_version() -> str:
    """The aipager code THIS daemon loaded (``aipager._version``, never the
    install's metadata, which an editable install can leave stale): what
    the hooks' own stamp is compared with."""
    return _version.__version__


class _Reporter:
    """The job's ONE status message: created once, then edited in place."""

    def __init__(self, bot: "TelegramBot", chat_id: int, message=None):
        self.bot = bot
        self.chat_id = chat_id
        self.message = message
        self._send_attempted = message is not None
        self._last: tuple | None = None
        self._last_at = 0.0

    async def show(self, text: str, markup=None, *, ornament: bool = False) -> None:
        key = (text, _markup_key(markup))
        if key == self._last:
            return
        now = time.monotonic()
        if ornament and self.message is not None and \
                now - self._last_at < self_update.PROGRESS_EDIT_MIN_SECONDS:
            return
        kwargs: dict = {"parse_mode": "HTML", "reply_markup": markup}
        try:
            if self.message is None:
                # The first message of a Mini App job: ESSENTIAL, sent once.
                if self._send_attempted:
                    return
                self._send_attempted = True
                app = getattr(self.bot, "_app", None)
                if app is None:
                    return
                result = await send_text(app.bot, self.chat_id, text, **kwargs)
                if result is MUTED or result is SKIPPED or result is None:
                    return
                self.message = result
            else:
                if ornament:
                    kwargs["rate_limit_args"] = rate_limit_args(
                        kind="skip", priority=PRIORITY_ORNAMENT)
                result = await edit_message(self.message, text, **kwargs)
                if result is MUTED or result is SKIPPED:
                    return
        except Exception:
            log.debug("update status edit failed", exc_info=True)
            return
        self._last = key
        self._last_at = now


def _markup_key(markup):
    if markup is None:
        return None
    try:
        return tuple(tuple(b.callback_data for b in row)
                     for row in markup.inline_keyboard)
    except Exception:
        return id(markup)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _source_dict(source: install_source.InstallSource) -> dict:
    return {
        "kind": source.kind, "origin": source.origin,
        "detail": source.origin_detail, "upgradable": source.upgradable,
        "reason": source.reason,
        "describe": source.describe(show_paths=True),
        "describe_short": source.describe(show_paths=False),
    }


@dataclass(frozen=True)
class UpdateOffer:
    """What a check found and the ONE update it offers (roadmap 8.43).

    ``kind`` is ``"aipager"``, ``"claude"``, ``"both"`` or None (nothing
    to offer); ``lines`` holds one plain-text line per product.
    ``restart_offer``: a newer aipager is installed and waiting, and this
    daemon can restart into it (the Restart now button); ``steps``: how
    to restart it by hand when it cannot (plain text)."""

    kind: str | None
    label: str | None
    lines: list[str]
    summary: str | None
    restart: str | None
    notes: list[str]
    restart_offer: bool = False
    steps: str | None = None


def installed_waiting(running, installed, installed_ok, latest) -> bool:
    """A newer aipager is installed (and loads) but not running yet, and
    it is not older than the newest release (unknown counts as not
    older): only a restart is missing, never another update."""
    if not installed_ok:
        return False
    if not self_update.is_newer(installed, running):
        return False
    return not latest or not self_update.is_newer(latest, installed)


def _code(command: str, html: bool):
    """A command or key name as a placeholder value: a ``<code>`` span in
    HTML, the plain command in text."""
    return Span(f"<code>{_esc(command)}</code>") if html else str(command)


def _docker_command(plan: dict) -> str:
    container = plan.get("container")
    return " ".join(("docker", "restart", str(container) if container else "<container>"))


def restart_steps_text(plan: dict, tr, *, to, html: bool) -> str:
    """How to restart aipager by hand for *plan* (a ``RestartPlan.to_dict()``),
    in *tr*'s language: a lead line, then the steps for this setup
    (terminal or tmux, launchd, Docker). systemd keeps its "Restart
    needed" text. HTML for a chat, plain text for the Mini App."""
    tr = tr or i18n.get("en")
    steps = plan.get("steps") or "terminal"
    if steps == "systemd":
        text = _manual_restart_html(plan.get("reason"), plan.get("manual_command"), [], tr)
        return text if html else _plain(text)
    render = tr.html if html else tr.text
    lines = [render(_STEPS_LEAD, to=str(to))]
    if steps == "launchd":
        command = plan.get("manual_command") or " ".join(
            ("launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{self_update.MACOS_LABEL}"))
        lines.append(render(_STEPS_LAUNCHD, command=_code(command, html)))
    elif steps == "docker":
        lines.append(render(_STEPS_DOCKER, command=_code(_docker_command(plan), html)))
        lines.append(render(_STEPS_DOCKER_IMAGE))
    else:
        lines.append(render(_STEPS_TERMINAL, keys=_code(_CTRL_C, html),
                            command=_code(" ".join(("aipager", "start")), html)))
    return "\n".join(lines)


def _manual_restart_html(reason, manual_command, at_risk, tr) -> str:
    """systemd's "Restart needed: ..." (the plan's reason is English data)."""
    lines = [tr.html(_MANUAL_NEEDED, reason=reason or Safe(tr.html(_YOURSELF)))]
    if manual_command:
        lines.append(f"<code>{_esc(manual_command)}</code>")
    if at_risk:
        lines.append(tr.html(_WOULD_KILL, labels=tr.join(at_risk)))
    return "\n".join(lines)


def update_offer(status: dict, tr=None) -> UpdateOffer:
    """THE decision both surfaces show: which products have an update this
    install can apply. A product is offered only when its lookup succeeded
    and found a strictly newer version, and only when this install can apply
    it (an aipager install that can be upgraded from here; a Claude Code not
    held back by ``claude.blocked``). The lines and the label in *tr*'s
    language (English when None); ``kind`` is the id logic compares."""
    tr = tr or i18n.get("en")
    ap = status["aipager"]
    cl = status["claude"]
    rs = status["restart"]
    src = ap["source"]
    lines: list[str] = []
    notes: list[str] = []
    failed = False

    running = ap["running"]
    latest = ap.get("latest")
    installed = ap.get("installed")
    offer_ap = False
    held_back = False   # newer, but can't be updated from here
    # Installed and waiting for a restart: never another update, only
    # Restart now (or the steps when this daemon cannot restart itself).
    waiting = installed_waiting(running, installed, ap.get("installed_ok") is True, latest)
    restart_offer = False
    steps = None
    if waiting:
        lines.append(tr.text(_AP_WAITING_LINE, installed=str(installed), running=str(running)))
        restart_offer = bool(rs.get("in_place") or rs.get("automatic"))
        if not restart_offer:
            steps = restart_steps_text(rs, tr, to=installed, html=False)
    elif not latest:
        failed = True
        lines.append(tr.text(_AP_UNCHECKED, running=str(running)))
    elif ap.get("update_available"):
        if src["upgradable"]:
            offer_ap = True
            lines.append(tr.text(_AP_NEWER, running=str(running), latest=str(latest)))
        else:
            held_back = True
            reason = src.get("reason") or Safe(tr.text(_UNKNOWN_INSTALL))
            lines.append(tr.text(_AP_NEWER_BLOCKED, running=str(running),
                                 latest=str(latest), reason=reason))
    else:
        lines.append(tr.text(_AP_CURRENT, running=str(running)))
    if not waiting and installed and installed != running:
        if ap.get("installed_ok") is False and self_update.is_newer(installed, running):
            notes.append(tr.text(_AP_BROKEN, installed=str(installed),
                                 command=install_source.reinstall_hint(str(src.get("kind")))))
        else:
            notes.append(tr.text(_AP_INSTALLED, installed=str(installed)))

    cur = cl.get("current")
    cl_latest = cl.get("latest")
    offer_cc = False
    if not cur:
        failed = True
        lines.append(tr.text(_CC_UNKNOWN))
    elif not cl_latest:
        failed = True
        lines.append(tr.text(_CC_UNCHECKED, current=str(cur)))
    elif cl.get("update_available"):
        if cl.get("blocked"):
            held_back = True
            lines.append(tr.text(_CC_NEWER_BLOCKED, current=str(cur), latest=str(cl_latest),
                                 reason=str(cl["blocked"])))
        else:
            offer_cc = True
            lines.append(tr.text(_CC_NEWER, current=str(cur), latest=str(cl_latest)))
    else:
        lines.append(tr.text(_CC_CURRENT, current=str(cur)))

    if offer_ap and offer_cc:
        kind, label = "both", tr.text(_LABEL_BOTH)
    elif offer_ap:
        kind, label = "aipager", tr.text(_LABEL_AP)
    elif offer_cc:
        kind, label = "claude", tr.text(_LABEL_CC)
    else:
        kind = label = None
    summary = None
    if kind is None:
        summary = tr.text(_SUMMARY_WAITING if waiting
                          else CANT_UPDATE_HERE_TEXT if held_back
                          else CHECK_FAILED_TEXT if failed else UP_TO_DATE_TEXT)
    restart = None
    if offer_ap:
        restart = (tr.text(_RESTART_AUTO) if rs["automatic"]
                   else tr.text(_RESTART_BUTTON) if rs.get("in_place")
                   else tr.text(_RESTART_MANUAL,
                                reason=rs.get("reason") or Safe(tr.text(_YOURSELF))))
    return UpdateOffer(kind=kind, label=label, lines=lines, summary=summary,
                       restart=restart, notes=notes, restart_offer=restart_offer,
                       steps=steps)


def _source_text(status: dict, show_paths: bool) -> str:
    src = status["aipager"]["source"]
    return str(src["describe"] if show_paths else src["describe_short"])


def check_payload(status: dict, *, show_paths: bool, tr=None) -> dict:
    """The Mini App's view of a check: the same offer the chat shows, in
    *tr*'s language (the viewer's; English when None). ``source`` is the
    install's own English description."""
    offer = update_offer(status, tr)
    return {
        "lines": list(offer.lines),
        "offer": ({"kind": offer.kind, "label": offer.label}
                  if offer.kind is not None else None),
        "summary": offer.summary,
        "restart": offer.restart,
        "notes": list(offer.notes),
        "source": _source_text(status, show_paths),
        # Installed and waiting: Restart now instead of an update, or the
        # steps to restart by hand (plain text).
        "restart_offer": offer.restart_offer,
        "steps": offer.steps,
        # Whether an offered aipager update restarts by itself...
        "restart_auto": bool((status.get("restart") or {}).get("automatic")),
        # ...or ends with Restart now (this process restarts in place).
        # Neither: it ends with the steps to restart by hand.
        "restart_in_place": bool((status.get("restart") or {}).get("in_place")),
    }


def check_prompt_markup(tr=None) -> InlineKeyboardMarkup:
    tr = tr or i18n.get("en")
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(tr.text(CHECK_BUTTON_TEXT), callback_data="_:up:chk")]])


def render_check(status: dict, *, show_paths: bool,
                 tr=None) -> tuple[str, InlineKeyboardMarkup]:
    """The chat's view of a check: the offer's lines, then ONE Update
    button and Cancel, or "Everything is up to date." and Check again.
    In *tr*'s language (the chat's; English when None)."""
    tr = tr or i18n.get("en")
    offer = update_offer(status, tr)
    lines = [tr.html(_HEADING), ""]
    lines += [_esc_text(line) for line in offer.lines]
    lines.append(f"<i>{_esc_text(_source_text(status, show_paths))}</i>")
    lines += [_esc_text(n) for n in offer.notes]
    if offer.restart:
        lines.append(_esc_text(offer.restart))
    if offer.steps:
        # In chat the commands are code spans.
        lines += ["", restart_steps_text(status["restart"], tr,
                                         to=status["aipager"]["installed"], html=True)]
    rows = []
    if offer.kind is None:
        lines += ["", _esc_text(offer.summary)]
    else:
        rows.append([
            InlineKeyboardButton(offer.label,
                                 callback_data=f"_:up:go:{_GO_VERBS[offer.kind]}"),
            InlineKeyboardButton(tr.text(_CANCEL), callback_data="_:up:x"),
        ])
    if offer.restart_offer:
        rows.append([InlineKeyboardButton(tr.text(_BTN_RESTART), callback_data="_:up:rs")])
    if not rows:
        rows.append([InlineKeyboardButton(tr.text(_CHECK_AGAIN), callback_data="_:up:chk")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def restart_markup(tr=None) -> InlineKeyboardMarkup:
    """The one Restart now button (``_:up:rs``)."""
    tr = tr or i18n.get("en")
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(tr.text(_BTN_RESTART), callback_data="_:up:rs")]])


def _job_markup(job: _Job, tr=None) -> InlineKeyboardMarkup | None:
    """The job's buttons, in *tr*'s language (the job chat's when None)."""
    if job.phase == "done" and job.can_restart:
        return restart_markup(tr or i18n.for_chat(job.chat_id))
    if job.phase not in ("waiting_for_idle", "gate_timeout"):
        return None
    tr = tr or i18n.for_chat(job.chat_id)
    now = InlineKeyboardButton(tr.text(_RESTART_NOW), callback_data=f"_:up:now:{job.id}")
    stop = InlineKeyboardButton(tr.text(_CANCEL), callback_data=f"_:up:stop:{job.id}")
    if job.phase == "waiting_for_idle":
        return InlineKeyboardMarkup([[now, stop]])
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(tr.text(_WAIT_MORE, mins=_WAIT_MORE_MINUTES),
                              callback_data=f"_:up:wait:{job.id}")],
        [now, stop],
    ])


def _job_text(job: _Job, tr=None) -> str:
    """The job's status message. Its sections stay in the language they
    were written in; what is drawn now uses *tr* (the job chat's when
    None). Blockers are English data."""
    tr = tr or i18n.for_chat(job.chat_id)
    parts = list(job.sections)
    if job.live:
        parts.append(job.live)
    if job.phase in ("waiting_for_idle", "gate_timeout") and job.blockers:
        parts.append(tr.html(_STILL_RUNNING) + "\n"
                     + "\n".join(f"  • {_esc(b)}" for b in job.blockers))
    return "\n\n".join(parts) or tr.html(_STARTING)


def _show_detail(chat_id) -> bool:
    """Paths and command output only go to a private chat (positive id)."""
    return isinstance(chat_id, int) and not isinstance(chat_id, bool) and chat_id > 0


def _safe_tail(text) -> str:
    """Defence in depth: the seam already redacts and trims output, but
    whatever reaches a chat is redacted (all of it, THEN cut) and capped
    again right here."""
    return self_update.redacted_tail(text)


def _pre_tail(text, chat_id, tr=None) -> str:
    """``\n<pre>tail</pre>`` for a private chat; a pointer to the daemon log
    in a group (installer output can carry home-directory paths), in *tr*'s
    language (the chat's when None)."""
    tail = _safe_tail(text)
    if not tail:
        return ""
    if not _show_detail(chat_id):
        return "\n" + (tr or i18n.for_chat(chat_id)).html(_OUTPUT_IN_LOG)
    return f"\n<pre>{_esc(tail)}</pre>"


def _plain(text: str) -> str:
    import re
    return html_mod.unescape(re.sub(r"<[^>]+>", "", text))


def _elapsed_label(seconds: float, tr=None) -> str:
    """The heartbeat's " (2 min 30s)", in *tr*'s language (English when
    None): its own coarse shape, not the card's clock (ruling 6)."""
    tr = tr or i18n.get("en")
    step = self_update.HEARTBEAT_GRANULARITY_SECONDS
    shown = int(seconds // step * step)
    if shown <= 0:
        return ""
    if shown < 60:
        return " (" + tr.text(_EL_S, s=shown) + ")"
    if shown % 60:
        return " (" + tr.text(_EL_MS, m=shown // 60, s=shown % 60) + ")"
    return " (" + tr.text(_EL_M, m=shown // 60) + ")"


# ---------------------------------------------------------------------------
# The manager
# ---------------------------------------------------------------------------

class UpdateManager:
    """Owned by the bot (``bot.updates``); at most one job at a time."""

    def __init__(self, bot: "TelegramBot"):
        self.bot = bot
        self._job: _Job | None = None
        self._lock = self_update.UpdateLock()
        self._status_cache: tuple[float, dict] | None = None
        # The last "Check for updates" result, ``(monotonic, status)``: the
        # only thing an Update button may start from (see start_offer).
        self._checked: tuple[float, dict] | None = None
        # Bumped whenever a job starts or ends: a check that spans either
        # is not remembered (its versions may predate the job).
        self._job_epoch = 0
        self._tasks: set[asyncio.Task] = set()
        self._job_task: asyncio.Task | None = None
        self._last_job_id = 0
        # The job id that owns ``self._lock``: only that job may release it,
        # so a stale watchdog or a late ``finally`` cannot free a lock a
        # later job holds.
        self._lock_owner: int | None = None
        self._shutting_down = False
        # Set by deliver_update_marker in the daemon an update restarted
        # into: ``{"from", "to", "readopted", "missing"}``, shown by the
        # Mini App's restart watch.
        self.last_restart: dict | None = None
        # A newer aipager known to be installed and waiting for a restart
        # (the last check, or an update job that ended with Restart now).
        self._waiting: str | None = None
        # The newest hook version seen running newer code than this daemon,
        # and one record per such version: {delivered, attempts,
        # last_try_mono, last_try, task, deferred} (one owner notice per version per run).
        self._hooks_newer: str | None = None
        self._hook_seen: dict[str, dict] = {}

    # ---- read side -------------------------------------------------------

    @property
    def restart_pending(self) -> bool:
        """A restart is scheduled and has not happened (nor been given up on
        by the watchdog). The lock stays held throughout."""
        return self._job is not None and self._job.phase == "restart_scheduled"

    @property
    def service_restart_pending(self) -> bool:
        """The service manager's restart (systemd) is scheduled: the SIGTERM
        that ends this daemon is that restart, not an operator's stop. An
        in-place restart is ``self_restart.pending()`` instead, which a real
        stop signal cancels."""
        return self.restart_pending and bool(self._job.restart_unit)

    @property
    def shutting_down(self) -> bool:
        return self._shutting_down

    @property
    def busy(self) -> bool:
        """A job is running OR its restart is still pending: no second
        update, no voice restart, and no start buttons meanwhile."""
        return self._job is not None and (not self._job.terminal
                                          or self.restart_pending)

    def snapshot(self) -> dict | None:
        return self._job.snapshot() if self._job is not None else None

    def invalidate_status(self) -> None:
        self._status_cache = None

    async def status(self, *, force: bool = False) -> dict:
        now = time.monotonic()
        cached = self._status_cache
        if (not force and cached is not None
                and now - cached[0] < self_update.STATUS_CACHE_SECONDS):
            return cached[1]
        data = await self._collect_status()
        self._status_cache = (time.monotonic(), data)
        return data

    async def check(self) -> dict:
        """"Check for updates": a fresh lookup of both products, remembered
        as the basis every Update button starts from."""
        epoch = self._job_epoch
        data = await self.status(force=True)
        if epoch == self._job_epoch and not self.busy:
            self._checked = (time.monotonic(), data)
            ap = data.get("aipager") or {}
            self._waiting = str(ap["installed"]) if ap.get("waiting") else None
        else:
            log.info("update.check.discarded reason=job-started-or-ended")
        return data

    def waiting_info(self) -> dict | None:
        """For the Mini App's banner, no lookup: a newer aipager installed
        and waiting for a restart, or hooks already running newer code."""
        running = self_update.running_version()
        if self._waiting and self_update.is_newer(self._waiting, running):
            return {"version": self._waiting, "running": running, "source": "installed"}
        mine = _daemon_code_version()
        if self._hooks_newer and self_update.is_newer(self._hooks_newer, mine):
            return {"version": self._hooks_newer, "running": mine, "source": "hooks"}
        return None

    async def _collect_status(self) -> dict:
        async def bounded(fn, *args):
            try:
                return await asyncio.wait_for(asyncio.to_thread(fn, *args),
                                              timeout=STATUS_STEP_TIMEOUT_SECONDS)
            except Exception:
                log.debug("update status lookup %s failed", getattr(fn, "__name__", fn),
                          exc_info=True)
                return None

        async def claude_side():
            cur = await bounded(self_update.current_claude)
            realpath = os.path.realpath(cur[0]) if cur else None
            try:
                method, channel = self_update.claude_channel_info(realpath)
            except Exception:
                method, channel = "unknown", "latest"
            latest = None
            if method != "unknown":
                latest = await bounded(self_update.latest_claude_version, method, channel)
            # Off the loop (it looks at folders); a failure or a timeout
            # is None, so the update is still offered.
            blocked = await bounded(self_update.claude_update_blocker, realpath, method)
            return cur, method, channel, latest, blocked

        async def installed_side():
            # What a restart would run: probed in a fresh interpreter (the
            # in-process metadata can predate a recreated venv).
            target = self_restart.exec_target()
            if target is None:
                return None
            return await bounded(self_update.probe_restart_target, target)

        source = install_source.detect_install_source()
        running = self_update.running_version()
        latest_ap, claude, plan, probe = await asyncio.gather(
            bounded(self_update.latest_aipager_version),
            claude_side(),
            bounded(self_update.restart_plan, self.bot.registry),
            installed_side(),
        )
        cur, method, channel, latest_cl, blocked_cl = claude
        if plan is None:
            plan = self_update.RestartPlan(
                "foreground", False, "could not tell how this daemon is run")
        if probe is not None:
            probed, installed_ok, _err = probe
            installed = probed or self_update.installed_version()
        else:
            # The probe could not run: the in-process reading, unproven.
            installed, installed_ok = self_update.installed_version(), None
        waiting = installed_waiting(running, installed, installed_ok is True, latest_ap)
        log.info("update.check running=%s latest=%s claude=%s claude_latest=%s "
                 "source=%s restart=%s installed=%s waiting=%s in_place=%s", running,
                 latest_ap, cur[1] if cur else None, latest_cl, source.kind, plan.mode,
                 installed, waiting, plan.in_place)
        return {
            "aipager": {
                "running": running,
                "installed": installed,
                "installed_ok": installed_ok,
                "waiting": waiting,
                "latest": latest_ap,
                "update_available": self_update.is_newer(latest_ap, running),
                "source": _source_dict(source),
            },
            "claude": {
                "current": cur[1] if cur else None,
                "latest": latest_cl,
                "update_available": bool(cur) and self_update.is_newer(latest_cl, cur[1]),
                "method": method,
                "channel": channel,
                # Why the update can't work here (None: it can, or unknown).
                "blocked": blocked_cl,
            },
            "restart": plan.to_dict(),
        }

    # ---- write side ------------------------------------------------------

    async def start_offer(self, kind: str, *, chat_id: int, user_id: int | None,
                          origin: str, status_message=None) -> StartResult:
        """The ONE entry point for an Update button, in chat or the Mini App.

        ``kind`` is what the tapped button said. It starts only if the last
        check, still fresh and taken since the last job, offers exactly
        that: a stale button, a hand-made request or a product with nothing
        newer starts nothing. No lookup happens here."""
        if kind not in KINDS:
            raise ValueError(f"unknown update kind {kind!r}")
        # start() repeats these two refusals; they are here too so a
        # refused tap never reads (or reports on) the stored check.
        if self._shutting_down:
            return StartResult(False, "shutting_down", self.snapshot())
        if self.busy:
            log.info("update.lock_busy user=%s chat=%s job=%s", user_id, chat_id,
                     self._job.id if self._job else None)
            return StartResult(False, "update_in_progress", self.snapshot())
        checked = self._checked
        if checked is None or time.monotonic() - checked[0] > OFFER_MAX_AGE_SECONDS:
            log.info("update.offer.expired user=%s chat=%s kind=%s", user_id, chat_id, kind)
            return StartResult(False, "check_expired", None)
        offer = update_offer(checked[1])
        if offer.kind != kind:
            log.info("update.offer.changed user=%s chat=%s asked=%s offered=%s",
                     user_id, chat_id, kind, offer.kind)
            return StartResult(False, "offer_changed", None)
        return await self.start(kind, chat_id=chat_id, user_id=user_id,
                                origin=origin, status_message=status_message)

    async def start(self, kind: str, *, chat_id: int, user_id: int | None,
                    origin: str, status_message=None) -> StartResult:
        if kind not in KINDS:
            raise ValueError(f"unknown update kind {kind!r}")
        if self._shutting_down:
            # Nothing started now could be seen through: its installer
            # would not be in shutdown()'s snapshot and would be orphaned.
            log.info("update.refused user=%s chat=%s reason=shutting-down", user_id, chat_id)
            return StartResult(False, "shutting_down", self.snapshot())
        if self.busy:
            log.info("update.lock_busy user=%s chat=%s job=%s", user_id, chat_id,
                     self._job.id if self._job else None)
            return StartResult(False, "update_in_progress", self.snapshot())
        if kind in ("aipager", "both"):
            source = install_source.detect_install_source()
            if not source.upgradable:
                log.info("update.aipager.refused user=%s chat=%s kind=%s reason=%s",
                         user_id, chat_id, source.kind, source.reason)
                return StartResult(False, "not_upgradable", None)
        if not self._lock.try_acquire():
            log.info("update.lock_busy user=%s chat=%s (held elsewhere)",
                     user_id, chat_id)
            return StartResult(False, "update_in_progress", self.snapshot())
        job_id = max(int(time.time()), self._last_job_id + 1)
        self._last_job_id = job_id
        self._lock_owner = job_id
        job = _Job(id=job_id, kind=kind, chat_id=chat_id, user_id=user_id,
                   origin=origin, started_at=time.time(), event=asyncio.Event())
        job.reporter = _Reporter(self.bot, chat_id, status_message)
        self._job = job
        self._job_epoch += 1
        log.info("update.start job=%s kind=%s user=%s chat=%s origin=%s",
                 job.id, kind, user_id, chat_id, origin)
        self._job_task = self._spawn(self._run_job(job))
        return StartResult(True, None, job.snapshot())

    async def start_restart(self, *, chat_id: int, user_id: int | None, origin: str,
                            status_message=None) -> StartResult:
        """Restart now: run the aipager that is installed (a ``restart``
        job). The same single-flight rules as :meth:`start`; no install
        source is required (an editable or Docker install may have a newer
        version waiting too)."""
        if self._shutting_down:
            log.info("update.restart.refused user=%s chat=%s reason=shutting-down",
                     user_id, chat_id)
            return StartResult(False, "shutting_down", self.snapshot())
        if self.busy:
            log.info("update.lock_busy user=%s chat=%s job=%s", user_id, chat_id,
                     self._job.id if self._job else None)
            return StartResult(False, "update_in_progress", self.snapshot())
        if not self._lock.try_acquire():
            log.info("update.lock_busy user=%s chat=%s (held elsewhere)", user_id, chat_id)
            return StartResult(False, "update_in_progress", self.snapshot(),
                               detail="lock_held")
        job_id = max(int(time.time()), self._last_job_id + 1)
        self._last_job_id = job_id
        self._lock_owner = job_id
        job = _Job(id=job_id, kind="restart", chat_id=chat_id, user_id=user_id,
                   origin=origin, started_at=time.time(), event=asyncio.Event(),
                   phase="verifying")
        # The card's first words: the installed version is checked first.
        job.live = i18n.for_chat(chat_id).html(_RS_CHECKING)
        job.reporter = _Reporter(self.bot, chat_id, status_message)
        self._job = job
        self._job_epoch += 1
        log.info("update.start job=%s kind=restart user=%s chat=%s origin=%s",
                 job.id, user_id, chat_id, origin)
        self._job_task = self._spawn(self._run_job(job))
        return StartResult(True, None, job.snapshot())

    def control(self, action: str, job_id) -> ControlResult:
        job = self._job
        if (job is None or job.terminal or not isinstance(job_id, int)
                or isinstance(job_id, bool) or job.id != job_id):
            return ControlResult(False, "no_matching_job", self.snapshot())
        if action == "restart-now":
            job.restart_now = True
        elif action == "wait-more":
            if job.phase != "gate_timeout":
                return ControlResult(False, "no_matching_job", job.snapshot())
            job.wait_more = True
        elif action == "cancel":
            if job.phase not in _CANCELLABLE:
                return ControlResult(False, "no_matching_job", job.snapshot())
            job.cancel_requested = True
        else:
            raise ValueError(f"unknown control {action!r}")
        log.info("update.control job=%s action=%s", job.id, action)
        job.wake()
        return ControlResult(True, None, job.snapshot())

    # ---- the job ---------------------------------------------------------

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def _report(self, job: _Job, *, ornament: bool = False) -> None:
        if job.reporter is not None:
            await job.reporter.show(_job_text(job), _job_markup(job), ornament=ornament)

    def _release_lock(self, job: _Job) -> None:
        """Release the update lock iff ``job`` owns it."""
        if self._lock_owner != job.id:
            log.info("update.lock.not_owner job=%s owner=%s", job.id, self._lock_owner)
            return
        self._lock_owner = None
        self._lock.release()

    def _finish(self, job: _Job, phase: str, section: str | None) -> None:
        if section:
            job.sections.append(section)
            job.summary = _plain(section)
        job.live = None
        job.phase = phase

    async def _run_job(self, job: _Job) -> None:
        # Tags every self_update log line (spawns, claude, restart) with
        # this job; to_thread copies the context into the worker thread.
        self_update.CURRENT_JOB_ID.set(job.id)
        restart_scheduled = False
        in_place: _InPlace | None = None
        try:
            await self._report(job)
            if job.kind == "restart":
                outcome = await self._restart_step(job)
                if isinstance(outcome, _InPlace):
                    in_place = outcome
                restart_scheduled = outcome is not None
            if job.kind in ("claude", "both"):
                await self._claude_step(job)
            if job.kind in ("aipager", "both") and not job.terminal:
                if self._shutting_down:
                    # "both": Claude Code finished inside the shutdown grace;
                    # do not begin the aipager half now.
                    self._finish(job, "cancelled", self._shutdown_section(job))
                else:
                    restart_scheduled = await self._aipager_step(job)
            if not job.terminal:
                job.phase = "done"
                job.live = None
        except asyncio.CancelledError:
            if self._shutting_down and job.installed is not None:
                # Cancelled in the post-install gate: B is on disk and the
                # next start runs it, so say so (and leave the A→B marker).
                self._installed_at_shutdown(job, *job.installed)
            else:
                self._finish(job, "cancelled",
                             self._shutdown_section(job) if self._shutting_down else None)
            raise
        except Exception:
            log.exception("update.failed job=%s", job.id)
            self._finish(job, "failed", i18n.for_chat(job.chat_id).html(_UNEXPECTED))
        finally:
            if not restart_scheduled:
                self._release_lock(job)
            self._status_cache = None
            # The versions have moved: a button from before this job must
            # not start another one.
            self._checked = None
            self._job_epoch += 1
            try:
                await self._report(job)
            except Exception:
                log.debug("final update report failed", exc_info=True)
        if in_place is not None:
            # Only now, with "installed, restarting…" on the card: the stop
            # this asks for tears the bot down.
            await self._request_in_place(job, in_place)
        elif restart_scheduled:
            self._spawn(self._restart_watchdog(job))

    async def _await_with_heartbeat(self, job: _Job, label: str, fn, *args):
        """Run blocking ``fn`` in a thread, editing a heartbeat meanwhile.
        ``label`` is the step's name, HTML, in the job chat's language."""
        tr = i18n.for_chat(job.chat_id)
        started = time.monotonic()
        job.live = tr.html(_STEP_STARTED, step=Safe(label))
        await self._report(job)
        fut = asyncio.ensure_future(asyncio.to_thread(fn, *args))
        while True:
            done, _ = await asyncio.wait(
                {fut}, timeout=max(0.01, float(self_update.PROGRESS_EDIT_MIN_SECONDS)))
            if done:
                return fut.result()
            job.live = tr.html(_STEP_STILL, step=Safe(label), elapsed=Safe(
                _elapsed_label(time.monotonic() - started, tr)))
            await self._report(job, ornament=True)

    # ---- Claude Code -------------------------------------------------------

    def _session_note(self, job: _Job, new_version: str | None) -> str:
        from aipager.state import Status

        registry = self.bot.registry
        everyone = {n: s for n, s in registry.all_sessions().items()
                    if s.status != Status.GONE}
        mine = {n: s for n, s in registry.all_sessions(job.chat_id).items()
                if s.status != Status.GONE}
        others = len(set(everyone) - set(mine))
        tr = i18n.for_chat(job.chat_id)
        lines = [tr.html(_SN_NONE_RESTARTED)]
        if mine:
            lines.append(tr.html(_SN_OLD, labels=tr.join(
                s.label or s.name for _, s in sorted(mine.items()))))
        if others:
            lines.append(tr.n(_SN_OTHERS, others))
        if not mine and not others:
            lines.append(tr.html(_SN_NONE))
        if new_version:
            lines.append(tr.html(_SN_NEW, version=str(new_version)))
        return "\n".join(lines)

    async def _claude_step(self, job: _Job) -> None:
        job.phase = "claude_updating"
        tr = i18n.for_chat(job.chat_id)
        res = await self._await_with_heartbeat(job, tr.html(_STEP_CLAUDE),
                                               self_update.run_claude_update)
        job.live = None
        if job.interrupted or (self._shutting_down
                               and res.error == self_update.SHUTTING_DOWN_ERROR):
            self._finish(job, "cancelled", self._shutdown_section(job))
            return
        if res.error and res.before is None:
            section = tr.html(_CC_ERROR, error=_safe_tail(res.error))
        else:
            if res.timed_out:
                head = tr.html(_CC_TIMEOUT, seconds=self_update.CLAUDE_UPDATE_TIMEOUT_SECONDS)
            elif res.returncode != 0 or res.error:
                head = tr.html(_CC_FAILED, code=str(
                    res.returncode if res.returncode is not None else "?"))
            elif res.after and res.before and res.after != res.before:
                head = tr.html(_CC_DONE, before=str(res.before), after=str(res.after))
            elif res.after is None and self._shutting_down:
                # The version probe is not started during a shutdown.
                head = tr.html(_CC_DONE_SHUTDOWN)
            else:
                head = tr.html(_CC_CURRENT_DONE, version=str(res.after or res.before))
            if not res.ok:
                head += _pre_tail(res.output_tail, job.chat_id, tr)
            section = head + "\n" + self._session_note(job, res.after or res.before)
        job.sections.append(section)
        job.summary = _plain(section)
        if job.kind == "claude":
            job.phase = "done" if res.ok else "failed"
        await self._report(job)

    # ---- aipager -------------------------------------------------------------

    def _manual_restart_text(self, plan, tr=None) -> str:
        """How to restart by hand, in *tr*'s language (English when None);
        the plan's reason is English data."""
        tr = tr or i18n.get("en")
        return _manual_restart_html(plan.reason, plan.manual_command, plan.at_risk, tr)

    def _after_install_text(self, job: _Job, plan, tr, new: str) -> str:
        """What follows "A → B installed." when the daemon does not restart
        itself: Restart now (in place), systemd's manual text, or the exact
        steps for this setup."""
        if plan.in_place:
            job.can_restart = True
            self._waiting = str(new)
            return tr.html(_AP_RESTART_OFFER)
        return self._steps_text(plan, tr, new)

    def _steps_text(self, plan, tr, new: str) -> str:
        """The manual way: systemd's "Restart needed" text, else the exact
        steps for this setup."""
        if plan.steps == "systemd":
            return self._manual_restart_text(plan, tr)
        return restart_steps_text(plan.to_dict(), tr, to=new, html=True)

    async def _aipager_step(self, job: _Job) -> bool:
        registry = self.bot.registry
        tr = i18n.for_chat(job.chat_id)
        source = install_source.detect_install_source()
        running = self_update.running_version()
        ver = {"frm": str(running)}
        if not source.upgradable:
            log.info("update.aipager.refused job=%s kind=%s", job.id, source.kind)
            self._finish(job, "failed", tr.html(_AP_BLOCKED, reason=str(source.reason)))
            return False
        argv = install_source.upgrade_argv(source)
        if argv is None:
            self._finish(job, "failed",
                         tr.html(_AP_NOT_FOUND, kind=str(source.kind),
                                 path=str(install_source.augmented_path())))
            return False
        plan = await asyncio.to_thread(self_update.restart_plan, registry)

        if source.origin == "index":
            latest = await asyncio.to_thread(self_update.latest_aipager_version)
            if latest and not self_update.is_newer(latest, running):
                log.info("update.aipager.unchanged job=%s running=%s latest=%s",
                         job.id, running, latest)
                self._finish(job, "done", tr.html(_AP_UP_TO_DATE, version=str(running)))
                return False

        if plan.automatic:
            if await self._wait_gate(job) == "cancel":
                self._finish(job, "cancelled", tr.html(_AP_CANCELLED))
                return False

        job.phase = "upgrading"
        log.info("update.aipager.start job=%s argv=%s", job.id, argv)
        res = await self._await_with_heartbeat(
            job, tr.html(_STEP_AIPAGER, kind=str(source.kind)),
            lambda: self_update.run_command(
                argv, timeout=self_update.UPGRADE_TIMEOUT_SECONDS,
                env=install_source.upgrade_env(source)))
        job.live = None
        if job.interrupted or (self._shutting_down
                               and res.error == self_update.SHUTTING_DOWN_ERROR):
            self._finish(job, "cancelled", self._shutdown_section(job))
            return False
        if res.timed_out:
            log.info("update.aipager.timeout job=%s", job.id)
            hint = install_source.reinstall_hint(source.kind)
            self._finish(job, "failed",
                         tr.html(_AP_TIMEOUT, seconds=self_update.UPGRADE_TIMEOUT_SECONDS,
                                 running=str(running), hint=str(hint))
                         + _pre_tail(res.output_tail, job.chat_id, tr))
            return False
        if res.returncode != 0 or res.error:
            log.info("update.aipager.failed job=%s rc=%s", job.id, res.returncode)
            self._finish(job, "failed",
                         tr.html(_AP_FAILED, code=str(
                             res.returncode if res.returncode is not None else "?"))
                         + _pre_tail(res.output_tail or res.error, job.chat_id, tr))
            return False

        new, importable, err = await asyncio.to_thread(
            self_update.probe_installed_version, source.python)
        if job.interrupted:
            # The shutdown killed the probe: the install may be partial.
            self._finish(job, "cancelled", self._shutdown_section(job))
            return False
        if self._shutting_down and err == self_update.SHUTTING_DOWN_ERROR:
            # Installed within the shutdown grace: the seam spawned no probe
            # (nothing new starts now), and no restart follows.
            self._installed_unprobed_at_shutdown(job, running)
            return False
        if not importable:
            log.info("update.aipager.smoke_failed job=%s error=%s", job.id, err)
            hint = install_source.reinstall_hint(source.kind)
            self._finish(job, "failed",
                         tr.html(_AP_IMPORT_FAILED, running=str(running), hint=str(hint))
                         + _pre_tail(err, job.chat_id, tr))
            return False
        if new == running:
            log.info("update.aipager.unchanged job=%s version=%s origin=%s",
                     job.id, running, source.origin)
            self._finish(job, "done", _already_at(tr, source, str(running), job.chat_id))
            return False
        ver["to"] = str(new)
        log.info("update.aipager.done job=%s from=%s to=%s", job.id, running, new)
        job.installed = (running, new)
        if self._shutting_down:
            self._installed_at_shutdown(job, running, new)
            return False

        if not plan.automatic:
            log.info("update.restart.refused job=%s mode=%s in_place=%s", job.id, plan.mode,
                     plan.in_place)
            self._finish(job, "done", tr.html(_AP_INSTALLED_OK, **ver) + "\n"
                         + self._after_install_text(job, plan, tr, new))
            return False

        if not job.restart_now:
            job.waiting_section = tr.html(_AP_WAITING, **ver)
            job.sections.append(job.waiting_section)
            if await self._wait_gate(job) == "cancel":
                self._drop_waiting_section(job)
                self._finish(job, "cancelled", tr.html(
                    _AP_RESTART_CANCELLED, **ver, cmd=str(plan.manual_command)))
                return False
            self._drop_waiting_section(job)

        plan = await asyncio.to_thread(self_update.restart_plan, registry)
        if self._shutting_down:
            # The gate or the re-read can span the start of a shutdown.
            self._installed_at_shutdown(job, running, new)
            return False
        if not plan.automatic:
            log.info("update.restart.refused job=%s mode=%s in_place=%s (re-read)", job.id,
                     plan.mode, plan.in_place)
            self._finish(job, "done", tr.html(_AP_INSTALLED_OK, **ver) + "\n"
                         + self._after_install_text(job, plan, tr, new))
            return False

        try:
            self_update.write_marker(self._update_marker(job, running, new))
        except OSError as e:
            log.info("update.restart.failed job=%s marker: %s", job.id, e)
            self._finish(job, "failed", tr.html(
                _AP_PREPARE_FAILED, **ver, error=str(e), cmd=str(plan.manual_command)))
            return False
        ok, detail = await asyncio.to_thread(self_update.schedule_restart, plan)
        if self._shutting_down:
            # Shutdown began while systemd-run ran. A timer that did get
            # made must not bring a stopped daemon back 5 s later.
            if ok:
                await asyncio.to_thread(self_update.cancel_scheduled_restart, detail,
                                        during_shutdown=True)
            self._installed_at_shutdown(job, running, new)
            return False
        if not ok:
            self_update.clear_marker()
            self._finish(job, "failed", tr.html(
                _AP_SCHEDULE_FAILED, **ver, detail=str(detail), cmd=str(plan.manual_command)))
            return False
        job.restart_unit = detail
        # No countdown (roadmap 8.45): the new daemon edits this message
        # into "✅ aipager updated A → B, N sessions re-adopted".
        self._finish(job, "restart_scheduled", tr.html(_AP_RESTARTING, **ver))
        return True

    # ---- Restart now (kind "restart") ---------------------------------------------

    async def _probe_target(self, job: _Job, running: str):
        """``(target, new)`` when a newer aipager is installed and loads;
        otherwise the job is finished and ``(None, None)`` comes back."""
        tr = i18n.for_chat(job.chat_id)
        target = self_restart.exec_target()
        if target is not None:
            new, importable, err = await asyncio.to_thread(
                self_update.probe_restart_target, target)
        else:
            # No interpreter to exec (systemd still restarts through its
            # unit): what the install itself imports.
            source = install_source.detect_install_source()
            new, importable, err = await asyncio.to_thread(
                self_update.probe_installed_version, source.python)
        if self._shutting_down and err == self_update.SHUTTING_DOWN_ERROR:
            if job.installed is not None:
                # The first check found B: the next start runs it.
                self._installed_at_shutdown(job, *job.installed)
            else:
                self._finish(job, "cancelled", self._shutdown_section(job))
            return None, None
        if not importable:
            log.info("update.restart.probe_failed job=%s error=%s", job.id, err)
            hint = install_source.reinstall_hint(install_source.detect_install_source().kind)
            self._finish(job, "failed", tr.html(_RS_BROKEN, running=str(running),
                                                hint=str(hint))
                         + _pre_tail(err, job.chat_id, tr))
            return None, None
        if not self_update.is_newer(new, running):
            log.info("update.restart.nothing job=%s running=%s installed=%s", job.id,
                     running, new)
            self._waiting = None
            self._finish(job, "done", tr.html(_RS_NOTHING, running=str(running)))
            return None, None
        return target, str(new)

    async def _restart_step(self, job: _Job):
        """Restart now. Returns ``_InPlace`` (exec once the final report is
        out), ``"systemd"`` (a restart is scheduled) or None (finished)."""
        registry = self.bot.registry
        tr = i18n.for_chat(job.chat_id)
        running = self_update.running_version()

        def checking() -> None:
            job.phase = "verifying"
            job.blockers = []
            job.live = tr.html(_RS_CHECKING)

        checking()
        await self._report(job)
        target, new = await self._probe_target(job, running)
        if new is None:
            return None
        job.installed = (running, new)
        plan = await asyncio.to_thread(self_update.restart_plan, registry)
        if self._shutting_down:
            self._installed_at_shutdown(job, running, new)
            return None
        if not plan.automatic and not plan.in_place:
            log.info("update.restart.refused job=%s mode=%s", job.id, plan.mode)
            self._finish(job, "done", self._steps_text(plan, tr, new))
            return None

        # One rule for every restart: never while a turn is running.
        if await self._wait_gate(job) == "cancel":
            self._finish(job, "cancelled", tr.html(_RS_CANCELLED, running=str(running)))
            return None

        # The verification that counts: right before the restart.
        checking()
        await self._report(job)
        plan = await asyncio.to_thread(self_update.restart_plan, registry)
        if self._shutting_down:
            self._installed_at_shutdown(job, running, new)
            return None
        target, new = await self._probe_target(job, running)
        if new is None:
            return None
        job.installed = (running, new)
        if self._shutting_down:
            self._installed_at_shutdown(job, running, new)
            return None
        if not plan.automatic and not (plan.in_place and target is not None):
            log.info("update.restart.refused job=%s mode=%s (re-read)", job.id, plan.mode)
            self._finish(job, "done", self._steps_text(plan, tr, new))
            return None

        ver = {"frm": str(running), "to": str(new)}
        manual = plan.manual_command or " ".join(("aipager", "start"))
        try:
            self_update.write_marker(self._update_marker(job, running, new))
        except OSError as e:
            log.info("update.restart.failed job=%s marker: %s", job.id, e)
            self._finish(job, "failed", tr.html(
                _AP_PREPARE_FAILED, **ver, error=str(e), cmd=str(manual)))
            return None
        if plan.automatic:
            ok, detail = await asyncio.to_thread(self_update.schedule_restart, plan)
            if self._shutting_down:
                if ok:
                    await asyncio.to_thread(self_update.cancel_scheduled_restart, detail,
                                            during_shutdown=True)
                self._installed_at_shutdown(job, running, new)
                return None
            if not ok:
                self_update.clear_marker()
                self._finish(job, "failed", tr.html(
                    _AP_SCHEDULE_FAILED, **ver, detail=str(detail), cmd=str(manual)))
                return None
            job.restart_unit = detail
            self._finish(job, "restart_scheduled", tr.html(_AP_RESTARTING, **ver))
            return "systemd"
        log.info("update.restart.in_place job=%s from=%s to=%s", job.id, running, new)
        self._finish(job, "restart_scheduled", tr.html(_AP_RESTARTING, **ver))
        return _InPlace(target=target, frm=str(running), to=str(new), steps=plan.steps,
                        plan=plan.to_dict())

    async def _request_in_place(self, job: _Job, step: _InPlace) -> None:
        """Ask the daemon to stop and exec the new aipager; if it cannot,
        say so with the steps, and free everything."""
        if self_restart.request(step.target, frm=step.frm, to=step.to, steps=step.steps):
            return
        log.warning("update.restart.failed job=%s reason=in-place-not-possible", job.id)
        self_update.clear_marker()
        self._release_lock(job)
        tr = i18n.for_chat(job.chat_id)
        plan = dict(step.plan) or {"steps": step.steps}
        job.sections = [tr.html(_RS_NOT_POSSIBLE) + "\n"
                        + restart_steps_text(plan, tr, to=step.to, html=True)]
        job.summary = _plain(job.sections[-1])
        job.live = None
        job.phase = "failed"
        try:
            await self._report(job)
        except Exception:
            log.debug("restart failure report failed", exc_info=True)

    # ---- hooks newer than this daemon --------------------------------------------

    def note_hook_version(self, version) -> None:
        """A hook or the status line runs aipager *version*. Cheap, sync,
        never raises: tells the owner once per newer version per run."""
        try:
            if self_update.parse_version(version) is None:
                return
            mine = _daemon_code_version()
            if not self_update.is_newer(version, mine):
                return
            rec = self._hook_seen.get(version)
            if rec is None:
                if len(self._hook_seen) >= _HOOK_SEEN_MAX:
                    return
                rec = {"delivered": False, "attempts": 0, "last_try_mono": None,
                       "last_try": None, "task": None, "deferred": None,
                       "skip_logged": False}
                self._hook_seen[version] = rec
                log.info("update.hooks_newer hook=%s daemon=%s", version, mine)
            if self._hooks_newer is None or self_update.is_newer(version, self._hooks_newer):
                self._hooks_newer = version
            self._try_hook_notice(version, rec)
        except Exception:
            log.debug("hook version note failed", exc_info=True)

    def hook_notice_tick(self) -> None:
        """The session monitor's tick: a notice held back while the owner's
        chat was not clear goes out once it is (a hook datagram mostly
        arrives mid-turn, so waiting for the next one is not enough).
        Cheap and sync; nothing at all when no notice is pending."""
        try:
            for version, rec in list(self._hook_seen.items()):
                self._try_hook_notice(version, rec)
        except Exception:
            log.debug("hook notice tick failed", exc_info=True)

    def _try_hook_notice(self, version: str, rec: dict) -> None:
        """Start the notice for *version* when it is owed, due and the
        owner's chat is clear. Holding it back costs no attempt and logs
        only when the reason changes; no task is made for it."""
        if rec["delivered"] or rec["attempts"] >= HOOK_NOTICE_MAX_ATTEMPTS:
            return
        task = rec["task"]
        if task is not None and not task.done():
            return
        last = rec["last_try_mono"]
        if last is not None:
            wait = (HOOK_NOTICE_SKIP_RETRY_SECONDS if rec["last_try"] == "skipped"
                    else HOOK_NOTICE_RETRY_SECONDS)
            if time.monotonic() - last < wait:
                return
        if self._hook_notice_covered(version):
            rec["delivered"] = True
            log.info("update.hooks_newer.notice hook=%s result=covered", version)
            return
        from aipager.bot import report_flow

        owner = None
        reason = None
        if self.busy or self._shutting_down:
            reason = "update-job"
        else:
            owner = report_flow.resolve_owner(self.bot)
            if owner is None:
                rec["delivered"] = True
                log.info("update.hooks_newer.no_owner hook=%s", version)
                return
            reason = self._owner_chat_wait_reason(owner)
        if reason is not None:
            if reason == "owner-chat-not-clear":
                # Muted, a 429 wait, a warning regime: these last a while,
                # so the (dearer) chat check is not redone every tick.
                rec["last_try_mono"], rec["last_try"] = time.monotonic(), "skipped"
            if rec["deferred"] != reason:
                rec["deferred"] = reason
                log.info("update.hooks_newer.notice hook=%s result=deferred reason=%s",
                         version, reason)
            return
        rec["deferred"] = None
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return          # nothing to send it from (never in the daemon)
        rec["task"] = self._spawn(self._hook_notice(version, owner))

    def _owner_chat_wait_reason(self, owner: int) -> str | None:
        """Why an unsolicited message to the owner's chat should wait, or
        None: a turn (or its card, answer, question or background agent) is
        live there, an answer is held for it, or the chat is muted, in a 429
        wait, a warning regime or minimal mode. The per-session predicates
        and the held count are the ones ``self_update.restart_blockers``
        asks, scoped to the owner's chat."""
        from aipager.bot import report_offer
        from aipager.state import Status, session_in_chat

        now = time.monotonic()
        registry = getattr(self.bot, "registry", None)
        sessions = registry.all_sessions() if registry is not None else {}
        for sess in sessions.values():
            if sess.status in (Status.GONE, Status.UNKNOWN):
                continue
            if not session_in_chat(sess, owner):
                continue
            if (sess.status in (Status.BUSY, Status.INTERACTIVE)
                    or (sess.busy_msg_id or 0) > 0 or sess.busy_card_owed
                    or sess.notify_in_flight or sess.pending_permission is not None
                    or sess.stack_top_kind() == "compacting"
                    or sess.work_in_flight(now)
                    or sess.job_background_open()):
                return "owner-chat-busy"
        from aipager.bot.held import HELD

        if HELD.count(owner):
            return "owner-chat-held-answers"
        if not report_offer.dm_clear(owner):
            return "owner-chat-not-clear"
        return None

    def _hook_notice_covered(self, version: str) -> bool:
        """A job card already offers (or runs) the restart into *version*."""
        job = self._job
        if job is None or job.installed is None or job.installed[1] != version:
            return False
        return not job.terminal or self.restart_pending or job.can_restart

    async def _hook_notice(self, version: str, owner: int) -> None:
        rec = self._hook_seen[version]
        mine = _daemon_code_version()
        tr = i18n.for_chat(owner)
        app = getattr(self.bot, "_app", None)
        sent = None
        if app is not None:
            try:
                sent = await send_text(
                    app.bot, owner, tr.html(_HOOKS_NEWER, hook=str(version), running=str(mine)),
                    parse_mode="HTML", reply_markup=restart_markup(tr),
                    disable_notification=True,
                    rate_limit_args=rate_limit_args(kind="skip", priority=PRIORITY_ORNAMENT))
            except FloodMuted:
                sent = MUTED
            except Exception:
                log.debug("hooks newer notice failed", exc_info=True)
                sent = None
        if sent is MUTED or sent is SKIPPED:
            # The outbound gate held it back: nothing reached Telegram, so
            # no attempt is spent; tried again shortly. Logged once per
            # streak: the gate can stay tight for a long while.
            rec["last_try_mono"], rec["last_try"] = time.monotonic(), "skipped"
            if not rec.get("skip_logged"):
                rec["skip_logged"] = True
                log.info("update.hooks_newer.notice hook=%s result=skipped", version)
            return
        rec["skip_logged"] = False
        if sent is None:
            rec["attempts"] += 1
            rec["last_try_mono"], rec["last_try"] = time.monotonic(), "failed"
            log.info("update.hooks_newer.notice hook=%s result=failed attempt=%d",
                     version, rec["attempts"])
            return
        rec["delivered"] = True
        log.info("update.hooks_newer.notice hook=%s result=sent", version)

    @staticmethod
    def _drop_waiting_section(job: _Job) -> None:
        if job.waiting_section is not None and job.waiting_section in job.sections:
            job.sections.remove(job.waiting_section)
        job.waiting_section = None

    async def _wait_gate(self, job: _Job) -> str:
        """``"open"``, ``"bypass"`` or ``"cancel"``."""
        if job.restart_now:
            log.info("update.gate.bypass job=%s", job.id)
            job.phase = "upgrading"
            return "bypass"
        loop = asyncio.get_running_loop()
        job.phase = "waiting_for_idle"
        deadline = loop.time() + self_update.GATE_MAX_WAIT_SECONDS
        decision_deadline = None
        streak = 0
        log.info("update.gate.wait job=%s", job.id)
        first = True
        while True:
            if job.restart_now:
                log.info("update.gate.bypass job=%s", job.id)
                job.blockers = []
                job.phase = "upgrading"
                return "bypass"
            if job.cancel_requested:
                log.info("update.gate.cancel job=%s", job.id)
                job.blockers = []
                return "cancel"
            blockers = self_update.restart_blockers(self.bot.registry)
            changed = blockers != job.blockers
            job.blockers = blockers
            streak = 0 if blockers else streak + 1
            if streak >= self_update.GATE_SETTLE_POLLS:
                log.info("update.gate.open job=%s", job.id)
                # Past the gate nothing may be cancelled any more: the
                # installer or the restart is about to run.
                job.phase = "upgrading"
                job.live = None
                return "open"
            now = loop.time()
            phase_changed = False
            if job.phase == "gate_timeout" and job.wait_more:
                job.wait_more = False
                job.phase = "waiting_for_idle"
                deadline = now + self_update.GATE_MAX_WAIT_SECONDS
                decision_deadline = None
                phase_changed = True
            if job.phase == "waiting_for_idle" and now >= deadline:
                log.info("update.gate.timeout job=%s blockers=%s", job.id, blockers)
                job.phase = "gate_timeout"
                decision_deadline = now + self_update.GATE_DECISION_MAX_SECONDS
                phase_changed = True
            if (job.phase == "gate_timeout" and decision_deadline is not None
                    and now >= decision_deadline):
                log.info("update.gate.cancel job=%s reason=unanswered", job.id)
                job.blockers = []
                return "cancel"
            job.live = self._gate_live(job)
            if first or phase_changed:
                await self._report(job)
            elif changed:
                await self._report(job, ornament=True)
            first = False
            job.event.clear()
            try:
                await asyncio.wait_for(job.event.wait(),
                                       timeout=self_update.GATE_POLL_SECONDS)
            except asyncio.TimeoutError:
                pass

    def _update_marker(self, job: _Job, running: str, new: str) -> dict:
        return {
            "from": running, "to": new, "chat_id": job.chat_id,
            "user_id": job.user_id, "job_id": job.id,
            "sessions": [
                {"name": s.name, "label": s.label}
                for s in self.bot.registry.all_sessions().values()
                if s.status.name != "GONE"
            ],
            "scheduled_at": time.time(),
            "message": self._message_ref(job),
        }

    @staticmethod
    def _message_ref(job: _Job) -> dict | None:
        """The job's status message, for the next daemon to edit into the
        outcome (roadmap 8.45); ``None`` when there is none to edit (a
        Mini App job whose first send never went out)."""
        msg = job.reporter.message if job.reporter is not None else None
        # None or MUTED (no message went out) has no int ids: None below.
        chat_id = _message_chat_id(msg)
        message_id = getattr(msg, "message_id", None)
        if not all(isinstance(v, int) and not isinstance(v, bool)
                   for v in (chat_id, message_id)):
            return None
        # Every caller drops the waiting section first: the restart (or
        # the next start) replaces it.
        return {"chat_id": chat_id, "message_id": message_id,
                "prefix": "\n\n".join(job.sections)}

    def _installed_unprobed_at_shutdown(self, job: _Job, running: str) -> None:
        """The installer finished but the daemon is stopping before the
        import probe: read the version on disk in-process, leave the A→B
        marker, and let the next start run it."""
        new = self_update.installed_version()
        if not new or new == running:
            log.info("update.restart.skipped job=%s reason=shutting-down to=%s",
                     job.id, new)
            self._finish(job, "done", i18n.for_chat(job.chat_id).html(_AP_FINISHED_SHUTDOWN))
            return
        self._installed_at_shutdown(job, running, new)

    def _installed_at_shutdown(self, job: _Job, running: str, new: str) -> None:
        """B is installed but the daemon is stopping: never schedule a
        restart (it would undo an operator's stop). Leave the normal A→B
        marker so the next start announces B."""
        log.info("update.restart.skipped job=%s reason=shutting-down from=%s to=%s",
                 job.id, running, new)
        self._drop_waiting_section(job)
        try:
            self_update.write_marker(self._update_marker(job, running, new))
        except OSError:
            log.warning("update.shutdown.marker_failed job=%s", job.id, exc_info=True)
        self._finish(job, "done", i18n.for_chat(job.chat_id).html(
            _AP_INSTALLED_SHUTDOWN, frm=str(running), to=str(new)))

    def _shutdown_section(self, job: _Job) -> str:
        tr = i18n.for_chat(job.chat_id)
        if job.interrupted:
            if job.phase == "claude_updating":
                fix = tr.html(_FIX_CLAUDE, command=Span("<code>claude update</code>"))
            else:
                src = install_source.detect_install_source()
                fix = tr.html(_FIX_REINSTALL, command=Span(
                    f"<code>{_esc(install_source.reinstall_hint(src.kind))}</code>"))
            return tr.html(_SHUTDOWN_PARTIAL, fix=fix)
        return tr.html(_SHUTDOWN_EARLY)

    async def shutdown(self) -> None:
        """Daemon shutdown: never leave an installer orphaned, never start
        anything new, and never schedule a restart.

        Called before ``bot.stop()`` so the final status edit can still go
        out, and bounded as a whole by ``SHUTDOWN_DEADLINE_SECONDS``. From
        the first line on, :meth:`start` refuses and the spawn seam starts
        nothing. A job in a gate wait is cancelled (if B is already
        installed it leaves the normal A→B marker). A job whose installer
        is running gets ``SHUTDOWN_GRACE_SECONDS`` to finish; if it does,
        the job leaves the A→B marker and schedules no restart. Otherwise
        its process group is killed — with KillMode=process it would
        survive us and keep writing the venv while the next daemon imports
        it — the kill is logged, and an "interrupted" marker makes the next
        daemon tell the admin. A pending restart's marker is left alone:
        that restart IS this shutdown. Never raises.
        """
        self._shutting_down = True
        loop = asyncio.get_running_loop()
        deadline = loop.time() + SHUTDOWN_DEADLINE_SECONDS
        mono_deadline = time.monotonic() + SHUTDOWN_DEADLINE_SECONDS

        def left(cap: float) -> float:
            return max(0.0, min(cap, deadline - loop.time()))

        job, task = self._job, self._job_task
        try:
            self_update.begin_shutdown(mono_deadline)
            if job is not None and task is not None and not task.done():
                if job.phase in _INSTALLING:
                    done, _ = await asyncio.wait(
                        {task}, timeout=left(SHUTDOWN_GRACE_SECONDS))
                    if not done and self_update.running_command_count():
                        phase = job.phase
                        job.interrupted = True
                        # Keep a slice of the budget for the job's last word
                        # and the cancel below.
                        kill = asyncio.ensure_future(asyncio.to_thread(
                            self_update.terminate_running_commands))
                        killed_done, _ = await asyncio.wait(
                            {kill}, timeout=left(SHUTDOWN_DEADLINE_SECONDS) * 0.75)
                        killed = kill.result() if killed_done and not kill.exception() \
                            else -1
                        log.warning("update.shutdown.interrupted job=%s phase=%s killed=%d "
                                    "— the install may be partial", job.id, phase, killed)
                        self._write_interrupted_marker(job, phase)
                        # The killed installer returns promptly: let the job
                        # post its own "interrupted" outcome.
                        await asyncio.wait({task}, timeout=left(SHUTDOWN_GRACE_SECONDS))
                if not task.done():
                    task.cancel()
                    await asyncio.wait({task}, timeout=left(SHUTDOWN_GRACE_SECONDS))
            # The watchdog and any status lookups (the job task is handled
            # above, with its own grace).
            others = [t for t in self._tasks if not t.done() and t is not task]
            for t in others:
                t.cancel()
            if others:
                await asyncio.wait(others, timeout=left(1.0))
        except Exception:
            log.warning("update shutdown failed", exc_info=True)
        if loop.time() > deadline:
            log.warning("update.shutdown.deadline_exceeded by=%.1fs",
                        loop.time() - deadline)

    def _write_interrupted_marker(self, job: _Job, phase: str) -> None:
        try:
            self_update.write_marker({
                "interrupted": phase, "kind": job.kind, "chat_id": job.chat_id,
                "user_id": job.user_id, "job_id": job.id,
                "from": self_update.running_version(),
                "source_kind": install_source.detect_install_source().kind,
                "scheduled_at": time.time(),
            })
        except Exception:
            log.warning("update.shutdown.marker_failed job=%s", job.id, exc_info=True)

    @staticmethod
    def _gate_live(job: _Job) -> str:
        tr = i18n.for_chat(job.chat_id)
        if job.phase == "gate_timeout":
            mins = max(1, int(self_update.GATE_MAX_WAIT_SECONDS // 60))
            # The blockers are self_update's English: logic reads them, never
            # their translation.
            held = any("held answer" in b for b in job.blockers)
            return tr.n(_GATE_BUSY_HELD if held else _GATE_BUSY, mins, wait=mins)
        return tr.html(_GATE_WAITING)

    async def _restart_watchdog(self, job: _Job) -> None:
        await asyncio.sleep(self_update.RESTART_DELAY_SECONDS + RESTART_WATCHDOG_SECONDS)
        if (self._job is not job or job.phase != "restart_scheduled"
                or self._lock_owner != job.id):
            # Superseded: another job owns the lock (and maybe a marker).
            log.info("update.restart.watchdog_stale job=%s current=%s owner=%s", job.id,
                     self._job.id if self._job else None, self._lock_owner)
            return
        # Still alive: the scheduled restart never happened.
        log.warning("update.restart.failed job=%s reason=still-running-after-schedule",
                    job.id)
        # Stop the timer first, so it cannot fire in the middle of a later
        # job once the lock is free.
        try:
            await asyncio.to_thread(self_update.cancel_scheduled_restart, job.restart_unit)
        except Exception:
            log.warning("update.restart.timer_stop_failed job=%s", job.id, exc_info=True)
        self_update.clear_marker()
        self._release_lock(job)
        job.sections.append(i18n.for_chat(job.chat_id).html(_WATCHDOG, command=Span(
            f"<code>systemctl --user restart {self_update.UNIT_NAME}</code>")))
        job.summary = _plain(job.sections[-1])
        job.phase = "failed"
        await self._report(job)


def _origin_explanation(source: install_source.InstallSource, chat_id) -> str:
    """Why a finished upgrade left the same version, as an id: ``local_at``
    (a local path, shown in a private chat), ``local``, ``git``, ``index``
    or ``plain``."""
    show = isinstance(chat_id, int) and chat_id > 0
    if source.origin == "local":
        return "local_at" if show and source.origin_detail else "local"
    if source.origin == "vcs":
        return "git"
    if source.origin == "index":
        return "index"
    return "plain"


def _already_at(tr, source: install_source.InstallSource, running: str, chat_id) -> str:
    """"aipager is already at X" and why, as one sentence per origin."""
    origin = _origin_explanation(source, chat_id)
    kind = str(source.kind)
    if origin == "local_at":
        return tr.html(_AP_SAME_LOCAL_AT, running=running, kind=kind,
                       where=str(source.origin_detail))
    if origin == "local":
        return tr.html(_AP_SAME_LOCAL, running=running, kind=kind)
    if origin == "git":
        return tr.html(_AP_SAME_GIT, running=running, kind=kind)
    if origin == "index":
        return tr.html(_AP_SAME_INDEX, running=running)
    return tr.html(_AP_SAME, running=running)


# ---------------------------------------------------------------------------
# Telegram entry points
# ---------------------------------------------------------------------------

def _busy_text(mgr: "UpdateManager", tr=None) -> str:
    tr = tr or i18n.get("en")
    if mgr.restart_pending:
        return tr.html(_BUSY_RESTARTING)
    snap = mgr.snapshot() or {}
    return tr.html(_BUSY_RUNNING, phase=str(snap.get("phase", "…")))


async def _show_check(bot: "TelegramBot", message, chat_id) -> None:
    """Run the check and edit the tapped message into its result, in that
    chat's language."""
    tr = i18n.for_chat(chat_id)
    try:
        status = await bot.updates.check()
        text, kb = render_check(status, show_paths=_show_detail(chat_id), tr=tr)
    except Exception:
        log.warning("update check failed", exc_info=True)
        text = tr.html(_CHECK_FAILED_NOW)
        kb = check_prompt_markup(tr)
    try:
        await edit_message(message, text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        log.debug("update check edit failed", exc_info=True)


async def handle_update_cmd(bot: "TelegramBot", update, ctx) -> None:
    """``/update`` — admin only (and, in personal mode, the operator only)."""
    if not await bot._authorize(update):
        return
    tg_user = update.effective_user
    user_id = tg_user.id if tg_user is not None else None
    chat_id = calling_chat_id(update)
    tr = i18n.for_update(update)
    if not bot._is_update_admin(user_id, chat_id):
        log.info("update.denied user=%s chat=%s", user_id, chat_id)
        try:
            await reply_text(update.message, tr.text(DENIED_TEXT))
        except Exception:
            log.debug("update denial reply failed", exc_info=True)
        return
    mgr = bot.updates
    if mgr.busy:
        try:
            await reply_text(update.message, _busy_text(mgr, tr), parse_mode="HTML")
        except Exception:
            log.debug("update busy reply failed", exc_info=True)
        return
    # No lookup yet: the versions are checked only when the button is tapped.
    try:
        await reply_text(update.message, tr.html(CHECK_PROMPT_TEXT), parse_mode="HTML",
                         reply_markup=check_prompt_markup(tr))
    except Exception:
        log.debug("update reply failed", exc_info=True)


async def handle_callback(bot: "TelegramBot", update, query, session_name: str,
                          action: str) -> bool:
    """True iff this is an ``_:up:`` callback (handled here). Pop-ups are
    in the tapper's language, edits of the message in its chat's."""
    if session_name != "_" or not action.startswith("up:"):
        return False
    from aipager.bot.language_menu import INVALID_CALLBACK
    tg_user = getattr(query, "from_user", None)
    user_id = tg_user.id if tg_user is not None else None
    message = getattr(query, "message", None)
    chat_id = _message_chat_id(message)
    if chat_id is None:
        chat_id = calling_chat_id(update)
    pop = tapper_tr(query)
    if not bot._is_update_admin(user_id, chat_id):
        log.info("update.denied user=%s chat=%s action=%s", user_id, chat_id, action)
        await bot._safe_answer(query, pop.text(DENIED_TEXT), show_alert=True)
        return True

    edit_tr = i18n.for_chat(chat_id)
    parts = action.split(":")[1:]
    verb = parts[0] if parts else ""
    mgr = bot.updates
    if verb == "x" and len(parts) == 1:
        try:
            await edit_text(query, edit_tr.text(_CANCELLED))
        except Exception:
            log.debug("update cancel edit failed", exc_info=True)
        return True
    if verb in ("cc", "ap", "both") and len(parts) == 1:
        # A pre-8.43 per-product button: never start from it.
        log.info("update.stale_menu user=%s chat=%s action=%s", user_id, chat_id, action)
        await bot._safe_answer(query, pop.text(MENU_OUT_OF_DATE_TEXT), show_alert=True)
        return True
    if verb == "chk" and len(parts) == 1:
        if mgr.busy or mgr.shutting_down:
            text = (edit_tr.html(_SHUTTING_DOWN)
                    if mgr.shutting_down and not mgr.busy else _busy_text(mgr, edit_tr))
            try:
                await edit_text(query, text, parse_mode="HTML")
            except Exception:
                log.debug("update check refusal edit failed", exc_info=True)
            return True
        await bot._safe_answer(query, pop.text(CHECKING_TEXT))
        try:
            await edit_text(query, "🔎 " + edit_tr.text(CHECKING_TEXT))
        except Exception:
            log.debug("update checking edit failed", exc_info=True)
        # PTB handles updates one at a time: never block it on the lookups.
        mgr._spawn(_show_check(bot, message, chat_id))
        return True
    if verb == "go" and len(parts) == 2 and parts[1] in _GO_CODES:
        kind = _GO_CODES[parts[1]]
        res = await mgr.start_offer(kind, chat_id=chat_id, user_id=user_id,
                                    origin="chat", status_message=message)
        if not res.ok:
            markup = None
            if res.error in ("check_expired", "offer_changed"):
                text, markup = edit_tr.html(OFFER_STALE_TEXT), check_prompt_markup(edit_tr)
            elif res.error == "not_upgradable":
                src = install_source.detect_install_source()
                text = edit_tr.html(_AP_BLOCKED_HERE, reason=src.reason or Safe(
                    edit_tr.html(_UNKNOWN_INSTALL)))
            elif res.error == "shutting_down":
                text = edit_tr.html(_SHUTTING_DOWN)
            else:
                phase = (res.job or {}).get("phase", "…")
                text = edit_tr.html(_BUSY_SHORT, phase=str(phase))
            try:
                await edit_text(query, text, parse_mode="HTML", reply_markup=markup)
            except Exception:
                log.debug("update refusal edit failed", exc_info=True)
        return True
    if verb == "rs" and len(parts) == 1:
        # Restart now: run the installed aipager. Refusals are pop-ups, so
        # they never overwrite a live job's card.
        res = await mgr.start_restart(chat_id=chat_id, user_id=user_id, origin="chat",
                                      status_message=message)
        if not res.ok:
            if res.error == "shutting_down":
                text = pop.text(_SHUTTING_DOWN)
            elif res.detail == "lock_held":
                text = pop.text(_RESTART_LOCKED)
            else:
                text = pop.text(_BUSY_SHORT, phase=str((res.job or {}).get("phase", "…")))
            await bot._safe_answer(query, text, show_alert=True)
        return True
    if verb in ("now", "wait", "stop") and len(parts) == 2:
        try:
            job_id = int(parts[1])
        except ValueError:
            await bot._safe_answer(query, pop.text(INVALID_CALLBACK))
            return True
        control = {"now": "restart-now", "wait": "wait-more", "stop": "cancel"}[verb]
        res = mgr.control(control, job_id)
        if not res.ok:
            log.info("update.control_refused user=%s action=%s job=%s", user_id, control,
                     job_id)
            await bot._safe_answer(query, pop.text(STALE_TEXT))
        return True
    await bot._safe_answer(query, pop.text(INVALID_CALLBACK))
    return True


# ---------------------------------------------------------------------------
# After the restart: the NEW daemon announces it
# ---------------------------------------------------------------------------

async def deliver_update_marker(bot: "TelegramBot", registry) -> None:
    """Announce "aipager updated A → B, N sessions re-adopted" once, by
    editing the old daemon's "installed, restarting…" message when the
    marker names it, else (or when that edit fails) by a new message.
    Never raises."""
    from aipager.state import Status

    try:
        await asyncio.sleep(self_update.MARKER_SETTLE_SECONDS)
        marker = self_update.read_and_clear_marker()
        if not marker:
            return
        age = time.time() - float(marker.get("scheduled_at") or 0)
        if age > self_update.MARKER_MAX_AGE_SECONDS or age < -300:
            log.info("update.marker.stale age=%.0fs", age)
            return
        try:
            chat_id = int(marker["chat_id"])
        except (KeyError, TypeError, ValueError):
            log.info("update.marker.stale reason=no-chat")
            return
        frm, to = str(marker.get("from", "?")), str(marker.get("to", "?"))
        running = self_update.running_version()
        # The chat that asked for the update, in its language now.
        tr = i18n.for_chat(chat_id)
        if marker.get("interrupted"):
            if marker.get("interrupted") == "claude_updating":
                fix = tr.text(_FIX_CLAUDE, command="`claude update`")
            else:
                fix = tr.text(_FIX_REINSTALL, command="`" + install_source.reinstall_hint(
                    str(marker.get("source_kind") or "")) + "`")
            text = tr.text(_MARKER_INTERRUPTED, running=str(running), fix=Safe(fix))
        elif running != to:
            text = tr.text(_MARKER_WRONG, running=str(running), to=to)
        else:
            back, missing = 0, []
            for entry in marker.get("sessions") or []:
                if not isinstance(entry, dict):
                    continue
                sess = registry.get(entry.get("name") or "")
                if sess is not None and sess.status not in (Status.GONE, Status.UNKNOWN):
                    back += 1
                else:
                    missing.append(str(entry.get("label") or entry.get("name") or "?"))
            text = tr.n(_MARKER_DONE, back, kind="text", frm=frm, to=to)
            if missing:
                text += "\n" + tr.text(_NOT_BACK, labels=tr.join(missing))
            mgr = getattr(bot, "updates", None)
            if mgr is not None:
                mgr.last_restart = {"from": frm, "to": to, "readopted": back,
                                    "missing": missing}
        app = getattr(bot, "_app", None)
        if app is None:
            return
        ref = _marker_message_ref(marker, chat_id)
        if ref is not None:
            # Edit the old daemon's "installed, restarting…" message into
            # the outcome (roadmap 8.45). ESSENTIAL, like the send below.
            message_id, prefix = ref
            body = _esc_text(text)
            if prefix:
                body = f"{prefix}\n\n{body}"
            try:
                result = await edit_text_at(app.bot, text=body, chat_id=chat_id,
                                            message_id=message_id, parse_mode="HTML")
            except BadRequest as e:
                if "not modified" in str(e).lower():
                    log.info("update.marker.delivered chat=%s from=%s to=%s via=edit "
                             "(unchanged)", chat_id, frm, to)
                    return
                # Deleted, or too old to edit: post it instead, as before.
                log.info("update.marker.edit_failed chat=%s detail=%s", chat_id, e)
            else:
                if result is MUTED:
                    log.info("update.marker.skipped_muted chat=%s", chat_id)
                    return
                # SKIPPED cannot happen for this ESSENTIAL (blocking) edit
                # today; if it ever does, nothing went out, so send.
                if result is not SKIPPED:
                    log.info("update.marker.delivered chat=%s from=%s to=%s via=edit",
                             chat_id, frm, to)
                    return
        await _send_with_retry(app.bot, chat_id=chat_id, text=text)
        log.info("update.marker.delivered chat=%s from=%s to=%s via=send", chat_id, frm, to)
    except FloodMuted as e:
        log.info("update.marker.skipped_muted retry_after=%ss", e.retry_after)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.warning("update marker delivery failed", exc_info=True)


def _marker_message_ref(marker: dict, chat_id: int) -> tuple[int, str] | None:
    """``(message_id, prefix_html)`` of the message the marker says to
    edit, or ``None``. Only a message in the marker's own chat (the one
    that asked for the update) is ever edited."""
    ref = marker.get("message")
    if not isinstance(ref, dict):
        return None
    message_id, ref_chat = ref.get("message_id"), ref.get("chat_id")
    if not all(isinstance(v, int) and not isinstance(v, bool)
               for v in (message_id, ref_chat)) or ref_chat != chat_id:
        return None
    prefix = ref.get("prefix")
    return message_id, prefix if isinstance(prefix, str) else ""


__all__ = [
    "ControlResult", "StartResult", "UpdateManager", "UpdateOffer", "check_payload",
    "deliver_update_marker", "handle_callback", "handle_update_cmd", "installed_waiting",
    "render_check", "restart_markup", "restart_steps_text", "update_offer",
]
