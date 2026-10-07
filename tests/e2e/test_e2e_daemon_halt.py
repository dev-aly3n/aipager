"""E2E: a real dtach session is cleanly halted on a safety block.

Regression for the wedge: raw Escape without cancelling the spinner /
resetting state left the session "thinking" forever. ``_halt_for_safety``
must interrupt Claude, cancel the animation, and return to IDLE — verified
here against a REAL dtach + Claude session, down to Claude Code's own
transcript: the turn it was writing ends on its interrupt marker
(``[Request interrupted by user``, what roadmap 8.99 reads too) and stays
ended, and the session is still alive.

Requires the live daemon to be **stopped** (it would otherwise adopt the
throwaway session and message the operator). Skips if it's running. Once
past that skip the operator stopped the daemon to run this, so every
later problem (no dtach, a launch that fails, a session that never
starts its turn) FAILS rather than skips.

How it launches, and why:

- tests/conftest.py's autouse ``_never_spawn_real_dtach`` points
  ``inject._DTACH`` at a nonexistent binary; this test overrides it (and
  ``inject._resolve_dtach``) with :func:`harness.real_dtach`, the escape
  hatch that guard documents, exactly as ``test_e2e_live_daemon``'s
  ``live_session`` does. The guard itself is untouched. The launch's
  "move an expired ``~/.claude/.credentials.json`` aside" step is
  switched off, and the child loses this process's ``CLAUDE*`` nesting
  variables (:func:`harness.child_env_clean`).
- The session starts in the repository root, which Claude Code already
  trusts: a fresh temp directory stops on the "trust this folder?" dialog
  and the prompt goes nowhere (see ``test_e2e_live_daemon``'s docstring).
- The operator's installed ``aipager-hook`` runs in the session (user
  settings). ``AIPAGER_SOCKET_PATH`` points it at a datagram socket this
  test binds under its tmp dir, so its events reach the test, never a
  daemon: ``SessionStart`` says Claude is up, ``UserPromptSubmit`` that
  the turn started and where its transcript is.
"""

from __future__ import annotations

import json
import shutil
import socket
import time
import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from aipager import transcript
from aipager.dtach import inject
from aipager.state import Status, TrackedSession
from tests.e2e import harness


class HookEvents:
    """The ``aipager-hook`` datagrams of one session, read from a socket
    bound at *path*."""

    def __init__(self, path: Path, session: str):
        self.session = session
        self.events: list[dict] = []
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self._sock.bind(str(path))

    def poll(self, wait: float = 0.5) -> None:
        """Take every datagram queued now, waiting up to *wait* for the first."""
        self._sock.settimeout(wait)
        while True:
            try:
                raw = self._sock.recv(1 << 20)
            except (socket.timeout, BlockingIOError):
                return
            self._sock.settimeout(0)
            try:
                data = json.loads(raw)
            except ValueError:
                continue
            if isinstance(data, dict) and data.get("session") == self.session:
                self.events.append(data)

    def wait(self, event: str, timeout: float, *, where=lambda e: True) -> dict | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.poll()
            for e in self.events:
                if e.get("hook_event_name") == event and where(e):
                    return e
        return None

    def seen(self, event: str) -> bool:
        self.poll()
        return any(e.get("hook_event_name") == event for e in self.events)

    def close(self) -> None:
        self._sock.close()


