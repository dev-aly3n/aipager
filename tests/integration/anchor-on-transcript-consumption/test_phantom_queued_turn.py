"""No phantom "next turn" for a message Claude absorbed mid-turn (roadmap 8.47).

Seen live on Claude Code 2.1.283, 2026-09-26: the daemon had restarted a
minute earlier, so the MessageDisplay hook was not yet known live, and turn
1 said nothing before its tool call. A message sent mid-turn was queued
(the hook's pick-up fires at SUBMIT time, as it has since at least
2.1.259), then absorbed (transcript ``remove``/``absorbed_mid_turn``). The
prose fallback's tick read past that line — it keeps only prose and tool
names, and shares ``stream_offset`` with the structure scan — and the
absorption check, which rode the structure scan, is inert until the hook is
known live. At Stop the message still looked queued, so the finish path
started a "next turn" for it: a busy card, then a bare "Done".

The fix gives queue lines their own reader and offset, run whether or not
the hook is live. The decision rule is main's: a queued message with no
absorption seen by the Stop is the next turn's prompt.

The transcript lines copy the SHAPE and ORDER of that turn's lines (file
order, not timestamp order). Content is only the test messages.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from aipager import policy_snapshot
from aipager import preferences as prefs
from aipager.bot.animation import _read_stream_text, _sync_anchors_from_transcript
from aipager.state import Status

CHAT_ID = -2002
PREFIX = "[via Telegram · @owner]\n"
M1 = "ok now its a test. do somthing that take 1min"
M2 = "ok this is a message you shouldnt pick it up yet"
SID = "00000000-0000-4000-8000-000000000000"


def _iso(sec: float) -> str:
    whole = int(sec)
    return f"2026-09-26T20:{whole // 60:02d}:{whole % 60:02d}.{int(sec * 1000) % 1000:03d}Z"


def _append(path, *entries: dict) -> None:
    with open(path, "a", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e) + "\n")


def _qop(operation, sec, content=None, reason=None):
    e = {"type": "queue-operation", "operation": operation,
         "timestamp": _iso(sec), "sessionId": SID}
    if content is not None:
        e["content"] = content
    if reason is not None:
        e["reason"] = reason
    return e


def _assistant(sec, mid, block):
    return {"type": "assistant", "timestamp": _iso(sec), "sessionId": SID,
            "message": {"id": mid, "type": "message", "role": "assistant",
                        "model": "claude-test", "content": [block],
                        "stop_reason": "end_turn"}}


def _tool_result(sec):
    return {"type": "user", "timestamp": _iso(sec), "sessionId": SID,
            "message": {"role": "user", "content": [
                {"tool_use_id": "toolu_1", "type": "tool_result",
                 "content": "done", "is_error": False}]}}


def _attachment(sec, attachment):
    return {"type": "attachment", "timestamp": _iso(sec), "sessionId": SID,
            "attachment": attachment}


def _system(sec, subtype):
    return {"type": "system", "subtype": subtype, "timestamp": _iso(sec),
            "sessionId": SID}


def _user_prompt(sec, text):
    return {"type": "user", "timestamp": _iso(sec), "sessionId": SID,
            "message": {"role": "user", "content": text}}


# Seconds after 20:00:00 — the live turn's own clock.
T_TOOL, T_ENQ, T_HAC, T_REMOVE, T_RESULT, T_ANSWER, T_STOP = (
    3356.055, 3366.890, 3367.069, 3425.812, 3425.770, 3430.127, 3430.449)


def _turn_head(path):
    """Turn 1's opening rounds: thinking, then a tool call — no prose."""
    _append(path,
            _assistant(T_TOOL - 1.3, "msg_1", {"type": "thinking", "thinking": "",
                                                "signature": ""}),
            _assistant(T_TOOL, "msg_1", {"type": "tool_use", "id": "toolu_1",
                                         "name": "Bash", "input": {}}))


def _m2_absorbed_round(path, reason="absorbed_mid_turn"):
    """2.1.283's flush after the tool-result round, in file order: the
    `remove` line, the round, then the `queued_command` attachment stamped
    EARLIER than the lines above it."""
    _append(path,
            _qop("remove", T_REMOVE, PREFIX + M2, reason=reason),
            _tool_result(T_RESULT),
            _attachment(T_ENQ, {"type": "queued_command", "prompt": PREFIX + M2,
                                "commandMode": "prompt", "origin": {"kind": "human"},
                                "timestamp": _iso(T_ENQ), "humanTurn": True}),
            _attachment(T_HAC, {"type": "hook_additional_context", "content": [""],
                                "hookName": "UserPromptSubmit", "toolUseID": "x",
                                "hookEvent": "UserPromptSubmit"}),
            _attachment(T_REMOVE - 0.002, {"type": "total_tokens_reminder", "text": ""}))


