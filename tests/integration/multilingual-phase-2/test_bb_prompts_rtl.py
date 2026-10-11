"""Black-box: permission and question prompts in a Persian or Russian group
(entrypoints.md "Permission and question prompts", "Format rules"):
the <pre> command is never touched by a mark, a hidden direction control
from Claude is still revealed, buttons and answered lines are translated,
the question answer uses ``←`` in Persian, and the audit keeps English.

Driven through the real notify path and taps (the suite's ``_World``
harness for separate prompts).
"""

from __future__ import annotations

import json

import pytest

from aipager import audit as audit_mod
from aipager import preferences
from tests.i18n_ml2_bb import (
    BIDI_CONTROLS,
    CYRILLIC,
    FA_DIGITS,
    PERSIAN,
    RLM,
    button_texts,
    catalog,
    code_segments,
)
from tests.test_separate_permission_prompt import BOB, GROUP, HOOK, _World

COMMAND = "rm -rf build9/ && echo 42"


@pytest.fixture
def world(mk_bot, monkeypatch):
    return _World(mk_bot, monkeypatch)


def _send_prompt(world, run_async, sess, tool_info):
    run_async(world.bot.notify(sess, "permission_prompt", {
        "tool_info": tool_info, "hook_reply": dict(HOOK)}))
    calls = world.bot._app.bot.send_message.await_args_list
    assert calls, "no prompt was sent"
    c = calls[-1]
    text = c.kwargs.get("text", c.args[1] if len(c.args) > 1 else "")
    markup = c.kwargs.get("reply_markup")
    world.bot._app.bot.send_message.reset_mock()
    return text, markup


def _bash(cmd=COMMAND):
    return {"name": "Bash", "summary": f"Bash: {cmd}", "input": {"command": cmd}}


def _question():
    return {"name": "AskUserQuestion", "summary": "AskUserQuestion",
            "input": {"questions": [{"question": "Which db 2?",
                                     "options": [{"label": "Postgres 16"},
                                                 {"label": "SQLite"}]}]}}


def _sess(world, code):
    preferences.set_ui_language(GROUP, code)
    s = world.session()
    s.label = "x12"
    return s


def _audit():
    path = audit_mod.AUDIT_LOG_PATH
    return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []


# ---- the prompt message ---------------------------------------------------------

def test_a_persian_prompt_keeps_the_command_intact(world, run_async):
    text, _m = _send_prompt(world, run_async, _sess(world, "fa"), _bash())
    assert any(COMMAND.replace("&", "&amp;") in seg for seg in code_segments(text))


def test_a_persian_prompt_puts_no_mark_inside_code(world, run_async):
    text, _m = _send_prompt(world, run_async, _sess(world, "fa"), _bash())
    segs = code_segments(text)
    assert segs and not any(BIDI_CONTROLS.search(seg) for seg in segs)


def test_a_persian_prompt_puts_no_persian_digit_inside_code(world, run_async):
    text, _m = _send_prompt(world, run_async, _sess(world, "fa"), _bash())
    assert not any(FA_DIGITS.search(seg) for seg in code_segments(text))


def test_a_persian_prompt_heading_is_persian(world, run_async):
    text, _m = _send_prompt(world, run_async, _sess(world, "fa"), _bash())
    assert PERSIAN.search(text.split("\n", 1)[0])


def test_a_persian_prompt_heading_starts_right_to_left(world, run_async):
    text, _m = _send_prompt(world, run_async, _sess(world, "fa"), _bash())
    assert text.startswith(RLM)


def test_a_persian_prompt_keeps_the_session_name_latin(world, run_async):
    text, _m = _send_prompt(world, run_async, _sess(world, "fa"), _bash())
    assert "x12" in text.split("\n", 1)[0]


def test_a_persian_prompt_has_persian_buttons(world, run_async):
    _t, markup = _send_prompt(world, run_async, _sess(world, "fa"), _bash())
    labels = button_texts(markup)
    want = catalog("fa")["perm.btn.allow"]["v"]
    assert any(want in b for b in labels)


def test_a_russian_prompt_has_no_marks(world, run_async):
    text, markup = _send_prompt(world, run_async, _sess(world, "ru"), _bash())
    assert not BIDI_CONTROLS.search(text + "".join(button_texts(markup)))


