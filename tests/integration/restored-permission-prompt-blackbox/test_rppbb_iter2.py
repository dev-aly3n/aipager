"""Black-box rows for roadmap 8.102, iteration 2: the behaviours the
branch's CHANGELOG and docs/commands.md promise on top of design.md.

- A second answer tap on the busy card after its prompt was answered says
  "already answered" and types nothing (with or without a restart), and
  the old buttons of a prompt that was NOT brought back after a restart do
  the same.
- A prompt asked inside a subagent (``agent_id`` on the hook) is not
  brought back after a restart.
- Hostile transcript lines never stop ``recover_sessions`` and never make
  ``saved_prompt_evidence`` raise.
- Boundary and error guesses missed in iteration 1: a concurrent double
  tap, a tap by a stranger, a fresh prompt on the same card after the
  first was answered, a separate prompt across two restarts.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone

import pytest

from aipager import preferences as prefs
from aipager.state import Status
from aipager.transcript import saved_prompt_evidence

CHAT = 256113222
NAME = "claude-rppbb_harness"
LABEL = "rppbb"
OWNER = 12345
STRANGER = 999001
ANSWER_VERBS = ("allow", "allow_always", "deny")
EVIDENCE = {"none", "answered", "moved_on", "unreadable"}


@pytest.fixture(autouse=True)
def _card_layout():
    prefs.set_preference(CHAT, "layout", "card")


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, tz=timezone.utc).isoformat().replace(
        "+00:00", "Z")


def _run(vloop, coro):
    return vloop.run_until_complete(coro)


def _dead(tmp_path) -> str:
    return str(tmp_path / "gone.sock")


def _user_prompt(r, at: float) -> None:
    r.append({"type": "user", "timestamp": _iso(at), "message": {
        "role": "user", "content": "[via Telegram · @owner]\nclean up"}})


def _already(toast) -> bool:
    return bool(toast) and "already answered" in toast.lower()


# ── A. a second answer tap on the busy card ──────────────────────────────

async def _answered_card(r, tmp_path, *, restart: bool, first: str = "allow"):
    """An inline prompt (optionally restored), answered with *first*.
    Returns the card id and the callback data of every answer button the
    card showed before the answer (what a stale client still shows)."""
    _user_prompt(r, r.wall())
    card = await r.inline_prompt(reply_addr=_dead(tmp_path))
    if restart:
        await r.restart()
    stale = {d.rsplit(":", 1)[-1]: d for d in r.chat.markups.get(card, [])}
    await r.tap(card, first)
    await asyncio.sleep(1)
    return card, stale


@pytest.mark.parametrize("restart", [True, False],
                         ids=["restored", "no-restart"])
def test_second_deny_tap_says_already_answered(replay, vloop, tmp_path,
                                               restart):
    r = replay

    async def scenario():
        card, stale = await _answered_card(r, tmp_path, restart=restart)
        return await r.tap(card, data=stale["deny"])

    assert _already(_run(vloop, scenario()))


@pytest.mark.parametrize("restart", [True, False],
                         ids=["restored", "no-restart"])
def test_second_deny_tap_types_nothing(replay, vloop, tmp_path, restart):
    r = replay

    async def scenario():
        card, stale = await _answered_card(r, tmp_path, restart=restart)
        before = list(r.pty.keys)
        await r.tap(card, data=stale["deny"])
        await asyncio.sleep(1)
        return before

    before = _run(vloop, scenario())
    assert r.pty.keys[len(before):] == []


@pytest.mark.parametrize("second", ["allow", "deny"])
def test_second_tap_after_deny_types_nothing(replay, vloop, tmp_path, second):
    """Deny answered first (typed refusal); a stale Allow or a repeated
    Deny types nothing more."""
    r = replay

    async def scenario():
        card, stale = await _answered_card(r, tmp_path, restart=True,
                                           first="deny")
        before = list(r.pty.keys)
        await r.tap(card, data=stale[second])
        await asyncio.sleep(1)
        return before

    before = _run(vloop, scenario())
    assert r.pty.keys[len(before):] == []


def test_repeated_allow_tap_types_nothing(replay, vloop, tmp_path):
    """A double tap of the same button: the second one types nothing."""
    r = replay

    async def scenario():
        card, stale = await _answered_card(r, tmp_path, restart=True)
        before = list(r.pty.keys)
        await r.tap(card, data=stale["allow"])
        await asyncio.sleep(1)
        return before

    before = _run(vloop, scenario())
    assert r.pty.keys[len(before):] == []


def test_second_tap_writes_no_second_audit_record(replay, vloop, tmp_path):
    from aipager import audit as audit_mod

    r = replay

    def _count() -> int:
        p = audit_mod.AUDIT_LOG_PATH
        return len(p.read_text().splitlines()) if p.exists() else 0

    async def scenario():
        card, stale = await _answered_card(r, tmp_path, restart=True)
        before = _count()
        await r.tap(card, data=stale["deny"])
        await asyncio.sleep(1)
        return before

    before = _run(vloop, scenario())
    assert _count() == before


def test_second_tap_leaves_the_session_working(replay, vloop, tmp_path):
    r = replay

    async def scenario():
        card, stale = await _answered_card(r, tmp_path, restart=True)
        await r.tap(card, data=stale["deny"])
        await asyncio.sleep(1)

    _run(vloop, scenario())
    assert r.sess.status is Status.BUSY


def test_second_tap_keeps_the_working_card_live(replay, vloop, tmp_path):
    """Refusing the stale tap must not strip the working card's buttons
    (its Stop): the card is still live a few seconds later."""
    r = replay

    async def scenario():
        card, stale = await _answered_card(r, tmp_path, restart=True)
        await r.tap(card, data=stale["deny"])
        await asyncio.sleep(5)
        return card

    card = _run(vloop, scenario())
    assert card in r.chat.live_cards()


def test_second_tap_does_not_remove_the_cards_keyboard(replay, vloop,
                                                       tmp_path):
    r = replay

    async def scenario():
        card, stale = await _answered_card(r, tmp_path, restart=True)
        await r.tap(card, data=stale["deny"])
        await asyncio.sleep(1)
        return r.last_query

    q = _run(vloop, scenario())
    removed = [c for c in q.edit_message_reply_markup.await_args_list
               if c.kwargs.get("reply_markup", "x") is None
               or (c.args and c.args[0] is None)]
    assert removed == []


@pytest.mark.parametrize("restart", [True, False],
                         ids=["restored", "no-restart"])
def test_tap_after_terminal_answer_types_nothing(
        replay, vloop, tmp_path, restart):
    """Answered in the terminal (Claude's PostToolUse and next PreToolUse
    arrive; the card is working again, only Stop left): a stale client's
    Allow on the card types nothing ("already answered")."""
    r = replay

    async def scenario():
        _user_prompt(r, r.wall())
        card = await r.inline_prompt(reply_addr=_dead(tmp_path))
        if restart:
            await r.restart()
        allow = r.button(card, "allow")
        r.post_tool()
        await asyncio.sleep(0.5)
        r.pre_tool("Read", {"file_path": "/x"}, tool_use_id="toolu_02")
        await asyncio.sleep(2)
        before = list(r.pty.keys)
        await r.tap(card, data=allow)
        await asyncio.sleep(1)
        return before

    before = _run(vloop, scenario())
    assert r.pty.keys[len(before):] == []


@pytest.mark.parametrize("restart", [True, False],
                         ids=["restored", "no-restart"])
def test_tap_after_turn_ended_types_nothing(replay, vloop, tmp_path, restart):
    """The prompt answered in the terminal and the turn ended (Stop): a
    stale client's Deny on the old card types nothing."""
    r = replay

    async def scenario():
        _user_prompt(r, r.wall())
        card = await r.inline_prompt(reply_addr=_dead(tmp_path))
        if restart:
            await r.restart()
        deny = r.button(card, "deny")
        r.stop("done in the terminal")
        await asyncio.sleep(20)
        before = list(r.pty.keys)
        await r.tap(card, data=deny)
        await asyncio.sleep(1)
        return before

    before = _run(vloop, scenario())
    assert r.pty.keys[len(before):] == []


