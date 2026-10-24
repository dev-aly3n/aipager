"""The hook and the status line inside a Claude Code background worker.

A worker (``CLAUDE_JOB_DIR`` set) inherits the session's
``CLAUDE_DTACH_SESSION`` but must never count for it on that tag alone:

- foreign (no link for its job): no datagram, no aipager file written,
  consumed or removed, no output except a PreToolUse deny or soft deny,
  decided read-only from the inherited session's rules;
- linked (the daemon's link names a session): it runs as that session,
  and every datagram carries ``bg_job``;
- the session's own claude also forwards ``CLAUDE_PID`` as ``claude_pid``.

Most tests run the real hook in a fresh interpreter, like
tests/test_hook_startup.py: a short mkdtemp folder is the instance
(``AIPAGER_INSTANCE_DIR``: runtime files and the daemon's socket) and holds
a throwaway HOME and a fake Claude config folder. Each foreign case has an
own-hook control showing the effect it suppresses really happens.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from tests.test_hook_startup import EVENTS, SESSION, _clean, _env, _payload, _received

REPO = Path(__file__).resolve().parents[1]
JOB = "ab12cd34"
LINKED = "claude-frontend"


@pytest.fixture
def root():
    path = Path(tempfile.mkdtemp(prefix="ahw-"))
    (path / "home").mkdir()
    (path / "cfg" / "jobs" / JOB).mkdir(parents=True)
    (path / "cfg" / "sessions").mkdir()
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def daemon(root):
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    sock.bind(str(root / "aipager.sock"))
    yield sock
    sock.close()


def _worker_env(root: Path, role: str, extra: dict | None = None) -> dict:
    env = _env(root)
    if role != "own":
        env["CLAUDE_JOB_DIR"] = str(root / "cfg" / "jobs" / JOB)
    env.update(extra or {})
    return env


def _link(root: Path, session: str = LINKED) -> None:
    """The daemon's link file for JOB, as step 2 writes it."""
    from aipager.dtach import bg_jobs
    assert bg_jobs.write_own_json(root / f"claude-bgjob-{JOB}.json",
                                  {"job": JOB, "session": session})


def _hook(root: Path, payload: dict, role: str = "foreign", *,
          module: str = "notify_hook", extra: dict | None = None,
          via_parent: bool = False) -> subprocess.CompletedProcess:
    """Run the real hook. *role*: ``own`` (no CLAUDE_JOB_DIR), ``foreign``
    or ``linked`` (a link file for JOB is planted first). *via_parent*
    starts it from a parent that carries the session's tag, as Claude Code
    does (the SendMessage receipt's process chain)."""
    if role == "linked":
        _link(root)
    cmd = [sys.executable, "-c", f"from aipager.dtach.{module} import main; main()"]
    if via_parent:
        cmd = [sys.executable, "-c",
               "import subprocess, sys; "
               f"sys.exit(subprocess.run({cmd!r}).returncode)"]
    return subprocess.run(cmd, input=json.dumps(payload), capture_output=True,
                          text=True, env=_worker_env(root, role, extra),
                          cwd=str(root), timeout=60)


def _py(root: Path, code: str) -> str:
    """Prepare state in the instance folder through aipager itself, in a
    separate interpreter (the measured hook never imports the writers)."""
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True, env=_env(root), cwd=str(root), timeout=60)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


#: The prompt Claude Code submits for a note written by :func:`_note`.
PROMPT = "[via Telegram]\nhello"


def _prompt() -> dict:
    payload = _payload("UserPromptSubmit")
    payload["prompt"] = PROMPT
    return payload


def _note(root: Path, session: str = SESSION, text: str = "hello") -> None:
    out = _py(root, (
        "from aipager.policy_snapshot import write_note\n"
        f"print(write_note({session!r}, None, None, None, msg_id=1, chat_id=1, "
        f"sender_key=(1, 1), body={'[via Telegram]' + chr(10) + text!r}, "
        f"raw_text={text!r}, style_text='', scope_mode=False))\n"))
    assert out != "None" and Path(out).exists()


_OWNER_SNAPSHOT = (
    "from aipager import policy_snapshot as ps\n"
    "from aipager.policy import Role\n"
    "from aipager.safety import BUILTIN_ROLE_DEFAULTS\n"
    "d = BUILTIN_ROLE_DEFAULTS['owner']\n"
    "role = Role(name='owner', **{k: (tuple(v) if isinstance(v, (list, tuple)) else v)"
    " for k, v in d.items()})\n"
    "snap = ps.resolve_snapshot(role, None, None)\n"
    "snap['turn_origin'] = 'telegram'\n"
    "ps.write_merged_snapshot(SESSION, snap)\n"
    "print(ps.snapshot_path(SESSION))\n"
)


