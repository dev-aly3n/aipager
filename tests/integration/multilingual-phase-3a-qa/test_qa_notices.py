"""Success criterion 16: every notify notice in scope reads in the session
chat's language and ``session_end`` picks its line by the source id."""

from __future__ import annotations

import re

import pytest

PERSIAN = re.compile("[؀-ۿ]")
CYRILLIC = re.compile("[Ѐ-ӿ]")
SCRIPT = {"fa": PERSIAN, "ru": CYRILLIC}
NOTICES = ("notice_quiet_turn", "notice_context", "notice_compact", "notice_session_end",
           "notice_misc")

@pytest.fixture
def render(scenario_out):
    return scenario_out


@pytest.mark.parametrize("lang", ["fa", "ru"])
@pytest.mark.parametrize("name", NOTICES)
def test_every_notice_and_button_is_in_the_chats_script(render, name, lang):
    assert [t for t in render(name, lang) if not SCRIPT[lang].search(t)] == []


@pytest.mark.parametrize("lang", ["fa", "ru"])
@pytest.mark.parametrize("name", NOTICES)
def test_every_notice_names_the_session(render, name, lang):
    texts = [t for t in render(name, lang) if not t.startswith("[btn]")]
    assert all("x1" in t for t in texts)


@pytest.mark.parametrize("lang", ["fa", "ru"])
def test_session_end_lines_split_like_the_english_by_source(render, lang):
    """Two sources share a line in Persian or Russian exactly when they do
    in English (other, unknown, a new id and none read "exited";
    disappeared reads "crashed")."""
    en = render("notice_session_end", "en")
    other = render("notice_session_end", lang)
    pairs = [(i, j) for i in range(len(en)) for j in range(len(en)) if i < j]
    assert [(i, j) for i, j in pairs if (en[i] == en[j]) != (other[i] == other[j])] == []


@pytest.mark.parametrize("lang", ["fa", "ru"])
def test_session_end_has_six_distinct_lines(render, lang):
    assert len(set(render("notice_session_end", lang))) == 6


@pytest.mark.parametrize("lang", ["en", "fa", "ru"])
def test_the_memory_cap_owner_and_non_owner_variants_differ(render, lang):
    out = [t for t in render("notice_misc", lang)]
    cap = out[-6:]
    assert cap[2] != cap[5]


@pytest.mark.parametrize("lang", ["fa", "ru"])
def test_the_memory_cap_keeps_hook_and_tool_as_code(render, lang):
    cap = render("notice_misc", lang)[-6]
    assert "<code>Bash</code>" in cap and "<code>aipager-hook</code>" in cap


@pytest.mark.parametrize("lang", ["fa", "ru"])
def test_the_queued_preview_is_shown_escaped_and_verbatim(render, lang):
    assert any("fix x1 &amp; &lt;b&gt;" in t for t in render("notice_misc", lang))


@pytest.mark.parametrize("lang", ["fa", "ru"])
def test_the_waiting_summary_is_shown_verbatim(render, lang):
    assert any("Bash: ls" in t for t in render("notice_misc", lang))


@pytest.mark.parametrize("lang", ["fa", "ru"])
def test_quiet_turn_keeps_tool_names(render, lang):
    first = render("notice_quiet_turn", lang)[0]
    assert "Bash" in first and "WebSearch" in first


def test_persian_compacted_uses_the_left_arrow(render):
    assert "←" in render("notice_compact", "fa")[0]


def test_russian_compacted_keeps_the_right_arrow(render):
    assert "→" in render("notice_compact", "ru")[0]
