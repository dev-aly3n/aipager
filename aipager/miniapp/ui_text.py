"""Every string the Mini App page shows, and the catalog the page reads.

The page (``static/_app.py`` and ``static/_shell.py``) holds keys only. Its
English is declared here as module-level :class:`~aipager.i18n.Msg` and
:class:`~aipager.i18n.Plural`, so ``tools/i18n.py extract`` sees it like
any other key and the same checks, glossary and ``todo``/``apply``
workflow cover it. The English reaches the page inlined
(:func:`english_catalog`, embedded by ``static/__init__.py``); another
language arrives from ``GET /api/i18n/<code>.json`` (:func:`catalog`)
when the server tells the page its viewer reads one (:func:`ui_payload`).

The catalog files are read through ``aipager.i18n.core._catalog``, the
package's own read-once reader: this module is the one place outside the
package that does (a public accessor is a later one-line follow-up).

Imports only :mod:`aipager.i18n`, so :mod:`aipager.miniapp.sessions` and
the page builder can import it cheaply, and nothing in the hook's import
closure reaches it.
"""

from __future__ import annotations

import hashlib
import json

from aipager import i18n
from aipager.i18n import Msg, Plural, core, locales, plurals

__all__ = ["AGO_D", "AGO_H", "AGO_M", "AGO_S", "PAGE_STRINGS", "REPORT_TRY_LATER",
           "canonical", "catalog", "catalog_version", "english_catalog", "ui_payload"]

# ---- shared with the server ------------------------------------------------

#: "5m ago": a session's last activity, on the page and in its facts.
AGO_S = Msg("ui.age.seconds", "{n}s ago", eg={"n": 30},
            note="Mini App: how long ago a session was last active, in seconds "
                 "(short, like a clock: 30s ago)")
AGO_M = Msg("ui.age.minutes", "{n}m ago", eg={"n": 5},
            note="Mini App: how long ago, in minutes (short: 5m ago)")
AGO_H = Msg("ui.age.hours", "{n}h ago", eg={"n": 2},
            note="Mini App: how long ago, in hours (short: 2h ago)")
AGO_D = Msg("ui.age.days", "{n}d ago", eg={"n": 3},
            note="Mini App: how long ago, in days (short: 3d ago)")

#: The page's copy of the report module's try-later line
#: (``aipager.report.wording.TRY_LATER``; a test keeps the two equal).
REPORT_TRY_LATER = Msg("ui.report.try_later",
                       "Could not send right now. Nothing was lost; try again later.",
                       note="Mini App problem report: a report could not be sent now")

# ---- the page ---------------------------------------------------------------------
# Notes say where a string shows. {session} is a session's name, {model} a
# model id, {path}/{folder} a folder: names, kept as they are.

# The connection and the header.
CONN_LIVE = Msg("ui.conn.live", "live", note="Mini App: the connection badge, connected (short)")
CONN_RECONNECTING = Msg("ui.conn.reconnecting", "reconnecting…",
                        note="Mini App: the connection badge, trying again (short)")
CONN_OFFLINE = Msg("ui.conn.offline", "offline", note="Mini App: the connection badge (short)")
CONN_EXPIRED = Msg("ui.conn.expired", "expired",
                   note="Mini App: the connection badge, the sign-in ran out (short)")
CONN_UNREACHABLE = Msg("ui.conn.unreachable", "Can't reach the server. Check the tunnel.",
                       note="Mini App: the page cannot reach aipager")
CONN_SESSION_EXPIRED = Msg("ui.conn.session_expired",
                           "Session expired. Reopen it from {command} in Telegram.",
                           eg={"command": "/app"},
                           note="Mini App: the page's sign-in ran out (session here means the "
                                "page's sign-in); {command} opens the Mini App")
CONN_NO_INIT = Msg("ui.conn.no_init", "Open this page from the Telegram app to sign in.",
                   note="Mini App: the page was opened outside Telegram")
SHELL_LOADING = Msg("ui.shell.loading", "Loading…", note="Mini App: before the first answer")
DAEMON_UPTIME = Msg("ui.daemon.uptime", "up {n}m", eg={"n": 10},
                    note="Mini App header: how long aipager has run, in minutes (short)")
SHELL_CHAT_ARIA = Msg("ui.shell.chat_aria", "Chat",
                      note="Mini App: screen-reader name of the chat switcher")
SHELL_SECTIONS_ARIA = Msg("ui.shell.sections_aria", "Sections",
                          note="Mini App: screen-reader name of the tab bar")
TAB_SESSIONS = Msg("ui.tab.sessions", "Sessions", note="Mini App tab")
TAB_SETTINGS = Msg("ui.tab.settings", "Settings", note="Mini App tab")
SHELL_NEW_SESSION_ARIA = Msg("ui.shell.new_session_aria", "New session",
                             note="Mini App: screen-reader name of the + button")
SHELL_ACTIONS_ARIA = Msg("ui.shell.actions_aria", "Session actions",
                         note="Mini App: screen-reader name of the session menu button")
SHELL_NEW_NAME_ARIA = Msg("ui.shell.new_name_aria", "New name",
                          note="Mini App rename: screen-reader name of the name field")

# The grid.
STATE_BUSY = Msg("ui.state.busy", "working", note="Mini App: a session's state (short)")
STATE_IDLE = Msg("ui.state.idle", "resting", note="Mini App: a session's state, idle (short)")
STATE_WAITING = Msg("ui.state.waiting", "needs you",
                    note="Mini App: a session's state, waiting on a person (short)")
STATE_GONE = Msg("ui.state.gone", "finished",
                 note="Mini App: a session's state, ended (short)")
STATE_UNKNOWN = Msg("ui.state.unknown", "unknown",
                    note="Mini App: a session's state, not reported yet (short)")
STATE_BACKGROUND = Msg("ui.state.background", "in the background",
                       note="Mini App: a session's state (short): its conversation moved into "
                            "a Claude Code background job, nothing is typed into it until it "
                            "is brought back")
TILE_NO_FOLDER = Msg("ui.tile.no_folder", "no folder",
                     note="Mini App grid: a session without a folder (short)")
TILE_ARIA = Msg("ui.tile.aria", "{session}, {state}, context {pct}%, {model}, {cost}",
                eg={"session": "x1", "state": "working", "pct": 40, "model": "Opus",
                    "cost": "$0.50"},
                note="Mini App grid: a session tile read aloud; context is how full "
                     "Claude's context is")
TILE_ARIA_NO_MODEL = Msg("ui.tile.aria_no_model", "{session}, {state}, context {pct}%, {cost}",
                         eg={"session": "x1", "state": "working", "pct": 40,
                             "cost": "$0.50"},
                         note="Mini App grid: a session tile read aloud, no model known")
GRID_NEEDS_YOU = Msg("ui.grid.needs_you", "Needs you",
                     note="Mini App grid: the heading over sessions waiting on a person")
GRID_FINISHED_N = Plural("ui.grid.finished_n", one="Finished ({n})", other="Finished ({n})",
                         note="Mini App grid: the shelf of ended sessions")
BEACON_OPEN_ARIA = Msg("ui.beacon.open_aria", "Open {session}", eg={"session": "x1"},
                       note="Mini App: screen-reader name of a session's open button")
KIND_QUESTION = Msg("ui.kind.question", "Question",
                    note="Mini App: Claude asks a question (a chip)")
KIND_PERMISSION = Msg("ui.kind.permission", "Permission",
                      note="Mini App: Claude asks permission for a tool (a chip)")
BEACON_ASKING = Msg("ui.beacon.asking", "Claude is asking you something.")
BEACON_WANTS_TOOL = Msg("ui.beacon.wants_tool", "Claude wants to use a tool.")
PULSE_NEED = Plural("ui.pulse.need", one="{n} needs you", other="{n} need you",
                    note="Mini App grid summary: sessions waiting on a person")
