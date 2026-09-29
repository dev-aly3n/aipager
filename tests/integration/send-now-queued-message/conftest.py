"""Plumbing for the "⚡ Send now" rows: the "⏳ Queued" reply line under a
message Claude Code still holds in its queue, its button and ``/now``.
No assertions, no production logic.

A copy (not an import) of ``tests/integration/card-lifecycle/conftest.py``,
as this repo does between integration directories, extended with: the
queued LINE as its own kind of message (never a card), a callback-tap
driver, a command driver, a terminal-write recorder (``pty``) and an
``enqueue`` transcript helper. What follows is the source harness's own
description.

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
#: A name no live session on a developer's machine can carry, so nothing
#: here can ever meet a real session's socket.
NAME = "claude-sendnow_harness"
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
        #: card msg_id -> {"reply_to", "stop", "deleted", "text", "texts"}
        #: (``text`` the latest version, ``texts`` every version in order)
        self.cards: dict[int, dict] = {}
        #: answer msg_id -> reply_to
        self.answers: dict[int, object] = {}
        #: msg_id -> [emoji, ...]
        self.reactions: dict[int, list[str]] = {}
        #: "⏳ Queued" line msg_id -> {"reply_to", "text", "button_text",
        #: "callback_data", "deleted", "t"} (plus "edits", once edited: an
        #: edit updates text and button): a sendMessage whose first
        #: button's callback_data carries ``:now:`` (never a card).
        self.lines: dict[int, dict] = {}
        #: every toast a tap answered with, in order (``None`` never listed)
        self.toasts: list[str] = []
        #: ``(chat_id, message_id)`` of every deleteMessage that reached
        #: "Telegram", in order
        self.deletes: list[tuple[object, object]] = []
        #: the message ids of each delete CALL (deleteMessage or the batch
        #: deleteMessages), in order
        self.delete_calls: list[list] = []
        #: ``(text, kwargs)`` of every sendMessage that reached "Telegram"
        self.sent: list[tuple[str, dict]] = []

    def new_id(self) -> int:
        self.next_id += 1
        return self.next_id

    def record(self, endpoint: str) -> None:
        self.calls.append((endpoint, self.clock()))

    def live_cards(self) -> list[int]:
        """Cards still showing their Stop button."""
        return [m for m, c in self.cards.items()
                if c["stop"] and not c["deleted"]]

    def live_lines(self) -> list[int]:
        """Queued lines sent and not deleted."""
        return [m for m, ln in self.lines.items() if not ln["deleted"]]

    def line_for(self, reply_to) -> dict | None:
        """The (latest) queued line replying to *reply_to*, deleted or not,
        with its own ``id`` added."""
        found = None
        for m, ln in self.lines.items():
            if ln["reply_to"] == reply_to:
                found = dict(ln, id=m)
        return found

    def card_sends_for(self, reply_to) -> int:
        return sum(1 for c in self.cards.values() if c["reply_to"] == reply_to)

    def count(self, endpoint=None) -> int:
        return sum(1 for e, _t in self.calls
                   if endpoint is None or e == endpoint)


def _first_button(markup):
    rows = getattr(markup, "inline_keyboard", None) or ()
    for row in rows:
        for button in row:
            return button
    return None


def _button_count(markup) -> int:
    rows = getattr(markup, "inline_keyboard", None) or ()
    return sum(len(row) for row in rows)


class _PtbDouble:
    """``bot._app.bot``: each call gated by the real limiter, answered by
    :class:`CardChat`."""

    _ENDPOINTS = {
        "send_message": ("sendMessage", 0),
        "edit_message_text": ("editMessageText", 1),
        "delete_message": ("deleteMessage", 0),
        "delete_messages": ("deleteMessages", 0),
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
            chat.sent.append((kwargs.get("text")
                              or (args[1] if len(args) > 1 else ""), kwargs))
            button = _first_button(kwargs.get("reply_markup"))
            data = getattr(button, "callback_data", None) or ""
            if ":now:" in data:
                text = kwargs.get("text") or (args[1] if len(args) > 1 else "")
                chat.lines[msg_id] = {
                    "reply_to": kwargs.get("reply_to_message_id"),
                    "text": text,
                    "button_text": getattr(button, "text", None),
                    "buttons": _button_count(kwargs.get("reply_markup")),
                    "callback_data": data,
                    "deleted": False,
                    "t": chat.clock(),
                }
                return SimpleNamespace(message_id=msg_id)
            if kwargs.get("reply_markup") is not None:
                text = kwargs.get("text") or (args[1] if len(args) > 1 else "")
                chat.cards[msg_id] = {
                    "reply_to": kwargs.get("reply_to_message_id"),
                    "stop": True, "deleted": False, "text": text,
                    "texts": [text], "t": chat.clock()}
            return SimpleNamespace(message_id=msg_id)
        if name == "edit_message_text":
            card = chat.cards.get(kwargs.get("message_id"))
            if card is not None:
                card["stop"] = kwargs.get("reply_markup") is not None
                card["text"] = kwargs.get("text") or (args[0] if args else "")
                card.setdefault("texts", []).append(card["text"])
            line = chat.lines.get(kwargs.get("message_id"))
            if line is not None:
                # An edit replaces the text and the keyboard: one sent
                # without reply_markup removes the button.
                markup = kwargs.get("reply_markup")
                button = _first_button(markup)
                line["text"] = kwargs.get("text") or (args[0] if args else "")
                line["button_text"] = getattr(button, "text", None)
                line["buttons"] = _button_count(markup)
                line["callback_data"] = getattr(button, "callback_data", None) or ""
                line["edits"] = line.get("edits", 0) + 1
            return True
        if name == "delete_message":
            chat.delete_calls.append([kwargs.get("message_id")])
            chat.deletes.append((kwargs.get("chat_id"), kwargs.get("message_id")))
            card = chat.cards.get(kwargs.get("message_id"))
            if card is not None:
                card["deleted"] = True
            line = chat.lines.get(kwargs.get("message_id"))
            if line is not None:
                line["deleted"] = True
                line.setdefault("deleted_t", chat.clock())
            return True
        if name == "delete_messages":
            ids = list(kwargs.get("message_ids") or [])
            chat.delete_calls.append(ids)
            for mid in ids:
                chat.deletes.append((kwargs.get("chat_id"), mid))
                card = chat.cards.get(mid)
                if card is not None:
                    card["deleted"] = True
                line = chat.lines.get(mid)
                if line is not None:
                    line["deleted"] = True
                    line.setdefault("deleted_t", chat.clock())
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
                card.setdefault("texts", []).append(card["text"])
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
        self.sess.label = "sendnow_harness"
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

    def tool_start(self, summary: str, agent_id: str = "") -> asyncio.Task:
        """A tool call's PreToolUse alone: the step is still running."""
        extra = {"agent_id": agent_id} if agent_id else {}
        return self.hook(hook_event_name="PreToolUse", tool_name="Bash",
                         tool_input={"command": summary}, **extra)

    def tool_end(self, summary: str, agent_id: str = "", *,
                 event: str = "PostToolUse") -> asyncio.Task:
        """The PostToolUse (or *event*, e.g. PostToolUseFailure) that ends
        a :meth:`tool_start` step."""
        extra = {"agent_id": agent_id} if agent_id else {}
        return self.hook(hook_event_name=event, tool_name="Bash",
                         tool_input={"command": summary}, **extra)

    def stop(self, answer: str) -> asyncio.Task:
        return self.hook(hook_event_name="Stop",
                         last_assistant_message=answer)

    def append(self, *entries: dict) -> None:
        with open(self.transcript, "a", encoding="utf-8") as fh:
            for e in entries:
                fh.write(json.dumps(e) + "\n")

    def enqueue(self, text: str) -> None:
        """Claude Code's transcript evidence that it holds *text* queued."""
        self.append({"type": "queue-operation", "operation": "enqueue",
                     "content": PREFIX + text})

    # ── button taps and commands ────────────────────────────────────────
    async def tap(self, line_id: int, *, user_id: int = 12345,
                  chat_id: int = CHAT, data: str | None = None) -> str | None:
        """Tap the button on message *line_id* (by default its recorded
        callback_data). Returns the first toast text, or ``None``."""
        if data is None:
            data = self.chat.lines[line_id]["callback_data"]
        answer = AsyncMock()
        query = MagicMock()
        query.data = data
        query.from_user = MagicMock()
        query.from_user.id = user_id
        query.message = MagicMock()
        query.message.message_id = line_id
        query.message.chat = MagicMock()
        query.message.chat.id = chat_id
        query.message.chat_id = chat_id
        line = self.chat.lines.get(line_id)
        query.message.text = line["text"] if line else ""
        query.message.reply_text = AsyncMock()
        query.answer = answer
        query.edit_message_text = AsyncMock()
        update = MagicMock()
        update.callback_query = query
        update.effective_user = MagicMock()
        update.effective_user.id = user_id
        update.effective_chat = MagicMock()
        update.effective_chat.id = chat_id
        #: the last tap's query (its ``edit_message_text`` mock is readable)
        self.last_query = query
        await self.bot._handle_callback(update, MagicMock())
        toast = None
        for call in answer.await_args_list:
            text = call.args[0] if call.args else call.kwargs.get("text")
            if text is not None:
                self.chat.toasts.append(text)
                if toast is None:
                    toast = text
        return toast

    async def cmd(self, handler_name: str, text: str, *, user_id: int = 12345,
                  chat_id: int = CHAT, message_id: int = 9000) -> list[str]:
        """Run ``bot.<handler_name>`` (e.g. ``_handle_now_cmd``) on a command
        message. Returns every text it replied with."""
        update = self.mk_update(text, message_id=message_id, user_id=user_id,
                                chat_id=chat_id)
        update.message.chat = MagicMock()
        update.message.chat.id = chat_id
        update.message.chat_id = chat_id
        update.effective_message = update.message
        await getattr(self.bot, handler_name)(update, MagicMock())
        #: every ``reply_text`` call's kwargs, e.g. a confirm keyboard
        self.last_reply_kwargs = [
            call.kwargs for call in update.message.reply_text.await_args_list]
        out = []
        for call in update.message.reply_text.await_args_list:
            reply = call.args[0] if call.args else call.kwargs.get("text")
            out.append(reply)
        return out

    async def settle(self, seconds: float) -> None:
        await asyncio.sleep(seconds)

    # ── scenario plumbing for the send-now rows ──────────────────────────
    def worker(self) -> asyncio.Task:
        return asyncio.ensure_future(self._updates())

    async def turn(self, mid: int, text: str, *, tools: int = 1) -> None:
        """A Telegram prompt reaching an idle Claude, taken as a new turn."""
        self.say(mid, text)
        await self.updates.join()
        self.prompt_hooks(mid, text)
        await asyncio.sleep(1)
        for i in range(tools):
            self.tool(f"{text} step {i}")
            await asyncio.sleep(1)

    async def queue(self, mid: int, text: str, *, evidence: bool = True,
                    pickups: int = 1) -> float:
        """*text* sent while a turn runs: typed into Claude's queue, its
        transcript ``enqueue`` line (unless *evidence* is False) and its
        ``queue_pickup``. Returns the virtual time of the pick-up."""
        self.say(mid, text)
        await self.updates.join()
        if evidence:
            self.enqueue(text)
        # Claude Code fires UserPromptSubmit the moment a message is queued
        # behind a running turn (not when it is later taken), just before
        # the pick-up of the note the injection left.
        await self.hook(hook_event_name="UserPromptSubmit",
                        prompt=PREFIX + text)
        policy_snapshot.consume_notes_matching(NAME, PREFIX + text)
        t0 = self.loop.time()
        for _ in range(pickups):
            await self.hook(type="queue_pickup", consumed=[
                {"msg_id": mid, "chat_id": CHAT, "raw_text": text}],
                expired=[])
        return t0

    def fate(self, operation: str, text: str | None = None, *,
             reason: str | None = None) -> None:
        """A queue-operation line: ``remove`` (with *reason*), ``dequeue``
        or ``popAll``."""
        entry = {"type": "queue-operation", "operation": operation}
        if reason is not None:
            entry["reason"] = reason
        if text is not None:
            entry["content"] = PREFIX + text
        self.append(entry)

    def absorb(self, text: str) -> None:
        self.fate("remove", text, reason="absorbed_mid_turn")

    async def wait_line(self, reply_to, timeout: float = 15.0) -> dict | None:
        """Poll (0.1 s virtual) until a line replying to *reply_to* exists."""
        end = self.loop.time() + timeout
        while self.loop.time() < end:
            line = self.chat.line_for(reply_to)
            if line is not None:
                return line
            await asyncio.sleep(0.1)
        return self.chat.line_for(reply_to)

    async def wait_deleted(self, line_id: int, timeout: float = 10.0) -> bool:
        end = self.loop.time() + timeout
        while self.loop.time() < end:
            if self.chat.lines[line_id]["deleted"]:
                return True
            await asyncio.sleep(0.1)
        return self.chat.lines[line_id]["deleted"]

    def restarted_bot(self, mk_bot):
        """Save the registry, load it into a fresh registry and bot (the
        daemon after a restart), talking to the same fake chat through the
        same limiter."""
        from aipager.state import SessionRegistry

        self.bot.registry.save()
        registry = SessionRegistry()
        registry.load()
        bot = mk_bot(registry)
        bot._app.bot = _PtbDouble(self.bot._app.bot._limiter, self.chat)
        bot._maybe_update_bot_name = AsyncMock()
        return bot




