"""``_apply_consumption``, ``_consume_and_reanchor``, and
``_send_merged_final``'s ``send_as_new`` mode (design.md "turn anchor
follows consumption").

The case-table black-box tests
(``tests/integration/turn-anchor-follows-consumption/``) exercise these
through the full notify()/handler surface; these are the Developer's
own narrower unit tests against the three new/changed pieces directly.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.state import Status, TrackedSession


@pytest.fixture
def rich_calls(monkeypatch):
    calls = []

    async def _fake_post(method, payload, **_kw):
        calls.append((method, payload))
        return {"ok": True, "result": {"message_id": 999}}

    monkeypatch.setattr("aipager.bot.rich_message._post", _fake_post)
    return calls


def _sess(*, status=Status.BUSY, busy_msg_id=100, trigger_msg_id=1, chat_id=555):
    s = TrackedSession(name="claude-jim", label="jim", status=status)
    s.scope_chat_id = chat_id
    s.busy_msg_id = busy_msg_id
    s.busy_card_trigger = trigger_msg_id
    s.trigger_msg_id = trigger_msg_id
    s.busy_started_at = time.monotonic()
    return s


# ---- _apply_consumption ---------------------------------------------------

def test_apply_consumption_moves_trigger_to_the_last_consumed(mk_bot, run_async):
    bot = mk_bot()
    bot._app.bot = AsyncMock()
    sess = _sess()
    bot.registry.track_message = MagicMock()

    run_async(bot._apply_consumption(sess, [
        {"msg_id": 2, "chat_id": 555, "raw_text": "part one"},
        {"msg_id": 3, "chat_id": 555, "raw_text": "part two"},
    ]))

    assert sess.trigger_msg_id == 3
    assert sess.last_prompt == "part two"


def test_apply_consumption_reacts_thumbsup_on_every_consumed_message(mk_bot, run_async):
    bot = mk_bot()
    bot._app.bot = AsyncMock()
    sess = _sess()
    bot.registry.track_message = MagicMock()

    run_async(bot._apply_consumption(sess, [
        {"msg_id": 2, "chat_id": 555, "raw_text": "a"},
        {"msg_id": 3, "chat_id": 555, "raw_text": "b"},
    ]))

    reaction_calls = bot._app.bot.set_message_reaction.await_args_list
    targets = [(c.args[0], c.args[1], c.args[2]) for c in reaction_calls]
    assert (555, 2, "👍") in targets
    assert (555, 3, "👍") in targets


def test_apply_consumption_tracks_every_consumed_message(mk_bot, run_async):
    bot = mk_bot()
    bot._app.bot = AsyncMock()
    sess = _sess()
    bot.registry.track_message = MagicMock()

    run_async(bot._apply_consumption(sess, [
        {"msg_id": 2, "chat_id": 555, "raw_text": "a"},
    ]))

    bot.registry.track_message.assert_called_once_with(2, "claude-jim", 555)


def test_apply_consumption_empty_list_is_a_noop(mk_bot, run_async):
    bot = mk_bot()
    bot._app.bot = AsyncMock()
    sess = _sess(trigger_msg_id=1)

    run_async(bot._apply_consumption(sess, []))

    assert sess.trigger_msg_id == 1
    bot._app.bot.set_message_reaction.assert_not_awaited()


def test_apply_consumption_falls_back_to_default_chat_id_when_note_lacks_one(
    mk_bot, run_async,
):
    bot = mk_bot()
    bot._app.bot = AsyncMock()
    sess = _sess(chat_id=777)
    bot.registry.track_message = MagicMock()

    run_async(bot._apply_consumption(sess, [{"msg_id": 5, "raw_text": "x"}]))

    bot.registry.track_message.assert_called_once_with(5, "claude-jim", 777)


def test_apply_consumption_skips_notes_with_no_msg_id(mk_bot, run_async):
    bot = mk_bot()
    bot._app.bot = AsyncMock()
    sess = _sess(trigger_msg_id=1)
    bot.registry.track_message = MagicMock()

    run_async(bot._apply_consumption(sess, [{"msg_id": None, "raw_text": "ghost"}]))

    bot.registry.track_message.assert_not_called()
    bot._app.bot.set_message_reaction.assert_not_awaited()
    # No consumed entry had a real msg_id, so the target never moves —
    # matches the guard in _apply_consumption's own `if last_msg_id is
    # not None:` check.
    assert sess.trigger_msg_id == 1
    assert sess.last_prompt != "ghost"


def test_apply_consumption_does_not_delete_notes(mk_bot, run_async, tmp_path, monkeypatch):
    """Deletion already happened upstream — _apply_consumption's job is
    the reply-target/reaction side effects only."""
    from aipager import policy_snapshot as ps
    monkeypatch.setattr(ps, "notes_dir", lambda name: tmp_path / f"notes-{name}")
    path = ps.write_note(
        "claude-jim", None, None, None,
        msg_id=2, chat_id=555, sender_key=(1, 1),
        body="x", raw_text="x",
    )
    bot = mk_bot()
    bot._app.bot = AsyncMock()
    sess = _sess()
    bot.registry.track_message = MagicMock()

    run_async(bot._apply_consumption(sess, [{"msg_id": 2, "chat_id": 555, "raw_text": "x"}]))

    assert path.exists()


# ---- _consume_and_reanchor -------------------------------------------------

def test_consume_and_reanchor_drains_the_whole_batch_before_deciding(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(busy_msg_id=100, trigger_msg_id=1)
    sess.stream_consumed_notes = [
        {"msg_id": 2, "chat_id": 555, "raw_text": "a"},
        {"msg_id": 3, "chat_id": 555, "raw_text": "b"},
    ]
    bot.registry.track_message = MagicMock()
    bot._reanchor_busy_card = AsyncMock()

    run_async(bot._consume_and_reanchor(sess))

    assert sess.stream_consumed_notes == []
    assert sess.trigger_msg_id == 3
    bot._reanchor_busy_card.assert_awaited_once_with(sess, 3, final=False)


def test_consume_and_reanchor_noop_when_nothing_consumed(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess()
    bot._reanchor_busy_card = AsyncMock()
    bot._apply_consumption = AsyncMock()

    run_async(bot._consume_and_reanchor(sess))

    bot._apply_consumption.assert_not_awaited()
    bot._reanchor_busy_card.assert_not_awaited()


def test_consume_and_reanchor_skips_reanchor_when_already_matching(mk_bot, run_async):
    """The consumed note's msg_id happens to equal the card's current
    anchor already (e.g. a redundant absorption of the SAME message) —
    no re-anchor needed."""
    bot = mk_bot()
    sess = _sess(busy_msg_id=100, trigger_msg_id=5)
    sess.busy_card_trigger = 5
    sess.stream_consumed_notes = [{"msg_id": 5, "chat_id": 555, "raw_text": "x"}]
    bot.registry.track_message = MagicMock()
    bot._reanchor_busy_card = AsyncMock()

    run_async(bot._consume_and_reanchor(sess))

    bot._reanchor_busy_card.assert_not_awaited()


def test_consume_and_reanchor_still_reanchors_when_busy_card_trigger_was_never_seeded(
    mk_bot, run_async,
):
    """review-1 rev-iter1-002: a live card whose busy_card_trigger was
    never recorded (e.g. a production bypass site that forgot to seed
    it, or a hand-built session) must NOT read as "nothing to compare
    against" — a `busy_card_trigger is not None` carve-out here used to
    silently disable every later re-anchor for the rest of the turn.
    `None != trigger_msg_id` is a real, actionable mismatch like any
    other."""
    bot = mk_bot()
    sess = _sess(busy_msg_id=100, trigger_msg_id=5)
    sess.busy_card_trigger = None
    sess.stream_consumed_notes = [{"msg_id": 9, "chat_id": 555, "raw_text": "x"}]
    bot.registry.track_message = MagicMock()
    bot._reanchor_busy_card = AsyncMock()

    run_async(bot._consume_and_reanchor(sess))

    assert sess.trigger_msg_id == 9  # consumption still applied
    bot._reanchor_busy_card.assert_awaited_once_with(sess, 9, final=False)


def test_consume_and_reanchor_noop_when_no_live_card(mk_bot, run_async):
    bot = mk_bot()
    sess = _sess(busy_msg_id=0, trigger_msg_id=1)
    sess.stream_consumed_notes = [{"msg_id": 2, "chat_id": 555, "raw_text": "x"}]
    bot.registry.track_message = MagicMock()
    bot._reanchor_busy_card = AsyncMock()

    run_async(bot._consume_and_reanchor(sess))

    assert sess.trigger_msg_id == 2
    bot._reanchor_busy_card.assert_not_awaited()


# ---- _send_merged_final(send_as_new=True) ---------------------------------

def test_send_merged_final_send_as_new_sends_and_tracks(mk_bot, run_async, rich_calls):
    bot = mk_bot()
    sess = _sess(busy_msg_id=100, trigger_msg_id=2, chat_id=555)
    bot.registry.track_message = MagicMock()

    ok = run_async(bot._send_merged_final(
        sess, "the answer", send_as_new=True, reply_to=2,
    ))

    assert ok is True
    methods = [m for m, _p in rich_calls]
    assert methods == ["sendRichMessage"]
    payload = rich_calls[0][1]
    assert payload["reply_to_message_id"] == 2
    assert sess.busy_msg_id == 999  # rich_calls' fake result message_id
    assert sess.busy_card_trigger == 2
    bot.registry.track_message.assert_called_once_with(999, "claude-jim", 555)


def test_send_merged_final_send_as_new_over_ceiling_never_sends(mk_bot, run_async, rich_calls):
    bot = mk_bot()
    sess = _sess(busy_msg_id=100, trigger_msg_id=2, chat_id=555)

    huge_answer = "x" * 40_000  # over _RICH_LIMIT
    ok = run_async(bot._send_merged_final(
        sess, huge_answer, send_as_new=True, reply_to=2,
    ))

    assert ok is False
    assert rich_calls == []


def test_send_merged_final_send_as_new_blocked_returns_false(mk_bot, run_async, monkeypatch):
    from aipager.bot.rich_message import RichMessageBlocked

    async def _raise_blocked(*_a, **_kw):
        raise RichMessageBlocked("403")

    monkeypatch.setattr("aipager.bot.notify.send_rich_message", _raise_blocked)
    bot = mk_bot()
    sess = _sess(busy_msg_id=100, trigger_msg_id=2, chat_id=555)

    ok = run_async(bot._send_merged_final(sess, "hi", send_as_new=True, reply_to=2))

    assert ok is False


def test_send_merged_final_edit_mode_unchanged_when_send_as_new_false(
    mk_bot, run_async, rich_calls,
):
    """The default (edit) mode is completely unaffected by the new
    parameter's existence."""
    bot = mk_bot()
    sess = _sess(busy_msg_id=100, trigger_msg_id=2, chat_id=555)

    ok = run_async(bot._send_merged_final(sess, "the answer"))

    assert ok is True
    methods = [m for m, _p in rich_calls]
    assert methods == ["editMessageText"]
    assert rich_calls[0][1]["message_id"] == 100


