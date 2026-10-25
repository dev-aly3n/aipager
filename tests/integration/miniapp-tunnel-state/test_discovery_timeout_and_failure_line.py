"""Two fixes, end to end through the REAL ``spawn_and_discover_url`` with a
fake cloudflared script under tmp_path (never the real binary, never the
network):

1. No zombie after a discovery timeout (review-1 rev-iter1-003; spec
   requirement 1 "the process is reaped at once (no zombie ...)"): a
   cloudflared that never reports a URL is killed AND reaped promptly,
   in the fast phase and in the slow phase (where a zombie would linger
   for a whole slow interval). Partitions: the script obeys SIGTERM,
   the script ignores it.
2. The failure log line never prints cloudflared's raw words twice
   (test-report-1 tester-iter1-004; entrypoints.md "One WARNING per
   failed fast attempt, naming the reason id and cloudflared's raw
   text"). Partitions: the same text in the log and on stderr (the
   incident), the same text in two log lines plus stderr, log only,
   stderr only, two different texts.

The test's kill seam never polls or waits, so only the product can reap.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

from aipager.miniapp import tunnel_state

from miniapp_tunnel_state_bb import (  # noqa: I001 (the package conftest)
    DNS_INCOMPLETE_TEXT, QUICK_TUNNEL_TEXT, _proc_state, child_is_gone, log_line,
    wait_until,
)


@pytest.fixture
def long_backoff(monkeypatch):
    """One attempt, then nothing: no later Popen() can reap the old child
    as a side effect inside the observation window."""
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_BASE_SECONDS", 100.0)
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_MAX_SECONDS", 100.0)


def _status():
    return tunnel_state.current_status()


# ---- 1. reaped after a discovery timeout -------------------------------------------

def _discovery_timeout(cloudflared, monkeypatch, make_manager, run_async, *,
                       ignore_sigterm=False, max_attempts=3):
    monkeypatch.setattr("aipager.config.TUNNEL_URL_DISCOVERY_TIMEOUT_SECONDS", 0.5)
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_MAX_ATTEMPTS", max_attempts)
    monkeypatch.setattr("aipager.config.TUNNEL_RETRY_SLOW_SECONDS", 3600.0)
    # Never reports a URL: no log line, nothing on the metrics port; it
    # would sleep 30 s if left alone.
    cloudflared.configure(code=0, delay=30.0, ignore_sigterm=ignore_sigterm)
    obs = {}

    async def scenario():
        mgr = make_manager()
        await mgr.start()
        try:
            await wait_until(lambda: _status() is not None
                             and _status().phase in ("retrying", "slow"), timeout=10.0)
            obs["phase"] = _status().phase
            obs["reason"] = _status().reason
            pid = cloudflared.children[0].pid
            loop = asyncio.get_running_loop()
            start = loop.time()
            # "Promptly": the kill timeout (0.2 s here) plus slack, far
            # under any backoff or slow interval.
            while loop.time() - start < 1.5 and not child_is_gone(pid):
                await asyncio.sleep(0.02)
            obs["gone"] = child_is_gone(pid)
            obs["proc_state"] = _proc_state(pid)
            obs["signals"] = list(cloudflared.signals)
            obs["popen_returncode"] = cloudflared.children[0].returncode
            obs["spawns"] = len(cloudflared.children)
        finally:
            await mgr.stop()

    run_async(scenario())
    return obs


@pytest.mark.parametrize("ignore_sigterm", [False, True],
                         ids=["obeys_sigterm", "ignores_sigterm"])
def test_a_discovery_timeout_leaves_no_zombie(
    cloudflared, monkeypatch, make_manager, run_async, long_backoff, ignore_sigterm,
):
    obs = _discovery_timeout(cloudflared, monkeypatch, make_manager, run_async,
                             ignore_sigterm=ignore_sigterm)
    assert obs["gone"] is True, (obs["signals"], obs["proc_state"], obs)


def test_a_discovery_timeout_in_the_slow_phase_leaves_no_zombie(
    cloudflared, monkeypatch, make_manager, run_async, long_backoff,
):
    """MAX 1: the first failure already enters the slow phase."""
    obs = _discovery_timeout(cloudflared, monkeypatch, make_manager, run_async,
                             max_attempts=1)
    assert obs["gone"] is True, (obs["signals"], obs["proc_state"], obs)


def test_the_slow_case_really_was_in_the_slow_phase(
    cloudflared, monkeypatch, make_manager, run_async, long_backoff,
):
    """Control for the test above."""
    obs = _discovery_timeout(cloudflared, monkeypatch, make_manager, run_async,
                             max_attempts=1)
    assert obs["phase"] == "slow"


def test_no_second_spawn_reaped_it_by_accident(
    cloudflared, monkeypatch, make_manager, run_async, long_backoff,
):
    """Control: within the window only one cloudflared was spawned, so the
    reap cannot have come from a later Popen()."""
    obs = _discovery_timeout(cloudflared, monkeypatch, make_manager, run_async)
    assert obs["spawns"] == 1


def test_a_discovery_timeout_with_nothing_in_the_files_is_other(
    cloudflared, monkeypatch, make_manager, run_async, long_backoff,
):
    obs = _discovery_timeout(cloudflared, monkeypatch, make_manager, run_async)
    assert obs["reason"] == "other"


# ---- 2. the raw words once ------------------------------------------------------

OTHER_STDERR = "zebra-stderr-words-5521 something odd happened"


def _failure_records(cloudflared, make_manager, run_async, caplog, **configure):
    cloudflared.configure(**configure)

    async def scenario():
        mgr = make_manager()
        caplog.set_level(logging.DEBUG)
        await mgr.start()
        try:
            await wait_until(lambda: _status() is not None and _status().phase == "retrying",
                             timeout=10.0)
            await asyncio.sleep(0.1)
        finally:
            await mgr.stop()

    run_async(scenario())
    return [r.getMessage() for r in caplog.records if r.name.startswith("aipager")]


CASES = {
    "log_and_stderr_same": dict(
        log_lines=[log_line("Initiating shutdown", error=DNS_INCOMPLETE_TEXT)],
        stderr=f"{DNS_INCOMPLETE_TEXT}\n"),
    "two_log_lines_and_stderr_same": dict(
        log_lines=[log_line("Serve tunnel error", error=DNS_INCOMPLETE_TEXT),
                   log_line("Initiating shutdown", error=DNS_INCOMPLETE_TEXT)],
        stderr=f"{DNS_INCOMPLETE_TEXT}\n"),
    "log_only": dict(
        log_lines=[log_line("Initiating shutdown", error=DNS_INCOMPLETE_TEXT)], stderr=""),
}


@pytest.mark.parametrize("case", list(CASES), ids=list(CASES))
def test_no_log_line_repeats_cloudflareds_words(
    cloudflared, make_manager, run_async, caplog, long_backoff, case,
):
    msgs = _failure_records(cloudflared, make_manager, run_async, caplog, **CASES[case])
    assert max((m.count(DNS_INCOMPLETE_TEXT) for m in msgs), default=0) <= 1, msgs


@pytest.mark.parametrize("case", list(CASES), ids=list(CASES))
def test_the_failure_line_still_names_cloudflareds_words(
    cloudflared, make_manager, run_async, caplog, long_backoff, case,
):
    msgs = _failure_records(cloudflared, make_manager, run_async, caplog, **CASES[case])
    assert any(m.count(DNS_INCOMPLETE_TEXT) == 1 for m in msgs), msgs


def test_stderr_only_words_appear_once(
    cloudflared, make_manager, run_async, caplog, long_backoff,
):
    msgs = _failure_records(cloudflared, make_manager, run_async, caplog,
                            stderr=f"{QUICK_TUNNEL_TEXT}\n")
    assert max((m.count(QUICK_TUNNEL_TEXT) for m in msgs), default=0) == 1, msgs


def test_two_different_texts_are_each_printed_at_most_once(
    cloudflared, make_manager, run_async, caplog, long_backoff,
):
    msgs = _failure_records(
        cloudflared, make_manager, run_async, caplog,
        log_lines=[log_line("Initiating shutdown", error=DNS_INCOMPLETE_TEXT)],
        stderr=f"{OTHER_STDERR}\n")
    worst = max((max(m.count(DNS_INCOMPLETE_TEXT), m.count(OTHER_STDERR)) for m in msgs),
                default=0)
    assert worst <= 1, msgs


def test_control_the_timed_out_cloudflared_was_signalled(
    cloudflared, monkeypatch, make_manager, run_async, long_backoff,
):
    """Control: the kill went through the test's non-reaping kill seam, so
    the reap seen above can only be the product's own."""
    obs = _discovery_timeout(cloudflared, monkeypatch, make_manager, run_async)
    assert any(pid == cloudflared.children[0].pid for pid, _sig in obs["signals"]), obs
