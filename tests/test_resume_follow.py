"""Resume and Restart of a session that is not parked (R1, R2).

Before a conversation is resumed (a GONE session's Resume, or Restart of a
live one), aipager looks at Claude's files:

- R1: when the transcript's LAST record is ``continued-in`` (the
  conversation moved on, ``←``), the newer conversation is resumed instead,
  at most a few hops, never in a circle, only to one that exists;
- R2: when a live background process holds the conversation (Claude Code
  would refuse to resume it), an idle job is stopped first, a busy one gives
  ``bg_busy`` unless forced, and a failed stop ``bg_stop_failed``. Only a
  live, non-interactive process with a matching start time counts.

An unreadable registry launches as before (S7). The fake world is the one
of tests/test_bring_back_core.py; no real claude or dtach runs.
"""

from __future__ import annotations

import pytest

from aipager import claude_jobs
from aipager.state import Status
from tests.test_miniapp_session_actions_api import (  # noqa: F401 - fixtures
    _configured_bot_token,
    server,
)
from tests.test_bring_back_core import (
    CLIENT,
    JOB,
    JS,
    NAME,
    WORKER,
    X,
    World,
)


@pytest.fixture
def world(mk_bot, monkeypatch, tmp_path):
    """tests/test_bring_back_core.py's parked session and its seams."""
    w = World(mk_bot, monkeypatch, tmp_path)
    yield w
    w.close()


Y = "bbbbbbbb-0000-4000-8000-000000000003"
Z = "cccccccc-0000-4000-8000-000000000004"
W = "dddddddd-0000-4000-8000-000000000005"


def _not_parked(world, *, gone=True, sid=X):
    sess = world.sess
    sess.parked_job = ""
    sess.parked_job_session_id = ""
    sess.parked_since = 0.0
    sess.claude_session_id = sid
    world.unreg(CLIENT)
    if gone:
        world.bot.registry.transition(NAME, Status.GONE)
    return sess


def _moved(world, sid, to):
    world.transcript(sid, {"type": "user"},
                     {"type": "continued-in", "continuedInSessionId": to})


def _resumed_ids(world):
    return [launch["resume_id"] for launch in world.launches()]


# ---- T3.13: R1, follow continued-in --------------------------------------------

def test_a_moved_conversation_is_resumed_where_it_went(world, run_async):
    sess = _not_parked(world)
    world.unreg(WORKER)
    world.alive.discard(WORKER)
    _moved(world, X, Y)
    world.transcript(Y, {"type": "user"})
    out = run_async(world.bot._do_resume_core(sess))
    assert out.ok
    assert _resumed_ids(world) == [Y]


def test_a_continued_in_that_is_not_last_is_not_followed(world, run_async):
    """Records after ``continued-in``: the old copy was resumed on purpose."""
    sess = _not_parked(world)
    world.unreg(WORKER)
    world.transcript(X, {"type": "user"},
                     {"type": "continued-in", "continuedInSessionId": Y},
                     {"type": "user"})
    world.transcript(Y, {"type": "user"})
    assert run_async(world.bot._do_resume_core(sess)).ok
    assert _resumed_ids(world) == [X]


def test_only_a_continued_in_record_moves_it(world, run_async):
    """A last record of another kind that happens to name a conversation
    is not a move."""
    sess = _not_parked(world)
    world.unreg(WORKER)
    world.transcript(X, {"type": "user"},
                     {"type": "summary", "continuedInSessionId": Y})
    world.transcript(Y, {"type": "user"})
    assert run_async(world.bot._do_resume_core(sess)).ok
    assert _resumed_ids(world) == [X]


def test_a_cycle_stops(world, run_async):
    sess = _not_parked(world)
    world.unreg(WORKER)
    _moved(world, X, Y)
    _moved(world, Y, Z)
    _moved(world, Z, X)
    assert run_async(world.bot._do_resume_core(sess)).ok
    assert _resumed_ids(world) == [Z], "X to Y to Z, and never back to X"


def test_the_hops_are_capped(world, run_async):
    sess = _not_parked(world)
    world.unreg(WORKER)
    _moved(world, X, Y)
    _moved(world, Y, Z)
    _moved(world, Z, W)
    _moved(world, W, JS)
    assert run_async(world.bot._do_resume_core(sess)).ok
    assert claude_jobs.MAX_HOPS == 3
    assert _resumed_ids(world) == [W], "three hops, not four"


