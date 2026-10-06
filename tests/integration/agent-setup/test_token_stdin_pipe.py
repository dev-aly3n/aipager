"""Iteration 2: ``--token-stdin`` on a real pipe (entrypoints.md): the pipe
must reach EOF within ``aipager.setup_cmd.STDIN_READ_TIMEOUT`` (30 s),
else exit 2 ``token_stdin_timeout``. The documented constant is the seam:
these tests lower it, so nothing really waits 30 s. A watchdog closes the
write end after WATCHDOG seconds, so a reader that ignores the deadline
makes the test fail (wrong outcome, too slow) instead of hanging.

Pipes come from ``os.pipe()``: no socket, nothing under /tmp."""

from __future__ import annotations

import os
import sys
import threading
import time

import pytest

from agent_setup_support import CHAT, TOKEN, Result, assert_no_secret

DEADLINE = 0.3
WATCHDOG = 3.0


class Pipe:
    def __init__(self):
        r, w = os.pipe()
        self.reader = os.fdopen(r, "r")
        self._w = w
        self._lock = threading.Lock()
        self._timer = threading.Timer(WATCHDOG, self.close_writer)
        self._timer.daemon = True
        self._timer.start()

    def write(self, data: str) -> None:
        # Under the lock, so a write never races the watchdog's close (a
        # closed fd number can be reused by another file at once).
        with self._lock:
            if self._w is None:
                raise OSError("the write end is closed")
            os.write(self._w, data.encode())

    def close_writer(self) -> None:
        with self._lock:
            if self._w is not None:
                os.close(self._w)
                self._w = None

    def close(self) -> None:
        self._timer.cancel()
        self.close_writer()
        self.reader.close()


@pytest.fixture
def pipe(env, monkeypatch):
    import aipager.setup_cmd as setup_cmd
    monkeypatch.setattr(setup_cmd, "STDIN_READ_TIMEOUT", DEADLINE)
    p = Pipe()
    yield p
    p.close()


def _run(env, pipe, *args):
    """env.run with a real pipe on stdin (env.run installs a StringIO)."""
    from aipager import cli
    argv = ["aipager", *[str(a) for a in args]]
    env.monkeypatch.setattr(sys, "argv", list(argv))
    env.monkeypatch.setattr(sys, "stdin", pipe.reader)
    env.capsys.readouterr()
    env.caplog.clear()
    code: object = "returned-without-SystemExit"
    t0 = time.monotonic()
    try:
        cli.main()
    except SystemExit as e:
        code = 0 if e.code is None else e.code
    elapsed = time.monotonic() - t0
    out, err = env.capsys.readouterr()
    r = Result(code, out, err, argv, env.caplog.text)
    r.elapsed = elapsed
    return r


def _setup(env, pipe, *extra, json_out=True):
    args = ["setup", "--token-stdin", "--chat-id", CHAT, *extra]
    if json_out:
        args.append("--json")
    return _run(env, pipe, *args)


def test_documented_default_deadline_is_30_seconds():
    import aipager.setup_cmd as setup_cmd
    assert setup_cmd.STDIN_READ_TIMEOUT == 30


# ---- a pipe that never closes ----

def test_open_pipe_exits_2(env, pipe):
    assert _setup(env, pipe).code == 2


def test_open_pipe_error_is_token_stdin_timeout(env, pipe):
    assert _setup(env, pipe).json["error"] == "token_stdin_timeout"


def test_open_pipe_returns_near_the_deadline(env, pipe):
    assert _setup(env, pipe).elapsed < DEADLINE + 2.0


def test_open_pipe_fix_names_another_way(env, pipe):
    """usage-class errors: fix names the flag (here: the file source or a
    closing pipe)."""
    fix = _setup(env, pipe).json["fix"] or ""
    assert "--token-file" in fix or "--token-stdin" in fix


def test_open_pipe_makes_no_http_call(env, pipe):
    _setup(env, pipe)
    assert env.tg.calls == []


