"""Black-box: per-language bot descriptions (phase 5 item 4; design.md section 6,
D5; entrypoints.md ``bot_descriptions`` and the fake Bot API's four
description methods).

Driven through the daemon's own client (``TelegramBot._make_builder()``,
with its rate limiter, pointed at the fake through the documented
``telegram_endpoint`` override) against ``tests/e2e/fake_telegram``'s server
on 127.0.0.1; nothing reaches Telegram.

Success criteria: descriptions are set per Telegram code (zh, pt included)
only when empty or equal to aipager's own last text, and only when the
catalog text changed (zero API calls on a second start); the owner's text is
never overwritten; hashes are in languages.json; ``en``/default are never
touched; ``start()`` returns while the sync is still pending.

Partitions per language and kind: current text = empty / ours / the owner's /
equal to the catalog text / ours with Telegram's whitespace or a mark;
recorded hash = none / matching / stale; API = ok / error.
"""

from __future__ import annotations

import asyncio
import hashlib
import json

import pytest

from aipager import config, i18n, telegram_endpoint
from aipager.bot import bot_descriptions as bd
from aipager.i18n import persist
from tests.e2e.fake_telegram.server import FakeBotApiThread, errors

SECRET = "AAFakeP5DescriptionsTail0123456789xyz"
TOKEN = f"7000000005:{SECRET}"
GET_SET = ("getMyDescription", "setMyDescription", "getMyShortDescription",
           "setMyShortDescription")


_CLIENT = {}


@pytest.fixture
def fake(mk_bot, monkeypatch):
    t = FakeBotApiThread(TOKEN, 7000000005, "aipager_p5_bot")
    base = t.start()
    monkeypatch.setattr("aipager.bot.lifecycle.BOT_TOKEN", TOKEN)
    monkeypatch.setenv(telegram_endpoint.BASE_ENV, base)
    _CLIENT["mk"] = lambda: mk_bot()._make_builder().build().bot
    try:
        yield t.api, base
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


def sync(fake):
    return asyncio.run(_with_client(bd.sync_descriptions))


def calls(fake, *methods):
    api, _base = fake
    return [c for c in api.calls() if c.method in (methods or GET_SET)]


def want(code, kind):
    return i18n.get(code).text(bd.DESCRIPTION if kind == "description" else bd.SHORT)


def h16(text):
    """design.md 6: a sha256 prefix of ``norm(text)``, which strips surrounding
    whitespace and the marks U+200E, U+200F and U+061C."""
    norm = text.strip().replace("\u200e", "").replace("\u200f", "").replace("\u061c", "")
    return hashlib.sha256(norm.strip().encode("utf-8")).hexdigest()[:16]


# ---- a first start ------------------------------------------------------------------------

def test_a_first_start_sets_every_language(fake, p5):
    out = sync(fake)
    assert sorted(out) == sorted(p5.TELEGRAM_CODES)


@pytest.mark.parametrize("kind", ["description", "short"])
def test_a_first_start_reports_set_for_every_language(fake, kind):
    out = sync(fake)
    assert {v[kind] for v in out.values()} == {"set"}


@pytest.mark.parametrize("code, tg", [("fa", "fa"), ("zh-Hans", "zh"), ("pt-BR", "pt"),
                                      ("ar", "ar"), ("de", "de")])
def test_the_description_is_the_catalog_text(fake, code, tg):
    sync(fake)
    assert fake[0].description(tg) == want(code, "description")


@pytest.mark.parametrize("code, tg", [("ru", "ru"), ("zh-Hans", "zh"), ("pt-BR", "pt")])
def test_the_short_description_is_the_catalog_text(fake, code, tg):
    sync(fake)
    assert fake[0].description(tg, short=True) == want(code, "short")


def test_the_catalog_texts_differ_from_english(fake):
    """Guard against a vacuous pass: the Persian text is a translation."""
    assert want("fa", "description") != want("en", "description")


@pytest.mark.parametrize("tg", ["zh-Hans", "pt-BR", "zh_Hans", "pt_BR"])
def test_no_call_uses_a_long_tag(fake, tg):
    sync(fake)
    assert [c for c in calls(fake) if c.params.get("language_code") == tg] == []


