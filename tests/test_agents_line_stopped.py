"""Roadmap 8.121 F5: an answer's agents block says the agents a person's
Stop ended were stopped, never done: the header reads "⏹ 2 agents stopped
(1:10)", a row "✗ 🤖 Explore · stopped (1:10)", and a block with both
finished and stopped agents names each part. ``_agents_view`` maps an end
recorded with outcome "stopped" to such a row."""

from __future__ import annotations

import time
from types import SimpleNamespace

from aipager import i18n
from aipager.bot import agents_line
from aipager.bot.agents_line import AgentRow, BlockView
from aipager.bot.notify import NotifyMixin
from aipager.state import BG_AGENTS_SETTLE_SECONDS


def test_two_stopped_agents_read_stopped():
    view = BlockView(agents=(AgentRow("Explore", True, 70.0, stopped=True),
                             AgentRow("Plan", True, 70.0, stopped=True)),
                     agents_done_seconds=70.0, agents_stopped_seconds=70.0)
    out = agents_line.render_block(view)
    assert out == "⏹ 2 agents stopped (1:10)"
    assert "done" not in out


def test_one_done_and_one_stopped_name_both():
    view = BlockView(agents=(AgentRow("Explore", True, 30.0),
                             AgentRow("Plan", True, 70.0, stopped=True)),
                     agents_done_seconds=30.0, agents_stopped_seconds=70.0)
    assert agents_line.render_block(view) == "✅ 1 agent done (0:30) · ⏹ 1 agent stopped (1:10)"


def test_a_stopped_row_beside_a_working_agent():
    view = BlockView(agents=(AgentRow("Explore", True, 70.0, stopped=True),
                             AgentRow("Plan", False)))
    out = agents_line.render_block(view)
    assert "✗ 🤖 **Explore** · stopped (1:10)" in out
    assert "⏳ 1 agent working" in out


def test_done_rows_still_read_done():
    view = BlockView(agents=(AgentRow("Explore", True, 42.0), AgentRow("Plan", False)))
    assert "✓ 🤖 **Explore** · done (0:42)" in agents_line.render_block(view)


def test_the_room_reserved_covers_the_stopped_header():
    agents_line._RESERVE.clear()
    tr = i18n.get("en")
    worst = BlockView(agents=tuple(AgentRow("x" * 40, True, 999 * 3600.0, stopped=i % 2 == 0)
                                   for i in range(120)),
                      agents_done_seconds=999 * 3600.0, agents_stopped_seconds=999 * 3600.0)
    assert len(agents_line.render_block(worst, tr).encode()) < agents_line.reserve_bytes(tr)


def _view_of(ends: dict, now: float):
    sess = SimpleNamespace(bg_agents={})
    rec = {"sent_at": now - 100.0, "ends": ends,
           "items": [{"id": "a1", "kind": "agent", "label": "Explore", "started": now - 100.0},
                     {"id": "a2", "kind": "agent", "label": "Plan", "started": now - 100.0}]}
    return NotifyMixin._agents_view(SimpleNamespace(), sess, rec, now)


def test_the_view_maps_a_stopped_end_to_a_stopped_row():
    now = time.monotonic()
    at = now - BG_AGENTS_SETTLE_SECONDS - 1.0
    view = _view_of({"a1": {"outcome": "stopped", "at": at},
                     "a2": {"outcome": "stopped", "at": at}}, now)
    assert [r.stopped for r in view.agents] == [True, True]
    assert agents_line.render_block(view).startswith("⏹ 2 agents stopped")


def test_the_view_keeps_a_done_end_done():
    now = time.monotonic()
    at = now - BG_AGENTS_SETTLE_SECONDS - 1.0
    view = _view_of({"a1": {"outcome": "done", "at": at},
                     "a2": {"outcome": "done", "at": at}}, now)
    assert [r.stopped for r in view.agents] == [False, False]
    assert agents_line.render_block(view).startswith("✅ 2 agents done")
