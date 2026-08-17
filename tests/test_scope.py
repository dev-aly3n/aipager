"""Tests for aipager.scope — aipager.yaml loader + dumper."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from aipager.scope import (
    Member,
    Scope,
    ScopeConfigError,
    disambiguated_name,
    dump_scopes,
    load_scopes,
    scope_suffix,
    strip_scope_suffix,
)


def test_scope_suffix_dm_and_group():
    assert scope_suffix(256113222, "dm") == "d256113222"
    assert scope_suffix(-4152307515, "group") == "g4152307515"


def test_disambiguated_name():
    assert disambiguated_name("jim", 256113222, "dm") == "claude-jim__d256113222"
    assert disambiguated_name("jim", -4152307515, "group") == "claude-jim__g4152307515"


def test_load_scopes_missing_returns_none(tmp_path: Path):
    assert load_scopes(tmp_path / "nope.yaml") is None


def test_load_scopes_happy_group_and_dm(tmp_path: Path):
    f = tmp_path / "aipager.yaml"
    f.write_text(textwrap.dedent("""\
        schema_version: 2
        bot_token: "TOK"
        scopes:
          - kind: group
            chat_id: -4152307515
            label: dev-team
            deny_tools: [Bash]
            members:
              - {id: 256113222, label: aly3n, role: owner}
              - {id: 127320832, label: arian, role: user, deny_tools: [SlashCommand]}
          - kind: dm
            chat_id: 256113222
            label: aly3n DM
            members:
              - {id: 256113222, label: aly3n, role: owner}
    """))
    result = load_scopes(f)
    assert result is not None
    scopes, token = result
    assert token == "TOK"
    assert len(scopes) == 2
    g = scopes[0]
    assert g.kind == "group" and g.chat_id == -4152307515
    assert g.deny_tools == ("Bash",)
    assert g.members[1].role == "user"
    assert g.members[1].deny_tools == ("SlashCommand",)
    assert scopes[1].kind == "dm"


def test_round_trip_preserves_overrides(tmp_path: Path):
    f = tmp_path / "aipager.yaml"
    scopes = [
        Scope(
            chat_id=-100,
            kind="group",
            label="g",
            members=(
                Member(id=1, label="a", role="owner"),
                Member(id=2, label="b", role="user",
                       deny_tools=("Bash",), allow_tools=("Read",),
                       bypass_safety=True, bypass_role_denies=False),
            ),
            deny_tools=("WebFetch",),
        ),
    ]
    dump_scopes(scopes, "TOK2", f)
    again, token = load_scopes(f)
    assert token == "TOK2"
    m = again[0].members[1]
    assert m.deny_tools == ("Bash",)
    assert m.allow_tools == ("Read",)
    assert m.bypass_safety is True
    assert m.bypass_role_denies is False
    assert again[0].deny_tools == ("WebFetch",)


def test_bad_schema_version(tmp_path: Path):
    f = tmp_path / "aipager.yaml"
    f.write_text("schema_version: 1\nbot_token: x\nscopes: []\n")
    with pytest.raises(ScopeConfigError, match="schema_version"):
        load_scopes(f)


def test_missing_bot_token(tmp_path: Path):
    f = tmp_path / "aipager.yaml"
    f.write_text("schema_version: 2\nscopes:\n  - {kind: dm, chat_id: 1, members: [{id: 1, label: a, role: admin}]}\n")
    with pytest.raises(ScopeConfigError, match="bot_token"):
        load_scopes(f)


def test_dm_scope_must_have_one_member(tmp_path: Path):
    f = tmp_path / "aipager.yaml"
    f.write_text(textwrap.dedent("""\
        schema_version: 2
        bot_token: x
        scopes:
          - kind: dm
            chat_id: 1
            members:
              - {id: 1, label: a, role: admin}
              - {id: 2, label: b, role: user}
    """))
    with pytest.raises(ScopeConfigError, match="exactly"):
        load_scopes(f)


def test_unknown_kind(tmp_path: Path):
    f = tmp_path / "aipager.yaml"
    f.write_text("schema_version: 2\nbot_token: x\nscopes:\n  - {kind: channel, chat_id: 1, members: [{id: 1, label: a, role: admin}]}\n")
    with pytest.raises(ScopeConfigError, match="kind"):
        load_scopes(f)


def test_duplicate_chat_id(tmp_path: Path):
    f = tmp_path / "aipager.yaml"
    f.write_text(textwrap.dedent("""\
        schema_version: 2
        bot_token: x
        scopes:
          - {kind: dm, chat_id: 5, members: [{id: 5, label: a, role: admin}]}
          - {kind: dm, chat_id: 5, members: [{id: 5, label: a, role: admin}]}
    """))
    with pytest.raises(ScopeConfigError, match="duplicate scope chat_id"):
        load_scopes(f)


def test_empty_scopes_rejected(tmp_path: Path):
    f = tmp_path / "aipager.yaml"
    f.write_text("schema_version: 2\nbot_token: x\nscopes: []\n")
    with pytest.raises(ScopeConfigError, match="non-empty"):
        load_scopes(f)


# ===== strip_scope_suffix — the inverse of a disambiguated name ==========
#
# Reported 2026-08-17: a killed session reappeared in the gone list as
# "Jkhk__d256113222" instead of "Jkhk". `SessionRegistry.get_or_create`
# derives a label when it adopts a session it did not create, and it was
# stripping only the "claude-" prefix, leaving the scope disambiguator.



@pytest.mark.parametrize("name,expected", [
    ("Jkhk__d256113222", "Jkhk"),          # the reported case
    ("dev__g4152307515", "dev"),           # group scope
    ("dev", "dev"),                        # unscoped / legacy
    ("", ""),
])
def test_a_real_suffix_is_removed(name, expected):
    assert strip_scope_suffix(name) == expected


@pytest.mark.parametrize("name", [
    "my__thing",        # underscores are legal in a label
    "my__d",            # no digits — not a suffix
    "my__dabc",         # not digits
    "my__x123",         # neither d nor g
    "my__1234",         # no kind letter
    "d256113222",       # a suffix shape with no separator is just a name
    "my__d123x",        # trailing junk after the digits
])
def test_something_that_only_looks_like_a_suffix_survives(name):
    assert strip_scope_suffix(name) == name


def test_only_the_trailing_suffix_goes():
    """A label may itself contain `__`; only the disambiguator at the end
    is the machine's, and only one of them."""
    assert strip_scope_suffix("my__thing__d123") == "my__thing"
    assert strip_scope_suffix("a__d1__d2") == "a__d1"


@pytest.mark.parametrize("label", ["dev", "Jkhk", "my__thing", "a", "x-1_2"])
@pytest.mark.parametrize("chat_id,kind", [(256113222, "dm"), (-4152307515, "group")])
def test_round_trip_with_the_real_name_builder(label, chat_id, kind):
    """Pinned against `disambiguated_name` itself, so the two cannot
    drift into disagreeing about the format."""
    name = disambiguated_name(label, chat_id, kind)
    assert strip_scope_suffix(name.removeprefix("claude-")) == label
    assert name.endswith("__" + scope_suffix(chat_id, kind))
