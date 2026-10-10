"""Black-box, fix iteration 2: an owner's default (BotFather) description means
aipager sets no per-language description of that kind (phase 5 item 4;
spec "only while the owner has not written their own in BotFather";
entrypoints.md ``bot_descriptions.sync_descriptions``: "before a set it reads
the default (no ``language_code``) once per kind; a non-empty default makes
every language of that kind 'owner'").

Driven through the daemon's own client against ``tests/e2e/fake_telegram``'s
server on 127.0.0.1 (the same wiring as ``test_p5_bot_descriptions.py``).
"BotFather writes the default" is simulated by a set with no
``language_code``.

Partitions: default description = empty / the owner's / blank after the
documented normalisation; default short description = empty / the owner's;
both; per-language text = empty / aipager's older one by hash; the owner
removes the default later.
"""

from __future__ import annotations

import asyncio
import hashlib

import pytest

from aipager import i18n, telegram_endpoint
from aipager.bot import bot_descriptions as bd
from aipager.i18n import persist
from tests.e2e.fake_telegram.server import FakeBotApiThread

TOKEN = "7000000015:AAFakeP5OwnerDefaultTail0123456789"
OWNER_TEXT = "My own bot, written in BotFather"
OWNER_SHORT = "My own short text"
_CLIENT = {}


@pytest.fixture
def fake(mk_bot, monkeypatch):
    t = FakeBotApiThread(TOKEN, 7000000015, "aipager_p5_owner_bot")
    base = t.start()
    monkeypatch.setattr("aipager.bot.lifecycle.BOT_TOKEN", TOKEN)
    monkeypatch.setenv(telegram_endpoint.BASE_ENV, base)
    _CLIENT["mk"] = lambda: mk_bot()._make_builder().build().bot
    try:
        yield t.api
    finally:
        _CLIENT.clear()
        t.stop()


async def _with_client(fn):
    bot = _CLIENT["mk"]()
    await bot.initialize()
    try:
        return await fn(bot)
    finally:
        await bot.shutdown()


def sync():
    return asyncio.run(_with_client(bd.sync_descriptions))


def botfather(*, description=None, short=None, language_code=None):
    """The owner writes (or clears, with "") a description in BotFather."""
    async def go(bot):
        kw = {"language_code": language_code} if language_code else {}
        if description is not None:
            await bot.set_my_description(description, **kw)
        if short is not None:
            await bot.set_my_short_description(short, **kw)
    asyncio.run(_with_client(go))


def set_calls(api, method):
    return [c for c in api.calls(method=method) if c.params.get("language_code")]


def default_reads(api, method):
    return [c for c in api.calls(method=method) if not c.params.get("language_code")]


def _desc_calls(api):
    return [c for c in api.calls() if "Description" in c.method]


def want(code, kind):
    return i18n.get(code).text(bd.DESCRIPTION if kind == "description" else bd.SHORT)


def h16(text):
    norm = text.strip().replace("\u200e", "").replace("\u200f", "").replace("\u061c", "")
    return hashlib.sha256(norm.strip().encode("utf-8")).hexdigest()[:16]


# ---- an owner default description -------------------------------------------------------------

def test_an_owner_default_description_means_no_per_language_description(fake):
    botfather(description=OWNER_TEXT)
    sync()
    assert set_calls(fake, "setMyDescription") == []


def test_an_owner_default_description_is_reported_owner_for_every_language(fake):
    botfather(description=OWNER_TEXT)
    out = sync()
    assert {v["description"] for v in out.values()} == {"owner"}


def test_an_owner_default_description_covers_all_fourteen(fake, p5):
    botfather(description=OWNER_TEXT)
    out = sync()
    assert sorted(out) == sorted(p5.TELEGRAM_CODES)


def test_an_owner_default_description_records_no_hash(fake, p5):
    botfather(description=OWNER_TEXT)
    sync()
    assert [tg for tg in p5.TELEGRAM_CODES
            if persist.description_hash(tg, "description") is not None] == []


