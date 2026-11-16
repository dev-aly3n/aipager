"""Plan readings arrive through the ``statusline`` datagram and the raw
status file (design.md Success criteria 1 and 3; entrypoints.md "Existing
drive points" and "Inputs").

Partitions: a payload with rate_limits, without (first payload, API key,
gateway), with only invalid windows; one login vs two logins vs the same
login from two sessions. Error guessing: a missing or corrupt status file
must not break the datagram, a newer reading that drops a window, a gpt-5
gateway payload carrying a dollar cost."""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock

import pytest

from aipager import statusline_file, usage_store
from aipager.bot import usage_cmd
from aipager.dtach import hook_receiver as hr
from aipager.state import SessionRegistry

R_FAR = int(time.time()) + 2 * 86400 + 5 * 3600


@pytest.fixture
def rig(tmp_state_file):
    registry = SessionRegistry()
    recv = hr.HookReceiver(registry, AsyncMock())
    return registry, recv


_seq = iter(range(1, 10_000_000))


def _payload(rate_limits=None, *, model="claude-opus-test", cost=None):
    data = {"session_id": "conv-a", "model": {"id": model, "display_name": model},
            "context_window": {"total_input_tokens": 10, "total_output_tokens": 5,
                               "used_percentage": 3}}
    if rate_limits is not None:
        data["rate_limits"] = rate_limits
    if cost is not None:
        data["cost"] = {"total_cost_usd": cost}
    return data


def _status(name, data):
    path = statusline_file.status_file_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, (bytes, str)):
        path.write_bytes(data if isinstance(data, bytes) else data.encode())
    else:
        path.write_text(json.dumps(data))


def _datagram(recv, run_async, name):
    run_async(recv._on_datagram(json.dumps({
        "type": "statusline", "session": name, "context_pct": 3,
        "session_id": "conv-a", "seq": next(_seq)}).encode()))


def _session(registry, name, label, cwd=""):
    s = registry.get_or_create(name)
    s.label = label
    s.cwd = cwd
    return s


def _feed(registry, recv, run_async, name, data, *, label=None, cwd=""):
    if registry.get(name) is None:
        _session(registry, name, label or name.removeprefix("claude-"), cwd)
    _status(name, data)
    _datagram(recv, run_async, name)


def _plan():
    return usage_store.snapshot(None, now=time.time()).plan


def _view(h):
    return usage_cmd.usage_view(usage_store.snapshot(None, now=time.time()),
                                usage_cmd.UsageAccess(None, "all", True), h.en(), time.time())


def _html(h):
    return usage_cmd.usage_html(usage_store.snapshot(None, now=time.time()),
                                usage_cmd.UsageAccess(None, "all", True), h.en(), time.time())


SEVEN = {"seven_day": {"used_percentage": 83, "resets_at": R_FAR}}
BOTH = {"five_hour": {"used_percentage": 7, "resets_at": int(time.time()) + 3 * 3600},
        "seven_day": {"used_percentage": 42, "resets_at": R_FAR}}


# ---- one reading --------------------------------------------------------------

def test_a_payload_with_rate_limits_stores_one_reading(rig, run_async):
    registry, recv = rig
    _feed(registry, recv, run_async, "claude-frontend", _payload(SEVEN))
    assert len(_plan()) == 1


def test_the_stored_reading_shows_its_percentage(rig, run_async, h):
    registry, recv = rig
    _feed(registry, recv, run_async, "claude-frontend", _payload(SEVEN))
    assert "This week (all models): 83% used" in _html(h)


def test_the_stored_reading_names_its_session(rig, run_async, h):
    registry, recv = rig
    _feed(registry, recv, run_async, "claude-frontend", _payload(SEVEN))
    assert "from session frontend." in _html(h)


def test_the_stored_reading_is_dated_now(rig, run_async, h):
    registry, recv = rig
    _feed(registry, recv, run_async, "claude-frontend", _payload(SEVEN))
    assert "(just now)" in _html(h)


