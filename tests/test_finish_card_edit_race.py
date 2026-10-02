"""A busy-card edit that is queued or in flight when a turn finishes must
never land after the finish (operator, 2026-10-02: a merged-layout answer
replaced by the stale frame "Working · 7s").

The card edit is held in a fake ``rich_message._post`` (as if waiting for a
chat token) from a task that is NOT ``sess.animate_task``, so
``_stop_animation`` does not cancel it: a hook-driven ``_edit_busy_rich``,
or the session monitor's refresh. Each test pins the order the chat sees.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.bot.rich_message import RichMessageFallbackRequired
from aipager.preferences import set_preference
from aipager.state import Status, TrackedSession

CHAT = 4242
ANSWER = "the zzrace final answer"


def _sess(*, busy_msg_id=42, trigger=7, card_trigger=7, status=Status.BUSY):
    s = TrackedSession(name="claude-zzrace", label="zzrace", status=status)
    s.scope_kind = "dm"
    s.scope_chat_id = CHAT
    s.busy_msg_id = busy_msg_id
    s.busy_started_at = time.monotonic() - 7
    s.trigger_msg_id = trigger
    s.busy_card_trigger = card_trigger
    return s


class _Chat:
    """What reached Telegram, in order, via ``rich_message._post`` and the
    bot's own ``delete_message`` / ``edit_message_text``. A busy frame
    ("Working", not the answer) waits on ``release`` the first time, like
    an ORNAMENT queued for a token."""

    def __init__(self, monkeypatch, *, new_id=77):
        self.log: list[tuple] = []
        self.release = asyncio.Event()
        self.held_once = False
        self.new_id = new_id
        self.on_delete = None
        self.tick_gone = False
        monkeypatch.setattr("aipager.bot.rich_message._post", self._post)

    async def _post(self, method, payload, **_kw):
        md = payload.get("rich_message", {}).get("markdown", "")
        is_answer = ANSWER in md
        if "Working" in md and not is_answer and not self.held_once:
            self.held_once = True
            await self.release.wait()
            if self.tick_gone:
                self.log.append(("edit", "gone", payload.get("message_id")))
                return {"ok": False, "error_code": 400,
                        "description": "Bad Request: message to edit not found"}
        if method.startswith("send"):
            self.log.append(("send", "answer" if is_answer else "card",
                             self.new_id))
            return {"ok": True, "result": {"message_id": self.new_id}}
        kind = ("answer" if is_answer
                else "busy" if "Working" in md else "final")
        self.log.append(("edit", kind, payload.get("message_id")))
        return {"ok": True, "result": {"message_id": payload.get("message_id")}}

    async def delete_message(self, *, chat_id, message_id, **_kw):
        self.log.append(("delete", "", message_id))
        if self.on_delete is not None:
            await self.on_delete()
        return True


def _bot(mk_bot, chat: _Chat):
    bot = mk_bot()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._app.bot.delete_message = chat.delete_message
    bot._maybe_update_bot_name = AsyncMock()
    return bot


async def _spin(n=20):
    for _ in range(n):
        await asyncio.sleep(0)


# ── merged: the edit in place ────────────────────────────────────────────

def test_merged_final_lands_after_a_card_edit_already_in_flight(
        mk_bot, monkeypatch, run_async):
    set_preference(CHAT, "layout", "merged")
    chat = _Chat(monkeypatch)
    bot = _bot(mk_bot, chat)
    s = _sess()
    bot.registry._sessions[s.name] = s
    late: list = []

    async def scenario():
        # A hook's card edit, waiting for a token inside the edit lock.
        tick = asyncio.ensure_future(bot._edit_busy_rich(s, "Working"))
        await _spin()
        bot.registry.transition(s.name, Status.IDLE)
        fin = asyncio.ensure_future(
            bot.notify(s, "idle_prompt", {"raw_md": ANSWER}))
        await _spin()
        # And one more issued after the finish began: it must send nothing.
        late.append(asyncio.ensure_future(bot._edit_busy_rich(s, "Working")))
        await asyncio.sleep(0.05)
        chat.release.set()
        await fin
        await tick
        return await late[0]

    late_result = run_async(scenario())
    edits = [e for e in chat.log if e[0] == "edit"]
    assert edits == [("edit", "busy", 42), ("edit", "answer", 42)], chat.log
    assert late_result is None
    assert s.busy_msg_id is None


# ── merged: send_as_new (the card is anchored to an unconsumed message) ───

def test_merged_send_as_new_is_never_overwritten_by_a_card_edit(
        mk_bot, monkeypatch, run_async):
    set_preference(CHAT, "layout", "merged")
    chat = _Chat(monkeypatch, new_id=77)
    bot = _bot(mk_bot, chat)
    # The card hangs under message 5, the turn consumed 7: send-as-new.
    s = _sess(trigger=7, card_trigger=5)
    bot.registry._sessions[s.name] = s
    late: list = []

    async def _during_delete():
        # The stale card's delete is out: busy_msg_id already names the
        # new answer message. An edit that asks for the lock now must not
        # paint "Working" over the answer.
        late.append(asyncio.ensure_future(bot._edit_busy_rich(s, "Working")))
        await _spin()

    chat.on_delete = _during_delete

    async def scenario():
        tick = asyncio.ensure_future(bot._edit_busy_rich(s, "Working"))
        await _spin()
        bot.registry.transition(s.name, Status.IDLE)
        fin = asyncio.ensure_future(
            bot.notify(s, "idle_prompt", {"raw_md": ANSWER}))
        await asyncio.sleep(0.05)
        chat.release.set()
        await fin
        await tick
        return await late[0]

    late_result = run_async(scenario())
    # The guard that matters: the edit asking for the lock during the stale
    # card's delete sends nothing, so nothing touches the answer (77).
    assert late_result is None
    assert ("send", "answer", 77) in chat.log, chat.log
    answer_at = chat.log.index(("send", "answer", 77))
    # Also: the edit in flight (on the card about to be deleted) went first.
    assert chat.log[:answer_at] == [("edit", "busy", 42)], chat.log
    assert all(e[2] != 77 for e in chat.log[answer_at + 1:]), chat.log
    assert ("delete", "", 42) in chat.log[answer_at + 1:]
    assert s.busy_msg_id is None


def test_merged_finish_does_not_edit_a_card_the_waited_edit_found_gone(
        mk_bot, monkeypatch, run_async):
    set_preference(CHAT, "layout", "merged")
    chat = _Chat(monkeypatch)
    chat.tick_gone = True
    bot = _bot(mk_bot, chat)
    s = _sess()
    bot.registry._sessions[s.name] = s

    async def scenario():
        tick = asyncio.ensure_future(bot._edit_busy_rich(s, "Working"))
        await _spin()
        bot.registry.transition(s.name, Status.IDLE)
        fin = asyncio.ensure_future(
            bot.notify(s, "idle_prompt", {"raw_md": ANSWER}))
        await asyncio.sleep(0.05)
        chat.release.set()
        await fin
        return await tick

    assert run_async(scenario()) is None  # the card was gone
    # No merge into "message 0": the answer went out on its own.
    assert [e for e in chat.log if e[0] == "edit"] == [("edit", "gone", 42)]
    assert ("send", "answer", 77) in chat.log, chat.log


# ── card: a final move (the finished card re-sent under the consumed msg) ─

def test_card_final_move_copy_is_never_repainted_busy(
        mk_bot, monkeypatch, run_async):
    set_preference(CHAT, "layout", "card")
    chat = _Chat(monkeypatch, new_id=77)
    chat.held_once = True  # nothing waits: the edit asks during the delete
    bot = _bot(mk_bot, chat)
    s = _sess(trigger=7, card_trigger=5)
    bot.registry._sessions[s.name] = s
    late: list = []

    async def _during_delete():
        late.append(await bot._edit_busy_rich(s, "Working"))

    chat.on_delete = _during_delete

    async def scenario():
        bot.registry.transition(s.name, Status.IDLE)
        await bot.notify(s, "idle_prompt", {"raw_md": ANSWER})

    run_async(scenario())
    assert ("send", "card", 77) in chat.log, chat.log
    assert late == [None]
    assert ("edit", "busy", 77) not in chat.log, chat.log


# ── a superseded waiting card, settled while its log goes out ────────────

def test_superseded_card_is_not_repainted_busy_during_its_attachment(
        mk_bot, monkeypatch, run_async):
    chat = _Chat(monkeypatch)
    chat.held_once = True
    bot = _bot(mk_bot, chat)
    s = _sess()
    s.tool_history = [{"tool": "Bash", "detail": "ls"}]
    monkeypatch.setattr(
        "aipager.bot.animation.build_stream_card_ex",
        lambda sess, verb, **kw: (f"zzrace · {verb}", True))
    late: list = []

    async def _send_document(*_a, **_kw):
        late.append(await bot._edit_busy_rich(s, "Working"))

    bot._app.bot.send_document = _send_document

    run_async(bot._close_superseded_card(s))
    assert late == [None]
    assert [e for e in chat.log if e[0] == "edit"] == [("edit", "final", 42)]


def test_final_plain_text_degrade_also_settles_the_card(
        mk_bot, monkeypatch, run_async):
    chat = _Chat(monkeypatch)
    bot = _bot(mk_bot, chat)
    bot._app.bot.edit_message_text = AsyncMock(return_value=True)
    s = _sess()

    async def _refuse(*_a, **_kw):
        raise RichMessageFallbackRequired("degrade")

    monkeypatch.setattr("aipager.bot.animation.edit_message_text_rich", _refuse)

    async def scenario():
        assert await bot._edit_busy_rich(s, "Done", final=True) is True
        return await bot._edit_busy_rich(s, "Working")

    assert run_async(scenario()) is None
    assert bot._app.bot.edit_message_text.await_count == 1


# ── compaction deadline on an idle session: its text is the last word ────

def test_compact_timeout_text_is_not_overwritten_by_a_card_edit(
        mk_bot, monkeypatch, run_async):
    chat = _Chat(monkeypatch)
    chat.held_once = True
    bot = _bot(mk_bot, chat)
    s = _sess(status=Status.IDLE)
    s.push_compacting(42, time.monotonic(), deadline_seconds=180.0)
    late: list = []

    async def _edit_text(text, **kw):
        chat.log.append(("edit", "timeout", kw.get("message_id")))
        # A hook's card edit asks for the lock while this one is out.
        late.append(asyncio.ensure_future(bot._edit_busy_rich(s, "Working")))
        await _spin()
        return True

    bot._app.bot.edit_message_text = _edit_text

    async def scenario():
        await bot.notify(s, "compact_timeout", {"elapsed_seconds": 190.0})
        return await late[0]

    assert run_async(scenario()) is None
    assert chat.log == [("edit", "timeout", 42)], chat.log
    assert s.busy_msg_id is None


def test_compact_timeout_stops_the_dots_before_its_last_word(
        mk_bot, monkeypatch, run_async):
    monkeypatch.setattr(
        "aipager.bot.animation.COMPACT_ANIMATE_INTERVAL_SECONDS", 0.005)
    chat = _Chat(monkeypatch)
    bot = _bot(mk_bot, chat)
    s = _sess(status=Status.IDLE)
    s.push_compacting(42, time.monotonic(), deadline_seconds=180.0)

    async def _edit_text(text, **kw):
        kind = "timeout" if "didn't confirm" in text else "dots"
        chat.log.append(("edit", kind, kw.get("message_id")))
        if kind == "timeout":
            # While this is out, a running dot loop would get its next
            # frame in behind it.
            await asyncio.sleep(0.05)
        return True

    bot._app.bot.edit_message_text = _edit_text

    async def scenario():
        s.animate_task = asyncio.ensure_future(bot._animate_compact(s))
        await asyncio.sleep(0.03)
        await bot.notify(s, "compact_timeout", {"elapsed_seconds": 190.0})
        await asyncio.sleep(0.03)

    run_async(scenario())
    kinds = [e[1] for e in chat.log]
    assert "dots" in kinds  # the loop was really running
    assert kinds[-1] == "timeout", kinds


def test_compact_timeout_skips_a_card_the_waited_edit_found_gone(
        mk_bot, monkeypatch, run_async):
    chat = _Chat(monkeypatch)
    chat.tick_gone = True
    bot = _bot(mk_bot, chat)
    s = _sess(status=Status.IDLE)
    s.push_compacting(42, time.monotonic(), deadline_seconds=180.0)
    raw = AsyncMock(return_value=True)
    bot._app.bot.edit_message_text = raw

    async def scenario():
        tick = asyncio.ensure_future(bot._edit_busy_rich(s, "Working"))
        await _spin()
        fin = asyncio.ensure_future(
            bot.notify(s, "compact_timeout", {"elapsed_seconds": 190.0}))
        await asyncio.sleep(0.05)
        chat.release.set()
        await fin
        return await tick

    assert run_async(scenario()) is None
    raw.assert_not_awaited()
    assert s.busy_msg_id is None


# ── close paths that end the card with a plain line ──────────────────────

def _late_edit_during_raw(bot, chat, s, late, marker):
    async def _edit_text(text, **kw):
        chat.log.append(("edit", marker if marker in text else "other",
                         kw.get("message_id")))
        late.append(asyncio.ensure_future(bot._edit_busy_rich(s, "Working")))
        await _spin()
        return True
    return _edit_text


@pytest.mark.parametrize("event,marker", [
    ("job_grace_expired", "Finished"),
    ("job_agents_lost", "agent lost"),
])
def test_job_close_line_is_not_overwritten_by_a_card_edit(
        mk_bot, monkeypatch, run_async, event, marker):
    chat = _Chat(monkeypatch)
    chat.held_once = True
    bot = _bot(mk_bot, chat)
    s = _sess(status=Status.IDLE)
    late: list = []
    bot._app.bot.edit_message_text = _late_edit_during_raw(
        bot, chat, s, late, marker)

    async def scenario():
        await bot.notify(s, event, {})
        return await late[0]

    assert run_async(scenario()) is None
    assert chat.log == [("edit", marker, 42)], chat.log
    assert s.busy_msg_id is None


def test_not_taken_line_is_not_overwritten_by_a_card_edit(
        mk_bot, monkeypatch, run_async):
    chat = _Chat(monkeypatch)
    chat.held_once = True
    bot = _bot(mk_bot, chat)
    s = _sess()
    bot.registry._sessions[s.name] = s
    late: list = []
    bot._app.bot.edit_message_text = _late_edit_during_raw(
        bot, chat, s, late, "Not taken")

    async def scenario():
        await bot.notify(s, "prompt_not_taken", {"grace": 8.0})
        return await late[0]

    assert run_async(scenario()) is None
    assert chat.log == [("edit", "Not taken", 42)], chat.log
    assert s.busy_msg_id is None


def test_stopped_line_is_not_overwritten_by_a_card_edit(
        mk_bot, monkeypatch, run_async):
    chat = _Chat(monkeypatch)
    chat.held_once = True
    bot = _bot(mk_bot, chat)
    s = _sess()
    bot.registry._sessions[s.name] = s
    monkeypatch.setattr("aipager.dtach.inject.send_keys",
                        AsyncMock(return_value=True))
    late: list = []
    bot._app.bot.edit_message_text = _late_edit_during_raw(
        bot, chat, s, late, "Stopped")

    async def scenario():
        outcome = await bot._stop_session_core(s)
        assert outcome.ok is True
        return await late[0]

    assert run_async(scenario()) is None
    assert chat.log == [("edit", "Stopped", 42)], chat.log
    assert s.busy_msg_id is None


def test_safety_halt_line_is_not_overwritten_by_a_card_edit(
        mk_bot, monkeypatch, run_async):
    chat = _Chat(monkeypatch)
    chat.held_once = True
    bot = _bot(mk_bot, chat)
    s = _sess()
    bot.registry._sessions[s.name] = s
    monkeypatch.setattr("aipager.dtach.inject.send_keys",
                        AsyncMock(return_value=True))
    late: list = []
    bot._app.bot.edit_message_text = _late_edit_during_raw(
        bot, chat, s, late, "safety policy")

    async def scenario():
        await bot._halt_for_safety(s, "zzrace test block")
        return await late[0]

    assert run_async(scenario()) is None
    assert chat.log == [("edit", "safety policy", 42)], chat.log
    assert s.busy_msg_id is None


@pytest.fixture(autouse=True)
def _no_flood_mute(monkeypatch):
    """Nothing here is about a mute; keep the gate's global state out."""
    from aipager.bot import notify as notify_mod
    monkeypatch.setattr(notify_mod.MUTE, "is_muted", lambda *_a, **_k: False)
