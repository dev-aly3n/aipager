"""What Stop says when it ends a session's background agents, or ends a
job with no keys (roadmap 8.121 F5).

Stop on a session whose own turn has ended while its job's background
agents still run presses Claude Code's kill chord (``STOP_KILL_AGENTS``);
with nothing but the grace window or the job's shells left it types
nothing (``STOP_END_JOB``). Every surface (the busy card, a toast, the
/stop reply, the Mini App's notice and its chat mirror) words the result
through the renderers here, so they all say the same. Lines are joined by
a line break, never glued into one sentence. A Stop that typed the two
Escapes (``STOP_KEYS``) keeps its own words, unchanged.
"""

from __future__ import annotations

import html as html_mod

from aipager.i18n import Command, Msg, Plural, Span
from aipager.state import STOP_END_JOB, STOP_KILL_AGENTS

#: Refusals of a Stop on a session whose own turn has ended: a dialog of
#: Claude's own (or one nobody can name) is open, or the kill chord could
#: not be pressed. Nothing changed in either case.
STOP_REFUSALS = frozenset({"dialog_open", "agents_not_stopped"})

CARD_STOPPED_AGENTS = Plural(
    "card.stopped_agents",
    one="⚠️ <b>{session}</b> · Stopped {n} background agent",
    other="⚠️ <b>{session}</b> · Stopped {n} background agents",
    note="HTML. The busy card after Stop ended the background agents of a session whose "
         "own turn had ended; {n} counts the agents stopped. "
         "Background agents are Claude's background helpers; background shells are commands "
         "it started in the background, which Stop does not end",
    eg={"session": "x1"})
CARD_STOPPED_AGENTS_BY = Plural(
    "card.stopped_agents_by",
    one="⚠️ <b>{session}</b> · {person} stopped {n} background agent",
    other="⚠️ <b>{session}</b> · {person} stopped {n} background agents",
    note="HTML. The busy card after Stop in a group ended background agents: {person} is "
         "who stopped them (@username); {n} counts the agents stopped. "
         "Background agents are Claude's background helpers; background shells are commands "
         "it started in the background, which Stop does not end",
    eg={"session": "x1", "person": "@bob"})
CARD_SHELLS_KEEP_RUNNING = Plural(
    "card.shells_keep_running",
    one="🖥 {n} background shell keeps running.",
    other="🖥 {n} background shells keep running.",
    note="HTML. The busy card's second line after a Stop: {n} counts the job's background "
         "shells, which Stop does not end.  "
         "Background agents are Claude's background helpers; background shells are commands "
         "it started in the background, which Stop does not end")
ANS_STOPPED_AGENTS = Plural(
    "ans.stopped_agents",
    one="⏹ Stopped {n} background agent of {session}",
    other="⏹ Stopped {n} background agents of {session}",
    note="After Stop ended the background agents of a session whose own turn had ended "
         "(a pop-up, a reply, the Mini App); {n} counts the agents stopped. "
         "Background agents are Claude's background helpers; background shells are commands "
         "it started in the background, which Stop does not end",
    eg={"session": "x1"})
ANS_STOPPED_AGENTS_BY = Plural(
    "ans.stopped_agents_by",
    one="⏹ {person} stopped {n} background agent of {session}",
    other="⏹ {person} stopped {n} background agents of {session}",
    note="After Stop in a group ended background agents: {person} is who stopped them "
         "(@username); {n} counts the agents stopped. "
         "Background agents are Claude's background helpers; background shells are commands "
         "it started in the background, which Stop does not end",
    eg={"session": "x1", "person": "@bob"})
ANS_SHELLS_KEEP_RUNNING = Plural(
    "ans.shells_keep_running",
    one="{n} background shell keeps running.",
    other="{n} background shells keep running.",
    note="A line after a Stop's result: {n} counts the job's background shells, which "
         "Stop does not end.  "
         "Background agents are Claude's background helpers; background shells are commands "
         "it started in the background, which Stop does not end")
ANS_QUEUED_DISCARDED = Plural(
    "ans.queued_discarded",
    one="{n} queued message was discarded.",
    other="{n} queued messages were discarded.",
    note="A line after a Stop's result: {n} counts the messages waiting in Claude's queue "
         "that were thrown away")
STOP_DIALOG_OPEN = Msg(
    "stop.dialog_open",
    "{session} has a dialog open in the terminal. Answer it there, then try Stop again.",
    note="Stop was refused, nothing changed: Claude itself shows a dialog in the terminal "
         "(a question or a permission prompt of its own). Stop is the button's name",
    eg={"session": "x1"})