class PtyRecorder:
    """Every terminal write, through the one subprocess seam they all share
    (``inject._run``): ``(t, stdin_bytes)`` in order."""

    def __init__(self, clock) -> None:
        self.clock = clock
        self.writes: list[tuple[float, bytes]] = []
        #: set False to make every write fail
        self.ok = True

    def data(self) -> list[bytes]:
        return [b for _t, b in self.writes]

    def chords(self) -> int:
        """How many Ctrl+X, Ctrl+S pairs were written, as separate writes."""
        seq = self.data()
        return sum(1 for a, b in zip(seq, seq[1:])
                   if a == b"\x18" and b == b"\x13")


@pytest.fixture
def pty(monkeypatch, vloop, tmp_path):
    """Record terminal writes instead of running ``dtach -p``, and stub the
    session process calls /kill, /restart and startup make. Socket paths
    point into this test's own directory, where no socket ever exists:
    /restart's poll sees the session gone without reading /tmp."""
    from aipager.dtach import inject

    rec = PtyRecorder(vloop.time)
    monkeypatch.setattr(inject, "SOCK_PREFIX", str(tmp_path / "claude-dtach-"))

    async def _run(args, stdin=b"", timeout=5):
        rec.writes.append((rec.clock(), bytes(stdin)))
        return (rec.ok, "")

    monkeypatch.setattr(inject, "_run", _run)
    monkeypatch.setattr(inject, "kill_session", AsyncMock(return_value=True))
    monkeypatch.setattr(inject, "launch_session",
                        AsyncMock(return_value=(True, "")))
    monkeypatch.setattr(inject, "list_sessions",
                        AsyncMock(return_value=[NAME]))
    return rec


def read_only_team():
    """A team for the read_only rows: 12345 may prompt, 999 is read_only."""
    from aipager.team import Role, Team, User as TeamUser

    return Team(group_id=CHAT, users={
        12345: TeamUser(id=12345, label="owner", role=Role.ADMIN),
        999: TeamUser(id=999, label="ro", role=Role.READ_ONLY),
    })


@pytest.fixture
def replay(daemon, mk_update, vloop):
    bot, receiver, chat, transcript = daemon
    return Replay(bot, receiver, chat, transcript, mk_update, vloop)
