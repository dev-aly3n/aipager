"""Plumbing for the black-box rows of roadmap 8.102 (a permission prompt
that was waiting when the daemon restarted). No assertions, no production
logic.

The virtual loop, the gated Telegram double and the replay driver are
copied from ``tests/integration/card-lifecycle/conftest.py`` (copied, not
imported, as this repo does between integration directories), with the
same two rules: nothing in ``asyncio`` is ever patched (time moves because
the LOOP is virtual, and each module's OWN ``time`` reference is rebound),
and every Telegram call goes through the real ``BudgetRateLimiter``.

What this directory adds:

- the chat remembers every message's buttons (``markups``), so a tap after
  the restart presses the button Telegram still shows, whatever its
  callback data looks like, and records keyboard removals;
- the PTY side records every key typed (``inject.send_keys``) and answers
  ``inject.list_sessions`` with the sessions the test says are alive;
- :meth:`Replay.restart` is a real restart: the registry is saved to the
  (isolated) state file, every task of the old process is cancelled, and
  a NEW registry, bot and hook receiver are built from a fresh ``load()``.
  The chat (Telegram) and the transcript (Claude) outlive it, as they do.
"""

from __future__ import annotations

import asyncio
import json
import time
import types
from pathlib import Path
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

import aipager.bot.rich_message as rm
from aipager import config, policy_snapshot
from aipager.bot import flood, flood_budget
from aipager.bot.flood_budget import BudgetRateLimiter

CHAT = 256113222
NAME = "claude-rppbb_harness"
LABEL = "rppbb"
PREFIX = "[via Telegram · @owner]\n"
OWNER = 12345

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
                "the virtual loop spun for too many REAL seconds - something "
                "is polling without a timer")
        if self._scheduled and not self._ready:
            live = [h._when for h in self._scheduled if not h._cancelled]
            if live and min(live) > self._virtual:
                self._virtual = min(live) + _LATENCY
        super()._run_once()


@pytest.fixture
def vloop(monkeypatch):
    """A virtual-time loop, with every module that reads time on this path
    bound to it: each module's OWN ``time``, never the global module and
    never ``asyncio``."""
    from aipager import session_monitor, state
    from aipager.bot import (
        animation,
        callbacks,
        dashboard,
        lifecycle,
        notify,
        session_ops,
    )
    from aipager.dtach import hook_receiver

    loop = _VirtualLoop()
    fake = types.SimpleNamespace(monotonic=loop.time, time=loop.wall,
                                 sleep=time.sleep)
    for module in (animation, notify, flood, flood_budget, state,
                   hook_receiver, session_monitor, lifecycle, callbacks,
                   session_ops, dashboard):
        monkeypatch.setattr(module, "time", fake, raising=False)
    session_ops._KEYSTROKE_WATCHES.clear()
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
        session_ops._KEYSTROKE_WATCHES.clear()


def _callback_datas(markup) -> list[str]:
    """Every button's callback_data, from a PTB markup or a raw JSON one."""
    if markup is None:
        return []
    rows = (markup.get("inline_keyboard") if isinstance(markup, dict)
            else getattr(markup, "inline_keyboard", None)) or ()
    out = []
    for row in rows:
        for button in row:
            data = (button.get("callback_data") if isinstance(button, dict)
                    else getattr(button, "callback_data", None))
            if data:
                out.append(data)
    return out


class CardChat:
    """Every call into the chat, and the state of every message in it."""

    def __init__(self, clock) -> None:
        self.clock = clock
        self.next_id = 5000
        self.calls: list[tuple[str, float]] = []
        #: msg_id -> {"reply_to", "stop", "deleted", "text", "texts"}
        self.cards: dict[int, dict] = {}
        self.answers: dict[int, object] = {}
        self.answer_texts: dict[int, str] = {}
        #: every sendMessage's (msg_id, text, reply_to)
        self.sent: list[tuple[int, str, object]] = []
        #: msg_id -> the callback datas its buttons carry now
        self.markups: dict[int, list[str]] = {}
        #: msg_ids whose keyboard was removed by editMessageReplyMarkup
        self.markup_removed: list[int] = []
        self.reactions: dict[int, list[str]] = {}

    def new_id(self) -> int:
        self.next_id += 1
        return self.next_id

    def record(self, endpoint: str) -> None:
        self.calls.append((endpoint, self.clock()))

    def sent_texts(self) -> list[str]:
        return list(self.answer_texts.values())

    def live_cards(self) -> list[int]:
        return [m for m, c in self.cards.items()
                if c["stop"] and not c["deleted"]]

    def count(self, endpoint=None) -> int:
        return sum(1 for e, _t in self.calls
                   if endpoint is None or e == endpoint)

    def edits_of(self, msg_id: int) -> int:
        card = self.cards.get(msg_id)
        return len(card.get("texts", [])) if card else 0


