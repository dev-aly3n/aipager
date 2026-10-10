"""Helpers (a plain module, imported as tests.answer_status_bb) for the
tester's black-box plumbing for the answer's live status block (design.md
"Success criteria" 1-13, entrypoints.md).

Only the public surface is driven: ``agents_line.render_block`` and its
dataclasses, the ``TrackedSession`` methods entrypoints.md lists, the hook
datagram handler (``HookReceiver._on_datagram``), and the bot's
``notify`` events ``idle_prompt`` (an answer goes out) and
``agents_line_done`` (the monitor's tick). Every Telegram call lands in
one recorded list; nothing reaches the network. ``asyncio`` is never
patched. Time moves by passing ``now`` to the tick, never by sleeping.
"""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import MagicMock

from aipager import preferences as prefs
from aipager.bot import notify as notify_mod
from aipager.dtach import hook_receiver as hr
from aipager.state import Status

CHAT = 777
TRIGGER = 11
CARD = 51
ANSWER = "Here is the answer about the settings fix."


class Wire:
    """Every outbound call, in order."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.next_id = 7000
        self.refuse_edits = False

    def of(self, method: str) -> list[dict]:
        return [p for m, p in self.calls if m == method]

    def edits(self) -> list[dict]:
        return self.of("editMessageText")


def make_wire(monkeypatch):
    from aipager.bot.flood_budget import FloodSkipped

    w = Wire()

    async def _post(method, payload, **kw):
        rec = dict(payload, _kw=kw)
        w.calls.append((method, rec))
        if w.refuse_edits and method == "editMessageText":
            raise FloodSkipped(CHAT, method)
        w.next_id += 1
        rec["_id"] = w.next_id
        return {"ok": True, "result": {"message_id": w.next_id}}

    monkeypatch.setattr("aipager.bot.rich_message._post", _post)
    return w


def make_wired_bot(mk_bot, wire):
    b = mk_bot()

    def _rec(name):
        async def _call(*args, **kwargs):
            payload = dict(kwargs)
            if args:
                payload["_args"] = args
            wire.calls.append((name, payload))
            wire.next_id += 1
            return MagicMock(message_id=wire.next_id)
        return _call

    for name in ("send_message", "delete_message", "edit_message_text",
                 "send_document", "send_chat_action"):
        setattr(b._app.bot, name, _rec(name))

    async def _noop(*_a, **_kw):
        return None

    b._maybe_update_bot_name = _noop
    return b


def make_sess(bot, *, label="bbx", layout="card"):
    s = bot.registry.get_or_create(f"claude-{label}")
    s.label = label
    s.status = Status.IDLE
    s.scope_chat_id = CHAT
    s.scope_kind = "dm"
    s.busy_msg_id = CARD
    s.busy_card_trigger = TRIGGER
    s.trigger_msg_id = TRIGGER
    s.busy_started_at = time.monotonic() - 5
    s.tool_history = [("Read: /a.py", True)]
    prefs.set_preference(CHAT, "layout", layout)
    return s


def bounded(coro):
    return asyncio.wait_for(coro, timeout=5)


async def _drain():
    for _ in range(5):
        pending = [t for t in getattr(notify_mod, "_BACKGROUND_TASKS", set())
                   if not t.done()]
        if not pending:
            return
        await asyncio.gather(*pending, return_exceptions=True)


def finish(run_async, bot, sess, text=ANSWER):
    """The turn ends and its answer goes out. Returns (t_before, t_after)
    in monotonic seconds, bracketing the send."""
    sess.trigger_msg_id = TRIGGER
    sess.status = Status.IDLE

    async def _go():
        await bot.notify(sess, "idle_prompt", {"summary": text, "raw_md": text})
        await _drain()

    t0 = time.monotonic()
    run_async(bounded(_go()))
    return t0, time.monotonic()


def tick(run_async, bot, sess, now):
    run_async(bounded(bot.notify(sess, "agents_line_done", {"now": now})))


def md(payload: dict) -> str:
    return payload["rich_message"]["markdown"]


def answer_text(wire, text=ANSWER) -> str:
    sent = [md(p) for m, p in wire.calls
            if m in ("sendRichMessage", "editMessageText")
            and "rich_message" in p and text in md(p)]
    assert sent, "the answer was never sent"
    return sent[0]


def answer_msg_id(wire, text=ANSWER) -> int:
    """The id Telegram gave the message that carried the answer."""
    for m, p in wire.calls:
        if m == "sendRichMessage" and "rich_message" in p and text in md(p):
            return p["_id"]
    raise AssertionError("no answer message")


def answer_edits(wire, text=ANSWER) -> list[dict]:
    """Every edit (attempted or done) of the answer's own message."""
    mid = answer_msg_id(wire, text)
    return [p for m, p in wire.calls
            if m == "editMessageText" and p.get("message_id") == mid]


def block_of(full: str, body: str = ANSWER) -> str:
    """The status block: everything after the answer's own text."""
    idx = full.rfind(body)
    assert idx >= 0
    rest = full[idx + len(body):]
    assert rest.startswith("\n\n"), repr(rest[:20])
    return rest[2:]


def receiver(registry, sink=None):
    async def _notify(_sess, event, context):
        if sink is not None:
            sink.append((event, context))
    return hr.HookReceiver(registry, _notify)


def hook(run_async, recv, sess, event, **fields):
    recv._recent_fingerprints.clear()
    payload = dict({"session": sess.name, "hook_event_name": event,
                    "transcript_path": ""}, **fields)
    run_async(recv._on_datagram(json.dumps(payload).encode()))
