"""Card rows in the chat's language (multilingual design, phase 2).

The busy card's settled rows are stored as English text in
``tool_history``: the settled agent row (``notify``), a background
shell's end (``bg_shells.settled_row``, written from the hook's import
closure, which never loads :mod:`aipager.i18n`) and a prompt's answer
(``callbacks``). They stay English in memory and on disk: the tally on
the status line parses them, and a switch of language must not leave a
card half in the old one.

This module draws them in the chat's language at render time. It knows
the closed list of shapes aipager's own code writes, matched by anchored
patterns kept next to those writers' formats (a test builds each row
through its writer). A matched row is drawn from a key, with its name,
label or detail in a code span and the words outside it, so nothing
invisible ever lands inside the span; its clock is re-rendered from the
parsed seconds (Persian digits in Persian). Anything else (every tool
summary, an older or unknown shape) is drawn as stored: tool summaries
are never translated.

English is the stored bytes, by construction: every function returns
exactly what the card drew before for ``tr.code == "en"``.

Display only: nothing here decides anything. Daemon only; the hook's
modules (``state``, ``bg_shells``, ``config``, ``policy_snapshot``) never
import it (``tests/test_i18n_hook_isolation.py``).
"""

from __future__ import annotations

import re

from aipager.i18n import Msg, Plural, Safe, Span

__all__ = ["ANSWER_VERBS", "live_agent", "live_agent_final", "live_shell", "live_shell_final",
           "render_md", "render_text", "verb_text"]

AGENT_DONE = Plural(
    "card.row.agent_done",
    one="🤖 {agent} · {n} tool call · {clock}",
    other="🤖 {agent} · {n} tool calls · {clock}",
    note="A finished agent's row on the busy card: its type (code), how many tool "
         "calls it made, how long it ran",
    eg={"agent": "Explore", "clock": "1:05"})
AGENT_LIVE = Msg("card.row.agent_live", "🤖 {agent} · {activity} · {clock}",
                 note="A running agent's row: its type, what it is doing now (a tool "
                      "call, kept as it is, or the word for starting), how long",
                 eg={"agent": "Explore", "activity": "Read: /src/app.py", "clock": "0:12"})
AGENT_STILL_RUNNING = Msg("card.row.agent_still_running", "🤖 {agent} (still running)",
                          note="A running agent on a card that has settled",
                          eg={"agent": "Explore"})
AGENT_WORD = Msg("card.row.agent_word", "agent",
                 note="An agent whose type is unknown, in place of its type")
STARTING = Msg("card.row.starting", "starting",
               note="What a just-started agent is doing: nothing yet")
SHELL_LIVE = Msg("card.row.shell_live", "shell: {label} ({clock})",
                 note="A background shell command still running, and for how long",
                 eg={"label": "npm run dev", "clock": "0:30"})
SHELL_STILL_RUNNING = Msg("card.row.shell_still_running", "shell: {label} (still running)",
                          note="A background shell still running on a settled card",
                          eg={"label": "npm run dev"})
SHELL_DONE = Msg("card.row.shell_done", "shell: {label} - done ({clock})",
                 note="A background shell that finished", eg={"label": "npm test",
                                                              "clock": "1:30"})
SHELL_FAILED = Msg("card.row.shell_failed", "shell: {label} - failed ({clock})",
                   note="A background shell that failed, after how long",
                   eg={"label": "make lint", "clock": "1:05"})
SHELL_FAILED_EXIT = Msg("card.row.shell_failed_exit", "shell: {label} - failed (exit {code})",
                        note="A background shell that failed with an exit code",
                        eg={"label": "make all", "code": 2})
SHELL_LOST = Msg("card.row.shell_lost", "shell: {label} - no end seen ({clock})",
                 note="A background shell whose end aipager never saw",
                 eg={"label": "sleep 900", "clock": "15:00"})
