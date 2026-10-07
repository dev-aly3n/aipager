"""Black-box rows for roadmap 8.102: every case where the saved prompt must
NOT come back (design.md success criteria 6, 7, 8, 9) and the unchanged
8.101 adoption of a turn that is not at a prompt (criterion 16).
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
from datetime import datetime, timezone

import pytest

from aipager import preferences as prefs
from aipager import state
from aipager.state import Status

CHAT = 256113222
NAME = "claude-rppbb_harness"
LABEL = "rppbb"
ADOPTED = re.compile(
    r"^\[rppbb\] busy card (\d+) adopted — the turn looks still running; "
    r"working again, .+")


@pytest.fixture(autouse=True)
def _card_layout():
    prefs.set_preference(CHAT, "layout", "card")


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, tz=timezone.utc).isoformat().replace(
        "+00:00", "Z")


def _run(vloop, coro):
    return vloop.run_until_complete(coro)


def _dead(tmp_path) -> str:
    return str(tmp_path / "gone.sock")


def _user_prompt(r, at: float) -> None:
    """This turn's own prompt line, written before the dialog opened."""
    r.append({"type": "user", "timestamp": _iso(at), "message": {
        "role": "user", "content": "[via Telegram · @owner]\nclean up"}})


def _drops(caplog) -> list[str]:
    return [m for m in caplog.messages
            if m.startswith(f"[{LABEL}] saved permission prompt")]


async def _down_then(r, tmp_path, while_down, *, live: bool = True,
                     before_recover=None):
    """An inline prompt; the daemon goes down; *while_down(r)* happens
    (e.g. the terminal answers it); the daemon comes back."""
    _user_prompt(r, r.wall())
    card = await r.inline_prompt(reply_addr=_dead(tmp_path))
    await r.stop_process()
    await asyncio.sleep(10)
    if while_down is not None:
        while_down(r)
    await asyncio.sleep(10)
    if not live:
        r.pty.alive.clear()
    r.start_process()
    if before_recover is not None:
        await before_recover(r)
    await r.bot.recover_sessions()
    await asyncio.sleep(2)
    return card


def _tool_result(at_offset: float, tool_use_id: str = "toolu_01"):
    def _write(r):
        r.append({"type": "user", "timestamp": _iso(r.wall() + at_offset),
                  "message": {"role": "user", "content": [{
                      "type": "tool_result", "tool_use_id": tool_use_id,
                      "content": "removed"}]}})
    return _write


# ── SC6: answered in the terminal while the daemon was down ──────────────

def test_tool_result_for_saved_id_is_not_restored(replay, vloop, tmp_path):
    r = replay
    _run(vloop, _down_then(r, tmp_path, _tool_result(0)))
    assert r.sess.status is Status.BUSY


def test_tool_result_for_saved_id_logs_answered(replay, vloop, tmp_path, caplog):
    r = replay
    with caplog.at_level(logging.INFO):
        _run(vloop, _down_then(r, tmp_path, _tool_result(0)))
    assert _drops(caplog) == [
        f"[{LABEL}] saved permission prompt dropped - answered in the "
        "transcript"]


def test_tool_result_dated_before_the_prompt_still_counts(
        replay, vloop, tmp_path, caplog):
    """A tool_result for the saved id answers it whatever its timestamp."""
    r = replay
    with caplog.at_level(logging.INFO):
        _run(vloop, _down_then(r, tmp_path, _tool_result(-3600)))
    assert r.sess.status is not Status.INTERACTIVE


def test_answered_card_is_adopted_with_the_8101_line(
        replay, vloop, tmp_path, caplog):
    r = replay
    with caplog.at_level(logging.INFO):
        card = _run(vloop, _down_then(r, tmp_path, _tool_result(0)))
    adopted = [m for m in caplog.messages if ADOPTED.match(m)]
    assert [int(ADOPTED.match(m).group(1)) for m in adopted] == [card]


def test_answered_card_keeps_ticking(replay, vloop, tmp_path):
    r = replay
    _run(vloop, _down_then(r, tmp_path, _tool_result(0)))
    assert r.sess.animation_running() is True


def test_answered_prompt_is_gone_from_the_next_save(replay, vloop, tmp_path):
    r = replay
    _run(vloop, _down_then(r, tmp_path, _tool_result(0)))
    r.bot.registry.save()
    assert "open_prompt" not in r.saved_entry()


