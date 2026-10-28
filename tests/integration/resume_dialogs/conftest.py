"""Plumbing for the black-box rows of roadmap 8.127 (a dialog in Claude
Code's terminal that no hook tells of). No assertions, no production logic.

Everything goes through the sanctioned seams (entrypoints.md): fake session
records under tmp_path via ``claude_jobs.config_dir``, every terminal write
captured through ``inject._run`` (exact bytes), clocks on ``term_dialog``'s
``_now_wall`` / ``_now_mono``. The world is ``tests/resume_dialog_kit.py``.
"""

from __future__ import annotations

import time

import pytest

from tests.resume_dialog_kit import World


@pytest.fixture
def world(mk_bot, monkeypatch, tmp_path, run_async):
    w = World(mk_bot, monkeypatch, tmp_path, run_async)
    # Both clocks start at the real ones, so aipager's own write stamps
    # (``inject.last_write_at``, real monotonic) line up with the seams.
    w.clock.wall = time.time()
    w.clock.mono = time.monotonic()
    return w
