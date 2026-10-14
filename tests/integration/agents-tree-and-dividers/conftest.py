"""Fixtures for the black-box tests of the agents tree under answers, the
robot rows and the real dividers (agents-tree-and-dividers).

Only the surface entrypoints.md names is driven: ``agents_line``
(``render_block``, ``reserve_bytes``, ``AgentRow``, ``BlockView``,
``ROWS_MAX``, ``BLOCKS_MAX``), ``aipager.bot.divider``, ``agent_tree``
(``LINEAGE_SEP``, ``nearest_listed``, ``top_ancestor``), the
``TrackedSession`` methods and fields it lists, hook datagrams through
``HookReceiver._on_datagram`` and the bot's notify events. Every Telegram
call lands in one recorded list (``tests/answer_status_bb.py``); nothing
reaches the network.

The virtual-loop rig is ``tests/agents_line_waiting_bb.Rig`` with one
change: every hook payload carries a unique ``_seq`` as well as a fresh
``prompt_id``, so the receiver's duplicate-fingerprint drop never eats a
repeated event.

This directory's name is not a Python identifier, so the shared helpers
are reached through the ``tree`` fixture rather than an import.
"""

from __future__ import annotations

import itertools
import json
import re

import pytest

from tests import agents_line_waiting_bb as h
from tests import answer_status_bb as bb
from tests.agents_line_waiting_bb import (  # noqa: F401  (fixtures)
    dtach_side,
    vloop,
)

RLM, LRM = "\u200f", "\u200e"
ROBOT = "\U0001f916"
_SEQ = itertools.count(1)


class TreeRig(h.Rig):
    """The agents-line-waiting rig; each datagram gets a unique ``_seq``."""

    def hook(self, event: str, **fields) -> None:
        self.n += 1
        payload = {"session": self.sess.name, "hook_event_name": event,
                   "transcript_path": self.transcript,
                   "cwd": "/home/alice/frontend",
                   "prompt_id": f"pid-{self.n}", "_seq": next(_SEQ)}
        payload.update(fields)
        self.events.append(event)
        self.run(self.recv._on_datagram(json.dumps(payload).encode()))
        self.drain()

    def call(self, child_type: str, description: str, *, by: str = "",
             tool_name: str = "Agent") -> dict:
        """The launcher's Agent PreToolUse alone (no start, no link)."""
        own = {"agent_id": by, "agent_type": "x"} if by else {}
        tool_input = {"subagent_type": child_type, "description": description,
                      "prompt": "go", "run_in_background": True}
        self.hook("PreToolUse", tool_name=tool_name, tool_input=tool_input, **own)
        return tool_input

    def launch(self, child: str, child_type: str, description: str, *,
               by: str = "") -> None:
        """PreToolUse by *by*, then the child's SubagentStart; the
        PostToolUse link is NOT sent."""
        self.call(child_type, description, by=by)
        self.start(child, child_type)


@pytest.fixture
def wire(monkeypatch):
    return bb.make_wire(monkeypatch)


@pytest.fixture
def rig(vloop, wire, mk_bot, dtach_side, tmp_path):  # noqa: F811
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_bytes(b"")
    bot = bb.make_wired_bot(mk_bot, wire)
    return TreeRig(vloop, bot, wire, str(transcript))


@pytest.fixture
def bot(mk_bot, wire):
    return bb.make_wired_bot(mk_bot, wire)


@pytest.fixture
def sess(bot):
    return bb.make_sess(bot)


# ---- pure helpers, shared through a fixture -----------------------------------

class Shapes:
    """Structure readers for a rendered block (no renderer internals)."""

    RLM = RLM
    LRM = LRM
    ROBOT = ROBOT
    MARK_PAIR = re.compile("[\u200e\u200f]{2}")

    @staticmethod
    def paragraphs(block: str) -> list[str]:
        return block.split("\n\n")

    @staticmethod
    def agent_lines(block: str) -> list[str]:
        """Every line holding an agent row, in order, list marker kept."""
        return [x for x in block.split("\n") if ROBOT in x]

    @staticmethod
    def is_item(line: str) -> bool:
        return line.startswith("- ")

    @staticmethod
    def name_of(line: str) -> str:
        """The bold name of a row ("ship-developer \u203a ship-reviewer")."""
        m = re.search(r"\*\*(.+?)\*\*", line)
        assert m, line
        return m.group(1).replace("\u2068", "").replace("\u2069", "")

    @staticmethod
    def tree(block: str) -> list[tuple[int, str]]:
        """(level, name) per agent row: 1 for a paragraph, 2 for an item."""
        return [(2 if Shapes.is_item(x) else 1, Shapes.name_of(x))
                for x in Shapes.agent_lines(block)]

    @staticmethod
    def conservative_blocks(tail: str) -> int:
        """entrypoints.md "Budgets": each paragraph 1, the divider 1, each
        list 1, each list item 2."""
        n = 0
        for para in tail.split("\n\n"):
            if not para:
                continue
            lines = para.split("\n")
            if all(x.startswith("- ") for x in lines):
                n += 1 + 2 * len(lines)
            else:
                n += 1
        return n


@pytest.fixture
def shapes():
    return Shapes
