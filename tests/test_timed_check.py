"""The Bash command check runs within a time limit (aipager.dtach.timed_check).

A regex cannot be stopped from inside the process, and some commands a few
KB long hold the search rules for minutes (``rg~/`` repeated: about 3 s at
6 KB). A hook that runs past Claude Code's timeout is a non-blocking error,
so the command would run. A long command is therefore checked in a forked
child the hook kills at the deadline; enforce then denies, as for any check
that could not run.

Each test forks only its own children and signals only them.
"""
from __future__ import annotations

import json
import os
import time

import pytest

from aipager import safety
from aipager.dtach import enforce, timed_check

# pytest-xdist runs a thread beside the tests; the real hook has none.
pytestmark = pytest.mark.filterwarnings(
    "ignore:This process .* is multi-threaded:DeprecationWarning")

PATTERNS = safety.DENY_BASH_PATTERNS + safety.DENY_BASH_SEARCH_PATTERNS
NL = chr(10)
# A one-word shape the search rules read in quadratic time (also on
# main before this change): a few ms at 1 KB, about 3 s at 6 KB.
SLOW = "rg~/" * 2048 + " ~"
SESSION = "claude-timed__d1"
UNCHECKED = "this tool call could not be checked, so it was denied"


def _long(text: str) -> str:
    """*text* after a harmless here-document that makes it long."""
    body = ("    total = compute(value, other)  # an ordinary line" + NL) * 40
    return "cat > notes.py <<'EOF'" + NL + body + "EOF" + NL + text


@pytest.fixture
def forks(monkeypatch):
    """Record every fork's child pid (the real fork still runs)."""
    pids: list[int] = []
    real = timed_check._fork

    def _recording_fork():
        pid = real()
        if pid:
            pids.append(pid)
        return pid

    monkeypatch.setattr(timed_check, "_fork", _recording_fork)
    return pids


def _gone(pid: int) -> bool:
    """True once *pid* (one of this test's children) has been reaped."""
    try:
        os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        return True
    return False


def _no_fork(monkeypatch):
    def _refuse():
        raise AssertionError("forked for a short command")
    monkeypatch.setattr(timed_check, "_fork", _refuse)


# ---- which commands fork ----------------------------------------------------

def test_a_short_command_is_checked_here(monkeypatch):
    _no_fork(monkeypatch)
    short = "x" * timed_check.FORK_ABOVE
    assert timed_check.bash_violation(short, PATTERNS) is None
    assert timed_check.bash_violation("grep -r token ~", PATTERNS) \
        == safety.BASH_SEARCH_REASON


def test_a_long_command_is_checked_in_a_child(forks):
    cmd = "x" * (timed_check.FORK_ABOVE + 1)
    assert timed_check.bash_violation(cmd, PATTERNS) is None
    assert len(forks) == 1 and _gone(forks[0])


@pytest.mark.parametrize("tail, expected", [
    ("python3 notes.py", None),
    ("grep -r token ~", safety.BASH_SEARCH_REASON),
    ("aipager setup group --chat-id -100 --member 1:a:owner", safety.BASH_REASON),
    ("cat ~/.config/aipager/aipager.yaml", safety.BASH_REASON),
], ids=["harmless", "home-search", "setup-group", "config-file"])
def test_the_child_gives_the_same_verdict(forks, tail, expected):
    cmd = _long(tail)
    assert len(cmd) > timed_check.FORK_ABOVE
    assert timed_check.bash_violation(cmd, PATTERNS) == expected
    assert safety.bash_violation(cmd, PATTERNS) == expected
    assert len(forks) == 1 and _gone(forks[0])


def test_without_fork_the_check_runs_here(monkeypatch):
    monkeypatch.setattr(timed_check, "_fork", None)
    assert timed_check.bash_violation(_long("grep -r token ~"), PATTERNS) \
        == safety.BASH_SEARCH_REASON


def test_a_failed_fork_checks_here(monkeypatch):
    def _fail():
        raise OSError("no more processes")
    monkeypatch.setattr(timed_check, "_fork", _fail)
    assert timed_check.bash_violation(_long("grep -r token ~"), PATTERNS) \
        == safety.BASH_SEARCH_REASON


# ---- the deadline -----------------------------------------------------------

def test_a_check_past_the_deadline_is_killed(monkeypatch, forks):
    monkeypatch.setattr(timed_check, "BUDGET_SECONDS", 0.3)
    started = time.monotonic()
    with pytest.raises(timed_check.CheckTimedOut):
        timed_check.bash_violation(SLOW, PATTERNS)
    assert time.monotonic() - started < 2.0
    assert len(forks) == 1 and _gone(forks[0])


def test_a_check_within_the_deadline_answers(monkeypatch, forks):
    def _slowish(command, patterns):
        time.sleep(0.2)
        return safety.BASH_REASON
    monkeypatch.setattr(safety, "bash_violation", _slowish)
    monkeypatch.setattr(timed_check, "BUDGET_SECONDS", 5.0)
    assert timed_check.bash_violation(_long("ls"), PATTERNS) == safety.BASH_REASON
    assert _gone(forks[0])


