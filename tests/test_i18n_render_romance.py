"""Spanish, Portuguese (Brazil), French and Italian rendered through the
real surfaces (phase 5, run 1).

Every registered surface whose migration has landed (the registry in
``tests/i18n_surfaces.py``, the language stored for its chats) reads in
the language: no English word left on it, every placeholder filled, no
invisible direction mark (these four are left to right), Latin digits, no
long dash. The golden surfaces are named so a registry change cannot drop
them silently. Plural forms are checked through the code that sends them
for the counts that matter in these languages (``many`` is for whole
millions; French and Portuguese put 0 in ``one``), the first-contact line
and the /language list carry the endonyms, and the Mini App page is
driven for real through its JS harness.

"No English left" is a word check like the pseudo sweep's: a word that
the English source uses and the language's own catalog never does is
English that leaked (shared words such as "chat", "status" or "Mini" are
not evidence either way and pass).
"""

from __future__ import annotations

import json
import re
import shutil

import pytest

from aipager import i18n, preferences
from aipager.bot import agents_line, animation, card_rows, kbwords, language_menu
from aipager.bot.notify import settled_agent_row
from aipager.i18n import core, locales, plurals
from tests import i18n_surfaces, i18n_tool

tool = i18n_tool.load()

CODES = ("es", "pt-BR", "fr", "it")
ENDONYM = {"es": "Español", "pt-BR": "Português (Brasil)", "fr": "Français",
           "it": "Italiano"}
COUNTS = (0, 1, 2, 3, 5, 11, 21, 22, 25, 100, 1_000_000)
#: CLDR categories of COUNTS: es/it put 0 in other, fr/pt-BR in one; whole
#: millions are many in all four.
EXPECTED = {
    "es": ["other", "one"] + ["other"] * 8 + ["many"],
    "it": ["other", "one"] + ["other"] * 8 + ["many"],
    "fr": ["one", "one"] + ["other"] * 8 + ["many"],
    "pt-BR": ["one", "one"] + ["other"] * 8 + ["many"],
}
#: Words a catalog value writes exactly as the English does because they
#: are the language's own words too (Italian "beta", French "Sections").
AS_IS = {"es": {"beta"}, "pt-BR": {"beta", "offline"}, "fr": {"sections"},
         "it": {"beta", "offline"}}
#: The fold line at 1 and 2, as each language writes it.
FOLD = {"es": ("▸ 1 llamada a herramienta", "▸ 2 llamadas a herramientas"),
        "pt-BR": ("▸ 1 chamada de ferramenta", "▸ 2 chamadas de ferramenta"),
        "fr": ("▸ 1 appel d'outil", "▸ 2 appels d'outils"),
        "it": ("▸ 1 chiamata a strumento", "▸ 2 chiamate a strumenti")}
#: The golden surfaces of the design (first contact is checked below):
#: the busy card, the permission prompt and its answered line, /settings,
#: /language, /status, errors and refusals.
GOLDEN = ("card.busy", "card.final", "prompt.separate", "prompt.inline", "answer.headers",
          "settings.root", "language.list", "status.list", "auth.refusals",
          "transport.texts", "group.texts", "keyboard.main", "menu.commands")
#: Data the registry feeds in, not text: a shell command of a tool row,
#: the update job's internal step names, the voice extra's install commands.
DATA = ("everything", "waiting_for_idle", "upgrading",
        "uv tool install --reinstall 'aipager[voice]'", "pip install 'aipager[voice]'")
#: Surfaces that read the same as in English: names and symbols only.
SAME_AS_ENGLISH = {"menu.button"}
MARKS = ("\u200e", "\u200f", "\u2066", "\u2067", "\u2068", "\u2069", "\u061c")
FOREIGN_DIGITS = re.compile("[۰-۹٠-٩]")
LEFT_HOLDER = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}")
#: A word, for the leak check: three letters or more (two-letter words
#: such as "do", "on" or "me" are words in several of these languages).
WORD = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]{3,}")


def _words(text: str) -> set[str]:
    return {w.lower() for w in WORD.findall(LEFT_HOLDER.sub(" ", text))}


def _values(value) -> list[str]:
    return list(value.values()) if isinstance(value, dict) else [value]


