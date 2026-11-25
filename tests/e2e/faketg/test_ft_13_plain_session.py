"""Scenario 13 (1.6.0 fix): a Claude Code session aipager did NOT start is
left alone by aipager's hooks, except for a turn carrying aipager's
Telegram mark.

The instance's HOME has aipager's hooks installed (the daemon put them in
its ``settings.json``), as on a machine where aipager runs. A plain
``claude`` there has no aipager session name:

A. (real Claude or the stand-in; the hook run directly with Claude Code's
   payloads) a command aipager refuses for a Telegram turn
   (``aipager config``, a search of ``~``) gets no answer from the hook,
   so Claude Code decides; the same call in a turn carrying the
   ``[via Telegram`` mark is refused; and a ``PermissionRequest``
   returns at once instead of waiting for a
   Telegram tap that cannot come.
B. (real Claude only) a plain ``claude`` in a terminal: its permission
   dialog is not held by a waiting hook, a typed turn with the mark is
   refused, and a ``/loop`` keeps running its Bash command on schedule
   (it used to be refused on its first scheduled fire and halted on every
   later one). Nothing reaches Telegram.
"""

from __future__ import annotations

import fcntl
import json
import os
import pty
import signal
import struct
import subprocess
import termios
import threading
import time
from pathlib import Path

import pytest

from tests.e2e.fake_telegram import instance as fti
from tests.e2e.fake_telegram.redaction import redact

MARK = "[via Telegram from @alice]"
DENY_PREFIX = "aipager safety policy:"


def _transcript(path: Path, prompt: str) -> str:
    path.write_text(json.dumps({
        "type": "user", "uuid": "u-1", "sessionId": "s-plain",
        "message": {"role": "user", "content": prompt}}) + "\n")
    return str(path)


def _hook(inst, payload: dict, session: str = "") -> tuple[str, float, int]:
    env = {k: v for k, v in inst.env.items() if k != "CLAUDE_DTACH_SESSION"}
    if session:
        env["CLAUDE_DTACH_SESSION"] = session
    t0 = time.monotonic()
    p = subprocess.run([str(inst.bin_dir / "aipager-hook")], input=json.dumps(payload),
                       env=env, cwd=str(inst.project), capture_output=True, text=True,
                       timeout=180)
    return p.stdout.strip(), time.monotonic() - t0, p.returncode


def _pre(transcript: str, cwd: Path, command: str) -> dict:
    return {"hook_event_name": "PreToolUse", "session_id": "s-plain",
            "transcript_path": transcript, "cwd": str(cwd), "permission_mode": "default",
            "tool_name": "Bash", "tool_input": {"command": command},
            "tool_use_id": "toolu_plain_1"}


def _deny_reason(out: str) -> str | None:
    if not out:
        return None
    hso = json.loads(out).get("hookSpecificOutput") or {}
    if hso.get("permissionDecision") != "deny":
        return None
    return hso.get("permissionDecisionReason", "")


def test_hook_leaves_a_plain_session_alone_unless_marked(fresh):
    inst, fake = fresh, fresh.fake
    since = fake.mark()
    folder = inst.home / "plain"
    folder.mkdir(exist_ok=True)
    plain = _transcript(folder / "plain.jsonl", "run the checks")
    marked = _transcript(folder / "marked.jsonl", f"{MARK} run the checks")

    for command in ("aipager config --help", "grep -rn needle ~",
                    'bash -c "grep -rn needle \\$HOME"'):
        out, _t, rc = _hook(inst, _pre(plain, inst.project, command))
        assert rc == 0 and out == "", redact(f"{command!r} was answered: {out!r} rc={rc}")

    out, _t, _rc = _hook(inst, _pre(marked, inst.project, "aipager config --help"))
    reason = _deny_reason(out)
    assert reason is not None and reason.startswith(DENY_PREFIX), redact(out)
    out, _t, _rc = _hook(inst, _pre(marked, inst.project, "grep -rn needle ~"))
    assert _deny_reason(out) is not None, redact(out)

    # A permission prompt of the plain session: no wait for a tap. The
    # instance's reply deadline is 120 s, so a hook that waited for the
    # daemon would sit here for two minutes.
    assert inst.env.get("AIPAGER_PERMISSION_REPLY_DEADLINE_SECONDS") == "120"
    out, took, rc = _hook(inst, {
        "hook_event_name": "PermissionRequest", "session_id": "s-plain",
        "transcript_path": plain, "cwd": str(inst.project), "permission_mode": "default",
        "tool_name": "Write",
        "tool_input": {"file_path": str(inst.project / "x.txt"), "content": "hi"},
        "permission_suggestions": []})
    assert rc == 0 and out == "", redact(out)
    assert took < 10, f"the PermissionRequest hook waited {took:.1f}s"

    # Nothing about any of it reached Telegram.
    time.sleep(2)
    sent = [c for c in fake.calls(since=since)
            if c.method != "getUpdates" and c.chat_id is not None]
    assert sent == [], redact("\n".join(c.summary() for c in sent))


