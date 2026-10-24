"""QA: noticing a park (P1-P3, P6, S7), black-box through the wired daemon.

design.md success criterion 3: "Within one monitor tick of ``parkedJobId``
(or on the job's first datagram) the session is parked, the link and pid
map exist, and one notice with 'Bring it back now' is posted."

Partitions: the client's registry entry has ``parkedJobId`` (parks) or not
(clears); no client entry, transcript whose LAST record is ``continued-in``
(parks) or has records after it (does not); registry unreadable or broken
(S7: nothing changes); the job's own entry vanishing (sticky, stays
parked); a second job (a second notice); a daemon restart (no repeat).
"""

from __future__ import annotations

import os
import stat
import time

from aipager.dtach import bg_jobs
from aipager.session_monitor import SessionMonitor
from aipager.state import SessionRegistry


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


# ---- parkedJobId parks within one tick ---------------------------------------------

def test_parked_job_id_parks_the_session_within_one_tick(stack):
    qa.park(stack)
    stack.tick()
    assert stack.sess.parked_job == stack.ids.JOB


def test_the_parked_session_records_the_jobs_conversation(stack):
    qa.park(stack)
    stack.tick()
    assert stack.sess.parked_job_session_id == stack.ids.JS


def test_the_parked_session_records_when_it_parked(stack):
    before = time.time()
    qa.park(stack)
    stack.tick()
    assert before - 1 <= stack.sess.parked_since <= time.time() + 1


def test_the_link_file_names_the_session_and_job(stack):
    qa.park(stack)
    stack.tick()
    link = qa.link(stack.ids.JOB)
    assert (link or {}).get("session"), link
    assert (link["job"], link["session"], link["job_session_id"]) == (
        stack.ids.JOB, stack.ids.NAME, stack.ids.JS)


def test_the_link_file_is_private(stack):
    qa.park(stack)
    stack.tick()
    mode = stat.S_IMODE(os.stat(bg_jobs.link_path(stack.ids.JOB)).st_mode)
    assert mode == 0o600


def test_the_pid_map_maps_the_client_to_the_session(stack):
    qa.own_pid_hook(stack)
    qa.park(stack)
    stack.tick()
    pids = (qa.pidmap() or {}).get("pids", {})
    assert pids.get(str(stack.ids.CLIENT), {}).get("session") == stack.ids.NAME


def test_the_pid_map_is_written_without_a_pid_hook(stack):
    """After a daemon restart no own hook has reported the pid: the client is
    found in the registry by its conversation (P1 fallback) and mapped."""
    qa.park(stack)
    stack.tick()
    pids = (qa.pidmap() or {}).get("pids", {})
    assert pids.get(str(stack.ids.CLIENT), {}).get("session") == stack.ids.NAME


def test_one_notice_is_posted_however_many_ticks(stack):
    qa.park(stack)
    for _ in range(4):
        stack.tick()
    assert qa.count(qa.sent_texts(stack), qa.PARK_NOTICE) == 1


def test_the_notice_carries_bring_it_back_now(stack):
    qa.park(stack)
    stack.tick()
    calls = qa.calls_with_text(stack, "moved to Claude Code's background")
    assert [qa.buttons(c) for c in calls] == [["bgback" + stack.ids.JOB]]


def test_the_notice_button_reads_bring_it_back_now(stack):
    qa.park(stack)
    stack.tick()
    call = qa.calls_with_text(stack, "moved to Claude Code's background")[0]
    labels = [b.text for row in call.kwargs["reply_markup"].inline_keyboard for b in row]
    assert labels == ["↩️ Bring it back now"]


def test_the_notice_goes_to_the_sessions_chat(stack):
    qa.park(stack)
    stack.tick()
    call = qa.calls_with_text(stack, "moved to Claude Code's background")[0]
    assert call.kwargs.get("chat_id", call.args[0] if call.args else None) == stack.ids.CHAT


def test_a_second_job_gets_its_own_notice(stack):
    qa.park(stack)
    stack.tick()
    qa.park(stack, job=stack.ids.SPARE_JOB, js=stack.ids.SPARE_ID, worker=4400)
    stack.tick()
    assert stack.sess.parked_job == stack.ids.SPARE_JOB
    assert qa.count(qa.sent_texts(stack), qa.PARK_NOTICE) == 2


def test_the_first_linked_datagram_parks_before_any_tick(stack):
    """The job's first hook can beat the tick (Fact 7, about 0.5 s): a
    worker ``Stop`` as the first thing the daemon hears still counts."""
    qa.park(stack)
    stack.hook(hook_event_name="Stop", bg_job=stack.ids.JOB, session_id=stack.ids.JS,
               last_assistant_message="done")
    assert stack.sess.parked_job == stack.ids.JOB


# ---- clearing and stickiness -------------------------------------------------------

def test_parked_job_id_gone_from_the_client_clears_the_park(stack):
    qa.park_and_tick(stack)
    stack.claude.reg(stack.ids.CLIENT, kind="interactive", sessionId=stack.ids.X,
                     status="idle")
    stack.tick()
    assert not stack.sess.parked_job


def test_clearing_the_park_removes_the_link(stack):
    qa.park_and_tick(stack)
    stack.claude.reg(stack.ids.CLIENT, kind="interactive", sessionId=stack.ids.X,
                     status="idle")
    stack.tick()
    assert qa.link(stack.ids.JOB) is None


def test_the_job_ending_does_not_clear_the_park(stack):
    """``parkedJobId`` is sticky (Fact 7): the job's own entry going away is
    no bring-back."""
    qa.park_and_tick(stack)
    stack.claude.unreg(stack.ids.WORKER)
    stack.tick()
    assert stack.sess.parked_job == stack.ids.JOB


