"""Why a vanished session ended (background-jobs step 4, E2/E3).

When a session's socket vanishes, the monitor reads the exit record the
launch shell left (``claude_launch.exit_record``) and the session-end
notice says why: no record reads as today's "crashed or killed"; a
non-zero code within ``START_WINDOW_SECONDS`` of the start "stopped right
after starting" (with a Bring it back button when a Claude Code
background job holds the conversation); 128+N is a signal by name; any
other non-zero code is an error code; 0 is "exited". A record older than
``EXIT_FRESH_SECONDS`` belongs to an earlier launch. A restart and /kill
still send nothing (E3).

The monitor runs its real ``_scan`` with its ``notify_fn`` wired to a
real bot (Telegram mocked), so each test goes from the record on disk to
the line in the chat. Exit records live under tmp_path (the conftest
redirects ``bg_jobs.exit_path``); Claude's registry is a folder under
tmp_path (``claude_jobs.config_dir`` seam).
"""

from __future__ import annotations

import json
import logging
import time

import pytest

from aipager import claude_jobs
from aipager.bot import notify as notify_mod
from aipager.dtach import bg_jobs
from aipager.session_monitor import SessionMonitor
from aipager.state import SessionRegistry, Status, TrackedSession

SID = "0f8b2a6e-1c3d-4e5f-8a9b-0c1d2e3f4a5b"
JOB_PID = 4242


async def _no_sessions():
    return []


@pytest.fixture
def world(mk_bot, monkeypatch, tmp_path):
    registry = SessionRegistry()
    bot = mk_bot(registry)
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    sess.claude_session_id = SID
    registry._sessions["claude-jim"] = sess
    monitor = SessionMonitor(registry, bot.notify)
    monkeypatch.setattr("aipager.dtach.inject.list_sessions", _no_sessions)
    cfg = tmp_path / "claude-config"
    (cfg / "sessions").mkdir(parents=True)
    monkeypatch.setattr(claude_jobs, "config_dir", lambda: cfg)
    monkeypatch.setattr(claude_jobs, "_pid_alive", lambda pid: pid == JOB_PID)

    class World:
        pass
    w = World()
    w.bot, w.sess, w.monitor, w.registry, w.cfg = bot, sess, monitor, registry, cfg
    return w


def _record(code, *, ran=2, age=1):
    """The launch shell's record: claude ran *ran* seconds and ended *age*
    seconds ago."""
    end = int(time.time()) - age
    path = bg_jobs.exit_path("claude-jim")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"code": code, "start": end - ran, "end": end}))


def _holder(cfg, session_id=SID, kind="bg"):
    (cfg / "sessions" / f"{JOB_PID}.json").write_text(json.dumps({
        "pid": JOB_PID, "sessionId": session_id, "kind": kind,
        "jobId": "ab12cd34", "status": "idle"}))


def _scan(w, run_async):
    run_async(w.monitor._scan())
    sends = w.bot._app.bot.send_message.await_args_list
    return [(c.args[1], c.kwargs.get("reply_markup")) for c in sends]


# ---- T4.4: what the record says --------------------------------------------------

def test_no_record_is_crashed_or_killed(world, run_async):
    sent = _scan(world, run_async)
    assert world.sess.status == Status.GONE
    assert [t for t, _ in sent] == ["🔴 <b>jim</b> · Session crashed or killed"]


@pytest.mark.parametrize("code,ran,text", [
    (1, 2, "🔴 <b>jim</b> · Claude Code stopped right after starting (exit code 1)"),
    (1, 10, "🔴 <b>jim</b> · Claude Code stopped right after starting (exit code 1)"),
    (1, 11, "🔴 <b>jim</b> · Session exited with error code 1"),
    (137, 2, "🔴 <b>jim</b> · Session killed by signal SIGKILL"),
    (143, 600, "🔴 <b>jim</b> · Session killed by signal SIGTERM"),
    (2, 60, "🔴 <b>jim</b> · Session exited with error code 2"),
    (0, 2, "🔴 <b>jim</b> · Session exited"),
    (0, 600, "🔴 <b>jim</b> · Session exited"),
], ids=["1-in-2s", "1-at-10s", "1-at-11s", "sigkill", "sigterm-late", "2-after-60s",
        "0-early", "0-late"])
def test_the_notice_says_why(world, run_async, code, ran, text):
    _record(code, ran=ran)
    sent = _scan(world, run_async)
    assert sent == [(text, None)]


def test_a_stale_record_is_crashed_or_killed(world, run_async):
    _record(1, ran=2, age=130)
    sent = _scan(world, run_async)
    assert [t for t, _ in sent] == ["🔴 <b>jim</b> · Session crashed or killed"]


def test_a_record_just_inside_the_freshness_counts(world, run_async):
    _record(2, ran=60, age=110)
    sent = _scan(world, run_async)
    assert [t for t, _ in sent] == ["🔴 <b>jim</b> · Session exited with error code 2"]


def test_the_log_line_carries_the_code(world, run_async, caplog):
    _record(137, ran=5)
    with caplog.at_level(logging.INFO, logger="aipager.session_monitor"):
        _scan(world, run_async)
    assert "[claude-jim] socket vanished: claude exit code 137 after 5s" in caplog.messages