def english_only(code: str) -> set[str]:
    """Words the English source uses that *code*'s catalog never uses in a
    translation of its own (a value left equal to its English proves
    nothing, so it adds no word), less the words the glossary keeps in
    English (commit, token, diff...)."""
    english: set[str] = set()
    sources = {}
    for row in tool.load_jsonl(tool.SOURCE):
        sources[row["k"]] = tool.english_forms(row)
        for form in sources[row["k"]]:
            english |= _words(form)
    own: set[str] = set()
    for key, value in core._catalog(code).items():
        for form in _values(value):
            if form not in sources.get(key, ()):
                own |= _words(form)
    keep = {w.lower() for w in tool.load_glossary(code)["keep_english"]}
    return english - own - keep - AS_IS[code]


def leaked(text: str, only: set[str], known=()) -> list[str]:
    """English-only words in *text*, after what is never translated
    (code, tags, commands, paths, mentions, numbers, names) is removed."""
    rest = re.sub(r"\b[A-Za-z_][\w.-]*\d[\w.-]*", " ", text)   # an identifier: x1, old2
    rest = re.sub(r"\b\w+_\w+\b", " ", rest)                   # a setting: deny_tools
    rest = i18n_surfaces._NOT_TEXT.sub(" ", rest)
    for word in sorted(set(i18n_surfaces.KNOWN) | set(known) | set(core.NEVER_TRANSLATE),
                       key=len, reverse=True):
        rest = re.sub(rf"(?<![\w-]){re.escape(word)}(?![\w-])", " ", rest)
    return [w for w in WORD.findall(rest) if w.lower() in only]


def _names():
    return {n for n, _f in i18n_surfaces.SURFACES} - i18n_surfaces.PENDING


def _texts(name, texts):
    for text in texts:
        if name == "menu.commands":
            text = text.split(" ", 1)[1]    # "/cmd description": the command is the menu's
        yield text


# ---- every surface ------------------------------------------------------------------

@pytest.mark.parametrize("code", CODES)
def test_every_surface_reads_in_the_language(mk_bot, run_async, monkeypatch, code):
    names = _names()
    assert set(GOLDEN) <= names
    renders = i18n_surfaces.render_all(mk_bot, run_async, monkeypatch, code, names=names)
    english = i18n_surfaces.render_all(mk_bot, run_async, monkeypatch, "en", names=names)
    assert set(renders) == names
    same = {n for n, texts in renders.items() if texts and texts == english[n]}
    assert same <= SAME_AS_ENGLISH, (code, sorted(same - SAME_AS_ENGLISH))
    only = english_only(code)
    leaks = []
    for name, texts in renders.items():
        known = i18n_surfaces.KNOWN_EXTRA.get(name, ()) + DATA
        for text in _texts(name, texts):
            found = leaked(text, only, known)
            if found:
                leaks.append((name, found, text))
            assert not LEFT_HOLDER.search(i18n_surfaces._NOT_TEXT.sub(" ", text)), (
                code, name, text)
            assert not any(m in text for m in MARKS), (code, name, text)
            assert not FOREIGN_DIGITS.search(text), (code, name, text)
            assert "\u2014" not in text, (code, name, text)
    assert leaks == [], (code, leaks)


@pytest.mark.parametrize("code", CODES)
def test_the_word_check_sees_english(code):
    """The leak check is not vacuous: an English sentence of the golden
    surfaces is caught in every language."""
    only = english_only(code)
    assert leaked("Session is still running. Use Kill to stop it first.", only)
    assert leaked("⚠️ <b>x1</b> · Stopped by @bob", only) == ["Stopped"]
    own = core._catalog(code)["card.stopped_by"]
    assert leaked(own.replace("{session}", "x1").replace("{person}", "@bob"), only) == []


# ---- plural forms ---------------------------------------------------------------------

def _form(code: str, key: str, n: int, **params) -> str:
    text = core._catalog(code)[key][plurals.category(code, n)].replace("{n}", str(n))
    for name, value in params.items():
        text = text.replace("{" + name + "}", value)
    return text


def _working_header(n: int, tr) -> str:
    """The first line of an answer's status block with *n* agents working."""
    view = agents_line.BlockView(agents=(agents_line.AgentRow("a", False),) * n)
    return agents_line.render_block(view, tr).split("\n\n")[0]


@pytest.mark.parametrize("code", CODES)
def test_plural_categories(code):
    assert [plurals.category(code, n) for n in COUNTS] == EXPECTED[code]
    for key, value in core._catalog(code).items():
        if isinstance(value, dict):
            assert set(value) == {"one", "many", "other"}, (code, key)


