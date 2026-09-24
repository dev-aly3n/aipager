"""The Mini App's "Last message" tracks the live transcript (roadmap 8.34).

On vm3 a live, healthy session showed a week-old
``Please run /login · API Error: 401 …`` as its last message. Two faults
stacked:

* the Mini App rendered ``last_assistant_preview`` — a snapshot taken
  only when a session goes GONE (for the /resume picker) and never
  cleared when the session came back, so it froze at the moment of an
  earlier death and was persisted across restarts;
* ``transcript.last_assistant_preview`` returned Claude Code's synthetic
  API-error entry (``model: "<synthetic>"``, ``isApiErrorMessage: true``)
  as "the last assistant text", so the snapshot itself was the 401.

Fixtures copy the SHAPE of real transcript lines (keys, ``<synthetic>``
model, ``stop_sequence`` stop reason, ``error``/``apiErrorStatus``);
every text in them is invented. Everything lives under ``tmp_path``.
"""

from __future__ import annotations

import json
import os
import time

import pytest

from aipager import transcript as transcript_mod
from aipager.miniapp.sessions import session_detail
from aipager.session_monitor import SessionMonitor
from aipager.state import SessionRegistry, Status, TrackedSession
from aipager.transcript import extract_last_response, last_assistant_preview

API_ERROR_TEXT = (
    "Please run /login · API Error: 401 "
    "{\"type\":\"error\",\"error\":{\"type\":\"authentication_error\"}}"
)


# ── transcript-shaped entries ──────────────────────────────────────────

def _usage() -> dict:
    return {
        "input_tokens": 0, "output_tokens": 0,
        "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
        "server_tool_use": {"web_search_requests": 0, "web_fetch_requests": 0},
        "service_tier": None,
        "cache_creation": {"ephemeral_1h_input_tokens": 0,
                           "ephemeral_5m_input_tokens": 0},
    }


def _base(etype: str, ts: str) -> dict:
    return {
        "parentUuid": "p-uuid", "isSidechain": False, "type": etype,
        "uuid": f"u-{ts}", "timestamp": ts, "userType": "external",
        "entrypoint": "cli", "cwd": "/work", "sessionId": "sid",
        "version": "2.1.259", "gitBranch": "main",
    }


def real_reply(text: str, ts: str = "2026-09-24T10:56:00.000Z") -> dict:
    e = _base("assistant", ts)
    e["requestId"] = "req_x"
    e["message"] = {
        "id": f"msg_{ts}", "type": "message", "role": "assistant",
        "model": "claude-opus-4-6", "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn", "stop_sequence": None, "usage": _usage(),
        "container": None, "context_management": None,
    }
    return e


def tool_call(ts: str = "2026-09-24T10:55:00.000Z") -> dict:
    e = _base("assistant", ts)
    e["message"] = {
        "id": f"msg_{ts}", "type": "message", "role": "assistant",
        "model": "claude-opus-4-6",
        "content": [{"type": "tool_use", "id": "toolu_x", "name": "Bash",
                     "input": {"command": "ls"}}],
        "stop_reason": "tool_use", "stop_sequence": None, "usage": _usage(),
    }
    return e


def tool_result(ts: str = "2026-09-24T10:55:01.000Z") -> dict:
    e = _base("user", ts)
    e["message"] = {"role": "user", "content": [
        {"tool_use_id": "toolu_x", "type": "tool_result", "content": "ok"},
    ]}
    return e


def api_error(ts: str = "2026-09-18T08:00:00.000Z",
              text: str = API_ERROR_TEXT) -> dict:
    """The shape Claude Code writes when a request fails (auth, 5xx, rate
    limit): a synthetic assistant entry no model produced."""
    e = _base("assistant", ts)
    e["message"] = {
        "id": f"msg_{ts}", "container": None, "model": "<synthetic>",
        "role": "assistant", "stop_details": None,
        "stop_reason": "stop_sequence", "stop_sequence": "",
        "type": "message", "usage": _usage(),
        "content": [{"type": "text", "text": text}],
        "context_management": None,
    }
    e["error"] = "authentication_failed"
    e["isApiErrorMessage"] = True
    e["apiErrorStatus"] = 401
    return e


