"""The exported functions of entrypoints.md: ``aipager.miniapp.chat_lines``,
``aipager.miniapp.phrase``, the ``sessions`` / ``launch`` constants and the
``tr=None`` keyword on ``display_facts``.

Oracles:
- English: the base branch's literals (copied below from a ``git archive`` of
  ``feature/multilingual``, never from the branch under test);
- Russian plurals: CLDR's grouping, observed without knowing the forms. The
  lines for n and m are the same form exactly when they are equal once their
  digits are masked; 1 and 21 share "one", 2 and 22 "few", 5, 11 and 25 "many".
"""

from __future__ import annotations

import html
import re

import pytest

from aipager import i18n
from aipager.miniapp import chat_lines, launch, phrase, sessions

EN, FA, RU = i18n.get("en"), i18n.get("fa"), i18n.get("ru")

# Today's English (base branch), keyed by constant name.
SESSIONS_EN = {
    "NO_TRANSCRIPT_REASON": "No resumable transcript - start a fresh session instead.",
    "NO_PERMISSION_REASON": "You don't have permission to control this session.",
    "PERMS_ADMIN_REQUIRED_REASON": "Switching to Auto mode requires admin.",
    "QUEUE_EMPTY_REASON": "Nothing queued to clear.",
    "MODEL_SWITCH_BUSY_REASON": "Claude is working - switch the model when this turn ends.",
    "MODEL_SWITCH_PROMPT_OPEN_REASON":
        "A prompt is open in the terminal - answer it before switching the model.",
    "MODEL_SWITCH_NOT_LIVE_REASON": "Session isn't running.",
    "MODEL_SWITCH_UNKNOWN_REASON":
        "aipager hasn't seen this session's state yet - try again in a moment.",
    "MODEL_SWITCH_PENDING_REASON":
        "The last model switch isn't confirmed yet - check the terminal for Claude Code's "
        "\"Switch model?\" question before switching again.",
    "MODEL_SWITCH_UNCONFIRMED":
        "not confirmed - check the session. Claude Code may be asking \"Switch model?\" "
        "in the terminal, or it was already on that model.",
    "QUEUE_FULL_REASON": "Queue is full (50 pending) - clear it or wait for it to drain.",
    "NO_CONTEXT_REASON": "Nothing to compact - context is already empty.",
}
LAUNCH_EN = {
    "NO_PROJECT_FOLDER": "Pick a project folder: create one with New folder, or ask an "
                         "admin to start the session.",
    "WIDE_FOLDER_REFUSAL": "Your role can't start a session in the home folder or a folder "
                           "above it. Pick a project folder: create one with New folder, or "
                           "ask an admin to start the session.",
    "NO_FOLDER_AT_ALL": "There is no project folder to start in yet: ask an admin to start "
                        "the session.",
}
# validate_*(value) -> (value, err) on the base branch.
NAME_ERRORS = {
    "": "Session name can't be empty.",
    "a" * 65: "Session name must be 64 characters or fewer.",
    "bad name": "Use letters, numbers, hyphens and underscores; start with a letter or number.",
    "-x": "Use letters, numbers, hyphens and underscores; start with a letter or number.",
    "new": "'new' is a reserved command name (/new). Pick another name.",
    "Help": "'help' is a reserved command name (/help). Pick another name.",
}
DIR_ERRORS = {
    "": "Folder name can't be empty.",
    "a b": "Use letters, numbers, dots, hyphens and underscores; start with a letter or number.",
    "../x": "Use letters, numbers, dots, hyphens and underscores; start with a letter or number.",
    ".x": "Use letters, numbers, dots, hyphens and underscores; start with a letter or number.",
}
ALL_CONSTANTS = ([(sessions, n) for n in SESSIONS_EN] + [(launch, n) for n in LAUNCH_EN])
CONST_IDS = [n for _m, n in ALL_CONSTANTS]
EXPECTED = {**SESSIONS_EN, **LAUNCH_EN}


# ---- constants stay str and today's English ------------------------------------------

@pytest.mark.parametrize("module,name", ALL_CONSTANTS, ids=CONST_IDS)
def test_a_constant_is_todays_english(module, name):
    assert getattr(module, name) == EXPECTED[name]


@pytest.mark.parametrize("module,name", ALL_CONSTANTS, ids=CONST_IDS)
def test_a_constant_is_a_str(module, name):
    assert isinstance(getattr(module, name), str)


@pytest.mark.parametrize("module,name", ALL_CONSTANTS, ids=CONST_IDS)
def test_a_constant_escapes_like_its_english(module, name):
    assert html.escape(getattr(module, name)) == html.escape(EXPECTED[name])