@pytest.mark.parametrize("code", CODES)
def test_plural_forms_through_the_code_that_sends_them(code, monkeypatch):
    tr = i18n.get(code)
    for n in COUNTS:
        assert animation._tool_fold_summary(n, tr) == _form(code, "card.fold", n)
        row = card_rows.render_md(tr, settled_agent_row("Explore", n, 65))
        assert row == _form(code, "card.row.agent_done", n, agent="`Explore`", clock="1:05")
        if n and n <= 30:
            assert _working_header(n, tr) == _form(code, "agents.head.working", n)
    # The forms themselves, written out: a swapped one/other or a plural
    # left in the singular fails here, not only against the catalog.
    one, two = FOLD[code]
    assert animation._tool_fold_summary(1, tr) == one
    assert animation._tool_fold_summary(2, tr) == two
    # One and other really differ (the noun is singular for one).
    for key in ("card.fold", "card.agents", "status.notes", "ui.pulse.need"):
        assert _form(code, key, 1) != _form(code, key, 2).replace("2", "1"), (code, key)
    # French and Portuguese say 0 in the singular; Spanish and Italian in the plural.
    zero = _form(code, "card.fold", 0)
    assert (zero.replace("0", "1") == _form(code, "card.fold", 1)) == (code in ("fr", "pt-BR"))


@pytest.mark.parametrize("code", CODES)
def test_status_and_the_pinned_bar_count_in_the_language(mk_bot, monkeypatch, code):
    from aipager.bot import dashboard
    from aipager.state import Status
    monkeypatch.setattr(dashboard, "CHAT_ID", str(i18n_surfaces.CHAT))
    monkeypatch.setattr(dashboard, "get_rate_limiter", lambda: None)
    preferences.set_ui_language(i18n_surfaces.CHAT, code)
    for n in (1, 2, 5, 21, 22):
        bot = mk_bot()
        bot._read_status_file = lambda name: None
        sessions = [i18n_surfaces._sess(name=f"claude-s{i}", label=f"s{i}",
                                        status=Status.IDLE) for i in range(n)]
        i18n_surfaces._registered(bot, *sessions)
        text, _kb = bot._render_status_list(i18n_surfaces.CHAT)
        assert text.startswith(_form(code, "status.sessions", n)), (code, n)
        if n > 1:
            first = bot._render_pinned(i18n_surfaces.CHAT, sessions)[0].split("\n")[0]
            assert first == "💤 " + _form(code, "pin.count.idle", n), (code, n)


# ---- first contact, the list, the keyboard -------------------------------------------

@pytest.mark.parametrize("code", CODES)
def test_the_first_contact_line(monkeypatch, code):
    line = language_menu.first_contact_text(i18n.get(code))
    want = core._catalog(code)["lang.first_contact"].replace("{language}", ENDONYM[code])
    assert line == want
    assert "/language" in line and "aipager" in line and "Claude" in line
    assert leaked(line, english_only(code)) == []


def test_the_language_list_offers_the_four_as_beta():
    text, kb = language_menu.render_language_menu(i18n.get("en"), private=True, current="auto")
    labels = [b.text for row in kb.inline_keyboard for b in row]
    for code, name in ENDONYM.items():
        assert f"{name} (beta)" in labels
        assert locales.LOCALES[code].enabled and locales.LOCALES[code].quality == "beta"
    assert locales.AUTO_DETECT is True    # phase 5: detection on


@pytest.mark.parametrize("code", CODES)
def test_the_list_marks_beta_in_the_viewers_language(code):
    _text, kb = language_menu.render_language_menu(i18n.get(code), private=True,
                                                   current=code)
    labels = [b.text for row in kb.inline_keyboard for b in row]
    own = core._catalog(code)["lang.beta"].replace("{language}", ENDONYM[code])
    assert own + " ✅" in labels


def test_the_keyboard_labels_pass_check_10():
    assert kbwords.label_problems(("en",) + CODES + ("qps",)) == []
    for code in CODES:
        # The four main-keyboard words stay the commands' own (style guide).
        assert [i18n.get(code).text(m) for m in kbwords.WORDS.values()] == [
            "status", "stop", "kill", "new"]
        for msg in (kbwords.KB_TEMPLATES, kbwords.KB_COMMANDS, kbwords.KB_MODELS):
            assert i18n.get(code).text(msg).endswith(" ›"), (code, msg.key)


# ---- the catalogs as a whole ------------------------------------------------------------

