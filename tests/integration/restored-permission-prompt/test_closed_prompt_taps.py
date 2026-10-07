"""Answer taps on a prompt surface (roadmap 8.102, review iter 2).

One rule: an answer tap (allow, allow_always, deny, submit, opt<N>) is
refused ("already answered", no keys) only when the tapped message's
prompt is KNOWN to be closed. When it is not known, the tap types its keys
as it always did:

- the INTERACTIVE watchdog's demotion (no hook for 300 s) does not prove
  the dialog closed: in a flood-muted or minimal-mode chat the card keeps
  Allow / Deny and Claude's dialog is most likely still up;
- a PostToolUse closes the prompt only when it is the prompt's own call
  (id, tool and input), never a parallel call of the same tool;
- a prompt shown on a card again is open again;
- the record of closed surfaces survives a restart.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from aipager import session_monitor
from aipager.state import Status

LABEL = "aipager_boss"
CHAT = 256113222
TUID = "toolu_01Restored"


def _run(vloop, coro):
    return vloop.run_until_complete(coro)


def _ts(wall: float) -> str:
    return datetime.fromtimestamp(wall, timezone.utc).isoformat()


def _dead_hook(tmp_path) -> dict:
    return {"addr": str(tmp_path / "gone.sock"), "request_id": "req-dead"}


async def _inline_prompt(r, tmp_path, *, command: str = "rm -rf build",
                         tool_use_id: str = TUID) -> int:
    card = await r.open_card()
    await r.permission(hook=_dead_hook(tmp_path), command=command,
                       tool_use_id=tool_use_id)
    await r.shown(card, "allow")
    await asyncio.sleep(5)
    return card


async def _demote(r) -> None:
    """Past the INTERACTIVE watchdog's timeout with no hook, one monitor
    scan: the session is demoted to BUSY and its prompt dropped."""
    await asyncio.sleep(session_monitor.INTERACTIVE_TIMEOUT_SECONDS + 5)
    monitor = session_monitor.SessionMonitor(r.bot.registry, r.bot.notify)
    await monitor._scan()
    await asyncio.sleep(1)


@pytest.fixture
def suppressed(monkeypatch):
    """The chat is flood-muted or in minimal mode: the card is not
    animated, so the demotion leaves its Allow / Deny on it."""
    monkeypatch.setattr(session_monitor, "cards_suppressed",
                        lambda chat_id: True)


# ── unknown: the watchdog's demotion still answers ───────────────────────

@pytest.mark.parametrize("verb, keys", [
    ("allow", ["Enter"]),
    ("deny", ["Down"] * 5 + ["Enter"]),
])
def test_watchdog_demoted_prompt_in_a_suppressed_chat_still_answers(
        replay, vloop, tmp_path, suppressed, verb, keys):
    r = replay

    async def scenario():
        card = await _inline_prompt(r, tmp_path)
        data = r.cb(card, verb)
        await _demote(r)
        # The premise: demoted, no prompt held, the card still shows it.
        assert r.sess.status is Status.BUSY
        assert r.sess.pending_permission is None
        assert r.cb(card, verb) == data
        before = len(r.keys)
        toast = await r.tap(card, data)
        await asyncio.sleep(1)
        return toast, r.keys[before:]

    toast, typed = _run(vloop, scenario())
    assert typed == keys
    assert toast != "already answered"


def test_a_second_tap_after_a_watchdog_demoted_answer_types_nothing(
        replay, vloop, tmp_path, suppressed):
    """The demoted prompt answered by a tap on the card is closed: a
    double tap types nothing more."""
    r = replay

    async def scenario():
        card = await _inline_prompt(r, tmp_path)
        allow, deny = r.cb(card, "allow"), r.cb(card, "deny")
        await _demote(r)
        await r.tap(card, allow)
        await asyncio.sleep(1)
        before = len(r.keys)
        toast = await r.tap(card, deny)
        await asyncio.sleep(1)
        return toast, r.keys[:before], r.keys[before:]

    toast, first, second = _run(vloop, scenario())
    assert first == ["Enter"]  # premise: the first tap answered
    assert second == []
    assert toast == "already answered"


def test_watchdog_demoted_prompt_answers_in_an_animated_chat_too(
        replay, vloop, tmp_path):
    """Not suppressed: the card repaints to Stop, but a client still
    showing Allow answers the dialog as before."""
    r = replay

    async def scenario():
        card = await _inline_prompt(r, tmp_path)
        data = r.cb(card, "allow")
        await _demote(r)
        assert r.sess.pending_permission is None
        before = len(r.keys)
        await r.tap(card, data)
        await asyncio.sleep(1)
        return r.keys[before:]

    assert _run(vloop, scenario()) == ["Enter"]


def test_watchdog_demotion_of_a_second_prompt_on_a_closed_card_answers(
        replay, vloop, tmp_path, suppressed):
    """The card's first prompt was answered by a tap (closed); a second
    prompt on the same card opens it again, so after that one's demotion
    the card's Allow still types."""
    r = replay

    async def scenario():
        card = await _inline_prompt(r, tmp_path)
        await r.tap(card, r.cb(card, "allow"))
        await asyncio.sleep(1)
        assert r.sess.prompt_known_closed(card)  # premise
        r.hook(hook_event_name="PostToolUse", tool_name="Bash",
               tool_input={"command": "rm -rf build"}, tool_use_id=TUID)
        await asyncio.sleep(1)
        await r.permission(hook=_dead_hook(tmp_path), command="rm -rf dist",
                           tool_use_id="toolu_02")
        await r.shown(card, "allow")
        assert not r.sess.prompt_known_closed(card)
        data = r.cb(card, "allow")
        await _demote(r)
        before = len(r.keys)
        await r.tap(card, data)
        await asyncio.sleep(1)
        return r.keys[before:]

    assert _run(vloop, scenario()) == ["Enter"]


