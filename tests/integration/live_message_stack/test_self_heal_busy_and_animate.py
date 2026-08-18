"""Black-box tests for design.md success criteria 10, 11, 12 — the fix
for "one stuck compacting card suppresses every later busy card
forever", the exact bug the operator reported live.

HISTORY, kept because it explains why these tests are shaped this way:

when first written, the `test_documented_entrypoint_*` tests below FAILED.
`bot.notify(sess, "user_prompt_submit", {})` — the surface entrypoints.md
names for this behaviour — had its own separate, unconditional early
return (`if not sess.busy_msg_id: ...`) that bailed BEFORE
`_send_busy_and_animate`, where the real Decision-8 logic lives. That gate
was pre-existing (identical at 3a9e2ab), not a regression, but it meant
the documented surface did not reach the fix at all: a wedged session
receiving a fresh prompt through that path still got no card.

That gate has since been removed — `notify` now delegates unconditionally
to `_send_busy_and_animate`, which is the single authority on whether a
fresh card is warranted and bails on its own when one is genuinely live.
All tests here now pass, and these `test_documented_entrypoint_*` tests
are the load-bearing guard against that gate being reinstated: the
orchestrator's own `tests/test_bot_notify.py` replacement does NOT
distinguish the fix from the bug (verified by mutation in review-2), so
do not rely on it for this.

`test_direct_send_busy_and_animate_*` exercise `_send_busy_and_animate`
directly — an established pre-existing seam — and pin the mechanism
independently of which caller reaches it.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock

from aipager.state import Status, TrackedSession


def _sess(label="jim", *, status=Status.IDLE):
    s = TrackedSession(name=f"claude-{label}", label=label, status=status)
    s.busy_started_at = time.monotonic()
    s.scope_kind = "dm"
    s.scope_chat_id = 12345
    return s


def _alive_task():
    async def _long():
        await asyncio.sleep(100)
    return asyncio.get_event_loop().create_task(_long())


# ===========================================================================
# Documented entrypoint: bot.notify(sess, "user_prompt_submit", {})
# ===========================================================================

def test_documented_entrypoint_criterion10_compacting_alive_task(mk_bot, run_async):
    """Criterion 10 via the literal entrypoints.md-documented call.

    EXPECTED (per entrypoints.md): exactly one fresh send_message call.
    OBSERVED: none -- see module docstring.
    """
    bot = mk_bot()
    sess = _sess(status=Status.IDLE)
    sess.busy_msg_id = 42
    sess.push_compacting(msg_id=42, now=time.monotonic() - 1000, deadline_seconds=180.0)

    async def _go():
        sess.animate_task = _alive_task()
        await bot.notify(sess, "user_prompt_submit", {})
        if sess.animate_task:
            sess.animate_task.cancel()
    run_async(_go())

    bot._app.bot.send_message.assert_awaited_once()


def test_documented_entrypoint_criterion10_compacting_no_task(mk_bot, run_async):
    """Same as above but with no animate_task at all -- entrypoints.md's
    'regardless of whether the animation task object is still running'
    explicitly includes this case."""
    bot = mk_bot()
    sess = _sess(status=Status.IDLE)
    sess.busy_msg_id = 42
    sess.push_compacting(msg_id=42, now=time.monotonic() - 1000, deadline_seconds=180.0)
    sess.animate_task = None

    run_async(bot.notify(sess, "user_prompt_submit", {}))

    bot._app.bot.send_message.assert_awaited_once()


def test_documented_entrypoint_criterion12_busy_dead_task_sends_fresh(mk_bot, run_async):
    """Criterion 12 via the literal entrypoints.md-documented call:
    'busy top with a DEAD task still clears and sends a fresh card.'"""
    bot = mk_bot()
    sess = _sess(status=Status.IDLE)
    sess.busy_msg_id = 99  # ordinary busy card, no compaction involved

    async def _go():
        async def _done():
            return None
        t = asyncio.get_event_loop().create_task(_done())
        await t
        sess.animate_task = t
        await bot.notify(sess, "user_prompt_submit", {})
    run_async(_go())

    bot._app.bot.send_message.assert_awaited_once()


def test_documented_entrypoint_criterion11_busy_alive_task_bails(mk_bot, run_async):
    """Criterion 11's regression check (unchanged behavior) DOES hold via
    this entrypoint -- included for completeness, but note it passes for
    the WRONG reason: the unconditional early-return gate bails
    regardless of task state, not because of task-liveness-aware
    race-guard logic specifically. See module docstring."""
    bot = mk_bot()
    sess = _sess(status=Status.IDLE)
    sess.busy_msg_id = 99

    async def _go():
        sess.animate_task = _alive_task()
        await bot.notify(sess, "user_prompt_submit", {})
        if sess.animate_task:
            sess.animate_task.cancel()
    run_async(_go())

    bot._app.bot.send_message.assert_not_awaited()
    assert sess.busy_msg_id == 99


# ===========================================================================
# Direct call: bot._send_busy_and_animate(sess) -- the real fix location
# ===========================================================================

def test_direct_send_busy_and_animate_criterion10_compacting_alive_task(
    mk_bot, run_async,
):
    """The actual Decision-8 self-heal, reached directly: a live
    compacting top with a STILL-ALIVE animate task must still be
    reclaimed and get a fresh busy card -- this is the live bug."""
    bot = mk_bot()
    sess = _sess(status=Status.IDLE)
    sess.busy_msg_id = 42
    sess.push_compacting(msg_id=42, now=time.monotonic() - 1000, deadline_seconds=180.0)
    bot._start_animation = MagicMock()

    async def _go():
        sess.animate_task = _alive_task()
        await bot._send_busy_and_animate(sess)
        if sess.animate_task:
            sess.animate_task.cancel()
    run_async(_go())

    bot._app.bot.send_message.assert_awaited_once()
    assert sess.stack_top_kind() == "busy"


def test_direct_send_busy_and_animate_criterion10_clears_stale_compacting_kind(
    mk_bot, run_async,
):
    bot = mk_bot()
    sess = _sess(status=Status.IDLE)
    sess.busy_msg_id = 42
    sess.push_compacting(msg_id=42, now=time.monotonic() - 1000, deadline_seconds=180.0)
    bot._start_animation = MagicMock()

    async def _go():
        sess.animate_task = _alive_task()
        await bot._send_busy_and_animate(sess)
        if sess.animate_task:
            sess.animate_task.cancel()
    run_async(_go())

    # The old compacting entry is gone, replaced by a fresh busy id.
    assert sess.busy_msg_id != 42


def test_direct_send_busy_and_animate_criterion11_busy_alive_task_bails(
    mk_bot, run_async,
):
    """The original race guard: a live BUSY top with an alive task must
    still bail without sending a duplicate card."""
    bot = mk_bot()
    sess = _sess(status=Status.IDLE)
    sess.busy_msg_id = 99

    async def _go():
        sess.animate_task = _alive_task()
        await bot._send_busy_and_animate(sess)
        if sess.animate_task:
            sess.animate_task.cancel()
    run_async(_go())

    bot._app.bot.send_message.assert_not_awaited()
    assert sess.busy_msg_id == 99


def test_direct_send_busy_and_animate_criterion12_busy_dead_task_resends(
    mk_bot, run_async,
):
    """The original stale-reset: a BUSY top whose task already died must
    clear and send a fresh card."""
    bot = mk_bot()
    sess = _sess(status=Status.IDLE)
    sess.busy_msg_id = 99
    bot._start_animation = MagicMock()

    async def _go():
        async def _done():
            return None
        t = asyncio.get_event_loop().create_task(_done())
        await t
        sess.animate_task = t
        await bot._send_busy_and_animate(sess)
    run_async(_go())

    bot._app.bot.send_message.assert_awaited_once()
    assert sess.busy_msg_id != 99