def test_answered_restart_types_no_keys(replay, vloop, tmp_path):
    r = replay
    _run(vloop, _down_then(r, tmp_path, _tool_result(0)))
    assert r.pty.keys == []


@pytest.mark.parametrize("entry", [
    {"type": "assistant", "message": {
        "id": "m2", "role": "assistant", "stop_reason": "tool_use",
        "content": [{"type": "text", "text": "done, next step"}]}},
    {"type": "assistant", "message": {
        "id": "m3", "role": "assistant", "stop_reason": "tool_use",
        "content": [{"type": "tool_use", "id": "toolu_99", "name": "Bash",
                     "input": {"command": "ls"}}]}},
    {"type": "user", "message": {"role": "user", "content": "a new prompt"}},
    {"type": "system", "subtype": "turn_duration", "durationMs": 1},
    {"type": "system", "subtype": "stop_hook_summary"},
], ids=["assistant-text", "other-tool-use", "user-prompt", "turn-duration",
        "stop-hook-summary"])
def test_turn_line_after_the_prompt_is_not_restored(
        replay, vloop, tmp_path, caplog, entry):
    r = replay

    def _write(r):
        r.append(dict(entry, timestamp=_iso(r.wall())))

    with caplog.at_level(logging.INFO):
        _run(vloop, _down_then(r, tmp_path, _write))
    assert _drops(caplog) == [
        f"[{LABEL}] saved permission prompt dropped - transcript moved on"]


@pytest.mark.parametrize("entry", [
    {"type": "queue-operation", "operation": "enqueue", "content": "more"},
    {"type": "system", "subtype": "compact_boundary"},
    {"type": "file-history-snapshot", "snapshot": {}},
    {"type": "summary", "summary": "a summary"},
], ids=["queue-operation", "other-system", "file-history", "summary"])
def test_sidecar_line_after_the_prompt_still_restores(
        replay, vloop, tmp_path, entry):
    r = replay

    def _write(r):
        r.append(dict(entry, timestamp=_iso(r.wall())))

    _run(vloop, _down_then(r, tmp_path, _write))
    assert r.sess.status is Status.INTERACTIVE


async def _user_line_around_the_prompt(r, tmp_path, offset: float):
    """A user line dated *offset* seconds from when the prompt was shown
    (the PermissionRequest is handled 1.5 s into the turn)."""
    t0 = r.wall()
    _user_prompt(r, t0)
    await r.inline_prompt(reply_addr=_dead(tmp_path))
    r.append({"type": "user", "timestamp": _iso(t0 + 1.5 + offset),
              "message": {"role": "user", "content": "a user line"}})
    await r.restart()


def test_user_line_just_before_the_prompt_still_restores(
        replay, vloop, tmp_path):
    """Boundary: a turn line dated before the prompt was shown is no
    evidence it was answered."""
    r = replay
    _run(vloop, _user_line_around_the_prompt(r, tmp_path, -0.5))
    assert r.sess.status is Status.INTERACTIVE


def test_user_line_just_after_the_prompt_is_not_restored(
        replay, vloop, tmp_path):
    r = replay
    _run(vloop, _user_line_around_the_prompt(r, tmp_path, +0.5))
    assert r.sess.status is not Status.INTERACTIVE


# ── SC7: the session is not alive ────────────────────────────────────────

def test_dead_session_prompt_is_not_restored(replay, vloop, tmp_path):
    r = replay
    _run(vloop, _down_then(r, tmp_path, None, live=False))
    assert r.sess.status is not Status.INTERACTIVE


def test_dead_session_prompt_logs_not_alive(replay, vloop, tmp_path, caplog):
    r = replay
    with caplog.at_level(logging.INFO):
        _run(vloop, _down_then(r, tmp_path, None, live=False))
    assert _drops(caplog) == [
        f"[{LABEL}] saved permission prompt dropped - session not alive"]


def test_dead_session_card_is_closed(replay, vloop, tmp_path):
    r = replay
    _run(vloop, _down_then(r, tmp_path, None, live=False))
    assert r.chat.live_cards() == []


