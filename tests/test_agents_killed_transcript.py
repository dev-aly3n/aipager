"""transcript.agents_killed_since: the wall time of Claude Code's newest
``{"type": "system", "subtype": "agents_killed"}`` line (written the moment
its kill chord stopped the background agents, 2.1.312), when at or after
*since_wall*; None for an older one, none at all, or no file (roadmap
8.121 F5). Reads the file's tail only and parses lines only when the
bytes hold the subtype."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from aipager import transcript


def _ts(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _write(path, *entries) -> str:
    path.write_text("".join(json.dumps(e) + "\n" for e in entries))
    return str(path)


def _killed(epoch: float) -> dict:
    return {"type": "system", "subtype": "agents_killed", "timestamp": _ts(epoch),
            "uuid": "u-kill"}


def _user(epoch: float, text: str) -> dict:
    return {"type": "user", "timestamp": _ts(epoch),
            "origin": {"kind": "task-notification"},
            "message": {"role": "user", "content": text}}


def test_a_line_after_since_gives_its_time(tmp_path):
    path = _write(tmp_path / "t.jsonl", _user(1000.0, "hi"), _killed(1500.0),
                  _user(1500.2, "2 background agents were stopped by the user."))
    assert transcript.agents_killed_since(path, 1200.0) == 1500.0


def test_the_newest_line_wins(tmp_path):
    path = _write(tmp_path / "t.jsonl", _killed(1300.0), _killed(1600.0))
    assert transcript.agents_killed_since(path, 1200.0) == 1600.0


def test_a_line_before_since_is_old(tmp_path):
    path = _write(tmp_path / "t.jsonl", _killed(1100.0), _user(1150.0, "later"))
    assert transcript.agents_killed_since(path, 1200.0) is None


def test_at_since_counts(tmp_path):
    path = _write(tmp_path / "t.jsonl", _killed(1200.0))
    assert transcript.agents_killed_since(path, 1200.0) == 1200.0


def test_no_line_no_file_no_path(tmp_path):
    path = _write(tmp_path / "t.jsonl", _user(1000.0, "hello"))
    assert transcript.agents_killed_since(path, 0.0) is None
    assert transcript.agents_killed_since(str(tmp_path / "missing.jsonl"), 0.0) is None
    assert transcript.agents_killed_since("", 0.0) is None


def test_the_subtype_word_in_a_user_message_is_not_a_kill(tmp_path):
    path = _write(tmp_path / "t.jsonl", _user(1500.0, 'grep "agents_killed" logs'))
    assert transcript.agents_killed_since(path, 0.0) is None


def test_a_line_without_a_timestamp_is_not_counted(tmp_path):
    entry = {"type": "system", "subtype": "agents_killed"}
    path = _write(tmp_path / "t.jsonl", entry)
    assert transcript.agents_killed_since(path, 0.0) is None


def test_bytes_without_the_subtype_split_no_line(tmp_path, monkeypatch):
    path = _write(tmp_path / "t.jsonl", *[_user(1000.0 + i, "x") for i in range(20)])
    calls = []
    real = transcript._split_tail

    def counting(data, start):
        calls.append(len(data))
        return real(data, start)

    monkeypatch.setattr(transcript, "_split_tail", counting)
    assert transcript.agents_killed_since(path, 0.0) is None
    assert calls == []
    _write(tmp_path / "t.jsonl", _killed(1500.0))
    assert transcript.agents_killed_since(path, 0.0) == 1500.0
    assert len(calls) == 1
