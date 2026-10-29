"""What an idle session waits for, in words (roadmap 8.121).

Claude's own turn ended while the background agents or shells its job
started still run (``TrackedSession.shown_state()`` is ``SHOWN_TASKS``).
The pinned bar, /status, the /x reply and the busy card all name it with
:func:`phrase`, from the same counts (``TrackedSession.job_counts()``), so
no two of them can disagree. The Mini App has its own copy of these
sentences in ``aipager/miniapp/ui_text.py`` (the page renders them).
"""
from __future__ import annotations

from aipager.bot.agents_line import COUNT_AGENTS, COUNT_SHELLS
from aipager.i18n import Msg, Plural, Safe

WAIT_AGENTS = Plural(
    "jobwait.agents", one="waiting for {n} agent", other="waiting for {n} agents",
    note="A session's state (lowercase, after a glyph or in parentheses): Claude's own "
         "turn ended, but the background agents it started still run; how many")
WAIT_SHELLS = Plural(
    "jobwait.shells", one="waiting for {n} shell", other="waiting for {n} shells",
    note="A session's state (lowercase, after a glyph or in parentheses): Claude's own "
         "turn ended, but the background shell commands it started still run; how many")
WAIT_BOTH = Msg(
    "jobwait.both", "waiting for {agents} and {shells}",
    note="A session's state (lowercase, after a glyph or in parentheses): Claude's own "
         "turn ended, but the background agents and shell commands it started still run",
    eg={"agents": "2 agents", "shells": "1 shell"})
WAIT_FINISHING = Msg(
    "jobwait.finishing", "finishing up",
    note="A session's state (lowercase, after a glyph or in parentheses): its background "
         "agents or shells just ended and Claude is about to continue with the results")


def phrase(tr, agents: int, shells: int, kind: str = "html") -> str:
    """``waiting for 2 agents``, ``waiting for 1 shell``, ``waiting for 2
    agents and 1 shell``, or ``finishing up`` while the job is open with
    nothing left running. Chosen from the counts, never from text."""
    if agents and shells:
        return getattr(tr, kind)(
            WAIT_BOTH,
            agents=Safe(tr.n(COUNT_AGENTS, agents, kind=kind)),
            shells=Safe(tr.n(COUNT_SHELLS, shells, kind=kind)))
    if agents:
        return tr.n(WAIT_AGENTS, agents, kind=kind)
    if shells:
        return tr.n(WAIT_SHELLS, shells, kind=kind)
    return getattr(tr, kind)(WAIT_FINISHING)
