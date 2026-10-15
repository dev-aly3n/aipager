"""The status block under an answer: its background work, live
(roadmap 8.41, Claude Code's agent panel style).

A turn's answer can go out while agents it launched in the background are
still working (Claude Code's Stop fires while they run), or while shells
of its job are still up. The answer then ends with a divider and a
block::

    ⏳ 4 agents working · 🖥 1 shell running

    ○ 🤖 **pipeline-runner** · Writing final.md for the pipeline

    - ○ 🤖 **ship-tester** · Write and run criteria tests
    - ○ 🤖 **ship-tester › Explore** · Searching for agent_parents

    ⏸ 🤖 **r2-runner** · waiting for its own agents

    Results from the agents will follow here.

A header with the counts (shells as a count, never their command), and,
while an agent works, the agents as a tree two levels deep (at most
:data:`ROWS_MAX` rows, then "+N more") and the promise. An agent the
session started (or one whose starter is not known) is a paragraph of its
own; every agent under it, at any depth, follows it as ONE flat list, each
right after the agent that started it, and one deeper than the second
level names that agent before its own ("ship-tester › Explore"). Once
every agent is done the header alone says so (``✅ 2 agents done (6:12) ·
🖥 1 shell running``). Each other line is its own paragraph: Telegram's
rich Markdown folds a single newline into a space. Pure text here; the
divider, the send, the bookkeeping and the edits live in ``notify`` and
``state``.

What an agent is doing comes, in this order, from its own latest prose
(Claude's text: never translated, shown as written), its latest tool as a
translated phrase ("Reading state.py"), or its Agent call's description.
An agent waiting for agents of its own says that instead. Agent types and
tool names are names: never translated.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# One formatter for how long something ran: the settled shell rows use it
# too, from a module the session state can import (``Tr.clock`` is the same
# clock in the block's language).
from aipager.bg_shells import duration  # noqa: F401
from aipager.agent_tree import LINEAGE_SEP
from aipager.bidi_guard import reveal
from aipager.bot.divider import DIVIDER_SEP
from aipager.bot.footer import CLOSE, OPEN, footer, md_to_html
from aipager.i18n import Msg, Plural, Safe

#: A label (an agent type, a tool name) longer than this is cut, with an
#: ellipsis.
LABEL_MAX = 32
#: An agent's activity (its prose, its Agent call's description) is cut to
#: this many characters, at a word boundary, with an ellipsis.
ACTIVITY_MAX = 60
#: A tool phrase's argument (a file name, a pattern) is cut to this many.
ARG_MAX = 40
#: At most this many agent rows in all, at both levels; the rest are
#: counted ("+N more"). Enough for two agents with two helpers each, and
#: short enough to fit a phone's screen with the header and the promise.
ROWS_MAX = 6
#: The most blocks the divider and the block can ever take in Telegram's
#: count (up to 500 a message, list items and nested blocks included),
#: counted high: a paragraph or the divider 1, a list 1, a list item 2.
#: The divider, the header, "+N more" and the promise take 4, and each of
#: the ROWS_MAX rows at most 2 (a list item; a row with a list under it
#: takes 1 and the list 1).
BLOCKS_MAX = 4 + 2 * ROWS_MAX

#: The paragraph break between the block's lines.
LINE_SEP = "\n\n"
#: A second-level row: an item of its top agent's list. Put before the row
#: drawn in its language, it lands exactly where a right-to-left line's
#: start mark wants a list marker (before the mark, i18n.core).
LIST_ITEM = "- "


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
    "agents.row.working", "○ 🤖 **{agent}** · {activity}",
    note="One background agent still working, under an answer: {agent} is its type (a "
         "name, never translated, like Explore; or two names, the agent that started "
         "it first, like \"ship-tester › Explore\"); {activity} what it is doing now. "
         "Keep the ○ and the 🤖",
    eg={"agent": "pipeline-runner", "activity": "Reading state.py"})
ROW_WORKING_BARE = Msg(
    "agents.row.working_bare", "○ 🤖 **{agent}**",
    note="One background agent still working, under an answer, when nothing is known "
         "about what it does: {agent} is its type (a name, never translated; or two "
         "names, the agent that started it first). Keep the ○ and the 🤖",
    eg={"agent": "Explore"})
ROW_WAITING = Msg(
    "agents.row.waiting", "⏸ 🤖 **{agent}** · waiting for its own agents",
    note="One background agent under an answer that has paused until the agents it "
         "started itself finish (then it goes on): {agent} is its type (a name, never "
         "translated; or two names, the agent that started it first). Keep the ⏸ "
         "and the 🤖",
    eg={"agent": "pipeline-runner"})
ROW_DONE = Msg(
    "agents.row.done", "✓ 🤖 **{agent}** · done ({clock})",
    note="One background agent that finished, under an answer: {agent} is its type (a "
         "name, never translated; or two names, the agent that started it first); "
         "{clock} how long it ran. Keep the ✓ and the 🤖",
    eg={"agent": "Explore", "clock": "0:42"})
MORE = Plural(
    "agents.more", one="+{n} more", other="+{n} more",
    note="After the agent rows shown under an answer: how many more agents there are "
         "that have no row")
PROMISE = Msg(
    "agents.promise", "Results from the agents will follow here.",
    note="Last line under an answer while background agents still work: their results "
         "will arrive in this chat")

#: What an agent is doing, in Claude Code's own English words and never
#: translated (operator, 2026-10-10: show "Verifying the mutation copy
#: matches the worktree", not a translated "running a command"): a tool that
#: names a file, a pattern, a query or a site reads "<phrase> <argument>";
#: a Bash, Agent or Task call shows its own description (Claude's words,
#: never the command), else the phrase alone.
_TOOL_PHRASES: dict[str, str] = {
    "Read": "Reading", "Write": "Writing", "Edit": "Editing", "MultiEdit": "Editing",
    "NotebookEdit": "Editing", "Grep": "Searching for", "Glob": "Searching for",
    "WebSearch": "Searching the web for", "WebFetch": "Fetching",
    "Bash": "Running a command", "Agent": "Starting an agent", "Task": "Starting an agent",
}
#: Tools whose argument is the call's own description, shown alone.
_DESCRIBED = frozenset({"Bash", "Agent", "Task"})


@dataclass(frozen=True)
class AgentRow:
    """One agent of the block. ``seconds`` is the run time shown once
    ``done``; ``waiting`` marks an agent paused for agents of its own (a
    done one is done). ``parent`` is the index, in its view's
    ``agents``, of the agent that started it, or -1 for one at the top;
    an index out of range, its own, or one that closes a loop counts as
    -1."""

    agent_type: str
    done: bool
    seconds: float | None = None
    prose: str = ""
    tool: str = ""
    tool_arg: str = ""
    description: str = ""
    waiting: bool = False
    parent: int = -1


@dataclass(frozen=True)
class BlockView:
    """Everything the block shows, at one moment. ``agents`` in the order
    they started."""

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


def _tool_phrase(tool: str, arg: str) -> str | None:
    """The agent's tool call as Claude Code would say it, in English."""
    if not tool:
        return None
    arg = reveal(arg or "")
    if tool in _DESCRIBED:
        text = first_line(arg) or _TOOL_PHRASES[tool]
        return _bs(text)
    phrase = _TOOL_PHRASES.get(tool)
    arg = _cut(arg, ARG_MAX)
    if phrase is not None and arg:
        return _bs(f"{phrase} {arg}")
    return _bs(f"Using {_label(tool)}")


