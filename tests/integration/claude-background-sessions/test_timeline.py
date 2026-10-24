"""The timeline of a session whose conversation moves into Claude Code's
background, end to end through the wired daemon (T5.5).

1. ``←`` on an empty prompt: Claude's registry says the session's own
   claude is parked on a job, and its transcript ends with
   ``continued-in``.
2. The job's first hook (its fork ``SessionStart``) arrives before the
   monitor's tick: it is linked through the receiver's synchronous check
   (D2), adopts the job's conversation, and leaves the turn open.
3. The monitor's tick posts one notice with "Bring it back now".
4. The turn moved with the job ends in the job: its ``Stop`` (from the
   worker) finishes the session's turn.
5. A spare's ``SessionStart`` (another job) is dropped: nothing moves.
6. A Telegram message while parked is held, brings the session back by
   itself (``claude stop``, kill, ``--resume`` of the job's conversation),
   and goes out once the relaunched claude is up.

Then the button flow (a busy job: the question, "Stop and bring back"), and
a resume Claude Code refuses because a background job holds the
conversation (exit code 1 right after starting: ``start_failed_bg`` with
Bring it back, whose tap stops the job and resumes).

Release timing: here the relaunched claude's SessionStart arrives after the
bring-back finished, the order these tests pin. They say nothing about a
SessionStart that arrives while the bring-back is still running.
"""

from __future__ import annotations

import json
import time

from aipager import i18n
from aipager.dtach import bg_jobs
from aipager.state import Status

EN = i18n.get("en")


def _park(stack, *, job_status="busy"):
    """``←``: the client parks on the job; the job's worker holds the
    conversation, which the client's transcript says moved there."""
    ids, claude = stack.ids, stack.claude
    claude.reg(ids.CLIENT, kind="interactive", sessionId=ids.X, status="idle",
               parkedJobId=ids.JOB)
    claude.reg(ids.WORKER, kind="bg", jobId=ids.JOB, sessionId=ids.JS, status=job_status)
    claude.stop_ends[ids.JOB] = ids.WORKER
    claude.append(ids.X, {"type": "continued-in", "continuedInSessionId": ids.JS})
    claude.transcript(ids.JS, {"type": "user"}, {"type": "assistant"})


def _fork_start(stack):
    ids = stack.ids
    stack.hook(hook_event_name="SessionStart", source="fork", bg_job=ids.JOB,
               session_id=ids.JS, cwd=str(stack.cc.work),
               transcript_path=str(stack.claude.projects / f"{ids.JS}.jsonl"))


def _notices(stack):
    return [c for c in stack.sent()
            if (c.kwargs.get("text") or "").startswith("🌙")]


def _buttons(call):
    return [b.callback_data.rsplit(":", 1)[1]
            for row in call.kwargs["reply_markup"].inline_keyboard for b in row]


def test_the_park_is_followed_from_the_arrow_to_the_message_that_brings_it_back(
        stack, mk_update):
    ids, sess = stack.ids, stack.sess
    # The session's own claude reports its pid (D1).
    stack.hook(hook_event_name="Notification", notification_type="idle_prompt_seen",
               claude_pid=ids.CLIENT, session_id=ids.X)
    assert sess.claude_pid == ids.CLIENT
    from aipager import claude_jobs
    assert claude_jobs.config_dir() == stack.cc.x, "found through daemon.env, not a seam"

    # 1-2. ←, then the fork's SessionStart before any tick: linked (D2).
    _park(stack)
    _fork_start(stack)
    assert sess.parked_job == ids.JOB
    assert sess.claude_session_id == ids.JS, "the job's conversation is adopted"
    assert sess.status == Status.BUSY, "a fork's start does not close the turn"
    link = bg_jobs.read_own_json(bg_jobs.link_path(ids.JOB))
    assert link["session"] == ids.NAME and link["job"] == ids.JOB

    # 3. The monitor's tick: one notice with Bring it back now, once.
    stack.tick()
    stack.tick()
    notices = _notices(stack)
    assert len(notices) == 1
    assert notices[0].kwargs["text"] == EN.html(
        _msg("BG_PARK_NOTICE"), session=ids.LABEL)
    assert _buttons(notices[0]) == ["bgback" + ids.JOB]

    # 4. The moved turn ends in the job: the worker's Stop finishes it.
    stack.hook(hook_event_name="Stop", bg_job=ids.JOB, session_id=ids.JS,
               last_assistant_message="Done in the background.")
    assert sess.status == Status.IDLE
    assert "idle_prompt" in stack.events
    assert sess.parked_job == ids.JOB, "the job ending its turn is no bring-back"

    # 5. A spare's SessionStart (another job): dropped, nothing moves.
    before = (sess.claude_session_id, sess.status, sess.last_hook_at, len(stack.events))
    stack.hook(hook_event_name="SessionStart", source="startup", bg_job=ids.SPARE_JOB,
               session_id=ids.SPARE_ID)
    assert (sess.claude_session_id, sess.status, sess.last_hook_at,
            len(stack.events)) == before
    assert stack.registry.get(ids.SPARE_ID) is None

    # 6. A message while parked: held, never typed; it brings the session
    # back by itself (the job is idle now).
    stack.claude.reg(ids.WORKER, kind="bg", jobId=ids.JOB, sessionId=ids.JS,
                     status="idle")
    stack.message(mk_update, "carry on with the tests", msg_id=31)
    assert stack.typed == []
    assert [q[0] for q in sess.pending_queue] == ["carry on with the tests"]
    stack.run_spawned()
    assert stack.claude.runs == [["stop", ids.JOB]]
    assert stack.kinds() == ["kill", "launch"]
    assert stack.launches()[0]["resume_id"] == ids.JS
    assert not sess.parked_job
    assert not bg_jobs.link_path(ids.JOB).exists()
    back = EN.html(_msg("BG_BACK"), session=ids.LABEL)
    assert stack.texts().count(back) == 1
    # The relaunched claude comes up: the held message goes out, once.
    stack.claude.reg(ids.RELAUNCHED, kind="interactive", sessionId=ids.JS)
    stack.hook(hook_event_name="SessionStart", source="resume", session_id=ids.JS,
               claude_pid=ids.RELAUNCHED)
    assert len(stack.typed) == 1 and stack.typed[0].endswith("carry on with the tests")
    assert sess.pending_queue == []
    # Nothing parks it again: the old client's file went with the kill.
    stack.tick()
    assert not sess.parked_job and len(_notices(stack)) == 1


