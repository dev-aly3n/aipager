"""A reply to one of Claude's answers (a Telegram rich message) carries
the answer's text, and a part highlighted in one carries that part.

Claude's answers go out through ``sendRichMessage``. Telegram delivers a
reply to one with ``reply_to_message.rich_message`` and no ``text``, and
a highlight from one as ``quote.rich_texts`` with an empty ``text`` (Bot
API 10.4). python-telegram-bot 22.7 keeps both in ``api_kwargs``: every
message here is parsed from Update JSON with ``Update.de_json`` so the
fields arrive exactly that way.

Two layers: the extractors in ``aipager.bot.rich_read`` (each block and
text type of the Bot API reference, nesting, unknown and malformed
shapes) and ``_build_reply_context`` / ``_resolve_reply_target`` fed
with parsed updates.
"""

from __future__ import annotations

import pytest
from telegram import Update

from aipager import policy_snapshot as ps
from aipager.bot import rich_read
from aipager.state import Status, TrackedSession

BOT_ID = 999999
CHAT = 4242
USER = 5


# ---- building updates ------------------------------------------------------

def _para(*parts):
    return {"type": "paragraph", "text": list(parts)}


def _answer(blocks, *, message_id=300, from_id=BOT_ID):
    return {
        "message_id": message_id, "date": 1700000000,
        "chat": {"id": CHAT, "type": "private"},
        "from": {"id": from_id, "is_bot": True, "first_name": "Bot"},
        "rich_message": {"blocks": blocks},
    }


def _update(reply_to=None, quote=None, text="what about this?"):
    message = {
        "message_id": 400, "date": 1700000100,
        "chat": {"id": CHAT, "type": "private"},
        "from": {"id": USER, "is_bot": False, "first_name": "A"},
        "text": text,
    }
    if reply_to is not None:
        message["reply_to_message"] = reply_to
    if quote is not None:
        message["quote"] = quote
    return Update.de_json({"update_id": 1, "message": message}, None)


def _sess(**kw):
    s = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    s.last_msg_id = 999
    for k, v in kw.items():
        setattr(s, k, v)
    return s


# ---- the fields arrive in api_kwargs (the premise) ---------------------------

def test_ptb_keeps_the_rich_message_of_a_replied_message_in_api_kwargs():
    msg = _update(_answer([_para("hi")])).message
    assert msg.reply_to_message.text is None
    assert msg.reply_to_message.caption is None
    assert "rich_message" in msg.reply_to_message.api_kwargs


def test_ptb_keeps_the_rich_texts_of_a_quote_in_api_kwargs():
    msg = _update(_answer([_para("hi")]),
                  quote={"text": "", "position": 0, "rich_texts": ["hi"]}).message
    assert msg.quote.text == ""
    assert msg.quote.api_kwargs["rich_texts"] == ["hi"]


# ---- rich_text_plain: every RichText shape -----------------------------------

@pytest.mark.parametrize("kind", [
    "bold", "italic", "underline", "strikethrough", "spoiler", "subscript",
    "superscript", "marked", "code", "url", "email_address", "phone_number",
    "bank_card_number", "ton_address", "mention", "hashtag", "cashtag",
    "bot_command", "anchor_link", "reference", "reference_link",
    "text_mention", "date_time",
])
def test_a_text_type_with_text_shows_its_text(kind):
    assert rich_read.rich_text_plain({"type": kind, "text": "word", "url": "x"}) == "word"


def test_plain_string_and_nested_arrays_join_in_order():
    value = ["a ", ["b", {"type": "bold", "text": ["c", {"type": "italic", "text": "d"}]}], " e"]
    assert rich_read.rich_text_plain(value) == "a bcd e"


def test_custom_emoji_shows_its_alternative_text():
    assert rich_read.rich_text_plain(
        {"type": "custom_emoji", "custom_emoji_id": "1", "alternative_text": "🙂"}) == "🙂"


def test_mathematical_expression_shows_the_expression():
    assert rich_read.rich_text_plain(
        {"type": "mathematical_expression", "expression": "x^2"}) == "x^2"


