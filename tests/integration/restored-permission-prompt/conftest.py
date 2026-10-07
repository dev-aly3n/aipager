"""Plumbing for the restored-permission-prompt rows (roadmap 8.102).
No assertions, no production logic.

COPIED from ``tests/integration/card-lifecycle/conftest.py`` (as this repo
does between integration directories, not imported), keeping its two
rules: nothing in ``asyncio`` is ever patched (time moves because the LOOP
is virtual, and each module's OWN ``time`` reference is rebound), and every
call goes through the real ``BudgetRateLimiter`` before it counts.

What this copy adds:

- ``aipager.bot.callbacks`` and ``aipager.bot.session_ops`` on the virtual
  clock too: a button tap reads ``time.monotonic()`` / ``time.time()``
  there directly;
- a chat that keeps every message's keyboard (a card is a message whose
  keyboard has a Stop button; a separate prompt's Allow/Deny is not a
  card) and every ``editMessageReplyMarkup``;
- :meth:`Replay.tap`, a button tap through the real ``_handle_callback``;
- :meth:`Replay.restart`, a daemon restart through the REAL state file
  (pytest's tmp home): save, forget everything the process held, load into
  the same registry object (the bot, the hook receiver and the monitor
  keep their references), then the startup recovery;
- ``inject.send_keys`` recorded (``Replay.keys``) and
  ``inject.list_sessions`` answering the live session.
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
from aipager.state import Status

CHAT = 256113222
OWNER = 12345
# A name no live session can have: a harness named like the operator's own
# session deleted that session's real policy snapshot (2026-09-28).
NAME = "claude-restoredprompt_harness"
LABEL = "aipager_boss"
#: The pending tool call's id, as Claude Code's PreToolUse carries it.
TUID = "toolu_01Restored"
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
    from aipager.bot import animation, callbacks, lifecycle, notify, session_ops
    from aipager.dtach import hook_receiver

    loop = _VirtualLoop()
    fake = types.SimpleNamespace(monotonic=loop.time, time=loop.wall,
                                 sleep=time.sleep)
    for module in (animation, notify, flood, flood_budget, state,
                   hook_receiver, session_monitor, lifecycle, callbacks,
                   session_ops):
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
        #: answer msg_id -> its text (the markdown of a rich answer)
        self.answer_texts: dict[int, str] = {}
        #: msg_id -> [emoji, ...]
        self.reactions: dict[int, list[str]] = {}
        #: every sendMessage that is not a card: msg_id -> {"text",
        #: "markup", "reply_to"}
        self.messages: dict[int, dict] = {}
        #: every editMessageReplyMarkup: (chat_id, msg_id, reply_markup)
        self.markup_edits: list[tuple] = []

    def new_id(self) -> int:
        self.next_id += 1
        return self.next_id

    def record(self, endpoint: str) -> None:
        self.calls.append((endpoint, self.clock()))

    def sent_texts(self) -> list[str]:
        """Every answer's text, in the order sent."""
        return list(self.answer_texts.values())

    def live_cards(self) -> list[int]:
        """Cards still showing their Stop button."""
        return [m for m, c in self.cards.items()
                if c["stop"] and not c["deleted"]]

    def card_sends_for(self, reply_to) -> int:
        return sum(1 for c in self.cards.values() if c["reply_to"] == reply_to)

    def count(self, endpoint=None) -> int:
        return sum(1 for e, _t in self.calls
                   if endpoint is None or e == endpoint)


def _callbacks(markup) -> dict[str, str]:
    """``{button text: callback_data}`` of a PTB markup or the JSON form
    the rich path posts."""
    out: dict[str, str] = {}
    if isinstance(markup, dict):
        rows = markup.get("inline_keyboard") or []
        for row in rows:
            for b in row:
                out[b.get("text", "")] = b.get("callback_data", "")
        return out
    for row in getattr(markup, "inline_keyboard", None) or ():
        for b in row:
            out[getattr(b, "text", "")] = getattr(b, "callback_data", "") or ""
    return out


def _is_card_markup(markup) -> bool:
    return any(cb.endswith(":stop") for cb in _callbacks(markup).values())


