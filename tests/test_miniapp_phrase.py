"""``Phrase`` and ``say``: a sentence that stays its English for the code
that shares it (the chat compares, escapes and glues these strings) and
still renders in the reader's language where the reader is known."""

from __future__ import annotations

import copy
import html

import pytest

from aipager import i18n
from aipager.i18n import Command, Msg, Plural
from aipager.miniapp.phrase import Phrase, say

GREET = Msg("app.test.greet", "Hello {name}, use {command}.")
COUNT = Plural("app.test.count", one="{n} file in {where}", other="{n} files in {where}")
PLAIN = Msg("app.test.plain", "Nothing to do.")


def test_phrase_is_its_english():
    p = Phrase(GREET, name="x1", command=Command("/new"))
    assert isinstance(p, str)
    assert p == "Hello x1, use /new."
    assert str(p) == "Hello x1, use /new."
    assert html.escape(p) == "Hello x1, use /new."
    assert f"[{p}]" == "[Hello x1, use /new.]"
    assert p.msg is GREET and p.params == {"name": "x1", "command": "/new"}
    assert Phrase(COUNT, n=1, where="a") == "1 file in a"
    assert Phrase(COUNT, n=3, where="a") == "3 files in a"


def test_a_phrase_survives_a_copy():
    p = Phrase(GREET, name="x1", command=Command("/new"))
    for q in (copy.copy(p), copy.deepcopy(p), copy.deepcopy({"detail": p})["detail"]):
        assert q == p and isinstance(q, Phrase) and q.msg.key == GREET.key


def test_a_phrase_needs_a_message_and_a_plural_its_count():
    with pytest.raises(TypeError):
        Phrase("plain text")
    with pytest.raises(TypeError):
        Phrase(COUNT, where="a")
    with pytest.raises(KeyError):
        Phrase(GREET, name="x1")


def test_say_without_a_reader_changes_nothing():
    p = Phrase(GREET, name="x1", command=Command("/new"))
    assert say(None, p) is p
    assert say(None, PLAIN) is PLAIN
    assert say(None, "from elsewhere") == "from elsewhere"
    assert say(i18n.get("fa"), None) is None


def test_say_renders_a_phrase():
    fa = i18n.get("fa")
    p = Phrase(GREET, name="x1", command=Command("/new"))
    assert say(i18n.get("en"), p) == p
    # No catalog entry for a test key: the English, but laid out for Persian
    # (a leading mark, the name and the command isolated whole).
    assert say(fa, p) == "\u200fHello \u2068x1\u2069, use \u2068/new\u2069."
    assert say(i18n.get("ru"), Phrase(COUNT, n=2, where="a")) == "2 files in a"
    assert say(fa, Phrase(COUNT, n=2, where="a")) == "\u200f۲ files in \u2068a\u2069"


def test_say_renders_a_msg_and_passes_anything_else():
    fa = i18n.get("fa")
    assert say(fa, PLAIN) == "\u200fNothing to do."
    assert say(fa, "Auto needs an admin.") == "Auto needs an admin."
    assert say(fa, 5) == "5"


def test_say_uses_the_catalog(monkeypatch):
    from aipager.i18n import core
    monkeypatch.setitem(core._CATALOGS, "ru", {**core._catalog("ru"),
                                               "app.test.plain": "Нечего делать."})
    assert say(i18n.get("ru"), PLAIN) == "Нечего делать."


# ---- the shared constants -------------------------------------------------------------------

