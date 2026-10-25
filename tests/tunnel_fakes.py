"""A fake process table for aipager.miniapp.tunnel_manager's seams.

No real cloudflared, process, signal, socket or network: every seam the
tunnel code uses to spawn, probe, read a command line, ask the metrics
server or send a signal is replaced by this table. Spawns and signals go
through ``self_restart._popen`` / ``_kill`` (the seams the suite refuses
by default), so a test that forgets this fixture fails loudly.
"""

from __future__ import annotations

import signal
from dataclasses import dataclass, field


class FakePopen:
    def __init__(self, pid: int, table=None):
        self.pid = pid
        self.waited = False
        self._table = table

    def wait(self, timeout=None):
        self.waited = True
        return 0

    def poll(self):
        """None while the table holds the pid alive, else its exit code
        (``table.exit_codes``, default 0)."""
        if self._table is None:
            return 0
        if self.pid in self._table.alive:
            return None
        return self._table.exit_codes.get(self.pid, 0)


@dataclass
class ProcessTable:
    """Processes by pid: their argv, alive or not; metrics answers by
    port; every spawn and every signal, recorded."""

    argv: dict = field(default_factory=dict)        # pid -> list[str]
    alive: set = field(default_factory=set)
    hosts: dict = field(default_factory=dict)       # metrics port -> body or None
    spawns: list = field(default_factory=list)      # (argv, kwargs)
    signals: list = field(default_factory=list)     # (pid, sig)
    stubborn: set = field(default_factory=set)      # pids that ignore SIGTERM
    unkillable: set = field(default_factory=set)    # pids no signal ends (unreapable)
    boot: str = "boot-1"
    next_pid: int = 4000
    next_port: int = 40001
    probe_ok: bool = True
    dead_urls: set = field(default_factory=set)     # urls whose probe fails
    probes: list = field(default_factory=list)
    spawn_error: Exception | None = None
    popens: list = field(default_factory=list)
    exit_codes: dict = field(default_factory=dict)  # pid -> code FakePopen.poll() reports
    stderr_modes: list = field(default_factory=list)  # st_mode of each int stderr given

    # -- seams --------------------------------------------------------------
    def popen(self, argv, **kwargs):
        if self.spawn_error is not None:
            raise self.spawn_error
        self.spawns.append((list(argv), kwargs))
        if isinstance(kwargs.get("stderr"), int) and kwargs["stderr"] >= 0:
            import os
            self.stderr_modes.append(os.fstat(kwargs["stderr"]).st_mode)
        pid = self.next_pid
        self.next_pid += 1
        if argv and argv[0] != "sh":
            self.argv[pid] = list(argv)
            self.alive.add(pid)
        popen = FakePopen(pid, self)
        self.popens.append(popen)
        return popen

    def kill(self, pid, sig):
        self.signals.append((pid, sig))
        if pid not in self.alive:
            raise ProcessLookupError(pid)
        if pid in self.unkillable:
            return
        if sig == signal.SIGKILL or (sig == signal.SIGTERM and pid not in self.stubborn):
            self.alive.discard(pid)

    def pid_alive(self, pid):
        return pid in self.alive

    def cmdline(self, pid):
        return self.argv.get(pid) if pid in self.alive else None

    async def query_metrics(self, port, path):
        assert path == "/quicktunnel"
        body = self.hosts.get(port)
        return body

    async def probe(self, url):
        self.probes.append(url)
        return self.probe_ok and url not in self.dead_urls

    def free_port(self):
        port = self.next_port
        self.next_port += 1
        return port

    # -- helpers ------------------------------------------------------------
    def tunnel_argv(self, binary="/fake/cloudflared", port=8765, metrics=40001):
        return [binary, "tunnel", "--url", f"http://127.0.0.1:{port}", "--no-autoupdate",
                "--metrics", f"127.0.0.1:{metrics}", "--loglevel", "warn"]

    def add_tunnel(self, pid, *, binary="/fake/cloudflared", port=8765, metrics=40001,
                   host="kept-words-here.trycloudflare.com"):
        self.argv[pid] = self.tunnel_argv(binary, port, metrics)
        self.alive.add(pid)
        if host is not None:
            self.hosts[metrics] = '{"hostname":"%s"}' % host

    def guards(self):
        return [a for a, _k in self.spawns if a[0] == "sh"]


def install(monkeypatch) -> ProcessTable:
    from aipager import self_restart
    from aipager.miniapp import tunnel as tunnel_mod
    from aipager.miniapp import tunnel_manager as tm

    table = ProcessTable()
    monkeypatch.setattr(self_restart, "_popen", table.popen)
    monkeypatch.setattr(self_restart, "_kill", table.kill)
    monkeypatch.setattr(tm, "_pid_alive", table.pid_alive)
    monkeypatch.setattr(tm, "_cmdline", table.cmdline)
    monkeypatch.setattr(tm, "_boot_id", lambda: table.boot)
    monkeypatch.setattr(tm, "_query_metrics", table.query_metrics)
    monkeypatch.setattr(tm, "_free_port", table.free_port)
    monkeypatch.setattr(tunnel_mod, "probe_public_url", table.probe)
    return table
