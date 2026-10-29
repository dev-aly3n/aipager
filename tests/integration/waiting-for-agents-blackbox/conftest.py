"""Black-box fixtures for the "waiting for agents shows as working"
state (roadmap 8.121), derived from design.md's success criteria and
entrypoints.md only.

The replay harness (a virtual-time loop, a Telegram that remembers every
card, a real ``HookReceiver`` driving a real ``TelegramBot``) is copied,
not imported, from ``tests/integration/show-background-shells-on-card/
conftest.py``, as this repo does between integration directories. Nothing
in ``asyncio`` is patched; each module's OWN ``time`` is rebound.

Added here: a static kit (a bot for the bar, /status and /x, sessions
built from their public fields, the Mini App over aiohttp with signed
initData, and the CLI's rows read from the file the registry writes).
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
# A name no live session can have (live ones are claude-<project dir>, and
# no project here is called "agentwait_bb_harness"): a harness named like the
# operator's own session deleted that session's real policy snapshot
# (2026-09-28).
NAME = "claude-agentwait_bb_harness"
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
                "the virtual loop spun for too many REAL seconds - something "
                "is polling without a timer")
        if self._scheduled and not self._ready:
            live = [h._when for h in self._scheduled if not h._cancelled]
            if live and min(live) > self._virtual:
                self._virtual = min(live) + _LATENCY
        super()._run_once()


@pytest.fixture
def vloop(monkeypatch):
    """A virtual-time loop, with every module that reads time for this
    feature bound to it - each module's OWN ``time``, never the global
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
    # Claude's files (the session registry read before a message is typed
    # into an idle session, roadmap 8.127) are read inline: while a worker
    # thread runs the loop has nothing ready, so its clock would jump to
    # the next timer and a read of microseconds would take virtual seconds.
    from aipager import claude_jobs

    async def inline(fn, *args, **kwargs):
        return fn(*args, **kwargs)
    monkeypatch.setattr(claude_jobs, "_to_thread", inline)
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
        #: answer msg_id -> its markdown, every version in order
        self.answer_texts: dict[int, list[str]] = {}
        #: plain messages sent without a keyboard: (text, reply_to)
        self.plain: list[tuple[str, object]] = []
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
                text = kwargs.get("text") or (args[1] if len(args) > 1 else "")
                chat.cards[msg_id] = {
                    "reply_to": kwargs.get("reply_to_message_id"),
                    "stop": True, "deleted": False, "text": text,
                    "texts": [text]}
            else:
                chat.plain.append((
                    kwargs.get("text") or (args[1] if len(args) > 1 else ""),
                    kwargs.get("reply_to_message_id")))
            return SimpleNamespace(message_id=msg_id)
        if name == "edit_message_text":
            card = chat.cards.get(kwargs.get("message_id"))
            if card is not None:
                card["stop"] = kwargs.get("reply_markup") is not None
                card["text"] = kwargs.get("text") or (args[0] if args else "")
                card.setdefault("texts", []).append(card["text"])
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
    the way ``lifecycle._make_builder`` installs it - with the chat's
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
    answered by :class:`CardChat` - so card edits and answers are metered
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
            elif msg_id in card_chat.answer_texts:
                card_chat.answer_texts[msg_id].append(
                    (payload.get("rich_message") or {}).get("markdown")
                    or payload.get("text") or "")
        elif endpoint == "sendRichMessage":
            msg_id = card_chat.new_id()
            text = ((payload.get("rich_message") or {}).get("markdown")
                    or payload.get("text") or "")
            if payload.get("disable_notification"):
                # A moved card (animation._send_card_copy): the whole
                # card in one silent send, its Stop button unless final.
                card_chat.cards[msg_id] = {
                    "reply_to": payload.get("reply_to_message_id"),
                    "stop": "reply_markup" in payload, "deleted": False,
                    "text": text, "texts": [text]}
            else:
                card_chat.answers[msg_id] = payload.get("reply_to_message_id")
                card_chat.answer_texts[msg_id] = [text]
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
        self.sess.label = "frontend"
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

    # ── background shells ───────────────────────────────────────────────
    def bg_bash(self, desc: str, task_id: str, *, command: str | None = None,
                agent_id: str = "", response_extra: dict | None = None,
                tool_name: str = "Bash") -> None:
        """A ``Bash`` call Claude Code moved to the background: its
        PreToolUse, then the PostToolUse whose ``tool_response`` carries
        the ``backgroundTaskId`` (sent at launch, with empty output)."""
        tool_input = {"command": command or f"run {desc}"}
        if desc:
            tool_input["description"] = desc
        response = {"backgroundTaskId": task_id, "stdout": "", "stderr": "",
                    "interrupted": False, "isImage": False,
                    "noOutputExpected": False}
        response.update(response_extra or {})
        extra = {"agent_id": agent_id} if agent_id else {}
        self.hook(hook_event_name="PreToolUse", tool_name=tool_name,
                  tool_input=tool_input, **extra)
        self.hook(hook_event_name="PostToolUse", tool_name=tool_name,
                  tool_input=tool_input, tool_response=response, **extra)

    def task_stop(self, task_id: str, *, tool_name: str = "TaskStop",
                  key: str = "task_id") -> None:
        self.hook(hook_event_name="PreToolUse", tool_name=tool_name,
                  tool_input={key: task_id})
        self.hook(hook_event_name="PostToolUse", tool_name=tool_name,
                  tool_input={key: task_id},
                  tool_response={"message": f"Successfully stopped task: "
                                 f"{task_id}", "task_id": task_id,
                                 "task_type": "local_bash"})

    def enqueue(self, content: str) -> None:
        """The transcript's ``queue-operation`` enqueue line."""
        self.append({"type": "queue-operation", "operation": "enqueue",
                     "timestamp": "2026-09-28T01:03:34.000Z",
                     "content": content})

    def absorb(self, content: str) -> None:
        self.append({"type": "queue-operation", "operation": "remove",
                     "reason": "absorbed_mid_turn",
                     "timestamp": "2026-09-28T01:03:35.000Z",
                     "content": content})

    def wake(self, prompt: str) -> asyncio.Task:
        """The wake-up turn Claude Code starts for a notification."""
        return self.hook(hook_event_name="UserPromptSubmit", prompt=prompt)

    @staticmethod
    def notification(*args, **kwargs) -> str:
        # Exposed here: this directory's name is not an importable
        # package, so test modules cannot import from this conftest.
        return notification(*args, **kwargs)