@pytest.mark.parametrize("payload", [
    _payload(None), _payload({}), _payload({"seven_day": {"used_percentage": True,
                                                          "resets_at": R_FAR}}),
    _payload(None, model="gpt-5", cost=0.000432)],
    ids=["no-rate-limits", "empty-object", "all-invalid", "gpt5-gateway"])
def test_a_payload_without_a_valid_reading_stores_nothing(rig, run_async, payload):
    registry, recv = rig
    _feed(registry, recv, run_async, "claude-frontend", payload)
    assert len(_plan()) == 0


@pytest.mark.parametrize("payload", [
    _payload(None), _payload({}), _payload({"seven_day": {"used_percentage": "x",
                                                          "resets_at": R_FAR}})],
    ids=["no-rate-limits", "empty-object", "all-invalid"])
def test_a_payload_without_rate_limits_never_erases_a_reading(rig, run_async, h, payload):
    registry, recv = rig
    _feed(registry, recv, run_async, "claude-frontend", _payload(SEVEN))
    _feed(registry, recv, run_async, "claude-frontend", payload)
    assert "This week (all models): 83% used" in _html(h)


@pytest.mark.parametrize("raw", [b"", b"{not json", b"[]", b"null"],
                         ids=["empty", "corrupt", "list", "null"])
def test_a_bad_status_file_never_erases_a_reading(rig, run_async, h, raw):
    registry, recv = rig
    _feed(registry, recv, run_async, "claude-frontend", _payload(SEVEN))
    _feed(registry, recv, run_async, "claude-frontend", raw)
    assert "83% used" in _html(h)


def test_a_missing_status_file_never_breaks_the_datagram(rig, run_async):
    registry, recv = rig
    _session(registry, "claude-frontend", "frontend")
    _datagram(recv, run_async, "claude-frontend")          # no file at all
    assert len(_plan()) == 0


def test_a_newer_reading_replaces_the_older_one(rig, run_async, h):
    registry, recv = rig
    _feed(registry, recv, run_async, "claude-frontend", _payload(SEVEN))
    _feed(registry, recv, run_async, "claude-frontend",
          _payload({"seven_day": {"used_percentage": 84, "resets_at": R_FAR}}))
    assert "84% used" in _html(h)


def test_a_newer_reading_leaves_no_trace_of_the_older_percentage(rig, run_async, h):
    registry, recv = rig
    _feed(registry, recv, run_async, "claude-frontend", _payload(SEVEN))
    _feed(registry, recv, run_async, "claude-frontend",
          _payload({"seven_day": {"used_percentage": 84, "resets_at": R_FAR}}))
    assert "83%" not in _html(h)


def test_a_window_the_newer_reading_lacks_is_no_longer_shown(rig, run_async, h):
    registry, recv = rig
    _feed(registry, recv, run_async, "claude-frontend", _payload(BOTH))
    _feed(registry, recv, run_async, "claude-frontend", _payload(SEVEN))
    assert "5-hour window" not in _html(h)


# ---- logins ---------------------------------------------------------------------

def test_the_same_login_from_two_sessions_keeps_one_reading(rig, run_async):
    """Both sessions have no folder: the same ("unknown") key."""
    registry, recv = rig
    _feed(registry, recv, run_async, "claude-frontend", _payload(SEVEN))
    _feed(registry, recv, run_async, "claude-backend",
          _payload({"seven_day": {"used_percentage": 90, "resets_at": R_FAR}}))
    assert len(_plan()) == 1


def test_the_same_login_shows_the_newest_session(rig, run_async, h):
    registry, recv = rig
    _feed(registry, recv, run_async, "claude-frontend", _payload(SEVEN))
    _feed(registry, recv, run_async, "claude-backend",
          _payload({"seven_day": {"used_percentage": 90, "resets_at": R_FAR}}))
    assert "from session backend." in _html(h)


