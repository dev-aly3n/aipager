"""Per-chat flood state in `aipager status` and `aipager doctor` (R9).

The operator-facing half of the ship. When a chat's cards go quiet the
question is always the same — "is it broken, or is it being careful?" —
and before 8.28 there was no way to tell from outside the process: the
earned rate lived in daemon memory, and the two signal files only ever
showed a chat that was actively muted or backing off.

Read from the DURABLE file, which is why a healthy chat's rate is visible
at all: the tmpfs signals are unlinked as soon as nothing is wrong.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from aipager import config, doctor, status

CHAT = 256113222


def _write(chats, *, written_at=None, version=1) -> Path:
    path = Path(config.FLOOD_STATE_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "version": version,
        "written_at": time.time() if written_at is None else written_at,
        "chats": chats,
    }))
    return path


def _row(**kw) -> dict:
    row = {
        "chat_id": CHAT, "rate": 0.5, "backoff": 1.0,
        "sustained_used": 7, "sustained_limit": 30, "minimal": False,
        "ban_stamps": [],
    }
    row.update(kw)
    return row


# ── the reader ───────────────────────────────────────────────────────────────

def test_a_missing_file_reports_no_chats():
    """The normal condition of a healthy install that has never been
    rate-limited. Flood state is never inferred."""
    assert status.read_flood_chats() == []


@pytest.mark.parametrize("body", [
    "{}", "[]", "not json", '{"version": 2, "chats": []}',
    '{"version": 1, "chats": "nope"}',
])
def test_a_malformed_or_wrong_version_file_reports_no_chats(body):
    """`aipager status` must not crash on a file the daemon was midway
    through replacing, or one written by a different version."""
    path = Path(config.FLOOD_STATE_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    assert status.read_flood_chats() == []


def test_a_chat_reports_its_rate_volume_and_ban_history():
    _write([_row(rate=0.25, sustained_used=12,
                 ban_stamps=[time.time() - 100, time.time() - 200])])
    (chat,) = status.read_flood_chats()
    assert chat["chat_id"] == CHAT
    assert chat["rate"] == pytest.approx(0.25)
    assert (chat["sustained_used"], chat["sustained_limit"]) == (12, 30)
    assert chat["minimal"] is False
    assert chat["bans_today"] == 2
    assert chat["stale"] is False
    assert "muted_until" not in chat


def test_a_ban_older_than_a_day_is_not_counted_as_today():
    """"Bans today" is the question an operator actually asks; a ban last
    week is history, not news."""
    _write([_row(ban_stamps=[time.time() - 100_000])])
    assert status.read_flood_chats()[0]["bans_today"] == 0


def test_a_lapsed_mute_is_dropped_but_a_live_one_is_reported():
    """Same rule `read_flood_mutes` follows: the file is a snapshot and
    the deadline may have passed since it was written."""
    _write([_row(muted_until=time.time() - 10)])
    assert "muted_until" not in status.read_flood_chats()[0]

    _write([_row(muted_until=time.time() + 5000)])
    assert status.read_flood_chats()[0]["muted_until"] > time.time()


def test_a_stale_document_is_flagged_so_its_volume_figures_are_ignored():
    """`sustained_used` describes a ROLLING window. If the document is
    older than that window the figure describes a window that has already
    rolled, and reporting it as current would be a lie with a number
    attached.

    Mutation: drop the `stale` computation and a daemon that stopped an
    hour ago still reports "7/30 in the last minute".
    """
    _write([_row()], written_at=time.time() - config.FLOOD_SUSTAINED_WINDOW - 5)
    chat = status.read_flood_chats()[0]
    assert chat["stale"] is True
    assert "in the last minute" not in "".join(
        status.flood_chat_lines([chat]))


# ── the lines ────────────────────────────────────────────────────────────────

def test_the_line_reads_as_a_sentence_for_a_healthy_chat():
    _write([_row(rate=0.5, sustained_used=7)])
    (line,) = status.flood_chat_lines(status.read_flood_chats())
    assert line == (f"Telegram chat {CHAT}: rate 0.50/s, "
                    "7/30 in the last minute")


def test_the_line_names_minimal_mode_and_the_mute():
    until = time.time() + 5000
    _write([_row(rate=0.05, minimal=True, muted_until=until,
                 ban_stamps=[time.time() - 60])])
    (line,) = status.flood_chat_lines(status.read_flood_chats())
    assert "MINIMAL MODE, card updates paused" in line
    assert "flood-muted until" in line
    assert "1 ban(s) in the last 24 h" in line


def test_the_lines_are_sorted_so_the_output_is_stable():
    """Dict order is insertion order, and the daemon's insertion order is
    whichever chat sent first. An operator diffing two runs should not see
    the rows move."""
    _write([_row(chat_id=999), _row(chat_id=-1001), _row(chat_id=CHAT)])
    lines = status.flood_chat_lines(status.read_flood_chats())
    assert lines == sorted(lines)


def test_no_chats_means_no_lines():
    assert status.flood_chat_lines([]) == []


# ── the --json shape ─────────────────────────────────────────────────────────

def test_status_json_carries_flood_chats_beside_the_existing_keys(
    monkeypatch, capsys,
):
    """R9's machine-readable half. Beside `flood_muted` and
    `flood_backoff`, not instead of them: they describe different things
    and both are still written."""
    import argparse

    _write([_row(rate=0.125, minimal=True)])
    monkeypatch.setattr(status, "BOT_TOKEN", "tok")
    monkeypatch.setattr(status, "CHAT_ID", "5")
    monkeypatch.setattr(status, "_daemon_alive", lambda: True)
    monkeypatch.setattr(status, "_gather_sessions", lambda: ([], set()))

    status.cmd_status(argparse.Namespace(as_json=True))
    out = json.loads(capsys.readouterr().out)

    assert "flood_muted" in out and "flood_backoff" in out
    assert out["flood_chats"][0]["chat_id"] == CHAT
    assert out["flood_chats"][0]["rate"] == pytest.approx(0.125)
    assert out["flood_chats"][0]["minimal"] is True


# ── both renderers ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("renderer", ["_render_rich", "_render_plain"])
def test_both_renderers_print_a_line_per_chat(monkeypatch, capsys, renderer):
    """Mutation: render in only one and half the operators — whoever pipes
    the output, or whoever does not — never see it."""
    _write([_row(rate=0.25)])
    chats = status.read_flood_chats()
    getattr(status, renderer)(True, [], 0.0, [], backoffs=[],
                             flood_chats=chats)
    assert f"chat {CHAT}" in capsys.readouterr().out


# ── doctor ───────────────────────────────────────────────────────────────────

@pytest.fixture
def live_daemon(monkeypatch, tmp_path):
    """A real AF_UNIX datagram listener, so `check_daemon` gets past its
    socket probe and reaches the flood rows this module is about.

    A bound socket rather than a patch of some internal: `check_daemon`
    has no seam to patch, and inventing one for a test would be a
    production change made for the test's convenience.
    """
    import socket as _socket

    sock_path = tmp_path / "aipager.sock"
    server = _socket.socket(_socket.AF_UNIX, _socket.SOCK_DGRAM)
    server.bind(str(sock_path))
    monkeypatch.setattr("aipager.config.SOCKET_PATH", str(sock_path))
    try:
        yield sock_path
    finally:
        server.close()



def test_doctor_warns_when_a_chat_is_in_minimal_mode(live_daemon):
    """A chat in minimal mode has stopped animating its cards, which looks
    broken and is not — the one new state worth a WARN. Mutation: report
    OK and the operator's only evidence is a card that stopped moving.
    """
    _write([_row(rate=0.1, minimal=True)])
    result = doctor.check_daemon()
    assert result.status == doctor.WARN
    assert any("minimal mode" in d for d in result.detail)
    assert any(f"chat {CHAT}" in d for d in result.detail)


def test_doctor_stays_ok_for_a_merely_reduced_rate(live_daemon):
    """A reduced rate is the system working. It rides along in `detail`
    without changing severity — exactly the decision 8.21 made for the
    backoff. Mutation: WARN on any rate below the ceiling and `doctor`
    cries wolf on every install that has ever seen one 429.
    """
    _write([_row(rate=0.25, minimal=False)])
    result = doctor.check_daemon()
    assert result.status == doctor.OK
    assert any(f"chat {CHAT}" in d for d in result.detail)


def test_doctor_still_warns_about_a_mute_first(live_daemon, monkeypatch, tmp_path):
    """A mute outranks minimal mode: it is the more urgent fact, and its
    row carries the "do not restart into it" advice."""
    mute_file = tmp_path / "mute.json"
    mute_file.write_text(json.dumps(
        {"muted": [{"chat_id": CHAT, "until": time.time() + 5000}]}))
    monkeypatch.setattr("aipager.config.FLOOD_MUTE_FILE", str(mute_file))
    _write([_row(minimal=True)])

    result = doctor.check_daemon()
    assert result.status == doctor.WARN
    assert any("do not restart into it" in d for d in result.detail)


def test_doctor_is_ok_with_no_flood_state_at_all(live_daemon):
    """The common case: a healthy install that has never been
    rate-limited has no file and nothing to say about it."""
    result = doctor.check_daemon()
    assert result.status == doctor.OK
