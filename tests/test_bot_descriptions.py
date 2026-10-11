"""The bot's description in each language (multilingual design 5.8).

Set per Telegram code (zh, pt included) only when Telegram has nothing for
that language or has what aipager itself set last time, and only when the
catalog text changed (no call at all otherwise); the owner's own text is
never overwritten, and a default (no language) text of the owner's stops
every language of that kind; ``en`` and the default are never written; the
start never waits for it. Against AsyncMock bots and the fake Bot API only.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.bot import bot_descriptions as bd
from aipager.i18n import core, get, persist
from aipager.i18n.locales import LOCALES, enabled_codes

TG_CODES = [LOCALES[c].telegram_code for c in enabled_codes() if c != "en"]
RLM = "\u200f"


class FakeTg:
    """An AsyncMock-style bot with Telegram's per-language storage."""

    def __init__(self, existing=None, fail=()):
        self.store = dict(existing or {})          # (kind, code) -> text
        self.fail = set(fail)
        self.calls = []

    def _get(self, kind, field):
        async def call(*, language_code=None, rate_limit_args=None):
            self.calls.append(("get", kind, language_code, rate_limit_args))
            if (kind, language_code) in self.fail:
                raise RuntimeError("telegram down")
            return SimpleNamespace(**{field: self.store.get((kind, language_code), "")})
        return call

    def _set(self, kind, field):
        async def call(*, language_code=None, rate_limit_args=None, **kw):
            self.calls.append(("set", kind, language_code, rate_limit_args))
            self.store[(kind, language_code)] = kw[field]
            return True
        return call

    @property
    def get_my_description(self):
        return self._get("description", "description")

    @property
    def set_my_description(self):
        return self._set("description", "description")

    @property
    def get_my_short_description(self):
        return self._get("short", "short_description")

    @property
    def set_my_short_description(self):
        return self._set("short", "short_description")


def _want(code, msg):
    return get(code).text(msg)


def test_every_language_is_set_when_telegram_has_nothing(run_async):
    tg = FakeTg()
    out = run_async(bd.sync_descriptions(tg))
    assert sorted(out) == sorted(TG_CODES) and "zh" in out and "pt" in out
    assert all(v == {"description": "set", "short": "set"} for v in out.values())
    assert tg.store[("description", "zh")] == _want("zh-Hans", bd.DESCRIPTION)
    assert tg.store[("short", "pt")] == _want("pt-BR", bd.SHORT)
    assert persist.description_hash("fa", "short") == bd._digest(_want("fa", bd.SHORT))


def test_never_english_or_the_default(run_async):
    tg = FakeTg()
    run_async(bd.sync_descriptions(tg))
    set_codes = {c[2] for c in tg.calls if c[0] == "set"}
    assert None not in set_codes and "en" not in set_codes and "" not in set_codes
    assert set_codes == set(TG_CODES)
    assert "en" not in {c[2] for c in tg.calls}
    # The default is only read, once per kind.
    assert sorted(c[:3] for c in tg.calls if c[2] is None) == [
        ("get", "description", None), ("get", "short", None)]


# ---- the owner's default text (review rev-iter1-003) -------------------------------

def test_an_owner_default_description_stops_every_language(run_async):
    tg = FakeTg(existing={("description", None): "My own bot"})
    out = run_async(bd.sync_descriptions(tg))
    assert {v["description"] for v in out.values()} == {"owner"}
    assert not [c for c in tg.calls if c[0] == "set" and c[1] == "description"]
    assert all(persist.description_hash(tg_code, "description") is None
               for tg_code in TG_CODES)
    # The short one has no default of the owner's: it is still set.
    assert {v["short"] for v in out.values()} == {"set"}


def test_an_owner_default_short_description_stops_every_language(run_async):
    tg = FakeTg(existing={("short", None): RLM + "Mine"})
    out = run_async(bd.sync_descriptions(tg))
    assert {v["short"] for v in out.values()} == {"owner"}
    assert {v["description"] for v in out.values()} == {"set"}


def test_an_owner_default_also_keeps_our_old_text_unchanged(run_async):
    old = "an older aipager description"
    persist.record_description("de", "description", bd._digest(old))
    tg = FakeTg(existing={("description", "de"): old, ("description", None): "Mine"})
    out = run_async(bd.sync_descriptions(tg))
    assert out["de"]["description"] == "owner"
    assert tg.store[("description", "de")] == old


def test_a_blank_default_is_not_the_owners(run_async):
    tg = FakeTg(existing={("description", None): "  \u200f "})
    out = run_async(bd.sync_descriptions(tg))
    assert {v["description"] for v in out.values()} == {"set"}


