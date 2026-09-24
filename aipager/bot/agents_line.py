"""The "background agents still running" line (roadmap 8.41).

A turn's answer can go out while agents it launched in the background are
still working (Claude Code's Stop fires while they run). The answer then
ends with one line saying so, and once every agent it named has finished
that line is edited once to say they are done. Pure text here; the send
and the edit live in ``notify``.
"""

from __future__ import annotations

import re

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


def _agents(n: int) -> str:
    return f"{n} agent" if n == 1 else f"{n} agents"


def running_line(labels: list[str], *, markdown: bool = False) -> str:
    """``⏳ 2 agents still running — a, b · results will follow here``."""
    names = [_label(x) for x in labels[:LABELS_SHOWN]]
    if markdown:
        names = [_md_escape(x) for x in names]
    shown = ", ".join(names)
    if len(labels) > LABELS_SHOWN:
        shown += f" +{len(labels) - LABELS_SHOWN} more"
    return (f"⏳ {_agents(len(labels))} still running — {shown} · "
            "results will follow here")


def duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    return f"{minutes // 60}h {minutes % 60}m"


def done_line(labels: list[str], seconds: float, *,
              markdown: bool = False) -> str:
    """``✅ pipeline-runner — done (6m)``, or ``✅ 2 agents done (6m)``."""
    if len(labels) == 1:
        name = _label(labels[0])
        if markdown:
            name = _md_escape(name)
        return f"✅ {name} — done ({duration(seconds)})"
    return f"✅ {_agents(len(labels))} done ({duration(seconds)})"
