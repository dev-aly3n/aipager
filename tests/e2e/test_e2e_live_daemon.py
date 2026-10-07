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
its own bookkeeping — and the bot token only ever signs initData. The
launch's "move an expired ``~/.claude/.credentials.json`` aside" step is
switched off in this process (the daemon does that for its own launches).
The real-home write guard (roadmap 8.97) stays on: the session inherits
``PYTEST_CURRENT_TEST``, and its hooks write only under ``/tmp``.

What each test proves, and what it leaves in the operator's DM:

- ``test_prompt_round_trip``: an injected prompt becomes a turn with a
  card and an answer. One card + one answer ("OK").
- ``test_subagent_hooks_and_cost``: a background agent is attributed by
  its own hooks and the session's cost rises. One card + one answer.
- ``test_miniapp_diff_detail_and_auth``: the diff route shows a file the
  session wrote; a forged initData gets 401. One card + one answer.
- ``test_replayed_idle_nudge_reminds_not_idles``: an AskUserQuestion stays
  INTERACTIVE through Claude Code's idle nudge. One card + a question
  card, left unanswered (the session is killed).
- ``test_long_answer_attaches_full_log``: an answer past the card limit is
  cut and goes out with a ``<label>_full_log.md`` attachment. One card,
  a long answer and the attachment.
- ``test_miniapp_chats_and_scope_header``: ``GET /api/chats`` lists the
  operator's chat as the current one; a request naming a chat they are
  not in is 403;
  the sessions list works with no chat named. Posts nothing.
- ``test_restart_mid_answer_recovers_card_and_delivers`` (also needs
  ``AIPAGER_E2E_RESTART=1``): restarts the daemon mid-answer; the new
  daemon adopts the card and delivers the answer. One card + one essay.
  The restart rewrites ``~/.local/share/aipager/daemon.lock``, the one
  real-home change the test registers with the session's real-home
  guard (``expect_real_home_change`` in tests/conftest.py).
- ``test_kill_route_ends_session``: the Mini App kill route ends the
  session. Posts the "Ended" line.

Every test also leaves a gone row for its throwaway session.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import socket
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime
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
from aipager.policy_snapshot import TURN_OPEN_FILE  # noqa: E402
from aipager.state import _default_scope  # noqa: E402

_SCOPE = _default_scope()
if _SCOPE is None:
    pytest.skip("no chat scope configured — nothing to address the session to",
                allow_module_level=True)
CHAT_ID = int(_SCOPE[0])
API = f"http://127.0.0.1:{config.MINIAPP_PORT}"
REPO_ROOT = harness.REPO_ROOT  # trusted by Claude Code; see the docstring
TURN_TIMEOUT = 240.0


# ---- oracle + helpers -----------------------------------------------------

