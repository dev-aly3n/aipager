"""R9 / criterion 23 — everything observable.

"We should never again have to read a journal to learn we are being
throttled." The durable file is cross-process, so ``aipager status`` and
``aipager doctor`` read it directly; every row here drives the documented
reader rather than the writer, because the reader is the contract another
process depends on.
"""

from __future__ import annotations

import argparse
import json
import socket
import time

import pytest

from aipager import config, doctor, status

CHAT = -1001234567
BAN = 34212.0


def _ns(**kw):
    return argparse.Namespace(**kw)


def _doc(tmp_path, chats, *, version=1, written_at=None, name="flood.json"):
    path = tmp_path / name
    path.write_text(json.dumps({
        "version": version,
        "written_at": time.time() if written_at is None else written_at,
        "chats": chats,
    }))
    return str(path)


def _chat(**kw):
    row = {
        "chat_id": CHAT, "rate": 0.05, "rate_earned_at": time.time(),
        "backoff": 4.0, "last_429_at": time.time() - 30,
        "ban_stamps": [time.time() - 100], "sustained_used": 17,
        "sustained_limit": 30, "minimal": False,
    }
    row.update(kw)
    return row


# ===== read_flood_chats — the reader's contract =========================

@pytest.mark.parametrize("key", [
    "chat_id", "rate", "sustained_used", "sustained_limit", "minimal",
    "bans_today",
])
def test_every_documented_field_is_present(tmp_path, key):
    """Criterion 23 names seven fields; a consumer that finds six is a
    consumer that crashes in the field."""
    rows = status.read_flood_chats(_doc(tmp_path, [_chat()]))
    assert key in rows[0]


def test_a_live_mute_is_reported_with_its_deadline(tmp_path):
    rows = status.read_flood_chats(_doc(tmp_path, [
        _chat(muted_until=time.time() + BAN, mute_retry_after=BAN)]))
    assert rows[0]["muted_until"] > time.time()


def test_a_lapsed_mute_is_not_reported_as_a_mute(tmp_path):
    """Boundary: 'omitted when lapsed'. Reporting yesterday's ban as
    current is exactly the confusion R9 exists to end."""
    rows = status.read_flood_chats(_doc(tmp_path, [
        _chat(muted_until=time.time() - 1, mute_retry_after=BAN)]))
    assert not rows[0].get("muted_until")


def test_a_fresh_document_is_not_stale(tmp_path):
    rows = status.read_flood_chats(_doc(tmp_path, [_chat()]))
    assert rows[0]["stale"] is False


def test_a_document_older_than_the_sustained_window_is_stale(tmp_path):
    """Criterion 23's honesty clause: the sustained figure is a snapshot,
    so past ``FLOOD_SUSTAINED_WINDOW`` it must be MARKED rather than
    quietly printed as current."""
    rows = status.read_flood_chats(_doc(
        tmp_path, [_chat()],
        written_at=time.time() - config.FLOOD_SUSTAINED_WINDOW - 5))
    assert rows[0]["stale"] is True


def test_bans_today_counts_the_stamps(tmp_path):
    rows = status.read_flood_chats(_doc(tmp_path, [
        _chat(ban_stamps=[time.time() - 100, time.time() - 50])]))
    assert rows[0]["bans_today"] == 2


def test_a_missing_file_reports_nothing(tmp_path):
    assert status.read_flood_chats(str(tmp_path / "nope.json")) == []


@pytest.mark.parametrize("body", ["", "{", "null", "[]", '{"chats": []}'])
def test_a_malformed_file_reports_nothing(tmp_path, body):
    """'never inferred' — a reader that guesses would tell the operator
    the bot is healthy during a 9.5-hour ban."""
    path = tmp_path / "broken.json"
    path.write_text(body)
    assert status.read_flood_chats(str(path)) == []


def test_a_wrong_version_reports_nothing(tmp_path):
    assert status.read_flood_chats(_doc(tmp_path, [_chat()], version=99)) == []


# ===== flood_chat_lines — what the operator actually reads ==============

def test_one_line_per_chat(tmp_path):
    rows = status.read_flood_chats(_doc(tmp_path, [
        _chat(), _chat(chat_id=-100999)]))
    assert len(status.flood_chat_lines(rows)) == 2


def test_the_lines_are_sorted_by_chat_id(tmp_path):
    rows = status.read_flood_chats(_doc(tmp_path, [
        _chat(chat_id=-100999), _chat(chat_id=-1001234567)]))
    lines = status.flood_chat_lines(rows)
    assert str(-1001234567) in lines[0]