PULSE_WORKING = Plural("ui.pulse.working", one="{n} working", other="{n} working",
                       note="Mini App grid summary: sessions working")
PULSE_RESTING = Plural("ui.pulse.resting", one="{n} resting", other="{n} resting",
                       note="Mini App grid summary: sessions idle")
PULSE_UNKNOWN = Plural("ui.pulse.unknown", one="{n} unknown", other="{n} unknown",
                       note="Mini App grid summary: sessions whose state is not known yet")
PULSE_BACKGROUND = Plural("ui.pulse.background", one="{n} in the background",
                          other="{n} in the background",
                          note="Mini App grid summary: sessions whose conversation is in a "
                               "Claude Code background job")
PULSE_FINISHED = Plural("ui.pulse.finished", one="{n} finished", other="{n} finished",
                        note="Mini App grid summary: ended sessions")
PULSE_QUIET = Msg("ui.pulse.all_quiet", "All quiet",
                  note="Mini App grid summary: nothing is running")
PULSE_SPENT = Msg("ui.pulse.spent", "{cost} spent", eg={"cost": "$4.21"},
                  note="Mini App grid summary: what the sessions cost")
EMPTY_TITLE = Msg("ui.empty.title", "No sessions yet.")
EMPTY_TEXT = Msg("ui.empty.text", "Start one here or send {command} in the chat.",
                 eg={"command": "/new"})
BTN_NEW_SESSION = Msg("ui.btn.new_session", "New session", note="Mini App button")

# Answer in chat.
BTN_ANSWER = Msg("ui.btn.answer_in_chat", "Answer in chat",
                 note="Mini App button: posts the waiting prompt into the chat")
ANSWER_CANNOT = Msg("ui.answer.cannot", "You can't answer prompts in this chat.")
ANSWER_SENT = Msg("ui.answer.sent", "Sent to the chat. Answer it there.")
ANSWER_FAILED = Msg("ui.answer.send_failed", "Couldn't send the prompt to the chat.")
ANSWER_UNREACHABLE = Msg("ui.answer.unreachable", "Couldn't reach the server. Nothing was sent.")

# The session page.
STATE_LINE_WORKING_FOR = Msg("ui.state_line.working_for", "Working for {clock}",
                             eg={"clock": "3:12"},
                             note="Mini App session page: the state line, with a clock")
STATE_LINE_WORKING = Msg("ui.state_line.working", "Working",
                         note="Mini App session page: the state line")
STATE_LINE_NEEDS_KIND = Msg("ui.state_line.needs_you_kind", "Needs you ({kind})",
                            eg={"kind": "permission"},
                            note="Mini App session page: the state line; kind is permission "
                                 "or question")
STATE_LINE_NEEDS = Msg("ui.state_line.needs_you", "Needs you",
                       note="Mini App session page: the state line")
STATE_LINE_RESTING_SINCE = Msg("ui.state_line.resting_since", "Resting, last active {age}",
                               eg={"age": "2m ago"},
                               note="Mini App session page: idle; age reads 2m ago")
STATE_LINE_RESTING = Msg("ui.state_line.resting", "Resting",
                         note="Mini App session page: the state line, idle")
STATE_LINE_FINISHED = Msg("ui.state_line.finished", "Finished",
                          note="Mini App session page: the state line, ended")
STATE_LINE_BACKGROUND = Msg("ui.state_line.background", "In Claude Code's background",
                            note="Mini App session page: the state line; the session's "
                                 "conversation moved into a Claude Code background job")
STATE_LINE_UNKNOWN = Msg("ui.state_line.unknown", "Not reported yet",
                         note="Mini App session page: the state is not known yet")
KIND_WORD_PERMISSION = Msg("ui.kind_word.permission", "permission",
                           note="Mini App: what a session waits on, inside Needs you (...)")
KIND_WORD_QUESTION = Msg("ui.kind_word.question", "question",
                         note="Mini App: what a session waits on, inside Needs you (...)")
ACTIVITY_QUEUED = Plural("ui.activity.queued", one="{n} queued", other="{n} queued",
                         note="Mini App session page: messages waiting behind the turn (a chip)")
ACTIVITY_RECENT = Msg("ui.activity.recent", "Recent activity",
                      note="Mini App session page: the latest tool calls")
ACTIVITY_TITLE = Msg("ui.activity.title", "Activity", note="Mini App session page")
ACTIVITY_RUNNING_FOR = Msg("ui.activity.running_for", "{tool} (running {clock})",
                           eg={"tool": "Bash: npm test", "clock": "0:14"},
                           note="Mini App: the last tool call, still running, with a clock")
ACTIVITY_RUNNING = Msg("ui.activity.running", "{tool} (running)",
                       eg={"tool": "Bash: npm test"},
                       note="Mini App: the last tool call, still running")
ACTIVITY_FAILED = Msg("ui.activity.failed", "{tool} (failed)", eg={"tool": "Bash: npm test"},
                      note="Mini App: the last tool call, which failed")
ACTIVITY_ARIA = Plural("ui.activity.aria",
                       one="Recent activity: {n} tool calls. Open the timeline.",
                       other="Recent activity: {n} tool calls. Open the timeline.",
                       note="Mini App: screen-reader text of the activity strip (the English "
                            "says calls even for one)")
PREVIEW_NOTHING_GONE = Msg("ui.preview.nothing_gone",
                           "Nothing was captured before this session ended.")
PREVIEW_NOTHING_YET = Msg("ui.preview.nothing_yet",
                          "Nothing captured yet. It arrives once Claude replies.")
WAITING_ASKING = Msg("ui.waiting.asking", "Claude is asking you",
                     note="Mini App session page: a question is open (a heading)")
WAITING_PERMISSION = Msg("ui.waiting.permission", "Claude needs your permission",
                         note="Mini App session page: a permission prompt is open (a heading)")
WAITING_QUESTION_OPEN = Msg("ui.waiting.question_open", "A question is open in the chat.")
WAITING_PERMISSION_OPEN = Msg("ui.waiting.permission_open",
                              "A permission prompt is open in the chat.")
DETAIL_LATEST_REPLY = Msg("ui.detail.latest_reply", "Latest reply",
                          note="Mini App session page: Claude's last message (a heading)")
DETAIL_SESSION_SETTINGS = Msg("ui.detail.session_settings", "Session settings",
                              note="Mini App session page: a heading")
DETAIL_SETTINGS_READONLY = Msg("ui.detail.settings_readonly",
                               "Only someone who can prompt this session can change these.")
BTN_RESET_DEFAULTS = Msg("ui.btn.reset_defaults", "Reset to defaults", note="Mini App button")
TIMELINE_EMPTY = Msg("ui.timeline.empty", "No activity yet.")
TIMELINE_TITLE_N = Plural("ui.timeline.title_n", one="Timeline ({n})", other="Timeline ({n})",
                          note="Mini App session page: the timeline section's heading")
TIMELINE_TITLE_EMPTY = Msg("ui.timeline.title_empty", "Timeline (empty)",
                           note="Mini App session page: the timeline section's heading")
TIMELINE_NOTE = Msg("ui.timeline.note",
                    "The timeline only covers the current daemon run. It isn't saved across "
                    "restarts, so an older session shows nothing here.",
                    note="daemon: aipager's background process")

# The model picker.
MODEL_TITLE = Msg("ui.model.title", "Model", note="Mini App: a heading over the model choices")
MODEL_SWITCHING = Msg("ui.model.switching", "switching…",
                      note="Mini App: the model is being switched (short, lower case)")
