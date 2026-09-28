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
# too, from a module the session state can import.
from aipager.bg_shells import duration

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


def _count(labels: list[str], kinds: list[str] | None) -> str:
    """``2 agents``, ``1 shell`` or ``2 agents, 1 shell``: what the line
    counts. *kinds* runs parallel to *labels* (``"agent"`` / ``"shell"``);
    ``None`` means every label is an agent, the line as it always read."""
    if kinds is None:
        return _agents(len(labels))
    shells = sum(1 for k in kinds if k == "shell")
    agents = len(labels) - shells
    parts = []
    if agents:
        parts.append(_agents(agents))
    if shells:
        parts.append(f"{shells} shell" if shells == 1 else f"{shells} shells")
    return ", ".join(parts)


def running_line(labels: list[str], *, kinds: list[str] | None = None,
                 markdown: bool = False) -> str:
    """``⏳ 2 agents still running (a, b) - results will follow here``, or
    with background shells ``⏳ 1 agent, 1 shell still running (a, b) -
    ...``."""
    names = [_label(x) for x in labels[:LABELS_SHOWN]]
    if markdown:
        names = [_md_escape(x) for x in names]
    shown = ", ".join(names)
    if len(labels) > LABELS_SHOWN:
        shown += f" +{len(labels) - LABELS_SHOWN} more"
    return (f"⏳ {_count(labels, kinds)} still running ({shown}) - "
            "results will follow here")


def done_line(labels: list[str], seconds: float, *,
              kinds: list[str] | None = None,
              markdown: bool = False) -> str:
    """``✅ pipeline-runner done (6m)``, ``✅ shell: sleep 90 - done (2m)``
    for a single shell, or ``✅ 2 agents, 1 shell done (6m)``."""
    if len(labels) == 1:
        name = _label(labels[0])
        if markdown:
            name = _md_escape(name)
        if kinds is not None and kinds[0] == "shell":
            return f"✅ shell: {name} - done ({duration(seconds)})"
        return f"✅ {name} done ({duration(seconds)})"
    return f"✅ {_count(labels, kinds)} done ({duration(seconds)})"
