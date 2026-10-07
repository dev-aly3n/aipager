"""A saved permission prompt that must NOT come back (roadmap 8.102).

The prompt is dropped, and the session is handled exactly as before 8.102
(8.101's adoption, or the card closed), when: the transcript shows it was
answered or the turn moved on while the daemon was down, the session is
not alive, a hook already moved the session, the separate prompt's chat
changed, or the saved record is corrupt. A dropped separate prompt's
message loses its buttons.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from aipager import state
from aipager.state import Status

# The harness's (conftest.py; a hyphenated directory is no package).
LABEL = "aipager_boss"
TUID = "toolu_01Restored"
CHAT = 256113222


def _run(vloop, coro):
    return vloop.run_until_complete(coro)


def _ts(wall: float) -> str:
    return datetime.fromtimestamp(wall, timezone.utc).isoformat()


def _dead_hook(tmp_path) -> dict:
    return {"addr": str(tmp_path / "gone.sock"), "request_id": "req-dead"}


async def _inline_prompt(r, tmp_path) -> int:
    card = await r.open_card()
    await r.permission(hook=_dead_hook(tmp_path))
    await r.shown(card, "allow")
    await asyncio.sleep(5)
    return card


async def _separate_prompt(r, tmp_path) -> int:
    r.bot.registry.transition(r.sess.name, Status.BUSY)
    r.sess.trigger_msg_id = 1
    await r.permission(hook=_dead_hook(tmp_path))
    (msg_id,) = [m for m, rec in r.chat.messages.items()
                 if "Permission needed" in rec["text"]]
    return msg_id


def _saved_prompt():
    data = json.loads(state.SESSION_STATE_FILE.read_text())
    return data["sessions"]["claude-restoredprompt_harness"].get("open_prompt")


def test_answered_in_terminal_while_down_adopts_busy(replay, vloop, tmp_path,
                                                     caplog):
    """Answered in the terminal while the daemon was down: the tool ran
    and Claude went on to its next call. Not restored; 8.101 adopts the
    card as a running turn's, with its log line unchanged."""
    r = replay

    async def scenario():
        card = await _inline_prompt(r, tmp_path)
        await asyncio.sleep(10)
        r.append({"type": "assistant", "timestamp": _ts(vloop.wall()),
                  "message": {"role": "assistant", "stop_reason": "tool_use",
                              "content": [{"type": "tool_use", "id": "toolu_next",
                                           "name": "Read",
                                           "input": {"file_path": "/x"}}]}})
        await r.restart()
        await asyncio.sleep(2)
        r.bot.registry.save()
        return card

    with caplog.at_level(logging.INFO):
        card = _run(vloop, scenario())
    assert r.sess.status is Status.BUSY
    assert r.sess.animation_running()
    assert r.sess.pending_permission is None
    assert (f"[{LABEL}] saved permission prompt dropped - transcript moved on"
            in caplog.messages)
    assert any(m.startswith(
        f"[{LABEL}] busy card {card} adopted — the turn looks still running; "
        "working again, ") for m in caplog.messages)
    assert _saved_prompt() is None


def test_a_result_for_the_saved_call_drops_the_prompt(replay, vloop, tmp_path,
                                                     caplog):
    r = replay

    async def scenario():
        await _inline_prompt(r, tmp_path)
        r.append({"type": "user", "timestamp": _ts(vloop.wall() - 3600),
                  "message": {"role": "user", "content": [
                      {"type": "tool_result", "tool_use_id": TUID,
                       "content": "removed"}]}})
        await r.restart()

    with caplog.at_level(logging.INFO):
        _run(vloop, scenario())
    assert r.sess.status is Status.BUSY
    assert (f"[{LABEL}] saved permission prompt dropped - answered in the "
            "transcript") in caplog.messages


def test_dead_session_prompt_dropped(replay, vloop, tmp_path, caplog,
                                     monkeypatch):
    r = replay

    async def scenario():
        card = await _inline_prompt(r, tmp_path)
        monkeypatch.setattr("aipager.dtach.inject.list_sessions",
                            AsyncMock(return_value=[]))
        await r.restart()
        await asyncio.sleep(5)
        return card

    with caplog.at_level(logging.INFO):
        card = _run(vloop, scenario())
    assert r.sess.status is not Status.INTERACTIVE
    assert r.sess.pending_permission is None
    assert (f"[{LABEL}] saved permission prompt dropped - session not alive"
            in caplog.messages)
    assert not r.chat.cards[card]["stop"]  # closed as today


