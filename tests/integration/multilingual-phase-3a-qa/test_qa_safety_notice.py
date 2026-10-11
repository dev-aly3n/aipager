"""Success criteria 9, 10 and 11: a safety block reads in the chat's
language from a reason code, Claude and the audit keep the English, an
unknown reason is shown as received, and a sticky "session halted" repeat
never halts or notifies again.

Drives ``aipager.bot.safety_notice`` and ``aipager.safety.REASON_SHAPES``
(entrypoints.md) and the ``safety_blocked`` notice through ``notify``."""

from __future__ import annotations

import html
import re

import pytest
import qa_p3a_scenarios as S

from aipager import i18n, safety
from aipager.bot import safety_notice

PERSIAN = re.compile("[؀-ۿ]")
CYRILLIC = re.compile("[Ѐ-ӿ]")
SCRIPT = {"fa": PERSIAN, "ru": CYRILLIC}

#: entrypoints.md: the codes REASON_SHAPES carries.
LISTED_CODES = {
    "bash_search", "bash", "tool_not_allowed", "tool_denied", "path_unreadable",
    "path_protected_write", "path_protected", "runs_commands", "wide_cwd_write",
    "wide_cwd_search", "outside_write", "outside_search", "search_folder_unreadable",
    "pattern_unreadable", "glob_unreadable", "glob_escaped", "glob_not_plain", "glob_climbs",
    "search_could_reach", "search_reaches", "search_anywhere", "search_protected_files",
    "halted", "unchecked",
    # enforce's refusal of SendMessage to a session aipager does not run
    # ("peer message authority").
    "send_unmanaged",
    # A turn held to a members' root folder (user-root-folders).
    "root_cwd_write", "root_cwd_search", "outside_root_read"}

#: The code each writer's sentence (S.safety_reasons(), in order) is, per
#: design.md section B's table.
EXPECTED_CODES = [
    "bash", "bash_search", "tool_not_allowed", "tool_denied", "path_unreadable",
    "path_protected", "path_protected_write", "runs_commands", "outside_write",
    "wide_cwd_write", "outside_search", "wide_cwd_search", "search_folder_unreadable",
    "path_protected", "search_could_reach", "search_reaches", "glob_unreadable",
    "glob_escaped", "glob_not_plain", "glob_climbs", "search_folder_unreadable",
    "pattern_unreadable", "search_anywhere", "search_protected_files"]

WRITERS = S.safety_reasons()
IDS = [f"{i}-{code}" for i, code in enumerate(EXPECTED_CODES)]
#: Writers whose sentence carries a protected path (glob) the person must
#: see verbatim.
WITH_GLOB = {5: "/srv/secret/**", 6: "/srv/keep2/**", 13: "/w/p/sub/**", 14: "**/.env",
             15: "/w/p/sub/**", 22: "**/.env"}

UNKNOWN = ["the moon is <b>full</b> & bright", "", "   ",
           safety.BASH_REASON + " ",                 # one byte longer than a known one
           " " + safety.BASH_REASON,                 # one byte earlier
           safety.BASH_REASON.upper(),
           "Read with an unreadable path.",          # a known shape plus a full stop
           "My Tool with an unreadable path",        # a tool name with a space
           "Session halted by someone",              # not the sticky prefix (case)
           " session halted"]                        # not at the start
HALTED = ["session halted", "session halted after a safety block; resume to continue",
          "session halted\nby a block earlier"]


def test_the_writers_cover_this_many_sentences():
    assert len(WRITERS) == len(EXPECTED_CODES)


def test_reason_shapes_carry_exactly_the_listed_codes():
    assert {code for code, _ in safety.REASON_SHAPES} == LISTED_CODES


def test_reason_shapes_patterns_compile():
    for _, pattern in safety.REASON_SHAPES:
        re.compile(pattern)


def test_reason_msgs_has_one_message_per_code():
    assert set(safety_notice.REASON_MSGS) == {code for code, _ in safety.REASON_SHAPES}


@pytest.mark.parametrize("i", range(len(EXPECTED_CODES)), ids=IDS)
def test_every_writer_sentence_parses_to_its_code(i):
    parsed = safety_notice.parse_reason(WRITERS[i][1])
    assert parsed is not None and parsed.code == EXPECTED_CODES[i]


@pytest.mark.parametrize("i", [i for i, (tool, r) in enumerate(WRITERS)
                               if EXPECTED_CODES[i] not in ("bash", "bash_search")])
def test_the_parsed_tool_is_the_one_blocked(i):
    tool, reason = WRITERS[i]
    assert safety_notice.parse_reason(reason).params.get("tool") == tool


@pytest.mark.parametrize("i", sorted(WITH_GLOB))
def test_the_parsed_glob_is_the_protected_path(i):
    assert safety_notice.parse_reason(WRITERS[i][1]).params.get("glob") == WITH_GLOB[i]