def test_new_prompt_on_the_same_card_still_answers(replay, vloop, tmp_path):
    """The stale-tap refusal must not refuse the NEXT prompt on the same
    card: a second PermissionRequest after the first was answered is
    answered by its Allow."""
    r = replay

    async def scenario():
        card, _stale = await _answered_card(r, tmp_path, restart=True)
        r.post_tool()
        await asyncio.sleep(0.5)
        r.pre_tool(tool_use_id="toolu_02",
                   tool_input={"command": "rm -rf dist"})
        await asyncio.sleep(0.5)
        r.permission(tool_input={"command": "rm -rf dist"},
                     reply_addr=_dead(tmp_path), request_id="req-2")
        await asyncio.sleep(15)
        before = list(r.pty.keys)
        await r.tap(card, "allow")
        await asyncio.sleep(1)
        return before

    before = _run(vloop, scenario())
    assert r.pty.keys[len(before):] == ["Enter"]


def test_first_tap_on_a_fresh_card_prompt_still_answers(replay, vloop,
                                                        tmp_path):
    """Regression guard for the stale-tap refusal: no restart, the first
    Allow on a live inline prompt types Enter."""
    r = replay

    async def scenario():
        card = await r.inline_prompt(reply_addr=_dead(tmp_path))
        await r.tap(card, "allow")
        await asyncio.sleep(1)

    _run(vloop, scenario())
    assert r.pty.keys == ["Enter"]


