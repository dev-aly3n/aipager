"""Words for Claude Code background jobs: the chat lines about a session
parked on one (``bg.*``: the held notice, the "bring it back first"
refusals, the park notice and its buttons, the busy question, the result
of a bring-back), why a bring-back could not stop a job (``bg.err.*``,
filled into a sentence of the caller's), and the ``aipager session`` and
``aipager resume`` lines about a conversation that moved or is held by a
job (``cli.session.*``, rendered in English: the command line is not
translated yet).

One module the bot (session_ops, session_parity, handlers, notify) and the
command line share, without import cycles: it imports only
``aipager.i18n``. Kept out of :mod:`aipager.claude_jobs`, which never
imports ``aipager.i18n``.
"""

from __future__ import annotations

from aipager.i18n import Msg

#: ``claude_jobs.stop_error`` codes -> the words for them.
BG_ERRORS = {
    "stop_timeout": Msg(
        "bg.err.stop_timeout", "claude stop did not answer in time",
        note="Filled into bg.failed as {error}: the command that stops a Claude Code "
             "background job took too long; \"claude stop\" is a command, keep it"),
    "stop_exit": Msg(
        "bg.err.stop_exit", "claude stop ended with exit code {code}",
        note="Filled into bg.failed as {error}: the command that stops a Claude Code "
             "background job failed; \"claude stop\" is a command, keep it",
        eg={"code": "1"}),
    "still_running": Msg(
        "bg.err.still_running", "the background job is still running",
        note="Filled into bg.failed as {error}: the Claude Code background job did not "
             "end after it was told to stop"),
    "no_claude": Msg(
        "bg.err.no_claude", "the claude command could not be run",
        note="Filled into bg.failed as {error}: Claude Code's own command is missing"),
}

CLI_CONTINUED = Msg(
    "cli.session.continued",
    "The conversation moved to {session_id}, so this session continues there.",
    note="Command line: the conversation to resume had moved on to a newer id",
    eg={"session_id": "ab12cd34-0000-4000-8000-000000000001"})
CLI_STOPPED_BG = Msg(
    "cli.session.stopped_bg",
    "Stopped Claude Code's background job {job}, which held the conversation, to "
    "resume it here.",
    note="Command line: an idle Claude Code background job held the conversation and "
         "was stopped", eg={"job": "ab12cd34"})
CLI_BG_BUSY = Msg(
    "cli.session.bg_busy",
    "The conversation is still working in Claude Code's background job {job}: stop it "
    "with {stop_command} or open it with {attach_command}, then try again.",
    note="Command line: a busy Claude Code background job holds the conversation; the "
         "two commands are shown as written",
    eg={"job": "ab12cd34", "stop_command": "claude stop ab12cd34",
        "attach_command": "claude attach ab12cd34"})
CLI_BG_STOP_FAILED = Msg(
    "cli.session.bg_stop_failed",
    "Could not stop Claude Code's background job {job}: {error}",
    note="Command line: stopping the job that held the conversation failed; {error} "
         "is one of bg.err.*", eg={"job": "ab12cd34", "error": "claude stop did not "
                                   "answer in time"})


def error_msg(code: str) -> Msg:
    """The words for error *code* (``still_running`` for an unknown one)."""
    return BG_ERRORS.get(code) or BG_ERRORS["still_running"]


def error_text(tr, code: str, params: dict | None = None) -> str:
    """Error *code* as plain text in *tr*'s language."""
    return tr.text(error_msg(code), **(params or {}))


# -- the chat: a session parked on a Claude Code background job ----------------

BG_HELD_NOTICE = Msg(
    "bg.held_notice",
    "Your message to <b>{session}</b> waits until someone brings it back from "
    "Claude Code's background.",
    note="HTML. Reply to a message held because the session's conversation moved into "
         "a Claude Code background job; sent once per person",
    eg={"session": "x1"})
