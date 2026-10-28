"""Taps around Claude's risky-rm prompt (design.md success criteria 2, 3,
4, 5, 7, 17, 18 and 19; entrypoints.md toasts).

- The order guard: a countdown candidate beside any other prompt refuses
  every typed tap with the "can't tell" pop-up, while a parked hook still
  answers (q6: an older background agent's prompt, then Claude's rm on
  top). A lone prompt and non-candidates keep today's behaviour.
- The refusal window: Allow / Allow always / Deny in the last 15 s before
  the shown time types nothing, sends no hook decision, records nothing.
- Late taps after the refused close: nothing typed, the refused pop-up.
- Auto-deny: never an Escape into a refused dialog, never a loop."""

from __future__ import annotations

import pytest

import tests.two_prompts_rig as rigmod
from aipager.bot.dashboard import ALREADY_ANSWERED
from aipager.scope import Member, Scope
from tests.rm_clock_bb import (
    REFUSE_AT,
    RM,
    T0,
    TOAST_REFUSAL_DUE,
    TOAST_REFUSED_CLOSED,
    TOAST_UNKNOWN,
    WINDOW_AT,
    Env,
)
from tests.two_prompts_rig import BG, BOB, CARD, GROUP, Rig, answers

ALICE = 1  # the rig's owner id (its first member)

IN_WINDOW = REFUSE_AT - 5.0
BEFORE = WINDOW_AT - 0.5
AFTER = REFUSE_AT + 1.0
TYPED_VERBS = ["allow", "deny"]
ANSWER_VERBS = ["allow", "allow_always", "deny"]


def decided(env):
    return [(r["request_id"], d["behavior"]) for r, d in env.rig.decisions]


def extra_ids(env):
    return [m["msg_id"] for m in env.rig.prompt_messages()
            if m.get("reply_to_message_id") == CARD]


# -- q6: an older background agent's prompt, then Claude's rm on top -------------------------------

def q6(env, *, mode="bypassPermissions", timed_out=True):
    """Returns (agent's surface, rm's surface)."""
    env.rig.launch(BG)
    env.rig.agent_bash(BG, "make", request_id="req-bg")
    if timed_out:
        env.rig.timeout("req-bg")
    env.rm(mode=mode, request_id="req-rm")
    if timed_out:
        env.rig.timeout("req-rm")
    return CARD, extra_ids(env)[0]


@pytest.mark.parametrize("mode", ["bypassPermissions", "auto"])
@pytest.mark.parametrize("verb", TYPED_VERBS)
def test_q6_typed_tap_on_the_agents_card_is_refused(cenv, mode, verb):
    agent, _rm = q6(cenv, mode=mode)
    assert answers(cenv.rig.tap(agent, verb)) == [TOAST_UNKNOWN]


@pytest.mark.parametrize("mode", ["bypassPermissions", "auto"])
@pytest.mark.parametrize("verb", TYPED_VERBS)
def test_q6_typed_tap_on_the_rm_card_is_refused(cenv, mode, verb):
    _agent, rm = q6(cenv, mode=mode)
    assert answers(cenv.rig.tap(rm, verb)) == [TOAST_UNKNOWN]


@pytest.mark.parametrize("mode", ["bypassPermissions", "auto"])
def test_q6_typed_taps_on_both_type_nothing(cenv, mode):
    agent, rm = q6(cenv, mode=mode)
    for msg in (agent, rm):
        for verb in TYPED_VERBS:
            cenv.rig.tap(msg, verb)
    assert cenv.rig.keys == []


def test_q6_typed_taps_keep_both_prompts_open(cenv):
    agent, rm = q6(cenv)
    cenv.rig.tap(agent, "allow")
    cenv.rig.tap(rm, "allow")
    assert len(cenv.rig.sess.open_prompts()) == 2


def test_q6_every_verdict_is_unknown(cenv):
    q6(cenv)
    sess = cenv.rig.sess
    assert [sess.keys_verdict(p, 0.0) for p in sess.open_prompts()] == ["unknown", "unknown"]


def test_q6_hook_allow_on_the_agent_still_works(cenv):
    agent, _rm = q6(cenv, timed_out=False)
    cenv.rig.tap(agent, "allow")
    assert decided(cenv) == [("req-bg", "allow")]


