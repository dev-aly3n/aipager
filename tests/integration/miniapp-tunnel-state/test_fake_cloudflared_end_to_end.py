"""End to end through the REAL ``spawn_and_discover_url`` with a fake
cloudflared: a tiny script under tmp_path that writes cloudflared-style
lines to its ``--logfile`` and stderr, then exits with a chosen code.
Never the real cloudflared, never the network (the metrics query is the
fake process table's, which knows no tunnel host).

Covers: the reason read from cloudflared's own log (the incident's exact
line) and from its stderr, the stderr file being owner-only and emptied
per spawn, the real exit code logged, the dead child reaped, and /app
telling the operator the DNS reason (spec requirements 1, 3 and 4;
design.md success criteria "Reasons", "A spawned cloudflared's real exit
status", "The stderr file is created 0600 and emptied per spawn").
"""
from __future__ import annotations

import logging
import os
import re
import stat
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from aipager.miniapp import tunnel_state

from miniapp_tunnel_state_bb import (  # noqa: I001 (the package conftest)
    DNS_INCOMPLETE_TEXT, DNS_LOOKUP_TEXT, QUICK_TUNNEL_TEXT, cf_paths, log_line, settle,
    wait_until,
)


@pytest.fixture
def slow_backoff(monkeypatch):
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_BASE_SECONDS", 100.0)
    monkeypatch.setattr("aipager.config.TUNNEL_RESTART_BACKOFF_MAX_SECONDS", 100.0)


def _first_failure(make_manager, run_async, caplog=None, then=None):
    async def scenario():
        mgr = make_manager()
        if caplog is not None:
            caplog.set_level(logging.DEBUG)
        await mgr.start()
        try:
            await wait_until(lambda: tunnel_state.current_status() is not None
                             and tunnel_state.current_status().phase == "retrying",
                             timeout=10.0)
            status = tunnel_state.current_status()
            extra = await then() if then else None
            return status, extra
        finally:
            await mgr.stop()
    return run_async(scenario())


# ---- reasons from the real files -------------------------------------------------

def test_the_incident_log_line_gives_dns_incomplete(
    cloudflared, slow_backoff, make_manager, run_async,
):
    cloudflared.configure(log_lines=[log_line("Initiating shutdown", error=DNS_INCOMPLETE_TEXT)])
    status, _ = _first_failure(make_manager, run_async)
    assert status.reason == "dns_incomplete"


def test_a_srv_lookup_failure_gives_dns_lookup_failed(
    cloudflared, slow_backoff, make_manager, run_async,
):
    cloudflared.configure(log_lines=[log_line("Initiating shutdown", error=DNS_LOOKUP_TEXT)])
    status, _ = _first_failure(make_manager, run_async)
    assert status.reason == "dns_lookup_failed"


def test_a_quick_tunnel_failure_on_stderr_gives_quick_tunnel(
    cloudflared, slow_backoff, make_manager, run_async,
):
    """cloudflared prints this on stderr only (urfave/cli), the log stays empty."""
    cloudflared.configure(stderr=f"{QUICK_TUNNEL_TEXT}\n")
    status, _ = _first_failure(make_manager, run_async)
    assert status.reason == "quick_tunnel"


def test_an_unknown_error_gives_other(cloudflared, slow_backoff, make_manager, run_async):
    cloudflared.configure(log_lines=[log_line("Initiating shutdown", error="zebra-marker-7781")],
                          stderr="zebra-marker-7781\n")
    status, _ = _first_failure(make_manager, run_async)
    assert status.reason == "other"


def test_a_garbage_log_gives_other(cloudflared, slow_backoff, make_manager, run_async):
    cloudflared.configure(log_lines=["\u0000not json{{{", "[1,2"], stderr="")
    status, _ = _first_failure(make_manager, run_async)
    assert status.reason == "other"


# ---- the stderr file ---------------------------------------------------------------

def test_cloudflared_gets_a_logfile_argument(cloudflared, slow_backoff, make_manager, run_async):
    _first_failure(make_manager, run_async)
    argv = cloudflared.argvs[0]
    assert Path(argv[argv.index("--logfile") + 1]) == cf_paths()[0]


def test_the_stderr_is_a_regular_file_not_a_pipe(
    cloudflared, slow_backoff, make_manager, run_async,
):
    _first_failure(make_manager, run_async)
    mode, _size = cloudflared.stderr_at_spawn[0]
    assert stat.S_ISREG(mode)


def test_the_stderr_file_is_owner_only(cloudflared, slow_backoff, make_manager, run_async):
    _first_failure(make_manager, run_async)
    assert stat.S_IMODE(os.stat(cf_paths()[1]).st_mode) == 0o600


