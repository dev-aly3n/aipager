"""The golden surfaces rendered in Chinese (Simplified), Korean, Vietnamese
and Indonesian (phase 5, translation run 3), with each language stored
for the chats: the first-contact line, the busy card, a permission prompt
and its answered line, /settings, /language, /status, a few errors and
refusals, and the Mini App page's main view through the JS harness.

Each asserts what the multilingual design asks of a translated surface:
no English word left (every ASCII word the English render of the same
surface shows, but the names that are never translated and the loanwords
a language writes as they are), every placeholder filled, the one plural
form these languages have for every count, Latin digits, no long dash, no
invisible mark, and the length budgets.
"""

from __future__ import annotations

import re

import pytest

from aipager import i18n, preferences
from aipager.bot import agents_line, animation, card_rows, language_menu
from aipager.bot.notify import settled_agent_row
from aipager.i18n import core, locales, plurals
from tests import i18n_surfaces, i18n_tool
from tests.test_miniapp_page_i18n import (  # noqa: F401 (node_bin is a fixture)
    RAW_KEY,
    _after_boot,
    _leaks,
    _plain_english,
    _opts,
    drive,
    node_bin,
)

tool = i18n_tool.load()

CODES = ("zh-Hans", "ko", "vi", "id")

#: The letters that show a text is in the language (Indonesian has none of
#: its own: a few of its commonest words stand in).
SCRIPT = {
    "zh-Hans": re.compile("[\u4e00-\u9fff]"),
    "ko": re.compile("[\uac00-\ud7a3]"),
    "vi": re.compile("[ăâđêôơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹĂÂĐ"
                     "ÊÔƠƯ]"),
}

#: The golden surfaces of the registry (tests/i18n_surfaces.py).
GOLDEN = ("card.busy", "card.thinking", "prompt.inline", "prompt.separate",
          "answer.headers", "settings.root", "language.list", "status.list",
          "auth.refusals", "route.no_session", "route.send_failures", "sess.end_confirm",
          "notice.not_taken", "modecard.cmd_replies")

#: Words a language writes as they are, so the English render's word is
#: not a leak there. Each is the language's own usage (a loanword) or a
#: decided term of its glossary or style guide (keep_english, the English
#: keyboard words, Auto).
LOANWORDS = {
    "zh-Hans": {"shell", "token", "commit", "diff", "log", "ctx", "hook"},
    "ko": {"token", "commit", "diff", "log", "ctx", "hook"},
    "vi": {"shell", "token", "commit", "diff", "log", "ctx", "prompt", "hook", "auto", "chat",
           "bot", "menu", "terminal", "daemon", "tunnel", "repo", "album"},
    "id": {"shell", "token", "commit", "diff", "log", "ctx", "prompt", "hook", "auto", "chat",
           "bot", "menu", "terminal", "daemon", "tunnel", "repo", "album", "admin", "model",
           "folder", "file", "status", "mode", "normal", "server", "beta", "manual", "per",
           "operator", "ask"},
}

#: Names on these surfaces that are never translated: every language's own
#: name (the /language list), and the words typed after /mode (stored and
#: typed values stay English, decision 1.2).
NAMES = tuple(loc.endonym for loc in locales.LOCALES.values()) + ("ask", "auto")

#: Page strings whose English is also the language's text: names, and the
#: words these languages keep (decided in their style guides).
PAGE_NAMES = {
    "zh-Hans": {"Mini App"},
    "ko": {"Mini App"},
    "vi": {"Mini App", "Auto"},
    "id": {"Mini App", "Auto", "Model", "Normal", "Chat"},
}

_NOT_TEXT = re.compile(r"<code\b[^>]*>.*?</code>|<pre\b[^>]*>.*?</pre>|<[^>]+>|&\w+;"
                       r"|(?<!\w)/[\w./*-]+|@\w+|`[^`]*`|https?://\S+|\S+\.(?:py|json|md)\b",
                       re.S)
_HOLE = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}")
_MARKS = ("\u200f", "\u2068", "\u2069")
_FOREIGN_DIGITS = re.compile("[\u06f0-\u06f9\u0660-\u0669]")


