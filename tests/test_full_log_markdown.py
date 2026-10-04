"""Long answers and the full log go out as Markdown files (delivery 15).

The operator reads these attachments on a phone, where Telegram shows a
``.md`` file itself and hands a ``.txt`` to another app. That only helps
if the file really is Markdown: ``build_full_log``'s rows must stay
separate list items (renamed as is, the old ``[v] ...`` lines collapsed
into one paragraph), and a tool summary must show literally rather than
render a ``*`` or ``#`` in a command as formatting. The shape is checked
with a real CommonMark parser (markdown-it-py, which ``rich`` already
depends on), not only by string matching.
"""

from __future__ import annotations

import mimetypes
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from aipager.bot.animation import build_full_log
from aipager.bot.transport import document_upload
from aipager.state import Status, TrackedSession

markdown_it = pytest.importorskip("markdown_it")

DONE, FAILED, PENDING = "✅", "❌", "⏳"


def _tokens(md: str) -> list:
    return markdown_it.MarkdownIt("commonmark").parse(md)


def _list_items(md: str) -> list[list]:
    """The inline children of every top-level list item, in order."""
    items: list[list] = []
    toks = _tokens(md)
    for i, tok in enumerate(toks):
        if tok.type == "list_item_open" and tok.level == 1:
            # list_item_open, paragraph_open, inline
            inline = toks[i + 2]
            assert inline.type == "inline"
            items.append(inline.children)
    return items


def _code(children: list) -> list[str]:
    return [c.content for c in children if c.type == "code_inline"]


# ---- tool rows ---------------------------------------------------------------

def test_each_row_is_its_own_list_item_and_never_joins_the_next():
    rows = [("Bash: ls", True), ("Read: /a.py", True),
            ("Bash: pytest", "failed"), ("Grep: foo", False)]
    log = build_full_log("jim", rows, [], "")
    items = _list_items(log)
    assert len(items) == 4
    assert [_code(c) for c in items] == [
        ["Bash: ls"], ["Read: /a.py"], ["Bash: pytest"], ["Grep: foo"],
    ]
    # No softbreak inside an item: a row never runs on into the next.
    for children in items:
        assert not [c for c in children if c.type in ("softbreak", "hardbreak")]
    assert "\n- ✅ `Bash: ls`\n- ✅ `Read: /a.py`\n" in log
    assert "\n- ❌ `Bash: pytest`\n- ⏳ `Grep: foo`" in log


@pytest.mark.parametrize("summary", [
    "Bash: ls *.py | grep _test_ && echo **bold**",
    "Bash: echo `date` and ``x``",
    "`starts with a backtick",
    "ends with a backtick`",
    "Write: # Heading-looking <b>html</b> <script>",
    "Bash: a_b_c *x* [link](http://e.x) ![i](y)",
    " leading space only",
    "trailing space only ",
    "```",
    "Bash: \\*escaped\\* &amp;",
])
def test_a_summary_renders_literally(summary):
    log = build_full_log("jim", [(summary, True)], [], "")
    (children,) = _list_items(log)
    assert _code(children) == [summary]
    rendered = {c.type for c in children}
    assert rendered <= {"text", "code_inline"}, rendered
    # nothing outside the code span except the mark
    text = "".join(c.content for c in children if c.type == "text")
    assert text == f"{DONE} "


def test_a_line_break_in_a_summary_cannot_break_out_of_the_row():
    log = build_full_log("jim", [("Bash: one\n# two\n- three", True),
                                  ("Bash: next", True)], [], "")
    items = _list_items(log)
    assert [_code(c) for c in items] == [["Bash: one # two - three"],
                                         ["Bash: next"]]
    assert not [t for t in _tokens(log)
                if t.type == "heading_open" and t.tag != "h1"]


@pytest.mark.parametrize("brk", ["\r", "\r\n"])
def test_a_carriage_return_in_a_summary_cannot_break_out_of_the_row(brk):
    log = build_full_log("jim", [(f"Bash: one{brk}# two{brk}- three", True),
                                  ("Bash: next", True)], [], "")
    assert [_code(c) for c in _list_items(log)] == [["Bash: one # two - three"],
                                                    ["Bash: next"]]
    assert "\r" not in log


@pytest.mark.parametrize("brk", ["\r", "\r\n"])
def test_a_carriage_return_in_commentary_stays_inside_the_quote(brk):
    note = f"line{brk}# not a heading{brk}- not a list"
    log = build_full_log("jim", [("Bash: a", True), ("Bash: b", True)],
                         [(1, note)], "")
    assert ("\n\n> line\n> # not a heading\n> - not a list\n\n"
            "- \u2705 `Bash: b`") in log
    toks = _tokens(log)
    assert not [t for t in toks if t.type == "heading_open" and t.level == 0
                and t.tag != "h1"]
    assert [_code(c) for c in _list_items(log)] == [["Bash: a"], ["Bash: b"]]


