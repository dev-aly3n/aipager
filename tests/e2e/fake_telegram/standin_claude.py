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
- Escape interrupts a running turn (no ``Stop``, like Claude). The
  permission dialog is up while the hook waits and, when the hook gives
  no decision, after it (as Claude shows its own dialog then). Its rows
  are Claude Code 2.1.291's for Write: "Yes", "Yes, allow all edits
  during this session", "No, and tell Claude what to do differently
  (esc)"; arrows move and clamp at the ends, Enter chooses. A key while
  the hook still waits ends the hook, as Claude does. The last row ends
  the turn like an interrupt, as 2.1.291 does: a rejected tool result,
  ``[Request interrupted by user for tool use]``, no ``PostToolUse``
  and no ``Stop`` (aipager's keystroke-fallback Deny).
- Hooks run in their own process group and a key kills the whole group,
  so the hook really stops (``sh -c`` under dash forks it, and killing
  only the shell left the hook waiting out its deadline).
- A test can hold the next tool call with ``standin-hold-<session>`` in
  the instance folder, to choose when the permission is asked, and give
  the PermissionRequest hook a shorter reply deadline with
  ``standin-hook-deadline-<session>`` (seconds), so the hook stops
  waiting and the answer is typed into the dialog.
- A prompt containing one of these keywords plays a tree of background
  agents (agents-line-waiting), with Claude Code 2.1.296's hook shapes.
  The ids come from the stand-in's session id: agent P is ``a`` + 16 hex,
  agent C ``b`` + 16 hex.

  - ``agents: launch``: the main loop's Agent call starts P
    (``pipeline-runner``; ``SubagentStart`` P, then the call's
    ``PostToolUse`` with ``agentId`` P); P's own Agent call (hooks with
    ``agent_id`` P) starts C (``ship-reviewer``); P ends its turn to wait
    for C (``SubagentStop`` P whose ``background_tasks`` lists C); one
    phantom ``SubagentStop`` (empty type, random id). Then the answer and
    ``Stop``.
  - ``agents: hold``: a ``Bash`` call whose ``PreToolUse`` fires and which
    then waits on ``standin-hold-<session>`` (up to 5 min) with the
    transcript showing the turn still running (for a daemon restart that
    adopts the card); then ``PostToolUse``, the answer and ``Stop``.
  - ``agents: finish``: C stops, P resumes (``SubagentStart`` P), runs a
    ``Read`` of its own (``PreToolUse``/``PostToolUse`` with ``agent_id``
    P) and stops for good (``background_tasks`` empty). Then the answer
    and ``Stop``.
- A prompt containing ``two prompts: agent-first`` or ``two prompts:
  claude-first`` plays two dialogs open at once (roadmap 8.120), with
  Claude Code 2.1.302's dialog order: the main loop's Agent call starts
  agent A in the background (``run_in_background``; ``SubagentStart`` A,
  then the call's ``PostToolUse`` with ``isAsync``). A asks for ``Bash``
  (``PermissionRequest`` with ``agent_id`` A) in its own thread, and the
  main loop asks a two-option AskUserQuestion (its ``PreToolUse``, then the
  ``PermissionRequest`` Claude Code runs for a question too), A first or
  the question first. One dialog is on screen at a time: the one that
  entered the terminal first, the others queued under it in order. The
  question's dialog enters when it is asked; A's, being a background
  agent's, only once its hook gave no decision (``awaitAutomatedChecks``
  ``BeforeDialog``), and then under every dialog already open. Arrows and
  Enter go to the dialog on screen; answering it shows the next. A's hook
  decision answers A wherever it is. Each answer is logged with the
  dialog it reached (``dialog_key``, ``hook_decision``,
  ``question_answered``, ``agent_answer``). Then A's tool ends, A stops,
  and the main loop answers and stops.