def _activity(row: AgentRow, tr):
    """What the row says the agent is doing: its prose (a plain string,
    escaped and isolated by ``tr``), its tool's phrase, its Agent call's
    description, or ``None``."""
    prose = first_line(row.prose)
    if prose:
        return _bs(prose)
    phrase = _tool_phrase(row.tool, row.tool_arg)
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


def _row(row: AgentRow, tr, hint: str | None = None) -> str:
    """*row* in *tr*'s language; *hint*, the type of the agent that
    started it, goes before its own name ("ship-tester › Explore")."""
    agent = _bs(_label(row.agent_type))
    if hint is not None:
        agent = f"{_bs(_label(hint))}{LINEAGE_SEP}{agent}"
    if row.done:
        return tr.md(ROW_DONE, agent=agent, clock=Safe(tr.clock(row.seconds or 0)))
    if row.waiting:
        # Never its last tool: that ended with its turn.
        return tr.md(ROW_WAITING, agent=agent)
    activity = _activity(row, tr)
    if activity is None:
        return tr.md(ROW_WORKING_BARE, agent=agent)
    return tr.md(ROW_WORKING, agent=agent, activity=activity)


# ---- the tree ---------------------------------------------------------------------

def _parents(agents: tuple[AgentRow, ...]) -> list[int]:
    """Each row's parent index, -1 for a top: one out of range is -1, and
    so is every row on a loop, its own parent included (no row of a loop
    could be placed under another of it)."""
    n = len(agents)
    parent = [a.parent if 0 <= a.parent < n else -1 for a in agents]
    looped = set()
    for i in range(n):
        seen = {i}
        current = parent[i]
        while current != -1 and current not in seen:
            seen.add(current)
            current = parent[current]
        if current == i:
            looped.add(i)
    for i in looped:
        parent[i] = -1
    return parent


