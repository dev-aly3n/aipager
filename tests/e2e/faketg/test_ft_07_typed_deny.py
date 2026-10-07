"""Scenario 10 (roadmap 8.99): a Deny typed into Claude Code's dialog.

When the PermissionRequest hook has stopped waiting, a Deny tap falls
back to typing keys into Claude Code's dialog: Downs that clamp on the
last row, "No, and tell Claude what to do differently (esc)", and Enter.
Claude Code 2.1.291 then ends the turn like an interrupt: no PostToolUse
and no Stop. The daemon must still settle the card as Denied and return
the session to IDLE (it used to stay BUSY until its 900 s cap), whether
the prompt was on the busy card or a separate message.

Stand-in: the stand-in shortens the hook's wait for one session (a
``standin-hook-deadline-<name>`` file). Real Claude, ``[card]`` only: the
test builds a daemon of its own whose sessions run the hook with a
``AIPAGER_PERMISSION_REPLY_DEADLINE_SECONDS`` of
:data:`REAL_HOOK_DEADLINE`, so the real hook gives up within seconds,
waits (from the daemon log) for Claude Code's own dialog to be up, taps
Deny, and reads from the daemon log how the turn ended. Only the
transcript's interrupt marker passes: the 10 s grace or "the transcript
moved on" means 8.99's assumption about Claude Code is wrong for the
version under test. ``[separate]`` stays stand-in only: real Claude's own
timing decides whether it asks before the busy card exists.
"""

from __future__ import annotations

import time

import pytest

from tests.e2e.fake_telegram import instance as fti
from tests.e2e.fake_telegram.redaction import redact
from tests.e2e.faketg import flows

G = fti.GROUP_ID
BOT = f"@{fti.BOT_USERNAME}"

#: The real hook's reply deadline in this test's own daemon (seconds).
REAL_HOOK_DEADLINE = "2"
#: How long real Claude's session may take to be IDLE after the tap: far
#: below the 900 s tool-in-flight cap, above the daemon's 10 s grace.
REAL_IDLE_WITHIN = 60

_ENDED = "turn ended at the permission dialog"
_MARKER = f"{_ENDED} (refused, transcript marker)"
_GRACE = f"{_ENDED} (refused, grace)"
_NOT_ENDED = "typed answer: the turn did not end at the dialog"


@pytest.mark.parametrize("where", ["card", "separate"])
def test_typed_deny_ends_the_turn_and_frees_the_session(request, where):
    from tests.e2e.faketg import conftest as ftc
    if ftc.claude_mode() != "standin":
        if where == "separate":
            pytest.skip("stand-in only: real Claude's timing decides whether it asks "
                        "before the busy card exists, so it cannot be made to on demand")
        inst = request.getfixturevalue("own_faketg")(
            {"AIPAGER_PERMISSION_REPLY_DEADLINE_SECONDS": REAL_HOOK_DEADLINE})
        _real_card(inst)
        return
    _standin(request.getfixturevalue("fresh"), where)


def _standin(inst, where):
    fake = inst.fake
    label = "ft16" if where == "card" else "ft17"
    name = inst.new_session(G, fti.BOB, label)
    # The hook gives up after 1 s, so the tap below is typed into the dialog.
    (inst.inst_dir / f"standin-hook-deadline-{name}").write_text("1")
    flows.settle(fake)
    since, log_since = fake.mark(), inst.log_mark()
    if where == "card":
        card, _allow = flows.ask_write(inst, name, label, G, fti.BOB, label)
    else:
        inst.release_tools(name)  # no hold: the prompt beats the busy card
        fake.inject_text(G, fti.user(fti.BOB),
                         f"{BOT} Use the Write tool to create {label}.txt containing hi")
        card, _allow = fake.wait_button(G, "Allow", since=since, timeout=120)
        assert "Permission needed" in card.get("text", ""), card.get("text")
    fti.wait_until(lambda: any(e.get("event") == "dialog_open"
                               for e in inst.standin_log(name)),
                   30, "the hook to stop waiting and the dialog to open")

    since = fake.mark()
    _msg, deny = fake.wait_button(G, "Deny", since=0, message_text_contains=label)
    cb = fake.inject_callback(fti.user(fti.BOB), fake.message(G, card["message_id"]), deny)
    # Updates are handled one at a time: on the separate path the tap
    # waits behind the prompt's own handler, which is sending the busy
    # card through the group's flood pacing.
    fake.wait_answer(cb, timeout=90)
    answered = time.monotonic()
    # Typed: the stand-in's dialog took the last row, and its turn ended
    # like an interrupt (no Stop).
    fti.wait_until(lambda: any(e.get("event") == "interrupted" and e.get("by") == "dialog"
                               for e in inst.standin_log(name)),
                   30, "the typed Deny to reach the dialog's last row")
    assert [e.get("answer") for e in inst.standin_log(name)
            if e.get("event") == "dialog_key"] == ["rejected"]
    flows.wait_text(fake, G, "Denied by @bob", since=since, timeout=30)
    # The session is IDLE again within seconds, not after the 900 s cap.
    flows.wait_turn_end(inst, name, label, since_log=log_since, timeout=30)
    assert time.monotonic() - answered < 30
    assert not inst.file_in_project(f"{label}.txt").exists()
    assert not [e for e in inst.standin_log(name) if e.get("event") == "stop"]
    inst.wait_log(f"[{label}] turn ended at the permission dialog", since=log_since,
                  timeout=30)

    # And it takes the next message as a new turn.
    (inst.inst_dir / f"standin-hook-deadline-{name}").unlink(missing_ok=True)
    flows.control(inst, G, fti.BOB, name, f"ping{label}")