def test_q6_hook_allow_on_the_rm_still_works(cenv):
    _agent, rm = q6(cenv, timed_out=False)
    cenv.rig.tap(rm, "allow")
    assert decided(cenv) == [("req-rm", "allow")]


def test_q6_hook_deny_on_the_rm_still_works(cenv):
    _agent, rm = q6(cenv, timed_out=False)
    cenv.rig.tap(rm, "deny")
    assert decided(cenv) == [("req-rm", "deny")]


def test_q6_hook_answers_type_nothing(cenv):
    agent, rm = q6(cenv, timed_out=False)
    cenv.rig.tap(agent, "allow")
    cenv.rig.tap(rm, "deny")
    assert cenv.rig.keys == []


def test_q6_rm_hook_answered_then_agent_alone_types(cenv):
    """2 -> 1 boundary: once the rm is answered by its hook, the agent's
    prompt is alone and its typed tap works again."""
    cenv.rig.launch(BG)
    cenv.rig.agent_bash(BG, "make", request_id="req-bg")
    cenv.rig.timeout("req-bg")
    cenv.rm(request_id="req-rm")
    cenv.rig.tap(extra_ids(cenv)[0], "deny")
    cenv.rig.tap(CARD, "allow")
    assert cenv.rig.keys != []


# -- the guard beside a question ---------------------------------------------------------------

@pytest.mark.parametrize("mode", ["bypassPermissions", "auto"])
def test_question_beside_the_rm_is_refused(cenv, mode):
    cenv.rig.question()
    cenv.rm(mode=mode, hook=False)
    assert answers(cenv.rig.tap(CARD, "opt0")) == [TOAST_UNKNOWN]


@pytest.mark.parametrize("mode", ["bypassPermissions", "auto"])
def test_question_beside_the_rm_types_nothing(cenv, mode):
    cenv.rig.question()
    cenv.rm(mode=mode, hook=False)
    cenv.rig.tap(CARD, "opt0")
    cenv.rig.tap(extra_ids(cenv)[0], "allow")
    assert cenv.rig.keys == []


# -- criterion 4: lone candidate and non-candidates ------------------------------------------------

@pytest.mark.parametrize("mode", ["bypassPermissions", "auto"])
def test_a_lone_rm_typed_tap_types_keys(env, mode):
    env.rm(mode=mode, hook=False)
    env.rig.tap(env.separate_ids()[0], "allow")
    assert env.rig.keys != []


@pytest.mark.parametrize("mode", ["default", "acceptEdits", "plan", None])
def test_two_non_candidate_claude_prompts_keep_fifo(cenv, mode):
    """Today's rule (8.120): the first of Claude's own prompts is on top."""
    cenv.rm(RM, mode=mode, hook=False, tool_use_id="toolu_p1")
    cenv.rm("rm -rf dist/", mode=mode, hook=False, tool_use_id="toolu_p2")
    cenv.rig.tap(CARD, "allow")
    assert cenv.rig.keys != []


@pytest.mark.parametrize("mode", ["bypassPermissions", "auto"])
def test_two_candidate_claude_prompts_refuse_the_first(cenv, mode):
    cenv.rm(RM, mode=mode, hook=False, tool_use_id="toolu_p1")
    cenv.rm("rm -rf dist/", mode=mode, hook=False, tool_use_id="toolu_p2")
    assert answers(cenv.rig.tap(CARD, "allow")) == [TOAST_UNKNOWN]


def test_a_non_removal_candidate_bash_also_guards(cenv):
    """The guard is about the countdown candidate (main loop, Bash, a
    candidate mode), not about the clock: ``ls`` in Full access counts."""
    cenv.rig.question()
    cenv.rm("ls -la", hook=False)
    assert answers(cenv.rig.tap(CARD, "opt0")) == [TOAST_UNKNOWN]


@pytest.mark.parametrize("mode", ["bypassPermissions", "auto"])
def test_an_agents_bypass_bash_is_not_a_candidate(cenv, mode):
    cenv.rig.launch(BG)
    cenv.rig.agent_bash(BG, "rm -rf x", request_id="req-bg", mode=mode)
    cenv.rig.question()
    cenv.rig.tap(extra_ids(cenv)[0], "opt1")
    assert cenv.rig.keys != []


