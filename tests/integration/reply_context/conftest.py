"""Package-wide /tmp isolation for the reply-context integration tests.

Every test in this package is autouse-isolated rather than each file
opting in, because opting in is exactly what gets forgotten: two files
here (`test_cross_chat_collision.py`, `test_migration.py`) shipped with
no isolation at all and wrote eight real files into `/tmp` between them —
`/tmp/claude-policy-claude-a.json`, `/tmp/claude-reply-claude-jim__d222.txt`
and friends.

That matters on this machine specifically: a live aipager daemon is
running here, and a test session name that collides with a real one would
clobber that session's actual policy snapshot or reply-context file. The
rule is "never write to a real /tmp/claude-* path"; an autouse fixture is
the only version of that rule a future test cannot skip.
"""
from __future__ import annotations

import pytest

from aipager import policy_snapshot as ps


@pytest.fixture(autouse=True)
def _isolate_claude_tmp_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(ps, "snapshot_path", lambda n: tmp_path / f"{n}.json")
    monkeypatch.setattr(ps, "reply_context_path", lambda n: tmp_path / f"{n}.txt")
    # `notes_dir` is already redirected to `tmp_path` by the suite-wide
    # autouse fixture in tests/conftest.py (`_isolate_notes_dir`) — no
    # separate redirect needed here.


def latest_note_reply_context(session_name: str) -> str | None:
    """The ``reply_context`` carried by the most recently written,
    still-outstanding note for a session — or ``None`` if there isn't
    one.

    Queue handoff (design.md) moved reply_context off the canonical,
    daemon-written snapshot (``ps.read_snapshot`` — now populated only
    by the ``UserPromptSubmit`` hook's pick-up merge, not at send time)
    onto the per-message note ``_inject_prompt`` writes instead. Every
    test in this package that used to assert
    ``ps.read_snapshot(name)["reply_context"]`` right after an inject
    reads it here instead — the note IS "what this send resolved
    reply_context to", which is exactly what those assertions meant.
    """
    notes = ps.list_outstanding_notes(session_name)
    if not notes:
        return None
    return notes[-1].get("reply_context", "")