def test_dead_session_prompt_types_no_keys_on_a_late_tap(
        replay, vloop, tmp_path):
    r = replay

    async def scenario():
        card = await _down_then(r, tmp_path, None, live=False)
        await r.tap(card, data=f"{NAME}:allow")
        await asyncio.sleep(1)

    _run(vloop, scenario())
    assert r.pty.keys == []


# ── SC8: a hook handled before recover_sessions wins ─────────────────────

async def _pre_tool(r):
    r.pre_tool(tool_use_id="toolu_02", tool_input={"command": "ls"})
    await asyncio.sleep(0.5)


async def _stop(r):
    r.stop("the terminal answered it")
    await asyncio.sleep(0.5)


async def _newer_prompt(r):
    r.pre_tool("Write", {"file_path": "/x/newer.txt", "content": "x"},
               tool_use_id="toolu_03")
    await asyncio.sleep(0.5)
    r.permission("Write", {"file_path": "/x/newer.txt", "content": "x"})
    await asyncio.sleep(0.5)


@pytest.mark.parametrize("hook", [_pre_tool, _stop, _newer_prompt],
                         ids=["PreToolUse", "Stop", "PermissionRequest"])
def test_hook_before_recovery_logs_already_moved(
        replay, vloop, tmp_path, caplog, hook):
    r = replay
    with caplog.at_level(logging.INFO):
        _run(vloop, _down_then(r, tmp_path, None, before_recover=hook))
    assert _drops(caplog) == [
        f"[{LABEL}] saved permission prompt dropped - session already moved"]


def test_pre_tool_before_recovery_leaves_the_session_busy(
        replay, vloop, tmp_path):
    r = replay
    _run(vloop, _down_then(r, tmp_path, None, before_recover=_pre_tool))
    assert r.sess.status is Status.BUSY


def test_stop_before_recovery_leaves_the_session_idle(replay, vloop, tmp_path):
    r = replay
    _run(vloop, _down_then(r, tmp_path, None, before_recover=_stop))
    assert r.sess.status is Status.IDLE


def test_newer_prompt_before_recovery_is_not_painted_over(
        replay, vloop, tmp_path):
    r = replay

    async def scenario():
        await _down_then(r, tmp_path, None, before_recover=_newer_prompt)
        await asyncio.sleep(5)

    _run(vloop, scenario())
    text, _kb = r.bot._render_pinned(CHAT)
    assert "newer.txt" in text, text


# ── SC9: a corrupt or partial saved record ───────────────────────────────

def _set(path: str, value):
    def _edit(rec):
        node = rec
        keys = path.split(".")
        for k in keys[:-1]:
            node = node[k]
        node[keys[-1]] = value
        return rec
    return _edit


def _drop(path: str):
    def _edit(rec):
        node = rec
        keys = path.split(".")
        for k in keys[:-1]:
            node = node[k]
        del node[keys[-1]]
        return rec
    return _edit


def _whole(value):
    return lambda _rec: value


_INVALID = {
    "v-2": _set("v", 2),
    "v-str": _set("v", "1"),
    "v-bool": _set("v", True),
    "no-v": _drop("v"),
    "no-shown_wall": _drop("shown_wall"),
    "shown_wall-future": lambda rec: _set("shown_wall", 2e9)(rec),
    "shown_wall-nan": _set("shown_wall", math.nan),
    "shown_wall-inf": _set("shown_wall", math.inf),
    "shown_wall-zero": _set("shown_wall", 0.0),
    "shown_wall-negative": _set("shown_wall", -1.0),
    "shown_wall-str": _set("shown_wall", "now"),
    "kind-unknown": _set("kind", "popup"),
    "no-kind": _drop("kind"),
    "card_msg_id-other-card": lambda rec: _set(
        "card_msg_id", rec["card_msg_id"] + 1)(rec),
    "card_msg_id-bool": _set("card_msg_id", True),
    "card_msg_id-str": lambda rec: _set(
        "card_msg_id", str(rec["card_msg_id"]))(rec),
    "card_msg_id-zero": _set("card_msg_id", 0),
    "no-card_msg_id": _drop("card_msg_id"),
    "tool_use_id-int": _set("tool_use_id", 5),
    "tool_use_id-201": _set("tool_use_id", "t" * 201),
    "no-perm": _drop("perm"),
    "perm-null": _set("perm", None),
    "perm-list": _set("perm", []),
    "perm-missing-detail": _drop("perm.detail"),
    "perm-extra-input": _set("perm.input", {"command": "rm -rf build"}),
    "tool_name-empty": _set("perm.tool_name", ""),
    "tool_name-201": _set("perm.tool_name", "B" * 201),
    "tool_name-int": _set("perm.tool_name", 7),
    "tool_summary-1001": _set("perm.tool_summary", "s" * 1001),
    "detail-4001": _set("perm.detail", "d" * 4001),
    "always_available-str": _set("perm.always_available", "yes"),
    "always_available-int": _set("perm.always_available", 1),
    "suggestion-setMode": _set("perm.standing_rule_suggestion",
                               {"type": "setMode", "mode": "acceptEdits"}),
    "suggestion-str": _set("perm.standing_rule_suggestion", "addRules"),
    "hook_reply-extra-key": _set("perm.hook_reply.pid", 1),
    "hook_reply-no-request_id": _drop("perm.hook_reply.request_id"),
    "hook_reply-empty-addr": _set("perm.hook_reply.addr", ""),
    "hook_reply-addr-108": _set("perm.hook_reply.addr", "/" + "a" * 107),
    "hook_reply-str": _set("perm.hook_reply", "/x.sock"),
    "question-on-a-tool": _set("perm.question", {
        "question": "Which?", "options": [{"label": "A", "description": ""}]}),
    "record-str": _whole("garbage"),
    "record-list": _whole([1, 2]),
    "record-int": _whole(42),
    "record-empty": _whole({}),
}

