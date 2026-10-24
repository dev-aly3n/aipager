"""QA: every way back from Claude Code's background (B1-B4, R1, R2, S5).

design.md success criteria 6 and 7: "Bring it back never kills anything
before a successful ``claude stop``, or while the job is busy unless
forced; it never launches an empty conversation." "Resume and Restart
follow ``continued-in`` and handle live holders."

Partitions:
- entry: the ``bgback``/``bgforce``/``bgleave`` buttons, Restart, Stop, a
  safety halt (parked); Resume and Restart (not parked, a held id);
- the tapper: may Restart / may not / taps from another chat;
- the stop: succeeds / exits non-zero / times out / cannot run / leaves a
  live holder;
- the conversation: the job's transcript / neither transcript;
- ``continued-in``: none, final, not final, 2, 3 and 4 hops, a cycle, a
  target with neither transcript nor holder;
- the holder: idle / busy / forced / interactive / dead / registry
  unreadable.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import claude_jobs
from aipager.bot import session_parity
from aipager.scope import Member, Scope
from aipager.state import Status


def _kit():
    import importlib.util
    import sys
    from pathlib import Path
    if "_bgj_qa_kit" not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            "_bgj_qa_kit", Path(__file__).with_name("_qa_kit.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules["_bgj_qa_kit"] = mod
        spec.loader.exec_module(mod)
    return sys.modules["_bgj_qa_kit"]


qa = _kit()

ADMIN, DEV = 555, 777
A = "a1a1a1a1-0000-4000-8000-00000000000a"
B = "b2b2b2b2-0000-4000-8000-00000000000b"
C = "c3c3c3c3-0000-4000-8000-00000000000c"
D = "d4d4d4d4-0000-4000-8000-00000000000d"


@pytest.fixture
def ordered(stack, monkeypatch):
    inner = stack.claude.exec

    async def exec_(*argv, **kw):
        stack.dtach.append(("run", list(argv[1:])))
        return await inner(*argv, **kw)
    monkeypatch.setattr(claude_jobs, "_create_subprocess_exec", exec_)
    return stack


@pytest.fixture
def team(stack, tmp_path):
    root = tmp_path / "team-root"
    root.mkdir()
    stack.bot.scopes = [Scope(
        chat_id=stack.ids.CHAT, kind="group", label="team",
        members=(Member(id=ADMIN, label="ada", role="admin"),
                 Member(id=DEV, label="bob", role="user")),
        root=str(root))]
    return stack


def _stop_fails(stack, monkeypatch, how):
    """``claude stop`` misbehaves: ``rc`` exits 1, ``timeout`` never answers,
    ``missing`` cannot be run at all."""
    inner = stack.claude.exec

    async def exec_(*argv, **kw):
        if list(argv[1:2]) != ["stop"]:
            return await inner(*argv, **kw)
        stack.claude.runs.append(list(argv[1:]))
        if how == "missing":
            raise FileNotFoundError(argv[0])

        class Proc:
            returncode = None

            async def communicate(self, _stdin=None):
                if how == "timeout":
                    raise asyncio.TimeoutError
                self.returncode = 1
                return b"", b"no such job"

            def kill(self):
                pass

            async def wait(self):
                return self.returncode
        return Proc()
    monkeypatch.setattr(claude_jobs, "_create_subprocess_exec", exec_)


def _tap(stack, verb, *, user=12345, chat=None):
    chat = stack.ids.CHAT if chat is None else chat
    q = MagicMock()
    q.from_user = MagicMock(id=user)
    q.message = MagicMock(chat=MagicMock(id=chat), chat_id=chat, message_id=77)
    q.answer = AsyncMock()
    q.edit_message_text = AsyncMock()
    q.edit_message_reply_markup = AsyncMock()
    u = MagicMock(callback_query=q, effective_chat=MagicMock(id=chat),
                  effective_user=MagicMock(id=user))
    stack.run(session_parity.handle_callback(stack.bot, u, q, stack.ids.NAME, verb))
    return q


def _edited(q) -> list[str]:
    return [c.args[0] if c.args else c.kwargs.get("text", "")
            for c in q.edit_message_text.await_args_list]


def _resumed(stack) -> list:
    return [lc.get("resume_id") for lc in stack.launches()]


def _gone(stack, sid):
    """A finished session (its socket gone) whose conversation is *sid*."""
    sess = stack.sess
    stack.claude.unreg(stack.ids.CLIENT)
    stack.live.discard(stack.ids.NAME)
    sess.claude_session_id = sid
    sess.transcript_path = str(stack.claude.projects / f"{sid}.jsonl")
    stack.registry.transition(stack.ids.NAME, Status.GONE)
    return sess


def _moved(stack, sid, to):
    stack.claude.transcript(sid, {"type": "user"},
                            {"type": "continued-in", "continuedInSessionId": to})


def _holder(stack, status="idle", *, kind="bg", alive=True, sid=None):
    ids = stack.ids
    stack.claude.reg(ids.WORKER, kind=kind, jobId=ids.JOB, sessionId=sid or ids.JS,
                     status=status)
    stack.claude.stop_ends[ids.JOB] = ids.WORKER
    if not alive:
        stack.claude.alive.discard(ids.WORKER)


# ---- the buttons ---------------------------------------------------------------------

def test_bring_it_back_now_on_an_idle_job_brings_it_back(stack):
    qa.park_and_tick(stack)
    _tap(stack, "bgback")
    assert (qa.stops(stack), _resumed(stack)) == ([["stop", stack.ids.JOB]], [stack.ids.JS])


def test_bring_it_back_now_on_a_busy_job_asks(stack):
    qa.park_and_tick(stack, job_status="busy")
    q = _tap(stack, "bgback")
    assert qa.BUSY_QUESTION in _edited(q) + qa.sent_texts(stack)


def test_bring_it_back_now_on_a_busy_job_touches_nothing(stack):
    qa.park_and_tick(stack, job_status="busy")
    _tap(stack, "bgback")
    assert (qa.stops(stack), stack.kinds()) == ([], [])


def test_stop_and_bring_back_forces_a_busy_job(stack):
    qa.park_and_tick(stack, job_status="busy")
    _tap(stack, "bgforce")
    assert (qa.stops(stack), stack.kinds()) == ([["stop", stack.ids.JOB]], ["kill", "launch"])


def test_leave_it_stops_nothing(stack):
    qa.park_and_tick(stack, job_status="busy")
    _tap(stack, "bgleave")
    assert (qa.stops(stack), stack.kinds(), stack.sess.parked_job) == (
        [], [], stack.ids.JOB)


def test_leave_it_says_the_job_keeps_working(stack):
    qa.park_and_tick(stack, job_status="busy")
    q = _tap(stack, "bgleave")
    assert qa.LEFT in _edited(q) + qa.sent_texts(stack)


@pytest.mark.parametrize("verb", ["bgback", "bgforce"])
def test_a_tapper_who_may_not_restart_brings_nothing_back(team, verb):
    stack = team
    qa.park_and_tick(stack)
    _tap(stack, verb, user=DEV)
    assert (qa.stops(stack), stack.kinds(), stack.sess.parked_job) == (
        [], [], stack.ids.JOB)


@pytest.mark.parametrize("verb", ["bgback", "bgforce"])
def test_a_tap_from_another_chat_brings_nothing_back(team, verb):
    stack = team
    qa.park_and_tick(stack)
    _tap(stack, verb, user=ADMIN, chat=-4242)
    assert (qa.stops(stack), stack.kinds()) == ([], [])


@pytest.mark.parametrize("verb", ["bgback", "bgforce"])
def test_a_tapper_who_may_not_restart_is_told(team, verb):
    stack = team
    qa.park_and_tick(stack)
    q = _tap(stack, verb, user=DEV)
    assert q.answer.await_count >= 1 and any(
        (c.args and c.args[0]) or c.kwargs.get("text") for c in q.answer.await_args_list)


def test_a_double_tap_brings_it_back_once(stack):
    qa.park_and_tick(stack)
    _tap(stack, "bgback")
    _tap(stack, "bgback")
    assert (qa.stops(stack), stack.kinds()) == ([["stop", stack.ids.JOB]], ["kill", "launch"])


# ---- Restart, Stop and a safety halt on a parked session -------------------------------

def test_restart_of_a_parked_session_brings_it_back(stack):
    qa.park_and_tick(stack)
    out = stack.run(stack.bot._restart_session_core(stack.sess))
    assert (out.ok, _resumed(stack)) == (True, [stack.ids.JS])


def test_restart_of_a_parked_session_never_presses_keys(stack):
    qa.park_and_tick(stack)
    stack.run(stack.bot._restart_session_core(stack.sess))
    assert "keys" not in stack.kinds()


def test_restart_of_a_busy_parked_session_asks(stack):
    qa.park_and_tick(stack, job_status="busy")
    out = stack.run(stack.bot._restart_session_core(stack.sess))
    assert (out.ok, out.reason, qa.stops(stack), stack.kinds()) == (False, "bg_busy", [], [])


def test_forced_restart_of_a_busy_parked_session_brings_it_back(stack):
    qa.park_and_tick(stack, job_status="busy")
    out = stack.run(stack.bot._restart_session_core(stack.sess, force=True))
    assert (out.ok, qa.stops(stack)) == (True, [["stop", stack.ids.JOB]])


def test_stop_on_a_parked_session_stops_even_a_busy_job(stack):
    qa.park_and_tick(stack, job_status="busy")
    stack.run(stack.bot._stop_session_core(stack.sess))
    assert (qa.stops(stack), _resumed(stack)) == ([["stop", stack.ids.JOB]], [stack.ids.JS])


def test_stop_on_a_parked_session_types_nothing(stack):
    qa.park_and_tick(stack, job_status="busy")
    stack.run(stack.bot._stop_session_core(stack.sess))
    assert ("keys" not in stack.kinds(), stack.typed) == (True, [])


def test_a_safety_halt_of_a_parked_session_stops_the_job(stack):
    qa.park_and_tick(stack, job_status="busy")
    stack.run(stack.bot._halt_for_safety(stack.sess, "qa halt"))
    assert qa.stops(stack) == [["stop", stack.ids.JOB]]


def test_a_safety_halt_of_a_parked_session_writes_no_escape(stack):
    qa.park_and_tick(stack, job_status="busy")
    stack.run(stack.bot._halt_for_safety(stack.sess, "qa halt"))
    assert ("keys" not in stack.kinds(), stack.typed) == (True, [])


def test_a_safety_halt_drops_the_held_messages(stack, mk_update):
    qa.park_and_tick(stack, job_status="busy")
    stack.message(mk_update, "carry on", msg_id=41)
    stack.run_spawned()
    stack.run(stack.bot._halt_for_safety(stack.sess, "qa halt"))
    qa.relaunched_start(stack)
    assert (stack.typed, "🤷" in qa.reactions(stack)) == ([], True)


# ---- S5: a stop that fails leaves everything as it was ---------------------------------

@pytest.mark.parametrize("how", ["rc", "timeout", "missing"])
def test_a_failed_stop_is_bg_stop_failed(stack, monkeypatch, how):
    qa.park_and_tick(stack)
    _stop_fails(stack, monkeypatch, how)
    out = stack.run(stack.bot._bring_back_core(stack.sess))
    assert (out.ok, out.reason) == (False, "bg_stop_failed")


@pytest.mark.parametrize("how", ["rc", "timeout", "missing"])
def test_a_failed_stop_kills_and_launches_nothing(stack, monkeypatch, how):
    qa.park_and_tick(stack)
    _stop_fails(stack, monkeypatch, how)
    stack.run(stack.bot._bring_back_core(stack.sess))
    assert stack.kinds() == []


@pytest.mark.parametrize("how", ["rc", "timeout", "missing"])
def test_a_failed_stop_leaves_the_session_parked_on_the_same_ids(stack, monkeypatch, how):
    qa.park_and_tick(stack)
    before = (stack.sess.parked_job, stack.sess.parked_job_session_id,
              stack.sess.claude_session_id, qa.link(stack.ids.JOB))
    _stop_fails(stack, monkeypatch, how)
    stack.run(stack.bot._bring_back_core(stack.sess))
    assert (stack.sess.parked_job, stack.sess.parked_job_session_id,
            stack.sess.claude_session_id, qa.link(stack.ids.JOB)) == before


def test_a_failed_stop_from_the_button_says_why(stack, monkeypatch):
    qa.park_and_tick(stack)
    _stop_fails(stack, monkeypatch, "rc")
    q = _tap(stack, "bgback")
    shown = _edited(q) + qa.sent_texts(stack)
    assert any(t.startswith("Could not bring <b>frontend</b> back from Claude Code's "
                            "background: ") for t in shown), shown


def test_a_holder_still_alive_after_the_stop_is_bg_stop_failed(stack):
    qa.park_and_tick(stack)
    stack.claude.stop_ends.pop(stack.ids.JOB)        # the stop "works", the job stays
    out = stack.run(stack.bot._bring_back_core(stack.sess))
    assert (out.reason, stack.kinds()) == ("bg_stop_failed", [])


def test_the_stop_runs_before_the_kill(ordered):
    stack = ordered
    qa.park_and_tick(stack)
    stack.run(stack.bot._bring_back_core(stack.sess))
    steps = [d[0] if d[0] != "run" else d[1][0] for d in stack.dtach if d[0] != "sleep"]
    assert [s for s in steps if s in ("stop", "kill", "launch")] == ["stop", "kill", "launch"]


def test_no_saved_conversation_launches_nothing(stack):
    qa.park_and_tick(stack)
    (stack.claude.projects / f"{stack.ids.JS}.jsonl").unlink()
    (stack.claude.projects / f"{stack.ids.X}.jsonl").unlink()
    out = stack.run(stack.bot._bring_back_core(stack.sess))
    assert (out.reason, qa.stops(stack), stack.kinds()) == ("bg_no_conversation", [], [])


def test_a_bring_back_already_running_is_already_restarting(stack):
    qa.park_and_tick(stack)
    stack.sess.relaunch_in_flight = True
    out = stack.run(stack.bot._bring_back_core(stack.sess))
    assert (out.reason, qa.stops(stack), stack.kinds()) == ("already_restarting", [], [])


def test_a_session_not_parked_is_not_parked(stack):
    out = stack.run(stack.bot._bring_back_core(stack.sess))
    assert (out.reason, stack.kinds()) == ("not_parked", [])


def test_a_successful_bring_back_clears_the_park(stack):
    qa.park_and_tick(stack)
    stack.run(stack.bot._bring_back_core(stack.sess))
    assert (stack.sess.parked_job, qa.link(stack.ids.JOB)) == ("", None)


def test_a_successful_bring_back_points_the_session_at_the_job_conversation(stack):
    qa.park_and_tick(stack)
    stack.run(stack.bot._bring_back_core(stack.sess))
    assert stack.sess.claude_session_id == stack.ids.JS


def test_a_successful_bring_back_drops_the_client_from_the_pid_map(stack):
    qa.own_pid_hook(stack)
    qa.park_and_tick(stack)
    stack.run(stack.bot._bring_back_core(stack.sess))
    pids = (qa.pidmap() or {}).get("pids", {})
    assert str(stack.ids.CLIENT) not in pids


def test_the_stopped_jobs_session_end_sends_no_notice(stack, monkeypatch):
    """B3: the worker's SessionEnd caused by ``claude stop`` must not alert."""
    import json
    qa.park_and_tick(stack)
    inner = stack.claude.exec
    seen = []

    async def exec_(*argv, **kw):
        if list(argv[1:2]) == ["stop"]:
            raw = json.dumps({"session": stack.ids.NAME, "hook_event_name": "SessionEnd",
                              "reason": "other", "bg_job": stack.ids.JOB,
                              "session_id": stack.ids.JS, "n": 9001}).encode()
            await stack.receiver._on_datagram(raw)
            seen.append("SessionEnd")
        return await inner(*argv, **kw)
    monkeypatch.setattr(claude_jobs, "_create_subprocess_exec", exec_)
    stack.run(stack.bot._bring_back_core(stack.sess))
    assert seen == ["SessionEnd"], "precondition: the job's SessionEnd arrived"
    assert ("session_end" not in stack.events, stack.sess.status != Status.GONE) == (
        True, True)


