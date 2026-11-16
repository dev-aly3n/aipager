"""GET /api/usage and ``can_usage`` in /api/sessions, through the real
Mini App app (design.md Success criteria 15 and 17; entrypoints.md "HTTP
routes").

Partitions over the caller (owner, admin of the chosen chat, admin
elsewhere only, user, read_only, stranger, bad or missing signature) and
the mode (scope vs personal). Error guessing: the view leaking a dollar
figure, a transcript path or a config home; a chosen scope the caller does
not manage."""

from __future__ import annotations

import json
import time

import pytest

from aipager import usage_store
from aipager.claude_bootstrap import SessionLogin
from aipager.statusline_file import Window

NOW = time.time()


@pytest.fixture
def world(make_bot, tmp_path, h):
    bot = make_bot("scope")
    for name, scope, n in (("claude-api__g", h.GROUP, 100), ("claude-web__g2", h.GROUP2, 1000),
                           ("claude-dev__d", h.OWNER, 10_000)):
        p = tmp_path / f"{name}.jsonl"
        h.append(p, h.entry(f"msg_{name}", NOW - 30, n, 0, model="claude-opus-test"))
        h.idle(h.add_session(bot.registry, name, scope=scope, transcript=p,
                             cwd=str(tmp_path)))
    usage_store.note_plan("frontend", {"seven_day": Window(83.0, int(NOW + 2 * 86400)),
                                       "five_hour": Window(7.0, int(NOW - 10))},
                          SessionLogin("saved", "", "/home/alice/.claude-private"), now=NOW)
    return bot


def _usage(h, bot, run_async, user, scope=None, hdrs=None):
    return h.get(bot, run_async, "/api/usage",
                 hdrs if hdrs is not None else h.headers(user, scope=scope))


def _sessions(h, bot, run_async, user, scope=None):
    return h.get(bot, run_async, "/api/sessions", h.headers(user, scope=scope))


# ---- 401 / 403 -----------------------------------------------------------------------

def test_a_bad_signature_is_401(h, world, run_async):
    assert _usage(h, world, run_async, h.OWNER,
                  hdrs=h.headers(h.OWNER, token="999:WRONG-token"))[0] == 401


def test_no_init_data_is_401(h, world, run_async):
    assert _usage(h, world, run_async, h.OWNER, hdrs={})[0] == 401


@pytest.mark.parametrize("who, scope", [("USER", "GROUP"), ("RO", "GROUP"),
                                        ("ADMIN", "GROUP2")],
                         ids=["user", "read_only", "admin-elsewhere-only"])
def test_a_non_admin_is_403(h, world, run_async, who, scope):
    assert _usage(h, world, run_async, getattr(h, who), getattr(h, scope))[0] == 403


@pytest.mark.parametrize("who", ["USER", "RO"])
def test_a_non_admin_gets_the_forbidden_body(h, world, run_async, who):
    assert _usage(h, world, run_async, getattr(h, who), h.GROUP)[1] == {"error": "forbidden"}


def test_a_stranger_is_refused(h, world, run_async):
    assert _usage(h, world, run_async, h.STRANGER, h.GROUP)[0] in (401, 403)


def test_a_stranger_in_personal_mode_is_403(h, make_bot, run_async):
    assert _usage(h, make_bot("personal"), run_async, h.STRANGER)[0] == 403


# ---- 200 ------------------------------------------------------------------------------------

@pytest.mark.parametrize("who, scope", [("OWNER", "OWNER"), ("OWNER", "GROUP"),
                                        ("ADMIN", "GROUP")],
                         ids=["owner-dm", "owner-group", "admin-group"])
def test_an_admin_gets_200(h, world, run_async, who, scope):
    assert _usage(h, world, run_async, getattr(h, who), getattr(h, scope))[0] == 200


def test_the_personal_operator_gets_200(h, make_bot, run_async):
    assert _usage(h, make_bot("personal"), run_async, h.OWNER)[0] == 200


def test_the_body_has_plan_and_tokens(h, world, run_async):
    _s, body = _usage(h, world, run_async, h.OWNER, h.OWNER)
    assert {"plan", "tokens"} <= set(body)


def test_the_plan_title(h, world, run_async):
    assert _usage(h, world, run_async, h.OWNER, h.OWNER)[1]["plan"]["title"] == "Plan limits"


@pytest.mark.parametrize("who, scope", [("OWNER", "GROUP"), ("ADMIN", "GROUP")],
                         ids=["owner", "admin"])
def test_the_mini_app_shows_the_plan_even_from_a_group_scope(h, world, run_async, who, scope):
    """The Mini App is private to the viewer: never narrowed like a group reply."""
    assert _usage(h, world, run_async, getattr(h, who),
                  getattr(h, scope))[1]["plan"]["shown"] is True


def test_the_window_lines(h, world, run_async):
    (login,) = _usage(h, world, run_async, h.OWNER, h.OWNER)[1]["plan"]["logins"]
    assert [w["line"] for w in login["windows"]] == [
        "5-hour window: reset since the last reading", "This week (all models): 83% used"]


def test_the_window_states(h, world, run_async):
    (login,) = _usage(h, world, run_async, h.OWNER, h.OWNER)[1]["plan"]["logins"]
    assert [w["state"] for w in login["windows"]] == ["reset", "ok"]


def test_the_ok_window_pct(h, world, run_async):
    (login,) = _usage(h, world, run_async, h.OWNER, h.OWNER)[1]["plan"]["logins"]
    assert login["windows"][1]["pct"] == 83


