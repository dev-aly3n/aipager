"""Roadmap 8.22 — a subagent is stale when it goes SILENT, not when it
gets old.

The incident these tests pin (2026-09-11 17:56–17:58 UTC, the operator's
own session, verified in the journal): a `/ship` pipeline's
``ship-developer`` started at 16:56:01 and was still working at 18:00. At
17:56:02 the age sweep logged ``dropping stale subagent a48ff0cc7f1e0ddd3
(no Stop hook in 60 min)`` and emptied ``active_subagents``; 113 seconds
later idle-recovery saw a session with no open job, a transcript that
looked finished and quiet, and published ``Finished (95m 2s)`` with every
interim update merged — while the agent was alive, and that merged card
is what the operator reads as the result.

The evidence to prevent it was already arriving: every hook event fired
inside a subagent carries its ``agent_id``, several times a minute for as
long as the agent works. Nothing looked at it.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from unittest.mock import AsyncMock

import pytest

from aipager.dtach import hook_receiver as hr
from aipager.session_monitor import (
    IDLE_RECOVERY_GRACE,
    SUBAGENT_SILENCE_DEFAULT,
    SUBAGENT_SILENCE_SECONDS,
    SessionMonitor,
    _resolve_silence_window,
)
from aipager.state import SessionRegistry, Status, TrackedSession

SESSION = "claude-jim"


async def _coroutine_returning(value):
    return value


def _mk_monitor(registry, notify=None):
    async def _noop(*a, **kw):
        return None
    return SessionMonitor(registry, notify or _noop)


def _only_sessions(monkeypatch, names=(SESSION,)):
    monkeypatch.setattr(
        "aipager.dtach.inject.list_sessions",
        lambda: _coroutine_returning(list(names)),
    )


# A turn whose tail reads as finished — exactly what the lazily-written
# transcript shows during a long background agent's run, because the
# INTERIM turn really did end.
_COMPLETE = [
    {"type": "user", "message": {"role": "user", "content": "run the pipeline"}},
    {"type": "assistant", "message": {
        "role": "assistant",
        "content": [{"type": "text", "text": "Spawning the developer."}],
        "stop_reason": "end_turn"}},
]


def _write_transcript(tmp_path, age_seconds):
    p = tmp_path / "rec.jsonl"
    p.write_text("\n".join(json.dumps(x) for x in _COMPLETE) + "\n")
    old = time.time() - age_seconds
    os.utime(p, (old, old))
    return str(p)


def _busy_session(clock, transcript_path, busy_age):
    """A BUSY session whose transcript looks finished and quiet — the
    shape idle-recovery acts on."""
    sess = TrackedSession(name=SESSION, label="jim", status=Status.BUSY)
    sess.busy_started_at = clock() - busy_age
    # Walltime, compared against the transcript's mtime by the
    # `written_this_turn` guard — the fake clock is monotonic only.
    sess.busy_started_wall = time.time() - busy_age
    sess.transcript_path = transcript_path
    return sess


def _agent(clock, *, started_ago, seen_ago):
    return {
        "type": "ship-developer",
        "started_at": clock() - started_ago,
        "last_seen": clock() - seen_ago,
        "history_idx": 0,
        "activity": "",
        "tool_count": 0,
        "last_tool_at": 0.0,
        "tools": [],
    }


# --------------------------------------------------------------------------
# The reproduction. Fails on the pre-8.22 age sweep: the 61-minute-old
# entry is dropped, which makes job_background_open() False, and the same
# scan then recovers the session to IDLE and publishes the finished card.
# --------------------------------------------------------------------------

def test_working_subagent_past_the_hour_is_kept_and_blocks_recovery(
    steady_clock, monkeypatch, run_async, tmp_path,
):
    """The incident, end to end: an agent 61 minutes into its run that
    sent a tool hook 10 seconds ago is neither dropped nor finalized."""
    tp = _write_transcript(tmp_path, age_seconds=IDLE_RECOVERY_GRACE + 5)
    sess = _busy_session(steady_clock, tp, busy_age=95 * 60)
    sess.active_subagents["a48ff0cc7f1e0ddd3"] = _agent(
        steady_clock, started_ago=61 * 60, seen_ago=10,
    )
    registry = SessionRegistry()
    registry._sessions[SESSION] = sess
    notify_fn = AsyncMock()
    _only_sessions(monkeypatch)

    run_async(_mk_monitor(registry, notify_fn)._scan())

    assert "a48ff0cc7f1e0ddd3" in sess.active_subagents, (
        "an agent that sent a hook 10s ago was dropped for being old"
    )
    assert sess.status == Status.BUSY, (
        "the session was finalized while its agent was still working"
    )
    events = [c.args[1] for c in notify_fn.await_args_list]
    assert "idle_prompt" not in events
    assert "job_agents_lost" not in events


# --------------------------------------------------------------------------
# The silence window itself.
# --------------------------------------------------------------------------

def test_agent_silent_past_the_window_is_dropped(
    steady_clock, monkeypatch, run_async, caplog,
):
    sess = TrackedSession(name=SESSION, label="jim", status=Status.BUSY)
    sess.active_subagents["a1"] = _agent(
        steady_clock, started_ago=61 * 60, seen_ago=SUBAGENT_SILENCE_SECONDS + 60,
    )
    registry = SessionRegistry()
    registry._sessions[SESSION] = sess
    _only_sessions(monkeypatch)

    with caplog.at_level(logging.INFO, logger="aipager.session_monitor"):
        run_async(_mk_monitor(registry)._scan())

    assert "a1" not in sess.active_subagents
    assert any(
        "dropping silent subagent a1 (no hook event in 30 min)" in r.getMessage()
        for r in caplog.records
    ), [r.getMessage() for r in caplog.records]


def test_agent_silent_just_under_the_window_is_kept(
    steady_clock, monkeypatch, run_async,
):
    sess = TrackedSession(name=SESSION, label="jim", status=Status.BUSY)
    sess.active_subagents["a1"] = _agent(
        steady_clock, started_ago=61 * 60, seen_ago=SUBAGENT_SILENCE_SECONDS - 60,
    )
    registry = SessionRegistry()
    registry._sessions[SESSION] = sess
    _only_sessions(monkeypatch)

    run_async(_mk_monitor(registry)._scan())

    assert "a1" in sess.active_subagents


# --------------------------------------------------------------------------
# The refresh: every datagram carrying a known agent_id is a heartbeat.
# --------------------------------------------------------------------------

@pytest.fixture
def receiver():
    registry = SessionRegistry()
    recv = hr.HookReceiver(registry, AsyncMock())
    return registry, recv


def _send(recv, run_async, **fields):
    run_async(recv._on_datagram(json.dumps(fields).encode()))


@pytest.mark.parametrize("event, extra", [
    ("PreToolUse", {"tool_name": "Bash", "tool_input": {"command": "ls"}}),
    ("PostToolUse", {"tool_name": "Bash", "tool_input": {"command": "ls"}}),
    ("Notification", {"message": "waiting"}),
    ("MessageDisplay", {"delta": "thinking about it", "message_id": "m1"}),
    ("statusline", {}),
])
def test_every_event_kind_with_a_known_agent_id_refreshes_last_seen(
    receiver, run_async, event, extra,
):
    """Including MessageDisplay, whose own branch returns early to fold a
    subagent's prose under its row — the stamp must land before that."""
    registry, recv = receiver
    sess = TrackedSession(name=SESSION, label="jim", status=Status.BUSY)
    stale = time.monotonic() - 4000
    sess.active_subagents["a1"] = {
        "type": "explore", "started_at": stale, "last_seen": stale,
    }
    registry._sessions[SESSION] = sess

    _send(recv, run_async, session=SESSION, hook_event_name=event,
          agent_id="a1", **extra)

    assert sess.active_subagents["a1"]["last_seen"] > stale, (
        f"{event} from a known agent did not refresh its liveness stamp"
    )