def _own_words(code: str) -> re.Pattern:
    """The words of *code*'s catalog that no English text of aipager uses:
    a Latin-script language with no letters of its own reads as itself
    when a text holds one of them."""
    english = set()
    for row in tool.load_jsonl(tool.SOURCE):
        for form in tool.english_forms(row):
            english |= {w.lower() for w in re.findall(r"[^\W\d_]+", form)}
    own = set()
    for value in core._catalog(code).values():
        for form in (value.values() if isinstance(value, dict) else [value]):
            own |= {w for w in re.findall(r"[^\W\d_]+", _HOLE.sub(" ", form))
                    if len(w) >= 4 and w.lower() not in english}
    return re.compile(r"\b(?:" + "|".join(sorted(map(re.escape, own))) + r")\b")


SCRIPT["id"] = _own_words("id")


def _words(text: str, known) -> set[str]:
    """The ASCII words of *text*, lower case, outside code, tags, commands,
    mentions and the names in *known*."""
    rest = _NOT_TEXT.sub(" ", text)
    for word in sorted(set(known) | set(core.NEVER_TRANSLATE), key=len, reverse=True):
        rest = re.sub(rf"(?<![\w-]){re.escape(word)}(?![\w-])", " ", rest)
    return {w.lower() for w in re.findall(r"[^\W\d_]+", rest)
            if w.isascii() and len(w) >= 3}


#: The reply keyboard's bare words of each language (Indonesian keeps the
#: English ones: they are also what a person types). Exempt only as the
#: whole text of a button, never inside a sentence.
KEYBOARD_WORDS = {code: {core._catalog(code)[f"kb.{w}"] for w in ("status", "stop", "new", "kill")}
                  for code in CODES}


def _renders(mk_bot, run_async, monkeypatch, code):
    return i18n_surfaces.render_all(mk_bot, run_async, monkeypatch, code, names=set(GOLDEN))


@pytest.mark.parametrize("code", CODES)
def test_the_language_is_offered_as_beta(code):
    assert locales.is_selectable(code)
    assert locales.LOCALES[code].quality == "beta"
    assert locales.LOCALES[code].digits == "latn" and not locales.LOCALES[code].rtl
    assert plurals.categories(code) == ("other",)
    assert locales.AUTO_DETECT is True    # phase 5: detection on


@pytest.mark.parametrize("code", CODES)
def test_the_golden_surfaces_leave_no_english(mk_bot, run_async, monkeypatch, code):
    english = _renders(mk_bot, run_async, monkeypatch, "en")
    renders = _renders(mk_bot, run_async, monkeypatch, code)
    assert set(renders) == set(GOLDEN)
    leaks = {}
    for name in GOLDEN:
        known = i18n_surfaces.KNOWN + i18n_surfaces.KNOWN_EXTRA.get(name, ()) + NAMES
        english_words = set().union(*(_words(t, known) for t in english[name]))
        assert english_words, name                     # the check has teeth
        for text in renders[name]:
            if text in KEYBOARD_WORDS[code]:
                continue                       # a keyboard word, typed as it is
            left = (_words(text, known) & english_words) - LOANWORDS[code]
            if left:
                leaks.setdefault(name, []).append((text, sorted(left)))
    assert not leaks, leaks


@pytest.mark.parametrize("code", CODES)
def test_the_golden_surfaces_read_in_the_language(mk_bot, run_async, monkeypatch, code):
    renders = _renders(mk_bot, run_async, monkeypatch, code)
    for name, texts in renders.items():
        assert any(SCRIPT[code].search(t) for t in texts), name
        for text in texts:
            assert not _HOLE.search(_NOT_TEXT.sub(" ", text)), (name, text)
            assert "\u2014" not in text, (name, text)
            assert not any(m in text for m in _MARKS), (name, text)
            assert not _FOREIGN_DIGITS.search(text), (name, text)


@pytest.mark.parametrize("code", CODES)
def test_the_first_contact_line(monkeypatch, code):
    tr = i18n.get(code)
    line = language_menu.first_contact_text(tr)
    assert line.startswith("🌐 ")
    assert locales.LOCALES[code].endonym in line and "/language" in line
    assert SCRIPT[code].search(line)
    assert not _HOLE.search(line) and "\u2014" not in line


#: Every count the design names; these languages have no zero form.
COUNTS = (0, 1, 2, 3, 5, 11, 21, 22, 25, 100)


