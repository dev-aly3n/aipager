"""A chat tap on a prompt already answered in the terminal types nothing and
records nothing (roadmap 8.151), and the clock line and the refused call's
row say what really happened.

What Claude Code 2.1.311 fires (its PostToolUseFailure runs only in the
catch around the allowed tool's own call):

- Yes in the terminal, the call then fails: PostToolUseFailure with
  ``is_interrupt`` false. The tool ran, so the prompt closes exactly as
  after a PostToolUse: every surface refuses a tap, the session works.
- Escape while the allowed call runs: PostToolUseFailure with
  ``is_interrupt`` true, then the interrupt marker and no Stop. The record
  stays for the session monitor's dialog_ended_in_terminal, which ends
  the turn; meanwhile no surface of it types.
- "No" or Escape in the dialog itself: no hook at all, only the marker
  (dialog_ended_in_terminal, unchanged).

Each surface is driven: the separate prompt message, the inline prompt on
the busy card, and a copy the pinned bar re-sent."""

from __future__ import annotations

import json
import time

import pytest

from aipager import state
from aipager.state import Status
from tests.rm_clock_bb import CLOCK_HEAD, RM, T0
from tests.two_prompts_rig import CARD, GROUP, S, answers

TUID = "toolu_p1"
MARKER = "[Request interrupted by user for tool use]"
ALREADY = "already answered"


def failure(*, interrupt=False, command=RM, tuid=TUID):
    return {"tool_name": "Bash", "tool_input": {"command": command, "description": command},
            "tool_use_id": tuid, "error": "Exit code 1", "is_interrupt": interrupt}


def failed(env, **kw):
    env.wall.t = T0 + 20.0
    env.hook("PostToolUseFailure", **failure(**kw))


def ran(env):
    env.wall.t = T0 + 20.0
    env.hook("PostToolUse", tool_name="Bash",
             tool_input={"command": RM, "description": RM}, tool_use_id=TUID,
             tool_response={"stdout": "", "stderr": "", "interrupted": False})


def marker(env):
    env.call()
    env.result(is_error=True)
    with env.path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "user", "message": {"role": "user", "content": [
            {"type": "text", "text": MARKER}]}}) + "\n")


def resend(env) -> int:
    """The pinned bar's Answer: a copy of the prompt; its message id."""
    before = len(env.rig.sent)
    outcome = env.run_async(env.rig.bot._resend_pending_prompt(GROUP, env.rig.sess))
    assert outcome == "sent" and len(env.rig.sent) == before + 1
    return env.rig.sent[-1]["msg_id"]


def tapped_nothing(env, q) -> bool:
    return env.rig.keys == [] and env.rig.decisions == [] and ALREADY in answers(q)


def allowed_audit(env) -> list:
    return [a for a in env.audit() if a.get("action") == "Allowed"]


# -- Yes in the terminal, the call failed (no interrupt) ------------------------------------

@pytest.mark.parametrize("hook", [True, False], ids=["hook-parked", "typed"])
def test_failed_call_separate_message_tap_types_nothing(env, hook):
    env.rm(hook=hook)
    failed(env)
    q = env.rig.tap(env.separate_ids()[0], "allow")
    assert tapped_nothing(env, q)
    assert allowed_audit(env) == []


def test_failed_call_inline_card_tap_types_nothing(cenv):
    cenv.rm(hook=False)
    failed(cenv)
    q = cenv.rig.tap(CARD, "allow")
    assert cenv.rig.keys == [] and ALREADY in answers(q)


def test_failed_call_resent_copy_tap_types_nothing(cenv):
    cenv.rm(hook=False)
    copy = resend(cenv)
    failed(cenv)
    q = cenv.rig.tap(copy, "allow")
    assert tapped_nothing(cenv, q)


@pytest.mark.parametrize("mode", ["default", "acceptEdits", "bypassPermissions"])
def test_failed_call_closes_any_prompt(env, mode):
    """Every prompt, not only a clocked rm: the session works again."""
    env.rig.claude_bash("make test", tool_use_id=TUID, hook=False, mode=mode)
    failed(env, command="make test")
    assert (env.rig.sess.open_prompts(), env.rig.sess.status) == ([], Status.BUSY)


