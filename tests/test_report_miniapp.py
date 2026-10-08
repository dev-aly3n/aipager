"""The Mini App's "Report a problem" page (roadmap 8.112 and its
follow-up): ``can_report`` in GET /api/preferences, POST /api/report/draft
(build and keep a draft) and POST /api/report/send (send that kept draft
plus the note the page showed). Owner only, from the private chat, rate
limited. Every send goes through a fake network."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from aiohttp.test_utils import TestClient, TestServer

from aipager.bot import report_flow
from aipager.miniapp.server import SCOPE_HEADER, MiniAppServer
from aipager.policy import load_policy
from aipager.scope import Member, Scope
from aipager.report import wording
from aipager.state import SessionRegistry
from tests.report_ui_harness import (
    GROUP,
    OWNER,
    FakeNet,
    FakeTg,
    attachment,
    key_doc,
    pin_version,
)
from tests.test_miniapp_preferences_api import BOT_TOKEN, _init_data

ADMIN = 555


@pytest.fixture(autouse=True)
def _pinned_version(monkeypatch):
    """Run as a known release whether or not the package is installed."""
    pin_version(monkeypatch)


@pytest.fixture(autouse=True)
def _configured_bot_token(monkeypatch):
    monkeypatch.setattr("aipager.config.BOT_TOKEN", BOT_TOKEN)


def _scopes(with_owner_dm=True):
    group = Scope(chat_id=GROUP, kind="group", label="team", members=(
        Member(id=OWNER, label="aly", role="owner"),
        Member(id=ADMIN, label="ada", role="admin")))
    dm = Scope(chat_id=OWNER, kind="dm", label="aly DM",
               members=(Member(id=OWNER, label="aly", role="owner"),))
    return [dm, group] if with_owner_dm else [group]


def _server(mk_bot, *, with_owner_dm=True):
    registry = SessionRegistry()
    bot = mk_bot(registry, scopes=_scopes(with_owner_dm))
    bot.policy = load_policy(Path("/nonexistent/policy.yaml"), Path("/nonexistent/policy.d"))
    tg = FakeTg()
    tg.username = "aipager_test_bot"
    bot._app.bot = tg
    return MiniAppServer(bot, registry, port=8765), bot, tg


def _hdr(user_id, scope=None):
    h = {"X-Telegram-Init-Data": _init_data(user_id)}
    if scope is not None:
        h[SCOPE_HEADER] = str(scope)
    return h


def _call(run_async, srv, method, path, headers):
    async def _run():
        client = TestClient(TestServer(srv._build_app()))
        await client.start_server()
        try:
            resp = await client.request(method, path, headers=headers, json={})
            return resp.status, await resp.json()
        finally:
            await client.close()
    return run_async(_run())


def test_can_report_only_owner_private(mk_bot, run_async):
    srv, _bot, _tg = _server(mk_bot)
    assert _call(run_async, srv, "GET", "/api/preferences", _hdr(OWNER))[1]["can_report"] is True
    assert _call(run_async, srv, "GET", "/api/preferences",
                 _hdr(OWNER, GROUP))[1]["can_report"] is False
    assert _call(run_async, srv, "GET", "/api/preferences", _hdr(ADMIN))[1]["can_report"] is False


# ---- POST /api/report/draft ----------------------------------------------------

def _post(run_async, srv, path, headers, body=None, *, raw=None):
    async def _run():
        client = TestClient(TestServer(srv._build_app()))
        await client.start_server()
        try:
            if raw is not None:
                resp = await client.post(path, headers=headers, data=raw)
            else:
                resp = await client.post(path, headers=headers, json={} if body is None else body)
            return resp.status, await resp.json()
        finally:
            await client.close()
    return run_async(_run())


def _posts(run_async, srv, calls):
    """Several (path, headers, body) requests against one server."""
    async def _run():
        client = TestClient(TestServer(srv._build_app()))
        await client.start_server()
        try:
            out = []
            for path, headers, body in calls:
                resp = await client.post(path, headers=headers, json=body)
                out.append((resp.status, await resp.json()))
            return out
        finally:
            await client.close()
    return run_async(_run())


@pytest.fixture
def net(monkeypatch):
    fake = FakeNet()
    monkeypatch.setattr(report_flow, "SEND_TRANSPORT", fake.transport)
    return fake


def test_draft_answers_the_builders_report(mk_bot, run_async):
    from aipager.report import builder, store
    srv, bot, tg = _server(mk_bot)
    status, body = _post(run_async, srv, "/api/report/draft", _hdr(OWNER))
    assert status == 200
    expected = builder.build_report("manual", errors=store.errors(),
                                    counters=store.counters_24h(),
                                    log_digest=store.digest_24h(),
                                    context=report_flow.report_context(bot))
    expected["runtime"]["uptime"] = body["report"]["runtime"]["uptime"]
    assert body["report"] == expected
    assert body["preview"] == builder.render_preview(body["report"])
    assert body["areas"] == []
    assert (body["note_max"], body["expires_in"]) == (500, 86400)
    assert list(bot._report_drafts) == [body["draft"]]
    assert tg.sent == [] and getattr(bot, "_report_cards", {}) == {}


def test_draft_areas_follow_the_offer_notice(mk_bot, run_async, monkeypatch):
    from aipager.bot import report_offer
    real_build = report_flow._build
    entry = {"fingerprint": "ap1-0123456789ab", "tier": "error", "where": "daemon",
             "trigger": "exception", "logger": "aipager.bot.notify", "type": "builtins.KeyError",
             "cause_types": [], "errno": None, "tg_class": None, "event": None, "tool": None,
             "count": 3, "first_day": "2026-10-01", "last_day": "2026-10-07",
             "versions_seen": ["0.7.20"], "external": [],
             "frames": [{"file": "aipager/bot/notify.py", "line": 10, "fn": "deliver"}]}
    monkeypatch.setattr(report_flow, "_build",
                        lambda trigger, errors, ctx: real_build(trigger, [entry], ctx))
    srv, _bot, _tg = _server(mk_bot)
    status, body = _post(run_async, srv, "/api/report/draft", _hdr(OWNER))
    assert status == 200 and len(body["report"]["errors"]) == 1
    assert body["areas"] == [report_offer.area(body["report"]["errors"][0])] == \
        ["message delivery"]


def test_draft_from_a_group_is_403_private_only(mk_bot, run_async):
    srv, bot, tg = _server(mk_bot)
    status, body = _post(run_async, srv, "/api/report/draft", _hdr(OWNER, GROUP))
    assert (status, body) == (403, {"error": "private_only"})
    assert getattr(bot, "_report_drafts", {}) == {} and tg.sent == []


def test_draft_403_non_owner(mk_bot, run_async):
    srv, bot, tg = _server(mk_bot)
    status, body = _post(run_async, srv, "/api/report/draft", _hdr(ADMIN))
    assert (status, body) == (403, {"error": "forbidden"})
    assert getattr(bot, "_report_drafts", {}) == {}


def test_draft_401_bad_init_data(mk_bot, run_async):
    srv, bot, _tg = _server(mk_bot)
    status, body = _post(run_async, srv, "/api/report/draft",
                         {"X-Telegram-Init-Data": "auth_date=1&hash=x"})
    assert (status, body) == (401, {"error": "unauthorized"})
    assert getattr(bot, "_report_drafts", {}) == {}


def test_draft_no_owner_409(mk_bot, run_async):
    srv, bot, _tg = _server(mk_bot, with_owner_dm=False)
    status, body = _post(run_async, srv, "/api/report/draft", _hdr(OWNER))
    assert (status, body) == (409, {"error": "no_owner"})
    assert getattr(bot, "_report_drafts", {}) == {}


def test_draft_429(mk_bot, run_async):
    srv, bot, _tg = _server(mk_bot)
    srv._write_hits[OWNER] = [time.monotonic()] * 100
    status, body = _post(run_async, srv, "/api/report/draft", _hdr(OWNER))
    assert (status, body) == (429, {"error": "too_many_requests"})
    assert getattr(bot, "_report_drafts", {}) == {}


def test_draft_503_when_the_build_fails(mk_bot, run_async, monkeypatch):
    def boom(*_a):
        raise OSError("disk")
    monkeypatch.setattr(report_flow, "_build", boom)
    srv, bot, _tg = _server(mk_bot)
    status, body = _post(run_async, srv, "/api/report/draft", _hdr(OWNER))
    assert (status, body) == (503, {"error": "not_built"})
    assert getattr(bot, "_report_drafts", {}) == {}


def test_the_draft_route_closes_the_owners_note_capture(mk_bot, run_async):
    srv, bot, tg = _server(mk_bot)
    run_async(report_flow.open_preview(bot, trigger="manual"))
    (kept,) = bot._report_cards.values()
    kept.state = "note"
    bot._report_note_pending = {"chat_id": OWNER, "user_id": OWNER, "msg_id": kept.msg_id,
                                "last_active": time.monotonic()}
    status, _body = _post(run_async, srv, "/api/report/draft", _hdr(OWNER))
    assert status == 200
    assert bot._report_note_pending is None and kept.state == "open"


def test_a_refused_draft_leaves_the_note_capture_open(mk_bot, run_async):
    srv, bot, tg = _server(mk_bot)
    run_async(report_flow.open_preview(bot, trigger="manual"))
    (kept,) = bot._report_cards.values()
    kept.state = "note"
    bot._report_note_pending = {"chat_id": OWNER, "user_id": OWNER, "msg_id": kept.msg_id,
                                "last_active": time.monotonic()}
    status, _body = _post(run_async, srv, "/api/report/draft", _hdr(OWNER, GROUP))
    assert status == 403
    assert bot._report_note_pending is not None and kept.state == "note"


def test_old_preview_route_is_gone(mk_bot, run_async):
    srv, bot, tg = _server(mk_bot)

    async def _run():
        client = TestClient(TestServer(srv._build_app()))
        await client.start_server()
        try:
            resp = await client.post("/api/report/preview", headers=_hdr(OWNER), json={})
            return resp.status
        finally:
            await client.close()
    assert run_async(_run()) in (404, 405)
    assert tg.sent == [] and getattr(bot, "_report_cards", {}) == {}


# ---- POST /api/report/send ----------------------------------------------------------

def _draft(run_async, srv):
    status, body = _post(run_async, srv, "/api/report/draft", _hdr(OWNER))
    assert status == 200
    return body


def _with_note(report: dict, note: str) -> bytes:
    shown = dict(report)
    if note:
        shown["note"] = note
    return json.dumps(shown, indent=2, ensure_ascii=False).encode()


def test_sent_attachment_equals_the_displayed_report_without_note(mk_bot, run_async, net):
    srv, _bot, _tg = _server(mk_bot)
    d = _draft(run_async, srv)
    status, body = _post(run_async, srv, "/api/report/send", _hdr(OWNER), {"draft": d["draft"]})
    assert status == 200
    assert body["outcome"] == "sent" and body["retry"] is False and body["repeat"] is False
    assert re.fullmatch(r"ap1-[0-9a-f]{12}|[0-9a-f]{32}", body["reference"])
    assert body["line"] == report_flow.outcome_line("sent", body["reference"])
    (post,) = net.posts
    assert attachment(post.content) == d["preview"].encode() == _with_note(d["report"], "")


def test_sent_attachment_equals_the_displayed_report_with_note(mk_bot, run_async, net):
    srv, _bot, _tg = _server(mk_bot)
    d = _draft(run_async, srv)
    note = 'It froze after "Send" \\ twice.\nThen 👨‍👩‍👧 🏴󠁧󠁢󠁳󠁣󠁴󠁿 \u200eok'
    status, body = _post(run_async, srv, "/api/report/send", _hdr(OWNER),
                         {"draft": d["draft"], "note": note})
    assert (status, body["outcome"]) == (200, "sent")
    (post,) = net.posts
    assert attachment(post.content) == _with_note(d["report"], note)


def test_a_report_key_in_the_body_is_ignored(mk_bot, run_async, net):
    srv, _bot, _tg = _server(mk_bot)
    d = _draft(run_async, srv)
    forged = dict(d["report"], trigger="crash")
    status, _body = _post(run_async, srv, "/api/report/send", _hdr(OWNER),
                          {"draft": d["draft"], "note": "", "report": forged})
    assert status == 200
    (post,) = net.posts
    assert attachment(post.content) == d["preview"].encode()


@pytest.mark.parametrize("note, normalized", [
    ("It froze. ", "It froze."),
    ("\u200b\u2060", ""),
    ("x" * 520, "x" * 500),
])
def test_a_note_not_in_its_normalized_form_is_refused_with_it(mk_bot, run_async, net,
                                                               note, normalized):
    srv, bot, _tg = _server(mk_bot)
    d = _draft(run_async, srv)
    status, body = _post(run_async, srv, "/api/report/send", _hdr(OWNER),
                         {"draft": d["draft"], "note": note})
    assert (status, body) == (422, {"error": "note_changed", "note": normalized})
    assert net.requests == []
    assert bot._report_drafts[d["draft"]].state == "open"


def test_a_note_the_report_checks_refuse_is_422(mk_bot, run_async, net, monkeypatch):
    from aipager.report import send
    srv, _bot, _tg = _server(mk_bot)
    d = _draft(run_async, srv)
    monkeypatch.setattr(send, "checked_preview", lambda _r: None)
    status, body = _post(run_async, srv, "/api/report/send", _hdr(OWNER),
                         {"draft": d["draft"], "note": "fine"})
    assert (status, body) == (422, {"error": "note_refused"})
    assert net.requests == []


@pytest.mark.parametrize("raw", [
    "[]", "not json", '{"draft": 5}', '{"draft": ""}', '{"draft": "%s"}' % ("a" * 65),
    '{"draft": "abc", "note": 5}', '{"draft": "abc", "note": null}',
    '{"draft": "abc", "note": "%s"}' % ("y" * 2001), "{}",
])
def test_bad_body_is_400(mk_bot, run_async, net, raw):
    srv, _bot, _tg = _server(mk_bot)
    _draft(run_async, srv)
    status, body = _post(run_async, srv, "/api/report/send",
                         dict(_hdr(OWNER), **{"Content-Type": "application/json"}), raw=raw)
    assert (status, body) == (400, {"error": "bad_request"})
    assert net.requests == []


def test_a_2000_character_note_is_not_a_bad_request(mk_bot, run_async, net):
    srv, _bot, _tg = _server(mk_bot)
    d = _draft(run_async, srv)
    status, body = _post(run_async, srv, "/api/report/send", _hdr(OWNER),
                         {"draft": d["draft"], "note": "y" * 2000})
    assert (status, body) == (422, {"error": "note_changed", "note": "y" * 500})


def test_unknown_draft_is_410_nothing_sent(mk_bot, run_async, net):
    srv, _bot, _tg = _server(mk_bot)
    _draft(run_async, srv)
    status, body = _post(run_async, srv, "/api/report/send", _hdr(OWNER), {"draft": "nope"})
    assert (status, body) == (410, {"error": "draft_gone"})
    assert net.requests == []


def test_foreign_draft_is_410_nothing_sent(mk_bot, run_async, net):
    srv, bot, _tg = _server(mk_bot)
    d = _draft(run_async, srv)
    bot._report_drafts[d["draft"]].user_id = ADMIN
    status, body = _post(run_async, srv, "/api/report/send", _hdr(OWNER), {"draft": d["draft"]})
    assert (status, body) == (410, {"error": "draft_gone"})
    assert net.requests == []


def test_expired_draft_is_410_nothing_sent(mk_bot, run_async, net, monkeypatch):
    from aipager.bot import report_drafts
    now = [5_000_000.0]
    monkeypatch.setattr(report_flow, "_mono", lambda: now[0])
    srv, _bot, _tg = _server(mk_bot)
    d = _draft(run_async, srv)
    now[0] += report_drafts.DRAFT_TTL + 1
    status, body = _post(run_async, srv, "/api/report/send", _hdr(OWNER), {"draft": d["draft"]})
    assert (status, body) == (410, {"error": "draft_gone"})
    assert net.requests == []


def test_evicted_draft_is_410(mk_bot, run_async, net):
    srv, _bot, _tg = _server(mk_bot)
    first = _draft(run_async, srv)
    for _ in range(3):
        _draft(run_async, srv)
    status, body = _post(run_async, srv, "/api/report/send", _hdr(OWNER),
                         {"draft": first["draft"]})
    assert (status, body) == (410, {"error": "draft_gone"})
    assert net.requests == []


def test_send_from_group_scope_is_refused(mk_bot, run_async, net):
    srv, _bot, _tg = _server(mk_bot)
    d = _draft(run_async, srv)
    status, body = _post(run_async, srv, "/api/report/send", _hdr(OWNER, GROUP),
                         {"draft": d["draft"]})
    assert (status, body) == (403, {"error": "private_only"})
    assert net.requests == []


def test_non_owner_cannot_send_the_owners_draft(mk_bot, run_async, net):
    srv, _bot, _tg = _server(mk_bot)
    d = _draft(run_async, srv)
    status, body = _post(run_async, srv, "/api/report/send", _hdr(ADMIN), {"draft": d["draft"]})
    assert (status, body) == (403, {"error": "forbidden"})
    assert net.requests == []


def test_send_with_bad_init_data_is_401_nothing_sent(mk_bot, run_async, net):
    srv, _bot, _tg = _server(mk_bot)
    d = _draft(run_async, srv)
    status, body = _post(run_async, srv, "/api/report/send",
                         {"X-Telegram-Init-Data": "auth_date=1&hash=x"}, {"draft": d["draft"]})
    assert (status, body) == (401, {"error": "unauthorized"})
    assert net.requests == []


def test_send_with_no_owner_is_409_nothing_sent(mk_bot, run_async, net):
    srv, bot, _tg = _server(mk_bot)
    d = _draft(run_async, srv)
    bot.scopes = _scopes(with_owner_dm=False)
    status, body = _post(run_async, srv, "/api/report/send", _hdr(OWNER), {"draft": d["draft"]})
    assert (status, body) == (409, {"error": "no_owner"})
    assert net.requests == []


def test_rate_limited_send_is_429_nothing_sent(mk_bot, run_async, net):
    srv, _bot, _tg = _server(mk_bot)
    d = _draft(run_async, srv)
    srv._write_hits[OWNER] = [time.monotonic()] * 100
    status, body = _post(run_async, srv, "/api/report/send", _hdr(OWNER), {"draft": d["draft"]})
    assert (status, body) == (429, {"error": "too_many_requests"})
    assert net.requests == []


def test_repeat_after_sent_returns_the_same_reference_no_second_post(mk_bot, run_async, net):
    srv, _bot, _tg = _server(mk_bot)
    d = _draft(run_async, srv)
    (first, second) = _posts(run_async, srv, [
        ("/api/report/send", _hdr(OWNER), {"draft": d["draft"], "note": "x"}),
        ("/api/report/send", _hdr(OWNER), {"draft": d["draft"], "note": "x"}),
    ])
    assert first[0] == second[0] == 200
    assert second[1] == dict(first[1], repeat=True)
    assert len(net.posts) == 1


def test_try_later_keeps_the_draft_and_a_retry_sends(mk_bot, run_async, monkeypatch):
    fake = FakeNet(sentry_status=429)
    monkeypatch.setattr(report_flow, "SEND_TRANSPORT", fake.transport)
    srv, _bot, _tg = _server(mk_bot)
    d = _draft(run_async, srv)
    status, body = _post(run_async, srv, "/api/report/send", _hdr(OWNER), {"draft": d["draft"]})
    assert (status, body) == (200, {"outcome": "rate_limited", "reference": None,
                                    "line": wording.TRY_LATER, "retry": True, "repeat": False})
    fake.sentry_status = 200
    status, body = _post(run_async, srv, "/api/report/send", _hdr(OWNER), {"draft": d["draft"]})
    assert (status, body["outcome"]) == (200, "sent")
    assert len(fake.posts) == 2
    assert attachment(fake.posts[1].content) == d["preview"].encode()


@pytest.mark.parametrize("case, outcome", [
    ("disabled", "disabled"), ("too_old", "too_old"), ("daily_cap", "daily_cap"),
    ("failed", "failed"),
])
def test_final_outcomes_are_mapped_with_the_shared_wording(mk_bot, run_async, monkeypatch,
                                                           case, outcome):
    from aipager.report import send
    doc = {"disabled": key_doc(enabled=False),
           "too_old": key_doc(min_version="99.0.0")}.get(case)
    fake = FakeNet(doc=doc)
    monkeypatch.setattr(report_flow, "SEND_TRANSPORT", fake.transport)
    if case == "daily_cap":
        monkeypatch.setattr(send, "SENDS_PER_DAY", 0)
    if case == "failed":
        def boom(*_a, **_k):
            raise RuntimeError("bug")
        monkeypatch.setattr(send, "send", boom)
    srv, _bot, _tg = _server(mk_bot)
    d = _draft(run_async, srv)
    status, body = _post(run_async, srv, "/api/report/send", _hdr(OWNER), {"draft": d["draft"]})
    line = (report_flow.SEND_FAILED_TEXT if outcome == "failed"
            else wording.OUTCOME_LINES[outcome])
    assert (status, body) == (200, {"outcome": outcome, "reference": None, "line": line,
                                    "retry": False, "repeat": False})
    assert fake.posts == []


def test_double_send_posts_once(mk_bot, run_async, monkeypatch):
    """Two sends of one draft at once: the second sees it sending."""
    import threading

    gate = threading.Event()
    posting = threading.Event()
    base = FakeNet()

    def handler(request):
        if request.method == "POST":
            posting.set()
            gate.wait(10)
        return base(request)
    monkeypatch.setattr(report_flow, "SEND_TRANSPORT", httpx.MockTransport(handler))
    srv, _bot, _tg = _server(mk_bot)
    d = _draft(run_async, srv)

    async def _run():
        import asyncio
        client = TestClient(TestServer(srv._build_app()))
        await client.start_server()
        try:
            async def one():
                resp = await client.post("/api/report/send", headers=_hdr(OWNER),
                                         json={"draft": d["draft"]})
                return resp.status, await resp.json()
            first = asyncio.ensure_future(one())
            for _ in range(1000):
                if posting.is_set():
                    break
                await asyncio.sleep(0.01)
            second = await one()
            gate.set()
            return await asyncio.wait_for(first, 10), second
        finally:
            gate.set()
            await client.close()
    first, second = run_async(_run())
    assert first[0] == 200 and first[1]["outcome"] == "sent"
    assert second == (409, {"error": "sending"})
    assert len(base.posts) == 1


def test_logs_carry_no_note_text(mk_bot, run_async, net, caplog):
    import logging
    caplog.set_level(logging.DEBUG)
    srv, _bot, _tg = _server(mk_bot)
    d = _draft(run_async, srv)
    _post(run_async, srv, "/api/report/send", _hdr(OWNER),
          {"draft": d["draft"], "note": "zebra-secret "})
    _post(run_async, srv, "/api/report/send", _hdr(OWNER),
          {"draft": d["draft"], "note": "zebra-secret"})
    assert len(net.posts) == 1
    assert "zebra" not in caplog.text
    assert d["draft"] not in caplog.text
    assert "report send refused (422)" in caplog.text


def test_the_send_route_closes_the_owners_note_capture(mk_bot, run_async, net):
    srv, bot, _tg = _server(mk_bot)
    d = _draft(run_async, srv)
    kept = _capturing(run_async, bot)
    status, _body = _post(run_async, srv, "/api/report/send", _hdr(OWNER), {"draft": d["draft"]})
    assert status == 200
    assert bot._report_note_pending is None and kept.state == "open"


# ---- any other Mini App action closes the note capture (spec criterion 8) ---

def _capturing(run_async, bot):
    run_async(report_flow.open_preview(bot, trigger="manual"))
    (kept,) = bot._report_cards.values()
    kept.state = "note"
    bot._report_note_pending = {"chat_id": OWNER, "user_id": OWNER, "msg_id": kept.msg_id,
                                "last_active": time.monotonic()}
    return kept


def _send(run_async, srv, method, path, headers, body):
    async def _run():
        client = TestClient(TestServer(srv._build_app()))
        await client.start_server()
        try:
            resp = await client.request(method, path, headers=headers, json=body)
            return resp.status
        finally:
            await client.close()
    return run_async(_run())


def test_a_preference_write_closes_the_note_capture(mk_bot, run_async):
    """A route that never opts into the session close target: the
    capture closes all the same (one place, keyed on the caller)."""
    srv, bot, _tg = _server(mk_bot)
    kept = _capturing(run_async, bot)
    status = _send(run_async, srv, "PUT", "/api/preferences/answer_length", _hdr(OWNER),
                   {"value": "short"})
    assert status == 200
    assert bot._report_note_pending is None and kept.state == "open"


def test_an_update_action_closes_the_note_capture(mk_bot, run_async):
    srv, bot, _tg = _server(mk_bot)
    bot.updates = MagicMock()
    bot.updates.control.return_value = SimpleNamespace(ok=True, job={"id": 7}, error=None)
    kept = _capturing(run_async, bot)
    status = _send(run_async, srv, "POST", "/api/update/cancel", _hdr(OWNER), {"job_id": 7})
    assert status == 200
    assert bot._report_note_pending is None and kept.state == "open"


def test_a_refused_write_leaves_the_note_capture_open(mk_bot, run_async):
    srv, bot, _tg = _server(mk_bot)
    kept = _capturing(run_async, bot)
    status = _send(run_async, srv, "PUT", "/api/preferences/answer_length", _hdr(OWNER),
                   {"value": "not-a-length"})
    assert status == 400
    assert bot._report_note_pending is not None and kept.state == "note"


def test_a_read_leaves_the_note_capture_open(mk_bot, run_async):
    srv, bot, _tg = _server(mk_bot)
    kept = _capturing(run_async, bot)
    assert _send(run_async, srv, "GET", "/api/preferences", _hdr(OWNER), None) == 200
    assert bot._report_note_pending is not None and kept.state == "note"


def test_someone_elses_write_leaves_the_owners_capture_open(mk_bot, run_async):
    srv, bot, _tg = _server(mk_bot)
    kept = _capturing(run_async, bot)
    status = _send(run_async, srv, "PUT", "/api/preferences/answer_length",
                   _hdr(ADMIN, GROUP), {"value": "short"})
    assert status == 200
    assert bot._report_note_pending is not None and kept.state == "note"
