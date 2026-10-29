"""Black-box fixtures for "Stop ends the background agents of a waiting
session" (roadmap 8.121 F5, design-6) and the agents-only waiting state
(design-amend-5 A).

Derived from spec.md, design-amend-5.md, design-6.md (Goal, Facts, the
entry points section, section 6 texts, Success criteria) and the probe's
payload and transcript shapes (kill-agents-probe.md), with every id, path
and name made up.

The harness: a real ``HookReceiver`` driving a real ``TelegramBot``, every
Telegram call recorded on mocks, and every byte aipager writes into the
session's terminal read on the dtach seam ``inject._run`` (Escapes, Ctrl+U
and the kill chord alike). The chord's gap is read on
``inject._key_gap_sleep`` and its clock on ``inject._kill_clock`` (design-6
section 2 names both seams). Nothing reaches a real dtach, Telegram, a
socket under /tmp or the real home; nothing in ``asyncio`` or ``time`` is
patched through a module path.
"""

from __future__ import annotations

import hashlib
import hmac
import itertools
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import urlencode

import pytest

from aipager.dtach import hook_receiver as hr
from aipager.dtach import hook_reply, inject

BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
# The single chat the suite pins (tests/conftest.py); a private chat's id is
# its person's id.
DM = 256113222
GROUP = -100
ADMIN = 555
# A name no live session can have.
NAME = "claude-stopkill_bb_harness"
LABEL = "x1"
CARD = 42
MARK = "[via Telegram msg=3421]\n"
A1 = "aafd000355d74c2e4"
A2 = "abfd000355d74c2e5"
A3 = "acfd000355d74c2e6"
SHELL = "b3vwpa6yy"
CHORD = b"\x18\x0b"
ESC = b"\x1b"


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    monkeypatch.setattr("aipager.config.BOT_TOKEN", BOT_TOKEN)
    # Replays send identical payloads within microseconds; the general hook
    # dedup is not what this suite is about.
    monkeypatch.setattr(hr, "HOOK_DEDUP_WINDOW_SECONDS", -1.0)


class Terminal:
    """What aipager wrote into the session's terminal."""

    def __init__(self) -> None:
        #: ``("write", bytes)`` per dtach write, ``("gap", s)`` per key gap.
        self.log: list[tuple[str, object]] = []
        #: async callables run (once each, in order) inside the next key gap:
        #: what happens in the world while the chord waits between presses.
        self.during_gap: list = []
        #: the chord's clock (``inject._kill_clock``): each read returns the
        #: next value; by default a quarter second per read.
        self.clock_values = itertools.count(5000.0, 0.25)

    def writes(self) -> list[bytes]:
        return [v for k, v in self.log if k == "write"]

    def gaps(self) -> list[float]:
        return [v for k, v in self.log if k == "gap"]

    def escapes(self) -> int:
        return sum(1 for w in self.writes() if ESC in w)

    def chords(self) -> int:
        return sum(1 for w in self.writes() if w == CHORD)