# ---- R1: Resume follows continued-in ------------------------------------------------------

def test_resume_follows_a_final_continued_in(stack):
    sess = _gone(stack, stack.ids.X)
    _moved(stack, stack.ids.X, A)
    stack.claude.transcript(A, {"type": "user"})
    assert stack.run(stack.bot._do_resume_core(sess)).ok
    assert _resumed(stack) == [A]


def test_resume_ignores_a_continued_in_that_is_not_last(stack):
    sess = _gone(stack, stack.ids.X)
    stack.claude.transcript(stack.ids.X, {"type": "user"},
                            {"type": "continued-in", "continuedInSessionId": A},
                            {"type": "user"})
    stack.claude.transcript(A, {"type": "user"})
    stack.run(stack.bot._do_resume_core(sess))
    assert _resumed(stack) == [stack.ids.X]


def test_resume_ignores_a_move_to_nowhere(stack):
    """R1: Y needs a transcript or a live holder."""
    sess = _gone(stack, stack.ids.X)
    _moved(stack, stack.ids.X, A)
    stack.run(stack.bot._do_resume_core(sess))
    assert _resumed(stack) == [stack.ids.X]


def test_resume_follows_two_hops(stack):
    sess = _gone(stack, stack.ids.X)
    _moved(stack, stack.ids.X, A)
    _moved(stack, A, B)
    stack.claude.transcript(B, {"type": "user"})
    stack.run(stack.bot._do_resume_core(sess))
    assert _resumed(stack) == [B]


