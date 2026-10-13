"""Fixtures for the black-box tests of a background agent that waits for
its own agent, and of running agents across a daemon restart. The
plumbing lives in ``tests/agents_line_waiting_bb.py`` (a plain module, so
the test files can import its helpers and ids)."""

from __future__ import annotations

from tests.agents_line_waiting_bb import (  # noqa: F401  (fixtures)
    dtach_side,
    rig,
    vloop,
    wire,
)
