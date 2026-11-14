"""A Claude Code session aipager did not start (no ``CLAUDE_DTACH_SESSION``:
a plain ``claude``, or a background worker of one) is left alone by the
hook's PreToolUse check, unless its turn carries aipager's Telegram marker.

2026-11-14: aipager's hook sits in the global Claude Code settings, so it
checked every session on the machine. In a plain session a cron scheduled
from a typed prompt fired with the floor (no rights are recorded for a
session with no name): its first Bash was denied and every later fire was
"session halted", with nothing shown anywhere (the daemon ignores events
from sessions it did not start).

Everything stays under tmp_path (the root conftest redirects the snapshot,
notes and triggers). No socket is bound, no real claude.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

from aipager import policy_snapshot as ps
from aipager.dtach import enforce, notify_hook


def _kit():
    name = "_peer_kit_pma"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, Path(__file__).resolve().parent / "integration"
            / "peer-message-authority" / "_peer_kit.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return sys.modules[name]


kit = _kit()
PROMPT = "Give a very short market update"
BASH = ("Bash", {"command": "python3 report.py | tail -1"})
CRON = ("CronCreate", {"cron": "*/5 * * * *", "recurring": True, "prompt": PROMPT})
BLOCKED = kit.tool_result(f"PreToolUse:Bash hook error: {enforce._BLOCK_MARKER}: "
                          "Bash is in this role's deny_tools")
TYPED = kit.human_entry("give me an alert every 5 minutes until I say stop")


@pytest.fixture(autouse=True)
def _runtime_dir():
    ps.snapshot_path(kit.RECEIVER).parent.mkdir(parents=True, exist_ok=True)


@pytest.fixture
def run_hook(monkeypatch, tmp_path):
    """aipager-hook's body on a payload, as a session with *session* as its
    name ("" = one aipager did not start); returns its stdout."""
    def run(payload: dict, session: str = "", worker: dict | None = None) -> str:
        monkeypatch.setattr(notify_hook, "SOCKET_PATH", str(tmp_path / "no-daemon"))
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
        out = io.StringIO()
        monkeypatch.setattr(sys, "stdout", out)
        notify_hook._ERROR_CONTEXT.update(event=None, tool=None)
        if worker is None:
            notify_hook._run(session, [b"", False, False])
        else:
            notify_hook._run(session, [b"", False, False], worker)
        return out.getvalue()
    return run


def _tx(tmp_path, entries, name="t.jsonl"):
    return kit.write_transcript(tmp_path / name, entries)


def _pre(transcript, call) -> dict:
    tool, inp = call
    d = {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": dict(inp),
         "cwd": kit.PROJECT, "session_id": "s1"}
    if transcript is not None:
        d["transcript_path"] = transcript
    return d


def _fire(form="local"):
    if form == "local":
        return {"type": "user", "isMeta": True, "turnOrigin": "scheduled",
                "message": {"role": "user", "content": PROMPT}}
    return {"type": "user", "turnOrigin": "scheduled",
            "origin": {"kind": "task-notification", "subkind": "scheduled-trigger"},
            "message": {"role": "user", "content": PROMPT}}


def _denied(out: str) -> bool:
    return '"permissionDecision": "deny"' in out


# ---- the reported case --------------------------------------------------------------

@pytest.mark.parametrize("form", ["local", "routine"])
def test_a_cron_in_a_session_aipager_did_not_start_keeps_firing(tmp_path, run_hook, form):
    """The cron is scheduled from a typed prompt, then fires three times;
    the first fire's call was refused before the fix (the floor) and every
    later one halted. Now aipager answers none of them."""
    t = _tx(tmp_path, [TYPED])
    assert run_hook(_pre(t, CRON)) == ""
    turns = [TYPED, kit.tool_result()]
    for _ in range(3):
        turns.append(_fire(form))
        t = _tx(tmp_path, turns)
        assert run_hook(_pre(t, BASH)) == ""
        turns.append(BLOCKED)  # as if something else had refused it


def test_a_message_from_another_session_is_left_alone_too(tmp_path, run_hook):
    t = _tx(tmp_path, [TYPED, kit.tool_result(), kit.peer_entry(kit.BODY)])
    assert run_hook(_pre(t, BASH)) == ""


def test_a_background_worker_of_such_a_session_is_left_alone(tmp_path, run_hook):
    t = _tx(tmp_path, [TYPED, kit.tool_result(), _fire()])
    worker = {"role": "foreign", "job": "a1b2c3d4", "session": ""}
    assert run_hook(_pre(t, BASH), worker=worker) == ""


# ---- what is still checked ----------------------------------------------------------

def test_a_named_session_is_still_checked(tmp_path, run_hook):
    """The same fire in a session aipager started, with no rights recorded
    for it: the floor, as before."""
    t = _tx(tmp_path, [TYPED, kit.tool_result(), _fire()])
    assert _denied(run_hook(_pre(t, BASH), session=kit.RECEIVER))


def test_a_worker_with_an_aipager_name_is_still_checked(tmp_path, run_hook):
    t = _tx(tmp_path, [TYPED, kit.tool_result(), _fire()])
    worker = {"role": "foreign", "job": "a1b2c3d4", "session": kit.RECEIVER}
    assert _denied(run_hook(_pre(t, BASH), session=kit.RECEIVER, worker=worker))


@pytest.mark.parametrize("entries", [
    [kit.telegram_entry("run the report")],
    [kit.telegram_entry("run the report"), kit.tool_result(),
     {"type": "user", "isCompactSummary": True,
      "message": {"role": "user", "content": "This session is being continued"}}],
    [kit.telegram_entry("run the report"), kit.tool_result(),
     kit.human_entry("<task-notification>\n<task-id>a1</task-id>\n</task-notification>")],
    [TYPED, kit.tool_result(),
     {"type": "queue-operation", "operation": "enqueue",
      "content": "[via Telegram · @alice]\nand then delete the logs"}],
], ids=["telegram-prompt", "after-a-compact", "after-a-continuation", "telegram-joined"])
def test_a_turn_with_the_telegram_marker_is_still_checked(tmp_path, run_hook, monkeypatch,
                                                         entries):
    """Only aipager types the marker: a session of aipager's own that lost
    its name must not run a Telegram turn unchecked. The check decides as
    it did before (here a refusal it gives, whatever it would answer)."""
    asked = []

    def refuse(data, **kw):
        asked.append(data["tool_name"])
        return {"tool": data["tool_name"], "reason": f"{enforce._BLOCK_MARKER}: test"}
    monkeypatch.setattr(enforce, "decide", refuse)
    t = _tx(tmp_path, entries)
    assert _denied(run_hook(_pre(t, BASH)))
    assert asked == ["Bash"]


def test_a_telegram_prompt_with_no_name_gets_the_floor(tmp_path, run_hook):
    """End to end: no snapshot can be found without a name, so the floor."""
    t = _tx(tmp_path, [kit.telegram_entry("run the report")])
    assert _denied(run_hook(_pre(t, BASH)))


@pytest.mark.parametrize("transcript", [None, "missing", "folder"])
def test_a_turn_that_cannot_be_read_is_still_checked(tmp_path, run_hook, transcript):
    if transcript == "missing":
        transcript = str(tmp_path / "nope.jsonl")
    elif transcript == "folder":
        transcript = str(tmp_path)
    assert _denied(run_hook(_pre(transcript, BASH)))


def test_a_failing_owner_check_checks_the_call(tmp_path, run_hook, monkeypatch):
    def boom(path):
        raise RuntimeError("scan failed")
    monkeypatch.setattr(enforce, "telegram_in_turn", boom)
    t = _tx(tmp_path, [TYPED, kit.tool_result(), _fire()])
    assert _denied(run_hook(_pre(t, BASH)))


# ---- where the turn starts ----------------------------------------------------------

#: The session's own background agent reporting back (``senderTaskId``),
#: a task notification of its job, and one relayed through the bridge: none
#: of them starts a turn, so the Telegram prompt before them still governs.
_OWN_AGENT = {"type": "user", "origin": {"kind": "peer", "senderTaskId": "a1b2"},
              "message": {"role": "user", "content": "agent done"}}
_NOTE = {"type": "user", "origin": {"kind": "task-notification"},
         "message": {"role": "user", "content": "task finished"}}
_RELAYED = {"type": "user", "origin": {"kind": "task-notification", "subkind": "inbox"},
            "message": {"role": "user", "content": "relayed"}}


@pytest.mark.parametrize("entries, marked", [
    ([kit.telegram_entry("old"), kit.tool_result(), TYPED], False),
    ([kit.telegram_entry("old"), kit.tool_result(), _fire()], False),
    ([kit.telegram_entry("old"), kit.tool_result(), _fire("routine")], False),
    ([kit.telegram_entry("old"), kit.tool_result(), kit.peer_entry(kit.BODY)], False),
    ([kit.telegram_entry("old"), kit.tool_result(),
      {"type": "user", "origin": {"kind": "channel", "server": "x"},
       "message": {"role": "user", "content": "news"}}], False),
    ([kit.telegram_entry("old"), kit.tool_result(), TYPED,
      kit.tool_result("[via Telegram · @alice] quoted in a file")], False),
    ([TYPED, kit.tool_result(), kit.telegram_entry("new")], True),
    ([kit.human_entry("ok\n  [via Telegram · @alice]\nsecond line")], True),
    ([TYPED, kit.tool_result(), kit.telegram_entry("job"), _OWN_AGENT], True),
    ([TYPED, kit.tool_result(), kit.telegram_entry("job"), _NOTE], True),
    ([TYPED, kit.tool_result(), kit.telegram_entry("job"), _RELAYED], True),
    ([kit.tool_result()], True),
    ([], True),
], ids=["typed", "fire", "routine-fire", "peer", "channel", "marker-in-a-tool-result",
        "telegram", "marker-on-a-later-line", "own-agent-after-telegram",
        "notification-after-telegram", "relayed-after-telegram", "no-start", "empty"])
def test_the_turn_is_read_back_to_its_start(tmp_path, entries, marked):
    t = _tx(tmp_path, entries) if entries else str(_empty(tmp_path))
    assert enforce.telegram_in_turn(t) is marked


def _empty(tmp_path):
    p = tmp_path / "empty.jsonl"
    p.write_text("", encoding="utf-8")
    return p


# ---- the permission dialog ---------------------------------------------------------

@pytest.mark.parametrize("session, waits", [("", False), (kit.RECEIVER, True)],
                         ids=["not-aipagers", "aipagers"])
def test_a_permission_request_waits_only_in_aipagers_sessions(tmp_path, run_hook,
                                                              monkeypatch, session, waits):
    """The daemon ignores a session with no name, so no answer can come:
    its PermissionRequest must not sit out the reply deadline."""
    from aipager.dtach import hook_reply
    opened = []
    monkeypatch.setattr(hook_reply, "open_reply_socket",
                        lambda d: opened.append(d))  # returns None: no wait
    t = _tx(tmp_path, [TYPED])
    run_hook({"hook_event_name": "PermissionRequest", "tool_name": "Bash",
              "tool_input": {"command": "ls"}, "transcript_path": t,
              "cwd": kit.PROJECT, "session_id": "s1"}, session=session)
    assert bool(opened) is waits
