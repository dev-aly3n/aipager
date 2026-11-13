"""Clear never enters Telegram's "/" autocomplete, and ``clear`` / ``reset``
are reserved session names: design.md success criteria 14 and 15.
"""

from __future__ import annotations

import re

import pytest

from aipager.bot.lifecycle import LifecycleMixin
from aipager.dtach import inject
from aipager.miniapp.launch import validate_session_name

LABEL_SETS = [set(), {"x1", "frontend"}, {"dev"}]
LABEL_IDS = ["no-sessions", "two-sessions", "one-session"]


@pytest.fixture(autouse=True)
def _miniapp_on(monkeypatch):
    monkeypatch.setattr("aipager.config.MINIAPP_ENABLED", True)


def _names(labels, group):
    return [c.command for c in LifecycleMixin._command_list(set(labels), group=group)]


# ---- criterion 14 ----------------------------------------------------------------

@pytest.mark.parametrize("group", [False, True], ids=["private", "group"])
@pytest.mark.parametrize("labels", LABEL_SETS, ids=LABEL_IDS)
def test_no_slash_command_contains_clear(labels, group):
    assert [n for n in _names(labels, group) if "clear" in n.lower()] == []


@pytest.mark.parametrize("group", [False, True], ids=["private", "group"])
@pytest.mark.parametrize("labels", LABEL_SETS, ids=LABEL_IDS)
def test_no_slash_command_is_reset(labels, group):
    assert [n for n in _names(labels, group) if n.lower() == "reset"] == []


@pytest.mark.parametrize("group", [False, True], ids=["private", "group"])
def test_no_slash_command_describes_clearing_a_conversation(group):
    """Under any name: no entry whose description is about clearing."""
    cmds = LifecycleMixin._command_list({"x1"}, group=group)
    assert [c.command for c in cmds if re.search(r"\bclear", c.description, re.I)] == []


@pytest.mark.parametrize("group", [False, True], ids=["private", "group"])
def test_slash_menu_is_not_empty(group):
    """Control: the pin above is not vacuous."""
    assert len(_names({"x1"}, group)) > 0


# ---- criterion 15 ----------------------------------------------------------------

@pytest.mark.parametrize("name", ["clear", "reset"])
def test_name_is_refused_as_reserved(name):
    clean, err = validate_session_name(name)
    assert (clean, "reserved" in err.lower()) == ("", True)


@pytest.mark.parametrize("name", ["clear", "reset"])
def test_name_is_in_dtach_reserved_set(name):
    assert name in inject._RESERVED


@pytest.mark.parametrize("name", ["clearing", "reset2", "x1"])
def test_near_miss_names_stay_allowed(name):
    """Boundary: only the exact words are reserved."""
    clean, err = validate_session_name(name)
    assert (clean, err) == (name, "")


def test_page_reserved_names_match_the_canonical_set():
    from aipager.miniapp.static import SOURCE_INDEX_HTML
    m = re.search(r"RENAME_RESERVED\s*=\s*\{(.*?)\}", SOURCE_INDEX_HTML, re.S)
    page = set(re.findall(r'"([^"]+)"\s*:\s*true', m.group(1)))
    assert page == set(inject._RESERVED)


@pytest.mark.parametrize("name", ["clear", "reset"])
def test_launch_session_refuses_the_reserved_name(run_async, name):
    ok, err = run_async(inject.launch_session(name, mode="ask"))
    assert (ok, "reserved" in err.lower()) == (False, True)
