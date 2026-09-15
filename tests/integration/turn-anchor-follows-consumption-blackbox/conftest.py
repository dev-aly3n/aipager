"""Shared fixtures for the black-box "turn anchor follows consumption" suite.

Independent of the Developer's own tests (``tests/integration/
turn-anchor-follows-consumption/`` and any adapted unit tests) — this
directory is the Tester's own, written against
``/home/aly/researches/ship/turn-anchor-follows-consumption/design.md``'s
case table (A, B1-B10) and ``entrypoints.md``'s public contract only,
without reading ``implementation.md`` or any non-entry-point source.

This directory's name is not a valid Python identifier, so test modules
here cannot ``from .conftest import ...`` — pytest still auto-discovers
this file regardless, matching every other hyphenated directory's own
documented convention (``queue-handoff``,
``collapse-busy-card-timeline-blackbox``). Shared constants and helper
*functions* are therefore exposed as fixtures (not module-level
importables) so test modules can request them by name without a
relative import.

Only the surfaces entrypoints.md documents are driven: ``bot.notify``,
``TelegramBot._handle_message``/``_handle_file``,
``HookReceiver._on_datagram``, raw writes to
``sess.stream_transcript_path``, and the mocked transports
(``rich_message._post``, ``_app.bot.send_message`` /
``delete_message`` / ``set_message_reaction``).
"""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.state import Status

CHAT_ID = -1001
NAME = "claude-x"
LABEL = "x"


@pytest.fixture
def chat_id():
    return CHAT_ID


@pytest.fixture
def sess_name():
    return NAME


@pytest.fixture
def rich_calls(monkeypatch):
    """Capture every raw HTTP call (method, payload) made through
    ``rich_message._post`` — the single transport for editMessageText and
    sendRichMessage alike (entrypoints.md: "the lowest-level seam...
    prefer mocking at this level"). Also the safety net that keeps ANY
    real render triggered by a genuinely-running animator task (started
    by a real ``_send_busy_and_animate``) from ever reaching the network.
    """
    calls = []

    async def _fake_post(method, payload, **_kw):
        calls.append((method, payload))
        return {"ok": True, "result": {"message_id": 999}}

    monkeypatch.setattr("aipager.bot.rich_message._post", _fake_post)
    return calls


@pytest.fixture
def wire_transport(mk_bot, monkeypatch):
    """A bot with the Telegram HTTP boundary mocked (incrementing message
    ids on every ``send_message``) and the dtach pty layer stubbed, but
    UNLIKE ``tests/integration/queue-handoff``'s ``wired`` fixture,
    ``_send_busy_and_animate`` and ``_react`` are left REAL — this suite
    needs to observe the actual busy-card send (``reply_to_message_id``,
    ``disable_notification``) and the actual reaction emoji, not a
    stubbed stand-in. Matches the pattern already used by
    ``tests/test_job_background_lifecycle.py`` for the same reason.

    Returns ``(bot, injected)`` — ``injected`` collects every string
    written to the mocked pty.
    """
    bot = mk_bot()
    next_id = [10_000]

    async def _send_message(chat_id, text=None, **kwargs):
        next_id[0] += 1
        return MagicMock(message_id=next_id[0])

    bot._app.bot.send_message = AsyncMock(side_effect=_send_message)
    bot._app.bot.delete_message = AsyncMock()
    bot._app.bot.set_message_reaction = AsyncMock()
    bot._app.bot.send_document = AsyncMock()
    bot._app.bot.send_chat_action = AsyncMock(return_value=None)
    bot._maybe_update_bot_name = AsyncMock()

    injected: list[str] = []

    async def _send_text_and_enter(name, body):
        injected.append(body)
        return True

    async def _send_keys(name, key):
        return True

    monkeypatch.setattr("aipager.dtach.inject.is_alive",
                        AsyncMock(return_value=True))
    monkeypatch.setattr("aipager.dtach.inject.send_text_and_enter",
                        _send_text_and_enter)
    monkeypatch.setattr("aipager.dtach.inject.send_keys", _send_keys)

    return bot, injected


