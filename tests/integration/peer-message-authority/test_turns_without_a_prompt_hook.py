"""Messages from another session that reach a turn with no
UserPromptSubmit (absorbed mid-turn, or run as the next turn from Claude
Code's queue), and the marks that get every such turn checked: the owner
short-circuit's tail check, the turn end, the daemon's backstop, the
continuation flag, the pick-up's own mark (C1), the wait for a lazily
written transcript, envelope drift and lock timeouts (review 1)."""

from __future__ import annotations

import fcntl
import importlib.util
import os
import sys
import time
from pathlib import Path

import pytest

from aipager import peer_auth
from aipager import policy_snapshot as ps
from aipager.dtach import enforce, notify_hook


def _kit():
    name = "_peer_kit_pma"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, Path(__file__).resolve().parent / "_peer_kit.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return sys.modules[name]


kit = _kit()
R = kit.RECEIVER
SHA = peer_auth.body_sha256(kit.BODY)
BASH = ("Bash", {"command": "rm -rf ~/x"})
WRITE = ("Write", {"file_path": f"{kit.PROJECT}/a.txt", "content": "x"})
READ = ("Read", {"file_path": f"{kit.PROJECT}/a.txt"})


def _decide(transcript, call, **kw):
    tool, inp = call
    return enforce.decide(kit.pretool(transcript, tool, dict(inp), **kw))


def _owner():
    ps.write_merged_snapshot(R, kit.owner_snapshot())


def _tx(tmp_path, entries, name="t.jsonl"):
    return kit.write_transcript(tmp_path / name, entries)


def _owner_note(text):
    return ps.write_note(R, kit.builtin_role("owner"), None, None,
                         msg_id=11, chat_id=1, sender_key=(1, 1),
                         author_user_id=1, body=f"[via Telegram · @owner]\n{text}",
                         raw_text=text, scope_mode=True)


