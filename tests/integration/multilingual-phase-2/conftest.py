"""Fixtures for the black-box scenarios of multilingual phase 2 (helpers
in ``tests/i18n_ml2.py``)."""

from __future__ import annotations

import pytest

from tests import i18n_ml2


@pytest.fixture
def ml2_bot(mk_bot):
    return i18n_ml2.make_bot(mk_bot)
