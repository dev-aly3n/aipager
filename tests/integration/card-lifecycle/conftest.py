"""Plumbing for the one-card-per-turn rows (roadmap 8.55/8.56/8.57/8.58).
No assertions, no production logic.

The virtual loop and the gated Telegram double follow
``tests/integration/long-run-volume-budget/conftest.py`` (copied, not
imported, as this repo does between integration directories), keeping its
two rules: nothing in ``asyncio`` is ever patched — time moves because the
LOOP is virtual, and each module's OWN ``time`` reference is rebound — and
every call goes through the real ``BudgetRateLimiter`` before it counts.

What this directory adds is a Telegram that remembers every busy CARD: a
``sendMessage`` with a Stop keyboard opens one, an ``editMessageText``
without ``reply_markup`` settles it (Telegram drops the keyboard), and a
``deleteMessage`` removes it. A card still carrying its Stop button when a
row ends, with no turn running, is exactly the operator's stuck card.
"""

from __future__ import annotations

import asyncio
import json
import time
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

import aipager.bot.rich_message as rm
from aipager import config, policy_snapshot
from aipager.bot import flood, flood_budget
from aipager.bot.flood_budget import BudgetRateLimiter

CHAT = 256113222
NAME = "claude-aipager_boss"
PREFIX = "[via Telegram · @owner]\n"

#: The REAL ``_post``, captured before conftest's autouse
#: ``_block_real_telegram_http`` swaps in its refusing stub.
_REAL_POST = rm._post

_LATENCY = 1e-6
_WALL0 = 1_800_000_000.0


class _VirtualLoop(asyncio.SelectorEventLoop):
    """An event loop whose clock jumps to the next timer instead of
    waiting for it."""

    def __init__(self, real_seconds: float = 60.0) -> None:
        super().__init__()
        self._virtual = 1_000_000.0
        self._real_deadline = time.monotonic() + real_seconds

    def time(self) -> float:
        return self._virtual

    def wall(self) -> float:
        return _WALL0 + (self._virtual - 1_000_000.0)

    def _run_once(self) -> None:
        if time.monotonic() > self._real_deadline:
            raise AssertionError(
                "the virtual loop spun for too many REAL seconds — something "
                "is polling without a timer")
        if self._scheduled and not self._ready:
            live = [h._when for h in self._scheduled if not h._cancelled]
            if live and min(live) > self._virtual:
                self._virtual = min(live) + _LATENCY
        super()._run_once()


@pytest.fixture
def vloop(monkeypatch):
    """A virtual-time loop, with every module that reads time for this
    feature bound to it — each module's OWN ``time``, never the global
    module and never ``asyncio``."""
    from aipager import session_monitor, state
    from aipager.bot import animation, lifecycle, notify
    from aipager.dtach import hook_receiver

    loop = _VirtualLoop()
    fake = types.SimpleNamespace(monotonic=loop.time, time=loop.wall,
                                 sleep=time.sleep)
    for module in (animation, notify, flood, flood_budget, state,
                   hook_receiver, session_monitor, lifecycle):
        monkeypatch.setattr(module, "time", fake, raising=False)
    yield loop
    try:
        pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(
                asyncio.gather(*pending, return_exceptions=True))
        loop.run_until_complete(loop.shutdown_default_executor())
    finally:
        loop.close()


class CardChat:
    """Every call into the chat, and the state of every message in it."""

    def __init__(self, clock) -> None:
        self.clock = clock
        self.next_id = 5000
        #: ``(endpoint, t)`` for every call that reached "Telegram".
        self.calls: list[tuple[str, float]] = []
        #: card msg_id -> {"reply_to", "stop", "deleted", "text"} (``text``
        #: once edited)
        self.cards: dict[int, dict] = {}
        #: answer msg_id -> reply_to
        self.answers: dict[int, object] = {}
        #: msg_id -> [emoji, ...]
        self.reactions: dict[int, list[str]] = {}

    def new_id(self) -> int:
        self.next_id += 1
        return self.next_id

    def record(self, endpoint: str) -> None:
        self.calls.append((endpoint, self.clock()))

    def live_cards(self) -> list[int]:
        """Cards still showing their Stop button."""
        return [m for m, c in self.cards.items()
                if c["stop"] and not c["deleted"]]

    def card_sends_for(self, reply_to) -> int:
        return sum(1 for c in self.cards.values() if c["reply_to"] == reply_to)

    def count(self, endpoint=None) -> int:
        return sum(1 for e, _t in self.calls
                   if endpoint is None or e == endpoint)