def test_unknown_agent_id_never_creates_an_entry(receiver, run_async):
    """Phantom SubagentStops (empty type, unknown id, 0.0s) arrive
    constantly and are tolerated by design — inventing a row for one would
    resurrect a dead agent and hold the job open forever."""
    registry, recv = receiver
    sess = TrackedSession(name=SESSION, label="jim", status=Status.BUSY)
    registry._sessions[SESSION] = sess

    _send(recv, run_async, session=SESSION, hook_event_name="SubagentStop",
          agent_id="phantom-id", agent_type="")
    _send(recv, run_async, session=SESSION, hook_event_name="PreToolUse",
          agent_id="also-unknown", tool_name="Bash", tool_input={})

    assert sess.active_subagents == {}


def test_subagent_start_stamps_last_seen(receiver, run_async):
    registry, recv = receiver
    sess = TrackedSession(name=SESSION, label="jim", status=Status.BUSY)
    registry._sessions[SESSION] = sess

    _send(recv, run_async, session=SESSION, hook_event_name="SubagentStart",
          agent_id="a1", agent_type="ship-developer")

    info = sess.active_subagents["a1"]
    assert info["last_seen"] == pytest.approx(info["started_at"], abs=1.0)


def test_touch_subagent_reports_whether_a_row_matched():
    sess = TrackedSession(name=SESSION, label="jim")
    sess.active_subagents["a1"] = {"type": "explore", "started_at": 1.0,
                                   "last_seen": 1.0}
    assert sess.touch_subagent("a1", 500.0) is True
    assert sess.active_subagents["a1"]["last_seen"] == 500.0
    assert sess.touch_subagent("nope", 500.0) is False
    assert set(sess.active_subagents) == {"a1"}