# ── known: the prompt's own tool finished ────────────────────────────────

def test_own_post_tool_use_closes_the_cards_prompt(replay, vloop, tmp_path):
    r = replay

    async def scenario():
        card = await _inline_prompt(r, tmp_path)
        data = r.cb(card, "allow")
        r.hook(hook_event_name="PostToolUse", tool_name="Bash",
               tool_input={"command": "rm -rf build"}, tool_use_id=TUID)
        await asyncio.sleep(1)
        before = len(r.keys)
        toast = await r.tap(card, data)
        await asyncio.sleep(1)
        return toast, r.keys[before:]

    toast, typed = _run(vloop, scenario())
    assert typed == []
    assert toast == "already answered"


def test_own_post_tool_use_failure_closes_the_cards_prompt(
        replay, vloop, tmp_path):
    r = replay

    async def scenario():
        card = await _inline_prompt(r, tmp_path)
        data = r.cb(card, "deny")
        r.hook(hook_event_name="PostToolUseFailure", tool_name="Bash",
               tool_input={"command": "rm -rf build"}, tool_use_id=TUID)
        await asyncio.sleep(1)
        before = len(r.keys)
        await r.tap(card, data)
        await asyncio.sleep(1)
        return r.keys[before:]

    assert _run(vloop, scenario()) == []


@pytest.mark.parametrize("post", [
    # A parallel call of the same tool that took the request's id (the
    # request carried none): another input.
    {"tool_name": "Bash", "tool_input": {"command": "ls"},
     "tool_use_id": TUID},
    # Another call's id.
    {"tool_name": "Bash", "tool_input": {"command": "rm -rf build"},
     "tool_use_id": "toolu_other"},
    # A subagent's call with the same id.
    {"tool_name": "Bash", "tool_input": {"command": "rm -rf build"},
     "tool_use_id": TUID, "agent_id": "agent-3"},
    # Another tool.
    {"tool_name": "Read", "tool_input": {"command": "rm -rf build"},
     "tool_use_id": TUID},
], ids=["other-input", "other-id", "subagent", "other-tool"])
def test_other_tool_ends_leave_the_cards_prompt_answering(
        replay, vloop, tmp_path, post):
    r = replay

    async def scenario():
        card = await _inline_prompt(r, tmp_path)
        data = r.cb(card, "allow")
        r.hook(hook_event_name="PostToolUse", **post)
        await asyncio.sleep(1)
        before = len(r.keys)
        await r.tap(card, data)
        await asyncio.sleep(1)
        return r.keys[before:]

    assert _run(vloop, scenario()) == ["Enter"]


# ── known: the turn ended ────────────────────────────────────────────────

def test_turn_end_closes_the_cards_prompt(replay, vloop, tmp_path):
    r = replay

    async def scenario():
        card = await _inline_prompt(r, tmp_path)
        data = r.cb(card, "deny")
        r.stop("done in the terminal")
        await asyncio.sleep(20)
        before = len(r.keys)
        toast = await r.tap(card, data)
        await asyncio.sleep(1)
        return toast, r.keys[before:]

    toast, typed = _run(vloop, scenario())
    assert typed == []
    assert toast == "already answered"


# ── the record survives a restart ────────────────────────────────────────