def test_no_chats_means_no_lines():
    assert status.flood_chat_lines([]) == []


def test_the_line_names_the_earned_rate(tmp_path):
    """The one number spec.md's live verification asks ``aipager status``
    to show."""
    rows = status.read_flood_chats(_doc(tmp_path, [_chat(rate=0.25)]))
    assert "0.25" in status.flood_chat_lines(rows)[0]


def test_the_line_says_when_a_chat_is_in_minimal_mode(tmp_path):
    rows = status.read_flood_chats(_doc(tmp_path, [_chat(minimal=True)]))
    assert "minimal" in status.flood_chat_lines(rows)[0].lower()


# ===== the JSON surface =================================================

def test_status_json_carries_flood_chats(monkeypatch, capsys, tmp_path):
    """Criterion 23's machine-readable half, beside the existing
    ``flood_muted`` / ``flood_backoff`` keys."""
    monkeypatch.setattr("aipager.config.FLOOD_STATE_FILE",
                        _doc(tmp_path, [_chat()]))
    monkeypatch.setattr(status, "BOT_TOKEN", "tok")
    monkeypatch.setattr(status, "CHAT_ID", "5")
    monkeypatch.setattr(status, "_daemon_alive", lambda: True)
    monkeypatch.setattr(status, "_gather_sessions", lambda: ([], set()))
    status.cmd_status(_ns(as_json=True))
    payload = json.loads(capsys.readouterr().out)
    assert [row["chat_id"] for row in payload["flood_chats"]] == [CHAT]


def test_status_json_still_carries_the_older_keys(
    monkeypatch, capsys, tmp_path,
):
    """The new key rides BESIDE the old ones; a consumer of either must
    not break."""
    monkeypatch.setattr("aipager.config.FLOOD_STATE_FILE",
                        _doc(tmp_path, [_chat()]))
    monkeypatch.setattr(status, "BOT_TOKEN", "tok")
    monkeypatch.setattr(status, "CHAT_ID", "5")
    monkeypatch.setattr(status, "_daemon_alive", lambda: True)
    monkeypatch.setattr(status, "_gather_sessions", lambda: ([], set()))
    status.cmd_status(_ns(as_json=True))
    payload = json.loads(capsys.readouterr().out)
    assert {"flood_muted", "flood_backoff"} <= set(payload)


# ===== doctor ===========================================================

def _listening(monkeypatch, tmp_path):
    sock_path = tmp_path / "aipager.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    server.bind(str(sock_path))
    monkeypatch.setattr("aipager.config.SOCKET_PATH", str(sock_path))
    return server


def test_a_healthy_daemon_row_is_ok(monkeypatch, tmp_path):
    """The control for the two rows below."""
    server = _listening(monkeypatch, tmp_path)
    try:
        monkeypatch.setattr("aipager.config.FLOOD_STATE_FILE",
                            _doc(tmp_path, [_chat()]))
        assert doctor.check_daemon().status == doctor.OK
    finally:
        server.close()


def test_minimal_mode_warns(monkeypatch, tmp_path):
    """Criterion 23's last clause: minimal mode is a NEW state and worth a
    row — a bot shedding pixels is a bot in trouble."""
    server = _listening(monkeypatch, tmp_path)
    try:
        monkeypatch.setattr("aipager.config.FLOOD_STATE_FILE",
                            _doc(tmp_path, [_chat(minimal=True)]))
        assert doctor.check_daemon().status == doctor.WARN
    finally:
        server.close()


def test_a_reduced_but_healthy_rate_does_not_change_severity(
    monkeypatch, tmp_path,
):
    """'a merely reduced rate never changes severity' — otherwise every
    429 in a busy hour would page the operator."""
    server = _listening(monkeypatch, tmp_path)
    try:
        monkeypatch.setattr("aipager.config.FLOOD_STATE_FILE",
                            _doc(tmp_path, [_chat(rate=0.25)]))
        assert doctor.check_daemon().status == doctor.OK
    finally:
        server.close()


def test_the_daemon_row_carries_the_per_chat_line(monkeypatch, tmp_path):
    server = _listening(monkeypatch, tmp_path)
    try:
        monkeypatch.setattr("aipager.config.FLOOD_STATE_FILE",
                            _doc(tmp_path, [_chat(rate=0.25)]))
        assert any("0.25" in line for line in doctor.check_daemon().detail)
    finally:
        server.close()
