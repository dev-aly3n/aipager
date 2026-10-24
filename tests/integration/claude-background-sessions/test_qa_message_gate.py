"""QA: a Telegram message to a parked session (P4, P5, S1, S5).

design.md success criterion 5: "A message from a permitted sender to a
parked session with an idle job brings it back and then runs as a normal
prompt; with a busy job the sender gets the question; others get the held
notice once." Criterion 4: no byte is written to a parked session's dtach.

Partitions:
- sender: may Restart (admin anywhere; a user inside the members' root) /
  may not (a user whose session works outside the members' root);
- job: idle / busy / unknown (Claude's registry unreadable: S5 says it
  counts as busy) / gone;
- bring-back: none yet / already in flight;
- per sender: first message / a later one (once per sender per park).
"""

from __future__ import annotations

import pytest

from aipager import claude_jobs
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

ADMIN, DEV, DEV2 = 555, 777, 778


@pytest.fixture
def team(stack, tmp_path):
    """The session's chat is a team: an admin, and two users whose members'
    root is somewhere else than the session's folder."""
    root = tmp_path / "team-root"
    root.mkdir()
    stack.bot.scopes = [Scope(
        chat_id=stack.ids.CHAT, kind="group", label="team",
        members=(Member(id=ADMIN, label="ada", role="admin"),
                 Member(id=DEV, label="bob", role="user"),
                 Member(id=DEV2, label="cy", role="user")),
        root=str(root))]
    return stack


@pytest.fixture
def team_inside(stack, tmp_path):
    """The same team, with the session inside the members' root."""
    stack.bot.scopes = [Scope(
        chat_id=stack.ids.CHAT, kind="group", label="team",
        members=(Member(id=ADMIN, label="ada", role="admin"),
                 Member(id=DEV, label="bob", role="user")),
        root=str(stack.cc.work.parent))]
    return stack


@pytest.fixture
def ordered(stack, monkeypatch):
    """One log of ``claude`` commands and dtach calls, in the order they ran."""
    inner = stack.claude.exec

    async def exec_(*argv, **kw):
        stack.dtach.append(("run", list(argv[1:])))
        return await inner(*argv, **kw)
    monkeypatch.setattr(claude_jobs, "_create_subprocess_exec", exec_)
    return stack


def _msg(stack, mk_update, text, msg_id, user=12345):
    return stack.message(mk_update, text, msg_id=msg_id, user_id=user)


def _queue(stack):
    return [q[0] for q in stack.sess.pending_queue]


# ---- idle job, a sender who may Restart: brought back by itself ----------------------

def test_the_message_is_never_typed_into_the_parked_client(stack, mk_update):
    qa.park_and_tick(stack)
    _msg(stack, mk_update, "carry on", 41)
    assert stack.typed == []


def test_the_message_is_held_in_the_queue(stack, mk_update):
    qa.park_and_tick(stack)
    _msg(stack, mk_update, "carry on", 41)
    assert _queue(stack) == ["carry on"]


def test_the_held_message_gets_the_eyes_reaction(stack, mk_update):
    qa.park_and_tick(stack)
    _msg(stack, mk_update, "carry on", 41)
    assert "👀" in qa.reactions(stack)


def test_an_idle_job_starts_one_bring_back(stack, mk_update):
    qa.park_and_tick(stack)
    _msg(stack, mk_update, "carry on", 41)
    assert len(stack.spawned) == 1


def test_the_bring_back_stops_the_job_before_killing_anything(ordered, mk_update):
    stack = ordered
    qa.park_and_tick(stack)
    _msg(stack, mk_update, "carry on", 41)
    stack.run_spawned()
    steps = [d[0] if d[0] != "run" else ("run", d[1][0]) for d in stack.dtach
             if d[0] != "sleep"]
    assert steps == [("run", "stop"), "kill", "launch"]


def test_the_bring_back_stops_the_linked_job(stack, mk_update):
    qa.park_and_tick(stack)
    _msg(stack, mk_update, "carry on", 41)
    stack.run_spawned()
    assert qa.stops(stack) == [["stop", stack.ids.JOB]]


def test_the_bring_back_resumes_the_jobs_conversation(stack, mk_update):
    qa.park_and_tick(stack)
    _msg(stack, mk_update, "carry on", 41)
    stack.run_spawned()
    assert [lc.get("resume_id") for lc in stack.launches()] == [stack.ids.JS]