def test_a_glob_across_lines_still_parses():
    parsed = safety_notice.parse_reason("Read on protected path /a\n/b/**")
    assert parsed is not None and parsed.params.get("glob") == "/a\n/b/**"


@pytest.mark.parametrize("reason", UNKNOWN)
def test_a_sentence_aipager_does_not_write_parses_to_none(reason):
    assert safety_notice.parse_reason(reason) is None


# ---- English: exactly as received -----------------------------------------------------

@pytest.mark.parametrize("i", range(len(WRITERS)), ids=IDS)
def test_english_html_is_the_reason_escaped(i):
    reason = WRITERS[i][1]
    assert safety_notice.reason_text(i18n.get("en"), reason) == html.escape(reason)


@pytest.mark.parametrize("i", range(len(WRITERS)), ids=IDS)
def test_english_text_is_the_reason_as_received(i):
    reason = WRITERS[i][1]
    assert safety_notice.reason_text(i18n.get("en"), reason, kind="text") == reason


@pytest.mark.parametrize("i", range(len(WRITERS)), ids=IDS)
def test_english_notice_bytes(i):
    reason = WRITERS[i][1]
    assert safety_notice.notice_html(i18n.get("en"), "x1", reason) == (
        f"🛑 <b>x1</b> · Blocked by safety policy: {html.escape(reason)} (stopped)")


# ---- Persian and Russian ---------------------------------------------------------------

LANG_WRITERS = [(lang, i) for lang in ("fa", "ru") for i in range(len(WRITERS))]


@pytest.mark.parametrize("lang,i", LANG_WRITERS)
def test_a_known_reason_reads_in_the_chats_language(lang, i):
    shown = safety_notice.reason_text(i18n.get(lang), WRITERS[i][1])
    assert SCRIPT[lang].search(shown)


@pytest.mark.parametrize("lang,i", LANG_WRITERS)
def test_a_known_reason_is_not_the_english(lang, i):
    reason = WRITERS[i][1]
    assert safety_notice.reason_text(i18n.get(lang), reason) not in (reason,
                                                                       html.escape(reason))


@pytest.mark.parametrize("lang,i", LANG_WRITERS)
def test_the_tool_name_stays_verbatim(lang, i):
    tool = WRITERS[i][0]
    assert tool in safety_notice.reason_text(i18n.get(lang), WRITERS[i][1])


@pytest.mark.parametrize("lang,i", [(lang, i) for lang in ("fa", "ru") for i in WITH_GLOB])
def test_the_protected_path_stays_verbatim(lang, i):
    shown = safety_notice.reason_text(i18n.get(lang), WRITERS[i][1])
    assert WITH_GLOB[i] in shown


def test_persian_keeps_the_latin_digit_of_a_path():
    shown = safety_notice.reason_text(i18n.get("fa"), WRITERS[6][1])
    assert "/srv/keep2/**" in shown


def test_a_path_with_markup_is_escaped_in_persian():
    shown = safety_notice.reason_text(i18n.get("fa"), "Read on protected path /a/<b>&/**")
    assert "/a/&lt;b&gt;&amp;/**" in shown


@pytest.mark.parametrize("lang", ["fa", "ru"])
def test_the_notice_names_the_session(lang):
    shown = safety_notice.notice_html(i18n.get(lang), "x1", WRITERS[5][1])
    assert re.search("<b>[\u2068]?x1[\u2069]?</b>", shown)


@pytest.mark.parametrize("lang", ["fa", "ru"])
def test_the_notice_opens_with_the_stop_sign(lang):
    shown = safety_notice.notice_html(i18n.get(lang), "x1", WRITERS[5][1])
    assert shown.lstrip("\u200f").startswith("🛑")


# ---- unknown reasons --------------------------------------------------------------------

UNKNOWN_CASES = [(lang, r) for lang in ("en", "fa", "ru") for r in UNKNOWN]


@pytest.mark.parametrize("lang,reason", UNKNOWN_CASES)
def test_an_unknown_reason_is_shown_as_received_html(lang, reason):
    assert safety_notice.reason_text(i18n.get(lang), reason) == html.escape(reason)


@pytest.mark.parametrize("lang,reason", UNKNOWN_CASES)
def test_an_unknown_reason_is_shown_as_received_text(lang, reason):
    assert safety_notice.reason_text(i18n.get(lang), reason, kind="text") == reason


# ---- the sticky repeat ------------------------------------------------------------------

@pytest.mark.parametrize("reason", HALTED)
def test_a_session_halted_reason_is_sticky(reason):
    assert safety_notice.is_sticky(reason) is True


@pytest.mark.parametrize("reason", UNKNOWN + [r for _, r in WRITERS])
def test_anything_else_is_not_sticky(reason):
    assert safety_notice.is_sticky(reason) is False