def _is_now_line(markup) -> bool:
    return any(":now:" in d for d in _callback_datas(markup))


class _PtbDouble:
    """``bot._app.bot``: each call gated by the real limiter, answered by
    :class:`CardChat`."""

    _ENDPOINTS = {
        "send_message": ("sendMessage", 0),
        "edit_message_text": ("editMessageText", 1),
        "edit_message_reply_markup": ("editMessageReplyMarkup", 0),
        "delete_message": ("deleteMessage", 0),
        "send_chat_action": ("sendChatAction", 0),
        "set_message_reaction": ("setMessageReaction", 0),
        "send_document": ("sendDocument", 0),
        "delete_messages": ("deleteMessages", 0),
    }

    def __init__(self, limiter, chat: CardChat) -> None:
        self._limiter = limiter
        self._chat = chat

    def _answer(self, name, args, kwargs):
        chat = self._chat
        if name == "send_message":
            msg_id = chat.new_id()
            text = kwargs.get("text") or (args[1] if len(args) > 1 else "")
            markup = kwargs.get("reply_markup")
            chat.sent.append((msg_id, text, kwargs.get("reply_to_message_id")))
            if markup is not None:
                chat.markups[msg_id] = _callback_datas(markup)
            if markup is not None and not _is_now_line(markup):
                chat.cards[msg_id] = {
                    "reply_to": kwargs.get("reply_to_message_id"),
                    "stop": True, "deleted": False, "text": text,
                    "texts": [text], "t": chat.clock()}
            return SimpleNamespace(message_id=msg_id)
        if name == "edit_message_text":
            mid = kwargs.get("message_id")
            card = chat.cards.get(mid)
            chat.markups[mid] = _callback_datas(kwargs.get("reply_markup"))
            if card is not None:
                card["stop"] = kwargs.get("reply_markup") is not None
                card["text"] = kwargs.get("text") or (args[0] if args else "")
                card.setdefault("texts", []).append(card["text"])
            return True
        if name == "edit_message_reply_markup":
            mid = kwargs.get("message_id")
            if kwargs.get("reply_markup") is None:
                chat.markup_removed.append(mid)
                chat.markups[mid] = []
            else:
                chat.markups[mid] = _callback_datas(kwargs.get("reply_markup"))
            return True
        if name == "delete_message":
            card = chat.cards.get(kwargs.get("message_id"))
            if card is not None:
                card["deleted"] = True
            return True
        if name == "delete_messages":
            for mid in kwargs.get("message_ids") or []:
                card = chat.cards.get(mid)
                if card is not None:
                    card["deleted"] = True
            return True
        if name == "set_message_reaction":
            chat.reactions.setdefault(args[1] if len(args) > 1 else None,
                                      []).append(args[2] if len(args) > 2 else None)
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
    lim = BudgetRateLimiter(clock=vloop.time, sleep=asyncio.sleep,
                            wall_clock=vloop.wall,
                            signal_path=config.FLOOD_BACKOFF_FILE)
    rm.set_rate_limiter(lim)
    yield lim
    rm.set_rate_limiter(None)
    lim.reset()


