"""The e2e harness knows which tools the Claude session offered (roadmap
8.98).

Claude Code 2.1.289 on the operator's box offers no Grep or Glob tool, so
two live e2e tests failed for a reason their names did not say (Claude
searched through Bash). The harness now runs ``claude -p`` with
``--output-format stream-json --verbose``, reads the ``init`` event's
tool list, and a test that needs a tool skips with the reason when the
session lacks it, and never when the list is unknown (it then runs its
assertions instead of passing vacuously). No real Claude runs here.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from tests.e2e import harness

# Shaped like Claude Code 2.1.291's own output (trimmed): the init event,
# an assistant event, the result event.
_INIT = {"type": "system", "subtype": "init", "cwd": "/x", "session_id": "s1",
         "tools": ["Task", "Bash", "Edit", "Read", "Write"],
         "claude_code_version": "2.1.291"}
_RESULT = {"type": "result", "subtype": "success", "session_id": "s1",
           "result": "done", "is_error": False,
           "permission_denials": [{"tool_name": "Bash", "tool_use_id": "t1",
                                   "tool_input": {"command": "grep -rn x ~"}}]}


def _stream(*events) -> str:
    return "".join(json.dumps(e) + "\n" for e in events)


def test_parse_stream_reads_init_tools_version_and_result():
    out = _stream(_INIT, {"type": "assistant", "message": {}}, _RESULT)
    result, tools, version = harness.parse_stream("not json\n" + out)
    assert result == _RESULT
    assert tools == ("Task", "Bash", "Edit", "Read", "Write")
    assert version == "2.1.291"


def test_parse_stream_without_init_leaves_tools_unknown():
    result, tools, version = harness.parse_stream(_stream(_RESULT))
    assert result == _RESULT and tools is None and version == ""


def test_parse_stream_without_result():
    assert harness.parse_stream(_stream(_INIT))[0] is None


def test_missing_tool_skips_with_the_reason():
    with pytest.raises(pytest.skip.Exception) as exc:
        harness.skip_unless_offered(("Bash", "Read"), "Grep", version="2.1.289")
    assert "Claude Code 2.1.289 offers no Grep tool" in str(exc.value)


def test_offered_tool_does_not_skip():
    try:
        harness.skip_unless_offered(("Bash", "Grep"), "Grep")
    except pytest.skip.Exception:
        pytest.fail("skipped although the tool is offered")


def test_unknown_tool_list_never_skips():
    """No init event seen: the test runs (and can fail), it does not pass
    by skipping on a guess."""
    assert harness.missing_tools(None, "Grep") == []
    try:
        harness.skip_unless_offered(None, "Grep", "Glob")
    except pytest.skip.Exception:
        pytest.fail("skipped on an unknown tool list")


def test_every_missing_tool_is_named():
    assert harness.missing_tools(("Read",), "Grep", "Read", "Glob") == ["Grep", "Glob"]


def test_run_reads_the_stream(monkeypatch, tmp_path):
    """``harness.run`` asks for the stream, and the run carries the tool
    list next to the result fields the tests read."""
    seen = {}

    def fake_run(argv, **kw):
        seen["argv"] = argv
        return subprocess.CompletedProcess(argv, 0, _stream(_INIT, _RESULT), "")

    monkeypatch.setattr(harness, "_spawn", fake_run)
    monkeypatch.setattr(harness, "PRODUCTION_SNAPSHOT_PATH",
                        lambda s: tmp_path / f"{s}.json")
    project = tmp_path / "proj"
    project.mkdir()
    r = harness.run("hi", session="claude-e2e-unit", project=project, home=tmp_path)
    argv = seen["argv"]
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert "--verbose" in argv
    assert r.offered_tools == ("Task", "Bash", "Edit", "Read", "Write")
    assert r.claude_version == "2.1.291"
    assert r.session_id == "s1" and r.result == "done" and r.denials == ["Bash"]
    with pytest.raises(pytest.skip.Exception):
        r.skip_unless_offered("Grep")
    try:
        r.skip_unless_offered("Bash")
    except pytest.skip.Exception:
        pytest.fail("skipped although Bash is offered")


def test_run_without_a_result_event_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(harness, "_spawn", lambda argv, **kw:
                        subprocess.CompletedProcess(argv, 1, _stream(_INIT), "boom"))
    monkeypatch.setattr(harness, "PRODUCTION_SNAPSHOT_PATH",
                        lambda s: tmp_path / f"{s}.json")
    project = tmp_path / "proj"
    project.mkdir()
    with pytest.raises(AssertionError, match="no result event"):
        harness.run("hi", session="claude-e2e-unit", project=project, home=tmp_path)


def test_probe_tools(monkeypatch, tmp_path):
    seen = {}

    def fake_run(argv, **kw):
        seen["argv"] = argv
        return subprocess.CompletedProcess(argv, 0, _stream(_INIT, _RESULT), "")

    monkeypatch.setattr(harness, "_spawn", fake_run)
    assert harness.probe_tools(tmp_path) == (
        ("Task", "Bash", "Edit", "Read", "Write"), "2.1.291")
    assert "stream-json" in seen["argv"] and "--verbose" in seen["argv"]


def test_probe_tools_failure_is_unknown(monkeypatch, tmp_path):
    def boom(argv, **kw):
        raise OSError("no claude")
    monkeypatch.setattr(harness, "_spawn", boom)
    assert harness.probe_tools(tmp_path) == (None, "")