#: Today's English, copied from the base (5a1f929) before any of it moved to
#: keys: the chat shows these as they are, and compares, escapes and glues
#: them.
TODAYS_ENGLISH = {
    "sessions.NO_TRANSCRIPT_REASON": "No resumable transcript - start a fresh session instead.",
    "sessions.NO_PERMISSION_REASON": "You don't have permission to control this session.",
    "sessions.PERMS_ADMIN_REQUIRED_REASON": "Switching to Full access mode requires admin.",
    "sessions.QUEUE_EMPTY_REASON": "Nothing queued to clear.",
    "sessions.MODEL_SWITCH_BUSY_REASON":
        "Claude is working - switch the model when this turn ends.",
    "sessions.MODEL_SWITCH_PROMPT_OPEN_REASON":
        "A prompt is open in the terminal - answer it before switching the model.",
    "sessions.MODEL_SWITCH_NOT_LIVE_REASON": "Session isn't running.",
    "sessions.MODEL_SWITCH_UNKNOWN_REASON":
        "aipager hasn't seen this session's state yet - try again in a moment.",
    "sessions.MODEL_SWITCH_PENDING_REASON":
        "The last model switch isn't confirmed yet - check the terminal for Claude Code's "
        "\"Switch model?\" question before switching again.",
    "sessions.MODEL_SWITCH_UNCONFIRMED":
        "not confirmed - check the session. Claude Code may be asking \"Switch model?\" in "
        "the terminal, or it was already on that model.",
    "sessions.QUEUE_FULL_REASON":
        "Queue is full (50 pending) - clear it or wait for it to drain.",
    "sessions.NO_CONTEXT_REASON": "Nothing to compact - context is already empty.",
    "launch.NO_PROJECT_FOLDER":
        "Pick a project folder: create one with New folder, or ask an admin to start the "
        "session.",
    "launch.WIDE_FOLDER_REFUSAL":
        "Your role can't start a session in the home folder or a folder above it. Pick a "
        "project folder: create one with New folder, or ask an admin to start the session.",
    "launch.NO_FOLDER_AT_ALL":
        "There is no project folder to start in yet: ask an admin to start the session.",
}


def _constant(path):
    from aipager.miniapp import launch, sessions
    module, name = path.split(".")
    return getattr({"sessions": sessions, "launch": launch}[module], name)


@pytest.mark.parametrize("path", sorted(TODAYS_ENGLISH))
def test_constants_are_today_s_english(path):
    value = _constant(path)
    assert isinstance(value, str)
    assert value == TODAYS_ENGLISH[path]
    assert html.escape(value) == html.escape(TODAYS_ENGLISH[path])
    assert f"{value}" == TODAYS_ENGLISH[path] and bool(value)


@pytest.mark.parametrize("path", sorted(TODAYS_ENGLISH))
@pytest.mark.parametrize("code", ["fa", "ru"])
def test_constants_render_in_persian_and_russian(path, code):
    text = say(i18n.get(code), _constant(path))
    assert text != TODAYS_ENGLISH[path]
    assert "\u2014" not in text
    for word in ("Claude Code", "aipager", "Claude"):
        if word in TODAYS_ENGLISH[path]:
            assert word in text


def test_launch_errors_are_today_s_english_and_render_with_their_command():
    from aipager.miniapp import launch
    cases = [
        (launch.validate_session_name(5), "Session name must be text."),
        (launch.validate_session_name(" "), "Session name can't be empty."),
        (launch.validate_session_name("a" * 70), "Session name must be 64 characters or fewer."),
        (launch.validate_session_name("status"),
         "'status' is a reserved command name (/status). Pick another name."),
        (launch.validate_new_dir_name("a" * 70), "Folder name must be 64 characters or fewer."),
        (launch.validate_new_dir_name("a/b"),
         "Use letters, numbers, dots, hyphens and underscores; start with a letter or number."),
        (launch.validate_model("m" * 70, ()), "Model name must be 64 characters or fewer."),
        (launch.validate_model("-x", ()),
         "Use one of the listed models, or a full model name like claude-opus-5."),
        (launch.validate_cwd(5, ["/"]), "Working directory must be text."),
        (launch.validate_cwd("/x", []),
         "No directory is available yet - start a session from chat first, then this picker "
         "will offer its project."),
    ]
    for (value, err), english in cases:
        assert value == "" and isinstance(err, str) and err == english
    err = launch.validate_session_name("status")[1]
    fa = say(i18n.get("fa"), err)
    assert "\u2068/status\u2069" in fa and "\u2068status\u2069" in fa
    ru = say(i18n.get("ru"), err)
    assert "/status" in ru and "\u2068" not in ru
    assert "۶۴" in say(i18n.get("fa"), launch.validate_session_name("a" * 70)[1])
    assert launch.validate_session_name("ok") == ("ok", "")