class _held_lock:
    """Hold the session's snapshot lock as another process would."""

    def __enter__(self):
        self.fd = os.open(ps.lock_path(R), os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        fcntl.flock(self.fd, fcntl.LOCK_UN)
        os.close(self.fd)


# ---- the owner short-circuit sees outside input at the transcript's end --------------

def test_a_peer_message_absorbed_into_an_owner_turn_is_held(tmp_path):
    """No UserPromptSubmit, no daemon: the queue line alone ends the
    owner's shortcut, and the turn is narrowed to the message's rights."""
    _owner()
    t = _tx(tmp_path, [kit.telegram_entry("work"), kit.tool_result(), kit.enqueue(kit.BODY)])
    assert _decide(t, BASH) is not None
    assert ps.read_snapshot(R)["joined_peers"][-1]["missed"] is True


def test_a_peer_message_run_as_the_next_turn_after_an_owner_turn_is_held(tmp_path):
    """Review 1's probe: the owner's turn ended, the queued message ran as
    the next turn with no UserPromptSubmit, the daemon never saw it."""
    _owner()
    t = _tx(tmp_path, [kit.telegram_entry("work"), kit.tool_result(),
                       kit.enqueue(kit.BODY), kit.peer_entry(kit.BODY)])
    assert not ps.verify_origin_flagged(R)
    assert _decide(t, BASH) is not None


def test_an_owner_receipt_still_gives_such_a_turn_the_owner_rights(tmp_path):
    _owner()
    kit.put_receipt(kit.OWNER_RULES)
    t = _tx(tmp_path, [kit.telegram_entry("work"), kit.tool_result(), kit.enqueue(kit.BODY)])
    assert _decide(t, BASH) is None
    assert ps.read_snapshot(R)["joined_peers"][-1]["status"] == "verified"


def _origin_entry(origin):
    return {"type": "user", "origin": origin, "message": {"role": "user", "content": "x"}}


@pytest.mark.parametrize("entry,hit", [
    (kit.peer_entry(kit.BODY), True),
    (kit.enqueue(kit.BODY), True),
    (_origin_entry({"kind": "remote", "callerSessionId": "x"}), True),
    (_origin_entry({"kind": "plugin", "name": "p"}), True),
    (_origin_entry({"kind": "a-kind-added-later"}), True),
    (_origin_entry({"kind": "task-notification", "subkind": "session-inbox"}), True),
    (_origin_entry({"kind": None}), True),
    ({"type": "user", "isMeta": True, "turnOrigin": "scheduled",
      "message": {"role": "user", "content": "x"}}, True),
    (_origin_entry({"from": "uds:x", "kind": "peer"}), True),
    (_origin_entry({"kind": "human"}), False),
    (_origin_entry({"kind": "suggestion"}), False),
    (_origin_entry({"kind": "auto-continuation"}), False),
    (_origin_entry({"kind": "task-notification", "producer": "session-task"}), False),
    (_origin_entry({"kind": "task-notification", "subkind": "scheduled-trigger"}), False),
    # Text that only mentions such entries: quotes inside a string are escaped.
    (kit.tool_result('{"origin":{"kind":"peer"}} <cross-session-message from="x">'), False),
    ({"type": "assistant", "message": {"content": "<cross-session-message from=\"x\">"}}, False),
], ids=["peer", "queued-peer", "remote", "plugin", "unknown-kind", "inbox",
        "kind-not-a-string", "kind-not-first", "a-scheduled-fire",
        "human", "suggestion", "auto-continuation", "session-task", "scheduled",
        "quoted-in-a-result", "said-by-claude"])
def test_the_tail_check_sees_only_outside_input(tmp_path, entry, hit):
    t = _tx(tmp_path, [kit.telegram_entry("x"), entry])
    assert enforce._foreign_input_near_end(t) is hit


def test_the_tail_check_reads_only_the_tail(tmp_path, monkeypatch):
    """What lies before the tail is the turn-end and daemon marks' job."""
    monkeypatch.setattr(enforce, "OWNER_TAIL_BYTES", 4096)
    t = _tx(tmp_path, [kit.enqueue(kit.BODY)] + [kit.tool_result("y" * 400)] * 30)
    assert enforce._foreign_input_near_end(t) is False


# ---- the turn end and the daemon mark what they cannot narrow ----------------------

def test_the_turn_end_marks_a_queued_peer_message(tmp_path, run_hook):
    _owner()
    ps.mark_turn_open(R)
    t = _tx(tmp_path, [kit.telegram_entry("x"), kit.tool_result(), kit.enqueue(kit.BODY)])
    run_hook({"hook_event_name": "Stop", "transcript_path": t, "session_id": "s1"})
    assert ps.verify_origin_flagged(R)


def test_the_turn_end_leaves_a_turn_without_one_unmarked(tmp_path, run_hook):
    _owner()
    ps.mark_turn_open(R)
    t = _tx(tmp_path, [kit.telegram_entry("x"), kit.tool_result()])
    run_hook({"hook_event_name": "Stop", "transcript_path": t, "session_id": "s1"})
    assert not ps.verify_origin_flagged(R)


def test_the_backstop_marks_a_closed_turn_and_leaves_the_receipt(tmp_path):
    _owner()
    kit.put_receipt(kit.OWNER_RULES)
    assert ps.narrow_for_peer_join(R, kit.enqueue(kit.BODY)["content"]) is False
    assert ps.verify_origin_flagged(R)
    assert len(peer_auth.claim_receipts(SHA)) == 1  # the turn's own check needs it


def test_the_daemon_narrows_when_it_reads_the_queue_line(tmp_path):
    """The backstop's call site in the card's queue scan."""
    from aipager.bot import animation
    from aipager.state import Status, TrackedSession
    _owner()
    ps.mark_turn_open(R)
    t = _tx(tmp_path, [kit.telegram_entry("x"), kit.tool_result(), kit.enqueue(kit.BODY)])
    sess = TrackedSession(name=R, label="backend", status=Status.BUSY)
    sess.stream_transcript_path = t
    animation._scan_queue_operations(sess)
    snap = ps.read_snapshot(R)
    assert snap["bypass_safety"] is False
    assert snap["joined_peers"][-1]["missed"] is True


def test_the_daemon_never_waits_for_nor_writes_past_a_held_lock():
    _owner()
    ps.mark_turn_open(R)
    with _held_lock():
        t0 = time.monotonic()
        assert ps.narrow_for_peer_join(R, kit.enqueue(kit.BODY)["content"]) is False
        assert time.monotonic() - t0 < 0.2
    assert ps.read_snapshot(R)["bypass_safety"] is True  # not written
    assert ps.verify_origin_flagged(R)  # the hook's own check takes over


# ---- a message whose body was not recorded ------------------------------------------

def test_a_bodiless_peer_message_after_a_verified_one_is_held(tmp_path):
    """Review 1: a resolved check accounted for any later message without
    a body. Nothing ties such a message to the check."""
    ps.write_merged_snapshot(R, {**kit.owner_snapshot(), "turn_origin": "peer",
                                 "verify_origin": True,
                                 "peer": {"status": "verified", "body_sha256": SHA}})
    t = _tx(tmp_path, [kit.peer_entry(kit.BODY), kit.tool_result(),
                       kit.peer_entry("second", pid=999, with_body=False)])
    assert _decide(t, BASH) is not None
    assert _decide(t, BASH) is not None  # and stays held


# ---- the continuation flag ------------------------------------------------------------

def test_a_continuation_marks_the_job_for_an_origin_check(tmp_path, run_hook, monkeypatch):
    """The flag the continuation sets is what still finds a relay's text
    once it has left the short-circuit's tail."""
    _owner()
    ps.mark_turn_open(R)
    run_hook({"hook_event_name": "UserPromptSubmit", "session_id": "s1",
              "prompt": "<task-notification>\n<task-id>a1</task-id>\n</task-notification>"})
    assert ps.verify_origin_flagged(R)
    assert ps.read_snapshot(R)["bypass_safety"] is True  # the job's rules untouched
    monkeypatch.setattr(enforce, "OWNER_TAIL_BYTES", 2048)
    relay = {"type": "user", "origin": {"kind": "task-notification",
                                        "subkind": "peer-send-message"},
             "message": {"role": "user", "content": "<task-notification>x</task-notification>"}}
    t = _tx(tmp_path, [kit.telegram_entry("job"), relay]
            + [kit.tool_result("y" * 200)] * 20)  # short lines: no long cut line
    assert enforce._foreign_input_near_end(t) is False
    assert _decide(t, BASH) is not None


# ---- the pick-up's own mark (C1) -----------------------------------------------------

def test_the_pick_up_marks_the_session_before_it_starts(monkeypatch):
    """A hook killed mid-way leaves the session checked, never the
    previous owner's snapshot trusted."""
    _owner()

    def killed(*a, **k):
        raise KeyboardInterrupt
    monkeypatch.setattr(ps, "list_outstanding_notes", killed)
    with pytest.raises(KeyboardInterrupt):
        notify_hook._match_and_promote(R, kit.envelope_text())
    assert ps.verify_origin_flagged(R)


def test_an_accounted_join_leaves_the_mark_as_it_was():
    _owner()
    ps.mark_turn_open(R)
    _owner_note("deploy")
    notify_hook._match_and_promote(R, "[via Telegram · @owner]\ndeploy")
    assert not ps.verify_origin_flagged(R)


def test_a_fresh_owner_prompt_clears_the_mark():
    _owner()
    ps.mark_verify_origin(R)
    _owner_note("deploy")
    notify_hook._match_and_promote(R, "[via Telegram · @owner]\ndeploy")
    assert not ps.verify_origin_flagged(R)
    assert ps.read_snapshot(R)["bypass_safety"] is True


# ---- envelope drift ------------------------------------------------------------------

def test_a_changed_wrapper_cannot_consume_an_owner_note_and_stays_marked():
    """Claude Code's envelope no longer parses: notes are matched only
    before the tag, and the turn stays marked for the check."""
    _owner_note("rm the old build")
    drifted = kit.envelope_text("[via Telegram · @owner]\nrm the old build").replace(
        "<cross-session-message ", "<cross-session-message\n", 1)
    assert peer_auth.parse_envelope(drifted) is None
    consumed, _ = notify_hook._match_and_promote(R, drifted)
    assert consumed == []
    assert ps.read_snapshot(R)["bypass_safety"] is False
    assert ps.verify_origin_flagged(R)


# ---- the wait for a lazily written transcript ----------------------------------------

def test_the_check_waits_for_a_message_not_written_yet(tmp_path):
    """Claude Code may not have written the message at the first tool
    call: the call is refused softly (no halt) and the check waits."""
    _owner()
    notify_hook._match_and_promote(R, kit.envelope_text())
    kit.put_receipt(kit.OWNER_RULES)
    early = _tx(tmp_path, [kit.telegram_entry("earlier")], "early.jsonl")
    out = _decide(early, WRITE)
    assert out is not None and out.get("soft") is True
    assert _decide(early, READ) is None  # what the peer floor allows still runs
    assert ps.read_snapshot(R)["peer"]["status"] == "pending"
    later = _tx(tmp_path, [kit.telegram_entry("earlier"), kit.peer_entry(kit.BODY)],
                "later.jsonl")
    assert _decide(later, WRITE) is None
    assert ps.read_snapshot(R)["peer"]["status"] == "verified"


def test_the_check_stops_waiting_after_a_few_calls(tmp_path, monkeypatch):
    monkeypatch.setattr(enforce, "PEER_WAIT_CALLS", 3)
    _owner()
    notify_hook._match_and_promote(R, kit.envelope_text())
    early = _tx(tmp_path, [kit.telegram_entry("earlier")])
    for _ in range(2):
        assert _decide(early, READ) is None
        assert ps.read_snapshot(R)["peer"]["status"] == "pending"
    _decide(early, READ)
    assert ps.read_snapshot(R)["peer"]["status"] == "unverified"


# ---- the pick-up never writes past a held lock ---------------------------------------

def test_a_held_lock_gives_the_pick_up_the_stricter_answer(monkeypatch):
    _owner()
    _owner_note("deploy")
    monkeypatch.setattr(notify_hook, "PICKUP_LOCK_SECONDS", 0.05)
    with _held_lock():
        notify_hook._match_and_promote(R, "[via Telegram · @owner]\ndeploy")
    assert ps.read_snapshot(R)["bypass_safety"] is False
    assert ps.verify_origin_flagged(R)


# ---- review 2: the session's own agents are the job's --------------------------------

def _own_agent_entry(kind, **extra):
    origin = {"kind": kind, "from": "task:a1b2", "senderTaskId": "a1b2", **extra}
    if kind == "peer":
        origin.update(name="worker", body="done: 3 files changed")
    e = {"type": "user", "origin": origin,
         "message": {"role": "user", "content": "done: 3 files changed"}}
    if kind == "observer":
        e["isMeta"] = True
    return e


OWN_AGENT = [_own_agent_entry("peer", handback=True), _own_agent_entry("peer"),
             _own_agent_entry("observer")]
OWN_IDS = ["hand-back", "send-to-main", "observer-report"]


@pytest.mark.parametrize("entry", OWN_AGENT, ids=OWN_IDS)
def test_the_sessions_own_background_agent_keeps_the_owner_job(tmp_path, entry):
    """Review 2: an agent's hand-back to the main thread is recorded as a
    "peer" with ``senderTaskId``; it is the job's own, not another
    session's."""
    _owner()
    t = _tx(tmp_path, [kit.telegram_entry("job"), kit.tool_result(), entry])
    assert enforce._foreign_input_near_end(t) is False
    assert _decide(t, BASH) is None
    assert ps.read_snapshot(R)["bypass_safety"] is True


@pytest.mark.parametrize("entry", OWN_AGENT, ids=OWN_IDS)
def test_an_own_agents_message_in_a_checked_job_keeps_it(tmp_path, entry):
    """The same through the full path: a job already marked (it had a
    continuation) reads the agent's message as its own."""
    _owner()
    ps.mark_verify_origin(R)
    t = _tx(tmp_path, [kit.telegram_entry("job"), kit.tool_result(), entry])
    assert _decide(t, BASH) is None
    assert ps.read_snapshot(R)["bypass_safety"] is True


@pytest.mark.parametrize("sid", ["", 7, None], ids=["empty", "number", "null"])
def test_only_a_real_task_id_makes_a_message_the_jobs_own(tmp_path, sid):
    _owner()
    entry = kit.peer_entry("from elsewhere")
    entry["origin"]["senderTaskId"] = sid
    t = _tx(tmp_path, [kit.telegram_entry("job"), entry])
    assert _decide(t, BASH) is not None


@pytest.mark.parametrize("entry", OWN_AGENT, ids=OWN_IDS)
def test_an_own_agents_message_never_clears_a_block(tmp_path, entry):
    ps.write_merged_snapshot(R, {**ps.resolve_snapshot(kit.builtin_role("user"), None, None),
                                 "turn_origin": "telegram"})
    blocked = kit.tool_result(f"denied by the {enforce._BLOCK_MARKER}: rm")
    t = _tx(tmp_path, [kit.telegram_entry("x"), blocked, entry])
    out = _decide(t, READ)
    assert out is not None and "halted" in out["reason"]


# ---- review 2: a prompt the session scheduled fires with its scheduler's rights ----

PROMPT = "check the deploy and report"


def _schedule(tmp_path, run_hook, tool="CronCreate"):
    t = _tx(tmp_path, [kit.telegram_entry("set up a loop")], "sched.jsonl")
    inp = ({"cron": "*/5 * * * *", "prompt": PROMPT} if tool == "CronCreate"
           else {"delaySeconds": 600, "prompt": PROMPT, "reason": "watching"})
    run_hook({"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": inp,
              "transcript_path": t, "cwd": kit.PROJECT, "session_id": "s1"})


def _fire_entry(text=PROMPT, form="local"):
    """How Claude Code (2.1.296) records the fire: a local cron or /loop
    wakeup is isMeta with no origin and ``turnOrigin: "scheduled"``; a
    routine arrives as a ``scheduled-trigger`` task notification."""
    if form == "local":
        return {"type": "user", "isMeta": True, "turnOrigin": "scheduled",
                "message": {"role": "user", "content": text}}
    return {"type": "user", "turnOrigin": "scheduled",
            "origin": {"kind": "task-notification", "subkind": "scheduled-trigger"},
            "message": {"role": "user", "content": text}}


def _fire(tmp_path, before=None, *, text=PROMPT, written=True, form="local", after=()):
    """The prompt fires while the session is idle: a fresh prompt (its
    pick-up records where the turn starts), then, once Claude Code has
    written it, the fire's own entry."""
    ps.clear_turn_open(R)
    earlier = [before or kit.telegram_entry("set up a loop"), kit.tool_result()]
    t = _tx(tmp_path, earlier, "fire.jsonl")
    notify_hook._match_and_promote(R, text, transcript_path=t)
    if written:
        t = _tx(tmp_path, [*earlier, _fire_entry(text, form), *after], "fire.jsonl")
    elif after:
        t = _tx(tmp_path, [*earlier, *after], "fire.jsonl")
    return t


@pytest.mark.parametrize("tool", ["CronCreate", "ScheduleWakeup"])
def test_an_owners_loop_fires_with_the_owners_rights(tmp_path, run_hook, tool):
    """Review 2: an idle fire got the floor (the previous rights must not
    apply, and nothing said whose loop it was)."""
    _owner()
    _schedule(tmp_path, run_hook, tool)
    assert ps.read_trigger(R, PROMPT)["bypass_safety"] is True
    t = _fire(tmp_path)
    snap = ps.read_snapshot(R)
    assert snap["bypass_safety"] is True and snap["scheduled"] is True
    assert _decide(t, ("Bash", {"command": "git push origin main"})) is None


def test_a_members_loop_fires_with_the_members_rights(tmp_path, run_hook):
    ps.write_merged_snapshot(R, {**ps.resolve_snapshot(kit.builtin_role("user"), None, None),
                                 "turn_origin": "telegram"})
    _schedule(tmp_path, run_hook, "ScheduleWakeup")  # a member may not CronCreate
    assert ps.read_trigger(R, PROMPT)["bypass_safety"] is False
    _owner()  # the owner spoke last: the loop is still the member's
    t = _fire(tmp_path)
    assert ps.read_snapshot(R)["bypass_safety"] is False
    assert _decide(t, ("Bash", {"command": "ls"})) is not None


def test_the_same_prompt_scheduled_twice_keeps_the_stricter_rights(tmp_path, run_hook):
    """A member's loop, then the owner schedules the same text: the
    member's loop must not fire with the owner's rights."""
    ps.write_merged_snapshot(R, {**ps.resolve_snapshot(kit.builtin_role("user"), None, None),
                                 "turn_origin": "telegram"})
    _schedule(tmp_path, run_hook, "ScheduleWakeup")
    assert ps.read_trigger(R, PROMPT)["bypass_safety"] is False
    _owner()
    _schedule(tmp_path, run_hook, "ScheduleWakeup")
    _fire(tmp_path)
    assert ps.read_snapshot(R)["bypass_safety"] is False


def test_a_prompt_nobody_scheduled_still_gets_the_floor(tmp_path):
    _owner()
    t = _fire(tmp_path)
    assert ps.read_snapshot(R)["bypass_safety"] is False
    assert _decide(t, ("Bash", {"command": "ls"})) is not None


CHANNEL = {"type": "user", "origin": {"kind": "channel", "server": "x"},
           "message": {"role": "user", "content": "news"}}


@pytest.mark.parametrize("written", [True, False], ids=["entry-written", "not-yet"])
@pytest.mark.parametrize("before", [kit.peer_entry(kit.BODY), CHANNEL,
                                    kit.human_entry("typed")],
                         ids=["after-peer", "after-channel", "after-terminal"])
def test_an_owners_loop_keeps_its_rights_whatever_came_before(tmp_path, run_hook,
                                                              before, written):
    """Review 3: the previous turn's entries say nothing about the fire's
    turn, written yet or not."""
    _owner()
    _schedule(tmp_path, run_hook)
    t = _fire(tmp_path, before=before, written=written)
    assert _decide(t, ("Bash", {"command": "ls"})) is None


def test_the_routine_form_of_a_fire_is_recognised_too(tmp_path, run_hook):
    _owner()
    _schedule(tmp_path, run_hook)
    t = _fire(tmp_path, before=kit.peer_entry(kit.BODY), form="routine")
    assert _decide(t, ("Bash", {"command": "ls"})) is None


@pytest.mark.parametrize("written", [True, False], ids=["entry-written", "not-yet"])
def test_a_members_loop_after_a_terminal_turn_is_never_unrestricted(tmp_path, run_hook,
                                                                    written):
    """Review 3: the scan read the terminal turn before and allowed all."""
    ps.write_merged_snapshot(R, {**ps.resolve_snapshot(kit.builtin_role("user"), None, None),
                                 "turn_origin": "telegram"})
    _schedule(tmp_path, run_hook, "ScheduleWakeup")
    t = _fire(tmp_path, before=kit.human_entry("typed in the terminal"), written=written)
    assert _decide(t, ("Bash", {"command": "rm -rf ~/x"})) is not None


def test_a_message_from_outside_during_the_fire_still_narrows_it(tmp_path, run_hook):
    """The boundary only hides what came BEFORE the fire's turn."""
    _owner()
    _schedule(tmp_path, run_hook)
    t = _fire(tmp_path, before=kit.peer_entry(kit.BODY), written=False,
              after=[kit.enqueue("joined meanwhile")])
    assert _decide(t, ("Bash", {"command": "ls"})) is not None


def test_previous_joins_are_not_the_fires(tmp_path, run_hook):
    """A queue line of the turn before the fire is not a join of it."""
    _owner()
    _schedule(tmp_path, run_hook)
    t = _fire(tmp_path, before=kit.enqueue("an old, accounted message"))
    assert _decide(t, ("Bash", {"command": "ls"})) is None


# ---- a bare /loop: its fire text is not the scheduled text ------------------------

LOOP_TEXT = "You are running an autonomous loop. Check what needs doing next..."


def _schedule_bare_loop(tmp_path, run_hook):
    t = _tx(tmp_path, [kit.telegram_entry("/loop")], "sched.jsonl")
    run_hook({"hook_event_name": "PreToolUse", "tool_name": "ScheduleWakeup",
              "tool_input": {"delaySeconds": 900, "prompt": "<<autonomous-loop-dynamic>>",
                             "reason": "idle tick"},
              "transcript_path": t, "cwd": kit.PROJECT, "session_id": "s1"})


def test_a_bare_loop_fires_with_its_schedulers_rights(tmp_path, run_hook):
    """Review 3: the fire's text is the loop's instructions, never the
    scheduled placeholder; the fire's entry identifies it."""
    _owner()
    _schedule_bare_loop(tmp_path, run_hook)
    assert ps.read_trigger(R, "<<autonomous-loop>>")["bypass_safety"] is True
    t = _fire(tmp_path, text=LOOP_TEXT)
    assert ps.read_snapshot(R)["bypass_safety"] is False  # unattributed until seen
    assert _decide(t, ("Bash", {"command": "ls"})) is None
    assert ps.read_snapshot(R)["scheduled"] is True


def test_a_bare_loop_waits_softly_for_its_entry(tmp_path, run_hook, monkeypatch):
    monkeypatch.setattr(enforce, "PEER_WAIT_CALLS", 3)
    _owner()
    _schedule_bare_loop(tmp_path, run_hook)
    t = _fire(tmp_path, text=LOOP_TEXT, written=False)
    for _ in range(2):
        out = _decide(t, ("Bash", {"command": "ls"}))
        assert out is not None and out.get("soft") is True
    out = _decide(t, ("Bash", {"command": "ls"}))
    assert out is not None and not out.get("soft")  # no fire yet: the floor, for real
    assert "loop_rules" in ps.read_snapshot(R)  # a fire written later is still adopted
    t = _tx(tmp_path, [kit.telegram_entry("set up a loop"), kit.tool_result(),
                       _fire_entry(LOOP_TEXT)], "fire.jsonl")
    assert _decide(t, ("Bash", {"command": "ls"})) is None


def test_a_terminal_prompt_is_never_mistaken_for_a_loop(tmp_path, run_hook):
    _owner()
    _schedule_bare_loop(tmp_path, run_hook)
    ps.clear_turn_open(R)
    t = _tx(tmp_path, [kit.telegram_entry("x"), kit.tool_result()], "term.jsonl")
    notify_hook._match_and_promote(R, "typed in the terminal", transcript_path=t)
    t = _tx(tmp_path, [kit.telegram_entry("x"), kit.tool_result(),
                       kit.human_entry("typed in the terminal")], "term.jsonl")
    assert _decide(t, ("Bash", {"command": "ls"})) is None


# ---- records last the session and are capped ------------------------------------------

def test_a_record_lasts_as_long_as_the_session(tmp_path, run_hook):
    """Review 8: a record that expired let its fire fall back to a bare
    loop's rights; nothing expires while the session runs."""
    _member()
    _schedule(tmp_path, run_hook, "ScheduleWakeup")
    path = ps.triggers_dir(R) / ps._trigger_name(PROMPT)
    old = time.time() - 365 * 24 * 3600
    os.utime(path, (old, old))
    assert ps.read_trigger(R, PROMPT)["bypass_safety"] is False
    ps.clear_triggers(R)  # the session ended
    assert ps.read_trigger(R, PROMPT) is None


def test_a_full_store_never_pushes_a_record_out(monkeypatch):
    """Review 8: a member scheduling many prompts pushed their own record
    out, and its fire then rode a bare loop's rights."""
    monkeypatch.setattr(ps, "TRIGGER_MAX_COUNT", 3)
    for i in range(3):
        assert ps.write_trigger(R, f"prompt {i}", kit.member_rules())
    assert ps.trigger_slot_free(R, "one more") is False  # scheduling is refused here
    assert ps.trigger_slot_free(R, "prompt 2") is True   # an update fits
    # Calls allowed at the same moment are still recorded, up to a hard limit.
    for i in range(3, 6):
        assert ps.write_trigger(R, f"prompt {i}", kit.member_rules())
    assert ps.write_trigger(R, "one more", dict(kit.OWNER_RULES)) is False
    assert ps.read_trigger(R, "prompt 0") is not None  # nothing pushed out
    assert ps.write_trigger(R, "prompt 1", dict(kit.OWNER_RULES))  # an update fits


def test_scheduling_past_the_limit_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "TRIGGER_MAX_COUNT", 1)
    _owner()
    ps.write_trigger(R, "the one", dict(kit.OWNER_RULES))
    t = _tx(tmp_path, [kit.telegram_entry("x")])
    out = _decide(t, ("ScheduleWakeup", {"delaySeconds": 60, "prompt": "another",
                                          "reason": "r"}))
    assert out is not None and out.get("soft") is True
    assert out["reason"] == peer_auth.SCHEDULE_FULL_REASON
    assert _decide(t, ("ScheduleWakeup", {"delaySeconds": 60, "prompt": "the one",
                                          "reason": "r"})) is None