def _owner_snapshot(root: Path, session: str = SESSION) -> Path:
    path = Path(_py(root, f"SESSION = {session!r}\n" + _OWNER_SNAPSHOT))
    assert path.exists()
    return path


def _tree(root: Path) -> dict:
    """Every entry under the instance folder (the daemon's socket aside):
    a regular file's bytes, anything else by its kind. Notes, snapshots,
    turn-open marks, pins, triggers, markers, reply sockets, status files
    and peer receipts all live here."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            path = Path(dirpath) / name
            rel = str(path.relative_to(root))
            if rel == "aipager.sock":
                continue
            if path.is_symlink() or not path.is_file():
                out[rel] = "dir" if path.is_dir() else "other"
            else:
                out[rel] = path.read_bytes()
    return out


# ---- every event of a foreign worker ------------------------------------------------------

@pytest.mark.parametrize("event", EVENTS)
def test_a_foreign_worker_sends_and_writes_nothing(root, daemon, event):
    _note(root)
    before = _tree(root)
    proc = _hook(root, _payload(event))
    assert proc.returncode == 0, proc.stderr
    assert _clean(proc.stderr), proc.stderr
    assert _received(daemon) == []
    assert _tree(root) == before
    if event == "PreToolUse":
        # No policy for the session: the floor denies, as for its own claude.
        decision = json.loads(proc.stdout.strip().splitlines()[0])
        assert decision["hookSpecificOutput"]["permissionDecision"] == "deny"
    else:
        assert proc.stdout == ""


def test_a_blank_job_dir_is_the_sessions_own_claude(root, daemon):
    proc = _hook(root, _payload("Notification"), "own", extra={"CLAUDE_JOB_DIR": "  "})
    assert proc.returncode == 0, proc.stderr
    got = _received(daemon)
    assert [(g["hook_event_name"], g["session"]) for g in got] == [("Notification", SESSION)]
    assert "bg_job" not in got[0]


def test_a_job_dir_naming_no_job_is_foreign(root, daemon):
    proc = _hook(root, _payload("Notification"), "own",
                 extra={"CLAUDE_JOB_DIR": str(root / "cfg" / "jobs" / "..")})
    assert proc.returncode == 0, proc.stderr
    assert _received(daemon) == []


# ---- T1.4: UserPromptSubmit -------------------------------------------------------------

@pytest.mark.parametrize("role", ["own", "foreign"])
def test_a_foreign_prompt_consumes_no_note(root, daemon, role):
    _note(root)
    snap = _owner_snapshot(root)
    before = _tree(root)
    proc = _hook(root, _prompt(), role)
    assert proc.returncode == 0 and _clean(proc.stderr), proc.stderr
    got = _received(daemon)
    notes = sorted((root / f"claude-notes-{SESSION}").glob("*.json"))
    if role == "own":
        # The control: the session's own claude consumes the note and
        # rewrites the snapshot.
        assert [g["hook_event_name"] for g in got] == ["queue_pickup", "UserPromptSubmit"]
        assert notes == [] and snap.read_bytes() != before[snap.name]
        return
    assert got == []
    assert proc.stdout == ""
    assert _tree(root) == before


# ---- T1.5: the turn end -----------------------------------------------------------------

@pytest.mark.parametrize("role", ["own", "foreign"])
@pytest.mark.parametrize("event", ["Stop", "SessionStart"])
def test_a_foreign_turn_end_leaves_the_turn_open(root, daemon, event, role):
    _owner_snapshot(root)
    turn_open = Path(_py(root, (
        "from aipager import policy_snapshot as ps\n"
        f"ps.mark_turn_open({SESSION!r})\n"
        f"print(ps.turn_open_path({SESSION!r}))\n")))
    assert turn_open.exists()
    payload = _payload(event)
    if event == "SessionStart":
        payload["source"] = "startup"
    proc = _hook(root, payload, role)
    assert proc.returncode == 0, proc.stderr
    got = _received(daemon)
    if role == "own":
        assert not turn_open.exists() and [g["hook_event_name"] for g in got] == [event]
        return
    assert turn_open.exists()
    assert got == []


# ---- T1.6: agent pins ---------------------------------------------------------------------

@pytest.mark.parametrize("role", ["own", "foreign"])
def test_a_foreign_agent_start_pins_nothing(root, daemon, role):
    _owner_snapshot(root)
    proc = _hook(root, _payload("SubagentStart"), role)
    assert proc.returncode == 0, proc.stderr
    pins = root / f"claude-policy-{SESSION}.pins"
    if role == "own":
        assert pins.is_dir() and list(pins.iterdir())
        return
    assert not pins.exists()
    assert _received(daemon) == []


# ---- T1.7: enforcement still runs, and tells no one ---------------------------------------

@pytest.mark.parametrize("role", ["own", "foreign"])
def test_a_foreign_denial_prints_and_reports_nothing(root, daemon, role):
    proc = _hook(root, _payload("PreToolUse"), role)
    assert proc.returncode == 0, proc.stderr
    decision = json.loads(proc.stdout.strip().splitlines()[0])
    assert decision["hookSpecificOutput"]["permissionDecision"] == "deny"
    kinds = [g["hook_event_name"] for g in _received(daemon)]
    if role == "own":
        assert kinds == ["PreToolUse", "safety_blocked"]
    else:
        assert kinds == []


def test_a_foreign_worker_keeps_the_inherited_sessions_owner_rights(root, daemon):
    """Read-only enforcement decides from the inherited session's rules:
    never looser, and an owner's turn is not made stricter either."""
    _owner_snapshot(root)
    proc = _hook(root, _payload("PreToolUse"))
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == ""
    assert _received(daemon) == []


