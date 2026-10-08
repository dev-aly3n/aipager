"""SC-23: no em dash in any new UI text (design.md Success criteria 23;
spec.md item 5 and "no em dashes in any new text").

Checked on what actually reaches Telegram (every text, button and toast
the fake recorded) across each flow, on the Mini App page's report
lines, and on the shared wording tables entrypoints.md exports.

Method: equivalence partitioning over the flows that produce text (each
partition drives its own wording)."""

from __future__ import annotations

import re

import pytest

from aipager.report import policy, send, store, wording

EM = "—"


async def _flow_manual(bot, d, h):
    await d.command("/help")
    cid, mid = await h.open_card(bot, d)
    await d.tap("_:rp:note", chat=cid, message_id=mid)
    await d.text("b" * 501)
    await d.tap("_:rp:note", chat=cid, message_id=mid)
    await d.text("​")
    await d.tap("_:rp:note", chat=cid, message_id=mid)
    await d.command("/help")
    await d.tap("_:rp:send", chat=cid, message_id=mid)
    cid2, mid2 = await h.open_card(bot, d)
    await d.tap("_:rp:cancel", chat=cid2, message_id=mid2)
    await d.tap("_:rp:send", chat=cid2, message_id=mid2)


async def _flow_strangers(bot, d, h):
    await d.tap("_:rp:open", user=h.STRANGER, chat=h.OWNER)
    await d.tap("_:rp:send", user=h.STRANGER, chat=h.OWNER, message_id=1)


async def _flow_group(bot, d, h):
    await d.command("/help", chat=h.GROUP)
    await d.tap("_:rp:open", chat=h.GROUP)
    await d.tap("_:rp:open", user=h.ADMIN, chat=h.GROUP)


async def _flow_no_owner(bot, d, h):
    await d.tap("_:rp:open", user=h.OWNER, chat=h.GROUP)


async def _flow_settings(bot, d, h):
    store.save_policy(policy.State(auto_off=True, declines_in_row=2))
    await d.command("/settings")
    card = [e for e in bot.tg.sent(h.OWNER) if "Settings" in (e["text"] or "")][-1]
    await d.tap("_:rp:set", message_id=card["message_id"])
    await d.tap("_:rp:set:off", message_id=card["message_id"])
    await d.tap("_:rp:set:on", message_id=card["message_id"])


FLOWS = {
    "manual": ("personal", _flow_manual),
    "strangers": ("personal", _flow_strangers),
    "group": ("scope", _flow_group),
    "no_owner": ("scope_no_dm", _flow_no_owner),
    "settings": ("personal", _flow_settings),
}


@pytest.mark.parametrize("name", list(FLOWS))
def test_no_em_dash_reaches_telegram(make_bot, drive, h, run_async, net, monkeypatch, name):
    if name == "settings":
        monkeypatch.setenv("AIPAGER_REPORT_PROMPTS", "0")
    mode, flow = FLOWS[name]
    bot = make_bot(mode)
    run_async(flow(bot, drive(bot), h))
    assert [s for s in bot.tg.all_strings() if EM in s] == []


@pytest.mark.parametrize("fake_kw", [{"doc_changes": {"enabled": False}},
                                     {"doc_changes": {"min_version": "99.0"}},
                                     {"sentry_status": 503}],
                         ids=["disabled", "too-old", "try-later"])
def test_no_em_dash_in_outcomes(bot, drive, h, run_async, use_net, fake_kw):
    if "doc_changes" in fake_kw:
        use_net(h.FakeNet(doc=h.key_doc(**fake_kw["doc_changes"])))
    else:
        use_net(h.FakeNet(**fake_kw))
    h.record_exc()
    d = drive(bot)

    async def go():
        cid, mid = await h.open_card(bot, d)
        await d.tap("_:rp:send", chat=cid, message_id=mid)
    run_async(go())
    assert [s for s in bot.tg.all_strings() if EM in s] == []


