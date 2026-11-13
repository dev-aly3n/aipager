"""A ``$`` in the text aipager sends stays a dollar sign.

Telegram's rich Markdown reads ``$...$`` as a LaTeX formula. Seen live
on 2026-11-12: an answer with "$10 of Up and $10 of Down ... +$5.64"
showed "10ofUpand10ofDown,andUpwon.Wallet1made+" as one italic formula.
Claude Code writes for a terminal, where ``$`` is never math, so every
``$`` outside code and URLs goes out escaped as ``\\$``.
"""
from __future__ import annotations

import time

import pytest

from aipager.bot import rich_message as rm
from aipager.bot.dollar_guard import escape_dollars

LIVE = ("The 18:15 market was a win: each wallet bought $10 of Up and $10 of "
        "Down, and Up won. Wallet 1 made +$5.64 and wallet 2 +$6.61.")


def _bare(md: str) -> list[int]:
    """Positions of a ``$`` with an even run of backslashes before it."""
    out = []
    for i, ch in enumerate(md):
        if ch == "$":
            n = 0
            while i - n - 1 >= 0 and md[i - n - 1] == "\\":
                n += 1
            if n % 2 == 0:
                out.append(i)
    return out


# ---- the escaper ------------------------------------------------------------

def test_the_live_answer_has_no_bare_dollar_left():
    out = escape_dollars(LIVE)
    assert out == LIVE.replace("$", "\\$")
    assert _bare(out) == []


@pytest.mark.parametrize("text, want", [
    pytest.param("$$E = mc^2$$", "\\$\\$E = mc^2\\$\\$", id="display-math"),
    pytest.param("$x^2 + y^2$", "\\$x^2 + y^2\\$", id="inline-math"),
    pytest.param("costs $5", "costs \\$5", id="lone"),
    pytest.param("$", "\\$", id="only-a-dollar"),
    pytest.param("echo $HOME and $PATH", "echo \\$HOME and \\$PATH", id="shell-vars"),
    pytest.param("- item $1\n  more $2", "- item \\$1\n  more \\$2", id="list-continuation"),
    pytest.param("    indented $1 and $2", "    indented \\$1 and \\$2", id="indented-line"),
    pytest.param("a\\$b", "a\\$b", id="already-escaped"),
    pytest.param("a\\\\$b", "a\\\\\\$b", id="escaped-backslash-then-bare"),
    pytest.param("a\\\\\\$b", "a\\\\\\$b", id="three-backslashes"),
    pytest.param("", "", id="empty"),
    pytest.param("no dollar here", "no dollar here", id="none"),
])
def test_escape_dollars(text, want):
    assert escape_dollars(text) == want


@pytest.mark.parametrize("text", [
    LIVE, "$$E$$", "a\\$b", "a\\\\$b", "`$x` and $y", "see https://x/$a then $b",
])
def test_escaping_twice_is_escaping_once(text):
    once = escape_dollars(text)
    assert escape_dollars(once) == once


def test_a_text_without_dollar_is_returned_as_is():
    # Two lines: a one-item join hands back the same object, which would
    # pass without the shortcut.
    text = "plain\nwords"
    assert escape_dollars(text) is text


@pytest.mark.parametrize("text, want", [
    pytest.param("run `echo $HOME` for $5", "run `echo $HOME` for \\$5", id="code-span"),
    pytest.param("``a $b``", "``a $b``", id="double-tick-span"),
    pytest.param("```\nprice=$5 + $6\n```\nnow $7",
                 "```\nprice=$5 + $6\n```\nnow \\$7", id="fence"),
    pytest.param("```math\nE = $mc^2$\n```", "```math\nE = $mc^2$\n```", id="math-fence"),
    pytest.param("~~~sh\necho $A\n~~~\n$B", "~~~sh\necho $A\n~~~\n\\$B", id="tilde-fence"),
    pytest.param("```\nnever closed $1", "```\nnever closed $1", id="open-fence"),
    pytest.param("`unclosed $1", "`unclosed \\$1", id="lone-backtick"),
])
def test_code_is_left_alone(text, want):
    assert escape_dollars(text) == want