def _tree_order(parent: list[int]) -> list[int]:
    """The rows in tree order: each top in start order, then every row
    under it depth first, each right after the row that started it,
    siblings in start order."""
    children: dict[int, list[int]] = {}
    for i, p in enumerate(parent):
        children.setdefault(p, []).append(i)
    order: list[int] = []
    stack = list(reversed(children.get(-1, [])))
    while stack:
        i = stack.pop()
        order.append(i)
        stack.extend(reversed(children.get(i, [])))
    return order


def _shown(agents: tuple[AgentRow, ...], parent: list[int],
           order: list[int]) -> set[int]:
    """The rows that get a place, at most :data:`ROWS_MAX`: the agents not
    done first, then the done ones, each in tree order, a row taken only
    with every row above it on its chain (a row never shows without the
    agent that started it), and only when all of them still fit."""
    ranked = ([i for i in order if not agents[i].done]
              + [i for i in order if agents[i].done])
    taken: set[int] = set()
    for i in ranked:
        chain = []
        current = i
        while current != -1 and current not in taken:
            chain.append(current)
            current = parent[current]
        if chain and len(taken) + len(chain) <= ROWS_MAX:
            taken.update(chain)
    return taken


def _agent_lines(view: BlockView, tr) -> list[str]:
    """The agent rows as the block's lines: each top agent shown, then (as
    one list) the agents under it that are shown, in tree order."""
    parent = _parents(view.agents)
    order = _tree_order(parent)
    taken = _shown(view.agents, parent, order)
    lines: list[str] = []
    items: list[str] = []
    for i in order:
        if i not in taken:
            continue
        p = parent[i]
        if p == -1:
            if items:
                lines.append("\n".join(items))
                items = []
            lines.append(str(_row(view.agents[i], tr)))
            continue
        hint = view.agents[p].agent_type if parent[p] != -1 else None
        items.append(LIST_ITEM + str(_row(view.agents[i], tr, hint)))
    if items:
        lines.append("\n".join(items))
    hidden = len(view.agents) - len(taken)
    if hidden:
        lines.append(str(tr.n(MORE, hidden, kind="md")))
    return lines


def render_block(view: BlockView, tr=None) -> str:
    """The block as Telegram rich Markdown, its lines separated by a blank
    line (a list's items by one line break), in *tr*'s language (English
    when None); ``""`` when it has no agent and no shell. The block alone:
    the divider above it is the caller's (:data:`~aipager.bot.divider.
    DIVIDER_SEP`). Drawn under an answer as small text, each line a
    footer block (:func:`~aipager.bot.footer.footer_block`, the caller's);
    :func:`reserve_bytes` measures it that way."""
    tr = _tr(tr)
    header = _header(view, tr)
    if not header:
        return ""
    lines = [header]
    if any(not a.done for a in view.agents):
        # Rows only while an agent works: once they are all done the
        # header says so, with the clock, in one line.
        lines += _agent_lines(view, tr)
        lines.append(tr.md(PROMISE))
    return LINE_SEP.join(str(line) for line in lines)