def test_no_em_dash_in_daily_cap_outcome(bot, drive, h, run_async, use_net):
    used = h.FakeNet()
    from aipager.report import builder
    for _ in range(send.SENDS_PER_DAY):
        send.send(builder.build_report("manual"), transport=used.transport)
    use_net(h.FakeNet())
    d = drive(bot)

    async def go():
        cid, mid = await h.open_card(bot, d)
        await d.tap("_:rp:send", chat=cid, message_id=mid)
    run_async(go())
    assert [s for s in bot.tg.all_strings() if EM in s] == []


def test_no_em_dash_in_offer_flow(offer_world, h, run_async):
    w = offer_world()
    store.save_policy(policy.State(declines_in_row=1))

    async def go():
        await w.owner_acts()
        n = (await w.run_ticks())[0]
        data = [b.callback_data for row in n["markup"].inline_keyboard for b in row]
        await w.drive.tap(data[0] + "9", chat=n["chat_id"],
                          message_id=n["message_id"])           # a stale offer ts
        await w.drive.tap(data[2], chat=n["chat_id"], message_id=n["message_id"])
    run_async(go())
    assert [s for s in w.bot.tg.all_strings() if EM in s] == []


def test_no_em_dash_in_offer_preview(offer_world, h, run_async):
    w = offer_world()

    async def go():
        await w.owner_acts()
        n = (await w.run_ticks())[0]
        data = [b.callback_data for row in n["markup"].inline_keyboard for b in row]
        await w.drive.tap(data[0], chat=n["chat_id"], message_id=n["message_id"])
    run_async(go())
    assert [s for s in w.bot.tg.all_strings() if EM in s] == []


def test_no_em_dash_in_memory_cap_notice(bot, h, run_async):
    sess = h.add_session(bot.registry, "jim")
    run_async(bot.notify(sess, "hook_memory_cap_hit", {"hook": "aipager-hook"}))
    labels = [t for e in bot.tg.sent() for t, _ in _rows(e["markup"])]
    assert [t for t in labels if EM in t] == []


def _rows(markup):
    if markup is None or not hasattr(markup, "inline_keyboard"):
        return []
    return [(b.text, b.callback_data) for row in markup.inline_keyboard for b in row]


def test_no_em_dash_in_shared_wording():
    texts = [wording.INTRO, wording.TRY_LATER, *wording.OUTCOME_LINES.values()]
    assert [t for t in texts if EM in t] == []


@pytest.mark.parametrize("module", ["aipager.bot.report_flow", "aipager.bot.report_offer",
                                    "aipager.report.wording"])
def test_no_em_dash_in_exported_text_constants(module):
    import importlib
    mod = importlib.import_module(module)
    texts = []
    for name in dir(mod):
        if name.isupper():
            value = getattr(mod, name)
            if isinstance(value, str):
                texts.append(value)
            elif isinstance(value, (tuple, list)):
                texts.extend(v for v in value if isinstance(v, str))
            elif isinstance(value, dict):
                texts.extend(v for v in value.values() if isinstance(v, str))
    assert [t for t in texts if EM in t] == []


def test_no_em_dash_in_page_report_lines(bot, run_async, monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer

    from aipager.miniapp.server import MiniAppServer
    monkeypatch.setattr("aipager.config.BOT_TOKEN", "1:x")
    srv = MiniAppServer(bot, bot.registry, port=8898)

    async def go():
        client = TestClient(TestServer(srv._build_app()))
        await client.start_server()
        try:
            return await (await client.get("/")).text()
        finally:
            await client.close()
    page = run_async(go())
    lines = [ln for ln in page.splitlines()
             if re.search(r"report|private chat|owner chat", ln, re.I)]
    assert [ln for ln in lines if EM in ln] == []


def test_scan_is_not_vacuous(make_bot, drive, h, run_async, net):
    """Control: the manual flow's recorded strings do include the card,
    the capture prompt and the outcome, so the scans above read them."""
    bot = make_bot("personal")
    run_async(_flow_manual(bot, drive(bot), h))
    joined = "\n".join(bot.tg.all_strings())
    assert all(s in joined for s in ("Report a problem", "Type your note", "Note added",
                                     "nothing was sent", "📤 Send"))
