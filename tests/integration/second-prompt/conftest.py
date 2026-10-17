"""Fixtures for the black-box second-prompt scenarios (roadmap 8.120).

The plumbing is the shared rig in ``tests/two_prompts_rig.py``: the real
hook receiver, notify path and tap handler, with keystrokes, hook
decisions and Telegram sends recorded. These tests read only what the
entry points promise: the chat's messages and buttons, the pop-ups, the
keys and hook decisions, ``open_prompts()``, ``dialog_is_open()``, the
session status and the state file."""

from __future__ import annotations

import pytest

from tests.two_prompts_rig import Rig


@pytest.fixture
def rig(mk_bot, monkeypatch, run_async):
    return Rig(mk_bot, monkeypatch, run_async)