# ---- a child that gives no verdict ------------------------------------------

def _child_does(monkeypatch, behaviour):
    monkeypatch.setattr(safety, "bash_violation", behaviour)


def _raise(exc):
    def _check(command, patterns):
        raise exc
    return _check


@pytest.mark.parametrize("behaviour", [
    _raise(RuntimeError("boom")),
    _raise(MemoryError()),
    lambda command, patterns: 5,
    lambda command, patterns: ["Bash command blocked by safety policy"],
], ids=["crash", "memory", "not-text", "a-list"])
def test_a_child_without_a_verdict_fails(monkeypatch, forks, behaviour):
    _child_does(monkeypatch, behaviour)
    with pytest.raises(timed_check.CheckFailed):
        timed_check.bash_violation(_long("ls"), PATTERNS)
    assert _gone(forks[0])


def test_a_child_that_exits_without_writing_fails(monkeypatch, forks):
    def _vanish(command, patterns):
        os._exit(0)
    _child_does(monkeypatch, _vanish)
    with pytest.raises(timed_check.CheckFailed):
        timed_check.bash_violation(_long("ls"), PATTERNS)
    assert _gone(forks[0])


# ---- a child never outlives its check ---------------------------------------

def _sleeps(command, patterns):
    time.sleep(30)
    return None


def test_the_child_ends_itself(monkeypatch, forks):
    """A hook killed while it waits cannot kill its child: the child's own
    timer ends it, here before the parent's deadline."""
    assert timed_check.CHILD_LIFE_SECONDS > timed_check.BUDGET_SECONDS
    _child_does(monkeypatch, _sleeps)
    monkeypatch.setattr(timed_check, "BUDGET_SECONDS", 20.0)
    monkeypatch.setattr(timed_check, "CHILD_LIFE_SECONDS", 0.3)
    started = time.monotonic()
    with pytest.raises(timed_check.CheckFailed):
        timed_check.bash_violation(_long("ls"), PATTERNS)
    assert time.monotonic() - started < 5.0
    assert _gone(forks[0])


def test_a_wait_that_fails_still_kills_the_child(monkeypatch, forks):
    """The parent kills it (the child's own timer is far off here)."""
    _child_does(monkeypatch, _sleeps)
    monkeypatch.setattr(timed_check, "CHILD_LIFE_SECONDS", 60.0)

    def _broken_wait(*args):
        raise OSError("wait failed")
    monkeypatch.setattr(timed_check, "_wait", _broken_wait)
    started = time.monotonic()
    with pytest.raises(OSError):
        timed_check.bash_violation(_long("ls"), PATTERNS)
    assert time.monotonic() - started < 3.0
    assert _gone(forks[0])


def test_the_child_holds_none_of_the_hook_s_streams(monkeypatch, forks, capfd):
    """The hook answers Claude Code on its stdout: a child must neither
    write there nor keep it open."""
    def _writes(command, patterns):
        os.write(1, b"child-out")
        os.write(2, b"child-err")
        return None
    _child_does(monkeypatch, _writes)
    assert timed_check.bash_violation(_long("ls"), PATTERNS) is None
    out, err = capfd.readouterr()
    assert "child-out" not in out and "child-err" not in err


@pytest.mark.parametrize("raw", [
    b"", b"{", b"null", b"[]", b"{}", b'{"reason": 1}', b'{"reason": {}}',
], ids=["empty", "cut", "null", "list", "no-key", "number", "object"])
def test_only_a_verdict_is_read_as_one(raw):
    with pytest.raises(timed_check.CheckFailed):
        timed_check._verdict(raw)


@pytest.mark.parametrize("reason", [None, safety.BASH_REASON])
def test_a_verdict_is_read(reason):
    assert timed_check._verdict(json.dumps({"reason": reason}).encode()) == reason


# ---- through enforce --------------------------------------------------------

def _decide(tmp_path, command):
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(json.dumps({"type": "user", "message": {
        "role": "user",
        "content": "[via Telegram · @bob · role:user]\ngo"}}) + NL, encoding="utf-8")
    return enforce.decide({
        "hook_event_name": "PreToolUse", "session": SESSION,
        "session_id": "5b1d7363-aaaa-bbbb-cccc-000000000101",
        "cwd": str(tmp_path), "tool_name": "Bash",
        "tool_input": {"command": command},
        "transcript_path": str(transcript)})


def test_enforce_denies_a_command_it_could_not_check_in_time(
        monkeypatch, tmp_path, forks):
    monkeypatch.setattr(timed_check, "BUDGET_SECONDS", 0.3)
    started = time.monotonic()
    assert _decide(tmp_path, SLOW) == {"tool": "Bash", "reason": UNCHECKED}
    assert time.monotonic() - started < 2.0
    assert _gone(forks[0])


def test_enforce_still_gives_the_rule_s_own_reason(tmp_path, forks):
    assert _decide(tmp_path, _long("grep -r token ~")) \
        == {"tool": "Bash", "reason": safety.BASH_SEARCH_REASON}
