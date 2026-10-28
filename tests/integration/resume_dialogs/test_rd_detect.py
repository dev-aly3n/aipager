"""Black-box rows for roadmap 8.127 detection (design.md success criteria 1
and 2): a ``waiting`` session record on an idle session no hook explains
opens a terminal dialog within one tick; nothing else does."""

from __future__ import annotations

import json
import time

import pytest

from aipager import claude_jobs
from aipager.dtach import bg_jobs, inject
from aipager.state import Status
from tests.resume_dialog_kit import CLIENT, NAME


def _own_entry(world):
    """The fake claude's entry, as the public registry reader returns it."""
    return [e for e in claude_jobs.read_registry(world.cfg) if e.get("pid") == CLIENT][0]


# -- criterion 1: open ------------------------------------------------------------

def test_waiting_record_opens_terminal_dialog_in_one_tick(world):
    world.waiting()
    world.scan()
    assert world.sess.terminal_dialog_open() is True


def test_waiting_record_makes_typing_probe_say_open(world):
    world.waiting()
    world.scan()
    assert inject.dialog_open(NAME) is True


def test_waiting_record_counts_as_any_dialog_open(world):
    world.waiting()
    world.scan()
    assert world.sess.any_dialog_open() is True


@pytest.mark.parametrize("waiting_for", ["dialog open", "input needed", ""],
                         ids=["dialog-open", "input-needed", "absent"])
def test_any_waiting_for_value_opens(world, waiting_for):
    world.waiting(waiting_for=waiting_for)
    world.scan()
    assert world.sess.terminal_dialog_open() is True


def test_idle_record_is_not_open(world):
    world.idle()
    world.scan()
    assert world.sess.terminal_dialog_open() is False


def test_busy_status_record_is_not_open(world):
    world.record(status="busy", statusUpdatedAt=int(world.clock.wall * 1000))
    world.scan()
    assert world.sess.terminal_dialog_open() is False


def test_probe_closed_before_any_record(world):
    world.scan()
    assert inject.dialog_open(NAME) is False


def test_idle_record_closes_open_dialog(world):
    world.waiting()
    world.scan()
    world.idle()
    world.scan()
    assert world.sess.terminal_dialog_open() is False


def test_probe_reopens_for_typing_after_close(world):
    world.waiting()
    world.scan()
    world.idle()
    world.scan()
    assert inject.dialog_open(NAME) is False


def test_record_file_removed_closes_dialog(world):
    path = world.waiting()
    world.scan()
    (world.cfg / "sessions" / f"{CLIENT}.json").unlink()
    world.scan()
    assert world.sess.terminal_dialog_open() is False, path


# -- criterion 2: never open ------------------------------------------------------

def test_permission_prompt_pending_is_not_terminal_dialog(world):
    world.sess.pending_permission = {"tool_name": "Bash", "tool_input": {"command": "ls"}}
    world.waiting()
    world.scan()
    assert world.sess.terminal_dialog_open() is False


def test_prompt_carried_over_stop_is_not_terminal_dialog(world):
    world.sess.prompt_wait_carry = True
    world.waiting()
    world.scan()
    assert world.sess.terminal_dialog_open() is False


def test_extra_open_prompt_is_not_terminal_dialog(world):
    world.sess.extra_prompts = [{"tool_name": "AskUserQuestion"}]
    world.waiting()
    world.scan()
    assert world.sess.terminal_dialog_open() is False


@pytest.mark.parametrize("status", [Status.BUSY, Status.INTERACTIVE],
                         ids=["busy", "interactive"])
def test_not_idle_session_never_opens(world, status):
    world.bot.registry.transition(NAME, status)
    world.waiting()
    world.scan()
    assert world.sess.terminal_dialog_open() is False


def test_busy_session_probe_not_from_record(world):
    world.bot.registry.transition(NAME, Status.BUSY)
    world.waiting()
    world.scan()
    assert world.sess.term_dialog is None


def test_foreign_owned_record_is_ignored(world, monkeypatch):
    world.waiting()
    monkeypatch.setattr(bg_jobs, "_getuid", lambda: 4_000_000)
    world.scan()
    assert world.sess.terminal_dialog_open() is False


@pytest.mark.parametrize("body", [
    "{not json",
    "",
    "[]",
    json.dumps("waiting"),
    "\x00\x01\x02",
], ids=["broken-json", "empty", "list", "string", "binary"])
def test_malformed_record_is_ignored(world, body):
    (world.cfg / "sessions" / f"{CLIENT}.json").write_text(body)
    world.scan()
    assert world.sess.terminal_dialog_open() is False


def test_record_without_status_is_ignored(world):
    world.record(waitingFor="dialog open", statusUpdatedAt=int(world.clock.wall * 1000))
    world.scan()
    assert world.sess.terminal_dialog_open() is False


def test_dead_pid_record_is_ignored(world):
    world.waiting()
    world.proc.alive.clear()
    world.scan()
    assert world.sess.terminal_dialog_open() is False


def test_reused_pid_record_is_ignored(world):
    """The pid is alive but started at another time: the file is a dead
    claude's leftover."""
    world.waiting()
    world.proc.start[CLIENT] = 999
    world.scan()
    assert world.sess.terminal_dialog_open() is False


