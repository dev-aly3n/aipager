"""A restart while a permission prompt waits as its own message (roadmap
8.102; the separate-message prompt of 8.99, sent before the turn's busy
card existed).

Before: the restarted daemon did not know the message, and a tap on it
was refused ("this prompt has expired") while Claude Code kept waiting.
Now the session comes back waiting on it and the message's Allow/Deny
answer it. Anything else carrying answer buttons is still refused.
"""

from __future__ import annotations

import asyncio
import json
import logging

from aipager import audit as audit_mod
from aipager.bot.dashboard import _pinned_state
from aipager.state import Status

# The harness's (conftest.py; a hyphenated directory is no package).
LABEL = "aipager_boss"
OWNER = 12345
CHAT = 256113222


def _run(vloop, coro):
    return vloop.run_until_complete(coro)


def _audit() -> dict:
    lines = audit_mod.AUDIT_LOG_PATH.read_text().splitlines()
    assert lines, "no audit record"
    return json.loads(lines[-1])


async def _separate_prompt(r, tmp_path) -> int:
    """A turn with no card yet asks for a tool: the prompt goes out as its
    own message. Returns its id."""
    r.bot.registry.transition(r.sess.name, Status.BUSY)
    r.sess.trigger_msg_id = 1
    await r.permission(hook={"addr": str(tmp_path / "gone.sock"),
                             "request_id": "req-dead"})
    assert r.sess.status is Status.INTERACTIVE and not r.sess.busy_msg_id
    (msg_id,) = [m for m, rec in r.chat.messages.items()
                 if "Permission needed" in rec["text"]]
    return msg_id


def test_allow_on_restored_separate_prompt(replay, vloop, tmp_path, caplog):
    r = replay

    async def scenario():
        msg = await _separate_prompt(r, tmp_path)
        await r.restart()
        restored = (r.sess.status, _pinned_state(r.sess))
        toast = await r.tap(msg, r.cb(msg, "allow"),
                            text=r.chat.messages[msg]["text"])
        await asyncio.sleep(2)
        return msg, restored, toast

    with caplog.at_level(logging.INFO):
        msg, restored, toast = _run(vloop, scenario())
    assert restored[0] is Status.INTERACTIVE and "needs you" in restored[1]
    assert (f"[{LABEL}] permission prompt restored after restart - waiting "
            f"for an answer (separate msg {msg})") in caplog.messages
    assert toast == f"Allowed [{LABEL}]"
    assert r.keys == ["Enter"]
    record = _audit()
    assert (record["via"], record["action"], record["user_id"]) == (
        "keystroke_fallback", "Allowed", OWNER)
    # The attributed line, threaded under the prompt itself (no card).
    line = [m for m in r.chat.messages.values() if "Allowed by" in m["text"]]
    assert line and line[0]["reply_to"] == msg
    edited = r.last_query.edit_message_text.await_args
    assert edited is not None and "→ Allowed" in edited.args[0]
    assert r.sess.status is Status.BUSY


def test_deny_on_restored_separate_prompt(replay, vloop, tmp_path):
    r = replay

    async def scenario():
        msg = await _separate_prompt(r, tmp_path)
        await r.restart()
        await r.tap(msg, r.cb(msg, "deny"), text=r.chat.messages[msg]["text"])
        await asyncio.sleep(1)

    _run(vloop, scenario())
    assert r.keys == ["Down"] * 5 + ["Enter"]
    record = _audit()
    assert (record["via"], record["denied"]) == ("keystroke_fallback", True)


def test_other_message_refused_while_restored(replay, vloop, tmp_path):
    """A copy the pinned bar's "Answer" re-sent before the restart is not
    re-registered: tapped while the restored prompt waits, it is refused
    and loses its buttons, and no key is typed."""
    r = replay

    async def scenario():
        msg = await _separate_prompt(r, tmp_path)
        outcome = await r.bot._resend_pending_prompt(CHAT, r.sess)
        assert outcome == "sent"
        copy_id = max(r.chat.messages)
        assert copy_id != msg
        await r.restart()
        toast = await r.tap(copy_id, r.cb(copy_id, "allow"))
        await asyncio.sleep(1)
        return toast

    toast = _run(vloop, scenario())
    assert toast == "this prompt has expired"
    assert r.keys == []
    r.last_query.edit_message_reply_markup.assert_awaited()
    assert r.sess.status is Status.INTERACTIVE


def test_a_tap_after_the_restored_prompt_was_answered_is_refused(
        replay, vloop, tmp_path):
    r = replay

    async def scenario():
        msg = await _separate_prompt(r, tmp_path)
        await r.restart()
        await r.tap(msg, r.cb(msg, "allow"))
        await asyncio.sleep(1)
        return await r.tap(msg, r.cb(msg, "deny"))

    toast = _run(vloop, scenario())
    assert toast == "already answered"
    assert r.keys == ["Enter"]