def test_an_empty_summary_still_makes_a_row_with_no_stray_backticks():
    # A bare "``" is not a code span: it would show as two backticks.
    log = build_full_log("jim", [("", True), ("Bash: x", True)], [], "")
    items = _list_items(log)
    assert len(items) == 2
    assert [c.type for c in items[0]] == ["text", "code_inline"]
    assert "`" not in "".join(c.content for c in items[0])


# ---- commentary ----------------------------------------------------------------

def test_multi_line_commentary_is_quoted_on_every_line():
    note = "First line.\nSecond line with *stars*.\n\nAfter a blank line."
    log = build_full_log("jim", [("Bash: a", True), ("Bash: b", True)],
                         [(1, note)], "")
    assert ("\n- ✅ `Bash: a`\n\n"
            "> First line.\n> Second line with *stars*.\n> \n"
            "> After a blank line.\n\n"
            "- ✅ `Bash: b`") in log
    toks = _tokens(log)
    quotes = [t for t in toks if t.type == "blockquote_open"]
    assert len(quotes) == 1
    assert quotes[0].level == 0  # its own block, not inside a list item
    # both paragraphs of the note are inside the one quote
    i = toks.index(quotes[0])
    j = next(k for k in range(i, len(toks)) if toks[k].type == "blockquote_close")
    inner = [t.content for t in toks[i:j] if t.type == "inline"]
    assert inner == ["First line.\nSecond line with *stars*.",
                     "After a blank line."]
    assert len(_list_items(log)) == 2


def test_commentary_before_the_first_row_and_after_the_last():
    log = build_full_log("jim", [("Bash: a", True)],
                         [(0, "opening"), (5, "closing\nthoughts")], "")
    assert "_\n\n> opening\n\n- ✅ `Bash: a`\n\n> closing\n> thoughts\n" in log
    quotes = [t for t in _tokens(log) if t.type == "blockquote_open"]
    assert [q.level for q in quotes] == [0, 0]


# ---- header, agents, final answer ----------------------------------------------

def test_header_is_a_heading_and_the_memory_note_is_italic():
    log = build_full_log("x1", [("Bash: a", True)], [], "")
    assert log.startswith("# x1 - full log\n\n_Memory holds the most recent 1 "
                          "tool rows; older rows of very long turns may already "
                          "be gone._\n\n")
    toks = _tokens(log)
    assert toks[0].type == "heading_open" and toks[0].tag == "h1"
    assert toks[1].content == "x1 - full log"
    note = toks[4].children
    assert [c.type for c in note] == ["em_open", "text", "em_close"]
    assert "—" not in log


def test_agents_then_the_final_answer_verbatim_at_the_end():
    answer = "Done.\n\n- [ ] item with `code`\n\n```py\nprint('x')\n```"
    agents = [{"type": "Explore", "elapsed": 75.0, "tool_count": 2,
               "tools": ["Grep: # TODO", "Read: a_b_c.py"]}]
    log = build_full_log("jim", [("Bash: a", True)], [(1, "note")], answer,
                         agents=agents)
    assert log.endswith(
        "> note\n\n"
        "## Agents\n\n"
        "- \U0001f916 `Explore` (1m 15s, 2 tool calls)\n"
        "  - `Grep: # TODO`\n"
        "  - `Read: a_b_c.py`\n\n"
        "## Final answer\n\n" + answer
    )
    heads = [(t.tag, _tokens(log)[i + 1].content)
             for i, t in enumerate(_tokens(log)) if t.type == "heading_open"]
    assert heads == [("h1", "jim - full log"), ("h2", "Agents"),
                     ("h2", "Final answer")]


def test_final_answer_follows_the_last_row_after_a_blank_line():
    log = build_full_log("jim", [("Bash: a", True)], [], "the answer")
    assert log.endswith("- ✅ `Bash: a`\n\n## Final answer\n\nthe answer")
    assert len(_list_items(log)) == 1


def test_agents_follow_the_last_row_after_a_blank_line():
    log = build_full_log("jim", [("Bash: a", True)], [], "",
                         agents=[{"type": "t", "elapsed": 1.0,
                                  "tool_count": 0, "tools": []}])
    assert log.endswith("- \u2705 `Bash: a`\n\n## Agents\n\n"
                        "- \U0001f916 `t` (1s, 0 tool calls)")


