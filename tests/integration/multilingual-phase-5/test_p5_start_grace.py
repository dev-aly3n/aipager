"""Black-box, fix iteration 2: the first-contact line (and the offer) still
arrives for a message sent right after start (phase 5 item 3; entrypoints.md
``language_launch.note_started(bot)``: "called by ``start()`` once polling
runs; each step then waits until ``START_GRACE`` (10 s) after it before its
send"; ``_sleep`` and ``_clock`` are the seams to replace in tests).

Two levels:
- the seams: a fake clock and a fake sleep show WHEN the send happens
  relative to ``note_started``, without waiting in real time;
- end to end: a real ``TelegramBot.start()`` against the fake Bot API
  (``tests/e2e/fake_telegram``, 127.0.0.1 only), a message at once, and the
  line comes after the reply (in real time, up to the 10 s grace).

Partitions: message time after start = 0 s / inside the grace / exactly at
its end / after it / start never noted; step = first-contact line / offer;
the reply = not delayed.
"""

from __future__ import annotations

import asyncio
import re
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager import i18n, telegram_endpoint
from aipager import preferences as prefs
from aipager.bot import bot_descriptions as bd
from aipager.bot import language_launch, language_menu
from aipager.i18n import persist
from tests.e2e.fake_telegram.server import FakeBotApiThread
from tests.i18n_ml2_bb import message

OWNER, BOB = 555, 2
T0 = 1000.0


class _Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def clock(monkeypatch):
    """A fake clock; the fake sleep advances it and never waits."""
    c = _Clock(T0)
    c.slept = []

    async def _sleep(seconds):
        c.slept.append(seconds)
        c.t += max(0.0, float(seconds))
        await asyncio.sleep(0)
    monkeypatch.setattr(language_launch, "_clock", c)
    monkeypatch.setattr(language_launch, "_sleep", _sleep)
    monkeypatch.setattr(language_launch, "SWEEP_DELAY", 0.0)
    return c


@pytest.fixture
def stamped(bot, clock):
    """The bot's sends record the fake time at which they happen."""
    bot.sent_at = []

    async def _send(*_a, **_kw):
        bot.sent_at.append(clock.t)
        return MagicMock(message_id=4242)
    bot._app.bot.send_message = AsyncMock(side_effect=_send)
    return bot


def _upd(user, code):
    return message("hi", chat=user, user=user, language_code=code)


def _started_at(bot, clock, t):
    clock.t = t
    language_launch.note_started(bot)


# ---- the constant ----------------------------------------------------------------------------

def test_the_grace_is_ten_seconds():
    assert language_launch.START_GRACE == 10.0


# ---- the first-contact line ------------------------------------------------------------------

def test_a_message_at_start_gets_its_line_no_earlier_than_the_grace(stamped, clock, launch, p5):
    p5.marker(OWNER)
    _started_at(stamped, clock, T0)
    launch(stamped, _upd(BOB, "fa"))
    assert stamped.sent_at and stamped.sent_at[0] >= T0 + language_launch.START_GRACE


def test_a_message_at_start_still_gets_exactly_one_line(stamped, clock, launch, p5):
    p5.marker(OWNER)
    _started_at(stamped, clock, T0)
    launch(stamped, _upd(BOB, "fa"))
    assert len(stamped.sent_at) == 1


def test_a_message_three_seconds_in_waits_the_rest(stamped, clock, launch, p5):
    p5.marker(OWNER)
    _started_at(stamped, clock, T0)
    clock.t = T0 + 3.0
    launch(stamped, _upd(BOB, "fa"))
    assert stamped.sent_at[0] == pytest.approx(T0 + 10.0, abs=0.5)


def test_the_wait_is_not_much_longer_than_the_grace(stamped, clock, launch, p5):
    p5.marker(OWNER)
    _started_at(stamped, clock, T0)
    launch(stamped, _upd(BOB, "fa"))
    assert stamped.sent_at[0] <= T0 + language_launch.START_GRACE + 1.0


def test_a_message_after_the_grace_sends_at_once(stamped, clock, launch, p5):
    p5.marker(OWNER)
    _started_at(stamped, clock, T0)
    clock.t = T0 + 25.0
    launch(stamped, _upd(BOB, "fa"))
    assert stamped.sent_at == [T0 + 25.0]


def test_a_message_exactly_at_the_end_of_the_grace_sends_at_once(stamped, clock, launch, p5):
    p5.marker(OWNER)
    _started_at(stamped, clock, T0)
    clock.t = T0 + language_launch.START_GRACE
    launch(stamped, _upd(BOB, "fa"))
    assert stamped.sent_at == [T0 + language_launch.START_GRACE]