def test_the_button_on_a_busy_job_asks_then_stops_it(stack):
    ids, sess = stack.ids, stack.sess
    stack.hook(hook_event_name="Notification", notification_type="idle_prompt_seen",
               claude_pid=ids.CLIENT, session_id=ids.X)
    _park(stack, job_status="busy")
    stack.tick()
    assert sess.parked_job == ids.JOB and len(_notices(stack)) == 1

    # Bring it back now, while the job works: the card becomes the question.
    q = stack.tap("bgback")
    question = EN.html(_msg("BG_BUSY_QUESTION"), session=ids.LABEL)
    assert q.edit_message_text.await_args.args[0] == question
    kb = q.edit_message_text.await_args.kwargs["reply_markup"]
    assert [b.callback_data.rsplit(":", 1)[1] for row in kb.inline_keyboard
            for b in row] == ["bgforce" + ids.JOB, "bgleave"]
    assert stack.claude.runs == [] and stack.kinds() == []

    # Leave it: nothing happens.
    stack.tap("bgleave")
    assert stack.claude.runs == [] and sess.parked_job == ids.JOB

    # Stop and bring back: stop, kill, resume the job's conversation.
    stack.tap("bgforce")
    assert stack.claude.runs == [["stop", ids.JOB]]
    assert stack.kinds() == ["kill", "launch"]
    assert stack.launches()[0]["resume_id"] == ids.JS
    assert not sess.parked_job
    assert stack.texts().count(EN.html(_msg("BG_BACK"), session=ids.LABEL)) == 1


def test_a_resume_refused_because_a_job_holds_the_conversation(stack):
    """Resumed from the terminal while a background job still holds the
    conversation, Claude Code exits with code 1 right after starting. The
    notice says why and offers Bring it back; the tap stops the idle job
    and resumes the conversation here."""
    ids, sess = stack.ids, stack.sess
    sess.claude_session_id = ids.JS
    sess.transcript_path = str(stack.claude.transcript(ids.JS, {"type": "user"}))
    stack.claude.unreg(ids.CLIENT)
    stack.claude.reg(ids.WORKER, kind="bg", jobId=ids.JOB, sessionId=ids.JS, status="idle")
    stack.claude.stop_ends[ids.JOB] = ids.WORKER
    end = int(time.time())
    path = bg_jobs.exit_path(ids.NAME)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"code": 1, "start": end - 1, "end": end}))
    stack.registry.transition(ids.NAME, Status.IDLE)

    # The socket is gone at the next tick.
    stack.live.discard(ids.NAME)
    stack.tick()
    assert sess.status == Status.GONE
    assert "session_end" in stack.events
    ends = [c for c in stack.sent() if "stopped right after starting" in
            (c.kwargs.get("text") or (c.args[1] if len(c.args) > 1 else ""))]
    assert len(ends) == 1
    text = ends[0].kwargs.get("text") or ends[0].args[1]
    assert text == EN.html(_msg("SESSION_END_MSGS", "start_failed_bg"), session=ids.LABEL)
    assert _buttons(ends[0]) == ["bgback"]

    # Bring it back: the idle job is stopped, then the conversation resumed.
    stack.tap("bgback")
    assert stack.claude.runs == [["stop", ids.JOB]]
    assert stack.kinds()[-1] == "launch"
    assert stack.launches()[-1]["resume_id"] == ids.JS


def _msg(name, key=None):
    """A chat line's Msg, by its module constant."""
    from aipager import bg_text
    from aipager.bot import notify
    if key is not None:
        return getattr(notify, name)[key]
    return getattr(bg_text, name)
