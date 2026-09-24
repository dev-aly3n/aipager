"""The self-update restart gate (8.36) waits for answers the flood mute
holds (8.30) — through the hold the answer path really takes, not a
hand-seeded one.

A turn that finishes while its chat is flood-muted leaves its answer in
``held.HELD``; a restart then would drop it, so ``restart_blockers``
must name it until it is delivered.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock

from aipager import preferences as prefs
from aipager import self_update
from aipager.state import Status, TrackedSession

CHAT = 556
ANSWER = "All done: the migration ran and the tests are green."


def _bot(mk_bot, monkeypatch):
    calls: list[str] = []

    async def _post(method, payload, **_kw):
        calls.append(method)
        return {"ok": True, "result": {"message_id": 9100 + len(calls)}}

    monkeypatch.setattr("aipager.bot.rich_message._post", _post)
    bot = mk_bot()

    async def _call(*_a, **_kw):
        await asyncio.sleep(0)
        return MagicMock(message_id=9000)

    for name in ("send_message", "delete_message", "edit_message_text",
                 "send_document", "send_chat_action"):
        setattr(bot._app.bot, name, _call)

    async def _noop(*_a, **_kw):
        return None

    bot._maybe_update_bot_name = _noop
    return bot, calls


def test_an_answer_held_by_the_flood_mute_blocks_the_update_restart(
    mk_bot, run_async, monkeypatch,
):
    from aipager.bot.flood import MUTE
    from aipager.bot.held import HELD

    bot, calls = _bot(mk_bot, monkeypatch)
    sess = TrackedSession(name="claude-q", label="q", status=Status.IDLE)
    sess.scope_chat_id = CHAT
    sess.scope_kind = "dm"
    sess.busy_started_at = time.monotonic() - 5
    sess.trigger_msg_id = 7
    bot.registry._sessions[sess.name] = sess
    prefs.set_preference(CHAT, "layout", "card")
    try:
        MUTE.mute(CHAT, 600)
        run_async(bot.notify(sess, "idle_prompt", {"summary": ANSWER}))
        assert calls == []
        assert HELD.count() == 1
        blockers = self_update.restart_blockers(bot.registry)
        assert blockers == ["1 held answer not yet delivered"], blockers

        MUTE.clear()
        assert run_async(bot.flush_held_answers(sess)) == 1
        assert self_update.restart_blockers(bot.registry) == []
    finally:
        MUTE.clear()
        HELD.clear()