def test_an_empty_tool_history_still_works():
    assert build_full_log("jim", [], [], "ans") == (
        "# jim - full log\n\n"
        "_Memory holds the most recent 0 tool rows; older rows of very long "
        "turns may already be gone._\n\n"
        "## Final answer\n\nans"
    )
    bare = build_full_log("jim", [], [], "")
    assert bare.startswith("# jim - full log\n\n_Memory holds")
    assert "## Final answer" not in bare
    assert build_full_log("jim", [], [(0, "only a note")], "").endswith(
        "gone._\n\n> only a note\n")


def test_the_builder_is_pure():
    rows = [("Bash: a", True)]
    notes = [(0, "n")]
    agents = [{"type": "t", "elapsed": 1.0, "tool_count": 0, "tools": []}]
    first = build_full_log("jim", rows, notes, "ans", agents=agents)
    assert build_full_log("jim", rows, notes, "ans", agents=agents) == first
    assert rows == [("Bash: a", True)] and notes == [(0, "n")]
    assert agents == [{"type": "t", "elapsed": 1.0, "tool_count": 0, "tools": []}]


# ---- the upload carries text/markdown -------------------------------------------

def test_an_md_upload_is_text_markdown_even_when_the_host_does_not_know_md(
    monkeypatch,
):
    """Python 3.12's built-in table has no ``.md``; only a host
    ``/etc/mime.types`` teaches it. Simulate a host without one."""
    monkeypatch.setattr(mimetypes, "guess_type",
                        lambda *a, **k: (None, None))
    upload = document_upload(b"# hi", "jim_full_log.md")
    assert upload.mimetype == "text/markdown"
    assert upload.filename == "jim_full_log.md"
    assert upload.input_file_content == b"# hi"


def test_a_non_md_upload_keeps_ptbs_own_guess(monkeypatch):
    monkeypatch.setattr(mimetypes, "guess_type",
                        lambda *a, **k: ("text/x-guessed", None))
    assert document_upload(b"x", "a.diff").mimetype == "text/x-guessed"
    assert document_upload(b"x", "notes.md.bak").mimetype == "text/x-guessed"
    assert document_upload(b"x", "LOG.MD").mimetype == "text/markdown"


# ---- the three sends' file names ----------------------------------------------

def test_long_idle_answer_sends_full_log_md_and_the_observer_copy_md(
    mk_bot, run_async, monkeypatch,
):
    monkeypatch.setattr("aipager.bot.notify.send_rich_message",
                        AsyncMock(return_value={}))
    bot = mk_bot()
    sess = TrackedSession(name="claude-jim", label="jim", status=Status.IDLE)
    sess.busy_started_at = time.monotonic()
    bot._app.bot.send_message = AsyncMock(return_value=MagicMock(message_id=99))
    bot._app.bot.send_document = AsyncMock()
    bot._maybe_update_bot_name = AsyncMock()
    bot.observers = MagicMock()
    bot.observers.broadcast_document = AsyncMock()
    bot.observers.broadcast = AsyncMock()

    long_md = "x" * 34_000
    run_async(bot.notify(sess, "idle_prompt", {"raw_md": long_md}))

    bot._app.bot.send_document.assert_awaited_once()
    kw = bot._app.bot.send_document.await_args.kwargs
    assert kw["filename"] == "jim_full_log.md"
    assert kw["document"].filename == "jim_full_log.md"
    assert kw["document"].mimetype == "text/markdown"
    body = kw["document"].input_file_content.decode("utf-8")
    assert body.startswith("# jim - full log\n")
    assert body.endswith("## Final answer\n\n" + long_md)

    bot.observers.broadcast_document.assert_called_once()
    _text, doc_bytes, filename = bot.observers.broadcast_document.call_args.args
    assert filename == "jim_response.md"
    assert doc_bytes == kw["document"].input_file_content


def test_observer_document_goes_out_as_markdown(monkeypatch, run_async):
    from aipager.bot.observer import ObserverBroadcaster

    fake = MagicMock()
    fake.send_message = AsyncMock()
    fake.send_document = AsyncMock()
    b = ObserverBroadcaster([])
    b._bots = [(fake, "c1")]
    monkeypatch.setattr(mimetypes, "guess_type", lambda *a, **k: (None, None))
    run_async(b.broadcast_document("summary", b"# log", "jim_response.md"))
    kw = fake.send_document.await_args.kwargs
    assert kw["filename"] == "jim_response.md"
    assert kw["document"].filename == "jim_response.md"
    assert kw["document"].mimetype == "text/markdown"
    assert kw["document"].input_file_content == b"# log"