@pytest.mark.parametrize("role", ["own", "foreign"])
def test_a_foreign_check_is_read_only(root, daemon, role):
    """Outside input in the inherited session's turn: its own claude's
    check narrows the snapshot (a write); a foreign worker's denies the
    same call and leaves the snapshot as it was."""
    snap = _owner_snapshot(root)
    before = snap.read_bytes()
    transcript = root / "t.jsonl"
    transcript.write_text("\n".join(json.dumps(e) for e in (
        {"type": "user", "message": {"role": "user",
                                     "content": "[via Telegram \u00b7 @alice]\nwork"}},
        {"type": "user", "origin": {"kind": "channel", "server": "x"},
         "message": {"role": "user", "content": "news"}},
    )) + "\n")
    payload = _payload("PreToolUse")
    payload.update(tool_input={"command": "rm -rf ~/x"}, transcript_path=str(transcript))
    proc = _hook(root, payload, role)
    assert proc.returncode == 0 and _clean(proc.stderr), proc.stderr
    decision = json.loads(proc.stdout.strip().splitlines()[0])
    assert decision["hookSpecificOutput"]["permissionDecision"] == "deny"
    if role == "own":
        assert snap.read_bytes() != before
        return
    assert snap.read_bytes() == before
    assert not (root / f"claude-policy-{SESSION}.lock").exists()
    assert _received(daemon) == []