@pytest.fixture
def term(monkeypatch) -> Terminal:
    t = Terminal()

    async def _run(args, stdin=b"", timeout=5):
        if stdin:
            t.log.append(("write", bytes(stdin)))
        return True, ""

    async def _gap(delay):
        t.log.append(("gap", delay))
        while t.during_gap:
            await t.during_gap.pop(0)()

    async def _no_sleep(_delay):
        return None

    monkeypatch.setattr(inject, "_run", _run)
    monkeypatch.setattr(inject, "_key_gap_sleep", _gap)
    monkeypatch.setattr(inject, "_kill_clock", lambda: next(t.clock_values))
    monkeypatch.setattr(inject, "is_alive", AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.bot.session_ops._sleep", _no_sleep)
    return t


def ts(epoch: float) -> str:
    return (datetime.fromtimestamp(epoch, tz=timezone.utc)
            .isoformat().replace("+00:00", "Z"))


def _sign(fields: dict) -> str:
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    return hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()


def init_header(user_id: int) -> dict:
    fields = {"auth_date": str(int(time.time())),
              "user": json.dumps({"id": user_id, "first_name": "Alice",
                                  "username": "alice"})}
    fields["hash"] = _sign(fields)
    return {"X-Telegram-Init-Data": urlencode(fields)}


class Harness:
    """One session, its chat, and every entry point a person can Stop it
    from."""

    def __init__(self, bot, run_async, term: Terminal, tmp_path: Path, *,
                 group: bool) -> None:
        self.bot = bot
        self.run_async = run_async
        self.term = term
        self.group = group
        self.chat = GROUP if group else DM
        self.user = ADMIN if group else DM
        self.ids = itertools.count(300)
        self.n = itertools.count(1)
        #: every sendMessage: dict(chat_id, text, msg_id, reply_markup, ...)
        self.sent: list[dict] = []
        #: every edit of any message: (msg_id, text, reply_markup or None)
        self.edits: list[tuple[object, str, object]] = []
        #: replies to a typed command (reply_text)
        self.replies: list[str] = []
        #: pop-ups (callback query answers)
        self.toasts: list[str] = []
        #: answers sent to a hook waiting for its decision
        self.decisions: list[tuple[dict, dict]] = []
        #: every busy card opened: its keyword arguments (``lazy`` and so on)
        self.cards_opened: list[dict] = []
        #: Mini App responses: (status, json)
        self.app: list[tuple[int, object]] = []
        self.transcript = tmp_path / "transcript.jsonl"
        self.transcript.write_bytes(b"")
        self.recv = hr.HookReceiver(bot.registry, bot.notify)
        self._wire()
        sess = bot.registry.get_or_create(NAME)
        sess.label = LABEL
        sess.scope_chat_id = self.chat
        sess.scope_kind = "group" if group else "dm"
        sess.cwd = "/home/alice/frontend"
        sess.transcript_path = str(self.transcript)
        bot.registry.last_active_session = NAME
        self.sess = sess

    # ---- wiring ---------------------------------------------------------
    def _wire(self) -> None:
        bot = self.bot

        async def _send(chat_id=None, text=None, **kw):
            msg_id = next(self.ids)
            self.sent.append({"chat_id": chat_id, "text": text or "",
                              "msg_id": msg_id, **kw})
            return MagicMock(message_id=msg_id)

        async def _edit(*args, **kw):
            mid = kw.get("message_id")
            text = kw.get("text")
            if text is None and args:
                text = args[0] if isinstance(args[0], str) else (
                    args[1] if len(args) > 1 else "")
            self.edits.append((mid, text or "", kw.get("reply_markup")))
            return True

        async def _edit_busy_raw(mid, text, **kw):
            self.edits.append((mid, text, kw.get("reply_markup")))
            return True

        async def _rich_edit(chat_id, msg_id, text, **kw):
            self.edits.append((msg_id, text, kw.get("reply_markup")))
            return True

        async def _rich_send(*args, **kw):
            text = kw.get("text") or kw.get("markdown") or next(
                (a for a in args if isinstance(a, str)), "")
            self.sent.append({"chat_id": args[0] if args else None,
                              "text": text, "msg_id": next(self.ids),
                              "rich": True})
            return {}

        async def _send_busy(sess, *args, **kw):
            self.cards_opened.append(dict(kw))
            sess.busy_msg_id = CARD
            sess.busy_started_at = time.monotonic()

        bot._app.bot.send_message = AsyncMock(side_effect=_send)
        bot._app.bot.edit_message_text = AsyncMock(side_effect=_edit)
        bot._app.bot.edit_message_reply_markup = AsyncMock(return_value=True)
        bot._app.bot.username = "aipager_test_bot"
        bot._app.bot.id = 999
        bot._edit_busy_raw = AsyncMock(side_effect=_edit_busy_raw)
        bot._send_busy_and_animate = AsyncMock(side_effect=_send_busy)
        bot._stop_animation = MagicMock()
        bot._start_animation = MagicMock()
        bot._card_edit_due = lambda *a, **k: True
        for name in ("_maybe_update_bot_name", "_update_bot_commands", "_react"):
            setattr(bot, name, AsyncMock())
        self._patches = {
            "aipager.bot.animation.edit_message_text_rich": _rich_edit,
            "aipager.bot.notify.send_rich_message": _rich_send,
        }

        def _decide(reply, decision):
            if isinstance(reply, dict) and reply.get("addr"):
                self.decisions.append((reply, decision))
                return True
            return False
        self._decide = _decide

    def install(self, monkeypatch) -> None:
        for target, fn in self._patches.items():
            monkeypatch.setattr(target, fn)
        monkeypatch.setattr(hook_reply, "send_decision", self._decide)

    # ---- hooks ----------------------------------------------------------
    async def hook(self, event: str, **fields) -> None:
        self.recv._recent_fingerprints.clear()
        payload = dict({"session": NAME, "hook_event_name": event,
                        "transcript_path": str(self.transcript),
                        "_seq": next(self.n)}, **fields)
        await self.recv._on_datagram(json.dumps(payload).encode())

    def append(self, *entries: dict) -> None:
        with self.transcript.open("a", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")

    async def prompt(self, text: str = "survey the docs in the background") -> None:
        await self.hook("UserPromptSubmit", prompt=MARK + text)

    async def launch(self, agent: str) -> None:
        call = {"subagent_type": "general-purpose", "description": "e2e helper",
                "prompt": "Survey it", "run_in_background": True}
        await self.hook("PreToolUse", tool_name="Agent", tool_input=call,
                        tool_use_id=f"toolu_launch_{agent}")
        await self.hook("SubagentStart", agent_id=agent,
                        agent_type="general-purpose")
        await self.hook("PostToolUse", tool_name="Agent", tool_input=call,
                        tool_use_id=f"toolu_launch_{agent}",
                        tool_response={"isAsync": True,
                                       "status": "async_launched",
                                       "agentId": agent})

    async def bg_shell(self, task_id: str = SHELL) -> None:
        tool_input = {"command": "sleep 150", "description": "wait a bit",
                      "run_in_background": True}
        await self.hook("PreToolUse", tool_name="Bash", tool_input=tool_input,
                        tool_use_id=f"toolu_sh_{task_id}")
        await self.hook("PostToolUse", tool_name="Bash", tool_input=tool_input,
                        tool_use_id=f"toolu_sh_{task_id}",
                        tool_response={"backgroundTaskId": task_id,
                                       "stdout": "", "stderr": "",
                                       "interrupted": False, "isImage": False,
                                       "noOutputExpected": False})

    @staticmethod
    def tasks(agents=(), shells=()) -> list[dict]:
        """A Stop payload's ``background_tasks`` in 2.1.312's shape."""
        out = [{"id": a, "type": "subagent", "status": "running",
                "description": "e2e helper"} for a in agents]
        out += [{"id": s, "type": "shell", "status": "running",
                 "command": "sleep 150"} for s in shells]
        return out

    async def turn_stop(self, answer: str = "Started a survey in the background.",
                        *, agents=(), shells=(), tasks=True) -> None:
        fields = {"last_assistant_message": answer}
        if tasks:
            fields["background_tasks"] = self.tasks(agents, shells)
        await self.hook("Stop", **fields)

    async def waiting(self, agents=(A1, A2), shells=()) -> None:
        """A Telegram prompt; Claude starts *shells* background shells and
        *agents* background agents; its own turn ends while they run."""
        await self.prompt()
        for s in shells:
            await self.bg_shell(s)
        for a in agents:
            await self.launch(a)
        await self.turn_stop(agents=agents, shells=shells)

    async def agent_permission(self, agent: str, command: str = "rm -rf build/") -> None:
        await self.hook("PermissionRequest", tool_name="Bash",
                        tool_input={"command": command, "description": command},
                        agent_id=agent, agent_type="general-purpose",
                        aipager_reply_addr=f"/nonexistent/reply-{agent}.sock",
                        aipager_request_id=f"req-{agent}")

    async def own_permission(self, command: str = "rm -rf dist/") -> None:
        await self.hook("PermissionRequest", tool_name="Bash",
                        tool_input={"command": command, "description": command},
                        tool_use_id="toolu_own1",
                        aipager_reply_addr="/nonexistent/reply-claude.sock",
                        aipager_request_id="req-own")

    # ---- entry points ---------------------------------------------------
    def _query(self, data: str, message_id: int):
        q = MagicMock()
        q.data = data
        q.message = MagicMock(text="card", message_id=message_id)
        kind = "supergroup" if self.group else "private"
        q.message.chat = MagicMock(id=self.chat, type=kind)
        q.message.chat_id = self.chat
        q.message.edit_text = AsyncMock()

        async def _answer(*args, **kw):
            text = args[0] if args else kw.get("text")
            if text:
                self.toasts.append(text)
        q.answer = AsyncMock(side_effect=_answer)
        q.edit_message_text = AsyncMock(side_effect=lambda *a, **k: self.edits.append(
            (message_id, a[0] if a else k.get("text", ""), k.get("reply_markup"))))
        q.edit_message_reply_markup = AsyncMock()
        q.from_user = MagicMock(id=self.user, username="alice", first_name="Alice")
        u = MagicMock()
        u.callback_query = q
        u.effective_chat = MagicMock(id=self.chat, type=kind)
        u.effective_user = q.from_user
        u.effective_message = SimpleNamespace(sender_chat=None, chat=q.message.chat)
        u.message = None
        return u

    async def tap(self, verb: str, message_id: int) -> None:
        await self.bot._handle_callback(self._query(f"{NAME}:{verb}", message_id),
                                        MagicMock())

    async def tap_data(self, data: str, message_id: int) -> None:
        await self.bot._handle_callback(self._query(data, message_id), MagicMock())

    def _typed(self, text: str, mid: int = 990):
        kind = "supergroup" if self.group else "private"
        u = MagicMock()
        u.message = MagicMock()
        u.message.text = text
        u.message.message_id = mid
        u.message.reply_to_message = None
        u.message.quote = None
        u.message.external_reply = None
        u.message.forward_origin = None
        u.message.via_bot = None
        u.message.media_group_id = None
        u.message.sender_chat = None

        async def _reply(text=None, *a, **kw):
            self.replies.append(text or kw.get("text", ""))
            return MagicMock(message_id=next(self.ids))
        u.message.reply_text = AsyncMock(side_effect=_reply)
        u.message.reply_html = AsyncMock(side_effect=_reply)
        u.message.chat = MagicMock(id=self.chat, type=kind)
        u.message.chat_id = self.chat
        u.effective_message = u.message
        u.effective_chat = MagicMock(id=self.chat, type=kind)
        u.effective_user = MagicMock(id=self.user, username="alice",
                                     first_name="Alice")
        u.callback_query = None
        return u

    async def stop_card(self) -> None:
        await self.tap("stop", self.sess.busy_msg_id or CARD)

    async def stop_status(self) -> None:
        await self.tap(f"ststop{self.sess.turn_key}", 777)

    async def stop_picker(self) -> None:
        await self.tap(f"pstop{self.sess.turn_key}", 778)

    async def stop_command(self) -> None:
        await self.bot._handle_stop_cmd(self._typed(f"/stop {LABEL}"), MagicMock())

    async def stop_label(self) -> None:
        await self.bot._handle_message(self._typed(f"/{LABEL} stop"), MagicMock())

    async def stop_miniapp(self) -> None:
        from aiohttp.test_utils import TestClient, TestServer

        from aipager.miniapp.server import MiniAppServer
        server = MiniAppServer(self.bot, self.bot.registry, port=8767)
        client = TestClient(TestServer(server._build_app()))
        await client.start_server()
        try:
            resp = await client.post(f"/api/sessions/{LABEL}/stop",
                                     headers=init_header(self.user))
            try:
                data = await resp.json()
            except Exception:  # noqa: BLE001 - a non-JSON body is the answer
                data = None
            self.app.append((resp.status, data))
        finally:
            await client.close()

    async def get_json(self, path: str):
        """``(status, json)`` of a Mini App GET with this chat's person."""
        from aiohttp.test_utils import TestClient, TestServer

        from aipager.miniapp.server import MiniAppServer
        server = MiniAppServer(self.bot, self.bot.registry, port=8767)
        client = TestClient(TestServer(server._build_app()))
        await client.start_server()
        try:
            resp = await client.get(path, headers=init_header(self.user))
            return resp.status, await resp.json()
        finally:
            await client.close()

    async def stop(self, entry: str) -> None:
        await getattr(self, f"stop_{entry}")()

    # ---- what the person reads ------------------------------------------
    def card_texts(self) -> list[str]:
        return [t for mid, t, _m in self.edits if mid == CARD]

    def said(self) -> list[str]:
        """Every pop-up, reply, sent message and Mini App detail, in order
        of kind (each a whole text)."""
        out = list(self.toasts) + list(self.replies)
        out += [m["text"] for m in self.sent]
        for _status, data in self.app:
            if isinstance(data, dict) and isinstance(data.get("detail"), str):
                out.append(data["detail"])
        return out

    def run(self, coro):
        return self.run_async(coro)


ENTRIES = ("card", "status", "picker", "command", "label", "miniapp")


def _bot(mk_bot, *, group: bool):
    from aipager.policy import load_policy
    from aipager.scope import Member, Scope
    if group:
        scope = Scope(chat_id=GROUP, kind="group", label="team",
                      members=(Member(id=ADMIN, label="alice", role="admin"),),
                      root="/home/alice")
        b = mk_bot(scopes=[scope])
    else:
        b = mk_bot()
    b.policy = load_policy(Path("/nonexistent/policy.yaml"),
                           Path("/nonexistent/policy.d"))
    return b


@pytest.fixture
def dm(mk_bot, run_async, term, tmp_path, monkeypatch) -> Harness:
    """The private chat (the success criteria's exact texts)."""
    h = Harness(_bot(mk_bot, group=False), run_async, term, tmp_path, group=False)
    h.install(monkeypatch)
    return h


@pytest.fixture
def grp(mk_bot, run_async, term, tmp_path, monkeypatch) -> Harness:
    """A team group with alice as its admin."""
    h = Harness(_bot(mk_bot, group=True), run_async, term, tmp_path, group=True)
    h.install(monkeypatch)
    return h


def agents_killed_line(epoch: float) -> dict:
    """The ``system`` line Claude Code writes the moment its kill chord
    stopped the agents (probe G1)."""
    return {"type": "system", "subtype": "agents_killed", "timestamp": ts(epoch),
            "uuid": "0f3a-kill", "sessionId": "5e1d-0000"}


def queue_lines(epoch: float, text: str) -> list[dict]:
    return [{"type": "queue-operation", "operation": "enqueue",
             "timestamp": ts(epoch), "content": text},
            {"type": "queue-operation", "operation": "dequeue",
             "timestamp": ts(epoch)}]


def wake_user_line(epoch: float, text: str) -> dict:
    return {"type": "user", "timestamp": ts(epoch),
            "origin": {"kind": "task-notification"},
            "message": {"role": "user", "content": text}}


WAKE_TWO = ('2 background agents were stopped by the user: '
            '"e2e helper", "e2e helper".')
WAKE_ONE = 'Background agent "e2e helper" was stopped by the user.'