_VALID_BOUNDARIES = {
    "tool_use_id-empty": _set("tool_use_id", ""),
    "tool_use_id-200": _set("tool_use_id", "t" * 200),
    "tool_summary-1000": _set("perm.tool_summary", "s" * 1000),
    "detail-4000": _set("perm.detail", "d" * 4000),
    "hook_reply-null": _set("perm.hook_reply", None),
    "hook_reply-addr-107": _set("perm.hook_reply.addr", "/" + "a" * 106),
    "always_available-null": _set("perm.always_available", None),
    "always_available-true": _set("perm.always_available", True),
    "suggestion-addRules": _set("perm.standing_rule_suggestion", {
        "type": "addRules", "rules": [{"toolName": "Bash"}],
        "behavior": "allow", "destination": "localSettings"}),
    "suggestion-addDirectories": _set("perm.standing_rule_suggestion", {
        "type": "addDirectories", "directories": ["/x"],
        "destination": "session"}),
}


async def _edited_restart(r, tmp_path, edit):
    _user_prompt(r, r.wall())
    card = await r.inline_prompt(reply_addr=_dead(tmp_path))
    r.old_allow = r.button(card, "allow")
    await r.stop_process()
    data = json.loads(state.SESSION_STATE_FILE.read_text())
    entry = data["sessions"][NAME]
    assert isinstance(entry.get("open_prompt"), dict), entry.keys()
    entry["open_prompt"] = edit(entry["open_prompt"])
    state.SESSION_STATE_FILE.write_text(json.dumps(data))
    await asyncio.sleep(30)
    r.start_process()
    await r.bot.recover_sessions()
    await asyncio.sleep(2)
    return card


@pytest.mark.parametrize("edit", list(_INVALID.values()), ids=list(_INVALID))
def test_invalid_record_is_not_restored(replay, vloop, tmp_path, edit):
    r = replay
    _run(vloop, _edited_restart(r, tmp_path, edit))
    assert r.sess.status is not Status.INTERACTIVE


@pytest.mark.parametrize("edit", list(_INVALID.values()), ids=list(_INVALID))
def test_invalid_record_logs_invalid_record(replay, vloop, tmp_path, caplog,
                                            edit):
    r = replay
    with caplog.at_level(logging.INFO):
        _run(vloop, _edited_restart(r, tmp_path, edit))
    assert (f"[{LABEL}] saved permission prompt ignored - invalid record"
            in caplog.messages), _drops(caplog)


@pytest.mark.parametrize("edit", [_INVALID["v-2"], _INVALID["record-str"],
                                  _INVALID["card_msg_id-other-card"]],
                         ids=["v-2", "record-str", "card_msg_id-other-card"])
