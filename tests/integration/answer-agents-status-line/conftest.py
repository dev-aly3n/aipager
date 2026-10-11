"""Fixtures for the black-box tests of the answer's live status block
(helpers in ``tests/answer_status_bb.py``)."""

from __future__ import annotations

import pytest

from tests import answer_status_bb as bb


@pytest.fixture
def wire(monkeypatch):
    return bb.make_wire(monkeypatch)


@pytest.fixture
def bot(mk_bot, wire):
    return bb.make_wired_bot(mk_bot, wire)


@pytest.fixture
def sess(bot):
    return bb.make_sess(bot)