def test_resume_follows_three_hops(stack):
    sess = _gone(stack, stack.ids.X)
    _moved(stack, stack.ids.X, A)
    _moved(stack, A, B)
    _moved(stack, B, C)
    stack.claude.transcript(C, {"type": "user"})
    stack.run(stack.bot._do_resume_core(sess))
    assert _resumed(stack) == [C]


def test_resume_stops_after_three_hops(stack):
    sess = _gone(stack, stack.ids.X)
    _moved(stack, stack.ids.X, A)
    _moved(stack, A, B)
    _moved(stack, B, C)
    _moved(stack, C, D)
    stack.claude.transcript(D, {"type": "user"})
    stack.run(stack.bot._do_resume_core(sess))
    assert _resumed(stack) == [C]


def test_resume_survives_a_cycle(stack):
    sess = _gone(stack, stack.ids.X)
    _moved(stack, stack.ids.X, A)
    _moved(stack, A, stack.ids.X)
    out = stack.run(stack.bot._do_resume_core(sess))
    assert out.ok and len(_resumed(stack)) == 1 and _resumed(stack)[0] in (stack.ids.X, A)


def test_resume_restores_nothing_wrong_after_a_hop(stack):
    """After a followed resume the session points at the newest id."""
    sess = _gone(stack, stack.ids.X)
    _moved(stack, stack.ids.X, A)
    stack.claude.transcript(A, {"type": "user"})
    stack.run(stack.bot._do_resume_core(sess))
    assert sess.claude_session_id in (A, "")  # never the stale copy X