def test_a_tap_answered_card_stays_closed_after_a_restart(
        replay, vloop, tmp_path):
    """Answered by a tap, then the daemon restarts (nothing to restore):
    the old Allow still types nothing."""
    r = replay

    async def scenario():
        card = await _inline_prompt(r, tmp_path)
        data = r.cb(card, "allow")
        await r.tap(card, data)
        await asyncio.sleep(1)
        # The turn is still running (no Stop): its prompt line is on disk.
        r.append({"type": "user", "timestamp": _ts(r.loop.wall()),
                  "message": {"role": "user", "content": "clean the build"}})
        await r.restart()
        await asyncio.sleep(2)
        before = len(r.keys)
        toast = await r.tap(card, data)
        await asyncio.sleep(1)
        return toast, r.keys[before:]

    toast, typed = _run(vloop, scenario())
    assert typed == []
    assert toast == "already answered"


def test_an_unreadable_transcript_drop_is_not_known_closed(
        replay, vloop, tmp_path):
    """A saved prompt dropped because its transcript cannot be read proves
    nothing about the dialog: the old Allow types as it did before 8.102."""
    r = replay

    async def scenario():
        card = await _inline_prompt(r, tmp_path)
        data = r.cb(card, "allow")
        # A directory: opening it fails, so the transcript is unreadable.
        r.sess.transcript_path = str(tmp_path)
        await r.restart()
        await asyncio.sleep(2)
        # The premise: dropped (not restored), the card kept.
        assert r.sess.status is not Status.INTERACTIVE
        assert r.sess.pending_permission is None
        assert not r.sess.prompt_known_closed(card)
        before = len(r.keys)
        await r.tap(card, data)
        await asyncio.sleep(1)
        return r.keys[before:]

    assert _run(vloop, scenario()) == ["Enter"]


# ── every way a tap answers closes its surface ───────────────────────────

MULTI_SELECT = [{"question": "Which files?", "multiSelect": True,
                 "options": [{"label": "a.py"}, {"label": "b.py"}]}]


def test_a_submitted_multi_select_card_types_nothing_more(replay, vloop):
    r = replay

    async def scenario():
        card = await r.open_card()
        await r.ask(MULTI_SELECT)
        await r.shown(card, "submit")
        opt, submit = r.cb(card, "opt0"), r.cb(card, "submit")
        await r.tap(card, opt)
        await asyncio.sleep(1)
        await r.tap(card, submit)
        await asyncio.sleep(1)
        assert r.sess.pending_permission is None  # premise: answered
        before = len(r.keys)
        toasts = [await r.tap(card, submit), await r.tap(card, opt)]
        await asyncio.sleep(1)
        return toasts, r.keys[before:]

    toasts, typed = _run(vloop, scenario())
    assert typed == []
    assert toasts == ["already answered"] * 2


async def _separate_prompt(r, tmp_path) -> int:
    r.bot.registry.transition(r.sess.name, Status.BUSY)
    r.sess.trigger_msg_id = 1
    await r.permission(hook=_dead_hook(tmp_path))
    assert r.sess.status is Status.INTERACTIVE and not r.sess.busy_msg_id
    (msg_id,) = [m for m, rec in r.chat.messages.items()
                 if "Permission needed" in rec["text"]]
    return msg_id


def test_an_answered_separate_prompt_stays_closed_after_a_restart(
        replay, vloop, tmp_path):
    """Its registration (which refuses a second tap) is gone after a
    restart; the closed record is not."""
    r = replay

    async def scenario():
        msg = await _separate_prompt(r, tmp_path)
        allow, deny = r.cb(msg, "allow"), r.cb(msg, "deny")
        await r.tap(msg, allow)
        await asyncio.sleep(1)
        await r.restart()
        await asyncio.sleep(2)
        before = len(r.keys)
        toast = await r.tap(msg, deny)
        await asyncio.sleep(1)
        return toast, r.keys[before:]

    toast, typed = _run(vloop, scenario())
    assert typed == []
    assert toast == "already answered"


def test_a_restored_prompt_answers_even_if_its_card_was_recorded_closed(
        replay, vloop, tmp_path):
    """A restored prompt opens its card again, whatever the file's closed
    record said about the card."""
    r = replay

    async def scenario():
        r.append({"type": "user", "timestamp": _ts(r.loop.wall() - 60),
                  "message": {"role": "user", "content": "clean the build"}})
        card = await _inline_prompt(r, tmp_path)
        data = r.cb(card, "allow")

        def edit(file):
            sd = next(iter(file["sessions"].values()))
            assert "open_prompt" in sd  # premise: it is restored
            sd["closed_prompt_msgs"] = [card]

        await r.restart(edit=edit)
        assert r.sess.status is Status.INTERACTIVE
        before = len(r.keys)
        await r.tap(card, data)
        await asyncio.sleep(1)
        return r.keys[before:]

    assert _run(vloop, scenario()) == ["Enter"]