def test_the_default_is_read_before_any_per_language_read(run_async):
    tg = FakeTg(existing={("description", c): "owner text" for c in TG_CODES})
    run_async(bd.sync_descriptions(tg))
    for kind in ("description", "short"):
        reads = [c[2] for c in tg.calls if c[0] == "get" and c[1] == kind]
        assert reads[0] is None and reads.count(None) == 1
        assert sorted(reads[1:]) == sorted(TG_CODES)


def test_an_owner_default_costs_one_read_per_kind_at_every_start(run_async):
    tg = FakeTg(existing={("description", None): "Mine", ("short", None): "Mine too"})
    for _start in range(2):
        persist.reset()                               # a restart
        tg.calls.clear()
        out = run_async(bd.sync_descriptions(tg))
        assert sorted(c[:3] for c in tg.calls) == [
            ("get", "description", None), ("get", "short", None)]
        assert all(v == {"description": "owner", "short": "owner"} for v in out.values())


def test_an_owner_default_of_one_kind_skips_only_that_kinds_reads(run_async):
    tg = FakeTg(existing={("description", None): "Mine"})
    run_async(bd.sync_descriptions(tg))
    assert [c[2] for c in tg.calls if c[1] == "description"] == [None]
    assert len([c for c in tg.calls if c[:2] == ("get", "short")]) == len(TG_CODES) + 1


def test_a_failed_default_read_is_an_error_and_is_asked_again(run_async):
    tg = FakeTg(fail={("description", None)})
    out = run_async(bd.sync_descriptions(tg))
    assert {v["description"] for v in out.values()} == {"error"}
    # One read of the default, then no per-language call of that kind.
    assert [c[:3] for c in tg.calls if c[1] == "description"] == [
        ("get", "description", None)]
    tg.fail.clear()
    assert {v["description"] for v in run_async(bd.sync_descriptions(tg)).values()} == {
        "set"}


def test_every_call_is_the_lowest_priority(run_async):
    tg = FakeTg()
    run_async(bd.sync_descriptions(tg))
    assert tg.calls and all(c[3]["class"] == "ornament" for c in tg.calls)


def test_a_second_start_makes_no_call(run_async):
    run_async(bd.sync_descriptions(FakeTg()))
    persist.reset()                                   # a restart
    tg = FakeTg()
    out = run_async(bd.sync_descriptions(tg))
    assert tg.calls == []
    assert all(v == {"description": "unchanged", "short": "unchanged"} for v in out.values())


def test_the_owners_text_is_never_overwritten(run_async):
    tg = FakeTg(existing={("description", "fa"): "ربات شخصی من"})
    out = run_async(bd.sync_descriptions(tg))
    assert out["fa"]["description"] == "owner"
    assert tg.store[("description", "fa")] == "ربات شخصی من"
    assert ("set", "description", "fa") not in {c[:3] for c in tg.calls}
    assert persist.description_hash("fa", "description") is None
    # Nor at the next start.
    tg.calls.clear()
    assert run_async(bd.sync_descriptions(tg))["fa"]["description"] == "owner"
    assert tg.store[("description", "fa")] == "ربات شخصی من"


def test_our_own_older_text_is_replaced_when_the_catalog_changed(run_async):
    old = "an older aipager description"
    persist.record_description("de", "description", bd._digest(old))
    tg = FakeTg(existing={("description", "de"): RLM + old + "  "})
    out = run_async(bd.sync_descriptions(tg))
    assert out["de"]["description"] == "set"
    assert tg.store[("description", "de")] == _want("de", bd.DESCRIPTION)
    assert persist.description_hash("de", "description") == bd._digest(
        _want("de", bd.DESCRIPTION))


def test_a_text_already_equal_is_recorded_without_a_set(run_async):
    tg = FakeTg(existing={("short", "ar"): RLM + _want("ar", bd.SHORT)})
    out = run_async(bd.sync_descriptions(tg))
    assert out["ar"]["short"] == "unchanged"
    assert ("set", "short", "ar") not in {c[:3] for c in tg.calls}
    assert persist.description_hash("ar", "short") == bd._digest(_want("ar", bd.SHORT))


def test_an_untranslated_language_is_skipped(run_async, monkeypatch):
    cat = dict(core._catalog("ko"))
    cat.pop("botdesc.short")
    monkeypatch.setitem(core._CATALOGS, "ko", cat)
    tg = FakeTg()
    out = run_async(bd.sync_descriptions(tg))
    assert out["ko"] == {"description": "set", "short": "untranslated"}
    assert ("get", "short", "ko") not in {c[:3] for c in tg.calls}