@pytest.fixture
def rich_chat(monkeypatch, card_chat):
    """The REAL ``rich_message._post`` over an ``httpx.MockTransport``
    answered by :class:`CardChat`."""
    monkeypatch.setattr("aipager.config.BOT_TOKEN", "TESTTOKEN")

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        endpoint = request.url.path.rsplit("/", 1)[-1]
        card_chat.record(endpoint)
        msg_id = payload.get("message_id")
        if endpoint == "editMessageText":
            card = card_chat.cards.get(msg_id)
            card_chat.markups[msg_id] = _callback_datas(payload.get("reply_markup"))
            if card is not None:
                card["stop"] = "reply_markup" in payload
                card["text"] = ((payload.get("rich_message") or {})
                                .get("markdown") or payload.get("text") or "")
                card.setdefault("texts", []).append(card["text"])
        elif endpoint == "editMessageReplyMarkup":
            if not payload.get("reply_markup"):
                card_chat.markup_removed.append(msg_id)
                card_chat.markups[msg_id] = []
        elif endpoint == "sendRichMessage":
            msg_id = card_chat.new_id()
            text = ((payload.get("rich_message") or {}).get("markdown")
                    or payload.get("text") or "")
            card_chat.sent.append((msg_id, text,
                                   payload.get("reply_to_message_id")))
            if payload.get("reply_markup"):
                card_chat.markups[msg_id] = _callback_datas(
                    payload.get("reply_markup"))
            if payload.get("disable_notification"):
                card_chat.cards[msg_id] = {
                    "reply_to": payload.get("reply_to_message_id"),
                    "stop": "reply_markup" in payload, "deleted": False,
                    "text": text, "texts": [text], "t": card_chat.clock()}
            else:
                card_chat.answers[msg_id] = payload.get("reply_to_message_id")
                card_chat.answer_texts[msg_id] = text
        result = {"message_id": msg_id or 4242}
        if "reply_markup" in payload:
            result["reply_markup"] = payload["reply_markup"]
        return httpx.Response(200, json={"ok": True, "result": result})

    monkeypatch.setattr(rm, "_post", _REAL_POST)
    monkeypatch.setattr(
        rm, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    yield card_chat
    monkeypatch.setattr(rm, "_client", None)


class Pty:
    """The dtach side: which sessions are alive, every key typed."""

    def __init__(self) -> None:
        self.alive: set[str] = {NAME}
        self.keys: list[str] = []
        self.texts: list[str] = []

    async def send_keys(self, name, key, *a, **k):
        self.keys.append(key)
        return True

    async def send_text_and_enter(self, name, text, *a, **k):
        self.texts.append(text)
        return True

    async def list_sessions(self, *a, **k):
        return sorted(self.alive)

    async def is_alive(self, name, *a, **k):
        return name in self.alive


@pytest.fixture
def pty(monkeypatch):
    p = Pty()
    monkeypatch.setattr("aipager.dtach.inject.send_keys", p.send_keys)
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter",
                        p.send_text_and_enter)
    monkeypatch.setattr("aipager.dtach.inject.list_sessions", p.list_sessions)
    monkeypatch.setattr("aipager.dtach.inject.is_alive", p.is_alive)
    return p


def iso(t: float) -> str:
    return datetime.fromtimestamp(t, tz=timezone.utc).isoformat().replace(
        "+00:00", "Z")