def test_the_bring_back_keeps_the_sessions_mode(stack, mk_update):
    qa.park_and_tick(stack)
    _msg(stack, mk_update, "carry on", 41)
    stack.run_spawned()
    launch = stack.launches()[0]
    assert "auto" in [v for v in launch.values() if isinstance(v, str)]


def test_the_chat_hears_it_is_back_once(stack, mk_update):
    qa.park_and_tick(stack)
    _msg(stack, mk_update, "carry on", 41)
    stack.run_spawned()
    assert qa.count(qa.sent_texts(stack), qa.BACK) == 1


def test_the_held_message_goes_in_once_the_relaunched_claude_is_up(stack, mk_update):
    qa.park_and_tick(stack)
    _msg(stack, mk_update, "carry on", 41)
    stack.run_spawned()
    assert stack.typed == [], "nothing is typed before the new claude starts"
    qa.relaunched_start(stack)
    assert [t.endswith("carry on") for t in stack.typed] == [True]


def test_held_messages_go_out_in_order(stack, mk_update):
    qa.park_and_tick(stack)
    _msg(stack, mk_update, "first", 41)
    _msg(stack, mk_update, "second", 42)
    _msg(stack, mk_update, "third", 43)
    stack.run_spawned()
    qa.relaunched_start(stack)
    qa.own_turns(stack, 3)
    assert [t.rsplit("\n", 1)[-1] for t in stack.typed] == ["first", "second", "third"]


def test_two_quick_messages_bring_it_back_once(stack, mk_update):
    """B3: a second message before the first bring-back ran never stops,
    kills or launches twice."""
    qa.park_and_tick(stack)
    _msg(stack, mk_update, "first", 41)
    _msg(stack, mk_update, "second", 42)
    stack.run_spawned()
    assert (qa.stops(stack), stack.kinds()) == ([["stop", stack.ids.JOB]],
                                                ["kill", "launch"])


def test_two_quick_messages_say_it_is_back_once(stack, mk_update):
    qa.park_and_tick(stack)
    _msg(stack, mk_update, "first", 41)
    _msg(stack, mk_update, "second", 42)
    stack.run_spawned()
    assert qa.count(qa.sent_texts(stack), qa.BACK) == 1


def test_a_gone_job_is_brought_back_without_a_stop(stack, mk_update):
    """B4: the job's own registry entry is gone (registry readable)."""
    qa.park_and_tick(stack)
    stack.claude.unreg(stack.ids.WORKER)
    _msg(stack, mk_update, "carry on", 41)
    stack.run_spawned()
    assert (qa.stops(stack), [lc.get("resume_id") for lc in stack.launches()]) == (
        [], [stack.ids.JS])


# ---- busy job: the question, once per sender ----------------------------------------------

def test_a_busy_job_is_not_stopped(stack, mk_update):
    qa.park_and_tick(stack, job_status="busy")
    _msg(stack, mk_update, "carry on", 41)
    stack.run_spawned()
    assert (qa.stops(stack), stack.kinds()) == ([], [])


def test_a_busy_job_asks_the_sender(stack, mk_update):
    qa.park_and_tick(stack, job_status="busy")
    u = _msg(stack, mk_update, "carry on", 41)
    stack.run_spawned()
    assert qa.count(qa.all_texts(stack, u), qa.BUSY_QUESTION) == 1


def test_the_question_offers_stop_and_bring_back_or_leave_it(stack, mk_update):
    qa.park_and_tick(stack, job_status="busy")
    _msg(stack, mk_update, "carry on", 41)
    stack.run_spawned()
    calls = qa.calls_with_text(stack, "is still working in Claude Code's background")
    assert [qa.buttons(c) for c in calls] == [["bgforce" + stack.ids.JOB, "bgleave"]]


def test_a_busy_job_asks_each_sender_once(team, mk_update):
    stack = team
    qa.park_and_tick(stack, job_status="busy")
    a = _msg(stack, mk_update, "one", 41, user=ADMIN)
    stack.run_spawned()
    b = _msg(stack, mk_update, "two", 42, user=ADMIN)
    stack.run_spawned()
    assert qa.count(qa.all_texts(stack, a, b), qa.BUSY_QUESTION) == 1


