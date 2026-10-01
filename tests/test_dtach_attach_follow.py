"""`aipager session` follows a relaunch instead of dropping to the shell.

Telegram's /mode, /restart and Restart kill the dtach master and start a
new one on the same socket about a second later. The attach client used to
exit with the old master and leave the terminal at a shell while the session
ran on, unseen. ``launcher._attach_following`` now tells the endings apart
by whether the socket is still alive after ``dtach -a`` returns, and still
the same socket file (``_sock_identity``) the attach began on.

Nothing here runs dtach or claude, opens a socket, or touches /tmp:
``subprocess.run``, ``_socket_alive``, ``_sleep`` and ``_clock`` are all
simulated, and ``Path`` is replaced for the two ``launch()`` tests.
"""

from __future__ import annotations

import threading

import pytest

from aipager.dtach import launcher

SOCK = "/fake/claude-dtach-boss.sock"


class _Status:
    def __init__(self, log, text):
        self.log, self.text = log, text

    def __enter__(self):
        self.log.append(("status-on", self.text))
        return self

    def __exit__(self, *exc):
        self.log.append(("status-off", self.text))
        return False


class _Console:
    def __init__(self, terminal):
        self.is_terminal = terminal
        self.log: list[tuple[str, str]] = []

    def print(self, text="", *a, **k):
        self.log.append(("print", str(text)))

    def status(self, text, **k):
        return _Status(self.log, text)

    def printed(self):
        return "\n".join(t for kind, t in self.log if kind == "print")


class _World:
    """A scripted session: each attach lasts ``attach_s`` fake seconds, and
    ``alive`` answers every socket probe in order (the last answer repeats).
    ``idents`` does the same for ``_sock_identity``. Its default, one
    identity throughout, is what real dtach gives while one master lives:
    its attach/detach chmod moves ctime but not the mtime the identity uses
    (pinned by ``test_socket_identity_ignores_dtachs_attach_chmod``).
    """

    def __init__(self, monkeypatch, alive, *, attach_s=30.0, terminal=False,
                 interrupt_on_sleep=None, idents=("old",)):
        self.now = 1000.0
        self.alive = list(alive)
        self.idents = list(idents)
        self.attach_s = attach_s
        self.runs: list[list[str]] = []
        self.probes = 0
        self.sleeps: list[float] = []
        self.titles: list[str] = []
        self.redraws: list[str] = []
        self.interrupt_on_sleep = interrupt_on_sleep
        self.console = _Console(terminal)

        monkeypatch.setattr(launcher, "_clock", lambda: self.now)
        monkeypatch.setattr(launcher, "_sleep", self._sleep)
        monkeypatch.setattr(launcher, "_socket_alive", self._alive)
        monkeypatch.setattr(launcher, "_sock_identity", self._ident)
        monkeypatch.setattr(launcher.subprocess, "run", self._run)
        monkeypatch.setattr(launcher, "_set_title", self.titles.append)
        monkeypatch.setattr(launcher, "_keep_title", lambda name, stop: None)
        monkeypatch.setattr(launcher, "_force_redraw", self.redraws.append)
        monkeypatch.setattr(launcher, "console", self.console)

    def _run(self, cmd, **k):
        assert cmd[:3] == ["/fake/dtach", "-a", SOCK], cmd
        self.runs.append(cmd)
        # A broken loop must fail here, not hang the suite.
        assert len(self.runs) <= 200, "attach loop never stopped"
        self.now += self.attach_s
        return None

    def _alive(self, sock):
        assert sock == SOCK
        self.probes += 1
        return self.alive.pop(0) if len(self.alive) > 1 else self.alive[0]

    def _ident(self, sock):
        assert sock == SOCK
        return self.idents.pop(0) if len(self.idents) > 1 else self.idents[0]

    def _sleep(self, s):
        self.sleeps.append(s)
        if self.interrupt_on_sleep and len(self.sleeps) == self.interrupt_on_sleep:
            raise KeyboardInterrupt
        self.now += s

    def attach(self, *, redraw=False):
        return launcher._attach_following("/fake/dtach", SOCK, "boss",
                                          redraw=redraw)


def _join_new_threads(before):
    # The title / redraw stand-ins run on real (daemon) threads; wait for
    # the ones this test started so their effects are recorded.
    for t in threading.enumerate():
        if t not in before and t is not threading.current_thread():
            t.join(timeout=1.0)