def test_a_hop_to_nowhere_is_not_taken(world, run_async):
    sess = _not_parked(world)
    world.unreg(WORKER)
    _moved(world, X, Y)          # Y has no transcript and no holder
    assert run_async(world.bot._do_resume_core(sess)).ok
    assert _resumed_ids(world) == [X]


# ---- T3.14: R2 on the resume of an ended session --------------------------------

def test_an_idle_holder_is_stopped_then_the_conversation_resumed(world, run_async):
    sess = _not_parked(world, sid=JS)
    out = run_async(world.bot._do_resume_core(sess))
    assert out.ok and out.reason == "resumed"
    assert world.kinds()[0] == "run" and world.runs()[0] == ["stop", JOB]
    assert world.kinds()[-1] == "launch"
    assert _resumed_ids(world) == [JS]
    assert sess.status == Status.IDLE


def test_a_busy_holder_is_asked_about(world, run_async):
    sess = _not_parked(world, sid=JS)
    world.reg(WORKER, kind="bg", jobId=JOB, sessionId=JS, status="busy")
    out = run_async(world.bot._do_resume_core(sess))
    assert (out.ok, out.reason) == (False, "bg_busy")
    assert world.calls == []
    assert sess.status == Status.GONE and sess.claude_session_id == JS
    assert sess.resuming_until == 0.0


def test_a_forced_resume_stops_a_busy_holder(world, run_async):
    sess = _not_parked(world, sid=JS)
    world.reg(WORKER, kind="bg", jobId=JOB, sessionId=JS, status="busy")
    out = run_async(world.bot._do_resume_core(sess, force=True))
    assert out.ok
    assert world.runs()[0] == ["stop", JOB] and world.kinds()[-1] == "launch"


def test_a_failed_stop_leaves_the_ended_session_as_it_was(world, run_async):
    sess = _not_parked(world, sid=JS)
    world.stop_rc = 3
    out = run_async(world.bot._do_resume_core(sess))
    assert (out.ok, out.reason, out.bg_err) == (False, "bg_stop_failed",
                                                ("stop_exit", {"code": 3}))
    assert "launch" not in world.kinds()
    assert sess.status == Status.GONE and sess.claude_session_id == JS


def test_a_holder_with_no_job_cannot_be_stopped(world, run_async):
    sess = _not_parked(world, sid=JS)
    world.reg(WORKER, kind="bg", sessionId=JS, status="idle")   # no jobId
    out = run_async(world.bot._do_resume_core(sess))
    assert (out.reason, out.bg_err) == ("bg_stop_failed", ("still_running", {}))
    assert world.calls == []


def test_an_unreadable_registry_resumes_as_before(world, run_async):
    sess = _not_parked(world, sid=JS)
    for path in (world.cfg / "sessions").iterdir():
        path.unlink()
    (world.cfg / "sessions").rmdir()
    assert run_async(world.bot._do_resume_core(sess)).ok
    assert world.runs() == [] and _resumed_ids(world) == [JS]


@pytest.mark.parametrize("case", ["interactive", "dead", "reused-pid"])
def test_what_is_no_holder(world, run_async, monkeypatch, case):
    sess = _not_parked(world, sid=JS)
    if case == "interactive":
        world.reg(WORKER, kind="interactive", sessionId=JS, status="idle")
    elif case == "dead":
        world.alive.discard(WORKER)
    else:
        world.reg(WORKER, kind="bg", jobId=JOB, sessionId=JS, status="idle",
                  procStart="55")
        monkeypatch.setattr(claude_jobs, "_proc_start",
                            lambda pid: 56 if pid == WORKER else None)
    assert run_async(world.bot._do_resume_core(sess)).ok
    assert world.runs() == [], "nothing stopped"
    assert _resumed_ids(world) == [JS]


# ---- rev-iter3-001: a resume onto a conversation whose job wrote no transcript ----