def test_a_non_bash_bypass_prompt_is_not_a_candidate(cenv):
    cenv.hook("PermissionRequest", tool_name="Edit",
              tool_input={"file_path": "/home/alice/a", "old_string": "a", "new_string": "b"},
              tool_use_id="toolu_e1", permission_mode="bypassPermissions")
    cenv.hook("PermissionRequest", tool_name="Edit",
              tool_input={"file_path": "/home/alice/b", "old_string": "a", "new_string": "b"},
              tool_use_id="toolu_e2", permission_mode="bypassPermissions")
    cenv.rig.tap(CARD, "allow")
    assert cenv.rig.keys != []


# -- criterion 5: a Notification-fallback prompt counts ------------------------------------------

def test_notification_fallback_beside_a_question_is_refused(cenv):
    cenv.hook("Notification", notification_type="permission_prompt",
              message="Claude needs your permission to use Bash")
    cenv.rig.question()
    assert answers(cenv.rig.tap(extra_ids(cenv)[0], "opt0")) == [TOAST_UNKNOWN]


# -- criterion 18: the refusal window before the close ------------------------------------------------

@pytest.mark.parametrize("verb", ANSWER_VERBS)
def test_tap_in_the_window_with_a_parked_hook_shows_refusal_due(env, verb):
    env.rm()
    env.wall.t = IN_WINDOW
    assert answers(env.rig.tap(env.separate_ids()[0], verb)) == [TOAST_REFUSAL_DUE]


@pytest.mark.parametrize("verb", ANSWER_VERBS)
def test_tap_in_the_window_with_a_parked_hook_sends_no_decision(env, verb):
    env.rm()
    env.wall.t = IN_WINDOW
    env.rig.tap(env.separate_ids()[0], verb)
    assert (env.rig.decisions, env.rig.keys) == ([], [])


@pytest.mark.parametrize("verb", ANSWER_VERBS)
def test_typed_tap_in_the_window_shows_refusal_due(env, verb):
    env.rm(hook=False)
    env.wall.t = IN_WINDOW
    assert answers(env.rig.tap(env.separate_ids()[0], verb)) == [TOAST_REFUSAL_DUE]


@pytest.mark.parametrize("verb", ANSWER_VERBS)
def test_typed_tap_in_the_window_types_nothing(env, verb):
    env.rm(hook=False)
    env.wall.t = IN_WINDOW
    env.rig.tap(env.separate_ids()[0], verb)
    assert env.rig.keys == []


@pytest.mark.parametrize("verb", ANSWER_VERBS)
def test_tap_in_the_window_records_nothing(env, verb):
    env.rm()
    env.wall.t = IN_WINDOW
    env.rig.tap(env.separate_ids()[0], verb)
    assert (env.audit(), env.rig.sess.tool_history) == ([], [])


def test_tap_in_the_window_keeps_the_prompt_open(env):
    env.rm()
    env.wall.t = IN_WINDOW
    env.rig.tap(env.separate_ids()[0], "allow")
    assert len(env.rig.sess.open_prompts()) == 1


def test_tap_in_the_window_keeps_the_buttons(env):
    env.rm()
    env.wall.t = IN_WINDOW
    q = env.rig.tap(env.separate_ids()[0], "allow")
    assert (q.edit_message_reply_markup.await_count, q.edit_message_text.await_count,
            env.text_edits()) == (0, 0, [])


def test_tap_in_the_window_on_the_inline_card_shows_refusal_due(cenv):
    cenv.rm()
    cenv.wall.t = IN_WINDOW
    assert answers(cenv.rig.tap(CARD, "allow")) == [TOAST_REFUSAL_DUE]


def test_tap_at_the_window_edge_is_refused(env):
    env.rm()
    env.wall.t = WINDOW_AT
    env.rig.tap(env.separate_ids()[0], "allow")
    assert env.rig.decisions == []


def test_tap_long_past_the_deadline_before_any_close_is_refused(env):
    """Live-main Q3: a late tap reported "Allowed" while Claude Code had
    already refused it. No close has run yet here."""
    env.rm()
    env.wall.t = REFUSE_AT + 300.0
    env.rig.tap(env.separate_ids()[0], "allow")
    assert env.rig.decisions == []