SHELL_STOPPED = Msg("card.row.shell_stopped", "shell: {label} - stopped ({clock})",
                    note="A background shell that was stopped",
                    eg={"label": "tail -f log", "clock": "0:30"})
ANSWER = Msg("card.row.answer", "{icon} {detail} → {verb}",
             note="An answered prompt on the busy card: the tool call or question (code), "
                  "then how it was answered. RTL catalogs use ←",
             eg={"icon": "🔑", "detail": "Bash: ls", "verb": "Allowed"})

#: The shown answer verb by the action id (the audit log keeps the
#: English of ``transport.ACTION_VERBS``).
ANSWER_VERBS = {
    "allow": Msg("ans.verb.allow", "Allowed", note="How a permission prompt was answered"),
    "deny": Msg("ans.verb.deny", "Denied", note="How a permission prompt was answered"),
    "continue": Msg("ans.verb.continue", "Continued",
                    note="How a prompt was answered: Claude was told to go on"),
    "allow_always": Msg("ans.verb.allow_always", "Allowed always",
                        note="How a permission prompt was answered: allowed, and a "
                             "standing rule saved"),
}
OPTION_VERB = Msg("ans.verb.option", "Selected option {n}",
                  note="How a question was answered: the option's number", eg={"n": 2})
SUBMITTED_NONE = Msg("ans.verb.submitted_none", "Submitted (none)",
                     note="A multiple-choice question submitted with nothing ticked")
SELECTED = Msg("ans.verb.selected", "Selected: {options}",
               note="A multiple-choice question: the options ticked (Claude's own words)",
               eg={"options": "Postgres, SQLite"})

_CLOCK = r"(?P<clock>\d+:\d{2}(?::\d{2})?)"
_AGENT_DONE_RE = re.compile(
    r"\A🤖 (?P<agent>.+?) · (?P<n>\d+) tool calls? · " + _CLOCK + r"\Z", re.S)
_SHELL_RE = re.compile(
    r"\Ashell: (?P<label>.*) - (?P<what>done|failed|no end seen|stopped) "
    r"\((?P<detail>[^()]*)\)\Z", re.S)
_ANSWER_RE = re.compile(
    r"\A(?P<icon>🔑|❓) (?P<detail>.*) → (?P<verb>Allowed always|Allowed|Denied|Continued"
    r"|Selected option (?P<opt>\d+)|Submitted \(none\)|Selected: (?P<sel>.*))\Z", re.S)
_CLOCK_ONLY = re.compile(r"\A" + _CLOCK + r"\Z")

_SHELL_MSGS = {"done": SHELL_DONE, "failed": SHELL_FAILED, "no end seen": SHELL_LOST,
               "stopped": SHELL_STOPPED}


def _seconds(clock: str) -> int:
    total = 0
    for part in clock.split(":"):
        total = total * 60 + int(part)
    return total


def _mono(text: str) -> str:
    from aipager.bot.animation import _mono as card_mono
    return card_mono(text)


def _verb_ids() -> dict[str, str]:
    from aipager.bot.transport import ACTION_VERBS
    return {english: action for action, english in ACTION_VERBS.items()}


def verb_text(tr, verb: str, kind: str = "md") -> str:
    """The shown form of an answer *verb* as stored (English): an action
    verb, ``Selected option N``, ``Submitted (none)``, ``Selected: ...``;
    anything else as stored."""
    render = getattr(tr, kind)
    action = _verb_ids().get(verb)
    if action in ANSWER_VERBS:
        return render(ANSWER_VERBS[action])
    m = re.fullmatch(r"Selected option (\d+)", verb)
    if m:
        return render(OPTION_VERB, n=int(m.group(1)))
    if verb == SUBMITTED_NONE:
        return render(SUBMITTED_NONE)
    if verb.startswith("Selected: "):
        options = verb[len("Selected: "):]
        return render(SELECTED, options=Span(_mono(options)) if kind == "md" else options)
    return verb