class _PtbDouble:
    """``bot._app.bot``: each call gated by the real limiter, answered by
    :class:`CardChat`."""

    _ENDPOINTS = {
        "send_message": ("sendMessage", 0),
        "edit_message_text": ("editMessageText", 1),
        "delete_message": ("deleteMessage", 0),
        "send_chat_action": ("sendChatAction", 0),
        "set_message_reaction": ("setMessageReaction", 0),
        "send_document": ("sendDocument", 0),
    }

    def __init__(self, limiter, chat: CardChat) -> None:
        self._limiter = limiter
        self._chat = chat

    def _answer(self, name, args, kwargs):
        chat = self._chat
        if name == "send_message":
            msg_id = chat.new_id()
            if kwargs.get("reply_markup") is not None:
                chat.cards[msg_id] = {
                    "reply_to": kwargs.get("reply_to_message_id"),
                    "stop": True, "deleted": False}
            return SimpleNamespace(message_id=msg_id)
        if name == "edit_message_text":
            card = chat.cards.get(kwargs.get("message_id"))
            if card is not None:
                card["stop"] = kwargs.get("reply_markup") is not None
                card["text"] = kwargs.get("text") or (args[0] if args else "")
            return True
        if name == "delete_message":
            card = chat.cards.get(kwargs.get("message_id"))
            if card is not None:
                card["deleted"] = True
            return True
        if name == "set_message_reaction":
            chat.reactions.setdefault(args[1], []).append(args[2])
            return True
        return SimpleNamespace(message_id=chat.new_id())

    def __getattr__(self, name):
        endpoint, position = self._ENDPOINTS.get(name, (name, 0))

        async def _method(*args, rate_limit_args=None, **kwargs):
            chat_id = kwargs.get("chat_id")
            if chat_id is None and len(args) > position:
                chat_id = args[position]

            async def _call():
                self._chat.record(endpoint)
                return self._answer(name, args, kwargs)

            return await self._limiter.process_request(
                callback=_call, args=(), kwargs={}, endpoint=endpoint,
                data={"chat_id": chat_id}, rate_limit_args=rate_limit_args,
            )

        return _method


@pytest.fixture
def card_chat(vloop):
    return CardChat(vloop.time)


@pytest.fixture
def vlimiter(vloop):
    """The daemon's limiter on the virtual loop, installed on the rich path
    the way ``lifecycle._make_builder`` installs it — with the chat's
    history on 2026-09-27: ONE ban inside the 7-day memory (2026-09-24,
    1184 s), so the ceiling is 0.5/s and the hourly budget is halved."""
    lim = BudgetRateLimiter(clock=vloop.time, sleep=asyncio.sleep,
                            wall_clock=vloop.wall,
                            signal_path=config.FLOOD_BACKOFF_FILE)
    lim.restore([{"chat_id": CHAT,
                  "ban_stamps": [vloop.wall() - 3 * 86400.0]}])
    rm.set_rate_limiter(lim)
    yield lim
    rm.set_rate_limiter(None)
    lim.reset()


