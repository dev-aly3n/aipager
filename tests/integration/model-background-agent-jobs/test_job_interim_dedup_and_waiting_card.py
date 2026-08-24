"""design.md success criteria:
- "A Stop/Notification/StopFailure hook arriving while active_subagents is
  non-empty never produces a '✅ … Finished' header; the live card is
  edited in place to the waiting frame... Stop button remains attached."
- "Claude's interim message is delivered exactly once even when multiple
  idle-class events fire with byte-identical content while the job stays
  open."

Equivalence partitioning over interim content (empty / identical /
different) and error-guessing the "idle with agents open but nothing to
say" case explicitly called out in the task brief.

``_edit_busy_rich`` is the one seam ``tests/test_bot_notify_idle.py``
(pre-feature) already asserts through via its ``kwargs["final"]`` — this
suite follows the same convention, checking ``kwargs["waiting"]`` for the
new frame, rather than scanning rendered text: ``_edit_busy_rich`` is
mocked in these tests, so any text ``build_stream_card`` would have
produced never reaches the mock's call args — only the arguments PASSED
IN to it do (the verb string and the ``final``/``waiting`` kwargs), which
is exactly what design.md documents as the dispatch contract.

Every test that calls ``bot.notify`` more than once on the SAME session
does so inside a single coroutine passed to ``run_async`` a single time —
calling ``run_async`` (a fresh event loop per invocation) twice against
one ``TrackedSession`` reuses asyncio primitives created on the first
loop from the second, which surfaces as a swallowed
"bound to a different event loop" error inside ``notify()`` and silently
skips the very code path under test.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from aipager.state import Status


def _job_open_sess(mk_job_session):
    return mk_job_session(status=Status.IDLE, busy_msg_id=42,
                          active_subagents={"a1": {"type": "Explore",
                                                    "history_idx": None}})


def _wire_common_mocks(bot):
    bot._edit_busy_rich = AsyncMock(return_value=True)
    bot._app.bot.edit_message_text = AsyncMock()
    bot._stop_animation = MagicMock()
    bot._maybe_update_bot_name = AsyncMock()


# ---- no false Finished while job is open ---------------------------------

def test_idle_with_agents_open_renders_waiting_not_final(
        mk_bot, run_async, mk_job_session):
    bot = mk_bot()
    _wire_common_mocks(bot)
    sess = _job_open_sess(mk_job_session)

    run_async(bot.notify(sess, "idle_prompt", {"summary": "interim answer"}))

    assert bot._edit_busy_rich.await_args is not None, (
        "no live-card edit happened at all for a job-open idle transition")
    kwargs = bot._edit_busy_rich.await_args.kwargs
    assert kwargs.get("waiting") is True, (
        f"expected the waiting frame (waiting=True), got kwargs={kwargs!r}")
    assert not kwargs.get("final"), (
        f"a Finished (final=True) edit fired while job_background_open() "
        f"was True: kwargs={kwargs!r}")


def test_idle_with_agents_open_does_not_clear_the_live_card(
        mk_bot, run_async, mk_job_session):
    """The Stop button (and the whole card) must stay attached — the
    weakest but always-true observable of that is that busy_msg_id is
    NOT torn down the way the real Finished path tears it down."""
    bot = mk_bot()
    _wire_common_mocks(bot)
    sess = _job_open_sess(mk_job_session)

    run_async(bot.notify(sess, "idle_prompt", {"summary": "interim answer"}))

    assert sess.busy_msg_id == 42, (
        "busy_msg_id was cleared while a background job was still open — "
        "the waiting card must stay live/editable, not be disposed of "
        "like a real Finished card")


def test_idle_once_agents_close_finished_is_produced(
        mk_bot, run_async, mk_job_session):
    """Positive control: once job_background_open() is False, the SAME
    idle_prompt event must go back to producing a real final=True edit —
    proves the waiting-frame assertion above isn't vacuously true for
    every idle_prompt call."""
    bot = mk_bot()
    _wire_common_mocks(bot)
    sess = mk_job_session(status=Status.IDLE, busy_msg_id=42,
                          active_subagents={})

    run_async(bot.notify(sess, "idle_prompt", {"summary": "final answer"}))

    assert bot._edit_busy_rich.await_args is not None
    kwargs = bot._edit_busy_rich.await_args.kwargs
    assert kwargs.get("final") is True, (
        f"no final=True edit was produced for a session with no open "
        f"background agents: kwargs={kwargs!r}")
    assert not kwargs.get("waiting"), (
        f"the waiting frame stayed on for a session with no open "
        f"background agents: kwargs={kwargs!r}")


# ---- content-dedup: identical vs different content ------------------------

def test_identical_interim_content_recorded_only_once(
        mk_bot, run_async, mk_job_session, all_sent_texts):
    """Contract change ("one response per background job" requirement 1):
    interim content is never sent standalone — it is recorded once in the
    job buffer for the single final message; identical strays dedup by
    membership."""
    bot = mk_bot()
    sess = _job_open_sess(mk_job_session)
    content = "the exact same 2122-char interim answer"

    async def _twice():
        await bot.notify(sess, "idle_prompt", {"summary": content})
        await bot.notify(sess, "idle_prompt", {"summary": content})

    run_async(_twice())

    texts = all_sent_texts(bot)
    assert not any(content in t for t in texts), (
        f"interim content must never go out standalone: {texts!r}")
    assert sess.job_interim_buffer == [content]


def test_different_interim_content_recorded_both_in_order(
        mk_bot, run_async, mk_job_session, all_sent_texts):
    """The complement of the dedup test under the one-response contract:
    two DIFFERENT interim payloads while the same job stays open are BOTH
    held for the final message, oldest first — dedup is content-keyed,
    not a blanket 'only one interim ever' rule."""
    bot = mk_bot()
    sess = _job_open_sess(mk_job_session)

    async def _twice():
        await bot.notify(sess, "idle_prompt", {"summary": "first interim answer"})
        await bot.notify(sess, "idle_prompt", {"summary": "second, different answer"})

    run_async(_twice())

    texts = all_sent_texts(bot)
    assert not any("interim answer" in t for t in texts)
    assert sess.job_interim_buffer == [
        "first interim answer", "second, different answer",
    ]


def test_empty_interim_summary_does_not_crash_and_still_renders_waiting(
        mk_bot, run_async, mk_job_session):
    """Error-guessing: idle-class event fires with agents still open but
    NO content (Claude's turn produced no assistant text this time, e.g.
    a pure tool-only interim turn). Must not crash, and — per the
    criterion above — the card must still resolve to the waiting frame,
    never the final one."""
    bot = mk_bot()
    _wire_common_mocks(bot)
    sess = _job_open_sess(mk_job_session)

    run_async(bot.notify(sess, "idle_prompt", {"summary": ""}))

    assert bot._edit_busy_rich.await_args is not None
    kwargs = bot._edit_busy_rich.await_args.kwargs
    assert not kwargs.get("final")


def test_missing_summary_key_does_not_crash(mk_bot, run_async, mk_job_session):
    """Boundary beyond empty-string: the context dict may omit the key
    entirely (no 'summary' at all, e.g. a Notification-class event with
    no assistant text field)."""
    bot = mk_bot()
    _wire_common_mocks(bot)
    sess = _job_open_sess(mk_job_session)

    run_async(bot.notify(sess, "idle_prompt", {}))  # must not raise


# ---- job_agents_lost: TTL-orphan terminal card ----------------------------

def test_job_agents_lost_produces_a_background_agent_lost_header(
        mk_bot, run_async, mk_job_session, all_sent_texts):
    import time as _time
    bot = mk_bot()
    _wire_common_mocks(bot)
    sess = mk_job_session(status=Status.IDLE, busy_msg_id=42, active_subagents={})
    sess.busy_started_at = _time.monotonic() - 90

    run_async(bot.notify(sess, "job_agents_lost", {}))

    texts = all_sent_texts(bot)
    assert any("background agent lost" in t for t in texts), (
        f"no 'background agent lost' terminal text found: {texts!r}")


def test_job_agents_lost_header_still_says_finished_but_is_qualified(
        mk_bot, run_async, mk_job_session, all_sent_texts):
    """entrypoints.md's exact table entry: the terminal TTL-orphan text
    reuses the word 'Finished' but ALWAYS qualifies it with '(background
    agent lost...)' — pinning both halves separately catches a future
    change that drops either the word or the qualifier."""
    import time as _time
    bot = mk_bot()
    _wire_common_mocks(bot)
    sess = mk_job_session(status=Status.IDLE, busy_msg_id=42, active_subagents={})
    sess.busy_started_at = _time.monotonic() - 90

    run_async(bot.notify(sess, "job_agents_lost", {}))

    texts = all_sent_texts(bot)
    matching = [t for t in texts if "Finished" in t and "background agent lost" in t]
    assert matching, f"no combined 'Finished (background agent lost...)' text: {texts!r}"
