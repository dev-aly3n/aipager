"""Success criteria 27-29: /mode words (incl. `full` and every language's
translated words) with their session-name precedence; the shown labels;
the i18n checks with nothing left to translate; the goldens."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from aipager import i18n
from aipager.bot import mode_labels
from aipager.i18n.locales import enabled_codes
from aipager.state import SessionRegistry, Status

from _tm3 import (DM_CHAT, OWNER, dm_bot, reply_buttons, reply_texts, settle_tasks, typed)

ROOT = Path(__file__).resolve().parents[3]

#: Direction marks a right-to-left render carries by design (LRM, RLM,
#: ALM, the isolates), dropped before comparing words and labels.
_MARKS = {cp: None for cp in (0x200E, 0x200F, 0x061C, 0x2066, 0x2067, 0x2068, 0x2069)}


def _plain(text: str) -> str:
    return text.translate(_MARKS)
CODES = [c for c in enabled_codes() if c != "en"]
WORDS = [("ask", mode_labels.WORD_ASK), ("auto", mode_labels.WORD_AUTO),
         ("full", mode_labels.WORD_FULL)]


# ---- labels ------------------------------------------------------------------

@pytest.mark.parametrize("mode,label", [
    ("ask", "💬 Ask mode"), ("auto", "🤖 Auto mode"), ("full", "🔓 Full access mode"),
], ids=["ask", "auto", "full"])
def test_english_labels(mode, label):
    assert i18n.get("en").text(mode_labels.mode_label(mode)) == label


def test_no_auto_label_text():
    """entrypoints.md "Shown English labels"."""
    assert i18n.get("en").text(mode_labels.MODE_ASK_NO_AUTO) == \
        "💬 Ask mode (Auto isn't available here)"


def test_other_mode_label_names_claude_codes_mode():
    text = i18n.get("en").text(mode_labels.MODE_OTHER, name="plan")
    assert text == "⚙️ Claude Code's plan mode"


@pytest.mark.parametrize("bad", [True, False], ids=["bool-true", "bool-false"])
def test_mode_label_refuses_a_bool(bad):
    with pytest.raises(TypeError):
        mode_labels.mode_label(bad)


def test_fourteen_languages_are_enabled():
    assert len(CODES) == 14


def test_persian_full_access_label():
    assert _plain(i18n.get("fa").text(mode_labels.MODE_FULL)) == "🔓 مود دسترسی کامل"


def test_persian_full_access_word():
    assert _plain(i18n.get("fa").text(mode_labels.WORD_FULL)) == "کامل"


@pytest.mark.parametrize("code", CODES, ids=CODES)
def test_full_access_label_is_translated(code):
    assert i18n.get(code).text(mode_labels.MODE_FULL) != "🔓 Full access mode"


@pytest.mark.parametrize("code", CODES, ids=CODES)
def test_full_access_label_keeps_its_emoji(code):
    assert _plain(i18n.get(code).text(mode_labels.MODE_FULL)).startswith("🔓")


@pytest.mark.parametrize("code", CODES, ids=CODES)
def test_full_access_word_appears_in_its_label(code):
    tr = i18n.get(code)
    assert _plain(tr.text(mode_labels.WORD_FULL)).lower() in \
        _plain(tr.text(mode_labels.MODE_FULL)).lower()


# ---- typed words --------------------------------------------------------------

@pytest.mark.parametrize("word", ["full", "FULL", "Full", "ask", "AUTO"],
                         ids=["full", "FULL", "Full", "ask", "AUTO"])
def test_english_ids_any_case(word):
    assert mode_labels.mode_from_word(word) == word.lower()


@pytest.mark.parametrize("code", ["en"] + CODES, ids=["en"] + CODES)
@pytest.mark.parametrize("mode,msg", WORDS, ids=[m for m, _ in WORDS])
def test_translated_word_is_recognised(code, mode, msg):
    assert mode_labels.mode_from_word(i18n.get(code).text(msg)) == mode


@pytest.mark.parametrize("word", ["", "bypass", "default", "fullaccess", "plan"],
                         ids=["empty", "bypass", "claude-default", "joined", "plan"])
def test_other_words_are_not_modes(word):
    assert mode_labels.mode_from_word(word) is None


PARSE = [
    (["full"], True, ("full", None, True)),
    (["x1", "full"], False, ("full", "x1", True)),
    (["full", "x1"], False, ("full", "x1", True)),
    (["کامل"], False, ("full", None, True)),
    (["کامل"], True, (None, "کامل", True)),
    (["x1", "کامل"], True, ("full", "x1", True)),
    (["x1"], False, (None, "x1", True)),
    (["x1", "full", "ask"], False, ("full", "x1", False)),
    (["a", "b"], False, (None, "a", False)),
]


@pytest.mark.parametrize("words,exists,expected", PARSE, ids=[
    "full-even-with-a-session-full", "name-then-full", "full-then-name",
    "translated-no-session", "translated-is-a-session", "name-then-translated",
    "name-only", "too-many", "two-names"])
def test_parse_mode_args(words, exists, expected):
    mode, label, ok = mode_labels.parse_mode_args(words, lambda name: exists)
    if not expected[2]:
        assert ok is False
    else:
        assert (mode, label, ok) == expected


# ---- /mode in chat ----------------------------------------------------------------

def _two_sessions(mk_bot, other_label="full"):
    reg = SessionRegistry()
    bot = dm_bot(mk_bot, reg)
    for name, label in (("claude-ben", "ben"), (f"claude-{other_label}", other_label)):
        s = reg.get_or_create(name)
        s.label, s.status, s.perm_mode = label, Status.IDLE, "ask"
        s.claude_session_id = "UUID"
    reg.last_active_session = "claude-ben"
    return bot


def _mode(bot, run_async, text):
    u = typed(text, chat_id=DM_CHAT, user_id=OWNER)

    async def _go():
        await bot._handle_mode_cmd(u, MagicMock())
        await settle_tasks()
    run_async(_go())
    return u


def _confirm_for(u) -> str:
    """The text of a Full access confirm card, or "" if none was shown."""
    if any(cb and cb.endswith("perms_confirm") for _t, cb in reply_buttons(u)):
        return "\n".join(reply_texts(u))
    return ""


@pytest.mark.parametrize("text", ["/mode full", "/mode FULL", "/mode ben full",
                                  "/mode full ben", "/perms full"],
                         ids=["full", "upper", "name-first", "mode-first", "alias"])
def test_mode_full_is_the_mode_even_with_a_session_named_full(mk_bot, run_async, pty, text):
    bot = _two_sessions(mk_bot, "full")
    u = _mode(bot, run_async, text) if not text.startswith("/perms") else None
    if u is None:
        u = typed(text, chat_id=DM_CHAT, user_id=OWNER)
        run_async(bot._handle_perms_cmd(u, MagicMock()))
    assert "<b>ben</b>" in _confirm_for(u), reply_texts(u)


def test_mode_full_ask_names_the_session_full(mk_bot, run_async, pty):
    """`/mode full ask` still reaches a session named full: ask is the
    mode, so full is the name (design Risks)."""
    bot = _two_sessions(mk_bot, "full")
    bot.registry.get("claude-full").perm_mode = "auto"
    _mode(bot, run_async, "/mode full ask")
    assert pty.presses == [b"\x1b[Z"]


def test_translated_word_names_a_live_session_of_that_name(mk_bot, run_async, pty):
    """es `Total` is Full access; a live session named `total` wins."""
    bot = _two_sessions(mk_bot, "total")
    u = _mode(bot, run_async, "/mode total")
    assert _confirm_for(u) == "" and "total" in "\n".join(reply_texts(u))


def test_translated_word_is_the_mode_without_such_a_session(mk_bot, run_async, pty):
    bot = _two_sessions(mk_bot, "other")
    u = _mode(bot, run_async, "/mode کامل")
    assert "<b>ben</b>" in _confirm_for(u), reply_texts(u)


@pytest.mark.parametrize("code", CODES, ids=CODES)
def test_every_languages_auto_word_switches_in_place(mk_bot, run_async, pty, code):
    bot = _two_sessions(mk_bot, "other")
    word = i18n.get(code).text(mode_labels.WORD_AUTO)
    _mode(bot, run_async, f"/mode ben {word}")
    assert pty.presses == [b"\x1b[Z"] * 3


def test_wrong_word_count_shows_usage_and_changes_nothing(mk_bot, run_async, pty):
    bot = _two_sessions(mk_bot, "other")
    u = _mode(bot, run_async, "/mode ben full ask")
    assert (pty.runs, pty.launched, _confirm_for(u)) == ([], [], "")


# ---- i18n checks ----------------------------------------------------------------

def _tool(*args):
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    return subprocess.run([sys.executable, str(ROOT / "tools" / "i18n.py"), *args],
                          cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=300)


def test_i18n_check_passes():
    proc = _tool("check")
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.parametrize("code", CODES, ids=CODES)
def test_i18n_todo_is_empty(code):
    proc = _tool("todo", "--lang", code)
    assert json.loads(proc.stdout).get("entries") == [], proc.stdout[:2000]


def _golden_keys():
    keys = []
    for line in (ROOT / "tools" / "i18n" / "golden.txt").read_text().splitlines():
        s = line.strip()
        if s and not s.startswith("#") and " " not in s and "." in s and not s.startswith("["):
            keys.append(s)
    return keys


def test_golden_list_at_most_180():
    assert len(_golden_keys()) <= 180


@pytest.mark.parametrize("key", ["mode.full", "mode.word.full"], ids=["label", "word"])
def test_golden_list_has_the_full_access_keys(key):
    assert key in _golden_keys()


# ---- goldens ---------------------------------------------------------------------

BASE = "884a99d"
JUSTIFIED = {
    "menu.commands", "modecard.card", "modecard.cmd_replies", "modecard.perms_flow",
    "modecard.switch_taps", "newflow.name_card", "newflow.starting", "newflow.ready",
    "newflow.ready_taps", "newflow.taps", "newflow.conflict_taps", "newflow.closed",
    "settings.new_sessions", "prompt.keyboards", "sess.taps", "status.switch",
    "transport.texts", "miniapp.mirrors", "miniapp.payload_texts",
    # Accepted by the orchestrator in fix iteration 2 (the reviewer checked
    # that each changes only for a mode reason):
    # the Mini App detail payload's mode, modes and mode label;
    "miniapp.details",
    # new: the confirm, retry and failure edits of an in-place switch;
    "modecard.switch_results",
    # the Ready card's mode buttons ("Switch to Auto mode", Full access);
    "newflow.ready_models",
    # /settings New sessions taps, with the Full access option and refusal.
    "settings.taps",
    # /help's /mode line names all three modes (2026-10-11).
    "help.dm", "help.group", "help.group_unnamed",
}


def _at_base(rel):
    try:
        return subprocess.run(["git", "show", f"{BASE}:{rel}"], cwd=str(ROOT),
                              capture_output=True, timeout=30, check=True).stdout
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        pytest.skip(f"base commit {BASE} not available")


def test_claude_facing_golden_is_byte_identical():
    rel = "tests/golden/claude_facing.json"
    assert (ROOT / rel).read_bytes() == _at_base(rel)


def test_english_goldens_change_only_in_the_justified_surfaces():
    rel = "tests/golden/english_surfaces.json"
    before = json.loads(_at_base(rel))
    after = json.loads((ROOT / rel).read_text())
    changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
    # The main keyboard lost its App button (2026-10-12), and only that.
    from tests.i18n_surfaces import (AGENTS_TREE_CARD_SURFACES, AGENTS_TREE_NEW_SURFACES,
                                     AGENTS_TREE_SURFACES, APPEARANCE_NEW_SURFACES,
                                     APPEARANCE_SURFACES, NO_KEYBOARD_APP_SURFACES,
                                     RESTART_NOW_NEW_SURFACES, with_keyboard_app_removed,
                                     without_appearance_row, without_card_divider)
    # Restart now without a service (2026-10-12) added one new surface.
    # The agents tree (2026-10-14) changed the block under an answer, put a
    # divider above the card's status line, and added one surface.
    # 🎨 Appearance (2026-10-15) added one row to the settings menus, and
    # its own page.
    assert sorted(changed - JUSTIFIED - NO_KEYBOARD_APP_SURFACES
                  - RESTART_NOW_NEW_SURFACES - AGENTS_TREE_SURFACES
                  - AGENTS_TREE_NEW_SURFACES - APPEARANCE_SURFACES
                  - APPEARANCE_NEW_SURFACES) == []
    for k in APPEARANCE_SURFACES:
        assert without_appearance_row(after[k]) == before[k], k
    for k in NO_KEYBOARD_APP_SURFACES:
        assert after[k] == with_keyboard_app_removed(before[k]), k
    for k in AGENTS_TREE_CARD_SURFACES:
        assert without_card_divider(after[k]) == before[k], k
