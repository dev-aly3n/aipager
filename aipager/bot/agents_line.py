"""The status block under an answer: its background work, live
(roadmap 8.41, Claude Code's agent panel style).

A turn's answer can go out while agents it launched in the background are
still working (Claude Code's Stop fires while they run), or while shells
of its job are still up. The answer then ends with a block::

    ⏳ 2 agents working · 🖥 1 shell running
    ○ **pipeline-runner** · Writing final.md for the pipeline
    ✓ **Explore** · done (0:42)
    Results from the agents will follow here.

A header with the counts (shells as a count, never their command), and,
while an agent works, one row per agent (at most :data:`ROWS_MAX`, then
"+N more") and the promise. Once every agent is done the header alone
says so (``✅ 2 agents done (6:12) · 🖥 1 shell running``). Each line
is its own paragraph: Telegram's rich Markdown folds a single newline
into a space. Pure text here; the send, the bookkeeping and the edits
live in ``notify`` and ``state``.

What an agent is doing comes, in this order, from its own latest prose
(Claude's text: never translated, shown as written), its latest tool as a
translated phrase ("Reading state.py"), or its Agent call's description.
Agent types and tool names are names: never translated.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# One formatter for how long something ran: the settled shell rows use it
# too, from a module the session state can import (``Tr.clock`` is the same
# clock in the block's language).
from aipager.bg_shells import duration  # noqa: F401
from aipager.bidi_guard import reveal
from aipager.i18n import Msg, Plural, Safe, Span

#: A label (an agent type, a tool name) longer than this is cut, with an
#: ellipsis.
LABEL_MAX = 32
#: An agent's activity (its prose, its Agent call's description) is cut to
#: this many characters, at a word boundary, with an ellipsis.
ACTIVITY_MAX = 60
#: A tool phrase's argument (a file name, a pattern) is cut to this many.
ARG_MAX = 40
#: At most this many agent rows; the rest are counted ("+N more").
ROWS_MAX = 4

#: The paragraph break between the block's lines (and before the block).
LINE_SEP = "\n\n"


def _md_escape(text: str) -> str:
    # The set the card escapes (animation._md_escape), plus the backslash:
    # a label is an agent type, and "general_purpose" must not turn italic,
    # nor a type ending in "\\" escape the bold's closing star.
    return re.sub(r"([\\*_`\[\]])", r"\\\1", text)


def _bs(text: str) -> str:
    """*text* (Claude's, plain) with every backslash doubled, for ``tr.md``:
    the card's escaper then escapes ``*_`[]`` but leaves a backslash
    alone, so one Claude wrote (``\\*``) would cancel the escape of the
    mark after it. Doubled first, it stays a literal backslash."""
    return text.replace("\\", "\\\\")


def _cut(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    if len(text) > limit:
        text = text[:limit - 1].rstrip() + "…"
    return text


def _label(text: str) -> str:
    # A name from Claude Code (an agent type, a tool name): a direction
    # control in it is shown as a visible marker, never left to reorder
    # the row.
    return _cut(reveal(str(text)), LABEL_MAX)


COUNT_AGENTS = Plural("agents.count_agents", one="{n} agent", other="{n} agents",
                      note="How many background agents (in the line under an answer)")
COUNT_SHELLS = Plural("agents.count_shells", one="{n} shell", other="{n} shells",
                      note="How many background shell commands")

HEAD_WORKING = Plural(
    "agents.head.working", one="⏳ {n} agent working", other="⏳ {n} agents working",
    note="Under an answer, the first line of its background work: how many background "
         "agents are still working on the task")
HEAD_DONE = Plural(
    "agents.head.done", one="✅ {n} agent done ({clock})", other="✅ {n} agents done ({clock})",
    note="Under an answer, once all its background agents finished; {clock} is how long "
         "after the answer the last one finished (like 6:12)",
    eg={"clock": "6:12"})
HEAD_SHELLS_RUNNING = Plural(
    "agents.head.shells_running", one="🖥 {n} shell running", other="🖥 {n} shells running",
    note="Under an answer: how many background shell commands of this task are still "
         "running (a dev server, a test run)")
HEAD_SHELLS_FINISHED = Plural(
    "agents.head.shells_finished", one="{n} shell finished ({clock})",
    other="{n} shells finished ({clock})",
    note="Under an answer: background shell commands that ended on their own; {clock} "
         "is how long the longest one ran",
    eg={"clock": "2:13:05"})
HEAD_SHELLS_STOPPED = Plural(
    "agents.head.shells_stopped", one="{n} shell stopped ({clock})",
    other="{n} shells stopped ({clock})",
    note="Under an answer: background shell commands that were stopped (killed) before "
         "they ended; {clock} is how long the longest one ran",
    eg={"clock": "4:10"})
HEAD_JOIN = Msg(
    "agents.head.join", "{first} · {second}",
    note="Joins two parts of the first line under an answer, like "
         "\"⏳ 2 agents working\" and \"🖥 1 shell running\"",
    eg={"first": "⏳ 2 agents working", "second": "🖥 1 shell running"})
ROW_WORKING = Msg(
    "agents.row.working", "○ **{agent}** · {activity}",
    note="One background agent still working, under an answer: {agent} is its type (a "
         "name, never translated, like Explore); {activity} what it is doing now",
    eg={"agent": "pipeline-runner", "activity": "Reading state.py"})
ROW_WORKING_BARE = Msg(
    "agents.row.working_bare", "○ **{agent}**",
    note="One background agent still working, under an answer, when nothing is known "
         "about what it does: {agent} is its type (a name, never translated)",
    eg={"agent": "Explore"})
ROW_DONE = Msg(
    "agents.row.done", "✓ **{agent}** · done ({clock})",
    note="One background agent that finished, under an answer: {agent} is its type (a "
         "name, never translated); {clock} how long it ran",
    eg={"agent": "Explore", "clock": "0:42"})
MORE = Plural(
    "agents.more", one="+{n} more", other="+{n} more",
    note="After the first four agent rows under an answer: how many more agents there are")
PROMISE = Msg(
    "agents.promise", "Results from the agents will follow here.",
    note="Last line under an answer while background agents still work: their results "
         "will arrive in this chat")

ACT_READING = Msg("agents.act.reading", "Reading {file}",
                  note="What a background agent is doing: reading a file ({file} is its name)",
                  eg={"file": "state.py"})
ACT_WRITING = Msg("agents.act.writing", "Writing {file}",
                  note="What a background agent is doing: writing a file ({file} is its name)",
                  eg={"file": "final.md"})
ACT_EDITING = Msg("agents.act.editing", "Editing {file}",
                  note="What a background agent is doing: changing a file ({file} is its name)",
                  eg={"file": "notify.py"})
ACT_RUNNING = Msg("agents.act.running", "Running a command",
                  note="What a background agent is doing: running a shell command (never "
                       "shown)")
ACT_SEARCHING = Msg("agents.act.searching", "Searching for {pattern}",
                    note="What a background agent is doing: searching files for a text or "
                         "file name pattern",
                    eg={"pattern": "agents_line"})
ACT_SEARCHING_WEB = Msg("agents.act.searching_web", "Searching the web for {query}",
                        note="What a background agent is doing: a web search",
                        eg={"query": "telegram bot api"})
ACT_FETCHING = Msg("agents.act.fetching", "Fetching {site}",
                   note="What a background agent is doing: loading a web page from a site",
                   eg={"site": "docs.python.org"})
ACT_AGENT = Msg("agents.act.agent", "Starting an agent",
                note="What a background agent is doing: starting another agent of its own")
ACT_TOOL = Msg("agents.act.tool", "Using {tool}",
               note="What a background agent is doing: using a tool ({tool} is the tool's "
                    "name, never translated)",
               eg={"tool": "TodoWrite"})

#: Tool name -> (phrase, the parameter its argument fills, or "").
_TOOL_PHRASES: dict[str, tuple[Msg, str]] = {
    "Read": (ACT_READING, "file"),
    "Write": (ACT_WRITING, "file"),
    "Edit": (ACT_EDITING, "file"),
    "MultiEdit": (ACT_EDITING, "file"),
    "NotebookEdit": (ACT_EDITING, "file"),
    "Bash": (ACT_RUNNING, ""),
    "Grep": (ACT_SEARCHING, "pattern"),
    "Glob": (ACT_SEARCHING, "pattern"),
    "WebSearch": (ACT_SEARCHING_WEB, "query"),
    "WebFetch": (ACT_FETCHING, "site"),
    "Agent": (ACT_AGENT, ""),
    "Task": (ACT_AGENT, ""),
}


@dataclass(frozen=True)
class AgentRow:
    """One agent of the block. ``seconds`` is the run time shown once
    ``done``."""

    agent_type: str
    done: bool
    seconds: float | None = None
    prose: str = ""
    tool: str = ""
    tool_arg: str = ""
    description: str = ""


@dataclass(frozen=True)
class BlockView:
    """Everything the block shows, at one moment."""

    agents: tuple[AgentRow, ...] = ()
    agents_done_seconds: float | None = None
    shells_running: int = 0
    shells_finished: int = 0
    shells_finished_seconds: float = 0.0
    shells_stopped: int = 0
    shells_stopped_seconds: float = 0.0


def _tr(tr):
    if tr is None:
        from aipager import i18n
        return i18n.get("en")
    return tr


# ---- an agent's activity ----------------------------------------------------------

_FENCE = re.compile(r"^(```|~~~)")
_BLOCK_MARKERS = re.compile(r"^(?:#{1,6}\s+|>\s*|[-*+]\s+|\d{1,9}[.)]\s+)+")
_SENTENCE_END = re.compile(r"[.!?](?=\s)|[。！？]")


def first_line(text: str) -> str:
    """The first thing *text* says: its first non-empty line outside a
    fenced code block (the whole block is skipped, not only its fence
    lines), without leading block markers (headings, quotes, list
    bullets), up to the end of its first sentence, direction controls
    shown as visible markers, whitespace collapsed, at most
    :data:`ACTIVITY_MAX` characters (cut at a word boundary with "…", or
    hard where there is no space). Inline markup (``**``, ``__``,
    backticks) stays as written: the renderer escapes it."""
    if not isinstance(text, str):
        return ""
    fenced = False
    for raw in text.splitlines():
        line = raw.strip()
        if _FENCE.match(line):
            fenced = not fenced
            continue
        if fenced or not line:
            continue
        line = _BLOCK_MARKERS.sub("", line).strip()
        if not line:
            continue
        m = _SENTENCE_END.search(line)
        if m:
            line = line[:m.end()]
        line = " ".join(reveal(line).split())
        if not line:
            continue
        return _trim(line, ACTIVITY_MAX)
    return ""


def _trim(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit - 1]
    space = cut.rfind(" ")
    if space > 0:
        cut = cut[:space]
    return cut.rstrip() + "…"


def _tool_phrase(tool: str, arg: str, tr) -> Safe | None:
    if not tool:
        return None
    phrase, param = _TOOL_PHRASES.get(tool, (None, ""))
    arg = _bs(_cut(reveal(arg or ""), ARG_MAX))
    if phrase is not None and not param:
        return tr.md(phrase)
    if phrase is not None and arg:
        return tr.md(phrase, **{param: arg})
    return tr.md(ACT_TOOL, tool=Span(_md_escape(_label(tool))))


def _activity(row: AgentRow, tr):
    """What the row says the agent is doing: its prose (a plain string,
    escaped and isolated by ``tr``), its tool's phrase, its Agent call's
    description, or ``None``."""
    prose = first_line(row.prose)
    if prose:
        return _bs(prose)
    phrase = _tool_phrase(row.tool, row.tool_arg, tr)
    if phrase is not None:
        return phrase
    desc = first_line(row.description)
    return _bs(desc) if desc else None


# ---- the block --------------------------------------------------------------------

def _header(view: BlockView, tr) -> str:
    parts: list[Safe] = []
    working = sum(1 for a in view.agents if not a.done)
    if working:
        parts.append(tr.n(HEAD_WORKING, working, kind="md"))
    elif view.agents:
        parts.append(tr.n(HEAD_DONE, len(view.agents), kind="md",
                          clock=Safe(tr.clock(view.agents_done_seconds or 0))))
    if view.shells_running:
        parts.append(tr.n(HEAD_SHELLS_RUNNING, view.shells_running, kind="md"))
    else:
        if view.shells_finished:
            parts.append(tr.n(HEAD_SHELLS_FINISHED, view.shells_finished, kind="md",
                              clock=Safe(tr.clock(view.shells_finished_seconds))))
        if view.shells_stopped:
            parts.append(tr.n(HEAD_SHELLS_STOPPED, view.shells_stopped, kind="md",
                              clock=Safe(tr.clock(view.shells_stopped_seconds))))
    if not parts:
        return ""
    head = parts[0]
    for part in parts[1:]:
        head = tr.md(HEAD_JOIN, first=Safe(head), second=Safe(part))
    return str(head)


def _row(row: AgentRow, tr) -> str:
    agent = _bs(_label(row.agent_type))
    if row.done:
        return tr.md(ROW_DONE, agent=agent, clock=Safe(tr.clock(row.seconds or 0)))
    activity = _activity(row, tr)
    if activity is None:
        return tr.md(ROW_WORKING_BARE, agent=agent)
    return tr.md(ROW_WORKING, agent=agent, activity=activity)


def render_block(view: BlockView, tr=None) -> str:
    """The block as Telegram rich Markdown, its lines separated by a blank
    line, in *tr*'s language (English when None); ``""`` when it has no
    agent and no shell."""
    tr = _tr(tr)
    header = _header(view, tr)
    if not header:
        return ""
    lines = [header]
    if any(not a.done for a in view.agents):
        # Rows only while an agent works: once they are all done the
        # header says so, with the clock, in one line.
        lines += [_row(row, tr) for row in view.agents[:ROWS_MAX]]
        if len(view.agents) > ROWS_MAX:
            lines.append(tr.n(MORE, len(view.agents) - ROWS_MAX, kind="md"))
        lines.append(tr.md(PROMISE))
    return LINE_SEP.join(str(line) for line in lines)


# ---- the room the block may ever need ---------------------------------------------

#: Counts the worst case tries: every plural category with up to three
#: digits (the agents and shells an answer lists are capped well below).
_WORST_COUNTS = tuple(range(0, 121)) + (999,)
_WORST_CLOCK = 999 * 3600 + 59 * 60 + 59
_WIDE = "\U0001f600"  # one character, four UTF-8 bytes
_RESERVE: dict[tuple[str, bool], int] = {}


def _size(text) -> int:
    return len(str(text).encode("utf-8"))


def _worst_rows(tr) -> int:
    agent = _WIDE * LABEL_MAX
    sizes = [_size(_row(AgentRow(agent, True, _WORST_CLOCK), tr)),
             _size(_row(AgentRow(agent, False), tr)),
             _size(_row(AgentRow(agent, False, prose=_WIDE * (ACTIVITY_MAX + 1)), tr)),
             _size(_row(AgentRow(agent, False, tool=_WIDE * LABEL_MAX), tr))]
    for tool in _TOOL_PHRASES:
        sizes.append(_size(_row(AgentRow(agent, False, tool=tool,
                                         tool_arg=_WIDE * (ARG_MAX + 1)), tr)))
    return max(sizes)


def _worst_header(tr) -> int:
    """An upper bound of the header: its largest agent part, then its
    largest shell part (running, or finished and stopped joined), each
    joined on."""
    clock = Safe(tr.clock(_WORST_CLOCK))

    def most(plural, **params) -> int:
        return max(_size(tr.n(plural, n, kind="md", **params)) for n in _WORST_COUNTS)

    join = _size(tr.md(HEAD_JOIN, first=Safe(""), second=Safe("")))
    agents = max(most(HEAD_WORKING), most(HEAD_DONE, clock=clock))
    shells = max(most(HEAD_SHELLS_RUNNING),
                 most(HEAD_SHELLS_FINISHED, clock=clock) + join
                 + most(HEAD_SHELLS_STOPPED, clock=clock))
    return agents + join + shells


def reserve_bytes(tr=None) -> int:
    """UTF-8 bytes of ``"\\n\\n"`` plus the largest block *tr*'s language
    can render: what an answer leaves free for its block, so no later edit
    of the block can push the message past the ceiling."""
    tr = _tr(tr)
    # Per language AND direction: a block drawn left to right in a
    # right-to-left language (multilingual design 5.7, rule 5) carries
    # other marks, so its own size.
    key = (tr.code, tr.rtl)
    cached = _RESERVE.get(key)
    if cached is not None:
        return cached
    sep = _size(LINE_SEP)
    more = max(_size(tr.n(MORE, n, kind="md")) for n in _WORST_COUNTS)
    total = (sep + _worst_header(tr) + ROWS_MAX * (sep + _worst_rows(tr))
             + sep + more + sep + _size(tr.md(PROMISE)))
    _RESERVE[key] = total
    return total