@pytest.fixture
def install_session(tmp_path):
    """Ground-truth session builder. Registers (or reuses) session
    ``claude-x`` on the given bot's registry, seeds the transient
    reply-target fields this feature adds
    (``busy_msg_id``/``trigger_msg_id``/``busy_card_trigger``), and
    points ``stream_transcript_path`` at a fresh, empty file the test can
    append to directly (entrypoints.md's documented transcript surface).

    Ground-truth field assignment for `busy_msg_id`/`trigger_msg_id`/
    `busy_card_trigger` mirrors the pattern already used by
    `tests/test_bot_notify.py`'s and `tests/test_transcript_exact_
    anchors.py`'s own `_sess()` helpers: these three fields are
    documented (entrypoints.md) as ground truth read/write points, not
    internals.
    """
    def _install(bot, *, status=Status.BUSY, busy_msg_id=None,
                 trigger_msg_id=None, busy_card_trigger=None,
                 scope_chat_id=CHAT_ID, name=NAME, label=LABEL):
        sess = bot.registry.get_or_create(name)
        sess.label = label
        bot.registry.last_active_session = name
        # Ground-truth status assignment, deliberately NOT through
        # ``registry.transition()``: transitioning a fresh (default
        # IDLE) session straight to BUSY trips the pre-existing
        # "new turn starting while a previous job is still open —
        # reclaiming the waiting card" branch (an unrelated,
        # already-shipped background-job mechanism), which resets
        # ``stream_transcript_path``/``stream_offset`` out from under
        # this fixture's own setup. A direct attribute assignment is
        # exactly how ``tests/test_bot_notify.py``'s and
        # ``tests/test_transcript_exact_anchors.py``'s own ``_sess()``
        # ground-truth helpers construct a mid-turn session too (they
        # build a bare ``TrackedSession(status=...)`` rather than
        # driving a real IDLE->BUSY transition).
        sess.status = status
        sess.scope_kind = "dm"
        sess.scope_chat_id = scope_chat_id
        sess.busy_started_at = time.monotonic() - 5
        sess.busy_started_wall = time.time() - 5
        sess.last_hook_at = time.monotonic() - 1
        sess.busy_msg_id = busy_msg_id
        sess.trigger_msg_id = trigger_msg_id
        sess.busy_card_trigger = busy_card_trigger

        p = tmp_path / f"{name}-transcript.jsonl"
        p.write_bytes(b"")
        sess.stream_transcript_path = str(p)
        sess.stream_offset = 0
        sess.stream_hook_live = True
        return sess
    return _install


@pytest.fixture
def append_queue_op():
    """Append one ``queue-operation`` JSONL line to
    ``sess.stream_transcript_path``, the way Claude Code writes one
    (entrypoints.md's documented transcript vocabulary). Pass
    ``newline=False`` to simulate a partial (mid-flush) line."""
    def _append(sess, operation, reason, content, ts=None, *, newline=True):
        line = {
            "type": "queue-operation",
            "operation": operation,
            "content": content,
            "timestamp": ts if ts is not None else time.time(),
        }
        if reason is not None:
            line["reason"] = reason
        raw = json.dumps(line).encode("utf-8")
        if newline:
            raw += b"\n"
        with open(sess.stream_transcript_path, "ab") as fh:
            fh.write(raw)
    return _append


@pytest.fixture
def tick():
    """Fire an ``assistant_text`` notify event — entrypoints.md's
    documented proxy for "a tick happened": "also the mid-turn
    absorption-detection tick when sess.stream_transcript_path has new
    queue-operation lines." Content is irrelevant to the assertions in
    this suite; only the transcript-file side effect matters.

    Auto-increments ``message_id``/``index`` on every call by default —
    a real stream never re-delivers the exact same (message_id, index,
    delta) twice, and doing so here trips an unrelated, pre-existing
    duplicate-delivery guard elsewhere in the notify pipeline (verified
    empirically: two ticks sharing one message_id silently no-op the
    second one, even when the transcript changed in between). Pass an
    explicit ``message_id`` only for a test that deliberately wants to
    exercise that literal-duplicate-delivery case.
    """
    counter = [0]

    def _tick(bot, run_async, sess, *, message_id=None):
        counter[0] += 1
        mid = message_id if message_id is not None else f"sync-tick-{counter[0]}"
        run_async(bot.notify(sess, "assistant_text", {
            "delta": "...", "message_id": mid, "index": 0,
            "final": False,
        }))
    return _tick


@pytest.fixture
def send_update(mk_update):
    """Build an Update the way every other suite in this repo does, and
    drive it through `_handle_message`."""
    def _update(text, message_id, user_id=12345, chat_id=CHAT_ID):
        return mk_update(text, message_id=message_id, user_id=user_id,
                         chat_id=chat_id)
    return _update