# ---------------------------------------------------------------------------
# B. A plain claude in a terminal (real Claude only).
# ---------------------------------------------------------------------------

class _Term:
    """A plain ``claude`` on a pty of the test's own, 50x200, its output
    drained by a thread. Killed by PID at close."""

    def __init__(self, argv: list[str], env: dict, cwd: Path):
        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 200, 0, 0))
        self.proc = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave,
                                     cwd=str(cwd), env=env, start_new_session=True)
        os.close(slave)
        self.buf = bytearray()
        self.lock = threading.Lock()
        self.alive = True
        threading.Thread(target=self._drain, daemon=True).start()

    def _drain(self):
        while self.alive:
            try:
                chunk = os.read(self.master, 65536)
            except OSError:
                return
            if not chunk:
                return
            with self.lock:
                self.buf.extend(chunk)

    def mark(self) -> int:
        with self.lock:
            return len(self.buf)

    def text(self, since: int = 0) -> str:
        with self.lock:
            return fti.pane_text(bytes(self.buf[since:]))

    def type(self, text: str) -> None:
        for i in range(0, len(text), 100):
            os.write(self.master, text[i:i + 100].encode())
            time.sleep(0.15)
        time.sleep(0.8)
        os.write(self.master, b"\r")

    def key(self, data: bytes) -> None:
        os.write(self.master, data)

    def close(self, inst_dir: Path):
        self.alive = False
        if self.proc.poll() is None:
            fti.kill_pid_if_ours(self.proc.pid, inst_dir, signal.SIGTERM)
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                fti.kill_pid_if_ours(self.proc.pid, inst_dir, signal.SIGKILL)
                self.proc.wait(10)
        try:
            os.close(self.master)
        except OSError:
            pass


def _hook_pids(inst_dir: Path) -> set[int]:
    needle = b"AIPAGER_INSTANCE_DIR=" + os.fsencode(str(inst_dir))
    out = set()
    for pid in fti._own_pids():
        if b"notify_hook" not in fti._read(f"/proc/{pid}/cmdline"):
            continue
        if needle in fti._read(f"/proc/{pid}/environ").split(b"\0"):
            out.add(pid)
    return out


class _HookWatch:
    """Samples the instance's hook processes every 50 ms: how long each
    one lived."""

    def __init__(self, inst_dir: Path):
        self.inst_dir = inst_dir
        self.first: dict[int, float] = {}
        self.last: dict[int, float] = {}
        self.on = True
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while self.on:
            now = time.monotonic()
            for pid in _hook_pids(self.inst_dir):
                self.first.setdefault(pid, now)
                self.last[pid] = now
            time.sleep(0.05)

    def longest(self) -> float:
        return max((self.last[p] - self.first[p] for p in self.first), default=0.0)


def _transcripts(home: Path) -> list[Path]:
    return sorted((home / ".claude" / "projects").glob("*/*.jsonl"),
                  key=lambda p: p.stat().st_mtime)


def _entries(path: Path) -> list[dict]:
    out = []
    for line in path.read_text(errors="replace").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if isinstance(e, dict):
            out.append(e)
    return out


def _tool_results(path: Path) -> list[str]:
    out = []
    for e in _entries(path):
        content = (e.get("message") or {}).get("content")
        if isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    c = b.get("content")
                    out.append(c if isinstance(c, str) else json.dumps(c))
    return out


