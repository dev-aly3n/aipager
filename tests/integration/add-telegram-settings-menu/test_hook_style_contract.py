"""Black-box tests: additional UserPromptSubmit hook edge cases beyond the
happy-path already exercised elsewhere.

entrypoints.md's contract: the ``aipager-hook`` console script prints to
stdout ONLY when the current session's snapshot has a non-empty
``style_text``, printing the exact
``{"hookSpecificOutput": {...}}`` envelope; otherwise it is silent,
matching its existing behaviour for every event it doesn't act on. UDP
forwarding to the daemon happens regardless. This file drives the hook only
via stdin / env / a pre-seeded snapshot file, per entrypoints.md's stated
harness (mirrors tests/test_dtach_hook_stubs.py's existing pattern).
"""

from __future__ import annotations

import io
import json
import socket
import sys

from aipager.dtach import notify_hook


def _set_stdin(monkeypatch, text):
    monkeypatch.setattr(sys, "stdin", io.StringIO(text))


def _isolate_snapshot_path(monkeypatch, tmp_path):
    from aipager import policy_snapshot
    monkeypatch.setattr(policy_snapshot, "snapshot_path",
                        lambda n: tmp_path / f"{n}.json")
    return policy_snapshot


def _seed_matching_note(policy_snapshot, session, body, *, style_text="",
                        reply_context=""):
    """Write a per-message note whose ``body`` a submitted prompt of the
    same text will match (queue handoff) — the canonical snapshot this
    hook reads is now populated by ITS OWN pick-up match, not by a
    pre-written file, so seeding a note (and echoing its text back as
    the submitted prompt) is what stands in for the old direct
    ``write_snapshot`` seed."""
    policy_snapshot.write_note(
        session, None, None, None,
        msg_id=1, chat_id=1, sender_key=(1, 1),
        body=body, raw_text=body,
        style_text=style_text, reply_context=reply_context,
    )


# ---- stdout is strictly the JSON envelope, nothing else --------------------

