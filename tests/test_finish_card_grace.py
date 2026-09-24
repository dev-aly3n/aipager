"""The finished card's head start before the answer (roadmap 8.23).

The finish path already renders the final busy card BEFORE it sends the
answer, and that render is a blocking call — the wire order has always
been right. What went wrong live (during the 8.21 round-trip) is that
both landed in the same second, and the phone client draws a new message
immediately but an edit a beat later: the answer arrived under a card
that still read "Processing", which reads as a lagging or broken card.
So the card now gets ``FINISH_CARD_GRACE_SECONDS`` to itself, measured
FROM the card edit rather than added on top of it — the answer-text
building in between already spends part of it, and only the remainder is
ever slept.

Driven through ``bot.notify(sess, "idle_prompt", context)``, the same
entry point the other finish-path suites use
(``tests/test_bot_notify_idle_rich.py``,
``tests/integration/add-telegram-settings-menu/test_layout_delivery_invariant.py``).
Three seams are recorders instead of transports — the final card render,
the answer send, and ``notify._finish_sleep`` — so the assertions are
about ORDER and the slept REMAINDER, with no real waiting anywhere.

``_finish_sleep`` is a module attribute for exactly this reason: patching
``asyncio.sleep`` through a module path mutates the global module and has
hung this suite twice (see CLAUDE.md). The same rule drives the clock
below: only each module's OWN ``time`` reference is rebound, never the
global module, or the event loop's own timers would move with it.
"""

from __future__ import annotations

import pathlib
import time as real_time
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import config
from aipager.bot.rich_message import RichMessageFallbackRequired
from aipager.preferences import set_preference
from aipager.state import Status, TrackedSession


# Pinned far from zero for the reason tests/conftest.py's `steady_clock`
# documents: a fabricated "N seconds ago" stamp must not go negative on a
# freshly booted CI runner.
CLOCK_BASE = 1_000_000.0

# The grace every test in this file pins, rather than reading the live
# constant: `conftest._pin_finish_card_grace` sets it to 0 for the whole
# suite, and an operator with FINISH_CARD_GRACE_SECONDS in their
# environment or config.env would otherwise turn these assertions red —
# the dev-box-env-hides-a-red-workflow trap this repo has already paid
# for once (roadmap 8.14). It happens to equal the shipped default; the
# static check at the bottom of this file is what guards THAT.
GRACE = 0.8


class _Clock:
    """A monotonic clock the test advances by hand."""

    def __init__(self) -> None:
        self.now = CLOCK_BASE

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _Harness:
    """What the finish path did, in the order it did it."""

    def __init__(self) -> None:
        self.bot = None
        self.sess = None
        self.clock = _Clock()
        # One tag per recorded step: "edit_final", "reanchor", "sleep",
        # "send". The ordering assertions read this directly.
        self.order: list[str] = []
        self.sleeps: list[float] = []
        self.answers: list[str] = []

    def run(self, run_async, **context):
        return run_async(self.bot.notify(self.sess, "idle_prompt", context))


def _sess(chat_id: int, clock: _Clock, *, busy_msg_id: int = 42,
          busy_card_trigger: int | None = 7) -> TrackedSession:
    s = TrackedSession(name="claude-fcg", label="fcg", status=Status.IDLE)
    s.scope_kind = "dm" if chat_id > 0 else "group"
    s.scope_chat_id = chat_id
    s.busy_msg_id = busy_msg_id
    # On the fake clock's timeline, not the real one.
    s.busy_started_at = clock.now - 5
    s.trigger_msg_id = 7
    # The grace is owed to a KEPT card, and a card is kept only with a
    # timeline on it — a tool-less one is deleted and the answer goes out
    # alone since roadmap 8.32 (tests/test_quiet_toolless_turns.py).
    s.tool_history = [("Read: /a.py", True)]
    # A real card always has busy_card_trigger seeded at send time ("turn
    # anchor follows consumption"). Seeding it equal to trigger_msg_id
    # keeps the re-anchor branch out of the way; the re-anchor test sets
    # them apart on purpose.
    s.busy_card_trigger = busy_card_trigger
    return s