def _is_now_line(markup) -> bool:
    """The "⏳ Queued / ⚡ Send now" line (bot/send_now.py): a reply with a
    button, but not a card. Its button's callback carries ``now:``."""
    for row in getattr(markup, "inline_keyboard", None) or ():
        for button in row:
            if ":now:" in (getattr(button, "callback_data", None) or ""):
                return True
    return False


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
            if markup is not None and _is_card_markup(markup):
                chat.cards[msg_id] = {
                    "reply_to": kwargs.get("reply_to_message_id"),
                    "stop": True, "deleted": False, "text": text,
                    "texts": [text], "t": chat.clock(), "markup": markup}
            else:
                chat.messages[msg_id] = {
                    "text": text, "markup": markup,
                    "reply_to": kwargs.get("reply_to_message_id")}
            return SimpleNamespace(message_id=msg_id)
        if name == "edit_message_text":
            card = chat.cards.get(kwargs.get("message_id"))
            if card is not None:
                card["stop"] = kwargs.get("reply_markup") is not None
                card["markup"] = kwargs.get("reply_markup")
                card["text"] = kwargs.get("text") or (args[0] if args else "")
                card.setdefault("texts", []).append(card["text"])
            return True
        if name == "edit_message_reply_markup":
            chat.markup_edits.append((kwargs.get("chat_id"),
                                      kwargs.get("message_id"),
                                      kwargs.get("reply_markup")))
            return True
        if name == "delete_message":
            card = chat.cards.get(kwargs.get("message_id"))
            if card is not None:
                card["deleted"] = True
                card["deleted_at"] = chat.clock()
            return True
        if name == "delete_messages":
            for mid in kwargs.get("message_ids") or []:
                card = chat.cards.get(mid)
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
                card["markup"] = payload.get("reply_markup")
                card["text"] = ((payload.get("rich_message") or {})
                                .get("markdown") or payload.get("text") or "")
                card.setdefault("texts", []).append(card["text"])
        elif endpoint == "sendRichMessage":
            msg_id = card_chat.new_id()
            if payload.get("disable_notification"):
                # A moved card (animation._send_card_copy): the whole
                # card in one silent send, its Stop button unless final.
                text = (payload.get("rich_message") or {}).get("markdown") or ""
                card_chat.cards[msg_id] = {
                    "reply_to": payload.get("reply_to_message_id"),
                    "stop": "reply_markup" in payload, "deleted": False,
                    "text": text, "texts": [text], "t": card_chat.clock(),
                    "markup": payload.get("reply_markup")}
            else:
                card_chat.answers[msg_id] = payload.get("reply_to_message_id")
                card_chat.answer_texts[msg_id] = (
                    (payload.get("rich_message") or {}).get("markdown")
                    or payload.get("text") or "")
        result = {"message_id": msg_id or 4242}
        if "reply_markup" in payload:
            result["reply_markup"] = payload["reply_markup"]
        return httpx.Response(200, json={"ok": True, "result": result})

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

    from aipager.policy import load_policy
    from aipager.scope import Member, Scope

    # Scope mode with the tapper as the chat's owner, so an answer's audit
    # record and chat line carry who tapped it.
    bot = mk_bot(scopes=[Scope(chat_id=CHAT, kind="dm", label="owner DM",
                               members=(Member(id=OWNER, label="owner",
                                               role="owner"),))])
    bot.policy = load_policy(tmp_path / "no-policy.yaml",
                             tmp_path / "no-policy.d")
    bot._app.bot = _PtbDouble(vlimiter, rich_chat)
    bot._maybe_update_bot_name = AsyncMock()
    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter",
                        AsyncMock(return_value=True))
    keys: list[str] = []

    async def _send_keys(name, key, *args, **kwargs):
        keys.append(key)
        return True

    monkeypatch.setattr("aipager.dtach.inject.send_keys", _send_keys)
    monkeypatch.setattr("aipager.dtach.inject.list_sessions",
                        AsyncMock(return_value=[NAME]))
    bot._test_keys = keys
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
        self.keys: list[str] = bot._test_keys
        self.bot = bot
        self.receiver = receiver
        self.chat = chat
        self.transcript = transcript
        self.mk_update = mk_update
        self.loop = loop
        self.updates: asyncio.Queue = asyncio.Queue()
        self.tasks: list[asyncio.Task] = []
        self.sess = bot.registry.get_or_create(NAME)
        self.sess.label = LABEL
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

    # ── button taps ─────────────────────────────────────────────────────
    def buttons(self, msg_id: int) -> dict[str, str]:
        """``{button text: callback_data}`` of message *msg_id* as the chat
        shows it now (a card's latest keyboard, or a message's)."""
        if msg_id in self.chat.cards:
            return _callbacks(self.chat.cards[msg_id].get("markup"))
        return _callbacks(self.chat.messages[msg_id].get("markup"))

    async def tap(self, msg_id: int, data: str, *, user_id: int = OWNER,
                  chat_id: int = CHAT, text: str = "") -> str | None:
        """Tap a button with callback *data* on message *msg_id*. Returns
        the first toast text, or ``None``. ``self.last_query`` keeps the
        query (its edit mocks are readable)."""
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
        query.message.text = text
        query.message.reply_text = AsyncMock()
        query.answer = answer
        query.edit_message_text = AsyncMock()
        query.edit_message_reply_markup = AsyncMock()
        update = MagicMock()
        update.callback_query = query
        update.effective_user = query.from_user
        update.effective_chat = query.message.chat
        update.message = None
        self.last_query = query
        await self.bot._handle_callback(update, MagicMock())
        for call in answer.await_args_list:
            toast = call.args[0] if call.args else call.kwargs.get("text")
            if toast is not None:
                return toast
        return None

    def cb(self, msg_id: int, verb: str) -> str:
        """The callback data of the *verb* button (``allow``, ``deny``,
        ``opt0`` ...) on message *msg_id*."""
        for data in self.buttons(msg_id).values():
            if data.endswith(":" + verb):
                return data
        raise KeyError(f"no {verb!r} button on {msg_id}: {self.buttons(msg_id)}")

    async def shown(self, msg_id: int, verb: str, limit: float = 60.0) -> None:
        """Wait until message *msg_id* shows a *verb* button: the card's
        "Waiting" edit queues behind the limiter like any other."""
        waited = 0.0
        while not any(d.endswith(":" + verb)
                      for d in self.buttons(msg_id).values()):
            if waited >= limit:
                raise AssertionError(f"{msg_id} never showed {verb!r}")
            await asyncio.sleep(1)
            waited += 1

    # ── the turn up to its prompt, as the hooks drive it ────────────────
    async def open_card(self) -> int:
        """A running turn with its busy card (no prompt yet)."""
        self.bot.registry.transition(self.sess.name, Status.BUSY)
        self.sess.trigger_msg_id = 1
        await self.bot._send_busy_and_animate(self.sess)
        await asyncio.sleep(1)
        return self.sess.busy_msg_id

    async def permission(self, *, tool: str = "Bash",
                         command: str = "rm -rf build",
                         tool_use_id: str = TUID, hook: dict | None = None,
                         settle: float = 2.0) -> None:
        """Claude Code asking for a tool: the parent's PreToolUse (with the
        call's id), then the PermissionRequest (with the parked hook's
        reply channel when *hook* is given)."""
        tool_input = {"command": command}
        self.hook(hook_event_name="PreToolUse", tool_name=tool,
                  tool_input=tool_input, tool_use_id=tool_use_id)
        await asyncio.sleep(0.2)
        reply = ({"aipager_reply_addr": hook["addr"],
                  "aipager_request_id": hook["request_id"]} if hook else {})
        self.hook(hook_event_name="PermissionRequest", tool_name=tool,
                  tool_input=tool_input, **reply)
        await asyncio.sleep(settle)

    async def ask(self, questions: list[dict], *,
                  tool_use_id: str = "toolu_ask", settle: float = 2.0) -> None:
        """Claude asking the user (AskUserQuestion's PreToolUse)."""
        self.hook(hook_event_name="PreToolUse", tool_name="AskUserQuestion",
                  tool_input={"questions": questions}, tool_use_id=tool_use_id)
        await asyncio.sleep(settle)

    # ── a daemon restart, through the real state file ───────────────────
    async def restart(self, *, recover: bool = True, edit=None) -> None:
        """Save, drop everything this process held, load the file into the
        same registry, then (unless *recover* is False) run the startup
        recovery. The hook channel, the keystroke watches and the animation
        tasks of the old process are gone, as at a real restart."""
        from aipager import state as state_mod
        from aipager.bot import session_ops

        reg = self.bot.registry
        reg.save()
        if edit is not None:
            # What the file says when the next process reads it.
            data = json.loads(state_mod.SESSION_STATE_FILE.read_text())
            edit(data)
            state_mod.SESSION_STATE_FILE.write_text(json.dumps(data))
        for sess in list(reg.all_sessions().values()):
            self.bot._stop_animation(sess)
        for task in list(session_ops._KEYSTROKE_WATCHES.values()):
            task.cancel()
        session_ops._KEYSTROKE_WATCHES.clear()
        await asyncio.sleep(0)
        self.bot._resent_prompts.clear()
        reg._sessions.clear()
        reg._msg_map.clear()
        reg.load()
        self.sess = reg.get(NAME)
        if recover:
            await self.bot.recover_sessions()




@pytest.fixture
def replay(daemon, mk_update, vloop):
    bot, receiver, chat, transcript = daemon
    return Replay(bot, receiver, chat, transcript, mk_update, vloop)