def test_tap_long_past_the_deadline_says_allowed_nowhere(env):
    env.rm(hook=False)
    env.wall.t = REFUSE_AT + 300.0
    env.rig.tap(env.separate_ids()[0], "allow")
    assert "Allowed" not in repr(env.audit()) + repr(env.rig.sess.tool_history) + repr(
        [m.get("text") for m in env.rig.sent])


def test_hook_allow_just_before_the_window_works(env):
    env.rm()
    env.wall.t = BEFORE
    env.rig.tap(env.separate_ids()[0], "allow")
    assert decided(env) == [("req-toolu_p1", "allow")]


def test_typed_allow_just_before_the_window_types(env):
    env.rm(hook=False)
    env.wall.t = BEFORE
    env.rig.tap(env.separate_ids()[0], "allow")
    assert env.rig.keys != []


def test_a_non_clock_prompt_has_no_refusal_window(env):
    env.rm(mode="default")
    env.wall.t = REFUSE_AT + 300.0
    env.rig.tap(env.separate_ids()[0], "allow")
    assert decided(env) == [("req-toolu_p1", "allow")]


# -- criterion 17: late taps after the refused close -------------------------------------------------

def closed_by_monitor(env):
    env.rm()
    env.refused_transcript()
    env.scan(AFTER)


def closed_by_next_tool(env):
    env.rm()
    env.wall.t = AFTER
    env.hook("PreToolUse", tool_name="Read", tool_input={"file_path": "/home/alice/a"},
             tool_use_id="toolu_next")


CLOSED = {"monitor": closed_by_monitor, "pretooluse": closed_by_next_tool}


@pytest.mark.parametrize("how", CLOSED)
@pytest.mark.parametrize("verb", ANSWER_VERBS)
def test_late_tap_on_the_refused_message_shows_refused_closed(env, how, verb):
    CLOSED[how](env)
    assert answers(env.rig.tap(env.separate_ids()[0], verb)) == [TOAST_REFUSED_CLOSED]


@pytest.mark.parametrize("how", CLOSED)
def test_late_tap_on_the_refused_message_types_nothing(env, how):
    CLOSED[how](env)
    for verb in ANSWER_VERBS:
        env.rig.tap(env.separate_ids()[0], verb)
    assert (env.rig.keys, env.rig.decisions) == ([], [])


@pytest.mark.parametrize("how", CLOSED)
def test_late_tap_on_the_refused_inline_card_shows_refused_closed(cenv, how):
    CLOSED[how](cenv)
    assert answers(cenv.rig.tap(CARD, "allow")) == [TOAST_REFUSED_CLOSED]


def test_late_tap_records_no_allowed(env):
    closed_by_monitor(env)
    env.rig.tap(env.separate_ids()[0], "allow")
    assert "Allowed" not in repr(env.audit()) + repr(env.rig.sess.tool_history)


def test_late_tap_after_a_silent_ok_close_is_not_refused_wording(env):
    env.rm()
    env.call()
    env.result(is_error=False)
    env.scan(AFTER)
    assert answers(env.rig.tap(env.separate_ids()[0], "allow")) == [str(ALREADY_ANSWERED)]


def test_late_tap_after_a_before_window_close_is_already_answered(env):
    env.rm()
    env.wall.t = T0 + 5.0
    env.hook("PreToolUse", tool_name="Read", tool_input={"file_path": "/home/alice/a"},
             tool_use_id="toolu_next")
    assert answers(env.rig.tap(env.separate_ids()[0], "allow")) == [str(ALREADY_ANSWERED)]


def test_a_new_rm_after_a_refused_one_answers_normally(env):
    """Error guessing: the refused memory of one surface must not leak
    into the next prompt's message."""
    closed_by_monitor(env)
    env.wall.t = AFTER + 10.0
    env.rm("rm -rf dist/", tool_use_id="toolu_p2", request_id="req-p2")
    env.rig.tap(env.separate_ids()[-1], "allow")
    assert decided(env) == [("req-p2", "allow")]


# -- criteria 7 and 19: auto-deny --------------------------------------------------------------------

def deny_scopes(*tools):
    def _s():
        return [Scope(chat_id=GROUP, kind="group", label="team", deny_tools=tools,
                      members=(Member(id=ALICE, label="alice", role="owner"),
                               Member(id=BOB, label="bob", role="user")))]
    return _s


