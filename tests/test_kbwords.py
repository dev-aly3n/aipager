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
    # "Status" is German's own label (enabled since translation run 2).
    for label in ("kill", "sTatus", "STOP", "templates"):
        assert label not in labels, label


def test_custom_buttons_match_as_written(bot):
    assert kbwords.custom_action(bot, "Run tests") == ("tpl", "Run the tests")
    assert kbwords.custom_action(bot, "Clear") == ("cmd", "/clear")
    assert kbwords.custom_action(bot, "Opus") == ("model", "/model opus")
    assert kbwords.custom_action(bot, "clear") is None
    assert kbwords.custom_action(SimpleNamespace(), "Clear") is None


def test_a_custom_label_with_end_marks_matches_the_label(bot):
    """A model label tapped from a right-to-left row may carry RLM at its
    ends; it still runs the label's payload, and nothing else."""
    rlm = "\u200f"
    assert kbwords.custom_action(bot, rlm + "Opus" + rlm) == ("model", "/model opus")
    assert kbwords.custom_action(bot, "Clear" + rlm) == ("cmd", "/clear")
    assert kbwords.classify(bot, rlm + "Opus") == "acting"
    assert kbwords.custom_action(bot, rlm + "opus" + rlm) is None
    assert kbwords.custom_action(bot, rlm) is None


def test_the_label_as_written_wins_over_a_normalized_one():
    """keyboard.json's own spelling is matched first: a label written with
    a mark is its own button even when a bare twin exists."""
    rlm = "\u200f"
    bot = SimpleNamespace(_template_map={"Opus": "bare"},
                          _model_map={rlm + "Opus": "/model marked"})
    assert kbwords.custom_action(bot, rlm + "Opus") == ("model", "/model marked")
    assert kbwords.custom_action(bot, "Opus") == ("tpl", "bare")


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


# ---- default labels, translated (phase 2) ------------------------------------------

def test_default_labels_are_the_shipped_defaults():
    pairs = list(config._DEFAULT_TEMPLATES) + list(config._DEFAULT_COMMANDS)
    assert list(kbwords.DEFAULT_LABELS) == pairs
    assert all(label == msg for (label, _p), msg in kbwords.DEFAULT_LABELS.items())


def test_a_custom_label_is_never_translated(german):
    german({"kbl.tpl.run_tests": "Tests laufen", "kbl.cmd.clear": "Leeren"})
    from aipager import i18n
    de = i18n.get("de")
    assert kbwords.shown_label(de, "Run tests", "Run the tests") == "Tests laufen"
    assert kbwords.shown_label(de, "Clear", "/clear") == "Leeren"
    # keyboard.json's own label, or a default label with another payload,
    # and every model: as written.
    assert kbwords.shown_label(de, "Run tests", "npm test please") == "Run tests"
    assert kbwords.shown_label(de, "Deploy", "Deploy it") == "Deploy"
    assert kbwords.shown_label(de, "Opus", "/model opus") == "Opus"
    assert kbwords.shown_label(i18n.get("en"), "Run tests", "Run the tests") == "Run tests"


def test_a_translated_default_label_sends_the_english_payload(german, bot):
    german({"kbl.tpl.run_tests": "Tests laufen", "kbl.cmd.clear": "Leeren"})
    assert kbwords.custom_action(bot, "Tests laufen") == ("tpl", "Run the tests")
    assert kbwords.custom_action(bot, "Leeren") == ("cmd", "/clear")
    assert kbwords.classify(bot, "Leeren") == "acting"
    assert "Leeren" in kbwords.group_labels(bot, -1001)
    # Not once keyboard.json replaced the pair.
    custom = SimpleNamespace(_template_map={"Run tests": "npm test please"},
                             _command_map={}, _model_map={})
    assert kbwords.custom_action(custom, "Tests laufen") is None
    assert kbwords.custom_action(custom, "Run tests") == ("tpl", "npm test please")


def test_a_group_tap_is_compared_normalized(german, bot):
    german({"kb.status": "\u200fzustand"})
    assert kbwords.is_group_label(bot, -1001, "zustand")
    assert kbwords.is_group_label(bot, -1001, "\u200fzustand")
    assert kbwords.is_group_label(bot, -1001, "status")
    assert not kbwords.is_group_label(bot, -1001, "kill")
    assert not kbwords.is_group_label(bot, -1001, "Zustand")
    assert not kbwords.is_group_label(bot, -1001, "")


def test_a_keyboard_word_with_a_space_fails(german):
    german({"kb.stop": "\u200fhalt an"})
    problems = kbwords.label_problems(("en", "de"))
    assert any("(stop) must be one word" in p for p in problems)
    german({"kb.stop": "\u200fhalt"})
    assert not any("one word" in p for p in kbwords.label_problems(("en", "de")))