def test_a_button_shows_its_text():
    assert rich_read.rich_text_plain(
        {"type": "button", "button": {"text": "Open", "url": "https://example.com"}}) == "Open"


def test_an_anchor_shows_nothing():
    assert rich_read.rich_text_plain({"type": "anchor", "name": "top"}) == ""


def test_an_unknown_text_type_with_text_still_shows_it():
    assert rich_read.rich_text_plain({"type": "from_the_future", "text": "kept"}) == "kept"


@pytest.mark.parametrize("value", [
    None, 5, 1.5, True, {"type": "bold"}, {"type": "bold", "text": 7},
    {"type": "custom_emoji", "alternative_text": None},
    {"type": "mathematical_expression", "expression": ["x"]},
    {"type": "button", "button": "Open"}, {},
])
def test_malformed_text_shows_nothing_and_never_raises(value):
    assert rich_read.rich_text_plain(value) == ""


def test_a_very_deep_nesting_is_cut_without_raising():
    value = "deep"
    for _ in range(500):
        value = [value]
    assert rich_read.rich_text_plain(value) == ""


# ---- rich_message_plain: every RichBlock shape --------------------------------

def _plain(*blocks):
    return rich_read.rich_message_plain({"blocks": list(blocks)})


def test_paragraphs_become_lines():
    assert _plain(_para("one"), _para("two ", {"type": "bold", "text": "three"})) == "one\ntwo three"


def test_a_heading_footer_and_thinking_block_show_their_text():
    assert _plain({"type": "heading", "text": "Title", "size": 2},
                  {"type": "footer", "text": "foot"},
                  {"type": "thinking", "text": "hmm"}) == "Title\nfoot\nhmm"


def test_code_keeps_its_lines():
    assert _plain({"type": "pre", "text": "a = 1\n  b = 2", "language": "python"}) == "a = 1\n  b = 2"


def test_a_divider_is_a_rule_and_an_anchor_is_nothing():
    assert _plain(_para("a"), {"type": "divider"}, {"type": "anchor", "name": "x"},
                  _para("b")) == "a\n---\nb"


def test_a_math_block_shows_the_expression():
    assert _plain({"type": "mathematical_expression", "expression": "e=mc^2"}) == "e=mc^2"


def test_a_list_shows_labels_and_nested_items():
    block = {"type": "list", "items": [
        {"label": "•", "blocks": [_para("first")]},
        {"label": "•", "blocks": [_para("second"), {"type": "list", "items": [
            {"label": "1.", "blocks": [_para("inner")]}]}]},
    ]}
    assert _plain(block) == "• first\n• second\n  1. inner"


def test_a_checklist_shows_its_boxes():
    block = {"type": "list", "items": [
        {"label": "", "has_checkbox": True, "is_checked": True, "blocks": [_para("done")]},
        {"label": "", "has_checkbox": True, "blocks": [_para("todo")]},
    ]}
    assert _plain(block) == "[x] done\n[ ] todo"


def test_a_table_shows_rows_with_cells_split_and_its_caption():
    block = {"type": "table", "caption": "Totals", "cells": [
        [{"text": "Name", "is_header": True, "align": "left", "valign": "top"},
         {"text": "Count", "is_header": True, "align": "left", "valign": "top"}],
        [{"text": "apples", "align": "left", "valign": "top"},
         {"text": ["4", {"type": "bold", "text": "2"}], "align": "left", "valign": "top"}],
        [{"align": "left", "valign": "top"}],
    ]}
    assert _plain(block) == "Name | Count\napples | 42\nTotals"


def test_quotes_show_with_a_marker_and_their_credit():
    assert _plain({"type": "blockquote", "blocks": [_para("said")], "credit": "someone"},
                  {"type": "expandable_blockquote", "text": "long\nstory"},
                  {"type": "pullquote", "text": "pulled"}) == (
        "> said\n> someone\n> long\n> story\n> pulled")


def test_details_show_the_summary_and_the_content():
    assert _plain({"type": "details", "summary": "More", "blocks": [_para("inside")]}) == "More\ninside"


