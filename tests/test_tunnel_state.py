"""aipager.miniapp.tunnel_state: reasons read from cloudflared's own
files, the status file doctor reads, and doctor's lines.

The log lines are cloudflared's zerolog JSON as aipager's --loglevel warn
writes them (keys "level", "time", "error", "message"); the strings are
cloudflared's own format strings at the pinned tag 2026.8.2, and the
incident's lines as they were captured. Every file lives under tmp_path.
"""

from __future__ import annotations

import json
import os
import stat

import pytest

from aipager.miniapp import tunnel_manager as tm
from aipager.miniapp import tunnel_state as ts

T = "2026-10-25T08:00:00Z"


def _line(message, error=None, level="error"):
    entry = {"level": level, "time": T, "message": message}
    if error is not None:
        entry["error"] = error
    return json.dumps(entry)


def _files(tmp_path, log_lines=(), err_lines=()):
    log = tmp_path / "aipager-cloudflared.log"
    err = tmp_path / "aipager-cloudflared.err"
    log.write_text("".join(f"{x}\n" for x in log_lines), encoding="utf-8")
    err.write_text("".join(f"{x}\n" for x in err_lines), encoding="utf-8")
    return log, err


# The incident: a features fetch error first, cloudflared's friendly DNS
# lines (error level, no "error" field), then the shutdown line.
INCIDENT = [
    _line("Failed to fetch features, default to disable",
          "lookup cfd-features.argotunnel.com on 127.0.0.53:53: no such host", "warn"),
    _line("Cloudflare's edge could not be found in DNS. This may be caused by your DNS "
          "server."),
    _line("Initiating shutdown",
          "expected at least 2 Cloudflare Regions regions, but SRV only returned 1"),
]

CASES = {
    "dns_incomplete": (INCIDENT, [], ts.DNS_INCOMPLETE),
    "dns_lookup_failed": ([
        _line("edge discovery: error looking up Cloudflare edge IPs: the DNS query failed",
              "lookup _v2-origintunneld._tcp.argotunnel.com: i/o timeout"),
        _line("Initiating shutdown",
              "Could not lookup srv records on _v2-origintunneld._tcp.argotunnel.com: "
              "lookup _v2-origintunneld._tcp.argotunnel.com on 127.0.0.53:53: i/o timeout"),
    ], [], ts.DNS_LOOKUP_FAILED),
    "quick_tunnel": ([], [
        "failed to request quick Tunnel: Post \"https://api.trycloudflare.com/tunnel\": "
        "dial tcp: lookup api.trycloudflare.com: no such host",
    ], ts.QUICK_TUNNEL),
    "other": ([_line("Initiating shutdown", "context canceled")], [], ts.OTHER),
    "non_json": (["this is not json", "{broken", "[1, 2]", ""], ["\x00\xff"], ts.OTHER),
}


@pytest.mark.parametrize("case", list(CASES), ids=list(CASES))
def test_classify(tmp_path, case):
    log_lines, err_lines, want = CASES[case]
    log, err = _files(tmp_path, log_lines, err_lines)
    assert ts.classify(log, err) == want


def test_classify_never_raises_on_missing_files(tmp_path):
    assert ts.classify(tmp_path / "nope.log", tmp_path / "nope.err") == ts.OTHER
    assert ts.classify_unreachable(tmp_path / "nope.log") == ts.UNREACHABLE
    assert ts.raw_cause(tmp_path / "nope.log", tmp_path / "nope.err") == ""


def test_shutdown_line_wins_over_a_later_unrelated_error_line(tmp_path):
    """The shutdown line says why cloudflared exited; an error logged
    after it (a connection torn down on the way out) does not."""
    log, err = _files(tmp_path, INCIDENT + [
        _line("Serve tunnel error", "context canceled"),
    ])
    assert ts.classify(log, err) == ts.DNS_INCOMPLETE


def test_quick_tunnel_from_stderr_with_an_empty_log(tmp_path):
    """The quick-tunnel request's failure is printed on stderr only."""
    log, err = _files(tmp_path, [], [
        "2026-10-25T08:00:00Z INF Requesting new quick Tunnel on trycloudflare.com...",
        "failed to request quick Tunnel: Post \"https://api.trycloudflare.com/tunnel\": EOF",
        "",
    ])
    assert ts.classify(log, err) == ts.QUICK_TUNNEL


def test_failed_dial_to_edge_is_edge_unreachable(tmp_path):
    log, _err = _files(tmp_path, [
        _line("Failed to dial a quic connection",
              "failed to dial to edge with quic: timeout: no recent network activity"),
    ])
    assert ts.classify_unreachable(log) == ts.EDGE_UNREACHABLE


def test_a_live_tunnel_without_a_dial_error_is_unreachable(tmp_path):
    log, _err = _files(tmp_path, [_line("Initiating shutdown", "context canceled")])
    assert ts.classify_unreachable(log) == ts.UNREACHABLE