# ---- R2: a live holder --------------------------------------------------------------------------

def test_resume_of_an_idly_held_conversation_stops_the_job_first(ordered):
    stack = ordered
    sess = _gone(stack, stack.ids.JS)
    stack.claude.transcript(stack.ids.JS, {"type": "user"})
    _holder(stack, "idle")
    assert stack.run(stack.bot._do_resume_core(sess)).ok
    steps = [d[0] if d[0] != "run" else d[1][0] for d in stack.dtach if d[0] != "sleep"]
    assert [s for s in steps if s in ("stop", "launch")] == ["stop", "launch"]


def test_resume_of_an_idly_held_conversation_resumes_it(stack):
    sess = _gone(stack, stack.ids.JS)
    stack.claude.transcript(stack.ids.JS, {"type": "user"})
    _holder(stack, "idle")
    stack.run(stack.bot._do_resume_core(sess))
    assert _resumed(stack) == [stack.ids.JS]


def test_resume_of_a_busily_held_conversation_asks(stack):
    sess = _gone(stack, stack.ids.JS)
    stack.claude.transcript(stack.ids.JS, {"type": "user"})
    _holder(stack, "busy")
    out = stack.run(stack.bot._do_resume_core(sess))
    assert (out.ok, out.reason, qa.stops(stack), stack.launches()) == (
        False, "bg_busy", [], [])