def test_a_default_label_clash_fails(german):
    german({"kbl.tpl.continue": "Weiter", "kbl.cmd.compact": "weiter"})
    problems = kbwords.label_problems(("en", "de"))
    assert any("means both tpl:Continue and cmd:Compact" in p for p in problems)
    german({"kbl.tpl.commit": "status"})
    assert any("means both status and tpl:Commit" in p
               for p in kbwords.label_problems(("en", "de")))


def test_a_mode_word_clash_fails(german):
    """Check 10 reads the typed mode words (mode.word.*), not the shown
    two-word labels."""
    german({"mode.word.ask": "Modus", "mode.word.auto": "modus"})
    problems = kbwords.label_problems(("en", "de"))
    assert any("means both mode:ask and mode:auto" in p for p in problems)
    german({"mode.word.ask": "Frag mich", "mode.word.auto": "Auto",
            "mode.ask": "💬 Frag mich-Modus", "mode.auto": "🤖 Auto-Modus"})
    assert any("(mode:ask) must be one word" in p
               for p in kbwords.label_problems(("en", "de")))
    assert kbwords.mode_word("🤖️ Auto ") == "auto"


def test_two_word_mode_labels_pass_check_10(german):
    """A label is the emoji, the word and the word for mode: its space is
    no problem, since nobody types the label."""
    german({"mode.word.ask": "Fragen", "mode.word.auto": "Auto",
            "mode.ask": "💬 Fragen-Modus", "mode.auto": "🤖 Auto-Modus"})
    assert kbwords.label_problems(("en", "de")) == []


def test_a_mode_label_must_show_its_typed_word(german):
    """What people read on the label is what /mode accepts: a label that
    drops or bends its typed word fails check 10."""
    german({"mode.word.ask": "Fragen", "mode.word.auto": "Auto",
            "mode.ask": "💬 Frage-Modus", "mode.auto": "🤖 Auto-Modus"})
    problems = kbwords.label_problems(("en", "de"))
    assert problems == ["de '💬 Frage-Modus' ∌ 'Fragen'"]
    german({"mode.word.ask": "Fragen", "mode.word.auto": "Auto",
            "mode.ask": "💬 Fragen-Modus", "mode.auto": "🤖 Automatik-Modus"})
    assert kbwords.label_problems(("en", "de")) == []
    german({"mode.word.ask": "Fragen", "mode.word.auto": "Automatik",
            "mode.ask": "💬 Fragen-Modus", "mode.auto": "🤖 Auto-Modus"})
    assert kbwords.label_problems(("en", "de")) == ["de '🤖 Auto-Modus' ∌ 'Automatik'"]


# ---- right-to-left labels carry a mark at both ends ------------------------------------

_RLM = "\u200f"


def _tapped(label: str) -> list[str]:
    """*label* as a tap may bring it: as rendered (a mark at each end in
    fa/ar), with one end's mark dropped by a client, or with none."""
    bare = label.strip(_RLM)
    return [label, _RLM + bare, bare + _RLM, _RLM + bare + _RLM, bare]


@pytest.mark.parametrize("code", ["fa", "ar"])
def test_a_label_with_marks_at_both_ends_reads_as_the_bare_label(code, bot):
    from aipager import i18n
    from aipager.bot import mode_labels
    tr = i18n.get(code)
    for action, msg in kbwords.WORDS.items():
        label = tr.text(msg)
        assert label.startswith(_RLM) and label.endswith(_RLM)
        for tapped in _tapped(label):
            assert kbwords.norm(tapped) == kbwords.norm(label.strip(_RLM))
            assert kbwords.bare_word(tapped) == action, ascii(tapped)
            assert kbwords.classify(bot, tapped) == kbwords.classify(bot, str(msg))
            if action != "kill":
                assert kbwords.is_group_label(bot, -1001, tapped), ascii(tapped)
    for action, msg in kbwords.NAV.items():
        for tapped in _tapped(tr.text(msg) if isinstance(msg, core.Msg) else msg):
            assert kbwords.nav_action(tapped) == f"nav:{action}", ascii(tapped)
    shipped = SimpleNamespace(
        _template_map={}, _command_map={}, _model_map={},
        registry=SimpleNamespace(live_labels=lambda chat_id: set()))
    for (label, payload), msg in kbwords.DEFAULT_LABELS.items():
        kind = "tpl" if not payload.startswith("/") else "cmd"
        getattr(shipped, f"_{'template' if kind == 'tpl' else 'command'}_map")[label] = payload
    for (label, payload), msg in kbwords.DEFAULT_LABELS.items():
        for tapped in _tapped(tr.text(msg)):
            assert kbwords.custom_action(shipped, tapped, chat_code=code)[1] == payload, (
                ascii(tapped))
    for msg in (mode_labels.MODE_AUTO, mode_labels.MODE_ASK, mode_labels.MODE_FULL):
        word = kbwords.mode_word(tr.text(msg).strip(_RLM))
        for tapped in _tapped(tr.text(msg)):
            assert kbwords.mode_word(tapped) == word, ascii(tapped)
