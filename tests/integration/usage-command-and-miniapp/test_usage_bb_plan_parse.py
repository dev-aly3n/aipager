"""statusline_file.parse_rate_limits, black box (design.md Success
criterion 2; entrypoints.md "Exported functions").

Equivalence partitions: a valid window, each kind of invalid window
(bool, string, NaN, infinity, None, missing field, not an object), an
unknown window name, a missing or non-object ``rate_limits``. Boundaries:
``used_percentage`` 0 / 1000 / just outside, ``resets_at`` 1e9 / 4e9 / just
outside. Error guessing: a float ``resets_at`` (truncated), extra keys
from a gateway (``used_usd``), every subset of the three windows."""

from __future__ import annotations

import itertools
import math

import pytest

from aipager.statusline_file import Window, parse_rate_limits

R = 1794862800
NAMES = ("five_hour", "seven_day", "spend_limit")


def _one(**window):
    return {"rate_limits": {"seven_day": window}}


def _parsed_seven_day(**window):
    out = parse_rate_limits(_one(**window))
    return None if out is None else out.get("seven_day")


def test_a_valid_window_parses():
    assert _parsed_seven_day(used_percentage=83, resets_at=R) == Window(83, R)


def test_a_valid_window_pct_is_a_float_value():
    assert _parsed_seven_day(used_percentage=82.6, resets_at=R).pct == pytest.approx(82.6)


def test_a_float_reset_time_is_truncated_to_an_int():
    assert _parsed_seven_day(used_percentage=1, resets_at=R + 0.9).resets_at == R


@pytest.mark.parametrize("pct", [0, 0.0, 100, 1000], ids=["zero", "zero-float", "100", "1000"])
def test_percentage_inside_the_range_is_kept(pct):
    assert _parsed_seven_day(used_percentage=pct, resets_at=R) is not None


@pytest.mark.parametrize("pct", [-0.001, -1, 1000.001, 1001],
                         ids=["just-below-0", "minus-1", "just-above-1000", "1001"])
def test_percentage_outside_the_range_stores_nothing(pct):
    assert parse_rate_limits(_one(used_percentage=pct, resets_at=R)) is None


@pytest.mark.parametrize("ts", [1_000_000_000, 4_000_000_000], ids=["1e9", "4e9"])
def test_reset_time_at_the_range_edges_is_kept(ts):
    assert _parsed_seven_day(used_percentage=5, resets_at=ts) is not None


@pytest.mark.parametrize("ts", [999_999_999, 4_000_000_001, 0, -5, 1794862800000],
                         ids=["just-below-1e9", "just-above-4e9", "zero", "negative",
                              "milliseconds"])
def test_reset_time_outside_the_range_stores_nothing(ts):
    assert parse_rate_limits(_one(used_percentage=5, resets_at=ts)) is None


@pytest.mark.parametrize("pct", [True, False, "83", None, math.nan, math.inf, -math.inf,
                                 [83], {"v": 83}],
                         ids=["true", "false", "string", "none", "nan", "inf", "-inf",
                              "list", "dict"])
def test_a_non_number_percentage_stores_nothing(pct):
    assert parse_rate_limits(_one(used_percentage=pct, resets_at=R)) is None


@pytest.mark.parametrize("ts", [True, "1794862800", None, math.nan, math.inf],
                         ids=["true", "string", "none", "nan", "inf"])
def test_a_non_number_reset_time_stores_nothing(ts):
    assert parse_rate_limits(_one(used_percentage=5, resets_at=ts)) is None


def test_a_missing_reset_time_stores_nothing():
    assert parse_rate_limits(_one(used_percentage=5)) is None


def test_a_missing_percentage_stores_nothing():
    assert parse_rate_limits(_one(resets_at=R)) is None


@pytest.mark.parametrize("window", ["83", 83, None, [83, R], True],
                         ids=["string", "number", "none", "list", "bool"])
def test_a_window_that_is_not_an_object_stores_nothing(window):
    assert parse_rate_limits({"rate_limits": {"seven_day": window}}) is None


def test_an_unknown_window_name_is_dropped():
    out = parse_rate_limits({"rate_limits": {
        "seven_day": {"used_percentage": 1, "resets_at": R},
        "seven_day_opus": {"used_percentage": 2, "resets_at": R}}})
    assert set(out) == {"seven_day"}


def test_only_an_unknown_window_stores_nothing():
    assert parse_rate_limits({"rate_limits": {
        "one_hour": {"used_percentage": 2, "resets_at": R}}}) is None


def test_an_invalid_window_is_dropped_and_a_valid_one_kept():
    out = parse_rate_limits({"rate_limits": {
        "five_hour": {"used_percentage": "7", "resets_at": R},
        "seven_day": {"used_percentage": 42, "resets_at": R}}})
    assert set(out) == {"seven_day"}


@pytest.mark.parametrize("data", [{}, {"rate_limits": None}, {"rate_limits": "x"},
                                  {"rate_limits": []}, {"rate_limits": {}},
                                  {"rate_limits": 5}],
                         ids=["absent", "none", "string", "list", "empty", "number"])
def test_no_usable_rate_limits_is_none(data):
    assert parse_rate_limits(data) is None


@pytest.mark.parametrize("data", [None, "x", 5, [], b"{}"],
                         ids=["none", "string", "number", "list", "bytes"])
def test_a_non_object_payload_never_raises(data):
    assert parse_rate_limits(data) is None


def test_a_gateway_spend_limit_with_dollar_fields_keeps_only_pct_and_reset():
    out = parse_rate_limits({"rate_limits": {"spend_limit": {
        "used_percentage": 12.5, "resets_at": R, "used_usd": 3.1, "limit_usd": 25}}})
    assert out["spend_limit"] == Window(12.5, R)


_SUBSETS = [c for n in range(1, 4) for c in itertools.combinations(NAMES, n)]


@pytest.mark.parametrize("subset", _SUBSETS, ids=["+".join(s) for s in _SUBSETS])
def test_any_subset_of_the_three_windows_is_kept_as_is(subset):
    data = {"rate_limits": {n: {"used_percentage": 10, "resets_at": R} for n in subset}}
    assert set(parse_rate_limits(data)) == set(subset)
