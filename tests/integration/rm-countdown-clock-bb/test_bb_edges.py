"""Error guessing around the refused close (design.md criteria 2, 12, 13,
17 and decision 3):

- q6 end to end: the older background agent's prompt with its hook gone,
  Claude's rm on top, Claude Code refuses the rm; the agent's prompt is
  then alone and answerable again, and no record says Allowed;
- the rm as an extra message beside another prompt is closed on that
  extra message;
- a terminal "No" (interrupt marker) wins over the refused close;
- a hook reply timeout arriving after the refused close changes nothing;
- the refused inline card no longer shows the clock line as a live ask."""

from __future__ import annotations

import json

from aipager.state import Status
from tests.rm_clock_bb import CLOCK_HEAD, REFUSE_AT, REFUSED_EN, TOAST_REFUSED_CLOSED
from tests.two_prompts_rig import BG, CARD, answers

AFTER = REFUSE_AT + 1.0
MARKER = "[Request interrupted by user for tool use]"


def extra_ids(env):
    return [m["msg_id"] for m in env.rig.prompt_messages()
            if m.get("reply_to_message_id") == CARD]


def q6_refused(cenv):
    cenv.rig.launch(BG)
    cenv.rig.agent_bash(BG, "make", request_id="req-bg")
    cenv.rig.timeout("req-bg")
    cenv.rm(request_id="req-rm")
    cenv.rig.timeout("req-rm")
    cenv.refused_transcript()
    cenv.scan(AFTER)
    return CARD, extra_ids(cenv)[0]


def test_q6_refused_rm_leaves_the_agents_prompt_open(cenv):
    q6_refused(cenv)
    assert [p["tool_info"].get("agent_id") for p in cenv.rig.sess.open_prompts()] == [BG]


def test_q6_refused_rm_keeps_the_session_waiting(cenv):
    q6_refused(cenv)
    assert cenv.rig.sess.status == Status.INTERACTIVE


def test_q6_refused_rm_edits_its_extra_message(cenv):
    _agent, rm = q6_refused(cenv)
    assert [k["message_id"] for k in cenv.refused_text_edits()] == [rm]


def test_q6_after_the_refusal_the_agents_typed_tap_works(cenv):
    agent, _rm = q6_refused(cenv)
    cenv.rig.tap(agent, "allow")
    assert cenv.rig.keys != []


def test_q6_after_the_refusal_the_rm_tap_types_nothing(cenv):
    _agent, rm = q6_refused(cenv)
    q = cenv.rig.tap(rm, "allow")
    assert (answers(q), cenv.rig.keys) == ([TOAST_REFUSED_CLOSED], [])


def test_q6_nothing_says_allowed_for_the_rm(cenv):
    _agent, rm = q6_refused(cenv)
    cenv.rig.tap(rm, "allow")
    rm_records = [a for a in cenv.audit() if "rm -rf" in str(a.get("summary"))]
    assert all(a.get("action") != "Allowed" and not str(a.get("action")).startswith("Allow")
               for a in rm_records)


def test_extra_rm_beside_a_question_is_closed_on_its_extra_message(cenv):
    cenv.rig.question()
    cenv.rm(hook=False)
    cenv.refused_transcript()
    cenv.scan(AFTER)
    assert [k["message_id"] for k in cenv.refused_text_edits()] == extra_ids(cenv)


def test_extra_rm_refused_edit_keeps_the_question_open(cenv):
    cenv.rig.question()
    cenv.rm(hook=False)
    cenv.refused_transcript()
    cenv.scan(AFTER)
    assert [p["tool_info"]["name"] for p in cenv.rig.sess.open_prompts()] == ["AskUserQuestion"]


def test_terminal_no_wins_over_the_refused_close(env):
    env.rm(hook=False)
    env.refused_transcript()
    with env.path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "user", "message": {"role": "user", "content": [
            {"type": "text", "text": MARKER}]}}) + "\n")
    env.scan(AFTER)
    assert env.refused_audit() == []


def test_hook_timeout_after_the_refused_close_changes_nothing(env):
    env.rm(request_id="req-rm")
    env.refused_transcript()
    env.scan(AFTER)
    env.rig.timeout("req-rm")
    assert (env.rig.sess.open_prompts(), env.rig.keys, len(env.refused_audit())) == ([], [], 1)


def test_refused_inline_card_drops_the_clock_line(cenv):
    cenv.rm()
    cenv.refused_transcript()
    cenv.scan(AFTER)
    assert CLOCK_HEAD not in cenv.last_card_text()


def test_refused_separate_text_appends_once(env):
    env.rm()
    env.refused_transcript()
    env.scan(AFTER)
    env.hook("Stop")
    texts = [k["text"] for k in env.text_edits()]
    assert sum(t.count(REFUSED_EN) for t in texts) == 1
