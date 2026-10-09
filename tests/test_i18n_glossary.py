"""Check 6: names and commands are spelled exactly in every translation,
and a recurring term uses the glossary's word. Plus the shape of the
glossary files themselves (the decided forms of address)."""

from __future__ import annotations

import json

from aipager.i18n import locales
from tests import i18n_tool

tool = i18n_tool.load()

ADDRESS = {"es": "tú", "pt-BR": "você", "fr": "vous", "de": "du", "it": "tu", "ru": "ты",
           "uk": "ти", "tr": "sen", "fa": "شما", "ar": "أنت", "zh-Hans": "你",
           "ko": "해요체", "id": "kamu", "vi": "bạn"}

TERMS = {"session", "turn", "card", "permission", "prompt", "question", "allow", "deny",
         "stop", "resume", "queue", "agent", "background", "owner", "admin", "member",
         "chat", "group", "settings", "update", "folder", "model"}


def test_every_language_has_a_glossary_and_a_style_guide():
    for code in locales.LOCALES:
        if code == "en":
            continue
        g = json.loads((tool.GLOSSARY_DIR / f"{code}.json").read_text(encoding="utf-8"))
        assert g["address"] == ADDRESS[code], code
        assert set(g["terms"]) == TERMS, code
        assert all(v == "" for v in g["terms"].values()), "no translations in this run"
        assert isinstance(g["keep_english"], list)
        style = (tool.STYLE_DIR / f"{code}.md").read_text(encoding="utf-8")
        assert ADDRESS[code] in style
        assert "No exclamation marks" in style and "U+2014" in style
    assert "←" in (tool.STYLE_DIR / "fa.md").read_text(encoding="utf-8")
    assert "۰-۹" in (tool.STYLE_DIR / "fa.md").read_text(encoding="utf-8")
    assert "٠-٩" in (tool.STYLE_DIR / "ar.md").read_text(encoding="utf-8")


SRC = {"k": "toast.sent", "en": "Sent /compact to Claude Code in the session",
       "placeholders": [], "eg": {}}


def test_a_misspelled_name_or_command_fails():
    gloss = {"terms": {}}
    assert tool.check_glossary("de", SRC, "/compact an Claude Code gesendet", gloss) == []
    assert tool.check_glossary("de", SRC, "/kompakt an Claude Code gesendet", gloss)
    assert tool.check_glossary("de", SRC, "/compact an Claude-Code gesendet", gloss)


def test_a_glossary_term_must_use_its_word():
    gloss = {"terms": {"session": ["Sitzung"], "card": ""}}
    assert tool.check_glossary("de", SRC, "/compact an Claude Code in der Sitzung",
                               gloss) == []
    problems = tool.check_glossary("de", SRC, "/compact an Claude Code in der Session", gloss)
    assert any("'session'" in p for p in problems)


def test_the_glossary_runs_inside_the_catalog_check(tmp_path):
    path = tmp_path / "de.jsonl"
    path.write_text(json.dumps({"k": "toast.sent", "v": "an Claude Code gesendet",
                                "src": tool.src_hash(SRC)}) + "\n", encoding="utf-8")
    problems = tool.check_catalogs([SRC], {"de": path}, (), glossaries={"de": {"terms": {}}})
    assert any("/compact" in p for p in problems)
