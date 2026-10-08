"""Problem report markers read without side effects (roadmap 8.112 step 3).

The automatic offer runs every minute and needs the install marker's
first start: reading it must never write it (``markers.first_start``
does, on purpose, at daemon start). ``set_started_at`` is this daemon's
start in memory, reset around every test by the root conftest.
"""

from __future__ import annotations

import json
from pathlib import Path

from aipager import config
from aipager.report import markers

NOW = 1_800_000_000


def test_read_does_not_create():
    path = Path(config.REPORT_INSTALL_FILE)
    assert not path.exists()
    assert markers.read_first_start(NOW) is None
    assert not path.exists()
    assert not path.parent.exists() or not any(path.parent.iterdir())


def test_read_returns_a_valid_marker():
    assert markers.first_start(NOW - 5000) == NOW - 5000   # writes it
    assert markers.read_first_start(NOW) == NOW - 5000


def test_read_refuses_a_bad_marker_without_rewriting_it():
    path = Path(config.REPORT_INSTALL_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    for doc in ({"first_start": "x"}, {"first_start": NOW + 10 * 86400}, [], {"other": 1}):
        path.write_text(json.dumps(doc), encoding="utf-8")
        assert markers.read_first_start(NOW) is None
        assert json.loads(path.read_text(encoding="utf-8")) == doc


def test_set_started_at_round_trip():
    assert markers.started_at() is None
    markers.set_started_at(NOW - 7)
    assert markers.started_at() == NOW - 7
    markers.set_started_at(None)
    assert markers.started_at() is None


def test_write_running_sets_started_at():
    markers.write_running(service=False, now=NOW)
    assert markers.started_at() == NOW