# ---- the room the block may ever need ---------------------------------------------

#: Counts the worst case tries: every plural category with up to three
#: digits (the agents and shells an answer lists are capped well below).
_WORST_COUNTS = tuple(range(0, 121)) + (999,)
_WORST_CLOCK = 999 * 3600 + 59 * 60 + 59
_WIDE = "\U0001f600"  # one character, four UTF-8 bytes
_AMP = "&"  # one character, five bytes once HTML-escaped
_RESERVE: dict[tuple[str, bool], int] = {}


def _size(text) -> int:
    return len(str(text).encode("utf-8"))


def _worst_rows(tr) -> int:
    """The largest row as drawn (a footer block). Tried with the widest
    character in UTF-8 and with the one HTML escapes widest ("&" →
    "&amp;"), since the footer's text is HTML."""
    best = 0
    for wide in (_WIDE, _AMP):
        agent = wide * LABEL_MAX
        rows = [AgentRow(agent, True, _WORST_CLOCK), AgentRow(agent, False),
                AgentRow(agent, False, waiting=True),
                AgentRow(agent, False, prose=wide * (ACTIVITY_MAX + 1)),
                AgentRow(agent, False, tool=wide * LABEL_MAX)]
        rows += [AgentRow(agent, False, tool=tool, tool_arg=wide * (ARG_MAX + 1))
                 for tool in _TOOL_PHRASES]
        best = max(best, max(_size(footer(str(_row(row, tr, hint))))
                             for row in rows for hint in (None, agent)))
    return best


def _worst_header(tr) -> int:
    """An upper bound of the header: its largest agent part, then its
    largest shell part (running, or finished and stopped joined), each
    joined on."""
    clock = Safe(tr.clock(_WORST_CLOCK))

    def most(plural, **params) -> int:
        return max(_size(md_to_html(str(tr.n(plural, n, kind="md", **params))))
                   for n in _WORST_COUNTS)

    join = _size(md_to_html(str(tr.md(HEAD_JOIN, first=Safe(""), second=Safe("")))))
    agents = max(most(HEAD_WORKING), most(HEAD_DONE, clock=clock))
    shells = max(most(HEAD_SHELLS_RUNNING),
                 most(HEAD_SHELLS_FINISHED, clock=clock) + join
                 + most(HEAD_SHELLS_STOPPED, clock=clock))
    # Drawn as one footer block.
    return _size(OPEN + CLOSE) + agents + join + shells


def reserve_bytes(tr=None) -> int:
    """UTF-8 bytes of the divider (:data:`~aipager.bot.divider.DIVIDER_SEP`)
    plus the largest block *tr*'s language can render: what an answer
    leaves free for its block, so no later edit of the block (agents
    joining it included) can push the message past the ceiling."""
    tr = _tr(tr)
    # Per language AND direction: a block drawn left to right in a
    # right-to-left language (multilingual design 5.7, rule 5) carries
    # other marks, so its own size.
    key = (tr.code, tr.rtl)
    cached = _RESERVE.get(key)
    if cached is not None:
        return cached
    sep = _size(LINE_SEP)
    # Before each row at most a blank line and a list marker ("\n\n- "):
    # a top row has the blank line, an item the line break or blank line
    # and the marker.
    row_lead = sep + _size(LIST_ITEM)
    more = max(_size(footer(str(tr.n(MORE, n, kind="md")))) for n in _WORST_COUNTS)
    total = (_size(DIVIDER_SEP) + _worst_header(tr)
             + ROWS_MAX * (row_lead + _worst_rows(tr))
             + sep + more + sep + _size(footer(str(tr.md(PROMISE)))))
    _RESERVE[key] = total
    return total