@pytest.mark.parametrize("module,name", ALL_CONSTANTS, ids=CONST_IDS)
def test_a_constant_formats_like_its_english(module, name):
    value = getattr(module, name)
    assert f"<{value}>" + "!" == f"<{EXPECTED[name]}>!"


@pytest.mark.parametrize("module,name", ALL_CONSTANTS, ids=CONST_IDS)
def test_a_constant_hashes_like_its_english(module, name):
    assert {getattr(module, name): 1}.get(EXPECTED[name]) == 1


@pytest.mark.parametrize("module,name", ALL_CONSTANTS, ids=CONST_IDS)
def test_a_constant_is_truthy(module, name):
    assert bool(getattr(module, name)) is True


@pytest.mark.parametrize("module,name", ALL_CONSTANTS, ids=CONST_IDS)
def test_a_constant_renders_in_persian(module, name):
    out = phrase.say(FA, getattr(module, name))
    assert any("\u0600" <= c <= "\u06ff" for c in out)


@pytest.mark.parametrize("module,name", ALL_CONSTANTS, ids=CONST_IDS)
def test_a_constant_renders_in_russian(module, name):
    out = phrase.say(RU, getattr(module, name))
    assert any("\u0400" <= c <= "\u04ff" for c in out)


@pytest.mark.parametrize("module,name", ALL_CONSTANTS, ids=CONST_IDS)
def test_a_constant_said_in_english_is_its_english(module, name):
    assert phrase.say(EN, getattr(module, name)) == EXPECTED[name]


def test_the_queue_cap_in_persian_uses_persian_digits():
    assert "\u06f5\u06f0" in phrase.say(FA, sessions.QUEUE_FULL_REASON)


# ---- launch validation: shape and English unchanged, renderable --------------------------

@pytest.mark.parametrize("value", sorted(NAME_ERRORS), ids=repr)
def test_a_name_refusal_is_todays_pair(value):
    assert launch.validate_session_name(value) == ("", NAME_ERRORS[value])


@pytest.mark.parametrize("value", sorted(DIR_ERRORS), ids=repr)
def test_a_folder_refusal_is_todays_pair(value):
    assert launch.validate_new_dir_name(value) == ("", DIR_ERRORS[value])


@pytest.mark.parametrize("value", ["ok", "a" * 64, "web2", "a"])
def test_a_valid_name_has_an_empty_error(value):
    name, err = launch.validate_session_name(value)
    assert (name, err, bool(err)) == (value, "", False)


@pytest.mark.parametrize("value", sorted(NAME_ERRORS), ids=repr)
def test_a_name_refusal_error_is_a_str(value):
    assert type(launch.validate_session_name(value)[1]).__mro__[-2] is str


@pytest.mark.parametrize("value", sorted(NAME_ERRORS), ids=repr)
def test_a_name_refusal_renders_in_persian(value):
    out = phrase.say(FA, launch.validate_session_name(value)[1])
    assert any("\u0600" <= c <= "\u06ff" for c in out)


def test_the_name_length_limit_boundary_64_passes_65_fails():
    assert (launch.validate_session_name("a" * 64)[1], bool(launch.validate_session_name("a" * 65)[1])) == ("", True)


def test_a_persian_reserved_name_keeps_the_command_whole():
    out = phrase.say(FA, launch.validate_session_name("new")[1])
    assert "/new" in out


# ---- Phrase and say ------------------------------------------------------------------------

def _msg():
    return i18n.Msg("test.p4.hello", "Hello {name}, {n} left")


def test_a_phrase_is_a_str():
    assert isinstance(phrase.Phrase(_msg(), name="ada", n=3), str)


def test_a_phrase_equals_its_english_rendering():
    assert phrase.Phrase(_msg(), name="ada", n=3) == "Hello ada, 3 left"


def test_a_phrase_remembers_its_msg():
    m = _msg()
    assert phrase.Phrase(m, name="ada", n=3).msg is m


def test_a_phrase_remembers_its_params():
    assert phrase.Phrase(_msg(), name="ada", n=3).params == {"name": "ada", "n": 3}


def test_say_without_a_tr_returns_the_value_itself():
    p = phrase.Phrase(_msg(), name="ada", n=3)
    assert phrase.say(None, p) is p


@pytest.mark.parametrize("value", ["plain", "", 5, None])
def test_say_without_a_tr_returns_any_value_unchanged(value):
    assert phrase.say(None, value) is value