def test_concurrent_double_tap_types_one_answer(replay, vloop, tmp_path):
    """Error guess: two taps racing (Allow and Deny delivered together)
    on a restored card type one answer, not both."""
    r = replay

    async def scenario():
        _user_prompt(r, r.wall())
        card = await r.inline_prompt(reply_addr=_dead(tmp_path))
        await r.restart()
        allow, deny = r.button(card, "allow"), r.button(card, "deny")
        before = list(r.pty.keys)
        await asyncio.gather(r.tap(card, data=allow), r.tap(card, data=deny))
        await asyncio.sleep(2)
        return before

    before = _run(vloop, scenario())
    typed = r.pty.keys[len(before):]
    assert typed in (["Enter"], ["Down"] * 5 + ["Enter"]), typed


def test_stranger_tap_on_restored_card_types_nothing(replay, vloop, tmp_path):
    """Only someone who may answer the prompt may use it."""
    r = replay

    async def scenario():
        _user_prompt(r, r.wall())
        card = await r.inline_prompt(reply_addr=_dead(tmp_path))
        await r.restart()
        before = list(r.pty.keys)
        await r.tap(card, "allow", user_id=STRANGER)
        await asyncio.sleep(1)
        return before

    before = _run(vloop, scenario())
    assert r.pty.keys[len(before):] == []


def test_stranger_tap_leaves_the_restored_prompt_waiting(replay, vloop,
                                                         tmp_path):
    r = replay

    async def scenario():
        _user_prompt(r, r.wall())
        card = await r.inline_prompt(reply_addr=_dead(tmp_path))
        await r.restart()
        await r.tap(card, "allow", user_id=STRANGER)
        await asyncio.sleep(1)

    _run(vloop, scenario())
    assert r.sess.status is Status.INTERACTIVE


# ── A'. the old buttons of a prompt NOT brought back ─────────────────────

def _tool_result_now(r) -> None:
    r.append({"type": "user", "timestamp": _iso(r.wall()), "message": {
        "role": "user", "content": [{"type": "tool_result",
                                     "tool_use_id": "toolu_01",
                                     "content": "removed"}]}})


