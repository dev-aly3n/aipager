"""The "background agents still running" line (roadmap 8.41).

A turn's answer can go out while agents it launched in the background are
still working (Claude Code's Stop fires while they run). The answer then
ends with one line saying so, and once every agent it named has finished
that line is edited once to say they are done. Pure text here; the send
and the edit live in ``notify``.
"""

from __future__ import annotations

import re

# One formatter for how long something ran: the settled shell rows use it
# too, from a module the session state can import (``Tr.clock`` is the same
# clock in the line's language).
from aipager.bg_shells import duration  # noqa: F401
from aipager.i18n import Msg, Plural, Safe, Span

#: A label longer than this is cut, with an ellipsis.
LABEL_MAX = 32
#: At most this many labels are named; the rest are counted.
LABELS_SHOWN = 3


def _md_escape(text: str) -> str:
    # The same set the card escapes (animation._md_escape): a label is an
    # agent type, and "general_purpose" must not turn italic.
    return re.sub(r"([*_`\[\]])", r"\\\1", text)


def _label(text: str) -> str:
    text = " ".join(str(text).split())
    if len(text) > LABEL_MAX:
        text = text[:LABEL_MAX - 1].rstrip() + "…"
    return text


COUNT_AGENTS = Plural("agents.count_agents", one="{n} agent", other="{n} agents",
                      note="How many background agents (in the line under an answer)")
COUNT_SHELLS = Plural("agents.count_shells", one="{n} shell", other="{n} shells",
                      note="How many background shell commands")
RUNNING = Msg("agents.running", "⏳ {count} still running ({names}) - results will follow here",
              note="Under an answer: background work still running; its results will "
                   "arrive in this chat. {count} is like 2 agents, 1 shell; {names} their names",
              eg={"count": "2 agents", "names": "Explore, Plan"})
NAMES_MORE = Plural("agents.names_more", one="{names} +{n} more", other="{names} +{n} more",
                    note="The first names, then how many more are not named",
                    eg={"names": "a, b, c"})
DONE_ONE = Msg("agents.done_one", "✅ {name} done ({clock})",
               note="The line under an answer once its one background agent finished",
               eg={"name": "Explore", "clock": "6:12"})
DONE_SHELL = Msg("agents.done_shell", "✅ shell: {name} - done ({clock})",
                 note="The line under an answer once its one background shell finished",
                 eg={"name": "sleep 90", "clock": "1:30"})
DONE_MANY = Msg("agents.done_many", "✅ {count} done ({clock})",
                note="The line under an answer once all its background work finished",
                eg={"count": "2 agents, 1 shell", "clock": "6:12"})


def _tr(tr):
    if tr is None:
        from aipager import i18n
        return i18n.get("en")
    return tr


def _count(labels: list[str], kinds: list[str] | None, tr, kind: str) -> str:
    """``2 agents``, ``1 shell`` or ``2 agents, 1 shell``: what the line
    counts. *kinds* runs parallel to *labels* (``"agent"`` / ``"shell"``);
    ``None`` means every label is an agent, the line as it always read."""
    if kinds is None:
        return tr.n(COUNT_AGENTS, len(labels), kind=kind)
    shells = sum(1 for k in kinds if k == "shell")
    agents = len(labels) - shells
    parts = []
    if agents:
        parts.append(tr.n(COUNT_AGENTS, agents, kind=kind))
    if shells:
        parts.append(tr.n(COUNT_SHELLS, shells, kind=kind))
    return tr.join(parts)


def running_line(labels: list[str], *, kinds: list[str] | None = None,
                 markdown: bool = False, tr=None) -> str:
    """``⏳ 2 agents still running (a, b) - results will follow here``, or
    with background shells ``⏳ 1 agent, 1 shell still running (a, b) -
    ...``. In *tr*'s language (English when None)."""
    tr = _tr(tr)
    kind = "md" if markdown else "text"
    names = [_label(x) for x in labels[:LABELS_SHOWN]]
    if markdown:
        names = [_md_escape(x) for x in names]
    # The names as one whole, as they are (never translated).
    shown = Span(tr.join(names))
    if len(labels) > LABELS_SHOWN:
        shown = Safe(tr.n(NAMES_MORE, len(labels) - LABELS_SHOWN, kind=kind, names=shown))
    render = getattr(tr, kind)
    return render(RUNNING, count=Safe(_count(labels, kinds, tr, kind)), names=shown)


def done_line(labels: list[str], seconds: float, *,
              kinds: list[str] | None = None,
              markdown: bool = False, tr=None) -> str:
    """``✅ pipeline-runner done (6:12)``, ``✅ shell: sleep 90 - done
    (1:30)`` for a single shell, or ``✅ 2 agents, 1 shell done (6:12)``."""
    tr = _tr(tr)
    kind = "md" if markdown else "text"
    render = getattr(tr, kind)
    clock = Safe(tr.clock(seconds))
    if len(labels) == 1:
        name = _label(labels[0])
        if markdown:
            name = _md_escape(name)
        if kinds is not None and kinds[0] == "shell":
            return render(DONE_SHELL, name=Span(name), clock=clock)
        return render(DONE_ONE, name=Span(name), clock=clock)
    return render(DONE_MANY, count=Safe(_count(labels, kinds, tr, kind)), clock=clock)
