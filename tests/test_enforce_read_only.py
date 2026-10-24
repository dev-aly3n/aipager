"""``enforce.decide(data, read_only=True)``: the PreToolUse check of a
foreign Claude Code background worker. It takes no lock, writes nothing
(snapshot, loop counter, triggers, peer receipts) and denies every call
the writing path denies.

One scenario per writer in the decide path (ids name the site), each
built with the "peer message authority" kit:

- ``adopt_loop_655``: a bare /loop's fire is seen; the writing path adopts
  the loop's rights into the snapshot;
- ``wait_for_loop_1771``: the fire's entry is not written yet; the writing
  path counts the wait (a soft refusal);
- ``pending_peer_waiting_1427``: a message from another session started the
  turn and is not in the transcript yet; the writing path counts the wait;
- ``pending_peer_final_1459``: it is; the writing path claims the sender's
  receipt and writes the result;
- ``narrow_to_1519``: input from outside (a channel push); the writing path
  narrows the snapshot;
- ``missed_joins_1579``: a message from another session joined the turn
  with no prompt hook; the writing path claims its receipt and narrows;
- ``untrusted_terminal_guard``: a turn typed in the terminal with a relayed
  notification after it: the narrowing must keep ``joined_from_peer``, or
  the turn would read as the terminal's and run unrestricted.

Everything stays under tmp_path (the root conftest redirects the snapshot,
notes and receipt store). No socket, no real claude.
"""

from __future__ import annotations

import importlib.util
import io
import json
import shutil
import sys
from pathlib import Path

import pytest

from aipager import peer_auth
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
R = kit.RECEIVER
LS = ("Bash", {"command": "ls"})
RM = ("Bash", {"command": "rm -rf ~/x"})
WRITE = ("Write", {"file_path": f"{kit.PROJECT}/a.txt", "content": "x"})
READ = ("Read", {"file_path": f"{kit.PROJECT}/a.txt"})
SECRET = ("Read", {"file_path": "~/.claude/settings.json"})
CALLS = [LS, RM, WRITE, READ, SECRET]
LOOP_TEXT = "You are running an autonomous loop. Check what needs doing next..."


@pytest.fixture(autouse=True)
def _runtime_dir():
    ps.snapshot_path(R).parent.mkdir(parents=True, exist_ok=True)


@pytest.fixture
def run_hook(monkeypatch, tmp_path):
    def run(payload: dict, session: str = R) -> str:
        monkeypatch.setattr(notify_hook, "SOCKET_PATH", str(tmp_path / "no-daemon"))
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
        out = io.StringIO()
        monkeypatch.setattr(sys, "stdout", out)
        notify_hook._ERROR_CONTEXT.update(event=None, tool=None)
        notify_hook._run(session, [b"", False, False])
        return out.getvalue()
    return run


def _tx(tmp_path, entries, name="t.jsonl"):
    return kit.write_transcript(tmp_path / name, entries)


def _owner():
    ps.write_merged_snapshot(R, kit.owner_snapshot())


def _fire_entry(text):
    return {"type": "user", "isMeta": True, "turnOrigin": "scheduled",
            "message": {"role": "user", "content": text}}


def _bare_loop(tmp_path, run_hook, *, written):
    _owner()
    sched = _tx(tmp_path, [kit.telegram_entry("/loop")], "sched.jsonl")
    run_hook({"hook_event_name": "PreToolUse", "tool_name": "ScheduleWakeup",
              "tool_input": {"delaySeconds": 900, "prompt": "<<autonomous-loop-dynamic>>",
                             "reason": "idle tick"},
              "transcript_path": sched, "cwd": kit.PROJECT, "session_id": "s1"})
    ps.clear_turn_open(R)
    earlier = [kit.telegram_entry("set up a loop"), kit.tool_result()]
    t = _tx(tmp_path, earlier, "fire.jsonl")
    notify_hook._match_and_promote(R, LOOP_TEXT, transcript_path=t)
    assert "loop_rules" in ps.read_snapshot(R)
    if written:
        t = _tx(tmp_path, [*earlier, _fire_entry(LOOP_TEXT)], "fire.jsonl")
    return t


def _adopt_loop(tmp_path, run_hook):
    return _bare_loop(tmp_path, run_hook, written=True)


def _wait_for_loop(tmp_path, run_hook):
    return _bare_loop(tmp_path, run_hook, written=False)


def _pending_waiting(tmp_path, run_hook):
    _owner()
    notify_hook._match_and_promote(R, kit.envelope_text())
    kit.put_receipt(kit.OWNER_RULES)
    return _tx(tmp_path, [kit.telegram_entry("earlier")])


