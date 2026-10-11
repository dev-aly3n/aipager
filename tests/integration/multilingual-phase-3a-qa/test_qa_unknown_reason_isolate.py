"""Iteration 3: in a Persian chat, a safety notice for a reason aipager does
not recognise carries that reason HTML-escaped and wrapped in one first
strong isolate ... pop directional isolate pair, so a left-to-right reason
cannot reorder the right-to-left frame around it. English and Russian bytes
for the same notice are unchanged from the iteration-2 expectations
(criterion 2 for English; criterion 10: the reason itself is as received).

Black-box through ``safety_notice.notice_html`` and the ``safety_blocked``
notice through ``notify`` (entrypoints.md). Invisible marks are written by
name, never as raw characters."""

from __future__ import annotations

import html

import pytest
import qa_p3a_scenarios as S

from aipager import i18n
from aipager.bot import safety_notice

FSI = "\N{FIRST STRONG ISOLATE}"
PDI = "\N{POP DIRECTIONAL ISOLATE}"
RLM = "\N{RIGHT-TO-LEFT MARK}"
ZWNJ = "\N{ZERO WIDTH NON-JOINER}"

UNKNOWN = ["the moon is <b>full</b> & bright",
           "error 42 at line 7 (code 0x1F)",
           "line one\nline <two> & three",
           "Read with an unreadable path.",      # a known shape plus a full stop
           "Session halted by someone",          # not the sticky prefix
           "\"quoted\" 'single'",
           ""]
IDS = [f"u{i}" for i in range(len(UNKNOWN))]

#: The frames each language showed at the end of iteration 2 (tree 62ad7a3),
#: with ``{r}`` for the HTML-escaped reason; Persian's as reworded in the
#: 2026-10-11 rewrite.
EN_FRAME = "🛑 <b>x1</b> · Blocked by safety policy: {r} (stopped)"
RU_FRAME = ("🛑 <b>x1</b> · Заблокировано правилами безопасности: {r} "
            "(остановлено)")
FA_FRAME = (RLM + "🛑 <b>" + FSI + "x1" + PDI + "</b> · قانون ایمنی جلویش را گرفت: {r} "
            "(متوقف شد)")


def _expected(frame: str, reason: str) -> str:
    return frame.replace("{r}", html.escape(reason), 1)


def _notice(lang: str, reason: str) -> str:
    return safety_notice.notice_html(i18n.get(lang), "x1", reason)


# ---- Persian: the reason sits in its own isolate ----------------------------------------

@pytest.mark.parametrize("reason", UNKNOWN, ids=IDS)
def test_a_persian_notice_isolates_the_escaped_unknown_reason(reason):
    assert FSI + html.escape(reason) + PDI in _notice("fa", reason)


@pytest.mark.parametrize("reason", UNKNOWN, ids=IDS)
def test_a_persian_notice_of_an_unknown_reason_is_the_frame_plus_one_isolate(reason):
    isolated = FA_FRAME.replace("{r}", FSI + "{r}" + PDI)
    assert _notice("fa", reason) == _expected(isolated, reason)


@pytest.mark.parametrize("reason", UNKNOWN, ids=IDS)
def test_a_persian_notice_balances_its_isolates(reason):
    shown = _notice("fa", reason)
    assert shown.count(FSI) == shown.count(PDI)


def test_a_persian_notice_leaves_no_raw_markup_from_the_reason():
    assert "<b>full</b>" not in _notice("fa", UNKNOWN[0])


def test_a_persian_notice_still_opens_with_a_right_to_left_mark():
    assert _notice("fa", UNKNOWN[0]).startswith(RLM + "🛑")


def test_the_persian_reason_text_itself_stays_as_received():
    """The isolate belongs to the notice; reason_text is unchanged (criterion 10)."""
    assert safety_notice.reason_text(i18n.get("fa"), UNKNOWN[0]) == html.escape(UNKNOWN[0])


# ---- English and Russian: byte for byte as before -----------------------------------------

@pytest.mark.parametrize("reason", UNKNOWN, ids=IDS)
def test_an_english_notice_of_an_unknown_reason_is_unchanged(reason):
    assert _notice("en", reason) == _expected(EN_FRAME, reason)


@pytest.mark.parametrize("reason", UNKNOWN, ids=IDS)
def test_a_russian_notice_of_an_unknown_reason_is_unchanged(reason):
    assert _notice("ru", reason) == _expected(RU_FRAME, reason)


@pytest.mark.parametrize("lang", ["en", "ru"])
def test_no_isolate_reaches_an_english_or_russian_notice(lang):
    assert FSI not in _notice(lang, UNKNOWN[0])


# ---- through notify (what the chat receives) --------------------------------------------

def _blocked_world(h, lang):
    from aipager.state import Status
    bot, tg = S.world(h, lang)
    h.record_audit()
    x1 = S.session(bot, "x1", chat=S.GROUP, status=Status.BUSY)
    x1.busy_msg_id = None
    return bot, tg, x1


def test_a_persian_chat_receives_the_isolated_unknown_reason(h):
    bot, tg, x1 = _blocked_world(h, "fa")
    h.run(bot.notify(x1, "safety_blocked", {"tool": "Bash", "reason": UNKNOWN[0]}))
    assert FSI + html.escape(UNKNOWN[0]) + PDI in tg.out[0]


def test_a_russian_chat_receives_the_unchanged_unknown_reason_notice(h):
    bot, tg, x1 = _blocked_world(h, "ru")
    h.run(bot.notify(x1, "safety_blocked", {"tool": "Bash", "reason": UNKNOWN[0]}))
    assert tg.out == [_expected(RU_FRAME, UNKNOWN[0])]


def test_an_english_chat_receives_the_unchanged_unknown_reason_notice(h):
    bot, tg, x1 = _blocked_world(h, "en")
    h.run(bot.notify(x1, "safety_blocked", {"tool": "Bash", "reason": UNKNOWN[0]}))
    assert tg.out == [_expected(EN_FRAME, UNKNOWN[0])]


def test_a_persian_block_is_still_audited_with_the_raw_reason(h):
    bot, tg, x1 = _blocked_world(h, "fa")
    h.run(bot.notify(x1, "safety_blocked", {"tool": "Bash", "reason": UNKNOWN[0]}))
    assert ("('reason', " + repr(UNKNOWN[0]) + ")") in h.audit[0]
