"""Fixtures for the black-box rm-countdown scenarios (roadmap 8.128).

Written from design.md's success criteria and entrypoints.md only: the
prompt rule through ``prompt_stack`` and the shared two-prompts rig (real
hook receiver, notify path and tap handler)."""

from __future__ import annotations

import pytest

from tests.two_prompts_rig import Rig


@pytest.fixture
def rig(mk_bot, monkeypatch, run_async):
    """A session as a hook creates it; aipager leaves the countdown on."""
    return Rig(mk_bot, monkeypatch, run_async)
