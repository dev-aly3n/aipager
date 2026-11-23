"""A tap on a background agent's prompt right after its hook gave up.

The agent's permission dialog enters Claude Code's terminal only once its
PermissionRequest hook is gone, and a freshly drawn dialog ignores a key
for a moment (Claude Code 2.1.326, release harness bga-keys-162728: an
Enter in the same second as the hook's timeout went into the empty input
line, the session went IDLE and the dialog stayed open). Such a tap types
nothing, keeps its buttons and says the prompt is still opening, until
Claude Code's record proves the dialog has been up for a moment or a short
grace after the hook's end has run out. Claude's own and a foreground
agent's prompts, whose dialog is up from arrival, are typed at once.
"""

from __future__ import annotations

import json
import time

import pytest

from aipager import i18n, prompt_gap, prompt_stack
from aipager.bot.callbacks import PROMPT_EXPIRED, PROMPT_OPENING, PROMPT_ORDER_UNKNOWN
from aipager.state import Status
from tests.two_prompts_rig import BG, FG, Rig, answers

EN = i18n.get("en")
CMD = "sed -i s/x/y/ heartbeat"
INTERIM = "The developer runs in the background."


@pytest.fixture
def rig(mk_bot, monkeypatch, run_async):
    rig = Rig(mk_bot, monkeypatch, run_async)

    async def _post(method, payload, **kw):
        if not method.startswith("send"):
            return {"ok": True, "result": True}
        msg_id = next(rig.ids)
        rig.sent.append({"chat_id": payload.get("chat_id"),
                         "text": json.dumps(payload, ensure_ascii=False),
                         "msg_id": msg_id, "rich": method})
        return {"ok": True, "result": {"message_id": msg_id}}

    monkeypatch.setattr("aipager.bot.rich_message._post", _post)
    rig.reads = []

    async def _record(sess):
        rig.reads.append(sess.name)
        return rig.record

    rig.record = None
    monkeypatch.setattr(prompt_gap, "_record", _record)
    return rig


def _after_stop(rig, agent=BG, *, background=True):
    rig.launch(agent, background=background)
    rig.hook("Stop", stop_hook_active=False, last_assistant_message=INTERIM)


def _prompt_msgs(rig):
    return [m for m in rig.prompt_messages() if "Allow" in " ".join(rig.buttons(m))]


def _order(rig):
    [perm] = rig.sess.open_prompts()
    return perm["tool_info"]["order"]


def _refused_opening(rig, q):
    assert rig.keys == []
    assert rig.decisions == []
    assert answers(q) == [EN.text(PROMPT_OPENING)]
    assert answers(q) != [EN.text(PROMPT_ORDER_UNKNOWN)]
    q.edit_message_reply_markup.assert_not_awaited()
    q.edit_message_text.assert_not_awaited()
    assert len(rig.sess.open_prompts()) == 1
    assert rig.sess.status == Status.INTERACTIVE


@pytest.mark.parametrize("verb", ["allow", "deny", "allow_always"])
def test_tap_right_after_timeout_is_refused_with_opening_toast(rig, verb):
    _after_stop(rig)
    rig.agent_bash(BG, CMD, request_id="req-1")
    [msg] = _prompt_msgs(rig)
    rig.timeout("req-1")
    q = rig.tap(msg["msg_id"], verb)
    _refused_opening(rig, q)
    # The prompt is still the session's, with its buttons.
    assert rig.sess.prompt_surface(rig.sess.open_prompts()[0]) == msg["msg_id"]


def test_tap_with_hook_gone_before_timeout_datagram_is_refused(rig):
    """The hook removed its reply socket before aipager handled its
    timeout: the decision fails, ``timed_out_at`` is still 0."""
    _after_stop(rig)
    rig.agent_bash(BG, CMD, request_id="req-1")
    [msg] = _prompt_msgs(rig)
    rig.hook_ok = False
    assert _order(rig)["timed_out_at"] == 0.0
    q = rig.tap(msg["msg_id"], "allow")
    _refused_opening(rig, q)


def test_tap_after_grace_types_enter(rig):
    _after_stop(rig)
    rig.agent_bash(BG, CMD, request_id="req-1")
    [msg] = _prompt_msgs(rig)
    rig.timeout("req-1")
    _order(rig)["timed_out_at"] = time.monotonic() - prompt_stack.OPEN_GRACE - 0.01
    rig.tap(msg["msg_id"], "allow")
    assert rig.keys == ["Enter"]
    assert rig.decisions == []
    assert rig.sess.open_prompts() == []


