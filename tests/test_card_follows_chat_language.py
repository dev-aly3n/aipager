"""The busy card follows the chat's language, and Claude's words on it keep
their own direction (operator, 2026-10-17).

The operator's screenshot: an English chat, the prompt and Claude in
Persian, the card sent with ``is_rtl`` true, so on Telegram Desktop every
line of it (aipager's English rows and status line too) sat on the
right. The card is mostly aipager's own lines, so it now takes the
chat's direction: an English chat's card is a left-to-right rich message,
a Persian or Arabic chat's a right-to-left one, whatever Claude or the
prompt is written in. A message that carries Claude's answer (the merged
layout's finished card) keeps following the answer.

Reading order is judged with the Unicode Bidirectional Algorithm
(:mod:`tests.bidi_uba`): ``para=0``/``para=1`` model Telegram Desktop and
Web, which force every block to the message's direction; ``para=None``
(P2/P3, the first strong character) models iPhone and Android. A line
"reads as" a list of parts when the app shows each left-to-right part
left to right and each right-to-left part right to left as a whole, in
the line's order (:func:`reads_as`).
"""

from __future__ import annotations

import re
import time
import unicodedata
from unittest.mock import AsyncMock

import pytest

from aipager import i18n
from aipager.bot import animation, card_rows, claude_bidi
from aipager.state import SessionRegistry, Status
from tests.bidi_uba import levels, shown_text, visual_order
from tests.footer_text import unfooter

RLM, LRM, LRI, RLI, FSI, PDI = "\u200f", "\u200e", "\u2066", "\u2067", "\u2068", "\u2069"
INVISIBLE = re.compile("[\u200e\u200f\u2066-\u2069]")
CODE = re.compile(r"(`+)(?:(?!\1).)*?\1", re.S)

#: Claude's Persian: a sentence, a search query, a question.
FA = "این فایل"            # "this file"
FA2 = "را خواندم"          # "I read"
FA_SENTENCE = FA + " " + FA2 + "."
FA_QUERY = "قیمت ذغال ۱۴۰۵"
FA_QUESTION = "کدام گزینه؟"
EN_SENTENCE = "I read the file first."


def _visible(ch: str) -> bool:
    return not (unicodedata.category(ch) == "Cf" or ch in "\u2066\u2067\u2068\u2069")


def visual(line: str, para: int | None) -> str:
    """*line* as shown, left to right, without the invisible controls."""
    return "".join(line[i] for i in visual_order(line, para) if _visible(line[i]))


def reads_as(line: str, parts: list[tuple[str, bool]], para: int | None,
             *, base_rtl: bool = False) -> bool:
    """Whether *line* shows as *parts* (``(text, rtl)``, in logical order,
    together the shown line without its controls): each part laid out in
    its own direction as a whole, the parts in the line's order (left to
    right in a left-to-right line, right to left in a right-to-left one,
    *base_rtl*)."""
    assert "".join(t for t, _r in parts) == "".join(ch for ch in line if _visible(ch))
    shown = [visual(text, 1 if rtl else 0) for text, rtl in parts]
    want = "".join(reversed(shown) if base_rtl else shown)
    return visual(line, para) == want


def _line_level(line: str, para: int | None) -> int:
    return min(lv for lv in levels(line, para) if lv is not None)


def _session(*, chat: int = 0, label: str = "safar"):
    reg = SessionRegistry()
    s = reg.get_or_create("claude-safar")
    s.label = label
    s.status = Status.BUSY
    if chat:
        s.scope_chat_id, s.scope_kind = chat, "dm"
    s.busy_started_at = time.monotonic() - 27
    s.cost_baseline, s.last_cost_usd = 0.0, 0.10
    s.tool_history = [("Bash: Look for earlier taxi and price research notes", True)]
    s.tool_history += [("WebSearch: " + FA_QUERY, False) for _ in range(4)]
    s.stream_commentary = []
    s.turn_prompt = FA_SENTENCE
    return s


def _rows(card: str) -> list[str]:
    return [ln for ln in card.split("\n")
            if ln.strip() and ln.lstrip(LRM + RLM)[:1] in ("✅", "⏳", "❌")]


def _no_invisible_in_code(text: str) -> None:
    for m in CODE.finditer(text):
        assert not INVISIBLE.search(m.group(0)), m.group(0)
    for tag in re.findall(r"<[^>]*>", text):
        assert not INVISIBLE.search(tag), tag