def _parks_in_the_gap(world, run_async, sid=X):
    """The relaunched claude's SessionStart names its conversation *sid*,
    and it has not written its registry file yet: what the check then
    decides from the transcript alone."""
    import json
    from unittest.mock import AsyncMock

    from aipager.dtach import hook_receiver as hr
    from aipager.session_monitor import SessionMonitor
    recv = hr.HookReceiver(world.bot.registry, world.bot.notify)
    run_async(recv._on_datagram(json.dumps({
        "hook_event_name": "SessionStart", "source": "resume", "session": NAME,
        "session_id": sid,
        "transcript_path": str(world.projects / f"{sid}.jsonl")}).encode()))
    assert world.sess.claude_session_id == sid, "precondition: the id is known"
    monitor = SessionMonitor(world.bot.registry, AsyncMock())
    return monitor.check_parked(world.sess)


def test_an_idle_holder_without_a_transcript_resumes_the_saved_conversation(
        world, run_async):
    """X moved into the idle job's JS, which never ran a prompt (no
    transcript): the job is stopped, X comes back (claude cannot resume
    JS), and X's old ``continued-in`` parks nothing."""
    sess = _not_parked(world)
    world.drop_transcript(JS)
    out = run_async(world.bot._do_resume_core(sess))
    assert out.ok
    assert world.runs()[0] == ["stop", JOB]
    assert _resumed_ids(world) == [X]
    assert _parks_in_the_gap(world, run_async) is False
    assert sess.parked_job == ""


def test_a_gone_job_without_a_transcript_parks_nothing_after_the_resume(world,
                                                                        run_async):
    sess = _not_parked(world)
    world.drop_transcript(JS)
    world.unreg(WORKER)
    world.alive.discard(WORKER)
    assert run_async(world.bot._do_resume_core(sess)).ok
    assert world.runs() == [] and _resumed_ids(world) == [X]
    assert _parks_in_the_gap(world, run_async) is False


def test_a_restart_onto_a_moved_conversation_parks_nothing_after_it(world,
                                                                    run_async):
    """Restart of a live session whose conversation still ends moved into
    a job that is gone: the relaunched X is not parked on it."""
    sess = _not_parked(world, gone=False)
    world.drop_transcript(JS)
    world.unreg(WORKER)
    world.alive.discard(WORKER)
    assert run_async(world.bot._restart_session_core(sess)).ok
    assert _resumed_ids(world) == [X]
    assert _parks_in_the_gap(world, run_async) is False


def test_a_resume_with_nothing_moved_forgets_an_old_note(world, run_async):
    sess = _not_parked(world)
    world.unreg(WORKER)
    world.transcript(X, {"type": "user"})
    sess.note_moved_handled(X, JOB)
    assert run_async(world.bot._do_resume_core(sess)).ok
    assert (sess.moved_handled_sid, sess.moved_handled_job) == ("", "")


def test_the_chat_resume_asks_with_the_two_buttons(world, run_async, mk_update):
    """``/resume x`` with a busy job holding the conversation: the busy
    question, with "Stop and bring back" and "Leave it"."""
    sess = _not_parked(world, sid=JS)
    world.reg(WORKER, kind="bg", jobId=JOB, sessionId=JS, status="busy")
    update = mk_update("/resume x", chat_id=-1001)
    replies = []

    async def reply(text, **kw):
        replies.append((text, kw))

    run_async(world.bot._do_resume(label="x", reply_fn=reply, update=update, sess=sess))
    text, kw = replies[-1]
    assert text == ("<b>x</b> is still working in Claude Code's background: stop that "
                    "work and bring the conversation back here?")
    buttons = [b for row in kw["reply_markup"].inline_keyboard for b in row]
    assert [b.text for b in buttons] == ["Stop and bring back", "Leave it"]
    assert [b.callback_data.rsplit(":", 1)[1] for b in buttons] == ["bgforce", "bgleave"]


def test_the_chat_resume_says_why_a_stop_failed(world, run_async, mk_update):
    sess = _not_parked(world, sid=JS)
    world.stop_timeout = True
    replies = []

    async def reply(text, **kw):
        replies.append(text)

    run_async(world.bot._do_resume(label="x", reply_fn=reply,
                                   update=mk_update("/resume x"), sess=sess))
    assert replies == ["Could not bring <b>x</b> back from Claude Code's background: "
                       "claude stop did not answer in time"]


# ---- T3.15: Restart of a live session that is not parked -------------------------

