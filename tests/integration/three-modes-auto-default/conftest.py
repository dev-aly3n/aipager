"""Fixtures for the three-modes black-box suite. See _tm3.py."""

from __future__ import annotations

import pytest

from _tm3 import Pty


@pytest.fixture
def pty(monkeypatch):
    """Every dtach write, gap, kill and launch, recorded; nothing real runs."""
    return Pty(monkeypatch)
