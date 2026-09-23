"""Tester-iter3-001 / rev-iter3-002: a ban answered to ANY chat-scoped PTB
call mutes the chat.

Until iteration 4 only three callers armed the mute: ``transport
._send_with_retry``, ``rich_message._ban_if_excessive`` and the typing
bubble's ``_send_typing``. A ban-sized ``RetryAfter`` answered to any other
PTB call (a plain ``editMessageText``, a pin, the pinned dashboard's edit,
a callback edit) reached only the limiter's ``note_ban``, which dropped the
rate and armed nothing. The calls queued behind it then went out into the
ban, and the turn's answer was sent instead of held.

The limiter's own ban branch (``BudgetRateLimiter._run``) now arms
``MUTE.mute`` for every chat-scoped call. These rows drive the real
limiter, the real ``transport.edit_text_at`` wrapper, the real ``notify``
and the real ``rich_message._post`` on the harness's virtual loop.
"""

from __future__ import annotations

import asyncio
import logging

import pytest
from telegram.error import RetryAfter

from aipager.bot import transport
from aipager.bot.flood import MUTE, FloodMuted
from aipager.bot.held import HELD
from aipager.state import Status

CHAT = 256113222
BAN = 25429
LONGER = 80000


def _session(bot, vloop, label: str):
    sess = bot.registry.get_or_create(f"claude-{label}")
    sess.label = label
    sess.status = Status.BUSY
    sess.scope_chat_id = CHAT
    sess.scope_kind = "dm"
    sess.busy_msg_id = 70
    sess.busy_started_at = vloop.time() - 300.0
    sess.last_tool_edit_at = vloop.time()
    return sess


def _slow_ban(vbot, vloop, endpoint: str, *, seconds: int = BAN):
    """A PTB method routed through the limiter under *endpoint*. It is
    admitted, stays in flight for a second, and is then answered with a
    ban of *seconds*."""
    limiter = vbot._limiter

    async def _method(*args, rate_limit_args=None, **kwargs):
        chat_id = kwargs.get("chat_id", args[0] if args else None)

        async def _call():
            vbot.calls.append((endpoint, chat_id, vloop.time()))
            await asyncio.sleep(1.0)            # in flight while calls queue
            raise RetryAfter(seconds)

        return await limiter.process_request(
            callback=_call, args=(), kwargs={}, endpoint=endpoint,
            data={"chat_id": chat_id}, rate_limit_args=rate_limit_args)

    return _method


async def _plain_edit(vbot):
    # The real wrapper every plain edit goes through. It has no ban arm of
    # its own: a `RetryAfter` propagates to the caller.
    return await transport.edit_text_at(
        vbot, text="a plain edit", chat_id=CHAT, message_id=555)


async def _pin(vbot):
    return await vbot.pin_chat_message(chat_id=CHAT, message_id=556)


CARRIERS = {
    "editMessageText": ("edit_message_text", _plain_edit),
    "pinChatMessage": ("pin_chat_message", _pin),
}


@pytest.mark.parametrize("endpoint", sorted(CARRIERS))
def test_a_ban_on_a_plain_ptb_call_mutes_the_chat_and_refuses_the_queue(
    mk_bot, vbot, vloop, volume_telegram, endpoint,
):
    """A plain PTB ``editMessageText`` (through ``transport.edit_text_at``)
    or a ``pinChatMessage`` is in flight for a second and Telegram answers
    it with vm3's 25,429 s ban. Meanwhile two ``sendMessage`` calls have
    drained the bucket and gone out (they were on the wire before the ban
    was known), and a third ``sendMessage`` plus the turn's answer (the
    real ``notify`` over the real ``rich_message._post``) are queued in the
    limiter. Neither may reach Telegram once the ban is back: the
    ``sendMessage`` is refused with ``FloodMuted``, the answer is HELD, and
    it is delivered once the ban lifts.

    Mutation: drop the ``MUTE.mute(...)`` from ``_run``'s ban branch and
    the chat is not muted, the queued calls go out into the ban and the
    answer is sent.
    """
    attr, carrier = CARRIERS[endpoint]
    bot = mk_bot()
    bot._app.bot = vbot
    sess = _session(bot, vloop, "vm3")
    limiter = vbot._limiter
    setattr(vbot, attr, _slow_ban(vbot, vloop, endpoint))
    out: dict = {"refused": 0, "sent": 0}

    async def _carrier():
        try:
            await carrier(vbot)
        except RetryAfter:
            out["carrier_banned"] = True

    async def _message(text):
        try:
            await vbot.send_message(chat_id=CHAT, text=text)
            out["sent"] += 1
        except FloodMuted:
            out["refused"] += 1

    async def _answer():
        sess.status = Status.IDLE
        await bot.notify(sess, "idle_prompt",
                         {"summary": "the answer the ban must not eat"})

    async def main():
        ban = asyncio.ensure_future(_carrier())
        await asyncio.sleep(0.1)
        early = [asyncio.ensure_future(_message(f"early {i}")) for i in range(2)]
        await asyncio.sleep(0)
        queued = [asyncio.ensure_future(_message("queued")),
                  asyncio.ensure_future(_answer())]
        await asyncio.sleep(0.2)
        out["waiting"] = len(limiter._budgets[CHAT].waiters)
        await ban
        out["banned_at"] = vloop.time()
        out["muted"] = MUTE.remaining(CHAT) > BAN - 5
        await asyncio.gather(*early, *queued)
        await asyncio.sleep(120.0)              # well past any floor-rate wait
        out["held"] = HELD.count(CHAT)
        out["vbot_in_ban"] = [c for c in vbot.calls if c[2] >= out["banned_at"]]
        out["rich_in_ban"] = list(volume_telegram.calls)
        await asyncio.sleep(BAN + 1.0)          # the ban lifts
        out["delivered"] = await bot.flush_held_answers(sess)

    vloop.run_until_complete(main())
    assert out.get("carrier_banned") is True
    assert [c[0] for c in vbot.calls][:1] == [endpoint]
    assert out["waiting"] >= 2, "the calls were not queued when the ban landed"
    assert out["muted"] is True, f"a ban on {endpoint} did not mute the chat"
    assert out["vbot_in_ban"] == [], "a queued call went into the ban"
    assert out["rich_in_ban"] == [], "the queued answer went into the ban"
    assert out["sent"] == 2 and out["refused"] == 1, out
    assert out["held"] == 1, "the queued answer was not held"
    assert out["delivered"] == 1
    assert [c[0] for c in volume_telegram.calls] == ["sendRichMessage"]
    assert HELD.count(CHAT) == 0