def _form(code: str, key: str, n: int, **params) -> str:
    forms = core._catalog(code)[key]
    assert set(forms) == {"other"}, key
    text = forms[plurals.category(code, n)].replace("{n}", str(n))
    for name, value in params.items():
        text = text.replace("{" + name + "}", value)
    return text


def _working_header(n: int, tr) -> str:
    """The first line of an answer's status block with *n* agents working."""
    view = agents_line.BlockView(agents=(agents_line.AgentRow("a", False),) * n)
    return agents_line.render_block(view, tr).split("\n\n")[0]


@pytest.mark.parametrize("code", CODES)
def test_every_count_reads_the_one_form(mk_bot, run_async, monkeypatch, code):
    """Every count picks the one form these languages have and fills it
    (the expected text is the catalog's own form: this proves the plural
    choice and the filling, not the wording)."""
    from aipager.bot import handlers, held_message
    preferences.set_ui_language(i18n_surfaces.CHAT, code)
    tr = i18n.get(code)
    assert {plurals.category(code, n) for n in COUNTS} == {"other"}
    for n in COUNTS:
        assert animation._tool_fold_summary(n, tr) == _form(code, "card.fold", n)
        row = card_rows.render_md(tr, settled_agent_row("Explore", n, 65))
        assert row == _form(code, "card.row.agent_done", n, agent="`Explore`", clock="1:05")
        assert _working_header(max(n, 1), tr) == _form(code, "agents.head.working", max(n, 1))
        # A late marker counts at least one minute.
        assert held_message.late_marker_text(tr, n * 60) == _form(
            code, "held.late_marker", max(n, 1))
        assert tr.n(handlers.CLEARED, n, kind="text", session="x1") == _form(
            code, "route.cleared", n, session="x1")


@pytest.mark.parametrize("code", CODES)
def test_the_status_title_counts(mk_bot, run_async, monkeypatch, code):
    from aipager.bot import dashboard
    from aipager.state import Status
    monkeypatch.setattr(dashboard, "CHAT_ID", str(i18n_surfaces.CHAT))
    monkeypatch.setattr(dashboard, "get_rate_limiter", lambda: None)
    preferences.set_ui_language(i18n_surfaces.CHAT, code)
    for n in (1, 2, 3, 5, 11):
        bot = mk_bot()
        bot._read_status_file = lambda name: None
        sessions = [i18n_surfaces._sess(name=f"claude-s{i}", label=f"s{i}",
                                        status=Status.IDLE) for i in range(n)]
        i18n_surfaces._registered(bot, *sessions)
        text, _kb = bot._render_status_list(i18n_surfaces.CHAT)
        assert text.startswith(_form(code, "status.sessions", n))


@pytest.mark.parametrize("code", CODES)
def test_every_entry_keeps_its_budget_and_shape(code):
    """The catalog-wide guards of check 3-9, here for the four languages:
    the length budget (cells for buttons, so a CJK character counts 2), no
    long dash, Latin digits, the one plural form."""
    rows = {r["k"]: r for r in tool.load_jsonl(tool.SOURCE)}
    catalog = tool.load_catalog(tool.LOCALES_DIR / f"{code}.jsonl")
    assert set(catalog) == set(rows)
    problems = []
    for key, entry in catalog.items():
        problems += tool.check_translation(code, rows[key], entry["v"])
        for form in (entry["v"].values() if isinstance(entry["v"], dict) else [entry["v"]]):
            problems += tool.scan_text(f"{code} {key}", form)
    assert problems == []


@pytest.mark.parametrize("code", ("zh-Hans", "ko"))
def test_wide_buttons_are_measured_in_cells(code):
    """A CJK character takes two cells: the keyboard words fit the 14-cell
    budget counted that way, not by characters."""
    for key in ("kb.status", "kb.stop", "kb.new", "kb.kill", "kb.templates", "kb.commands",
                "kb.models", "kb.back"):
        label = core._catalog(code)[key]
        assert tool.cells(label) <= 14, (key, label)
        assert tool.cells(label) > len(label) or label.isascii(), (key, label)