# ---- the card's one direction ------------------------------------------------------------

@pytest.mark.parametrize("code", sorted(i18n.locales.LOCALES))
def test_the_card_takes_the_chats_direction_whatever_claude_writes(code):
    tr = i18n.get(code)
    for prompt in ("", FA_SENTENCE, EN_SENTENCE):
        for words in ((), (FA_SENTENCE,), (EN_SENTENCE,)):
            s = _session()
            s.turn_prompt = prompt
            s.stream_commentary = [(0, w) for w in words]
            assert animation._card_rtl(s, tr) is tr.language_rtl
            assert animation.card_tr(s, tr) is tr


def test_the_screenshots_card_is_left_to_right_in_an_english_chat(mk_bot, run_async,
                                                                  monkeypatch):
    from aipager import preferences
    preferences.set_ui_language(4343, "")
    bot = mk_bot()
    s = _session(chat=4343)
    s.stream_commentary = [(1, FA_SENTENCE)]
    s.busy_msg_id = 55
    bot.registry._sessions[s.name] = s
    edit = AsyncMock(return_value={"message_id": 55})
    monkeypatch.setattr(animation, "edit_message_text_rich", edit)
    run_async(bot._edit_busy_rich(s, "Noodling"))
    assert edit.await_args.kwargs["is_rtl"] is False
    card = unfooter(edit.await_args.args[2])
    _no_invisible_in_code(card)
    status = card.rsplit("\n", 1)[-1]
    # aipager's own lines carry no mark in an English chat.
    assert not INVISIBLE.search(status)
    assert status.startswith("⏳ **safar** · Noodling · 0:27 · $0.10")
    rows = _rows(card.rsplit("\n", 1)[0])
    assert len(rows) == 5
    assert rows[0] == "✅ `Bash: Look for earlier taxi and price research notes`"
    for para in (0, None):
        assert reads_as(shown_text(rows[0]), [(shown_text(rows[0]), False)], para)
        for row in rows[1:]:
            line = shown_text(row)
            assert reads_as(line, [("⏳ WebSearch: ", False), (FA_QUERY, True)], para), \
                (para, visual(line, para))
            assert _line_level(line, para) == 0
        prose = [ln for ln in card.split("\n") if ln.startswith("> ")]
        assert prose == ["> " + RLM + RLI + FA_SENTENCE + PDI + RLM]
        line = prose[0][2:]
        assert reads_as(line, [(FA_SENTENCE, True)], para), visual(line, para)


@pytest.mark.parametrize("final", [False, True], ids=["live", "final"])
def test_a_persian_chats_card_under_english_is_right_to_left(final, mk_bot, run_async,
                                                             monkeypatch):
    # The mirror case: Claude writes English in a Persian chat.
    from aipager import preferences
    preferences.set_ui_language(4343, "fa")
    bot = mk_bot()
    s = _session(chat=4343)
    s.tool_history = [("Read: /src/app.py", True)]
    s.turn_prompt = EN_SENTENCE
    s.stream_commentary = [(0, EN_SENTENCE)]
    s.busy_msg_id = 55
    bot.registry._sessions[s.name] = s
    edit = AsyncMock(return_value={"message_id": 55})
    monkeypatch.setattr(animation, "edit_message_text_rich", edit)
    run_async(bot._edit_busy_rich(s, "Working", final=final))
    assert edit.await_args.kwargs["is_rtl"] is True
    card = unfooter(edit.await_args.args[2])
    _no_invisible_in_code(card)
    assert card.rsplit("\n", 1)[-1].startswith(RLM)
    prose = [ln for ln in card.split("\n") if ln.startswith("> ")]
    assert prose == ["> " + LRM + LRI + EN_SENTENCE + PDI + LRM]
    line = prose[0][2:]
    for para in (1, None):
        assert reads_as(line, [(EN_SENTENCE, False)], para, base_rtl=para == 1), \
            visual(line, para)


@pytest.mark.parametrize("code,answer,want", [("en", FA_SENTENCE, True),
                                              ("en", EN_SENTENCE, False),
                                              ("fa", EN_SENTENCE, False),
                                              ("fa", FA_SENTENCE, True)])