@pytest.fixture
def rich_chat(monkeypatch, card_chat):
    """The REAL ``rich_message._post`` over an ``httpx.MockTransport``
    answered by :class:`CardChat` — so card edits and answers are metered
    by the limiter exactly as in the daemon."""
    monkeypatch.setattr("aipager.config.BOT_TOKEN", "TESTTOKEN")

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        endpoint = request.url.path.rsplit("/", 1)[-1]
        card_chat.record(endpoint)
        msg_id = payload.get("message_id")
        if endpoint == "editMessageText":
            card = card_chat.cards.get(msg_id)
            if card is not None:
                card["stop"] = "reply_markup" in payload
                card["text"] = ((payload.get("rich_message") or {})
                                .get("markdown") or payload.get("text") or "")
        elif endpoint == "sendRichMessage":
            msg_id = card_chat.new_id()
            card_chat.answers[msg_id] = payload.get("reply_to_message_id")
        return httpx.Response(200, json={
            "ok": True, "result": {"message_id": msg_id or 4242}})

    monkeypatch.setattr(rm, "_post", _REAL_POST)
    monkeypatch.setattr(
        rm, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    yield card_chat
    monkeypatch.setattr(rm, "_client", None)


@pytest.fixture
def daemon(mk_bot, monkeypatch, tmp_path, vlimiter, rich_chat):
    """A bot + hook receiver on the virtual loop, the Telegram side gated by
    the real limiter, the dtach side mocked. Returns ``(bot, receiver,
    chat)``."""
    from aipager.dtach.hook_receiver import HookReceiver

    bot = mk_bot()
    bot._app.bot = _PtbDouble(vlimiter, rich_chat)
    bot._maybe_update_bot_name = AsyncMock()
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter",
                        AsyncMock(return_value=True))
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_bytes(b"")
    receiver = HookReceiver(bot.registry, bot.notify)
    return bot, receiver, rich_chat, str(transcript)


class Replay:
    """Drives the daemon like the incident did: Telegram messages through
    ``_handle_message`` one at a time (PTB's default), hook datagrams
    through ``HookReceiver._on_datagram`` each as its own task (as the
    daemon's datagram protocol does)."""

    def __init__(self, bot, receiver, chat, transcript, mk_update, loop):
        self.bot = bot
        self.receiver = receiver
        self.chat = chat
        self.transcript = transcript
        self.mk_update = mk_update
        self.loop = loop
        self.updates: asyncio.Queue = asyncio.Queue()
        self.tasks: list[asyncio.Task] = []
        self.sess = bot.registry.get_or_create(NAME)
        self.sess.label = "aipager_boss"
        self.sess.scope_chat_id = CHAT
        self.sess.scope_kind = "dm"
        self.sess.transcript_path = transcript
        bot.registry.last_active_session = NAME
        self._n = 0

    # ── the Telegram side: one update at a time, like PTB ───────────────
    async def _updates(self) -> None:
        while True:
            mid, text = await self.updates.get()
            try:
                await self.bot._handle_message(
                    self.mk_update(text, message_id=mid, user_id=12345,
                                   chat_id=CHAT), MagicMock())
            finally:
                self.updates.task_done()

    def say(self, mid: int, text: str) -> None:
        self.updates.put_nowait((mid, text))

    # ── the hook side: every datagram is its own task, like the daemon ──
    def hook(self, **fields) -> asyncio.Task:
        self._n += 1
        fields.setdefault("session", NAME)
        fields["_seq"] = self._n  # never a duplicate fingerprint
        task = asyncio.ensure_future(
            self.receiver._on_datagram(json.dumps(fields).encode()))
        self.tasks.append(task)
        return task

    def prompt_hooks(self, mid: int, text: str) -> None:
        """What Claude Code sends when it takes the message: its
        UserPromptSubmit, and the pick-up of the note the injection left."""
        self.hook(hook_event_name="UserPromptSubmit", prompt=PREFIX + text)
        policy_snapshot.consume_notes_matching(NAME, PREFIX + text)
        self.hook(type="queue_pickup", consumed=[
            {"msg_id": mid, "chat_id": CHAT, "raw_text": text}], expired=[])

    def tool(self, summary: str, agent_id: str = "") -> None:
        extra = {"agent_id": agent_id} if agent_id else {}
        self.hook(hook_event_name="PreToolUse", tool_name="Bash",
                  tool_input={"command": summary}, **extra)
        self.hook(hook_event_name="PostToolUse", tool_name="Bash",
                  tool_input={"command": summary}, **extra)

    def stop(self, answer: str) -> asyncio.Task:
        return self.hook(hook_event_name="Stop",
                         last_assistant_message=answer)

    def append(self, *entries: dict) -> None:
        with open(self.transcript, "a", encoding="utf-8") as fh:
            for e in entries:
                fh.write(json.dumps(e) + "\n")

    async def settle(self, seconds: float) -> None:
        await asyncio.sleep(seconds)




@pytest.fixture
def replay(daemon, mk_update, vloop):
    bot, receiver, chat, transcript = daemon
    return Replay(bot, receiver, chat, transcript, mk_update, vloop)