def _render(tr, summary: str, kind: str, code) -> str | None:
    """*summary* drawn from its key, or None for a shape not ours."""
    render = getattr(tr, kind)

    def span(text: str):
        return Span(code(text)) if kind == "md" else text

    m = _AGENT_DONE_RE.match(summary)
    if m:
        return tr.n(AGENT_DONE, int(m.group("n")), kind=kind, agent=span(m.group("agent")),
                    clock=Safe(tr.clock(_seconds(m.group("clock")))))
    m = _SHELL_RE.match(summary)
    if m:
        label, what, detail = m.group("label"), m.group("what"), m.group("detail")
        exit_code = re.fullmatch(r"exit (-?\d+)", detail)
        if what == "failed" and exit_code:
            return render(SHELL_FAILED_EXIT, label=span(label), code=int(exit_code.group(1)))
        if _CLOCK_ONLY.match(detail):
            return render(_SHELL_MSGS[what], label=span(label),
                          clock=Safe(tr.clock(_seconds(detail))))
        return None
    m = _ANSWER_RE.match(summary)
    if m:
        return render(ANSWER, icon=Safe(m.group("icon")), detail=span(m.group("detail")),
                      verb=Safe(verb_text(tr, m.group("verb"), kind)))
    return None


def render_md(tr, summary: str, *, code=None) -> str:
    """A stored row's text part for the card (Markdown): ``_mono(summary)``
    in English and for any shape not ours; else drawn from its key with
    the name in a code span (*code*, ``_mono`` by default; the full log
    passes its own one-line span)."""
    code = code or _mono
    summary = str(summary)
    if tr.code == "en":
        return code(summary)
    drawn = _render(tr, summary, "md", code)
    return drawn if drawn is not None else code(summary)


def render_text(tr, summary: str) -> str:
    """A stored row as plain text (/status's current step): the stored
    text in English and for any shape not ours."""
    summary = str(summary)
    if tr.code == "en":
        return summary
    drawn = _render(tr, summary, "text", str)
    return drawn if drawn is not None else summary


def _agent_name(tr, info: dict):
    agent_type = info.get("type")
    return Span(_mono(agent_type)) if agent_type else Safe(tr.md(AGENT_WORD))


def live_agent(tr, info: dict, unit: str = "s") -> str:
    """A running agent's row (Markdown): the English row in one code span,
    else the words outside the spans."""
    from aipager.bot import animation
    if tr.code == "en":
        return _mono(animation._agent_live_row(info, unit))
    activity = info.get("activity")
    started = info.get("started_at", animation.time.monotonic())
    clock = tr.clock(animation.time.monotonic() - started, unit)
    return tr.md(AGENT_LIVE, agent=_agent_name(tr, info),
                 activity=Span(_mono(activity)) if activity else Safe(tr.md(STARTING)),
                 clock=Safe(clock))


def live_agent_final(tr, info: dict) -> str:
    """A running agent on a settled card: no counter."""
    from aipager.bot import animation
    if tr.code == "en":
        return _mono(f"{animation._SUBAGENT_MARK}{info.get('type') or 'agent'}"
                     " (still running)")
    return tr.md(AGENT_STILL_RUNNING, agent=_agent_name(tr, info))


def live_shell(tr, label: str, started: float, unit: str = "s") -> str:
    """A running background shell's row (Markdown)."""
    from aipager import bg_shells
    from aipager.bot import animation
    if tr.code == "en":
        return _mono(bg_shells.live_row(label, animation._elapsed_str(started, unit)))
    clock = tr.clock(animation.time.monotonic() - started, unit)
    return tr.md(SHELL_LIVE, label=Span(_mono(label)), clock=Safe(clock))


def live_shell_final(tr, label: str) -> str:
    """A running shell on a settled card: no counter."""
    from aipager import bg_shells
    if tr.code == "en":
        return _mono(bg_shells.final_live_row(label))
    return tr.md(SHELL_STILL_RUNNING, label=Span(_mono(label)))