def test_a_bubble_ban_armed_by_the_limiter_and_the_caller_is_one_mute(
    mk_bot, vbot, vloop, caplog,
):
    """The typing bubble's ``_send_typing`` still arms the mute itself, a
    moment after the limiter's ban branch armed it. That second arming is
    the SAME ban: one WARNING, and no "extended" line, which would mean the
    deadline was rewritten, the signal file written again and the limiter
    told again.

    Mutation: drop ``_SAME_BAN_SLACK`` from ``FloodMute.mute``'s
    comparison and the caller's arming reads as an extension.
    """
    bot = mk_bot()
    bot._app.bot = vbot
    sess = _session(bot, vloop, "vm3")
    vbot.fail["sendChatAction"] = lambda: RetryAfter(BAN)
    caplog.set_level(logging.DEBUG, logger="aipager.bot.flood")

    async def main():
        chat = bot._typing_chat(sess)
        assert chat is not None
        await bot._send_typing(sess, chat)

    vloop.run_until_complete(main())
    flood_lines = [r.getMessage() for r in caplog.records
                   if r.name == "aipager.bot.flood"]
    assert MUTE.is_muted(CHAT)
    assert sum("muted for" in m for m in flood_lines) == 1, flood_lines
    assert not [m for m in flood_lines if "extended" in m], flood_lines


def test_a_ptb_ban_never_shortens_a_longer_mute(mk_bot, vbot, vloop):
    """A chat already muted for longer than the new ban keeps the longer
    mute when the limiter arms the shorter one.

    Mutation: let ``FloodMute.mute`` overwrite an existing entry whatever
    its deadline and the mute drops to 25,429 s.
    """
    vbot.edit_message_text = _slow_ban(vbot, vloop, "editMessageText")

    async def main():
        # The ban carrier is admitted only while the chat is not muted, so
        # the longer mute lands while it is already in flight.
        call = asyncio.ensure_future(_plain_edit(vbot))
        await asyncio.sleep(0.1)
        MUTE.mute(CHAT, LONGER, source="elsewhere")
        with pytest.raises(RetryAfter):
            await call

    vloop.run_until_complete(main())
    assert MUTE.remaining(CHAT) > LONGER - 5


def test_the_same_ban_armed_again_a_moment_later_is_not_an_extension(
    vloop, caplog,
):
    """On a real clock the caller's arming lands a moment after the
    limiter's (the virtual loop does not advance between the two, so the
    bubble row above cannot see that gap). Half a second later, the same
    ban's deadline is half a second further out: it is still the same
    ban, so the deadline in force stays and no "extended" line is logged.
    A second more than the slack IS an extension.

    Mutation: drop ``_SAME_BAN_SLACK`` from ``FloodMute.mute``'s
    comparison and the half-second re-arming extends the mute.
    """
    caplog.set_level(logging.DEBUG, logger="aipager.bot.flood")

    async def main():
        MUTE.mute(CHAT, BAN, source="editMessageText")
        first = MUTE.remaining(CHAT)
        await asyncio.sleep(0.5)
        MUTE.mute(CHAT, BAN, source="sendMessage")
        out = {"same": MUTE.remaining(CHAT), "first": first}
        await asyncio.sleep(1.0)
        MUTE.mute(CHAT, BAN, source="sendMessage")
        out["later"] = MUTE.remaining(CHAT)
        return out

    out = vloop.run_until_complete(main())
    assert out["same"] == pytest.approx(out["first"] - 0.5)
    assert out["later"] == pytest.approx(BAN)
    lines = [r.getMessage() for r in caplog.records
             if r.name == "aipager.bot.flood"]
    assert sum("muted for" in m for m in lines) == 1, lines
    assert sum("extended" in m for m in lines) == 1, lines
