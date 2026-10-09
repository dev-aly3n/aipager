"""Prompt answer lines read "Allowed · @bob"; the deny reason Claude reads
still says "Denied via aipager by @bob" (design.md success criterion 6;
entrypoints.md "Prompt answer lines").

Driven through the real notify path (a permission prompt sent as its own
message) and the real tap handler; the outside world (keystrokes, the
hook's reply channel) is recorded, never reached.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.dtach import hook_reply, inject
from aipager.policy import load_policy
from aipager.scope import Member, Scope
from aipager.state import Status

GROUP = -1001
DM = 555
S = "claude-api__g1001"
DM_S = "claude-api__d555"
ALY, BOB = 1, 2
PROMPT_ID = 77
HOOK = {"addr": "/nonexistent/aipager-reply-x.sock", "request_id": "req-1"}
CMD = "Bash: rm -rf build/"


def _scopes():
    return [
        Scope(chat_id=GROUP, kind="group", label="team", members=(
            Member(id=ALY, label="aly", role="owner"),
            Member(id=BOB, label="bob", role="user"),
        )),
        Scope(chat_id=DM, kind="dm", label="aly DM",
              members=(Member(id=ALY, label="aly", role="owner"),)),
    ]


class _World:
    def __init__(self, mk_bot, monkeypatch):
        bot = mk_bot(scopes=_scopes())
        bot.policy = load_policy(Path("/nonexistent/policy.yaml"),
                                 Path("/nonexistent/policy.d"))
        bot._app.bot.id = 999
        bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=PROMPT_ID))
        bot._app.bot.edit_message_text = AsyncMock()
        for name in ("_maybe_update_bot_name", "_update_bot_commands",
                     "_edit_busy_raw", "_react"):
            setattr(bot, name, AsyncMock())
        bot._start_animation = MagicMock()
        bot._stop_animation = MagicMock()
        bot._watch_keystroke_answer = MagicMock()
        self.bot = bot
        self.keys: list[str] = []
        self.decisions: list[dict] = []

        async def _keys(name, key, *a, **k):
            self.keys.append(key)
            return True

        def _decide(reply, decision):
            if isinstance(reply, dict) and reply.get("addr"):
                self.decisions.append(decision)
                return True
            return False

        monkeypatch.setattr(inject, "send_keys", _keys)
        monkeypatch.setattr(inject, "is_alive", AsyncMock(return_value=True))
        monkeypatch.setattr(hook_reply, "send_decision", _decide)

    def session(self, name=S, chat=GROUP, kind="group"):
        s = self.bot.registry.get_or_create(name)
        s.label = "api"
        s.scope_chat_id, s.scope_kind = chat, kind
        s.status = Status.INTERACTIVE
        s.busy_msg_id = None
        return s

    def prompt(self, sess, run_async, tool_info=None, hook=HOOK):
        tool_info = tool_info or {"name": "Bash", "summary": CMD,
                                  "input": {"command": "rm -rf build/"}}
        run_async(self.bot.notify(sess, "permission_prompt", {
            "tool_info": tool_info, "hook_reply": dict(hook) if hook else None}))
        self.bot._app.bot.send_message.reset_mock()

    def tap(self, run_async, chat, uid, verb, name=S):
        q = MagicMock()
        q.data = f"{name}:{verb}"
        q.from_user = MagicMock(id=uid)
        q.message = MagicMock()
        q.message.chat = MagicMock(id=chat)
        q.message.chat_id = chat
        q.message.message_id = PROMPT_ID
        q.message.text = "🔐 api · Permission needed"
        q.answer = AsyncMock()
        q.edit_message_text = AsyncMock()
        q.edit_message_reply_markup = AsyncMock()
        u = MagicMock()
        u.callback_query = q
        u.effective_chat = q.message.chat
        u.effective_user = q.from_user
        u.message = None
        run_async(self.bot._handle_callback(u, MagicMock()))
        return q

    def lines(self, chat):
        out = []
        for c in self.bot._app.bot.send_message.await_args_list:
            target = c.kwargs.get("chat_id", c.args[0] if c.args else None)
            text = c.kwargs.get("text", c.args[1] if len(c.args) > 1 else "")
            if target == chat:
                out.append(text)
        return out


@pytest.fixture
def world(mk_bot, monkeypatch):
    return _World(mk_bot, monkeypatch)


@pytest.mark.parametrize("verb,line", [
    ("allow", f"✅ <b>api</b> · Allowed · @bob · {CMD}"),
    ("deny", f"🚫 <b>api</b> · Denied · @bob · {CMD}"),
])
def test_a_decision_line_puts_the_actor_after_a_dot(world, run_async, verb, line):
    sess = world.session()
    world.prompt(sess, run_async)
    world.tap(run_async, GROUP, BOB, verb)
    assert world.lines(GROUP) == [line]


@pytest.mark.parametrize("verb", ["allow", "deny"])
def test_no_decision_line_says_by(world, run_async, verb):
    sess = world.session()
    world.prompt(sess, run_async)
    world.tap(run_async, GROUP, BOB, verb)
    assert not any(" by @" in t for t in world.lines(GROUP))


def test_an_allow_always_line_puts_the_actor_after_a_dot(world, run_async):
    sess = world.session()
    world.prompt(sess, run_async, tool_info={
        "name": "Bash", "summary": CMD, "input": {"command": "rm -rf build/"},
        "permission_suggestions": [{"type": "addRules", "rules": [
            {"toolName": "Bash", "ruleContent": "rm -rf build/"}],
            "behavior": "allow", "destination": "localSettings"}]})
    world.tap(run_async, GROUP, BOB, "allow_always")
    (line,) = world.lines(GROUP)
    # A degraded always (no real rule) is an allow; either way, "· @bob".
    assert line in (f"🟢 <b>api</b> · Allowed always · @bob · {CMD}",
                    f"✅ <b>api</b> · Allowed · @bob · {CMD}")


def test_an_option_line_puts_the_actor_after_a_dot(world, run_async):
    sess = world.session()
    world.prompt(sess, run_async, tool_info={
        "name": "AskUserQuestion", "summary": "AskUserQuestion",
        "input": {"questions": [{"question": "Pick one",
                                 "options": [{"label": "A"}, {"label": "B"}]}]}})
    world.tap(run_async, GROUP, BOB, "opt1")
    assert world.lines(GROUP) == ["· <b>api</b> · Selected option 2 · @bob · Pick one"]


def test_the_deny_reason_claude_reads_is_unchanged(world, run_async):
    sess = world.session()
    world.prompt(sess, run_async)
    world.tap(run_async, GROUP, BOB, "deny")
    (decision,) = world.decisions
    assert decision["message"] == "Denied via aipager by @bob"


def test_the_deny_reason_never_uses_the_dot_form(world, run_async):
    sess = world.session()
    world.prompt(sess, run_async)
    world.tap(run_async, GROUP, BOB, "deny")
    (decision,) = world.decisions
    assert "·" not in decision["message"]


def test_an_allow_sends_claude_no_message(world, run_async):
    sess = world.session()
    world.prompt(sess, run_async)
    world.tap(run_async, GROUP, BOB, "allow")
    assert world.decisions == [{"behavior": "allow"}]


def test_a_dm_line_has_one_dot_per_part(world, run_async):
    """Without an actor the "· @x" part is absent, never an empty field."""
    sess = world.session(name=DM_S, chat=DM, kind="dm")
    world.prompt(sess, run_async)
    world.tap(run_async, DM, ALY, "allow", name=DM_S)
    (line,) = world.lines(DM)
    assert line in (f"✅ <b>api</b> · Allowed · @aly · {CMD}",
                    f"✅ <b>api</b> · Allowed · {CMD}")


def test_the_actor_is_html_escaped(world, run_async, monkeypatch):
    """Error guessing: a label with markup must not break the HTML line."""
    world.bot.scopes = [Scope(chat_id=GROUP, kind="group", label="team", members=(
        Member(id=ALY, label="aly", role="owner"),
        Member(id=BOB, label="b<o>b", role="user"),
    ))]
    sess = world.session()
    world.prompt(sess, run_async)
    world.tap(run_async, GROUP, BOB, "allow")
    lines = world.lines(GROUP)
    assert lines and "<o>" not in lines[0]