# ---- review 2: a message larger than the tail ----------------------------------------

def _real_key_order(big):
    """Claude Code's own order (2.1.296): the message first, then the
    origin with its body LAST, so a big body ends the line."""
    return {"type": "user", "isMeta": True,
            "message": {"role": "user", "content": "see the attached message"},
            "origin": {"kind": "peer", "from": "uds:x",
                       "verifiedPeerPid": kit.SENDER_PID, "body": big}}


def _pid_after_body(big):
    """Another order (the pid after a big body): found by the pid key."""
    return {"type": "user", "isMeta": True,
            "message": {"role": "user", "content": "see the attached message"},
            "origin": {"kind": "peer", "from": "uds:x", "body": big,
                       "verifiedPeerPid": kit.SENDER_PID, "name": "x" * 900}}


@pytest.mark.parametrize("form", ["queued", "entry", "real-key-order", "pid-after-body"])
def test_a_message_larger_than_the_tail_is_still_seen(tmp_path, monkeypatch, form):
    monkeypatch.setattr(enforce, "OWNER_TAIL_BYTES", 4096)
    big = "x" * 20_000
    last = {"queued": lambda: kit.enqueue(big), "entry": lambda: kit.peer_entry(big),
            "real-key-order": lambda: _real_key_order(big),
            "pid-after-body": lambda: _pid_after_body(big)}[form]()
    t = _tx(tmp_path, [kit.telegram_entry("work"), kit.tool_result(), last])
    assert enforce._foreign_input_near_end(t) is True
    _owner()
    assert _decide(t, BASH) is not None


# ---- review 2: a hand-off back and forth ----------------------------------------------

def test_a_second_message_in_a_row_waits_for_its_own_entry(tmp_path):
    """The previous turn's message is the last one written when the next
    one's first tool call comes: wait for the right one, do not settle."""
    ps.write_merged_snapshot(R, kit.owner_snapshot())
    notify_hook._match_and_promote(R, kit.envelope_text())
    kit.put_receipt(kit.OWNER_RULES)
    t1 = _tx(tmp_path, [kit.telegram_entry("x"), kit.peer_entry(kit.BODY)], "t1.jsonl")
    assert _decide(t1, WRITE) is None
    ps.clear_turn_open(R)
    second = "and now the second part"
    notify_hook._match_and_promote(R, kit.envelope_text(second))
    kit.put_receipt(kit.OWNER_RULES, body=second)
    out = _decide(t1, WRITE)  # its entry is not written yet
    assert out is not None and out.get("soft") is True
    assert ps.read_snapshot(R)["peer"]["status"] == "pending"
    t2 = _tx(tmp_path, [kit.telegram_entry("x"), kit.peer_entry(kit.BODY),
                        kit.tool_result(), kit.peer_entry(second)], "t2.jsonl")
    assert _decide(t2, WRITE) is None
    assert ps.read_snapshot(R)["peer"]["status"] == "verified"


# ---- review 2: no write and no claim without the lock --------------------------------

def test_a_missed_join_claims_nothing_while_the_lock_is_held(tmp_path, monkeypatch):
    monkeypatch.setattr(ps.snapshot_lock, "__init__",
                        lambda self, name, timeout=5.0: (
                            setattr(self, "session_name", name),
                            setattr(self, "timeout", 0.05),
                            setattr(self, "fd", None), setattr(self, "held", False))[0])
    _owner()
    ps.mark_verify_origin(R)
    kit.put_receipt(kit.OWNER_RULES)
    t = _tx(tmp_path, [kit.telegram_entry("work"), kit.tool_result(), kit.enqueue(kit.BODY)])
    with _held_lock():
        assert _decide(t, BASH) is not None  # this call: the peer floor
    assert ps.read_snapshot(R)["bypass_safety"] is True  # nothing written
    assert len(peer_auth.claim_receipts(SHA)) == 1  # nothing claimed


def test_a_receipt_this_session_wrote_is_never_its_own_proof(tmp_path):
    """exclude_session at the check: a session cannot vouch for a message
    to itself."""
    ps.write_merged_snapshot(R, kit.owner_snapshot())
    notify_hook._match_and_promote(R, kit.envelope_text())
    kit.put_receipt(kit.OWNER_RULES, sender=R)
    t = _tx(tmp_path, [kit.telegram_entry("x"), kit.peer_entry(kit.BODY)])
    assert _decide(t, BASH) is not None
    assert ps.read_snapshot(R)["peer"]["status"] == "unverified"


def test_a_guard_writes_nothing_while_the_lock_is_held(tmp_path, monkeypatch):
    monkeypatch.setattr(ps.snapshot_lock, "__init__",
                        lambda self, name, timeout=5.0: (
                            setattr(self, "session_name", name),
                            setattr(self, "timeout", 0.05),
                            setattr(self, "fd", None), setattr(self, "held", False))[0])
    _owner()
    channel = {"type": "user", "origin": {"kind": "channel", "server": "x"},
               "message": {"role": "user", "content": "news"}}
    t = _tx(tmp_path, [kit.telegram_entry("work"), channel])
    with _held_lock():
        assert _decide(t, BASH) is not None  # this call: the peer floor
    assert ps.read_snapshot(R)["bypass_safety"] is True  # written by no one


def test_a_shared_folder_of_scheduled_prompts_is_never_trusted(tmp_path):
    import json
    d = ps.triggers_dir(R)
    d.mkdir()
    os.chmod(d, 0o777)
    name = ps._trigger_name(PROMPT)
    (d / name).write_text(json.dumps({"rules": dict(kit.OWNER_RULES)}))
    assert ps.read_trigger(R, PROMPT) is None
    assert ps.write_trigger(R, PROMPT, dict(kit.OWNER_RULES)) is False
    _owner()
    _fire(tmp_path)
    assert ps.read_snapshot(R)["bypass_safety"] is False