# ---- finish-path branching: card_already_final / merged_send_as_new ------

def _idle_sess(*, layout, busy_msg_id=100, trigger_msg_id=2,
              busy_card_trigger=1, chat_id=555):
    from aipager import preferences
    s = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    s.scope_chat_id = chat_id
    s.busy_msg_id = busy_msg_id
    s.trigger_msg_id = trigger_msg_id
    s.busy_card_trigger = busy_card_trigger
    s.busy_started_at = time.monotonic() - 5
    preferences.set_preference(chat_id, "layout", layout)
    return s


def _wire_idle_bot(mk_bot):
    bot = mk_bot()
    bot._app.bot = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    bot.registry.track_message = MagicMock()
    return bot


def test_finish_path_card_layout_reanchors_and_skips_redundant_final_edit(
    mk_bot, run_async,
):
    bot = _wire_idle_bot(mk_bot)
    sess = _idle_sess(layout="card")
    bot._reanchor_busy_card = AsyncMock()
    bot._edit_busy_rich = AsyncMock()

    run_async(bot.notify(sess, "idle_prompt", {"summary": "the answer"}))

    bot._reanchor_busy_card.assert_awaited_once_with(sess, 2, final=True)
    bot._edit_busy_rich.assert_not_awaited()  # card_already_final skips it