def test_background_job_record_is_not_the_session(world):
    world.record(status="waiting", waitingFor="dialog open", kind="bg",
                 statusUpdatedAt=int(world.clock.wall * 1000))
    world.scan()
    assert world.sess.terminal_dialog_open() is False


def test_other_sessions_record_does_not_open(world):
    path = world.cfg / "sessions" / "5555.json"
    world.proc.alive.add(5555)
    world.proc.start[5555] = 555
    path.write_text(json.dumps({
        "pid": 5555, "kind": "interactive", "procStart": "555",
        "sessionId": "22222222-2222-4222-8222-222222222222",
        "status": "waiting", "waitingFor": "dialog open",
        "statusUpdatedAt": int(world.clock.wall * 1000)}))
    world.idle()
    world.scan()
    assert world.sess.terminal_dialog_open() is False


def test_unreadable_registry_never_opens(world, monkeypatch, tmp_path):
    world.waiting()
    monkeypatch.setattr(claude_jobs, "config_dir", lambda: tmp_path / "missing")
    world.scan()
    assert world.sess.terminal_dialog_open() is False


def test_unreadable_registry_keeps_open_dialog_open(world, monkeypatch, tmp_path):
    """Fail closed: a read that says nothing changes nothing."""
    world.waiting()
    world.scan()
    monkeypatch.setattr(claude_jobs, "config_dir", lambda: tmp_path / "missing")
    world.scan()
    assert world.sess.terminal_dialog_open() is True


def test_relaunch_in_flight_never_opens(world):
    """The old claude and its dialog die with a relaunch."""
    world.sess.relaunch_in_flight = True
    world.waiting()
    world.scan()
    assert world.sess.terminal_dialog_open() is False


def test_gone_session_clears_opening(world):
    world.waiting()
    world.scan()
    world.bot.registry.transition(NAME, Status.GONE)
    assert world.sess.terminal_dialog_open() is False


# -- the 64 KiB record bound (boundary) ---------------------------------------------

def _padded(world, size):
    body = {"pid": CLIENT, "kind": "interactive", "sessionId": world.sess.claude_session_id,
            "procStart": "777", "status": "waiting", "waitingFor": "dialog open",
            "statusUpdatedAt": int(world.clock.wall * 1000), "pad": ""}
    base = len(json.dumps(body))
    body["pad"] = "x" * (size - base)
    text = json.dumps(body)
    assert len(text) == size
    (world.cfg / "sessions" / f"{CLIENT}.json").write_text(text)


def test_record_just_under_64_kib_is_read(world):
    _padded(world, 64 * 1024 - 64)
    world.scan()
    assert world.sess.terminal_dialog_open() is True


def test_record_over_64_kib_never_opens(world):
    _padded(world, 64 * 1024 + 64)
    world.scan()
    assert world.sess.terminal_dialog_open() is False


# -- read_registry's new fields (statusAt ms vs s, plausibility) --------------------

def _status_at(world, stamp):
    world.record(status="waiting", waitingFor="dialog open", statusUpdatedAt=stamp)
    return _own_entry(world)


def test_registry_entry_keeps_waiting_for(world):
    assert _status_at(world, int(time.time() * 1000))["waitingFor"] == "dialog open"


def test_registry_entry_waiting_for_absent_is_empty_string(world):
    world.record(status="waiting", statusUpdatedAt=int(time.time() * 1000))
    assert _own_entry(world)["waitingFor"] == ""


def test_registry_entry_waiting_for_non_string_is_empty_string(world):
    world.record(status="waiting", waitingFor=7, statusUpdatedAt=int(time.time() * 1000))
    assert _own_entry(world)["waitingFor"] == ""


def test_status_at_in_milliseconds_becomes_seconds(world):
    now = time.time()
    assert _status_at(world, int(now * 1000))["statusAt"] == pytest.approx(now, abs=0.01)


def test_status_at_in_seconds_stays_seconds(world):
    now = time.time()
    assert _status_at(world, now)["statusAt"] == pytest.approx(now, abs=0.01)


@pytest.mark.parametrize("stamp", ["soon", None, [1], True],
                         ids=["string", "null", "list", "bool"])
def test_status_at_non_numeric_is_none(world, stamp):
    assert _status_at(world, stamp)["statusAt"] is None


def test_status_at_29_days_old_is_kept(world):
    at = time.time() - 29 * 86400
    assert _status_at(world, int(at * 1000))["statusAt"] == pytest.approx(at, abs=0.01)


def test_status_at_31_days_old_is_none(world):
    at = time.time() - 31 * 86400
    assert _status_at(world, int(at * 1000))["statusAt"] is None


def test_status_at_50_s_ahead_is_kept(world):
    at = time.time() + 50
    assert _status_at(world, int(at * 1000))["statusAt"] == pytest.approx(at, abs=0.01)


def test_status_at_70_s_ahead_is_none(world):
    at = time.time() + 70
    assert _status_at(world, int(at * 1000))["statusAt"] is None


def test_status_at_zero_is_none(world):
    assert _status_at(world, 0)["statusAt"] is None


def test_waiting_still_reads_unknown_for_jobs(world):
    entry = _status_at(world, int(time.time() * 1000))
    assert claude_jobs.entry_state(entry) == claude_jobs.entry_state(
        {**entry, "status": "no-such-status"})