MODEL_UNCONFIRMED = Msg("ui.model.unconfirmed", "not confirmed (check the session)",
                        note="Mini App: the switch was not confirmed (short, lower case)")
MODEL_NOW = Msg("ui.model.now", "Model is now {model}.", eg={"model": "opus"})
MODEL_NOW_DEFAULT = Msg(
    "ui.model.now_default",
    "Model is now {model}, and Claude Code made it the default for new sessions too.",
    eg={"model": "opus"},
    note="after a model switch: Claude Code itself saves a switched model as the default "
         "for every new session")
MODEL_FAILED = Msg("ui.model.failed", "Couldn't switch the model.")
MODEL_UNREACHABLE = Msg("ui.model.unreachable",
                        "Couldn't reach the server. Check the session's model.")

# Changed files.
DIFF_TITLE = Msg("ui.diff.title", "Changed files",
                 note="Mini App session page: the section of uncommitted changes")
DIFF_TITLE_NONE = Msg("ui.diff.title_none", "Changed files (none)",
                      note="Mini App session page: the section's heading, nothing changed")
DIFF_TITLE_N = Plural("ui.diff.title_n", one="Changed files ({n})", other="Changed files ({n})",
                      note="Mini App session page: the section's heading")
DIFF_NOT_A_REPO = Msg("ui.diff.reason.not_a_git_repo",
                      "This session's working directory isn't a git repository.",
                      note="git is the version control program; keep it as it is")
DIFF_NO_GIT = Msg("ui.diff.reason.git_not_installed", "git isn't available on this machine.",
                  note="git is a program; keep it as it is")
DIFF_CWD_MISSING = Msg("ui.diff.reason.cwd_missing",
                       "This session's working directory no longer exists.")
DIFF_NO_COMMITS = Msg("ui.diff.reason.no_commits_yet", "This repository has no commits yet.")
DIFF_GIT_ERROR = Msg("ui.diff.reason.git_error", "git couldn't produce a diff for this session.",
                     note="git is a program; keep it as it is")
DIFF_UNAVAILABLE = Msg("ui.diff.unavailable", "Diff unavailable.")
DIFF_UNTRACKED = Msg("ui.diff.change.untracked", "untracked",
                     note="Mini App: a changed file's state, new and not in git (short)")
DIFF_DELETED = Msg("ui.diff.change.deleted", "deleted", note="Mini App: a file's change (short)")
DIFF_ADDED = Msg("ui.diff.change.added", "added", note="Mini App: a file's change (short)")
DIFF_RENAMED = Msg("ui.diff.change.renamed", "renamed", note="Mini App: a file's change (short)")
DIFF_MODIFIED = Msg("ui.diff.change.modified", "modified",
                    note="Mini App: a file's change (short)")
DIFF_BINARY = Msg("ui.diff.binary", "Binary file (no preview).")
DIFF_TOO_LARGE = Msg("ui.diff.too_large", "Diff too large to show.")
DIFF_NO_CHANGES = Msg("ui.diff.no_changes", "No changes.")
DIFF_CLEAN_IN = Msg("ui.diff.clean_in", "No uncommitted changes in {repo}.",
                    eg={"repo": "aipager"}, note="repo is a folder's name")
DIFF_CLEAN = Msg("ui.diff.clean", "No uncommitted changes.")
DIFF_PARTIAL = Msg("ui.diff.partial",
                   "Showing a partial set of changed files. The full diff is larger than this "
                   "viewer renders.")
DIFF_LOADING = Msg("ui.diff.loading", "Loading diff…")
DIFF_GONE = Msg("ui.diff.gone", "Session no longer available.")
DIFF_FAILED = Msg("ui.diff.failed", "Could not load diff.")

# The session menu.
BTN_STOP = Msg("ui.btn.action.stop", "Stop", note="Mini App session menu")
BTN_KILL = Msg("ui.btn.action.kill", "Kill",
               note="Mini App session menu: end the session's process")
BTN_RESUME = Msg("ui.btn.action.resume", "Resume", note="Mini App session menu")
BTN_DELETE = Msg("ui.btn.action.delete", "Delete",
                 note="Mini App session menu: remove a finished session from the list")
BTN_CLEARQUEUE = Msg("ui.btn.action.clearqueue", "Clear queue", note="Mini App session menu")
BTN_COMPACT = Msg("ui.btn.action.compact", "Compact now",
                  note="Mini App session menu: shrink Claude's context (/compact)")
BTN_RESTART = Msg("ui.btn.action.restart", "Restart", note="Mini App session menu")
BTN_RENAME = Msg("ui.btn.action.rename", "Rename", note="Mini App session menu")
BTN_BRINGBACK = Msg("ui.btn.action.bringback", "Bring it back",
                    note="Mini App session menu: bring the session back from Claude Code's "
                         "background into its own terminal")
BTN_BG_FORCE = Msg("ui.btn.bg_force", "Stop and bring back",
                   note="Mini App: a button under app.err.bg_busy: stop the background work "
                        "and bring the conversation back")
BTN_BG_LEAVE = Msg("ui.btn.bg_leave", "Leave it",
                   note="Mini App: a button under app.err.bg_busy: leave the background work "
                        "running")
BTN_TO_ASK = Msg("ui.btn.switch_to_ask", "Switch to Ask mode",
                 note="Mini App session menu: Ask is the permission mode (as in /mode)")
BTN_TO_AUTO = Msg("ui.btn.switch_to_auto", "Switch to Auto mode",
                  note="Mini App session menu: Auto is the permission mode (as in /mode)")
BTN_TO_FULL = Msg("ui.btn.switch_to_full", "Switch to Full access mode",
                  note="Mini App session menu: Full access is the permission mode (as in /mode)")
DONE_STOP = Msg("ui.done.stop", "Stopped.", note="Mini App: a short confirmation")
DONE_RESUME = Msg("ui.done.resume", "Resumed.", note="Mini App: a short confirmation")
DONE_CLEARQUEUE = Msg("ui.done.clearqueue", "Cleared.",
                      note="Mini App: the queue was cleared (short)")
DONE_COMPACT = Msg("ui.done.compact", "Compacting…", note="Mini App: a short confirmation")
DONE_PERMS = Msg("ui.done.perms", "Switched.",
                 note="Mini App: the permission mode was switched (short)")
DONE_MODE_SENT = Msg("ui.done.mode_sent", "Switch sent. Claude Code confirms it with its next step.",
                     note="Mini App: an Ask/Auto switch was sent to the session (no restart); "
                          "Claude Code confirms it with its next step")
DONE_RESTART = Msg("ui.done.restart", "Restarted.", note="Mini App: a short confirmation")
DONE_BRINGBACK = Msg("ui.done.bringback", "Back from Claude Code's background.",
                     note="Mini App: a short confirmation; the session was brought back from "
                          "a Claude Code background job")
DONE_ALREADY_GONE = Msg("ui.done.already_gone", "Already gone.",
                        note="Mini App: the session had ended already (short)")
DONE_KILL = Msg("ui.done.kill", "Killed.", note="Mini App: the session's process was ended")
DONE_DELETE = Msg("ui.done.delete", "Deleted.", note="Mini App: a short confirmation")
DONE_GENERIC = Msg("ui.done.generic", "Done.", note="Mini App: a short confirmation")
DONE_RENAME = Msg("ui.done.rename", "Renamed.", note="Mini App: a short confirmation")
DONE_RENAME_SAME = Msg("ui.done.rename_same", "No change.",
                       note="Mini App: the new name was the old one")
ACTION_FAILED = Msg("ui.action.failed", "Couldn't complete that.")
ACTION_UNREACHABLE = Msg("ui.action.unreachable", "Couldn't reach the server. Nothing changed.")
MENU_CHANGED = Msg("ui.menu.changed", "This session changed. Reopen the menu.")