def test_the_default_description_is_never_touched(fake):
    # Fix iteration 2 (review rev-iter1-003): the default is now READ, once
    # per kind, to see whether the owner wrote one; it is still never set.
    sync(fake)
    assert [c for c in calls(fake, "setMyDescription", "setMyShortDescription")
            if not c.params.get("language_code")] == []


def test_english_is_never_touched(fake):
    sync(fake)
    assert [c for c in calls(fake) if c.params.get("language_code") == "en"] == []


def test_english_readers_see_no_description(fake):
    sync(fake)
    assert (fake[0].description(""), fake[0].description("en")) == ("", "")


def test_the_hashes_are_in_the_language_state_file(fake):
    sync(fake)
    doc = json.loads(config.LANGUAGE_STATE_FILE.read_text(encoding="utf-8"))
    assert doc["descriptions"]["fa"]["description"] == h16(want("fa", "description"))


def test_the_short_hash_is_sixteen_hex(fake):
    sync(fake)
    got = persist.description_hash("zh", "short")
    assert got is not None and len(got) == 16 and int(got, 16) >= 0


# ---- a second start --------------------------------------------------------------------------

def test_a_second_start_makes_no_api_call(fake):
    sync(fake)
    before = len(calls(fake))
    sync(fake)
    assert len(calls(fake)) == before


def test_a_second_start_after_a_restart_makes_no_api_call(fake, p5):
    sync(fake)
    before = len(calls(fake))
    p5.restart()
    sync(fake)
    assert len(calls(fake)) == before


@pytest.mark.parametrize("kind", ["description", "short"])
def test_a_second_start_reports_unchanged(fake, kind):
    sync(fake)
    out = sync(fake)
    assert {v[kind] for v in out.values()} == {"unchanged"}


# ---- the owner's text -------------------------------------------------------------------------

def _owner_sets(fake, tg, text, *, short=False):
    """The owner writes a description in BotFather (simulated by a direct
    set on the fake server)."""
    async def go(bot):
        if short:
            await bot.set_my_short_description(text, language_code=tg)
        else:
            await bot.set_my_description(text, language_code=tg)
    asyncio.run(_with_client(go))


def test_the_owners_description_is_never_overwritten(fake):
    _owner_sets(fake, "fa", "My own Persian bot text")
    sync(fake)
    assert fake[0].description("fa") == "My own Persian bot text"


def test_the_owners_short_description_is_never_overwritten(fake):
    _owner_sets(fake, "zh", "Owner short", short=True)
    sync(fake)
    assert fake[0].description("zh", short=True) == "Owner short"


def test_the_owners_text_is_reported(fake):
    _owner_sets(fake, "fa", "My own Persian bot text")
    assert sync(fake)["fa"]["description"] == "owner"


def test_the_owners_text_records_nothing(fake):
    _owner_sets(fake, "fa", "My own Persian bot text")
    sync(fake)
    assert persist.description_hash("fa", "description") is None


def test_the_owners_text_in_one_language_does_not_stop_the_others(fake):
    _owner_sets(fake, "fa", "My own Persian bot text")
    sync(fake)
    assert fake[0].description("ru") == want("ru", "description")


def test_the_owner_overwriting_ours_later_is_respected(fake):
    sync(fake)
    _owner_sets(fake, "de", "Owner changed it")
    persist.record_description("de", "description", "0" * 16)  # catalog changed since
    sync(fake)
    assert fake[0].description("de") == "Owner changed it"


def test_an_owner_text_equal_to_ours_is_recorded_without_a_set(fake):
    _owner_sets(fake, "fa", want("fa", "description"))
    before = len(calls(fake, "setMyDescription"))
    sync(fake)
    sets = [c for c in calls(fake, "setMyDescription")[before:]
            if c.params.get("language_code") == "fa"]
    assert sets == []


def test_an_owner_text_equal_to_ours_is_reported_unchanged(fake):
    _owner_sets(fake, "fa", want("fa", "description"))
    assert sync(fake)["fa"]["description"] == "unchanged"