def notification(task_id: str, status: str | None, summary: str = "",
                 *, result: str | None = None, note: str | None = None) -> str:
    """One ``<task-notification>`` block in Claude Code 2.1.283's shape.
    ``status=None`` is a Monitor event's (no ``<status>`` at all)."""
    parts = ["<task-notification>", f"<task-id>{task_id}</task-id>",
             f"<tool-use-id>toolu_{task_id}</tool-use-id>",
             f"<output-file>/x/tasks/{task_id}.output</output-file>"]
    if status is not None:
        parts.append(f"<status>{status}</status>")
    if summary:
        parts.append(f"<summary>{summary}</summary>")
    if note is not None:
        parts.append(f"<note>{note}</note>")
    if result is not None:
        parts.append(f"<result>{result}</result>")
    parts.append("</task-notification>")
    return "\n".join(parts)




@pytest.fixture
def replay(daemon, mk_update, vloop):
    bot, receiver, chat, transcript = daemon
    return Replay(bot, receiver, chat, transcript, mk_update, vloop)


# ── the static kit ─────────────────────────────────────────────────────────
#
# Sessions built from TrackedSession's public fields (the inputs
# design.md names for ``job_background_open()``: ``active_subagents``,
# ``bg_shells`` with ``in_job``, ``job_grace_until``,
# ``job_continuation_active``), and every surface read through its entry
# point in entrypoints.md.

LABEL = "x1"
SCOPE_CHAT = -100
ADMIN = 555
READONLY = 888
BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"


def _agent(i: int) -> dict:
    return {"type": "Explore", "started_at": time.monotonic() - 30,
            "history_idx": None}


def _shell(label: str, in_job: bool) -> dict:
    return {"label": label, "summary": f"Bash: {label}",
            "started": time.monotonic() - 30, "in_job": in_job,
            "history_idx": None}


def make_session(*, status=None, agents: int = 0, job_shells: int = 0,
                 other_shells: int = 0, other_agents: int = 0,
                 name: str = "claude-x1", label: str = LABEL,
                 chat: int = CHAT):
    """A session in *status* whose JOB runs *agents* agents and
    *job_shells* shells, plus *other_shells* shells outside the job and
    *other_agents* agents tracked session-wide only (``bg_agents``)."""
    from aipager.state import Status, TrackedSession
    sess = TrackedSession(name=name, label=label,
                          status=Status.IDLE if status is None else status)
    sess.scope_chat_id, sess.scope_kind = chat, "dm"
    sess.active_subagents = {f"a{i}": _agent(i) for i in range(agents)}
    # Every job agent is also running session-wide (both tables are fed
    # by every SubagentStart, design.md "Which count is right").
    sess.bg_agents = {f"a{i}": {"label": "Explore"} for i in range(agents)}
    for i in range(other_agents):
        sess.bg_agents[f"o{i}"] = {"label": "Plan"}
    shells = {f"j{i}": _shell(f"job{i}", True) for i in range(job_shells)}
    shells.update({f"s{i}": _shell(f"server{i}", False)
                   for i in range(other_shells)})
    sess.bg_shells = shells
    return sess