# Confirms (a switch into or out of Full access restarts the session).
CONFIRM_PERMS_BUSY_AUTO = Msg("ui.confirm.perms_busy_auto",
                              "Stop the current task and switch {session} to Auto mode?",
                              eg={"session": "x1"},
                              note="Auto is the permission mode (as in /mode)")
CONFIRM_PERMS_BUSY_ASK = Msg("ui.confirm.perms_busy_ask",
                             "Stop the current task and switch {session} to Ask mode?",
                             eg={"session": "x1"},
                             note="Ask is the permission mode (as in /mode)")
CONFIRM_PERMS_BUSY_FULL = Msg("ui.confirm.perms_busy_full",
                              "Stop the current task and switch {session} to Full access mode?",
                              eg={"session": "x1"},
                              note="Full access is the permission mode (as in /mode)")
CONFIRM_PERMS_AUTO = Msg("ui.confirm.perms_auto", "Switch {session} to Auto mode?",
                         eg={"session": "x1"}, note="Auto is the permission mode (as in /mode)")
CONFIRM_PERMS_ASK = Msg("ui.confirm.perms_ask", "Switch {session} to Ask mode?",
                        eg={"session": "x1"}, note="Ask is the permission mode (as in /mode)")
CONFIRM_PERMS_FULL = Msg("ui.confirm.perms_full", "Switch {session} to Full access mode?",
                         eg={"session": "x1"},
                         note="Full access is the permission mode (as in /mode)")
CONFIRM_PERMS_FULL_BODY = Msg("ui.confirm.perms_full_body",
                              "Everything runs with no checks. The session restarts; the "
                              "conversation is kept.",
                              note="Under the Full access confirm")
CONFIRM_PERMS_LEAVE_FULL_BODY = Msg("ui.confirm.perms_leave_full_body",
                                    "The session restarts; the conversation is kept.",
                                    note="Under the confirm for leaving Full access mode")
BTN_STOP_AND_SWITCH = Msg("ui.btn.stop_and_switch", "Stop task & switch",
                          note="Mini App button: stop the running task, then switch the mode")
BTN_SWITCH = Msg("ui.btn.switch", "Switch", note="Mini App button")
BTN_NOT_NOW = Msg("ui.btn.not_now", "Not now", note="Mini App button")
BTN_CANCEL = Msg("ui.btn.cancel", "Cancel", note="Mini App button")
BTN_SAVE = Msg("ui.btn.save", "Save", note="Mini App button")
BTN_RESET = Msg("ui.btn.reset", "Reset", note="Mini App button: back to the defaults")
CONFIRM_RESTART = Msg("ui.confirm.restart", "Restart {session}?", eg={"session": "x1"})
CONFIRM_RESTART_BODY = Msg("ui.confirm.restart_body",
                           "The session stops and relaunches with the same history. Any turn "
                           "in progress is interrupted.")
CONFIRM_KILL = Msg("ui.confirm.kill", "Kill {session}?", eg={"session": "x1"},
                   note="Kill: end the session's process")
CONFIRM_KILL_BODY = Msg("ui.confirm.kill_body",
                        "This stops the session and removes it. Anything it was running is "
                        "interrupted.")
CONFIRM_DELETE = Msg("ui.confirm.delete", "Delete {session}?", eg={"session": "x1"})
CONFIRM_DELETE_BODY = Msg("ui.confirm.delete_body",
                          "This removes the session from your list. Its transcript stays on "
                          "disk.")
CONFIRM_RENAME = Msg("ui.confirm.rename", "Rename {session}?", eg={"session": "x1"})
CONFIRM_RENAME_BODY = Msg("ui.confirm.rename_body",
                          "The session keeps its history and working directory. Only the name "
                          "changes.")
CONFIRM_RESET = Msg("ui.confirm.reset", "Reset settings to defaults?")
CONFIRM_RESET_BODY = Msg("ui.confirm.reset_body",
                         "This session's own settings are cleared and it goes back to "
                         "following the chat's defaults.")
RENAME_EMPTY = Msg("ui.rename.empty", "Session name can't be empty.")
RENAME_TOO_LONG = Msg("ui.rename.too_long", "Session name must be {max} characters or fewer.",
                      eg={"max": 64})
RENAME_PATTERN = Msg("ui.rename.pattern",
                     "Use letters, numbers, hyphens and underscores; start with a letter or "
                     "number.", note="What a session name may hold (Latin letters)")
RENAME_RESERVED = Msg("ui.rename.reserved", "'{name}' is a reserved command name.",
                      eg={"name": "status"},
                      note="The name is one of the bot's commands")
RENAME_FAILED = Msg("ui.rename.failed", "Couldn't rename.")

# Settings.
SETTINGS_SAVED = Msg("ui.settings.saved", "Saved.", note="Mini App: a short confirmation")
SETTINGS_SAVE_FAILED = Msg("ui.settings.save_failed",
                           "Couldn't save. The value on screen is what's stored.")
SETTINGS_RESET_FAILED = Msg("ui.settings.reset_failed",
                            "Some settings could not be reset. Reopen to retry.")
SETTINGS_RESET_DONE = Msg("ui.settings.reset_done", "Reset to defaults.",
                          note="Mini App: the session's settings follow the chat again")
SETTINGS_DEFAULT_TAG = Msg("ui.settings.default_tag", "default",
                           note="Mini App: a tag on the chat's default choice (short)")
SETTINGS_CHAT_PREFS = Msg("ui.settings.chat_prefs", "Chat preferences",
                          note="Mini App Settings: a heading")
SETTINGS_INTRO = Msg("ui.settings.intro", "These apply to every session in this chat.")
SETTINGS_READONLY = Msg("ui.settings.readonly", "Only an admin can change these.")
SETTINGS_APPEARANCE = Msg("ui.settings.appearance", "Appearance",
                          note="Mini App Settings: a heading, the section of button colours")
SETTINGS_APPEARANCE_INTRO = Msg("ui.settings.appearance_intro",
                                "How aipager's buttons look in this chat.")

# Telegram's own button.
BTN_START_SESSION = Msg("ui.btn.start_session", "Start session", note="Mini App button")
BTN_TRY_AGAIN = Msg("ui.btn.try_again", "Try again", note="Mini App button")
BTN_DONE = Msg("ui.btn.done", "Done", note="Mini App button")
BTN_SEND_REPORT = Msg("ui.btn.send_report", "Send report",
                      note="Mini App button: send the problem report")
BTN_SENDING = Msg("ui.btn.sending", "Sending...",
                  note="Mini App button: the report is being sent")

# The problem report.
REPORT_TITLE = Msg("ui.report.title", "Report a problem",
                   note="Mini App: the problem report page and its card (a heading)")
BTN_REPORT = Msg("ui.btn.report", "Report a problem",
                 note="Mini App button: opens the problem report")
REPORT_PRIVACY = Msg("ui.report.privacy",
                     "It holds versions, counts and places in aipager's code. Never your chats, "
                     "prompts, names or paths.")
REPORT_CARD = Msg("ui.report.card_text",
                  "Tell the maintainer what went wrong. You see the whole report before "
                  "anything is sent.")
REPORT_NOTE_LABEL = Msg("ui.report.note_label", "Your note (optional)")
REPORT_NOTE_PLACEHOLDER = Msg("ui.report.note_placeholder",
                              "What were you doing when it went wrong?",
                              note="Mini App: a hint inside the empty note field")
REPORT_AS_WRITTEN = Msg("ui.report.as_written", "Sent exactly as written.",
                        note="Mini App: under the note field")
