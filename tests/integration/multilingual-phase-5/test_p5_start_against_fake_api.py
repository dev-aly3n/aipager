"""Black-box, end to end: ``TelegramBot.start()`` / ``stop()`` against the fake
Bot API (``tests/e2e/fake_telegram``, 127.0.0.1 only).

design.md success criteria: item 4 "``start()`` returns while the sync is
still pending"; item 3 "the marker is written ... before polling starts" and
"a new private chat gets exactly one first-contact message after its reply";
the existing owner's offer card; entrypoints.md "start never waits for
descriptions or offers; stop cancels them" and "a fake-Telegram daemon run
never lists them in ``unknown_methods``".

The bot runs in legacy single-chat mode: the configured chat 222333444
(pinned by the suite's conftest) is the one served private chat. The
description task's wait goes through the documented ``bot_descriptions._sleep``
seam; asyncio itself is never patched.
"""

from __future__ import annotations

import asyncio

import pytest

from aipager import i18n, telegram_endpoint
from aipager import preferences as prefs
from aipager.bot import bot_descriptions as bd
from aipager.bot import language_launch, language_menu
from aipager.i18n import persist
from tests.e2e.fake_telegram.server import FakeBotApiThread

TOKEN = "7000000007:AAFakeP5StartTail0123456789abcdefg"
CHAT = 222333444
DESC = ("getMyDescription", "setMyDescription", "getMyShortDescription",
        "setMyShortDescription")


@pytest.fixture
def fake(monkeypatch):
    t = FakeBotApiThread(TOKEN, 7000000007, "aipager_p5_start_bot")
    base = t.start()
    t.api.add_chat(CHAT, "private")
    monkeypatch.setattr("aipager.bot.lifecycle.BOT_TOKEN", TOKEN)
    monkeypatch.setenv(telegram_endpoint.BASE_ENV, base)
    monkeypatch.setattr("aipager.config.MINIAPP_ENABLED", False)
    monkeypatch.setattr(language_launch, "SWEEP_DELAY", 0.0)
    try:
        yield t.api
    finally:
        t.stop()


@pytest.fixture
def blocked_descriptions(monkeypatch):
    """The description task's start delay never ends; ``entered`` is set
    once the task is waiting."""
    state = {"entered": False}

    async def _forever(_seconds):
        state["entered"] = True
        await asyncio.Event().wait()
    monkeypatch.setattr(bd, "_sleep", _forever)
    return state


@pytest.fixture
def quick_descriptions(monkeypatch):
    async def _now(_seconds):
        return None
    monkeypatch.setattr(bd, "_sleep", _now)


def run(scenario, *, linger=0.0):
    """Start a real TelegramBot, run *scenario(bot)*, always stop it; keep the
    loop alive *linger* seconds after the stop."""
    from aipager.bot import TelegramBot
    from aipager.state import SessionRegistry

    async def go():
        bot = TelegramBot(SessionRegistry())
        await asyncio.wait_for(bot.start(), 30)
        try:
            return await scenario(bot)
        finally:
            await asyncio.wait_for(bot.stop(), 30)
            if linger:
                await asyncio.sleep(linger)
    return asyncio.run(go())


def desc_calls(api):
    return [c for c in api.calls() if c.method in DESC]


# ---- start never waits for the descriptions ---------------------------------------------

def test_start_returns_while_the_description_sync_is_pending(fake, blocked_descriptions):
    async def scenario(bot):
        for _ in range(100):
            if blocked_descriptions["entered"]:
                break
            await asyncio.sleep(0.02)
        return blocked_descriptions["entered"]
    assert run(scenario) is True


def test_no_description_call_before_the_start_delay_ends(fake, blocked_descriptions):
    async def scenario(bot):
        await asyncio.sleep(0.3)
    run(scenario)
    assert desc_calls(fake) == []


def test_the_descriptions_are_set_after_start(fake, quick_descriptions):
    async def scenario(bot):
        await asyncio.to_thread(
            fake.wait_for, lambda c: c.method == "setMyShortDescription", 30)
    run(scenario)
    assert desc_calls(fake) != []


def test_a_daemon_run_never_meets_an_unknown_method(fake, quick_descriptions):
    async def scenario(bot):
        await asyncio.to_thread(
            fake.wait_for, lambda c: c.method == "setMyShortDescription", 30)
    run(scenario)
    assert fake.unknown_methods == []