def test_the_log_line_says_when_there_is_no_record(world, run_async, caplog):
    with caplog.at_level(logging.INFO, logger="aipager.session_monitor"):
        _scan(world, run_async)
    assert "[claude-jim] socket vanished: no exit record from claude" in caplog.messages


def test_the_notice_reads_in_the_sessions_language(world, run_async, monkeypatch):
    from aipager import i18n
    monkeypatch.setattr(notify_mod.i18n, "for_sess", lambda sess: i18n.get("de"))
    _record(137)
    sent = _scan(world, run_async)
    assert sent == [("🔴 <b>jim</b> · Sitzung durch Signal SIGKILL beendet", None)]


# ---- T4.5: a start failure while a background job holds the conversation ------------

def test_a_held_conversation_gives_start_failed_bg_with_bring_it_back(world, run_async):
    _holder(world.cfg)
    _record(1, ran=1)
    sent = _scan(world, run_async)
    assert len(sent) == 1
    text, markup = sent[0]
    assert text == ("🔴 <b>jim</b> · Claude Code stopped right after starting because its "
                    "conversation is running in Claude Code's background")
    (button,), = markup.inline_keyboard
    assert button.callback_data.rsplit(":", 1)[1] == "bgback"
    assert button.text == "↩️ Bring it back now"


@pytest.mark.parametrize("why", ["dead", "interactive", "other-conversation"])
def test_no_live_holder_is_a_plain_start_failure(world, run_async, monkeypatch, why):
    if why == "dead":
        _holder(world.cfg)
        monkeypatch.setattr(claude_jobs, "_pid_alive", lambda pid: False)
    elif why == "interactive":
        _holder(world.cfg, kind="interactive")
    else:
        _holder(world.cfg, session_id="9a8b7c6d-0000-4000-8000-000000000000")
    _record(1, ran=1)
    sent = _scan(world, run_async)
    assert sent == [("🔴 <b>jim</b> · Claude Code stopped right after starting "
                     "(exit code 1)", None)]


def test_a_holder_does_not_change_a_late_error(world, run_async):
    """The holder only explains a claude that stopped at start."""
    _holder(world.cfg)
    _record(1, ran=30)
    sent = _scan(world, run_async)
    assert sent == [("🔴 <b>jim</b> · Session exited with error code 1", None)]


def test_a_resumes_cleared_id_is_found_through_the_transcript(world, run_async):
    """A resume clears the id before its launch; the transcript still names
    the conversation, and the id comes back so Bring it back can resume it."""
    world.sess.claude_session_id = ""
    world.sess.transcript_path = f"/home/alice/.claude/projects/p/{SID}.jsonl"
    _holder(world.cfg)
    _record(1, ran=1)
    sent = _scan(world, run_async)
    assert sent[0][1] is not None
    assert world.sess.claude_session_id == SID


def test_without_a_holder_a_cleared_id_stays_cleared(world, run_async):
    world.sess.claude_session_id = ""
    world.sess.transcript_path = f"/home/alice/.claude/projects/p/{SID}.jsonl"
    _record(1, ran=1)
    _scan(world, run_async)
    assert world.sess.claude_session_id == ""


def test_a_conversation_that_moved_to_a_held_one_counts(world, run_async, tmp_path):
    """The session's id moved on (``continued-in``) to a conversation a job
    holds: that job is what is in the way."""
    moved = "7d6e5f40-1111-4222-8333-444455556666"
    projects = world.cfg / "projects" / "p"
    projects.mkdir(parents=True)
    (projects / f"{SID}.jsonl").write_text(
        json.dumps({"type": "user"}) + "\n"
        + json.dumps({"type": "continued-in", "continuedInSessionId": moved}) + "\n")
    (projects / f"{moved}.jsonl").write_text(json.dumps({"type": "user"}) + "\n")
    _holder(world.cfg, session_id=moved)
    _record(1, ran=1)
    sent = _scan(world, run_async)
    assert sent[0][1] is not None


# ---- T4.6 (E3, unchanged): a restart and /kill send nothing ---------------------------

def test_a_restart_sends_no_notice_whatever_the_record(world, run_async):
    world.sess.restarting_until = time.monotonic() + 10
    _record(143, ran=1)
    assert _scan(world, run_async) == []
    assert world.sess.status == Status.IDLE


def test_a_killed_session_sends_no_notice(world, run_async):
    """/kill records the killed process's id; its SessionEnd is quiet."""
    world.registry.killed_sessions[SID] = time.monotonic()
    _record(143, ran=1)
    run_async(world.bot.notify(world.sess, "session_end",
                               {"source": "other", "session_id": SID}))
    world.bot._app.bot.send_message.assert_not_awaited()


def test_a_killed_session_out_of_the_registry_is_not_announced(world, run_async):
    """/kill removes the entry, so a vanished socket finds no session."""
    world.registry._sessions.pop("claude-jim")
    _record(143, ran=1)
    assert _scan(world, run_async) == []


def test_a_hooks_session_end_ignores_the_record(world, run_async):
    """Only a vanished socket reads the record: a SessionEnd says its own
    reason."""
    run_async(world.bot.notify(world.sess, "session_end", {
        "source": "logout",
        "exit": {"code": 137, "start": 1, "end": 2}}))
    text = world.bot._app.bot.send_message.await_args.args[1]
    assert text == "🔴 <b>jim</b> · Session logged out"