REPORT_WHAT_SENT = Msg("ui.report.what_sent", "What gets sent", note="Mini App: a heading")
REPORT_ERRORS = Msg("ui.report.errors", "Errors", note="Mini App problem report: a heading")
REPORT_ERRORS_N = Plural("ui.report.errors_n", one="Errors ({n})", other="Errors ({n})",
                         note="Mini App problem report: a heading")
REPORT_NO_ERRORS = Msg("ui.report.no_errors",
                       "No errors recorded lately. The facts above and your note still help.")
REPORT_EXACT_NOTE = Msg("ui.report.exact_note",
                        "This is the exact text that is sent, your note included.")
REPORT_FOOT = Msg("ui.report.foot",
                  "Nothing is sent until you tap Send report. It goes to the maintainer's error "
                  "inbox, without your name.",
                  note="Send report is the button's name")
REPORT_PREPARING = Msg("ui.report.preparing", "Preparing the report...")
BTN_COPY = Msg("ui.btn.copy", "Copy", note="Mini App button")
BTN_COPIED = Msg("ui.btn.copied", "Copied", note="Mini App button, after copying")
REPORT_COPIED = Msg("ui.report.copied", "Copied.", note="Mini App: a short confirmation")
REPORT_COPY_HINT = Msg("ui.report.copy_hint", "Long-press the reference to copy it.")
REPORT_LATER = Msg("ui.report.later",
                   "No answer from aipager in time. Tap Send report again: if it already went, "
                   "you will see its reference, and it is not sent twice.",
                   note="Send report is the button's name")
REPORT_NOOWNER_TITLE = Msg("ui.report.panel.noowner_title", "Can't send from here",
                           note="Mini App problem report: a heading")
REPORT_NOOWNER = Msg("ui.report.panel.noowner",
                     "This aipager has no single owner chat. In a terminal, {command} still "
                     "works.", eg={"command": "aipager report"},
                     note="{command} is a terminal command")
REPORT_OWNER_TITLE = Msg("ui.report.panel.owner_title", "Owner only",
                         note="Mini App problem report: a heading")
REPORT_OWNER = Msg("ui.report.panel.owner",
                   "Only the owner of this aipager can send a problem report.")
REPORT_GROUP_TITLE = Msg("ui.report.panel.group_title", "Open your private chat",
                         note="Mini App problem report: a heading")
REPORT_GROUP = Msg("ui.report.panel.group",
                   "Reports are sent from the Mini App opened in your private chat with the "
                   "bot.")
REPORT_BUILD_TITLE = Msg("ui.report.panel.build_title", "Couldn't prepare the report",
                         note="Mini App problem report: a heading")
REPORT_BUILD = Msg("ui.report.panel.build", "Try again in a moment.")
REPORT_SENT_TITLE = Msg("ui.report.sent_title", "Report sent", note="Mini App: a heading")
REPORT_TOO_OLD_TITLE = Msg("ui.report.too_old_title", "Update needed",
                           note="Mini App: a heading, this aipager is too old to send")
REPORT_DAILY_CAP_TITLE = Msg("ui.report.daily_cap_title", "Daily limit reached",
                             note="Mini App: a heading")
REPORT_NOT_SENT = Msg("ui.report.not_sent", "Not sent", note="Mini App: a heading")
REPORT_THANKS = Msg("ui.report.thanks",
                    "Thank you. If you open a GitHub issue, quote this reference:",
                    note="The reference code follows below")
REPORT_STALE = Msg("ui.report.stale",
                   "That report is no longer here. Here is a fresh one: check it, then tap "
                   "Send report again.", note="Send report is the button's name")
REPORT_STILL_SENDING = Msg("ui.report.still_sending",
                           "Still sending. Wait a moment, then tap Send report again.",
                           note="Send report is the button's name")
REPORT_TIDIED = Msg("ui.report.tidied",
                    "Your note was tidied up (invisible characters, extra spaces or anything "
                    "past 500 characters were removed). Check it, then tap Send report again.",
                    note="Send report is the button's name")
REPORT_BAD_NOTE = Msg("ui.report.bad_note",
                      "That note could not be added. Try a shorter, plainer one.")
REPORT_TOO_MANY = Msg("ui.report.too_many", "Too many taps. Try again in a minute.")
REPORT_SENDING_WAIT = Msg("ui.report.sending_wait", "Sending. One moment.")
REPORT_MORE = Msg("ui.report.more", "More details", note="Mini App problem report: a toggle")
REPORT_EXACT = Msg("ui.report.exact", "Show the exact report",
                   note="Mini App problem report: a toggle")
REPORT_TOO_LONG = Plural("ui.report.too_long", one="Too long by {n} character.",
                         other="Too long by {n} characters.",
                         note="Mini App: the note is over its limit")
REPORT_SHOW_MORE = Plural("ui.report.show_more", one="Show {n} more", other="Show {n} more",
                          note="Mini App problem report: more errors (a toggle)")
REPORT_ONCE = Msg("ui.report.once", "once", note="Mini App problem report: an error seen once")
REPORT_TIMES = Plural("ui.report.times", one="{n} times", other="{n} times",
                      note="Mini App problem report: how often an error was seen "
                           "(n is 2 or more)")
REPORT_ERR_SUB = Msg("ui.report.err_sub", "{times}, last seen {day}",
                     eg={"times": "3 times", "day": "2026-10-04"},
                     note="Mini App problem report: under an error; times reads once or 3 times")
REPORT_ERR_DETAIL = Msg("ui.report.err_detail", "{fingerprint}, first seen {day}, on {versions}",
                        eg={"fingerprint": "ab12", "day": "2026-10-01", "versions": "0.7.17"},
                        note="Mini App problem report: an error's fingerprint, first day and "
                             "versions")
REPORT_ALSO_THROUGH = Msg("ui.report.also_through", "Also passed through: {names}",
                          eg={"names": "aiohttp"},
                          note="Mini App problem report: libraries the error passed through")
REPORT_ERR_CRASH = Msg("ui.report.err.crash", "aipager stopped unexpectedly")
REPORT_ERR_HOOK_CAP = Msg("ui.report.err.hook_cap", "The hook hit its memory cap",
                          note="the hook: the small program Claude Code runs on each event")
REPORT_ERR_LOGGED = Msg("ui.report.err.logged_in", "Logged error in {area}",
                        eg={"area": "notify"}, note="area is a part of aipager's code")
REPORT_ERR_TYPE = Msg("ui.report.err.type_in", "{type} in {area}",
                      eg={"type": "ValueError", "area": "notify"},
                      note="type is an error's class name, area a part of aipager's code")
REPORT_ERR_ERROR = Msg("ui.report.err.error", "Error",
                       note="Mini App problem report: an error with no class name")
REPORT_LABEL_SYSTEM = Msg("ui.report.label.system", "System",
                          note="Mini App problem report: a fact's label (the OS)")
REPORT_LABEL_SETUP = Msg("ui.report.label.setup", "Setup",
                         note="Mini App problem report: a fact's label")
REPORT_LABEL_FEATURES = Msg("ui.report.label.features", "Features",
                            note="Mini App problem report: a fact's label")
REPORT_LABEL_LIBRARIES = Msg("ui.report.label.libraries", "Libraries",
                             note="Mini App problem report: a fact's label")
REPORT_LABEL_STOPS = Msg("ui.report.label.stops", "Stops",
                         note="Mini App problem report: how aipager last stopped (a label)")
REPORT_LABEL_LIMITS = Msg("ui.report.label.limits", "Telegram limits",
                          note="Mini App problem report: a fact's label")
REPORT_LABEL_COUNTS = Msg("ui.report.label.counts", "Counts, last 24 hours",
                          note="Mini App problem report: a fact's label")
REPORT_LABEL_LOG = Msg("ui.report.label.log", "Log, last 24 hours",
                       note="Mini App problem report: a fact's label")
