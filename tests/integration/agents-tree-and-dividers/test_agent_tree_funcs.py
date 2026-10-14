"""``aipager.agent_tree``: ``LINEAGE_SEP``, ``nearest_listed`` and
``top_ancestor`` (entrypoints.md "Exported functions"; design.md success
criteria 3, 6, 9)."""

from __future__ import annotations

import pytest

from aipager.agent_tree import LINEAGE_SEP, nearest_listed, top_ancestor

# child -> parent: R1 started D, D started V, V started W.
CHAIN = {"D": "R1", "V": "D", "W": "V"}


def test_lineage_separator():
    assert LINEAGE_SEP == " \u203a "


# ---- nearest_listed ----------------------------------------------------------------

def test_nearest_listed_is_the_parent_when_listed():
    assert nearest_listed(CHAIN, "W", {"V", "R1"}) == "V"


def test_nearest_listed_skips_an_unlisted_parent():
    assert nearest_listed(CHAIN, "W", {"R1"}) == "R1"


def test_nearest_listed_never_returns_the_agent_itself():
    assert nearest_listed(CHAIN, "W", {"W"}) == ""


def test_nearest_listed_is_empty_with_nothing_listed():
    assert nearest_listed(CHAIN, "W", set()) == ""


def test_nearest_listed_is_empty_for_an_agent_without_a_parent():
    assert nearest_listed(CHAIN, "R1", {"R1", "D"}) == ""


def test_nearest_listed_is_empty_for_an_unknown_agent():
    assert nearest_listed(CHAIN, "zzz", {"R1"}) == ""


def test_nearest_listed_accepts_a_list():
    assert nearest_listed(CHAIN, "V", ["R1"]) == "R1"


@pytest.mark.parametrize("parents", [
    {"a": "b", "b": "a"},
    {"a": "a"},
    {"a": "b", "b": "c", "c": "b"},
], ids=["two-cycle", "self", "tail-cycle"])
def test_nearest_listed_terminates_on_a_cycle(parents):
    assert nearest_listed(parents, "a", {"zzz"}) == ""


# ---- top_ancestor ---------------------------------------------------------------------

@pytest.mark.parametrize("agent,top", [
    ("D", "R1"), ("V", "R1"), ("W", "R1"),
], ids=["depth2", "depth3", "depth4"])
def test_top_ancestor_is_the_root(agent, top):
    assert top_ancestor(CHAIN, agent) == top


def test_top_ancestor_of_a_top_is_empty():
    assert top_ancestor(CHAIN, "R1") == ""


def test_top_ancestor_of_an_unknown_agent_is_empty():
    assert top_ancestor(CHAIN, "zzz") == ""


@pytest.mark.parametrize("parents", [
    {"a": "b", "b": "a"},
    {"a": "a"},
    {"a": "b", "b": "c", "c": "b"},
], ids=["two-cycle", "self", "tail-cycle"])
def test_top_ancestor_terminates_on_a_cycle(parents):
    assert isinstance(top_ancestor(parents, "a"), str)
