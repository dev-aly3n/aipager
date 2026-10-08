"""POST /api/report/draft, black box (design.md Success criterion 5,
entrypoints.md "HTTP routes" and "Draft response facts").

Methods: equivalence partitioning over the caller (owner, member who is
not the owner, stranger, bad signature), the configuration (personal,
scope, no owner, two owners) and the chosen scope (private, group);
boundary: the write budget (full vs not); error guessing: refusal order
when two refusals apply at once, a non-JSON body, the old route still
answering."""

from __future__ import annotations

import json

import pytest

from aipager.bot import report_flow, report_offer
from aipager.report import builder, store


def _draft(bot, run_async, h, **kw):
    return h.serve(bot, run_async, lambda api: api.draft(**kw))


# ---- the happy path ---------------------------------------------------------------

def test_owner_in_personal_mode_gets_200(bot, h, run_async):
    status, _ = _draft(bot, run_async, h)
    assert status == 200


def test_owner_in_scope_dm_gets_200(make_bot, h, run_async):
    status, _ = _draft(make_bot("scope"), run_async, h, scope=h.OWNER)
    assert status == 200


def test_response_carries_every_promised_key(bot, h, run_async):
    _, body = _draft(bot, run_async, h)
    assert set(body) >= {"draft", "report", "preview", "areas", "note_max", "expires_in"}


def test_note_max_is_500(bot, h, run_async):
    _, body = _draft(bot, run_async, h)
    assert body["note_max"] == 500


def test_expires_in_is_24_hours(bot, h, run_async):
    _, body = _draft(bot, run_async, h)
    assert body["expires_in"] == 86400


def test_draft_id_is_a_short_string(bot, h, run_async):
    _, body = _draft(bot, run_async, h)
    assert isinstance(body["draft"], str) and 1 <= len(body["draft"]) <= 64


def test_two_drafts_get_different_ids(bot, h, run_async):
    async def go(api):
        return await api.draft_id(), await api.draft_id()
    a, b = h.serve(bot, run_async, go)
    assert a != b


def test_report_is_a_manual_report(bot, h, run_async):
    _, body = _draft(bot, run_async, h)
    assert body["report"]["trigger"] == "manual"


def test_report_carries_no_note_key(bot, h, run_async):
    _, body = _draft(bot, run_async, h)
    assert "note" not in body["report"]


def test_body_is_ignored_even_when_not_json(bot, h, run_async):
    status, _ = h.serve(bot, run_async,
                        lambda api: api.post("/api/report/draft", raw=b"not json at all"))
    assert status == 200


# ---- the draft equals the bot's builder ------------------------------------------------

def _builder_report(bot):
    return builder.build_report("manual", errors=store.errors(),
                                counters=store.counters_24h(), log_digest=store.digest_24h(),
                                context=report_flow.report_context(bot))


@pytest.mark.parametrize("errors", [0, 1, 4], ids=["no-errors", "one-error", "four-errors"])
def test_draft_equals_builder_for_same_state(bot, h, run_async, errors):
    for kind in h._EXC_TYPES[:errors]:
        h.record_exc(kind)
    _, body = _draft(bot, run_async, h)
    assert body["report"] == _builder_report(bot)


def test_draft_equals_builder_with_a_logged_bug(bot, h, run_async):
    h.record_bug()
    _, body = _draft(bot, run_async, h)
    assert body["report"] == _builder_report(bot)


def test_preview_is_the_report_rendered(bot, h, run_async):
    h.record_exc()
    _, body = _draft(bot, run_async, h)
    assert body["preview"] == json.dumps(body["report"], indent=2, ensure_ascii=False)


@pytest.mark.parametrize("errors", [1, 3], ids=["one", "three"])
def test_areas_has_one_entry_per_error(bot, h, run_async, errors):
    for kind in h._EXC_TYPES[:errors]:
        h.record_exc(kind)
    _, body = _draft(bot, run_async, h)
    assert len(body["areas"]) == len(body["report"]["errors"])


def test_areas_match_report_offer_area(bot, h, run_async):
    h.record_exc()
    h.record_bug(file="aipager/bot/animation.py")
    h.record_bug(fn="serve", file="aipager/miniapp/server.py")
    _, body = _draft(bot, run_async, h)
    assert body["areas"] == [report_offer.area(e) for e in body["report"]["errors"]]


def test_areas_empty_with_no_errors(bot, h, run_async):
    _, body = _draft(bot, run_async, h)
    assert body["areas"] == []


# ---- 401 -------------------------------------------------------------------------------

def test_bad_init_data_is_401(bot, h, run_async):
    status, _ = _draft(bot, run_async, h, hdrs=h.bad_headers())
    assert status == 401


def test_bad_init_data_says_unauthorized(bot, h, run_async):
    _, body = _draft(bot, run_async, h, hdrs=h.bad_headers())
    assert body == {"error": "unauthorized"}


def test_missing_init_data_is_401(bot, h, run_async):
    status, _ = _draft(bot, run_async, h, hdrs={})
    assert status == 401