def test_relaunch_is_followed_with_one_muted_line(monkeypatch):
    # attach ends, socket dead; two polls dead, third alive; then the
    # second attach ends with the socket alive (a detach).
    w = _World(monkeypatch, [False, False, False, True, True])
    before = set(threading.enumerate())
    w.attach()
    _join_new_threads(before)
    assert len(w.runs) == 2
    out = w.console.printed()
    assert out.count("boss restarted, reattaching") == 1
    assert "waiting for it to come back" in out
    # The window title is put back after the reattach.
    assert w.titles == ["boss", "boss"]
    # The relaunched claude drew its screen unattended: force a redraw.
    assert w.redraws == ["boss"]


def test_deliberate_detach_returns_at_once(monkeypatch):
    # Only the attach ended (its client was signalled); the master runs on.
    w = _World(monkeypatch, [True])
    elapsed = w.attach()
    assert len(w.runs) == 1
    # Back at the shell after the short grace, never the 15 s wait.
    assert w.sleeps == [launcher._FOLLOW_POLL_S] * launcher._DETACH_GRACE_PROBES
    assert sum(w.sleeps) <= 1.0
    assert w.probes == 1 + launcher._DETACH_GRACE_PROBES
    assert "waiting" not in w.console.printed()
    assert elapsed == pytest.approx(30.0)


def test_client_killed_before_its_dying_master_is_still_followed(monkeypatch):
    """`inject.kill_session` SIGTERMs the attach client and the master in
    one loop, so the first probe can still reach the dying master. The
    socket going dead during the grace means a relaunch, not a detach."""
    w = _World(monkeypatch, [True, True, False, False, True, True])
    w.attach()
    assert len(w.runs) == 2
    assert w.console.printed().count("boss restarted, reattaching") == 1


def test_relaunch_stepped_over_by_the_probes_is_still_followed(monkeypatch):
    """Every probe can land on a live socket: the old master's before it
    dies, the new master's after. The socket FILE changed in between, so
    this is a relaunch to follow, not the attach alone ending."""
    # Identity: before attach 1, then (after it) the new file from then on.
    w = _World(monkeypatch, [True], idents=["old", "new"])
    w.attach()
    assert len(w.runs) == 2
    assert w.console.printed().count("boss restarted, reattaching") == 1


def test_socket_identity_reads_the_file_at_the_path(monkeypatch):
    class _St:
        st_dev, st_ino, st_mtime_ns = 7, 42, 123

    seen = []
    monkeypatch.setattr(launcher, "_stat",
                        lambda p: seen.append(p) or _St())
    assert launcher._sock_identity(SOCK) == (7, 42, 123)
    assert seen == [SOCK]

    def missing(p):
        raise FileNotFoundError(p)

    monkeypatch.setattr(launcher, "_stat", missing)
    assert launcher._sock_identity(SOCK) is None


class _Stat:
    def __init__(self, *, ino=42, mtime=1_000, ctime=1_000, mode=0o140600):
        self.st_dev, self.st_ino = 7, ino
        self.st_mtime_ns, self.st_ctime_ns, self.st_mode = mtime, ctime, mode


def test_socket_identity_ignores_dtachs_attach_chmod(monkeypatch):
    """The dtach master chmods its socket u+x while a client is attached
    and back when none is, which moves ctime (seen on a live socket:
    srwx------ with ctime at the attach, mtime at the master's start). The
    same master must keep the same identity across that."""
    stats = iter([_Stat(ctime=1_000, mode=0o140600),
                  _Stat(ctime=9_999, mode=0o140700)])
    monkeypatch.setattr(launcher, "_stat", lambda p: next(stats))
    assert launcher._sock_identity(SOCK) == launcher._sock_identity(SOCK)


def test_socket_identity_tells_a_rebound_socket_apart(monkeypatch):
    """ext4 hands the freed inode number straight back to the next bind
    (seen: three binds 0.2 s apart, one inode), so the bind time decides."""
    stats = iter([_Stat(ino=42, mtime=1_000), _Stat(ino=42, mtime=2_000)])
    monkeypatch.setattr(launcher, "_stat", lambda p: next(stats))
    assert launcher._sock_identity(SOCK) != launcher._sock_identity(SOCK)


def test_unknown_identity_falls_back_to_alive_alone(monkeypatch):
    # The stat failed before the attach: a live socket must still read as
    # "only the attach ended", or nothing could ever return to the shell.
    # (The stat works again afterwards, so a strict comparison would fail.)
    w = _World(monkeypatch, [True], idents=[None, "readable-now"])
    w.attach()
    assert len(w.runs) == 1
    assert "waiting" not in w.console.printed()


def test_ctrl_c_during_the_detach_grace_returns_to_the_shell(monkeypatch):
    w = _World(monkeypatch, [True], interrupt_on_sleep=1)
    try:
        w.attach()
    except KeyboardInterrupt:
        pytest.fail("Ctrl-C escaped the detach grace")
    assert len(w.runs) == 1
    assert "waiting" not in w.console.printed()