def test_stdout_is_exactly_one_json_line_no_extra_output(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(notify_hook, "SOCKET_PATH", str(tmp_path / "nope.sock"))
    policy_snapshot = _isolate_snapshot_path(monkeypatch, tmp_path)
    _seed_matching_note(
        policy_snapshot, "claude-oneline", "do the thing",
        style_text="Apply this reply-style guidance"
                   " to your next answer; do not"
                   " mention or quote it:\n"
                   "- Keep the answer short —"
                   " a few sentences.")
    _set_stdin(monkeypatch,
              '{"hook_event_name":"UserPromptSubmit","prompt":"do the thing"}')
    monkeypatch.setenv("CLAUDE_DTACH_SESSION", "claude-oneline")
    notify_hook.main()
    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected exactly one non-empty stdout line, got: {lines!r}"
    json.loads(lines[0])  # must parse as valid JSON on its own


# ---- old / partial snapshot shape resilience --------------------------------

def test_snapshot_missing_style_text_key_entirely_prints_nothing(monkeypatch, tmp_path, capsys):
    """A snapshot written before this field existed (partial-rollout /
    upgrade-mid-session) must degrade to silence, not crash the hook."""
    monkeypatch.setattr(notify_hook, "SOCKET_PATH", str(tmp_path / "nope.sock"))
    _isolate_snapshot_path(monkeypatch, tmp_path)
    legacy_path = tmp_path / "claude-legacy.json"
    legacy_path.write_text(json.dumps({"origin": "telegram", "bypass_safety": False}))
    _set_stdin(monkeypatch, '{"hook_event_name":"UserPromptSubmit"}')
    monkeypatch.setenv("CLAUDE_DTACH_SESSION", "claude-legacy")
    notify_hook.main()  # must not raise
    assert capsys.readouterr().out == ""


def test_corrupt_snapshot_file_prints_nothing_no_crash(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(notify_hook, "SOCKET_PATH", str(tmp_path / "nope.sock"))
    _isolate_snapshot_path(monkeypatch, tmp_path)
    bad_path = tmp_path / "claude-corrupt.json"
    bad_path.write_text("{ not json at all")
    _set_stdin(monkeypatch, '{"hook_event_name":"UserPromptSubmit"}')
    monkeypatch.setenv("CLAUDE_DTACH_SESSION", "claude-corrupt")
    notify_hook.main()  # must not raise
    assert capsys.readouterr().out == ""


def test_style_text_wrong_type_prints_nothing_no_crash(monkeypatch, tmp_path, capsys):
    """style_text as a non-string (e.g. a stray integer from manual editing)
    must not crash the hook or produce malformed stdout."""
    monkeypatch.setattr(notify_hook, "SOCKET_PATH", str(tmp_path / "nope.sock"))
    _isolate_snapshot_path(monkeypatch, tmp_path)
    weird_path = tmp_path / "claude-weird.json"
    weird_path.write_text(json.dumps({"style_text": 12345}))
    _set_stdin(monkeypatch, '{"hook_event_name":"UserPromptSubmit"}')
    monkeypatch.setenv("CLAUDE_DTACH_SESSION", "claude-weird")
    notify_hook.main()  # must not raise
    out = capsys.readouterr().out
    if out.strip():
        json.loads(out.strip())  # if it printed anything, it must be valid JSON


# ---- JSON-escaping correctness ----------------------------------------------

def test_style_text_with_quotes_and_backslashes_produces_valid_json(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(notify_hook, "SOCKET_PATH", str(tmp_path / "nope.sock"))
    policy_snapshot = _isolate_snapshot_path(monkeypatch, tmp_path)
    tricky = 'Use "quotes" and a backslash \\ and a newline\ncorrectly.'
    _seed_matching_note(policy_snapshot, "claude-tricky", "do the thing",
                        style_text=tricky)
    _set_stdin(monkeypatch,
              '{"hook_event_name":"UserPromptSubmit","prompt":"do the thing"}')
    monkeypatch.setenv("CLAUDE_DTACH_SESSION", "claude-tricky")
    notify_hook.main()
    out = capsys.readouterr().out.strip()
    payload = json.loads(out)  # must not raise — strictly valid JSON
    assert payload["hookSpecificOutput"]["additionalContext"] == tricky


# ---- session isolation -------------------------------------------------------

def test_two_sessions_do_not_leak_style_text_into_each_other(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(notify_hook, "SOCKET_PATH", str(tmp_path / "nope.sock"))
    policy_snapshot = _isolate_snapshot_path(monkeypatch, tmp_path)
    _seed_matching_note(policy_snapshot, "claude-a", "do the thing",
                        style_text="Keep the answer short — a few sentences.")
    _seed_matching_note(policy_snapshot, "claude-b", "do the thing", style_text="")

    _set_stdin(monkeypatch,
              '{"hook_event_name":"UserPromptSubmit","prompt":"do the thing"}')
    monkeypatch.setenv("CLAUDE_DTACH_SESSION", "claude-b")
    notify_hook.main()
    assert capsys.readouterr().out == "", "session B (empty style_text) must stay silent"

    _set_stdin(monkeypatch,
              '{"hook_event_name":"UserPromptSubmit","prompt":"do the thing"}')
    monkeypatch.setenv("CLAUDE_DTACH_SESSION", "claude-a")
    notify_hook.main()
    out_a = capsys.readouterr().out.strip()
    payload = json.loads(out_a)
    assert "short" in payload["hookSpecificOutput"]["additionalContext"]


# ---- UDP forwarding happens even when style_text is non-empty ---------------

def test_udp_forward_still_happens_when_style_text_is_non_empty(monkeypatch, tmp_path):
    sock_path = tmp_path / "aipager.sock"
    monkeypatch.setattr(notify_hook, "SOCKET_PATH", str(sock_path))
    policy_snapshot = _isolate_snapshot_path(monkeypatch, tmp_path)
    policy_snapshot.write_snapshot("claude-udp2", None, None, None,
                                   style_text="Give a long, thorough answer.")
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    srv.bind(str(sock_path))
    srv.settimeout(1.0)
    try:
        _set_stdin(monkeypatch, '{"hook_event_name":"UserPromptSubmit"}')
        monkeypatch.setenv("CLAUDE_DTACH_SESSION", "claude-udp2")
        notify_hook.main()
        data, _ = srv.recvfrom(4096)
        payload = json.loads(data.decode())
        assert payload["hook_event_name"] == "UserPromptSubmit"
        assert payload["session"] == "claude-udp2"
    finally:
        srv.close()


# ---- events other than UserPromptSubmit never print style JSON -------------

def test_pre_tool_use_event_never_prints_style_json_even_with_style_text(monkeypatch, tmp_path, capsys):
    """A style_text present in the snapshot must only ever surface on the
    UserPromptSubmit event — never leak onto an unrelated event's stdout."""
    monkeypatch.setattr(notify_hook, "SOCKET_PATH", str(tmp_path / "nope.sock"))
    policy_snapshot = _isolate_snapshot_path(monkeypatch, tmp_path)
    policy_snapshot.write_snapshot("claude-other-evt", None, None, None,
                                   style_text="Use simple, everyday words.")
    _set_stdin(monkeypatch, '{"hook_event_name":"PreToolUse","tool_name":"Read"}')
    monkeypatch.setenv("CLAUDE_DTACH_SESSION", "claude-other-evt")
    notify_hook.main()
    assert capsys.readouterr().out == ""