def now_stamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def journal(label: str, since: str, until: str | None = None) -> list[str]:
    """Daemon journal lines about ``label`` since ``since`` (wall clock),
    and before ``until`` when given, minus the noise every turn produces."""
    out = subprocess.run(
        ["journalctl", "--user", "-u", "aipager", "--since", since,
         *(["--until", until] if until else []), "--no-pager",
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


def journal_all(since: str) -> list[str]:
    """Every daemon journal line since ``since`` (for warnings logged
    without a session label)."""
    out = subprocess.run(
        ["journalctl", "--user", "-u", "aipager", "--since", since, "--no-pager",
         "-o", "short-precise"], capture_output=True, text=True, timeout=30,
    ).stdout
    return [line.split("]: ", 1)[-1] for line in out.splitlines()]


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


def api(method: str, path: str, body=None, *, init_data: str | None = None,
        headers: dict | None = None):
    """(status, json) from the Mini App with a signed initData header
    (or a caller-supplied one, to probe the auth gate), plus ``headers``."""
    req = urllib.request.Request(
        API + path, method=method,
        headers={"X-Telegram-Init-Data": _init_data() if init_data is None else init_data,
                 "Content-Type": "application/json", **(headers or {})},
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
    dtach = harness.real_dtach()
    if not dtach:
        pytest.skip("no dtach binary available to launch a live session")
    monkeypatch.setattr(inject, "_resolve_dtach", lambda: dtach)
    monkeypatch.setattr(inject, "_DTACH", dtach, raising=False)
    # No moving the operator's ~/.claude/.credentials.json from a test.
    monkeypatch.setattr(inject, "_stash_expired_credentials_file", lambda: None)
    harness.child_env_clean(monkeypatch)
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


_RICH_LINE = re.compile(r"sendRichMessage: (\d+) chars, rtl=\w+, overflow=(True|False)")
#: Over the 32,768-byte rich-message ceiling with room to spare.
_LONG_LINES = 800
_RICH_LIMIT = 32_768
_SENT = "full-log attachment sent:"


def _daemon_logs_attachment() -> bool:
    """Whether the running daemon's aipager has the "full-log attachment
    sent" log line (added with this suite). Asks the daemon process's own
    interpreter (its ``argv[0]``: ``/proc/<pid>/exe`` resolves past the
    venv to the system binary) in isolated mode from ``/``, so this
    checkout (the cwd, ``PYTHONPATH``) cannot answer for it, and locates
    the package without importing it (an import reads the operator's
    config). Reads the installed source, which a reinstall without a
    restart can put ahead of what the process loaded; that only turns a
    skip into a fail. Skips when the install cannot be inspected at all."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    problems = []
    for pid in harness.daemon_pids():
        try:
            python = (Path("/proc") / pid / "cmdline").read_bytes().split(b"\0")[0].decode()
            if not os.path.isabs(python):
                # A bare `python -m aipager start`: PATH's python may not be its.
                problems.append(f"{pid}: interpreter {python!r} is not an absolute path")
                continue
            p = subprocess.run(
                [python, "-I", "-c", "import importlib.util, pathlib; "
                 "o = importlib.util.find_spec('aipager').origin; "
                 "print(" + repr(_SENT) + " in (pathlib.Path(o).parent / 'bot' / "
                 "'notify.py').read_text())"],
                cwd="/", env=env, capture_output=True, text=True, timeout=60)
        except (OSError, UnicodeDecodeError, subprocess.TimeoutExpired) as e:
            problems.append(f"{pid}: {type(e).__name__}")
            continue
        if p.returncode == 0 and p.stdout.strip() in ("True", "False"):
            return p.stdout.strip() == "True"
        problems.append(f"{pid}: rc={p.returncode} {p.stderr.strip()[-160:]!r}")
    pytest.skip("could not inspect the running daemon's install for the "
                f"'{_SENT}' line: {problems or 'no daemon process found'}")


def test_long_answer_attaches_full_log(claude_available, live_session, run_async):
    """An answer past the card limit is cut (``overflow=True`` on its
    ``sendRichMessage`` line) and the whole turn goes out as
    ``<label>_full_log.md`` (md-attachments): the daemon logs
    ``[<label>] full-log attachment sent: <label>_full_log.md`` only after
    Telegram took the document. A daemon installed before that line
    existed cannot show it: the test then skips after the overflow check."""
    s = live_session
    filename = f"{s.label}_full_log.md"
    send(run_async, s.name,
         f"Without using any tools, write a numbered list from 1 to {_LONG_LINES}, one "
         "item per line, each line exactly: <n>. mountains rivers valleys forests "
         "meadows glaciers. Write every line, no headings, no commentary, do not "
         "stop early or abbreviate.")
    wait_for(s.label, s.since, "BUSY → IDLE", timeout=600)
    deadline = time.monotonic() + 90
    while True:
        lines = journal(s.label, s.since)
        rich = next((m for m in map(_RICH_LINE.search, lines) if m), None)
        sent = any(f"[{s.label}] {_SENT} {filename}" in ln for ln in lines)
        if (rich and sent) or time.monotonic() > deadline:
            break
        time.sleep(3)
    if rich is None:
        pytest.skip("no sendRichMessage line: the answer went out with the card "
                    "(the merged layout delivers an answer that fits), nothing overflowed")
    chars, overflow = int(rich.group(1)), rich.group(2) == "True"
    if not overflow:
        assert chars < _RICH_LIMIT, f"a {chars}-char answer was sent whole (overflow=False)"
        pytest.skip(f"Claude wrote only {chars} chars: the answer fit, nothing to attach")
    if sent:
        return
    bad = (f"[{s.label}] Response too large", f"[{s.label}] full-log attachment skipped",
           "Failed to send full response file")
    hit = [ln for ln in journal_all(s.since) if any(b in ln for b in bad)]
    assert not hit, "the full-log attachment did not go out:\n" + "\n".join(hit)
    if not _daemon_logs_attachment():
        pytest.skip("the running daemon predates the 'full-log attachment sent' line; "
                    "the answer overflowed and no failure was logged, but the document "
                    "itself is not observable: deploy this branch to check it")
    pytest.fail(f"no '{_SENT} {filename}' line within 90 s of the overflowing answer")


def test_miniapp_chats_and_scope_header():
    """Mini App chat switcher (roadmap 8.73): ``GET /api/chats`` lists the
    operator's chat, once, as the current one (a personal install has only
    that one; any group they are in is listed too); a request naming a
    chat they are not in (``X-Aipager-Scope``) is refused with 403, while
    naming their own chat, or none, lists the sessions."""
    status, body = api("GET", "/api/chats")
    assert status == 200, body
    scopes = [c.get("scope") for c in body.get("chats", [])]
    assert str(CHAT_ID) in scopes and len(scopes) == len(set(scopes)), body
    assert body.get("current") == str(CHAT_ID), body

    foreign = "-1009999999999"  # a group the operator is certainly not in
    status, body = api("GET", "/api/sessions", headers={"X-Aipager-Scope": foreign})
    assert status == 403, body
    status, body = api("GET", "/api/chats", headers={"X-Aipager-Scope": foreign})
    assert status == 403, body

    status, body = api("GET", "/api/sessions", headers={"X-Aipager-Scope": str(CHAT_ID)})
    assert status == 200 and isinstance(body.get("sessions"), list), body
    status, body = api("GET", "/api/sessions")
    assert status == 200 and isinstance(body.get("sessions"), list), body


#: The busy card's message id, on any of its "sent" lines (first send,
#: late send, re-send after a lost card).
_BUSY_SENT = re.compile(r"Busy message sent(?: late| again)? \(msg_id=(\d+)")
#: The daemon's own lock, rewritten by every daemon start
#: (``cli/daemon.py::_acquire_daemon_lock`` computes it inline).
_DAEMON_LOCK = Path.home() / ".local" / "share" / "aipager" / "daemon.lock"


def precise_stamp() -> str:
    """A journal anchor to the microsecond (``journalctl --since`` takes
    fractional seconds): nothing logged before this instant matches."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")


@pytest.mark.skipif(os.environ.get("AIPAGER_E2E_RESTART") != "1",
                    reason="set AIPAGER_E2E_RESTART=1: this restarts the operator's daemon")
def test_restart_mid_answer_recovers_card_and_delivers(claude_available, live_session, run_async,
                                                         expect_real_home_change):
    """Roadmap 2.1/2.2 and 8.55: a daemon restart while a turn is
    mid-answer ADOPTS the turn's busy card, it is not closed or edited.
    The new daemon logs ``[<label>] busy card <id> adopted — the turn
    looks still running`` for the very card the old one sent (the id on
    its ``Busy message sent (msg_id=<id>`` line) and counts it in
    ``recovered N sessions: … adopted``; the answer then goes out
    (``[<label>] sendRichMessage``) once the Stop lands on the restored
    session. The answer's line is read only from journal entries written
    after ``systemctl restart`` returned, so the old daemon cannot
    satisfy it, and no fresh card goes out for the turn after it. Skips
    when the essay finished before the restart, or had ended by the time
    recovery ran (the hook's turn-open file gone, the transcript complete
    and last written before the recovery's journal timestamp: the card
    is then rightly closed, not adopted)."""
    s = live_session
    nonce = uuid.uuid4().hex[:8]
    send(run_async, s.name, "Write a 1200-word essay about mountains. Plain paragraphs, "
                            f"no headings, no lists, no tools. Do not stop early. ({nonce})")
    wait_for(s.label, s.since, "IDLE → BUSY", "Busy message sent", timeout=60)
    time.sleep(12)
    lines = journal(s.label, s.since)
    if any("BUSY → IDLE" in ln for ln in lines):
        pytest.skip("the essay finished before the restart — nothing mid-answer to recover")
    cards = [int(m.group(1)) for ln in lines
             if f"[{s.label}]" in ln and (m := _BUSY_SENT.search(ln))]
    assert cards, "no 'Busy message sent (msg_id=' line for this session:\n" + "\n".join(lines)
    card = cards[-1]
    # The new daemon rewrites its lock file; the session-wide real-home
    # guard lets exactly that path through (tests/conftest.py).
    expect_real_home_change(_DAEMON_LOCK)
    before_restart = precise_stamp()
    subprocess.run(["systemctl", "--user", "restart", "aipager"], check=True, timeout=60)
    restarted = precise_stamp()
    if any("BUSY → IDLE" in ln for ln in journal(s.label, s.since, until=restarted)):
        pytest.skip("the essay finished just before the restart — nothing mid-answer to recover")

    # The new daemon's verdict on this card, read only from lines logged
    # since the restart began (the old daemon logs neither line).
    adopted = f"[{s.label}] busy card {card} adopted — the turn looks still running"
    restored = f"Restored session: claude-{s.name}"
    turn_open = harness.PRODUCTION_NOTES_DIR(f"claude-{s.name}") / TURN_OPEN_FILE
    deadline = time.monotonic() + 60
    while True:
        lines = journal(s.label, before_restart)
        if any(restored in ln for ln in lines) and any(adopted in ln for ln in lines):
            break
        if time.monotonic() > deadline:
            recovered_at = _recovery_logged_at(before_restart)
            # Skip only when every sign agrees: the hook's turn-open file is
            # gone (its Stop fired, at some point), and the transcript is
            # complete and was last written before recovery ran (the bound
            # that places the turn's end before recovery).
            if (recovered_at is not None and not turn_open.exists()
                    and _turn_ended_by(nonce, recovered_at)):
                pytest.skip("the essay's turn had ended by the time recovery ran: "
                            "the card was rightly closed, not adopted")
            pytest.fail(f"journal never showed {restored!r} and {adopted!r} within 60s:\n"
                        + "\n".join(lines[-20:]))
        time.sleep(2)
    # The adopted line is the contract: in one recovery pass a card is
    # either adopted or recovered (closed), never both. This only rules
    # out a recovery attempt that failed loudly.
    assert not any(f"[{s.label}] orphan msg" in ln for ln in lines), lines
    lines = wait_for(s.label, restarted, f"[{s.label}] sendRichMessage")
    # And the turn finished on the adopted card: no fresh card for it.
    assert not any(f"[{s.label}]" in ln and _BUSY_SENT.search(ln) for ln in lines), lines


def _recovery_logged_at(since: str) -> float | None:
    """The epoch time the daemon logged its startup ``recovered N
    sessions:`` summary since ``since``, from the journal's own timestamp
    (``-o short-unix``), or None when it did not."""
    out = subprocess.run(
        ["journalctl", "--user", "-u", "aipager", "--since", since, "--no-pager",
         "-o", "short-unix"], capture_output=True, text=True, timeout=30,
    ).stdout
    for line in out.splitlines():
        body = line.split("]: ", 1)[-1]
        if "recovered" in body and "sessions:" in body:
            try:
                return float(line.split(None, 1)[0])
            except ValueError:
                return None
    return None


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _turn_ended_by(nonce: str, when: float) -> bool:
    """Whether the throwaway session's transcript (the one holding
    *nonce*, under Claude Code's project folder for the repo root) shows
    its turn complete and was last written at or before *when* (epoch
    seconds): the file's own mtime, so a late write only ever turns a
    skip into a failure. Read-only."""
    from aipager import transcript
    from aipager.claude_resolve import _probe_project_dir
    for tp in sorted(_probe_project_dir(str(REPO_ROOT)).glob("*.jsonl"),
                     key=_mtime, reverse=True)[:20]:
        try:
            if nonce not in tp.read_text(errors="replace"):
                continue
        except OSError:
            continue
        return transcript.turn_appears_complete(str(tp)) and 0.0 < _mtime(tp) <= when
    return False


def test_kill_route_ends_session(claude_available, live_session, run_async):
    """Roadmap 3.2 (kill core): the Mini App's kill route ends the
    session and its socket is gone."""
    s = live_session
    status, body = api("POST", f"/api/sessions/{s.label}/kill")
    assert status == 200 and body.get("status") == "killed", body
    time.sleep(3)
    assert run_async(inject.is_alive(s.name)) is False