BG_REFUSED_PARKED = Msg(
    "bg.refused_parked", "Bring <b>{session}</b> back from Claude Code's background first.",
    note="HTML. A command (compact, model, mode, /now, Retry) refused because the "
         "session's conversation is in a Claude Code background job",
    eg={"session": "x1"})
BG_TOAST_REFUSED_PARKED = Msg(
    "bg.toast_refused_parked", "Bring {session} back from Claude Code's background first.",
    note="Pop-up or plain line: the same refusal as bg.refused_parked",
    eg={"session": "x1"})
BG_ANSWER_REFUSED = Msg(
    "bg.answer_refused",
    "Bring <b>{session}</b> back from Claude Code's background to answer this.",
    note="HTML. A prompt answer aipager would type was not typed: the session's "
         "conversation is in a Claude Code background job",
    eg={"session": "x1"})
BG_TOAST_ANSWER_REFUSED = Msg(
    "bg.toast_answer_refused",
    "Bring {session} back from Claude Code's background to answer this.",
    note="Pop-up: a tap on a prompt's button typed nothing, because the session's "
         "conversation is in a Claude Code background job",
    eg={"session": "x1"})
BG_PARK_NOTICE = Msg(
    "bg.park_notice",
    "🌙 <b>{session}</b> moved to Claude Code's background, and your next message "
    "brings it back here.",
    note="HTML. Sent once when the session's conversation moved into a Claude Code "
         "background job; the button under it brings it back",
    eg={"session": "x1"})
BG_BTN_BRING_BACK = Msg("bg.btn_bring_back", "↩️ Bring it back now",
                        note="Button under bg.park_notice: bring the session back from "
                             "Claude Code's background")
BG_BUSY_QUESTION = Msg(
    "bg.busy_question",
    "<b>{session}</b> is still working in Claude Code's background: stop that work and "
    "bring the conversation back here?",
    note="HTML. Asked before stopping a busy Claude Code background job that holds the "
         "session's conversation; two buttons follow",
    eg={"session": "x1"})
BG_BTN_STOP_AND_BRING = Msg("bg.btn_stop_and_bring", "Stop and bring back",
                            note="Button under bg.busy_question: stop the background "
                                 "work and bring the conversation back")
BG_BTN_LEAVE = Msg("bg.btn_leave", "Leave it",
                   note="Button under bg.busy_question: leave the background work running")
BG_LEFT = Msg("bg.left", "<b>{session}</b> keeps working in Claude Code's background.",
              note="HTML. bg.busy_question after Leave it was tapped",
              eg={"session": "x1"})
BG_BACK = Msg("bg.back", "↩️ <b>{session}</b> is back from Claude Code's background.",
              note="HTML. The session was brought back from a Claude Code background job",
              eg={"session": "x1"})
BG_FAILED = Msg(
    "bg.failed", "Could not bring <b>{session}</b> back from Claude Code's background: {error}",
    note="HTML. Bringing the session back failed; {error} is one of bg.err.*",
    eg={"session": "x1", "error": "claude stop did not answer in time"})
BG_TOAST_FAILED = Msg(
    "bg.toast_failed", "Could not bring {session} back from Claude Code's background: {error}",
    note="Pop-up: the same as bg.failed", eg={"session": "x1",
                                             "error": "claude stop did not answer in time"})
BG_NO_CONVERSATION = Msg(
    "bg.no_conversation",
    "Could not bring <b>{session}</b> back because Claude Code has no saved conversation "
    "for it.",
    note="HTML. Bringing the session back found no saved conversation to resume",
    eg={"session": "x1"})
BG_TOAST_NO_CONVERSATION = Msg(
    "bg.toast_no_conversation",
    "Could not bring {session} back because Claude Code has no saved conversation for it.",
    note="Pop-up: the same as bg.no_conversation", eg={"session": "x1"})
BG_TOAST_NOT_PARKED = Msg("bg.toast_not_parked", "{session} is not in Claude Code's background.",
                          note="Pop-up: Bring it back tapped for a session that is not in "
                               "a Claude Code background job", eg={"session": "x1"})