def test_only_the_fires_own_joins_count(tmp_path, run_hook):
    """A message that joined the turn BEFORE the fire is not the fire's,
    even when one joins the fire too (the boundary alone no longer hides
    the old one then)."""
    _owner()
    _schedule(tmp_path, run_hook)
    kit.put_receipt(kit.OWNER_RULES, body="joined the fire")
    t = _fire(tmp_path, before=kit.enqueue("an old, accounted message"),
              after=[kit.enqueue("joined the fire")])
    assert _decide(t, ("Bash", {"command": "ls"})) is None


def test_a_scheduled_turn_is_never_read_as_the_terminal(tmp_path, run_hook):
    """Not written yet, something queued since it began: the scan reads
    the terminal turn before, and the scheduled snapshot must still rule."""
    ps.write_merged_snapshot(R, {**ps.resolve_snapshot(kit.builtin_role("user"), None, None),
                                 "turn_origin": "telegram"})
    _schedule(tmp_path, run_hook, "ScheduleWakeup")
    attachment = {"type": "attachment", "attachment": {
        "type": "queued_command", "prompt": "x", "origin": {"kind": "unclassified"}}}
    t = _fire(tmp_path, before=kit.human_entry("typed in the terminal"), written=False,
              after=[attachment])
    assert _decide(t, ("Bash", {"command": "rm -rf ~/x"})) is not None


def test_a_telegram_turn_never_waits_for_a_loop(tmp_path, run_hook):
    """A loop record changes nothing for a Telegram message: a refusal
    there is a refusal (no soft wait)."""
    _owner()
    _schedule_bare_loop(tmp_path, run_hook)
    ps.clear_turn_open(R)
    ps.write_note(R, kit.builtin_role("user"), None, None, msg_id=21, chat_id=1,
                  sender_key=(1, 3), author_user_id=3,
                  body="[via Telegram · @bob]\nrun it", raw_text="run it", scope_mode=True)
    t = _tx(tmp_path, [kit.telegram_entry("x"), kit.tool_result()], "tg.jsonl")
    notify_hook._match_and_promote(R, "[via Telegram · @bob]\nrun it", transcript_path=t)
    t = _tx(tmp_path, [kit.telegram_entry("x"), kit.tool_result(),
                       kit.telegram_entry("run it", "@bob")], "tg.jsonl")
    out = _decide(t, ("Bash", {"command": "ls"}))
    assert out is not None and not out.get("soft")


def test_a_message_whose_end_just_reaches_the_tail_is_seen(tmp_path, monkeypatch):
    """Its closing tag on the short cut line is all the tail holds of it."""
    monkeypatch.setattr(enforce, "OWNER_TAIL_BYTES", 4096)
    t = _tx(tmp_path, [kit.telegram_entry("work"), kit.enqueue("x" * 4000)]
            + [kit.tool_result("y" * 200)] * 12)  # 12 x 320 bytes: the tail starts inside it
    with open(t, "rb") as fh:
        data = fh.read()
    cut = data[len(data) - 4096:]
    first = cut[:cut.find(b"\n")]
    assert b"cross-session-message" in first and len(first) < 1024
    assert enforce._foreign_input_near_end(t) is True


def test_a_verified_pid_on_a_short_cut_line_is_seen(tmp_path, monkeypatch):
    """Another key order (the pid after the body): only the pid key of the
    message reaches the tail, on a short cut line with no envelope tag."""
    monkeypatch.setattr(enforce, "OWNER_TAIL_BYTES", 4096)
    entry = {"type": "user", "isMeta": True,
             "message": {"role": "user", "content": "see the attached message"},
             "origin": {"kind": "peer", "from": "uds:x", "body": "x" * 3800,
                        "verifiedPeerPid": kit.SENDER_PID}}
    t = _tx(tmp_path, [kit.telegram_entry("work"), entry]
            + [kit.tool_result("y" * 200)] * 12)
    with open(t, "rb") as fh:
        data = fh.read()
    cut = data[len(data) - 4096:]
    first = cut[:cut.find(b"\n")]
    assert b"verifiedPeerPid" in first and b"cross-session" not in first
    assert len(first) < 1024 and b'"origin"' not in cut
    assert enforce._foreign_input_near_end(t) is True


# ---- review 4: one boundary, the bytes since the turn began --------------------------

def _loop_turn_then(tmp_path, run_hook, text, *, member=False, written):
    """A bare /loop turn ran (its fire is in the transcript), then *text*
    arrives as a fresh prompt no Telegram message accounts for."""
    if member:
        ps.write_merged_snapshot(R, {**ps.resolve_snapshot(kit.builtin_role("user"), None, None),
                                     "turn_origin": "telegram"})
    else:
        _owner()
    _schedule_bare_loop(tmp_path, run_hook)
    loop_turn = [kit.telegram_entry("/loop"), _fire_entry(LOOP_TEXT), kit.tool_result()]
    ps.clear_turn_open(R)
    t = _tx(tmp_path, loop_turn, "next.jsonl")
    notify_hook._match_and_promote(R, text, transcript_path=t)
    return t, loop_turn


@pytest.mark.parametrize("written", [False, True], ids=["not-yet", "entry-written"])
def test_a_channel_push_after_a_loop_turn_never_gets_the_loops_rights(tmp_path, run_hook,
                                                                      written):
    """Review 4 (A): the earlier turn's fire is not this turn's."""
    t, loop_turn = _loop_turn_then(tmp_path, run_hook, "news from a channel", written=written)
    if written:
        t = _tx(tmp_path, [*loop_turn, CHANNEL], "next.jsonl")
    assert _decide(t, BASH) is not None
    assert ps.read_snapshot(R).get("scheduled") is not True


def test_a_terminal_prompt_after_a_members_loop_is_unrestricted(tmp_path, run_hook):
    """Review 4 (B): the owner typing in the terminal after a member's loop
    turn; once its prompt is written it is the terminal's, and before
    that a refusal is only soft (Claude tries again)."""
    t, loop_turn = _loop_turn_then(tmp_path, run_hook, "typed here", member=True,
                                   written=False)
    out = _decide(t, ("Bash", {"command": "ls"}))
    assert out is None or out.get("soft") is True
    t = _tx(tmp_path, [*loop_turn, kit.human_entry("typed here")], "next.jsonl")
    assert _decide(t, ("Bash", {"command": "ls"})) is None


@pytest.mark.parametrize("bare", [False, True], ids=["cron", "bare-loop"])
def test_a_join_queued_before_the_fires_entry_still_narrows(tmp_path, run_hook, bare):
    """Review 4 (C): the fire's own entry is written lazily, after a
    message that joined the turn."""
    _owner()
    if bare:
        _schedule_bare_loop(tmp_path, run_hook)
        text = LOOP_TEXT
    else:
        _schedule(tmp_path, run_hook)
        text = PROMPT
    t = _fire(tmp_path, before=kit.telegram_entry("x"), text=text, written=False,
              after=[kit.enqueue("joined meanwhile"), _fire_entry(text), kit.tool_result()])
    assert _decide(t, BASH) is not None


def test_a_peer_message_run_after_a_fire_with_no_prompt_hook_is_held(tmp_path, run_hook):
    """Review 4 (D): the message then runs as the next turn, its entry not
    written yet; the fire's snapshot is still the one on disk."""
    _owner()
    _schedule(tmp_path, run_hook)
    t = _fire(tmp_path, before=kit.telegram_entry("x"),
              after=[kit.tool_result(), kit.enqueue("run me next")])
    assert _decide(t, BASH) is not None


def _pin_of(tmp_path, run_hook, t):
    run_hook({"hook_event_name": "SubagentStart", "agent_id": "ag9",
              "transcript_path": t, "session_id": "s1"})
    return ps.read_pin(R, "ag9")


@pytest.mark.parametrize("written", [True, False], ids=["entry-written", "not-yet"])
def test_an_agent_of_a_members_loop_is_pinned_to_the_member(tmp_path, run_hook, written):
    """Review 4 (F): the pin read the terminal turn before the fire."""
    ps.write_merged_snapshot(R, {**ps.resolve_snapshot(kit.builtin_role("user"), None, None),
                                 "turn_origin": "telegram"})
    _schedule(tmp_path, run_hook, "ScheduleWakeup")
    t = _fire(tmp_path, before=kit.human_entry("typed"), written=written)
    pin = _pin_of(tmp_path, run_hook, t)
    assert pin["rules"]["bypass_safety"] is False
    assert "Bash" in pin["rules"]["deny_tools"]


def test_an_agent_of_the_owners_cron_after_a_channel_turn_keeps_the_owner(tmp_path,
                                                                          run_hook):
    """Review 4 (G/H): the pin read the channel push before the fire."""
    _owner()
    _schedule(tmp_path, run_hook)
    t = _fire(tmp_path, before=CHANNEL)
    pin = _pin_of(tmp_path, run_hook, t)
    assert pin["rules"]["bypass_safety"] is True


def _shrink(t):
    """The transcript is now shorter than where the turn began (rewritten
    or cut): nothing to read the turn by."""
    snap = ps.read_snapshot(R)
    snap["turn_start_size"] = os.path.getsize(t) + 10_000
    ps.write_merged_snapshot(R, snap)


@pytest.mark.parametrize("written", [True, False], ids=["fire-written", "not-yet"])
def test_a_turn_whose_bytes_cannot_be_read_is_decided_as_any_turn(tmp_path, run_hook,
                                                                   written):
    """Review 4: the ordinary scan decides. The fire's own entry governs
    its turn (review 6); without it, the scan finds the peer message
    before and holds the turn."""
    _owner()
    _schedule(tmp_path, run_hook)
    t = _fire(tmp_path, before=kit.peer_entry(kit.BODY), written=written)
    _shrink(t)
    if written:
        assert _decide(t, BASH) is None
    else:
        assert _decide(t, BASH) is not None


def test_a_long_turn_is_read_by_its_start_and_its_newest_part(tmp_path, run_hook,
                                                               monkeypatch):
    """Review 5: more than the read limit since the start no longer halts
    an owner's loop that followed a peer turn; its newest part is still
    read."""
    _owner()
    _schedule(tmp_path, run_hook)
    filler = [kit.tool_result("y" * 200)] * 40
    t = _fire(tmp_path, before=kit.peer_entry(kit.BODY), after=filler)
    monkeypatch.setattr(enforce, "TURN_READ_MAX", 4096)
    assert os.path.getsize(t) - ps.read_snapshot(R)["turn_start_size"] > 4096
    assert _decide(t, BASH) is None
    t = _fire(tmp_path, before=kit.peer_entry(kit.BODY), after=[*filler, CHANNEL])
    assert _decide(t, BASH) is not None


def test_a_marked_message_no_note_matched_never_waits_for_a_loop(tmp_path, run_hook):
    """Telegram text with no note: the strictest of what waits, refused
    for real, never the loop's soft wait."""
    _owner()
    _schedule_bare_loop(tmp_path, run_hook)
    ps.clear_turn_open(R)
    t = _tx(tmp_path, [kit.telegram_entry("x"), kit.tool_result()], "m.jsonl")
    notify_hook._match_and_promote(R, "[via Telegram · @bob]\nnot a note", transcript_path=t)
    assert "loop_rules" not in ps.read_snapshot(R)
    out = _decide(t, BASH)
    assert out is not None and not out.get("soft")


def test_outside_input_during_an_owners_cron_turn_narrows_it(tmp_path, run_hook):
    _owner()
    _schedule(tmp_path, run_hook)
    t = _fire(tmp_path, after=[kit.tool_result(), CHANNEL])
    assert _decide(t, BASH) is not None


def _blocked_result():
    return kit.tool_result(f"denied by the {enforce._BLOCK_MARKER}: rm")


