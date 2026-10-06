#!/usr/bin/env python3
"""A credential-free stand-in for the ``claude`` CLI (plumbing runs only).

The fake-Telegram scenarios run against real Claude in the orchestrator's
credentialed run. Pipeline agents and CI have no credential, so this
script plays Claude's part closely enough for aipager's side to be
exercised end to end: it runs inside the real dtach session, reads what
aipager types into the terminal, writes a Claude-shaped transcript and
fires the hooks from ``$HOME/.claude/settings.json`` with Claude-shaped
payloads (so the real ``aipager-hook`` and ``aipager-statusline`` run).

- ``--version`` prints ``2.1.291 (Claude Code)``; ``auth status`` prints
  a logged-in JSON status; ``-p ...`` prints ``ok``.
- Interactive: fires ``SessionStart``, then reads the terminal in raw
  mode. Text is collected until a lone carriage return (aipager writes
  the text and the Enter separately) and becomes one prompt: a user
  record in the transcript, then ``UserPromptSubmit``.
- A prompt containing "Write tool" asks to write ``<cwd>/<name>.txt``
  (``<name>`` from "create <name>.txt", else ``standin``): ``PreToolUse``,
  then, unless launched with ``--dangerously-skip-permissions`` or the
  hook already decided, ``PermissionRequest``, whose hook output decides.
  The file is written only when allowed.
- A prompt containing "Reply with exactly: X" answers ``X``; any other
  answers ``stand-in: <decision or ok>``. Then ``Stop``.
- Escape interrupts a running turn (no ``Stop``, like Claude). Enter
  while the permission hook is still waiting answers the dialog: "Yes"
  on the first row, "No" after a Down arrow (aipager's keystroke
  fallback).
- A test can hold the next tool call with ``standin-hold-<session>`` in
  the instance folder, to choose when the permission is asked.
- Every prompt and decision goes to
  ``$AIPAGER_INSTANCE_DIR/standin-<CLAUDE_DTACH_SESSION>.jsonl``.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid

VERSION = "2.1.291"

_REPLY_RE = re.compile(r"Reply with exactly:\s*(.+)", re.I)
_NAME_RE = re.compile(r"create\s+([A-Za-z0-9_-]+)\.txt", re.I)


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _slug(path: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", path)


class StandIn:
    def __init__(self, argv: list[str]):
        self.argv = argv
        self.skip_perms = "--dangerously-skip-permissions" in argv
        self.session = os.environ.get("CLAUDE_DTACH_SESSION", "")
        self.cwd = os.getcwd()
        self.session_id = str(uuid.uuid4())
        home = os.environ.get("HOME", "")
        proj = os.path.join(home, ".claude", "projects", _slug(self.cwd))
        os.makedirs(proj, exist_ok=True)
        self.transcript = os.path.join(proj, f"{self.session_id}.jsonl")
        self.settings_path = os.path.join(home, ".claude", "settings.json")
        inst = os.environ.get("AIPAGER_INSTANCE_DIR", "").strip() or self.cwd
        self.log_path = os.path.join(inst, f"standin-{self.session or 'nosession'}.jsonl")
        self.hold_path = os.path.join(inst, f"standin-hold-{self.session or 'nosession'}")
        self.dialog_answer: str | None = None
        self.in_permission = False
        self.dialog_row = 0
        self.lock = threading.Lock()
        self.queue: list[str] = []
        self.turn: threading.Thread | None = None
        self.hook_proc: subprocess.Popen | None = None
        self.interrupted = False
        self.parent_uuid: str | None = None
        self.n = 0

    # -- records -------------------------------------------------------------

    def log(self, **entry) -> None:
        entry["ts"] = time.time()
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    def _append(self, entry: dict) -> None:
        u = str(uuid.uuid4())
        entry.setdefault("uuid", u)
        entry.setdefault("parentUuid", self.parent_uuid)
        entry.setdefault("timestamp", _now_iso())
        entry.setdefault("sessionId", self.session_id)
        entry.setdefault("cwd", self.cwd)
        entry.setdefault("version", VERSION)
        entry.setdefault("isSidechain", False)
        entry.setdefault("userType", "external")
        self.parent_uuid = entry["uuid"]
        with open(self.transcript, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    def user_text(self, text: str) -> None:
        self._append({"type": "user", "message": {"role": "user", "content": text}})

    def assistant(self, content: list) -> str:
        self.n += 1
        mid = f"msg_standin_{self.session_id[:8]}_{self.n}"
        self._append({"type": "assistant", "message": {
            "id": mid, "type": "message", "role": "assistant", "model": "claude-haiku-standin",
            "content": content, "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5}}})
        return mid

    def tool_result(self, tool_use_id: str, text: str, is_error: bool) -> None:
        self._append({"type": "user", "message": {"role": "user", "content": [{
            "type": "tool_result", "tool_use_id": tool_use_id, "content": text,
            "is_error": is_error}]}})

    # -- hooks ---------------------------------------------------------------

    def _hook_commands(self, event: str, tool: str | None = None) -> list[tuple[str, float]]:
        try:
            with open(self.settings_path, encoding="utf-8") as f:
                settings = json.load(f)
        except (OSError, ValueError):
            return []
        out = []
        for block in (settings.get("hooks") or {}).get(event) or []:
            matcher = block.get("matcher")
            if tool is not None and matcher not in (None, "", "*") and \
                    not re.fullmatch(matcher, tool):
                continue
            for h in block.get("hooks") or []:
                if h.get("type") == "command" and h.get("command"):
                    out.append((h["command"], float(h.get("timeout") or 600)))
        return out

    def fire(self, event: str, extra: dict | None = None, tool: str | None = None) -> list[dict]:
        payload = {
            "session_id": self.session_id, "transcript_path": self.transcript,
            "cwd": self.cwd, "hook_event_name": event,
            "permission_mode": "bypassPermissions" if self.skip_perms else "default",
        }
        payload.update(extra or {})
        outputs = []
        for cmd, timeout in self._hook_commands(event, tool):
            try:
                proc = subprocess.Popen(cmd, shell=True, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                        text=True)
            except OSError as e:
                self.log(event="hook_error", hook=event, error=str(e))
                continue
            with self.lock:
                self.hook_proc = proc
            try:
                out, _ = proc.communicate(json.dumps(payload), timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                out, _ = proc.communicate()
            finally:
                with self.lock:
                    self.hook_proc = None
            try:
                parsed = json.loads(out) if out and out.strip() else {}
            except ValueError:
                parsed = {}
            outputs.append(parsed if isinstance(parsed, dict) else {})
        return outputs

    def statusline(self) -> None:
        try:
            with open(self.settings_path, encoding="utf-8") as f:
                cmd = ((json.load(f).get("statusLine") or {}).get("command") or "")
        except (OSError, ValueError):
            return
        if not cmd:
            return
        payload = {"session_id": self.session_id, "transcript_path": self.transcript,
                   "cwd": self.cwd, "model": {"id": "claude-haiku", "display_name": "Haiku"},
                   "context_window": {"used_percentage": 1, "total_input_tokens": 10,
                                      "total_output_tokens": 5},
                   "cost": {"total_cost_usd": 0.0}}
        try:
            subprocess.run(cmd, shell=True, input=json.dumps(payload), text=True,
                           capture_output=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            pass

    # -- a turn --------------------------------------------------------------

    def run_turn(self, prompt: str) -> None:
        self.interrupted = False
        self.log(event="prompt", prompt=prompt)
        self.user_text(prompt)
        outs = self.fire("UserPromptSubmit", {"prompt": prompt})
        if any(o.get("decision") == "block" for o in outs):
            self.log(event="blocked_prompt")
            return
        decision = "ok"
        if "write tool" in prompt.lower():
            m = _NAME_RE.search(prompt)
            name = m.group(1) if m else "standin"
            path = os.path.join(self.cwd, f"{name}.txt")
            tool_input = {"file_path": path, "content": "hi"}
            tool_use_id = f"toolu_standin_{uuid.uuid4().hex[:12]}"
            self.assistant([{"type": "tool_use", "id": tool_use_id, "name": "Write",
                             "input": tool_input}])
            self._wait_hold()
            decision = ("interrupted" if self.interrupted
                        else self._ask_tool("Write", tool_input, tool_use_id))
            if self.interrupted:
                self.log(event="interrupted")
                self.user_text("[Request interrupted by user]")
                return
            self.log(event="decision", tool="Write", decision=decision, path=path)
            if decision == "allowed":
                with open(path, "w", encoding="utf-8") as f:
                    f.write("hi")
                self.fire("PostToolUse", {"tool_name": "Write", "tool_input": tool_input,
                                          "tool_response": {"filePath": path},
                                          "tool_use_id": tool_use_id}, tool="Write")
                self.tool_result(tool_use_id, f"File created successfully at: {path}", False)
            else:
                self.tool_result(tool_use_id, f"Permission {decision}", True)
        m = _REPLY_RE.search(prompt)
        answer = m.group(1).strip().splitlines()[0] if m else f"stand-in: {decision}"
        self.assistant([{"type": "text", "text": answer}])
        if self.interrupted:
            return
        self.fire("Stop", {"stop_hook_active": False, "last_assistant_message": answer})
        self.log(event="stop", answer=answer)
        self.statusline()

    def _wait_hold(self, limit: float = 120.0) -> None:
        """A test may hold this session's next tool call (file
        ``standin-hold-<session>`` in the instance folder) to choose when
        "Claude" asks for permission. Real Claude's timing is its own."""
        deadline = time.monotonic() + limit
        while (os.path.exists(self.hold_path) and not self.interrupted
               and time.monotonic() < deadline):
            time.sleep(0.1)

    def _ask_tool(self, tool: str, tool_input: dict, tool_use_id: str) -> str:
        pre = self.fire("PreToolUse", {"tool_name": tool, "tool_input": tool_input,
                                       "tool_use_id": tool_use_id}, tool=tool)
        for o in pre:
            hso = o.get("hookSpecificOutput") or {}
            d = hso.get("permissionDecision") or o.get("decision")
            if d in ("deny", "block"):
                return "denied"
            if d in ("allow", "approve"):
                return "allowed"
        if self.skip_perms:
            return "allowed"
        with self.lock:
            self.dialog_answer = None
            self.dialog_row = 0
            self.in_permission = True
        try:
            outs = self.fire("PermissionRequest", {
                "tool_name": tool, "tool_input": tool_input, "permission_suggestions": []},
                tool=tool)
        finally:
            with self.lock:
                self.in_permission = False
        if self.dialog_answer is not None:
            # A key reached the permission dialog (aipager's keystroke
            # fallback): Enter takes the highlighted first row, "Yes".
            return self.dialog_answer
        for o in outs:
            behavior = ((o.get("hookSpecificOutput") or {}).get("decision") or {}).get("behavior")
            if behavior == "allow":
                return "allowed"
            if behavior == "deny":
                return "denied"
        return "no decision"

    # -- terminal ------------------------------------------------------------

    def _turn_loop(self) -> None:
        while True:
            with self.lock:
                if not self.queue:
                    self.turn = None
                    return
                prompt = self.queue.pop(0)
            try:
                self.run_turn(prompt)
            except Exception as e:  # noqa: BLE001 - never die mid-session
                self.log(event="turn_error", error=repr(e))
            sys.stdout.write("\r\n❯ ")
            sys.stdout.flush()

    def submit(self, prompt: str) -> None:
        with self.lock:
            self.queue.append(prompt)
            if self.turn is None:
                self.turn = threading.Thread(target=self._turn_loop, daemon=True)
                self.turn.start()

    def _answer_dialog(self, answer: str) -> bool:
        """A key while the permission dialog is up answers it."""
        with self.lock:
            if not self.in_permission:
                return False
            self.dialog_answer = answer
            proc = self.hook_proc
        self.log(event="dialog_key", answer=answer)
        if proc is not None:
            try:
                proc.kill()
            except OSError:
                pass
        return True

    def interrupt(self) -> None:
        with self.lock:
            busy = self.turn is not None
            proc = self.hook_proc
            if busy:
                self.interrupted = True
                self.queue.clear()
        if proc is not None:
            try:
                proc.kill()
            except OSError:
                pass
        if busy:
            self.log(event="escape")

    def interactive(self) -> None:
        import termios
        import tty
        fd = sys.stdin.fileno()
        try:
            old = termios.tcgetattr(fd)
            tty.setraw(fd)
        except termios.error:
            old = None

        def _bye(*_a):
            self.log(event="exit")
            if old is not None:
                try:
                    termios.tcsetattr(fd, termios.TCSADRAIN, old)
                except termios.error:
                    pass
            os._exit(0)

        signal.signal(signal.SIGTERM, _bye)
        signal.signal(signal.SIGHUP, _bye)
        self.log(event="start", argv=self.argv, skip_perms=self.skip_perms)
        self.fire("SessionStart", {"source": "startup"})
        sys.stdout.write("stand-in Claude Code\r\n❯ ")
        sys.stdout.flush()
        buf = ""
        while True:
            try:
                data = os.read(fd, 4096)
            except OSError:
                _bye()
            if not data:
                _bye()
            text = data.decode("utf-8", errors="replace")
            if text in ("\x1b[A", "\x1b[B") and self.in_permission:
                with self.lock:
                    self.dialog_row = max(0, self.dialog_row + (1 if text.endswith("B") else -1))
                continue
            if text == "\r" and self._answer_dialog(
                    "allowed" if self.dialog_row == 0 else "denied"):
                buf = ""
                continue
            if text == "\r":
                prompt = buf.strip()
                buf = ""
                if prompt:
                    self.submit(prompt)
                continue
            if "\x1b" in text:
                buf = ""
                self.interrupt()
                continue
            if text in ("\x18", "\x13", "\x03", "\x15"):
                continue  # send-now chord / Ctrl+C / Ctrl+U: nothing queued here
            buf += text


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--version" in argv or "-v" in argv:
        print(f"{VERSION} (Claude Code)")
        return 0
    if argv[:2] == ["auth", "status"]:
        print(json.dumps({"loggedIn": True, "authMethod": "oauth_token",
                          "apiProvider": "firstParty"}))
        return 0
    if "-p" in argv or "--print" in argv:
        print("ok")
        return 0
    StandIn(argv).interactive()
    return 0


if __name__ == "__main__":
    sys.exit(main())