def test_real_end_returns_after_the_bound(monkeypatch):
    w = _World(monkeypatch, [False], attach_s=0.3)
    elapsed = w.attach()
    assert len(w.runs) == 1
    polls = int(launcher._FOLLOW_WAIT_S / launcher._FOLLOW_POLL_S)
    assert len(w.sleeps) == polls
    assert sum(w.sleeps) == pytest.approx(launcher._FOLLOW_WAIT_S)
    assert 10.0 <= launcher._FOLLOW_WAIT_S <= 15.0
    # The caller's "exited immediately" check sees the attach itself,
    # not the wait after it.
    assert elapsed == pytest.approx(0.3)
    assert "reattaching" not in w.console.printed()


def test_waiting_line_is_a_transient_status_on_a_terminal(monkeypatch):
    w = _World(monkeypatch, [False, False, True, True], terminal=True)
    w.attach()
    kinds = [k for k, t in w.console.log if "waiting" in t]
    assert kinds == ["status-on", "status-off"]


def test_ctrl_c_during_the_wait_ends_it_at_once(monkeypatch):
    w = _World(monkeypatch, [False], interrupt_on_sleep=3)
    try:
        w.attach()
    except KeyboardInterrupt:
        pytest.fail("Ctrl-C escaped the wait instead of ending it")
    assert len(w.runs) == 1
    assert len(w.sleeps) == 3


def test_crash_loop_is_capped(monkeypatch):
    # Every attach dies after 1 s and the socket is back at the first poll.
    w = _World(monkeypatch, [False, True], attach_s=1.0)
    w.alive = [False, True] * 50 + [False]
    w.attach()
    assert len(w.runs) == launcher._FOLLOW_MAX_REATTACHES + 1
    out = w.console.printed()
    assert out.count("reattaching") == launcher._FOLLOW_MAX_REATTACHES
    assert "no longer following" in out


def test_restarts_spread_out_over_time_keep_being_followed(monkeypatch):
    # Each attach lasts longer than the cap window, so old reattaches age
    # out and a long-lived session is followed through many relaunches.
    n = launcher._FOLLOW_MAX_REATTACHES + 3
    w = _World(monkeypatch, [], attach_s=launcher._FOLLOW_WINDOW_S + 1)
    w.alive = [False, True] * n + [True]
    w.attach()
    assert len(w.runs) == n + 1
    assert "no longer following" not in w.console.printed()


# ---- both attach sites in launch() go through the one helper -----------


class _FakePath:
    """Before the attach the socket is live (or absent, for a fresh
    launch); after it, it exists or not as the test says."""

    def __init__(self, state):
        self.state = state

    def exists(self):
        if self.state["attached"]:
            return self.state["exists_after"]
        return self.state["live_socket"]

    def is_socket(self):
        if self.state["attached"]:
            return self.state["exists_after"]
        return True

    def unlink(self):
        raise AssertionError("no stale socket expected")


def _launch_with(monkeypatch, *, live_socket, attach_elapsed, exists_after):
    calls = []
    state = {"attached": False, "live_socket": live_socket,
             "exists_after": exists_after}

    def fake_attach(dtach, sock, name, *, redraw):
        calls.append((dtach, sock, name, redraw))
        state["attached"] = True
        return attach_elapsed

    def fake_path(p):
        assert "zz_follow_test" in str(p), p
        return _FakePath(state)

    class _Spawn:
        returncode = 0
        stderr = ""
        stdout = ""

    monkeypatch.setattr(launcher, "_attach_following", fake_attach)
    monkeypatch.setattr(launcher, "Path", fake_path)
    monkeypatch.setattr(launcher, "_resolve_dtach", lambda: "/fake/dtach")
    monkeypatch.setattr(launcher, "_dtach_works", lambda p: (True, ""))
    monkeypatch.setattr(launcher, "_socket_alive", lambda s: live_socket)
    monkeypatch.setattr(launcher.subprocess, "run", lambda *a, **k: _Spawn())
    monkeypatch.setattr(launcher.daemon_secrets, "build_session_env",
                        lambda: {})
    monkeypatch.setattr(launcher, "_sleep", lambda s: None)
    rc = launcher.launch("zz_follow_test")
    return rc, calls


@pytest.mark.parametrize("live_socket,redraw", [(True, True), (False, False)])
def test_both_attach_sites_use_the_helper(monkeypatch, live_socket, redraw):
    rc, calls = _launch_with(monkeypatch, live_socket=live_socket,
                             attach_elapsed=40.0, exists_after=True)
    assert rc == 0
    assert calls == [("/fake/dtach", "/tmp/claude-dtach-zz_follow_test.sock",
                      "zz_follow_test", redraw)]


