"""The restored permission prompt's edges (roadmap 8.102, review iter 1).

- A restore that fails for any reason drops the saved prompt and never
  stops the startup recovery: the session gets 8.101's handling.
- An odd transcript line is not a crash at startup.
- A live session saved with its Claude session id loads GONE; restored, it
  is not a GONE entry any more.
- A prompt asked inside a subagent is never saved, so never restored.
- The busy card's answer buttons answer nothing once the card holds no
  prompt: a second tap after the answer, or the old buttons of a saved
  prompt that was not restored.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone

import pytest

from aipager.state import Status

# The harness's (conftest.py; a hyphenated directory is no package).
LABEL = "aipager_boss"
CHAT = 256113222
TUID = "toolu_01Restored"
RESTORED = f"[{LABEL}] permission prompt restored after restart - waiting "


def _run(vloop, coro):
    return vloop.run_until_complete(coro)


def _ts(wall: float) -> str:
    return datetime.fromtimestamp(wall, timezone.utc).isoformat()


def _dead_hook(tmp_path) -> dict:
    return {"addr": str(tmp_path / "gone.sock"), "request_id": "req-dead"}


def _running_turn(r, at: float) -> None:
    """The transcript of a turn still running: its prompt, nothing after
    (8.101 adopts such a card)."""
    r.append({"type": "user", "timestamp": _ts(at),
              "message": {"role": "user", "content": "clean the build"}})


async def _inline_prompt(r, tmp_path) -> int:
    card = await r.open_card()
    await r.permission(hook=_dead_hook(tmp_path))
    await r.shown(card, "allow")
    await asyncio.sleep(5)
    return card


async def _separate_prompt(r, tmp_path) -> int:
    r.bot.registry.transition(r.sess.name, Status.BUSY)
    r.sess.trigger_msg_id = 1
    await r.permission(hook=_dead_hook(tmp_path))
    (msg_id,) = [m for m, rec in r.chat.messages.items()
                 if "Permission needed" in rec["text"]]
    return msg_id


async def _recover_or_exception(r) -> str:
    """The startup recovery's outcome: "ok", or what it raised (so a raise
    fails an assertion instead of erroring the test)."""
    try:
        await r.bot.recover_sessions()
    except Exception as exc:  # noqa: BLE001 - the guard under test
        return f"raised {type(exc).__name__}: {exc}"
    return "ok"


def _adopted(caplog, card: int) -> bool:
    return any(m.startswith(
        f"[{LABEL}] busy card {card} adopted — the turn looks still running; "
        "working again, ") for m in caplog.messages)


# ── a restore that fails is a dropped prompt, never a failed start ───────

def _evidence_raises(r, monkeypatch):
    def _boom(*args, **kwargs):
        raise TypeError("unhashable type: 'list'")
    monkeypatch.setattr("aipager.bot.lifecycle.saved_prompt_evidence", _boom)


def _rebuild_raises(r, monkeypatch):
    """Half way: the session already re-entered its turn when it fails."""
    def _boom(*args, **kwargs):
        raise KeyError("perm")
    monkeypatch.setattr(r.bot, "_rebuild_open_prompt", _boom)


def _entering_the_wait_raises(r, monkeypatch):
    """Later still: the prompt is rebuilt when it fails."""
    registry = r.bot.registry
    real = registry.transition

    def _transition(name, status, *args, **kwargs):
        if status is Status.INTERACTIVE:
            raise RuntimeError("no wait")
        return real(name, status, *args, **kwargs)

    monkeypatch.setattr(registry, "transition", _transition)


@pytest.mark.parametrize("fail", [_evidence_raises, _rebuild_raises,
                                  _entering_the_wait_raises],
                         ids=["before-any-change", "half-way",
                              "after-the-rebuild"])
def test_a_failing_restore_drops_the_prompt_and_adopts_the_card(
        replay, vloop, tmp_path, caplog, monkeypatch, fail):
    r = replay

    async def scenario():
        _running_turn(r, vloop.wall() - 60)
        card = await _inline_prompt(r, tmp_path)
        await r.restart(recover=False)
        fail(r, monkeypatch)
        outcome = await _recover_or_exception(r)
        await asyncio.sleep(2)
        return card, outcome

    with caplog.at_level(logging.INFO):
        card, outcome = _run(vloop, scenario())
    assert outcome == "ok"
    assert any(m.startswith(f"[{LABEL}] saved permission prompt dropped - ")
               for m in caplog.messages)
    assert not any(m.startswith(RESTORED) for m in caplog.messages)
    assert r.sess.status is Status.BUSY
    assert r.sess.pending_permission is None
    assert _adopted(caplog, card)
    assert any(m.startswith("recovered 1 sessions: 1 adopted")
               for m in caplog.messages)


def test_a_failing_restore_of_one_session_does_not_stop_the_recovery(
        replay, vloop, tmp_path, caplog, monkeypatch):
    """The rest of the start goes on: the summary line is still logged."""
    r = replay

    async def scenario():
        _running_turn(r, vloop.wall() - 60)
        await _inline_prompt(r, tmp_path)
        await r.restart(recover=False)
        _evidence_raises(r, monkeypatch)
        return await _recover_or_exception(r)

    with caplog.at_level(logging.INFO):
        outcome = _run(vloop, scenario())
    assert outcome == "ok"
    assert any(m.startswith("recovered ") for m in caplog.messages)


def test_a_line_with_a_list_subtype_does_not_stop_the_restore(
        replay, vloop, tmp_path, caplog):
    """The review's reproduction: a system line whose ``subtype`` is a
    list. It says nothing about the prompt, so the prompt comes back."""
    r = replay

    async def scenario():
        card = await _inline_prompt(r, tmp_path)
        r.append({"type": "system", "subtype": ["x"],
                  "timestamp": _ts(vloop.wall())})
        await r.restart(recover=False)
        outcome = await _recover_or_exception(r)
        return card, outcome

    with caplog.at_level(logging.INFO):
        card, outcome = _run(vloop, scenario())
    assert outcome == "ok"
    assert r.sess.status is Status.INTERACTIVE
    assert f"{RESTORED}for an answer (inline card {card})" in caplog.messages


def test_a_blocked_bot_does_not_take_the_restored_card(
        replay, vloop, tmp_path, caplog, monkeypatch):
    """A card earlier in the recovery found the bot blocked: the cards
    after it are let go unedited, but the restored prompt's card is its
    prompt's, and stays."""
    r = replay
    other = "claude-other_harness"

    def _other_first(data):
        sessions = data["sessions"]
        data["sessions"] = {other: sessions.pop(other), **sessions}

    async def scenario():
        card = await _inline_prompt(r, tmp_path)
        o = r.bot.registry.get_or_create(other)
        o.label, o.scope_chat_id, o.scope_kind = "other", CHAT, "dm"
        o.busy_msg_id = 4242
        real = r.bot._recover_busy_message

        async def _blocked(bot, name, sess, live_names):
            if name == other:
                return "blocked"
            return await real(bot, name, sess, live_names)

        monkeypatch.setattr(r.bot, "_recover_busy_message", _blocked)
        await r.restart(edit=_other_first)
        kept = r.sess.busy_msg_id
        toast = await r.tap(card, r.cb(card, "allow"))
        await asyncio.sleep(1)
        return card, kept, toast

    with caplog.at_level(logging.INFO):
        card, kept, toast = _run(vloop, scenario())
    assert any(m.startswith("recovered 2 sessions: 1 blocked, 1 restored")
               for m in caplog.messages), [m for m in caplog.messages
                                           if m.startswith("recovered")]
    assert kept == card
    assert toast == f"Allowed [{LABEL}]"
    assert r.keys == ["Enter"]


