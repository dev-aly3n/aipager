"""The daemon's tunnel state file and ``aipager doctor``'s Mini App row
(spec requirement 6; design.md success criteria "aipager doctor shows
WARN ..." and "the status file is 0600, holds no URL, and is deleted at
stop"; entrypoints.md ``read_status_file``, ``doctor_lines`` and the
``tunnel-status.json`` side effect).

Partitions for the reader: a live daemon's file, a missing file, a dead
pid, another boot, a pid that is not aipager, malformed content. For the
row: each phase, each reason, the disabled / aiohttp-missing branches
that must stay as today. Doctor never FAILs.
"""
from __future__ import annotations

import json
import os
import stat
import time
from pathlib import Path

import pytest

from aipager import doctor
from aipager.miniapp import tunnel_state
from aipager.miniapp.tunnel_manager import TunnelLaunchError

from miniapp_tunnel_state_bb import (  # noqa: I001 (the package conftest)
    FakeProcess, wait_until, write_status,
)

HOST = "status-file-secret-words.trycloudflare.com"
URL = f"https://{HOST}"
REASONS = ["dns_incomplete", "dns_lookup_failed", "quick_tunnel", "edge_unreachable",
           "binary_missing", "unreachable", "other"]


def _status_path() -> Path:
    from aipager import config
    return Path(config.TUNNEL_STATUS_FILE)


def _fail(reason="dns_incomplete"):
    return TunnelLaunchError("simulated", reason=reason)


@pytest.fixture
def slow_backoff(monkeypatch):
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_BASE_SECONDS", 100.0)
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_MAX_SECONDS", 100.0)


def _while_retrying(seam, make_manager, run_async, fn):
    seam.steps = [_fail() for _ in range(10)]

    async def scenario():
        mgr = make_manager()
        await mgr.start()
        try:
            await wait_until(lambda: _status_path().exists() and tunnel_state.current_status()
                             is not None and tunnel_state.current_status().phase == "retrying")
            await wait_until(lambda: '"retrying"' in _status_path().read_text())
            return fn()
        finally:
            await mgr.stop()

    return run_async(scenario())


# ---- the file the daemon writes -------------------------------------------------------

def test_the_daemon_writes_the_status_file(seam, slow_backoff, make_manager, run_async):
    assert _while_retrying(seam, make_manager, run_async, lambda: _status_path().exists())


def test_the_status_file_is_owner_only(seam, slow_backoff, make_manager, run_async):
    mode = _while_retrying(seam, make_manager, run_async,
                           lambda: stat.S_IMODE(os.stat(_status_path()).st_mode))
    assert mode == 0o600, oct(mode)


def test_the_status_file_lives_in_aipagers_state_folder_not_claude(
    seam, slow_backoff, make_manager, run_async,
):
    path = _while_retrying(seam, make_manager, run_async, _status_path)
    assert path.parts[-4:] == (".local", "state", "aipager", "tunnel-status.json")


def test_the_status_file_has_the_documented_fields(seam, slow_backoff, make_manager, run_async):
    data = _while_retrying(seam, make_manager, run_async,
                           lambda: json.loads(_status_path().read_text()))
    assert set(data) == {"v", "pid", "boot_id", "phase", "reason", "attempt",
                         "next_try_at", "since"}


def test_the_status_file_says_the_phase_and_reason(seam, slow_backoff, make_manager, run_async):
    data = _while_retrying(seam, make_manager, run_async,
                           lambda: json.loads(_status_path().read_text()))
    assert (data["v"], data["phase"], data["reason"], data["pid"]) == (
        1, "retrying", "dns_incomplete", os.getpid())


def test_a_live_daemons_file_reads_back(
    seam, slow_backoff, make_manager, run_async, status_file_seams,
):
    status = _while_retrying(seam, make_manager, run_async, tunnel_state.read_status_file)
    assert (status.phase, status.reason, status.attempt) == ("retrying", "dns_incomplete", 1)


def test_the_status_file_is_removed_at_stop(seam, slow_backoff, make_manager, run_async):
    seam.steps = [_fail() for _ in range(10)]

    async def scenario():
        mgr = make_manager()
        await mgr.start()
        await wait_until(_status_path().exists)
        await mgr.stop()
        return _status_path().exists()

    assert run_async(scenario()) is False


def test_the_status_file_never_holds_the_url_or_host(seam, make_manager, run_async):
    """Sampled while working and after the tunnel died (retrying)."""
    proc = FakeProcess()
    seam.steps = [(proc, URL)] + [_fail() for _ in range(10)]
    seen: list[str] = []

    async def scenario():
        mgr = make_manager()
        await mgr.start()
        try:
            await wait_until(lambda: _status_path().exists()
                             and '"working"' in _status_path().read_text())
            seen.append(_status_path().read_text())
            proc.die(1)
            await wait_until(lambda: '"working"' not in _status_path().read_text())
            seen.append(_status_path().read_text())
        finally:
            await mgr.stop()

    run_async(scenario())
    assert len(seen) == 2 and not any(HOST in s or "trycloudflare" in s for s in seen), seen