@pytest.mark.parametrize("live_socket", [True, False])
def test_exited_immediately_warning_still_fires(monkeypatch, capsys,
                                                live_socket):
    _launch_with(monkeypatch, live_socket=live_socket, attach_elapsed=0.2,
                 exists_after=False)
    assert "exited immediately" in capsys.readouterr().err


@pytest.mark.parametrize("live_socket", [True, False])
def test_no_warning_when_the_session_is_still_there(monkeypatch, capsys,
                                                    live_socket):
    _launch_with(monkeypatch, live_socket=live_socket, attach_elapsed=0.2,
                 exists_after=True)
    assert "exited immediately" not in capsys.readouterr().err


def test_followed_relaunch_reports_the_last_attach(monkeypatch):
    """The helper reports the LAST attach's duration, so a short first
    attach cut off by a relaunch is not what the caller judges."""
    w = _World(monkeypatch, [False, True, True], attach_s=0.2)
    durations = iter([0.2, 50.0])

    def run(cmd, **k):
        w.runs.append(cmd)
        assert len(w.runs) <= 200, "attach loop never stopped"
        w.now += next(durations)

    monkeypatch.setattr(launcher.subprocess, "run", run)
    assert w.attach() == pytest.approx(50.0)
    assert len(w.runs) == 2


# ---- launch() end to end with the real helper ---------------------------


def _launch_real_helper(monkeypatch, *, alive, durations, exists_after):
    """Drive launch()'s reattach branch with the real _attach_following.

    ``alive`` scripts every _socket_alive probe, including launch()'s own
    two (name resolution and the reattach check) before the attach.
    """
    sock = "/tmp/claude-dtach-zz_follow_test.sock"
    state = {"attached": False, "live_socket": True,
             "exists_after": exists_after}
    probes = list(alive)
    durations = list(durations)
    clock = {"now": 500.0}
    runs = []

    def run(cmd, **k):
        assert cmd[:3] == ["/fake/dtach", "-a", sock], cmd
        runs.append(cmd)
        assert len(runs) <= 200, "attach loop never stopped"
        state["attached"] = True
        clock["now"] += durations.pop(0) if durations else 100.0
        return None

    def probe(s):
        assert s == sock
        return probes.pop(0) if len(probes) > 1 else probes[0]

    def sleep(s):
        clock["now"] += s

    def fake_path(p):
        assert "zz_follow_test" in str(p), p
        return _FakePath(state)

    monkeypatch.setattr(launcher, "Path", fake_path)
    monkeypatch.setattr(launcher, "_resolve_dtach", lambda: "/fake/dtach")
    monkeypatch.setattr(launcher, "_dtach_works", lambda p: (True, ""))
    monkeypatch.setattr(launcher, "_socket_alive", probe)
    # One master throughout: its identity does not change (see _World).
    monkeypatch.setattr(launcher, "_sock_identity", lambda s: (1, 2, 3))
    monkeypatch.setattr(launcher.subprocess, "run", run)
    monkeypatch.setattr(launcher, "_sleep", sleep)
    monkeypatch.setattr(launcher, "_clock", lambda: clock["now"])
    monkeypatch.setattr(launcher, "_set_title", lambda n: None)
    monkeypatch.setattr(launcher, "_keep_title", lambda name, stop: None)
    monkeypatch.setattr(launcher, "_force_redraw", lambda name: None)
    rc = launcher.launch("zz_follow_test")
    return rc, runs


def test_quick_relaunch_through_launch_does_not_warn(monkeypatch, capsys):
    # Two probes in launch() say live; the first attach lasts 0.2 s and
    # the socket is dead after it; it is back at the first poll; the
    # second attach is long and then the session really ends (socket gone
    # for good), so the warning guard's socket test is True and only the
    # elapsed it is handed (the LAST attach's) keeps it quiet.
    rc, runs = _launch_real_helper(
        monkeypatch, alive=[True, True, False, True, False],
        durations=[0.2, 50.0], exists_after=False)
    assert rc == 0
    assert len(runs) == 2
    captured = capsys.readouterr()
    assert "exited immediately" not in captured.err
    assert "reattaching" in captured.out + captured.err


def test_real_quick_end_through_launch_still_warns(monkeypatch, capsys):
    rc, runs = _launch_real_helper(
        monkeypatch, alive=[True, True, False], durations=[0.2],
        exists_after=False)
    assert rc == 0
    assert len(runs) == 1
    assert "exited immediately" in capsys.readouterr().err