def _pending_final(tmp_path, run_hook):
    _owner()
    notify_hook._match_and_promote(R, kit.envelope_text())
    kit.put_receipt(kit.OWNER_RULES)
    return _tx(tmp_path, [kit.telegram_entry("earlier"), kit.peer_entry(kit.BODY)])


def _narrow(tmp_path, run_hook):
    _owner()
    channel = {"type": "user", "origin": {"kind": "channel", "server": "x"},
               "message": {"role": "user", "content": "news"}}
    return _tx(tmp_path, [kit.telegram_entry("work"), channel])


def _missed(tmp_path, run_hook):
    _owner()
    ps.mark_verify_origin(R)
    kit.put_receipt(kit.OWNER_RULES)
    return _tx(tmp_path, [kit.telegram_entry("work"), kit.tool_result(), kit.enqueue(kit.BODY)])


def _untrusted_terminal(tmp_path, run_hook):
    _owner()
    relayed = {"type": "user",
               "origin": {"kind": "task-notification", "subkind": "inbox"},
               "message": {"role": "user", "content": "<task-notification>relayed text"}}
    return _tx(tmp_path, [kit.human_entry("typed at the terminal"), relayed])


SITES = {
    "adopt_loop_655": _adopt_loop,
    "wait_for_loop_1771": _wait_for_loop,
    "pending_peer_waiting_1427": _pending_waiting,
    "pending_peer_final_1459": _pending_final,
    "narrow_to_1519": _narrow,
    "missed_joins_1579": _missed,
    "untrusted_terminal_guard": _untrusted_terminal,
}
#: The sites whose read-only answer is the writing path's, call by call
#: (the same rules, decided without the write).
SAME_ANSWER = {"adopt_loop_655", "narrow_to_1519", "untrusted_terminal_guard"}


def _roots() -> list[Path]:
    """Every folder the decision could write: the session files (snapshot,
    lock, verify flag, pins, triggers), the notes and the receipt store."""
    return [ps.snapshot_path(R).parent, ps.notes_dir(R).parent, peer_auth.store_dir()]


def _state() -> dict:
    out = {}
    for base in _roots():
        if not base.exists():
            continue
        for p in sorted(base.rglob("*")):
            out[str(p)] = p.read_bytes() if p.is_file() else "dir"
    return out


def _decide(t, call, *, read_only):
    tool, inp = call
    return enforce.decide(kit.pretool(t, tool, dict(inp)), read_only=read_only)


def _denied(block) -> bool:
    return block is not None


# ---- T1.16: read-only writes nothing ---------------------------------------------------------

@pytest.mark.parametrize("site", list(SITES))
def test_read_only_writes_nothing(tmp_path, run_hook, site):
    t = SITES[site](tmp_path, run_hook)
    ps.lock_path(R).unlink(missing_ok=True)   # setup took the lock; start clean
    before = _state()
    for call in CALLS:
        _decide(t, call, read_only=True)
    assert _state() == before
    assert not ps.lock_path(R).exists()


@pytest.mark.parametrize("site", list(SITES))
def test_read_only_takes_no_lock(tmp_path, run_hook, monkeypatch, site):
    t = SITES[site](tmp_path, run_hook)
    taken = []

    class _NoLock:
        def __init__(self, name, timeout=5.0):
            taken.append(name)

        def __enter__(self):
            raise AssertionError("read-only took the snapshot lock")

        def __exit__(self, *exc):
            return False
    monkeypatch.setattr(ps, "snapshot_lock", _NoLock)
    monkeypatch.setattr(enforce, "snapshot_lock", _NoLock)
    for call in CALLS:
        _decide(t, call, read_only=True)
    assert taken == []


def test_the_writing_path_does_write(tmp_path, run_hook):
    """Guard the guard: each scenario really reaches its writer."""
    for site, setup in SITES.items():
        for base in _roots():
            shutil.rmtree(base, ignore_errors=True)
        ps.snapshot_path(R).parent.mkdir(parents=True, exist_ok=True)
        t = setup(tmp_path, run_hook)
        before = _state()
        for call in CALLS:
            _decide(t, call, read_only=False)
        assert _state() != before, site


# ---- T1.17: never looser ---------------------------------------------------------------------

def _backup(tmp_path) -> Path:
    dest = tmp_path / "state-backup"
    for i, base in enumerate(_roots()):
        if base.exists():
            shutil.copytree(base, dest / str(i))
    return dest