def test_the_raw_cause_is_capped_and_one_line(tmp_path):
    log, err = _files(tmp_path, [_line("Initiating shutdown", "x" * 1000 + "\nnext")])
    raw = ts.raw_cause(log, err)
    assert len(raw) == 300 and "\n" not in raw


def test_only_the_tail_of_a_huge_log_is_read(tmp_path):
    """A log that grew for weeks is read from its last 64 KiB."""
    log, err = _files(tmp_path, [_line("old", "y" * 200)] * 2000 + INCIDENT)
    assert os.path.getsize(log) > 128 * 1024
    assert ts.classify(log, err) == ts.DNS_INCOMPLETE


# ---- the status file ----------------------------------------------------------------

@pytest.fixture
def daemon(monkeypatch):
    """This test process plays the running daemon: same boot, alive, an
    aipager (the seams doctor uses to tell a live daemon's file)."""
    monkeypatch.setattr(tm, "_boot_id", lambda: "boot-1")
    monkeypatch.setattr(ts, "_daemon_alive", lambda pid: pid == os.getpid())
    monkeypatch.setattr(tm, "_is_aipager", lambda pid: pid == os.getpid())


def _status(**kw):
    base = dict(phase=ts.SLOW, reason=ts.DNS_INCOMPLETE, attempt=8,
                next_try_at=1_900_000_000.0, since=1_800_000_000.0)
    base.update(kw)
    return ts.TunnelStatus(**base)


def test_status_file_round_trip(daemon):
    assert ts.write_status_file(_status())
    assert ts.read_status_file() == _status()


def test_status_file_is_owner_only(daemon):
    from aipager import config

    path = config.TUNNEL_STATUS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}")
    os.chmod(path, 0o644)
    assert ts.write_status_file(_status())
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    data = json.loads(path.read_text())
    assert set(data) == {"v", "pid", "boot_id", "phase", "reason", "attempt",
                         "next_try_at", "since"}


def test_status_file_writes_go_through_the_home_guard(daemon, monkeypatch):
    seen = []
    monkeypatch.setattr("aipager._test_guard.check_write", seen.append)
    ts.write_status_file(_status())
    from aipager import config
    assert seen == [config.TUNNEL_STATUS_FILE]


@pytest.mark.parametrize("case", ["dead_pid", "other_boot", "not_aipager", "malformed",
                                  "bad_phase", "missing"])
def test_stale_status_file_is_ignored(daemon, monkeypatch, case):
    from aipager import config

    assert ts.write_status_file(_status())
    if case == "dead_pid":
        monkeypatch.setattr(ts, "_daemon_alive", lambda pid: False)
    elif case == "other_boot":
        monkeypatch.setattr(tm, "_boot_id", lambda: "boot-2")
    elif case == "not_aipager":
        monkeypatch.setattr(tm, "_is_aipager", lambda pid: False)
    elif case == "malformed":
        config.TUNNEL_STATUS_FILE.write_text('{"v": 1, "pid": "x"')
    elif case == "bad_phase":
        data = json.loads(config.TUNNEL_STATUS_FILE.read_text())
        data["phase"] = "exploded"
        config.TUNNEL_STATUS_FILE.write_text(json.dumps(data))
    elif case == "missing":
        ts.delete_status_file()
    assert ts.read_status_file() is None


def test_delete_status_file_is_idempotent(daemon):
    ts.write_status_file(_status())
    ts.delete_status_file()
    ts.delete_status_file()
    assert ts.read_status_file() is None


def test_request_attempt_without_a_manager_is_false():
    assert ts.request_attempt() is False


def test_a_hook_that_raises_is_false():
    def _boom():
        raise RuntimeError("x")
    ts.set_attempt_hook(_boom)
    assert ts.request_attempt() is False


# ---- doctor's lines -------------------------------------------------------------------

def test_doctor_lines_working_and_starting_have_no_warning():
    assert ts.doctor_lines(_status(phase=ts.WORKING, reason="")) == (
        ["tunnel: working"], [], None)
    assert ts.doctor_lines(_status(phase=ts.STARTING, reason="")) == (
        ["tunnel: starting"], [], None)


@pytest.mark.parametrize("reason", ts.REASONS)
def test_doctor_lines_say_the_reason_and_a_fix(reason):
    detail, warn, fix = ts.doctor_lines(_status(reason=reason), now=1_900_000_000.0 - 600)
    assert detail[0].startswith("tunnel: not working - ")
    assert warn == ["8 failed attempts; next try in about 10 min", "retrying slowly"]
    assert fix
    assert "trycloudflare.com/" not in " ".join(detail + warn + [fix])


def test_doctor_lines_while_retrying_now():
    _detail, warn, _fix = ts.doctor_lines(_status(phase=ts.RETRYING, attempt=1,
                                                  next_try_at=0.0))
    assert warn == ["1 failed attempt; trying now"]


def test_an_unknown_reason_reads_as_other():
    detail, _warn, _fix = ts.doctor_lines(_status(reason="martian"))
    assert detail == ts.doctor_lines(_status(reason=ts.OTHER))[0]