def _two_logins(registry, recv, run_async, tmp_path):
    folder = tmp_path / "proj"
    folder.mkdir()
    _feed(registry, recv, run_async, "claude-frontend", _payload(SEVEN), cwd="")
    _feed(registry, recv, run_async, "claude-backend",
          _payload({"seven_day": {"used_percentage": 12, "resets_at": R_FAR}}),
          cwd=str(folder))


def test_two_logins_keep_two_readings(rig, run_async, tmp_path):
    """An empty folder is the "unknown" login; a real folder is not."""
    registry, recv = rig
    _two_logins(registry, recv, run_async, tmp_path)
    assert len(_plan()) == 2


def test_two_logins_show_both_percentages(rig, run_async, tmp_path, h):
    registry, recv = rig
    _two_logins(registry, recv, run_async, tmp_path)
    html = _html(h)
    assert ("83% used" in html, "12% used" in html) == (True, True)


def test_two_logins_each_get_a_freshness_line(rig, run_async, tmp_path, h):
    registry, recv = rig
    _two_logins(registry, recv, run_async, tmp_path)
    html = _html(h)
    assert ("from session frontend." in html, "from session backend." in html) == (True, True)


def test_two_logins_are_each_named(rig, run_async, tmp_path, h):
    registry, recv = rig
    _two_logins(registry, recv, run_async, tmp_path)
    names = [lg["name"] for lg in _view(h)["plan"]["logins"]]
    assert len(names) == 2 and all(names) and names[0] != names[1]


def test_one_login_is_not_named(rig, run_async, h):
    registry, recv = rig
    _feed(registry, recv, run_async, "claude-frontend", _payload(SEVEN))
    assert [lg["name"] for lg in _view(h)["plan"]["logins"]] == [None]


def test_the_unknown_login_is_called_unknown_sign_in(rig, run_async, tmp_path, h):
    registry, recv = rig
    _two_logins(registry, recv, run_async, tmp_path)
    assert "Unknown sign-in" in _html(h)


# ---- no dollars, ever ------------------------------------------------------------

def test_a_gpt5_gateway_session_shows_no_dollar(rig, run_async, h):
    registry, recv = rig
    _feed(registry, recv, run_async, "claude-gw", _payload(None, model="gpt-5", cost=12.34))
    assert "$" not in _html(h)


def test_a_spend_limit_with_dollar_fields_shows_no_dollar(rig, run_async, h):
    registry, recv = rig
    _feed(registry, recv, run_async, "claude-gw", _payload(
        {"spend_limit": {"used_percentage": 30, "resets_at": R_FAR,
                         "used_usd": 7.5, "limit_usd": 25}}, cost=7.5))
    assert "$" not in _html(h)


def test_a_spend_limit_shows_its_percentage(rig, run_async, h):
    registry, recv = rig
    _feed(registry, recv, run_async, "claude-gw", _payload(
        {"spend_limit": {"used_percentage": 30, "resets_at": R_FAR,
                         "used_usd": 7.5, "limit_usd": 25}}))
    assert "Spend limit: 30% used" in _html(h)


def test_the_view_never_carries_the_dollar_amounts(rig, run_async, h):
    registry, recv = rig
    _feed(registry, recv, run_async, "claude-gw", _payload(
        {"spend_limit": {"used_percentage": 30, "resets_at": R_FAR,
                         "used_usd": 7.5, "limit_usd": 25}}, cost=7.5))
    assert "7.5" not in json.dumps(_view(h))


def _keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _keys(v)


def test_the_view_has_no_cost_or_usd_field(rig, run_async, h):
    registry, recv = rig
    _feed(registry, recv, run_async, "claude-gw", _payload(
        {"spend_limit": {"used_percentage": 30, "resets_at": R_FAR,
                         "used_usd": 7.5, "limit_usd": 25}}, cost=7.5))
    assert [k for k in _keys(_view(h)) if "usd" in k or "cost" in k] == []