def test_tap_after_record_waiting_and_settle_types(rig):
    _after_stop(rig)
    rig.agent_bash(BG, CMD, request_id="req-1")
    [msg] = _prompt_msgs(rig)
    rig.timeout("req-1")
    order = _order(rig)
    gone = time.monotonic() - 2.4
    order["timed_out_at"] = gone
    wall_gone = time.time() - (time.monotonic() - gone)
    rig.record = {"status": "waiting", "waitingFor": "permission prompt",
                  "statusAt": wall_gone + 0.3}
    rig.tap(msg["msg_id"], "allow")
    assert rig.reads == [rig.sess.name]
    assert order["dialog_seen_at"] == pytest.approx(gone + 0.3, abs=0.05)
    assert rig.keys == ["Enter"]


def test_record_waiting_too_recently_still_refuses(rig):
    _after_stop(rig)
    rig.agent_bash(BG, CMD, request_id="req-1")
    [msg] = _prompt_msgs(rig)
    rig.timeout("req-1")
    order = _order(rig)
    gone = time.monotonic() - 1.0
    order["timed_out_at"] = gone
    wall_gone = time.time() - (time.monotonic() - gone)
    rig.record = {"status": "waiting", "waitingFor": "permission prompt",
                  "statusAt": wall_gone + 0.3}
    q = rig.tap(msg["msg_id"], "allow")
    assert order.get("dialog_seen_at")
    _refused_opening(rig, q)


def test_prompt_closed_during_record_read_is_refused_expired(rig, monkeypatch):
    _after_stop(rig)
    rig.agent_bash(BG, CMD, request_id="req-1")
    [msg] = _prompt_msgs(rig)
    rig.timeout("req-1")
    sess = rig.sess

    async def _record(s):
        # The tool ran (answered at the terminal) while the record was read.
        sess.close_prompts(lambda p: True)
        return None

    monkeypatch.setattr(prompt_gap, "_record", _record)
    q = rig.tap(msg["msg_id"], "allow")
    assert rig.keys == [] and rig.decisions == []
    assert answers(q) == [EN.text(PROMPT_EXPIRED)]


def test_claude_own_prompt_after_timeout_types_at_once(rig):
    rig.claude_bash("make build", request_id="req-c")
    rig.timeout("req-c")
    rig.tap(50, "allow")
    assert rig.keys == ["Enter"]
    assert rig.reads == []


def test_foreground_agent_prompt_after_timeout_types_at_once(rig):
    rig.launch(FG, background=False)
    rig.agent_bash(FG, "make", request_id="req-f")
    rig.timeout("req-f")
    assert _order(rig)["launch"] == prompt_stack.FOREGROUND
    rig.tap(50, "allow")
    assert rig.keys == ["Enter"]


@pytest.mark.parametrize("verb, behavior", [("allow", "allow"), ("deny", "deny")])
def test_parked_hook_decision_answers_even_in_gap(rig, verb, behavior):
    _after_stop(rig)
    rig.agent_bash(BG, CMD, request_id="req-1")
    [msg] = _prompt_msgs(rig)
    assert rig.sess.keys_verdict(rig.sess.open_prompts()[0],
                                 time.monotonic()) == "opening"
    rig.tap(msg["msg_id"], verb)
    assert [(r["request_id"], d["behavior"]) for r, d in rig.decisions] == [
        ("req-1", behavior)]
    assert rig.keys == []


def test_background_agent_question_option_in_gap_is_refused(rig):
    _after_stop(rig)
    n = len(rig.sent)
    rig.question(agent=BG)
    [msg] = [m for m in rig.sent[n:] if m.get("reply_markup") is not None]
    q = rig.tap(msg["msg_id"], "opt0")
    assert rig.keys == []
    assert answers(q) == [EN.text(PROMPT_OPENING)]
    _order(rig)["arrived"] = time.monotonic() - prompt_stack.OPEN_GRACE - 0.01
    rig.tap(msg["msg_id"], "opt0")
    assert rig.keys and rig.keys[-1] == "Enter"


def test_restored_prompt_without_order_types_at_once(rig):
    """A prompt with no order (restored after a restart: an agent's never
    is) counts as Claude's own: never in a gap."""
    _after_stop(rig)
    rig.agent_bash(BG, CMD, request_id="req-1")
    [msg] = _prompt_msgs(rig)
    rig.timeout("req-1")
    del rig.sess.open_prompts()[0]["tool_info"]["order"]
    rig.tap(msg["msg_id"], "allow")
    assert rig.keys == ["Enter"]