def test_control_another_calls_failure_leaves_the_prompt_answerable(env):
    """The tap path itself works: another call's failure closes nothing,
    and the tap types Enter."""
    env.rm(hook=False)
    failed(env, command="make test", tuid="toolu_other")
    env.rig.tap(env.separate_ids()[0], "allow")
    assert env.rig.keys == ["Enter"]


def test_control_the_resent_copy_answers_while_open(cenv):
    cenv.rm(hook=False)
    copy = resend(cenv)
    cenv.rig.tap(copy, "allow")
    assert cenv.rig.keys == ["Enter"]


# -- Escape while the call ran (is_interrupt) ----------------------------------------------

def test_interrupted_call_keeps_the_record_for_the_turns_end(env):
    env.rm(hook=False)
    failed(env, interrupt=True)
    assert (len(env.rig.sess.open_prompts()), env.rig.sess.status) == (1, Status.INTERACTIVE)


@pytest.mark.parametrize("hook", [True, False], ids=["hook-parked", "typed"])
def test_interrupted_call_separate_message_tap_types_nothing(env, hook):
    env.rm(hook=hook)
    failed(env, interrupt=True)
    q = env.rig.tap(env.separate_ids()[0], "allow")
    assert tapped_nothing(env, q)


def test_interrupted_call_inline_card_tap_types_nothing(cenv):
    cenv.rm(hook=False)
    failed(cenv, interrupt=True)
    q = cenv.rig.tap(CARD, "allow")
    assert cenv.rig.keys == [] and ALREADY in answers(q)


def test_interrupted_call_resent_copy_tap_types_nothing(cenv):
    cenv.rm(hook=False)
    copy = resend(cenv)
    failed(cenv, interrupt=True)
    q = cenv.rig.tap(copy, "allow")
    assert tapped_nothing(cenv, q)


@pytest.mark.parametrize("surface", ["separate", "inline"])
def test_the_bar_sends_no_copy_of_an_interrupted_prompt(env, cenv, surface):
    """Its dialog is gone: the bar's Answer says it is not waiting."""
    e = env if surface == "separate" else cenv
    e.rm(hook=False)
    failed(e, interrupt=True)
    sent = len(e.rig.sent)
    outcome = e.run_async(e.rig.bot._resend_pending_prompt(GROUP, e.rig.sess))
    assert (outcome, len(e.rig.sent)) == ("not_waiting", sent)


@pytest.mark.parametrize("surface", ["separate", "inline"])
def test_interrupted_call_turn_still_ends_at_the_marker(env, cenv, surface):
    """dialog_ended_in_terminal still sees the kept record: the turn ends
    at the marker with no Stop, every prompt closed."""
    e = env if surface == "separate" else cenv
    e.rm(hook=False)
    failed(e, interrupt=True)
    marker(e)
    e.scan(T0 + 22.0)
    assert (e.rig.sess.status, e.rig.sess.open_prompts()) == (Status.IDLE, [])


def test_control_terminal_no_still_ends_the_turn(env):
    """A terminal "No" fires no hook: the marker alone ends the turn."""
    env.rm(hook=False)
    marker(env)
    env.scan(T0 + 22.0)
    assert env.rig.sess.status == Status.IDLE


# -- the clock line goes once the prompt is closed (not refused) ----------------------------

def prompt_edits(env, msg_id) -> list[dict]:
    return [k for k in env.text_edits() if k.get("message_id") == msg_id]


@pytest.mark.parametrize("close", [
    lambda e: failed(e), lambda e: failed(e, interrupt=True), ran,
    lambda e: (marker(e), e.scan(T0 + 22.0)),
], ids=["failed", "interrupted", "ran", "terminal-no"])
def test_a_terminal_close_takes_the_clock_line_off_the_message(env, close):
    env.rm(hook=False)
    [sent] = env.rig.prompt_messages()
    assert CLOCK_HEAD in sent["text"]
    close(env)
    [edit] = prompt_edits(env, sent["msg_id"])
    assert edit["text"] == sent["text"].rsplit("\n", 1)[0]
    # Closed: the edit brings no answer buttons back (roadmap 8.152).
    assert edit["reply_markup"] is None