def test_plain_claude_dialog_marked_turn_and_loop(fresh):
    inst, fake = fresh, fresh.fake
    if inst.claude_mode != "real":
        pytest.skip("real Claude only: the stand-in has no /loop and no crons")
    token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")
    assert token
    since = fake.mark()
    log_since = inst.log_mark()
    env = {k: v for k, v in inst.env.items()
           if k not in ("CLAUDE_DTACH_SESSION", "PYTEST_CURRENT_TEST")}
    env.update({"CLAUDE_CODE_OAUTH_TOKEN": token, "TERM": "xterm-256color"})
    known = set(_transcripts(inst.home))
    watch = _HookWatch(inst.inst_dir)
    # Ask mode: Claude Code 2.1.326 starts a plain claude in Auto mode,
    # which would answer the Write dialog below by itself.
    term = _Term([inst.claude_bin, "--permission-mode", "default", "--allowedTools", "Bash"],
                 env, inst.project)
    try:
        fti.wait_until(lambda: "for shortcuts" in term.text() or "\u276f" in term.text(),
                       90, "the plain claude's prompt", lambda: term.text()[-1500:])
        time.sleep(3)

        # 1. Its permission dialog: the hook does not hold it.
        mark = term.mark()
        term.type("Use the Write tool to create plainw.txt containing hi. Do nothing else.")
        fti.wait_until(lambda: "Doyouwantto" in term.text(mark).replace(" ", ""), 120,
                       "the plain claude's Write dialog", lambda: term.text(mark)[-1500:])
        time.sleep(8)
        assert watch.longest() < 5, f"a hook waited {watch.longest():.1f}s at the dialog"
        assert watch.first, "no hook process was ever seen (the watch is blind)"
        term.key(b"\x1b")
        time.sleep(3)
        assert not (inst.project / "plainw.txt").exists()
        transcript = fti.wait_until(
            lambda: next(iter(set(_transcripts(inst.home)) - known), None), 60,
            "the plain claude's transcript")

        # 2. A typed turn carrying aipager's mark is checked: refused.
        n = len(_tool_results(transcript))
        term.type(f"{MARK} Run this Bash command exactly: `date +%s > plainmarked.txt` "
                  "(one Bash call, nothing else; if it is refused, stop).")
        res = fti.wait_until(
            lambda: [r for r in _tool_results(transcript)[n:] if DENY_PREFIX in r], 120,
            "the marked turn's Bash call to be refused",
            lambda: redact("\n".join(_tool_results(transcript)[n:])[-1500:]
                           + "\n" + term.text()[-1500:]))
        assert res
        time.sleep(5)
        assert not (inst.project / "plainmarked.txt").exists()

        # 3. A /loop: its scheduled fires run the command, never refused.
        # In a fresh conversation: real Claude that has just seen Bash
        # refused may decline to schedule it at all (seen 2026-11-25).
        seen = set(_transcripts(inst.home))
        mark = term.mark()
        term.type("/clear")
        fti.wait_until(lambda: "\u276f" in term.text(mark), 60,
                       "the plain claude's prompt after /clear",
                       lambda: term.text(mark)[-1500:])
        time.sleep(3)
        loop_file = inst.project / "plainloop.txt"
        term.type("/loop 1m Run this Bash command exactly: `date +%s >> plainloop.txt` "
                  "(one Bash call, nothing else).")
        transcript = fti.wait_until(
            lambda: next(iter(set(_transcripts(inst.home)) - seen - {transcript}), None), 60,
            "the fresh conversation's transcript")
        n = 0

        def _fires():
            try:
                return len(loop_file.read_text().split()) >= 2
            except OSError:
                return False
        fti.wait_until(_fires, 240, "two runs of the loop's command",
                       lambda: redact("\n".join(_tool_results(transcript)[n:])[-2000:]
                                      + "\n" + term.text()[-1500:]))
        refused = [r for r in _tool_results(transcript)[n:] if DENY_PREFIX in r]
        assert refused == [], redact("\n".join(refused))
        assert watch.longest() < 10, f"a hook lived {watch.longest():.1f}s"
    finally:
        watch.on = False
        term.close(inst.inst_dir)

    # Nothing reached Telegram, and the daemon halted nothing.
    sent = [c for c in fake.calls(since=since)
            if c.method != "getUpdates" and c.chat_id is not None]
    assert sent == [], redact("\n".join(c.summary() for c in sent))
    halted = [line for line in inst.log_lines(log_since) if "halt" in line.lower()]
    assert halted == [], redact("\n".join(halted))