@pytest.fixture
def finish(mk_bot, monkeypatch):
    """Wire a bot whose finish path is fully recorded.

    ``build_cost`` is charged on the fake clock inside ``detect_rtl``,
    which notify.py calls in the answer-text stretch BETWEEN the final
    card render and the answer send — i.e. exactly the time the grace is
    supposed to absorb rather than add to.
    """
    def _wire(*, chat_id: int, layout: str = "card", busy_msg_id: int = 42,
              busy_card_trigger: int | None = 7, edit_result=True,
              edit_raises: bool = False, build_cost: float = 0.0,
              grace: float = GRACE, send_raises=None) -> _Harness:
        h = _Harness()
        set_preference(chat_id, "layout", layout)

        fake_time = types.SimpleNamespace(
            monotonic=h.clock.monotonic, time=real_time.time,
            sleep=real_time.sleep,
        )
        monkeypatch.setattr("aipager.bot.notify.time", fake_time)
        monkeypatch.setattr("aipager.bot.animation.time", fake_time)

        bot = mk_bot()
        bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
        bot._app.bot.delete_message = AsyncMock()
        bot._app.bot.send_document = AsyncMock()
        bot._maybe_update_bot_name = AsyncMock()
        bot._stop_animation = MagicMock()

        async def _edit(sess, verb, *, final=False, **kw):
            if final:
                h.order.append("edit_final")
                if edit_raises:
                    raise RuntimeError("final render blew up")
                return edit_result
            h.order.append("edit")
            return True

        async def _reanchor(sess, trigger, *, final=False, **kw):
            h.order.append("reanchor")
            return True

        async def _send(chat, text, **kw):
            h.order.append("send")
            h.answers.append(text)
            if send_raises is not None:
                raise send_raises
            return {"message_id": 4242}

        async def _sleep(seconds):
            h.order.append("sleep")
            h.sleeps.append(seconds)

        def _detect(text):
            h.clock.advance(build_cost)
            return False

        bot._edit_busy_rich = _edit
        bot._reanchor_busy_card = _reanchor
        monkeypatch.setattr("aipager.bot.notify.send_rich_message", _send)
        monkeypatch.setattr("aipager.bot.notify._finish_sleep", _sleep)
        monkeypatch.setattr("aipager.bot.notify.detect_rtl", _detect)
        # Always pinned, never read from the live config: this patch is
        # also what overrides conftest's autouse 0.0 for this file (a
        # later monkeypatch wins). `notify` is the binding that matters —
        # it imported the float into its own namespace at import time.
        monkeypatch.setattr(
            "aipager.bot.notify.FINISH_CARD_GRACE_SECONDS", grace,
        )

        # Anything else the finish path puts on the wire (the `merged`
        # layout's combined edit) lands here instead of api.telegram.org.
        async def _post(method, payload, *, kind="blocking"):
            return {"ok": True, "result": {"message_id": 4242}}

        monkeypatch.setattr("aipager.bot.rich_message._post", _post)

        h.bot = bot
        h.sess = _sess(chat_id, h.clock, busy_msg_id=busy_msg_id,
                       busy_card_trigger=busy_card_trigger)
        return h
    return _wire


# ── (a) the head start itself ───────────────────────────────────────────

def test_card_layout_sleeps_the_grace_remainder_after_the_final_edit(
    finish, run_async,
):
    """The answer waits out what is LEFT of the grace, and waits in the
    right place: after the finished card is out, before the answer goes.

    Drop the sleep call and this fails; send the answer first and the
    order assertion fails.
    """
    h = finish(chat_id=811001, layout="card", build_cost=0.1)
    h.run(run_async, summary="A short finished answer.")

    assert h.order == ["edit_final", "sleep", "send"]
    assert len(h.sleeps) == 1
    # 0.8 grace − 0.1 s already spent building the answer text.
    expected = GRACE - 0.1
    assert h.sleeps[0] == pytest.approx(expected, abs=1e-6)
    assert "A short finished answer." in h.answers[0]


def test_the_grace_is_measured_from_the_card_edit_not_added_to_it(
    finish, run_async,
):
    """Building the answer text is charged AGAINST the grace.

    Measuring from "now" (the moment before the send) instead of from the
    card edit would sleep the full grace here — a wait the operator's
    card has already served.
    """
    h = finish(chat_id=811002, layout="card", build_cost=0.5)
    h.run(run_async, summary="Answer.")

    assert h.sleeps[0] == pytest.approx(
        GRACE - 0.5, abs=1e-6,
    )


# ── (b) the grace is a floor, never an addition ─────────────────────────

def test_no_sleep_when_building_already_outlasted_the_grace(finish, run_async):
    """A turn whose answer text took longer than the grace to build has
    already given the card its head start — it must not wait again.

    This is the test that fails if the remainder is ever measured from
    "now" instead of from the card edit.
    """
    h = finish(chat_id=811003, layout="card", build_cost=1.0)
    h.run(run_async, summary="Answer.")

    assert h.sleeps == []
    assert h.order == ["edit_final", "send"]


# ── (c) the off switch ──────────────────────────────────────────────────

def test_zero_grace_disables_the_wait(finish, run_async):
    """FINISH_CARD_GRACE_SECONDS=0 restores the old same-second
    behaviour — and still delivers the answer."""
    h = finish(chat_id=811004, layout="card", grace=0.0, build_cost=0.0)
    h.run(run_async, summary="Answer.")

    assert h.sleeps == []
    assert h.order == ["edit_final", "send"]
    assert h.answers