@pytest.mark.parametrize("code", CODES)
def test_the_mini_app_main_view(node_bin, tmp_path, code):  # noqa: F811
    from aipager.miniapp.static import INDEX_HTML

    cat = core._catalog(code)
    # The server's fact labels, as it sends them in the viewer's language.
    labels = {fid: cat["app.fact." + fid] for fid in
              ("model", "context", "cost", "working", "last_active", "cwd")}
    run = drive(node_bin, tmp_path, INDEX_HTML, opts=_opts(code, factLabels=labels))
    assert (run["lang"], run["dir"]) == (code, "ltr")
    assert run["islands"]["pct_sign"] == "%"
    assert _leaks(run, code) == []
    texts = [t for _n, t in _after_boot(run)]
    assert any(SCRIPT[code].search(t) for t in texts)
    assert not [t for t in texts if any(m in t for m in _MARKS)]
    assert not [t for t in texts if _FOREIGN_DIGITS.search(t) or RAW_KEY.search(t)]
    assert not [t for t in texts if "\u2014" in t]
    # No page string is left in English, even one the catalog itself
    # holds in English (which ``_leaks`` takes for a name): only the
    # names and the words the language writes as they are.
    english = set(_plain_english().values()) - PAGE_NAMES[code]
    assert sorted({t for t in texts if t in english}) == []
    grid = {s["name"]: s["texts"] for s in run["screens"]}["grid"]
    assert cat["ui.tab.sessions"] in grid and cat["ui.tab.settings"] in grid
    assert cat["ui.age.seconds"].replace("{n}", "30") in grid


@pytest.mark.parametrize("code", CODES)
def test_the_mini_app_counts(node_bin, tmp_path, code):  # noqa: F811
    from aipager.miniapp.static import INDEX_HTML

    counts = [n for n in COUNTS if n]
    run = drive(node_bin, tmp_path, INDEX_HTML, scenario="plurals",
                opts=_opts(code, counts=counts))
    for n in counts:
        got = run["plurals"][str(n)]
        assert got["need"] == _form(code, "ui.pulse.need", n), n
        assert got["queued"] == _form(code, "ui.activity.queued", n), n
        assert got["timeline"] == _form(code, "ui.timeline.title_n", n), n
        assert got["errors"] == _form(code, "ui.report.errors_n", n + 3), n
        assert got["more"] == _form(code, "ui.report.show_more", n), n
        assert got["tooLong"] == _form(code, "ui.report.too_long", n), n


@pytest.mark.parametrize("code", CODES)
def test_the_busy_card_after_a_stop_or_a_refusal(code):
    """The busy card's outcome headers (a Stop, a Denied, in a group with
    who did it) read in the language, with no English word left."""
    from aipager.bot import session_ops
    tr, en = i18n.get(code), i18n.get("en")
    pairs = [(f(tr), f(en)) for f in (
        lambda t: session_ops._stopped_card_text("x1", "", t),
        lambda t: session_ops._stopped_card_text("x1", "@bob", t),
        lambda t: session_ops._refused_card_text("x1", "", tr=t),
        lambda t: session_ops._refused_card_text("x1", "@bob", tr=t))]
    for text, english in pairs:
        assert text != english
        assert SCRIPT[code].search(text), text
        assert "x1" in text and not _HOLE.search(text)
        left = (_words(text, ("x1",)) & _words(english, ("x1",))) - LOANWORDS[code]
        assert not left, (text, left)
    assert "@bob" in pairs[1][0] and "@bob" in pairs[3][0]


@pytest.mark.parametrize("code,tags", [
    ("zh-Hans", ("zh", "zh-CN", "zh-Hans", "zh-SG")), ("ko", ("ko", "ko-KR")),
    ("vi", ("vi", "vi-VN")), ("id", ("id", "in"))])
def test_a_stranger_is_refused_in_their_telegram_language(code, tags):
    """Someone not on the allow-list hears the refusal in their Telegram
    app's language once it is enabled; Traditional Chinese stays English."""
    from types import SimpleNamespace

    from aipager.bot import auth
    for tag in tags:
        user = SimpleNamespace(id=987654, language_code=tag)
        assert auth._stranger_tr(user).code == code, tag
    for tag in ("zh-TW", "zh-Hant", "zh-HK", "ja"):
        user = SimpleNamespace(id=987654, language_code=tag)
        assert auth._stranger_tr(user).code == "en", tag