def test_a_busy_job_keeps_every_message_held(stack, mk_update):
    qa.park_and_tick(stack, job_status="busy")
    _msg(stack, mk_update, "one", 41)
    stack.run_spawned()
    _msg(stack, mk_update, "two", 42)
    stack.run_spawned()
    assert (_queue(stack), stack.typed) == (["one", "two"], [])


def test_an_unknown_job_state_counts_as_busy(stack, mk_update):
    """S5: Claude's registry unreadable and no ``claude agents`` listing:
    nothing is stopped or killed without a force."""
    qa.park_and_tick(stack)
    stack.claude.sessions.chmod(0)
    try:
        _msg(stack, mk_update, "carry on", 41)
        stack.run_spawned()
    finally:
        stack.claude.sessions.chmod(0o755)
    assert (qa.stops(stack), stack.kinds(), stack.typed) == ([], [], [])


# ---- a sender who may not Restart: held, one notice per sender ---------------------------

def test_a_sender_who_may_not_restart_starts_no_bring_back(team, mk_update):
    stack = team
    qa.park_and_tick(stack)
    _msg(stack, mk_update, "carry on", 41, user=DEV)
    stack.run_spawned()
    assert (qa.stops(stack), stack.kinds()) == ([], [])


def test_a_sender_who_may_not_restart_is_held(team, mk_update):
    stack = team
    qa.park_and_tick(stack)
    _msg(stack, mk_update, "carry on", 41, user=DEV)
    assert (_queue(stack), stack.typed) == (["carry on"], [])


def test_a_sender_who_may_not_restart_gets_the_held_notice(team, mk_update):
    stack = team
    qa.park_and_tick(stack)
    u = _msg(stack, mk_update, "carry on", 41, user=DEV)
    assert qa.count(qa.all_texts(stack, u), qa.HELD_NOTICE) == 1


def test_the_held_notice_is_sent_once_per_sender(team, mk_update):
    stack = team
    qa.park_and_tick(stack)
    a = _msg(stack, mk_update, "one", 41, user=DEV)
    b = _msg(stack, mk_update, "two", 42, user=DEV)
    c = _msg(stack, mk_update, "three", 43, user=DEV)
    assert qa.count(qa.all_texts(stack, a, b, c), qa.HELD_NOTICE) == 1


def test_another_sender_gets_their_own_held_notice(team, mk_update):
    stack = team
    qa.park_and_tick(stack)
    a = _msg(stack, mk_update, "one", 41, user=DEV)
    b = _msg(stack, mk_update, "two", 42, user=DEV2)
    assert qa.count(qa.all_texts(stack, a, b), qa.HELD_NOTICE) == 2


def test_the_held_notice_comes_again_after_a_new_park(team, mk_update):
    stack = team
    qa.park_and_tick(stack)
    a = _msg(stack, mk_update, "one", 41, user=DEV)
    qa.park(stack, job=stack.ids.SPARE_JOB, js=stack.ids.SPARE_ID, worker=4400)
    stack.tick()
    b = _msg(stack, mk_update, "two", 42, user=DEV)
    assert qa.count(qa.all_texts(stack, a, b), qa.HELD_NOTICE) == 2


def test_an_admin_brings_back_a_session_outside_the_members_root(team, mk_update):
    stack = team
    qa.park_and_tick(stack)
    _msg(stack, mk_update, "carry on", 41, user=ADMIN)
    stack.run_spawned()
    assert qa.stops(stack) == [["stop", stack.ids.JOB]]


def test_a_user_inside_the_members_root_brings_it_back(team_inside, mk_update):
    stack = team_inside
    qa.park_and_tick(stack)
    _msg(stack, mk_update, "carry on", 41, user=DEV)
    stack.run_spawned()
    assert qa.stops(stack) == [["stop", stack.ids.JOB]]


# ---- the queue is not drained into a parked client ----------------------------------------

def test_a_linked_turn_end_does_not_drain_the_held_queue(stack, mk_update):
    qa.park_and_tick(stack, job_status="busy")
    _msg(stack, mk_update, "carry on", 41)
    stack.run_spawned()
    stack.hook(hook_event_name="Stop", bg_job=stack.ids.JOB, session_id=stack.ids.JS,
               last_assistant_message="done")
    assert (_queue(stack), stack.typed) == (["carry on"], [])


