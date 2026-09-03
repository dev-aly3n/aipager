"""design.md success criteria (requirement 3 / the "continuation" half):
- A `<task-notification>` UserPromptSubmit reuses the existing job card/
  attribution and never re-tags origin.
- A genuinely new real prompt (Telegram-typed, terminal-typed, or
  drained from the queue) arriving while a *previous* job's background
  agents are still open starts its own fresh turn exactly as it does
  today — never silently absorbed.

Error-guessing target from the task brief: the continuation prompt
arriving when `active_subagents` is ALREADY empty (the real SubagentStop
raced ahead of the synthetic UserPromptSubmit, as it did in the hiva
transcript at 09:14:54 vs. moments later).
"""

from __future__ import annotations

import time

from aipager.state import Status


TASK_NOTIFICATION_PROMPT = (
    "<task-notification>\n<task-id>ab2ae82400fc97e4c</task-id>\n"
    "Background agent finished."
)


def _seed_open_job(registry, mk_job_session, *, active_subagents):
    sess = mk_job_session(status=Status.IDLE, active_subagents=active_subagents,
                          last_prompt_origin="telegram")
    registry._sessions[sess.name] = sess
    return sess


# ---- continuation dispatches "job_continuation", not "user_prompt_submit" -

def test_continuation_prompt_dispatches_job_continuation_event(
        receiver, send_hook, mk_job_session):
    registry, recv, notify_fn = receiver
    _seed_open_job(registry, mk_job_session, active_subagents={
        "ab2ae82400fc97e4c": {"type": "Explore", "started_at": 0.0,
                              "history_idx": 0},
    })

    send_hook(recv, hook_event_name="UserPromptSubmit", session="hiva",
             prompt=TASK_NOTIFICATION_PROMPT, transcript_path="")

    events = [c.args[1] for c in notify_fn.await_args_list]
    assert "job_continuation" in events
    assert "user_prompt_submit" not in events


def test_continuation_prompt_does_not_change_last_prompt_origin(
        receiver, send_hook, mk_job_session):
    registry, recv, notify_fn = receiver
    sess = _seed_open_job(registry, mk_job_session, active_subagents={
        "ab2ae82400fc97e4c": {"type": "Explore", "started_at": 0.0,
                              "history_idx": 0},
    })
    assert sess.last_prompt_origin == "telegram"

    send_hook(recv, hook_event_name="UserPromptSubmit", session="hiva",
             prompt=TASK_NOTIFICATION_PROMPT, transcript_path="")

    assert sess.last_prompt_origin == "telegram"


def test_continuation_prompt_preserves_trigger_msg_id(
        receiver, send_hook, mk_job_session):
    registry, recv, notify_fn = receiver
    sess = _seed_open_job(registry, mk_job_session, active_subagents={
        "ab2ae82400fc97e4c": {"type": "Explore", "started_at": 0.0,
                              "history_idx": 0},
    })
    sess.trigger_msg_id = 3420

    send_hook(recv, hook_event_name="UserPromptSubmit", session="hiva",
             prompt=TASK_NOTIFICATION_PROMPT, transcript_path="")

    assert sess.trigger_msg_id == 3420


# ---- error-guessing: continuation arrives when active_subagents is EMPTY -

def test_continuation_prompt_still_dispatched_when_agents_already_closed(
        receiver, send_hook, mk_job_session):
    """The real SubagentStop can race ahead of the synthetic
    UserPromptSubmit (observed live in the hiva transcript). The job is
    kept open across that gap by the grace window a SubagentStop arms
    after an interim idle, so the continuation is still recognised and
    dispatched as job_continuation — active_subagents' emptiness alone
    never decides it."""
    registry, recv, notify_fn = receiver
    sess = _seed_open_job(registry, mk_job_session, active_subagents={})
    sess.job_interim_seen = True
    sess.job_grace_until = time.monotonic() + 60
    assert sess.job_background_open() is True  # grace armed

    send_hook(recv, hook_event_name="UserPromptSubmit", session="hiva",
             prompt=TASK_NOTIFICATION_PROMPT, transcript_path="")

    events = [c.args[1] for c in notify_fn.await_args_list]
    assert "job_continuation" in events
    assert "user_prompt_submit" not in events
    assert sess.last_prompt_origin == "telegram"