def test_restart_with_an_idle_holder_stops_it_first(world, run_async):
    sess = _not_parked(world, gone=False, sid=JS)
    out = run_async(world.bot._restart_session_core(sess))
    assert out.ok
    kinds = world.kinds()
    assert kinds.index("run") < kinds.index("kill") < kinds.index("launch")
    assert world.runs()[0] == ["stop", JOB]
    assert _resumed_ids(world) == [JS]


def test_restart_with_a_busy_holder_is_asked_about(world, run_async):
    sess = _not_parked(world, gone=False, sid=JS)
    world.reg(WORKER, kind="bg", jobId=JOB, sessionId=JS, status="busy")
    window = sess.restarting_until
    out = run_async(world.bot._restart_session_core(sess))
    assert (out.ok, out.reason) == (False, "bg_busy")
    assert world.calls == [], "nothing killed, nothing typed"
    assert sess.restarting_until == window and sess.status == Status.IDLE


def test_a_forced_restart_stops_a_busy_holder(world, run_async):
    sess = _not_parked(world, gone=False, sid=JS)
    world.reg(WORKER, kind="bg", jobId=JOB, sessionId=JS, status="busy")
    assert run_async(world.bot._restart_session_core(sess, force=True)).ok
    assert world.runs()[0] == ["stop", JOB]


def test_restart_follows_a_moved_conversation(world, run_async):
    sess = _not_parked(world, gone=False)
    world.unreg(WORKER)
    _moved(world, X, Y)
    world.transcript(Y, {"type": "user"})
    assert run_async(world.bot._restart_session_core(sess)).ok
    assert _resumed_ids(world) == [Y] and sess.claude_session_id == Y


def test_restart_with_an_unreadable_registry_is_as_before(world, run_async):
    sess = _not_parked(world, gone=False, sid=JS)
    for path in (world.cfg / "sessions").iterdir():
        path.unlink()
    (world.cfg / "sessions").rmdir()
    assert run_async(world.bot._restart_session_core(sess)).ok
    assert world.runs() == [] and world.kinds() == ["kill", "launch"]


def test_a_restart_with_nothing_in_the_way_is_unchanged(world, run_async):
    sess = _not_parked(world, gone=False, sid=JS)
    world.unreg(WORKER)
    assert run_async(world.bot._restart_session_core(sess)).ok
    assert world.runs() == [] and world.kinds() == ["kill", "launch"]



# ---- the Mini App never says "done" when a background job was in the way ----

@pytest.mark.parametrize("route, core, reason", [
    ("restart", "_restart_session_core", "bg_busy"),
    ("restart", "_restart_session_core", "bg_stop_failed"),
    ("resume", "_do_resume_core", "bg_busy"),
    ("resume", "_do_resume_core", "bg_stop_failed"),
])
def test_the_mini_app_answers_409_not_200(server, run_async, route, core,  # noqa: F811
                                          reason):
    """Until the page's own background flow (step 5), a refusal by a
    background job is a 409 with its reason, never the route's success."""
    from unittest.mock import AsyncMock

    from aipager.bot.session_ops import RestartOutcome, ResumeOutcome
    from tests.test_miniapp_session_actions_api import (
        ADMIN_ID, _client_for, _hdr, _mk_session)
    err = ("stop_exit", {"code": 1}) if reason == "bg_stop_failed" else ()
    outcome = (RestartOutcome(ok=False, reason=reason, label="dev", bg_err=err)
               if route == "restart" else ResumeOutcome(ok=False, reason=reason,
                                                        bg_err=err))
    setattr(server.bot, core, AsyncMock(return_value=outcome))

    async def _run():
        _mk_session(server, "dev",
                    status=Status.IDLE if route == "restart" else Status.GONE, mode="ask")
        client = await _client_for(server)
        try:
            resp = await client.post(f"/api/sessions/dev/{route}", headers=_hdr(ADMIN_ID))
            assert resp.status == 409
            body = await resp.json()
            assert body["error"] == reason
            if reason == "bg_stop_failed":
                assert body["detail"] == ("Could not bring dev back from Claude Code's "
                                          "background: claude stop ended with exit code 1")
        finally:
            await client.close()
    run_async(_run())
    assert server.bot._app.bot.send_message.await_count == 0, "no success mirror"