@pytest.mark.parametrize("text, want", [
    pytest.param("see https://x.test/a?p=$1 for $2",
                 "see https://x.test/a?p=$1 for \\$2", id="bare-url"),
    pytest.param("<https://x.test/$a> costs $3",
                 "<https://x.test/$a> costs \\$3", id="autolink"),
    pytest.param("www.x.test/$a and $4", "www.x.test/$a and \\$4", id="www"),
    pytest.param("[pay $5](https://x.test/$p)", "[pay \\$5](https://x.test/$p)", id="link"),
    pytest.param("ftp://x.test/$f", "ftp://x.test/$f", id="ftp"),
])
def test_urls_gain_no_backslash(text, want):
    assert escape_dollars(text) == want


@pytest.mark.parametrize("text, want", [
    pytest.param("> ```sh\n> echo $HOME $PATH\n> ```\n> costs $1",
                 "> ```sh\n> echo $HOME $PATH\n> ```\n> costs \\$1", id="quoted-fence"),
    pytest.param("1. ```sh\n   echo $A\n   ```\n2. $3",
                 "1. ```sh\n   echo $A\n   ```\n2. \\$3", id="list-fence"),
    pytest.param("- a\n    - b\n        ```\n        x=$1\n        ```\n$2",
                 "- a\n    - b\n        ```\n        x=$1\n        ```\n\\$2", id="deep-fence"),
    pytest.param("> > ```\n> > $x\n> > ```", "> > ```\n> > $x\n> > ```", id="nested-quote-fence"),
])
def test_a_fence_in_a_quote_or_a_list_is_code(text, want):
    assert escape_dollars(text) == want


def test_the_busy_card_quote_keeps_a_quoted_fence_as_code():
    from aipager.bot.animation import _quote
    prose = "Running it:\n```sh\necho $HOME and $PATH\n```\nIt cost $1 and $2."
    out = escape_dollars(_quote(prose))
    assert "> echo $HOME and $PATH" in out
    assert "> It cost \\$1 and \\$2." in out


@pytest.mark.parametrize("text, want", [
    pytest.param("<footer>⏳ <b>dev</b> · Working · 0:07 · $0.25</footer>",
                 "<footer>⏳ <b>dev</b> · Working · 0:07 · $0.25</footer>", id="footer"),
    pytest.param("- <footer>$2</footer>", "- <footer>$2</footer>", id="footer-list-item"),
    pytest.param("> <pre>\n> x=$1\n> </pre>\n$2", "> <pre>\n> x=$1\n> </pre>\n\\$2", id="quoted-pre"),
    pytest.param("<pre>\nx=$1\ny=$2\n</pre>\nnow $3",
                 "<pre>\nx=$1\ny=$2\n</pre>\nnow \\$3", id="pre-block"),
    pytest.param("<pre class=\"x\">a $1</pre> $2", "<pre class=\"x\">a $1</pre> \\$2", id="pre-inline"),
    pytest.param("<pre>\nnever closed $1", "<pre>\nnever closed $1", id="pre-open"),
    pytest.param("<code>echo $A</code> for $5 and <CODE>$B</CODE>",
                 "<code>echo $A</code> for \\$5 and <CODE>$B</CODE>", id="code-tag"),
    pytest.param("<code>no close $1", "<code>no close \\$1", id="code-tag-open"),
    pytest.param("<pre>\n```\n$1\n</pre>\n$2", "<pre>\n```\n$1\n</pre>\n\\$2", id="fence-inside-pre"),
])
def test_raw_html_blocks_are_left_as_written(text, want):
    assert escape_dollars(text) == want


@pytest.mark.parametrize("text", [
    "Move the price into the `<footer>` component.",
    "Move the price into the <footer> component.",
    "Wrap it in `<pre>` or `<PRE>`.",
    "Use <code><pre></code> for it.",
    "Use <code><footer></code> for it.",
    "$1 then <footer>$2</footer> then",
])
def test_a_tag_named_in_a_sentence_opens_no_raw_block(text):
    """Only a tag at the start of a line opens a block: a tag named in
    prose or code must not leave the amounts after it unescaped."""
    out = escape_dollars(text + "\nPro is $19 and Team is $49.")
    assert out.endswith("\nPro is \\$19 and Team is \\$49."), out


@pytest.mark.parametrize("text", [
    "- <footer> holds the links",
    "<footer>\n",
    "> <pre>\n> x",
    "<footer/>",
    "<pre/>",
    "```x``` costs $5 and $6",
])
def test_a_raw_tag_or_tick_run_at_a_line_start_does_not_swallow_the_rest(text):
    out = escape_dollars(text + "\nPro is $19 and Team is $49.")
    assert out.endswith("\nPro is \\$19 and Team is \\$49."), out


def test_a_line_starting_with_a_triple_tick_code_span_is_escaped_outside_it():
    assert escape_dollars("```x``` costs $5") == "```x``` costs \\$5"