def test_continuation_prompt_with_no_open_job_starts_a_fresh_turn(
        receiver, send_hook, mk_job_session):
    """A daemon restart drops the job state (active_subagents, the waiting
    card's animator) and the monitor recovers the session as plain IDLE.
    The <task-notification> that follows has nothing to continue: it must
    start a fresh turn — user_prompt_submit, so a busy card goes out and
    the turn-start stamps are set — while origin is still never re-tagged.
    (Live: two card-less turns right after the 12:00 restart, whose
    answers then arrived as a bare header plus a body.)"""
    registry, recv, notify_fn = receiver
    sess = _seed_open_job(registry, mk_job_session, active_subagents={})
    assert sess.job_background_open() is False  # nothing to continue
    before = sess.turn_entered_wall

    send_hook(recv, hook_event_name="UserPromptSubmit", session="hiva",
             prompt=TASK_NOTIFICATION_PROMPT, transcript_path="")

    events = [c.args[1] for c in notify_fn.await_args_list]
    assert "user_prompt_submit" in events
    assert "job_continuation" not in events
    assert sess.status is Status.BUSY
    assert sess.turn_entered_wall > before
    assert sess.busy_started_wall > 0.0
    assert sess.job_continuation_active is False
    assert sess.last_prompt_origin == "telegram"


# ---- a REAL prompt mid-job must start a fresh turn, never be absorbed ----

def test_real_telegram_prompt_mid_job_dispatches_user_prompt_submit(
        receiver, send_hook, mk_job_session):
    registry, recv, notify_fn = receiver
    _seed_open_job(registry, mk_job_session, active_subagents={
        "ab2ae82400fc97e4c": {"type": "Explore", "started_at": 0.0,
                              "history_idx": 0},
    })

    send_hook(recv, hook_event_name="UserPromptSubmit", session="hiva",
             prompt="[via Telegram msg=999]\na brand new unrelated question",
             transcript_path="")

    events = [c.args[1] for c in notify_fn.await_args_list]
    assert "user_prompt_submit" in events
    assert "job_continuation" not in events


def test_real_terminal_prompt_mid_job_also_dispatches_user_prompt_submit(
        receiver, send_hook, mk_job_session):
    """Equivalence partition: the fresh-turn behaviour must not depend on
    the new prompt's own origin marker — a terminal-typed prompt arriving
    mid-job is just as much a genuinely new turn as a Telegram one."""
    registry, recv, notify_fn = receiver
    _seed_open_job(registry, mk_job_session, active_subagents={
        "ab2ae82400fc97e4c": {"type": "Explore", "started_at": 0.0,
                              "history_idx": 0},
    })

    send_hook(recv, hook_event_name="UserPromptSubmit", session="hiva",
             prompt="a brand new unrelated question typed at the terminal",
             transcript_path="")

    events = [c.args[1] for c in notify_fn.await_args_list]
    assert "user_prompt_submit" in events
    assert "job_continuation" not in events


def test_prompt_mentioning_the_tag_mid_string_is_not_treated_as_continuation(
        receiver, send_hook, mk_job_session):
    """Boundary-value guard on the prefix match itself: the check must be
    an actual leading-prefix test, not a substring/contains check, or a
    genuine user message that happens to quote or discuss the literal tag
    would be silently swallowed as a continuation (never delivered as a
    real turn, origin never re-tagged)."""
    registry, recv, notify_fn = receiver
    _seed_open_job(registry, mk_job_session, active_subagents={
        "ab2ae82400fc97e4c": {"type": "Explore", "started_at": 0.0,
                              "history_idx": 0},
    })

    send_hook(recv, hook_event_name="UserPromptSubmit", session="hiva",
             prompt=("[via Telegram msg=1000]\nplease explain what "
                     "<task-notification> tags mean in the logs"),
             transcript_path="")

    events = [c.args[1] for c in notify_fn.await_args_list]
    assert "user_prompt_submit" in events
    assert "job_continuation" not in events
