"""Fixtures for the two-open-prompts scenarios (roadmap 8.120). The
plumbing lives in ``tests/two_prompts_rig.py`` (a plain module, shared
with the unit tests): the real hook receiver, notify path and tap handler,
with keys, hook decisions and sends recorded."""

from __future__ import annotations

import pytest

from tests.two_prompts_rig import Rig


@pytest.fixture
def rig(mk_bot, monkeypatch, run_async):
    return Rig(mk_bot, monkeypatch, run_async)