BG_TOAST_BRINGING_BACK = Msg("bg.toast_bringing_back", "Bringing {session} back…",
                             note="Pop-up: a bring-back from Claude Code's background started",
                             eg={"session": "x1"})

BG_HALT_NOT_STOPPED = Msg(
    "bg.halt_not_stopped",
    "⚠️ The Claude Code background job of <b>{session}</b> could not be stopped, so the "
    "blocked work may still be running there: {error}",
    note="HTML. Under a safety halt's notice: the session's conversation runs in a Claude "
         "Code background job, and stopping that job failed; {error} is one of bg.err.*",
    eg={"session": "x1", "error": "claude stop did not answer in time"})
BG_HALT_NOT_STOPPED_PLAIN = Msg(
    "bg.halt_not_stopped_plain",
    "⚠️ The Claude Code background job of <b>{session}</b> could not be stopped, so the "
    "blocked work may still be running there.",
    note="HTML. bg.halt_not_stopped when there is no reason to give",
    eg={"session": "x1"})

#: The reasons a bring-back or a resume gives when a background job is in
#: the way (RestartOutcome / ResumeOutcome).
BG_FAILURES = frozenset({"bg_busy", "bg_stop_failed", "bg_no_conversation"})


def bg_error(tr, outcome) -> str:
    """Why *outcome* (``bg_stop_failed``) failed, as plain text in *tr*'s
    language."""
    code, params = outcome.bg_err or ("", {})
    return error_text(tr, code, params)


def bg_outcome_html(tr, outcome, label: str) -> str | None:
    """The chat line for a bring-back or resume refused by a background
    job (HTML, *tr*'s language), or None for any other reason."""
    if outcome.reason == "bg_busy":
        return tr.html(BG_BUSY_QUESTION, session=label)
    if outcome.reason == "bg_stop_failed":
        return tr.html(BG_FAILED, session=label, error=bg_error(tr, outcome))
    if outcome.reason == "bg_no_conversation":
        return tr.html(BG_NO_CONVERSATION, session=label)
    if outcome.reason == "not_parked":
        return tr.html(BG_TOAST_NOT_PARKED, session=label)
    if outcome.reason == "parked":
        return tr.html(BG_REFUSED_PARKED, session=label)
    return None


def bg_outcome_text(tr, outcome, label: str) -> str | None:
    """:func:`bg_outcome_html` for a pop-up (plain text)."""
    if outcome.reason == "bg_stop_failed":
        return tr.text(BG_TOAST_FAILED, session=label, error=bg_error(tr, outcome))
    if outcome.reason == "bg_no_conversation":
        return tr.text(BG_TOAST_NO_CONVERSATION, session=label)
    if outcome.reason == "not_parked":
        return tr.text(BG_TOAST_NOT_PARKED, session=label)
    if outcome.reason == "parked":
        return tr.text(BG_TOAST_REFUSED_PARKED, session=label)
    return None


def halt_not_stopped_html(tr, outcome, label: str) -> str:
    """The line a safety halt adds when it could not stop the background
    job its session's conversation runs in (HTML, *tr*'s language)."""
    if outcome is not None and getattr(outcome, "bg_err", ()):
        return tr.html(BG_HALT_NOT_STOPPED, session=label, error=bg_error(tr, outcome))
    return tr.html(BG_HALT_NOT_STOPPED_PLAIN, session=label)


def stop_refused_html(tr, outcome) -> str:
    """Why a Stop of a parked session did not bring it back (HTML)."""
    return (bg_outcome_html(tr, outcome, outcome.label)
            or tr.html(BG_FAILED, session=outcome.label, error=bg_error(tr, outcome)))


def stop_refused_text(tr, outcome) -> str:
    """:func:`stop_refused_html` for a pop-up."""
    return (bg_outcome_text(tr, outcome, outcome.label)
            or tr.text(BG_TOAST_FAILED, session=outcome.label, error=bg_error(tr, outcome)))