def test_a_block_of_the_turn_before_never_halts_a_fire(tmp_path, run_hook):
    ps.write_merged_snapshot(R, {**ps.resolve_snapshot(kit.builtin_role("user"), None, None),
                                 "turn_origin": "telegram"})
    _schedule(tmp_path, run_hook, "ScheduleWakeup")
    t = _fire(tmp_path, before=_blocked_result())  # the turn before ended blocked
    assert _decide(t, READ) is None


def test_a_block_in_the_fires_own_turn_halts_it(tmp_path, run_hook):
    ps.write_merged_snapshot(R, {**ps.resolve_snapshot(kit.builtin_role("user"), None, None),
                                 "turn_origin": "telegram"})
    _schedule(tmp_path, run_hook, "ScheduleWakeup")
    t = _fire(tmp_path, after=[_blocked_result()])
    out = _decide(t, READ)
    assert out is not None and "halted" in out["reason"]


def test_an_agent_of_a_bare_loop_fire_gets_the_loops_rights(tmp_path, run_hook):
    _owner()
    _schedule_bare_loop(tmp_path, run_hook)
    t = _fire(tmp_path, text=LOOP_TEXT)
    assert _pin_of(tmp_path, run_hook, t)["rules"]["bypass_safety"] is True


def test_an_agent_launched_after_outside_input_in_a_cron_turn_is_held(tmp_path, run_hook):
    _owner()
    _schedule(tmp_path, run_hook)
    t = _fire(tmp_path, after=[kit.tool_result(), CHANNEL])
    pin = _pin_of(tmp_path, run_hook, t)
    assert pin["rules"]["bypass_safety"] is False and "WebFetch" in pin["rules"]["deny_tools"]


def test_an_own_agents_hand_back_in_a_cron_turn_keeps_it(tmp_path, run_hook):
    _owner()
    _schedule(tmp_path, run_hook)
    t = _fire(tmp_path, after=[kit.tool_result(), _own_agent_entry("peer", handback=True)])
    assert _decide(t, BASH) is None


@pytest.mark.parametrize("written", [True, False], ids=["fire-written", "not-yet"])
def test_a_members_cron_with_an_unreadable_turn_is_never_the_terminals(tmp_path, run_hook,
                                                                        written):
    """The region cannot be read: the ordinary scan finds the fire, or,
    before it is written, the terminal turn before it, and the scheduled
    snapshot must still rule."""
    ps.write_merged_snapshot(R, {**ps.resolve_snapshot(kit.builtin_role("user"), None, None),
                                 "turn_origin": "telegram"})
    _schedule(tmp_path, run_hook, "ScheduleWakeup")
    t = _fire(tmp_path, before=kit.human_entry("typed in the terminal"), written=written)
    _shrink(t)
    assert _decide(t, ("Bash", {"command": "rm -rf ~/x"})) is not None


def test_a_telegram_turn_never_waits_for_a_loop_before_its_entry(tmp_path, run_hook):
    _owner()
    _schedule_bare_loop(tmp_path, run_hook)
    ps.clear_turn_open(R)
    ps.write_note(R, kit.builtin_role("user"), None, None, msg_id=22, chat_id=1,
                  sender_key=(1, 3), author_user_id=3,
                  body="[via Telegram · @bob]\nrun it", raw_text="run it", scope_mode=True)
    t = _tx(tmp_path, [kit.telegram_entry("x"), kit.tool_result()], "tg2.jsonl")
    notify_hook._match_and_promote(R, "[via Telegram · @bob]\nrun it", transcript_path=t)
    out = _decide(t, ("Bash", {"command": "ls"}))  # its entry not written yet
    assert out is not None and not out.get("soft")


# ---- review 5: a message joining a running scheduled turn ---------------------------

MEMBER_ROLE = "user"


def _member():
    ps.write_merged_snapshot(R, {**ps.resolve_snapshot(kit.builtin_role(MEMBER_ROLE), None, None),
                                 "turn_origin": "telegram"})


def _setup(tmp_path, run_hook, who, kind):
    (_owner if who == "owner" else _member)()
    if kind == "cron":
        _schedule(tmp_path, run_hook, "ScheduleWakeup")
        return PROMPT
    _schedule_bare_loop(tmp_path, run_hook)
    return LOOP_TEXT


def _join(t, entries, how):
    """A message joins the running turn: its pick-up, then its entry."""
    if how == "owner-telegram":
        ps.write_note(R, kit.builtin_role("owner"), None, None, msg_id=31, chat_id=1,
                      sender_key=(1, 1), author_user_id=1,
                      body="[via Telegram · @owner]\nalso this", raw_text="also this",
                      scope_mode=True)
        notify_hook._match_and_promote(R, "[via Telegram · @owner]\nalso this",
                                       transcript_path=t)
        return [*entries, kit.telegram_entry("also this")]
    if how == "member-telegram":
        ps.write_note(R, kit.builtin_role(MEMBER_ROLE), None, None, msg_id=32, chat_id=1,
                      sender_key=(1, 3), author_user_id=3,
                      body="[via Telegram · @bob]\nalso this", raw_text="also this",
                      scope_mode=True)
        notify_hook._match_and_promote(R, "[via Telegram · @bob]\nalso this",
                                       transcript_path=t)
        return [*entries, kit.telegram_entry("also this", "@bob")]
    notify_hook._match_and_promote(R, "typed now", transcript_path=t)
    return [*entries, kit.human_entry("typed now")]


@pytest.mark.parametrize("kind", ["cron", "bare-loop"])
@pytest.mark.parametrize("how", ["owner-telegram", "terminal"])
def test_a_join_never_widens_a_members_scheduled_turn(tmp_path, run_hook, kind, how):
    """Review 5 (S9, S3m): the join read the turn as pure terminal and the
    joiner's rights replaced the member loop's."""
    text = _setup(tmp_path, run_hook, "member", kind)
    before = kit.human_entry("typed") if how == "terminal" else kit.telegram_entry("x", "@bob")
    t = _fire(tmp_path, before=before, text=text)
    entries = [before, kit.tool_result(), _fire_entry(text)]
    assert _decide(t, ("Bash", {"command": "rm -rf ~/x"})) is not None
    t = _tx(tmp_path, _join(t, entries, how), "fire.jsonl")
    assert _decide(t, ("Bash", {"command": "rm -rf ~/x"})) is not None


@pytest.mark.parametrize("kind", ["cron", "bare-loop"])
@pytest.mark.parametrize("how", ["owner-telegram", "terminal"])
def test_the_owner_joining_their_own_loop_after_a_peer_turn_keeps_it(tmp_path, run_hook,
                                                                     kind, how):
    """Review 5 (S3, S3t): the join dropped the scheduled state and the
    scan found the peer turn before the fire."""
    text = _setup(tmp_path, run_hook, "owner", kind)
    t = _fire(tmp_path, before=kit.peer_entry(kit.BODY), text=text)
    entries = [kit.peer_entry(kit.BODY), kit.tool_result(), _fire_entry(text)]
    assert _decide(t, ("Bash", {"command": "ls"})) is None
    t = _tx(tmp_path, _join(t, entries, how), "fire.jsonl")
    assert _decide(t, ("Bash", {"command": "ls"})) is None


def test_a_member_joining_before_a_bare_loop_is_adopted_narrows_it(tmp_path, run_hook):
    """The loop's rights, once adopted, are merged with every message that
    joined before."""
    _setup(tmp_path, run_hook, "owner", "bare-loop")
    t = _fire(tmp_path, text=LOOP_TEXT, written=False)
    entries = [kit.telegram_entry("set up a loop"), kit.tool_result()]
    entries = _join(t, entries, "member-telegram")
    t = _tx(tmp_path, [*entries, _fire_entry(LOOP_TEXT)], "fire.jsonl")
    assert _decide(t, ("Bash", {"command": "ls"})) is not None
    assert ps.read_snapshot(R)["scheduled"] is True


def test_the_operator_typing_before_a_bare_loop_is_adopted_adds_no_limit(tmp_path,
                                                                       run_hook):
    """The operator types into the loop's turn before a tool call adopted
    it (the fire's entry first, then the typed one): no limit added."""
    _setup(tmp_path, run_hook, "owner", "bare-loop")
    t = _fire(tmp_path, text=LOOP_TEXT, written=False)
    earlier = [kit.telegram_entry("set up a loop"), kit.tool_result()]
    _join(t, earlier, "terminal")
    t = _tx(tmp_path, [*earlier, _fire_entry(LOOP_TEXT), kit.human_entry("typed now")],
            "fire.jsonl")
    assert _decide(t, ("Bash", {"command": "ls"})) is None


def test_a_scheduled_turn_holds_other_senders_like_a_terminal_turn():
    from aipager.state import TURN_SENDER_TERMINAL, turn_sender_from_report
    assert turn_sender_from_report({"origin": "scheduled", "authors": []}) == TURN_SENDER_TERMINAL


# ---- review 5: the rest -------------------------------------------------------------

@pytest.mark.parametrize("written", [True, False], ids=["fire-written", "not-yet"])
def test_an_unreadable_bare_loop_turn_is_never_the_terminals(tmp_path, run_hook, written):
    """Review 5 (002): a member's bare loop whose turn cannot be read is
    never unrestricted."""
    _setup(tmp_path, run_hook, "member", "bare-loop")
    t = _fire(tmp_path, before=kit.human_entry("typed"), text=LOOP_TEXT, written=written)
    _shrink(t)
    assert _decide(t, ("Bash", {"command": "rm -rf ~/x"})) is not None


def test_a_relayed_notification_in_a_cron_turn_narrows_it(tmp_path, run_hook):
    _owner()
    _schedule(tmp_path, run_hook)
    relay = {"type": "user", "origin": {"kind": "task-notification",
                                        "subkind": "session-inbox"},
             "message": {"role": "user", "content": "<task-notification>x</task-notification>"}}
    t = _fire(tmp_path, after=[kit.tool_result(), relay])
    assert _decide(t, BASH) is not None


def test_a_delivered_peer_message_with_no_queue_line_narrows_a_cron_turn(tmp_path,
                                                                        run_hook):
    _owner()
    _schedule(tmp_path, run_hook)
    t = _fire(tmp_path, after=[kit.tool_result(), kit.peer_entry("delivered")])
    assert _decide(t, BASH) is not None


@pytest.mark.parametrize("written", [True, False], ids=["fire-written", "not-yet"])
def test_an_agent_pin_in_an_unreadable_members_cron_turn_is_never_the_terminals(
        tmp_path, run_hook, written):
    _member()
    _schedule(tmp_path, run_hook, "ScheduleWakeup")
    t = _fire(tmp_path, before=kit.human_entry("typed"), written=written)
    _shrink(t)
    assert _pin_of(tmp_path, run_hook, t)["rules"]["bypass_safety"] is False


def test_a_bare_loops_routine_form_fire_is_adopted(tmp_path, run_hook):
    _owner()
    _schedule_bare_loop(tmp_path, run_hook)
    t = _fire(tmp_path, text=LOOP_TEXT, form="routine")
    assert enforce._turn_region(t, ps.read_snapshot(R)["turn_start_size"])["fire"] is True
    assert _decide(t, ("Bash", {"command": "ls"})) is None


def test_an_owner_joining_a_members_unadopted_loop_never_widens_it(tmp_path, run_hook):
    """Before the fire's entry is written the turn reads as typed in the
    terminal; a join must not hand it the joiner's rights."""
    _setup(tmp_path, run_hook, "member", "bare-loop")
    t = _fire(tmp_path, text=LOOP_TEXT, written=False)
    _join(t, [kit.telegram_entry("set up a loop"), kit.tool_result()], "owner-telegram")
    assert _decide(t, ("Bash", {"command": "rm -rf ~/x"})) is not None


