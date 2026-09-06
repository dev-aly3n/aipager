"""Live-daemon e2e: drive a throwaway session through the RUNNING aipager
daemon and its Mini App, with the daemon's journal as the oracle
(roadmap 8.13; the recipe the 2026-09-06 roadmap validation used).

Opt-in twice over. These tests are marked ``e2e`` like the rest of this
directory (excluded by ``addopts``), AND the module skips itself unless
``AIPAGER_E2E_LIVE=1`` is set, the daemon is running and a chat scope is
configured — because every test here posts a busy card and an answer to
the operator's Telegram chat and leaves a gone row in the registry
(which ages out after ``GONE_SESSION_MAX_AGE_DAYS``). The restart test
additionally needs ``AIPAGER_E2E_RESTART=1``: it restarts the operator's
daemon.

Run, capped like everything else::

    AIPAGER_E2E_LIVE=1 systemd-run --user --scope -q -p MemoryMax=2G \\
      -p MemorySwapMax=0 .venv/bin/python -m pytest -q -p no:cacheprovider \\
      tests/e2e/test_e2e_live_daemon.py -m e2e

``claude_available`` probes ``claude -p`` in THIS process's environment,
so from inside a Claude Code session (where a nested ``claude`` is not
logged in) run it under ``aipager.daemon_secrets.build_session_env``.

Sessions launch in the repository root, not a temp dir: Claude Code
stops a fresh directory on its "trust this folder?" dialog and an
injected prompt then goes nowhere (observed 2026-09-06 — four tests saw
the session go idle and never busy). The diff test writes its probe file
under the repo root and removes it again.

The autouse ``_never_spawn_real_dtach`` guard is left intact; the
``live_session`` fixture overrides ``inject._DTACH`` (the module
constant the spawn actually reads) and ``inject._resolve_dtach`` itself,
which is the escape hatch that guard documents. The session is addressed
to the default scope's chat, which on a personal install is the
operator's DM. Nothing here reads or
writes ``~/.claude`` or ``~/.config/aipager`` directly — the daemon does
its own bookkeeping — and the bot token only ever signs initData.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

import pytest

from tests.e2e import harness

if os.environ.get("AIPAGER_E2E_LIVE") != "1":
    pytest.skip("set AIPAGER_E2E_LIVE=1 to drive the live daemon (posts to the operator's chat)",
                allow_module_level=True)
if not harness.daemon_running():
    pytest.skip("no aipager daemon running — this module tests the live daemon",
                allow_module_level=True)

from aipager import config  # noqa: E402
from aipager.dtach import inject  # noqa: E402
from aipager.dtach.notify_hook import SOCKET_PATH  # noqa: E402
from aipager.miniapp.auth import _secret_key, verify_init_data  # noqa: E402
from aipager.state import _default_scope  # noqa: E402

_SCOPE = _default_scope()
if _SCOPE is None:
    pytest.skip("no chat scope configured — nothing to address the session to",
                allow_module_level=True)
CHAT_ID = int(_SCOPE[0])
API = f"http://127.0.0.1:{config.MINIAPP_PORT}"
REPO_ROOT = Path(__file__).resolve().parents[2]  # trusted by Claude Code; see the docstring
TURN_TIMEOUT = 240.0


def _real_dtach() -> str | None:
    """The resolver the daemon uses, minus the conftest null: the bundled
    binary, then PATH, then the pipx venv the daemon itself runs from."""
    try:
        from dtach_bin import path
        return path()
    except (ImportError, FileNotFoundError):
        pass
    found = shutil.which("dtach")
    if found:
        return found
    pipx = Path.home() / ".local/share/pipx/venvs/aipager/bin/dtach"
    return str(pipx) if pipx.exists() else None


def _child_env_clean(monkeypatch) -> None:
    """A child claude must not inherit this process's nesting vars (the
    daemon's sessions get their credential from its own env overlay).
    Through ``monkeypatch`` so the test process's environment is restored
    afterwards, like every other env-isolation fixture in this suite."""
    for k in list(os.environ):
        if k.startswith("CLAUDE") and k != "CLAUDE_CODE_OAUTH_TOKEN":
            monkeypatch.delenv(k, raising=False)


# ---- oracle + helpers -----------------------------------------------------

def now_stamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def journal(label: str, since: str) -> list[str]:
    """Daemon journal lines about ``label`` since ``since`` (wall clock),
    minus the noise every turn produces."""
    out = subprocess.run(
        ["journalctl", "--user", "-u", "aipager", "--since", since, "--no-pager",
         "-o", "short-precise"], capture_output=True, text=True, timeout=30,
    ).stdout
    keep = []
    for line in out.splitlines():
        body = line.split("]: ", 1)[-1]
        if (f"[{label}]" in body or f"claude-{label}__" in body) \
                and "dropping duplicate" not in body and "SubagentStop:  (" not in body:
            keep.append(body)
        elif "recovered" in body and "sessions" in body:
            keep.append(body)
    return keep


def wait_for(label: str, since: str, *needles: str, timeout: float = TURN_TIMEOUT) -> list[str]:
    """Poll the journal until every needle has appeared; fail with the
    lines seen so far otherwise. Never a bare sleep."""
    deadline = time.monotonic() + timeout
    while True:
        lines = journal(label, since)
        if all(any(n in ln for ln in lines) for n in needles):
            return lines
        if time.monotonic() > deadline:
            pytest.fail(f"journal never showed {needles!r} within {timeout:.0f}s:\n"
                        + "\n".join(lines[-20:]))
        time.sleep(2)


def send(run_async, name: str, text: str) -> None:
    assert run_async(inject.send_text_and_enter(name, text)), "inject failed"


def _init_data() -> str:
    data = {"user": json.dumps({"id": CHAT_ID, "first_name": "e2e"}),
            "auth_date": str(int(time.time())), "query_id": "e2e"}
    check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    data["hash"] = hmac.new(_secret_key(config.BOT_TOKEN), check.encode(),
                            hashlib.sha256).hexdigest()
    encoded = urlencode(data)
    verify_init_data(encoded, config.BOT_TOKEN)  # self-check before use
    return encoded


def api(method: str, path: str, body=None, *, init_data: str | None = None):
    """(status, json) from the Mini App with a signed initData header
    (or a caller-supplied one, to probe the auth gate)."""
    req = urllib.request.Request(
        API + path, method=method,
        headers={"X-Telegram-Init-Data": _init_data() if init_data is None else init_data,
                 "Content-Type": "application/json"},
        data=json.dumps(body).encode() if body is not None else None,
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:200]


def nudge(name: str, seq: int) -> None:
    """Replay Claude Code's "waiting for your input" notification for
    ``name`` to the daemon socket (2.1.261 does not emit one while a
    prompt is open, so the daemon's handling has to be driven)."""
    payload = {"session": f"claude-{name}", "hook_event_name": "Notification",
               "notification_type": "idle_prompt",
               "message": "Claude is waiting for your input",
               "transcript_path": "", "seq": seq}
    s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        s.sendto(json.dumps(payload).encode(), SOCKET_PATH)
    finally:
        s.close()


def cost(label: str) -> float:
    out = subprocess.run(["aipager", "session", "ls", "--json"],
                         capture_output=True, text=True, timeout=30).stdout
    for s in json.loads(out).get("sessions", []):
        if s.get("label") == label:
            return float(s.get("cost_usd") or 0.0)
    return -1.0


@dataclass
class LiveSession:
    name: str      # dtach name without the claude- prefix
    label: str     # what the daemon logs as [label]
    since: str     # journal anchor
    cwd: Path


@pytest.fixture
def live_session(monkeypatch, run_async) -> LiveSession:
    dtach = _real_dtach()
    if not dtach:
        pytest.skip("no dtach binary available to launch a live session")
    monkeypatch.setattr(inject, "_resolve_dtach", lambda: dtach)
    monkeypatch.setattr(inject, "_DTACH", dtach, raising=False)
    _child_env_clean(monkeypatch)
    label = f"e2elive-{uuid.uuid4().hex[:6]}"
    name = f"{label}__d{CHAT_ID}"
    since = now_stamp()
    ok, err = run_async(inject.launch_session(name, True, cwd=str(REPO_ROOT)))
    if not ok:
        pytest.skip(f"could not launch the throwaway session: {err}")
    wait_for(label, since, "→ IDLE", timeout=90)
    time.sleep(3)  # let the TUI settle before the first inject
    sess = LiveSession(name=name, label=label, since=now_stamp(), cwd=REPO_ROOT)
    try:
        yield sess
    finally:
        try:
            run_async(inject.kill_session(name))
        except Exception:
            pass


# ---- tests ------------------------------------------------------------------

def test_prompt_round_trip(claude_available, live_session, run_async):
    """A Telegram-style inject becomes a turn with a card and an answer:
    `IDLE → BUSY`, `Busy message sent`, `BUSY → IDLE`, `sendRichMessage`."""
    s = live_session
    send(run_async, s.name, "Reply with exactly: OK")
    lines = wait_for(s.label, s.since, "IDLE → BUSY", "Busy message sent",
                     "BUSY → IDLE", "sendRichMessage")
    assert not any("stale_busy" in ln or "still working" in ln for ln in lines)


def test_subagent_hooks_and_cost(claude_available, live_session, run_async):
    """Roadmap 4.5/4.6: a background agent is attributed by its own hooks
    (`SubagentStart:` … `hook SubagentStop (agent_id=<id>`) and the
    session's cost in `aipager session ls --json` rises with the turn."""
    s = live_session
    before = cost(s.label)
    send(run_async, s.name,
         "Use the Agent tool exactly once with subagent_type general-purpose and the prompt "
         "'reply with the single word OK'. When it returns, answer with the single word done.")
    lines = wait_for(s.label, s.since, "SubagentStart:", "BUSY → IDLE", "sendRichMessage")
    assert any("hook SubagentStop (agent_id=" in ln and "agent_id=-" not in ln for ln in lines)
    # The cost lands with the next statusline datagram, not the Stop —
    # poll for it rather than trusting a fixed pause.
    deadline = time.monotonic() + 30
    while cost(s.label) <= before and time.monotonic() < deadline:
        time.sleep(2)
    assert cost(s.label) > before


def test_miniapp_diff_detail_and_auth(claude_available, live_session, run_async):
    """Roadmap 4.4: the Mini App's diff route shows a file the session
    created; the detail route answers; a forged initData is refused."""
    s = live_session
    probe = s.cwd / "e2e_diff_probe.txt"
    try:
        send(run_async, s.name,
             f"Using the Write tool, create the file {probe} containing exactly one line: "
             "hello diff. Then answer with the single word done.")
        wait_for(s.label, s.since, "BUSY → IDLE", "sendRichMessage")
        assert probe.exists()

        status, body = api("GET", f"/api/sessions/{s.label}/diff")
        assert status == 200, body
        files = body.get("files") or []
        assert any(f.get("path") == "e2e_diff_probe.txt" and f.get("patch") for f in files), body

        status, detail = api("GET", f"/api/sessions/{s.label}")
        assert status == 200 and detail.get("label") == s.label

        status, _ = api("GET", f"/api/sessions/{s.label}", init_data="garbage")
        assert status == 401
    finally:
        probe.unlink(missing_ok=True)


def test_replayed_idle_nudge_reminds_not_idles(claude_available, live_session, run_async):
    """Roadmap 8.4: a session blocked on an AskUserQuestion stays
    INTERACTIVE when Claude Code's idle nudge arrives — one
    `reminding, not idling` line for two nudges, never `INTERACTIVE → IDLE`."""
    s = live_session
    send(run_async, s.name,
         "Use the AskUserQuestion tool to ask me one question: do I prefer tea or coffee, "
         "with those two options. Wait for my answer.")
    wait_for(s.label, s.since, "BUSY → INTERACTIVE", timeout=90)
    time.sleep(3)
    nudge(s.name, 1)
    wait_for(s.label, s.since, "reminding, not idling", timeout=30)
    nudge(s.name, 2)
    time.sleep(6)
    lines = journal(s.label, s.since)
    assert sum("reminding, not idling" in ln for ln in lines) == 1
    assert not any("INTERACTIVE → IDLE" in ln for ln in lines)


@pytest.mark.skipif(os.environ.get("AIPAGER_E2E_RESTART") != "1",
                    reason="set AIPAGER_E2E_RESTART=1: this restarts the operator's daemon")
def test_restart_mid_answer_recovers_card_and_delivers(claude_available, live_session, run_async):
    """Roadmap 2.1/2.2: a daemon restart while a turn is mid-answer
    rewrites the orphan card (`recovered N sessions: N edited`) and the
    answer still goes out once the Stop lands on the restored session."""
    s = live_session
    send(run_async, s.name, "Write a 1200-word essay about mountains. Plain paragraphs, "
                            "no headings, no lists, no tools. Do not stop early.")
    wait_for(s.label, s.since, "IDLE → BUSY", "Busy message sent", timeout=60)
    time.sleep(12)
    if any("BUSY → IDLE" in ln for ln in journal(s.label, s.since)):
        pytest.skip("the essay finished before the restart — nothing mid-answer to recover")
    subprocess.run(["systemctl", "--user", "restart", "aipager"], check=True, timeout=60)
    lines = wait_for(s.label, s.since, f"Restored session: claude-{s.name}", "recovered",
                     "sendRichMessage")
    assert any("recovered" in ln and "edited" in ln for ln in lines)


def test_kill_route_ends_session(claude_available, live_session, run_async):
    """Roadmap 3.2 (kill core): the Mini App's kill route ends the
    session and its socket is gone."""
    s = live_session
    status, body = api("POST", f"/api/sessions/{s.label}/kill")
    assert status == 200 and body.get("status") == "killed", body
    time.sleep(3)
    assert run_async(inject.is_alive(s.name)) is False
