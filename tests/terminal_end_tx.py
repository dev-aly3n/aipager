"""The transcript lines Claude Code 2.1.312 writes when a turn ends at the
terminal on a permission dialog, as measured live (release harness,
2026-10-28, roadmap 8.152). Field names and shapes are copied from the
real lines; only ids, times and the paths in the commands differ.

- "No" or Escape in the dialog (D5): the call's rejected result, then the
  interrupt marker with no ``interruptedMessageId``.
- Yes in the dialog, then Escape or ctrl+c while the command runs (D3):
  the very same rejected result, then the marker naming the assistant
  message that holds the call (``interruptedMessageId``).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

MARKER = "[Request interrupted by user for tool use]"
REJECTED = ("The user doesn't want to proceed with this tool use. The tool use was "
            "rejected (eg. if it was a file edit, the new_string was NOT written to "
            "the file). STOP what you are doing and wait for the user to tell you how "
            "to proceed.")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z")


def call(tool_use_id: str, *, message_id: str = "msg_t5", command: str = "ls",
         ts: str | None = None) -> dict:
    return {"type": "assistant", "timestamp": ts or _now_iso(), "uuid": "u-call",
            "message": {"id": message_id, "type": "message", "role": "assistant",
                        "content": [{"type": "tool_use", "id": tool_use_id, "name": "Bash",
                                     "input": {"command": command,
                                               "description": "Run the requested command"}}],
                        "stop_reason": "tool_use"}}


def rejected(tool_use_id: str, *, kind: str | None = "user-rejected",
             ts: str | None = None) -> dict:
    entry = {"type": "user", "timestamp": ts or _now_iso(), "uuid": "u-result",
             "message": {"role": "user", "content": [
                 {"type": "tool_result", "content": REJECTED, "is_error": True,
                  "tool_use_id": tool_use_id}]},
             "toolUseResult": "User rejected tool use",
             "sourceToolAssistantUUID": "u-call"}
    if kind is not None:
        entry["toolDenialKind"] = kind
    return entry


def marker(*, interrupted: str | None = None,
           ts: str | None = None) -> dict:
    entry = {"type": "user", "timestamp": ts or _now_iso(), "uuid": "u-marker",
             "message": {"role": "user", "content": [{"type": "text", "text": MARKER}]}}
    if interrupted is not None:
        entry["interruptedMessageId"] = interrupted
    return entry


def write(path, *entries) -> None:
    with open(path, "a", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e) + "\n")


def terminal_no(path, tool_use_id: str = "toolu_1", *, message_id: str = "msg_t5") -> None:
    """D5: "No" (or Escape) in the dialog."""
    write(path, call(tool_use_id, message_id=message_id), rejected(tool_use_id), marker())


def stopped_while_running(path, tool_use_id: str = "toolu_1", *,
                          message_id: str = "msg_t5") -> None:
    """D3: Yes in the dialog, then Escape while the command ran."""
    write(path, call(tool_use_id, message_id=message_id), rejected(tool_use_id),
          marker(interrupted=message_id))