@pytest.fixture
def send_text():
    async def _send(bot, update):
        await bot._handle_message(update, MagicMock())
    return _send


@pytest.fixture
def live_turn(install_session, send_update, send_text, run_async, tmp_path):
    """Start a turn for real, from IDLE, via ``_handle_message`` — the
    ONLY way to get a genuinely-alive busy card (a real running animator
    task backing ``busy_msg_id``) without tripping the pre-existing,
    unrelated "stale busy_msg_id (animation dead)" self-heal a purely
    synthetic ``busy_msg_id`` assignment runs into (that self-heal
    silently re-sends a fresh card and resets stream state the moment a
    second message arrives against a `busy_msg_id` no real task backs —
    verified empirically while building this fixture).

    Returns ``(sess, transcript_path)``. Re-establishes
    ``stream_transcript_path`` after the send because
    ``_send_busy_and_animate``'s own per-turn reset clears it (correct:
    in production a subsequent hook event repopulates it; this suite
    doesn't drive that hook event for every test, so it re-seeds the
    field directly, exactly as a hook's `PreToolUse`/`UserPromptSubmit`
    payload would).
    """
    def _start(bot, text, message_id, *, user_id=12345, name=NAME):
        sess = install_session(bot, status=Status.IDLE, busy_msg_id=None,
                               trigger_msg_id=None, busy_card_trigger=None,
                               name=name)
        tp = tmp_path / f"{name}-live.jsonl"
        tp.write_bytes(b"")

        run_async(send_text(bot, send_update(text, message_id, user_id=user_id)))

        sess.stream_transcript_path = str(tp)
        sess.stream_offset = 0
        sess.stream_hook_live = True

        # The message that starts a turn from IDLE is consumed by its
        # OWN real UserPromptSubmit hook (send and consumption are the
        # same event, per the invariant) — this suite drives only the
        # Telegram side of that send, so its note would otherwise sit
        # outstanding forever and, being the OLDEST outstanding note,
        # would block the prefix-run matcher (`match_notes_prefix_run`
        # matches oldest-first and stops at the first miss) from ever
        # reaching a LATER message's note. Retiring it here stands in
        # for the hook-side pickup this suite doesn't separately drive.
        from aipager import policy_snapshot as ps
        own_note = [n for n in ps.list_outstanding_notes(name)
                    if n.get("msg_id") == message_id]
        if own_note:
            ps.delete_notes(name, own_note)
        return sess, tp
    return _start


@pytest.fixture
def mid_turn(live_turn, send_update, send_text, run_async):
    """Start turn 1 for real (M1 from IDLE via ``live_turn``), then send
    each ``(text, message_id)`` pair in ``while_busy`` while the session
    is BUSY — R1's own precondition for every "message arrives mid-turn"
    case-table row. Returns ``(sess, transcript_path, c1)`` with the
    Telegram mocks reset right after setup so a test's own assertions
    start from a clean slate.
    """
    def _build(bot, m1_text, m1_id, *while_busy):
        sess, tp = live_turn(bot, m1_text, m1_id)
        c1 = sess.busy_msg_id
        bot._app.bot.send_message.reset_mock()
        bot._app.bot.delete_message.reset_mock()
        bot._app.bot.set_message_reaction.reset_mock()
        for text, mid in while_busy:
            run_async(send_text(bot, send_update(text, mid)))
            assert sess.trigger_msg_id == m1_id, (
                "setup assumption broke: R1 should leave the target at "
                "the message that started the turn")
        bot._app.bot.send_message.reset_mock()
        bot._app.bot.delete_message.reset_mock()
        return sess, tp, c1
    return _build


@pytest.fixture
def hook_receiver():
    """A real ``HookReceiver`` bound to the given bot's own registry and
    ``notify`` — for replaying hook datagrams end to end (R6's ordering
    consequence, B2's next-turn card send, B10's drain-releases-a-turn),
    per entrypoints.md's ``HookReceiver._on_datagram`` entrypoint."""
    from aipager.dtach import hook_receiver as hr

    def _mk(bot):
        return hr.HookReceiver(bot.registry, bot.notify)
    return _mk


@pytest.fixture
def send_datagram(run_async):
    def _send(recv, **fields):
        run_async(recv._on_datagram(json.dumps(fields).encode()))
    return _send