def no_response(ts: str = "2026-09-24T11:00:00.000Z") -> dict:
    """The other synthetic kind: the "No response requested." placeholder."""
    e = api_error(ts, text="No response requested.")
    del e["error"], e["apiErrorStatus"]
    e["isApiErrorMessage"] = False
    return e


def sidecar(ts: str = "2026-09-24T11:00:01.000Z") -> dict:
    return {"type": "last-prompt", "lastPrompt": "x", "sessionId": "sid"}


def write(path, entries) -> str:
    path.write_text("".join(json.dumps(e) + "\n" for e in entries))
    return str(path)


def append(path, entries) -> None:
    with open(path, "a") as fh:
        for e in entries:
            fh.write(json.dumps(e) + "\n")


def _live(registry: SessionRegistry, name: str, tp: str, *,
          status: Status = Status.IDLE, preview: str = "") -> TrackedSession:
    sess = registry.get_or_create(name)
    sess.label = name.removeprefix("claude-")
    sess.status = status
    sess.transcript_path = tp
    sess.last_assistant_preview = preview
    return sess


async def _returning(value):
    return value


async def _noop(*_a, **_kw):
    return None


def _scan_with(monkeypatch, registry, alive: list[str], run_async) -> None:
    monkeypatch.setattr("aipager.dtach.inject.list_sessions",
                        lambda: _returning(alive))
    run_async(SessionMonitor(registry, _noop)._scan())


@pytest.fixture(autouse=True)
def _fresh_preview_cache():
    transcript_mod._preview_cache.clear()
    yield
    transcript_mod._preview_cache.clear()


# ── R2: synthetic entries are never "the last assistant message" ──────

def test_preview_reader_skips_a_trailing_api_error(tmp_path):
    """The reader that produced the vm3 snapshot: it returned the 401."""
    tp = write(tmp_path / "t.jsonl", [
        real_reply("The migration is done."), api_error(),
    ])
    assert last_assistant_preview(tp) == "The migration is done."


def test_preview_reader_skips_the_no_response_placeholder(tmp_path):
    tp = write(tmp_path / "t.jsonl", [
        real_reply("Earlier real answer."), no_response(), sidecar(),
    ])
    assert last_assistant_preview(tp) == "Earlier real answer."


def test_preview_reader_skips_several_synthetic_entries_and_tool_rounds(tmp_path):
    tp = write(tmp_path / "t.jsonl", [
        real_reply("The real one."),
        *[x for _ in range(30) for x in (tool_call(), tool_result())],
        api_error(), api_error(), no_response(), sidecar(),
    ])
    assert transcript_mod.last_real_assistant_text(tp) == "The real one."


def test_transcript_with_only_api_errors_gives_the_empty_state(tmp_path):
    tp = write(tmp_path / "t.jsonl", [api_error(), api_error(), sidecar()])
    assert transcript_mod.last_real_assistant_text(tp) is None
    assert last_assistant_preview(tp) == ""
    registry = SessionRegistry()
    sess = _live(registry, "claude-e", tp)
    assert session_detail(sess, time.monotonic())["last_message"] == ""


def test_turn_summary_reader_still_reports_the_api_error(tmp_path):
    """The Stop/idle-recovery summary path keeps seeing the error: that is
    what raises the error card and its retry button. Skipping it there
    would publish the PREVIOUS turn's answer as this turn's."""
    tp = write(tmp_path / "t.jsonl", [real_reply("Old answer."), api_error()])
    assert extract_last_response(tp) == API_ERROR_TEXT


def test_reader_finds_a_reply_behind_a_long_tail(tmp_path):
    """A reply sitting behind more than one read chunk of tool traffic."""
    big = "y" * 20_000
    entries = [real_reply("Behind the chunk boundary.")]
    for _ in range(20):
        r = tool_result()
        r["message"]["content"][0]["content"] = big
        entries += [tool_call(), r]
    tp = write(tmp_path / "t.jsonl", entries)
    assert transcript_mod.last_real_assistant_text(tp) == "Behind the chunk boundary."