# ---- the session ends instead ---------------------------------------------------------------

def test_held_messages_are_dropped_with_a_shrug_when_the_session_ends(stack, mk_update):
    qa.park_and_tick(stack, job_status="busy")
    _msg(stack, mk_update, "carry on", 41)
    stack.run_spawned()
    qa.vanish(stack)
    assert stack.sess.status == Status.GONE, "precondition"
    assert ("🤷" in qa.reactions(stack), _queue(stack)) == (True, [])


def test_a_parked_session_that_ends_is_no_longer_parked(stack, mk_update):
    qa.park_and_tick(stack, job_status="busy")
    qa.vanish(stack)
    assert not stack.sess.parked_job


def test_a_parked_session_that_ends_leaves_no_link(stack):
    qa.park_and_tick(stack, job_status="busy")
    qa.vanish(stack)
    assert qa.link(stack.ids.JOB) is None


def test_another_permitted_sender_gets_their_own_question(team_inside, mk_update):
    stack = team_inside
    qa.park_and_tick(stack, job_status="busy")
    a = _msg(stack, mk_update, "one", 41, user=ADMIN)
    stack.run_spawned()
    b = _msg(stack, mk_update, "two", 42, user=DEV)
    stack.run_spawned()
    assert qa.count(qa.all_texts(stack, a, b), qa.BUSY_QUESTION) == 2


def test_an_unknown_job_state_asks_the_question(stack, mk_update):
    """S5: unknown counts as busy, so the sender is asked (P5)."""
    qa.park_and_tick(stack)
    stack.claude.sessions.chmod(0)
    try:
        u = _msg(stack, mk_update, "carry on", 41)
        stack.run_spawned()
    finally:
        stack.claude.sessions.chmod(0o755)
    assert qa.count(qa.all_texts(stack, u), qa.BUSY_QUESTION) == 1


def test_an_unknown_job_state_keeps_the_message_held(stack, mk_update):
    qa.park_and_tick(stack)
    stack.claude.sessions.chmod(0)
    try:
        _msg(stack, mk_update, "carry on", 41)
        stack.run_spawned()
    finally:
        stack.claude.sessions.chmod(0o755)
    assert _queue(stack) == ["carry on"]


# ---- error guessing: the auto bring-back fails or races ---------------------------------------

def _stop_rc_1(stack, monkeypatch):
    inner = stack.claude.exec

    async def exec_(*argv, **kw):
        if list(argv[1:2]) == ["stop"]:
            stack.claude.runs.append(list(argv[1:]))

            class Proc:
                returncode = None

                async def communicate(self, _stdin=None):
                    self.returncode = 1
                    return b"", b""

                def kill(self):
                    pass
            return Proc()
        return await inner(*argv, **kw)
    monkeypatch.setattr(claude_jobs, "_create_subprocess_exec", exec_)


def test_a_failed_auto_bring_back_tells_the_sender(stack, mk_update, monkeypatch):
    qa.park_and_tick(stack)
    _stop_rc_1(stack, monkeypatch)
    u = _msg(stack, mk_update, "carry on", 41)
    stack.run_spawned()
    shown = qa.all_texts(stack, u)
    assert any(t.startswith("Could not bring <b>frontend</b> back from Claude Code's "
                            "background: ") for t in shown), shown


def test_a_failed_auto_bring_back_keeps_the_message_held(stack, mk_update, monkeypatch):
    qa.park_and_tick(stack)
    _stop_rc_1(stack, monkeypatch)
    _msg(stack, mk_update, "carry on", 41)
    stack.run_spawned()
    assert (_queue(stack), stack.typed, stack.kinds()) == (["carry on"], [], [])


def test_no_saved_conversation_tells_the_sender(stack, mk_update):
    qa.park_and_tick(stack)
    (stack.claude.projects / f"{stack.ids.JS}.jsonl").unlink()
    (stack.claude.projects / f"{stack.ids.X}.jsonl").unlink()
    u = _msg(stack, mk_update, "carry on", 41)
    stack.run_spawned()
    want = ("Could not bring <b>frontend</b> back because Claude Code has no saved "
            "conversation for it.")
    assert (want in qa.all_texts(stack, u), stack.kinds()) == (True, [])