# ---- 403 forbidden -----------------------------------------------------------------------

@pytest.mark.parametrize("member", ["ADMIN", "USER"])
def test_non_owner_is_403(make_bot, h, run_async, member):
    status, body = _draft(make_bot("scope"), run_async, h, user=getattr(h, member),
                          scope=h.GROUP)
    assert (status, body) == (403, {"error": "forbidden"})


def test_stranger_is_403_forbidden(make_bot, h, run_async):
    status, body = _draft(make_bot("scope"), run_async, h, user=h.STRANGER)
    assert (status, body) == (403, {"error": "forbidden"})


def test_stranger_in_personal_mode_is_403(bot, h, run_async):
    status, _ = _draft(bot, run_async, h, user=h.STRANGER)
    assert status == 403


# ---- 409 no_owner ----------------------------------------------------------------------------

@pytest.mark.parametrize("mode", ["scope_no_dm", "scope_two_owners"])
def test_no_owner_is_409(make_bot, h, run_async, mode):
    status, body = _draft(make_bot(mode), run_async, h, user=h.OWNER, scope=h.GROUP)
    assert (status, body) == (409, {"error": "no_owner"})


# ---- 403 private_only ----------------------------------------------------------------------

def test_owner_in_group_is_403_private_only(make_bot, h, run_async):
    status, body = _draft(make_bot("scope"), run_async, h, scope=h.GROUP)
    assert (status, body) == (403, {"error": "private_only"})


# ---- 429 -------------------------------------------------------------------------------------

def test_rate_limited_is_429(bot, h, run_async):
    async def go(api):
        api.exhaust_budget()
        return await api.draft()
    status, body = h.serve(bot, run_async, go)
    assert (status, body) == (429, {"error": "too_many_requests"})


def test_rate_limited_keeps_nothing_and_a_later_draft_works(bot, h, run_async):
    async def go(api):
        api.exhaust_budget()
        await api.draft()
        api.keep_budget = False
        return await api.draft()
    status, _ = h.serve(bot, run_async, go)
    assert status == 200


# ---- refusal order: 401, 403 non-member, 409, 403 non-owner, 403 private_only, 429 ----------

def test_order_401_before_no_owner(make_bot, h, run_async):
    status, _ = _draft(make_bot("scope_no_dm"), run_async, h, hdrs=h.bad_headers())
    assert status == 401


def test_order_non_member_403_before_no_owner(make_bot, h, run_async):
    status, body = _draft(make_bot("scope_no_dm"), run_async, h, user=h.STRANGER)
    assert (status, body) == (403, {"error": "forbidden"})


def test_order_no_owner_before_non_owner(make_bot, h, run_async):
    status, body = _draft(make_bot("scope_no_dm"), run_async, h, user=h.ADMIN, scope=h.GROUP)
    assert (status, body) == (409, {"error": "no_owner"})


def test_order_non_owner_before_private_only(make_bot, h, run_async):
    """A member who is not the owner, in a group scope: forbidden, not
    private_only."""
    status, body = _draft(make_bot("scope"), run_async, h, user=h.USER, scope=h.GROUP)
    assert body == {"error": "forbidden"}


def test_order_private_only_before_429(make_bot, h, run_async):
    async def go(api):
        api.exhaust_budget(h.OWNER)
        return await api.draft(scope=h.GROUP)
    status, body = h.serve(make_bot("scope"), run_async, go)
    assert (status, body) == (403, {"error": "private_only"})


def test_order_non_owner_before_429(make_bot, h, run_async):
    async def go(api):
        api.exhaust_budget(h.ADMIN)
        return await api.draft(user=h.ADMIN, scope=h.GROUP)
    status, body = h.serve(make_bot("scope"), run_async, go)
    assert (status, body) == (403, {"error": "forbidden"})


# ---- refusals keep nothing --------------------------------------------------------------------

def test_refused_draft_ids_never_leak(make_bot, h, run_async):
    _, body = _draft(make_bot("scope"), run_async, h, scope=h.GROUP)
    assert "draft" not in body


# ---- the old route is gone ------------------------------------------------------------------

def test_old_preview_route_is_gone(bot, h, run_async):
    status, _ = h.serve(bot, run_async, lambda api: api.post("/api/report/preview"))
    assert status in (404, 405)


def test_old_preview_route_posts_no_card(bot, h, run_async):
    h.serve(bot, run_async, lambda api: api.post("/api/report/preview"))
    assert bot._app.bot.send_message.await_count == 0


def test_draft_posts_nothing_to_telegram(bot, h, run_async):
    _draft(bot, run_async, h)
    assert bot._app.bot.send_message.await_count == 0


def test_draft_touches_no_network(bot, h, run_async, net):
    _draft(bot, run_async, h)
    assert net.requests == []


def test_draft_id_is_not_guessable_from_the_owner(bot, h, run_async):
    _, body = _draft(bot, run_async, h)
    assert str(h.OWNER) not in body["draft"]