def test_the_reset_window_has_no_pct(h, world, run_async):
    (login,) = _usage(h, world, run_async, h.OWNER, h.OWNER)[1]["plan"]["logins"]
    assert "pct" not in login["windows"][0]


def test_the_freshness_line(h, world, run_async):
    (login,) = _usage(h, world, run_async, h.OWNER, h.OWNER)[1]["plan"]["logins"]
    assert login["fresh"].endswith("from session frontend.")


def test_owner_tokens_title(h, world, run_async):
    assert _usage(h, world, run_async, h.OWNER, h.OWNER)[1]["tokens"]["title"] == "Tokens"


def test_owner_sees_every_chats_tokens(h, world, run_async):
    rows = _usage(h, world, run_async, h.OWNER, h.OWNER)[1]["tokens"]["rows"]
    assert rows[0] == {"label": "Today", "cells": ["11.1k", "0", "0", "0"]}


def test_admin_tokens_title(h, world, run_async):
    assert _usage(h, world, run_async, h.ADMIN, h.GROUP)[1]["tokens"]["title"] == \
        "Tokens in the chats you manage"


def test_admin_sees_only_managed_tokens(h, world, run_async):
    rows = _usage(h, world, run_async, h.ADMIN, h.GROUP)[1]["tokens"]["rows"]
    assert rows[0]["cells"][0] == "100"


def test_the_columns(h, world, run_async):
    assert _usage(h, world, run_async, h.OWNER, h.OWNER)[1]["tokens"]["columns"] == [
        "", "In", "Out", "Cache read", "Cache write"]


def test_the_rows_are_today_and_last_7_days(h, world, run_async):
    rows = _usage(h, world, run_async, h.OWNER, h.OWNER)[1]["tokens"]["rows"]
    assert [r["label"] for r in rows] == ["Today", "Last 7 days"]


def test_seven_day_rows(h, world, run_async):
    assert len(_usage(h, world, run_async, h.OWNER, h.OWNER)[1]["tokens"]["days"]) == 7


def test_the_day_and_model_titles(h, world, run_async):
    t = _usage(h, world, run_async, h.OWNER, h.OWNER)[1]["tokens"]
    assert (t["days_title"], t["models_title"]) == ("By day", "By model, last 7 days")


def test_the_model_rows_name_the_model_as_recorded(h, world, run_async):
    models = _usage(h, world, run_async, h.OWNER, h.OWNER)[1]["tokens"]["models"]
    assert [m["label"] for m in models] == ["claude-opus-test"]


def test_tokens_present_means_no_empty_line(h, world, run_async):
    assert _usage(h, world, run_async, h.OWNER, h.OWNER)[1]["tokens"]["empty"] is None


def test_caught_up_means_no_note(h, world, run_async):
    assert _usage(h, world, run_async, h.OWNER, h.OWNER)[1]["tokens"]["note"] is None


def test_no_tokens_fills_the_empty_line(h, make_bot, run_async):
    body = _usage(h, make_bot("scope"), run_async, h.OWNER, h.OWNER)[1]
    assert body["tokens"]["empty"] == "No tokens recorded yet."


def test_no_reading_fills_the_plan_empty_line(h, make_bot, run_async):
    body = _usage(h, make_bot("scope"), run_async, h.OWNER, h.OWNER)[1]
    assert body["plan"]["empty"].startswith("Plan limits show here after Claude answers")


# ---- what the view never carries ------------------------------------------------------------

def test_no_dollar_in_the_body(h, world, run_async):
    _s, body = _usage(h, world, run_async, h.OWNER, h.OWNER)
    assert "$" not in json.dumps(body, ensure_ascii=False)


def test_no_config_home_in_the_body(h, world, run_async):
    _s, body = _usage(h, world, run_async, h.OWNER, h.OWNER)
    assert ".claude-private" not in json.dumps(body)


def test_no_transcript_path_in_the_body(h, world, run_async, tmp_path):
    _s, body = _usage(h, world, run_async, h.OWNER, h.OWNER)
    assert str(tmp_path) not in json.dumps(body)


def _keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _keys(v)


def test_no_cost_field_in_the_body(h, world, run_async):
    _s, body = _usage(h, world, run_async, h.OWNER, h.OWNER)
    assert [k for k in _keys(body) if "cost" in k.lower() or "usd" in k.lower()] == []


# ---- can_usage in /api/sessions ------------------------------------------------------------------

@pytest.mark.parametrize("who, scope, expected", [
    ("OWNER", "OWNER", True), ("OWNER", "GROUP", True), ("ADMIN", "GROUP", True),
    ("USER", "GROUP", False), ("RO", "GROUP", False)],
    ids=["owner-dm", "owner-group", "admin", "user", "read_only"])
def test_can_usage_follows_the_role(h, world, run_async, who, scope, expected):
    status, body = _sessions(h, world, run_async, getattr(h, who), getattr(h, scope))
    assert (status, body.get("can_usage")) == (200, expected)


def test_can_usage_in_personal_mode(h, make_bot, run_async):
    status, body = _sessions(h, make_bot("personal"), run_async, h.OWNER)
    assert (status, body.get("can_usage")) == (200, True)


def test_can_usage_agrees_with_the_route(h, world, run_async):
    """The hint never says yes where the route says no, for each caller."""
    pairs = []
    for who, scope in ((h.OWNER, h.OWNER), (h.ADMIN, h.GROUP), (h.USER, h.GROUP),
                       (h.RO, h.GROUP)):
        can = _sessions(h, world, run_async, who, scope)[1].get("can_usage")
        pairs.append((can, _usage(h, world, run_async, who, scope)[0] == 200))
    assert all(a == b for a, b in pairs)