def test_a_join_proven_before_the_loop_is_adopted_still_counts_after(tmp_path, run_hook):
    _setup(tmp_path, run_hook, "owner", "bare-loop")
    kit.put_receipt(kit.member_rules(), body="joined early")
    entries = [kit.telegram_entry("set up a loop"), kit.tool_result()]
    t = _fire(tmp_path, text=LOOP_TEXT, written=False, after=[kit.enqueue("joined early")])
    _decide(t, ("Bash", {"command": "ls"}))  # the join is narrowed and accounted
    t = _tx(tmp_path, [*entries, kit.enqueue("joined early"), _fire_entry(LOOP_TEXT)],
            "fire.jsonl")
    assert _decide(t, ("Bash", {"command": "ls"})) is not None


def test_a_narrowed_scheduled_turn_is_still_read_from_its_own_bytes(tmp_path, run_hook):
    """A narrowing keeps the scheduled state: the block of the turn before
    still says nothing about this one."""
    _member()
    _schedule(tmp_path, run_hook, "ScheduleWakeup")
    t = _fire(tmp_path, before=_blocked_result(), after=[kit.tool_result(), CHANNEL])
    assert _decide(t, READ) is None  # narrowed to the peer floor, reads still run
    assert ps.read_snapshot(R)["scheduled"] is True
    assert _decide(t, READ) is None


@pytest.mark.parametrize("written", [True, False], ids=["fire-written", "not-yet"])
def test_an_agent_pin_in_an_unreadable_members_bare_loop_is_never_the_terminals(
        tmp_path, run_hook, written):
    _setup(tmp_path, run_hook, "member", "bare-loop")
    t = _fire(tmp_path, before=kit.human_entry("typed"), text=LOOP_TEXT, written=written)
    _shrink(t)
    assert _pin_of(tmp_path, run_hook, t)["rules"]["bypass_safety"] is False


@pytest.mark.parametrize("how", ["owner-telegram", "terminal"])
def test_the_owner_joining_their_loop_keeps_it_before_the_join_is_written(tmp_path,
                                                                          run_hook, how):
    """The join's own entry not written yet: the scan would read past the
    fire to the peer turn before, unless the turn is still read from its
    own bytes."""
    _setup(tmp_path, run_hook, "owner", "cron")
    t = _fire(tmp_path, before=kit.peer_entry(kit.BODY))
    _join(t, [kit.peer_entry(kit.BODY), kit.tool_result(), _fire_entry()], how)
    assert _decide(t, ("Bash", {"command": "ls"})) is None


# ---- review 6 ---------------------------------------------------------------------------

def test_a_join_never_moves_where_the_turn_began(tmp_path, run_hook):
    """Review 6 (Z3): outside input absorbed before a join must stay inside
    the turn's bytes."""
    _setup(tmp_path, run_hook, "owner", "cron")
    t = _fire(tmp_path, after=[kit.tool_result(), CHANNEL])
    start = ps.read_snapshot(R)["turn_start_size"]
    entries = [kit.telegram_entry("set up a loop"), kit.tool_result(), _fire_entry(),
               kit.tool_result(), CHANNEL]
    t = _tx(tmp_path, _join(t, entries, "owner-telegram"), "fire.jsonl")
    assert ps.read_snapshot(R)["turn_start_size"] == start
    assert _decide(t, BASH) is not None


def test_the_owner_joining_before_their_loop_is_adopted_still_adopts_it(tmp_path, run_hook):
    """Review 6 (Z2): the fire was written, the owner's message joined
    before any tool call adopted the loop."""
    _setup(tmp_path, run_hook, "owner", "bare-loop")
    t = _fire(tmp_path, text=LOOP_TEXT)
    entries = [kit.telegram_entry("set up a loop"), kit.tool_result(), _fire_entry(LOOP_TEXT)]
    t = _tx(tmp_path, _join(t, entries, "owner-telegram"), "fire.jsonl")
    assert _decide(t, ("Bash", {"command": "ls"})) is None


def test_a_fire_with_no_record_after_a_terminal_turn_gets_the_floor(tmp_path, run_hook):
    """Review 6 (X1): it read as the terminal turn before it."""
    _member()
    t = _fire(tmp_path, before=kit.human_entry("typed in the terminal"))
    assert _decide(t, ("Bash", {"command": "rm -rf ~/x"})) is not None


def test_a_fire_run_with_no_prompt_hook_gets_its_records_rights(tmp_path, run_hook):
    """A fire Claude Code ran straight from its queue (no UserPromptSubmit):
    the snapshot on disk is the turn before's; the fire's record decides."""
    _member()
    _schedule(tmp_path, run_hook, "ScheduleWakeup")
    _owner()  # the owner's turn ran last
    t = _tx(tmp_path, [kit.telegram_entry("owner work"), kit.tool_result(), _fire_entry()],
            "queued.jsonl")
    assert _decide(t, ("Bash", {"command": "rm -rf ~/x"})) is not None
    _owner()
    _schedule(tmp_path, run_hook, "CronCreate")  # now an owner-recorded prompt too
    # The turn before was typed in the terminal (its snapshot is only a
    # placeholder; its pick-up recorded where it began): the fire Claude
    # Code ran right after it gets exactly its record.
    ps.write_merged_snapshot(R, {**ps.floor_snapshot(), "turn_origin": "terminal",
                                 "turn_start_size": 0})
    other = "another owner prompt"
    ps.write_trigger(R, other, dict(kit.OWNER_RULES))
    t = _tx(tmp_path, [kit.human_entry("typed"), kit.tool_result(), _fire_entry(other)],
            "queued2.jsonl")
    assert _decide(t, ("Bash", {"command": "ls"})) is None


def test_background_agents_never_spend_the_loops_wait(tmp_path, run_hook, monkeypatch):
    """Review 6 (W3o): an earlier job's agent used up the wait, and the
    owner's loop fired at the floor."""
    monkeypatch.setattr(enforce, "PEER_WAIT_CALLS", 3)
    _setup(tmp_path, run_hook, "owner", "bare-loop")
    t = _fire(tmp_path, text=LOOP_TEXT, written=False)
    for _ in range(5):
        _decide(t, READ, agent_id="agent7")
    out = _decide(t, ("Bash", {"command": "ls"}))
    assert out is not None and out.get("soft") is True


def test_a_busy_lock_at_adoption_refuses_softly_with_the_loops_rights(tmp_path, run_hook,
                                                                      monkeypatch):
    """Review 6 (005): the in-memory rights merge the joins, and a refusal
    is soft until the adoption is written."""
    _setup(tmp_path, run_hook, "owner", "bare-loop")
    t = _fire(tmp_path, text=LOOP_TEXT, written=False)
    entries = _join(t, [kit.telegram_entry("set up a loop"), kit.tool_result()],
                    "member-telegram")
    t = _tx(tmp_path, [*entries, _fire_entry(LOOP_TEXT)], "fire.jsonl")
    monkeypatch.setattr(enforce, "adopt_loop_rules", lambda s: None)
    out = _decide(t, ("Bash", {"command": "ls"}))
    assert out is not None and out.get("soft") is True  # the member's limit, softly
    assert _decide(t, READ) is None


def test_a_pin_in_an_unadopted_loop_merges_the_joins(tmp_path, run_hook):
    _setup(tmp_path, run_hook, "owner", "bare-loop")
    t = _fire(tmp_path, text=LOOP_TEXT, written=False)
    entries = _join(t, [kit.telegram_entry("set up a loop"), kit.tool_result()],
                    "member-telegram")
    t = _tx(tmp_path, [*entries, _fire_entry(LOOP_TEXT)], "fire.jsonl")
    assert _pin_of(tmp_path, run_hook, t)["rules"]["bypass_safety"] is False


def test_a_reload_narrows_a_scheduled_prompt_to_its_authors_new_rights(tmp_path):
    """Review 6 (004): a demoted member's loop keeps firing with the old
    rights otherwise."""
    ps.write_trigger(R, PROMPT, kit.admin_rules(), authors=[3])
    ps.write_trigger(R, "the operator's own", dict(kit.OWNER_RULES), authors=[])
    ro = [ps.safety_rules(ps.resolve_snapshot(kit.builtin_role("read_only"), None, None))]
    assert ps.narrow_triggers(R, lambda uid: ro if uid == 3 else None) == 1
    assert "Bash" in ps.read_trigger(R, PROMPT)["deny_tools"]
    assert ps.read_trigger(R, "the operator's own")["bypass_safety"] is True


def test_the_scheduler_is_recorded_from_the_turns_senders(tmp_path, run_hook):
    ps.write_note(R, kit.builtin_role(MEMBER_ROLE), None, None, msg_id=41, chat_id=1,
                  sender_key=(1, 3), author_user_id=3,
                  body="[via Telegram · @bob]\nloop it", raw_text="loop it", scope_mode=True)
    notify_hook._match_and_promote(R, "[via Telegram · @bob]\nloop it")
    _schedule(tmp_path, run_hook, "ScheduleWakeup")
    rec = ps._read_trigger_record(ps.triggers_dir(R), ps._trigger_name(PROMPT))
    assert rec["authors"] == [3]


def test_live_reload_narrows_the_sessions_scheduled_prompts(tmp_path, monkeypatch):
    from aipager.bot import live_reload
    from aipager.state import Status, TrackedSession
    ps.write_trigger(R, PROMPT, kit.admin_rules(), authors=[3])
    ro = [ps.safety_rules(ps.resolve_snapshot(kit.builtin_role("read_only"), None, None))]
    monkeypatch.setattr(live_reload, "narrowed_rules",
                        lambda *a: ro if a[-1] == 3 else None)
    sess = TrackedSession(name=R, label="backend", status=Status.IDLE)
    live_reload._narrow_turn(sess, -5, None, None, None, None)
    assert "Bash" in ps.read_trigger(R, PROMPT)["deny_tools"]


def test_an_agent_of_a_queued_fire_gets_the_fires_rights(tmp_path, run_hook):
    _member()
    _schedule(tmp_path, run_hook, "ScheduleWakeup")
    _owner()  # the owner's turn ran last
    t = _tx(tmp_path, [kit.telegram_entry("owner work"), kit.tool_result(), _fire_entry()],
            "queued.jsonl")
    assert _pin_of(tmp_path, run_hook, t)["rules"]["bypass_safety"] is False


def test_a_busy_lock_at_adoption_decides_by_the_loops_rights_not_the_disk(tmp_path, run_hook,
                                                                          monkeypatch):
    """The adoption could not be written: this call still runs with the
    loop's rights merged with the joins (owner and admin: Bash allowed),
    never with the unadopted floor on disk."""
    _setup(tmp_path, run_hook, "owner", "bare-loop")
    t = _fire(tmp_path, text=LOOP_TEXT, written=False)
    ps.write_note(R, kit.builtin_role("admin"), None, None, msg_id=51, chat_id=1,
                  sender_key=(1, 4), author_user_id=4,
                  body="[via Telegram · @ann]\nalso this", raw_text="also this",
                  scope_mode=True)
    notify_hook._match_and_promote(R, "[via Telegram · @ann]\nalso this", transcript_path=t)
    t = _tx(tmp_path, [kit.telegram_entry("set up a loop"), kit.tool_result(),
                       kit.telegram_entry("also this", "@ann"), _fire_entry(LOOP_TEXT)],
            "fire.jsonl")
    monkeypatch.setattr(enforce, "adopt_loop_rules", lambda s: None)
    assert _decide(t, ("Bash", {"command": "ls"})) is None