async def _not_brought_back(r, tmp_path, how: str):
    _user_prompt(r, r.wall())
    card = await r.inline_prompt(reply_addr=_dead(tmp_path))
    stale = {d.rsplit(":", 1)[-1]: d for d in r.chat.markups.get(card, [])}
    await r.stop_process()
    await asyncio.sleep(15)
    if how == "answered-in-terminal":
        _tool_result_now(r)
    elif how == "invalid-record":
        from aipager import state
        data = json.loads(state.SESSION_STATE_FILE.read_text())
        data["sessions"][NAME]["open_prompt"]["v"] = 99
        state.SESSION_STATE_FILE.write_text(json.dumps(data))
    await asyncio.sleep(15)
    r.start_process()
    await r.bot.recover_sessions()
    await asyncio.sleep(2)
    return card, stale


@pytest.mark.parametrize("how", ["answered-in-terminal", "invalid-record"])
@pytest.mark.parametrize("verb", ["allow", "deny"])
def test_old_button_of_a_dropped_prompt_says_already_answered(
        replay, vloop, tmp_path, how, verb):
    r = replay

    async def scenario():
        card, stale = await _not_brought_back(r, tmp_path, how)
        return await r.tap(card, data=stale[verb])

    assert _already(_run(vloop, scenario()))


@pytest.mark.parametrize("how", ["answered-in-terminal", "invalid-record"])
@pytest.mark.parametrize("verb", ["allow", "deny"])
def test_old_button_of_a_dropped_prompt_types_nothing(
        replay, vloop, tmp_path, how, verb):
    r = replay

    async def scenario():
        card, stale = await _not_brought_back(r, tmp_path, how)
        await r.tap(card, data=stale[verb])
        await asyncio.sleep(1)

    _run(vloop, scenario())
    assert r.pty.keys == []


def test_old_button_of_a_dropped_prompt_keeps_the_adopted_card_live(
        replay, vloop, tmp_path):
    """The adopted (8.101) card stays a live working card after a refused
    stale tap: its Stop is not stripped."""
    r = replay

    async def scenario():
        card, stale = await _not_brought_back(r, tmp_path,
                                              "answered-in-terminal")
        await r.tap(card, data=stale["allow"])
        await asyncio.sleep(5)
        return card

    card = _run(vloop, scenario())
    assert card in r.chat.live_cards()


# ── B. a prompt asked inside a subagent ──────────────────────────────────

async def _subagent_prompt(r, tmp_path, *, agent_id: str = "agent-7"):
    """A card turn whose subagent hits a permission dialog."""
    _user_prompt(r, r.wall())
    await r.card_turn()
    r.pre_tool("Agent", {"description": "clean", "prompt": "clean"},
               tool_use_id="toolu_agent")
    await asyncio.sleep(0.5)
    r.pre_tool(tool_use_id="toolu_sub", agent_id=agent_id,
               agent_type="general-purpose")
    await asyncio.sleep(0.5)
    r.permission(reply_addr=_dead(tmp_path), agent_id=agent_id,
                 agent_type="general-purpose", tool_use_id="toolu_sub")
    await asyncio.sleep(15)
    return r.sess.busy_msg_id


def test_subagent_prompt_is_shown_and_waiting(replay, vloop, tmp_path):
    """Precondition for the subagent rows: the subagent's prompt really is
    on the card and the session waits on it before the restart."""
    r = replay

    async def scenario():
        card = await _subagent_prompt(r, tmp_path)
        return "allow" in [d.rsplit(":", 1)[-1]
                           for d in r.chat.markups.get(card, [])]

    shown = _run(vloop, scenario())
    assert (shown, r.sess.status) == (True, Status.INTERACTIVE)


def test_subagent_prompt_is_not_saved(replay, vloop, tmp_path):
    r = replay

    async def scenario():
        await _subagent_prompt(r, tmp_path)
        r.bot.registry.save()
        return r.saved_entry()

    assert "open_prompt" not in _run(vloop, scenario())


def test_subagent_prompt_is_not_restored(replay, vloop, tmp_path):
    r = replay

    async def scenario():
        await _subagent_prompt(r, tmp_path)
        await r.restart()
        await asyncio.sleep(2)

    _run(vloop, scenario())
    assert r.sess.status is not Status.INTERACTIVE