REPORT_NOT_FOUND = Msg("ui.report.not_found", "Not found",
                       note="Mini App problem report: Claude Code is not installed")
REPORT_SYSTEM_IN = Msg("ui.report.system_in", "{system}, in {container}",
                       eg={"system": "Ubuntu 24.04", "container": "docker"},
                       note="Mini App problem report: the OS and the container it runs in")
REPORT_SETUP_PERSONAL = Msg("ui.report.setup.personal", "Personal setup",
                            note="Mini App problem report: aipager runs for one person")
REPORT_SETUP_TEAM = Msg("ui.report.setup.team", "Team setup",
                        note="Mini App problem report: aipager runs for a team")
REPORT_SETUP_SCOPE = Msg("ui.report.setup.scope", "Multi-chat setup",
                         note="Mini App problem report: aipager runs in several chats")
REPORT_SETUP_LINE = Msg("ui.report.setup_line", "{mode}, {dms}, {groups}, running {uptime}",
                        eg={"mode": "Team setup", "dms": "1 private chat", "groups": "2 groups",
                            "uptime": "1 to 24 hours"},
                        note="Mini App problem report: uptime reads like under 10 minutes or "
                             "for an unknown time")
REPORT_SETUP_NO_GROUPS = Msg("ui.report.setup_line_no_groups", "{mode}, {dms}, running {uptime}",
                             eg={"mode": "Team setup", "dms": "1 private chat",
                                 "uptime": "1 to 24 hours"},
                             note="Mini App problem report: as above, no groups")
REPORT_SETUP_NO_MODE = Msg("ui.report.setup_line_no_mode", "{dms}, {groups}, running {uptime}",
                           eg={"dms": "1 private chat", "groups": "2 groups",
                               "uptime": "1 to 24 hours"},
                           note="Mini App problem report: as above, no setup kind")
REPORT_SETUP_BARE = Msg("ui.report.setup_line_bare", "{dms}, running {uptime}",
                        eg={"dms": "1 private chat", "uptime": "1 to 24 hours"},
                        note="Mini App problem report: as above, no kind and no groups")
REPORT_N_DMS = Plural("ui.report.n_private_chats", one="{n} private chat",
                      other="{n} private chats", note="Mini App problem report")
REPORT_N_GROUPS = Plural("ui.report.n_groups", one="{n} group", other="{n} groups",
                         note="Mini App problem report")
REPORT_UPTIME_10M = Msg("ui.report.uptime.under_10m", "under 10 minutes",
                        note="Mini App problem report: follows running")
REPORT_UPTIME_1H = Msg("ui.report.uptime.10m_1h", "10 minutes to an hour",
                       note="Mini App problem report: follows running")
REPORT_UPTIME_24H = Msg("ui.report.uptime.1h_24h", "1 to 24 hours",
                        note="Mini App problem report: follows running")
REPORT_UPTIME_7D = Msg("ui.report.uptime.1d_7d", "1 to 7 days",
                       note="Mini App problem report: follows running")
REPORT_UPTIME_WEEK = Msg("ui.report.uptime.over_week", "over a week",
                         note="Mini App problem report: follows running")
REPORT_UPTIME_UNKNOWN = Msg("ui.report.uptime.unknown", "for an unknown time",
                            note="Mini App problem report: follows running")
REPORT_FEATURE_MINIAPP = Msg("ui.report.feature.miniapp", "Mini App",
                             note="Mini App problem report: a feature's name")
REPORT_FEATURE_TUNNEL = Msg("ui.report.feature.tunnel_managed", "managed tunnel",
                            note="Mini App problem report: a feature (aipager runs the tunnel)")
REPORT_FEATURE_OWN_TUNNEL = Msg("ui.report.feature.tunnel_override", "own tunnel address",
                                note="Mini App problem report: a feature")
REPORT_FEATURE_VOICE = Msg("ui.report.feature.voice", "voice notes",
                           note="Mini App problem report: a feature")
REPORT_FEATURE_DIFFS = Msg("ui.report.feature.diff_preview_any", "change previews",
                           note="Mini App problem report: a feature (diff messages)")
REPORT_FEATURE_SUMMARIES = Msg("ui.report.feature.rich_summaries", "rich summaries",
                               note="Mini App problem report: a feature")
REPORT_NONE = Msg("ui.report.none", "None", note="Mini App problem report: an empty fact")
REPORT_MISSING = Msg("ui.report.missing", "(missing)",
                     note="Mini App problem report: a library that is not installed")
REPORT_STOPS = Msg("ui.report.stops", "Last stop: {last}. Unexpected stops in 7 days: {n}.",
                   eg={"last": "a crash", "n": 2},
                   note="Mini App problem report: last reads clean, a crash, a reboot or "
                        "unknown")
REPORT_EXIT_CLEAN = Msg("ui.report.exit.clean", "clean",
                        note="Mini App problem report: aipager last stopped cleanly")
REPORT_EXIT_CRASH = Msg("ui.report.exit.crash", "a crash",
                        note="Mini App problem report: aipager last stopped with a crash")
REPORT_EXIT_REBOOT = Msg("ui.report.exit.reboot", "a reboot",
                         note="Mini App problem report: the machine rebooted")
REPORT_EXIT_UNKNOWN = Msg("ui.report.exit.unknown", "unknown",
                          note="Mini App problem report: how aipager last stopped")
REPORT_LIMITS = Plural("ui.report.limits", one="{n} ban in 7 days, busiest hour load {load}",
                       other="{n} bans in 7 days, busiest hour load {load}",
                       eg={"load": "0.3"},
                       note="Mini App problem report: Telegram's flood bans and the busiest "
                            "hour's share of the limit")
REPORT_LOG_LINE = Plural("ui.report.log_line",
                         one="{n} place logged a warning or error ({times})",
                         other="{n} places logged a warning or error ({times})",
                         eg={"times": "4 times"},
                         note="Mini App problem report: places in the code; times reads once "
                              "or 4 times")
REPORT_NOTHING_LOGGED = Msg("ui.report.nothing_logged", "Nothing logged")

# Updates.
UPDATE_TITLE = Msg("ui.update.title", "Updates", note="Mini App Settings: a card's heading")
UPDATE_RUNNING = Msg("ui.update.running", "An update is already running.")
UPDATE_TOO_MANY = Msg("ui.update.too_many", "Too many requests. Try again in a minute.")
UPDATE_SHUTTING_DOWN = Msg("ui.update.shutting_down",
                           "aipager is shutting down. Try again once it is back.")
UPDATE_CHECK_FAILED = Msg("ui.update.check_failed", "Couldn't check for updates. Try again.")
UPDATE_UNREACHABLE = Msg("ui.update.unreachable", "Couldn't reach aipager. Try again.")
UPDATE_STALE = Msg("ui.update.stale", "That check is out of date. Check again.")
UPDATE_FINISHED = Msg("ui.update.finished", "That update already finished.")
UPDATE_START_FAILED = Msg("ui.update.start_failed", "Couldn't start that. Try again.")
UPDATE_RESTARTING = Msg("ui.update.restarting", "Restarting aipager…")
UPDATE_UPDATED = Msg("ui.update.updated", "Updated to {version}.", eg={"version": "0.7.18"})
UPDATE_UPDATED_READOPTED = Plural("ui.update.updated_readopted",
                                  one="Updated to {version}, {n} session re-adopted.",
                                  other="Updated to {version}, {n} sessions re-adopted.",
                                  eg={"version": "0.7.18"},
                                  note="re-adopted: aipager took the running sessions back "
                                       "after its restart")
UPDATE_NOT_BACK = Msg("ui.update.not_back", "Not back: {names}", eg={"names": "x2, x3"},
                      note="Sessions aipager did not take back after its restart")