def turn_end_verdict(lines: list[str], label: str) -> tuple[str, str] | None:
    """How the daemon log says session *label*'s typed refusal ended:
    ``("marker" | "grace" | "not_ended", line)``, or None while it says
    nothing yet."""
    for line in lines:
        if f"[{label}]" not in line:
            continue
        if _MARKER in line:
            return "marker", line
        if _GRACE in line:
            return "grace", line
        if _NOT_ENDED in line:
            return "not_ended", line
    return None


def verdict_problem(verdict: str, line: str, transcript_tail: str) -> str | None:
    """None when the turn ended on the transcript's interrupt marker, else
    why the test fails (with the transcript's last entries)."""
    if verdict == "marker":
        return None
    if verdict == "grace":
        why = ("the daemon ended the turn on its 10 s grace, not on the transcript's "
               "interrupt marker: 8.99's assumption that Claude Code writes "
               "'[Request interrupted by user ...]' as the newest entry after a typed "
               "refusal is wrong for this Claude Code version")
    else:
        why = ("the daemon left the turn to its hooks after the typed Deny (the "
               "transcript moved on, or Claude had queued messages): 8.99's assumption "
               "that a typed refusal ends the turn like an interrupt is wrong for this "
               "Claude Code version")
    return redact(f"{why}\ndaemon log: {line.strip()}\n"
                  f"the transcript's last entries:\n{transcript_tail}")


def settled_denied_card(label: str, by: str) -> str:
    """The busy card's last word after a typed refusal ended the turn
    (``session_ops._refused_card_text``, as the fake shows it: plain)."""
    return f"🚫 {label} · Denied by {by}"


#: How long, after the prompt reached the chat, the dialog may take to
#: be signalled (the hook gives up after REAL_HOOK_DEADLINE seconds;
#: Claude Code's permission_prompt Notification follows its dialog by
#: about 6 s, the delay 2.1.292 schedules it with).
DIALOG_WITHIN = 30.0
#: With the hook's give-up seen but no Notification, how long to wait
#: before typing anyway (the dialog has been drawn by then).
AFTER_REPLY_TIMEOUT = 12.0

#: Daemon log lines (``hook_receiver``) that say Claude Code's dialog is up.
DIALOG_SIGNALS = {
    "notification": "hook permission_prompt (",  # Claude Code's own Notification
    "reply_timeout": "hook permission_reply_timeout (",  # the hook stopped waiting
}


def dialog_signals(lines: list[str], label: str) -> set[str]:
    """Which of :data:`DIALOG_SIGNALS` the daemon logged for *label*."""
    return {key for key, needle in DIALOG_SIGNALS.items()
            if any(f"[{label}] {needle}" in line for line in lines)}


def dialog_ready(seen: set[str], reply_timeout_at: float | None, now: float) -> bool:
    """The Deny may be typed: Claude Code's Notification says its dialog is
    up, or the hook gave up :data:`AFTER_REPLY_TIMEOUT` seconds ago."""
    if "notification" in seen:
        return True
    return reply_timeout_at is not None and now - reply_timeout_at >= AFTER_REPLY_TIMEOUT


def signals_line(seen: set[str]) -> str:
    """``seen: ...; not seen: ...`` over :data:`DIALOG_SIGNALS`."""
    names = {"notification": "Claude Code's permission_prompt Notification",
             "reply_timeout": "the hook's permission_reply_timeout"}
    yes = [names[k] for k in DIALOG_SIGNALS if k in seen] or ["none"]
    no = [names[k] for k in DIALOG_SIGNALS if k not in seen] or ["none"]
    return f"dialog signals seen: {', '.join(yes)}; not seen: {', '.join(no)}"