def test_finish_path_card_layout_no_reanchor_when_trigger_already_matches(
    mk_bot, run_async,
):
    bot = _wire_idle_bot(mk_bot)
    sess = _idle_sess(layout="card", trigger_msg_id=1, busy_card_trigger=1)
    bot._reanchor_busy_card = AsyncMock()
    bot._edit_busy_rich = AsyncMock(return_value=True)

    run_async(bot.notify(sess, "idle_prompt", {"summary": "the answer"}))

    bot._reanchor_busy_card.assert_not_awaited()
    bot._edit_busy_rich.assert_awaited_once()


def test_finish_path_trims_answer_tail_before_the_immediate_card_reanchor(
    mk_bot, run_async, rich_calls,
):
    """review-1 rev-iter1-001 (regression): a `card`-layout re-anchor
    used to render the final card via ``_reanchor_busy_card`` BEFORE
    ``_drop_answer_tail`` trimmed a trailing commentary block that
    duplicates the incoming answer — ``card_already_final`` then skipped
    the only OTHER render that would have caught it, so the duplicate
    survived in the re-anchored card's own timeline for good. The trim
    must run before any final render, not after — this test drives the
    REAL ``_reanchor_busy_card`` (unlike
    ``test_finish_path_card_layout_reanchors_and_skips_redundant_final_edit``
    above, which mocks it out and so cannot see this ordering bug)."""
    from types import SimpleNamespace

    bot = _wire_idle_bot(mk_bot)
    ids = iter(range(9001, 9010))

    async def _send_message(*_a, **_kw):
        return SimpleNamespace(message_id=next(ids))

    bot._app.bot.send_message = AsyncMock(side_effect=_send_message)
    bot._app.bot.delete_message = AsyncMock()

    sess = _idle_sess(layout="card", trigger_msg_id=2, busy_card_trigger=1)
    # A trailing commentary block that duplicates the incoming answer —
    # the exact shape _drop_answer_tail's docstring describes (the
    # hook-arrival-order anchor inference slipped and let the answer's
    # own sentence land inside the timeline instead of being withheld
    # for the answer message).
    sess.tool_history = [("Read file.py", True)]
    answer = "it was indeed a test"
    sess.stream_commentary = [(0, answer)]

    run_async(bot.notify(sess, "idle_prompt", {
        "summary": answer, "raw_md": answer,
    }))

    edits = [p for m, p in rich_calls if m == "editMessageText"]
    assert len(edits) == 1, f"expected exactly one final render; got {rich_calls}"
    markdown = edits[0]["rich_message"]["markdown"]
    assert answer not in markdown, (
        "the re-anchored FINAL card must not carry the answer's own "
        f"text in its own timeline (rev-iter1-001); markdown={markdown!r}"
    )
    # The trim actually ran (not just happened to not show up in this
    # one render) — the duplicate block is gone from session state too.
    assert sess.stream_commentary == []