def test_forced_resume_of_a_busily_held_conversation_stops_it(stack):
    sess = _gone(stack, stack.ids.JS)
    stack.claude.transcript(stack.ids.JS, {"type": "user"})
    _holder(stack, "busy")
    out = stack.run(stack.bot._do_resume_core(sess, force=True))
    assert (out.ok, qa.stops(stack), _resumed(stack)) == (
        True, [["stop", stack.ids.JOB]], [stack.ids.JS])


def test_an_interactive_holder_is_not_a_live_holder(stack):
    sess = _gone(stack, stack.ids.JS)
    stack.claude.transcript(stack.ids.JS, {"type": "user"})
    _holder(stack, "busy", kind="interactive")
    stack.run(stack.bot._do_resume_core(sess))
    assert (qa.stops(stack), _resumed(stack)) == ([], [stack.ids.JS])


def test_a_dead_holder_is_not_a_live_holder(stack):
    sess = _gone(stack, stack.ids.JS)
    stack.claude.transcript(stack.ids.JS, {"type": "user"})
    _holder(stack, "busy", alive=False)
    stack.run(stack.bot._do_resume_core(sess))
    assert (qa.stops(stack), _resumed(stack)) == ([], [stack.ids.JS])


def test_an_unreadable_registry_resumes_as_today(stack):
    sess = _gone(stack, stack.ids.JS)
    stack.claude.transcript(stack.ids.JS, {"type": "user"})
    _holder(stack, "busy")
    stack.claude.sessions.chmod(0)
    try:
        stack.run(stack.bot._do_resume_core(sess))
    finally:
        stack.claude.sessions.chmod(0o755)
    assert (qa.stops(stack), _resumed(stack)) == ([], [stack.ids.JS])