def test_open_pipe_writes_nothing(env, pipe):
    before = env.snapshot()
    _setup(env, pipe)
    assert env.snapshot() == before


def test_open_pipe_json_is_one_object(env, pipe):
    import json
    assert isinstance(json.loads(_setup(env, pipe).out), dict)


def test_open_pipe_plain_exits_2(env, pipe):
    assert _setup(env, pipe, json_out=False).code == 2


def test_token_written_but_pipe_left_open_times_out(env, pipe):
    """Boundary: a full token with no EOF is still not a finished read."""
    pipe.write(TOKEN + "\n")
    r = _setup(env, pipe)
    assert (r.code, r.json["error"]) == (2, "token_stdin_timeout")


def test_token_written_but_pipe_left_open_sends_nothing(env, pipe):
    pipe.write(TOKEN + "\n")
    _setup(env, pipe)
    assert env.tg.calls == []


def test_token_written_but_pipe_left_open_leaks_nothing(env, pipe):
    pipe.write(TOKEN + "\n")
    r = _setup(env, pipe)
    assert_no_secret(r.out + r.err + r.log, "stdin timeout output")


def test_trickling_pipe_still_hits_the_deadline(env, pipe):
    """Error guessing: the deadline is for the whole read, not per chunk;
    a writer that keeps sending one byte at a time never closes."""
    stop = threading.Event()

    def _trickle():
        while not stop.wait(DEADLINE / 6):
            try:
                pipe.write(" ")
            except OSError:
                return
    t = threading.Thread(target=_trickle, daemon=True)
    t.start()
    try:
        r = _setup(env, pipe)
    finally:
        stop.set()
        t.join(2)
    assert (r.code, r.json["error"]) == (2, "token_stdin_timeout")


def test_detect_chat_open_pipe_does_not_hang(env, pipe):
    """detect-chat takes the same --token-stdin source."""
    r = _run(env, pipe, "setup", "detect-chat", "--token-stdin",
             "--timeout", "0", "--json")
    assert (r.code, r.json["error"]) == (2, "token_stdin_timeout")


# ---- a pipe that closes ----

def test_closed_pipe_with_token_installs(env, pipe):
    pipe.write(TOKEN + "\n")
    pipe.close_writer()
    assert _setup(env, pipe).code == 0


def test_closed_pipe_without_newline_installs(env, pipe):
    pipe.write(TOKEN)
    pipe.close_writer()
    assert _setup(env, pipe).code == 0


def test_closed_pipe_install_leaks_nothing(env, pipe):
    pipe.write(TOKEN + "\n")
    pipe.close_writer()
    r = _setup(env, pipe)
    assert_no_secret(r.out + r.err + r.log, "stdin pipe install output")


def test_closed_empty_pipe_is_malformed(env, pipe):
    pipe.close_writer()
    r = _setup(env, pipe)
    assert (r.code, r.json["error"]) == (2, "token_malformed")


def test_closed_pipe_at_4096_is_accepted(env, pipe):
    """Boundary just inside the 4096 cap."""
    pipe.write((TOKEN + "\n").ljust(4096, " "))
    pipe.close_writer()
    assert _setup(env, pipe).code == 0


def test_closed_pipe_over_4096_is_refused(env, pipe):
    """Boundary just outside the cap."""
    pipe.write((TOKEN + "\n").ljust(4097, " "))
    pipe.close_writer()
    assert _setup(env, pipe).code == 2


def test_closed_pipe_over_4096_makes_no_http_call(env, pipe):
    pipe.write((TOKEN + "\n").ljust(4097, " "))
    pipe.close_writer()
    _setup(env, pipe)
    assert env.tg.calls == []


def test_huge_pipe_is_refused_without_blocking(env, pipe):
    """Error guessing: a writer pushing far more than the cap and leaving
    the pipe open must not stall setup past the deadline."""
    pipe.write("A" * 60000)
    r = _setup(env, pipe)
    assert r.code == 2 and r.elapsed < DEADLINE + 2.0