def test_a_message_just_inside_the_grace_still_waits(stamped, clock, launch, p5):
    p5.marker(OWNER)
    _started_at(stamped, clock, T0)
    clock.t = T0 + language_launch.START_GRACE - 0.5
    launch(stamped, _upd(BOB, "fa"))
    assert stamped.sent_at[0] >= T0 + language_launch.START_GRACE


# ---- the offer waits too ------------------------------------------------------------------------

def test_an_offer_at_start_waits_out_the_grace(stamped, clock, launch, p5):
    p5.marker(OWNER)
    _started_at(stamped, clock, T0)
    launch(stamped, _upd(OWNER, "fa"))
    assert stamped.sent_at and stamped.sent_at[0] >= T0 + language_launch.START_GRACE


# ---- the reply is never held back ------------------------------------------------------------

def test_the_post_reply_hook_returns_before_the_grace_ends(bot, monkeypatch, run_async, p5):
    """A sleep that never ends: ``after_update`` must still return at once
    (its work is background), and nothing is sent yet."""
    p5.marker(OWNER)
    clock = _Clock(T0)
    monkeypatch.setattr(language_launch, "_clock", clock)

    async def _forever(_seconds):
        await asyncio.Event().wait()
    monkeypatch.setattr(language_launch, "_sleep", _forever)
    language_launch.note_started(bot)

    async def go():
        await asyncio.wait_for(
            language_launch.after_update(bot, _upd(BOB, "fa"), MagicMock()), 2)
        sent = bot._app.bot.send_message.await_count
        for t in list(asyncio.all_tasks()):
            if t is not asyncio.current_task():
                t.cancel()
        return sent
    assert run_async(go()) == 0


# ---- end to end against the fake Bot API -------------------------------------------------------

TOKEN = "7000000017:AAFakeP5GraceTail0123456789abcdefgh"
CHAT = 256113222        # the suite's configured chat (legacy single-chat mode)


@pytest.fixture
def fake(monkeypatch):
    t = FakeBotApiThread(TOKEN, 7000000017, "aipager_p5_grace_bot")
    base = t.start()
    t.api.add_chat(CHAT, "private")
    monkeypatch.setattr("aipager.bot.lifecycle.BOT_TOKEN", TOKEN)
    monkeypatch.setenv(telegram_endpoint.BASE_ENV, base)
    monkeypatch.setattr("aipager.config.MINIAPP_ENABLED", False)
    monkeypatch.setattr(language_launch, "SWEEP_DELAY", 0.0)

    async def _forever(_seconds):
        await asyncio.Event().wait()
    monkeypatch.setattr(bd, "_sleep", _forever)      # descriptions stay out of the way
    try:
        yield t.api
    finally:
        t.stop()


def _fc_text(code):
    return re.sub(r"<[^>]+>", "", str(language_menu.first_contact_text(i18n.get(code))))


def _talk_at_once(api, code, predicate):
    """Start a real bot, send one message immediately, wait (up to 30 s) for
    a sendMessage matching *predicate*; return the chat's sendMessage calls."""
    from aipager.bot import TelegramBot
    from aipager.state import SessionRegistry

    async def go():
        bot = TelegramBot(SessionRegistry())
        await asyncio.wait_for(bot.start(), 30)
        try:
            sender = api.user(CHAT, "owner", "Owner")
            sender["language_code"] = code
            since = api.calls()[-1].seq if api.calls() else 0
            await asyncio.to_thread(api.inject_text, CHAT, sender, "/help")
            try:
                await asyncio.to_thread(
                    api.wait_for, lambda c: c.method == "sendMessage" and c.chat_id == CHAT
                    and predicate(c), 30, since=since)
            except Exception:
                pass
            await asyncio.sleep(0.5)
            return list(api.calls(method="sendMessage", chat_id=CHAT, since=since))
        finally:
            await asyncio.wait_for(bot.stop(), 30)
    return asyncio.run(go())


def test_a_message_right_after_start_gets_the_first_contact_line(fake):
    assert persist.note_prior_run() is False
    sent = _talk_at_once(fake, "fa", lambda c: c.text == _fc_text("fa"))
    assert [c.text for c in sent].count(_fc_text("fa")) == 1


def test_right_after_start_the_line_comes_after_the_reply(fake):
    assert persist.note_prior_run() is False
    sent = _talk_at_once(fake, "fa", lambda c: c.text == _fc_text("fa"))
    texts = [c.text for c in sent]
    assert len(texts) >= 2 and texts[0] != _fc_text("fa") and texts[-1] == _fc_text("fa")


def test_a_message_right_after_start_on_an_upgrade_gets_the_offer(fake):
    prefs.set_ui_language(9001, "en")          # a preferences file: a prior run
    persist.note_prior_run()
    sent = _talk_at_once(fake, "fa",
                         lambda c: "lang_offer" in str(c.params.get("reply_markup")))
    assert ["lang_offer" in str(c.params.get("reply_markup")) for c in sent].count(True) == 1