UPDATE_OTHER = Msg("ui.update.other", "aipager restarted but is running {version}.",
                   eg={"version": "0.7.99"})
UPDATE_OTHER_NOT = Msg("ui.update.other_not",
                       "aipager restarted but is running {version}, not {expected}.",
                       eg={"version": "0.7.99", "expected": "0.7.18"})
UPDATE_UNKNOWN_VERSION = Msg("ui.update.unknown_version", "an unknown version",
                             note="Fills {version} in: aipager restarted but is running ...")
UPDATE_REOPEN = Msg("ui.update.reopen",
                    "aipager restarted, reopen the app once the chat says it is updated. Close "
                    "it, then open it again from the chat (the menu button or {command}).",
                    eg={"command": "/app"})
UPDATE_REFUSED = Msg("ui.update.refused", "Reopen the app to see the update's result.")
UPDATE_SLOW = Msg("ui.update.slow", "Still restarting, reopen the app in a moment.")
UPDATE_JOB_WAITING = Msg("ui.update.job.waiting_idle",
                         "Waiting for every session to go idle before the restart.")
UPDATE_JOB_GATE = Msg("ui.update.job.gate_timeout",
                      "Sessions are still busy. Wait more, restart now, or cancel?")
UPDATE_JOB_STARTING = Msg("ui.update.job.starting", "Working… (starting)",
                          note="Mini App: an update's step")
UPDATE_JOB_CLAUDE = Msg("ui.update.job.claude_updating", "Working… (claude updating)",
                        note="Mini App: an update's step, Claude Code is being updated")
UPDATE_JOB_UPGRADING = Msg("ui.update.job.upgrading", "Working… (upgrading)",
                           note="Mini App: an update's step")
UPDATE_JOB_PHASE = Msg("ui.update.job.working_phase", "Working… ({phase})",
                       eg={"phase": "something new"},
                       note="Mini App: an update's step this page has no words for (its id)")
UPDATE_RESTART_NOW_CONFIRM = Msg("ui.update.restart_now_confirm",
                                 "Restart now interrupts the running turns listed here.")
UPDATE_OFFER_CONFIRM = Msg("ui.update.offer_confirm",
                           "{offer}? aipager restarts when no turn is running.",
                           eg={"offer": "Update aipager"},
                           note="offer is the update button's text")
UPDATE_UP_TO_DATE = Msg("ui.update.up_to_date", "Everything is up to date.")
BTN_CLOSE_APP = Msg("ui.btn.close_app", "Close the app", note="Mini App button")
BTN_CHECK_UPDATES = Msg("ui.btn.check_updates", "Check for updates", note="Mini App button")
BTN_CHECKING = Msg("ui.btn.checking", "Checking…", note="Mini App button, while checking")
BTN_WAIT_MORE = Msg("ui.btn.wait_more", "Wait 10 more min", note="Mini App button")
BTN_RESTART_NOW = Msg("ui.btn.restart_now", "Restart now", note="Mini App button")
UPDATE_RESTART_CONFIRM = Msg("ui.update.restart_confirm",
                             "Restart aipager now? It waits until no turn is running, then is "
                             "back in a few seconds.",
                             note="Mini App: asked before Restart now runs the installed aipager")
UPDATE_WAITING_BANNER = Msg("ui.update.waiting_banner",
                            "aipager {version} is installed and waiting for a restart.",
                            eg={"version": "1.1.0"},
                            note="Mini App Updates card: only a restart is missing")
UPDATE_HOOKS_BANNER = Msg("ui.update.hooks_banner",
                          "Claude Code's hooks already run aipager {version}, but aipager "
                          "itself still runs {running}. Restart it to finish the update.",
                          eg={"version": "1.1.0", "running": "1.0.1"},
                          note="Mini App Updates card: a newer aipager is installed, the "
                               "running one is older")
UPDATE_JOB_VERIFYING = Msg("ui.update.job.verifying", "Working… (checking the installed version)",
                           note="Mini App: Restart now's step, the installed aipager is checked")
UPDATE_OFFER_CONFIRM_MANUAL = Msg("ui.update.offer_confirm_manual",
                                  "{offer}? Afterwards, tap Restart now to run the new version.",
                                  eg={"offer": "Update aipager"},
                                  note="offer is the update button's text; Restart now is a "
                                       "button")
UPDATE_OFFER_CONFIRM_STEPS = Msg("ui.update.offer_confirm_steps",
                                 "{offer}? aipager can't restart itself here, so the steps to "
                                 "restart it show once the update is installed.",
                                 eg={"offer": "Update aipager"},
                                 note="offer is the update button's text; the steps to restart "
                                      "aipager by hand follow the finished update")

# The new-session form.
NEW_TITLE = Msg("ui.new.title", "New session", note="Mini App: the form's heading")
NEW_SUB = Msg("ui.new.sub", "Name it, pick a folder, and Claude starts there.")
NEW_NAME = Msg("ui.new.name", "Name", note="Mini App new session: a step's heading")
NEW_WHERE = Msg("ui.new.where", "Where", note="Mini App new session: a step's heading")
NEW_MORE = Msg("ui.new.more", "More options", note="Mini App new session: a toggle")
NEW_FULL_NEEDS_ADMIN = Msg("ui.new.full_needs_admin", "Full access mode requires admin.",
                           note="Full access is the permission mode (as in /mode)")
NEW_MODEL_NAME = Msg("ui.new.model_name", "Model name", note="Mini App: a field's label")
NEW_FOLDER_NAME = Msg("ui.new.folder_name", "New folder name", note="Mini App: a field's label")
NEW_CREATE_FOLDER = Msg("ui.new.create_folder", "Create folder",
                        note="Mini App: the button under the new folder's name that creates it")
NEW_NOT_SET = Msg("ui.new.not_set", "Not set yet",
                  note="Mini App: no model typed yet (short)")
NEW_DEFAULT = Msg("ui.new.default", "Default", note="Mini App: a choice (short)")
NEW_DEFAULT_MODEL_HELP = Msg("ui.new.default_model_help", "Whatever the CLI is configured to use",
                             note="the CLI: Claude Code's command line program")
NEW_OTHER_MODEL = Msg("ui.new.other_model", "Other model", note="Mini App: a choice (short)")
NEW_OTHER_MODEL_HELP = Msg("ui.new.other_model_help", "Type a full name, e.g. {example}",
                           eg={"example": "claude-opus-5"},
                           note="example is a model's full name, written as it is")
NEW_DEFAULT_DIR_HELP = Msg("ui.new.default_dir_help", "The daemon's own directory",
                           note="the daemon: aipager's background process")
NEW_FOLDER = Msg("ui.new.new_folder", "New folder", note="Mini App: a choice (short)")
NEW_FOLDER_HELP = Msg("ui.new.new_folder_help", "Inside the directory selected above")
NEW_CHIPS_RECENT = Msg("ui.new.chips_recent", "Recent",
                       note="Mini App: a caption before recent folders (short)")
NEW_CHIPS_SUGGESTED = Msg("ui.new.chips_suggested", "Suggested",
                          note="Mini App: a caption before suggested models (short)")
NEW_CWD_TITLE = Msg("ui.new.cwd_title", "Working directory", note="Mini App: a heading")
NEW_MODE_TITLE = Msg("ui.new.mode_title", "Permission mode", note="Mini App: a heading")
NEW_MODE_ASK = Msg("ui.new.mode_ask", "Ask",
                   note="Mini App: the permission mode, as in /mode (one word)")
NEW_MODE_AUTO = Msg("ui.new.mode_auto", "Auto",
                    note="Mini App: the permission mode, as in /mode (one word)")
NEW_MODE_FULL = Msg("ui.new.mode_full", "Full access",
                    note="Mini App: the permission mode, as in /mode (short)")