def test_shipped_default_grace_is_eight_hundred_milliseconds():
    """Sub-second: long enough that the phone draws the edit first, short
    enough that nobody reads the answer as slow.

    Asserted against the DECLARED default in the source, not the live
    constant. The live one answers "what is this machine configured to
    do" — conftest's autouse pin sets it to 0, and an operator with
    FINISH_CARD_GRACE_SECONDS in their environment or config.env would
    make a test of the shipped default pass or fail for reasons that have
    nothing to do with what aipager ships.
    """
    src = pathlib.Path(config.__file__).read_text(encoding="utf-8")
    assert 'os.environ.get("FINISH_CARD_GRACE_SECONDS", "0.8")' in src


# ── (d) the other two layouts have no ordering to fix ───────────────────

def test_merged_layout_never_sleeps(finish, run_async):
    """`merged` edits the answer INTO the card — one message, so there is
    no second message that could outrun it.

    Honest note on what this test can and cannot catch (the project has
    documented dozens of tests that passed for unrelated reasons):
    stamping the card time in every layout does NOT make this test fail,
    because merged's delivery never reaches the grace site at all — it is
    already gone by then. What it does catch is the grace being MOVED
    ahead of the delivery section, which is the realistic way merged
    would start waiting for a second message it never sends (verified:
    stamp-everywhere + relocate the grace above `merged_delivered` fails
    exactly this test)."""
    h = finish(chat_id=811005, layout="merged", build_cost=0.0)
    h.run(run_async, summary="Answer.")

    # Nothing on this path reaches a recorder at all: the answer went out
    # as the card's own edit, so there was no separate send to delay and
    # no final render to delay it behind. Pinning the whole (empty) order
    # rather than just `sleeps` is what makes the docstring's reasoning
    # falsifiable — if the harness ever stopped delivering through the
    # merged path, a "send" would show up here and this would fail
    # instead of quietly proving nothing (`sess.busy_msg_id is None`
    # cannot: notify.py nulls it on the merged fallback path too).
    assert h.order == []
    assert h.sleeps == []


def test_replace_layout_never_sleeps(finish, run_async):
    """`replace` deletes the card — there is no finished card left for
    the answer to follow."""
    h = finish(chat_id=811006, layout="replace", build_cost=0.0)
    h.run(run_async, summary="Answer.")

    assert h.sleeps == []
    assert h.order == ["send"]
    h.bot._app.bot.delete_message.assert_awaited_once()


# ── (e) nothing to wait for ─────────────────────────────────────────────

def test_failed_final_card_edit_gets_no_grace_and_still_answers(
    finish, run_async,
):
    """A final render that did not land leaves no finished card, so
    holding the answer back for one would only delay it."""
    h = finish(chat_id=811007, layout="card", edit_result=None,
               build_cost=0.0)
    h.run(run_async, summary="Answer.")

    assert h.sleeps == []
    assert h.order == ["edit_final", "send"]
    assert h.answers


def test_raising_final_card_edit_gets_no_grace_and_still_answers(
    finish, run_async,
):
    """Same when the render raises rather than returning falsey."""
    h = finish(chat_id=811008, layout="card", edit_raises=True,
               build_cost=0.0)
    h.run(run_async, summary="Answer.")

    assert h.sleeps == []
    assert h.order == ["edit_final", "send"]
    assert h.answers


def test_no_busy_card_means_no_grace(finish, run_async):
    """A turn that never had a card (no card to read as lagging) sends
    its answer straight away."""
    h = finish(chat_id=811009, layout="card", busy_msg_id=0, build_cost=0.0)
    h.run(run_async, summary="Answer.")

    assert h.sleeps == []
    assert h.order == ["send"]


# ── (f) the re-anchored card is a rendered card too ─────────────────────

def test_reanchored_final_card_still_gets_its_head_start(finish, run_async):
    """When the card was re-anchored to the message this turn actually
    consumed, `_reanchor_busy_card` has ALREADY rendered the finished
    card, so the ordinary final edit is skipped — but a finished card is
    just as much out on the wire, and the answer owes it the same head
    start."""
    h = finish(chat_id=811010, layout="card", busy_card_trigger=5,
               build_cost=0.1)
    h.run(run_async, summary="Answer.")

    assert "edit_final" not in h.order  # the re-anchor render replaced it
    assert h.order == ["reanchor", "sleep", "send"]
    assert h.sleeps[0] == pytest.approx(
        GRACE - 0.1, abs=1e-6,
    )


# ── (g) the grace is served once per finish, never twice ────────────────

def test_plain_text_fallback_sleeps_exactly_once(finish, run_async):
    """The fallback follows a FAILED rich send, by which point the head
    start has already elapsed — a second wait there would delay the
    answer twice over for one card."""
    h = finish(chat_id=811011, layout="card", build_cost=0.1,
               send_raises=RichMessageFallbackRequired("no numeric chat id"))
    h.run(run_async, summary="Answer that must survive the fallback.")

    assert len(h.sleeps) == 1
    assert h.order == ["edit_final", "sleep", "send"]
    # The answer still reached the chat, as plain text.
    fallback = [c.args[1] for c in h.bot._app.bot.send_message.await_args_list]
    assert any("Answer that must survive the fallback." in t for t in fallback)
