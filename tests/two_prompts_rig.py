"""A rig for more than one open prompt (roadmap 8.120): the real hook
receiver, the real notify path and the real tap handler, with every
outside effect recorded.

- ``hook(event, **fields)`` plays one hook datagram;
- ``launch(agent, background=True)`` plays the main loop's Agent call and
  the agent's SubagentStart (and, for a background one, the call's
  PostToolUse with ``isAsync``);
- ``agent_bash(agent, command, hook=True)`` an agent's PermissionRequest
  for Bash, parked on a hook reply channel unless *hook* is False;
- ``question(...)`` Claude's own AskUserQuestion at PreToolUse;
- ``tap(msg_id, verb)`` a tap on a message's button;
- ``keys``, ``decisions`` (hook replies), ``sent`` (sendMessage calls),
  ``card_edits`` (the busy card's edits).
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from aipager.dtach import hook_receiver as hr
from aipager.dtach import hook_reply, inject
from aipager.policy import load_policy
from aipager.scope import Member, Scope
from aipager.state import Status

GROUP = -1001
S = "claude-api__g1001"
ALY, BOB = 1, 2
CARD = 50
TRIGGER = 40
BG = "a1b2c3d4e5f60718a"
FG = "b1b2c3d4e5f60718b"
OTHER = "c9f8e7d6c5b4a3921"
QUESTION = {"questions": [{
    "question": "Which file should change?", "header": "Scope",
    "multiSelect": False,
    "options": [{"label": "Only the card", "description": "a"},
                {"label": "The card and the bar", "description": "b"}]}]}


def scopes():
    return [Scope(chat_id=GROUP, kind="group", label="team", members=(
        Member(id=ALY, label="alice", role="owner"),
        Member(id=BOB, label="bob", role="user"),
    ))]


class Rig:
    def __init__(self, mk_bot, monkeypatch, run_async, *, card=True, registry=None):
        bot = mk_bot(scopes=scopes())
        if registry is not None:
            bot.registry = registry
        bot.policy = load_policy(Path("/nonexistent/policy.yaml"),
                                 Path("/nonexistent/policy.d"))
        bot._app.bot.id = 999
        self.run_async = run_async
        self.ids = itertools.count(100)
        self.sent: list[dict] = []

        async def _send(chat_id=None, text=None, **kwargs):
            msg_id = next(self.ids)
            self.sent.append({"chat_id": chat_id, "text": text, "msg_id": msg_id,
                              **kwargs})
            return MagicMock(message_id=msg_id)

        bot._app.bot.send_message = AsyncMock(side_effect=_send)
        bot._app.bot.edit_message_text = AsyncMock()
        self.card_edits: list[tuple[int, str, object]] = []

        async def _edit(mid, text, **kwargs):
            self.card_edits.append((mid, text, kwargs.get("reply_markup")))
            return True

        bot._edit_busy_raw = AsyncMock(side_effect=_edit)
        self.frames: list[str] = []

        async def _rich(chat_id, msg_id, text, **kwargs):
            self.frames.append(text)
            return True

        monkeypatch.setattr("aipager.bot.animation.edit_message_text_rich", _rich)
        bot._card_edit_due = lambda *a, **k: True
        for name in ("_maybe_update_bot_name", "_update_bot_commands", "_react"):
            setattr(bot, name, AsyncMock())
        bot._start_animation = MagicMock()
        bot._stop_animation = MagicMock()
        bot._watch_keystroke_answer = MagicMock()
        self.bot = bot
        self.keys: list[str] = []
        self.decisions: list[tuple[dict, dict]] = []
        self.hook_ok = True

        async def _keys(name, key, *a, **k):
            self.keys.append(key)
            return True

        def _decide(reply, decision):
            if isinstance(reply, dict) and reply.get("addr") and self.hook_ok:
                self.decisions.append((reply, decision))
                return True
            return False

        monkeypatch.setattr(inject, "send_keys", _keys)
        monkeypatch.setattr(inject, "is_alive", AsyncMock(return_value=True))
        monkeypatch.setattr(hook_reply, "send_decision", _decide)

        sess = bot.registry.get_or_create(S)
        if registry is None:
            sess.label = "api"
            sess.scope_chat_id, sess.scope_kind = GROUP, "group"
            sess.status = Status.BUSY
            sess.busy_msg_id = CARD if card else None
            sess.trigger_msg_id = TRIGGER
        self.sess = sess
        self.recv = hr.HookReceiver(bot.registry, bot.notify)
        self.n = itertools.count(1)

    # -- hooks -------------------------------------------------------------

    def hook(self, event, **fields):
        self.recv._recent_fingerprints.clear()
        payload = dict({"session": S, "hook_event_name": event,
                        "transcript_path": "", "_seq": next(self.n)}, **fields)
        self.run_async(self.recv._on_datagram(json.dumps(payload).encode()))

    def launch(self, agent, *, background=True, agent_type="ship-reviewer"):
        call = {"subagent_type": agent_type, "description": "Review",
                "prompt": "Review it"}
        if background:
            call["run_in_background"] = True
        self.hook("PreToolUse", tool_name="Agent", tool_input=call,
                  tool_use_id=f"toolu_launch_{agent}")
        self.hook("SubagentStart", agent_id=agent, agent_type=agent_type)
        if background:
            self.hook("PostToolUse", tool_name="Agent", tool_input=call,
                      tool_use_id=f"toolu_launch_{agent}",
                      tool_response={"isAsync": True, "status": "async_launched",
                                     "agentId": agent})

    def agent_bash(self, agent, command="make", *, hook=True, request_id=None,
                   description=None, mode=None):
        fields = {"tool_name": "Bash",
                  "tool_input": {"command": command,
                                 "description": description or command},
                  "agent_id": agent, "agent_type": "ship-reviewer"}
        if mode is not None:
            fields["permission_mode"] = mode
        if hook:
            fields["aipager_reply_addr"] = f"/nonexistent/reply-{agent}.sock"
            fields["aipager_request_id"] = request_id or f"req-{agent}-{command}"
        self.hook("PermissionRequest", **fields)

    def claude_bash(self, command="rm -rf build/", *, tool_use_id="toolu_p1",
                    hook=True, request_id=None, mode=None, suggestions=None,
                    transcript_path=None):
        """*mode*: the payload's ``permission_mode``, sent only when given;
        *suggestions*: its ``permission_suggestions``, likewise; a None
        *tool_use_id* sends none (as Claude Code 2.1.311 does);
        *transcript_path*: the payload's (else the rig's empty one)."""
        fields = {"tool_name": "Bash",
                  "tool_input": {"command": command, "description": command}}
        if tool_use_id is not None:
            fields["tool_use_id"] = tool_use_id
        if mode is not None:
            fields["permission_mode"] = mode
        if suggestions is not None:
            fields["permission_suggestions"] = suggestions
        if transcript_path is not None:
            fields["transcript_path"] = transcript_path
        if hook:
            fields["aipager_reply_addr"] = "/nonexistent/reply-claude.sock"
            fields["aipager_request_id"] = request_id or f"req-{tool_use_id or 'p'}"
        self.hook("PermissionRequest", **fields)

    def question(self, *, agent=None, tool_use_id="toolu_q1", mode=None):
        fields = {"tool_name": "AskUserQuestion", "tool_input": QUESTION,
                  "tool_use_id": tool_use_id}
        if mode is not None:
            fields["permission_mode"] = mode
        if agent:
            fields.update(agent_id=agent, agent_type="ship-reviewer")
        self.hook("PreToolUse", **fields)

    def timeout(self, request_id):
        self.hook("permission_reply_timeout", aipager_request_id=request_id)

    # -- what the chat shows -------------------------------------------------

    def prompt_messages(self):
        """The prompts sent as their own messages, in order."""
        return [m for m in self.sent if m.get("reply_markup") is not None]

    def buttons(self, msg) -> list[str]:
        markup = msg.get("reply_markup")
        rows = getattr(markup, "inline_keyboard", None) or []
        return [b.text for row in rows for b in row]

    # -- taps ----------------------------------------------------------------

    def tap(self, msg_id, verb, uid=BOB):
        q = MagicMock()
        q.data = f"{S}:{verb}"
        q.from_user = MagicMock(id=uid)
        q.message = MagicMock()
        q.message.chat = MagicMock(id=GROUP)
        q.message.chat_id = GROUP
        q.message.message_id = msg_id
        q.message.text = "🔐 api · Permission needed"
        q.answer = AsyncMock()
        q.edit_message_text = AsyncMock()
        q.edit_message_reply_markup = AsyncMock()
        u = MagicMock()
        u.callback_query = q
        u.effective_chat = q.message.chat
        u.effective_user = q.from_user
        u.message = None
        self.run_async(self.bot._handle_callback(u, MagicMock()))
        return q


def answers(q) -> list[str]:
    return [c.args[0] for c in q.answer.await_args_list if c.args]
