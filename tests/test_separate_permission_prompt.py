"""Roadmap 8.99: a permission prompt sent before the busy card exists.

The daemon shows it as its own message. Its Allow/Deny used to type keys
into Claude Code's dialog straight away and never post the attributed
line ("✅ api · Allowed by @bob · ..."). Pinned here, through the real
notify path and the real tap handler:

- Allow / Deny tapped on the separate message answer through the parked
  PermissionRequest hook (no key typed) and post the attributed line in
  the session's chat, with the same audit record as the inline prompt;
- the keys are typed only once the hook stopped waiting (its
  ``permission_reply_timeout`` clears the separate prompt's channel too);
- the same gates: a read_only member's tap decides nothing, an answered
  prompt's message decides nothing again;
- a question shown as a separate message posts its line too;
- DM parity, and the inline prompt's line unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import audit as audit_mod
from aipager.bot import tap_gate
from aipager.dtach import hook_receiver as hr
from aipager.dtach import hook_reply, inject
from aipager.policy import load_policy
from aipager.scope import Member, Scope
from aipager.state import Status

GROUP = -1001
DM = 555
S = "claude-api__g1001"
DM_S = "claude-api__d555"
ALY, BOB, RO = 1, 2, 3
PROMPT_ID = 77
HOOK = {"addr": "/nonexistent/aipager-reply-x.sock", "request_id": "req-1"}


def _scopes():
    return [
        Scope(chat_id=GROUP, kind="group", label="team", members=(
            Member(id=ALY, label="aly", role="owner"),
            Member(id=BOB, label="bob", role="user"),
            Member(id=RO, label="ro", role="read_only"),
        )),
        Scope(chat_id=DM, kind="dm", label="aly DM",
              members=(Member(id=ALY, label="aly", role="owner"),)),
    ]


class _World:
    """The bot, and everything a tap could do to the outside."""

    def __init__(self, mk_bot, monkeypatch):
        bot = mk_bot(scopes=_scopes())
        bot.policy = load_policy(Path("/nonexistent/policy.yaml"),
                                 Path("/nonexistent/policy.d"))
        bot._app.bot.id = 999
        bot._app.bot.send_message = AsyncMock(
            return_value=MagicMock(message_id=PROMPT_ID))
        bot._app.bot.edit_message_text = AsyncMock()
        for name in ("_maybe_update_bot_name", "_update_bot_commands",
                     "_edit_busy_raw", "_react"):
            setattr(bot, name, AsyncMock())
        bot._start_animation = MagicMock()
        bot._stop_animation = MagicMock()
        bot._watch_keystroke_answer = MagicMock()
        self.bot = bot
        self.keys: list[str] = []
        self.decisions: list[tuple[dict, dict]] = []

        async def _keys(name, key, *a, **k):
            self.keys.append(key)
            return True

        real_send = hook_reply.send_decision

        def _decide(reply, decision):
            # A channel the daemon still holds stands for a hook that is
            # parked waiting; anything else is answered by the real code
            # (False for a missing channel).
            if isinstance(reply, dict) and reply.get("addr"):
                self.decisions.append((reply, decision))
                return True
            return real_send(reply, decision)

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

    def prompt(self, sess, tool_info=None, hook=HOOK, run_async=None):
        """The real notify path, with no busy card: a separate message."""
        tool_info = tool_info or {"name": "Bash", "summary": "Bash: rm -rf build/",
                                  "input": {"command": "rm -rf build/"}}
        run_async(self.bot.notify(sess, "permission_prompt", {
            "tool_info": tool_info, "hook_reply": dict(hook) if hook else None}))
        self.bot._app.bot.send_message.reset_mock()

    def tap(self, run_async, chat, uid, verb, name=S, msg_id=PROMPT_ID):
        q = MagicMock()
        q.data = f"{name}:{verb}"
        q.from_user = MagicMock(id=uid)
        q.message = MagicMock()
        q.message.chat = MagicMock(id=chat)
        q.message.chat_id = chat
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
        run_async(self.bot._handle_callback(u, MagicMock()))
        return q

    def lines(self, chat):
        """The texts sent into *chat* (sendMessage), with their reply ids."""
        out = []
        for c in self.bot._app.bot.send_message.await_args_list:
            target = c.kwargs.get("chat_id", c.args[0] if c.args else None)
            text = c.kwargs.get("text", c.args[1] if len(c.args) > 1 else "")
            if target == chat:
                out.append((text, c.kwargs.get("reply_to_message_id")))
        return out


@pytest.fixture
def world(mk_bot, monkeypatch):
    return _World(mk_bot, monkeypatch)


def _audit():
    path = audit_mod.AUDIT_LOG_PATH
    return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []


def _answers(q):
    return [c.args[0] for c in q.answer.await_args_list if c.args]


# ---- the notify side --------------------------------------------------------

def test_a_separate_prompt_keeps_the_hooks_channel(world, run_async):
    sess = world.session()
    world.prompt(sess, run_async=run_async)
    assert sess.pending_permission is None            # not on a card
    perm = sess.pending_prompt_msg["perm"]
    assert perm["hook_reply"] == HOOK
    assert perm["tool_summary"] == "Bash: rm -rf build/"
    assert perm["tool_info"]["name"] == "Bash"


def test_a_separate_question_carries_no_hook_channel(world, run_async):
    sess = world.session()
    world.prompt(sess, run_async=run_async, tool_info={
        "name": "AskUserQuestion", "summary": "AskUserQuestion",
        "input": {"questions": [{"question": "Pick one",
                                 "options": [{"label": "A"}, {"label": "B"}]}]}})
    perm = sess.pending_prompt_msg["perm"]
    assert "hook_reply" not in perm
    assert perm["ask_question"] is True and perm["question"] == "Pick one"


# ---- answered through the hook ----------------------------------------------

def test_allow_on_a_separate_prompt_answers_through_the_hook_and_names_who(
        world, run_async):
    sess = world.session()
    world.prompt(sess, run_async=run_async)
    sess.busy_started_at = 1000.0
    q = world.tap(run_async, GROUP, BOB, "allow")

    assert world.keys == []                           # nothing typed
    assert sess.busy_started_at > 1000.0              # the wait is not "thinking"
    assert [d for _r, d in world.decisions] == [{"behavior": "allow"}]
    assert world.decisions[0][0] == HOOK
    lines = world.lines(GROUP)
    assert lines == [("✅ <b>api</b> · Allowed by @bob · Bash: rm -rf build/", PROMPT_ID)]
    rec = _audit()[-1]
    assert (rec["action"], rec["via"], rec["username"], rec["tool"]) == (
        "Allowed", "hook_decision", "bob", "Bash")
    assert sess.status is Status.BUSY
    assert any("Allowed" in a for a in _answers(q))
    world.bot._watch_keystroke_answer.assert_not_called()
    # One decision per parked hook: the channel is spent.
    assert sess.pending_prompt_msg["perm"]["hook_reply"] is None


def test_the_session_is_busy_before_the_paced_sends(world, run_async):
    """Claude has its answer the moment the hook decision goes out; a send
    waiting in the flood pacing must not hold the BUSY back (a quick Stop
    in between would otherwise be undone)."""
    sess = world.session()
    world.prompt(sess, run_async=run_async)
    seen = []

    async def _send(*a, **k):
        seen.append(sess.status)
        return MagicMock(message_id=99)

    world.bot._app.bot.send_message = AsyncMock(side_effect=_send)
    world.tap(run_async, GROUP, BOB, "allow")
    assert seen and all(st is Status.BUSY for st in seen)


def test_deny_on_a_separate_prompt_answers_through_the_hook(world, run_async):
    sess = world.session()
    world.prompt(sess, run_async=run_async)
    world.tap(run_async, GROUP, BOB, "deny")

    assert world.keys == []
    (_reply, decision), = world.decisions
    assert decision["behavior"] == "deny" and decision["interrupt"] is False
    assert "@bob" in decision["message"]
    assert world.lines(GROUP) == [
        ("🚫 <b>api</b> · Denied by @bob · Bash: rm -rf build/", PROMPT_ID)]
    assert _audit()[-1]["via"] == "hook_decision"
    world.bot._watch_keystroke_answer.assert_not_called()


def test_the_line_threads_under_the_busy_card_once_there_is_one(world, run_async):
    """The card can arrive after the prompt (the flood limit delays it):
    the line then reads under the card, as on the inline path."""
    sess = world.session()
    world.prompt(sess, run_async=run_async)
    sess.busy_msg_id = 80
    world.tap(run_async, GROUP, BOB, "allow")
    assert world.lines(GROUP) == [
        ("✅ <b>api</b> · Allowed by @bob · Bash: rm -rf build/", 80)]


# ---- typed only once the hook stopped waiting --------------------------------

def test_once_the_hook_gave_up_the_answer_is_typed_and_still_named(
        world, run_async):
    sess = world.session()
    world.prompt(sess, run_async=run_async)
    recv = hr.HookReceiver(world.bot.registry, AsyncMock())
    run_async(recv._on_datagram(json.dumps({
        "hook_event_name": "permission_reply_timeout", "session": S,
        "aipager_request_id": HOOK["request_id"]}).encode()))
    assert sess.pending_prompt_msg["perm"]["hook_reply"] is None

    world.tap(run_async, GROUP, BOB, "deny")
    assert world.decisions == []
    assert world.keys == ["Down"] * 5 + ["Enter"]     # overshoot, unchanged
    assert world.lines(GROUP) == [
        ("🚫 <b>api</b> · Denied by @bob · Bash: rm -rf build/", PROMPT_ID)]
    assert _audit()[-1]["via"] == "keystroke_fallback"
    # The typed refusal row ends the turn like an interrupt: watched.
    world.bot._watch_keystroke_answer.assert_called_once()
    assert world.bot._watch_keystroke_answer.call_args.kwargs["refusal"] is True
    assert world.bot._watch_keystroke_answer.call_args.kwargs["by"] == "@bob"


def test_a_timeout_for_another_request_leaves_the_channel(world, run_async):
    sess = world.session()
    world.prompt(sess, run_async=run_async)
    recv = hr.HookReceiver(world.bot.registry, AsyncMock())
    run_async(recv._on_datagram(json.dumps({
        "hook_event_name": "permission_reply_timeout", "session": S,
        "aipager_request_id": "req-older"}).encode()))
    assert sess.pending_prompt_msg["perm"]["hook_reply"] == HOOK


def test_a_typed_allow_is_watched_but_not_as_a_refusal(world, run_async):
    sess = world.session()
    world.prompt(sess, run_async=run_async, hook=None)
    world.tap(run_async, GROUP, BOB, "allow")
    assert world.keys == ["Enter"]
    assert world.bot._watch_keystroke_answer.call_args.kwargs["refusal"] is False


# ---- the same gates ------------------------------------------------------------

@pytest.mark.parametrize("verb", ["allow", "deny"])
def test_a_read_only_tap_on_a_separate_prompt_decides_nothing(world, run_async, verb):
    sess = world.session()
    world.prompt(sess, run_async=run_async)
    q = world.tap(run_async, GROUP, RO, verb)
    assert _answers(q) == [tap_gate.REFUSED_TEXT]
    assert world.decisions == [] and world.keys == []
    assert world.lines(GROUP) == []
    assert sess.status is Status.INTERACTIVE


def test_an_answered_separate_prompt_decides_nothing_again(world, run_async):
    sess = world.session()
    world.prompt(sess, run_async=run_async)
    world.tap(run_async, GROUP, BOB, "allow")
    world.bot._app.bot.send_message.reset_mock()
    q = world.tap(run_async, GROUP, BOB, "deny")
    assert len(world.decisions) == 1 and world.keys == []
    assert "already answered" in _answers(q)
    assert world.lines(GROUP) == []


def test_another_chats_tap_on_a_separate_prompt_is_refused(world, run_async):
    sess = world.session()
    world.prompt(sess, run_async=run_async)
    q = world.tap(run_async, DM, ALY, "allow")
    assert _answers(q) == [tap_gate.OTHER_CHAT_TEXT]
    assert world.decisions == [] and world.keys == []


# ---- a question, DM parity, the inline prompt ------------------------------------

def test_an_option_on_a_separate_question_posts_the_line(world, run_async):
    sess = world.session()
    world.prompt(sess, run_async=run_async, tool_info={
        "name": "AskUserQuestion", "summary": "AskUserQuestion",
        "input": {"questions": [{"question": "Pick one",
                                 "options": [{"label": "A"}, {"label": "B"}]}]}})
    world.tap(run_async, GROUP, BOB, "opt1")
    assert world.decisions == []
    assert world.keys == ["Down", "Enter"]
    assert world.lines(GROUP) == [
        ("· <b>api</b> · Selected option 2 by @bob · Pick one", PROMPT_ID)]
    world.bot._watch_keystroke_answer.assert_not_called()


def test_dm_parity_a_separate_prompt_in_a_dm(world, run_async):
    sess = world.session(name=DM_S, chat=DM, kind="dm")
    world.prompt(sess, run_async=run_async)
    world.tap(run_async, DM, ALY, "allow", name=DM_S)
    assert world.keys == []
    assert [d for _r, d in world.decisions] == [{"behavior": "allow"}]
    (text, reply), = world.lines(DM)
    assert text.startswith("✅ <b>api</b> · Allowed") and reply == PROMPT_ID
    assert world.lines(GROUP) == []


def test_the_inline_prompts_line_is_unchanged(world, run_async):
    sess = world.session()
    sess.busy_msg_id = 50
    sess.pending_permission = {"tool_summary": "Bash: ls", "tool_info": {"name": "Bash"},
                               "hook_reply": dict(HOOK)}
    world.tap(run_async, GROUP, BOB, "allow", msg_id=50)
    assert world.keys == []
    assert world.lines(GROUP) == [("✅ <b>api</b> · Allowed by @bob · Bash: ls", 50)]
    assert sess.pending_permission is None and sess.status is Status.BUSY


def test_a_card_that_came_after_the_prompt_loses_no_time_it_never_counted(
        world, run_async):
    """The busy card's clock started after the prompt was sent: only the
    wait since then is taken off, so the clock never runs ahead of now."""
    import time as _time
    sess = world.session()
    world.prompt(sess, run_async=run_async)
    sess.pending_prompt_msg["perm"]["wait_started_at"] = _time.monotonic() - 50
    sess.busy_started_at = _time.monotonic() - 5      # the card, 45 s later
    world.tap(run_async, GROUP, BOB, "allow")
    assert sess.busy_started_at <= _time.monotonic()