def _answer_and_stop(path):
    _append(path,
            _assistant(T_ANSWER, "msg_1", {"type": "text", "text": "answer one"}),
            _system(T_STOP, "stop_hook_summary"),
            _system(T_STOP + 0.024, "turn_duration"))


def _m2_popped(path):
    _append(path, _qop("dequeue", T_STOP + 0.03), _user_prompt(T_STOP + 0.05, PREFIX + M2))


# ── drivers ─────────────────────────────────────────────────────────────

def _send(bot, mk_update, run_async, text, mid):
    run_async(bot._handle_message(
        mk_update(text, message_id=mid, user_id=12345, chat_id=CHAT_ID), MagicMock()))


def _pickup(bot, run_async, sess, mid, text):
    """The hook's pick-up: it deletes the matched note on disk
    (`_match_and_promote`), then the daemon handles `queue_pickup`."""
    policy_snapshot.consume_notes_matching(sess.name, PREFIX + text)
    run_async(bot.notify(sess, "queue_pickup", {
        "consumed": [{"msg_id": mid, "chat_id": CHAT_ID, "raw_text": text}],
        "expired": [],
    }))


def _start_turn_1(bot, mk_update, run_async, sess):
    prefs.set_preference(sess.scope_chat_id, "layout", "card")
    _send(bot, mk_update, run_async, M1, 1)
    _pickup(bot, run_async, sess, 1, M1)
    assert sess.status == Status.BUSY and sess.trigger_msg_id == 1


def _queue_m2_new_shape(bot, mk_update, run_async, sess):
    """2.1.283 (and every release since at least 2.1.259): M2's
    UserPromptSubmit — so aipager's pick-up — fires at ENQUEUE time."""
    _send(bot, mk_update, run_async, M2, 2)
    _append(sess.transcript_path, _qop("enqueue", T_ENQ, PREFIX + M2))
    _pickup(bot, run_async, sess, 2, M2)
    assert [t["msg_id"] for t in sess.queued_targets] == [2]


def _tick(sess):
    """One animation tick's transcript reads, in the tick's own order."""
    _read_stream_text(sess)
    _sync_anchors_from_transcript(sess)


def _stop(bot, run_async, sess, answer="answer one"):
    sess.status = Status.IDLE
    run_async(bot.notify(sess, "idle_prompt", {"summary": answer, "raw_md": answer}))


def _reactions(bot, mid):
    return [c.args[2] for c in bot._app.bot.set_message_reaction.await_args_list
            if c.args[1] == mid]


def _cards(bot):
    return [c for c in bot._app.bot.send_message.await_args_list
            if c.kwargs.get("disable_notification") is not True]


def _answers(rich_calls):
    return [p["reply_to_message_id"] for m, p in rich_calls if m == "sendRichMessage"]


# ── the live regression ─────────────────────────────────────────────────

def test_absorbed_message_gets_no_phantom_turn_when_hook_not_yet_live(
    wired, mk_update, run_async, rich_calls,
):
    """The 2026-09-26 turn, line for line: hook not yet known live, a tick
    reads the flushed round, then Stop. No next turn, no second card; M2
    turns 👍 at its absorption and the answer goes under it."""
    bot, sess, _ = wired
    _start_turn_1(bot, mk_update, run_async, sess)
    assert sess.stream_hook_live is False
    cards_before = len(_cards(bot))
    _turn_head(sess.transcript_path)
    _tick(sess)
    _queue_m2_new_shape(bot, mk_update, run_async, sess)
    assert "👍" not in _reactions(bot, 2), "merely queued: M2 keeps its 👀"

    _m2_absorbed_round(sess.transcript_path)
    _tick(sess)
    _answer_and_stop(sess.transcript_path)
    _stop(bot, run_async, sess)

    assert sess.status == Status.IDLE, "an absorbed message never becomes a next turn"
    assert len(_cards(bot)) == cards_before, "no card for a turn that never ran"
    assert sess.queued_targets == []
    assert _reactions(bot, 2) == ["👀", "👍"]
    assert _answers(rich_calls) == [2], "turn 1 answered M2 too: its answer goes under M2"


