"""design.md success criteria "Threshold": ``usage_store.limit_level`` judged
on the rounded percentage, 0 below / 1 near / 2 reached; bad input gives 0."""

from __future__ import annotations

import math

import pytest

from aipager import usage_store


def test_near_limit_constant_is_90():
    assert usage_store.NEAR_LIMIT_PERCENT == 90


def test_limit_reached_constant_is_100():
    assert usage_store.LIMIT_REACHED_PERCENT == 100


@pytest.mark.parametrize("pct, level", [
    (0, 0), (50, 0), (89, 0), (89.4, 0), (89.49, 0),
    (89.5, 1), (89.9, 1), (90, 1), (90.0, 1), (95, 1), (99, 1), (99.4, 1),
    (99.5, 2), (100, 2), (100.0, 2), (101, 2), (250, 2), (999.9, 2), (1000, 2),
], ids=lambda v: str(v).replace(".", "p"))
def test_limit_level_partitions_and_edges(pct, level):
    assert usage_store.limit_level(pct) == level


@pytest.mark.parametrize("bad", [
    -1, -90, -100.0, math.nan, math.inf, -math.inf, True, False, "92", "", None,
    [92], {"pct": 92}, b"92",
], ids=["neg1", "neg90", "neg100", "nan", "inf", "neginf", "true", "false", "str92",
        "empty", "none", "list", "dict", "bytes"])
def test_limit_level_bad_input_is_zero(bad):
    assert usage_store.limit_level(bad) == 0


def test_limit_level_never_raises_on_huge_number():
    assert usage_store.limit_level(10**400) == 2