def _restore(backup: Path) -> None:
    for i, base in enumerate(_roots()):
        shutil.rmtree(base, ignore_errors=True)
        if (backup / str(i)).exists():
            shutil.copytree(backup / str(i), base)


@pytest.mark.parametrize("site", list(SITES))
def test_read_only_is_never_looser(tmp_path, run_hook, site):
    t = SITES[site](tmp_path, run_hook)
    backup = _backup(tmp_path)
    read_only = {i: _decide(t, call, read_only=True) for i, call in enumerate(CALLS)}
    writing = {}
    for i, call in enumerate(CALLS):
        _restore(backup)   # each call from the same state
        writing[i] = _decide(t, call, read_only=False)
    looser = [CALLS[i] for i in writing if _denied(writing[i]) and not _denied(read_only[i])]
    assert looser == []
    if site in SAME_ANSWER:
        assert ({i: _denied(b) for i, b in read_only.items()}
                == {i: _denied(b) for i, b in writing.items()})


@pytest.mark.parametrize("site,expected", [
    ("wait_for_loop_1771", "hard"),
    ("pending_peer_waiting_1427", "soft"),
    ("pending_peer_final_1459", "soft"),
    ("missed_joins_1579", "soft"),
], ids=["wait_for_loop_1771", "pending_peer_waiting_1427", "pending_peer_final_1459",
        "missed_joins_1579"])
def test_read_only_answers_where_it_cannot_write(tmp_path, run_hook, site, expected):
    """The answers chosen for each skipped write: a /loop that never waits
    refuses for real; a peer check or a missed join it cannot run refuses
    this call softly, whatever the call."""
    t = SITES[site](tmp_path, run_hook)
    # (Under the floor a /loop waits on, reading the project is allowed.)
    for call in ((LS,) if expected == "hard" else (LS, READ)):
        block = _decide(t, call, read_only=True)
        assert block is not None, call
        assert bool(block.get("soft")) is (expected == "soft"), (call, block)


def test_an_untrusted_terminal_turn_is_never_unrestricted(tmp_path, run_hook):
    t = _untrusted_terminal(tmp_path, run_hook)
    assert _decide(t, RM, read_only=True) is not None
    assert _decide(t, RM, read_only=False) is not None


def test_the_read_only_narrowing_keeps_joined_from_peer(monkeypatch, tmp_path, run_hook):
    _owner()
    before = _state()
    monkeypatch.setattr(enforce, "_cur", {"rules": None, "info": {}, "read_only": True})
    rec = [{"body_sha256": "ab", "status": "unverified", "sender_session": None,
            "guard": True}]
    new = enforce._narrow_to(R, [ps.peer_floor_snapshot()], rec, mark_peer=True)
    assert new["joined_from_peer"] is True and new["verify_origin"] is True
    assert new["joined_peers"][-1] == rec[0]
    assert new["bypass_safety"] is False
    assert _state() == before
    # ... exactly what the locked path writes.
    monkeypatch.setattr(enforce, "_cur", {"rules": None, "info": {}, "read_only": False})
    assert enforce._narrow_to(R, [ps.peer_floor_snapshot()], rec, mark_peer=True) == new
    assert ps.read_snapshot(R) == json.loads(json.dumps(new))


# ---- T1.18: read-only claims no receipt --------------------------------------------------------

@pytest.mark.parametrize("site", list(SITES))
def test_read_only_claims_no_receipt(tmp_path, run_hook, monkeypatch, site):
    t = SITES[site](tmp_path, run_hook)
    claimed = []

    def _claim(*a, **k):
        claimed.append(a)
        raise AssertionError("read-only claimed a receipt")
    monkeypatch.setattr(peer_auth, "claim_receipts", _claim)
    for call in CALLS:
        _decide(t, call, read_only=True)
    assert claimed == []


@pytest.mark.parametrize("site", ["pending_peer_final_1459", "missed_joins_1579"])
def test_the_writing_path_claims_the_receipt(tmp_path, run_hook, monkeypatch, site):
    """Guard the guard: the replaced claim is the one the writing path uses."""
    t = SITES[site](tmp_path, run_hook)
    claimed = []
    monkeypatch.setattr(peer_auth, "claim_receipts",
                        lambda *a, **k: claimed.append(a) or [])
    _decide(t, LS, read_only=False)
    assert claimed


def test_the_default_is_the_writing_path(tmp_path, run_hook):
    t = _adopt_loop(tmp_path, run_hook)
    assert enforce.decide(kit.pretool(t, *LS)) is None
    assert ps.read_snapshot(R)["scheduled"] is True   # adopted, written
    assert enforce._cur["read_only"] is False
