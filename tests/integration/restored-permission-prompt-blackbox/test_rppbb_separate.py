"""Black-box rows for roadmap 8.102: a restart while a separate prompt
message (8.99, no busy card) waits (design.md success criteria 3, 7, 8 for
the separate surface, and 10).
"""

from __future__ import annotations

import asyncio
import json
import logging

import pytest

from aipager import audit as audit_mod
from aipager import preferences as prefs
from aipager import state
from aipager.state import Status

CHAT = 256113222
OTHER_CHAT = 256113999
NAME = "claude-rppbb_harness"
LABEL = "rppbb"


@pytest.fixture(autouse=True)
def _card_layout():
    prefs.set_preference(CHAT, "layout", "card")


def _run(vloop, coro):
    return vloop.run_until_complete(coro)


def _dead(tmp_path) -> str:
    return str(tmp_path / "gone.sock")


def _audit() -> list[dict]:
    path = audit_mod.AUDIT_LOG_PATH
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


async def _restored_separate(r, tmp_path) -> int:
    msg = await r.separate_prompt(reply_addr=_dead(tmp_path))
    await r.restart()
    return msg


# ── SC3: restored, and its own message answers it ────────────────────────

def test_restored_separate_prompt_is_interactive(replay, vloop, tmp_path):
    r = replay
    _run(vloop, _restored_separate(r, tmp_path))
    assert r.sess.status is Status.INTERACTIVE


def test_restored_separate_prompt_logs_the_restored_line(
        replay, vloop, tmp_path, caplog):
    r = replay
    with caplog.at_level(logging.INFO):
        msg = _run(vloop, _restored_separate(r, tmp_path))
    assert (f"[{LABEL}] permission prompt restored after restart - waiting "
            f"for an answer (separate msg {msg})") in caplog.messages


def test_restored_separate_prompt_keeps_its_buttons(replay, vloop, tmp_path):
    r = replay
    msg = _run(vloop, _restored_separate(r, tmp_path))
    assert msg not in r.chat.markup_removed


def test_restored_separate_prompt_pinned_bar_says_needs_you(
        replay, vloop, tmp_path):
    r = replay
    _run(vloop, _restored_separate(r, tmp_path))
    text, _kb = r.bot._render_pinned(CHAT)
    assert "needs you" in text


async def _allow_separate(r, tmp_path):
    msg = await _restored_separate(r, tmp_path)
    sent_before = len(r.chat.sent)
    keys_before = list(r.pty.keys)
    toast = await r.tap(msg, "allow")
    await asyncio.sleep(3)
    return msg, sent_before, keys_before, toast


def test_allow_on_restored_separate_prompt_types_enter(replay, vloop, tmp_path):
    r = replay
    _msg, _s, before, _t = _run(vloop, _allow_separate(r, tmp_path))
    assert r.pty.keys[len(before):] == ["Enter"]


def test_allow_on_restored_separate_prompt_posts_the_attributed_line(
        replay, vloop, tmp_path):
    r = replay
    msg, sent_before, _k, _t = _run(vloop, _allow_separate(r, tmp_path))
    lines = [(t, reply) for _m, t, reply in r.chat.sent[sent_before:]]
    assert any("Allowed by @owner" in t and reply == msg for t, reply in lines), \
        lines


def test_allow_on_restored_separate_prompt_edits_the_message(
        replay, vloop, tmp_path):
    r = replay
    _run(vloop, _allow_separate(r, tmp_path))
    edits = [c.args[0] if c.args else c.kwargs.get("text", "")
             for c in r.last_query.edit_message_text.await_args_list]
    assert any("→ Allowed" in e for e in edits), edits


def test_allow_on_restored_separate_prompt_makes_the_session_busy(
        replay, vloop, tmp_path):
    r = replay
    _run(vloop, _allow_separate(r, tmp_path))
    assert r.sess.status is Status.BUSY


def test_allow_on_restored_separate_prompt_is_audited(replay, vloop, tmp_path):
    r = replay
    _run(vloop, _allow_separate(r, tmp_path))
    rec = _audit()[-1]
    assert (rec["action"], rec["via"], rec["username"]) == (
        "Allowed", "keystroke_fallback", "owner")


def test_deny_on_restored_separate_prompt_types_five_downs_then_enter(
        replay, vloop, tmp_path):
    r = replay

    async def scenario():
        msg = await _restored_separate(r, tmp_path)
        before = list(r.pty.keys)
        await r.tap(msg, "deny")
        await asyncio.sleep(1)
        return before

    before = _run(vloop, scenario())
    assert r.pty.keys[len(before):] == ["Down"] * 5 + ["Enter"]


def test_deny_on_restored_separate_prompt_is_audited_denied(
        replay, vloop, tmp_path):
    r = replay

    async def scenario():
        msg = await _restored_separate(r, tmp_path)
        await r.tap(msg, "deny")
        await asyncio.sleep(1)

    _run(vloop, scenario())
    rec = _audit()[-1]
    assert (rec["denied"], rec["via"]) == (True, "keystroke_fallback")


# ── SC10: any other message is refused while the restored prompt waits ───