def test_a_failed_stop_of_a_holder_resumes_nothing(stack, monkeypatch):
    sess = _gone(stack, stack.ids.JS)
    stack.claude.transcript(stack.ids.JS, {"type": "user"})
    _holder(stack, "idle")
    _stop_fails(stack, monkeypatch, "rc")
    out = stack.run(stack.bot._do_resume_core(sess))
    assert (out.reason, stack.launches()) == ("bg_stop_failed", [])


def test_resume_follows_the_move_and_then_stops_its_holder(stack):
    """R1 then R2: X moved to JS, which an idle job holds."""
    sess = _gone(stack, stack.ids.X)
    _moved(stack, stack.ids.X, stack.ids.JS)
    stack.claude.transcript(stack.ids.JS, {"type": "user"})
    _holder(stack, "idle")
    stack.run(stack.bot._do_resume_core(sess))
    assert (qa.stops(stack), _resumed(stack)) == ([["stop", stack.ids.JOB]], [stack.ids.JS])


def test_restart_of_a_live_session_whose_id_is_held_stops_the_holder(stack):
    """R2 for Restart of a session that is not parked."""
    stack.sess.claude_session_id = stack.ids.JS
    stack.claude.transcript(stack.ids.JS, {"type": "user"})
    stack.claude.reg(stack.ids.CLIENT, kind="interactive", sessionId=stack.ids.JS,
                     status="idle")
    _holder(stack, "idle")
    stack.run(stack.bot._restart_session_core(stack.sess))
    assert (qa.stops(stack), _resumed(stack)[-1:]) == ([["stop", stack.ids.JOB]],
                                                       [stack.ids.JS])


def test_restart_of_a_live_session_whose_id_is_busily_held_asks(stack):
    stack.sess.claude_session_id = stack.ids.JS
    stack.claude.transcript(stack.ids.JS, {"type": "user"})
    stack.claude.reg(stack.ids.CLIENT, kind="interactive", sessionId=stack.ids.JS,
                     status="idle")
    _holder(stack, "busy")
    out = stack.run(stack.bot._restart_session_core(stack.sess))
    assert (out.reason, qa.stops(stack), stack.kinds()) == ("bg_busy", [], [])


def test_restart_of_a_session_with_no_holder_is_as_today(stack):
    """Scenario 7: no background job, no claude command run."""
    stack.run(stack.bot._restart_session_core(stack.sess))
    assert (stack.claude.runs, stack.kinds()[-1:]) == ([], ["launch"])