# ---- the reader fails closed ---------------------------------------------------------

def test_a_missing_file_reads_as_none(status_file_seams):
    assert tunnel_state.read_status_file() is None


def test_a_hand_written_live_file_reads_back(status_file_seams):
    write_status(phase="slow", reason="edge_unreachable", attempt=8)
    status = tunnel_state.read_status_file()
    assert (status.phase, status.reason, status.attempt) == ("slow", "edge_unreachable", 8)


def test_a_dead_daemons_file_is_ignored(status_file_seams, monkeypatch):
    monkeypatch.setattr(tunnel_state, "_daemon_alive", lambda pid: False)
    write_status()
    assert tunnel_state.read_status_file() is None


def test_another_boots_file_is_ignored(status_file_seams):
    write_status(boot_id="boot-before-the-reboot")
    assert tunnel_state.read_status_file() is None


def test_a_file_whose_pid_is_not_aipager_is_ignored(status_file_seams, monkeypatch):
    import aipager.miniapp.tunnel_manager as tm
    monkeypatch.setattr(tm, "_is_aipager", lambda pid: False)
    write_status()
    assert tunnel_state.read_status_file() is None


@pytest.mark.parametrize("raw", [
    "", "{", "not json", "[]", "null", '"retrying"',
    '{"v": 1}',
    '{"v": 1, "pid": "abc", "boot_id": "boot-test-1", "phase": "retrying"}',
    json.dumps({"v": 1, "pid": None, "boot_id": "boot-test-1", "phase": "retrying",
                "reason": "dns_incomplete", "attempt": 1, "next_try_at": 0, "since": 0}),
], ids=["empty", "truncated", "text", "list", "null", "string", "only_version",
        "pid_not_int", "pid_null"])
def test_a_malformed_file_is_ignored(status_file_seams, raw):
    write_status(raw=raw)
    assert tunnel_state.read_status_file() is None


def test_a_binary_garbage_file_is_ignored(status_file_seams):
    _status_path().parent.mkdir(parents=True, exist_ok=True)
    _status_path().write_bytes(b"\xff\xfe\x00\x01garbage")
    assert tunnel_state.read_status_file() is None


# ---- doctor's row ------------------------------------------------------------------

@pytest.fixture
def miniapp_on(monkeypatch):
    monkeypatch.setattr("aipager.config.MINIAPP_ENABLED", True)
    monkeypatch.setattr("aipager.config.MINIAPP_PORT", 8765)
    monkeypatch.setattr("aipager.miniapp.server.miniapp_extra_available", lambda: True)


def _row_text(r) -> str:
    return "\n".join(list(r.detail) + [r.fix or ""])


@pytest.mark.parametrize("phase", ["retrying", "slow"])
@pytest.mark.parametrize("reason", REASONS)
def test_doctor_failing_tunnel_is_warn(miniapp_on, status_file_seams, phase, reason):
    write_status(phase=phase, reason=reason)
    assert doctor.check_miniapp().status == doctor.WARN


@pytest.mark.parametrize("phase", ["starting", "working", "retrying", "slow"])
@pytest.mark.parametrize("reason", REASONS + ["martian_reason", ""])
def test_doctor_never_fails_on_the_tunnel(miniapp_on, status_file_seams, phase, reason):
    write_status(phase=phase, reason=reason)
    assert doctor.check_miniapp().status != doctor.FAIL


def test_doctor_shows_the_dns_reason(miniapp_on, status_file_seams):
    write_status(phase="retrying", reason="dns_incomplete")
    assert ("tunnel: not working - this machine's DNS server returns only one "
            "Cloudflare region record") in doctor.check_miniapp().detail


def test_doctor_gives_the_dns_fix(miniapp_on, status_file_seams):
    write_status(phase="retrying", reason="dns_incomplete")
    fix = doctor.check_miniapp().fix or ""
    assert "resolvectl query -t SRV _v2-origintunneld._tcp.argotunnel.com" in fix


@pytest.mark.parametrize("reason", REASONS)
def test_doctor_gives_a_fix_for_every_reason(miniapp_on, status_file_seams, reason):
    write_status(phase="retrying", reason=reason)
    assert doctor.check_miniapp().fix


@pytest.mark.parametrize("reason", REASONS)
def test_doctor_names_the_reason_in_a_not_working_line(miniapp_on, status_file_seams, reason):
    write_status(phase="retrying", reason=reason)
    assert any(d.startswith("tunnel: not working - ")
               for d in doctor.check_miniapp().detail)


def test_doctor_reasons_have_distinct_lines(miniapp_on, status_file_seams):
    lines = set()
    for reason in REASONS:
        write_status(phase="retrying", reason=reason)
        lines.add(next(d for d in doctor.check_miniapp().detail
                       if d.startswith("tunnel: not working")))
    assert len(lines) == len(REASONS)