def test_a_button_tap_racing_the_auto_bring_back_brings_it_back_once(stack, mk_update):
    from unittest.mock import AsyncMock, MagicMock

    from aipager.bot import session_parity
    qa.park_and_tick(stack)
    _msg(stack, mk_update, "carry on", 41)      # spawned, not yet run
    q = MagicMock()
    q.from_user = MagicMock(id=12345)
    q.message = MagicMock(chat=MagicMock(id=stack.ids.CHAT), chat_id=stack.ids.CHAT,
                          message_id=77)
    q.answer = AsyncMock()
    q.edit_message_text = AsyncMock()
    q.edit_message_reply_markup = AsyncMock()
    u = MagicMock(callback_query=q, effective_chat=MagicMock(id=stack.ids.CHAT),
                  effective_user=MagicMock(id=12345))
    stack.run(session_parity.handle_callback(stack.bot, u, q, stack.ids.NAME,
                                             "bgback" + stack.ids.JOB))
    stack.run_spawned()
    assert (qa.stops(stack), stack.kinds()) == ([["stop", stack.ids.JOB]], ["kill", "launch"])


def test_a_button_tap_racing_the_auto_bring_back_still_delivers_the_message(
        stack, mk_update):
    from unittest.mock import AsyncMock, MagicMock

    from aipager.bot import session_parity
    qa.park_and_tick(stack)
    _msg(stack, mk_update, "carry on", 41)
    q = MagicMock()
    q.from_user = MagicMock(id=12345)
    q.message = MagicMock(chat=MagicMock(id=stack.ids.CHAT), chat_id=stack.ids.CHAT,
                          message_id=77)
    q.answer = AsyncMock()
    q.edit_message_text = AsyncMock()
    q.edit_message_reply_markup = AsyncMock()
    u = MagicMock(callback_query=q, effective_chat=MagicMock(id=stack.ids.CHAT),
                  effective_user=MagicMock(id=12345))
    stack.run(session_parity.handle_callback(stack.bot, u, q, stack.ids.NAME,
                                             "bgback" + stack.ids.JOB))
    stack.run_spawned()
    qa.relaunched_start(stack)
    assert [t.endswith("carry on") for t in stack.typed] == [True]


# ---- held messages from everyone go out in order after any bring-back -------------------------

def _drain(stack, n):
    qa.relaunched_start(stack)
    qa.own_turns(stack, n)
    return [t.rsplit("\n", 1)[-1] for t in stack.typed]


def test_a_held_message_from_someone_who_may_not_restart_goes_out_first(team, mk_update):
    stack = team
    qa.park_and_tick(stack)
    _msg(stack, mk_update, "from bob", 41, user=DEV)
    _msg(stack, mk_update, "from ada", 42, user=ADMIN)
    stack.run_spawned()
    assert _drain(stack, 2) == ["from bob", "from ada"]


def test_stop_and_bring_back_releases_the_held_messages_in_order(stack, mk_update):
    from unittest.mock import AsyncMock, MagicMock

    from aipager.bot import session_parity
    qa.park_and_tick(stack, job_status="busy")
    _msg(stack, mk_update, "one", 41)
    _msg(stack, mk_update, "two", 42)
    stack.run_spawned()
    q = MagicMock()
    q.from_user = MagicMock(id=12345)
    q.message = MagicMock(chat=MagicMock(id=stack.ids.CHAT), chat_id=stack.ids.CHAT,
                          message_id=77)
    q.answer = AsyncMock()
    q.edit_message_text = AsyncMock()
    q.edit_message_reply_markup = AsyncMock()
    u = MagicMock(callback_query=q, effective_chat=MagicMock(id=stack.ids.CHAT),
                  effective_user=MagicMock(id=12345))
    stack.run(session_parity.handle_callback(stack.bot, u, q, stack.ids.NAME,
                                             "bgforce" + stack.ids.JOB))
    assert _drain(stack, 2) == ["one", "two"]


def test_a_forced_restart_releases_the_held_messages_in_order(stack, mk_update):
    qa.park_and_tick(stack, job_status="busy")
    _msg(stack, mk_update, "one", 41)
    _msg(stack, mk_update, "two", 42)
    stack.run_spawned()
    assert stack.run(stack.bot._restart_session_core(stack.sess, force=True)).ok
    assert _drain(stack, 2) == ["one", "two"]
