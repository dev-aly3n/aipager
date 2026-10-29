"""What an idle session waits for, in words (roadmap 8.121).

Claude's own turn ended while the background agents its job started
still run (``TrackedSession.shown_state()`` is ``SHOWN_TASKS``). The
pinned bar, /status, the /x reply and the busy card all name it with
:func:`phrase`, from the same count (``TrackedSession.job_agent_count()``),
so no two of them can disagree. The Mini App has its own copy of this
sentence in ``aipager/miniapp/ui_text.py`` (the page renders it).

Agents only: a background shell alone never puts a session in this
state (a dev server would keep it waiting through every later turn).
"""
from __future__ import annotations

from aipager.i18n import Plural

WAIT_AGENTS = Plural(
    "jobwait.agents", one="waiting for {n} agent", other="waiting for {n} agents",
    note="A session's state (lowercase, after a glyph or in parentheses): Claude's own "
         "turn ended, but the background agents it started still run; how many")


def phrase(tr, agents: int, kind: str = "html") -> str:
    """``waiting for 2 agents``: what a session in the ``SHOWN_TASKS``
    state waits for, from its job's agent count."""
    return tr.n(WAIT_AGENTS, agents, kind=kind)