def test_finish_path_merged_layout_flags_send_as_new_not_immediate_reanchor(
    mk_bot, run_async, rich_calls,
):
    """merged never calls _reanchor_busy_card directly — the old card is
    still needed for _drop_answer_tail and the answer text isn't known
    yet; the actual delete+resend happens via _send_merged_final."""
    bot = _wire_idle_bot(mk_bot)
    sess = _idle_sess(layout="merged")
    bot._reanchor_busy_card = AsyncMock()

    run_async(bot.notify(sess, "idle_prompt", {"raw_md": "the answer"}))

    bot._reanchor_busy_card.assert_not_awaited()
    methods = [m for m, _p in rich_calls]
    assert "sendRichMessage" in methods
    payload = next(p for m, p in rich_calls if m == "sendRichMessage")
    assert payload["reply_to_message_id"] == 2
    bot._app.bot.delete_message.assert_awaited_once_with(chat_id=555, message_id=100)


def test_finish_path_replace_layout_never_reanchors(mk_bot, run_async):
    """R5: 'replace' is unchanged — the delete-then-answer-under-
    trigger_msg_id path already reads the (already-correct)
    trigger_msg_id; no _reanchor_busy_card call at all."""
    bot = _wire_idle_bot(mk_bot)
    sess = _idle_sess(layout="replace")
    bot._reanchor_busy_card = AsyncMock()

    run_async(bot.notify(sess, "idle_prompt", {"summary": "the answer"}))

    bot._reanchor_busy_card.assert_not_awaited()
    bot._app.bot.delete_message.assert_awaited_once_with(chat_id=555, message_id=100)