def test_the_owner_joining_their_adopted_loop_keeps_it(tmp_path, run_hook):
    """A bare loop has no record for its text: once adopted, only the
    carried scheduled state keeps its rights through a join."""
    _setup(tmp_path, run_hook, "owner", "bare-loop")
    t = _fire(tmp_path, text=LOOP_TEXT)
    assert _decide(t, ("Bash", {"command": "ls"})) is None  # adopted
    entries = [kit.telegram_entry("set up a loop"), kit.tool_result(), _fire_entry(LOOP_TEXT)]
    _join(t, entries, "owner-telegram")  # its entry not written yet
    assert _decide(t, ("Bash", {"command": "ls"})) is None


def test_a_long_bare_loop_turn_is_still_adopted(tmp_path, run_hook, monkeypatch):
    """Past the read limit the fire is still found at the turn's start."""
    _setup(tmp_path, run_hook, "owner", "bare-loop")
    filler = [kit.tool_result("y" * 200)] * 40
    t = _fire(tmp_path, text=LOOP_TEXT, after=filler)
    monkeypatch.setattr(enforce, "TURN_READ_MAX", 4096)
    assert _decide(t, ("Bash", {"command": "ls"})) is None



# ---- review 7: a fire's rights are never wider than the snapshot on disk ----------

def _after_owner_cron(tmp_path, run_hook):
    """The owner's cron turn ran (its fire is the newest prompt written)."""
    _owner()
    _schedule(tmp_path, run_hook)
    t = _fire(tmp_path)
    assert _decide(t, ("Bash", {"command": "ls"})) is None
    ps.clear_turn_open(R)
    return t


def test_a_members_turn_after_the_owners_cron_never_gets_the_owners_rights(tmp_path,
                                                                           run_hook):
    """Review 7 (PA): the member's prompt is not written yet, so the owner's
    fire is still the newest prompt in the transcript."""
    t = _after_owner_cron(tmp_path, run_hook)
    ps.write_note(R, kit.builtin_role(MEMBER_ROLE), None, None, msg_id=61, chat_id=1,
                  sender_key=(1, 3), author_user_id=3,
                  body="[via Telegram · @bob]\nclean up", raw_text="clean up",
                  scope_mode=True)
    notify_hook._match_and_promote(R, "[via Telegram · @bob]\nclean up", transcript_path=t)
    assert _decide(t, ("Bash", {"command": "rm -rf ~/x"})) is not None
    assert _pin_of(tmp_path, run_hook, t)["rules"]["bypass_safety"] is False


@pytest.mark.parametrize("prompt", ["peer", "channel", "unattributed"])
def test_nothing_after_the_owners_cron_rides_its_rights(tmp_path, run_hook, prompt):
    """Review 7 (PA2): an unproven message from another session (its check
    must still run), a channel push, unattributed text."""
    t = _after_owner_cron(tmp_path, run_hook)
    text = {"peer": kit.envelope_text(), "channel": '<channel source="x">news</channel>',
            "unattributed": "do the thing"}[prompt]
    notify_hook._match_and_promote(R, text, transcript_path=t)
    for _ in range(3):
        assert _decide(t, ("Bash", {"command": "rm -rf ~/x"})) is not None


@pytest.mark.parametrize("join", ["attachment", "not-written"])
def test_a_join_into_a_fire_run_with_no_prompt_hook_still_narrows_it(tmp_path, run_hook, join):
    """Review 7 (PB): the fire ran straight from Claude Code's queue; a
    member's message joined (its pick-up narrowed the snapshot) and shows
    in the transcript only as an attachment, or not yet."""
    _owner()
    _schedule(tmp_path, run_hook)
    _owner()  # the owner's turn before; the fire runs with no prompt hook
    ps.mark_turn_open(R)
    entries = [kit.telegram_entry("owner work"), kit.tool_result(), _fire_entry()]
    t = _tx(tmp_path, entries, "queued.jsonl")
    _join(t, entries, "member-telegram")
    if join == "attachment":
        entries = [*entries, {"type": "attachment", "attachment": {
            "type": "queued_command", "prompt": "[via Telegram · @bob]\nalso this"}}]
    t = _tx(tmp_path, entries, "queued.jsonl")
    assert _decide(t, ("Bash", {"command": "rm -rf ~/x"})) is not None


def test_a_fire_run_with_no_prompt_hook_keeps_a_narrowing_on_disk(tmp_path, run_hook):
    """Review 7 (PC)."""
    _owner()
    _schedule(tmp_path, run_hook)
    ps.write_merged_snapshot(R, {**ps.narrowed_snapshot(kit.owner_snapshot(),
                                                        [ps.peer_floor_snapshot()]),
                                 "turn_origin": "telegram"})
    t = _tx(tmp_path, [kit.telegram_entry("owner work"), kit.tool_result(), _fire_entry()],
            "queued.jsonl")
    assert _decide(t, ("Bash", {"command": "rm -rf ~/x"})) is not None


def test_a_queued_routine_fire_governs_its_turn(tmp_path, run_hook):
    """The routine form, run with no prompt hook after a terminal turn:
    its record decides, never the terminal turn before."""
    _member()
    _schedule(tmp_path, run_hook, "ScheduleWakeup")
    ps.write_merged_snapshot(R, {**ps.floor_snapshot(), "turn_origin": "terminal",
                                 "turn_start_size": 0})
    t = _tx(tmp_path, [kit.human_entry("typed"), kit.tool_result(),
                       _fire_entry(PROMPT, "routine")], "queued.jsonl")
    assert _decide(t, ("Bash", {"command": "rm -rf ~/x"})) is not None


def _owner_record(text="owner's own cron"):
    ps.write_trigger(R, text, dict(kit.OWNER_RULES))
    return text


def test_a_terminal_turns_start_is_recorded_for_a_fire_queued_after_it(tmp_path):
    """Through the pick-up: the terminal turn records where it began, so a
    fire Claude Code runs right after it gets exactly its record."""
    text = _owner_record()
    t = _tx(tmp_path, [kit.telegram_entry("earlier"), kit.tool_result()], "q.jsonl")
    notify_hook._match_and_promote(R, "typed in the terminal", transcript_path=t)
    t = _tx(tmp_path, [kit.telegram_entry("earlier"), kit.tool_result(),
                       kit.human_entry("typed in the terminal"), kit.tool_result(),
                       _fire_entry(text)], "q.jsonl")
    assert _decide(t, ("Bash", {"command": "ls"})) is None


def test_a_terminal_turn_that_took_in_outside_input_is_no_placeholder(tmp_path):
    text = _owner_record()
    ps.write_merged_snapshot(R, {**ps.floor_snapshot(), "turn_origin": "terminal",
                                 "turn_start_size": 0})
    t = _tx(tmp_path, [kit.human_entry("typed"), CHANNEL, kit.tool_result(),
                       _fire_entry(text)], "q.jsonl")
    assert _decide(t, ("Bash", {"command": "ls"})) is not None


def test_a_terminal_turn_a_member_joined_is_no_placeholder(tmp_path):
    text = _owner_record()
    ps.write_merged_snapshot(R, {**ps.floor_snapshot(), "turn_origin": "terminal",
                                 "joined_from_telegram": True, "turn_start_size": 0})
    t = _tx(tmp_path, [kit.human_entry("typed"), kit.tool_result(), _fire_entry(text)],
            "q.jsonl")
    assert _decide(t, ("Bash", {"command": "ls"})) is not None


def test_a_join_inside_a_queued_fires_turn_still_narrows_it(tmp_path):
    """A message from another session queued before the fire, inside the
    snapshot's own turn."""
    text = _owner_record()
    ps.write_merged_snapshot(R, {**kit.owner_snapshot(), "turn_start_size": 0})
    t = _tx(tmp_path, [kit.telegram_entry("owner work"), kit.enqueue("slipped in"),
                       kit.tool_result(), _fire_entry(text)], "q.jsonl")
    assert _decide(t, ("Bash", {"command": "ls"})) is not None


def test_outside_input_inside_a_queued_fires_turn_still_narrows_it(tmp_path):
    text = _owner_record()
    ps.write_merged_snapshot(R, {**kit.owner_snapshot(), "turn_start_size": 0})
    t = _tx(tmp_path, [kit.telegram_entry("owner work"), CHANNEL, kit.tool_result(),
                       _fire_entry(text)], "q.jsonl")
    assert _decide(t, ("Bash", {"command": "ls"})) is not None


def test_with_no_recorded_start_an_earlier_fire_never_lends_its_rights(tmp_path, run_hook):
    """The pick-up could not record where the turn began (no transcript
    path, a failed stat): the fire cannot be placed, so it never counts
    the placeholder as no limit."""
    t = _after_owner_cron(tmp_path, run_hook)
    notify_hook._match_and_promote(R, "do the thing")  # no start recorded
    assert "turn_start_size" not in ps.read_snapshot(R)
    assert _decide(t, ("Bash", {"command": "rm -rf ~/x"})) is not None


# ---- review 8 ---------------------------------------------------------------------------

MEMBER_CRON = "member's own cron"


@pytest.mark.parametrize("first", ["cron", "bare-loop"])
def test_a_second_fire_run_from_the_queue_never_rides_the_first(tmp_path, run_hook, first):
    """Review 8 (M, N): two fires back to back, the second with no prompt
    hook; it was read as part of the owner's scheduled turn."""
    _member()
    _schedule(tmp_path, run_hook, "ScheduleWakeup")  # records PROMPT for the member
    ps.write_trigger(R, MEMBER_CRON, kit.member_rules(), authors=[3])
    _owner()
    if first == "cron":
        ps.write_trigger(R, "owner's cron", dict(kit.OWNER_RULES))
        text = "owner's cron"
    else:
        _schedule_bare_loop(tmp_path, run_hook)
        text = LOOP_TEXT
    t = _fire(tmp_path, text=text, after=[kit.tool_result(), _fire_entry(MEMBER_CRON)])
    assert _decide(t, ("Bash", {"command": "rm -rf ~/x"})) is not None
    assert _pin_of(tmp_path, run_hook, t)["rules"]["bypass_safety"] is False


def test_the_owners_bare_loop_queued_behind_their_telegram_turn_keeps_its_rights(
        tmp_path, run_hook):
    """Review 8 (D): its text is the loop's instructions, never the
    placeholder; the session's bare loop record gives its rights."""
    _owner()
    _schedule_bare_loop(tmp_path, run_hook)
    ps.clear_turn_open(R)
    _owner_note("look at the logs")
    t = _tx(tmp_path, [kit.telegram_entry("earlier"), kit.tool_result()], "d.jsonl")
    notify_hook._match_and_promote(R, "[via Telegram · @owner]\nlook at the logs",
                                   transcript_path=t)
    t = _tx(tmp_path, [kit.telegram_entry("earlier"), kit.tool_result(),
                       kit.telegram_entry("look at the logs"), kit.tool_result(),
                       _fire_entry(LOOP_TEXT)], "d.jsonl")
    assert _decide(t, ("Bash", {"command": "ls"})) is None


def test_a_members_turn_is_no_placeholder_for_a_queued_owner_fire(tmp_path):
    """Review 8 (P4): only a turn typed in the terminal is a placeholder."""
    text = _owner_record()
    ps.write_merged_snapshot(R, {**ps.resolve_snapshot(kit.builtin_role("user"), None, None),
                                 "turn_origin": "telegram", "turn_start_size": 0})
    t = _tx(tmp_path, [kit.telegram_entry("x", "@bob"), kit.tool_result(), _fire_entry(text)],
            "q.jsonl")
    assert _decide(t, ("Bash", {"command": "ls"})) is not None


