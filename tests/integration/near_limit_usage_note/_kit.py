"""Shared fixtures-by-hand for the near-limit usage note black-box tests.

Only the public surface from entrypoints.md is used: usage_store's
note_plan / plan_key / plan_windows / limit_level, usage_note's card_rows /
windows_for, usage_cmd.reset_text and animation.build_stream_card_ex."""

from __future__ import annotations

import time

from aipager.claude_bootstrap import SessionLogin
from aipager.scope import Member, Scope
from aipager.state import Status, TrackedSession

#: The fake-Telegram people (entrypoints.md "Test inputs").
OWNER = 900000001
USER = 900000002
READER = 900000003
ADMIN = 900000004
GROUP = -4000000001

NOW = 1_794_000_000.0
DAY = 86400

LOGIN_A = SessionLogin("saved", "", "/home/alice/.claude")
LOGIN_B = SessionLogin("saved", "", "/home/alice/.claude-work")
UNKNOWN = SessionLogin("unknown")

#: Bidi and isolate marks the card may wrap a row in (escaped on purpose).
MARKS = "\u200e\u200f\u2066\u2067\u2068\u2069\u061c"


def strip_marks(text: str) -> str:
    return text.translate({ord(c): None for c in MARKS})


def scopes():
    """Owner DM, admin DM, a plain user's DM, a read-only member's DM, and a
    group the owner shares with a plain user."""
    return [
        Scope(chat_id=OWNER, kind="dm", label="owner", members=(
            Member(id=OWNER, label="alice", role="owner"),)),
        Scope(chat_id=ADMIN, kind="dm", label="dave", members=(
            Member(id=ADMIN, label="dave", role="admin"),)),
        Scope(chat_id=USER, kind="dm", label="bob", members=(
            Member(id=USER, label="bob", role="user"),)),
        Scope(chat_id=READER, kind="dm", label="carol", members=(
            Member(id=READER, label="carol", role="read_only"),)),
        Scope(chat_id=GROUP, kind="group", label="team", members=(
            Member(id=OWNER, label="alice", role="owner"),
            Member(id=USER, label="bob", role="user"))),
    ]


def card_sess(label="frontend", rows=(("Bash: ls", True),), chat=None):
    sess = TrackedSession(name=f"claude-{label}", label=label, status=Status.BUSY)
    sess.busy_msg_id = 10
    sess.busy_started_at = time.monotonic() - 5
    sess.stream_last_rendered = ""
    sess.tool_history = list(rows)
    if chat is not None:
        sess.scope_chat_id = chat
    return sess