# --------------------------------------------------------------------------
# End to end through the REAL path: hook datagrams keep the row alive, the
# row keeps `job_background_open()` True, and that is what holds recovery
# back. Nothing is monkeypatched into a state production cannot reach.
# --------------------------------------------------------------------------

def test_hook_traffic_keeps_the_agent_alive_then_silence_lets_recovery_run(
    monkeypatch, run_async, tmp_path,
):
    """One agent, two scans, driven only by datagrams.

    Scan 1: the agent is an hour past the old age TTL but sent a tool hook
    moments ago — the row survives and the session is not finalized. Scan
    2: it has been silent for a full window — the row is swept and
    recovery finalizes the turn exactly as before roadmap 8.22.

    Deliberately NOT on the fake clock: the receiver stamps
    ``time.monotonic()`` directly, and `steady_clock` rebinds only
    session_monitor's and animation's `time`, so a fabricated base would
    put the two halves of this test on different clocks.
    """
    registry = SessionRegistry()
    recv = hr.HookReceiver(registry, AsyncMock())
    agent = "a48ff0cc7f1e0ddd3"

    # The agent starts through the real SubagentStart datagram.
    _send(recv, run_async, session=SESSION, hook_event_name="SubagentStart",
          agent_id=agent, agent_type="ship-developer")
    sess = registry.get(SESSION)
    assert agent in sess.active_subagents

    registry.transition(SESSION, Status.BUSY)
    sess.transcript_path = _write_transcript(
        tmp_path, age_seconds=IDLE_RECOVERY_GRACE + 5,
    )
    sess.busy_started_at = time.monotonic() - 95 * 60
    sess.busy_started_wall = time.time() - 95 * 60
    # Back-date the agent to the incident: 61 minutes in, and — so far —
    # last heard from 61 minutes ago.
    sess.active_subagents[agent]["started_at"] -= 61 * 60
    sess.active_subagents[agent]["last_seen"] -= 61 * 60

    # …then it does what a working agent does: a tool call. The pair is
    # what a real agent emits, and PostToolUse clears the in-flight stamp,
    # so the scan below cannot stand down for the unrelated
    # work_in_flight reason instead of the one under test.
    _send(recv, run_async, session=SESSION, hook_event_name="PreToolUse",
          agent_id=agent, tool_name="Bash", tool_input={"command": "pytest -q"})
    _send(recv, run_async, session=SESSION, hook_event_name="PostToolUse",
          agent_id=agent, tool_name="Bash", tool_input={"command": "pytest -q"})
    assert sess.work_in_flight_reason(time.monotonic()) is None

    notify_fn = AsyncMock()
    monitor = _mk_monitor(registry, notify_fn)
    _only_sessions(monkeypatch)

    run_async(monitor._scan())

    assert agent in sess.active_subagents, (
        "an agent that sent a tool hook moments ago was dropped for being old"
    )
    assert sess.status == Status.BUSY, (
        "the session was finalized while its agent was still working"
    )
    events = [c.args[1] for c in notify_fn.await_args_list]
    assert "idle_prompt" not in events
    assert "job_agents_lost" not in events

    # Now it stops talking — a genuinely missed SubagentStop. One silence
    # window later the row goes and the turn is finalized as it always was.
    sess.active_subagents[agent]["last_seen"] -= SUBAGENT_SILENCE_SECONDS + 60

    run_async(monitor._scan())

    assert sess.active_subagents == {}
    assert sess.status == Status.IDLE
    assert "idle_prompt" in [c.args[1] for c in notify_fn.await_args_list]