def test_a_foreign_soft_refusal_prints_softly(root, daemon):
    """SendUserFile of a credential: the send floor's soft refusal, as for
    the session's own claude, and no send_files_refused datagram."""
    _owner_snapshot(root)
    payload = _payload("PreToolUse")
    payload.update(tool_name="SendUserFile",
                   tool_input={"files": [str(root / "home" / ".ssh" / "id_rsa")]})
    proc = _hook(root, payload)
    assert proc.returncode == 0, proc.stderr
    decision = json.loads(proc.stdout.strip().splitlines()[0])["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    assert "aipager safety policy" not in decision["permissionDecisionReason"]
    assert _received(daemon) == []


# ---- T1.8: no SendMessage receipt -----------------------------------------------------------

@pytest.mark.parametrize("role", ["own", "foreign"])
def test_a_foreign_send_message_writes_no_receipt(root, daemon, role):
    _owner_snapshot(root)
    payload = _payload("PreToolUse")
    payload.update(tool_name="SendMessage",
                   tool_input={"to": "uds:/run/user/1/cc-socks/1.sock", "message": "hi"})
    store = root / "home" / ".local" / "state" / "aipager" / "peer"
    proc = _hook(root, payload, role, via_parent=True)
    assert proc.returncode == 0 and _clean(proc.stderr), proc.stderr
    assert proc.stdout == ""   # allowed
    receipts = sorted(store.rglob("*")) if store.exists() else []
    if role == "own":
        assert [p for p in receipts if p.is_file()], "the control wrote no receipt"
        return
    assert receipts == []
    assert _received(daemon) == []


# ---- T1.9: PermissionRequest ------------------------------------------------------------------

@pytest.mark.parametrize("role", ["own", "foreign"])
def test_a_foreign_permission_request_opens_no_reply_socket(root, daemon, role):
    before = _tree(root)
    proc = _hook(root, _payload("PermissionRequest"), role)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == ""
    got = _received(daemon)
    if role == "own":
        assert got and got[0].get("aipager_reply_addr")
        return
    assert got == []
    assert _tree(root) == before


# ---- T1.10: PreModelSwitch ---------------------------------------------------------------------

@pytest.mark.parametrize("role", ["own", "foreign"])
def test_a_foreign_model_switch_keeps_the_marker(root, daemon, role):
    marker = Path(_py(root, (
        "from aipager.dtach import model_switch_marker as m\n"
        f"assert m.write_marker({str(root)!r}, {SESSION!r}, 'opus', 's-lean')\n"
        f"print(m.marker_path({str(root)!r}, {SESSION!r}))\n")))
    assert marker.exists()
    payload = {"hook_event_name": "PreModelSwitch", "source": "command",
               "session_id": "s-lean", "requested_model": "opus"}
    proc = _hook(root, payload, role)
    assert proc.returncode == 0, proc.stderr
    if role == "own":
        assert not marker.exists()
        assert json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecision"] == "allow"
        return
    assert marker.exists()
    assert proc.stdout == ""
    assert _received(daemon) == []


# ---- T1.12: a linked worker is the parked session ---------------------------------------------

def test_a_linked_prompt_runs_as_the_linked_session(root, daemon):
    _note(root, LINKED)
    _note(root, SESSION)
    proc = _hook(root, _prompt(), "linked",
                 extra={"CLAUDE_PID": "4242"})
    assert proc.returncode == 0 and _clean(proc.stderr), proc.stderr
    got = _received(daemon)
    assert [g["hook_event_name"] for g in got] == ["queue_pickup", "UserPromptSubmit"]
    assert all(g["session"] == LINKED and g["bg_job"] == JOB for g in got), got
    assert all("claude_pid" not in g for g in got)   # a worker's pid is not the session's
    assert list((root / f"claude-notes-{LINKED}").glob("*.json")) == []
    assert len(list((root / f"claude-notes-{SESSION}").glob("*.json"))) == 1


@pytest.mark.parametrize("event", ["Stop", "PreToolUse", "PermissionRequest", "Notification"])
def test_every_linked_datagram_carries_the_job(root, daemon, event):
    proc = _hook(root, _payload(event), "linked")
    assert proc.returncode == 0, proc.stderr
    got = _received(daemon)
    assert got and all(g.get("session") == LINKED and g.get("bg_job") == JOB
                       for g in got), got


def test_a_linked_pre_tool_use_is_decided_as_the_linked_session(root, daemon):
    _owner_snapshot(root, LINKED)
    proc = _hook(root, _payload("PreToolUse"), "linked")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == ""   # the linked session's owner rights
    assert [g["hook_event_name"] for g in _received(daemon)] == ["PreToolUse"]


# ---- T1.13: the status line ----------------------------------------------------------------------

_STATUS = {"session_id": "s-lean", "model": {"display_name": "Opus"},
           "context_window": {"used_percentage": 3}, "cost": {"total_cost_usd": 0}}


def test_a_linked_status_line_writes_the_linked_file(root, daemon):
    proc = _hook(root, _STATUS, "linked", module="statusline_notify")
    assert proc.returncode == 0, proc.stderr
    got = _received(daemon)
    assert [(g["type"], g["session"], g.get("bg_job")) for g in got] == [
        ("statusline", LINKED, JOB)]
    assert (root / f"claude-status-{LINKED}.json").exists()
    assert not (root / f"claude-status-{SESSION}.json").exists()


def test_a_foreign_status_line_only_prints(root, daemon):
    before = _tree(root)
    proc = _hook(root, _STATUS, "foreign", module="statusline_notify")
    assert proc.returncode == 0, proc.stderr
    assert "Opus" in proc.stdout
    assert _received(daemon) == []
    assert _tree(root) == before


def test_an_own_status_line_is_unchanged(root, daemon):
    proc = _hook(root, _STATUS, "own", module="statusline_notify")
    assert proc.returncode == 0, proc.stderr
    got = _received(daemon)
    assert [(g["type"], g["session"]) for g in got] == [("statusline", SESSION)]
    assert "bg_job" not in got[0]


# ---- T1.14: the session's own claude forwards its pid ------------------------------------------

@pytest.mark.parametrize("value,expected", [
    ("4242", 4242), ("  4242 ", 4242), ("42a2", None), ("", None), ("-1", None),
    ("12345678901", None), ("٤٢", None),
], ids=["digits", "padded", "letters", "empty", "negative", "too-long", "non-ascii"])
def test_an_own_hook_forwards_claude_pid(root, daemon, value, expected):
    proc = _hook(root, _payload("Notification"), "own", extra={"CLAUDE_PID": value})
    assert proc.returncode == 0, proc.stderr
    got = _received(daemon)
    assert len(got) == 1
    assert got[0].get("claude_pid") == expected
    if expected is None:
        assert "claude_pid" not in got[0]


# ---- T1.11: a cap hit (in process) ------------------------------------------------------------------

class _Sock:
    def __init__(self, sink):
        self.sink = sink

    def sendto(self, data, _addr):
        self.sink.append(json.loads(data.decode()))

    def setblocking(self, _flag):
        pass

    def close(self):
        pass


@pytest.mark.parametrize("role", ["own", "linked", "foreign"])
def test_a_cap_hit_in_a_foreign_worker_sends_and_removes_nothing(monkeypatch, tmp_path, role):
    from aipager import policy_snapshot as ps
    from aipager.dtach import bg_jobs, notify_hook

    snap = ps.snapshot_path(SESSION)
    snap.parent.mkdir(parents=True, exist_ok=True)
    ps.write_merged_snapshot(SESSION, {"bypass_safety": True})
    ps.write_merged_snapshot(LINKED, {"bypass_safety": True})
    monkeypatch.setenv("CLAUDE_DTACH_SESSION", SESSION)
    if role != "own":
        monkeypatch.setenv("CLAUDE_JOB_DIR", str(tmp_path / "cfg" / "jobs" / JOB))
    if role == "linked":
        assert bg_jobs.write_own_json(bg_jobs.link_path(JOB), {"job": JOB, "session": LINKED})
    sent: list[dict] = []
    monkeypatch.setattr(socket, "socket", lambda *_a, **_k: _Sock(sent))

    def boom(session, slot, worker=None):
        slot[1] = True   # a PreToolUse ...
        slot[2] = True   # ... and the snapshot-removal flag
        raise MemoryError
    monkeypatch.setattr(notify_hook, "_run", boom)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    with pytest.raises(SystemExit) as exc:
        notify_hook.main()
    assert exc.value.code == 2
    if role == "own":
        assert [(s["type"], s["session"]) for s in sent] == [("hook_memory_cap_hit", SESSION)]
        assert ps.read_snapshot(SESSION) is None
    elif role == "linked":
        assert [(s["session"], s.get("bg_job")) for s in sent] == [(LINKED, JOB)]
        assert ps.read_snapshot(LINKED) is None
        assert ps.read_snapshot(SESSION) is not None
    else:
        assert sent == []
        assert ps.read_snapshot(SESSION) is not None
        assert ps.read_snapshot(LINKED) is not None


@pytest.mark.parametrize("role", ["own", "foreign"])
def test_a_foreign_failure_denies_and_reports_nothing(monkeypatch, tmp_path, role):
    """An error escaping a foreign PreToolUse: main's fail-closed deny,
    and no problem report (a datagram)."""
    from aipager.dtach import notify_hook

    monkeypatch.setenv("CLAUDE_DTACH_SESSION", SESSION)
    if role == "foreign":
        monkeypatch.setenv("CLAUDE_JOB_DIR", str(tmp_path / "cfg" / "jobs" / JOB))
    sent: list[dict] = []
    monkeypatch.setattr(socket, "socket", lambda *_a, **_k: _Sock(sent))

    def broken(session, slot, worker=None):
        slot[1] = True
        raise RuntimeError("boom")
    monkeypatch.setattr(notify_hook, "_run", broken)
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    notify_hook.main()
    decision = json.loads(out.getvalue().strip().splitlines()[0])
    assert decision["hookSpecificOutput"]["permissionDecision"] == "deny"
    if role == "own":
        assert sent, "the control sent no problem report"
    else:
        assert sent == []