async def _tap_other(r, tmp_path, *, data_from_prompt: bool):
    msg = await _restored_separate(r, tmp_path)
    other = r.chat.new_id()  # e.g. a bar copy re-sent before the restart
    data = (r.button(msg, "allow") if data_from_prompt
            else f"{NAME}:allow")
    before = list(r.pty.keys)
    toast = await r.tap(other, data=data)
    await asyncio.sleep(1)
    return toast, before


@pytest.mark.parametrize("data_from_prompt", [True, False],
                         ids=["short-index-data", "long-name-data"])
def test_other_message_tap_is_refused_as_expired(
        replay, vloop, tmp_path, data_from_prompt):
    r = replay
    toast, _b = _run(vloop, _tap_other(r, tmp_path,
                                       data_from_prompt=data_from_prompt))
    assert toast and "this prompt has expired" in toast


@pytest.mark.parametrize("data_from_prompt", [True, False],
                         ids=["short-index-data", "long-name-data"])
def test_other_message_tap_types_no_keys(replay, vloop, tmp_path,
                                         data_from_prompt):
    r = replay
    _t, before = _run(vloop, _tap_other(r, tmp_path,
                                        data_from_prompt=data_from_prompt))
    assert r.pty.keys[len(before):] == []


def test_other_message_tap_leaves_the_prompt_waiting(replay, vloop, tmp_path):
    r = replay
    _run(vloop, _tap_other(r, tmp_path, data_from_prompt=True))
    assert r.sess.status is Status.INTERACTIVE


def test_other_message_tap_removes_that_messages_keyboard(
        replay, vloop, tmp_path):
    r = replay

    async def scenario():
        await _tap_other(r, tmp_path, data_from_prompt=True)
        return r.last_query

    q = _run(vloop, scenario())
    removed = (q.edit_message_reply_markup.await_count
               or q.edit_message_text.await_count)
    assert removed


def test_second_tap_on_restored_separate_prompt_types_nothing(
        replay, vloop, tmp_path):
    r = replay

    async def scenario():
        msg = await _restored_separate(r, tmp_path)
        deny = r.button(msg, "deny")
        await r.tap(msg, "allow")
        await asyncio.sleep(1)
        before = list(r.pty.keys)
        await r.tap(msg, data=deny)
        await asyncio.sleep(1)
        return before

    before = _run(vloop, scenario())
    assert r.pty.keys[len(before):] == []


# ── dropped separate prompts lose their keyboard ─────────────────────────

def test_dead_session_separate_prompt_is_not_restored(replay, vloop, tmp_path):
    r = replay

    async def scenario():
        msg = await r.separate_prompt(reply_addr=_dead(tmp_path))
        r.pty.alive.clear()
        await r.restart()
        return msg

    _run(vloop, scenario())
    assert r.sess.status is not Status.INTERACTIVE


def test_dead_session_separate_prompt_loses_its_keyboard(
        replay, vloop, tmp_path):
    r = replay

    async def scenario():
        msg = await r.separate_prompt(reply_addr=_dead(tmp_path))
        r.pty.alive.clear()
        await r.restart()
        await asyncio.sleep(5)
        return msg

    msg = _run(vloop, scenario())
    assert msg in r.chat.markup_removed


def test_answered_while_down_separate_prompt_loses_its_keyboard(
        replay, vloop, tmp_path):
    from datetime import datetime, timezone

    r = replay

    async def scenario():
        msg = await r.separate_prompt(reply_addr=_dead(tmp_path))
        await r.stop_process()
        await asyncio.sleep(10)
        stamp = datetime.fromtimestamp(r.wall(), timezone.utc).isoformat()
        r.append({"type": "user", "timestamp": stamp, "message": {
            "role": "user", "content": [{
                "type": "tool_result", "tool_use_id": "toolu_x",
                "content": "ok"}]}})
        await asyncio.sleep(10)
        r.start_process()
        await r.bot.recover_sessions()
        await asyncio.sleep(5)
        return msg

    msg = _run(vloop, scenario())
    assert msg in r.chat.markup_removed


# ── a chat migration rewrote the session's chat: dropped ─────────────────

async def _migrated(r, tmp_path):
    msg = await r.separate_prompt(reply_addr=_dead(tmp_path))
    await r.stop_process()
    data = json.loads(state.SESSION_STATE_FILE.read_text())
    data["sessions"][NAME]["scope_chat_id"] = OTHER_CHAT
    state.SESSION_STATE_FILE.write_text(json.dumps(data))
    await asyncio.sleep(30)
    r.start_process()
    await r.bot.recover_sessions()
    return msg


def test_separate_prompt_in_another_chat_is_not_restored(
        replay, vloop, tmp_path):
    r = replay
    _run(vloop, _migrated(r, tmp_path))
    assert r.sess.status is not Status.INTERACTIVE


def test_separate_prompt_in_another_chat_logs_chat_changed(
        replay, vloop, tmp_path, caplog):
    r = replay
    with caplog.at_level(logging.INFO):
        _run(vloop, _migrated(r, tmp_path))
    assert (f"[{LABEL}] saved permission prompt dropped - chat changed"
            in caplog.messages), [m for m in caplog.messages if LABEL in m]