def test_subagent_prompt_logs_no_restored_line(replay, vloop, tmp_path,
                                               caplog):
    r = replay

    async def scenario():
        await _subagent_prompt(r, tmp_path)
        await r.restart()

    with caplog.at_level(logging.INFO):
        _run(vloop, scenario())
    assert not [m for m in caplog.messages
                if "permission prompt restored after restart" in m]


def test_subagent_prompt_old_allow_types_nothing_after_restart(
        replay, vloop, tmp_path):
    """The prompt was not brought back, so its old Allow types nothing."""
    r = replay

    async def scenario():
        card = await _subagent_prompt(r, tmp_path)
        allow = r.button(card, "allow")   # LookupError if never shown
        await r.restart()
        await asyncio.sleep(2)
        await r.tap(card, data=allow)
        await asyncio.sleep(1)

    _run(vloop, scenario())
    assert r.pty.keys == []


def test_subagent_question_is_not_saved(replay, vloop):
    r = replay

    async def scenario():
        _user_prompt(r, r.wall())
        await r.card_turn(1, "ask me")
        r.pre_tool("AskUserQuestion", {"questions": [{
            "question": "Which?", "header": "Pick", "multiSelect": False,
            "options": [{"label": "A", "description": "a"},
                        {"label": "B", "description": "b"}]}]},
            tool_use_id="toolu_subq", agent_id="agent-7",
            agent_type="general-purpose")
        await asyncio.sleep(15)
        r.bot.registry.save()
        return r.saved_entry()

    assert "open_prompt" not in _run(vloop, scenario())


def test_subagent_tool_use_id_is_not_taken_for_the_parents_prompt(
        replay, vloop, tmp_path):
    """The parent's PermissionRequest carries no tool_use_id; a subagent's
    PreToolUse of the same tool came after the parent's. Its tool_result in
    the transcript (dated before the prompt) must not count as the
    parent's answer: the parent's prompt is restored."""
    r = replay

    async def scenario():
        _user_prompt(r, r.wall())
        await r.card_turn()
        r.pre_tool(tool_use_id="toolu_parent")
        await asyncio.sleep(0.2)
        r.pre_tool(tool_use_id="toolu_sub", agent_id="agent-7",
                   agent_type="general-purpose")
        await asyncio.sleep(0.2)
        r.permission(reply_addr=_dead(tmp_path))
        await asyncio.sleep(15)
        await r.stop_process()
        await asyncio.sleep(10)
        r.append({"type": "user", "timestamp": _iso(r.wall() - 3600),
                  "message": {"role": "user", "content": [{
                      "type": "tool_result", "tool_use_id": "toolu_sub",
                      "content": "ok"}]}})
        await asyncio.sleep(10)
        r.start_process()
        await r.bot.recover_sessions()

    _run(vloop, scenario())
    assert r.sess.status is Status.INTERACTIVE


def test_parent_prompt_after_a_subagent_prompt_is_saved(replay, vloop,
                                                        tmp_path):
    """Boundary: the subagent's prompt is answered, then the parent's own
    prompt opens; that one is saved."""
    r = replay

    async def scenario():
        card = await _subagent_prompt(r, tmp_path)
        await r.tap(card, "allow")
        await asyncio.sleep(1)
        r.post_tool(tool_use_id="toolu_sub")
        await asyncio.sleep(0.5)
        r.pre_tool(tool_use_id="toolu_parent2",
                   tool_input={"command": "rm -rf dist"})
        await asyncio.sleep(0.5)
        r.permission(tool_input={"command": "rm -rf dist"},
                     reply_addr=_dead(tmp_path), request_id="req-2")
        await asyncio.sleep(15)
        r.bot.registry.save()
        return r.saved_entry()

    assert "open_prompt" in _run(vloop, scenario())


# ── C. hostile transcript lines ──────────────────────────────────────────