def test_the_merged_finished_card_keeps_following_the_answer(code, answer, want, mk_bot,
                                                             run_async, monkeypatch):
    from aipager import preferences
    from aipager.bot import notify
    preferences.set_ui_language(4343, "" if code == "en" else code)
    bot = mk_bot()
    s = _session(chat=4343)
    s.stream_commentary = [(0, EN_SENTENCE), (1, FA_SENTENCE)]
    s.busy_msg_id = 55
    bot.registry._sessions[s.name] = s
    edit = AsyncMock(return_value={"message_id": 55})
    monkeypatch.setattr(notify, "edit_message_text_rich", edit)
    assert run_async(bot._send_merged_final(s, answer)) is True
    assert edit.await_args.kwargs["is_rtl"] is want
    text = edit.await_args.args[2]
    _no_invisible_in_code(text)
    prose = [ln for ln in text.split("\n") if ln.startswith("> ")]
    # Each of Claude's lines is isolated only where it goes against the
    # message: the answer's direction decides the message.
    if want:
        assert prose == ["> " + LRM + LRI + EN_SENTENCE + PDI + LRM, "> " + FA_SENTENCE]
    else:
        assert prose == ["> " + EN_SENTENCE, "> " + RLM + RLI + FA_SENTENCE + PDI + RLM]


def test_the_paused_and_moved_cards_take_the_chats_direction(mk_bot, run_async, monkeypatch):
    from aipager import preferences
    preferences.set_ui_language(4343, "")
    bot = mk_bot()
    s = _session(chat=4343)
    s.stream_commentary = [(0, FA_SENTENCE)]
    s.busy_msg_id = 55
    bot.registry._sessions[s.name] = s
    rich = AsyncMock(return_value={"message_id": 56})
    monkeypatch.setattr(animation, "send_rich_message", rich)
    s.stream_last_rendered = animation._PAUSED_CARD_TEXT
    run_async(bot._send_card_copy(s, 9, verb="Working", final=False, waiting=False))
    assert rich.await_args.kwargs["is_rtl"] is False
    assert not INVISIBLE.search(rich.await_args.args[1])
    s.busy_msg_id = 56
    s.stream_last_rendered = ""
    run_async(bot._send_card_copy(s, 9, verb="Working", final=True, waiting=False))
    assert rich.await_args.kwargs["is_rtl"] is False


# ---- Claude's prose: claude_bidi.prose ---------------------------------------------------

#: (block, the expected marked block in a left-to-right card, the parts the
#: marked line reads as, the line index judged)
PROSE_LTR = {
    "sentence": (FA_SENTENCE, RLM + RLI + FA_SENTENCE + PDI + RLM),
    "english-word": (FA + " config.py " + FA2 + ".",
                     RLM + RLI + FA + " config.py " + FA2 + "." + PDI + RLM),
    "opens-on-code": ("`config.py` " + FA2 + ".",
                      RLM + RLI + "`config.py` " + FA2 + "." + PDI + RLM),
    "list-item": ("- " + FA_SENTENCE, "- " + RLM + RLI + FA_SENTENCE + PDI + RLM),
    "ordered": ("2. " + FA_SENTENCE, "2. " + RLM + RLI + FA_SENTENCE + PDI + RLM),
    "heading": ("## " + FA, "## " + RLM + RLI + FA + PDI + RLM),
    "nested": ("  > - " + FA, "  > - " + RLM + RLI + FA + PDI + RLM),
    "bold": ("**" + FA + ".**", "**" + RLM + RLI + FA + "." + PDI + RLM + "**"),
    "italic": ("_" + FA + "_", "_" + RLM + RLI + FA + PDI + RLM + "_"),
    "hard-break": (FA + "  ", RLM + RLI + FA + PDI + RLM + "  "),
    # A link's address and a tag's words are not the sentence's letters.
    "link": ("[" + FA + "](https://example.invalid/a/rather/long/address/here)",
             RLM + RLI + "[" + FA + "](https://example.invalid/a/rather/long/address/here)"
             + PDI + RLM),
    "tag": ('<span class="tg-spoiler">' + FA + "</span>",
            RLM + RLI + '<span class="tg-spoiler">' + FA + "</span>" + PDI + RLM),
    # A *, _ or ~ that pairs with nothing is text: it stays inside.
    "star-glob": ("*.py " + FA, RLM + RLI + "*.py " + FA + PDI + RLM),
    "tilde-path": ("~/src " + FA_SENTENCE, RLM + RLI + "~/src " + FA_SENTENCE + PDI + RLM),
    "lone-tail": (FA + " foo~", RLM + RLI + FA + " foo~" + PDI + RLM),
    "bold-mid": ("**" + FA + "** " + FA2, "**" + RLM + RLI + FA + "** " + FA2 + PDI + RLM),
}