def test_reader_finds_a_reply_that_straddles_a_chunk_boundary(tmp_path):
    """Whatever byte the chunk boundary lands on inside the reply's line,
    the two halves are rejoined."""
    chunk = transcript_mod._TAIL_CHUNK_BYTES
    reply_line = len(json.dumps(real_reply("Straddling reply.")) + "\n")
    base_after = len(json.dumps(tool_result()) + "\n") - len('"ok"') + 2
    hits = 0
    for cut in range(1, reply_line, max(1, reply_line // 12)):
        pad = chunk - cut - base_after
        after = tool_result()
        after["message"]["content"][0]["content"] = "z" * pad
        p = tmp_path / f"s{cut}.jsonl"
        tp = write(p, [tool_call(), real_reply("Straddling reply."), after])
        size = p.stat().st_size
        start = size - len(json.dumps(after) + "\n") - reply_line
        if start < size - chunk < start + reply_line:
            hits += 1
        assert transcript_mod.last_real_assistant_text(tp) == "Straddling reply."
    assert hits >= 5, "fixture no longer puts the boundary inside the reply"


def test_reader_never_scans_past_the_byte_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(transcript_mod, "_TAIL_MAX_BYTES", 128 * 1024,
                        raising=False)
    big = "y" * 10_000
    entries = [real_reply("Too far back.")]
    for _ in range(30):
        r = tool_result()
        r["message"]["content"][0]["content"] = big
        entries += [tool_call(), r]
    tp = write(tmp_path / "t.jsonl", entries)
    assert transcript_mod.last_real_assistant_text(tp) is None


def test_reader_only_takes_text_from_assistant_entries(tmp_path):
    """A non-assistant line after the reply that carries message text
    (and the word "assistant", so it gets past the byte prefilter) is
    not the last reply."""
    other = _base("system", "2026-09-24T11:00:00.000Z")
    other["message"] = {"role": "assistant", "content": [
        {"type": "text", "text": "Not something Claude said."},
    ]}
    tp = write(tmp_path / "t.jsonl", [real_reply("The real reply."), other])
    assert transcript_mod.last_real_assistant_text(tp) == "The real reply."


def test_reader_survives_malformed_assistant_lines(tmp_path):
    """Never raises: the Mini App polls this, and the picker calls it."""
    null_msg = real_reply("x")
    null_msg["message"] = None
    bad_text = real_reply("x")
    bad_text["message"]["content"] = [{"type": "text", "text": 42}]
    bad_content = real_reply("x")
    bad_content["message"]["content"] = 7
    tp = write(tmp_path / "t.jsonl", [
        real_reply("Survivor."), null_msg, bad_text, bad_content,
    ])
    assert transcript_mod.last_real_assistant_text(tp) == "Survivor."
    assert last_assistant_preview(tp) == "Survivor."


def test_reader_handles_lines_longer_than_a_chunk(tmp_path):
    chunk = transcript_mod._TAIL_CHUNK_BYTES
    long_reply = real_reply("L" * (chunk * 3))
    after = tool_result()
    after["message"]["content"][0]["content"] = "z" * (chunk * 2 + 17)
    p = tmp_path / "t.jsonl"
    write(p, [real_reply("Older."), long_reply, after])
    with open(p, "a") as fh:  # a half-written last line, also > a chunk
        fh.write(json.dumps(real_reply("H" * (chunk + 5)))[: chunk + 100])
    assert transcript_mod.last_real_assistant_text(str(p)) == "L" * (chunk * 3)


def test_reader_handles_edge_shaped_files(tmp_path):
    empty = tmp_path / "empty.jsonl"
    empty.write_bytes(b"")
    assert transcript_mod.last_real_assistant_text(str(empty)) is None
    one = tmp_path / "one.jsonl"
    one.write_text(json.dumps(real_reply("Only line.")) + "\n")
    assert transcript_mod.last_real_assistant_text(str(one)) == "Only line."
    # Same rule as read_turn_blocks: a line is a record only once its
    # newline is written, even if its bytes already parse.
    pending = tmp_path / "p.jsonl"
    pending.write_text(json.dumps(real_reply("Committed.")) + "\n"
                       + json.dumps(real_reply("Not yet committed.")))
    assert transcript_mod.last_real_assistant_text(str(pending)) == "Committed."
    unterminated = tmp_path / "u.jsonl"
    unterminated.write_text(json.dumps(real_reply("Still being written.")))
    assert transcript_mod.last_real_assistant_text(str(unterminated)) is None


def test_reader_ignores_a_half_written_last_line(tmp_path):
    p = tmp_path / "t.jsonl"
    write(p, [real_reply("Complete reply.")])
    with open(p, "a") as fh:
        fh.write(json.dumps(real_reply("Half"))[:40])
    assert transcript_mod.last_real_assistant_text(str(p)) == "Complete reply."


# ── (a) live session: the Mini App shows the real reply ───────────────

def test_live_session_whose_last_entry_is_an_api_error_shows_the_real_reply(tmp_path):
    tp = write(tmp_path / "t.jsonl", [
        real_reply("Here is the summary you asked for."), api_error(),
    ])
    registry = SessionRegistry()
    sess = _live(registry, "claude-a", tp)
    detail = session_detail(sess, time.monotonic())
    assert detail["last_message"] == "Here is the summary you asked for."


def test_live_session_ignores_a_stale_snapshot(tmp_path):
    tp = write(tmp_path / "t.jsonl", [real_reply("Fresh reply from today.")])
    registry = SessionRegistry()
    sess = _live(registry, "claude-a", tp, status=Status.BUSY,
                 preview=API_ERROR_TEXT)
    detail = session_detail(sess, time.monotonic())
    assert detail["last_message"] == "Fresh reply from today."


def test_live_session_with_no_transcript_does_not_fall_back_to_the_snapshot():
    registry = SessionRegistry()
    sess = _live(registry, "claude-a", "", preview=API_ERROR_TEXT)
    assert session_detail(sess, time.monotonic())["last_message"] == ""


# ── (b) GONE → live → replies ──────────────────────────────────────────

def test_session_back_from_gone_shows_its_new_reply(tmp_path, monkeypatch, run_async):
    p = tmp_path / "t.jsonl"
    tp = write(p, [real_reply("Reply before it died.")])
    registry = SessionRegistry()
    sess = _live(registry, "claude-b", tp, status=Status.GONE,
                 preview="Reply before it died.")
    sess.gone_at = time.time() - 3600

    _scan_with(monkeypatch, registry, ["claude-b"], run_async)
    assert sess.status == Status.IDLE
    assert sess.last_assistant_preview == ""  # R3: the snapshot is dropped

    append(p, [real_reply("Reply after coming back.", "2026-09-24T12:00:00.000Z")])
    detail = session_detail(sess, time.monotonic())
    assert detail["last_message"] == "Reply after coming back."


def test_every_transition_out_of_gone_drops_the_snapshot():
    """The clear lives in the one gate every path out of GONE passes —
    the monitor, /resume, /new's Replace, a hook — including the IDLE
    debounce, which returns before the rest of transition() runs."""
    for target in (Status.IDLE, Status.BUSY, Status.UNKNOWN):
        registry = SessionRegistry()
        sess = _live(registry, "claude-t", "", status=Status.GONE,
                     preview="Old snapshot.")
        registry.transition("claude-t", target)
        assert sess.last_assistant_preview == "", target
    registry = SessionRegistry()
    sess = _live(registry, "claude-t", "", status=Status.GONE,
                 preview="Old snapshot.")
    sess.last_idle_at = time.monotonic()  # inside the debounce window
    registry.transition("claude-t", Status.IDLE)
    assert sess.status == Status.IDLE
    assert sess.last_assistant_preview == ""


def test_moving_between_live_states_keeps_whatever_is_there():
    registry = SessionRegistry()
    sess = _live(registry, "claude-t", "", status=Status.IDLE, preview="kept")
    registry.transition("claude-t", Status.BUSY)
    assert sess.last_assistant_preview == "kept"


def test_resume_clears_the_snapshot_but_its_recap_still_shows_it(
        monkeypatch, mk_bot, mk_update, run_async):
    """/resume leaves GONE by itself (the monitor never sees it GONE), so
    the clear must happen there too — while the post-resume recap still
    quotes where the session left off."""
    from unittest.mock import MagicMock

    from aipager.dtach import inject

    registry = SessionRegistry()
    sess = _live(registry, "claude-r", "", status=Status.GONE,
                 preview="Where it left off.")
    sess.claude_session_id = "sid-r"
    sess.gone_at = time.time() - 60
    bot = mk_bot(registry)
    monkeypatch.setattr(inject, "launch_session",
                        lambda *a, **kw: _returning((True, "")))
    monkeypatch.setattr(bot, "_build_session_dashboard", lambda s: "<dash>")

    update = mk_update("/resume r")
    run_async(bot._handle_resume_cmd(update, MagicMock()))

    assert sess.status == Status.IDLE
    assert sess.last_assistant_preview == ""
    body = update.message.reply_text.await_args.args[0]
    assert "Where it left off." in body


def test_clearing_the_snapshot_on_return_is_persisted(
        tmp_path, tmp_state_file, monkeypatch, run_async):
    tp = write(tmp_path / "t.jsonl", [real_reply("x")])
    registry = SessionRegistry()
    sess = _live(registry, "claude-b", tp, status=Status.GONE,
                 preview="Old snapshot.")
    sess.gone_at = time.time() - 3600
    registry.save()
    registry._dirty = False

    _scan_with(monkeypatch, registry, ["claude-b"], run_async)
    registry.save_if_dirty()
    saved = json.loads(tmp_state_file.read_text())["sessions"]["claude-b"]
    assert saved["last_assistant_preview"] == ""


# ── (c) the vm3 case: stale snapshot persisted on a live session ──────

def test_persisted_stale_snapshot_on_a_live_session_is_healed(
        tmp_path, tmp_state_file, monkeypatch, run_async):
    tp = write(tmp_path / "t.jsonl", [
        api_error(), real_reply("Latest real reply on the new token."),
    ])
    tmp_state_file.write_text(json.dumps({"sessions": {
        "claude-catfish": {
            "name": "claude-catfish", "label": "catfish",
            "transcript_path": tp, "claude_session_id": "sid",
            "cwd": "/work", "gone_at": None,
            "last_assistant_preview": API_ERROR_TEXT,
        },
    }}))
    registry = SessionRegistry()
    registry.load()
    sess = registry.get("claude-catfish")
    assert sess.last_assistant_preview == ""  # R4: ignored at load

    _scan_with(monkeypatch, registry, ["claude-catfish"], run_async)
    assert sess.status == Status.IDLE
    assert sess.last_assistant_preview == ""
    detail = session_detail(sess, time.monotonic())
    assert detail["last_message"] == "Latest real reply on the new token."
    assert "401" not in json.dumps(detail)


def test_load_keeps_the_snapshot_of_a_gone_session(tmp_path, tmp_state_file):
    tmp_state_file.write_text(json.dumps({"sessions": {
        "claude-old": {
            "name": "claude-old", "label": "old", "claude_session_id": "sid",
            "gone_at": time.time() - 60,
            "last_assistant_preview": "Where I left off.",
        },
    }}))
    registry = SessionRegistry()
    registry.load()
    sess = registry.get("claude-old")
    assert sess.status == Status.GONE
    assert sess.last_assistant_preview == "Where I left off."


# ── (d) GONE keeps its snapshot, and the snapshot is never an API error ─

def test_gone_snapshot_skips_the_api_error_and_the_mini_app_shows_it(
        tmp_path, monkeypatch, run_async):
    tp = write(tmp_path / "t.jsonl", [
        real_reply("Last real words before the token died."), api_error(),
    ])
    registry = SessionRegistry()
    sess = _live(registry, "claude-d", tp)

    _scan_with(monkeypatch, registry, [], run_async)
    assert sess.status == Status.GONE
    assert sess.last_assistant_preview == "Last real words before the token died."

    # The GONE page reads the snapshot, not the transcript: moving the
    # file away must not change what it shows.
    (tmp_path / "t.jsonl").unlink()
    detail = session_detail(sess, time.monotonic())
    assert detail["last_message"] == "Last real words before the token died."


def test_gone_session_without_a_snapshot_reads_its_transcript(tmp_path):
    """Claude's own SessionEnd hook (every /exit) sends a session GONE
    without a snapshot — show what the picker and the recap would."""
    tp = write(tmp_path / "t.jsonl", [real_reply("Said before /exit."), api_error()])
    registry = SessionRegistry()
    sess = _live(registry, "claude-x", tp, status=Status.GONE, preview="")
    assert session_detail(sess, time.monotonic())["last_message"] == "Said before /exit."


# ── (f) the cache ──────────────────────────────────────────────────────

def test_repeated_mini_app_reads_do_not_reread_an_unchanged_transcript(
        tmp_path, monkeypatch):
    p = tmp_path / "t.jsonl"
    tp = write(p, [real_reply("First.")])
    calls: list[str] = []
    real = getattr(transcript_mod, "last_real_assistant_text", None)

    def counting(path):
        calls.append(path)
        return real(path)
    monkeypatch.setattr(transcript_mod, "last_real_assistant_text", counting,
                        raising=False)

    registry = SessionRegistry()
    sess = _live(registry, "claude-f", tp)
    for _ in range(5):
        assert session_detail(sess, time.monotonic())["last_message"] == "First."
    assert len(calls) == 1

    append(p, [real_reply("Second.", "2026-09-24T12:00:00.000Z")])
    for _ in range(3):
        assert session_detail(sess, time.monotonic())["last_message"] == "Second."
    assert len(calls) == 2


def test_cache_notices_a_same_size_rewrite(tmp_path):
    p = tmp_path / "t.jsonl"
    tp = write(p, [real_reply("Reply AAAA.")])
    assert transcript_mod.cached_last_assistant_preview(tp) == "Reply AAAA."
    st = p.stat()
    write(p, [real_reply("Reply BBBB.")])
    assert p.stat().st_size == st.st_size
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    assert transcript_mod.cached_last_assistant_preview(tp) == "Reply BBBB."


def test_cache_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(transcript_mod, "_PREVIEW_CACHE_MAX", 4, raising=False)
    transcript_mod._preview_cache.clear()
    for i in range(10):
        tp = write(tmp_path / f"t{i}.jsonl", [real_reply(f"r{i}")])
        assert transcript_mod.cached_last_assistant_preview(tp) == f"r{i}"
    assert len(transcript_mod._preview_cache) <= 4


# ── (g) /resume picker and /new collision still show a preview ────────

def test_resume_picker_and_new_collision_show_the_real_reply(
        tmp_path, monkeypatch, run_async, mk_bot, mk_update):
    from unittest.mock import MagicMock

    tp = write(tmp_path / "t.jsonl", [
        real_reply("Picker should show this."), api_error(),
    ])
    registry = SessionRegistry()
    sess = _live(registry, "claude-g", tp)
    sess.claude_session_id = "sid-g"
    sess.cwd = "/work"
    _scan_with(monkeypatch, registry, [], run_async)
    assert sess.status == Status.GONE

    bot = mk_bot(registry)
    text, _kb = bot._render_resume_picker(page=0)
    assert "Picker should show this." in text
    assert "401" not in text

    update = mk_update("/new g")
    run_async(bot._handle_new_cmd(update, MagicMock()))
    reply = update.message.reply_text.await_args.args[0]
    assert "Picker should show this." in reply
    assert "401" not in reply
