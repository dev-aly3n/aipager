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