class Replay:
    """Drives one daemon process, and then the next one."""

    def __init__(self, mk_bot, mk_update, loop, chat, limiter, pty,
                 transcript) -> None:
        self.mk_bot = mk_bot
        self.mk_update = mk_update
        self.loop = loop
        self.chat = chat
        self.limiter = limiter
        self.pty = pty
        self.transcript = transcript
        self.updates: asyncio.Queue = asyncio.Queue()
        self.tasks: list[asyncio.Task] = []
        self.toasts: list[str] = []
        self._n = 0
        self.restarts = 0
        self._boot(None)
        sess = self.bot.registry.get_or_create(NAME)
        sess.label = LABEL
        sess.scope_chat_id = CHAT
        sess.scope_kind = "dm"
        sess.transcript_path = transcript
        self.bot.registry.last_active_session = NAME
        self.sess = sess

    def _boot(self, registry) -> None:
        from aipager.dtach.hook_receiver import HookReceiver
        from aipager.state import SessionRegistry

        from aipager.policy import load_policy
        from aipager.scope import Member, Scope

        if registry is None:
            registry = SessionRegistry()
        bot = self.mk_bot(registry, scopes=[Scope(
            chat_id=CHAT, kind="dm", label="owner DM",
            members=(Member(id=OWNER, label="owner", role="owner"),))])
        bot.policy = load_policy(Path("/nonexistent/policy.yaml"),
                                 Path("/nonexistent/policy.d"))
        bot._app.bot = _PtbDouble(self.limiter, self.chat)
        bot._maybe_update_bot_name = AsyncMock()
        self.bot = bot
        self.receiver = HookReceiver(bot.registry, bot.notify)

    # ── the restart ──────────────────────────────────────────────────────
    async def stop_process(self) -> None:
        """The old process saves its state and dies: every task it had
        running is gone."""
        self.bot.registry.save()
        me = asyncio.current_task()
        others = [t for t in asyncio.all_tasks() if t is not me and not t.done()]
        for t in others:
            t.cancel()
        await asyncio.gather(*others, return_exceptions=True)

    def start_process(self) -> None:
        """A new process: a fresh registry from the state file, a new bot
        and hook receiver. ``recover_sessions`` is the caller's (so a hook
        can be handled before it, as in the daemon's start order)."""
        from aipager.state import SessionRegistry

        reg = SessionRegistry()
        reg.load()
        self._boot(reg)
        self.sess = reg.get(NAME)
        self.restarts += 1

    async def restart(self, down: float = 30.0) -> None:
        await self.stop_process()
        await asyncio.sleep(down)
        self.start_process()
        await self.bot.recover_sessions()

    def saved_entry(self) -> dict:
        from aipager import state
        data = json.loads(state.SESSION_STATE_FILE.read_text())
        return data["sessions"][NAME]

    # ── the Telegram side ───────────────────────────────────────────────
    async def _updates(self) -> None:
        while True:
            mid, text = await self.updates.get()
            try:
                await self.bot._handle_message(
                    self.mk_update(text, message_id=mid, user_id=OWNER,
                                   chat_id=CHAT), MagicMock())
            finally:
                self.updates.task_done()

    def worker(self) -> asyncio.Task:
        return asyncio.ensure_future(self._updates())

    def say(self, mid: int, text: str) -> None:
        self.updates.put_nowait((mid, text))

    def button(self, msg_id: int, verb: str) -> str:
        """The callback data of the button *verb* the chat still shows on
        message *msg_id*."""
        for data in self.chat.markups.get(msg_id, []):
            if data.rsplit(":", 1)[-1] == verb:
                return data
        raise LookupError(f"no {verb!r} button on {msg_id}: "
                          f"{self.chat.markups.get(msg_id)}")

    async def tap(self, msg_id: int, verb: str | None = None, *,
                  data: str | None = None, user_id: int = OWNER,
                  chat_id: int = CHAT) -> str | None:
        """Tap a button on *msg_id*. Returns the first toast, or None."""
        if data is None:
            data = self.button(msg_id, verb)
        answer = AsyncMock()
        query = MagicMock()
        query.data = data
        query.from_user = MagicMock()
        query.from_user.id = user_id
        query.from_user.username = "owner"
        query.message = MagicMock()
        query.message.message_id = msg_id
        query.message.chat = MagicMock()
        query.message.chat.id = chat_id
        query.message.chat_id = chat_id
        card = self.chat.cards.get(msg_id)
        query.message.text = card["text"] if card else ""
        query.message.reply_text = AsyncMock()
        query.answer = answer
        query.edit_message_text = AsyncMock()
        query.edit_message_reply_markup = AsyncMock()
        update = MagicMock()
        update.callback_query = query
        update.effective_user = query.from_user
        update.effective_chat = MagicMock()
        update.effective_chat.id = chat_id
        update.message = None
        self.last_query = query
        await self.bot._handle_callback(update, MagicMock())
        toast = None
        for call in answer.await_args_list:
            text = call.args[0] if call.args else call.kwargs.get("text")
            if text is not None:
                self.toasts.append(text)
                if toast is None:
                    toast = text
        return toast

    # ── the hook side ───────────────────────────────────────────────────
    def hook(self, **fields) -> asyncio.Task:
        self._n += 1
        fields.setdefault("session", NAME)
        fields["_seq"] = f"{self.restarts}-{self._n}"
        task = asyncio.ensure_future(
            self.receiver._on_datagram(json.dumps(fields).encode()))
        self.tasks.append(task)
        return task

    def prompt_hooks(self, mid: int, text: str) -> None:
        self.hook(hook_event_name="UserPromptSubmit", prompt=PREFIX + text)
        policy_snapshot.consume_notes_matching(NAME, PREFIX + text)
        self.hook(type="queue_pickup", consumed=[
            {"msg_id": mid, "chat_id": CHAT, "raw_text": text}], expired=[])

    def stop(self, answer: str) -> asyncio.Task:
        return self.hook(hook_event_name="Stop",
                         last_assistant_message=answer)

    # ── the prompts, as Claude Code raises them ─────────────────────────
    async def card_turn(self, mid: int = 1, text: str = "clean up") -> None:
        """A Telegram prompt taken as a new turn: its busy card is sent."""
        w = self.worker()
        self.say(mid, text)
        await self.updates.join()
        w.cancel()
        self.prompt_hooks(mid, text)
        await asyncio.sleep(1)

    def pre_tool(self, tool: str = "Bash", tool_input: dict | None = None,
                 tool_use_id: str = "toolu_01", **extra) -> asyncio.Task:
        fields = {"hook_event_name": "PreToolUse", "tool_name": tool,
                  "tool_input": tool_input or {"command": "rm -rf build"}}
        if tool_use_id:
            fields["tool_use_id"] = tool_use_id
        fields.update(extra)
        return self.hook(**fields)

    def post_tool(self, tool: str = "Bash", tool_input: dict | None = None,
                  tool_use_id: str = "toolu_01") -> asyncio.Task:
        fields = {"hook_event_name": "PostToolUse", "tool_name": tool,
                  "tool_input": tool_input or {"command": "rm -rf build"}}
        if tool_use_id:
            fields["tool_use_id"] = tool_use_id
        return self.hook(**fields)

    def permission(self, tool: str = "Bash", tool_input: dict | None = None,
                   *, reply_addr: str | None = None, request_id: str = "req-1",
                   **extra) -> asyncio.Task:
        fields = {"hook_event_name": "PermissionRequest", "tool_name": tool,
                  "tool_input": tool_input or {"command": "rm -rf build"}}
        if reply_addr is not None:
            fields["aipager_reply_addr"] = reply_addr
            fields["aipager_request_id"] = request_id
        fields.update(extra)
        return self.hook(**fields)

    async def inline_prompt(self, *, reply_addr: str | None = None,
                            tool_use_id: str = "toolu_01",
                            settle: float = 15.0, **extra) -> int:
        """A card turn that reaches a permission dialog: the card shows
        Allow / Deny. Returns the card's message id."""
        await self.card_turn()
        self.pre_tool(tool_use_id=tool_use_id)
        await asyncio.sleep(0.5)
        self.permission(reply_addr=reply_addr, **extra)
        await asyncio.sleep(settle)
        return self.sess.busy_msg_id

    async def separate_prompt(self, *, reply_addr: str | None = None,
                              settle: float = 15.0) -> int:
        """A permission dialog with no busy card (8.99): its own message.
        Returns that message's id."""
        before = {m for m, _t, _r in self.chat.sent}
        self.permission(reply_addr=reply_addr)
        await asyncio.sleep(settle)
        new = [m for m, _t, _r in self.chat.sent
               if m not in before and any(
                   d.rsplit(":", 1)[-1] == "allow"
                   for d in self.chat.markups.get(m, []))]
        assert new, f"no separate prompt was sent: {self.chat.sent}"
        return new[-1]

    def wall(self) -> float:
        return self.loop.wall()

    # ── the transcript (Claude's file) ──────────────────────────────────
    def append(self, *entries: dict) -> None:
        with open(self.transcript, "a", encoding="utf-8") as fh:
            for e in entries:
                fh.write(json.dumps(e) + "\n")


@pytest.fixture
def replay(mk_bot, mk_update, vloop, vlimiter, rich_chat, pty, tmp_path):
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_bytes(b"")
    return Replay(mk_bot, mk_update, vloop, rich_chat, vlimiter, pty,
                  str(transcript))
