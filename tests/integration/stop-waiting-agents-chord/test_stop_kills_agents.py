"""Stop on a session whose own turn has ended while its job's background
agents still run (roadmap 8.121 F5, design-6 section 3): every entry point
presses Claude Code's kill chord twice, one second apart, and never types
Escape; aipager then ends the agents in its own tables and says so. With
only the grace window or the job's shells left it types nothing at all.
A running turn, and Claude's own prompt, keep today's two Escapes. A
dialog of Claude's own refuses the Stop with nothing changed.

Entry points, each driven as its user drives it: the busy card's Stop
button, /status's Stop (``ststop``), the /stop picker (``pstop``), the
/stop command, ``/<label> stop``, and the Mini App's stop route over HTTP
with signed initData. Bytes are read on the dtach seam (``inject._run``).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import urlencode

import pytest
from aiohttp.test_utils import TestClient, TestServer

from aipager.bot import session_ops
from aipager.dtach import hook_receiver as hr
from aipager.dtach import inject
from aipager.miniapp.server import MiniAppServer
from aipager.state import STOP_KILL_AGENTS, Status
from tests.two_prompts_rig import ALICE, BG, CARD, GROUP, OTHER, S, answers

# The same values as this directory's conftest (which cannot be imported).
CHORD = b"\x18\x0b"
ESC = b"\x1b"
BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
LABEL = "api"


# ---- getting there ------------------------------------------------------------------

def waiting(rig, *agents):
    """A Telegram turn launches background agents and Claude's turn ends:
    IDLE, waiting for them (the job's interim Stop)."""
    for agent in agents or (BG,):
        rig.launch(agent)
    rig.hook("Stop", stop_hook_active=False, last_assistant_message="Started them.")
    assert rig.sess.status is Status.IDLE
    assert rig.sess.shown_state().state == "tasks"
    return rig.sess


# ---- the entry points -------------------------------------------------------------------

def _sign(fields: dict) -> str:
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    return hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()


def _init_header(user_id: int = ALICE) -> dict:
    fields = {"auth_date": str(int(time.time())),
              "user": json.dumps({"id": user_id, "first_name": "Alice"})}
    fields["hash"] = _sign(fields)
    return {"X-Telegram-Init-Data": urlencode(fields)}


def _command_update(text: str):
    update = MagicMock()
    update.message = MagicMock()
    update.message.text = text
    update.message.message_id = 990
    update.message.reply_to_message = None
    update.message.quote = None
    update.message.sender_chat = None
    update.message.reply_text = AsyncMock()
    update.message.chat = MagicMock(id=GROUP, type="supergroup")
    update.effective_message = update.message
    update.effective_chat = MagicMock(id=GROUP, type="supergroup")
    update.effective_user = MagicMock(id=ALICE, username="alice", first_name="Alice")
    update.callback_query = None
    return update


def _replies(update) -> list[str]:
    calls = update.message.reply_text.await_args_list
    return [c.args[0] if c.args else c.kwargs.get("text") for c in calls]


def _card(rig) -> list[str]:
    return answers(rig.tap(CARD, "stop", uid=ALICE))


def _status(rig) -> list[str]:
    return answers(rig.tap(777, f"ststop{rig.sess.turn_key}", uid=ALICE))


def _picker(rig) -> list[str]:
    return answers(rig.tap(778, f"pstop{rig.sess.turn_key}", uid=ALICE))


def _command(rig) -> list[str]:
    update = _command_update(f"/stop {LABEL}")
    rig.run_async(rig.bot._handle_stop_cmd(update, MagicMock()))
    return _replies(update)


def _by_label(rig) -> list[str]:
    update = _command_update(f"/{LABEL} stop")
    rig.run_async(rig.bot._stop_by_label(update, LABEL))
    return _replies(update)


def _miniapp(rig) -> list[str]:
    server = MiniAppServer(rig.bot, rig.bot.registry, port=8766)

    async def go():
        client = TestClient(TestServer(server._build_app()))
        await client.start_server()
        try:
            resp = await client.post(f"/api/sessions/{LABEL}/stop", headers=_init_header())
            body = await resp.json()
        finally:
            await client.close()
        return resp.status, body

    status, body = rig.run_async(go())
    rig.miniapp = (status, body)
    return [body.get("detail") or ""]


ENTRY = {"card": _card, "status": _status, "picker": _picker, "command": _command,
         "label": _by_label, "miniapp": _miniapp}


def _agent_prompt_message(rig) -> dict:
    """Both agents ask; the second one's prompt is a message of its own
    (the first sits on the card). Returns that message."""
    rig.agent_bash(BG, "rm -rf build")
    rig.agent_bash(OTHER, "rm -rf dist")
    assert rig.sess.status is Status.INTERACTIVE and rig.sess.wait_from_idle
    msgs = rig.prompt_messages()
    assert msgs, rig.sent
    return msgs[-1]


def _on_prompt(rig) -> list[str]:
    """The ⏹ Stop on an agent's own prompt message (rev-iter6-004)."""
    msg = _agent_prompt_message(rig)
    rig.prompt_query = rig.tap(msg["msg_id"], "stop", uid=ALICE)
    rig.prompt_msg = msg
    return answers(rig.prompt_query)


#: Every entry point, the Stop on an agent's prompt message included (that
#: one needs two agents asking first).
CHORD_ENTRY = {**ENTRY, "prompt": _on_prompt}


# ---- the chord ----------------------------------------------------------------------

@pytest.mark.parametrize("entry", list(CHORD_ENTRY))
def test_idle_with_job_agents_gets_the_chord_twice_and_never_escape(rig, entry):
    sess = waiting(rig, BG, OTHER)
    shown = CHORD_ENTRY[entry](rig)
    assert rig.writes.bytes() == [CHORD, CHORD]
    assert rig.writes.gaps() == [inject.KILL_AGENTS_PRESS_GAP]
    assert all(ESC not in w for w in rig.writes.bytes())
    # aipager's tables: no agent left, every surface idle at once.
    assert sess.status is Status.IDLE
    assert sess.active_subagents == {} and sess.bg_agents == {}
    assert sess.shown_state().state == "idle"
    assert sess.job_background_open() is False
    assert "Stopped 2 background agents of" in "\n".join(shown) or \
        "stopped 2 background agents of" in "\n".join(shown)


def test_the_card_says_how_many_agents_were_stopped(rig):
    waiting(rig, BG, OTHER)
    _command(rig)
    assert rig.card_edits[-1][1] == "⚠️ <b>api</b> · @alice stopped 2 background agents"
    assert rig.card_edits[-1][2] is None


def test_the_reply_and_the_mini_app_say_it_too(rig):
    waiting(rig)
    assert _command(rig) == ["⏹ @alice stopped 1 background agent of <b>api</b>"]


def test_the_mini_app_payload_counts_them(rig):
    waiting(rig, BG, OTHER)
    _miniapp(rig)
    status, body = rig.miniapp
    assert status == 200
    assert body["status"] == "stopped" and body["agents"] == 2 and body["shells"] == 0
    assert body["detail"] == "⏹ @alice stopped 2 background agents of api"


def test_the_stopped_agents_are_marked_killed_and_their_answers_say_stopped(rig):
    sess = waiting(rig)
    sess.agents_lines.append({"ids": [BG], "ends": {}})
    _card(rig)
    assert sess.bg_agents_recent[BG]["ended_by"] == "killed"
    assert sess.agents_lines[0]["ends"][BG]["outcome"] == "stopped"


# ---- no keys: grace window or shells only --------------------------------------------------

def _shell(sess, task_id="b3vwpa6yy"):
    sess.bg_shells[task_id] = {"label": "sleep 150", "in_job": True,
                               "started_at": time.monotonic()}


@pytest.mark.parametrize("entry", list(ENTRY))
def test_grace_window_only_ends_the_job_with_no_keys(rig, entry):
    sess = waiting(rig)
    sess.active_subagents.clear()
    sess.bg_agents.clear()
    sess.job_grace_until = time.monotonic() + 30.0
    assert sess.can_be_stopped()
    ENTRY[entry](rig)
    assert rig.writes == []
    assert sess.job_background_open() is False
    assert sess.status is Status.IDLE


@pytest.mark.parametrize("entry", list(ENTRY))
def test_shells_only_end_the_job_with_no_keys_and_say_they_run_on(rig, entry):
    sess = waiting(rig)
    sess.active_subagents.clear()
    sess.bg_agents.clear()
    _shell(sess)
    shown = ENTRY[entry](rig)
    assert rig.writes == []
    assert sess.job_background_open() is False
    assert "1 background shell keeps running." in "\n".join(shown)
    assert rig.card_edits[-1][1].endswith("\n🖥 1 background shell keeps running.")
    # The shell itself is not stopped: still counted, outside the job.
    assert sess.bg_shells["b3vwpa6yy"]["in_job"] is False


def test_a_shell_beside_the_agents_gets_its_own_line(rig):
    sess = waiting(rig)
    _shell(sess)
    shown = _card(rig)
    assert rig.writes.bytes() == [CHORD, CHORD]
    assert shown == ["⏹ @alice stopped 1 background agent of api\n"
                     "1 background shell keeps running."]
    assert rig.card_edits[-1][1] == ("⚠️ <b>api</b> · @alice stopped 1 background agent\n"
                                     "🖥 1 background shell keeps running.")


# ---- the Escape paths stay ---------------------------------------------------------------

@pytest.mark.parametrize("entry", ["card", "command", "miniapp"])
def test_a_running_turn_with_agents_keeps_two_escapes(rig, entry):
    rig.launch(BG)
    assert rig.sess.status is Status.BUSY
    ENTRY[entry](rig)
    assert rig.writes.bytes() == [ESC, ESC]


def test_claudes_own_prompt_with_an_agent_keeps_two_escapes(rig):
    rig.launch(BG)
    rig.question()
    assert rig.sess.status is Status.INTERACTIVE
    _card(rig)
    assert rig.writes.bytes() == [ESC, ESC]
    assert CHORD not in rig.writes.bytes()


def test_an_agents_prompt_in_a_running_turn_keeps_two_escapes(rig):
    rig.launch(BG)
    rig.agent_bash(BG, "make")
    assert rig.sess.status is Status.INTERACTIVE and not rig.sess.wait_from_idle
    _card(rig)
    assert rig.writes.bytes() == [ESC, ESC]


# ---- an agent's prompt after Claude's turn ended ------------------------------------------

def test_an_agents_prompt_while_waiting_gets_the_chord(rig):
    sess = waiting(rig, BG, OTHER)
    rig.agent_bash(BG, "rm -rf build")
    assert sess.status is Status.INTERACTIVE and sess.wait_from_idle
    _status(rig)
    assert rig.writes.bytes() == [CHORD, CHORD]
    assert sess.open_prompts() == []
    assert sess.status is Status.IDLE and sess.active_subagents == {}


def test_a_person_typing_at_the_terminal_during_that_wait_turns_stop_back_to_escape(rig):
    sess = waiting(rig)
    rig.agent_bash(BG, "rm -rf build")
    rig.hook("UserPromptSubmit", prompt="also check the README")
    assert sess.wait_from_idle is False
    _card(rig)
    assert rig.writes.bytes() == [ESC, ESC]


# ---- refusals ------------------------------------------------------------------------

@pytest.mark.parametrize("entry", list(ENTRY))
def test_a_terminal_dialog_refuses_with_nothing_changed(rig, entry):
    sess = waiting(rig)
    sess.term_dialog = {"kind": "resume", "epoch": 1}
    shown = ENTRY[entry](rig)
    assert rig.writes == []
    assert list(sess.active_subagents) == [BG] and list(sess.bg_agents) == [BG]
    assert sess.shown_state().state == "tasks"
    text = "api has a dialog open in the terminal. Answer it there, then try Stop again."
    assert any(text in s for s in shown), shown
    if entry == "miniapp":
        assert rig.miniapp[0] == 409 and rig.miniapp[1]["error"] == "dialog_open"


def test_a_dialog_datagram_not_handled_yet_refuses(rig, monkeypatch):
    sess = waiting(rig)
    monkeypatch.setitem(hr._DIALOG_DATAGRAMS, S, 1)
    shown = _card(rig)
    assert rig.writes == []
    assert list(sess.active_subagents) == [BG]
    assert any("has a dialog open in the terminal" in s for s in shown)


def test_the_command_says_the_refusal_once_and_never_not_working(rig):
    sess = waiting(rig)
    sess.term_dialog = {"kind": "resume", "epoch": 1}
    replies = _command(rig)
    assert len(replies) == 1
    assert "is not working" not in replies[0]


def test_a_failed_press_refuses_and_leaves_the_agents(rig, monkeypatch):
    sess = waiting(rig)

    async def _fail(args, stdin=b"", timeout=5):
        rig.writes.append(("write", stdin))
        return False, ""

    monkeypatch.setattr(inject, "_run", _fail)
    shown = _command(rig)
    assert rig.writes.bytes() == [CHORD]
    assert list(sess.active_subagents) == [BG]
    assert shown == ["The background agents of api could not be stopped. Try Stop again."]
    assert sess.kill_expected_until == 0.0 and sess.stop_gate is None


def test_a_turn_that_started_during_the_press_is_interrupted_instead(rig, monkeypatch):
    sess = waiting(rig)

    async def _turn_started(name, may_press, before_second_press=None):
        sess.status = Status.BUSY
        return inject.DIALOG_BEFORE_TYPING

    monkeypatch.setattr(inject, "press_kill_agents", _turn_started)
    outcome = rig.run_async(rig.bot._stop_session_core(sess))
    assert outcome.ok and outcome.mode == "keys"
    assert rig.writes.bytes()[:2] == [ESC, ESC]


# ---- what was held is wiped before the next prompt, never with Escape --------------------------

def test_a_held_back_message_is_wiped_before_the_next_prompt(rig, monkeypatch, tmp_path):
    sess = waiting(rig)
    note = tmp_path / "note.json"
    note.write_text("{}")
    monkeypatch.setitem(session_ops._HELD_BACK, S, note)
    monkeypatch.setattr("aipager.policy_snapshot.list_outstanding_notes",
                        lambda _name: [{"_path": Path(note), "body": "hello there"}])
    inject._PENDING_WIPE.pop(S, None)
    outcome = rig.run_async(rig.bot._stop_session_core(sess))
    assert outcome.ok and outcome.mode == STOP_KILL_AGENTS
    assert ESC not in b"".join(rig.writes.bytes())
    assert inject._PENDING_WIPE.get(S, 0) >= len("hello there")
    inject._PENDING_WIPE.pop(S, None)
    inject._WIPE_DRAFT_ONLY.discard(S)


def test_the_keyless_end_wipes_a_held_back_message_too(rig, monkeypatch, tmp_path):
    sess = waiting(rig)
    sess.active_subagents.clear()
    sess.bg_agents.clear()
    _shell(sess)
    note = tmp_path / "note.json"
    note.write_text("{}")
    monkeypatch.setitem(session_ops._HELD_BACK, S, note)
    monkeypatch.setattr("aipager.policy_snapshot.list_outstanding_notes",
                        lambda _name: [{"_path": Path(note), "body": "hello there"}])
    inject._PENDING_WIPE.pop(S, None)
    rig.run_async(rig.bot._stop_session_core(sess))
    assert rig.writes == []
    assert inject._PENDING_WIPE.get(S, 0) >= len("hello there")
    inject._PENDING_WIPE.pop(S, None)
    inject._WIPE_DRAFT_ONLY.discard(S)


# ---- the guard every Stop path types through ------------------------------------------------

def test_the_guard_types_no_escape_into_an_agents_only_wait(rig):
    sess = waiting(rig)
    rig.agent_bash(BG, "rm -rf build")
    assert sess.own_turn_over() and sess.status is Status.INTERACTIVE
    rig.run_async(rig.bot._send_stop_keys(sess, [], 0))
    assert rig.writes == []


def test_the_guard_still_types_for_a_running_turn(rig):
    rig.launch(BG)
    rig.run_async(rig.bot._send_stop_keys(rig.sess, [], 0))
    assert rig.writes.bytes() == [ESC, ESC]


# ---- Stop on an agent's own prompt message (rev-iter6-004) ---------------------------------

def test_stop_on_an_agents_prompt_keeps_what_it_asked_and_the_stopped_line(rig):
    waiting(rig, BG, OTHER)
    _on_prompt(rig)
    msg, query = rig.prompt_msg, rig.prompt_query
    assert rig.writes.bytes() == [CHORD, CHORD]
    # The tapped message is not turned into a copy of the card.
    query.edit_message_text.assert_not_awaited()
    edits = [c.kwargs for c in rig.bot._app.bot.edit_message_text.await_args_list
             if c.kwargs.get("message_id") == msg["msg_id"]]
    assert edits
    assert edits[-1]["text"].startswith(msg["text"].split("\n")[0])
    assert edits[-1]["text"].endswith(
        "\n\n⏹ This prompt closed because its background agent was stopped.")
    assert edits[-1]["reply_markup"] is None
    # The card itself says how the Stop went.
    assert rig.card_edits[-1][1] == "⚠️ <b>api</b> · @alice stopped 2 background agents"


def test_stop_on_the_card_still_edits_the_card(rig):
    """Control: the tap on the busy card edits that message, the card."""
    waiting(rig, BG, OTHER)
    query = rig.tap(CARD, "stop", uid=ALICE)
    query.edit_message_text.assert_awaited()
    assert query.edit_message_text.await_args.args[0] == (
        "⚠️ <b>api</b> · @alice stopped 2 background agents")


def test_stop_on_claudes_own_prompt_message_keeps_its_text(rig):
    """A running turn's prompt message (Escape path): its buttons go, its
    text stays; the card is not copied onto it."""
    rig.launch(BG)
    rig.agent_bash(BG, "make")
    rig.agent_bash(OTHER, "make all")
    msgs = rig.prompt_messages()
    assert msgs
    query = rig.tap(msgs[-1]["msg_id"], "stop", uid=ALICE)
    assert rig.writes.bytes() == [ESC, ESC]
    query.edit_message_text.assert_not_awaited()
    query.edit_message_reply_markup.assert_awaited_with(reply_markup=None)


# ---- a prompt nobody can name, after Claude's turn ended: no Escape (rev-iter6-002) --------

def _unnamed(sess) -> None:
    """The open prompts' tool is not known any more: their owner is not
    known (``_perm_owner`` None)."""
    for perm in sess.open_prompts():
        perm["tool_info"] = None


@pytest.mark.parametrize("entry", ["card", "command", "miniapp"])
def test_an_unnamed_prompt_in_a_wait_from_idle_gets_no_key(rig, entry):
    sess = waiting(rig)
    rig.agent_bash(BG, "rm -rf build")
    _unnamed(sess)
    assert sess.wait_from_idle and not sess.own_turn_over()
    shown = ENTRY[entry](rig)
    assert rig.writes == []
    assert sess.status is Status.INTERACTIVE and sess.open_prompts()
    assert list(sess.active_subagents) == [BG]
    text = "api has a dialog open in the terminal. Answer it there, then try Stop again."
    assert any(text in s for s in shown), shown
    if entry == "miniapp":
        assert rig.miniapp[0] == 409 and rig.miniapp[1]["error"] == "dialog_open"


def test_an_unnamed_prompt_carried_over_a_stop_gets_no_key(rig):
    sess = waiting(rig)
    rig.agent_bash(BG, "rm -rf build")
    _unnamed(sess)
    sess.wait_from_idle, sess.resumed_after_stop = False, True
    _card(rig)
    assert rig.writes == []
    assert sess.open_prompts()


def test_the_guard_types_no_escape_for_an_unnamed_prompt_after_the_turn(rig):
    sess = waiting(rig)
    rig.agent_bash(BG, "rm -rf build")
    _unnamed(sess)
    rig.run_async(rig.bot._send_stop_keys(sess, [], 0))
    assert rig.writes == []


def test_control_an_unnamed_prompt_in_a_running_turn_keeps_two_escapes(rig):
    rig.launch(BG)
    rig.agent_bash(BG, "make")
    _unnamed(rig.sess)
    assert rig.sess.status is Status.INTERACTIVE and not rig.sess.wait_from_idle
    _card(rig)
    assert rig.writes.bytes() == [ESC, ESC]


def test_control_claudes_own_prompt_in_a_wait_from_idle_keeps_two_escapes(rig):
    """Claude's own prompt open beside an agent's: its turn may run, so
    the Escapes stay (the owner is known, and it is Claude)."""
    sess = waiting(rig)
    rig.agent_bash(BG, "rm -rf build")
    rig.question()
    assert sess.status is Status.INTERACTIVE
    _card(rig)
    assert rig.writes.bytes() == [ESC, ESC]


# ---- the fallback after a press that did not go out: Escapes only for BUSY ------------------

def test_a_press_that_failed_on_a_session_not_working_types_no_escape(rig, monkeypatch):
    sess = waiting(rig)

    async def _waits_on_a_person(name, may_press, before_second_press=None):
        # A wait no Stop path may type Escape into (no turn of Claude's
        # running), and the chord did not go out.
        sess.status = Status.INTERACTIVE
        return inject.DIALOG_BEFORE_TYPING

    monkeypatch.setattr(inject, "press_kill_agents", _waits_on_a_person)
    outcome = rig.run_async(rig.bot._stop_session_core(sess))
    assert not outcome.ok and outcome.reason == "dialog_open"
    assert rig.writes == []


def test_a_session_that_ended_during_the_press_is_not_working(rig, monkeypatch):
    sess = waiting(rig)

    async def _ended(name, may_press, before_second_press=None):
        sess.status = Status.GONE
        return inject.DIALOG_BEFORE_TYPING

    monkeypatch.setattr(inject, "press_kill_agents", _ended)
    replies = _command(rig)
    assert rig.writes == []
    assert replies == ["<b>api</b> is not working."]


def test_a_session_parked_during_the_press_could_not_be_stopped(rig, monkeypatch):
    sess = waiting(rig)

    async def _parked(name, may_press, before_second_press=None):
        sess.parked_job = {"id": "bg-1"}
        return inject.DIALOG_BEFORE_TYPING

    monkeypatch.setattr(inject, "press_kill_agents", _parked)
    replies = _command(rig)
    assert rig.writes == []
    assert replies == ["The background agents of api could not be stopped. Try Stop again."]


def test_the_keyless_end_rechecks_after_reading_the_queue(rig, monkeypatch):
    """A turn that starts while the job's end reads Claude's queue is
    interrupted as a working session, never torn down to idle unseen."""
    sess = waiting(rig)
    sess.active_subagents.clear()
    sess.bg_agents.clear()
    sess.job_grace_until = time.monotonic() + 30.0

    async def _turn_starts(_sess, **_kw):
        sess.status = Status.BUSY

    monkeypatch.setattr(rig.bot, "_settle_queued_targets", _turn_starts)
    outcome = rig.run_async(rig.bot._stop_session_core(sess))
    assert outcome.ok and outcome.mode == "keys"
    assert rig.writes.bytes() == [ESC, ESC]


def test_a_wait_that_opens_during_the_dialog_look_gets_no_escape(rig, monkeypatch):
    """The session stops being one whose turn is over while Stop looks for
    a terminal dialog, but no turn of Claude's runs (not BUSY): refused,
    never the Escapes."""
    sess = waiting(rig)

    async def _look(_sess):
        sess.status = Status.INTERACTIVE
        return False

    monkeypatch.setattr(rig.bot, "_terminal_dialog_before_typing", _look)
    outcome = rig.run_async(rig.bot._stop_session_core(sess))
    assert not outcome.ok and outcome.reason == "dialog_open"
    assert rig.writes == []