def test_halt_for_safety_interrupts_real_dtach(
    claude_available, mk_bot, run_async, monkeypatch, tmp_path,
):
    if harness.daemon_running():
        pytest.skip("live daemon running — it would adopt the throwaway "
                    "session and message the operator; stop it to run this "
                    "isolated test")
    # From here on the operator stopped the daemon for this test: a
    # problem is a failure, never a skip.
    dtach = harness.real_dtach()
    if not dtach:
        pytest.fail("no dtach binary (dtach-bin, PATH or the pipx venv) to launch with")
    monkeypatch.setattr(inject, "_resolve_dtach", lambda: dtach)
    monkeypatch.setattr(inject, "_DTACH", dtach, raising=False)
    # No moving the operator's ~/.claude/.credentials.json from a test.
    monkeypatch.setattr(inject, "_stash_expired_credentials_file", lambda: None)
    harness.child_env_clean(monkeypatch)
    monkeypatch.delenv("AIPAGER_INSTANCE_DIR", raising=False)  # it would outrank the socket
    hook_sock = tmp_path / "hooks.sock"
    monkeypatch.setenv("AIPAGER_SOCKET_PATH", str(hook_sock))

    name = harness.new_session().replace("claude-", "e2ehalt-")
    session = f"claude-{name}"
    hooks = HookEvents(hook_sock, session)
    nonce = uuid.uuid4().hex[:8]
    try:
        ok, err = run_async(inject.launch_session(
            name, skip_perms=True, cwd=str(harness.REPO_ROOT)))
        if not ok:
            pytest.fail(f"could not launch the dtach/claude session: {err}")
        assert hooks.wait("SessionStart", 60), (
            "Claude never fired SessionStart: is aipager-hook in the operator's "
            "Claude Code settings?")
        time.sleep(3)  # let the TUI settle before the first inject
        assert run_async(inject.send_text_and_enter(
            name, f"Write a 1500-word essay about rivers, then the word {nonce}. "
                  "Plain paragraphs, no headings, no tools. Do not stop early."))
        submit = hooks.wait("UserPromptSubmit", 60,
                            where=lambda e: nonce in str(e.get("prompt", "")))
        assert submit, ("the prompt never became a turn (no UserPromptSubmit): a "
                        "'trust this folder?' dialog at the repository root?")
        tp = submit.get("transcript_path") or ""
        assert tp, submit
        time.sleep(5)  # Claude is writing the essay now
        assert not hooks.seen("Stop"), "the turn ended before the halt"
        assert run_async(inject.is_alive(name)) is True

        bot = mk_bot()
        sess = TrackedSession(name=session, label="halt", status=Status.BUSY)
        sess.scope_chat_id = 999
        sess.busy_msg_id = None
        anim = MagicMock()
        anim.done.return_value = False
        sess.animate_task = anim
        bot.registry._sessions[session] = sess

        halted_at = time.time()
        run_async(bot._halt_for_safety(sess, "blocked by safety policy"))

        # Interrupted (not killed) + cleanly reset.
        assert run_async(inject.is_alive(name)) is True
        assert sess.status == Status.IDLE
        assert sess.animate_task is None       # spinner cancelled
        anim.cancel.assert_called_once()        # the wedge fix
        # And Claude really stopped: its transcript's newest turn entry
        # is the interrupt marker, written after the Escapes ...
        deadline = time.monotonic() + 30
        while not transcript.interrupted_since(tp, halted_at):
            if time.monotonic() > deadline:
                pytest.fail("no '[Request interrupted by user' marker in the "
                            f"transcript after the halt: {tp}")
            time.sleep(1)
        # ... and still is a while later: the turn did not carry on.
        time.sleep(5)
        assert transcript.interrupted_since(tp, halted_at), (
            "the transcript moved on after the interrupt marker")
        assert not hooks.seen("Stop"), "the turn ran on to a Stop after the halt"
        assert run_async(inject.is_alive(name)) is True
    finally:
        hooks.close()
        try:
            run_async(inject.kill_session(name))
        finally:
            # Clean the statusline file Claude wrote for this throwaway session.
            Path(f"/tmp/claude-status-claude-{name}.json").unlink(missing_ok=True)
            Path(f"/tmp/claude-status-{name}.json").unlink(missing_ok=True)
            # And what its hook wrote: the policy snapshot, and the notes
            # folder with the turn-open file (an interrupt fires no Stop to
            # clear it, and the halt's own clear went to the redirect).
            harness.PRODUCTION_SNAPSHOT_PATH(session).unlink(missing_ok=True)
            shutil.rmtree(harness.PRODUCTION_NOTES_DIR(session), ignore_errors=True)