# ---- the transcript fallback -------------------------------------------------------

def test_no_client_entry_and_a_final_continued_in_parks(stack):
    stack.claude.unreg(stack.ids.CLIENT)
    stack.claude.append(stack.ids.X, {"type": "continued-in",
                                      "continuedInSessionId": stack.ids.JS})
    stack.claude.transcript(stack.ids.JS, {"type": "user"})
    stack.tick()
    assert stack.sess.parked_job == stack.ids.JOB


def test_a_continued_in_followed_by_records_does_not_park(stack):
    stack.claude.unreg(stack.ids.CLIENT)
    stack.claude.append(stack.ids.X,
                        {"type": "continued-in", "continuedInSessionId": stack.ids.JS},
                        {"type": "user"}, {"type": "assistant"})
    stack.claude.transcript(stack.ids.JS, {"type": "user"})
    stack.tick()
    assert not stack.sess.parked_job


def test_a_transcript_without_continued_in_does_not_park(stack):
    stack.claude.unreg(stack.ids.CLIENT)
    stack.claude.append(stack.ids.X, {"type": "assistant"})
    stack.tick()
    assert not stack.sess.parked_job


# ---- S7: unreadable Claude files ----------------------------------------------------

def test_an_unreadable_registry_changes_nothing(stack):
    sessions = stack.claude.sessions
    before = stack.sess.status
    sessions.chmod(0)
    try:
        stack.tick()
        state = (stack.sess.parked_job, stack.sess.status, stack.sess.claude_session_id)
    finally:
        sessions.chmod(0o755)
    assert state == ("", before, stack.ids.X) and not stack.sent()


def test_a_broken_registry_file_changes_nothing(stack):
    (stack.claude.sessions / f"{stack.ids.CLIENT}.json").write_text('{"pid": 41')
    stack.tick()
    assert (stack.sess.parked_job, qa.link(stack.ids.JOB), stack.sent()) == ("", None, [])


def test_a_missing_registry_folder_changes_nothing(stack):
    for f in stack.claude.sessions.iterdir():
        f.unlink()
    stack.claude.sessions.rmdir()
    stack.tick()
    assert (stack.sess.parked_job, stack.sent()) == ("", [])


def test_a_session_that_never_parks_gets_no_notice_and_no_link(stack):
    for _ in range(3):
        stack.tick()
    assert (stack.sess.parked_job, qa.link(stack.ids.JOB), stack.sent()) == ("", None, [])


# ---- error guessing: a job id that is not a job id -----------------------------------

def test_a_parked_job_id_with_a_path_in_it_writes_nothing_outside_the_runtime_folder(
        stack, tmp_path):
    rt = bg_jobs.link_path(stack.ids.JOB).parent
    stack.claude.reg(stack.ids.CLIENT, kind="interactive", sessionId=stack.ids.X,
                     status="idle", parkedJobId="../../qa-evil")
    stack.tick()
    escaped = [p for p in tmp_path.rglob("*qa-evil*")
               if rt.resolve() not in p.resolve().parents]
    assert escaped == []


def test_a_parked_job_id_with_a_path_in_it_is_not_a_park(stack):
    stack.claude.reg(stack.ids.CLIENT, kind="interactive", sessionId=stack.ids.X,
                     status="idle", parkedJobId="../../qa-evil")
    stack.tick()
    assert not stack.sess.parked_job


# ---- persistence and the daemon restart ----------------------------------------------

def test_the_park_survives_save_and_load(stack):
    qa.park_and_tick(stack)
    stack.registry.save()
    loaded = SessionRegistry()
    loaded.load()
    got = loaded.get(stack.ids.NAME)
    assert (got.parked_job, got.parked_job_session_id, got.park_notified_job) == (
        stack.ids.JOB, stack.ids.JS, stack.ids.JOB)


def test_no_second_notice_after_a_daemon_restart(stack):
    qa.park_and_tick(stack)
    stack.registry.save()
    loaded = SessionRegistry()
    loaded.load()

    async def notify(sess, event, context=None):
        await stack.bot.notify(sess, event, context or {})
    monitor = SessionMonitor(loaded, notify)
    stack.run(monitor.tick())
    stack.run(monitor.tick())
    assert qa.count(qa.sent_texts(stack), qa.PARK_NOTICE) == 1


# ---- cleanup of the runtime files ---------------------------------------------------------

def test_a_session_that_goes_gone_leaves_no_pid_map_entry(stack):
    qa.own_pid_hook(stack)
    qa.park_and_tick(stack)
    stack.live.discard(stack.ids.NAME)
    stack.tick()
    pids = (qa.pidmap() or {}).get("pids", {})
    assert str(stack.ids.CLIENT) not in pids


def test_removing_a_parked_session_removes_its_link(stack):
    qa.park_and_tick(stack)
    stack.registry.remove(stack.ids.NAME)
    assert qa.link(stack.ids.JOB) is None


def test_the_client_on_another_conversation_without_a_park_clears_it(stack):
    """P2: the client's entry shows another ``sessionId`` and no
    ``parkedJobId`` (someone resumed something else at the terminal)."""
    qa.own_pid_hook(stack)
    qa.park_and_tick(stack)
    stack.claude.reg(stack.ids.CLIENT, kind="interactive",
                     sessionId="dddddddd-0000-4000-8000-00000000000d", status="idle")
    stack.tick()
    assert not stack.sess.parked_job