def test_the_clock_line_goes_once(env):
    env.rm(hook=False)
    failed(env, interrupt=True)
    marker(env)
    env.scan(T0 + 22.0)
    assert len(prompt_edits(env, env.separate_ids()[0])) == 1


def test_a_prompt_with_no_clock_is_not_edited(env):
    env.rig.claude_bash("make test", tool_use_id=TUID, hook=False, mode="default")
    failed(env, command="make test")
    assert prompt_edits(env, env.separate_ids()[0]) == []


def test_a_tap_takes_the_clock_line_off(env):
    env.rm()
    [sent] = env.rig.prompt_messages()
    q = env.rig.tap(sent["msg_id"], "allow")
    text = q.edit_message_text.await_args.args[0]
    assert CLOCK_HEAD not in text and "Allowed" in text
    assert text.startswith(sent["text"].rsplit("\n", 1)[0])


def test_control_a_tap_on_a_plain_prompt_keeps_its_text(env):
    env.rig.claude_bash("make test", tool_use_id=TUID, mode="default")
    q = env.rig.tap(env.separate_ids()[0], "allow")
    # The rig's tapped message shows this plain text: edited on as before.
    assert q.edit_message_text.await_args.args[0].startswith(
        "🔐 api · Permission needed\n\n")


def test_the_inline_card_drops_the_clock_line_once_closed(cenv):
    cenv.rm(hook=False)
    assert CLOCK_HEAD in cenv.last_card_text()
    failed(cenv)
    cenv.rig.frames.clear()
    cenv.rig.card_edits.clear()
    cenv.hook("PreToolUse", tool_name="Read", tool_input={"file_path": "/home/alice/a"},
              tool_use_id="toolu_next")
    shown = [t for _m, t, _k in cenv.rig.card_edits] + cenv.rig.frames
    assert shown and not any(CLOCK_HEAD in t for t in shown)


# -- the refused call's own row is not a tick ------------------------------------------------

def rm_rows(text):
    return [line for line in text.splitlines() if RM in line]


@pytest.mark.parametrize("event", ["Stop", "StopFailure"])
def test_the_refused_calls_row_is_no_tick_on_the_finished_card(cenv, event):
    """Its own row (from its PreToolUse) becomes the refused row: one ❌
    row for the call, no ✅ above it."""
    cenv.hook("PreToolUse", tool_name="Bash", tool_input={"command": RM, "description": RM},
              tool_use_id=TUID)
    cenv.rm()
    cenv.refused_transcript()
    cenv.scan(T0 + 121.0)
    cenv.hook(event)
    rows = rm_rows(cenv.rig.frames[-1])
    assert len(rows) == 1 and "❌" in rows[0] and "Refused by Claude Code" in rows[0]


def test_control_a_call_that_ran_keeps_its_tick(cenv):
    cenv.hook("PreToolUse", tool_name="Bash", tool_input={"command": RM, "description": RM},
              tool_use_id=TUID)
    cenv.rm()
    ran(cenv)
    cenv.hook("Stop")
    rows = rm_rows(cenv.rig.frames[-1])
    assert rows and all("✅" in r for r in rows)


def test_no_clock_lines_event_for_a_plain_prompt(env):
    events = []
    real = env.rig.recv.notify_fn

    async def spy(sess, event, context=None):
        events.append(event)
        return await real(sess, event, context)
    env.rig.recv.notify_fn = spy
    env.rig.claude_bash("make test", tool_use_id=TUID, hook=False, mode="default")
    failed(env, command="make test")
    assert "tool_failed" in events and "clock_lines_drop" not in events