def test_an_owner_default_description_stays_the_owners(fake):
    botfather(description=OWNER_TEXT)
    sync()
    assert fake.description("") == OWNER_TEXT


@pytest.mark.parametrize("tg", ["fa", "zh", "pt", "ru"])
def test_readers_of_a_language_keep_seeing_the_owners_default(fake, tg):
    botfather(description=OWNER_TEXT)
    sync()
    assert fake.description(tg) == ""          # empty per-language -> Telegram shows the default


def test_an_owner_default_description_does_not_stop_the_short_ones(fake):
    botfather(description=OWNER_TEXT)
    out = sync()
    assert {v["short"] for v in out.values()} == {"set"}


def test_with_an_owner_default_description_the_short_text_is_the_catalogs(fake):
    botfather(description=OWNER_TEXT)
    sync()
    assert fake.description("zh", short=True) == want("zh-Hans", "short")


# ---- an owner default short description ----------------------------------------------------------

def test_an_owner_default_short_description_means_no_per_language_short_one(fake):
    botfather(short=OWNER_SHORT)
    sync()
    assert set_calls(fake, "setMyShortDescription") == []


def test_an_owner_default_short_description_is_reported_owner(fake):
    botfather(short=OWNER_SHORT)
    out = sync()
    assert {v["short"] for v in out.values()} == {"owner"}


def test_an_owner_default_short_description_does_not_stop_the_long_ones(fake):
    botfather(short=OWNER_SHORT)
    sync()
    assert fake.description("fa") == want("fa", "description")


def test_both_owner_defaults_mean_no_set_at_all(fake):
    botfather(description=OWNER_TEXT, short=OWNER_SHORT)
    sync()
    assert (set_calls(fake, "setMyDescription") + set_calls(fake, "setMyShortDescription")) == []


# ---- aipager's older text and the owner's default --------------------------------------------------

def test_with_an_owner_default_aipagers_older_text_is_left_as_it_is(fake):
    old = "aipager old German description"
    botfather(description=old, language_code="de")        # what aipager set long ago
    persist.record_description("de", "description", h16(old))
    botfather(description=OWNER_TEXT)
    sync()
    assert fake.description("de") == old


def test_with_an_owner_default_aipagers_older_text_is_reported_owner(fake):
    old = "aipager old German description"
    botfather(description=old, language_code="de")
    persist.record_description("de", "description", h16(old))
    botfather(description=OWNER_TEXT)
    assert sync()["de"]["description"] == "owner"


# ---- boundaries: a blank default, a removed default -------------------------------------------------

def test_without_any_default_every_language_is_set(fake):
    """Control for the tests above: the same sync with no owner default sets."""
    sync()
    assert len(set_calls(fake, "setMyDescription")) == 14


@pytest.mark.parametrize("blank", ["   ", "\u200f", " \u200e\n"])
def test_a_default_that_is_blank_after_normalisation_is_not_the_owners(fake, blank):
    botfather(description=blank)
    out = sync()
    assert {v["description"] for v in out.values()} == {"set"}


def test_once_the_owner_removes_the_default_the_next_start_sets(fake):
    botfather(description=OWNER_TEXT)
    sync()
    botfather(description="")
    sync()
    assert fake.description("ru") == want("ru", "description")


# ---- the default is read, not hammered -----------------------------------------------------------

@pytest.mark.parametrize("method", ["getMyDescription", "getMyShortDescription"])
def test_the_default_is_read_once_per_kind_with_no_owner_text(fake, method):
    sync()
    assert len(default_reads(fake, method)) == 1


@pytest.mark.parametrize("method", ["getMyDescription", "getMyShortDescription"])
def test_the_default_is_read_once_per_kind_with_owner_text(fake, method):
    botfather(description=OWNER_TEXT, short=OWNER_SHORT)
    before = len(default_reads(fake, method))
    sync()
    assert len(default_reads(fake, method)) - before == 1


def test_a_second_start_after_a_full_set_still_makes_no_call(fake):
    sync()
    before = len(_desc_calls(fake))
    sync()
    assert len(_desc_calls(fake)) == before
