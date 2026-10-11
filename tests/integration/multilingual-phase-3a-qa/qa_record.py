"""Recorder copied (with qa_scenarios.py beside it) into an archive of a base
tree and run there by pytest, so the base's own conftest isolates it. Each
scenario's English output and the exported constants are written as JSON
into ``$QA_P3A_OUT``. Used only when re-recording the golden file
(``qa_golden.py``); not collected here: its name does not start with
``test_``."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "qa_p3a_scenarios_base", Path(__file__).with_name("qa_scenarios.py"))
S = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(S)

OUT = Path(os.environ.get("QA_P3A_OUT", "/nonexistent"))


@pytest.mark.parametrize("name", sorted(S.SCENARIOS))
def test_record(name, mk_bot, run_async, monkeypatch, tmp_path):
    out = S.output(name, "en", lambda: S.H(mk_bot, run_async, monkeypatch, tmp_path))
    (OUT / f"{name}.json").write_text(json.dumps(out, ensure_ascii=True))


def test_record_constants():
    out = {k: str(v) for k, v in S.constants().items()}
    (OUT / "_constants.json").write_text(json.dumps(out, ensure_ascii=True))