# ── loaded GONE (saved with its Claude session id): restored, not GONE ──

def _with_claude_session(r) -> None:
    r.sess.claude_session_id = "0b6c5f3e-claude-session"


def _transcript_now(r, vloop) -> None:
    """The load backfills ``gone_at`` from the transcript's mtime: dated on
    the virtual clock, or the load's GONE pruning would drop the entry."""
    os.utime(r.transcript, (vloop.wall(), vloop.wall()))


def test_inline_prompt_of_a_session_loaded_gone_is_restored(
        replay, vloop, tmp_path, caplog):
    r = replay

    async def scenario():
        _with_claude_session(r)
        card = await _inline_prompt(r, tmp_path)
        _transcript_now(r, vloop)
        await r.restart(recover=False)
        loaded = (r.sess.status, r.sess.gone_at)
        await r.bot.recover_sessions()
        after = (r.sess.status, r.sess.gone_at)
        toast = await r.tap(card, r.cb(card, "allow"))
        await asyncio.sleep(1)
        return card, loaded, after, toast

    with caplog.at_level(logging.INFO):
        card, loaded, after, toast = _run(vloop, scenario())
    assert loaded[0] is Status.GONE and loaded[1] is not None  # the premise
    assert after == (Status.INTERACTIVE, None)
    assert f"{RESTORED}for an answer (inline card {card})" in caplog.messages
    assert toast == f"Allowed [{LABEL}]"
    assert r.keys == ["Enter"]
    assert r.sess.status is Status.BUSY
    assert r.sess.gone_at is None


def test_separate_prompt_of_a_session_loaded_gone_is_restored(
        replay, vloop, tmp_path, caplog):
    r = replay

    async def scenario():
        _with_claude_session(r)
        msg = await _separate_prompt(r, tmp_path)
        _transcript_now(r, vloop)
        await r.restart(recover=False)
        loaded = (r.sess.status, r.sess.gone_at)
        await r.bot.recover_sessions()
        after = (r.sess.status, r.sess.gone_at)
        toast = await r.tap(msg, r.cb(msg, "allow"))
        await asyncio.sleep(1)
        return msg, loaded, after, toast

    with caplog.at_level(logging.INFO):
        msg, loaded, after, toast = _run(vloop, scenario())
    assert loaded[0] is Status.GONE and loaded[1] is not None  # the premise
    assert after == (Status.INTERACTIVE, None)
    assert f"{RESTORED}for an answer (separate msg {msg})" in caplog.messages
    assert toast == f"Allowed [{LABEL}]"
    assert r.keys == ["Enter"]
    assert r.sess.gone_at is None