def test_stop_cancels_the_pending_description_sync(fake, monkeypatch):
    """The start delay is short here (0.3 s, through the seam). The bot stops
    before it ends and the loop then lives on for 1.5 s: a task stop() left
    running would reach the fake in that time."""
    async def _short(_seconds):
        await asyncio.sleep(0.3)
    monkeypatch.setattr(bd, "_sleep", _short)

    async def scenario(bot):
        return None
    run(scenario, linger=1.5)
    assert desc_calls(fake) == []


def test_the_linger_window_is_long_enough_to_see_a_sync(fake, monkeypatch):
    """Control for the test above: without stop() inside the window, the same
    short delay does reach the fake."""
    async def _short(_seconds):
        await asyncio.sleep(0.3)
    monkeypatch.setattr(bd, "_sleep", _short)

    async def scenario(bot):
        await asyncio.sleep(1.5)
    run(scenario)
    assert desc_calls(fake) != []


# ---- the marker exists once start returns -----------------------------------------------

def test_start_writes_the_launch_marker(fake, blocked_descriptions):
    async def scenario(bot):
        return persist.launch_marker()
    assert run(scenario) is not None


def test_start_on_a_fresh_machine_writes_a_fresh_marker(fake, blocked_descriptions):
    assert persist.note_prior_run() is False

    async def scenario(bot):
        return persist.launch_marker()
    assert run(scenario)["fresh"] is True


def test_start_after_a_prior_run_counts_the_configured_chat(fake, blocked_descriptions):
    prefs.set_ui_language(9001, "en")
    persist.note_prior_run()

    async def scenario(bot):
        return persist.launch_marker()
    assert CHAT in run(scenario)["people"]


# ---- a real update: reply first, then the first-contact line or the offer ----------------

def _person(api, code):
    sender = api.user(CHAT, "owner", "Owner")
    sender["language_code"] = code
    return sender


#: Seconds between start() and the person's message. Right after start the
#: start-up sends use the outbound budget, and a skip-kind ORNAMENT send is
#: then dropped (and not recorded, so it comes on a later message); measured:
#: 0 s drops it, 4 s and 8 s do not.
SETTLE = 5.0


def _talk(fake, code, text="/help"):
    """Send one message from the chat's person; return the bot's sendMessage
    calls to that chat made after it, once things settle."""
    async def scenario(bot):
        await asyncio.sleep(SETTLE)
        since = fake.calls()[-1].seq if fake.calls() else 0
        await asyncio.to_thread(fake.inject_text, CHAT, _person(fake, code), text)
        await asyncio.to_thread(
            fake.wait_for, lambda c: c.method == "sendMessage" and c.chat_id == CHAT,
            30, since=since)
        await asyncio.sleep(1.0)
        await language_launch.drain(bot)
        return [c for c in fake.calls(method="sendMessage", chat_id=CHAT, since=since)]
    return run(scenario)


def _fc_text(code):
    import re
    return re.sub(r"<[^>]+>", "", str(language_menu.first_contact_text(i18n.get(code))))


def test_a_fresh_install_gets_the_first_contact_line_after_the_reply(fake,
                                                                     blocked_descriptions):
    assert persist.note_prior_run() is False
    sent = _talk(fake, "fa")
    texts = [c.text for c in sent]
    assert len(texts) >= 2 and texts[-1] == _fc_text("fa") and texts[0] != _fc_text("fa")


def test_a_fresh_install_gets_exactly_one_first_contact_line(fake, blocked_descriptions):
    persist.note_prior_run()
    sent = _talk(fake, "fa")
    assert [c.text for c in sent].count(_fc_text("fa")) == 1


def test_an_upgraded_install_offers_after_the_reply(fake, blocked_descriptions):
    prefs.set_ui_language(9001, "en")        # a preferences file: a prior run
    persist.note_prior_run()
    sent = _talk(fake, "fa")
    marks = ["lang_offer" in str(c.params.get("reply_markup")) for c in sent]
    assert marks[0] is False and marks.count(True) == 1 and marks[-1] is True


def test_an_upgraded_english_install_gets_neither(fake, blocked_descriptions):
    prefs.set_ui_language(9001, "en")
    persist.note_prior_run()
    sent = _talk(fake, "en")
    assert all("lang_offer" not in str(c.params.get("reply_markup"))
               and c.text != _fc_text("fa") for c in sent)


def test_the_reply_of_an_upgraded_persian_owner_stays_english(fake, blocked_descriptions):
    prefs.set_ui_language(9001, "en")
    persist.note_prior_run()
    english = _talk(fake, "en")[0].text
    assert _talk(fake, "fa")[0].text == english