@pytest.mark.parametrize("case", sorted(PROSE_LTR))
def test_a_persian_line_in_a_left_to_right_card_reads_right_to_left(case):
    block, want = PROSE_LTR[case]
    assert claude_bidi.prose(block, False) == want
    if case in ("link", "tag"):
        return          # the marks' place is pinned above; markup is not judged
    line = shown_text(want.lstrip(" >-#0123456789.").lstrip())
    if case not in ("star-glob", "tilde-path", "lone-tail"):
        line = line.replace("_", "")
    line = line.rstrip()      # a hard break's spaces are not shown
    for para in (0, None):
        assert reads_as(line, [(INVISIBLE.sub("", line), True)], para), (para, visual(line, para))


def test_a_persian_line_without_marks_reads_out_of_order_on_desktop():
    # What the marks are for: the same sentence with an English word, bare,
    # breaks apart in a left-to-right block on Desktop and Web.
    line = FA + " config.py " + FA2 + "."
    assert not reads_as(line, [(line, True)], 0)


@pytest.mark.parametrize("block", [
    "```\n" + FA + "\n```",
    "~~~\n" + FA + "\n~~~",
    "<pre>" + FA + "</pre>",
    "<pre>\n" + FA + "\n</pre>",
    "| " + FA + " | x |",
    "`" + FA + "\n" + FA2 + "`",
    "\u2067" + FA + "\u2069",
    EN_SENTENCE,
    "`" + FA + "`",
    "123 · ---",
    "    " + FA,
    "\t" + FA,
], ids=["fence", "tilde-fence", "pre", "pre-lines", "table", "span-across-lines",
        "claudes-own-isolate", "english", "code-only", "no-letters", "indented-spaces",
        "indented-tab"])
def test_nothing_is_marked_in_code_tables_or_english(block):
    assert claude_bidi.prose(block, False) == block


def test_each_line_of_a_block_is_judged_on_its_own():
    block = "\n".join([EN_SENTENCE, FA_SENTENCE, "```", FA, "```", "- " + FA])
    assert claude_bidi.prose(block, False).split("\n") == [
        EN_SENTENCE, RLM + RLI + FA_SENTENCE + PDI + RLM, "```", FA, "```",
        "- " + RLM + RLI + FA + PDI + RLM]
    assert claude_bidi.prose(block, True).split("\n") == [
        LRM + LRI + EN_SENTENCE + PDI + LRM, FA_SENTENCE, "```", FA, "```", "- " + FA]


def test_an_english_line_in_a_right_to_left_card_reads_left_to_right():
    mixed = "Read " + FA + " now."
    marked = claude_bidi.prose(mixed, True)
    assert marked == LRM + LRI + mixed + PDI + LRM
    for para in (1, None):
        assert reads_as(marked, [("Read ", False), (FA, True), (" now.", False)], para), \
            (para, visual(marked, para))
    assert not reads_as(mixed, [("Read ", False), (FA, True), (" now.", False)], 1)


def test_a_card_quoting_a_persian_sentence_never_marks_inside_code():
    s = _session()
    s.stream_commentary = [(0, "`" + FA + "` " + FA2 + "\n```\n" + FA + "\n```")]
    card, _ = animation.build_stream_card_ex(s, "Working", tr=i18n.get("en"))
    _no_invisible_in_code(card)
    assert "> " + RLM + RLI + "`" + FA + "` " + FA2 + PDI + RLM in card.split("\n")


# ---- Claude's words in rows built in code ------------------------------------------------

def _row(tr, summary: str, done=True) -> str:
    s = _session()
    s.tool_history = [(summary, done)]
    card, _ = animation.build_stream_card_ex(s, "Working", tr=tr)
    (row,) = _rows(card)
    _no_invisible_in_code(row)
    return row


def test_a_persian_question_row_is_laid_out_left_to_right():
    row = _row(i18n.get("en"), "❓ " + FA_QUESTION + " → Selected option 2")
    assert row == (LRM + "✅ ❓ " + FSI + "`" + FA_QUESTION + "`" + PDI
                   + " → Selected option 2")
    line = shown_text(row)
    for para in (0, None):
        assert reads_as(line, [("✅ ❓ ", False), (FA_QUESTION, True),
                               (" → Selected option 2", False)], para), \
            (para, visual(line, para))
    # Without the mark iPhone and Android lay it out right to left.
    bare = INVISIBLE.sub("", line)
    assert not reads_as(bare, [("✅ ❓ ", False), (FA_QUESTION, True),
                               (" → Selected option 2", False)], None)