def test_idle_recovery_proceeds_once_the_agent_falls_silent(
    steady_clock, monkeypatch, run_async, tmp_path,
):
    """The fail-safe direction, in isolation: a genuinely missed
    SubagentStop still clears, one silence window late instead of never."""
    tp = _write_transcript(tmp_path, age_seconds=IDLE_RECOVERY_GRACE + 5)
    sess = _busy_session(steady_clock, tp, busy_age=IDLE_RECOVERY_GRACE + 20)
    sess.active_subagents["a1"] = _agent(
        steady_clock, started_ago=90 * 60,
        seen_ago=SUBAGENT_SILENCE_SECONDS + 60,
    )
    registry = SessionRegistry()
    registry._sessions[SESSION] = sess
    notify_fn = AsyncMock()
    _only_sessions(monkeypatch)

    run_async(_mk_monitor(registry, notify_fn)._scan())

    assert sess.active_subagents == {}
    assert sess.status == Status.IDLE
    assert "idle_prompt" in [c.args[1] for c in notify_fn.await_args_list]


# --------------------------------------------------------------------------
# The deprecated env alias.
# --------------------------------------------------------------------------

def test_silence_window_defaults_to_half_an_hour():
    assert _resolve_silence_window({}) == (SUBAGENT_SILENCE_DEFAULT, None)
    assert SUBAGENT_SILENCE_DEFAULT == 1800.0


def test_legacy_ttl_env_still_sets_the_window_and_is_reported():
    assert _resolve_silence_window({"AIPAGER_SUBAGENT_TTL": "900"}) == (
        900.0, "AIPAGER_SUBAGENT_TTL",
    )


def test_new_env_wins_when_both_are_set():
    assert _resolve_silence_window({
        "AIPAGER_SUBAGENT_SILENCE": "120",
        "AIPAGER_SUBAGENT_TTL": "900",
    }) == (120.0, None)


def test_blank_values_count_as_unset():
    """A blank env var is how CI and systemd units spell "no override";
    float("") would take the daemon down at import."""
    assert _resolve_silence_window({
        "AIPAGER_SUBAGENT_SILENCE": "", "AIPAGER_SUBAGENT_TTL": "",
    }) == (SUBAGENT_SILENCE_DEFAULT, None)


