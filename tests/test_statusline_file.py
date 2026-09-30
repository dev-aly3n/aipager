"""The shared status-line file reader and the Mini App's context/cost.

The daemon keeps a session's context % and cost in memory only
(``last_token_pct`` / ``last_cost_usd``), set by each ``statusline``
datagram; a restart loses them and a new turn zeroes ``last_token_pct``.
The Mini App used to show those in-memory values, so after a restart every
card read ``0%`` and ``$0.00`` until the next status-line tick. It now
reads the same file ``/status`` reads.

Every file here lives under this test's own tmp dir, set by the local
autouse fixture below on top of conftest's, so these tests can never write
to the real ``/tmp`` even if conftest's isolation breaks (that isolation
has its own test, ``test_statusline_isolation.py``).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from aipager import statusline_file
from aipager.dtach import hook_receiver as hr
from aipager.miniapp.server import MiniAppServer
from aipager.miniapp.sessions import grid_totals, session_detail, session_summary
from aipager.scope import Member, Scope
from aipager.state import SessionRegistry, Status, TrackedSession


@pytest.fixture(autouse=True)
def _own_status_dir(tmp_path, monkeypatch):
    d = tmp_path / "own-status-line"
    d.mkdir()
    monkeypatch.setattr(statusline_file, "STATUS_DIR", str(d))


def _sess(**kwargs) -> TrackedSession:
    defaults = {"name": "claude-jim", "label": "jim", "status": Status.IDLE}
    defaults.update(kwargs)
    return TrackedSession(**defaults)


def _write(name: str, data, *, mtime: float | None = None) -> Path:
    path = statusline_file.status_file_path(name)
    path.write_text(data if isinstance(data, str) else json.dumps(data))
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


_FILE = {
    "context_window": {"used_percentage": 42.4, "total_input_tokens": 900,
                       "total_output_tokens": 100},
    "cost": {"total_cost_usd": 3.14159},
    "model": {"display_name": "Opus 5.5"},
}


# ---- the restart case ------------------------------------------------------

def test_restart_zero_in_memory_with_file_shows_the_files_numbers():
    sess = _sess()  # fresh after a restart: nothing in memory
    assert sess.last_token_pct == 0 and sess.last_cost_usd == 0.0
    _write(sess.name, _FILE)
    row = session_summary(sess, time.monotonic())
    assert row["context_pct"] == 42
    assert row["cost_usd"] == pytest.approx(3.1416)
    assert grid_totals([row])["cost_usd"] == pytest.approx(3.1416)
    detail = session_detail(sess, time.monotonic())
    assert detail["context_pct"] == 42
    assert detail["cost_usd"] == pytest.approx(3.1416)
    # The Compact action is gated on context_pct: a real one enables it.
    assert detail["actions"]["compact"]["available"] is True


def test_restart_model_falls_back_to_the_file_when_none_is_known():
    sess = _sess(model_name="")
    _write(sess.name, _FILE)
    assert session_summary(sess, time.monotonic())["model"] == "Opus 5.5"


def test_known_model_is_kept_over_the_file():
    sess = _sess(model_name="Sonnet 5")
    _write(sess.name, _FILE)
    assert session_summary(sess, time.monotonic())["model"] == "Sonnet 5"


def test_restart_through_the_http_payloads(mk_bot):
    registry = SessionRegistry()
    scope = Scope(chat_id=-100, kind="group", label="team",
                  members=(Member(id=555, label="ada", role="developer"),))
    bot = mk_bot(registry, scopes=[scope])
    for label, pct, cost in (("a", 10, 1.25), ("b", 70, 2.5)):
        sess = registry.get_or_create(f"claude-{label}")
        sess.label = label
        sess.scope_chat_id = -100
        sess.status = Status.IDLE
        _write(sess.name, {"context_window": {"used_percentage": pct},
                           "cost": {"total_cost_usd": cost}})
    server = MiniAppServer(bot, registry, port=8765)

    grid = server._build_sessions_payload(-100)
    by_label = {r["label"]: r for r in grid["sessions"]}
    assert by_label["a"]["context_pct"] == 10
    assert by_label["b"]["cost_usd"] == pytest.approx(2.5)
    assert grid["totals"]["cost_usd"] == pytest.approx(3.75)

    status = server._build_status_payload(-100)
    by_label = {r["label"]: r for r in status["sessions"]}
    assert by_label["a"]["context_pct"] == 10
    assert by_label["b"]["cost_usd"] == pytest.approx(2.5)


# ---- the turn-start case ---------------------------------------------------

@pytest.fixture
def receiver():
    registry = SessionRegistry()
    return registry, hr.HookReceiver(registry, AsyncMock())


def _statusline(recv, run_async, **fields):
    payload = {"type": "statusline", "session": "claude-jim", **fields}
    run_async(recv._on_datagram(json.dumps(payload).encode()))


def test_statusline_event_records_when_and_what(receiver, run_async):
    registry, recv = receiver
    before = time.time()
    _statusline(recv, run_async, context_pct=40, cost_usd=0.5)
    sess = registry.get("claude-jim")
    assert sess.statusline_ctx_pct == 40
    assert sess.statusline_at >= before


def test_turn_start_keeps_the_last_real_numbers_with_the_file(receiver, run_async):
    registry, recv = receiver
    # The hook writes the file, then sends the datagram.
    _write("claude-jim", {"context_window": {"used_percentage": 40},
                          "cost": {"total_cost_usd": 0.5}},
           mtime=time.time() - 1)
    _statusline(recv, run_async, context_pct=40, cost_usd=0.5)
    sess = registry.get("claude-jim")
    sess.status = Status.BUSY
    sess.last_token_pct = 0  # what a new turn's start does (animation.py)
    row = session_summary(sess, time.monotonic())
    assert row["context_pct"] == 40
    assert row["cost_usd"] == pytest.approx(0.5)


def test_turn_start_keeps_the_last_real_numbers_without_a_file(receiver, run_async):
    registry, recv = receiver
    _statusline(recv, run_async, context_pct=40, cost_usd=0.5)
    sess = registry.get("claude-jim")
    sess.status = Status.BUSY
    sess.last_token_pct = 0
    row = session_summary(sess, time.monotonic())
    assert row["context_pct"] == 40
    assert row["cost_usd"] == pytest.approx(0.5)


def test_newer_in_memory_update_beats_an_older_file():
    # e.g. the hook could not write the file for its latest tick.
    sess = _sess(statusline_at=time.time(), statusline_ctx_pct=55,
                 last_cost_usd=2.0)
    _write(sess.name, _FILE, mtime=time.time() - 60)
    row = session_summary(sess, time.monotonic())
    assert row["context_pct"] == 55
    assert row["cost_usd"] == pytest.approx(2.0)


def test_newer_file_beats_an_older_in_memory_update():
    sess = _sess(statusline_at=time.time() - 60, statusline_ctx_pct=55,
                 last_token_pct=55, last_cost_usd=2.0)
    _write(sess.name, _FILE, mtime=time.time())
    row = session_summary(sess, time.monotonic())
    assert row["context_pct"] == 42
    assert row["cost_usd"] == pytest.approx(3.1416)


# ---- missing and malformed files -------------------------------------------

def test_missing_file_uses_the_in_memory_numbers():
    sess = _sess(last_token_pct=12, last_cost_usd=0.75)
    assert statusline_file.read_status_line(sess.name) is None
    row = session_summary(sess, time.monotonic())
    assert row["context_pct"] == 12
    assert row["cost_usd"] == pytest.approx(0.75)


@pytest.mark.parametrize("content", [
    "{not json",
    "",
    "[1, 2, 3]",
    '"just a string"',
    "null",
])
def test_unusable_file_uses_the_in_memory_numbers(content):
    sess = _sess(last_token_pct=12, last_cost_usd=0.75)
    _write(sess.name, content)
    assert statusline_file.read_status_line(sess.name) is None
    row = session_summary(sess, time.monotonic())
    assert row["context_pct"] == 12
    assert row["cost_usd"] == pytest.approx(0.75)


@pytest.mark.parametrize("content", [
    None,  # no file at all
    "{not json",
    b"\xff\xfe{",
    "[1, 2, 3]",
    json.dumps({"context_window": None, "cost": "x", "model": 7}),
    json.dumps({"context_window": {"used_percentage": float("nan")}}),
])
def test_grid_route_answers_with_a_missing_or_malformed_file(
        content, mk_bot, run_async, monkeypatch):
    """What the Mini App actually gets: a 200 grid with the in-memory
    numbers, never a 500, whatever the status-line file holds."""
    from aiohttp.test_utils import TestClient, TestServer

    from tests.test_miniapp_server import BOT_TOKEN, _init_data

    monkeypatch.setattr("aipager.config.BOT_TOKEN", BOT_TOKEN)
    registry = SessionRegistry()
    scope = Scope(chat_id=-100, kind="group", label="team",
                  members=(Member(id=555, label="ada", role="developer"),))
    bot = mk_bot(registry, scopes=[scope])
    bot._app.bot.username = "aipager_test_bot"
    sess = registry.get_or_create("claude-jim")
    sess.label = "jim"
    sess.scope_chat_id = -100
    sess.status = Status.IDLE
    sess.last_token_pct = 12
    sess.last_cost_usd = 0.75
    if isinstance(content, bytes):
        _write(sess.name, "").write_bytes(content)
    elif content is not None:
        _write(sess.name, content)
    server = MiniAppServer(bot, registry, port=8765)

    async def _run():
        client = TestClient(TestServer(server._build_app()))
        await client.start_server()
        try:
            resp = await client.get(
                "/api/sessions", headers={"X-Telegram-Init-Data": _init_data(555)})
            assert resp.status == 200
            body = await resp.json()
        finally:
            await client.close()
        row = body["sessions"][0]
        if content is None or isinstance(content, bytes) or not content.startswith("{\"c"):
            assert (row["context_pct"], row["cost_usd"]) == (12, 0.75)
        else:  # parsed, with the unusable fields read as zero
            assert (row["context_pct"], row["cost_usd"]) == (0, 0.0)
        assert body["totals"]["cost_usd"] == row["cost_usd"]
    run_async(_run())


def test_non_utf8_file_is_unusable():
    sess = _sess(last_token_pct=12)
    _write(sess.name, "").write_bytes(b"\xff\xfe{")
    assert statusline_file.read_status_line(sess.name) is None
    assert session_summary(sess, time.monotonic())["context_pct"] == 12


@pytest.mark.parametrize("data", [
    {"context_window": None, "cost": None, "model": None},
    {"context_window": "x", "cost": [1], "model": 7},
    {"context_window": {"used_percentage": "42", "remaining_percentage": None},
     "cost": {"total_cost_usd": "3"}, "model": {"display_name": None}},
    {"context_window": {"used_percentage": True}, "cost": {"total_cost_usd": False},
     "model": {"display_name": 7}},
    {"context_window": {"used_percentage": float("nan")},
     "cost": {"total_cost_usd": float("nan")}},
])
def test_wrongly_typed_fields_read_as_zero_not_a_crash(data):
    sess = _sess(model_name="")
    _write(sess.name, data)
    sl = statusline_file.read_status_line(sess.name)
    assert sl is not None
    assert (sl.context_pct, sl.cost_usd, sl.model) == (0, 0.0, "")
    row = session_summary(sess, time.monotonic())
    assert (row["context_pct"], row["cost_usd"], row["model"]) == (0, 0.0, "")


def test_remaining_percentage_is_used_when_used_is_missing():
    _write("claude-jim", {"context_window": {"remaining_percentage": 75}})
    assert statusline_file.read_status_line("claude-jim").context_pct == 25


def test_remaining_percentage_is_used_when_used_is_wrongly_typed():
    _write("claude-jim", {"context_window": {"used_percentage": "x",
                                             "remaining_percentage": 75}})
    assert statusline_file.read_status_line("claude-jim").context_pct == 25


# ---- GONE sessions and I/O ---------------------------------------------------

def test_gone_session_ignores_the_file():
    sess = _sess(status=Status.GONE, last_token_pct=0, last_cost_usd=0.0)
    _write(sess.name, _FILE)
    row = session_summary(sess, time.monotonic())
    assert row["status"] == "gone"
    assert row["context_pct"] == 0
    assert row["cost_usd"] == 0.0
    assert row["model"] == ""
    assert grid_totals([row])["cost_usd"] == 0.0


def test_gone_session_keeps_its_in_memory_numbers():
    sess = _sess(status=Status.GONE, last_token_pct=33, last_cost_usd=1.5,
                 statusline_at=time.time() - 60, statusline_ctx_pct=77)
    _write(sess.name, _FILE)
    row = session_summary(sess, time.monotonic())
    assert (row["context_pct"], row["cost_usd"]) == (33, 1.5)


def test_one_file_read_per_row(monkeypatch):
    sess = _sess()
    _write(sess.name, _FILE)
    calls = []
    real = statusline_file.read_raw

    def _counting(name):
        calls.append(name)
        return real(name)

    monkeypatch.setattr(statusline_file, "read_raw", _counting)
    session_summary(sess, time.monotonic())
    assert calls == [sess.name]
    calls.clear()
    session_detail(sess, time.monotonic())
    assert calls == [sess.name]


# ---- the other readers share it --------------------------------------------

def test_chat_status_and_hook_receiver_read_the_same_file(mk_bot):
    _write("claude-jim", _FILE)
    bot = mk_bot()
    out = bot._read_status_file("claude-jim")
    assert out == {"ctx_pct": 42, "cost": pytest.approx(3.14159),
                   "model": "Opus 5.5", "total_output": 100}
    assert hr._read_statusline("claude-jim") == {
        "context_pct": 42, "total_input": 900, "total_output": 100,
        "total_tokens": 1000,
    }


def test_cli_status_reads_the_same_file():
    from aipager import status
    _write("claude-jim", _FILE)
    assert status._read_statusline("claude-jim") == _FILE
    assert status._read_statusline("claude-other") == {}
    _write("claude-bad", "[1]")
    assert status._read_statusline("claude-bad") == {}
