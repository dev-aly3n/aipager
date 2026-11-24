"""A kill+relaunch under the same entry closes the old claude's prompts.

/new's Replace and a restart (``_kill_and_relaunch_core``: /restart, a
mode switch, a bring-back) keep the session's entry for the claude they
start in place of the old one. Before this, the old claude's prompts
stayed open on that entry: a tap on its question typed Down, Enter into
the fresh claude (found 2026-11-24 by a reproduction run), and a Replace
of a working session left the entry BUSY under its old Working card.
Once the old socket is confirmed gone every prompt closes and its own
message loses its buttons; Replace also ends the old turn as a Stop
does. When the kill fails the old claude and its dialogs live on, so
nothing is closed.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from aipager.bot.dashboard import ALREADY_ANSWERED
from aipager.dtach import inject
from aipager.state import Status
from tests.two_prompts_rig import ALICE, BG, CARD, S, Rig, answers


@pytest.fixture
def rig(mk_bot, monkeypatch, run_async):
    r = Rig(mk_bot, monkeypatch, run_async)
    r.bot._app.bot.edit_message_reply_markup = AsyncMock()
    r.launches = []

    async def _launch(short, **kw):
        r.launches.append(short)
        return True, ""

    async def _no_sleep(_s):
        pass

    monkeypatch.setattr(inject, "launch_session", _launch)
    monkeypatch.setattr(inject, "kill_session", AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.bot.callbacks._sleep", _no_sleep)
    monkeypatch.setattr("aipager.bot.session_ops._sleep", _no_sleep)
    return r


def _socket_stays(monkeypatch, rig):
    """The kill fails: the old socket is still there (and the launch beside
    it refuses, as ``launch_session`` does for a live name)."""
    real = Path.is_socket
    monkeypatch.setattr(Path, "is_socket",
                        lambda self: "claude-dtach-" in str(self) or real(self))

    async def _refused(short, **kw):
        rig.launches.append(short)
        return False, "already exists"

    monkeypatch.setattr(inject, "launch_session", _refused)


def _replace(rig):
    rig.bot._new_conflict_pending[S] = {"prompt": "", "mode": "ask", "user_id": ALICE,
                                        "msg_id": CARD + 500, "was_working": True}
    rig.tap(CARD + 500, "new_replace", uid=ALICE)


def _restart(rig, run_async):
    return run_async(rig.bot._kill_and_relaunch_core(
        rig.sess, target_mode="ask", interrupt_first=False))


def _two_prompts(rig):
    """An agent's Bash prompt on the card and Claude's question as its own
    message."""
    rig.launch(BG)
    rig.agent_bash(BG, "make", request_id="req-bg")
    rig.question()
    assert rig.sess.status == Status.INTERACTIVE
    (extra,) = rig.sess.extra_prompts
    return extra["msg_id"]


def _stripped(rig) -> list[int]:
    return [c.kwargs.get("message_id")
            for c in rig.bot._app.bot.edit_message_reply_markup.await_args_list
            if c.kwargs.get("reply_markup") is None]


# ── /new Replace ────────────────────────────────────────────────────────────────

def test_replace_closes_the_old_question_so_a_tap_types_nothing(rig):
    """The reproduction: Claude's question on the card, Replace, then a tap
    on its option. Mutation: the Replace step not run (``old_gone`` never
    set) and the tap types Down, Enter into the fresh claude."""
    rig.question()
    _replace(rig)
    assert rig.launches == [S.removeprefix("claude-")]
    rig.keys.clear()
    q = rig.tap(CARD, "opt1", uid=ALICE)
    assert rig.keys == []
    assert answers(q) == [str(ALREADY_ANSWERED)]
    assert rig.sess.pending_permission is None
    assert rig.sess.status == Status.IDLE


def test_replace_closes_every_prompt_and_strips_their_messages(rig):
    """Both prompts close: the card's agent prompt answers no hook and the
    question's own message loses its buttons. Mutation: the buttons not
    stripped (``_show_prompts_closed`` skipped)."""
    extra_id = _two_prompts(rig)
    _replace(rig)
    assert extra_id in _stripped(rig)
    rig.keys.clear()
    rig.tap(CARD, "allow", uid=ALICE)
    rig.tap(extra_id, "opt0", uid=ALICE)
    assert rig.keys == []
    assert rig.decisions == []
    assert rig.sess.open_prompts() == []
    assert rig.sess.extra_prompts == []


def test_replace_of_a_working_session_ends_its_turn(rig):
    """A working session replaced: the entry is idle for the fresh claude,
    its animation stops and the card says Stopped with no buttons, the job
    and the reply target are gone. Mutations: the IDLE transition removed
    (BUSY stays), or the card not settled."""
    rig.hook("UserPromptSubmit", prompt="x")
    assert rig.sess.status == Status.BUSY
    rig.card_edits.clear()
    _replace(rig)
    sess = rig.sess
    assert sess.status == Status.IDLE
    rig.bot._stop_animation.assert_called()
    ((mid, text, markup),) = [e for e in rig.card_edits if e[0] == CARD]
    assert "Stopped" in text and markup is None
    assert sess.busy_msg_id is None
    assert sess.trigger_msg_id is None
    assert sess.active_subagents == {}


def test_replace_whose_kill_fails_closes_nothing(rig, monkeypatch):
    """The old claude outlived the kill: its question is still on screen,
    so it stays open (the launch refuses beside it). Mutation: the step run
    whatever the socket wait found."""
    rig.question()
    _socket_stays(monkeypatch, rig)
    rig.card_edits.clear()
    _replace(rig)
    assert rig.sess.status == Status.INTERACTIVE
    assert rig.sess.pending_permission is not None
    assert [e for e in rig.card_edits if e[0] == CARD] == []


# ── restart (kill and relaunch in place) ────────────────────────────────────────

def test_a_restart_closes_the_old_prompts_before_the_relaunch(rig, run_async):
    """Mutation: the close step removed from ``_kill_and_relaunch_core``
    (a tap on the old question types into the relaunched claude)."""
    extra_id = _two_prompts(rig)
    out = _restart(rig, run_async)
    assert out.ok
    assert extra_id in _stripped(rig)
    rig.keys.clear()
    rig.tap(CARD, "allow", uid=ALICE)
    rig.tap(extra_id, "opt1", uid=ALICE)
    assert rig.keys == []
    assert rig.decisions == []
    assert rig.sess.pending_permission is None


def test_a_restart_whose_old_claude_survives_keeps_its_prompts(rig, run_async, monkeypatch):
    """still_stopping: the old claude and its dialog live on, so the
    prompt stays answerable."""
    rig.question()
    _socket_stays(monkeypatch, rig)
    out = _restart(rig, run_async)
    assert (out.ok, out.reason) == (False, "still_stopping")
    assert rig.sess.pending_permission is not None
    assert rig.sess.status == Status.INTERACTIVE


def test_replace_with_no_card_closes_the_prompt_sent_as_its_own_message(
        mk_bot, monkeypatch, run_async):
    """No card yet: Claude's question went out as its own message (the
    primary prompt, not the card's). Replace closes it too: no prompt is
    left on the entry and its message loses its buttons."""
    r = Rig(mk_bot, monkeypatch, run_async, card=False)
    r.bot._app.bot.edit_message_reply_markup = AsyncMock()

    async def _launch(short, **kw):
        return True, ""

    async def _no_sleep(_s):
        pass

    monkeypatch.setattr(inject, "launch_session", _launch)
    monkeypatch.setattr(inject, "kill_session", AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.bot.callbacks._sleep", _no_sleep)
    r.question()
    (msg,) = r.prompt_messages()
    _replace(r)
    assert r.sess.pending_permission is None
    assert r.sess.pending_prompt_msg is None
    assert msg["msg_id"] in _stripped(r)
    r.keys.clear()
    r.tap(msg["msg_id"], "opt0", uid=ALICE)
    assert r.keys == []