def wait_for_dialog(inst, name: str, label: str, since: int) -> set[str]:
    """Wait until Claude Code's own permission dialog is up (see
    :func:`dialog_ready`), bounded by :data:`DIALOG_WITHIN`; the signals
    seen. On a timeout, fail naming the signals seen and not seen, the
    daemon's last log lines and what a read of the pane returned."""
    deadline = time.monotonic() + DIALOG_WITHIN
    reply_timeout_at = None
    while True:
        seen = dialog_signals(inst.log_lines(since), label)
        now = time.monotonic()
        if "reply_timeout" in seen and reply_timeout_at is None:
            reply_timeout_at = now
        if dialog_ready(seen, reply_timeout_at, now):
            return seen
        if now >= deadline:
            pytest.fail(redact(
                f"Claude Code's permission dialog was not signalled within "
                f"{DIALOG_WITHIN:.0f}s of the prompt reaching the chat\n"
                f"{signals_line(seen)}\n"
                f"the daemon's last log lines:\n{inst.log_tail(30)}\n"
                f"{inst.screen_report(name)}"), pytrace=False)
        time.sleep(0.2)


def _real_card(inst):
    """Real Claude: the hook gives up after REAL_HOOK_DEADLINE seconds,
    Claude Code shows its own dialog, and the Deny is typed into it; the
    turn must end on the transcript's marker."""
    fake = inst.fake
    label = "ft16"
    name = inst.new_session(G, fti.BOB, label)
    values = inst.session_env_values(name, "AIPAGER_PERMISSION_REPLY_DEADLINE_SECONDS")
    assert values and set(values) == {REAL_HOOK_DEADLINE}, (
        f"the session's processes do not carry the short hook deadline: {values}")
    flows.settle(fake)
    log_since = inst.log_mark()
    card, _allow = flows.ask_write(inst, name, label, G, fti.BOB, label)
    # The Deny is typed blind, so the tap waits for the dialog to be up.
    seen = wait_for_dialog(inst, name, label, log_since)
    signals = signals_line(seen)

    since = fake.mark()
    _msg, deny = fake.wait_button(G, "Deny", since=0, message_text_contains=label)
    cb = fake.inject_callback(fti.user(fti.BOB), fake.message(G, card["message_id"]), deny)
    fake.wait_answer(cb, timeout=90)
    answered = time.monotonic()
    # Typed (the keystroke fallback), never the hook's decision.
    typed = inst.wait_log(f"[{label}]", "Denied (via=", since=log_since, timeout=30)
    assert "(via=keystroke_fallback)" in typed, redact(
        f"the Deny was not typed into the dialog: {typed.strip()}\n{signals}")

    verdict = fti.wait_until(
        lambda: turn_end_verdict(inst.log_lines(log_since), label), REAL_IDLE_WITHIN,
        "the daemon to log how the typed Deny ended the turn",
        lambda: ("no verdict line means the daemon's watch stood down because Claude "
                 "carried on (a Stop, a new tool call or a new turn after the typed "
                 "refusal), or a background job was open: either way 8.99's assumption "
                 "that a typed refusal ends the turn like an interrupt does not hold "
                 "for this Claude Code version\n" + signals + "\n"
                 + inst.log_tail(30) + "\nthe transcript's last entries:\n"
                 + inst.transcript_tail(name)))
    problem = verdict_problem(*verdict, inst.transcript_tail(name) + "\n" + signals)
    if problem is not None:
        pytest.fail(problem, pytrace=False)

    # The card is settled as Denied (not the tap's own "Working" edit, whose
    # tool history also reads "Denied"), and the session is IDLE, well
    # before the cap.
    settled = settled_denied_card(label, "@bob")
    cards = {card["message_id"], *flows.busy_card_ids(inst.log_lines(log_since), label)}
    fake.wait_for(lambda c: c.method == "editMessageText" and c.chat_id == G
                  and c.params.get("message_id") in cards and c.text.strip() == settled,
                  timeout=30, since=since,
                  what=f"the busy card to be settled as {settled!r}")
    flows.wait_turn_end(inst, name, label, since_log=log_since,
                        timeout=max(1.0, REAL_IDLE_WITHIN - (time.monotonic() - answered)))
    assert time.monotonic() - answered < REAL_IDLE_WITHIN
    assert not inst.file_in_project(f"{label}.txt").exists()

    # And it takes the next message as a new turn.
    flows.control(inst, G, fti.BOB, name, f"ping{label}")