def denv(mk_bot, monkeypatch, run_async, tmp_path, wall, tools):
    monkeypatch.setattr(rigmod, "scopes", deny_scopes(*tools))
    env = Env(Rig(mk_bot, monkeypatch, run_async, card=True), wall, tmp_path, run_async)
    env.rig.sess.last_driver_user_id = BOB
    return env


def owners_rm_then_users_write(env):
    """Claude's rm opens in the owner's turn (no auto-deny), then the
    driver is a user whose role refuses Write, and Claude's Write prompt
    arrives beside the rm: its auto-deny would have to type."""
    env.rig.sess.last_driver_user_id = ALICE
    env.rm(hook=False)
    env.rig.sess.last_driver_user_id = BOB
    write_request(env)


def write_request(env, tuid="toolu_w1"):
    env.hook("PermissionRequest", tool_name="Write",
             tool_input={"file_path": "/home/alice/a.py", "content": "x"}, tool_use_id=tuid,
             permission_mode="bypassPermissions")


def test_auto_deny_beside_the_rm_types_nothing(mk_bot, monkeypatch, run_async, tmp_path,
                                               wall, _alive):
    env = denv(mk_bot, monkeypatch, run_async, tmp_path, wall, ("Write",))
    owners_rm_then_users_write(env)
    assert env.rig.keys == []


def test_auto_deny_beside_the_rm_types_nothing_over_ticks(mk_bot, monkeypatch, run_async,
                                                          tmp_path, wall, _alive):
    env = denv(mk_bot, monkeypatch, run_async, tmp_path, wall, ("Write",))
    owners_rm_then_users_write(env)
    for k in range(3):
        env.scan(T0 + 2.0 * (k + 1))
    assert env.rig.keys == []


def test_auto_deny_beside_the_rm_does_not_loop(mk_bot, monkeypatch, run_async, tmp_path,
                                               wall, _alive):
    env = denv(mk_bot, monkeypatch, run_async, tmp_path, wall, ("Write",))
    owners_rm_then_users_write(env)
    env.scan(T0 + 2.0)
    sent = len(env.rig.sent)
    for k in range(3):
        env.scan(T0 + 4.0 + 2.0 * k)
    assert len(env.rig.sent) == sent


def test_the_overlap_really_holds_two_prompts(mk_bot, monkeypatch, run_async, tmp_path,
                                              wall, _alive):
    """Control for criterion 7: the scenario does put the Write beside the rm."""
    env = denv(mk_bot, monkeypatch, run_async, tmp_path, wall, ("Write",))
    owners_rm_then_users_write(env)
    assert sorted(p["tool_info"]["name"] for p in env.rig.sess.open_prompts()) == [
        "Bash", "Write"]


def test_auto_deny_of_a_lone_rm_types_escape(mk_bot, monkeypatch, run_async, tmp_path,
                                             wall, _alive):
    """Control for criterion 19: outside the window the auto-deny types."""
    env = denv(mk_bot, monkeypatch, run_async, tmp_path, wall, ("Bash",))
    env.rm(hook=False)
    assert env.rig.keys == ["Escape"]


def _deferred_rm_auto_deny(env, at):
    """Claude's question is open, then its rm (refused for the user) waits
    behind it; the question is answered in the terminal at *at*, leaving the
    rm's deferred auto-deny due."""
    env.rig.question()
    env.rm(hook=False)
    env.wall.t = at
    env.hook("PostToolUse", tool_name="AskUserQuestion", tool_use_id="toolu_q1",
             tool_input={}, tool_response={})
    env.scan(at)


def test_deferred_auto_deny_before_the_window_types_escape(mk_bot, monkeypatch, run_async,
                                                           tmp_path, wall, _alive):
    """Control: the deferred auto-deny fires once the rm is alone."""
    env = denv(mk_bot, monkeypatch, run_async, tmp_path, wall, ("Bash",))
    _deferred_rm_auto_deny(env, T0 + 5.0)
    assert env.rig.keys == ["Escape"]


def test_deferred_auto_deny_in_the_window_types_no_escape(mk_bot, monkeypatch, run_async,
                                                          tmp_path, wall, _alive):
    env = denv(mk_bot, monkeypatch, run_async, tmp_path, wall, ("Bash",))
    _deferred_rm_auto_deny(env, IN_WINDOW)
    assert "Escape" not in env.rig.keys