# ---- the catalog text changed ----------------------------------------------------------------

def test_a_changed_catalog_text_replaces_our_old_text(fake):
    old = "aipager old description text"
    _owner_sets(fake, "de", old)                       # what aipager set last time
    persist.record_description("de", "description", h16(old))
    sync(fake)
    assert fake[0].description("de") == want("de", "description")


def test_a_changed_catalog_text_is_reported_set(fake):
    old = "aipager old description text"
    _owner_sets(fake, "de", old)
    persist.record_description("de", "description", h16(old))
    assert sync(fake)["de"]["description"] == "set"


def test_our_old_text_with_telegrams_whitespace_still_counts_as_ours(fake):
    """design.md Risks: Telegram may normalize whitespace; ``norm`` strips it."""
    old = "aipager old description text"
    _owner_sets(fake, "it", "  " + old + "\n")
    persist.record_description("it", "description", h16(old))
    sync(fake)
    assert fake[0].description("it") == want("it", "description")


def test_our_old_text_with_a_leading_rtl_mark_still_counts_as_ours(fake):
    old = "متن قدیمی aipager"
    _owner_sets(fake, "fa", "\u200f" + old)
    persist.record_description("fa", "description", h16(old))
    sync(fake)
    assert fake[0].description("fa") == want("fa", "description")


# ---- errors (error guessing) --------------------------------------------------------------------

# Fix iteration 3: the first getMyDescription is now the default's read, so a
# one-language failure is injected on the set; a failed default read is its own case.

def test_an_error_in_one_language_is_reported(fake):
    fake[0].fail_next("setMyDescription", errors.blocked())
    out = sync(fake)
    assert sorted(v["description"] for v in out.values()).count("error") == 1


def test_an_error_in_one_language_does_not_stop_the_rest(fake):
    fake[0].fail_next("setMyDescription", errors.blocked())
    out = sync(fake)
    assert sorted(v["description"] for v in out.values()).count("set") == 13


def test_a_failed_default_read_reports_every_language_of_that_kind_as_an_error(fake):
    fake[0].fail_next("getMyDescription", errors.blocked())
    out = sync(fake)
    assert {v["description"] for v in out.values()} == {"error"}
    assert {v["short"] for v in out.values()} == {"set"}


def test_an_error_records_nothing_and_the_next_start_retries(fake):
    fake[0].fail_next("setMyShortDescription", errors.blocked())
    first = sync(fake)
    (failed,) = [tg for tg, v in first.items() if v["short"] == "error"]
    assert sync(fake)[failed]["short"] == "set"


def test_a_server_that_is_down_never_raises(mk_bot, monkeypatch):
    monkeypatch.setattr("aipager.bot.lifecycle.BOT_TOKEN", TOKEN)
    monkeypatch.setenv(telegram_endpoint.BASE_ENV, "http://127.0.0.1:9")
    bot = mk_bot()._make_builder().build().bot
    out = asyncio.run(bd.sync_descriptions(bot))
    assert {v["description"] for v in out.values()} == {"error"}


# ---- run_at_start ---------------------------------------------------------------------------------

def test_the_start_delay_is_thirty_seconds():
    assert bd.START_DELAY == 30.0


def test_run_at_start_waits_the_start_delay_first(fake, monkeypatch):
    waited = []

    async def _sleep(s):
        waited.append((s, len(calls(fake))))
    monkeypatch.setattr(bd, "_sleep", _sleep)
    asyncio.run(_with_client(bd.run_at_start))
    assert waited == [(30.0, 0)]


def test_run_at_start_syncs_after_the_wait(fake, monkeypatch):
    async def _sleep(s):
        return None
    monkeypatch.setattr(bd, "_sleep", _sleep)
    asyncio.run(_with_client(bd.run_at_start))
    assert fake[0].description("ko") == want("ko", "description")


def test_run_at_start_swallows_every_exception(monkeypatch):
    async def _sleep(s):
        return None

    class _Broken:
        def __getattr__(self, name):
            raise RuntimeError("boom")
    monkeypatch.setattr(bd, "_sleep", _sleep)
    assert asyncio.run(bd.run_at_start(_Broken())) is None