@pytest.mark.parametrize("text, want", [
    pytest.param("> ```\n> echo $A\nAfter the quote $1 and $2",
                 "> ```\n> echo $A\nAfter the quote \\$1 and \\$2", id="quoted-fence-ends-with-quote"),
    pytest.param("```md\n> ```\n> $x\n```\nnow $1",
                 "```md\n> ```\n> $x\n```\nnow \\$1", id="quoted-fence-line-inside-a-fence"),
    pytest.param("> > ```\n> > $a\n> still $b\n> > ```",
                 "> > ```\n> > $a\n> still \\$b\n> > ```", id="inner-quote-ends"),
])
def test_a_fence_belongs_to_the_quote_it_opened_in(text, want):
    assert escape_dollars(text) == want


def test_a_dollar_written_right_after_a_url_counts_as_part_of_it():
    """Documented trade: GitHub's autolinks take ",$5" into the address."""
    assert escape_dollars("see https://x.test,$5 or $6") == "see https://x.test,$5 or \\$6"


def test_a_hostile_line_is_escaped_quickly():
    """It runs on the event loop for every rich send and card edit."""
    for text in ("$" * 50000, "\\" * 30000 + "$", "`$" * 20000, "https://" + "$" * 40000,
                 "<a:" + "$" * 40000, "<code>$" * 20000, "<code" + " " * 40000 + "$",
                 "<pre>$\n" * 20000, "<footer>" * 20000 + "$", "> " * 30000 + "```$",
                 "> " * 30000 + "x $", "1. " * 20000 + "$", " \t" * 30000 + "$"):
        t0 = time.perf_counter()
        escape_dollars(text)
        assert time.perf_counter() - t0 < 0.5, text[:20]


# ---- the two payload builders and the plain-text fallback --------------------

def _capture(monkeypatch):
    seen: list[tuple[str, dict]] = []

    async def _fake_post(method, payload, **_kw):
        seen.append((method, payload))
        return {"ok": True, "result": {"message_id": 1}}

    monkeypatch.setattr(rm, "_post", _fake_post)
    return seen


def test_a_rich_send_carries_escaped_dollars(monkeypatch, run_async):
    seen = _capture(monkeypatch)
    run_async(rm.send_rich_message(1, LIVE))
    method, payload = seen[0]
    assert method == "sendRichMessage"
    assert payload["rich_message"]["markdown"] == LIVE.replace("$", "\\$")


def test_a_rich_edit_carries_escaped_dollars(monkeypatch, run_async):
    seen = _capture(monkeypatch)
    run_async(rm.edit_message_text_rich(1, 2, "card: spent $1 of $2"))
    method, payload = seen[0]
    assert method == "editMessageText"
    assert payload["rich_message"]["markdown"] == "card: spent \\$1 of \\$2"


def test_a_busy_card_edit_keeps_its_footer_cost_as_written(monkeypatch, run_async):
    from aipager.bot.footer import footer
    seen = _capture(monkeypatch)
    card = "> Spent $1 on it, $2 left.\n\n---\n" + footer("⏳ **dev** · Working · 0:07 · $0.25")
    run_async(rm.edit_message_text_rich(1, 2, card))
    md = seen[0][1]["rich_message"]["markdown"]
    assert "· $0.25</footer>" in md
    assert "Spent \\$1 on it, \\$2 left." in md


def test_the_plain_text_fallback_keeps_the_dollars_as_written(mk_bot, run_async, monkeypatch):
    from aipager.state import SessionRegistry, Status

    async def _refuse(method, payload, **_kw):
        raise rm.RichMessageFallbackRequired("400")

    monkeypatch.setattr(rm, "_post", _refuse)
    bot = mk_bot()
    reg = SessionRegistry()
    s = reg.get_or_create("claude-x1")
    s.label = "x1"
    s.status = Status.BUSY
    s.scope_chat_id, s.scope_kind = 4343, "dm"
    s.busy_started_at = time.monotonic() - 5
    bot.registry._sessions[s.name] = s
    s.bg_agent_started("a1", "general-purpose", time.monotonic())
    run_async(bot._deliver_job_interim(s, LIVE, time.time()))
    sent = [c.args[1] if len(c.args) > 1 else c.kwargs.get("text")
            for c in bot._app.bot.send_message.await_args_list]
    assert any(LIVE in t for t in sent), sent
    assert not any("\\$" in t for t in sent), sent