def test_absorption_seen_only_at_stop_when_hook_not_yet_live(
    wired, mk_update, run_async, rich_calls,
):
    """No tick between the flush and Stop: the finish path's own scan reads
    the absorption though the hook is not known live."""
    bot, sess, _ = wired
    _start_turn_1(bot, mk_update, run_async, sess)
    cards_before = len(_cards(bot))
    _turn_head(sess.transcript_path)
    _queue_m2_new_shape(bot, mk_update, run_async, sess)
    _m2_absorbed_round(sess.transcript_path)
    _answer_and_stop(sess.transcript_path)
    _stop(bot, run_async, sess)

    assert sess.status == Status.IDLE
    assert len(_cards(bot)) == cards_before
    assert _reactions(bot, 2) == ["👀", "👍"]
    assert _answers(rich_calls) == [2]


def test_delivered_to_agent_is_seen_when_hook_not_yet_live(
    wired, mk_update, run_async, rich_calls,
):
    bot, sess, _ = wired
    _start_turn_1(bot, mk_update, run_async, sess)
    cards_before = len(_cards(bot))
    _turn_head(sess.transcript_path)
    _tick(sess)
    _queue_m2_new_shape(bot, mk_update, run_async, sess)
    _m2_absorbed_round(sess.transcript_path, reason="delivered_to_agent")
    _tick(sess)
    _answer_and_stop(sess.transcript_path)
    _stop(bot, run_async, sess)

    assert sess.status == Status.IDLE and len(_cards(bot)) == cards_before
    assert _reactions(bot, 2) == ["👀", "👍"]


def test_popall_is_seen_when_hook_not_yet_live(
    wired, mk_update, run_async, rich_calls,
):
    """Escape pulled M2 back into the input box (`popAll`), read past by the
    prose fallback: M2 is no longer queued — no next turn, 👀 stays."""
    bot, sess, _ = wired
    _start_turn_1(bot, mk_update, run_async, sess)
    cards_before = len(_cards(bot))
    _turn_head(sess.transcript_path)
    _queue_m2_new_shape(bot, mk_update, run_async, sess)
    _append(sess.transcript_path, _qop("popAll", T_ENQ + 5, PREFIX + M2),
            _tool_result(T_RESULT))
    _tick(sess)
    _answer_and_stop(sess.transcript_path)
    _stop(bot, run_async, sess)

    assert sess.status == Status.IDLE and len(_cards(bot)) == cards_before
    assert _reactions(bot, 2) == ["👀"]


def test_absorbed_message_gets_no_phantom_turn_with_hook_live(
    wired, mk_update, run_async, rich_calls,
):
    """The same turn once MessageDisplay has been seen (the path that
    already worked): still no next turn."""
    bot, sess, _ = wired
    _start_turn_1(bot, mk_update, run_async, sess)
    sess.stream_hook_live = True
    cards_before = len(_cards(bot))
    _turn_head(sess.transcript_path)
    _queue_m2_new_shape(bot, mk_update, run_async, sess)
    _m2_absorbed_round(sess.transcript_path)
    _tick(sess)
    _answer_and_stop(sess.transcript_path)
    _stop(bot, run_async, sess)

    assert sess.status == Status.IDLE and len(_cards(bot)) == cards_before
    assert _reactions(bot, 2) == ["👀", "👍"]


def test_queue_lines_before_the_turn_are_not_rescanned(
    wired, mk_update, run_async, rich_calls,
):
    """The queue scan starts where the turn starts: an earlier turn's
    absorption line for the same text does not consume this turn's M2."""
    bot, sess, _ = wired
    _append(sess.transcript_path, _qop("remove", 1.0, PREFIX + M2, reason="absorbed_mid_turn"))
    _start_turn_1(bot, mk_update, run_async, sess)
    _queue_m2_new_shape(bot, mk_update, run_async, sess)
    _tick(sess)
    assert [t["msg_id"] for t in sess.queued_targets] == [2]


# ── main's rule is unchanged: an unabsorbed queued message is the next turn

