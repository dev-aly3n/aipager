"""Shared black-box helpers for the three-modes suite (Ask / Auto / Full
access, Auto by default).

Everything here drives the public surface listed in the ship's
entrypoints.md: the bot's command and callback handlers, the hook
receiver's ``_on_datagram``, the Mini App HTTP routes, and the exported
functions. The only things replaced are the external boundaries the
contract names as seams: ``inject._run`` (the dtach write),
``inject._key_gap_sleep`` (the gap between presses), and the launch/kill
of a real dtach process.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import urlencode

from aipager.dtach import inject
from aipager.policy import load_policy
from aipager.scope import Member, Scope

BACKTAB = b"\x1b[Z"

#: The flag text the spec fixes per mode.
FLAGS = {
    "ask": "--permission-mode default",
    "auto": "--permission-mode auto",
    "full": "--dangerously-skip-permissions",
}
NEVER = "--allow-dangerously-skip-permissions"

# ---- a group with real roles --------------------------------------------

GROUP = -1001
OWNER, BOB, RO, ADA = 1, 2, 3, 4          # owner, user, read_only, admin
DM_CHAT = 256113222   # the configured chat conftest pins


#: The group's members' root folder (user-root-folders): the folder the
#: sessions below work in, so the root rule lets bob and ro act on them
#: and the mode rules are what these tests see. A temporary folder per
#: test (conftest's ``group_root``), never a real one.
GROUP_ROOT = ""


def group_root() -> str:
    """The folder the group's sessions work in (its members' root)."""
    return GROUP_ROOT


def group_scope() -> Scope:
    return Scope(chat_id=GROUP, kind="group", label="team", root=GROUP_ROOT or None, members=(
        Member(id=OWNER, label="aly", role="owner"),
        Member(id=BOB, label="bob", role="user"),
        Member(id=RO, label="ro", role="read_only"),
        Member(id=ADA, label="ada", role="admin"),
    ))


def dm_scope() -> Scope:
    return Scope(chat_id=DM_CHAT, kind="dm", label="aly DM",
                 members=(Member(id=OWNER, label="aly", role="owner"),))


def prepare_bot(bot):
    """Mock every Telegram call the flows under test may make."""
    bot._app.bot.id = 999
    bot._app.bot.username = "aipager_test_bot"
    bot._app.bot.send_message = AsyncMock(
        side_effect=lambda *a, **k: MagicMock(message_id=7000 + len(
            bot._app.bot.send_message.await_args_list)))
    bot._app.bot.edit_message_text = AsyncMock()
    bot._app.bot.edit_message_reply_markup = AsyncMock()
    bot._app.bot.delete_message = AsyncMock()
    bot._app.bot.set_my_commands = AsyncMock()
    bot._app.bot.set_message_reaction = AsyncMock()
    bot._app.bot.pin_chat_message = AsyncMock()
    for name in ("_card_for_injected", "_react", "_maybe_update_bot_name",
                 "_update_bot_commands", "_edit_busy_raw",
                 "_send_busy_and_animate"):
        setattr(bot, name, AsyncMock())
    bot._start_animation = MagicMock()
    bot._stop_animation = MagicMock()
    return bot


def scoped_bot(mk_bot, registry=None):
    bot = mk_bot(registry, scopes=[group_scope(), dm_scope()])
    bot.policy = load_policy(Path("/nonexistent/policy.yaml"),
                             Path("/nonexistent/policy.d"))
    return prepare_bot(bot)


def dm_bot(mk_bot, registry=None, *, admin=True):
    bot = prepare_bot(mk_bot(registry))
    bot._is_admin = MagicMock(return_value=admin)
    bot._is_admin_user = MagicMock(return_value=admin)
    return bot


# ---- the PTY and the process boundary ------------------------------------

class Pty:
    """Records every dtach write, every gap between presses, every kill and
    every launch. Nothing reaches a real dtach or claude."""

    def __init__(self, monkeypatch, *, write_ok=True):
        self.runs: list[tuple[list[str], bytes]] = []
        self.gaps: list[float] = []
        self.killed: list[str] = []
        self.launched: list[tuple[str, dict]] = []
        self.order: list[str] = []
        self.write_ok = write_ok

        async def _run(args, stdin=b"", timeout=5):
            self.runs.append((list(args), stdin))
            self.order.append("write")
            return self.write_ok, ""

        async def _gap(seconds):
            self.gaps.append(seconds)
            self.order.append("gap")

        async def _kill(name, *a, **k):
            self.killed.append(name)
            return True

        async def _launch(name, **kw):
            self.launched.append((name, kw))
            return True, ""

        monkeypatch.setattr(inject, "_run", _run)
        monkeypatch.setattr(inject, "_key_gap_sleep", _gap)
        monkeypatch.setattr(inject, "kill_session", _kill)
        monkeypatch.setattr(inject, "launch_session", _launch)
        monkeypatch.setattr(inject, "is_alive", AsyncMock(return_value=True))
        monkeypatch.setattr(inject, "list_sessions", AsyncMock(return_value=[]))

    @property
    def presses(self) -> list[bytes]:
        return [s for _a, s in self.runs if s == BACKTAB]

    @property
    def stdin_writes(self) -> list[bytes]:
        return [s for _a, s in self.runs if s]

    @property
    def launched_modes(self) -> list[str]:
        return [kw.get("mode") for _n, kw in self.launched]


def quiet_restart(monkeypatch):
    """A relaunch waits for the old socket to go: say it is gone at once."""
    try:
        import aipager.bot.session_ops as so
        if hasattr(so, "_sleep"):
            async def _nosleep(*a, **k):
                return None
            monkeypatch.setattr(so, "_sleep", _nosleep)
    except ImportError:  # pragma: no cover
        pass


# ---- chat updates and taps -----------------------------------------------

def typed(text, *, chat_id, user_id, message_id=100, chat_type="private"):
    u = MagicMock()
    u.message = MagicMock()
    u.message.text = text
    u.message.message_id = message_id
    u.message.reply_text = AsyncMock(return_value=MagicMock(message_id=message_id + 1))
    u.message.reply_to_message = None
    u.message.quote = None
    u.message.external_reply = None
    u.message.forward_origin = None
    u.message.via_bot = None
    u.message.media_group_id = None
    u.message.chat = MagicMock(id=chat_id, type=chat_type)
    u.message.chat_id = chat_id
    u.effective_user = MagicMock(id=user_id)
    u.effective_chat = MagicMock(id=chat_id, type=chat_type)
    return u


def tap(data, *, chat_id, user_id, msg_id=50, text="card"):
    q = MagicMock()
    q.data = data
    q.from_user = MagicMock(id=user_id)
    q.message = MagicMock()
    q.message.chat = MagicMock(id=chat_id)
    q.message.chat_id = chat_id
    q.message.message_id = msg_id
    q.message.text = text
    q.message.edit_text = AsyncMock()
    q.answer = AsyncMock()
    q.edit_message_text = AsyncMock()
    q.edit_message_reply_markup = AsyncMock()
    u = MagicMock()
    u.callback_query = q
    u.effective_chat = q.message.chat
    u.effective_user = q.from_user
    u.message = None
    return u, q


def reply_texts(update) -> list[str]:
    out = []
    for c in update.message.reply_text.await_args_list:
        if c.args:
            out.append(str(c.args[0]))
        elif "text" in c.kwargs:
            out.append(str(c.kwargs["text"]))
    return out


def reply_buttons(update) -> list[tuple[str, str]]:
    out = []
    for c in update.message.reply_text.await_args_list:
        kb = c.kwargs.get("reply_markup")
        if kb is not None and hasattr(kb, "inline_keyboard"):
            out += [(b.text, b.callback_data) for row in kb.inline_keyboard for b in row]
    return out


def answers(q) -> list[str]:
    return [str(c.args[0]) if c.args else str(c.kwargs.get("text", ""))
            for c in q.answer.await_args_list]


def all_bot_texts(bot, *updates_or_queries) -> str:
    """Every text the bot put on screen: sends, edits and replies."""
    parts = []
    for c in bot._app.bot.send_message.await_args_list:
        parts.append(str(c.kwargs.get("text", c.args[1] if len(c.args) > 1 else "")))
    for c in bot._app.bot.edit_message_text.await_args_list:
        parts.append(str(c.kwargs.get("text", c.args[0] if c.args else "")))
    for x in updates_or_queries:
        if hasattr(x, "message") and getattr(x, "message", None) is not None \
                and hasattr(x.message, "reply_text"):
            parts += reply_texts(x)
        if hasattr(x, "edit_message_text"):
            for c in x.edit_message_text.await_args_list:
                parts.append(str(c.kwargs.get("text", c.args[0] if c.args else "")))
        if hasattr(x, "answer"):
            parts += answers(x)
    return "\n".join(parts)


async def settle_tasks(rounds: int = 20):
    """Let tasks the bot spawned (presses, card edits) run to completion."""
    for _ in range(rounds):
        await asyncio.sleep(0.01)


# ---- hooks ---------------------------------------------------------------

_HOOK_SEQ = [0]


def hook(session, event="PostToolUse", *, mode=None, agent_id=None, **extra) -> bytes:
    """One hook datagram. Each carries its own tool_use_id, as Claude
    Code's real ones do, so the receiver's duplicate filter never drops a
    test's second report as a resend of the first."""
    _HOOK_SEQ[0] += 1
    payload = {"session": session, "hook_event_name": event}
    if event in ("PreToolUse", "PostToolUse") and "tool_name" not in extra:
        payload["tool_name"] = "Bash"
        payload["tool_input"] = {"command": f"ls {_HOOK_SEQ[0]}"}
    if event in ("PreToolUse", "PostToolUse", "PermissionRequest"):
        payload["tool_use_id"] = f"toolu_tm3_{_HOOK_SEQ[0]}"
    if mode is not None:
        payload["permission_mode"] = mode
    if agent_id is not None:
        payload["agent_id"] = agent_id
    payload.update(extra)
    return json.dumps(payload).encode()


def mode_only_notify(bot):
    """The daemon wires ``HookReceiver(registry, bot.notify)``. Forward the
    ``mode_reported`` event (the one under test) to the real bot.notify and
    record the rest, which belong to other features' cards."""
    seen: list[tuple[str, dict]] = []

    async def _notify(sess, event, context):
        seen.append((event, dict(context or {})))
        if event == "mode_reported":
            await bot.notify(sess, event, context)

    _notify.seen = seen
    return _notify


# ---- Mini App ------------------------------------------------------------

BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"


def _sign(fields, bot_token):
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    return hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()


def init_data(user_id):
    fields = {"auth_date": str(int(time.time())),
              "user": json.dumps({"id": user_id, "first_name": "T"})}
    fields["hash"] = _sign(fields, BOT_TOKEN)
    return urlencode(fields)


def hdr(user_id):
    return {"X-Telegram-Init-Data": init_data(user_id)}
