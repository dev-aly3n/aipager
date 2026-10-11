"""Fixtures for the phase 3a tester's black-box scenarios.

``golden`` is what the base tree showed in English (scenario outputs,
exported constants, the literal baseline and safety.py's imports), read
from the committed ``golden_base.json``. A normal run never calls git.
``AIPAGER_RECORD_QA_GOLDEN=1`` re-records the file from the base commit
first (``qa_golden.py``: a ``git archive`` copy run in a subprocess with a
throwaway HOME; the worktree is never checked out or changed otherwise).

``scenario_out(name, lang)`` runs a scenario once per session and returns
its output (``qa_scenarios.output``)."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent

_spec = importlib.util.spec_from_file_location("qa_p3a_scenarios", HERE / "qa_scenarios.py")
S = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(S)
#: The test modules here import the drivers by this name (the directory's
#: hyphen makes it no package path).
sys.modules.setdefault("qa_p3a_scenarios", S)
S = sys.modules["qa_p3a_scenarios"]

GOLDEN = HERE / "golden_base.json"


@pytest.fixture(scope="session")
def golden(tmp_path_factory):
    if os.environ.get("AIPAGER_RECORD_QA_GOLDEN") == "1":
        _gspec = importlib.util.spec_from_file_location("qa_p3a_golden", HERE / "qa_golden.py")
        recorder = importlib.util.module_from_spec(_gspec)
        _gspec.loader.exec_module(recorder)
        recorder.write(recorder.record(S, tmp_path_factory.mktemp("qa-golden")))
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


@pytest.fixture
def scenario_out(mk_bot, run_async, monkeypatch, tmp_path):
    made = iter(range(1000))

    def factory():
        sub = tmp_path / f"h{next(made)}"
        sub.mkdir()
        return S.H(mk_bot, run_async, monkeypatch, sub)

    def run(name, lang):
        return S.output(name, lang, factory)
    return run


@pytest.fixture
def h(mk_bot, run_async, monkeypatch, tmp_path):
    return S.H(mk_bot, run_async, monkeypatch, tmp_path)


@pytest.fixture
def scen():
    return S