def test_a_russian_prompt_has_russian_buttons(world, run_async):
    _t, markup = _send_prompt(world, run_async, _sess(world, "ru"), _bash())
    assert any(CYRILLIC.search(b) for b in button_texts(markup))


@pytest.mark.parametrize("code", ["fa", "ru"])
def test_the_code_element_is_byte_identical_to_english(world, run_async, code):
    english, _m = _send_prompt(world, run_async, _sess(world, ""), _bash())
    other, _m = _send_prompt(world, run_async, _sess(world, code), _bash())
    assert code_segments(other) == code_segments(english)


def test_an_english_prompt_has_no_marks(world, run_async):
    text, markup = _send_prompt(world, run_async, _sess(world, ""), _bash())
    assert not BIDI_CONTROLS.search(text + "".join(button_texts(markup)))


def test_an_english_prompt_has_todays_buttons(world, run_async):
    _t, markup = _send_prompt(world, run_async, _sess(world, ""), _bash())
    labels = button_texts(markup)
    assert "✅ Allow" in labels and "❌ Deny" in labels


# ---- answered lines ----------------------------------------------------------------

def _answer(world, run_async, code, verb, tool_info):
    sess = _sess(world, code)
    _send_prompt(world, run_async, sess, tool_info)
    query = world.tap(run_async, GROUP, BOB, verb)
    return world.lines(GROUP), query


def test_a_persian_option_line_counts_in_persian_digits(world, run_async):
    lines, _q = _answer(world, run_async, "fa", "opt1", _question())
    verb = catalog("fa")["ans.verb.option"]["v"].replace("{n}", "\u06f2")
    assert verb in lines[-1][0]


def test_a_persian_option_line_keeps_claudes_question_digits(world, run_async):
    lines, _q = _answer(world, run_async, "fa", "opt1", _question())
    assert "Which db 2?" in lines[-1][0]


def test_a_persian_option_line_has_no_right_arrow(world, run_async):
    lines, _q = _answer(world, run_async, "fa", "opt1", _question())
    assert "\u2192" not in lines[-1][0]


def test_an_english_option_line_is_todays_text(world, run_async):
    lines, _q = _answer(world, run_async, "", "opt1", _question())
    assert lines[-1][0] == "\u00b7 <b>x12</b> \u00b7 Selected option 2 \u00b7 @bob \u00b7 Which db 2?"


def test_a_russian_allow_line_is_russian(world, run_async):
    lines, _q = _answer(world, run_async, "ru", "allow", _bash())
    assert CYRILLIC.search(lines[-1][0])


def test_a_russian_allow_line_has_no_marks(world, run_async):
    lines, _q = _answer(world, run_async, "ru", "allow", _bash())
    assert not BIDI_CONTROLS.search(lines[-1][0])


@pytest.mark.parametrize("code", ["fa", "ru"])
@pytest.mark.parametrize("verb, action", [("allow", "Allowed"), ("deny", "Denied")])
def test_the_audit_action_stays_english(world, run_async, code, verb, action):
    _answer(world, run_async, code, verb, _bash())
    assert _audit()[-1]["action"] == action


@pytest.mark.parametrize("code", ["fa", "ru"])
def test_the_option_audit_stays_english(world, run_async, code):
    _answer(world, run_async, code, "opt1", _question())
    assert _audit()[-1]["action"] == "Selected option 2"


@pytest.mark.parametrize("code", ["fa", "ru"])
def test_the_keys_claude_gets_for_an_option_do_not_depend_on_language(world, run_async, code):
    _answer(world, run_async, code, "opt1", _question())
    assert world.keys[-2:] == ["Down", "Enter"]


@pytest.mark.parametrize("code", ["fa", "ru"])
@pytest.mark.parametrize("verb", ["allow", "deny"])
def test_the_hook_decision_does_not_depend_on_language(world, run_async, code, verb):
    _answer(world, run_async, "", verb, _bash())
    english = world.decisions[-1][1]
    _answer(world, run_async, code, verb, _bash())
    assert world.decisions[-1][1] == english