def test_a_persian_selection_and_shell_rows_keep_their_clock_and_words_after():
    en = i18n.get("en")
    row = _row(en, "❓ Pick → Selected: " + FA)
    assert row.endswith("Selected: " + FSI + "`" + FA + "`" + PDI)
    row = _row(en, "shell: " + FA + " - done (1:30)", True)
    line = shown_text(row)
    for para in (0, None):
        assert reads_as(line, [("✅ shell: ", False), (FA, True),
                               (" - done (1:30)", False)], para), (para, visual(line, para))


def test_a_live_agent_and_a_live_shell_keep_the_clock_last(monkeypatch):
    en = i18n.get("en")
    now = time.monotonic()
    agent = card_rows.live_agent(en, {"type": "general-purpose", "activity": "WebSearch: " + FA,
                                      "started_at": now - 12})
    assert agent == ("\U0001f916 `general-purpose` · " + FSI + "`WebSearch: " + FA + "`"
                     + PDI + " · 0:12")
    shell = card_rows.live_shell(en, FA, now - 30)
    assert shell == "shell: " + FSI + "`" + FA + "`" + PDI + " (0:30)"
    for text, parts in ((agent, [("\U0001f916 general-purpose · WebSearch: ", False),
                                 (FA, True), (" · 0:12", False)]),
                        (shell, [("shell: ", False), (FA, True), (" (0:30)", False)])):
        line = shown_text(text)
        for para in (0, None):
            assert reads_as(line, parts, para), (para, visual(line, para))
        # Without the isolate the clock joins Claude's phrase.
        assert not reads_as(INVISIBLE.sub("", line), parts, 0)


def test_english_rows_stay_byte_for_byte():
    en = i18n.get("en")
    now = time.monotonic()
    assert card_rows.live_agent(en, {"type": "general-purpose", "activity": "Read: /a",
                                     "started_at": now - 12}) == \
        "`\U0001f916 general-purpose · Read: /a · 0:12`"
    assert card_rows.live_shell(en, "npm run dev", now - 30) == "`shell: npm run dev (0:30)`"
    assert card_rows.render_md(en, "❓ Which? → Selected option 2") == \
        "`❓ Which? → Selected option 2`"
    assert _row(en, "WebSearch: " + FA_QUERY, False) == "⏳ `WebSearch: " + FA_QUERY + "`"


def test_a_persian_chats_rows_are_not_isolated_twice():
    fa = i18n.get("fa")
    row = _row(fa, "❓ " + FA_QUESTION + " → Selected option 2")
    assert row.count(FSI) == row.count(PDI) and FSI + FSI not in row
    assert LRM not in row


def test_a_row_is_marked_only_in_a_left_to_right_rendering():
    row = "✅ ❓ `" + FA_QUESTION + "` → Selected option 2"
    assert claude_bidi.row(i18n.get("en"), row) == LRM + row
    assert claude_bidi.row(i18n.get("fa").directed(False), row) == LRM + row
    # A right-to-left rendering (the chat's own, or an English chat's
    # in-RTL twin) marks its rows itself: nothing is added here.
    assert claude_bidi.row(i18n.get("fa"), row) == row
    assert claude_bidi.row(i18n.get("en").directed(True), row) == row
    assert claude_bidi.row(i18n.get("en"), "✅ `Read: /a`") == "✅ `Read: /a`"


def test_a_python_dunder_name_follows_commonmark():
    # CommonMark (and GitHub) draw __init__.py as bold "init" then ".py":
    # the run pairs, so the marks go inside it. A run that pairs with
    # nothing is text, and the marks stay outside it.
    assert claude_bidi.prose("__init__.py " + FA, False) == \
        "__" + RLM + RLI + "init__.py " + FA + PDI + RLM
    assert claude_bidi.prose("__init " + FA, False) == RLM + RLI + "__init " + FA + PDI + RLM


def test_a_row_starting_with_a_tag_is_judged_by_its_text():
    row = "<b>x</b> " + FA
    assert claude_bidi.row(i18n.get("en"), "<b>" + FA + "</b> ok") == \
        LRM + "<b>" + FA + "</b> ok"
    assert claude_bidi.row(i18n.get("en"), row) == row