def test_invalid_record_session_loads_as_today(replay, vloop, tmp_path, caplog,
                                               edit):
    """Fail closed to 8.101: the running turn's card is adopted."""
    r = replay
    with caplog.at_level(logging.INFO):
        _run(vloop, _edited_restart(r, tmp_path, edit))
    assert any(ADOPTED.match(m) for m in caplog.messages), \
        [m for m in caplog.messages if LABEL in m]


def test_invalid_record_old_allow_types_no_keys(replay, vloop, tmp_path):
    """Fail closed: the old prompt's Allow, tapped after its record was
    thrown away, answers nothing."""
    r = replay

    async def scenario():
        card = await _edited_restart(r, tmp_path, _INVALID["v-2"])
        await r.tap(card, data=r.old_allow)
        await asyncio.sleep(1)

    _run(vloop, scenario())
    assert r.pty.keys == []


@pytest.mark.parametrize("edit", list(_VALID_BOUNDARIES.values()),
                         ids=list(_VALID_BOUNDARIES))
def test_valid_boundary_record_is_restored(replay, vloop, tmp_path, edit):
    r = replay
    _run(vloop, _edited_restart(r, tmp_path, edit))
    assert r.sess.status is Status.INTERACTIVE


def test_invalid_json_line_in_transcript_does_not_break_restore(
        replay, vloop, tmp_path):
    """Error guessing: a torn last line (a partial write) is not evidence."""
    r = replay

    async def scenario():
        _user_prompt(r, r.wall())
        await r.inline_prompt(reply_addr=_dead(tmp_path))
        with open(r.transcript, "a", encoding="utf-8") as fh:
            fh.write('{"type": "assistant", "message": {"id"')
        await r.restart()

    _run(vloop, scenario())
    assert r.sess.status is Status.INTERACTIVE


# ── an unreadable transcript fails closed ────────────────────────────────

def test_unreadable_transcript_is_not_restored(replay, vloop, tmp_path, caplog):
    r = replay

    def _make_dir(r):
        os.unlink(r.transcript)
        os.mkdir(r.transcript)

    with caplog.at_level(logging.INFO):
        _run(vloop, _down_then(r, tmp_path, _make_dir))
    assert _drops(caplog) == [
        f"[{LABEL}] saved permission prompt dropped - transcript unreadable"]


# ── SC16: a restart outside a prompt is 8.101's, unchanged ───────────────

async def _busy_restart(r):
    _user_prompt(r, r.wall())
    await r.card_turn()
    r.pre_tool(tool_use_id="toolu_01", tool_input={"command": "make"})
    await asyncio.sleep(1)
    r.post_tool(tool_use_id="toolu_01", tool_input={"command": "make"})
    await asyncio.sleep(10)
    card = r.sess.busy_msg_id
    await r.stop_process()
    saved = r.saved_entry()
    await asyncio.sleep(30)
    r.start_process()
    await r.bot.recover_sessions()
    return card, saved


def test_busy_restart_saves_no_prompt(replay, vloop):
    r = replay
    _card, saved = _run(vloop, _busy_restart(r))
    assert "open_prompt" not in saved


def test_busy_restart_logs_the_adopted_line_byte_identical(
        replay, vloop, caplog):
    r = replay
    with caplog.at_level(logging.INFO):
        card, _saved = _run(vloop, _busy_restart(r))
    prefix = (f"[{LABEL}] busy card {card} adopted — the turn looks still "
              "running; working again, ")
    assert sum(1 for m in caplog.messages if m.startswith(prefix)) == 1, \
        [m for m in caplog.messages if LABEL in m]


def test_busy_restart_is_busy(replay, vloop):
    r = replay
    _run(vloop, _busy_restart(r))
    assert r.sess.status is Status.BUSY


def test_busy_restart_logs_no_prompt_lines(replay, vloop, caplog):
    r = replay
    with caplog.at_level(logging.INFO):
        _run(vloop, _busy_restart(r))
    assert not any("permission prompt" in m for m in caplog.messages)


def test_busy_restart_answer_lands_without_a_fresh_card(replay, vloop):
    r = replay

    async def scenario():
        card, _saved = await _busy_restart(r)
        before = set(r.chat.cards)
        r.stop("made it")
        await asyncio.sleep(20)
        return card, before

    _card, before = _run(vloop, scenario())
    assert set(r.chat.cards) == before and any(
        "made it" in t for t in r.chat.sent_texts())
