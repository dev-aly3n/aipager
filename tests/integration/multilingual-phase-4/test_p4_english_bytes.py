"""English is byte-identical (design.md success criteria "English, byte for
byte"): every route scenario of ``_scenarios.py``, run by an English viewer in
an English group, answers and posts exactly what the base branch did.

``base_english.json`` was recorded by running this same file on a
``git archive`` of the base branch (``feature/multilingual``) with
``P4_RECORD=<dir>``; nothing of the branch under test went into it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


def _load_scenarios():
    """The shared scenario module, loaded by path (the directory name has
    hyphens, so it is not importable as a package)."""
    import importlib.util
    import sys
    name = "p4_scenarios"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, Path(__file__).with_name("_scenarios.py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


_scenarios = _load_scenarios()

GOLDEN = Path(__file__).with_name("base_english.json")
RECORD = os.environ.get("P4_RECORD")


def _run(env, name):
    _kind, actor, runner = _scenarios.SCENARIOS[name]
    out = runner(env, actor)
    project = str(env.project)
    return {
        "status": out["status"],
        "body": _scenarios.normalize(out["body"], project),
        "mirrors": [m.replace(project, "<proj>") for m in out["mirrors"]],
    }


@pytest.fixture(scope="module")
def golden():
    if RECORD:
        return {}
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", sorted(_scenarios.SCENARIOS))
def test_english_status_is_todays(env, golden, name):
    got = _run(env, name)
    if RECORD:
        Path(RECORD, f"{name}.json").write_text(json.dumps(got, ensure_ascii=False, indent=1))
        return
    assert got["status"] == golden[name]["status"]


@pytest.mark.parametrize("name", sorted(_scenarios.SCENARIOS))
def test_english_body_is_todays(env, golden, name):
    if RECORD:
        return
    assert _run(env, name)["body"] == golden[name]["body"]


@pytest.mark.parametrize("name", sorted(_scenarios.SCENARIOS))
def test_english_chat_lines_are_todays(env, golden, name):
    if RECORD:
        return
    assert _run(env, name)["mirrors"] == golden[name]["mirrors"]