def test_a_terminal_turn_another_session_joined_is_no_placeholder(tmp_path):
    """Review 8 (P2)."""
    text = _owner_record()
    snap = ps.narrowed_snapshot({**ps.floor_snapshot(), "turn_origin": "terminal"},
                                [ps.peer_floor_snapshot()])
    ps.write_merged_snapshot(R, {**snap, "turn_origin": "terminal", "joined_from_peer": True,
                                 "joined_peers": [{"body_sha256": None, "status": "unverified"}],
                                 "turn_start_size": 0})
    t = _tx(tmp_path, [kit.human_entry("typed"), kit.tool_result(), _fire_entry(text)],
            "q.jsonl")
    assert _decide(t, ("Bash", {"command": "rm -rf ~/x"})) is not None


def test_a_relayed_notification_before_a_queued_fire_narrows_it(tmp_path):
    """Review 8 (P13)."""
    text = _owner_record()
    ps.write_merged_snapshot(R, {**kit.owner_snapshot(), "turn_start_size": 0})
    relay = {"type": "user", "origin": {"kind": "task-notification",
                                        "subkind": "session-inbox"},
             "message": {"role": "user", "content": "<task-notification>x</task-notification>"}}
    t = _tx(tmp_path, [kit.telegram_entry("owner work"), relay, kit.tool_result(),
                       _fire_entry(text)], "q.jsonl")
    assert _decide(t, ("Bash", {"command": "rm -rf ~/x"})) is not None



def test_an_agent_in_a_loop_turn_before_its_fire_is_written_is_never_the_terminals(
        tmp_path, run_hook):
    """The pin reads the turn through its own bytes (nothing yet), never
    the terminal turn before it."""
    _setup(tmp_path, run_hook, "member", "bare-loop")
    t = _fire(tmp_path, before=kit.human_entry("typed"), text=LOOP_TEXT, written=False)
    assert _pin_of(tmp_path, run_hook, t)["rules"]["bypass_safety"] is False


def test_the_owner_joining_their_cron_before_anything_is_written_keeps_it(tmp_path,
                                                                        run_hook):
    """Neither the fire's nor the join's entry is written: the carried
    scheduled state keeps the turn read from its own bytes, not from the
    peer turn before it."""
    _setup(tmp_path, run_hook, "owner", "cron")
    t = _fire(tmp_path, before=kit.peer_entry(kit.BODY), written=False)
    _join(t, [kit.peer_entry(kit.BODY), kit.tool_result()], "owner-telegram")
    assert _decide(t, ("Bash", {"command": "ls"})) is None


# ---- review 9 ---------------------------------------------------------------------------

def _registry_with(status):
    from aipager.state import SessionRegistry, TrackedSession
    reg = SessionRegistry()
    reg._sessions[R] = TrackedSession(name=R, label="backend", status=status)
    return reg


def test_a_session_that_ends_keeps_its_scheduled_prompts(tmp_path):
    """Review 9: Claude Code brings a session's crons and pending /loop
    wakeup back on resume; their records must still be there."""
    from aipager.state import Status
    ps.write_trigger(R, PROMPT, kit.member_rules(), authors=[3])
    reg = _registry_with(Status.IDLE)
    reg.transition(R, Status.GONE)
    assert ps.read_trigger(R, PROMPT) is not None
    reg.remove(R)  # /kill
    assert ps.read_trigger(R, PROMPT) is None


def test_a_members_wakeup_brought_back_by_a_resume_keeps_the_members_rights(tmp_path,
                                                                           run_hook):
    """Review 9 (R1): its record was dropped at GONE, the owner then set up
    a bare loop, and the wakeup fired with the loop's rights."""
    from aipager.state import Status
    _member()
    _schedule(tmp_path, run_hook, "ScheduleWakeup")
    _registry_with(Status.IDLE).transition(R, Status.GONE)
    _owner()
    _schedule_bare_loop(tmp_path, run_hook)
    t = _fire(tmp_path, before=kit.telegram_entry("/loop"))
    assert _decide(t, ("Bash", {"command": "rm -rf ~/x"})) is not None
    assert _pin_of(tmp_path, run_hook, t)["rules"]["bypass_safety"] is False


def test_a_scheduled_slash_command_fires_with_its_own_record(tmp_path, run_hook):
    """Review 9 (R4): run from the queue its entry is Claude Code's command
    record, not the text that was scheduled; never the bare loop's rights."""
    _member()
    t0 = _tx(tmp_path, [kit.telegram_entry("x")], "s.jsonl")
    run_hook({"hook_event_name": "PreToolUse", "tool_name": "ScheduleWakeup",
              "tool_input": {"delaySeconds": 60, "prompt": "/review-pr 42", "reason": "r"},
              "transcript_path": t0, "cwd": kit.PROJECT, "session_id": "s1"})
    assert ps.read_trigger(R, "/review-pr 42") is not None
    _owner()
    _schedule_bare_loop(tmp_path, run_hook)
    ps.write_merged_snapshot(R, {**ps.floor_snapshot(), "turn_origin": "terminal",
                                 "turn_start_size": 0})
    record = ("<command-name>/review-pr</command-name>\n"
              "<command-message>review-pr</command-message>\n"
              "<command-args>42</command-args>")
    t = _tx(tmp_path, [kit.human_entry("typed"), kit.tool_result(), _fire_entry(record)],
            "q.jsonl")
    assert _decide(t, ("Bash", {"command": "rm -rf ~/x"})) is not None


def test_every_fire_in_a_queued_run_counts(tmp_path):
    """Review 9 (Q4): two fires after a terminal turn, the owner's then the
    member's; the member's must count."""
    owner_text = _owner_record()
    ps.write_trigger(R, MEMBER_CRON, kit.member_rules())
    ps.write_merged_snapshot(R, {**ps.floor_snapshot(), "turn_origin": "terminal",
                                 "turn_start_size": 0})
    t = _tx(tmp_path, [kit.human_entry("typed"), kit.tool_result(), _fire_entry(owner_text),
                       kit.tool_result(), _fire_entry(MEMBER_CRON)], "q.jsonl")
    assert _decide(t, ("Bash", {"command": "rm -rf ~/x"})) is not None


def test_a_later_fire_never_widens_the_scheduled_turn(tmp_path, run_hook):
    """Review 9 (Q10): the member's cron ran with its prompt hook, then the
    owner's from the queue: still the member's limits."""
    _member()
    _schedule(tmp_path, run_hook, "ScheduleWakeup")
    owner_text = _owner_record()
    t = _fire(tmp_path, before=kit.telegram_entry("x", "@bob"),
              after=[kit.tool_result(), _fire_entry(owner_text)])
    assert _decide(t, ("Bash", {"command": "rm -rf ~/x"})) is not None


def test_scheduling_into_a_folder_no_one_can_trust_is_refused(tmp_path):
    """Review 9 (Q9): the record could not be written, so the prompt would
    fire with no known rights."""
    d = ps.triggers_dir(R)
    d.mkdir()
    os.chmod(d, 0o777)
    _owner()
    t = _tx(tmp_path, [kit.telegram_entry("x")])
    out = _decide(t, ("ScheduleWakeup", {"delaySeconds": 60, "prompt": "p", "reason": "r"}))
    assert out is not None and out.get("soft") is True


# ---- a scheduled turn that compacts on its own --------------------------------------

FIRE_PID = "prompt-of-the-fire"
COMPACT_BOUNDARY = {"type": "system", "subtype": "compact_boundary",
                    "compactMetadata": {"trigger": "auto", "preTokens": 190000}}
CLAUDE_BASH = {"type": "assistant", "message": {"role": "assistant", "content": [
    {"type": "tool_use", "id": "tu1", "name": "Bash", "input": {"command": "rm -rf ~/x"}}]}}


def _stamped_summary():
    """How Claude Code (2.1.302) writes the summary of a scheduled turn
    that compacted: the fire's promptId and turnOrigin copied onto it."""
    return {"type": "user", "isCompactSummary": True, "isVisibleInTranscriptOnly": True,
            "turnOrigin": "scheduled", "promptId": FIRE_PID,
            "message": {"role": "user", "content":
                        "This session is being continued from a previous conversation "
                        "that ran out of context. Summary: checking the deploy."}}


def _compacted_fire(tmp_path, text, how, after=()):
    """*text* fires, the turn compacts at once, then Claude calls Bash.
    *how*: ``picked-up`` (its prompt hook ran) or ``queued`` (Claude Code
    ran it after a terminal turn with no prompt hook)."""
    fire = {**_fire_entry(text), "promptId": FIRE_PID}
    rest = [fire, COMPACT_BOUNDARY, _stamped_summary(), *after, CLAUDE_BASH]
    if how == "picked-up":
        ps.clear_turn_open(R)
        earlier = [kit.telegram_entry("set up a cron"), kit.tool_result()]
        t = _tx(tmp_path, earlier, "compacted.jsonl")
        notify_hook._match_and_promote(R, text, transcript_path=t)
        return _tx(tmp_path, [*earlier, *rest], "compacted.jsonl")
    ps.write_merged_snapshot(R, {**ps.floor_snapshot(), "turn_origin": "terminal",
                                 "turn_start_size": 0})
    return _tx(tmp_path, [kit.human_entry("typed"), kit.tool_result(), *rest],
               "compacted.jsonl")


@pytest.mark.parametrize("how", ["picked-up", "queued"])
def test_an_owners_cron_keeps_its_rights_when_its_turn_compacts(tmp_path, run_hook, how):
    """2026-10-17: the owner's cron fired, the turn compacted on its own and
    its first Bash call was stopped as the floor's: the summary carries the
    fire's turnOrigin and was read as a second fire with no record."""
    _owner()
    _schedule(tmp_path, run_hook)
    assert ps.read_trigger(R, PROMPT)["bypass_safety"] is True
    t = _compacted_fire(tmp_path, PROMPT, how)
    assert _decide(t, BASH) is None


@pytest.mark.parametrize("how", ["picked-up", "queued"])
def test_a_members_cron_that_compacts_keeps_the_members_limits(tmp_path, run_hook, how):
    """Passing the summary over never widens a turn: a member's fire keeps
    the member's limits after the compaction."""
    _member()
    _schedule(tmp_path, run_hook, "ScheduleWakeup")
    assert ps.read_trigger(R, PROMPT)["bypass_safety"] is False
    _owner()
    t = _compacted_fire(tmp_path, PROMPT, how)
    assert _decide(t, BASH) is not None


@pytest.mark.parametrize("how", ["picked-up", "queued"])
def test_a_real_second_fire_after_the_compaction_still_narrows(tmp_path, run_hook, how):
    """A fire of its own after the summary (its own text, its own
    promptId, no record) still takes the turn to the floor."""
    _owner()
    _schedule(tmp_path, run_hook)
    second = {**_fire_entry("a prompt nobody recorded"), "promptId": "prompt-of-another"}
    t = _compacted_fire(tmp_path, PROMPT, how, after=[kit.tool_result(), second])
    assert _decide(t, BASH) is not None


def test_both_scans_pass_the_compact_summary_over(tmp_path):
    """The backward scan reaches the fire itself, and the turn's own bytes
    hold one fire, whatever turnOrigin the summary carries."""
    t = _tx(tmp_path, [kit.telegram_entry("x"), kit.tool_result(),
                       {**_fire_entry(), "promptId": FIRE_PID}, COMPACT_BOUNDARY,
                       _stamped_summary(), CLAUDE_BASH])
    scan = enforce._classify_turn(t)
    assert scan["origin"] == "scheduled"
    assert scan["entry_origin"]["text"] == PROMPT
    assert enforce._turn_region(t, 0)["fires"] == [PROMPT]
