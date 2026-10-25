"""Keeping the Mini App's tunnel address across a planned restart
(aipager.miniapp.tunnel_manager, "keeping the address across a restart").

Every process, signal, metrics answer and public probe comes from
tests/tunnel_fakes.py's table, except the guard tests at the end, which
run the real guard script with ``sh`` against a harmless stand-in
process (Linux only).
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import stat
import subprocess
import sys
import time
from unittest.mock import AsyncMock

import pytest

from aipager.miniapp import cloudflared_fetch
from aipager.miniapp import tunnel as tunnel_mod
from aipager.miniapp import tunnel_manager as tm
from tests import tunnel_fakes

HOST = "kept-words-here.trycloudflare.com"
URL = f"https://{HOST}"


@pytest.fixture
def table(monkeypatch):
    tunnel_mod.set_managed_tunnel_url("")
    t = tunnel_fakes.install(monkeypatch)
    monkeypatch.setattr(cloudflared_fetch, "ensure_cloudflared",
                        AsyncMock(return_value="/fake/cloudflared"))
    monkeypatch.setattr("aipager.config.TUNNEL_CHECK_SECONDS", 0.01)
    monkeypatch.setattr("aipager.config.TUNNEL_KILL_TIMEOUT_SECONDS", 0.05)
    yield t
    tunnel_mod.set_managed_tunnel_url("")


def _kept_record(pid=5000, *, port=8765, metrics=40001, host=HOST, owner=0,
                 token="tok123", boot="boot-1", binary="/fake/cloudflared"):
    return {"pid": pid, "metrics_port": metrics, "port": port, "binary": binary,
            "boot_id": boot, "host_sha256": tm._host_digest(host), "owner": owner,
            "handover_token": token}


def _published():
    calls = []

    async def on_change(url):
        calls.append(url)
    return calls, on_change


async def _until(pred, limit=2.0):
    end = time.monotonic() + limit
    while not pred():
        if time.monotonic() > end:
            raise AssertionError("condition never became true")
        await asyncio.sleep(0.005)


# ---- the record ---------------------------------------------------------------

def test_the_record_holds_a_hash_of_the_address_never_the_address(table):
    tunnel = tm.Tunnel(4000, 40001, "/fake/cloudflared", 8765, host=HOST)
    tm._write_record(tunnel.record(owner=42, handover_token="abc"))

    path = tm._record_path()
    text = path.read_text(encoding="utf-8")
    assert HOST not in text and "trycloudflare" not in text
    assert '"handover_token": "abc"' in text           # the guard greps for this
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    rec = tm._read_record()
    assert rec["pid"] == 4000 and rec["owner"] == 42 and rec["boot_id"] == "boot-1"
    assert rec["host_sha256"] == tm._host_digest(HOST)


@pytest.mark.parametrize("raw", [
    "not json", "[]", '{"v": 2, "pid": 4000}',
    json.dumps({**_kept_record(), "v": 1, "pid": 1}),
    json.dumps({**_kept_record(), "v": 1, "metrics_port": 0}),
    json.dumps({**_kept_record(), "v": 1, "port": 70000}),
    json.dumps({**_kept_record(), "v": 1, "binary": ""}),
    json.dumps({k: v for k, v in {**_kept_record(), "v": 1}.items() if k != "host_sha256"}),
], ids=["garbage", "list", "other-version", "pid-1", "metrics-0", "port-70000",
        "no-binary", "no-hash"])
def test_an_unusable_record_is_none(table, raw):
    path = tm._record_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw, encoding="utf-8")
    assert tm._read_record() is None


# ---- a spawned tunnel is recorded --------------------------------------------

def test_a_new_tunnel_is_recorded_as_this_daemons(table, run_async):
    table.hosts[40001] = json.dumps({"hostname": HOST})
    calls, on_change = _published()
    manager = tm.TunnelManager(8765, on_change)

    async def _run():
        await manager.start()
        await _until(lambda: calls)
        await manager.stop()

    run_async(_run())
    assert calls[0] == URL
    # Killed on a plain stop, and its record forgotten.
    assert (4000, signal.SIGTERM) in table.signals
    assert not tm._record_path().exists()


def test_a_running_tunnel_has_a_record_owned_by_this_process(table, run_async):
    table.hosts[40001] = json.dumps({"hostname": HOST})
    calls, on_change = _published()
    manager = tm.TunnelManager(8765, on_change)
    seen = {}

    async def _run():
        await manager.start()
        await _until(lambda: calls)
        seen.update(tm._read_record())
        await manager.stop()

    run_async(_run())
    assert seen["pid"] == 4000 and seen["owner"] == os.getpid()
    assert seen["handover_token"] == "" and seen["port"] == 8765


# ---- stop: keep or kill ---------------------------------------------------------

def _run_then(table, run_async, then):
    """Start a manager on a fresh tunnel, wait for its URL, then await
    ``then(manager)``, all on one event loop."""
    table.hosts[40001] = json.dumps({"hostname": HOST})
    calls, on_change = _published()
    manager = tm.TunnelManager(8765, on_change)

    async def _run():
        await manager.start()
        await _until(lambda: calls)
        await then(manager)
    run_async(_run())
    return manager


def test_a_planned_restart_keeps_the_tunnel_with_a_guard(table, run_async):
    manager = _run_then(table, run_async, lambda m: m.stop(keep=True))

    assert 4000 in table.alive and table.signals == []
    rec = tm._read_record()
    assert rec["owner"] == 0 and len(rec["handover_token"]) == 16
    [guard] = table.guards()
    assert guard[:2] == ["sh", "-c"] and guard[2] == tm._GUARD_SCRIPT
    assert guard[4:] == ["4000", str(tm._record_path()), rec["handover_token"], "180",
                         "/fake/cloudflared", "http://127.0.0.1:8765"]
    assert manager.current_url == "" and tunnel_mod._managed_tunnel_url == ""
    # The guard's launcher exits at once and is reaped: no zombie survives
    # into the image an in-place restart execs.
    assert table.popens[-1].waited is True


def test_keeping_turned_off_kills_even_on_a_planned_restart(table, run_async, monkeypatch):
    monkeypatch.setattr("aipager.config.MINIAPP_KEEP_TUNNEL", False)
    _run_then(table, run_async, lambda m: m.stop(keep=True))

    assert 4000 not in table.alive and table.guards() == []
    assert not tm._record_path().exists()


def test_a_plain_stop_kills_and_forgets(table, run_async):
    _run_then(table, run_async, lambda m: m.stop(keep=False))

    assert (4000, signal.SIGTERM) in table.signals and 4000 not in table.alive
    assert table.guards() == [] and not tm._record_path().exists()


def test_a_stubborn_tunnel_gets_a_sigkill(table, run_async):
    table.stubborn.add(4000)
    _run_then(table, run_async, lambda m: m.stop())
    assert table.signals == [(4000, signal.SIGTERM), (4000, signal.SIGKILL)]


def test_a_shutdown_never_waits_forever_on_a_tunnel_it_cannot_end(table, run_async):
    """One adopted from the last daemon is not this image's child: if it
    never goes away (a zombie nobody reaps yet), the stop still returns."""
    table.unkillable.add(4000)

    async def _bounded_stop(manager):
        await asyncio.wait_for(manager.stop(), timeout=2.0)
    _run_then(table, run_async, _bounded_stop)
    assert table.signals == [(4000, signal.SIGTERM), (4000, signal.SIGKILL)]


def test_a_dead_tunnel_is_not_kept(table, run_async):
    async def _die_then_stop(manager):
        table.alive.discard(4000)
        await manager.stop(keep=True)
    _run_then(table, run_async, _die_then_stop)
    assert table.guards() == [] and not tm._record_path().exists()


# ---- startup: take over or replace ---------------------------------------------

def _start_and_settle(table, run_async, *, keep=False):
    """Start a manager, wait for its first URL, then stop it (keeping the
    tunnel when *keep*). Returns the first URL it published."""
    calls, on_change = _published()
    manager = tm.TunnelManager(8765, on_change)

    async def _run():
        await manager.start()
        await _until(lambda: calls)
        first = calls[0]
        rec = tm._read_record()
        await manager.stop(keep=keep)
        return first, rec
    return run_async(_run())


def test_the_next_daemon_takes_over_a_kept_tunnel(table, run_async):
    table.add_tunnel(5000)
    tm._write_record(_kept_record())
    url, rec = _start_and_settle(table, run_async, keep=True)

    assert url == URL
    assert [a for a, _k in table.spawns if a[0] != "sh"] == []   # no new cloudflared
    assert table.probes == [URL]
    assert rec["owner"] == os.getpid() and rec["handover_token"] == ""
    assert 5000 in table.alive and table.signals == []           # kept again


def test_a_crash_leftover_is_taken_over_too(table, run_async):
    table.add_tunnel(5000)
    tm._write_record(_kept_record(owner=999999, token=""))   # its owner is gone
    url, _rec = _start_and_settle(table, run_async)
    assert url == URL and _fresh_spawned(table) == []


def _fresh_spawned(table):
    return [a for a, _k in table.spawns if a[0] == "/fake/cloudflared"]


@pytest.mark.parametrize("case,killed", [
    ("other-boot", False), ("dead", False), ("not-cloudflared", False),
    ("other-port", True), ("other-host", True), ("no-metrics", True),
    ("not-answering", True),
], ids=lambda v: v if isinstance(v, str) else None)
def test_a_kept_tunnel_failing_a_check_is_replaced(table, run_async, case, killed):
    table.add_tunnel(5000, port=9999 if case == "other-port" else 8765)
    rec = _kept_record(port=9999 if case == "other-port" else 8765)
    if case == "other-boot":
        rec["boot_id"] = "boot-0"
    elif case == "dead":
        table.alive.discard(5000)
    elif case == "not-cloudflared":
        table.argv[5000] = ["/usr/bin/vim", "notes.txt"]
    elif case == "other-host":
        table.hosts[40001] = json.dumps({"hostname": "other-words.trycloudflare.com"})
    elif case == "no-metrics":
        table.hosts[40001] = None
    elif case == "not-answering":
        # Only the kept address: the fresh tunnel's own probe (the manager
        # probes every new URL) answers.
        table.dead_urls.add(f"https://{HOST}")
    tm._write_record(rec)
    table.hosts[40002] = json.dumps({"hostname": "fresh-words.trycloudflare.com"})
    table.next_port = 40002

    url, rec = _start_and_settle(table, run_async)

    assert url == "https://fresh-words.trycloudflare.com"
    assert len(_fresh_spawned(table)) == 1
    sent = [s for p, s in table.signals if p == 5000]
    if killed:
        assert sent == [signal.SIGTERM] and 5000 not in table.alive
    else:
        assert sent == []                                  # never unproven
    assert rec["pid"] == 4000                              # the fresh one's


def test_a_tunnel_another_live_daemon_holds_is_left_alone(table, run_async):
    table.add_tunnel(5000)
    table.alive.add(4242)                                   # its owner, running
    table.argv[4242] = ["/usr/bin/python3", "/home/alice/.local/bin/aipager", "start"]
    tm._write_record(_kept_record(owner=4242, token=""))
    table.hosts[40002] = json.dumps({"hostname": "fresh-words.trycloudflare.com"})
    table.next_port = 40002
    _start_and_settle(table, run_async)
    assert [p for p, _s in table.signals if p == 5000] == [] and 5000 in table.alive


def test_a_dead_owners_pid_reused_by_something_else_does_not_hold_it(table, run_async):
    """A crash leftover whose owner's pid now belongs to another program
    is still a leftover: it is taken over, never forgotten running."""
    table.add_tunnel(5000)
    table.alive.add(4242)
    table.argv[4242] = ["/usr/bin/vim", "notes.txt"]
    tm._write_record(_kept_record(owner=4242, token=""))
    url, rec = _start_and_settle(table, run_async)
    assert url == URL and _fresh_spawned(table) == [] and rec["owner"] == os.getpid()


def test_an_adopted_tunnel_that_dies_is_replaced(table, run_async):
    table.add_tunnel(5000)
    tm._write_record(_kept_record())
    table.hosts[40002] = json.dumps({"hostname": "fresh-words.trycloudflare.com"})
    table.next_port = 40002
    calls, on_change = _published()
    manager = tm.TunnelManager(8765, on_change)

    async def _run():
        await manager.start()
        await _until(lambda: calls == [URL])
        table.alive.discard(5000)                           # it dies
        await _until(lambda: "https://fresh-words.trycloudflare.com" in calls, 5.0)
        await manager.stop()

    run_async(_run())
    assert calls[:3] == [URL, "", "https://fresh-words.trycloudflare.com"]


# ---- identity -----------------------------------------------------------------

@pytest.mark.parametrize("argv,ours", [
    (["/fake/cloudflared", "tunnel", "--url", "http://127.0.0.1:8765", "--no-autoupdate"], True),
    (["/other/cloudflared", "tunnel", "--url", "http://127.0.0.1:8765"], False),
    (["/fake/cloudflared", "tunnel", "--url", "http://127.0.0.1:876"], False),
    (["/fake/cloudflared", "tunnel", "--url", "http://127.0.0.1:87650"], False),
    (["/fake/cloudflared", "--url", "http://127.0.0.1:8765"], False),
    (["/fake/cloudflared", "tunnel", "http://127.0.0.1:8765"], False),
    (["/fake/cloudflared tunnel --url http://127.0.0.1:8765 --no-autoupdate"], True),
    (["/fake/cloudflaredX tunnel --url http://127.0.0.1:8765"], False),
], ids=["exact", "other-binary", "port-prefix", "port-longer", "no-tunnel", "no-url-flag",
        "ps-line", "ps-binary-prefix"])
def test_only_our_tunnel_is_ours(monkeypatch, argv, ours):
    monkeypatch.setattr(tm, "_cmdline", lambda pid: argv)
    assert tm._is_our_tunnel(4000, "/fake/cloudflared", 8765) is ours


def test_a_ps_line_keeps_a_binary_path_with_spaces(monkeypatch):
    binary = "/Users/alice/Library/Application Support/aipager/cloudflared"
    monkeypatch.setattr(tm, "_cmdline", lambda pid: [
        f"{binary} tunnel --url http://127.0.0.1:8765 --no-autoupdate"])
    assert tm._is_our_tunnel(4000, binary, 8765) is True


def test_a_signal_is_never_sent_to_a_pid_that_is_not_our_tunnel(table):
    table.argv[4000] = ["/usr/bin/vim"]
    table.alive.add(4000)
    tunnel = tm.Tunnel(4000, 40001, "/fake/cloudflared", 8765)
    with pytest.raises(ProcessLookupError):
        tunnel.terminate()
    assert table.signals == []


def test_uninstall_stops_a_recorded_tunnel(table):
    table.add_tunnel(5000)
    tm._write_record(_kept_record())
    assert tm.kill_recorded_tunnel() is True
    assert table.signals == [(5000, signal.SIGTERM)] and not tm._record_path().exists()


def test_uninstall_never_signals_a_reused_pid(table):
    table.argv[5000] = ["/usr/bin/vim"]
    table.alive.add(5000)
    tm._write_record(_kept_record())
    assert tm.kill_recorded_tunnel() is False
    assert table.signals == [] and not tm._record_path().exists()


# ---- the real process table (Linux) ---------------------------------------------

linux_only = pytest.mark.skipif(not os.path.exists("/proc/self/cmdline"),
                                reason="needs /proc")


@linux_only
def test_an_exited_child_is_reaped_and_not_alive():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    end = time.monotonic() + 5
    while time.monotonic() < end:
        try:
            with open(f"/proc/{proc.pid}/stat", encoding="ascii") as fh:
                if fh.read().rsplit(")", 1)[1].split()[0] == "Z":
                    break
        except OSError:
            break
        time.sleep(0.01)
    assert tm._pid_alive(proc.pid) is False
    assert not os.path.exists(f"/proc/{proc.pid}")         # reaped, not a zombie
    assert tm._pid_alive(os.getpid()) is True


def _stand_in(tmp_path, binary, url):
    """A harmless process whose /proc cmdline reads like our tunnel:
    argv[0] is the fake binary path, and the script python runs is a
    file named "tunnel" in its working folder."""
    (tmp_path / "tunnel").write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    proc = subprocess.Popen([binary, "tunnel", "--url", url, "--no-autoupdate"],
                            executable=sys.executable, cwd=tmp_path,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # Until its exec is done, /proc shows the forking test's command line.
    want = [binary, "tunnel", "--url", url, "--no-autoupdate"]
    end = time.monotonic() + 5
    while tm._cmdline(proc.pid) != want and time.monotonic() < end:
        time.sleep(0.01)
    return proc


def _run_guard(pid, record, token, binary, url):
    """Run the real guard and wait until its backgrounded body is done
    (no process left whose command line names this test's record; a /proc
    scan, so Linux only, like its callers)."""
    subprocess.run(["sh", "-c", tm._GUARD_SCRIPT, "guard", str(pid), str(record),
                    token, "0", binary, url], timeout=10, check=False)
    needle = str(record).encode()
    end = time.monotonic() + 10
    while time.monotonic() < end:
        busy = False
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            try:
                with open(f"/proc/{entry}/cmdline", "rb") as fh:
                    if needle in fh.read():
                        busy = True
                        break
            except OSError:
                continue
        if not busy:
            return
        time.sleep(0.01)
    raise AssertionError("the guard never finished")


@linux_only
@pytest.mark.parametrize("case", ["unclaimed", "claimed", "not-ours"])
def test_the_guard_kills_only_an_unclaimed_tunnel_of_ours(tmp_path, case):
    binary, url = "/fake/cloudflared", "http://127.0.0.1:8765"
    stand_in = _stand_in(tmp_path, binary, url if case != "not-ours" else url + "0")
    try:
        record = tmp_path / "aipager-tunnel.json"
        token = "tok123" if case != "claimed" else ""
        record.write_text(json.dumps({"pid": stand_in.pid, "handover_token": token}),
                          encoding="utf-8")
        if case == "unclaimed":
            # The real readers see it as our tunnel, exactly.
            assert tm._cmdline(stand_in.pid) == [binary, "tunnel", "--url", url,
                                                 "--no-autoupdate"]
            assert tm._is_our_tunnel(stand_in.pid, binary, 8765) is True
        _run_guard(stand_in.pid, record, "tok123", binary, url)
        if case == "unclaimed":
            assert stand_in.wait(timeout=5) is not None
            assert not record.exists()
        else:
            assert stand_in.poll() is None
            assert record.exists()
    finally:
        if stand_in.poll() is None:
            stand_in.kill()
            stand_in.wait(timeout=5)


def test_a_record_naming_another_tunnel_is_not_forgotten(table):
    tm._write_record(_kept_record(pid=5000))
    tm._delete_record(4000)
    assert tm._read_record()["pid"] == 5000
    tm._delete_record(5000)
    assert not tm._record_path().exists()


# ---- keep and claim failures ------------------------------------------------------

def test_no_record_means_no_keep(table, run_async, monkeypatch):
    """A kept tunnel nothing could stop if no daemon came back is a leak:
    without its record, it is stopped like any other stop."""
    real_write = tm._write_record

    async def _break_then_stop(manager):
        monkeypatch.setattr(tm, "_write_record", lambda rec: False)
        await manager.stop(keep=True)
        monkeypatch.setattr(tm, "_write_record", real_write)
    _run_then(table, run_async, _break_then_stop)
    assert 4000 not in table.alive and table.guards() == []


def test_no_guard_means_no_keep(table, run_async, monkeypatch):
    async def _break_then_stop(manager):
        monkeypatch.setattr(tm, "_start_guard", lambda tunnel, token: False)
        await manager.stop(keep=True)
    _run_then(table, run_async, _break_then_stop)
    assert 4000 not in table.alive and not tm._record_path().exists()


def test_a_guard_that_cannot_start_reports_it(table):
    table.spawn_error = OSError("fork failed")
    tunnel = tm.Tunnel(4000, 40001, "/fake/cloudflared", 8765, host=HOST)
    assert tm._start_guard(tunnel, "tok") is False


def test_a_claim_that_cannot_be_written_is_not_a_takeover(table, run_async, monkeypatch):
    table.add_tunnel(5000)
    tm._write_record(_kept_record())
    real_write = tm._write_record
    monkeypatch.setattr(tm, "_write_record",
                        lambda rec: False if rec["pid"] == 5000 else real_write(rec))
    table.hosts[40002] = json.dumps({"hostname": "fresh-words.trycloudflare.com"})
    table.next_port = 40002
    url, _rec = _start_and_settle(table, run_async)
    assert url == "https://fresh-words.trycloudflare.com"
    assert (5000, signal.SIGTERM) in table.signals


def test_a_running_tunnel_that_stops_being_ours_is_replaced(table, run_async):
    """A zombie nobody reaps (or a reused pid) has no matching command
    line: on Linux the poll notices it, and a new tunnel starts."""
    if not sys.platform.startswith("linux"):
        pytest.skip("identity is polled on Linux only")
    assert tm._CHECK_IDENTITY_WHILE_RUNNING is True
    table.hosts[40001] = json.dumps({"hostname": HOST})
    table.hosts[40002] = json.dumps({"hostname": "fresh-words.trycloudflare.com"})
    calls, on_change = _published()
    manager = tm.TunnelManager(8765, on_change)

    async def _run():
        await manager.start()
        await _until(lambda: calls == [URL])
        table.argv[4000] = []                        # alive, but no command line
        await _until(lambda: "https://fresh-words.trycloudflare.com" in calls, 5.0)
        await manager.stop()
    run_async(_run())


# ---- a whole planned restart, two daemons ---------------------------------------

def test_a_planned_restart_round_trip_keeps_the_address(table, run_async):
    """Daemon A starts a tunnel and stops with keep; daemon B starts,
    takes the same tunnel over (no new cloudflared) and publishes the
    same address; the record is B's and the guard carried A's token."""
    table.hosts[40001] = json.dumps({"hostname": HOST})
    first, on_a = _published()
    second, on_b = _published()

    async def _run():
        a = tm.TunnelManager(8765, on_a)
        await a.start()
        await _until(lambda: first)
        await a.stop(keep=True)
        token = tm._read_record()["handover_token"]
        b = tm.TunnelManager(8765, on_b)
        await b.start()
        await _until(lambda: second)
        rec = tm._read_record()
        await b.stop(keep=True)
        return token, rec
    token, rec = run_async(_run())

    assert first == [URL] and second == [URL]
    assert len(_fresh_spawned(table)) == 1               # only A's
    assert rec["owner"] == os.getpid() and rec["handover_token"] == ""
    guards = table.guards()
    assert guards[0][6] == token and len(guards) == 2   # A's keep, then B's
    assert 4000 in table.alive and table.signals == []


# ---- the metrics reader, against a real local server -------------------------------

def _serve(run_async, reply, body_size=None, hang=False):
    """Start a one-shot HTTP server on 127.0.0.1:0 answering *reply*, ask it
    through the real `_query_metrics`, and return what that gave."""
    async def _run():
        async def handle(reader, writer):
            await reader.readuntil(b"\r\n\r\n")
            if hang:
                await asyncio.sleep(10)
            data = reply if body_size is None else reply + b"x" * body_size
            writer.write(data)
            await writer.drain()
            writer.close()
        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            return await tm._query_metrics(port, "/quicktunnel")
        finally:
            server.close()
    return run_async(_run())


def test_metrics_a_200_answer_is_its_body(run_async):
    body = _serve(run_async, b'HTTP/1.0 200 OK\r\nContent-Type: text/plain\r\n\r\n'
                             b'{"hostname":"' + HOST.encode() + b'"}')
    assert json.loads(body) == {"hostname": HOST}


def test_metrics_another_status_is_none(run_async):
    assert _serve(run_async, b"HTTP/1.1 404 Not Found\r\n\r\nnot here") is None


def test_metrics_a_reply_that_is_not_http_is_none(run_async):
    assert _serve(run_async, b"FTP 200 ready\r\n\r\n{}") is None


def test_metrics_reading_stops_at_the_cap(run_async):
    body = _serve(run_async, b"HTTP/1.0 200 OK\r\n\r\n", body_size=200_000)
    assert body is not None and len(body) < 16_384


def test_metrics_a_server_that_never_answers_is_none(run_async, monkeypatch):
    monkeypatch.setattr(tm, "_METRICS_TIMEOUT_SECONDS", 0.05)
    assert _serve(run_async, b"", hang=True) is None


def test_metrics_a_closed_port_is_none(run_async):
    port = tm._free_port()
    assert run_async(tm._query_metrics(port, "/quicktunnel")) is None


@pytest.mark.parametrize("body", ["[1]", "null", "not json", '{"hostname": 7}', ""],
                         ids=["list", "null", "garbage", "not-text", "empty"])
def test_a_quick_tunnel_host_that_is_not_a_hostname_is_empty(monkeypatch, run_async, body):
    async def _answer(port, path):
        return body
    monkeypatch.setattr(tm, "_query_metrics", _answer)
    assert run_async(tm._quick_tunnel_host(40001)) == ""


def test_a_stale_temp_file_does_not_loosen_the_record(table):
    path = tm._record_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("{}", encoding="utf-8")
    os.chmod(tmp, 0o644)                                 # a crash left it
    tm._write_record(_kept_record())
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_a_tunnel_signal_refused_by_the_system_never_breaks_shutdown(run_async):
    class _NotOurs:
        returncode = None

        def terminate(self):
            raise PermissionError("not ours to signal")

    manager = tm.TunnelManager(8765, None)
    manager._proc = _NotOurs()
    run_async(manager.stop())


@pytest.mark.parametrize("argv,daemon", [
    (["/usr/bin/python3", "/home/alice/.local/bin/aipager", "start"], True),
    (["/usr/bin/python3", "-m", "aipager", "start"], True),
    (["/usr/bin/python3", "/home/alice/.local/bin/aipager-hook"], False),
    (["/usr/bin/vim", "/home/alice/aipager/notes.txt"], False),
    (["/opt/homebrew/bin/python3 /opt/homebrew/bin/aipager start"], True),
    (["/opt/homebrew/bin/python3 /opt/homebrew/bin/aipager-hook"], False),
    (["/nix/store/abc-aipager/bin/.aipager-wrapped", "start"], True),
], ids=["shim", "module", "hook", "a-file-named-like-it", "ps-line", "ps-line-hook", "nix"])
def test_only_aipager_itself_holds_a_tunnel(monkeypatch, argv, daemon):
    monkeypatch.setattr(tm, "_cmdline", lambda pid: argv)
    assert tm._is_aipager(4242) is daemon


def test_a_kept_tunnel_of_this_boot_is_known(table):
    assert tm.has_kept_tunnel() is False
    tm._write_record(_kept_record())
    assert tm.has_kept_tunnel() is True
    table.boot = "boot-2"                                 # the machine rebooted
    assert tm.has_kept_tunnel() is False
