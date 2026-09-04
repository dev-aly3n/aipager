"""Black-box coverage of entrypoints.md's documented ``via`` field on
``aipager.audit.append`` -- the primary observable marker of which path
(hook decision vs keystroke fallback) answered a permission tap.
"""

from __future__ import annotations

import json

from aipager import audit


def test_via_hook_decision_round_trips(tmp_path):
    log = tmp_path / "audit.jsonl"
    ok = audit.append(
        session="claude-jim", label="jim", action="Allowed",
        tool="Bash", summary="ls -la",
        path=log, via="hook_decision",
    )
    assert ok is True
    record = json.loads(log.read_text().splitlines()[-1])
    assert record["via"] == "hook_decision"


def test_via_keystroke_fallback_round_trips(tmp_path):
    log = tmp_path / "audit.jsonl"
    audit.append(
        session="claude-jim", label="jim", action="Denied",
        tool="Bash", summary="rm -rf /",
        path=log, via="keystroke_fallback",
    )
    record = json.loads(log.read_text().splitlines()[-1])
    assert record["via"] == "keystroke_fallback"


def test_via_omitted_defaults_to_empty_string(tmp_path):
    """Existing call sites that never pass via= must be unaffected."""
    log = tmp_path / "audit.jsonl"
    audit.append(
        session="claude-jim", label="jim", action="Allowed",
        tool="Bash", summary="ls -la",
        path=log,
    )
    record = json.loads(log.read_text().splitlines()[-1])
    assert record.get("via", "") == ""