@pytest.fixture
def chat_bot(mk_bot, monkeypatch):
    """A bot whose pinned bar and /status render for :data:`CHAT`."""
    from aipager.bot import dashboard
    monkeypatch.setattr(dashboard, "CHAT_ID", str(CHAT))
    monkeypatch.setattr(dashboard, "get_rate_limiter", lambda: None)
    b = mk_bot()
    b._read_status_file = lambda name: None
    return b


def register(bot_or_registry, *sessions):
    registry = getattr(bot_or_registry, "registry", bot_or_registry)
    for s in sessions:
        registry._sessions[s.name] = s
    return sessions[0] if len(sessions) == 1 else sessions


# ---- the Mini App over HTTP ------------------------------------------------

def _sign(fields: dict) -> str:
    import hashlib
    import hmac
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    return hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()


def init_header(user_id: int = ADMIN) -> dict:
    from urllib.parse import urlencode
    fields = {"auth_date": str(int(time.time())),
              "user": json.dumps({"id": user_id, "first_name": "Test"})}
    fields["hash"] = _sign(fields)
    return {"X-Telegram-Init-Data": urlencode(fields)}


@pytest.fixture
def miniapp(mk_bot, monkeypatch):
    """A Mini App server over a fresh registry, one team chat with an
    admin and a read-only member."""
    from pathlib import Path

    from aipager.miniapp.server import MiniAppServer
    from aipager.policy import load_policy
    from aipager.scope import Member, Scope
    from aipager.state import SessionRegistry

    monkeypatch.setattr("aipager.config.BOT_TOKEN", BOT_TOKEN)
    registry = SessionRegistry()
    scope = Scope(chat_id=SCOPE_CHAT, kind="group", label="team",
                  members=(Member(id=ADMIN, label="alice", role="admin"),
                           Member(id=READONLY, label="cleo", role="read_only")),
                  root="/home/alice")
    bot = mk_bot(registry, scopes=[scope])
    bot.policy = load_policy(Path("/nonexistent/policy.yaml"),
                             Path("/nonexistent/policy.d"))
    bot._app.bot.username = "aipager_test_bot"
    bot._update_bot_commands = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    return MiniAppServer(bot, registry, port=8769)


def miniapp_session(server, **kw):
    """:func:`make_session`, registered in the Mini App's team chat."""
    sess = make_session(chat=SCOPE_CHAT, **kw)
    sess.cwd = "/home/alice/proj"
    sess.perm_mode = "ask"
    server.registry._sessions[sess.name] = sess
    return sess


def miniapp_call(server, run_async, method: str, path: str, user: int = ADMIN):
    """``(status, json)`` of one call through the app's own aiohttp app."""
    from aiohttp.test_utils import TestClient, TestServer

    async def run():
        client = TestClient(TestServer(server._build_app()))
        await client.start_server()
        try:
            resp = await getattr(client, method)(path, headers=init_header(user))
            try:
                data = await resp.json()
            except Exception:  # noqa: BLE001 - a non-JSON body is the answer
                data = None
            return resp.status, data
        finally:
            await client.close()
    return run_async(run())


def miniapp_rows(server, run_async) -> list[dict]:
    status, data = miniapp_call(server, run_async, "get", "/api/sessions")
    assert status == 200, data
    return data["sessions"] if isinstance(data, dict) and "sessions" in data else data


def miniapp_row(server, run_async, label: str = LABEL) -> dict:
    return next(r for r in miniapp_rows(server, run_async) if r["label"] == label)


def miniapp_detail(server, run_async, label: str = LABEL) -> dict:
    status, data = miniapp_call(server, run_async, "get", f"/api/sessions/{label}")
    assert status == 200, data
    return data


# ---- the CLI ---------------------------------------------------------------

def cli_rows(monkeypatch, live) -> dict[str, dict]:
    """``_gather_sessions()``'s rows by name, the dtach side faked (*live*
    is the set of names with a live terminal)."""
    from aipager import status
    monkeypatch.setattr(status, "_live_sessions", lambda: set(live))
    monkeypatch.setattr(status, "_read_statusline", lambda name: {})
    rows, _live = status._gather_sessions()
    return {r["name"]: r for r in rows}


def saved_sessions() -> dict:
    """The sessions in the state file (under the test's redirected home)."""
    from aipager import state
    return json.loads(state.SESSION_STATE_FILE.read_text())["sessions"]