STOP_AGENTS_NOT_STOPPED = Msg(
    "stop.agents_not_stopped",
    "The background agents of {session} could not be stopped. Try Stop again.",
    note="Stop could not send the keys that end the background agents, so they may still "
         "run. Stop is the button's name.  "
         "Background agents are Claude's background helpers; background shells are commands "
         "it started in the background, which Stop does not end",
    eg={"session": "x1"})
TOAST_STOPPING = Msg(
    "toast.stopping",
    "⏹ Stopping {session}…",
    note="Pop-up right after Stop was tapped, while the stop is still under way (it can take "
         "a few seconds). The busy card, or the list the button was on, shows how it went "
         "a moment later",
    eg={"session": "x1"})
PROMPT_AGENT_STOPPED = Msg(
    "prompt.agent_stopped",
    "⏹ This prompt closed because its background agent was stopped.",
    note="Added under a background agent's permission prompt or question when Stop ended "
         "that agent: its buttons are gone and do nothing.  "
         "Background agents are Claude's background helpers; background shells are commands "
         "it started in the background, which Stop does not end")


def _mode(outcome) -> str:
    mode = getattr(outcome, "mode", "")
    return mode if isinstance(mode, str) else ""


def _by(outcome) -> str:
    by = getattr(outcome, "by", "")
    return by if isinstance(by, str) else ""


def _count(outcome, name: str) -> int:
    value = getattr(outcome, name, 0)
    return value if isinstance(value, int) and value > 0 else 0


def card_text(tr, outcome) -> str:
    """The busy card after a Stop (HTML, *tr*'s language): after the kill
    chord `⚠️ x1 · Stopped 2 background agents`, after a Stop that ended
    the job with no keys the plain Stopped line; either way, with job
    shells left, a second line saying they keep running. Any other Stop
    reads exactly as before."""
    from aipager.bot.session_ops import _stopped_card_text
    by = _by(outcome)
    mode = _mode(outcome)
    if mode == STOP_KILL_AGENTS:
        params = {"session": outcome.label}
        if by:
            params["person"] = Command(by)
        first = str(tr.n(CARD_STOPPED_AGENTS_BY if by else CARD_STOPPED_AGENTS,
                         _count(outcome, "agents"), kind="html", **params))
    else:
        first = str(_stopped_card_text(outcome.label, by, tr))
    lines = [first]
    shells = _count(outcome, "shells")
    if shells and mode in (STOP_KILL_AGENTS, STOP_END_JOB):
        lines.append(str(tr.n(CARD_SHELLS_KEEP_RUNNING, shells, kind="html")))
    return "\n".join(lines)


def stopped_line(tr, outcome, *, html: bool, first: str | None = None) -> str:
    """What a toast, the /stop reply, the Mini App's notice or its chat
    mirror says after Stop ended a session's background agents (or its
    job, with no keys): the first line, then the queued messages
    discarded, then the shells left running, each its own line. *first*
    replaces the first line of a Stop that ended the job with no keys (the
    chat mirror's own words); by default it is the Stop line every
    surface uses."""
    mode = _mode(outcome)
    kind = "html" if html else "text"
    by = _by(outcome)
    lines: list[str] = []
    if mode == STOP_KILL_AGENTS:
        session = (Span(f"<b>{html_mod.escape(outcome.label)}</b>") if html
                   else outcome.label)
        params = {"session": session}
        if by:
            params["person"] = Command(by)
        lines.append(str(tr.n(ANS_STOPPED_AGENTS_BY if by else ANS_STOPPED_AGENTS,
                              _count(outcome, "agents"), kind=kind, **params)))
        dropped = _count(outcome, "dropped")
        if dropped:
            lines.append(str(tr.n(ANS_QUEUED_DISCARDED, dropped, kind=kind)))
    else:
        if first is None:
            from aipager.bot.callbacks import _keys_stopped_line
            first = _keys_stopped_line(outcome, html=html, tr=tr)
        lines.append(first)
    shells = _count(outcome, "shells")
    if shells and mode in (STOP_KILL_AGENTS, STOP_END_JOB):
        lines.append(str(tr.n(ANS_SHELLS_KEEP_RUNNING, shells, kind=kind)))
    return "\n".join(lines)


def refusal(tr, outcome, *, html: bool) -> str | None:
    """Why a Stop of a session whose own turn has ended changed nothing
    (:data:`STOP_REFUSALS`), in *tr*'s language; None for any other
    outcome."""
    reason = getattr(outcome, "reason", "")
    render = tr.html if html else tr.text
    if reason == "dialog_open":
        return str(render(STOP_DIALOG_OPEN, session=outcome.label))
    if reason == "agents_not_stopped":
        return str(render(STOP_AGENTS_NOT_STOPPED, session=outcome.label))
    return None