def _hostile(t: float) -> dict[str, str]:
    ts = _iso(t)
    big = "x" * 300_000
    return {
        "system-subtype-list": json.dumps(
            {"type": "system", "subtype": ["turn_duration"], "timestamp": ts}),
        "system-subtype-dict": json.dumps(
            {"type": "system", "subtype": {"a": 1}, "timestamp": ts}),
        "system-subtype-int": json.dumps(
            {"type": "system", "subtype": 7, "timestamp": ts}),
        "type-list": json.dumps({"type": ["user"], "timestamp": ts,
                                 "message": {"content": "x"}}),
        "type-null": json.dumps({"type": None, "timestamp": ts}),
        "timestamp-int": json.dumps({"type": "assistant", "timestamp": 12,
                                     "message": {"content": []}}),
        "timestamp-list": json.dumps({"type": "user", "timestamp": [ts],
                                      "message": {"content": "x"}}),
        "timestamp-garbage": json.dumps({"type": "user", "timestamp": "soon",
                                         "message": {"content": "x"}}),
        "message-str": json.dumps({"type": "assistant", "timestamp": ts,
                                   "message": "hi"}),
        "message-list": json.dumps({"type": "user", "timestamp": ts,
                                    "message": [1, 2]}),
        "content-int": json.dumps({"type": "assistant", "timestamp": ts,
                                   "message": {"content": 5}}),
        "content-items-str": json.dumps({"type": "assistant", "timestamp": ts,
                                         "message": {"content": ["a", 1]}}),
        "content-block-type-list": json.dumps(
            {"type": "assistant", "timestamp": ts, "message": {"content": [
                {"type": ["tool_use"], "id": "toolu_01", "name": "Bash"}]}}),
        "tool-use-name-dict": json.dumps(
            {"type": "assistant", "timestamp": ts, "message": {"content": [
                {"type": "tool_use", "id": {"x": 1}, "name": {"y": 2}}]}}),
        "tool-result-id-list": json.dumps(
            {"type": "user", "timestamp": ts, "message": {"content": [
                {"type": "tool_result", "tool_use_id": ["toolu_01"]}]}}),
        "json-number": "42",
        "json-string": json.dumps("a line"),
        "json-null": "null",
        "deep-nesting": "[" * 100_000 + "]" * 100_000,
        "nul-bytes": '{"type": "user", "timestamp": "' + ts + '", '
                     '"message": {"content": "a\\u0000b"}}',
        "huge-line": json.dumps({"type": "assistant", "timestamp": ts,
                                 "message": {"content": [
                                     {"type": "text", "text": big}]}}),
        "nan-literal": '{"type": "system", "subtype": NaN, "timestamp": "'
                       + ts + '"}',
    }


_HOSTILE_IDS = list(_hostile(0.0))
_SINCE = 1_800_000_000.0


@pytest.mark.parametrize("kind", _HOSTILE_IDS)
@pytest.mark.parametrize("offset", [-60.0, 60.0], ids=["before", "after"])
def test_evidence_never_raises_on_a_hostile_line(tmp_path, kind, offset):
    path = tmp_path / "t.jsonl"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(_hostile(_SINCE + offset)[kind] + "\n")
    os.utime(path, (_SINCE - 3600, _SINCE - 3600))
    assert saved_prompt_evidence(str(path), _SINCE, "toolu_01",
                                 "Bash") in EVIDENCE


@pytest.mark.parametrize("kind", _HOSTILE_IDS)
def test_hostile_line_before_a_real_result_still_answers(tmp_path, kind):
    """A hostile line does not hide a real tool_result after it."""
    path = tmp_path / "t.jsonl"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(_hostile(_SINCE - 60)[kind] + "\n")
        fh.write(json.dumps({"type": "user", "timestamp": _iso(_SINCE + 5),
                             "message": {"role": "user", "content": [{
                                 "type": "tool_result",
                                 "tool_use_id": "toolu_01",
                                 "content": "ok"}]}}) + "\n")
    os.utime(path, (_SINCE - 3600, _SINCE - 3600))
    assert saved_prompt_evidence(str(path), _SINCE, "toolu_01",
                                 "Bash") == "answered"