def test_an_earlier_call_that_ran_keeps_its_tick_beside_the_refused_one(cenv):
    """The same command ran before: its row stays ✅, the refused call's
    own pending row becomes the refused one."""
    cenv.hook("PreToolUse", tool_name="Bash", tool_input={"command": RM, "description": RM},
              tool_use_id="toolu_first")
    cenv.hook("PostToolUse", tool_name="Bash", tool_input={"command": RM, "description": RM},
              tool_use_id="toolu_first", tool_response={"stdout": ""})
    cenv.hook("PreToolUse", tool_name="Bash", tool_input={"command": RM, "description": RM},
              tool_use_id=TUID)
    cenv.rm()
    cenv.refused_transcript()
    cenv.scan(T0 + 121.0)
    assert [done for _row, done in cenv.rig.sess.tool_history] == [True, "failed"]


def test_a_background_shells_row_is_not_taken_for_the_refused_call(cenv):
    """A running background shell holds its row: never the refused call's."""
    cenv.hook("PreToolUse", tool_name="Bash", tool_input={"command": RM, "description": RM},
              tool_use_id=TUID)
    sess = cenv.rig.sess
    sess.tool_history.insert(0, (sess.tool_history[0][0], False))
    sess.bg_shell_started("bshell1", "build", sess.tool_history[0][0], time.monotonic())
    sess.bg_shells["bshell1"]["history_idx"] = 0
    cenv.rm()
    cenv.refused_transcript()
    cenv.scan(T0 + 121.0)
    assert [done for _row, done in sess.tool_history] == [False, "failed"]


def test_a_record_never_sent_is_not_edited(env):
    """A prompt whose send was lost (no message id): its text loses the
    line, and no edit is sent for a message that does not exist."""
    env.rm(hook=False)
    rec = env.rig.sess.pending_prompt_msg
    rec.pop("msg_id")
    env.run_async(env.rig.bot._drop_clock_lines(env.rig.sess, [rec]))
    assert env.text_edits() == [] and CLOCK_HEAD not in rec["text"]


def test_the_saved_prompt_after_an_interrupt_has_no_clock_line(env):
    """The kept record shows its text without the line from then on (a
    restart restores it so)."""
    env.rm(hook=False)
    failed(env, interrupt=True)
    env.rig.bot.registry.save()
    saved = json.loads(state.SESSION_STATE_FILE.read_text())["sessions"][S]["open_prompt"]
    assert CLOCK_HEAD not in saved["text"] and "text_unclocked" not in saved


def test_two_clocked_prompts_one_call_ends_the_others_line_stays(env):
    """Claude's two parallel rm calls: one ran, the other is still open
    and keeps its line."""
    env.rm()
    env.rm("rm -rf dist/", tool_use_id="toolu_p2")
    first, second = env.rig.prompt_messages()
    assert CLOCK_HEAD in second["text"]
    ran(env)
    assert prompt_edits(env, second["msg_id"]) == []
    assert [e["message_id"] for e in env.text_edits()] == [first["msg_id"]]


def test_a_tap_on_the_bars_copy_takes_the_line_off_the_message_too(env):
    env.rm()
    [sent] = env.rig.prompt_messages()
    copy = resend(env)
    env.rig.tap(copy, "allow")
    [edit] = prompt_edits(env, sent["msg_id"])
    assert edit["text"] == sent["text"].rsplit("\n", 1)[0]
    # Answered from the copy: the message's own buttons go (roadmap 8.152).
    assert edit["reply_markup"] is None


def test_a_tapped_message_gets_no_buttons_back_when_the_turn_ends_in_the_terminal(env):
    """Live D3: a tap took a closed prompt's buttons off, then the turn
    ended at the terminal; the clock line's edit must not bring them
    back (roadmap 8.152)."""
    env.rm(hook=False)
    [sent] = env.rig.prompt_messages()
    failed(env, interrupt=True)
    q = env.rig.tap(sent["msg_id"], "allow")
    assert ALREADY in answers(q)
    marker(env)
    env.scan(T0 + 22.0)
    edits = prompt_edits(env, sent["msg_id"])
    assert edits and all(e["reply_markup"] is None for e in edits)


def test_a_tap_on_the_message_itself_edits_no_other_message(env):
    env.rm()
    [sent] = env.rig.prompt_messages()
    env.rig.tap(sent["msg_id"], "allow")
    assert env.text_edits() == []