@pytest.mark.parametrize("value,want", [("plain", "plain"), ("", ""), (5, "5")])
def test_say_with_a_tr_passes_other_values_as_str(value, want):
    assert phrase.say(FA, value) == want


def test_say_with_a_tr_passes_none_through():
    """entrypoints.md (amended at iteration 2): None stays None, so
    `if err:` callers keep working."""
    assert phrase.say(FA, None) is None


def test_say_renders_a_phrase_with_its_params():
    """No fa entry exists for a test key, so the English pattern is used,
    with the params filled and Persian digits."""
    out = phrase.say(FA, phrase.Phrase(_msg(), name="ada", n=3))
    assert "ada" in out and "\u06f3" in out


def test_say_renders_a_plain_msg():
    assert phrase.say(EN, i18n.Msg("test.p4.bare", "Nothing here.")) == "Nothing here."


def test_say_renders_a_translated_msg_in_its_language():
    m = sessions.QUEUE_EMPTY_REASON
    assert phrase.say(RU, m) != phrase.say(EN, m)


def test_say_returns_plain_text_not_html():
    m = i18n.Msg("test.p4.tag", "A <b> & {x}")
    assert phrase.say(EN, phrase.Phrase(m, x="<i>")) == "A <b> & <i>"


def test_a_plural_phrase_renders_by_count():
    p = i18n.Plural("test.p4.items", one="{n} item", other="{n} items")
    assert (phrase.Phrase(p, n=1), phrase.Phrase(p, n=2)) == ("1 item", "2 items")


# ---- chat lines: Russian plurals ------------------------------------------------------------

def _forms(fn, *args):
    by = {}
    for n in (1, 2, 5, 11, 21, 22, 25):
        line = fn(RU, *args[:1], *args[1:], n) if args else fn(RU, n)
        by[n] = re.sub(r"\d+", "#", line)
    return by


@pytest.mark.parametrize("fn", ["queue_cleared", "stopped"])
@pytest.mark.parametrize("a,b", [(1, 21), (2, 22), (5, 11), (5, 25)])
def test_russian_counts_of_one_category_share_a_form(fn, a, b):
    by = _forms(getattr(chat_lines, fn), "dev")
    assert by[a] == by[b]


@pytest.mark.parametrize("fn", ["queue_cleared", "stopped"])
@pytest.mark.parametrize("a,b", [(1, 2), (2, 5), (1, 5), (21, 22), (22, 25), (1, 11)])
def test_russian_counts_of_different_categories_differ(fn, a, b):
    by = _forms(getattr(chat_lines, fn), "dev")
    assert by[a] != by[b]


@pytest.mark.parametrize("fn", ["queue_cleared", "stopped"])
@pytest.mark.parametrize("n", [1, 2, 5, 11, 21, 22, 25])
def test_a_russian_count_shows_its_number(fn, n):
    assert str(n) in getattr(chat_lines, fn)(RU, "dev", n)


def test_stopped_with_nothing_dropped_mentions_no_count():
    assert not re.search(r"\d", chat_lines.stopped(RU, "dev", 0))


@pytest.mark.parametrize("n,want", [
    (0, "\u23f9\ufe0f Stopped [dev] from the Mini App"),
    (1, "\u23f9\ufe0f Stopped [dev] from the Mini App (1 queued message discarded)"),
    (2, "\u23f9\ufe0f Stopped [dev] from the Mini App (2 queued messages discarded)"),
])
def test_english_stopped_is_todays(n, want):
    assert chat_lines.stopped(EN, "dev", n) == want


@pytest.mark.parametrize("n,want", [
    (1, "\U0001f9f9 Cleared 1 queued message for [dev] from the Mini App"),
    (2, "\U0001f9f9 Cleared 2 queued messages for [dev] from the Mini App"),
    (21, "\U0001f9f9 Cleared 21 queued messages for [dev] from the Mini App"),
])
def test_english_queue_cleared_is_todays(n, want):
    assert chat_lines.queue_cleared(EN, "dev", n) == want


@pytest.mark.parametrize("n", [1, 2, 5, 11, 21])
def test_persian_counts_use_persian_digits(n):
    line = chat_lines.queue_cleared(FA, "dev", n)
    assert not re.search(r"[0-9]", line)


def test_a_persian_count_does_not_touch_a_name_with_digits():
    assert "web2" in chat_lines.queue_cleared(FA, "web2", 3)


# ---- chat lines: every function in fa, ru and English ----------------------------------------

