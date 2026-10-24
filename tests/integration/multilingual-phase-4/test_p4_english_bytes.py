"""English is byte-identical (design.md success criteria "English, byte for
byte"): every route scenario of ``_scenarios.py``, run by an English viewer in
an English group, answers and posts exactly what the base branch did.

``base_english.json`` was recorded by running this same file on a
``git archive`` of the base branch (``feature/multilingual``) with
``P4_RECORD=<dir>``; nothing of the branch under test went into it.

``three_modes_english.json`` holds the scenarios whose answers the three
permission modes (2026-10-11) changed on purpose, and only those: Auto is
the default, the admin gate moved to Full access, the detail payload names
the mode, the perms route takes a target. Every other scenario must still
answer exactly as the base did.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.i18n_surfaces import without_mode_labels


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
THREE_MODES = json.loads(Path(__file__).with_name("three_modes_english.json")
                         .read_text(encoding="utf-8"))
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
    return {**json.loads(GOLDEN.read_text(encoding="utf-8")), **THREE_MODES}


@pytest.mark.parametrize("name", sorted(_scenarios.SCENARIOS))
def test_english_status_is_todays(env, golden, name):
    got = _run(env, name)
    if RECORD:
        Path(RECORD, f"{name}.json").write_text(json.dumps(got, ensure_ascii=False, indent=1))
        return
    assert got["status"] == golden[name]["status"]


#: The scenarios whose body /settings → 🎨 Appearance (2026-10-15) gave an
#: "appearance" entry (the Mini App's Appearance group), and nothing else.
APPEARANCE_BODIES = {"get_preferences"}
#: The scenarios whose body Claude Code background jobs (2026-10-24) gave
#: "parked" and "parked_seconds_ago" (a session whose conversation moved
#: into a background job), and nothing else: none of them is parked.
BG_JOB_BODIES = {"get_detail_busy", "get_detail_idle", "get_detail_viewer"}


@pytest.mark.parametrize("name", sorted(_scenarios.SCENARIOS))
def test_english_body_is_todays(env, golden, name):
    if RECORD:
        return
    body = _run(env, name)["body"]
    if name in APPEARANCE_BODIES:
        body = dict(body)
        appearance = body.pop("appearance")
        assert [g["field"] for g in appearance["schema"]] == ["stop", "prompt", "app", "settings"]
    if name in BG_JOB_BODIES:
        body = dict(body)
        assert body.pop("parked") is False
        assert body.pop("parked_seconds_ago", None) is None
    assert body == golden[name]["body"]


@pytest.mark.parametrize("name", sorted(_scenarios.SCENARIOS))
def test_english_chat_lines_are_todays(env, golden, name):
    if RECORD:
        return
    # The Mini App's chat lines show the mode label, which names the mode
    # as a mode since 2026-10-10 (operator); nothing else may differ.
    got, want = _run(env, name)["mirrors"], golden[name]["mirrors"]
    if name in THREE_MODES:
        assert got == want
        return
    assert without_mode_labels(got) == without_mode_labels(want)
    assert (got != want) == any("🤖 Auto" in m or "💬 Ask" in m for m in want)


def test_the_three_modes_changes_are_only_these():
    """The scenarios recorded apart are exactly the ones three modes
    changed; a new difference anywhere else fails above, against the base."""
    assert sorted(THREE_MODES) == [
        "create_full_needs_admin", "create_ok", "create_ok_full", "get_detail_busy",
        "get_detail_idle", "get_detail_viewer", "get_session_options",
        "perms_full_needs_admin", "perms_to_ask", "perms_to_full", "resume_full_by_member"]