NEW_MODE_ASK_HELP = Msg("ui.new.mode_ask_help", "Claude asks before running tools")
NEW_MODE_AUTO_HELP = Msg("ui.new.mode_auto_help",
                         "A safety check runs safe actions and stops risky ones")
NEW_MODE_FULL_HELP = Msg("ui.new.mode_full_help", "Everything runs with no checks")
NEW_REQUIRES_ADMIN = Msg("ui.new.requires_admin", "Requires admin")
NEW_MODEL_PATTERN = Msg("ui.new.model_pattern",
                        "Use letters, numbers and . _ - : / @ ~ , with no spaces; start with "
                        "a letter or number.",
                        note="What a model name may hold (Latin letters); keep the symbols "
                             "as they are")
NEW_MODEL_NEEDED = Msg("ui.new.model_needed", "Type a model name to continue.")
NEW_PICK_DIR_FIRST = Msg("ui.new.pick_dir_first", "Pick a working directory above first.")
NEW_CREATING = Msg("ui.new.creating", "Creating…", note="Mini App: a folder is being created")
NEW_SUMMARY_NAMED_ASK = Msg("ui.new.summary_named_ask",
                            "Starts Claude as {name} in {where}, in Ask mode, using {model}.",
                            eg={"name": "x1", "where": "/home/op/web", "model": "opus"},
                            note="Mini App: what Create will do. where is a folder or the "
                                 "daemon's own directory; model a model or the CLI's own "
                                 "default model. Ask is the permission mode")
NEW_SUMMARY_NAMED_AUTO = Msg("ui.new.summary_named_auto",
                             "Starts Claude as {name} in {where}, in Auto mode, using {model}.",
                             eg={"name": "x1", "where": "/home/op/web", "model": "opus"},
                             note="Mini App: as above, in Auto (the permission mode)")
NEW_SUMMARY_NAMED_FULL = Msg("ui.new.summary_named_full",
                             "Starts Claude as {name} in {where}, in Full access mode, using "
                             "{model}.",
                             eg={"name": "x1", "where": "/home/op/web", "model": "opus"},
                             note="Mini App: as above, in Full access (the permission mode)")
NEW_SUMMARY_ASK = Msg("ui.new.summary_ask",
                      "Starts Claude in {where}, in Ask mode, using {model}.",
                      eg={"where": "/home/op/web", "model": "opus"},
                      note="Mini App: as above, no name typed yet")
NEW_SUMMARY_AUTO = Msg("ui.new.summary_auto",
                       "Starts Claude in {where}, in Auto mode, using {model}.",
                       eg={"where": "/home/op/web", "model": "opus"},
                       note="Mini App: as above, no name typed yet, in Auto")
NEW_SUMMARY_FULL = Msg("ui.new.summary_full",
                       "Starts Claude in {where}, in Full access mode, using {model}.",
                       eg={"where": "/home/op/web", "model": "opus"},
                       note="Mini App: as above, no name typed yet, in Full access")
NEW_DAEMON_DIR = Msg("ui.new.daemon_dir", "the daemon's own directory",
                     note="Fills {where} in the summary above (a place, lower case)")
NEW_CLI_MODEL = Msg("ui.new.cli_model", "the CLI's own default model",
                    note="Fills {model} in the summary above (lower case)")
NEW_CREATED = Msg("ui.new.created", "Session created.")
NEW_CREATE_FORBIDDEN = Msg("ui.new.create_forbidden", "You're not allowed to create sessions here.")
NEW_CREATE_FAILED = Msg("ui.new.create_failed", "Couldn't create that session.")
NEW_CREATE_UNREACHABLE = Msg("ui.new.create_unreachable",
                             "Couldn't reach the server. The session was not created.")
NEW_FOLDER_FORBIDDEN = Msg("ui.new.folder_forbidden", "You're not allowed to create folders here.")
NEW_FOLDER_FAILED = Msg("ui.new.folder_failed", "Couldn't create that folder.")
NEW_FOLDER_EXISTED = Msg("ui.new.folder_existed",
                         "That folder already existed, so it is selected.")
NEW_FOLDER_CREATED = Msg("ui.new.folder_created", "Folder created.")
NEW_FOLDER_UNREACHABLE = Msg("ui.new.folder_unreachable",
                             "Couldn't reach the server. No folder was created.")
# The page's own copies of the server's folder-name refusals
# (aipager/miniapp/launch.py validate_new_dir_name), shown before any
# request; the server still checks and its refusal is still shown.
NEW_FOLDER_TOO_LONG = Msg("ui.new.folder_too_long",
                          "Folder name must be {max} characters or fewer.", eg={"max": 64})
NEW_FOLDER_PATTERN = Msg("ui.new.folder_pattern",
                         "Use letters, numbers, dots, hyphens and underscores; start with a "
                         "letter or number.", note="What a new folder's name may hold")
NEW_FOLDER_TOO_MANY = Msg("ui.new.folder_too_many",
                          "Too many tries. Try again in a minute.",
                          note="Mini App: folder creation was rate limited")


#: Everything the page may ask for, in a stable order.
PAGE_STRINGS: tuple = tuple(
    value for name, value in sorted(globals().items())
    if not name.startswith("_") and isinstance(value, (Msg, Plural))
)


# ---- the catalogs --------------------------------------------------------------

def _forms(plural: Plural) -> dict[str, str]:
    return {"one": plural.one, "other": plural.other}


def english_catalog() -> dict:
    """key -> English pattern, or ``{"one", "other"}`` for a Plural."""
    return {m.key: (_forms(m) if isinstance(m, Plural) else str(m)) for m in PAGE_STRINGS}


def catalog(code: str) -> dict | None:
    """The ``/api/i18n/<code>.json`` body for a language a person may pick
    (or a pseudo code a test switched on), else None. ``strings`` holds
    the page keys that language has; a missing translation is left out,
    so the page shows its English (never a key)."""
    if not isinstance(code, str) or not locales.is_selectable(code):
        return None
    loc = locales.lookup(code)
    if loc is None:
        return None
    tr = i18n.get(code)
    strings: dict = {}
    if code in locales.PSEUDO:
        for m in PAGE_STRINGS:
            strings[m.key] = ({c: core.pseudo(v) for c, v in _forms(m).items()}
                              if isinstance(m, Plural) else core.pseudo(str(m)))
    elif code != "en":
        cat = core._catalog(code)
        for m in PAGE_STRINGS:
            value = cat.get(m.key)
            if isinstance(m, Plural):
                if isinstance(value, dict):
                    forms = {c: value[c] for c in plurals.categories(code)
                             if isinstance(value.get(c), str)}
                    if forms:
                        strings[m.key] = forms
            elif isinstance(value, str):
                strings[m.key] = value
    return {
        "code": code,
        "rtl": bool(loc.rtl),
        "digits": tr.num("0123456789"),
        "decimal": loc.decimal,
        "group": loc.group,
        "percent": loc.percent,
        "list": tr.join(["", ""]),
        "strings": strings,
    }


def canonical(body: dict) -> bytes:
    """The bytes the catalog route sends (and its version hashes)."""
    return json.dumps(body, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def catalog_version(code: str) -> str | None:
    """16 hex characters naming *code*'s catalog as it is now: the page
    puts it in the catalog URL, so a changed catalog is a new URL."""
    body = catalog(code)
    if body is None:
        return None
    return hashlib.sha256(canonical(body)).hexdigest()[:16]


def ui_payload(tr) -> dict | None:
    """What the page needs to switch to *tr*'s language, or None for
    English (the page's own)."""
    if tr is None or tr.code == "en":
        return None
    version = catalog_version(tr.code)
    if version is None:
        return None
    return {"code": tr.code, "rtl": bool(tr.rtl), "v": version}
