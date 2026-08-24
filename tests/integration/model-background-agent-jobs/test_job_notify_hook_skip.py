"""design.md success criterion (requirement 4 / notify_hook.py half of the
safety fix):
- `notify_hook._match_and_promote` is never called for a continuation
  UserPromptSubmit, and neither is the style/reply-context
  `additionalContext` print — "so the merged policy snapshot stays
  pinned."

Entrypoints.md's stated harness: feed JSON via stdin to
``notify_hook.main()``/``notify_hook._run``, with ``SOCKET_PATH``
redirected to a throwaway ``tmp_path`` unix socket that this test binds
and reads from — mirrors ``tests/test_dtach_hook_stubs.py``'s
established "bind-and-recvfrom" pattern for observing the fire-and-
forget UDP datagram(s), used here instead of a real Telegram/daemon
process.
"""

from __future__ import annotations

import io
import json
import socket
import sys
from unittest.mock import MagicMock

from aipager.dtach import notify_hook

CONTINUATION_PROMPT = (
    "<task-notification>\n<task-id>abc123</task-id>\nBackground agent finished."
)


def _set_stdin(monkeypatch, text):
    monkeypatch.setattr(sys, "stdin", io.StringIO(text))


def _bound_socket(tmp_path):
    sock_path = tmp_path / "aipager.sock"
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    srv.bind(str(sock_path))
    srv.settimeout(0.3)
    return sock_path, srv


def _drain(srv, max_msgs=4):
    out = []
    for _ in range(max_msgs):
        try:
            data, _ = srv.recvfrom(65536)
        except (socket.timeout, OSError):
            break
        out.append(json.loads(data.decode()))
    return out


def _run_hook(monkeypatch, tmp_path, prompt, session="hiva"):
    sock_path, srv = _bound_socket(tmp_path)
    monkeypatch.setattr(notify_hook, "SOCKET_PATH", str(sock_path))
    _set_stdin(monkeypatch, json.dumps({
        "hook_event_name": "UserPromptSubmit",
        "prompt": prompt,
        "session": session,
    }))
    monkeypatch.setenv("CLAUDE_DTACH_SESSION", session)
    try:
        notify_hook.main()
    finally:
        pass
    return srv


# ---- _match_and_promote is skipped for a continuation ---------------------

def test_match_and_promote_not_called_for_continuation(monkeypatch, tmp_path):
    mock_match = MagicMock(return_value=([], []))
    monkeypatch.setattr(notify_hook, "_match_and_promote", mock_match)

    srv = _run_hook(monkeypatch, tmp_path, CONTINUATION_PROMPT)
    try:
        _drain(srv)
    finally:
        srv.close()

    mock_match.assert_not_called()


def test_match_and_promote_is_called_for_a_genuine_prompt(monkeypatch, tmp_path):
    """Contrast/control: without this, the assertion above would be
    trivially true if the hook simply never calls the matcher at all —
    prove the seam is genuinely wired for the non-continuation case."""
    mock_match = MagicMock(return_value=([], []))
    monkeypatch.setattr(notify_hook, "_match_and_promote", mock_match)

    srv = _run_hook(monkeypatch, tmp_path, "a brand new genuine prompt")
    try:
        _drain(srv)
    finally:
        srv.close()

    mock_match.assert_called_once()
    args = mock_match.call_args.args
    assert args[0] == "hiva"
    assert args[1] == "a brand new genuine prompt"


# ---- no queue_pickup datagram for a continuation ---------------------------

def test_no_queue_pickup_datagram_for_continuation(monkeypatch, tmp_path):
    """Even if the matcher WOULD have matched something, the continuation
    branch must return before ever calling it — so no queue_pickup event
    can be observed on the wire."""
    mock_match = MagicMock(return_value=(
        [{"body": "would have matched"}], []))
    monkeypatch.setattr(notify_hook, "_match_and_promote", mock_match)

    srv = _run_hook(monkeypatch, tmp_path, CONTINUATION_PROMPT)
    try:
        received = _drain(srv)
    finally:
        srv.close()

    kinds = [m.get("hook_event_name") for m in received]
    assert "queue_pickup" not in kinds, (
        f"a queue_pickup datagram was sent for a continuation prompt: "
        f"{received!r}")


def test_queue_pickup_datagram_sent_for_genuine_matching_prompt(
        monkeypatch, tmp_path):
    mock_match = MagicMock(return_value=(
        [{"body": "matched note", "msg_id": 1, "chat_id": 1}], []))
    monkeypatch.setattr(notify_hook, "_match_and_promote", mock_match)

    srv = _run_hook(monkeypatch, tmp_path, "do the matched thing")
    try:
        received = _drain(srv)
    finally:
        srv.close()

    kinds = [m.get("hook_event_name") for m in received]
    assert "queue_pickup" in kinds, (
        f"expected a queue_pickup datagram for a genuine match, got: "
        f"{received!r}")


# ---- stdout: no additionalContext print for a continuation ---------------

def test_continuation_prints_nothing_to_stdout(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("aipager.policy_snapshot.read_snapshot",
                        lambda session: {"style_text": "Keep it short.",
                                         "reply_context": ""})
    srv = _run_hook(monkeypatch, tmp_path, CONTINUATION_PROMPT)
    try:
        _drain(srv)
    finally:
        srv.close()

    assert capsys.readouterr().out == ""


def test_genuine_prompt_prints_additional_context_when_style_present(
        monkeypatch, tmp_path, capsys):
    """Contrast/control for the assertion above: prove the style-text
    print path is genuinely reachable in this harness for a
    non-continuation prompt, so the empty-stdout assertion isn't
    vacuous."""
    monkeypatch.setattr("aipager.policy_snapshot.read_snapshot",
                        lambda session: {"style_text": "Keep it short.",
                                         "reply_context": ""})
    srv = _run_hook(monkeypatch, tmp_path, "a brand new genuine prompt")
    try:
        _drain(srv)
    finally:
        srv.close()

    out = capsys.readouterr().out.strip()
    assert out != ""
    payload = json.loads(out)
    assert payload["hookSpecificOutput"]["additionalContext"] == "Keep it short."