# ── a subagent's prompt is never saved ───────────────────────────────────

def test_a_subagents_prompt_is_not_restored(replay, vloop, tmp_path, caplog):
    """Its answer goes to the subagent's own transcript: answered in the
    terminal while the daemon was down, the parent's transcript would show
    nothing, and a restore would be wrong. Handled as before 8.102."""
    r = replay

    async def scenario():
        _running_turn(r, vloop.wall() - 60)
        card = await r.open_card()
        tool_input = {"command": "rm -rf build"}
        r.hook(hook_event_name="SubagentStart", agent_id="a1agent",
               agent_type="general-purpose")
        r.hook(hook_event_name="PreToolUse", tool_name="Bash",
               tool_input=tool_input, agent_id="a1agent",
               tool_use_id="toolu_agent")
        await asyncio.sleep(0.2)
        r.hook(hook_event_name="PermissionRequest", tool_name="Bash",
               tool_input=tool_input, agent_id="a1agent",
               aipager_reply_addr=_dead_hook(tmp_path)["addr"],
               aipager_request_id="req-dead")
        await asyncio.sleep(2)
        await r.shown(card, "allow")
        shown = (r.sess.status, bool(r.sess.pending_permission))
        await r.restart()
        await asyncio.sleep(2)
        return card, shown

    with caplog.at_level(logging.INFO):
        card, shown = _run(vloop, scenario())
    assert shown == (Status.INTERACTIVE, True)  # the premise: it was shown
    assert not any(m.startswith(RESTORED) for m in caplog.messages)
    assert r.sess.status is Status.BUSY
    assert _adopted(caplog, card)


# ── the busy card's answer buttons with no prompt on it ──────────────────

def test_a_second_tap_on_the_card_is_already_answered(replay, vloop, tmp_path):
    r = replay

    async def scenario():
        card = await _inline_prompt(r, tmp_path)
        await r.restart()
        deny = r.cb(card, "deny")  # what a client that did not repaint shows
        await r.tap(card, r.cb(card, "allow"))
        await asyncio.sleep(1)
        before = list(r.keys)
        toast = await r.tap(card, deny)
        await asyncio.sleep(1)
        return before, toast

    before, toast = _run(vloop, scenario())
    assert before == ["Enter"]
    assert toast == "already answered"
    assert r.keys == before
    assert r.sess.status is Status.BUSY
    # The working card keeps its Stop button: the refusal edits nothing.
    r.last_query.edit_message_reply_markup.assert_not_awaited()


def test_a_second_tap_without_a_restart_is_already_answered(replay, vloop,
                                                            tmp_path):
    """Not only after a restart: a double tap on a live card too."""
    r = replay

    async def scenario():
        card = await _inline_prompt(r, tmp_path)
        allow = r.cb(card, "allow")
        await r.tap(card, allow)
        await asyncio.sleep(1)
        before = list(r.keys)
        toast = await r.tap(card, allow)
        await asyncio.sleep(1)
        return before, toast

    before, toast = _run(vloop, scenario())
    assert before == ["Enter"]
    assert toast == "already answered"
    assert r.keys == before


def _invalid(data):
    data["sessions"]["claude-restoredprompt_harness"]["open_prompt"]["v"] = 2


def test_old_allow_of_an_ignored_prompt_types_nothing(replay, vloop, tmp_path,
                                                      caplog):
    """The saved prompt was ignored (invalid record) and the card adopted
    as working: its old Allow, from a client that still shows it, answers
    nothing."""
    r = replay

    async def scenario():
        _running_turn(r, vloop.wall() - 60)
        card = await _inline_prompt(r, tmp_path)
        allow = r.cb(card, "allow")
        await r.restart(edit=_invalid)
        await asyncio.sleep(1)
        toast = await r.tap(card, allow)
        await asyncio.sleep(1)
        return card, toast

    with caplog.at_level(logging.INFO):
        card, toast = _run(vloop, scenario())
    assert _adopted(caplog, card)  # the premise: 8.101's adoption
    assert toast == "already answered"
    assert r.keys == []
    assert r.sess.status is Status.BUSY


def test_the_adopted_card_shows_no_answer_buttons(replay, vloop, tmp_path,
                                                  caplog):
    """Why recovery need not strip them: the adopted card repaints as
    Working with its Stop button at once."""
    r = replay

    async def scenario():
        _running_turn(r, vloop.wall() - 60)
        card = await _inline_prompt(r, tmp_path)
        await r.restart(edit=_invalid)
        await asyncio.sleep(5)
        return card

    with caplog.at_level(logging.INFO):
        card = _run(vloop, scenario())
    assert _adopted(caplog, card)
    verbs = {d.rsplit(":", 1)[1] for d in r.buttons(card).values()}
    assert verbs and not verbs & {"allow", "deny", "allow_always"}
