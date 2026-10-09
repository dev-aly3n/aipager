"""The one reply-keyboard resolver (the multilingual design, 5.5), and
check 10: no keyboard label clash in any language, no Latin label that
could be a session name."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from aipager import config
from aipager.bot import kbwords
from aipager.i18n import core, locales


@pytest.fixture
def bot():
    return SimpleNamespace(
        _template_map={"Run tests": "Run the tests"},
        _command_map={"Clear": "/clear"},
        _model_map={"Opus": "/model opus"},
        registry=SimpleNamespace(live_labels=lambda chat_id: {"x1"}))


@pytest.fixture
def german(tmp_path, monkeypatch):
    """A fake enabled language whose catalog the test writes."""
    monkeypatch.setattr(core, "LOCALES_DIR", tmp_path)
    monkeypatch.setattr(locales, "_TEST_ENABLED", frozenset({"de"}))
    core.reset_caches()

    def write(labels):
        lines = [json.dumps({"k": k, "v": v, "src": "0"}, ensure_ascii=False)
                 for k, v in sorted(labels.items())]
        (tmp_path / "de.jsonl").write_text("".join(x + "\n" for x in lines),
                                           encoding="utf-8")
        core.reset_caches()
    return write


def test_nav_labels_equal_config():
    assert kbwords.KB_TEMPLATES == config.TEMPLATES_BUTTON
    assert kbwords.KB_COMMANDS == config.COMMANDS_BUTTON
    assert kbwords.KB_MODELS == config.MODELS_BUTTON
    assert kbwords.KB_BACK == config.BACK_BUTTON
    assert kbwords.KB_APP == config.APP_BUTTON


def test_navigation_buttons_match_the_whole_text():
    assert kbwords.nav_action("Templates") == "nav:templates"
    assert kbwords.nav_action("« Back") == "nav:back"
    assert kbwords.nav_action("📱 App") == "nav:app"
    assert kbwords.nav_action("Model ›") == "nav:models"
    assert kbwords.nav_action("templates") is None
    assert kbwords.nav_action("Templates please") is None
    # A variation selector a client adds or drops does not matter.
    assert kbwords.nav_action("📱" + chr(0xfe0f) + " App") == "nav:app"


def test_dm_bare_words_ignore_case():
    for text, action in (("status", "status"), ("STOP", "stop"), ("Kill", "kill"),
                         ("NEW", "new")):
        assert kbwords.bare_word(text) == action
    assert kbwords.bare_word("stopp") is None


def test_spaces_never_make_a_bare_word(german):
    assert kbwords.bare_word("stop now") is None
    assert kbwords.bare_word(" stop") is None
    assert kbwords.bare_word("/stop") is None
    assert kbwords.bare_word(None) is None
    # The rule, not the labels: a label with a space or a slash is still
    # not a bare word (the text router's rule, kept for every language).
    german({"kb.new": "neue sitzung", "kb.stop": "/halt"})
    assert kbwords.bare_word("neue sitzung") is None
    assert kbwords.bare_word("/halt") is None
    assert kbwords.classify(SimpleNamespace(), "neue sitzung") == "looking"


def test_group_words_are_exact(bot):
    labels = kbwords.group_labels(bot, -1001)
    for label in ("status", "stop", "new", "Templates", "Commands", "Model ›", "« Back",
                  "📱 App", "Run tests", "Clear", "Opus", "x1"):
        assert label in labels, label
    for label in ("kill", "Status", "STOP", "templates"):
        assert label not in labels, label


def test_custom_buttons_match_as_written(bot):
    assert kbwords.custom_action(bot, "Run tests") == ("tpl", "Run the tests")
    assert kbwords.custom_action(bot, "Clear") == ("cmd", "/clear")
    assert kbwords.custom_action(bot, "Opus") == ("model", "/model opus")
    assert kbwords.custom_action(bot, "clear") is None
    assert kbwords.custom_action(SimpleNamespace(), "Clear") is None


def test_classify_keeps_the_capture_rules(bot):
    for text in ("new", " Status ", "Templates", "« Back", "📱 App"):
        assert kbwords.classify(bot, text) == "looking", text
    for text in ("stop", "KILL ", "Run tests", "Clear", "Opus"):
        assert kbwords.classify(bot, text) == "acting", text
    for text in ("my-session", "stop it", "clear", "/new"):
        assert kbwords.classify(bot, text) is None, text


def test_norm_folds_only_what_it_should():
    assert kbwords.norm("A\u200fB\u2068C\u2069\ufe0f") == "ABC"
    assert kbwords.norm("يك") == "یک"
    assert kbwords.norm("Stop  Now") == "Stop  Now"
    assert kbwords.norm("Ｓtop") == "Stop"      # full-width S (NFKC)
    assert kbwords.norm(None) == ""


def test_every_enabled_language_is_accepted(german, bot):
    german({"kb.stop": "halt", "kb.templates": "Vorlagen ›", "kb.status": "zustand"})
    assert kbwords.bare_word("HALT") == "stop"
    assert kbwords.bare_word("stop") == "stop"
    assert kbwords.nav_action("Vorlagen ›") == "nav:templates"
    assert "Vorlagen ›" in kbwords.group_labels(bot, -1)


# ---- check 10 -------------------------------------------------------------------

def test_the_shipped_labels_pass_check_10():
    assert kbwords.label_problems(locales.enabled_codes() + ("qps",)) == []


def test_a_label_clash_fails(german):
    german({"kb.stop": "ende", "kb.kill": "Ende"})
    problems = kbwords.label_problems(("en", "de"))
    assert any("means both" in p for p in problems)


def test_a_clash_across_languages_fails(german):
    german({"kb.kill": "stop"})
    problems = kbwords.label_problems(("en", "de"))
    assert any("'stop' means both" in p for p in problems)


def test_a_latin_label_that_could_be_a_session_name_fails(german):
    german({"kb.status": "zustand"})
    problems = kbwords.label_problems(("en", "de"))
    assert any("'zustand' could be a session name" in p for p in problems)


def test_english_templates_and_commands_are_grandfathered():
    assert kbwords.GRANDFATHERED == {"Templates", "Commands"}
    from aipager.dtach.inject import _VALID_NAME
    assert all(_VALID_NAME.match(x) for x in kbwords.GRANDFATHERED)