def test_blank_legacy_env_does_not_break_the_import():
    """`float("")` at import took the whole daemon down. Checked in a
    subprocess because the constant is resolved once, at import time —
    reloading the module in-process would hand every other test a
    different module object than the one their fixtures patched."""
    env = dict(os.environ, AIPAGER_SUBAGENT_TTL="", AIPAGER_SUBAGENT_SILENCE="")
    out = subprocess.run(
        [sys.executable, "-c",
         "import aipager.session_monitor as m;"
         "print(m.SUBAGENT_TTL_SECONDS, m.SUBAGENT_SILENCE_SECONDS)"],
        env=env, capture_output=True, text=True,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == ["3600.0", "1800.0"]


def test_deprecation_is_logged_once_at_startup(monkeypatch, caplog):
    import aipager.session_monitor as sm
    monkeypatch.setattr(sm, "_SILENCE_DEPRECATED_ENV", "AIPAGER_SUBAGENT_TTL")
    monkeypatch.setattr(sm, "_silence_deprecation_logged", False)
    registry = SessionRegistry()

    with caplog.at_level(logging.WARNING, logger="aipager.session_monitor"):
        sm.SessionMonitor(registry, AsyncMock())
        sm.SessionMonitor(registry, AsyncMock())

    lines = [r.getMessage() for r in caplog.records
             if "AIPAGER_SUBAGENT_TTL is deprecated" in r.getMessage()]
    assert len(lines) == 1, lines
    assert "AIPAGER_SUBAGENT_SILENCE" in lines[0]


def test_no_deprecation_line_when_the_legacy_env_is_unused(monkeypatch, caplog):
    import aipager.session_monitor as sm
    monkeypatch.setattr(sm, "_SILENCE_DEPRECATED_ENV", None)
    monkeypatch.setattr(sm, "_silence_deprecation_logged", False)

    with caplog.at_level(logging.WARNING, logger="aipager.session_monitor"):
        sm.SessionMonitor(SessionRegistry(), AsyncMock())

    assert not [r for r in caplog.records if "deprecated" in r.getMessage()]


# --------------------------------------------------------------------------
# Entries that arrive without a stamp (a restored table, a hand-built one).
# --------------------------------------------------------------------------

def test_stampless_entry_starts_its_window_now_rather_than_being_dropped(
    steady_clock, monkeypatch, run_async,
):
    sess = TrackedSession(name=SESSION, label="jim", status=Status.BUSY)
    sess.active_subagents["restored"] = {"type": "ship-developer"}
    registry = SessionRegistry()
    registry._sessions[SESSION] = sess
    _only_sessions(monkeypatch)
    monitor = _mk_monitor(registry)

    run_async(monitor._scan())
    assert "restored" in sess.active_subagents
    assert sess.active_subagents["restored"]["last_seen"] == pytest.approx(
        steady_clock(), abs=5.0,
    )

    # …and is then swept like any other entry once the window passes.
    sess.active_subagents["restored"]["last_seen"] -= SUBAGENT_SILENCE_SECONDS + 60
    run_async(monitor._scan())
    assert "restored" not in sess.active_subagents


def test_stampless_entry_keeps_its_own_start_stamp_when_it_has_one(
    steady_clock, monkeypatch, run_async,
):
    """A pre-8.22 in-memory entry carries `started_at` and nothing else —
    its window runs from the start it does know, so a genuinely abandoned
    one is not made immortal by the upgrade."""
    sess = TrackedSession(name=SESSION, label="jim", status=Status.BUSY)
    started = steady_clock() - SUBAGENT_SILENCE_SECONDS - 60
    sess.active_subagents["legacy"] = {"type": "explore", "started_at": started}
    registry = SessionRegistry()
    registry._sessions[SESSION] = sess
    _only_sessions(monkeypatch)

    run_async(_mk_monitor(registry)._scan())

    assert "legacy" not in sess.active_subagents


def test_normalize_prefers_started_at_then_now():
    sess = TrackedSession(name=SESSION, label="jim")
    sess.active_subagents["has-start"] = {"started_at": 42.0}
    sess.active_subagents["has-nothing"] = {}
    sess.active_subagents["already-stamped"] = {"started_at": 1.0, "last_seen": 7.0}

    sess.normalize_subagent_liveness(1_000.0)

    assert sess.active_subagents["has-start"]["last_seen"] == 42.0
    assert sess.active_subagents["has-nothing"]["last_seen"] == 1_000.0
    # Untouched without reset=True: the sweep must not restamp a live
    # agent's own evidence, or nothing would ever be swept.
    assert sess.active_subagents["already-stamped"]["last_seen"] == 7.0


def test_reset_restamps_every_entry_including_stamped_ones():
    """A stamp that came out of a file was written against another boot's
    monotonic clock, so it is meaningless — not merely stale."""
    sess = TrackedSession(name=SESSION, label="jim")
    sess.active_subagents["from-a-previous-boot"] = {
        "started_at": 12.0, "last_seen": 34.0,
    }
    sess.active_subagents["stamped-in-the-future"] = {
        # A longer previous uptime lands ahead of this boot's clock; left
        # alone, `now - last_seen` is negative and never exceeds the
        # window, so the row would never be swept at all.
        "started_at": 9e9, "last_seen": 9e9,
    }
    sess.active_subagents["no-stamp"] = {"started_at": 5.0}

    sess.normalize_subagent_liveness(1_000.0, reset=True)

    assert [i["last_seen"] for i in sess.active_subagents.values()] == [
        1_000.0, 1_000.0, 1_000.0,
    ]


def test_restored_entry_with_a_stale_stamp_survives_one_window_then_goes(
    steady_clock, monkeypatch, run_async,
):
    """R4 end to end: a row restored with a previous boot's stamp is not
    dropped on the first scan after load, and is dropped one silence
    window later."""
    sess = TrackedSession(name=SESSION, label="jim", status=Status.BUSY)
    sess.active_subagents["restored"] = {
        "type": "ship-developer", "started_at": 12.0, "last_seen": 34.0,
    }
    # What load() does for every restored session.
    sess.normalize_subagent_liveness(steady_clock(), reset=True)
    registry = SessionRegistry()
    registry._sessions[SESSION] = sess
    _only_sessions(monkeypatch)
    monitor = _mk_monitor(registry)

    run_async(monitor._scan())
    assert "restored" in sess.active_subagents

    sess.active_subagents["restored"]["last_seen"] -= SUBAGENT_SILENCE_SECONDS + 60
    run_async(monitor._scan())
    assert "restored" not in sess.active_subagents


def test_load_resets_subagent_liveness(tmp_state_file, monkeypatch):
    """`active_subagents` is not in `_PERSIST_FIELDS`, so `load()` has
    nothing to restamp today — the call is wired, and pinned with
    `reset=True`, so the invariant holds on the day the table is
    persisted."""
    calls = []
    real = TrackedSession.normalize_subagent_liveness

    def _spy(self, now, **kw):
        calls.append((self.name, kw))
        return real(self, now, **kw)

    monkeypatch.setattr(TrackedSession, "normalize_subagent_liveness", _spy)
    tmp_state_file.write_text(json.dumps({
        "version": 1,
        "sessions": {SESSION: {"name": SESSION, "label": "jim"}},
    }))

    registry = SessionRegistry()
    registry.load()

    assert calls == [(SESSION, {"reset": True})]


def test_active_subagents_stays_out_of_persistence():
    """The reason the reset above exists at all. If this ever changes,
    `save()`/`load()` must strip or restamp the monotonic stamps."""
    assert "active_subagents" not in SessionRegistry._PERSIST_FIELDS
    assert "finished_subagents" not in SessionRegistry._PERSIST_FIELDS