def _status(code, label):
    from aipager import preferences
    preferences.set_ui_language(4343, "" if code == "en" else code)
    s = _session(chat=4343, label=label)
    status = unfooter(animation.build_stream_card(s, "Working")).rsplit("\n", 1)[-1]
    return s, status


@pytest.mark.parametrize("code", ["en", "de", "ko"])
def test_a_persian_session_name_keeps_aipagers_lines_left_to_right(code, mk_bot, run_async,
                                                                   monkeypatch):
    s, status = _status(code, FA)
    assert status.startswith(LRM) and status.count(LRM) == 1
    line = shown_text(status)
    for para in (0, None):
        assert _line_level(line, para) == 0, (para, visual(line, para))
        assert visual(line, para).startswith("\u23f3"), (para, visual(line, para))
    # An ASCII name gets nothing.
    _s, plain = _status(code, "safar")
    assert not INVISIBLE.search(plain)
    # The waiting frame's status line (a background job finishing).
    s.status = Status.IDLE
    waiting = unfooter(animation.build_stream_card(s, "Working", waiting=True))
    waiting = waiting.rsplit("\n", 1)[-1]
    assert waiting.startswith(LRM) and _line_level(shown_text(waiting), None) == 0
    s.status = Status.BUSY
    # The paused line of a moved card.
    bot = mk_bot()
    bot.registry._sessions[s.name] = s
    s.busy_msg_id = 55
    s.stream_last_rendered = animation._PAUSED_CARD_TEXT
    rich = AsyncMock(return_value={"message_id": 56})
    monkeypatch.setattr(animation, "send_rich_message", rich)
    run_async(bot._send_card_copy(s, 9, verb="Working", final=False, waiting=False))
    assert rich.await_args.kwargs["is_rtl"] is False
    paused = rich.await_args.args[1]
    assert paused.startswith(LRM) and _line_level(shown_text(paused), None) == 0


@pytest.mark.parametrize("code", ["ko", "tr"])
def test_a_persian_peer_name_keeps_the_peer_line_left_to_right(code):
    # ko and tr name the sending session first.
    s = _session(label="safar")
    s.peer_from = {"status": "verified", "sender_session": "claude-" + FA2.replace(" ", "")}
    card = animation.build_stream_card(s, "Working", tr=i18n.get(code))
    peer = card.split("\n", 1)[0]
    assert peer.startswith(LRM) and FA2.replace(" ", "") in peer
    assert _line_level(shown_text(peer), None) == 0
    s.peer_from = {"status": "verified", "sender_session": "claude-frontend"}
    card = animation.build_stream_card(s, "Working", tr=i18n.get(code))
    assert not INVISIBLE.search(card.split("\n", 1)[0])


#: Delimiter runs that open or close nothing (review rev-iter2-001): each
#: line is wrapped whole, its *, _ and ~ kept inside with the words.
UNPAIRED = {
    "intraword-underscores": FA + " my_var_",
    "intraword-double": FA + " a__b__",
    "underscore-name-first": "_foo_bar " + FA,
    "lead-before-space": "** " + FA + "**",
    "two-globs": "*.py \u0648 *.md " + FA,
    "two-paths": "~/src \u0648 ~/lib " + FA,
    "tail-after-space": FA + " *x *",
    "intraword-star-tail": FA + " a*b *",
    "unequal-lead": "**" + FA + "* x*",
    "unequal-lead-short": "*" + FA + "** x**",
}


@pytest.mark.parametrize("case", sorted(UNPAIRED))
def test_a_run_that_pairs_with_nothing_stays_inside(case):
    line = UNPAIRED[case]
    assert claude_bidi.prose(line, False) == RLM + RLI + line + PDI + RLM


def test_a_symbol_counts_as_punctuation_for_emphasis():
    # CommonMark: a Unicode symbol (here "=") is punctuation, so the "_"
    # after it opens and the last "_" closes: the marks stay inside.
    assert claude_bidi.prose(FA + " a=_x_", False) == RLM + RLI + FA + " a=_x" + PDI + RLM + "_"


def test_the_last_run_never_pairs_with_itself():
    # Review rev-iter3-001: before a backslash line break, a "*" between
    # punctuation could both open and close; alone it is text and stays
    # inside, the break outside.
    assert claude_bidi.prose(FA + " .*\\", False) == RLM + RLI + FA + " .*" + PDI + RLM + "\\"