@pytest.mark.parametrize("kind,field", [
    ("photo", "photo"), ("video", "video"), ("animation", "animation"),
    ("audio", "audio"), ("document", "document"), ("voice_note", "voice_note"),
    ("map", "location"),
])
def test_a_media_block_shows_only_its_caption(kind, field):
    block = {"type": kind, field: {"file_id": "x"},
             "caption": {"text": "the caption", "credit": "by me"}}
    assert _plain(block) == "the caption - by me"
    assert _plain({"type": kind, field: {"file_id": "x"}}) == ""


def test_collage_and_slideshow_show_their_inner_blocks_and_caption():
    assert _plain({"type": "collage", "blocks": [{"type": "photo", "photo": [],
                                                   "caption": {"text": "p1"}}],
                   "caption": {"text": "all"}},
                  {"type": "slideshow", "blocks": [_para("slide")]}) == "p1\nall\nslide"


def test_a_button_row_shows_nothing():
    assert _plain({"type": "buttons", "buttons": [{"text": "Go", "url": "https://example.com"}]}) == ""


def test_an_unknown_block_shows_its_text_and_inner_blocks():
    assert _plain({"type": "from_the_future", "text": "top",
                   "blocks": [_para("inner")]}) == "top\ninner"


@pytest.mark.parametrize("rich", [
    None, "text", [], 5, {}, {"blocks": None}, {"blocks": {"type": "paragraph", "text": "x"}},
    {"blocks": ["x", 5, None]}, {"blocks": [{"type": "list", "items": {"label": "x"}}]},
    {"blocks": [{"type": "list", "items": ["x", {"blocks": "y"}]}]},
    {"blocks": [{"type": "table", "cells": {"text": "x"}}]},
    {"blocks": [{"type": "table", "cells": ["row", [5, None]]}]},
    {"blocks": [{"type": "blockquote", "blocks": "x"}]},
])
def test_malformed_messages_show_nothing_and_never_raise(rich):
    assert rich_read.rich_message_plain(rich) == ""


def test_a_very_deep_block_nesting_is_cut():
    block = {"type": "divider"}      # no rich text inside: only the block cap applies
    for _ in range(30):
        block = {"type": "blockquote", "blocks": [block]}
    assert rich_read.rich_message_plain({"blocks": [block]}) == ""


def test_a_shallow_quote_nesting_is_read():
    block = _para("deep")
    for _ in range(3):
        block = {"type": "blockquote", "blocks": [block]}
    assert rich_read.rich_message_plain({"blocks": [block]}) == "> > > deep"