def test_genuine_next_turn_pop_still_gets_its_card(
    wired, mk_update, run_async, rich_calls,
):
    """M2 still queued at Stop (no absorption line): exactly as on main, the
    finish path starts its turn — one card under M2, 👍 — and that turn's
    own Stop starts nothing more."""
    bot, sess, _ = wired
    _start_turn_1(bot, mk_update, run_async, sess)
    cards_before = len(_cards(bot))
    _turn_head(sess.transcript_path)
    _tick(sess)
    _queue_m2_new_shape(bot, mk_update, run_async, sess)
    _append(sess.transcript_path, _tool_result(T_RESULT))
    _tick(sess)
    _answer_and_stop(sess.transcript_path)
    _m2_popped(sess.transcript_path)
    _stop(bot, run_async, sess)

    assert _answers(rich_calls) == [1]
    assert sess.status == Status.BUSY and sess.trigger_msg_id == 2
    new_cards = _cards(bot)[cards_before:]
    assert len(new_cards) == 1 and new_cards[0].kwargs["reply_to_message_id"] == 2
    assert _reactions(bot, 2) == ["👀", "👍"]

    _stop(bot, run_async, sess, answer="answer two")
    assert _answers(rich_calls) == [1, 2]
    assert sess.status == Status.IDLE
    assert len(_cards(bot)) == cards_before + 1


# ── older Claude Code: the hook fires at consumption ────────────────────

def test_old_shape_absorption_no_phantom_turn(
    wired, mk_update, run_async, rich_calls,
):
    """A release whose UserPromptSubmit fires when the message is taken: no
    pick-up while queued; the absorption line consumes the note itself —
    with the hook not yet known live."""
    bot, sess, _ = wired
    _start_turn_1(bot, mk_update, run_async, sess)
    cards_before = len(_cards(bot))
    _turn_head(sess.transcript_path)
    _send(bot, mk_update, run_async, M2, 2)
    _append(sess.transcript_path, _qop("enqueue", T_ENQ, PREFIX + M2))
    _tick(sess)
    _append(sess.transcript_path,
            _qop("remove", T_REMOVE, PREFIX + M2, reason="absorbed_mid_turn"),
            _tool_result(T_RESULT))
    _tick(sess)
    run_async(bot._consume_and_reanchor(sess))
    _answer_and_stop(sess.transcript_path)
    _stop(bot, run_async, sess)

    assert sess.status == Status.IDLE and len(_cards(bot)) == cards_before
    assert _reactions(bot, 2) == ["👀", "👍"]
    assert _answers(rich_calls) == [2]


def test_old_shape_next_turn_starts_from_its_own_pickup(
    wired, mk_update, run_async, rich_calls,
):
    """Hook at consumption: M2's pick-up arrives after turn 1's Stop, on an
    IDLE session, and starts its turn; the finish path starts nothing."""
    bot, sess, _ = wired
    _start_turn_1(bot, mk_update, run_async, sess)
    cards_before = len(_cards(bot))
    _turn_head(sess.transcript_path)
    _send(bot, mk_update, run_async, M2, 2)
    _append(sess.transcript_path, _qop("enqueue", T_ENQ, PREFIX + M2), _tool_result(T_RESULT))
    _tick(sess)
    _answer_and_stop(sess.transcript_path)
    _m2_popped(sess.transcript_path)
    _stop(bot, run_async, sess)
    assert sess.status == Status.IDLE and len(_cards(bot)) == cards_before

    _pickup(bot, run_async, sess, 2, M2)
    assert sess.trigger_msg_id == 2
    assert _reactions(bot, 2) == ["👀", "👍"]


# ── restart mid-turn: as on main ────────────────────────────────────────

def test_restart_mid_turn_behaves_as_on_main(
    wired, mk_update, run_async, rich_calls,
):
    """After a daemon restart mid-turn no animation runs; the next message
    sent re-seeds the turn (card and transcript) as on main, and a message
    queued then and still unabsorbed at Stop starts the next turn, with its
    card — the same outcome this test gives on main."""
    bot, sess, _ = wired
    prefs.set_preference(sess.scope_chat_id, "layout", "card")
    sess.status = Status.BUSY      # restored, no card seeded this process
    sess.trigger_msg_id = 1
    assert sess.stream_transcript_path == ""
    _queue_m2_new_shape(bot, mk_update, run_async, sess)
    cards_before = len(_cards(bot))
    _m2_popped(sess.transcript_path)
    _stop(bot, run_async, sess)

    assert sess.status == Status.BUSY and sess.trigger_msg_id == 2
    assert len(_cards(bot)) == cards_before + 1