def test_an_error_on_one_language_records_nothing_and_goes_on(run_async):
    tg = FakeTg(fail={("description", "ru")})
    out = run_async(bd.sync_descriptions(tg))
    assert out["ru"]["description"] == "error"
    assert out["ru"]["short"] == "set" and out["vi"]["description"] == "set"
    assert persist.description_hash("ru", "description") is None
    # The next start retries it.
    tg.fail.clear()
    assert run_async(bd.sync_descriptions(tg))["ru"]["description"] == "set"


def test_the_marks_telegram_may_add_never_change_the_hash():
    assert bd._digest(RLM + " text \u200e") == bd._digest("text")
    assert bd._norm(None) == ""


def test_run_at_start_waits_through_the_seam_and_never_raises(run_async, monkeypatch):
    slept = []

    async def _sleep(seconds):
        slept.append(seconds)

    async def _boom(_tg):
        raise RuntimeError("no")

    monkeypatch.setattr(bd, "_sleep", _sleep)
    monkeypatch.setattr(bd, "sync_descriptions", _boom)
    run_async(bd.run_at_start(MagicMock()))
    assert slept == [bd.START_DELAY]


def test_start_returns_while_the_sync_is_still_pending(mk_bot, run_async, monkeypatch):
    monkeypatch.setattr("aipager.bot.lifecycle.CHAT_ID", "555")
    entered = []

    async def _blocked(_tg):
        entered.append(1)
        await asyncio.Event().wait()

    monkeypatch.setattr(bd, "run_at_start", _blocked)
    tg = mk_bot()
    stub = MagicMock()
    stub.initialize = AsyncMock()
    stub.start = AsyncMock()
    stub.updater.start_polling = AsyncMock()
    stub.updater.stop = AsyncMock()
    stub.stop = AsyncMock()
    stub.shutdown = AsyncMock()
    tg._make_builder = MagicMock(return_value=MagicMock(build=MagicMock(return_value=stub)))
    tg._update_bot_commands = AsyncMock()

    async def _go():
        await asyncio.wait_for(tg.start(), timeout=5)
        await asyncio.sleep(0)
        task = tg._descriptions_task
        pending = not task.done()
        await tg.stop()
        await asyncio.sleep(0)
        return pending, task

    pending, task = run_async(_go())
    assert pending and entered == [1] and task.cancelled()


# ---- the fake Bot API ---------------------------------------------------------------

@pytest.fixture
def fake():
    from tests.e2e.fake_telegram.server import FakeBotApiThread
    t = FakeBotApiThread("7000000001:FAKEtokenFAKEtokenFAKEtoken", 7000000001, "fake_bot")
    base = t.start()
    try:
        yield t.api, base
    finally:
        t.stop()


def _ext_bot(base):
    from telegram.ext import BaseRateLimiter, ExtBot

    class _PassThrough(BaseRateLimiter):
        async def initialize(self):
            return None

        async def shutdown(self):
            return None

        async def process_request(self, callback, args, kwargs, endpoint, data,
                                  rate_limit_args):
            return await callback(*args, **kwargs)

    return ExtBot("7000000001:FAKEtokenFAKEtokenFAKEtoken", base_url=f"{base}/bot",
                  base_file_url=f"{base}/file/bot", rate_limiter=_PassThrough())


def test_a_round_trip_through_the_fake_bot_api(fake, run_async):
    api, base = fake

    async def go():
        async with _ext_bot(base) as bot:
            await bot.set_my_description("Mon bot à moi", language_code="fr")
            first = await bd.sync_descriptions(bot)
            persist.reset()
            before = len(api.calls())
            second = await bd.sync_descriptions(bot)
            return first, second, len(api.calls()) - before

    first, second, calls = run_async(go())
    assert first["zh"] == {"description": "set", "short": "set"}
    assert first["fr"] == {"description": "owner", "short": "set"}
    assert api.description("zh") == _want("zh-Hans", bd.DESCRIPTION)
    assert api.description("pt", short=True) == _want("pt-BR", bd.SHORT)
    assert api.description("fr") == "Mon bot à moi"
    assert api.description("") == "" and api.description("en") == ""
    # Only fr's description is asked again (the owner's): the default, then fr.
    assert calls == 2 and second["fr"]["description"] == "owner"
    assert api.unknown_methods == []


def test_an_owner_default_through_the_fake_bot_api(fake, run_async):
    api, base = fake

    async def go():
        async with _ext_bot(base) as bot:
            await bot.set_my_description("My own bot, written in BotFather")
            return await bd.sync_descriptions(bot)

    out = run_async(go())
    assert {v["description"] for v in out.values()} == {"owner"}
    assert {v["short"] for v in out.values()} == {"set"}
    assert api.description("fa") == "" and api.description("zh") == ""
    assert api.description("") == "My own bot, written in BotFather"
    assert api.description("fa", short=True) == _want("fa", bd.SHORT)
