"""Shared constants and helpers for the black-box "files Claude sends"
tests (loaded by path; see conftest.py)."""

from __future__ import annotations

from pathlib import Path

from aipager import member_root, policy
from aipager import policy_snapshot as ps

SESSION = "claude-frontend"
SID = "5b1d7363-aaaa-bbbb-cccc-0000000000ab"
TOOL = "SendUserFile"
MARKERS = {
    "owner": "[via Telegram · @alice · role:owner]\nsend it",
    "admin": "[via Telegram · @bob · role:admin]\nsend it",
    "user": "[via Telegram · @carol · role:user]\nsend it",
}
FLOOR_WHY = ("a protected file: aipager's settings, Claude's credentials, keys "
             "and secrets are never sent")
TAIL = ". Tell the person which file could not be sent and why."
HEAD = "SendUserFile refused, nothing was sent: "


def claude_line(obj: dict, cwd: str) -> dict:
    """*obj* as Claude Code writes a transcript line: with its ``cwd`` and
    a UTC ``timestamp`` (the daemon matches a SendUserFile call to the
    turn and folder by them)."""
    from datetime import datetime, timezone
    stamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    return {**obj, "cwd": cwd, "timestamp": stamp.replace("+00:00", "Z")}


def reason_for(*items: tuple[str, str]) -> str:
    """The exact refusal sentence of entrypoints.md for (shown, why) items."""
    return HEAD + "; ".join(f"{s} ({w})" for s, w in items) + TAIL


def _pol():
    return policy.load_policy(Path("/nonexistent/p.yaml"), Path("/nonexistent/p.d"))


def use_role(name: str, root_dir=None):
    role = _pol().get_role(name)
    bound = (member_root.RootBound(str(root_dir), ()) if root_dir is not None
             else None)
    ps.write_merged_snapshot(SESSION, ps.resolve_snapshot(
        role, None, None, root_bound=bound))


def png(w: int, h: int) -> bytes:
    import struct
    import zlib
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    chunk = struct.pack(">I", 13) + b"IHDR" + ihdr + struct.pack(
        ">I", zlib.crc32(b"IHDR" + ihdr) & 0xFFFFFFFF)
    iend = struct.pack(">I", 0) + b"IEND" + struct.pack(">I", zlib.crc32(b"IEND"))
    return b"\x89PNG\r\n\x1a\n" + chunk + iend


def gif() -> bytes:
    return b"GIF89a\x04\x00\x04\x00\x00\x00\x00;"


def mp4() -> bytes:
    body = b"isom\x00\x00\x02\x00isomavc1"
    return (8 + len(body)).to_bytes(4, "big") \
        + b"ftyp" + body + b"\x00" * 16


def mp3() -> bytes:
    return b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\xff\xfb\x90\x00" + b"\x00" * 32