LINES = {
    "session_created": ("made", False, "/srv/p"),
    "folder_created": ("/srv/p/web",),
    "setting_changed": ("answer_length", "short"),
    "session_setting_changed": ("dev", "answer_length", "short", False),
    "perms_switched": ("dev", True),
    "queue_cleared": ("dev", 2),
    "compacted": ("dev",),
    "model_switched": ("dev", "claude-opus-5-5"),
    "restarted": ("dev",),
    "renamed": ("dev", "web2"),
    "stopped": ("dev", 2),
    "killed": ("dev",),
    "resumed": ("dev", True),
    "deleted": ("dev",),
}


@pytest.mark.parametrize("fn", sorted(LINES))
def test_a_line_is_plain_text_in_persian(fn):
    line = getattr(chat_lines, fn)(FA, *LINES[fn])
    assert isinstance(line, str) and any("\u0600" <= c <= "\u06ff" for c in line)


@pytest.mark.parametrize("fn", sorted(LINES))
def test_a_line_is_russian(fn):
    line = getattr(chat_lines, fn)(RU, *LINES[fn])
    assert any("\u0400" <= c <= "\u04ff" for c in line)


@pytest.mark.parametrize("fn", sorted(LINES))
def test_a_line_carries_no_em_dash(fn):
    assert all("\u2014" not in getattr(chat_lines, fn)(tr, *LINES[fn]) for tr in (FA, RU, EN))


@pytest.mark.parametrize("fn", sorted(LINES))
def test_a_line_holds_no_html_escapes(fn):
    """Plain text: an html-escaped name would show its entities."""
    args = tuple("a&b" if a == "dev" else a for a in LINES[fn])
    line = getattr(chat_lines, fn)(FA, *args)
    assert "&amp;" not in line


def test_session_created_with_a_folder_has_a_second_line():
    assert chat_lines.session_created(FA, "made", False, "/srv/p").count("\n") == 1


def test_session_created_without_a_folder_is_one_line():
    assert "\n" not in chat_lines.session_created(FA, "made", False, "")


def test_session_created_english_is_todays():
    assert chat_lines.session_created(EN, "made", True, "/srv/p") == (
        "\U0001f680 made created from the Mini App \u00b7 \U0001f916 Auto\n\U0001f4c1 /srv/p")


def test_an_unknown_setting_passes_its_raw_id():
    line = chat_lines.setting_changed(FA, "no_such_field", "zz_value")
    assert "no_such_field" in line and "zz_value" in line


def test_resumed_in_ask_differs_from_a_plain_resume():
    assert chat_lines.resumed(FA, "dev", True) != chat_lines.resumed(FA, "dev", False)


# ---- display_facts: numbers in the viewer's digits ---------------------------------------------

def _detail():
    return {
        "label": "dev", "status": "busy", "model": "claude-opus-5", "context_pct": 45,
        "cost_usd": 1.5, "busy_elapsed_seconds": 192, "busy_elapsed_unit": "s",
        "last_active_seconds_ago": 300, "cwd": "/srv/web2", "skip_perms": False,
    }


def _facts(tr):
    return {f["id"]: f["value"] for f in sessions.display_facts(_detail(), tr=tr)}


def test_without_tr_facts_are_english():
    labels = [f["label"] for f in sessions.display_facts(_detail())]
    assert labels and all(re.fullmatch(r"[A-Za-z ]+", x) for x in labels)


def test_persian_context_percent():
    assert _facts(FA).get("context") == "\u06f4\u06f5\u066a"


def test_persian_cost():
    assert _facts(FA).get("cost") == "$\u06f1\u066b\u06f5\u06f0"


def test_persian_working_clock():
    assert _facts(FA).get("working") == "\u06f3:\u06f1\u06f2"


def test_english_numbers_without_tr():
    facts = {f["label"]: f["value"] for f in sessions.display_facts(_detail())}
    assert {"45%", "$1.50", "3:12"} <= set(facts.values())


def test_russian_numbers_stay_latin():
    f = _facts(RU)
    assert (f.get("context"), f.get("cost"), f.get("working")) == ("45%", "$1.50", "3:12")


@pytest.mark.parametrize("tr", [FA, RU], ids=["fa", "ru"])
def test_model_and_folder_values_are_never_converted(tr):
    f = _facts(tr)
    assert (f.get("model"), f.get("cwd")) == ("claude-opus-5", "/srv/web2")


def test_a_fact_id_is_the_same_in_every_language():
    ids = [[f["id"] for f in sessions.display_facts(_detail(), tr=t)] for t in (None, FA, RU)]
    assert ids[0] == ids[1] == ids[2]