- A prompt asking for a file, "send me the image at <path>" (or the
  Persian "... \u062f\u0631 <path> \u0647\u0633\u062a ..."), plays Claude
  Code 2.1.302's SendUserFile with ``CLAUDE_CODE_ATTACHMENTS_BY_PATH``: the
  tool_use (``files`` a list, ``status`` normal), ``PreToolUse``; on a deny
  the hook's reason is the tool result and the answer quotes it; else (a
  read-only tool, no dialog) ``PostToolUse`` with a 2.1.302-shaped
  ``tool_response`` (``attachments`` with the absolute ``path``, ``size``,
  ``isImage``, ``media_type``, ``pathValidated``), the tool result "1 file
  delivered to user.", the answer and ``Stop``.
- Every prompt and decision goes to
  ``$AIPAGER_INSTANCE_DIR/standin-<CLAUDE_DTACH_SESSION>.jsonl``.
"""

from __future__ import annotations

import datetime as _dt
import json
import mimetypes
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
# "send me the image at <path>", and the Persian "<...> \u062f\u0631 <path>
# \u0647\u0633\u062a <...>" ("the picture that is at <path>").
_SEND_EN_RE = re.compile(r"send me the (?:image|file|picture|photo) at (\S+)", re.I)
_SEND_FA_RE = re.compile("\u062f\u0631 (\\S+) \u0647\u0633\u062a")

#: Claude Code 2.1.291's Write permission dialog, top to bottom. Row 1
#: reads as a plain "allowed" here: the stand-in keeps no session mode.
DIALOG_ROWS = ("Yes", "Yes, allow all edits during this session",
               "No, and tell Claude what to do differently (esc)")
#: What Claude Code records for a tool the dialog's last row rejected.
REJECTED_TOOL_RESULT = ("The user doesn't want to proceed with this tool use. The tool "
                        "use was rejected (eg. if it was a file edit, the new_string was "
                        "NOT written to the file). STOP what you are doing and wait for "
                        "the user to tell you how to proceed.")
INTERRUPT_MARKER = "[Request interrupted by user for tool use]"
_NAME_RE = re.compile(r"create\s+([A-Za-z0-9_-]+)\.txt", re.I)
_AGENTS_RE = re.compile(r"agents:\s*(launch|hold|finish)\b", re.I)
_TWO_RE = re.compile(r"two prompts:\s*(agent-first|claude-first)\b", re.I)
#: Claude Code 2.1.302's Bash permission dialog for an agent, top to bottom.
AGENT_ROWS = ("Yes", "Yes, and don't ask again for make commands in this folder",
              "No, and tell Claude what to do differently (esc)")
#: The two-prompt scenario's question.
TWO_QUESTION = {"questions": [{
    "question": "Which checks should run?", "header": "Checks", "multiSelect": False,
    "options": [{"label": "Only the fast ones", "description": "unit tests"},
                {"label": "All of them", "description": "the whole suite"}]}]}
_ARROWS_RE = re.compile(r"(?:\x1b\[[AB])+")


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _slug(path: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", path)


class StandIn:
    def __init__(self, argv: list[str]):
        self.argv = argv
        self.skip_perms = "--dangerously-skip-permissions" in argv
        # Claude Code's raw permission mode, as its hooks report it; Shift+Tab
        # cycles default -> acceptEdits -> plan -> auto (aipager never adds
        # bypass to the cycle).
        self.raw_mode = "bypassPermissions" if self.skip_perms else "default"
        if "--permission-mode" in argv:
            i = argv.index("--permission-mode")
            if i + 1 < len(argv):
                self.raw_mode = argv[i + 1]
        if self.raw_mode == "manual":
            self.raw_mode = "default"
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
        self.deadline_path = os.path.join(
            inst, f"standin-hook-deadline-{self.session or 'nosession'}")
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
        # The two-prompt scenario's dialogs: [0] is on screen, the rest
        # wait under it in the order they entered the terminal.
        self.dialogs: list[dict] = []
        # The hook processes of that scenario's threads ({"proc": ...}).
        self.holders: list[dict] = []

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

    def assistant(self, content: list, stop_reason: str = "end_turn") -> str:
        self.n += 1
        mid = f"msg_standin_{self.session_id[:8]}_{self.n}"
        self._append({"type": "assistant", "message": {
            "id": mid, "type": "message", "role": "assistant", "model": "claude-haiku-standin",
            "content": content, "stop_reason": stop_reason,
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

    def fire(self, event: str, extra: dict | None = None, tool: str | None = None,
             holder: dict | None = None) -> list[dict]:
        payload = {
            "session_id": self.session_id, "transcript_path": self.transcript,
            "cwd": self.cwd, "hook_event_name": event,
            "permission_mode": self.raw_mode,
        }
        payload.update(extra or {})
        outputs = []
        env = None
        if event == "PermissionRequest":
            try:
                with open(self.deadline_path, encoding="utf-8") as f:
                    deadline = f.read().strip()
            except OSError:
                deadline = ""
            if deadline:
                env = dict(os.environ, AIPAGER_PERMISSION_REPLY_DEADLINE_SECONDS=deadline)
        for cmd, timeout in self._hook_commands(event, tool):
            try:
                # Its own process group: a key at the dialog kills the
                # hook itself, not only the shell that started it.
                proc = subprocess.Popen(cmd, shell=True, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                        text=True, env=env, start_new_session=True)
            except OSError as e:
                self.log(event="hook_error", hook=event, error=str(e))
                continue
            with self.lock:
                if holder is None:
                    self.hook_proc = proc
                else:
                    # Another thread's hook (the two-prompt scenario): its
                    # own dialog's key ends it, not the shared slot.
                    holder["proc"] = proc
            try:
                out, _ = proc.communicate(json.dumps(payload), timeout=timeout)
            except subprocess.TimeoutExpired:
                _kill_group(proc)
                out, _ = proc.communicate()
            finally:
                with self.lock:
                    if holder is None:
                        self.hook_proc = None
                    else:
                        holder["proc"] = None
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
        agents = _AGENTS_RE.search(prompt)
        two = _TWO_RE.search(prompt)
        if two:
            decision = self._two_prompts_turn(two.group(1).lower())
            if self.interrupted:
                self.log(event="interrupted")
                self.user_text("[Request interrupted by user]")
                return
        elif agents:
            decision = self._agents_turn(agents.group(1).lower())
            if self.interrupted:
                self.log(event="interrupted")
                self.user_text("[Request interrupted by user]")
                return
        elif _SEND_EN_RE.search(prompt) or _SEND_FA_RE.search(prompt):
            m = _SEND_EN_RE.search(prompt) or _SEND_FA_RE.search(prompt)
            decision = self._send_file_turn(m.group(1).rstrip(".,;:!?"))
        elif "write tool" in prompt.lower():
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
            if decision == "rejected":
                # The dialog's last row: Claude Code 2.1.291 ends the turn
                # like an interrupt (no PostToolUse, no Stop) and waits.
                self.tool_result(tool_use_id, REJECTED_TOOL_RESULT, True)
                self.user_text(INTERRUPT_MARKER)
                self.log(event="interrupted", by="dialog")
                return
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

    # -- a file sent to the person (SendUserFile) ------------------------------

    def _send_file_turn(self, path: str) -> str:
        tool_input = {"files": [path], "status": "normal"}
        tool_use_id = f"toolu_standin_{uuid.uuid4().hex[:12]}"
        self.assistant([{"type": "tool_use", "id": tool_use_id, "name": "SendUserFile",
                         "input": tool_input}])
        pre = self.fire("PreToolUse", {"tool_name": "SendUserFile",
                                       "tool_input": tool_input,
                                       "tool_use_id": tool_use_id}, tool="SendUserFile")
        reason = None
        for o in pre:
            hso = o.get("hookSpecificOutput") or {}
            if hso.get("permissionDecision") == "deny" or o.get("decision") == "block":
                reason = hso.get("permissionDecisionReason") or o.get("reason") or "denied"
        if reason is not None:
            self.tool_result(tool_use_id, reason, True)
            self.log(event="decision", tool="SendUserFile", decision="denied",
                     reason=reason, path=path)
            return f"could not send: {reason}"
        full = path if os.path.isabs(path) else os.path.join(self.cwd, path)
        try:
            size = os.path.getsize(full)
        except OSError:
            size = 0
        media_type = mimetypes.guess_type(full)[0] or "application/octet-stream"
        response = {"attachments": [{
            "path": os.path.abspath(full), "size": size,
            "isImage": media_type.startswith("image/"), "media_type": media_type,
            "pathValidated": True}]}
        self.fire("PostToolUse", {"tool_name": "SendUserFile", "tool_input": tool_input,
                                  "tool_response": response,
                                  "tool_use_id": tool_use_id}, tool="SendUserFile")
        self.tool_result(tool_use_id, "1 file delivered to user.", False)
        self.log(event="decision", tool="SendUserFile", decision="sent", path=path)
        return "sent"

    # -- a tree of background agents (agents-line-waiting) -------------------

    def agent_ids(self) -> tuple[str, str]:
        hexid = self.session_id.replace("-", "")
        return "a" + hexid[:16], "b" + hexid[:16]

    def _in_agent(self, agent_id: str, agent_type: str, prompt_id: str) -> dict:
        """The base fields of a hook fired inside an agent."""
        return {"agent_id": agent_id, "agent_type": agent_type,
                "prompt_id": prompt_id}

    def _agents_turn(self, kind: str) -> str:
        p_id, c_id = self.agent_ids()
        prompt_id = str(uuid.uuid4())
        if kind == "launch":
            p_input = {"subagent_type": "pipeline-runner",
                       "description": "Run the pipeline",
                       "prompt": "Run the pipeline", "run_in_background": True}
            tuid = f"toolu_standin_{uuid.uuid4().hex[:12]}"
            self.assistant([{"type": "tool_use", "id": tuid, "name": "Agent",
                             "input": p_input}], stop_reason="tool_use")
            self.fire("PreToolUse", {"tool_name": "Agent", "tool_input": p_input,
                                     "tool_use_id": tuid, "prompt_id": prompt_id},
                      tool="Agent")
            self.fire("SubagentStart", {"agent_id": p_id,
                                        "agent_type": "pipeline-runner"})
            self.fire("PostToolUse", {
                "tool_name": "Agent", "tool_input": p_input, "tool_use_id": tuid,
                "prompt_id": prompt_id,
                "tool_response": {"isAsync": True, "status": "async_launched",
                                  "agentId": p_id, "description": "Run the pipeline"}},
                tool="Agent")
            self.tool_result(tuid, f"Async agent launched successfully. agentId: {p_id}",
                             False)
            # Inside P: its own Agent call starts C.
            c_input = {"subagent_type": "ship-reviewer", "description": "Review",
                       "prompt": "Review the change", "run_in_background": True}
            inner = self._in_agent(p_id, "pipeline-runner", prompt_id)
            ctuid = f"toolu_standin_{uuid.uuid4().hex[:12]}"
            self.fire("PreToolUse", dict(inner, tool_name="Agent", tool_input=c_input,
                                         tool_use_id=ctuid), tool="Agent")
            self.fire("SubagentStart", {"agent_id": c_id, "agent_type": "ship-reviewer"})
            self.fire("PostToolUse", dict(
                inner, tool_name="Agent", tool_input=c_input, tool_use_id=ctuid,
                tool_response={"isAsync": True, "status": "async_launched",
                               "agentId": c_id, "description": "Review"}),
                tool="Agent")
            # P ends its turn to wait for C.
            self.fire("SubagentStop", dict(
                inner, stop_hook_active=False,
                agent_transcript_path=os.path.join(
                    os.path.dirname(self.transcript), "subagents",
                    f"agent-{p_id}.jsonl"),
                last_assistant_message="Waiting for the reviewer.",
                background_tasks=[{"id": c_id, "type": "local_agent",
                                   "status": "running", "description": "Review",
                                   "agent_type": "ship-reviewer"}],
                session_crons=[]))
            # A phantom stop: empty type, an id nobody started.
            self.fire("SubagentStop", {"agent_id": "a" + uuid.uuid4().hex[:16],
                                       "agent_type": "", "stop_hook_active": False,
                                       "last_assistant_message": "",
                                       "background_tasks": [], "session_crons": []})
            self.log(event="agents", kind=kind, parent=p_id, child=c_id)
            return "agents launched"
        if kind == "hold":
            b_input = {"command": "sleep 1", "description": "Wait a moment"}
            tuid = f"toolu_standin_{uuid.uuid4().hex[:12]}"
            self.assistant([{"type": "tool_use", "id": tuid, "name": "Bash",
                             "input": b_input}], stop_reason="tool_use")
            self.fire("PreToolUse", {"tool_name": "Bash", "tool_input": b_input,
                                     "tool_use_id": tuid, "prompt_id": prompt_id},
                      tool="Bash")
            self.log(event="agents", kind=kind, held=os.path.exists(self.hold_path))
            self._wait_hold(limit=300.0)
            if self.interrupted:
                return "interrupted"
            self.fire("PostToolUse", {"tool_name": "Bash", "tool_input": b_input,
                                      "tool_use_id": tuid, "prompt_id": prompt_id,
                                      "tool_response": {"stdout": "", "stderr": "",
                                                        "interrupted": False}},
                      tool="Bash")
            self.tool_result(tuid, "", False)
            return "agents held"
        # finish
        inner = self._in_agent(p_id, "pipeline-runner", prompt_id)
        self.fire("SubagentStop", dict(
            self._in_agent(c_id, "ship-reviewer", prompt_id), stop_hook_active=False,
            last_assistant_message="Review done.", background_tasks=[],
            session_crons=[]))
        self.fire("SubagentStart", {"agent_id": p_id, "agent_type": "pipeline-runner",
                                    "prompt_id": prompt_id})
        r_input = {"file_path": os.path.join(self.cwd, "review.md")}
        rtuid = f"toolu_standin_{uuid.uuid4().hex[:12]}"
        self.fire("PreToolUse", dict(inner, tool_name="Read", tool_input=r_input,
                                     tool_use_id=rtuid), tool="Read")
        self.fire("PostToolUse", dict(inner, tool_name="Read", tool_input=r_input,
                                      tool_use_id=rtuid,
                                      tool_response={"type": "text"}), tool="Read")
        self.fire("SubagentStop", dict(
            inner, stop_hook_active=False, last_assistant_message="Pipeline done.",
            background_tasks=[], session_crons=[]))
        self.log(event="agents", kind=kind, parent=p_id, child=c_id)
        return "agents finished"

    # -- two dialogs open at once (roadmap 8.120) ----------------------------

    def _push_dialog(self, dialog: dict) -> int:
        """A dialog enters the terminal: under every dialog already open
        (Claude Code queues a newer ask below the older ones, and puts a
        background agent's under everything). Its place, 1 = on screen."""
        with self.lock:
            self.dialogs.append(dialog)
            place = len(self.dialogs)
        self.log(event="dialog_open", dialog=dialog["id"], place=place)
        return place

    def _wait_dialog(self, dialog: dict, limit: float = 600.0) -> str | None:
        deadline = time.monotonic() + limit
        while time.monotonic() < deadline:
            with self.lock:
                answer, stopped = dialog.get("answer"), self.interrupted
            if answer is not None or stopped:
                return answer
            time.sleep(0.05)
        return None

    def _answer_top(self) -> bool:
        """Enter: the dialog on screen takes the row it is on, and the next
        one shows."""
        with self.lock:
            if not self.dialogs:
                return False
            top = self.dialogs.pop(0)
            row = top["row"]
            if top["kind"] == "question":
                answer = f"row {row + 1}"
            else:
                answer = "rejected" if row == len(top["rows"]) - 1 else "allowed"
            top["answer"] = answer
            proc = (top.get("holder") or {}).get("proc")
        self.log(event="dialog_key", dialog=top["id"], row=row + 1, answer=answer)
        if proc is not None:
            _kill_group(proc)   # a key at the dialog ends its hook
        return True

    def _agent_asks(self, agent_id: str, prompt_id: str, out: dict) -> None:
        """Background agent A asks for Bash. Its dialog does not exist while
        its hook waits; with no decision it enters under every dialog open."""
        inner = self._in_agent(agent_id, "ship-reviewer", prompt_id)
        b_input = {"command": "make check", "description": "Run the checks"}
        tuid = f"toolu_standin_{uuid.uuid4().hex[:12]}"
        self.fire("PreToolUse", dict(inner, tool_name="Bash", tool_input=b_input,
                                     tool_use_id=tuid), tool="Bash")
        holder: dict = {}
        with self.lock:
            self.holders.append(holder)
        outs = self.fire("PermissionRequest", dict(
            inner, tool_name="Bash", tool_input=b_input, permission_suggestions=[]),
            tool="Bash", holder=holder)
        behavior = None
        for o in outs:
            behavior = ((o.get("hookSpecificOutput") or {}).get("decision")
                        or {}).get("behavior") or behavior
        if self.interrupted:
            out["answer"] = "interrupted"
            return
        if behavior in ("allow", "deny"):
            self.log(event="hook_decision", dialog="A", behavior=behavior)
            answer, by = ("allowed" if behavior == "allow" else "denied"), "hook"
        else:
            dialog = {"id": "A", "kind": "tool", "rows": AGENT_ROWS, "row": 0,
                      "answer": None}
            self._push_dialog(dialog)
            answer, by = self._wait_dialog(dialog) or "no decision", "key"
        self.log(event="agent_answer", dialog="A", answer=answer, by=by)
        out["answer"] = answer
        if answer == "allowed":
            self.fire("PostToolUse", dict(
                inner, tool_name="Bash", tool_input=b_input, tool_use_id=tuid,
                tool_response={"stdout": "ok", "stderr": "", "interrupted": False}),
                tool="Bash")
        else:
            self.fire("PostToolUseFailure", dict(
                inner, tool_name="Bash", tool_input=b_input, tool_use_id=tuid,
                error="The user doesn't want to proceed with this tool use."),
                tool="Bash")
        self.fire("SubagentStop", dict(
            inner, stop_hook_active=False, last_assistant_message="Checks done.",
            background_tasks=[], session_crons=[]))

    def _open_question(self) -> tuple[dict, str] | None:
        """The main loop asks its question: the dialog enters at once (under
        any already open), and the PermissionRequest hooks run meanwhile."""
        tuid = f"toolu_standin_{uuid.uuid4().hex[:12]}"
        self.assistant([{"type": "tool_use", "id": tuid, "name": "AskUserQuestion",
                         "input": TWO_QUESTION}], stop_reason="tool_use")
        pre = self.fire("PreToolUse", {"tool_name": "AskUserQuestion",
                                       "tool_input": TWO_QUESTION, "tool_use_id": tuid},
                        tool="AskUserQuestion")
        for o in pre:
            hso = o.get("hookSpecificOutput") or {}
            if (hso.get("permissionDecision") or o.get("decision")) in ("deny", "block"):
                self.log(event="question_blocked")
                return None
        rows = tuple(o["label"] for o in TWO_QUESTION["questions"][0]["options"]) + (
            "Type something.",)
        dialog = {"id": "Q", "kind": "question", "rows": rows, "row": 0,
                  "answer": None, "holder": {}}
        with self.lock:
            self.holders.append(dialog["holder"])
        self._push_dialog(dialog)
        threading.Thread(target=self.fire, args=("PermissionRequest", {
            "tool_name": "AskUserQuestion", "tool_input": TWO_QUESTION,
            "permission_suggestions": []}),
            kwargs={"tool": "AskUserQuestion", "holder": dialog["holder"]},
            daemon=True).start()
        return dialog, tuid

    def _finish_question(self, opened: tuple[dict, str] | None) -> str:
        if opened is None:
            return "blocked"
        dialog, tuid = opened
        answer = self._wait_dialog(dialog)
        if answer is None:
            return "interrupted"
        row = int(answer.split()[1])
        label = dialog["rows"][row - 1]
        self.log(event="question_answered", dialog="Q", row=row, label=label)
        question = TWO_QUESTION["questions"][0]["question"]
        self.fire("PostToolUse", {
            "tool_name": "AskUserQuestion", "tool_input": TWO_QUESTION,
            "tool_use_id": tuid,
            "tool_response": {"questions": TWO_QUESTION["questions"],
                              "answers": {question: label}}}, tool="AskUserQuestion")
        self.tool_result(tuid, f'User has answered your questions: "{question}"="{label}".',
                         False)
        return label

    def _two_prompts_turn(self, order: str) -> str:
        a_id, _c_id = self.agent_ids()
        prompt_id = str(uuid.uuid4())
        a_input = {"subagent_type": "ship-reviewer", "description": "Run the checks",
                   "prompt": "Run the checks", "run_in_background": True}
        tuid = f"toolu_standin_{uuid.uuid4().hex[:12]}"
        self.assistant([{"type": "tool_use", "id": tuid, "name": "Agent",
                         "input": a_input}], stop_reason="tool_use")
        self.fire("PreToolUse", {"tool_name": "Agent", "tool_input": a_input,
                                 "tool_use_id": tuid, "prompt_id": prompt_id}, tool="Agent")
        self.fire("SubagentStart", {"agent_id": a_id, "agent_type": "ship-reviewer"})
        self.fire("PostToolUse", {
            "tool_name": "Agent", "tool_input": a_input, "tool_use_id": tuid,
            "prompt_id": prompt_id,
            "tool_response": {"isAsync": True, "status": "async_launched",
                              "agentId": a_id, "description": "Run the checks"}},
            tool="Agent")
        self.tool_result(tuid, f"Async agent launched successfully. agentId: {a_id}", False)
        # The busy card is up before anyone asks (its send is paced).
        time.sleep(3.0)
        agent_out: dict = {}
        agent = threading.Thread(target=self._agent_asks, args=(a_id, prompt_id, agent_out),
                                 daemon=True)
        if order == "agent-first":
            agent.start()
            time.sleep(1.5)
            label = self._finish_question(self._open_question())
        else:
            opened = self._open_question()
            time.sleep(1.5)
            agent.start()
            label = self._finish_question(opened)
        self.log(event="two_prompts", order=order, agent=a_id)
        agent.join(timeout=600)
        if self.interrupted:
            return "interrupted"
        return f"question {label}; agent {agent_out.get('answer', 'no decision')}"

    def _wait_hold(self, limit: float = 120.0) -> None:
        """A test may hold this session's next tool call (file
        ``standin-hold-<session>`` in the instance folder) to choose when
        "Claude" asks for permission. Real Claude's timing is its own."""
        deadline = time.monotonic() + limit
        while (os.path.exists(self.hold_path) and not self.interrupted
               and time.monotonic() < deadline):
            time.sleep(0.1)

    def _cycle_mode(self, count: int) -> None:
        """Shift+Tab: the next mode in Claude Code's cycle (no hook fires;
        the next event carries the new mode)."""
        for _ in range(count):
            if self.raw_mode in _MODE_CYCLE:
                i = _MODE_CYCLE.index(self.raw_mode)
                self.raw_mode = _MODE_CYCLE[(i + 1) % len(_MODE_CYCLE)]
        self.log(event="mode", mode=self.raw_mode)

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
        # Auto asks like Ask here: the stand-in has no safety check, and the
        # e2e scenarios drive the permission dialog in new (Auto) sessions.
        if self.raw_mode == "bypassPermissions":
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
            # A key reached the permission dialog while the hook waited
            # (aipager's keystroke fallback).
            return self.dialog_answer
        for o in outs:
            behavior = ((o.get("hookSpecificOutput") or {}).get("decision") or {}).get("behavior")
            if behavior == "allow":
                return "allowed"
            if behavior == "deny":
                return "denied"
        # No decision from the hook: Claude Code shows its own dialog and
        # waits for a key (or an Escape).
        with self.lock:
            if self.dialog_answer is None and not self.interrupted:
                self.in_permission = True
        self.log(event="dialog_open")
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            with self.lock:
                answer, stopped = self.dialog_answer, self.interrupted
            if answer is not None or stopped:
                break
            time.sleep(0.05)
        with self.lock:
            self.in_permission = False
        return self.dialog_answer or "no decision"

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
            _kill_group(proc)
        return True

    def interrupt(self) -> None:
        with self.lock:
            busy = self.turn is not None
            procs = [self.hook_proc] + [h.get("proc") for h in self.holders]
            if busy:
                self.interrupted = True
                self.queue.clear()
                self.dialogs.clear()
        for proc in procs:
            if proc is not None:
                _kill_group(proc)
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
        self.log(event="start", argv=self.argv, skip_perms=self.skip_perms,
                 mode=self.raw_mode)
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
            if self.dialogs and _ARROWS_RE.fullmatch(text):
                # The dialog on screen takes the arrows (two-prompt scenario).
                with self.lock:
                    if self.dialogs:
                        top = self.dialogs[0]
                        for key in re.findall(r"\x1b\[([AB])", text):
                            top["row"] = min(len(top["rows"]) - 1, max(
                                0, top["row"] + (1 if key == "B" else -1)))
                continue
            if self.dialogs and text == "\r" and self._answer_top():
                buf = ""
                continue
            # aipager paces its keys (0.1 s apart), so an arrow and the
            # Enter after it arrive in separate reads; an Enter in the same
            # read as an arrow would fall through to the input buffer.
            if self.in_permission and _ARROWS_RE.fullmatch(text):
                with self.lock:
                    for key in re.findall(r"\x1b\[([AB])", text):
                        # Claude's menu clamps at both ends; it never wraps.
                        self.dialog_row = min(len(DIALOG_ROWS) - 1, max(
                            0, self.dialog_row + (1 if key == "B" else -1)))
                continue
            if text == "\r" and self._answer_dialog(
                    "rejected" if self.dialog_row == len(DIALOG_ROWS) - 1 else "allowed"):
                buf = ""
                continue
            if text == "\r":
                prompt = buf.strip()
                buf = ""
                if prompt:
                    self.submit(prompt)
                continue
            if text and text == "\x1b[Z" * (len(text) // 3):
                self._cycle_mode(len(text) // 3)
                continue
            if "\x1b" in text:
                buf = ""
                self.interrupt()
                continue
            if text in ("\x18", "\x13", "\x03", "\x15"):
                continue  # send-now chord / Ctrl+C / Ctrl+U: nothing queued here
            buf += text


_MODE_CYCLE = ("default", "acceptEdits", "plan", "auto")


def _kill_group(proc: subprocess.Popen) -> None:
    """Kill a hook and everything it started (its own process group)."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        try:
            proc.kill()
        except OSError:
            pass


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
