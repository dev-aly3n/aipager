"""``SessionOpsMixin._adopt_trigger`` (design.md "turn anchor follows
consumption" R1): a send while BUSY must never move ``trigger_msg_id``
— only a non-BUSY session adopts the message immediately, because for
that one message send and consumption are the same event.
"""

from __future__ import annotations

from aipager.state import Status, TrackedSession


def _sess(status):
    return TrackedSession(name="claude-jim", label="jim", status=status)


def test_adopt_trigger_sets_target_when_idle(mk_bot):
    bot = mk_bot()
    sess = _sess(Status.IDLE)

    bot._adopt_trigger(sess, 5, "hello")

    assert sess.trigger_msg_id == 5
    assert sess.last_prompt == "hello"


def test_adopt_trigger_sets_target_when_interactive(mk_bot):
    bot = mk_bot()
    sess = _sess(Status.INTERACTIVE)

    bot._adopt_trigger(sess, 5, "hello")

    assert sess.trigger_msg_id == 5


def test_adopt_trigger_noops_when_busy(mk_bot):
    bot = mk_bot()
    sess = _sess(Status.BUSY)
    sess.trigger_msg_id = 1
    sess.last_prompt = "earlier"

    bot._adopt_trigger(sess, 5, "hello")

    assert sess.trigger_msg_id == 1, "R1: a send while BUSY must not move the target"
    assert sess.last_prompt == "earlier"


def test_adopt_trigger_none_text_leaves_last_prompt_untouched(mk_bot):
    bot = mk_bot()
    sess = _sess(Status.IDLE)
    sess.last_prompt = "earlier"

    bot._adopt_trigger(sess, 5, None)

    assert sess.trigger_msg_id == 5
    assert sess.last_prompt == "earlier"


def test_adopt_trigger_gone_status_also_adopts(mk_bot):
    """Only BUSY is the no-op status — every other status behaves like
    IDLE for this purpose (send and consumption are the same event)."""
    bot = mk_bot()
    sess = _sess(Status.GONE)

    bot._adopt_trigger(sess, 5, "hello")

    assert sess.trigger_msg_id == 5
