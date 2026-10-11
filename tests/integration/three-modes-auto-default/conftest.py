"""Fixtures for the three-modes black-box suite. See _tm3.py."""

from __future__ import annotations

import pytest

import _tm3
from _tm3 import Pty


@pytest.fixture(autouse=True)
def group_root(tmp_path, monkeypatch):
    """The group's members' root folder, the folder its sessions work in:
    a temporary one, so a member's /new may create it."""
    root = tmp_path / "group-root"
    root.mkdir()
    monkeypatch.setattr(_tm3, "GROUP_ROOT", str(root))
    return str(root)


@pytest.fixture
def pty(monkeypatch):
    """Every dtach write, gap, kill and launch, recorded; nothing real runs."""
    return Pty(monkeypatch)
