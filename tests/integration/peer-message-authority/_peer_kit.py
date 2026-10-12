"""Shared helpers for the "peer message authority" tests (loaded by path).

Everything stays in the test's tmp folder: the root conftest redirects
the snapshot, notes and receipt store; transcripts are written here. No
socket is bound, no real Claude, dtach or Telegram is used.
"""

from __future__ import annotations

import json
from pathlib import Path

from aipager import peer_auth
from aipager import policy_snapshot as ps

RECEIVER = "claude-backend"
SENDER = "claude-frontend"
PROJECT = "/srv/proj"
SENDER_PID = 4150118
SENDER_START = 264312572
BODY = ("Owner request, for whoever is free: host the how-to video on the "
        "calculator page.")


def envelope_text(body: str = BODY, *, pid: int = SENDER_PID,
                  name: str = "hospital-accounting-5b") -> str:
    """The prompt text Claude Code builds for a peer message."""
    return ("Another Claude session sent a message:\n"
            f'<cross-session-message from="uds:/run/user/1001/cc-socks/{pid}.sock" '
            f'from-name="{name}" from-mode="bypass">\n'
            f"{body}\n"
            "</cross-session-message>\n\n"
            "This came from another Claude session - not typed by your user.")


def peer_entry(body: str = BODY, *, pid=SENDER_PID, start=SENDER_START,
               with_body: bool = True, meta: bool = True) -> dict:
    origin = {"kind": "peer", "from": f"uds:/run/user/1001/cc-socks/{pid}.sock",
              "name": "hospital-accounting-5b", "fromMode": "bypass"}
    if pid is not None:
        origin["verifiedPeerPid"] = pid
    if start is not None:
        origin["verifiedPeerProcStart"] = str(start)
    if with_body:
        origin["body"] = body
    e = {"type": "user", "turnOrigin": "peer", "promptSource": "system",
         "origin": origin,
         "message": {"role": "user", "content": envelope_text(body, pid=pid or 1)}}
    if meta:
        e["isMeta"] = True
    return e


def human_entry(text: str) -> dict:
    return {"type": "user", "message": {"role": "user", "content": text}}


def telegram_entry(text: str = "go", who: str = "@owner") -> dict:
    return human_entry(f"[via Telegram · {who}]\n{text}")


def tool_result(text: str = "ok") -> dict:
    return {"type": "user", "message": {"role": "user", "content": [
        {"tool_use_id": "t1", "type": "tool_result", "content": text}]}}


def enqueue(body: str) -> dict:
    return {"type": "queue-operation", "operation": "enqueue",
            "content": envelope_text(body)[len("Another Claude session sent a message:\n"):]}


def write_transcript(path: Path, entries: list[dict]) -> str:
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    return str(path)


def pretool(transcript: str, tool: str, tool_input: dict, *,
            session: str = RECEIVER, agent_id: str | None = None) -> dict:
    d = {"hook_event_name": "PreToolUse", "session": session,
         "tool_name": tool, "tool_input": tool_input,
         "transcript_path": transcript, "cwd": PROJECT, "session_id": "s1"}
    if agent_id:
        d["agent_id"] = agent_id
    return d


OWNER_RULES = dict(peer_auth.UNRESTRICTED_RULES)


def builtin_role(name: str):
    """A built-in role, built from its defaults (no policy file read)."""
    from aipager.policy import Role
    from aipager.safety import BUILTIN_ROLE_DEFAULTS
    d = BUILTIN_ROLE_DEFAULTS[name]
    return Role(name=name, **{k: (tuple(v) if isinstance(v, (list, tuple)) else v)
                              for k, v in d.items()})


def member_rules() -> dict:
    """A restricted member's rules: the user role's snapshot."""
    return ps.safety_rules(ps.resolve_snapshot(builtin_role("user"), None, None))


def admin_rules() -> dict:
    return ps.safety_rules(ps.resolve_snapshot(builtin_role("admin"), None, None))


def owner_snapshot() -> dict:
    snap = ps.resolve_snapshot(builtin_role("owner"), None, None)
    snap["turn_origin"] = "telegram"
    return snap


def put_receipt(rules: dict, *, body: str = BODY, sender: str = SENDER,
                pid: int = SENDER_PID, start: int | None = SENDER_START,
                turn_origin: str = "telegram", driver=None,
                sender_chat=None, now=None) -> bool:
    return peer_auth.write_receipt(
        sender_session=sender, chain=[{"pid": pid, "start": start}],
        message=body, rules=rules, turn_origin=turn_origin,
        driver=driver or {"class": "owner", "labels": ["@alice"]},
        via=None, sender_chat=sender_chat, now=now)