def test_doctor_says_the_attempts_and_next_try(miniapp_on, status_file_seams):
    write_status(phase="retrying", reason="dns_incomplete", attempt=3,
                 next_try_at=time.time() + 610)
    text = _row_text(doctor.check_miniapp())
    assert "3 failed attempts; next try in about 10 min" in text, text


def test_doctor_says_trying_now_when_no_next_try(miniapp_on, status_file_seams):
    write_status(phase="retrying", reason="dns_incomplete", attempt=2, next_try_at=0)
    assert "trying now" in _row_text(doctor.check_miniapp())


def test_doctor_says_retrying_slowly_in_the_slow_phase(miniapp_on, status_file_seams):
    write_status(phase="slow", reason="dns_incomplete", attempt=8)
    assert "retrying slowly" in doctor.check_miniapp().detail


def test_doctor_does_not_say_slowly_in_the_fast_phase(miniapp_on, status_file_seams):
    write_status(phase="retrying", reason="dns_incomplete")
    assert "retrying slowly" not in doctor.check_miniapp().detail


def test_doctor_working_is_ok(miniapp_on, status_file_seams):
    write_status(phase="working", reason="")
    assert doctor.check_miniapp().status == doctor.OK


def test_doctor_working_says_so(miniapp_on, status_file_seams):
    write_status(phase="working", reason="")
    assert "tunnel: working" in doctor.check_miniapp().detail


def test_doctor_working_keeps_the_port_line(miniapp_on, status_file_seams):
    write_status(phase="working", reason="")
    assert any("enabled" in d and "8765" in d for d in doctor.check_miniapp().detail)


def test_doctor_starting_is_ok(miniapp_on, status_file_seams):
    write_status(phase="starting", reason="")
    assert doctor.check_miniapp().status == doctor.OK


def test_doctor_starting_says_so(miniapp_on, status_file_seams):
    write_status(phase="starting", reason="")
    assert "tunnel: starting" in doctor.check_miniapp().detail


def test_doctor_row_never_carries_cloudflared_text_or_the_host(miniapp_on, status_file_seams):
    write_status(phase="retrying", reason="dns_incomplete")
    text = _row_text(doctor.check_miniapp())
    assert "SRV only returned" not in text and "trycloudflare.com" not in text.replace(
        "api.trycloudflare.com", "")


def _baseline_row():
    r = doctor.check_miniapp()
    return (r.status, r.title, list(r.detail), r.fix)


@pytest.mark.parametrize("stale", ["dead_pid", "other_boot", "not_aipager", "malformed"])
def test_doctor_row_is_unchanged_when_the_daemons_state_is_stale(
    miniapp_on, status_file_seams, monkeypatch, stale,
):
    import aipager.miniapp.tunnel_manager as tm
    before = _baseline_row()           # no file at all: today's row
    if stale == "dead_pid":
        monkeypatch.setattr(tunnel_state, "_daemon_alive", lambda pid: False)
        write_status()
    elif stale == "other_boot":
        write_status(boot_id="an-older-boot")
    elif stale == "not_aipager":
        monkeypatch.setattr(tm, "_is_aipager", lambda pid: False)
        write_status()
    else:
        write_status(raw="{not json")
    assert _baseline_row() == before


def test_doctor_row_without_a_daemon_is_ok(miniapp_on, status_file_seams):
    assert doctor.check_miniapp().status == doctor.OK


def test_doctor_disabled_row_is_unchanged_by_a_failing_tunnel(status_file_seams, monkeypatch):
    monkeypatch.setattr("aipager.config.MINIAPP_ENABLED", False)
    before = _baseline_row()
    write_status(phase="slow", reason="dns_incomplete")
    assert _baseline_row() == before


def test_doctor_missing_aiohttp_row_is_unchanged_by_a_failing_tunnel(
    status_file_seams, monkeypatch,
):
    monkeypatch.setattr("aipager.config.MINIAPP_ENABLED", True)
    monkeypatch.setattr("aipager.miniapp.server.miniapp_extra_available", lambda: False)
    before = _baseline_row()
    write_status(phase="slow", reason="dns_incomplete")
    assert _baseline_row() == before


# ---- doctor_lines directly (the exported helper) -------------------------------------

def test_doctor_lines_minutes_round_from_a_fixed_now():
    status = tunnel_state.TunnelStatus(phase="retrying", reason="dns_incomplete",
                                       attempt=3, next_try_at=1_000_600.0, since=1_000_000.0)
    detail, warn, _fix = tunnel_state.doctor_lines(status, now=1_000_000.0)
    assert "3 failed attempts; next try in about 10 min" in detail + warn, (detail, warn)


def test_doctor_lines_working_has_no_fix():
    _detail, _warn, fix = tunnel_state.doctor_lines(
        tunnel_state.TunnelStatus(phase="working"), now=0.0)
    assert fix is None


def test_doctor_lines_never_raise_on_an_unknown_reason():
    detail, _warn, fix = tunnel_state.doctor_lines(
        tunnel_state.TunnelStatus(phase="slow", reason="martian_reason", attempt=9,
                                  next_try_at=0.0), now=0.0)
    assert detail and fix