def test_an_error_inside_the_reader_gives_nothing(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("a shape nobody planned for")
    monkeypatch.setattr(rich_read, "_blocks_lines", boom)
    monkeypatch.setattr(rich_read, "rich_text_plain", boom)
    assert rich_read.rich_message_plain({"blocks": [_para("x")]}) == ""
    assert rich_read.quote_text(_update(_answer([_para("x")]), quote={
        "text": "", "position": 0, "rich_texts": ["x"]}).message.quote) == ""


def test_the_first_rich_message_logs_its_shape_once_without_content(monkeypatch, caplog):
    monkeypatch.setattr(rich_read, "_shape_logged", False)
    caplog.set_level("DEBUG", logger="aipager.bot.rich_read")
    rich_read.rich_message_plain({"blocks": [_para("secret words"), {"type": "pre", "text": "x"}]})
    rich_read.rich_message_plain({"blocks": [_para("more")]})
    lines = [r.getMessage() for r in caplog.records if r.name == "aipager.bot.rich_read"]
    assert len(lines) == 1
    assert "paragraph" in lines[0] and "pre" in lines[0]
    assert "secret" not in lines[0]


def test_the_first_rich_highlight_logs_its_type_tree_once_without_content(monkeypatch, caplog):
    monkeypatch.setattr(rich_read, "_quote_shape_logged", False)
    caplog.set_level("DEBUG", logger="aipager.bot.rich_read")
    quote = {"text": "", "position": 0,
             "rich_texts": ["secret ", [{"type": "bold", "text": "words"}, "x"]]}
    assert rich_read.quote_text(_update(_answer([_para("x")]), quote=quote).message.quote) \
        == "secret \nwordsx"
    rich_read.quote_text(_update(_answer([_para("x")]), quote=quote).message.quote)
    lines = [r.getMessage() for r in caplog.records if r.name == "aipager.bot.rich_read"]
    assert lines == ["highlight from a rich message: rich_texts shape ['s', ['bold', 's']]"]


# ---- message_text / quote_text ----------------------------------------------

def test_message_text_prefers_text_then_caption_then_rich():
    reply = _answer([_para("rich words")])
    reply["text"] = "plain words"
    assert rich_read.message_text(_update(reply).message.reply_to_message) == "plain words"
    reply = _answer([_para("rich words")])
    assert rich_read.message_text(_update(reply).message.reply_to_message) == "rich words"


def test_message_text_of_a_photo_is_empty():
    photo = {"message_id": 300, "date": 1, "chat": {"id": CHAT, "type": "private"},
             "photo": [{"file_id": "a", "file_unique_id": "b", "width": 1, "height": 1}]}
    assert rich_read.message_text(_update(photo).message.reply_to_message) == ""


def test_quote_text_prefers_text_then_rich_texts():
    msg = _update(_answer([_para("x")]), quote={"text": "typed", "position": 0,
                                                "rich_texts": ["rich"]}).message
    assert rich_read.quote_text(msg.quote) == "typed"
    msg = _update(_answer([_para("x")]), quote={"text": "", "position": 0,
                                                "rich_texts": [["ri", {"type": "bold", "text": "ch"}]]}).message
    assert rich_read.quote_text(msg.quote) == "rich"


# The shape seen live on 2026-11-12 (type tree [['bold','s'], 'bold', 's',
# 's', 's', ['bold','s']]): a bold-led paragraph, a bold-only paragraph,
# three list items and another bold-led paragraph, one item per block.
_LIVE_RICH_TEXTS = [
    [{"type": "bold", "text": "What changed:"}, " replies work."],
    {"type": "bold", "text": "Checks:"},
    "The suite passed.",
    "Each guard was broken on purpose.",
    "The reviewer approved.",
    [{"type": "bold", "text": "One open question:"}, " a highlight across"],
]


def test_a_highlight_across_blocks_gives_one_line_per_block():
    msg = _update(_answer([_para("x")]), quote={"text": "", "position": 0,
                                                "rich_texts": _LIVE_RICH_TEXTS}).message
    assert rich_read.quote_text(msg.quote).split("\n") == [
        "What changed: replies work.",
        "Checks:",
        "The suite passed.",
        "Each guard was broken on purpose.",
        "The reviewer approved.",
        "One open question: a highlight across",
    ]


@pytest.mark.parametrize("rich_texts, want", [
    pytest.param([["plain ", {"type": "bold", "text": "bold"}, " plain"]],
                 "plain bold plain", id="bold-inside-one-paragraph"),
    pytest.param([[{"type": "bold", "text": "Lead:"}, " rest"]],
                 "Lead: rest", id="live-single-item"),
    pytest.param(["just words"], "just words", id="lone-string"),
    pytest.param("not a list", "not a list", id="bare-string"),
    pytest.param({"type": "bold", "text": "one"}, "one", id="bare-object"),
    pytest.param([], "", id="empty"),
])
def test_a_highlight_inside_one_block_has_no_line_break(rich_texts, want):
    msg = _update(_answer([_para("x")]), quote={"text": "", "position": 0,
                                                "rich_texts": rich_texts}).message
    assert rich_read.quote_text(msg.quote) == want


def test_a_newer_ptb_attribute_is_read_too():
    class Quote:
        text = ""
        rich_texts = ["from the attribute"]
        api_kwargs = {}
    assert rich_read.quote_text(Quote()) == "from the attribute"


# ---- the reply context Claude reads ------------------------------------------

def test_a_reply_to_an_older_rich_answer_shows_its_text(mk_bot):
    bot = mk_bot()
    msg = _update(_answer([_para("The build failed on step three.")])).message
    ctx = bot._build_reply_context(msg, _sess(), bot_id=BOT_ID, allow_file=False)
    assert "The build failed on step three." in ctx
    assert "non-text" not in ctx
    assert "by you" in ctx


def test_a_long_rich_answer_is_cut_to_the_excerpt(mk_bot):
    bot = mk_bot()
    msg = _update(_answer([_para("a" * 150)])).message
    ctx = bot._build_reply_context(msg, _sess(), bot_id=BOT_ID, allow_file=False)
    assert ("a" * 80 + "…") in ctx
    assert ("a" * 81) not in ctx


def test_a_reply_to_a_rich_answer_writes_its_full_text_file(mk_bot, tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "reply_context_path", lambda n: tmp_path / f"{n}.txt")
    bot = mk_bot()
    body = [_para("x" * 120), {"type": "pre", "text": "code line"}]
    msg = _update(_answer(body)).message
    ctx = bot._build_reply_context(msg, _sess(), bot_id=BOT_ID, allow_file=True)
    p = tmp_path / "claude-jim.txt"
    assert p.exists()
    assert "code line" in p.read_text()
    assert "claude-jim.txt" in ctx


def test_a_highlight_from_a_rich_answer_shows_the_highlight(mk_bot):
    bot = mk_bot()
    quote = {"text": "", "position": 4, "is_manual": True,
             "rich_texts": [["step ", {"type": "bold", "text": "three"}]]}
    msg = _update(_answer([_para("The step three failed.")]), quote=quote).message
    ctx = bot._build_reply_context(msg, _sess(), bot_id=BOT_ID, allow_file=False)
    assert "step three" in ctx
    assert "highlighting" in ctx
    assert "non-text" not in ctx


def test_a_highlight_from_the_latest_rich_answer_still_shows(mk_bot):
    bot = mk_bot()
    quote = {"text": "", "position": 0, "rich_texts": ["only this"]}
    msg = _update(_answer([_para("only this and more")], message_id=300), quote=quote).message
    ctx = bot._build_reply_context(msg, _sess(last_msg_id=300), bot_id=BOT_ID, allow_file=False)
    assert "only this" in ctx
    assert "quoting" in ctx


def test_a_reply_to_the_latest_rich_answer_still_adds_nothing(mk_bot):
    bot = mk_bot()
    msg = _update(_answer([_para("latest")], message_id=300)).message
    assert bot._build_reply_context(msg, _sess(last_msg_id=300), bot_id=BOT_ID,
                                    allow_file=False) == ""


def test_a_reply_to_a_photo_still_says_non_text(mk_bot):
    bot = mk_bot()
    photo = {"message_id": 300, "date": 1700000000, "chat": {"id": CHAT, "type": "private"},
             "photo": [{"file_id": "a", "file_unique_id": "b", "width": 1, "height": 1}]}
    ctx = bot._build_reply_context(_update(photo).message, _sess(), bot_id=BOT_ID,
                                   allow_file=False)
    assert "non-text message" in ctx


def test_a_reply_to_an_empty_rich_message_still_says_non_text(mk_bot):
    bot = mk_bot()
    msg = _update(_answer([])).message
    ctx = bot._build_reply_context(msg, _sess(), bot_id=BOT_ID, allow_file=False)
    assert "non-text message" in ctx


def test_reply_routing_finds_the_session_from_a_rich_answers_text(mk_bot):
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    sess.scope_chat_id = CHAT
    bot.registry._sessions["claude-jim"] = sess
    reply = _answer([_para("✅ jim · Finished")], message_id=99999)
    assert bot._resolve_reply_target(_update(reply).message.reply_to_message, CHAT) is sess


def test_a_code_block_keeps_its_first_line_indent():
    assert _plain({"type": "pre", "text": "    indented\nx"}) == "    indented\nx"


def test_message_text_never_raises_from_a_strange_object():
    class Strange:
        @property
        def text(self):
            raise ValueError("no")
    assert rich_read.message_text(Strange()) == ""


def test_a_rich_highlight_from_another_chat_shows_as_an_outside_quote(mk_bot):
    bot = mk_bot()
    quote = {"text": "", "position": 0, "rich_texts": ["from elsewhere"]}
    msg = _update(quote=quote).message
    ctx = bot._build_reply_context(msg, _sess(), bot_id=BOT_ID, allow_file=False)
    assert "from elsewhere" in ctx