def _blocked_world(h, lang):
    from aipager.state import Status
    bot, tg = S.world(h, lang)
    h.record_audit()
    x1 = S.session(bot, "x1", chat=S.GROUP, status=Status.BUSY)
    x1.busy_msg_id = None
    return bot, tg, x1


@pytest.mark.parametrize("lang", ["en", "fa", "ru"])
def test_a_sticky_repeat_after_a_block_posts_nothing(h, lang):
    bot, tg, x1 = _blocked_world(h, lang)
    h.run(bot.notify(x1, "safety_blocked", {"tool": "Write", "reason": WRITERS[6][1]}))
    shown = list(tg.out)
    h.run(bot.notify(x1, "safety_blocked", {"tool": "Bash", "reason": HALTED[1]}))
    assert tg.out == shown


@pytest.mark.parametrize("lang", ["en", "fa", "ru"])
def test_a_sticky_repeat_after_a_block_does_not_halt_again(h, lang):
    bot, tg, x1 = _blocked_world(h, lang)
    h.run(bot.notify(x1, "safety_blocked", {"tool": "Write", "reason": WRITERS[6][1]}))
    keys = list(h.keys)
    h.run(bot.notify(x1, "safety_blocked", {"tool": "Bash", "reason": HALTED[1]}))
    assert h.keys == keys


@pytest.mark.parametrize("lang", ["en", "fa", "ru"])
def test_a_sticky_repeat_is_audited_in_english(h, lang):
    bot, tg, x1 = _blocked_world(h, lang)
    h.run(bot.notify(x1, "safety_blocked", {"tool": "Bash", "reason": HALTED[1]}))
    assert h.audit and HALTED[1] in h.audit[-1]


@pytest.mark.parametrize("lang", ["fa", "ru"])
def test_a_multi_line_halted_repeat_posts_nothing(h, lang):
    """Today's rule is a prefix test: a second line does not make it new."""
    bot, tg, x1 = _blocked_world(h, lang)
    h.run(bot.notify(x1, "safety_blocked", {"tool": "Bash", "reason": HALTED[2]}))
    assert tg.out == []


@pytest.mark.parametrize("lang", ["fa", "ru"])
def test_a_first_block_halts_the_session(h, lang):
    bot, tg, x1 = _blocked_world(h, lang)
    h.run(bot.notify(x1, "safety_blocked", {"tool": "Write", "reason": WRITERS[6][1]}))
    assert h.keys


@pytest.mark.parametrize("lang", ["fa", "ru"])
def test_a_first_block_posts_the_translated_notice(h, lang):
    bot, tg, x1 = _blocked_world(h, lang)
    h.run(bot.notify(x1, "safety_blocked", {"tool": "Write", "reason": WRITERS[6][1]}))
    assert tg.out == [safety_notice.notice_html(i18n.get(lang), "x1", WRITERS[6][1])]


@pytest.mark.parametrize("lang", ["fa", "ru"])
def test_a_block_is_audited_with_the_english_reason(h, lang):
    bot, tg, x1 = _blocked_world(h, lang)
    h.run(bot.notify(x1, "safety_blocked", {"tool": "Write", "reason": WRITERS[6][1]}))
    assert ("('reason', " + repr(WRITERS[6][1]) + ")") in h.audit[0]


@pytest.mark.parametrize("lang", ["fa", "ru"])
def test_a_block_audit_summary_is_the_english_reason(h, lang):
    bot, tg, x1 = _blocked_world(h, lang)
    h.run(bot.notify(x1, "safety_blocked", {"tool": "Write", "reason": WRITERS[6][1]}))
    assert ("('summary', " + repr(WRITERS[6][1]) + ")") in h.audit[0]


@pytest.mark.parametrize("lang", ["fa", "ru"])
def test_an_unknown_reason_through_notify_carries_it_verbatim(h, lang):
    bot, tg, x1 = _blocked_world(h, lang)
    h.run(bot.notify(x1, "safety_blocked", {"tool": "Bash", "reason": UNKNOWN[0]}))
    assert html.escape(UNKNOWN[0]) in tg.out[0]


# ---- enforce's own fail-closed reason ---------------------------------------------------

def test_enforces_fail_closed_reason_parses_as_unchecked():
    from aipager.dtach import enforce
    closed = enforce.fail_closed({"session": "claude-x__g100", "tool_name": "Bash"})
    reason = (closed or {}).get("reason", "")
    parsed = safety_notice.parse_reason(reason)
    assert parsed is not None and parsed.code == "unchecked"


@pytest.mark.parametrize("lang", ["fa", "ru"])
def test_enforces_fail_closed_reason_reads_in_the_chats_language(lang):
    from aipager.dtach import enforce
    closed = enforce.fail_closed({"session": "claude-x__g100", "tool_name": "Bash"})
    reason = (closed or {}).get("reason", "")
    assert SCRIPT[lang].search(safety_notice.reason_text(i18n.get(lang), reason))
