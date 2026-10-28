"""Helpers for Claude Code's risky-removal countdown tests (roadmap 8.128):
a wall clock aipager reads through ``rm_countdown._now_wall``, Claude
Code's transcript as a test writes it, and Claude's own risky-removal
prompt played through the two-prompts rig."""

from __future__ import annotations

import hashlib
import json
import time

ARRIVED = 1_800_000_000.0
RM = "rm -rf /home/alice/app"
TUID = "toolu_rm1"
REQ = "req-rm"


class Clock:
    def __init__(self):
        self.now = ARRIVED

    def __call__(self) -> float:
        return self.now

    def at(self, seconds_after_arrival: float) -> None:
        self.now = ARRIVED + seconds_after_arrival


class Transcript:
    """Claude Code's transcript as the test writes it: the call's
    ``tool_use`` and its ``tool_result``, by id, name and command."""

    def __init__(self, path):
        self.path = path
        path.write_text("")

    def __str__(self) -> str:
        return str(self.path)

    def _add(self, entry) -> None:
        with self.path.open("a") as f:
            f.write(json.dumps(entry) + "\n")

    def call(self, tuid=TUID, command=RM, name="Bash") -> None:
        self._add({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": tuid, "name": name, "input": {"command": command}}]}})

    def result(self, tuid=TUID, is_error=True) -> None:
        self._add({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": tuid, "is_error": is_error,
             "content": "whatever Claude Code says"}]}})

    def refused(self, tuid=TUID, command=RM) -> None:
        self.call(tuid, command)
        self.result(tuid, True)


def rm_prompt(r, tx=None, *, command=RM, tool_use_id=TUID, mode="bypassPermissions",
              hook=True, request_id=REQ):
    """Claude's own risky-removal prompt in Full access (a clock)."""
    r.claude_bash(command, tool_use_id=tool_use_id, mode=mode, suggestions=[],
                  transcript_path=str(tx) if tx is not None else None, hook=hook,
                  request_id=request_id)


def scan(r, monitor) -> None:
    r.run_async(monitor._scan_sessions(time.monotonic(), set()))


def sha(command=RM) -> str:
    return hashlib.sha256(command.encode()).hexdigest()