def test_invalid_utf8_bytes_do_not_raise(tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_bytes(b'{"type": "user", "message": {"content": "\xff\xfe"}}\n'
                     b"\xc3\x28\n")
    os.utime(path, (_SINCE - 3600, _SINCE - 3600))
    assert saved_prompt_evidence(str(path), _SINCE, "toolu_01",
                                 "Bash") in EVIDENCE


@pytest.mark.parametrize("kind", _HOSTILE_IDS)
def test_hostile_line_does_not_stop_recovery_of_a_prompt(
        replay, vloop, tmp_path, caplog, kind):
    """A hostile line written while the daemon was down: recover_sessions
    runs to its summary line."""
    r = replay

    async def scenario():
        _user_prompt(r, r.wall())
        await r.inline_prompt(reply_addr=_dead(tmp_path))
        await r.stop_process()
        await asyncio.sleep(10)
        with open(r.transcript, "a", encoding="utf-8") as fh:
            fh.write(_hostile(r.wall())[kind] + "\n")
        await asyncio.sleep(10)
        r.start_process()
        await r.bot.recover_sessions()

    with caplog.at_level(logging.INFO):
        _run(vloop, scenario())
    assert any(m.startswith("recovered ") for m in caplog.messages), \
        [m for m in caplog.messages if LABEL in m or "recover" in m]


@pytest.mark.parametrize("kind", _HOSTILE_IDS)
def test_hostile_line_leaves_the_card_handled(replay, vloop, tmp_path, kind):
    """The card is restored or adopted (still live with the session
    waiting or working), or closed as before 8.102: never left half done."""
    r = replay

    async def scenario():
        _user_prompt(r, r.wall())
        card = await r.inline_prompt(reply_addr=_dead(tmp_path))
        await r.stop_process()
        await asyncio.sleep(10)
        with open(r.transcript, "a", encoding="utf-8") as fh:
            fh.write(_hostile(r.wall())[kind] + "\n")
        await asyncio.sleep(10)
        r.start_process()
        await r.bot.recover_sessions()
        await asyncio.sleep(2)
        return card

    card = _run(vloop, scenario())
    live = card in r.chat.live_cards()
    handled = (live and r.sess.status in (Status.INTERACTIVE, Status.BUSY)) \
        or (not live and r.chat.cards[card]["stop"] is False)
    assert handled, (r.sess.status, live)


@pytest.mark.parametrize("kind", _HOSTILE_IDS)
def test_hostile_line_does_not_stop_a_plain_busy_adoption(
        replay, vloop, caplog, kind):
    """No prompt at all (8.101): a running turn whose transcript tail is a
    hostile line still runs recovery to its summary line."""
    r = replay

    async def scenario():
        _user_prompt(r, r.wall())
        await r.card_turn()
        r.pre_tool(tool_use_id="toolu_09")
        await asyncio.sleep(3)
        await r.stop_process()
        await asyncio.sleep(10)
        with open(r.transcript, "a", encoding="utf-8") as fh:
            fh.write(_hostile(r.wall())[kind] + "\n")
        r.start_process()
        await r.bot.recover_sessions()

    with caplog.at_level(logging.INFO):
        _run(vloop, scenario())
    assert any(m.startswith("recovered ") for m in caplog.messages)


# ── D. a separate prompt across two restarts ─────────────────────────────

def test_separate_prompt_survives_a_second_restart(replay, vloop, tmp_path):
    r = replay

    async def scenario():
        msg = await r.separate_prompt(reply_addr=_dead(tmp_path))
        await r.restart()
        await asyncio.sleep(5)
        await r.restart()
        await r.tap(msg, "allow")
        await asyncio.sleep(1)

    _run(vloop, scenario())
    assert r.pty.keys == ["Enter"]


def test_restart_with_no_downtime_still_restores(replay, vloop, tmp_path):
    """Boundary: the daemon comes back at once (well inside the hook's
    deadline, but the hook channel here is dead)."""
    r = replay

    async def scenario():
        _user_prompt(r, r.wall())
        await r.inline_prompt(reply_addr=_dead(tmp_path))
        await r.restart(down=0.0)

    _run(vloop, scenario())
    assert r.sess.status is Status.INTERACTIVE