def test_the_catalogs_pass_every_check():
    rows = tool.load_jsonl(tool.SOURCE)
    catalogs = {c: tool.LOCALES_DIR / f"{c}.jsonl" for c in CODES}
    assert tool.check_catalogs(rows, catalogs, CODES, glossaries=tool.all_glossaries()) == []
    for code in CODES:
        assert set(core._catalog(code)) == {r["k"] for r in rows}, code


@pytest.mark.parametrize("code", CODES)
def test_no_long_dash_and_no_exclamation_in_the_catalog(code):
    for key, value in core._catalog(code).items():
        for form in _values(value):
            assert "\u2014" not in form and "!" not in form and "¡" not in form, (code, key)


# ---- the Mini App page, driven for real ------------------------------------------------

@pytest.fixture(scope="module")
def node_bin():
    exe = shutil.which("node") or shutil.which("nodejs")
    if not exe:
        pytest.skip("node not available - the page i18n harness is skipped")
    return exe


@pytest.mark.parametrize("code", CODES)
def test_the_mini_app_page(node_bin, tmp_path, code):
    from aipager.miniapp.static import INDEX_HTML
    from tests import test_miniapp_page_i18n as page

    run = page.drive(node_bin, tmp_path, INDEX_HTML, opts=page._opts(code))
    assert (run["lang"], run["dir"]) == (code, "ltr")
    assert run["islands"]["pct_sign"] == "%"
    assert page._leaks(run, code) == []
    texts = [t for _n, t in page._after_boot(run)]
    assert not [t for t in texts if page.RAW_KEY.search(t)]
    assert not [t for t in texts if any(m in t for m in MARKS) or FOREIGN_DIGITS.search(t)]
    only = english_only(code)
    screens = {s["name"]: s["texts"] for s in run["screens"]}
    cat = core._catalog(code)
    assert cat["ui.tab.sessions"] in screens["grid"]
    assert cat["ui.tab.settings"] in screens["grid"]
    # What the harness's fake server answered is data here (the real server
    # sends it in the viewer's language through its own keys, tested in
    # tests/test_miniapp_server_language.py); the page's own words are not.
    served = sorted((v for v in run["served"] if len(v) > 1), key=len, reverse=True)

    def own_words(text):
        if any(text.strip() in value for value in served):
            return []                   # a piece of one served value
        if text.lstrip().startswith("{"):
            return []                   # the exact report: fixed English JSON
        for value in served:
            text = re.sub(rf"(?<!\w){re.escape(value)}(?!\w)", " ", text)
        return leaked(text, only, page_known())

    leaks = [(name, own_words(text), text)
             for name, text in page._after_boot(run) if own_words(text)]
    assert leaks == [], (code, leaks)


def page_known() -> tuple[str, ...]:
    """Names and data the harness feeds the page (sessions, models,
    folders, a tool row, the report's facts), which are not text."""
    return ("me DM", "team", "other", "some new phase", "my-project", "stale busy",
            "card edits", "alpha", "bravo", "charlie", "delta", "old1", "dev", "web", "proj",
            "aipager_bot",
            "pytest", "docker", "Ubuntu", "Linux", "aiohttp", "notify", "ValueError",
            "KeyError", "RuntimeError", "pip", "uv", "systemd", "node_modules", "README")


@pytest.mark.parametrize("code", CODES)
def test_the_mini_app_plurals(node_bin, tmp_path, code):
    from aipager.miniapp.static import INDEX_HTML
    from tests import test_miniapp_page_i18n as page

    # The harness builds n items for some counts, so no million here (the
    # Python side checks "many" above; the page's rule is the same port).
    counts = (1, 2, 3, 5, 11, 21, 22, 25, 100)
    run = page.drive(node_bin, tmp_path, INDEX_HTML, scenario="plurals",
                     opts=page._opts(code, counts=list(counts)))
    for n in counts:
        got = run["plurals"][str(n)]
        assert got["need"] == _form(code, "ui.pulse.need", n), n
        assert got["queued"] == _form(code, "ui.activity.queued", n), n
        assert got["timeline"] == _form(code, "ui.timeline.title_n", n), n
        assert got["more"] == _form(code, "ui.report.show_more", n), n
        assert got["tooLong"] == _form(code, "ui.report.too_long", n), n


def test_the_page_catalog_is_served_whole():
    from aipager.miniapp import ui_text
    for code in CODES:
        body = ui_text.catalog(code)
        assert body["rtl"] is False and body["digits"] == "0123456789"
        assert set(body["strings"]) == {m.key for m in ui_text.PAGE_STRINGS}
        assert json.dumps(body, ensure_ascii=False).count("\u2014") == 0