@pytest.mark.parametrize("hook", ["PreToolUse", "Stop", "PermissionRequest"])
def test_hook_before_recovery_wins(replay, vloop, tmp_path, caplog, hook):
    """A hook handled between the hook receiver's start and the recovery
    is the live state: answered (PreToolUse), finished (Stop), or a NEWER
    prompt (PermissionRequest), never painted over by the saved one."""
    r = replay

    async def scenario():
        await _inline_prompt(r, tmp_path)
        await r.restart(recover=False)
        if hook == "PreToolUse":
            r.hook(hook_event_name="PreToolUse", tool_name="Read",
                   tool_input={"file_path": "/x"}, tool_use_id="toolu_next")
        elif hook == "Stop":
            r.stop("all done")
        else:
            r.hook(hook_event_name="PermissionRequest", tool_name="Write",
                   tool_input={"file_path": "/new", "content": "x"})
        await asyncio.sleep(3)
        await r.bot.recover_sessions()
        await asyncio.sleep(1)

    with caplog.at_level(logging.INFO):
        _run(vloop, scenario())
    assert (f"[{LABEL}] saved permission prompt dropped - session already "
            "moved") in caplog.messages
    if hook == "PreToolUse":
        assert r.sess.status is Status.BUSY
    elif hook == "Stop":
        assert r.sess.status is Status.IDLE
    else:
        assert r.sess.status is Status.INTERACTIVE
        perm = r.sess.pending_permission or r.sess.pending_prompt_msg["perm"]
        assert perm["tool_info"]["name"] == "Write"


def test_separate_prompt_in_another_chat_dropped(replay, vloop, tmp_path,
                                                 caplog):
    """The chat the prompt was sent to is not the session's any more (a
    group migrated to a supergroup rewrote its id, 8.87)."""
    r = replay

    async def scenario():
        await _separate_prompt(r, tmp_path)
        await r.restart(recover=False)
        r.sess.scope_chat_id = -1009999
        await r.bot.recover_sessions()

    with caplog.at_level(logging.INFO):
        _run(vloop, scenario())
    assert r.sess.status is not Status.INTERACTIVE
    assert (f"[{LABEL}] saved permission prompt dropped - chat changed"
            in caplog.messages)


def test_dropped_separate_prompt_loses_its_buttons(replay, vloop, tmp_path,
                                                   monkeypatch):
    r = replay

    async def scenario():
        msg = await _separate_prompt(r, tmp_path)
        monkeypatch.setattr("aipager.dtach.inject.list_sessions",
                            AsyncMock(return_value=[]))
        await r.restart()
        await asyncio.sleep(2)
        return msg

    msg = _run(vloop, scenario())
    assert (CHAT, msg, None) in r.chat.markup_edits


def test_a_restored_separate_prompt_keeps_its_buttons(replay, vloop, tmp_path):
    """The control for the row above: only a dropped prompt's are taken."""
    r = replay

    async def scenario():
        await _separate_prompt(r, tmp_path)
        await r.restart()
        await asyncio.sleep(2)

    _run(vloop, scenario())
    assert r.sess.status is Status.INTERACTIVE
    assert r.chat.markup_edits == []


def _corrupt(data):
    rec = data["sessions"]["claude-restoredprompt_harness"]["open_prompt"]
    rec["perm"]["hook_reply"]["pid"] = 4242  # an extra key: not the schema


def test_corrupt_record_fails_closed(replay, vloop, tmp_path, caplog):
    r = replay

    async def scenario():
        card = await _inline_prompt(r, tmp_path)
        await r.restart(edit=_corrupt)
        await asyncio.sleep(5)
        return card

    with caplog.at_level(logging.INFO):
        card = _run(vloop, scenario())
    assert (f"[{LABEL}] saved permission prompt ignored - invalid record"
            in caplog.messages)
    assert r.sess.status is not Status.INTERACTIVE
    assert r.sess.pending_permission is None
    assert not any("permission prompt restored" in m for m in caplog.messages)
    # Handled as before 8.102: an empty transcript says nothing about a
    # turn, so the card is closed.
    assert not r.chat.cards[card]["stop"]