def test_the_stderr_file_is_owner_only_even_if_it_existed_wider(
    cloudflared, slow_backoff, make_manager, run_async,
):
    err = cf_paths()[1]
    err.parent.mkdir(parents=True, exist_ok=True)
    err.write_text("old\n")
    err.chmod(0o644)
    _first_failure(make_manager, run_async)
    assert stat.S_IMODE(os.stat(err).st_mode) == 0o600


def test_the_stderr_file_is_emptied_at_spawn(cloudflared, slow_backoff, make_manager, run_async):
    err = cf_paths()[1]
    err.parent.mkdir(parents=True, exist_ok=True)
    err.write_text(f"{QUICK_TUNNEL_TEXT}\n" * 50)
    _first_failure(make_manager, run_async)
    assert cloudflared.stderr_at_spawn[0][1] == 0


def test_an_old_stderr_cause_is_never_reported_for_a_new_tunnel(
    cloudflared, slow_backoff, make_manager, run_async,
):
    """Error guessing: a stale .err from an earlier run must not classify
    a new failure."""
    err = cf_paths()[1]
    err.parent.mkdir(parents=True, exist_ok=True)
    err.write_text(f"{QUICK_TUNNEL_TEXT}\n")
    cloudflared.configure(log_lines=[log_line("Initiating shutdown", error="zebra-marker-7781")])
    status, _ = _first_failure(make_manager, run_async)
    assert status.reason == "other"


def test_the_stderr_file_holds_what_cloudflared_printed(
    cloudflared, slow_backoff, make_manager, run_async,
):
    cloudflared.configure(stderr=f"{QUICK_TUNNEL_TEXT}\n")
    _first_failure(make_manager, run_async)
    assert QUICK_TUNNEL_TEXT in cf_paths()[1].read_text()


# ---- exit code and reap --------------------------------------------------------------

def test_the_real_exit_code_is_logged(cloudflared, slow_backoff, make_manager, run_async, caplog):
    cloudflared.configure(log_lines=[log_line("Initiating shutdown", error=DNS_INCOMPLETE_TEXT)],
                          code=3)
    _first_failure(make_manager, run_async, caplog)
    msgs = [r.getMessage() for r in caplog.records if r.name.startswith("aipager")]
    assert any(re.search(r"code=3\b", m) for m in msgs), msgs


def test_the_dead_child_is_reaped(cloudflared, slow_backoff, make_manager, run_async):
    cloudflared.configure(code=1)
    _first_failure(make_manager, run_async)
    pid = cloudflared.children[0].pid
    assert not Path(f"/proc/{pid}").exists() or "Z" not in (
        Path(f"/proc/{pid}/stat").read_text().split(") ")[1][:1])


def test_a_failed_launch_raises_the_launch_error_type(cloudflared, run_async):
    from aipager.miniapp.tunnel_manager import TunnelLaunchError, spawn_and_discover_url

    cloudflared.configure(log_lines=[log_line("Initiating shutdown", error=DNS_INCOMPLETE_TEXT)])

    async def go():
        try:
            await spawn_and_discover_url(str(cloudflared.path), 8765)
        except TunnelLaunchError as e:
            return e
        return None

    assert isinstance(run_async(go()), TunnelLaunchError)


# ---- the incident, end to end, through /app ------------------------------------------------

def test_app_tells_the_operator_the_dns_reason_for_the_incident(
    cloudflared, slow_backoff, make_manager, run_async, mk_bot, mk_update, monkeypatch,
):
    monkeypatch.setattr("aipager.config.MINIAPP_ENABLED", True)
    monkeypatch.setattr("aipager.config.MINIAPP_PORT", 8765)
    monkeypatch.setattr("aipager.config.CHAT_ID", "555")
    monkeypatch.setattr("aipager.miniapp.server.miniapp_extra_available", lambda: True)
    cloudflared.configure(log_lines=[log_line("Initiating shutdown", error=DNS_INCOMPLETE_TEXT)])
    bot = mk_bot()
    upd = mk_update("/app", user_id=555, chat_id=555)
    upd.effective_chat.type = "private"

    async def then():
        await bot._handle_app_cmd(upd, MagicMock())

    _first_failure(make_manager, run_async, then=then)
    text = upd.message.reply_text.await_args.args[0]
    assert "DNS server gave an incomplete answer" in text and "SRV" not in text, text


def test_the_restart_follows_promptly_after_the_exit(cloudflared, make_manager, run_async):
    """The default fast backoff (0.01 s here): a second spawn follows the
    first exit without waiting for a probe window or a check interval
    pile-up."""
    cloudflared.configure(code=1, delay=0.1)

    async def scenario():
        mgr = make_manager()
        await mgr.start()
        try:
            await wait_until(lambda: len(cloudflared.children) >= 2, timeout=10.0)
            await settle(lambda: len(cloudflared.children), stable_checks=5)
            return len(cloudflared.children)
        finally:
            await mgr.stop()

    assert run_async(scenario()) >= 2
